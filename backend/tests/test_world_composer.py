"""Phase 14 — World Composer unit tests (pure in-process; no DB, no network).

Covers the deterministic composition contract: evidence reachability on every
kit, prompt->world diversity, provenance, procedural fallback, the WORld issue
bucket, variant provenance and determinism (same input -> identical world).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets.catalog import load_catalog_from_repo  # noqa: E402
from app.assets.resolver import Provenance  # noqa: E402
from app.environments.manifests import load_all_environments  # noqa: E402
from app.environments.placer import EVIDENCE_CAPABLE_TYPES  # noqa: E402
from app.environments.resolver import resolve_environment  # noqa: E402
from app.generation.parser import parse_stage  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.world.composer import (  # noqa: E402
    KIT_BASE_OBJECT_IDS,
    KnownObjectSpecProvider,
    compose_world,
)
from app.world.extract import extract_world_requirements  # noqa: E402
from app.world.requirements import ObjectRequest, WorldRequirements  # noqa: E402

from fixtures.world_showcase import (  # noqa: E402
    SHOWCASE_EXPECTED,
    SHOWCASE_PROMPTS,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEV_MODE_CASE = BACKEND_DIR / "app" / "services" / "dev_mode_case.json"

CATALOG = load_catalog_from_repo()
KITS = {kit.environment_id: kit for kit in load_all_environments()}
_RUN_RESOLUTIONS: dict[str, str] = {}


def _golden_placements():
    script = json.loads(DEV_MODE_CASE.read_text(encoding="utf-8"))
    return parse_stage(GenerationStage.WORLD_GRAPH, script["world_graph"][0]).placements


GOLDEN_PLACEMENTS = _golden_placements()


def _compose(prompt_or_reqs, environment_id=None):
    if isinstance(prompt_or_reqs, str):
        world_reqs = extract_world_requirements(prompt_or_reqs)
    else:
        world_reqs = prompt_or_reqs
    kit = KITS[environment_id or (world_reqs.environment_hint or "apartment")]
    return compose_world(
        world_reqs,
        env_resolver=resolve_environment,
        spec_provider=KnownObjectSpecProvider(),
        evidence_placements=GOLDEN_PLACEMENTS,
        catalog=CATALOG,
        kit=kit,
    )


# --------------------------------------------------------------------------- #
# required evidence reachable placement (knife + forensic on EVERY kit)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kit_id", sorted(KITS))
def test_required_evidence_gets_reachable_placement_on_every_kit(kit_id):
    kit = KITS[kit_id]
    composition = _compose(WorldRequirements(environment_hint=kit_id), kit_id)
    assert composition.issues == ()
    knife = next(
        p for p in composition.placements if p.object_id == "kitchen_knife"
    )
    assert knife.evidence_id == "forensic_knife_match_01"
    assert knife.interaction == "inspect"
    anchor = kit.by_id[knife.anchor]
    assert anchor.type in EVIDENCE_CAPABLE_TYPES
    # data-level pickable/hitbox bound: the anchor transform is finite and
    # inside the manifest bounds (|v| <= 20), the phase-14 pin holds.
    for value in (anchor.position.x, anchor.position.y, anchor.position.z):
        assert math.isfinite(value)
        assert abs(value) <= 20.0
    laptop = next(p for p in composition.placements if p.object_id == "apartment_laptop")
    assert laptop.evidence_id == "email_thomas_01"
    assert laptop.interaction == "read"


# --------------------------------------------------------------------------- #
# deterministic identical output for identical input
# --------------------------------------------------------------------------- #


def test_same_input_produces_identical_composition():
    first = _compose(SHOWCASE_PROMPTS["warehouse"], "warehouse")
    second = _compose(SHOWCASE_PROMPTS["warehouse"], "warehouse")
    assert first.placements == second.placements
    assert first.provenance_by_object_id == second.provenance_by_object_id
    assert first.new_objects == second.new_objects
    assert first.issues == second.issues
    # byte-level: serialized placement list identical
    assert json.dumps([p.__dict__ for p in first.placements], sort_keys=True) == json.dumps(
        [p.__dict__ for p in second.placements], sort_keys=True
    )


# --------------------------------------------------------------------------- #
# five different prompts do not collapse to the same world
# --------------------------------------------------------------------------- #


def test_five_prompts_do_not_collapse_to_the_same_world():
    compositions = {
        env_id: _compose(SHOWCASE_PROMPTS[env_id], env_id)
        for env_id in SHOWCASE_PROMPTS
    }
    assert {c.environment_id for c in compositions.values()} == set(KITS)
    # office carries the proc trophy asset absent from the apartment world
    office_assets = {p.asset_id for p in compositions["office"].placements}
    apartment_assets = {p.asset_id for p in compositions["apartment"].placements}
    trophy_assets = {
        p.asset_id for p in compositions["office"].placements
        if p.asset_id.startswith("proc.")
    }
    assert trophy_assets  # the office proc trophy is present
    assert office_assets - apartment_assets  # prompt-specific objects differ
    # warehouse carries the wrench absent from the apartment world
    warehouse_assets = {p.asset_id for p in compositions["warehouse"].placements}
    assert "PROP_WRENCH_01" in warehouse_assets
    assert "PROP_WRENCH_01" not in apartment_assets
    # object sets differ pairwise (no collapse)
    sets = {
        env: {p.object_id for p in composition.placements}
        for env, composition in compositions.items()
    }
    assert sets["office"] != sets["apartment"]
    assert sets["warehouse"] != sets["office"]
    assert sets["hotel_suite"] != sets["warehouse"]
    assert sets["mansion"] != sets["hotel_suite"]


def test_showcase_compositions_publish_with_expected_assets_and_provenance():
    for env_id, expectation in SHOWCASE_EXPECTED.items():
        composition = _compose(SHOWCASE_PROMPTS[env_id], env_id)
        assert composition.environment_id == expectation.environment_id
        assert composition.kit_version == KITS[env_id].version
        assert composition.issues == ()
        assets = {p.asset_id for p in composition.placements}
        for required in expectation.required_in_world:
            assert required in assets, (env_id, required)
        if expectation.proc_assets_expected:
            proc_assets = {p.asset_id for p in composition.placements if p.asset_id.startswith("proc.")}
            assert proc_assets, env_id
        # supporting objects resolve through the catalog (correct provenance)
        for object_id, provenance in composition.provenance_by_object_id.items():
            assert provenance in {
                Prov.value for Prov in (
                    Provenance.CATALOG_EXACT,
                    Provenance.CATALOG_ALIAS,
                    Provenance.PROCEDURAL_GENERATED,
                )
            }
        # knife keeps CATALOG_EXACT provenance on every kit
        assert composition.provenance_by_object_id["kitchen_knife"] == "CATALOG_EXACT"


def test_procedural_assets_embed_definitions_with_matching_asset_ids():
    office = _compose(SHOWCASE_PROMPTS["office"], "office")
    for placement in office.placements:
        if placement.asset_id.startswith("proc."):
            assert placement.generated_definition is not None
            assert placement.generated_definition["assetId"] == placement.asset_id
            assert placement.generated_definition["category"] == "decor"
    mansion = _compose(SHOWCASE_PROMPTS["mansion"], "mansion")
    assert any(p.asset_id.startswith("proc.") for p in mansion.placements)


def test_provenance_of_prompt_specific_objects():
    office = _compose(SHOWCASE_PROMPTS["office"], "office")
    trophy_ids = [
        object_id
        for object_id, provenance in office.provenance_by_object_id.items()
        if provenance == "PROCEDURAL_GENERATED"
    ]
    assert trophy_ids
    warehouse = _compose(SHOWCASE_PROMPTS["warehouse"], "warehouse")
    assert warehouse.provenance_by_object_id.get("adjustable_wrench") == "CATALOG_EXACT"
    assert warehouse.provenance_by_object_id.get("rope") == "CATALOG_EXACT"


# --------------------------------------------------------------------------- #
# unknown-but-valid -> procedural; unknown-and-unknown -> world issue
# --------------------------------------------------------------------------- #


def test_unknown_but_valid_object_goes_through_procedural_builder():
    world_reqs = WorldRequirements(
        objects=(ObjectRequest(requested_name="Custom Trophy"),)
    )
    composition = _compose(world_reqs, "office")
    assert composition.issues == ()
    proc_assets = {p.asset_id for p in composition.placements if p.asset_id.startswith("proc.")}
    assert proc_assets
    assert all(p.generated_definition is not None for p in composition.placements if p.asset_id.startswith("proc."))


def test_unresolvable_object_is_a_structured_world_issue():
    world_reqs = WorldRequirements(
        objects=(ObjectRequest(requested_name="quantum woggle"),)
    )
    composition = _compose(world_reqs, "office")
    assert composition.issues
    assert any("world.unresolved-object" in issue for issue in composition.issues)
    # sanitized: no prompt echo, no paths, no internals
    combined = " ".join(composition.issues)
    assert "quantum woggle" in combined  # the (already-safe) request name
    assert "http" not in combined and "/" not in combined
    assert "\\" not in combined and ".." not in combined
    # requirement: KNOWN-UNSAFE requests are never composed into an asset
    unsafe_reqs = WorldRequirements(
        objects=(),
        unsafe_unsupported=("unsafeUnsupported: known-unsafe object term 'bomb' was not composed",),
    )
    safe = _compose(unsafe_reqs, "apartment")
    assert safe.issues == ()
    assert not any("bomb" in p.asset_id for p in safe.placements)


def test_unsupported_dangerous_asset_request_fails_safely():
    extracted = extract_world_requirements("A bomb and a gun were at the castle.")
    assert extracted.objects == ()
    assert extracted.unsafe_unsupported
    composition = _compose(extracted, "apartment")
    assert composition.issues == ()
    joined = " ".join(composition.issues)
    assert "bomb" not in joined and "gun" not in joined
    # no arbitrary asset was composed: only the golden base set sits in the world
    assert {p.asset_id for p in composition.placements} == set(
        CATALOG.by_id and {p.asset_id for p in _compose(WorldRequirements(), "apartment").placements}
    )


# --------------------------------------------------------------------------- #
# crafted invalid composition surfaces the WORld bucket
# --------------------------------------------------------------------------- #


def test_crafted_evidence_on_non_evidence_anchor_is_a_world_issue():
    # A crafted request forces an evidence link onto an object whose ONLY
    # anchors are non-evidence-capable (a window cannot host evidence).
    world_reqs = WorldRequirements(
        objects=(
            ObjectRequest(
                requested_name="window",
                evidence_id="forensic_knife_match_01",
            ),
        )
    )
    composition = _compose(world_reqs, "apartment")
    assert composition.issues
    assert any(("world.invalid-placement" in i) for i in composition.issues) or any(
        ("world.unreachable-evidence" in i) for i in composition.issues
    )


def test_crafted_evidence_without_interaction_is_a_world_issue():
    world_reqs = WorldRequirements(
        objects=(
            ObjectRequest(
                requested_name="wrench",
                evidence_id="forensic_knife_match_01",  # fake link, no interaction
            ),
        )
    )
    composition = _compose(world_reqs, "warehouse")
    assert composition.issues
    issue_text = " ".join(composition.issues)
    assert "world." in issue_text


# --------------------------------------------------------------------------- #
# variant params (Phase 12) -> PARAMETRIC_VARIANT provenance
# --------------------------------------------------------------------------- #


def test_variant_params_resolve_with_parametric_provenance():
    # A NON-base catalog asset with declared Phase 12 variants: applying a
    # bounded variant records PARAMETRIC_VARIANT provenance (never fabricated).
    request = ObjectRequest(
        requested_name="cup",
        variant_params=(("color", "#b5651d"), ("material", "ceramic")),
    )
    composition = _compose(WorldRequirements(objects=(request,)), "apartment")
    assert composition.issues == ()
    record = composition.resolution_record["resolved"]["cup"]
    assert record["provenance"] == "PARAMETRIC_VARIANT"
    # the variant request is placed as a supporting decorative object
    assert my_get(composition.new_objects, "cup") is not None or any(
        o.asset_id == "PROP_CUP_01" for o in composition.new_objects
    )


def my_get(sequence, object_id):
    for item in sequence:
        if item.object_id == object_id:
            return item
    return None


# --------------------------------------------------------------------------- #
# world issues are structured + sanitized (no prompt echo, no internals)
# --------------------------------------------------------------------------- #


def test_world_diagnostics_are_structured_and_sanitized():
    extracted = extract_world_requirements(
        "A very secret weird object was found nowhere."
    )
    composition = _compose(extracted, "office")
    joined = " ".join(composition.issues)
    assert "secret" not in joined or "world." in joined
    for issue in composition.issues:
        assert issue.startswith("world.")
    # absolutely no prompt text leakage beyond safe request names
    prompts_words = set("a very secret weird object was found nowhere".split())
    leaked = [
        word for word in prompts_words
        if word in joined and word not in ("a", "was", "found", "object")
    ]
    assert leaked == []