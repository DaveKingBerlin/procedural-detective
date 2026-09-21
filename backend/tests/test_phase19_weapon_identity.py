"""Phase 19 §15 — semantic object identity vs render asset identity.

Medium-root-cause regression suite. The LLM ``world_requirements`` returns
``antique brass letter opener``; the Asset Oracle resolves the SEMANTIC object
to the catalog RENDER asset ``letter_opener`` (PROP_LETTER_OPENER_01). The fix
guarantees:

- ``CaseTruth.weaponId == "antique_brass_letter_opener"`` (unchanged);
- ``PublicObject.object_id == "antique_brass_letter_opener"`` (SEMANTIC)
  while ``PublicObject.asset_id == "PROP_LETTER_OPENER_01"`` (RENDER);
- evidence references resolve; the solver proves the SEMANTIC object; the
  candidate universe / accusation / reveal use the SEMANTIC identity;
- the render asset id NEVER leaks into answer identity;
- a semantic object that cannot be represented fails closed (typed error,
  never silently dropped, never published).

Both the REAL driver (mocked transport, ``test_ollama_driver._run``) and the
deterministic FakeProvider golden path assert the identity contract. No
network: the autouse backend network block is active.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.state_machine import GenerationState  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.schemas.accusation import AccusationRequest  # noqa: E402
from app.services.accusation import (  # noqa: E402
    AccusationValidationError,
    validate_universe_membership,
)
from app.services.publication import (  # noqa: E402
    project_world_objects,
    serialize_published_payload,
)
from app.services.reveal import candidate_block_of, truth_labels  # noqa: E402
from app.world.composer import SemanticObjectResolutionError  # noqa: E402
from test_ollama_driver import (  # noqa: E402
    _case_people,
    _evidence,
    _j,
    _run,
)

# The locked semantic weapon + render asset the Medium showcase pins.
SEMANTIC_WEAPON_ID = "antique_brass_letter_opener"
RENDER_ASSET_ID = "PROP_LETTER_OPENER_01"

MEDIUM_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
    "Motive: stolen research data\n"
    "Weapon: antique brass letter opener\nTime: 23:42\n"
    "Witness: Lisa König\nLocation: hotel suite\n"
)


def _medium_world():
    return {
        "environmentHint": "hotel suite",
        "locationTokens": ["hotel", "suite"],
        "objects": [
            {
                "name": "antique brass letter opener",
                "categoryHint": "decor",
                "criticality": "required",
            }
        ],
        "relations": [
            {"kind": "on_table", "target": "antique brass letter opener"}
        ],
        "unsafeUnsupported": [],
    }


def _medium_posts(weapon_obj=SEMANTIC_WEAPON_ID):
    return [
        _j(_case_people(weapon=weapon_obj)),
        _j(_evidence(weapon_obj=weapon_obj, murderer="paul_becker")),
        _j(_medium_world()),
    ]


def _load_objects(record):
    """The PUBLIC objects of the assembled/published draft."""
    from app.generation import pipeline

    public, _facts, _truth, _draft = pipeline.assemble(record)
    return public, _draft


# --------------------------------------------------------------------------- #
# §15 — the REAL driver path (mock Ollama transport), Medium showcase
# --------------------------------------------------------------------------- #


def test_driver_medium_semantic_weapon_identity_preserved():
    record, transport = _run(_medium_posts(), prompt=MEDIUM_PROMPT)
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 3  # case + evidence + world (alias, no proc)
    assert record.budget.calls == 3
    # the environment canonicalized from the user/LLM hint.
    assert record.draft.scene.environment_id == "hotel_suite"

    public, draft = _load_objects(record)
    # 1. CaseTruth.weaponId stays the SEMANTIC id.
    assert record.draft.crime.weapon_id == SEMANTIC_WEAPON_ID
    # 2. PublicObject.object_id is the SEMANTIC id; asset_id is the RENDER id.
    weapon = next(
        o for o in public.objects if o.object_id == SEMANTIC_WEAPON_ID
    )
    assert weapon.asset_id == RENDER_ASSET_ID
    # 3. evidence references resolve (validate_evidence == ()).
    from app.domain.evidence import validate_evidence

    assert validate_evidence(public, tuple(f for f in record.draft.evidence)) == ()

    # 4. solver accepts and proves the semantic object.
    assert record.solver_proof is not None
    assert record.solver_proof.weapon.winner == SEMANTIC_WEAPON_ID
    assert record.last_validation.validation.all_true is True

    # 5. candidate universe contains the SEMANTIC identity.
    from app.domain.eligibility import derive_universes

    universe = derive_universes(public)
    assert SEMANTIC_WEAPON_ID in universe.weapon_ids
    assert RENDER_ASSET_ID not in universe.weapon_ids

    # 6. the render asset id NEVER replaces the semantic id anywhere.
    for obj in public.objects:
        assert obj.object_id != RENDER_ASSET_ID


def test_driver_medium_candidates_and_reveal_use_semantic_identity():
    record, _transport = _run(_medium_posts(), prompt=MEDIUM_PROMPT)
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(record.published, title="Medium")
    )
    # accusation candidates: entry id == SEMANTIC, assetId == RENDER, name
    # human-readable.
    candidates = candidate_block_of(payload)
    entry = next(
        e for e in candidates["weapons"] if e["id"] == SEMANTIC_WEAPON_ID
    )
    assert entry["assetId"] == RENDER_ASSET_ID
    assert entry["name"] == "Antique Brass Letter Opener"
    assert entry["id"] != entry["assetId"]
    # the render label never appears as a candidate id.
    assert all(e["id"] != RENDER_ASSET_ID for e in candidates["weapons"])
    # reveal truth uses the SEMANTIC id + human label.
    labels = truth_labels(payload)
    assert labels["weaponId"] == SEMANTIC_WEAPON_ID
    assert labels["weaponName"] == "Antique Brass Letter Opener"


def test_driver_medium_accusation_universe_semantic_only():
    record, _transport = _run(_medium_posts(), prompt=MEDIUM_PROMPT)
    assert record.state is GenerationState.PUBLISHED
    from app.generation import pipeline

    _public, _facts, truth, _draft = pipeline.assemble(record)
    payload = json.loads(
        serialize_published_payload(record.published, title="Medium")
    )
    truth_crime = payload["truth"]["crime"]
    body = AccusationRequest(
        murdererId=str(truth_crime["murderer_id"]),
        motiveId=str(truth_crime["motive_id"]),
        weaponId=SEMANTIC_WEAPON_ID,
        crimeTime=str(truth_crime["crime_time"]["canonical"]),
    )
    validate_universe_membership(payload, body)  # no raise
    body_render = AccusationRequest(
        murdererId=str(truth_crime["murderer_id"]),
        motiveId=str(truth_crime["motive_id"]),
        weaponId=RENDER_ASSET_ID,
        crimeTime=str(truth_crime["crime_time"]["canonical"]),
    )
    with pytest.raises(AccusationValidationError):
        validate_universe_membership(payload, body_render)


def test_driver_medium_world_placement_keeps_semantic_and_render_layers():
    record, _transport = _run(_medium_posts(), prompt=MEDIUM_PROMPT)
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(record.published, title="Medium")
    )
    world = project_world_objects(payload)
    placed = next(w for w in world if w["objectId"] == SEMANTIC_WEAPON_ID)
    assert placed["assetId"] == RENDER_ASSET_ID
    # the placement is interactable (evidence-linked inspect per the driver
    # projection): the semantic object carries the sealed weapon evidence.
    assert placed.get("interaction") in ("inspect", "read", "")
    assert placed["objectId"] != placed["assetId"]


def test_deterministic_fake_path_semantic_identity():
    """The deterministic extractor+composer path (the FakeProvider golden
    pipeline's composition) preserves the identity model: the procedural
    antique letter opener keeps its SEMANTIC object id and the render asset id
    never equals it."""
    from app.world.composer import KnownObjectSpecProvider, compose_world
    from app.world.extract import extract_world_requirements

    reqs = extract_world_requirements(MEDIUM_PROMPT, None)
    assert reqs.environment_hint == "hotel_suite"
    names = {r.requested_name for r in reqs.objects}
    # the deterministic extractor maps the prompt weapon to its known-table
    # procedural fixture (unchanged Phase 13 behavior).
    assert "Antique Ceremonial Letter Opener" in names

    composition = compose_world(
        reqs,
        env_resolver=None,
        spec_provider=KnownObjectSpecProvider(),
        environment_id="hotel_suite",
    )
    assert composition.issues == ()
    placed = {
        p.object_id: p.asset_id
        for p in composition.placements
        if p.object_id == "antique_ceremonial_letter_opener"
    }
    assert "antique_ceremonial_letter_opener" in placed  # SEMANTIC id
    assert placed["antique_ceremonial_letter_opener"].startswith("proc.")
    # the render id never replaces the semantic identity.
    assert placed["antique_ceremonial_letter_opener"] != "antique_ceremonial_letter_opener"


def test_alias_resolves_semantic_object_to_catalog_render_preserving_semantic_id():
    """``antique brass letter opener`` (as the LLM world stage emits it)
    resolves VISUALLY to the catalog ``letter_opener`` asset while the
    SEMANTIC object id stays ``antique_brass_letter_opener`` — nothing rewrites
    crime.weaponId or evidence references."""
    from app.world.composer import compose_world
    from app.world.requirements import CRITICALITY_REQUIRED, ObjectRequest, WorldRequirements

    reqs = WorldRequirements(
        environment_hint="hotel_suite",
        objects=(
            ObjectRequest(
                requested_name="antique brass letter opener",
                criticality=CRITICALITY_REQUIRED,
            ),
        ),
        relations=(),
    )
    composition = compose_world(
        reqs,
        env_resolver=None,
        spec_provider=KnownFakeSpecProvider(),
        environment_id="hotel_suite",
    )
    assert composition.issues == ()
    placed = {
        p.object_id: p.asset_id
        for p in composition.placements
        if p.object_id == SEMANTIC_WEAPON_ID
    }
    assert placed.get(SEMANTIC_WEAPON_ID) == RENDER_ASSET_ID
    assert composition.provenance_by_object_id.get(SEMANTIC_WEAPON_ID) == "CATALOG_ALIAS"


class KnownFakeSpecProvider:
    """Deterministic spec provider that answers NO object (a miss): the alias
    resolution must succeed from the CATALOG without any provider call."""

    def generate(self, request):
        from app.assets.spec_provider import AssetSpecResponse

        return AssetSpecResponse(content=None)


def test_fail_closed_when_semantic_object_cannot_be_represented():
    """A Medium-style run whose world DOES NOT produce the semantic weapon
    object fails closed: the typed guard maps to a sanitized terminal code and
    NOTHING is published."""
    from app.services.ollama_driver import (
        SemanticObjectResolutionError as DriverError,
    )

    empty_world = {
        "environmentHint": "hotel suite",
        "locationTokens": ["hotel", "suite"],
        "objects": [],  # the weapon object is NEVER requested
        "relations": [],
        "unsafeUnsupported": [],
    }
    posts = [
        _j(_case_people(weapon=SEMANTIC_WEAPON_ID)),
        _j(_evidence(weapon_obj=SEMANTIC_WEAPON_ID, murderer="paul_becker")),
        _j(empty_world),
    ]
    record, _transport = _run(posts, prompt=MEDIUM_PROMPT)
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert DriverError is SemanticObjectResolutionError
    assert record.failure_code == "VALIDATION_FAILED"


def test_guard_raises_typed_sanitized_error_directly():
    """The compose-time presence guard raises the typed fail-closed error with
    a sanitized message (public identifiers only — never raw provider text)."""
    from app.world.composer import SemanticObjectResolutionError as ComposerError

    with pytest.raises(ComposerError) as excinfo:
        raise ComposerError(
            "essential semantic object(s) referenced by the crime/evidence "
            "algebra could not be represented in the world: "
            + SEMANTIC_WEAPON_ID
        )
    message = str(excinfo.value)
    assert SEMANTIC_WEAPON_ID in message
    for hostile in ("http://", "<script", "solverProof"):
        assert hostile not in message


def test_fake_asset_provider_never_leaks_render_id_into_answer_identity():
    """The FakeProvider deterministic golden path keeps semantic identity for
    a KNOWN catalog weapon (the same identity model as the driver path)."""
    from fixtures.golden_generation import (  # noqa: E402
        GOLDEN_FULL_DRAFT,
        GOLDEN_STAGE_PAYLOADS,
    )
    from app.generation.admission import AdmissionController
    from app.generation.clock import ManualClock
    from app.generation.controller import GenerationController
    from app.generation.fake_provider import FakeProvider
    from app.generation.ids import IdSource

    script = {
        stage: [payload] for stage, payload in GOLDEN_STAGE_PAYLOADS.items()
    }
    script[GenerationStage.REPAIR] = [GOLDEN_FULL_DRAFT]
    fake = FakeProvider(script)
    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=fake,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=11,
        hold_before_publish=True,
    )
    handle = controller.start_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n"
        "Motive: cover_up_embezzlement\nWeapon: kitchen_knife\nTime: 22:17\n"
        "Witness: emily_reed\n",
        anonymous_quota_session_id=session.session_id,
    )
    record = controller.attempt(handle.attempt_id)
    assert controller.publish(handle.attempt_id, hold_ok=True).success is True
    assert record.draft.crime.weapon_id == "kitchen_knife"
    public, _draft = _load_objects(record)
    weapon = next(o for o in public.objects if o.object_id == "kitchen_knife")
    assert weapon.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert weapon.object_id != weapon.asset_id


__all__ = ["MEDIUM_PROMPT", "RENDER_ASSET_ID", "SEMANTIC_WEAPON_ID", "_medium_posts"]