"""Phase 13 — generated-asset integration tests (oracle cache / placer /
world-graph / picking / identity).

Closes the REQUIRED Phase13.md test list items that are cross-module rather
than parser/compiler-local:

- ``test_cache_hit_avoids_provider_call`` — REQUIRED "cache hit avoids provider
  call": a ``CountingSpecProvider`` proves the SAME requestedName resolves twice
  with one provider call; a DIFFERENT requestedName calls again.
- ``test_generated_assets_place_into_office_and_hotel_suite_kits`` — REQUIRED
  "generated asset can be placed into at least two environment kits": all four
  golden example assets place into BOTH the office and hotel_suite kits on the
  exact anchors a catalog asset of their category allows, deterministically.
- ``test_world_graph_rejects_proc_placement_without_embedded_definition`` —
  REQUIRED world-graph structural rule: a ``proc.*`` placement MUST carry an
  embedded ``generated_definition`` whose ``assetId`` matches the placement.
- ``test_project_world_objects_projects_valid_and_skips_invalid_proc_placement``
  — the projection gate PROJECTS a proc.* placement whose embedded definition
  validates (the bootstrap WorldObjectDTO carries ``generated``) and SKIPS one
  whose definition fails current schema/compiler validation — never a crash.
- ``test_generated_asset_picking_hitbox_and_transforms_bounded_at_data_level``
  — REQUIRED "generated asset works with direct picking/hitbox" (data level):
  definition part ids are stable, the projected ``generated`` block's hitbox
  scale is finite and within [HITBOX_MIN, HITBOX_MAX], and every part transform
  is finite and inside the documented bounds.
- ``test_two_hundred_distinct_specs_produce_distinct_asset_ids`` — REQUIRED
  adversarial "hash collision attempts where practical": 200 deterministically
  different specs (category / name / dimensions / color vary) produce 200
  DISTINCT content-addressed assetIds.

The existing REQUIRED items live in the module-local suites
(``test_asset_specs`` / ``test_asset_compiler`` / ``test_generated_cache``) and
are untouched here. "Same input/spec produces identical output" and
"a proc.* asset compiles to the SAME id across two separate compiler pipelines"
are already covered by ``test_compiler_id_stable_across_fresh_compile_pipelines``
and ``test_same_input_identical_output_bytes`` in ``test_asset_compiler.py``.

Everything is deterministic and in-process: no network, no DB, no real provider.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from fixtures.asset_specs import (
    ANTIQUE_LETTER_OPENER_NAME,
    CUSTOM_TROPHY_NAME,
    GOLDEN_SPEC_CONTENT,
    GOLDEN_SPEC_NAMES,
)
from app.assets.catalog import load_catalog_from_repo
from app.assets.compiler import (
    HITBOX_MAX,
    HITBOX_MIN,
    MAX_PART_SCALE,
    MAX_POSITION_BOUND,
    MAX_ROTATION_BOUND,
    MIN_PART_SCALE,
    compile_asset_spec,
)
from app.assets.oracle import GeneratedAssetOracle
from app.assets.resolver import AssetRequest
from app.assets.spec_provider import CountingSpecProvider, FakeAssetSpecProvider
from app.assets.specs import CATEGORY_SPEC_ALLOWLIST, parse_asset_spec
from app.environments.manifests import load_all_environments
from app.environments.placer import (
    PlacementRequest,
    generated_asset_anchor_meta,
    place_objects,
    validate_placement,
)
from app.generation.safety import ANCHOR_ALLOWLIST, validate_world_graph
from app.generation.schemas import (
    PlacementSpec,
    WorldGraphLocationSpec,
    WorldGraphSpec,
)
from app.services.publication import project_world_objects

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ENVIRONMENTS_DIR = REPO_ROOT / "assets" / "environments"


def _compile_golden():
    """assetId -> frozen definition for all four golden example specs."""
    definitions = {}
    for name in GOLDEN_SPEC_NAMES:
        raw = GOLDEN_SPEC_CONTENT[name.casefold().strip()]
        spec = parse_asset_spec(raw, non_throwing=False)
        definitions[compile_asset_spec(spec).asset_id] = compile_asset_spec(spec)
    return definitions


def _catalog():
    return load_catalog_from_repo()


@pytest.fixture(scope="module")
def catalog():
    return _catalog()


@pytest.fixture(scope="module")
def kits():
    return load_all_environments(directory=ENVIRONMENTS_DIR)


def _kit(kits, kit_id):
    return next(k for k in kits if k.environment_id == kit_id)


# --------------------------------------------------------------------------- #
# REQUIRED: cache hit avoids provider call
# --------------------------------------------------------------------------- #


def test_cache_hit_avoids_provider_call(catalog):
    """The SAME requestedName resolves twice with exactly ONE provider call; a
    DIFFERENT requestedName calls the provider again (bounded cache, proven by
    a CountingSpecProvider — never a mocked monkeypatch)."""
    oracle = GeneratedAssetOracle(catalog=catalog)
    counting = CountingSpecProvider(FakeAssetSpecProvider(GOLDEN_SPEC_CONTENT))

    first_request = AssetRequest(requested_name=CUSTOM_TROPHY_NAME)
    first = oracle.resolve_or_generate(
        first_request, spec_provider=counting, force_generate=True
    )
    assert first.generated is not None and first.error is None
    assert counting.call_count == 1

    # Cache hit: the second resolve of the SAME requestedName makes NO call.
    second = oracle.resolve_or_generate(
        first_request, spec_provider=counting, force_generate=True
    )
    assert second.generated is not None
    assert counting.call_count == 1
    assert second.generated.asset_id == first.generated.asset_id

    # A DIFFERENT requestedName is a cache miss -> provider called once more.
    other = oracle.resolve_or_generate(
        AssetRequest(requested_name=ANTIQUE_LETTER_OPENER_NAME),
        spec_provider=counting,
        force_generate=True,
    )
    assert other.generated is not None and other.error is None
    assert counting.call_count == 2
    assert other.generated.asset_id != first.generated.asset_id


# --------------------------------------------------------------------------- #
# REQUIRED: generated asset can be placed into at least two environment kits
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kit_id", ("office", "hotel_suite"))
def test_generated_assets_place_into_office_and_hotel_suite_kits(
    kits, catalog, kit_id
):
    """All four golden generated assets place into BOTH kits on anchors a
    catalog asset of their category allows, and the placement is deterministic
    (same input -> identical output)."""
    kit = _kit(kits, kit_id)
    definitions = _compile_golden()
    requests = [
        PlacementRequest(asset_id=asset_id, object_id=f"gen_{index:02d}")
        for index, asset_id in enumerate(sorted(definitions))
    ]
    placed = place_objects(
        kit, requests, catalog=catalog, generated_definitions=definitions
    )
    assert len(placed) == len(definitions) == 4
    # The placement contract passes for the full set.
    assert validate_placement(
        kit, placed, catalog=catalog, generated_definitions=definitions
    ) == ()
    # Every placed anchor is one the asset's category is allowed to host
    # (the exact derivation rule the placer applies — same category ALWAYS
    # yields the same anchor set).
    for item in placed:
        anchor = kit.by_id[item.anchor]
        definition = definitions[item.asset_id]
        _category, allowed_anchors = generated_asset_anchor_meta(definition, catalog)
        assert definition.category in anchor.allowed_categories
        assert anchor.type in allowed_anchors
    # Deterministic: a re-run yields byte-identical placements.
    again = place_objects(
        kit, requests, catalog=catalog, generated_definitions=definitions
    )
    assert again == placed
    assert [p.anchor for p in again] == [p.anchor for p in placed]


# --------------------------------------------------------------------------- #
# REQUIRED: world-graph structural rule + projection gate
# --------------------------------------------------------------------------- #


def _world_graph_for(asset_id, embedded_definition, anchor_id):
    return WorldGraphSpec(
        locations=(WorldGraphLocationSpec(location_id="loc_01", template="t"),),
        placements=(
            PlacementSpec(
                object_id="generated_prop",
                asset_id=asset_id,
                location_id="loc_01",
                anchor=anchor_id,
                interaction="",
                evidence_id=None,
                generated_definition=embedded_definition,
            ),
        ),
    )


def test_world_graph_rejects_proc_placement_without_embedded_definition(kits):
    r"""A proc.* placement whose assetId matches ^proc\.[a-z0-9_]+\.[0-9a-f]{16}$
    but carries NO validated embedded generatedDefinition is a structural
    world-graph issue (validate_world_graph reports it — never silent)."""
    anchor_id = next(
        a for a in ANCHOR_ALLOWLIST if a in {k.anchor_id for k in _kit(kits, "office").anchors}
    )
    definitions = _compile_golden()
    asset_id = next(iter(sorted(definitions)))

    # (a) MISSING definition -> structural issue.
    issues = validate_world_graph(
        _world_graph_for(asset_id, None, anchor_id), {"generated_prop"}, set()
    )
    assert any("requires an embedded generatedDefinition" in issue for issue in issues)

    # (b) Definition present but assetId MISMATCHES the placement -> issue.
    mismatched = dict(definitions[asset_id].to_definition_json())
    mismatched["assetId"] = "proc.decor.0123456789abcdef"
    issues = validate_world_graph(
        _world_graph_for(asset_id, mismatched, anchor_id), {"generated_prop"}, set()
    )
    assert any(
        "generatedDefinition.assetId does not match the placement assetId" in issue
        for issue in issues
    )

    # (c) A matching embedded definition removes the structural issue.
    issues = validate_world_graph(
        _world_graph_for(
            asset_id, definitions[asset_id].to_definition_json(), anchor_id
        ),
        {"generated_prop"},
        set(),
    )
    assert not any("generatedDefinition" in issue for issue in issues)


def test_project_world_objects_projects_valid_and_skips_invalid_proc_placement():
    """project_world_objects PROJECTS a proc.* placement whose embedded
    definition validates (the bootstrap WorldObjectDTO carries ``generated``)
    and SKIPS one whose definition fails current schema/compiler validation —
    no crash, sanitized (the invalid object never reaches the client)."""
    definitions = _compile_golden()
    valid_id = sorted(definitions)[0]
    invalid_id = sorted(definitions)[1]
    valid_definition = definitions[valid_id].to_definition_json()
    tampered = dict(definitions[invalid_id].to_definition_json())
    tampered["hitbox"] = {"scale": {"x": 1e308, "y": 1.0, "z": 1.0}}

    payload = {
        "draft": {
            "objects": [
                {"object_id": "valid_prop", "asset_id": valid_id,
                 "affordances": ["INSPECTABLE"], "subtype": "trophy"},
                {"object_id": "invalid_prop", "asset_id": invalid_id,
                 "affordances": [], "subtype": None},
            ],
            "evidence": [],
            "world_graph": {
                "placements": [
                    {"object_id": "valid_prop", "asset_id": valid_id,
                     "location_id": "loc_01", "anchor": "dining_table",
                     "interaction": "", "evidence_id": None,
                     "generated_definition": valid_definition},
                    {"object_id": "invalid_prop", "asset_id": invalid_id,
                     "location_id": "loc_01", "anchor": "dining_table",
                     "interaction": "", "evidence_id": None,
                     "generated_definition": tampered},
                ]
            },
        }
    }
    world_objects = project_world_objects(payload)
    object_ids = [item["objectId"] for item in world_objects]
    # The valid placement is projected WITH its generated block; the invalid
    # placement is skipped (sanitized) — never a crash, never a leak.
    assert object_ids == ["valid_prop"]
    (projected,) = world_objects
    assert "generated" in projected
    assert projected["generated"]["assetId"] == valid_id
    assert projected["generated"]["hitbox"] is not None


# --------------------------------------------------------------------------- #
# REQUIRED: generated asset works with direct picking/hitbox (data level)
# --------------------------------------------------------------------------- #


def test_generated_asset_picking_hitbox_and_transforms_bounded_at_data_level():
    """The definition's parts carry STABLE ids; the WorldObjectDTO ``generated``
    block (the direct-picking metadata the client sees) carries a hitbox with
    bounded finite scale, and every part transform is finite and in-bounds."""
    definition = next(iter(_compile_golden().values()))
    definition_json = definition.to_definition_json()

    # Stable part ids (compiler-ordered part_00.. part_0N, input-order free).
    assert [part["id"] for part in definition_json["parts"]] == [
        part.id for part in definition.parts
    ]
    assert len(definition_json["parts"]) == len(
        {part["id"] for part in definition_json["parts"]}
    )

    payload = {
        "draft": {
            "objects": [
                {"object_id": "gen_prop", "asset_id": definition.asset_id,
                 "affordances": ["INSPECTABLE"], "subtype": definition.subtype},
            ],
            "evidence": [],
            "world_graph": {
                "placements": [
                    {"object_id": "gen_prop", "asset_id": definition.asset_id,
                     "location_id": "loc_01", "anchor": "dining_table",
                     "interaction": "", "evidence_id": None,
                     "generated_definition": definition_json},
                ]
            },
        }
    }
    (dto,) = project_world_objects(payload)
    generated = dto["generated"]

    # Hitbox: finite and bounded (>= HITBOX_MIN pickable, <= HITBOX_MAX).
    hitbox = generated["hitbox"]["scale"]
    for axis in ("x", "y", "z"):
        value = hitbox[axis]
        assert math.isfinite(value), f"hitbox.{axis} must be finite"
        assert HITBOX_MIN <= value <= HITBOX_MAX, (
            f"hitbox.{axis} must be within [{HITBOX_MIN}, {HITBOX_MAX}]"
        )

    # Every part transform: finite and inside the documented bounds.
    for part in generated["parts"]:
        position = part["transform"]["position"]
        rotation = part["transform"]["rotation"]
        scale = part["transform"]["scale"]
        for axis in ("x", "y", "z"):
            assert math.isfinite(position[axis]) and math.isfinite(rotation[axis])
            assert math.isfinite(scale[axis])
            assert abs(position[axis]) <= MAX_POSITION_BOUND
            assert abs(rotation[axis]) <= MAX_ROTATION_BOUND
            assert MIN_PART_SCALE <= scale[axis] <= MAX_PART_SCALE


# --------------------------------------------------------------------------- #
# REQUIRED: hash-collision sanity (adversarial focus, where practical)
# --------------------------------------------------------------------------- #


def test_two_hundred_distinct_specs_produce_distinct_asset_ids():
    """200 deterministically different specs (category / name / dimensions /
    color all vary) produce 200 DISTINCT content-addressed assetIds."""
    categories = list(CATEGORY_SPEC_ALLOWLIST)
    materials = ["plastic", "wood.dark", "metal.brass", "metal.steel",
                 "wood.light", "ceramic", "leather", "fabric"]

    def _spec(index: int) -> dict:
        dim = round(0.06 + (index % 90) * 0.04, 4)  # 0.06 .. 3.62 within bounds
        z_scale = round(0.06 + (index % 47) * 0.04, 4)  # 0.06 .. 1.90
        return {
            "canonicalName": f"Collision Probe {index:03d}",
            "category": categories[index % len(categories)],
            "subtype": None,
            "dimensions": {"x": dim, "y": 0.2, "z": 0.2},
            "parts": [
                {
                    "id": "part_00",
                    "role": "base",
                    "primitive": "box",
                    "transform": {
                        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "scale": {"x": 0.2, "y": 0.2, "z": z_scale},
                    },
                    "material": materials[index % len(materials)],
                }
            ],
        }

    asset_ids = [
        compile_asset_spec(
            parse_asset_spec(_spec(index), non_throwing=False)
        ).asset_id
        for index in range(200)
    ]
    assert len(asset_ids) == 200
    assert len(set(asset_ids)) == 200, "hash collision across 200 distinct specs"