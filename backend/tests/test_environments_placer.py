"""Phase 11 — placement validation + deterministic object placer tests.

Covers the documented placement contract:

- exclusive anchors are occupied ONCE (duplicate occupancy is an issue);
- incompatible asset category/anchor combinations are rejected, as are
  anchor-type-incompatible combinations;
- evidence placed on an inaccessible/unpickable anchor (DOOR/WINDOW/CCTV/
  ACCESS_CONTROL/PLAYER_SPAWN) is rejected;
- evidence-bearing objects keep >= 0.3 units of separation;
- a BODY anchor stays navigable (spawn >= 0.8 away) and the spawn never
  intersects any anchor position (>= 0.4);
- `place_objects` is deterministic: same input -> same output, IDENTICAL under
  a SHUFFLED request order, and all player-facing objectIds are stable;
- every kit can host the golden evidence-relevant object set (the same 9
  objects the golden case uses) with the Phase 10 interaction contract
  (evidence-linked non-empty, decorative "");
- the composed world graph of every kit validates through the pipeline's
  world-graph allowlists (this also locks ANCHOR_ALLOWLIST as a superset of
  every shipped kit's anchorIds).
"""

from __future__ import annotations

import dataclasses
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.assets.catalog import load_catalog_from_repo
from app.environments.compose import compose_world_graph_for_kit
from app.environments.manifests import load_all_environments
from app.environments.placer import (
    EVIDENCE_CAPABLE_TYPES,
    PlacedObject,
    PlacementError,
    PlacementRequest,
    place_objects,
    validate_placement,
)
from app.generation.parser import parse_stage
from app.generation.provider import GenerationStage
from app.generation.safety import (
    ANCHOR_ALLOWLIST,
    INTERACTION_ALLOWLIST,
    validate_world_graph,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ENVIRONMENTS_DIR = REPO_ROOT / "assets" / "environments"
DEV_MODE_CASE = BACKEND_DIR / "app" / "services" / "dev_mode_case.json"

# The 9 golden placements with their Phase 10 contract (order from the
# golden world graph stage; the placer re-sorts deterministically).
GOLDEN_PLACEMENTS = (
    {"objectId": "kitchen_knife", "assetId": "PROP_KITCHEN_KNIFE_01",
     "anchorTypeHint": None, "interaction": "inspect",
     "evidenceId": "forensic_knife_match_01"},
    {"objectId": "letter_opener", "assetId": "PROP_LETTER_OPENER_01",
     "anchorTypeHint": None, "interaction": "inspect",
     "evidenceId": "forensic_letter_opener_01"},
    {"objectId": "scissors", "assetId": "PROP_SCISSORS_01",
     "anchorTypeHint": None, "interaction": "inspect",
     "evidenceId": "forensic_scissors_01"},
    {"objectId": "vase_01", "assetId": "PROP_VASE_01",
     "anchorTypeHint": None, "interaction": "",
     "evidenceId": None},
    {"objectId": "apartment_laptop", "assetId": "PROP_LAPTOP_01",
     "anchorTypeHint": None, "interaction": "read",
     "evidenceId": "email_thomas_01"},
    {"objectId": "apartment_table", "assetId": "PROP_TABLE_01",
     "anchorTypeHint": None, "interaction": "",
     "evidenceId": None},
    {"objectId": "apartment_door", "assetId": "DOOR_APARTMENT_01",
     "anchorTypeHint": None, "interaction": "",
     "evidenceId": None},
    {"objectId": "apartment_lamp", "assetId": "PROP_LAMP_01",
     "anchorTypeHint": None, "interaction": "",
     "evidenceId": None},
    {"objectId": "victim_body_placeholder", "assetId": "PROP_BODY_PLACEHOLDER_01",
     "anchorTypeHint": None, "interaction": "",
     "evidenceId": None},
)


def _golden_placements():
    """The golden (dev-mode) world-graph placements as parsed PlacementSpecs."""
    with open(DEV_MODE_CASE, encoding="utf-8") as handle:
        script = json.load(handle)
    wg = parse_stage(GenerationStage.WORLD_GRAPH, script["world_graph"][0])
    return wg.placements


@pytest.fixture(scope="module")
def kits():
    return load_all_environments(directory=ENVIRONMENTS_DIR)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog_from_repo()


@pytest.mark.parametrize(
    "kit_id",
    ("apartment", "office", "hotel_suite", "warehouse", "mansion"),
)
def test_golden_object_set_places_and_is_clickable(kits, catalog, kit_id):
    """For EVERY kit: the golden 9-object set can be placed; every
    evidence-linked object lands on an evidence-capable anchor with a
    non-empty interaction (clickable under the Phase 10 contract); decorative
    objects keep interaction '' and no evidence link."""
    kit = next(k for k in kits if k.environment_id == kit_id)
    requests = [
        PlacementRequest(
            asset_id=item["assetId"],
            object_id=item["objectId"],
            interaction=item["interaction"],
            evidence_id=item["evidenceId"],
            category_hint=catalog.by_id[item["assetId"]].category,
        )
        for item in GOLDEN_PLACEMENTS
    ]
    placed = place_objects(kit, requests, catalog=catalog)
    assert validate_placement(kit, placed, catalog=catalog) == ()
    by_object = {p.object_id: p for p in placed}
    for item in GOLDEN_PLACEMENTS:
        result = by_object[item["objectId"]]
        anchor = kit.by_id[result.anchor]
        if item["interaction"]:
            assert item["interaction"] in INTERACTION_ALLOWLIST
            assert result.interaction == item["interaction"]
            # evidence on evidence-capable anchor
            assert anchor.type in EVIDENCE_CAPABLE_TYPES
            assert result.evidence_id == item["evidenceId"]
        else:
            assert result.interaction == ""
            assert result.evidence_id is None
        # catalog-level compatibility both ways
        asset = catalog.by_id[item["assetId"]]
        assert asset.category in anchor.allowed_categories
        assert anchor.type in asset.allowed_anchors


@pytest.mark.parametrize(
    "kit_id",
    ("apartment", "office", "hotel_suite", "warehouse", "mansion"),
)
def test_place_objects_deterministic_under_shuffled_input(kits, catalog, kit_id):
    """Same input -> same output, IDENTICAL under shuffled request order, all
    player-facing objectIds stable."""
    kit = next(k for k in kits if k.environment_id == kit_id)
    base_requests = [
        PlacementRequest(asset_id=item["assetId"], object_id=item["objectId"])
        for item in GOLDEN_PLACEMENTS
    ]
    base = place_objects(kit, base_requests, catalog=catalog)
    shuffled = list(base_requests)
    rng = random.Random(17)
    outputs = set()
    for _ in range(5):
        rng.shuffle(shuffled)
        outputs.add(tuple(place_objects(kit, shuffled, catalog=catalog)))
    assert len(outputs) == 1
    (again,) = outputs
    assert again == base
    assert [p.object_id for p in again] == [p.object_id for p in base]
    assert [p.anchor for p in again] == [p.anchor for p in base]


def test_duplicate_exclusive_occupancy_rejected(kits, catalog):
    kit = next(k for k in kits if k.environment_id == "office")
    body_anchor = next(a for a in kit.anchors if a.type == "BODY")
    placed = (
        PlacedObject(object_id="victim", asset_id="PROP_BODY_PLACEHOLDER_01",
                     location_id=body_anchor.zone_id, anchor=body_anchor.anchor_id,
                     interaction="", evidence_id=None),
        PlacedObject(object_id="second_victim", asset_id="PROP_BODY_PLACEHOLDER_01",
                     location_id=body_anchor.zone_id, anchor=body_anchor.anchor_id,
                     interaction="", evidence_id=None),
    )
    issues = validate_placement(kit, placed, catalog=catalog)
    assert any(
        "duplicate occupancy of exclusive anchor" in issue for issue in issues
    )


def test_incompatible_category_and_anchor_type_rejected(kits, catalog):
    kit = next(k for k in kits if k.environment_id == "office")
    door = next(a for a in kit.anchors if a.type == "DOOR")
    # evidence category on a DOOR anchor (category AND anchor-type mismatch).
    placed = (
        PlacedObject(object_id="knife", asset_id="PROP_KITCHEN_KNIFE_01",
                     location_id=door.zone_id, anchor=door.anchor_id,
                     interaction="inspect", evidence_id="ev1"),
    )
    issues = validate_placement(kit, placed, catalog=catalog)
    assert any("is not allowed on anchor" in issue for issue in issues)
    assert any("cannot host asset" in issue for issue in issues)
    assert any("inaccessible/unpickable" in issue for issue in issues)


def test_evidence_on_inaccessible_anchor_rejected(kits, catalog):
    """Evidence on a PLAYER_SPAWN/DOOR/WINDOW/CCTV/ACCESS_CONTROL anchor is
    never allowed (documented accessibility rule)."""
    kit = next(k for k in kits if k.environment_id == "office")
    access = next(a for a in kit.anchors if a.type == "ACCESS_CONTROL")
    placed = (
        PlacedObject(object_id="knife", asset_id="PROP_KITCHEN_KNIFE_01",
                     location_id=access.zone_id, anchor=access.anchor_id,
                     interaction="inspect", evidence_id="forensic_knife_match_01"),
    )
    issues = validate_placement(kit, placed, catalog=catalog)
    assert any("inaccessible/unpickable" in issue for issue in issues)


def test_evidence_minimum_spacing_enforced(kits, catalog):
    kit = next(k for k in kits if k.environment_id == "mansion")
    desk_a = next(a for a in kit.anchors if a.type == "DESK_EVIDENCE")
    # a second evidence object crammed onto the same desk anchor spacing
    placed = (
        PlacedObject(object_id="knife", asset_id="PROP_KITCHEN_KNIFE_01",
                     location_id=desk_a.zone_id, anchor=desk_a.anchor_id,
                     interaction="inspect", evidence_id="ev1"),
        PlacedObject(object_id="scissors", asset_id="PROP_SCISSORS_01",
                     location_id=desk_a.zone_id, anchor=desk_a.anchor_id,
                     interaction="inspect", evidence_id="ev2"),
    )
    issues = validate_placement(kit, placed, catalog=catalog)
    assert any("apart" in issue and "minimum 0.3" in issue for issue in issues)


def test_body_stays_navigable(kits, catalog):
    """A BODY anchor moved onto the spawn fails placement-time validation
    (the spawn must stay >= 0.8 navigable from the body)."""
    import dataclasses

    from app.environments.manifests import Vec3

    kit = next(k for k in kits if k.environment_id == "office")
    body = next(a for a in kit.anchors if a.type == "BODY")
    moved = dataclasses.replace(
        body, position=Vec3(x=kit.spawn.position.x, y=0.0, z=kit.spawn.position.z)
    )
    # Mutation bypasses the (valid) constructor gate on purpose: this test
    # exercises the PLACEMENT-time navigability check on an otherwise-valid kit.
    mutated = dataclasses.replace(kit)
    object.__setattr__(
        mutated,
        "anchors",
        tuple(moved if a.anchor_id == moved.anchor_id else a for a in kit.anchors),
    )
    single = PlacedObject(
        object_id="victim", asset_id="PROP_BODY_PLACEHOLDER_01",
        location_id=moved.zone_id, anchor=moved.anchor_id,
        interaction="", evidence_id=None,
    )
    issues = validate_placement(mutated, (single,), catalog=catalog)
    assert any("must stay navigable" in issue for issue in issues)


def test_spawn_never_intersects_anchor(kits, catalog):
    """A kit whose spawn overlaps an anchor position fails placement-time
    validation (spawn >= 0.4 clearance rule)."""
    import dataclasses

    from app.environments.manifests import Vec3

    kit = next(k for k in kits if k.environment_id == "office")
    door = next(a for a in kit.anchors if a.type == "DOOR")
    mutated = dataclasses.replace(kit)
    object.__setattr__(
        mutated,
        "spawn",
        dataclasses.replace(
            kit.spawn,
            position=Vec3(x=door.position.x, y=door.position.y, z=door.position.z),
        ),
    )
    issues = validate_placement(mutated, (), catalog=catalog)
    assert any("intersects anchor" in issue for issue in issues)


@pytest.mark.parametrize(
    "kit_id",
    ("apartment", "office", "hotel_suite", "warehouse", "mansion"),
)
def test_composed_world_graph_passes_pipeline_validation(kits, catalog, kit_id):
    """The composed world graph of every kit re-anchors the golden object set
    and validates through the exact allowlists the pipeline applies
    (this also locks ANCHOR_ALLOWLIST as a superset of kit anchorIds)."""
    kit = next(k for k in kits if k.environment_id == kit_id)
    golden = _golden_placements()
    composed = compose_world_graph_for_kit(kit, golden, catalog=catalog)
    assert len(composed.placements) == 9
    assert validate_placement(kit, composed.placements, catalog=catalog) == ()
    object_ids = {golden[i].object_id for i in range(len(golden))}
    evidence_ids = {
        p.evidence_id for p in golden if p.evidence_id is not None
    }
    issues = validate_world_graph(composed, object_ids, evidence_ids)
    assert issues == ()


def test_anchor_allowlist_superset_of_kit_anchor_ids(kits):
    """ANCHOR_ALLOWLIST (pipeline world-graph gate) covers every shipped
    anchorId — the static allowlist and the manifests can never drift."""
    all_anchor_ids = {
        a.anchor_id for kit in kits for a in kit.anchors
    }
    missing = sorted(all_anchor_ids - set(ANCHOR_ALLOWLIST))
    assert not missing, f"anchors missing from ANCHOR_ALLOWLIST: {missing}"


def test_anchor_type_hint_is_strict(kits, catalog):
    """An explicit anchorTypeHint restricts the pool exactly; unsatisfiable
    hints raise PlacementError (never a silent fallback)."""
    kit = next(k for k in kits if k.environment_id == "office")
    with pytest.raises(PlacementError):
        place_objects(
            kit,
            [PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01", anchor_type_hint="CCTV")],
            catalog=catalog,
        )
    # a satisfiable hint lands on the requested anchor type
    placed = place_objects(
        kit,
        [PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01", anchor_type_hint="DESK_EVIDENCE")],
        catalog=catalog,
    )
    assert kit.by_id[placed[0].anchor].type == "DESK_EVIDENCE"


def test_unknown_asset_and_exhausted_exclusive_pool_raise(kits, catalog):
    kit = next(k for k in kits if k.environment_id == "office")
    with pytest.raises(PlacementError):
        place_objects(kit, [PlacementRequest(asset_id="NO_SUCH_ASSET_99")], catalog=catalog)
    # Both DOOR anchors occupied by two different doors: the exclusive pool is
    # exhausted for a third door request.
    door = next(a for a in kit.anchors if a.type == "DOOR")
    with pytest.raises(PlacementError):
        place_objects(
            kit,
            [
                PlacementRequest(asset_id="DOOR_APARTMENT_01", object_id="d1"),
                PlacementRequest(asset_id="DOOR_APARTMENT_01", object_id="d2"),
                PlacementRequest(asset_id="DOOR_APARTMENT_01", object_id="d3"),
            ],
            catalog=catalog,
        )
    _ = door  # (door used for readability only)


def test_generated_object_ids_are_stable_and_unique(kits, catalog):
    kit = next(k for k in kits if k.environment_id == "warehouse")
    placed = place_objects(
        kit,
        [
            PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01"),
            PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01"),
            PlacementRequest(asset_id="PROP_VASE_01"),
        ],
        catalog=catalog,
    )
    ids = [p.object_id for p in placed]
    assert len(set(ids)) == len(ids)  # unique
    again = place_objects(
        kit,
        [
            PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01"),
            PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01"),
            PlacementRequest(asset_id="PROP_VASE_01"),
        ],
        catalog=catalog,
    )
    assert [p.object_id for p in again] == ids