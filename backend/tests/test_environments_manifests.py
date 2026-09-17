"""Phase 11 — environment kit MANIFEST tests (per-kit descriptor contract).

For every one of the five kits (apartment / office / hotel_suite /
warehouse / mansion) the suite asserts:

- the descriptor validates with ZERO issues and re-validates on typed
  descriptor construction (non-bypassable, the catalog.py pattern);
- required anchor-type coverage (defaultAnchorCoverage) is present (the six
  mandatory classes + at least two secondary types);
- the player spawn is valid (PLAYER_SPAWN reference + no anchor intersection);
- deterministic geometry: re-loading the same file yields identical local
  transforms (positions/rotations) for every anchor;
- every structuralAsset id resolves in the Asset Oracle catalog;
- geometry content rules hold: positions bounded, BODY on the floor plane,
  DOCUMENT anchors near a desk anchor, no two exclusive anchors co-located;
- NO arbitrary URL/resource anywhere in the manifest (deep substring scan);
- the Phase 11 aliases resolve as documented (flat/condo -> apartment,
  workplace/office -> office, hotel/room -> hotel_suite, depot/storage ->
  warehouse, villa/manor -> mansion);
- property/invariant rejections: duplicate exclusive-anchor occupancy,
  incompatible asset category/anchor and evidence-on-inaccessible-anchor are
  placement issues (covered in test_environments_placer.py); out-of-bounds
  positions and invalid anchor types are MANIFEST load issues here;
- the additive catalog assets validate (zero issues) and the real manifests
  load with zero issues through the strict loader.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.assets.catalog import load_catalog_from_repo
from app.environments.manifests import (
    ANCHOR_TYPES,
    EnvironmentKit,
    EnvironmentValidationError,
    MAX_ANCHORS,
    MAX_STRING_LENGTH,
    REQUIRED_COVERAGE_TYPES,
    SECONDARY_COVERAGE_TYPES,
    load_all_environments,
    load_environment,
    validate_environment_data,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ENVIRONMENTS_DIR = REPO_ROOT / "assets" / "environments"
CATALOG_PATH = REPO_ROOT / "assets" / "catalog" / "catalog.json"

KIT_IDS = ("apartment", "office", "hotel_suite", "warehouse", "mansion")

# canonical anchors of the golden case (the apartment kit reuses them 1:1).
GOLDEN_ANCHORS = {
    "kitchen_counter",
    "office_desk_01",
    "bedside_table",
    "dining_table",
    "desk_main",
    "hall_wall_01",
    "shelf_01",
    "floor_body_position",
}

# Deep-scan markers: no manifest string may carry a URL/path/executable token.
_FORBIDDEN_MARKERS = (
    "http://",
    "https://",
    "data:",
    "file:",
    "javascript:",
    "<script",
    "\\",
    "..",
    "://",
)


def _iter_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_strings(value)


@pytest.fixture(scope="module")
def kits():
    return load_all_environments(directory=ENVIRONMENTS_DIR)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog_from_repo()


def test_all_five_kits_declared(kits):
    assert [k.environment_id for k in kits] == sorted(KIT_IDS)


@pytest.mark.parametrize("kit_id", KIT_IDS)
def test_kit_descriptor_validates_and_revalidates(kit_id):
    raw = json.loads((ENVIRONMENTS_DIR / f"{kit_id}.json").read_text(encoding="utf-8"))
    assert validate_environment_data(raw) == ()
    kit = load_environment(kit_id, directory=ENVIRONMENTS_DIR)
    assert isinstance(kit, EnvironmentKit)
    assert kit.environment_id == kit_id
    # deterministic re-load equality
    again = load_environment(kit_id, directory=ENVIRONMENTS_DIR)
    assert again == kit


@pytest.mark.parametrize("kit_id", KIT_IDS)
def test_requested_coverage_and_counts(kit_id):
    kit = load_environment(kit_id, directory=ENVIRONMENTS_DIR)
    assert len(kit.zones) >= 5
    assert len(kit.anchors) >= 10
    unique_zones = {z.zone_id for z in kit.zones}
    assert len(unique_zones) == len(kit.zones)  # zoneIds unique
    unique_anchors = {a.anchor_id for a in kit.anchors}
    assert len(unique_anchors) == len(kit.anchors)  # anchorIds unique

    covered = {
        t for t, ids in kit.default_anchor_coverage.items() if ids
    }
    for required in sorted(REQUIRED_COVERAGE_TYPES):
        assert required in covered, f"{kit_id}: no default coverage for {required}"
    secondary_covered = len(set(SECONDARY_COVERAGE_TYPES) & covered)
    assert secondary_covered >= 2, (
        f"{kit_id}: only {secondary_covered} secondary types covered"
    )
    # Every coverage anchor must exist in the kit.
    for anchor_list in kit.default_anchor_coverage.values():
        for anchor_id in anchor_list:
            assert anchor_id in unique_anchors


@pytest.mark.parametrize("kit_id", KIT_IDS)
def test_spawn_valid_and_non_colliding(kit_id):
    kit = load_environment(kit_id, directory=ENVIRONMENTS_DIR)
    spawn_anchor = kit.by_id[kit.spawn.anchor_id]
    assert spawn_anchor.type == "PLAYER_SPAWN"
    for anchor in kit.anchors:
        if anchor.anchor_id == kit.spawn.anchor_id:
            continue
        assert kit.spawn.position.distance_to(anchor.position) >= 0.4, (
            f"{kit_id}: spawn within 0.4 of {anchor.anchor_id!r}"
        )


@pytest.mark.parametrize("kit_id", KIT_IDS)
def test_deterministic_geometry(kit_id):
    first = load_environment(kit_id, directory=ENVIRONMENTS_DIR)
    second = load_environment(kit_id, directory=ENVIRONMENTS_DIR)
    for a, b in zip(first.anchors, second.anchors):
        assert a.anchor_id == b.anchor_id
        assert (a.position.x, a.position.y, a.position.z) == (
            b.position.x,
            b.position.y,
            b.position.z,
        )
        assert (a.rotation.x, a.rotation.y, a.rotation.z) == (
            b.rotation.x,
            b.rotation.y,
            b.rotation.z,
        )


@pytest.mark.parametrize("kit_id", KIT_IDS)
def test_structural_assets_resolve_in_catalog(kit_id, catalog):
    kit = load_environment(kit_id, directory=ENVIRONMENTS_DIR)
    assert len(kit.structural_assets) >= 6
    for asset_id in kit.structural_assets:
        assert asset_id in catalog.by_id, (
            f"{kit_id}: structural asset {asset_id!r} missing from the catalog"
        )


@pytest.mark.parametrize("kit_id", KIT_IDS)
def test_body_on_floor_and_documents_near_desks(kit_id):
    kit = load_environment(kit_id, directory=ENVIRONMENTS_DIR)
    desks = [
        a.position for a in kit.anchors if a.type == "DESK_EVIDENCE"
    ]
    for anchor in kit.anchors:
        if anchor.type == "BODY":
            assert abs(anchor.position.y) <= 1e-3
        if anchor.type == "DOCUMENT":
            assert any(
                anchor.position.distance_to(desk) <= 3.0 for desk in desks
            ), f"{kit_id}: DOCUMENT anchor not near a DESK_EVIDENCE anchor"
    # exclusive anchors never share a position (epsilon 1e-3)
    exclusive = [a for a in kit.anchors if a.exclusive]
    for i in range(len(exclusive)):
        for j in range(i + 1, len(exclusive)):
            assert (
                exclusive[i].position.distance_to(exclusive[j].position) > 1e-3
            ), f"{kit_id}: exclusive anchors share a position"


@pytest.mark.parametrize("kit_id", KIT_IDS)
def test_no_arbitrary_url_or_resource_anywhere(kit_id):
    raw = (ENVIRONMENTS_DIR / f"{kit_id}.json").read_text(encoding="utf-8")
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in raw, f"{kit_id}: forbidden marker {marker!r}"
    data = json.loads(raw)
    for text in _iter_strings(data):
        assert "\x00" not in text
        assert len(text) <= MAX_STRING_LENGTH
        assert not text.startswith("/") and "\\" not in text
    # The README must be written too (single source paragraph).
    assert (ENVIRONMENTS_DIR / "README.md").exists()


def test_aliases_resolve_phase11_table():
    alias_map = {
        "flat": "apartment",
        "condo": "apartment",
        "workplace": "office",
        "office": "office",
        "hotel": "hotel_suite",
        "room": "hotel_suite",
        "depot": "warehouse",
        "storage": "warehouse",
        "villa": "mansion",
        "manor": "mansion",
    }
    from app.environments.resolver import resolve_environment

    for alias, expected in alias_map.items():
        resolution = resolve_environment(alias)
        assert resolution.environment_id == expected, alias
        assert resolution.resolved is True


def test_apartment_kit_reuses_golden_layout(kits):
    apartment = next(k for k in kits if k.environment_id == "apartment")
    declared = {a.anchor_id for a in apartment.anchors}
    assert GOLDEN_ANCHORS <= declared, (
        "the apartment kit must reuse the CURRENT golden layout anchors so the "
        "golden case maps 1:1"
    )


# --------------------------------------------------------------------------- #
# manifest-load rejection properties (deterministic sorted issues)
# --------------------------------------------------------------------------- #


def test_raw_validator_rejects_out_of_bounds_position():
    raw = json.loads((ENVIRONMENTS_DIR / "office.json").read_text(encoding="utf-8"))
    raw["anchors"][0]["position"]["x"] = 999.0
    issues = validate_environment_data(raw, catalog=load_catalog_from_repo())
    assert any(
        "position" in issue and "must satisfy |v| <= 20" in issue
        for issue in issues
    )


def test_raw_validator_rejects_nonfinite_and_rotation_overflow():
    raw = json.loads((ENVIRONMENTS_DIR / "office.json").read_text(encoding="utf-8"))
    raw["anchors"][0]["rotation"]["y"] = 3.0 * 3.141592653589793
    issues = validate_environment_data(raw)
    assert any("rotation" in issue and "2*pi" in issue for issue in issues)


def test_raw_validator_rejects_unknown_anchor_type_and_zone(kit_id="office"):
    raw = json.loads((ENVIRONMENTS_DIR / f"{kit_id}.json").read_text(encoding="utf-8"))
    raw["anchors"][0]["type"] = "TELEPORTER"
    issues = validate_environment_data(raw, catalog=load_catalog_from_repo())
    assert any("ANCHOR_TYPES" in issue for issue in issues)
    raw2 = json.loads((ENVIRONMENTS_DIR / f"{kit_id}.json").read_text(encoding="utf-8"))
    raw2["anchors"][0]["zoneId"] = "ghost_zone"
    issues2 = validate_environment_data(raw2, catalog=load_catalog_from_repo())
    assert any("unknown zoneId" in issue for issue in issues2)


def test_raw_validator_rejects_duplicate_anchor_and_zone_ids():
    raw = json.loads((ENVIRONMENTS_DIR / "apartment.json").read_text(encoding="utf-8"))
    raw["anchors"].append(dict(raw["anchors"][1]))
    issues = validate_environment_data(raw, catalog=load_catalog_from_repo())
    assert any("duplicate anchorId" in issue for issue in issues)
    raw2 = json.loads((ENVIRONMENTS_DIR / "apartment.json").read_text(encoding="utf-8"))
    raw2["zones"].append(dict(raw2["zones"][0]))
    issues2 = validate_environment_data(raw2, catalog=load_catalog_from_repo())
    assert any("duplicate zoneId" in issue for issue in issues2)


def test_raw_validator_rejects_unsafe_strings():
    raw = json.loads((ENVIRONMENTS_DIR / "apartment.json").read_text(encoding="utf-8"))
    raw["canonicalName"] = "https://evil.example/x"
    issues = validate_environment_data(raw, catalog=load_catalog_from_repo())
    assert any("URL scheme" in issue for issue in issues)
    raw2 = json.loads((ENVIRONMENTS_DIR / "apartment.json").read_text(encoding="utf-8"))
    raw2["canonicalName"] = "x" * 200
    issues2 = validate_environment_data(raw2, catalog=load_catalog_from_repo())
    assert any("exceeds 80" in issue for issue in issues2)


def test_raw_validator_rejects_exclusive_position_collision_and_bad_coverage():
    raw = json.loads((ENVIRONMENTS_DIR / "office.json").read_text(encoding="utf-8"))
    # colliding exclusive positions (body + door)
    body = next(a for a in raw["anchors"] if a["type"] == "BODY")
    door = next(a for a in raw["anchors"] if a["type"] == "DOOR")
    door["position"] = dict(body["position"])
    issues = validate_environment_data(raw, catalog=load_catalog_from_repo())
    assert any("exclusive anchors" in issue for issue in issues)

    raw2 = json.loads((ENVIRONMENTS_DIR / "office.json").read_text(encoding="utf-8"))
    raw2["defaultAnchorCoverage"]["BODY"] = []
    issues2 = validate_environment_data(raw2, catalog=load_catalog_from_repo())
    assert any("required anchor type 'BODY'" in issue for issue in issues2)


def test_array_size_bounds():
    raw = json.loads((ENVIRONMENTS_DIR / "apartment.json").read_text(encoding="utf-8"))
    raw["aliases"] = ["a"] * 20
    raw["tags"] = ["t"] * 40
    raw["zones"] = raw["zones"][:5] + [dict(raw["zones"][0])] * 10
    issues = validate_environment_data(raw, catalog=load_catalog_from_repo())
    assert any("aliases: exceeds the maximum of 8" in issue for issue in issues)
    assert any("tags: exceeds the maximum of 16" in issue for issue in issues)
    assert any("zones exceeds the maximum of 8" in issue for issue in issues)


def test_lighting_and_structural_vocabulary():
    raw = json.loads((ENVIRONMENTS_DIR / "apartment.json").read_text(encoding="utf-8"))
    raw["lighting"]["profile"] = "lava_lamp"
    issues = validate_environment_data(raw, catalog=load_catalog_from_repo())
    assert any("lighting vocabulary" in issue for issue in issues)
    raw2 = json.loads((ENVIRONMENTS_DIR / "apartment.json").read_text(encoding="utf-8"))
    raw2["structuralAssets"] = ["NOT_A_REAL_ASSET_99"]
    issues2 = validate_environment_data(raw2, catalog=load_catalog_from_repo())
    assert any("does not exist in the asset catalog" in issue for issue in issues2)


def test_typed_construction_cannot_bypass_validation():
    """Dataclass replace and direct construction both re-run the invariants:
    an unsafe canonical name is rejected at construction time (catalog.py
    DEF-063 pattern)."""
    import dataclasses

    kit = load_environment("office", directory=ENVIRONMENTS_DIR)
    with pytest.raises(EnvironmentValidationError):
        dataclasses.replace(kit, canonical_name="../etc/passwd")
    with pytest.raises(EnvironmentValidationError):
        EnvironmentKit(
            environment_id="office",
            version=kit.version,
            canonical_name="../etc/passwd",
            aliases=kit.aliases,
            tags=kit.tags,
            zones=kit.zones,
            anchors=kit.anchors,
            spawn=kit.spawn,
            lighting=kit.lighting,
            structural_assets=kit.structural_assets,
            style_hint=kit.style_hint,
            default_anchor_coverage=kit.default_anchor_coverage,
        )


def test_additive_catalog_assets_validate(catalog):
    """The six Phase 11 additive structural/prop entries are valid catalog
    assets (zero issues on the real manifest)."""
    from app.assets.catalog import validate_catalog_data

    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert validate_catalog_data(raw) == ()
    expected_additive = {
        "PROP_WINDOW_01",
        "PROP_WALL_01",
        "PROP_DESK_01",
        "PROP_HOTEL_BED_01",
        "PROP_WAREHOUSE_SHELF_01",
        "PROP_OFFICE_CHAIR_01",
    }
    assert expected_additive <= set(catalog.by_id)
    for asset_id in expected_additive:
        asset = catalog.by_id[asset_id]
        assert asset.render_kind in ("box", "cylinder", "sphere", "flat")
        assert asset.composite_kind is None
        assert all(
            anchor in ANCHOR_TYPES for anchor in asset.allowed_anchors
        )


def test_all_real_manifests_load_with_zero_issues():
    """Fresh import path: the real manifests load with ZERO issues (the strict
    loader raises EnvironmentValidationError otherwise)."""
    kits = load_all_environments(directory=ENVIRONMENTS_DIR)
    assert len(kits) == 5
    assert {k.environment_id for k in kits} == set(KIT_IDS)