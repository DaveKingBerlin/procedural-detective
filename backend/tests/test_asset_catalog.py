"""Phase 10 — Asset Oracle catalog loading + load-time validation.

Covers the Phase 10 required catalog tests: duplicate asset ids rejected,
conflicting/confusable aliases deterministically rejected, canonical-name
collisions, id-pattern / dimension / color / vocabulary / composite-rule
checks, string-safety scans, fallback existence, byte-stability and
determinism, and the invariant that mutating a catalog descriptor in memory
never affects an already-published CaseVersion (public DTO / investigation DTO
/ stored payload bytes stay unchanged).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.assets.catalog import (
    DOCUMENTED_ASSET_KEYS,
    REQUIRED_ASSET_KEYS,
    Catalog,
    CatalogValidationError,
    load_catalog,
    validate_catalog_data,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "assets" / "catalog" / "catalog.json"
BACKEND_DIR = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# minimal manifest builders (valid by default; tests override one field each)
# --------------------------------------------------------------------------- #


def _asset(asset_id: str = "PROP_X_01", **overrides) -> dict:
    defaults = {
        "assetId": asset_id,
        "version": 1,
        "canonicalName": asset_id.lower().replace("_", " "),
        "aliases": [],
        "category": "utility",
        "subtype": "prop",
        "tags": [],
        "renderKind": "box",
        "compositeKind": None,
        "dimensions": {"x": 0.5, "y": 0.5, "z": 0.5},
        "colors": {"body": "#8d8d93"},
        "label": "Test object",
        "interactable": False,
        "supportedInteractions": [],
        "evidenceCapabilities": [],
        "allowedAnchors": ["GENERIC_PROP"],
    }
    defaults.update(overrides)
    return defaults


def _catalog(assets: list[dict], fallback: str = "PROP_FALLBACK_X") -> dict:
    return {"catalogVersion": 1, "fallbackAsset": fallback, "assets": assets}


def _write_catalog(tmp_path: Path, catalog: dict, name: str = "catalog.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return path


def _valid_catalog(extra: list[dict] | None = None) -> dict:
    return _catalog([_asset("PROP_X_01"), _asset("PROP_FALLBACK_X")] + (extra or []))


# --------------------------------------------------------------------------- #
# repo manifest contract
# --------------------------------------------------------------------------- #


def test_repo_manifest_loads_clean():
    """The shipped manifest is valid and produces zero load issues."""
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert validate_catalog_data(raw) == ()


def test_repo_manifest_has_phase12_catalog_and_fallback():
    """The v1 manifest now carries the Phase 12 100-object showcase: >= 100
    distinct entries containing the 9 golden assets, the neutral fallback and
    the six Phase 11 additive structural/prop entries (additive only)."""
    catalog = load_catalog(MANIFEST_PATH)
    assert len(catalog.assets) >= 100
    assert catalog.fallback_asset == "PROP_FALLBACK_01"
    assert "PROP_FALLBACK_01" in catalog.by_id
    legacy_ids = {
        "PROP_KITCHEN_KNIFE_01",
        "PROP_LETTER_OPENER_01",
        "PROP_SCISSORS_01",
        "PROP_LAPTOP_01",
        "PROP_TABLE_01",
        "DOOR_APARTMENT_01",
        "PROP_LAMP_01",
        "PROP_BODY_PLACEHOLDER_01",
        "PROP_VASE_01",
        "PROP_FALLBACK_01",
        # Phase 11 additive structural/prop entries
        "PROP_WINDOW_01",
        "PROP_WALL_01",
        "PROP_DESK_01",
        "PROP_HOTEL_BED_01",
        "PROP_WAREHOUSE_SHELF_01",
        "PROP_OFFICE_CHAIR_01",
    }
    assert legacy_ids <= {a.asset_id for a in catalog.assets}


def test_every_asset_uses_only_documented_keys():
    """Schema smoke: each manifest asset uses ONLY the documented keys.

    Phase 12 makes ``templateId``/``variants`` OPTIONAL keys: the Phase 10/11
    non-composite entries keep their byte-stable shape without them, while
    every composite entry carries both (its template + its bounded variants).
    """
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for index, item in enumerate(raw["assets"]):
        assert set(item.keys()) <= DOCUMENTED_ASSET_KEYS, (
            f"assets[{index}] unknown keys: {sorted(set(item) - DOCUMENTED_ASSET_KEYS)}"
        )
        assert set(item.keys()) >= REQUIRED_ASSET_KEYS, (
            f"assets[{index}] missing required keys: "
            f"{sorted(REQUIRED_ASSET_KEYS - set(item))}"
        )
        if item["renderKind"] == "composite":
            assert "templateId" in item and "variants" in item, (
                f"assets[{index}] composite without templateId/variants"
            )


def test_catalog_is_byte_stable_across_loads():
    """Two loads of the same file yield equal typed Catalogs (no drift)."""
    first = load_catalog(MANIFEST_PATH)
    second = load_catalog(MANIFEST_PATH)
    assert first == second
    assert first.catalog_version == second.catalog_version == 1
    assert first.by_id.keys() == second.by_id.keys()


def test_catalog_descriptor_fields_are_typed():
    catalog = load_catalog(MANIFEST_PATH)
    knife = catalog.by_id["PROP_KITCHEN_KNIFE_01"]
    assert knife.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert knife.version == 1
    assert knife.canonical_name == "kitchen knife"
    assert knife.aliases == ("chef knife", "knife", "kitchen_knife")
    assert knife.category == "evidence"
    assert knife.subtype == "sharp"
    assert knife.tags == ("weapon", "knife", "blade")
    assert knife.render_kind == "composite"
    assert knife.composite_kind == "kitchen_knife"
    assert (knife.dimensions.x, knife.dimensions.y, knife.dimensions.z) == (
        0.24,
        0.024,
        0.045,
    )
    assert knife.colors["blade"] == "#c8ccd4"
    assert knife.label == "Kitchen knife"
    assert knife.interactable is True
    assert knife.supported_interactions == ("inspect", "collect")
    assert knife.evidence_capabilities == ("weapon", "sharp")
    assert knife.allowed_anchors == (
        "DESK_EVIDENCE",
        "FLOOR_EVIDENCE",
        "TABLE_PROP",
        "GENERIC_PROP",
    )
    door = catalog.by_id["DOOR_APARTMENT_01"]
    assert door.render_kind == "box"
    assert door.composite_kind is None


# --------------------------------------------------------------------------- #
# duplicate / conflicting identity checks
# --------------------------------------------------------------------------- #


def test_duplicate_asset_ids_rejected_at_load(tmp_path):
    assets = [_asset("PROP_X_01"), _asset("PROP_X_01", canonicalName="other")]
    path = _write_catalog(tmp_path, _catalog(assets))
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("duplicate assetId 'PROP_X_01'" in issue for issue in issues)
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    assert any("duplicate assetId" in issue for issue in excinfo.value.issues)


def test_duplicate_conflicting_aliases_rejected(tmp_path):
    assets = [
        _asset("PROP_X_01", aliases=["chef knife"]),
        _asset("PROP_Y_01", canonicalName="second object", aliases=["chef knife"]),
    ]
    path = _write_catalog(tmp_path, _catalog(assets))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    joined = "\n".join(excinfo.value.issues)
    assert "conflicting aliases" in joined
    assert "PROP_X_01" in joined and "PROP_Y_01" in joined


def test_alias_colliding_with_other_canonical_rejected(tmp_path):
    assets = [
        _asset("PROP_X_01", canonicalName="kitchen knife"),
        _asset("PROP_Y_01", canonicalName="second object", aliases=["kitchen knife"]),
    ]
    path = _write_catalog(tmp_path, _catalog(assets))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    joined = "\n".join(excinfo.value.issues)
    assert "conflicting aliases" in joined
    assert "PROP_X_01" in joined and "PROP_Y_01" in joined


def test_duplicate_canonical_names_rejected(tmp_path):
    assets = [
        _asset("PROP_X_01", canonicalName="duplicate thing"),
        _asset("PROP_Y_01", canonicalName="duplicate thing"),
    ]
    path = _write_catalog(tmp_path, _catalog(assets))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    assert any(
        "conflicting canonical names" in issue for issue in excinfo.value.issues
    )


def test_asset_id_pattern_rejected(tmp_path):
    path = _write_catalog(
        tmp_path, _catalog([_asset("lower_knife"), _asset("PROP_FALLBACK_X")])
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("assetId 'lower_knife'" in issue for issue in issues)


# --------------------------------------------------------------------------- #
# dimension / color / vocabulary / composite checks
# --------------------------------------------------------------------------- #


def test_dimensions_must_be_positive_and_bounded(tmp_path):
    for bad in (
        {"x": 0.0, "y": 0.1, "z": 0.1},
        {"x": -1.0, "y": 0.1, "z": 0.1},
        {"x": 100.0, "y": 0.1, "z": 0.1},
        {"x": 1.0, "y": 0.1},
    ):
        path = _write_catalog(tmp_path, _catalog([_asset(dimensions=bad)]))
        issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
        assert issues, f"expected dimension issues for {bad!r}"


def test_color_values_must_be_rrggbb(tmp_path):
    path = _write_catalog(
        tmp_path, _catalog([_asset(colors={"body": "#gg00ff"})])
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("#gg00ff" in issue for issue in issues)


def test_category_must_be_in_documented_vocabulary(tmp_path):
    path = _write_catalog(tmp_path, _catalog([_asset(category="misc")]))
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("category 'misc'" in issue for issue in issues)


def test_interactions_must_be_in_allowlist(tmp_path):
    path = _write_catalog(
        tmp_path, _catalog([_asset(supportedInteractions=["teleport"])])
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("interaction 'teleport'" in issue for issue in issues)


def test_render_composite_consistency(tmp_path):
    # Phase 12: composite renderKind REQUIRES a templateId from
    # TEMPLATE_VOCABULARY; compositeKind stays null unless it is one of the
    # six special builders.
    # composite without compositeKind AND without templateId -> issue.
    path = _write_catalog(
        tmp_path, _catalog([_asset(renderKind="composite", compositeKind=None)])
    )
    assert validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    # composite with a valid templateId and no compositeKind -> valid.
    path = _write_catalog(
        tmp_path,
        _catalog(
            [_asset(renderKind="composite", compositeKind=None, templateId="blade_chef", variants=[]),
             _asset("PROP_FALLBACK_X")]
        ),
    )
    assert validate_catalog_data(json.loads(path.read_text(encoding="utf-8"))) == ()
    # composite with a templateId OUTSIDE the frozen vocabulary -> issue.
    path = _write_catalog(
        tmp_path,
        _catalog([_asset(renderKind="composite", compositeKind=None, templateId="chainsaw")]),
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("templateId" in issue and "TEMPLATE_VOCABULARY" in issue for issue in issues)
    # non-composite renderKind with a compositeKind -> issue.
    path = _write_catalog(
        tmp_path, _catalog([_asset(renderKind="box", compositeKind="kitchen_knife")])
    )
    assert validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    # non-composite renderKind with a templateId -> issue.
    path = _write_catalog(
        tmp_path, _catalog([_asset(renderKind="box", templateId="blade_chef")])
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("templateId must be null" in issue for issue in issues)


def test_fallback_asset_must_exist(tmp_path):
    path = _write_catalog(
        tmp_path, _catalog([_asset("PROP_X_01")], fallback="PROP_NOPE_01")
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("fallbackAsset 'PROP_NOPE_01'" in issue for issue in issues)


def test_missing_required_keys_rejected(tmp_path):
    broken = _asset()
    del broken["label"]
    path = _write_catalog(tmp_path, _catalog([broken]))
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("missing required keys" in issue for issue in issues)


def test_unknown_keys_rejected(tmp_path):
    path = _write_catalog(
        tmp_path, _catalog([_asset(evilKey="javascript:alert(1)")])
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("unknown keys" in issue for issue in issues)


# --------------------------------------------------------------------------- #
# catalog-side string safety
# --------------------------------------------------------------------------- #


def test_catalog_strings_reject_url_schemes(tmp_path):
    path = _write_catalog(
        tmp_path, _catalog([_asset(canonicalName="prop http://evil.example/x")])
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("URL scheme" in issue for issue in issues)


def test_catalog_strings_reject_path_separators_and_traversal(tmp_path):
    path = _write_catalog(
        tmp_path, _catalog([_asset(aliases=["../../x", "a\\b"])])
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    joined = "\n".join(issues)
    assert "path separator" in joined
    assert "path traversal" in joined


def test_catalog_strings_reject_control_characters_and_oversize(tmp_path):
    path = _write_catalog(
        tmp_path, _catalog([_asset(label="bad\x00label"), _asset("PROP_FALLBACK_X")])
    )
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("control characters" in issue for issue in issues)
    long_alias = _asset(aliases=["x" * 121])
    path = _write_catalog(tmp_path, _catalog([long_alias, _asset("PROP_FALLBACK_X")]))
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("exceeds 120 characters" in issue for issue in issues)


def test_catalog_load_rejects_invalid_json(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{ not json !", encoding="utf-8")
    with pytest.raises(Exception) as excinfo:
        load_catalog(path)
    assert "not valid JSON" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# published-case stability under in-memory descriptor mutation
# --------------------------------------------------------------------------- #


def _published_golden(phase5_app):
    """Create + publish the golden case via the dev provider through the API,
    and return (public dto, bootstrap dto, payload bytes, case_id, creator)."""
    from fastapi.testclient import TestClient

    from phase5_helpers import create_case, create_playthrough, create_session

    with TestClient(phase5_app) as c:
        session_token, _ = create_session(c)
        case = create_case(c, session_token)
        case_id = case["caseId"]
        creator = case["creatorAccessToken"]
        public = c.get(
            f"/api/v1/cases/{case_id}/versions/1",
            headers={"Authorization": f"Bearer {creator}"},
        ).json()
        status, body = create_playthrough(c, creator, case_id, 1)
        assert status == 201, body
        bootstrap = c.get(
            f"/api/v1/playthroughs/{body['playthroughId']}/investigation",
            headers={"Authorization": f"Bearer {body['playthroughAccessToken']}"},
        ).json()
    payload_bytes = phase5_app.state.store.get_published(case_id, 1).payload_json
    return public, bootstrap, payload_bytes, case_id, creator


def test_catalog_descriptor_mutation_does_not_affect_published_semantics(
    phase5_app,
):
    """Colors/geometry of the catalog may change in memory; the already
    published CaseVersion (public DTO, investigation DTO, stored payload) is
    byte-identical because publication carries only assetId + safe metadata."""
    public, bootstrap, payload_bytes, case_id, creator = _published_golden(
        phase5_app
    )

    original = load_catalog(MANIFEST_PATH)
    knife = original.by_id["PROP_KITCHEN_KNIFE_01"]
    mutated_colors = dict(knife.colors)
    mutated_colors["blade"] = "#ff0000"
    mutated = dataclasses.replace(
        knife,
        dimensions=dataclasses.replace(knife.dimensions, x=99.0),
        colors=mutated_colors,
    )
    assert mutated.colors["blade"] == "#ff0000"
    assert mutated.dimensions.x == 99.0
    assert knife.colors["blade"] == "#c8ccd4"  # the original is untouched

    # Resolution through the MUTATED catalog still classifies the golden
    # placements as CATALOG_EXACT: the semantic identity of an asset is the
    # logical id, never its render metadata.
    from app.assets import resolve
    from app.assets.resolver import AssetRequest, AssetResolver

    mutated_catalog = dataclasses.replace(
        original,
        assets=tuple(
            mutated if a.asset_id == mutated.asset_id else a
            for a in original.assets
        ),
    )
    assert (
        mutated_catalog.by_id["PROP_KITCHEN_KNIFE_01"].colors["blade"] == "#ff0000"
    )
    probe = AssetResolver(mutated_catalog).resolve_request(
        AssetRequest(requested_name="PROP_KITCHEN_KNIFE_01")
    )
    assert probe.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert probe.provenance.value == "CATALOG_EXACT"

    # The stored payload and the served DTOs are byte-identical afterwards.
    assert (
        phase5_app.state.store.get_published(case_id, 1).payload_json
        == payload_bytes
    )
    assert _public_case_dto(phase5_app, case_id, creator) == public
    # The bootstrap DTO differs ONLY in the fresh playthroughId (each
    # playthrough gets its own id); every player-safe block is unchanged.
    def _strip_playthrough(dto: dict) -> dict:
        normalized = dict(dto)
        normalized.pop("playthroughId", None)
        return normalized

    assert _strip_playthrough(_bootstrap_of(phase5_app, case_id, creator)) == (
        _strip_playthrough(bootstrap)
    )

    # The mutated catalog still falls back explicitly for unknown objects.
    resolved = resolve({"requestedName": "dragon"}, catalog=mutated_catalog)
    assert resolved.provenance.value == "FALLBACK"
    assert resolved.asset_id == "PROP_FALLBACK_01"


def _public_case_dto(phase5_app, case_id: str, creator: str) -> dict:
    """GET the exact published-version PublicCaseResponse DTO."""
    from fastapi.testclient import TestClient

    with TestClient(phase5_app) as c:
        return c.get(
            f"/api/v1/cases/{case_id}/versions/1",
            headers={"Authorization": f"Bearer {creator}"},
        ).json()


def _bootstrap_of(phase5_app, case_id: str, creator: str) -> dict:
    """GET the investigation bootstrap DTO of the exact version."""
    from fastapi.testclient import TestClient

    from phase5_helpers import create_playthrough

    with TestClient(phase5_app) as c:
        status, body = create_playthrough(c, creator, case_id, 1)
        assert status == 201, body
        return c.get(
            f"/api/v1/playthroughs/{body['playthroughId']}/investigation",
            headers={
                "Authorization": f"Bearer {body['playthroughAccessToken']}"
            },
        ).json()


# --------------------------------------------------------------------------- #
# DEF-057 — catalog ID-IDENTITY cross-checks (canonical/alias/assetId share
# ONE normal-form namespace; two different assets may not claim the same one).
# --------------------------------------------------------------------------- #


def test_cross_set_identity_rejects_alias_shadowing_asset_id(tmp_path):
    """QA repro: 'PROP_LAPTOP_01' as ANOTHER asset's alias must not load."""
    laptop = _asset(
        "PROP_LAPTOP_01",
        canonicalName="laptop",
        category="electronics",
        subtype="computer",
        interactable=True,
    )
    imposter = _asset(
        "PROP_FAKE_01", canonicalName="imposter", aliases=["PROP_LAPTOP_01"]
    )
    path = _write_catalog(tmp_path, _catalog([laptop, imposter]))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    joined = "\n".join(excinfo.value.issues)
    assert "conflicting identities" in joined
    assert "PROP_LAPTOP_01" in joined and "PROP_FAKE_01" in joined


def test_cross_set_identity_rejects_canonical_shadowing_asset_id(tmp_path):
    """QA repro: canonicalName 'PROP_LAPTOP_01' on ANOTHER asset must not load."""
    laptop = _asset(
        "PROP_LAPTOP_01",
        canonicalName="laptop",
        category="electronics",
        subtype="computer",
        interactable=True,
    )
    imposter = _asset("PROP_FAKE_02", canonicalName="PROP_LAPTOP_01")
    path = _write_catalog(tmp_path, _catalog([laptop, imposter]))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    joined = "\n".join(excinfo.value.issues)
    assert "conflicting identities" in joined
    assert "PROP_LAPTOP_01" in joined and "PROP_FAKE_02" in joined


def test_cross_set_identity_rejects_id_colliding_with_other_identity(tmp_path):
    """assetId normal form may not collide with ANOTHER asset's canonical or
    alias (the symmetric side of the shadow checks)."""
    fake = _asset(
        "PROP_Z_01",
        canonicalName="PROP_X_01",
        aliases=["alias-a", "PROP_X_01"],
    )
    other = _asset("PROP_X_01", canonicalName="the real thing")
    path = _write_catalog(tmp_path, _catalog([fake, other]))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    joined = "\n".join(excinfo.value.issues)
    assert "conflicting identities" in joined
    # both the canonical and the alias collisions are reported.
    assert joined.count("conflicting identities") >= 2


# --------------------------------------------------------------------------- #
# DEF-058 — FALLBACK INVARIANT: the fallback must be a declared, NEUTRAL
# (category 'utility', non-interactable) asset.
# --------------------------------------------------------------------------- #


def test_fallback_invariant_rejects_non_neutral_fallback(tmp_path):
    """fallbackAsset=knife (evidence, interactable) is a load issue — the
    QA resolution impact (unresolved 'dragon' -> interactable knife) is
    unreachable because the catalog can never load."""
    knife = _asset(
        "PROP_KITCHEN_KNIFE_01",
        canonicalName="kitchen knife",
        category="evidence",
        subtype="sharp",
        tags=["weapon"],
        renderKind="box",
        compositeKind=None,
        interactable=True,
        supportedInteractions=["inspect"],
    )
    path = _write_catalog(tmp_path, _catalog([knife], fallback="PROP_KITCHEN_KNIFE_01"))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    joined = "\n".join(excinfo.value.issues)
    assert "category 'evidence'" in joined
    assert "interactable" in joined

    # Utility-but-interactable is still rejected...
    lamp = _asset(
        "PROP_LAMP_01",
        canonicalName="lamp",
        category="utility",
        subtype="prop",
        interactable=True,
    )
    path = _write_catalog(tmp_path, _catalog([lamp], fallback="PROP_LAMP_01"))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    assert any("interactable" in issue for issue in excinfo.value.issues)

    # ...and neutral-but-wrong-category is rejected too.
    vase = _asset(
        "PROP_VASE_01",
        canonicalName="vase",
        category="decor",
        subtype="vase",
        interactable=False,
    )
    path = _write_catalog(tmp_path, _catalog([vase], fallback="PROP_VASE_01"))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    assert any("category 'decor'" in issue for issue in excinfo.value.issues)


def test_neutral_fallback_still_loads(tmp_path):
    """The REAL fallback (utility, non-interactable) remains legal."""
    catalog = load_catalog(MANIFEST_PATH)
    fallback = catalog.by_id[catalog.fallback_asset]
    assert fallback.category == "utility"
    assert fallback.interactable is False


# --------------------------------------------------------------------------- #
# DEF-059 — compositeKind SYMMETRY: renderKind 'composite' only accepts the
# frontend-buildable vocabulary.
# --------------------------------------------------------------------------- #


def test_composite_kind_vocabulary_rejected(tmp_path):
    """QA repro: compositeKind 'chainsaw' is a load issue."""
    chainsaw = _asset(
        "PROP_CHAINSAW_01",
        canonicalName="chainsaw",
        category="utility",
        renderKind="composite",
        compositeKind="chainsaw",
    )
    path = _write_catalog(tmp_path, _catalog([chainsaw], fallback="PROP_CHAINSAW_01"))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    assert any(
        "compositeKind" in issue and "documented vocabulary" in issue
        for issue in excinfo.value.issues
    )


def test_composite_kind_vocabulary_all_six_buildable_kinds_accepted(tmp_path):
    """Every frontend-buildable composite kind still loads (with its frozen
    Phase 12 templateId)."""
    from app.assets.catalog import COMPOSITE_KIND_ALLOWLIST

    assert COMPOSITE_KIND_ALLOWLIST == (
        "kitchen_knife",
        "letter_opener",
        "scissors",
        "laptop",
        "victim",
        "table",
    )
    kind_to_template = {
        "kitchen_knife": "blade_chef",
        "letter_opener": "blade_letter",
        "scissors": "blades_scissor",
        "laptop": "tablet_flat",
        "victim": "bed_form",
        "table": "table_form",
    }
    for index, kind in enumerate(COMPOSITE_KIND_ALLOWLIST):
        entry = _asset(
            f"PROP_TEST_{index:02d}",
            canonicalName=f"test {kind}",
            category="utility",
            renderKind="composite",
            compositeKind=kind,
            templateId=kind_to_template[kind],
        )
        fallback = _asset("PROP_FALLBACK_X")
        path = _write_catalog(
            tmp_path, _catalog([entry, fallback]), name=f"c{index}.json"
        )
        assert load_catalog(path) is not None


# --------------------------------------------------------------------------- #
# DEF-060 — array-size bounds in the catalog manifest validator.
# --------------------------------------------------------------------------- #


def test_oversized_catalog_arrays_rejected(tmp_path):
    """QA repro: a 200-tag catalog is a load issue; every bounded array is."""
    from app.assets.catalog import (
        MAX_ALLOWED_ANCHORS,
        MAX_ALIASES,
        MAX_COLORS,
        MAX_EVIDENCE_CAPABILITIES,
        MAX_SUPPORTED_INTERACTIONS,
        MAX_TAGS,
    )

    oversized = _asset(tags=[f"t{i}" for i in range(MAX_TAGS + 1)])
    path = _write_catalog(tmp_path, _catalog([oversized]), name="tags.json")
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("tags: exceeds the maximum" in issue for issue in issues)

    oversized = _asset(aliases=[f"a{i}" for i in range(MAX_ALIASES + 1)])
    path = _write_catalog(tmp_path, _catalog([oversized]), name="aliases.json")
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("aliases: exceeds the maximum" in issue for issue in issues)

    oversized = _asset(supportedInteractions=[
        "inspect" for _ in range(MAX_SUPPORTED_INTERACTIONS + 1)
    ])
    path = _write_catalog(tmp_path, _catalog([oversized]), name="interactions.json")
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("supportedInteractions: exceeds the maximum" in issue for issue in issues)

    oversized = _asset(evidenceCapabilities=[
        "digital" for _ in range(MAX_EVIDENCE_CAPABILITIES + 1)
    ])
    path = _write_catalog(tmp_path, _catalog([oversized]), name="caps.json")
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("evidenceCapabilities: exceeds the maximum" in issue for issue in issues)

    oversized = _asset(allowedAnchors=["GENERIC_PROP" for _ in range(MAX_ALLOWED_ANCHORS + 1)])
    path = _write_catalog(tmp_path, _catalog([oversized]), name="anchors.json")
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("allowedAnchors: exceeds the maximum" in issue for issue in issues)

    oversized = _asset(colors={f"c{i}": "#8d8d93" for i in range(MAX_COLORS + 1)})
    path = _write_catalog(tmp_path, _catalog([oversized]), name="colors.json")
    issues = validate_catalog_data(json.loads(path.read_text(encoding="utf-8")))
    assert any("colors exceeds the maximum" in issue for issue in issues)


# --------------------------------------------------------------------------- #
# DEF-063 — CONSTRUCTOR INVARIANTS: direct Catalog(...) construction cannot
# bypass the cross-set/global validation.
# --------------------------------------------------------------------------- #


def _typed_asset(asset_id="PROP_X_01", **overrides):
    from types import MappingProxyType

    from app.assets.catalog import AssetDescriptor, Dimensions

    fields = dict(
        asset_id=asset_id,
        version=1,
        canonical_name=asset_id.lower().replace("_", " "),
        aliases=(),
        category="utility",
        subtype="prop",
        tags=(),
        render_kind="box",
        composite_kind=None,
        dimensions=Dimensions(x=0.5, y=0.5, z=0.5),
        colors=MappingProxyType({"body": "#8d8d93"}),
        label="Test object",
        interactable=False,
        supported_interactions=(),
        evidence_capabilities=(),
        allowed_anchors=("GENERIC_PROP",),
    )
    fields.update(overrides)
    return AssetDescriptor(**fields)


def test_catalog_constructor_rejects_shadowed_identity():
    """DEF-063: aliases may never shadow another asset's assetId even when the
    Catalog is built directly (the dataclass constructor is not bypassable)."""
    from app.assets.catalog import Catalog, CatalogValidationError

    a = _typed_asset("PROP_LAPTOP_01", canonical_name="laptop")
    b = _typed_asset("PROP_FAKE_01", canonical_name="imposter", aliases=("PROP_LAPTOP_01",))
    with pytest.raises(CatalogValidationError) as excinfo:
        Catalog(catalog_version=1, fallback_asset="PROP_LAPTOP_01", assets=(a, b))
    assert any("conflicting identities" in issue for issue in excinfo.value.issues)


def test_catalog_constructor_rejects_non_neutral_fallback():
    """DEF-063: an interactable/wrong-category fallback is rejected even when
    constructed directly."""
    from app.assets.catalog import Catalog, CatalogValidationError

    knife = _typed_asset(
        "PROP_KITCHEN_KNIFE_01",
        canonical_name="kitchen knife",
        category="evidence",
        interactable=True,
    )
    with pytest.raises(CatalogValidationError) as excinfo:
        Catalog(
            catalog_version=1,
            fallback_asset="PROP_KITCHEN_KNIFE_01",
            assets=(knife,),
        )
    joined = "\n".join(excinfo.value.issues)
    assert "category 'evidence'" in joined
    assert "interactable" in joined


def test_catalog_constructor_rejects_unknown_composite_kind():
    """DEF-063: an unknown compositeKind is rejected on direct construction."""
    from app.assets.catalog import Catalog, CatalogValidationError

    chainsaw = _typed_asset(
        "PROP_CHAINSAW_01",
        canonical_name="chainsaw",
        render_kind="composite",
        composite_kind="chainsaw",
    )
    with pytest.raises(CatalogValidationError) as excinfo:
        Catalog(
            catalog_version=1,
            fallback_asset="PROP_CHAINSAW_01",
            assets=(chainsaw,),
        )
    assert any("documented vocabulary" in issue for issue in excinfo.value.issues)


def test_catalog_constructor_rejects_oversized_arrays():
    """DEF-063: array-size bounds apply to hand-built descriptors too."""
    from app.assets.catalog import Catalog, CatalogValidationError

    big = _typed_asset("PROP_BIG_01", tags=tuple(f"t{i}" for i in range(200)))
    with pytest.raises(CatalogValidationError) as excinfo:
        Catalog(catalog_version=1, fallback_asset="PROP_BIG_01", assets=(big,))
    assert any("tags: exceeds the maximum" in issue for issue in excinfo.value.issues)


def test_catalog_constructor_accepts_valid_manifest_descriptors():
    """DEF-063: reconstructing the REAL manifest's descriptors directly is
    still legal (the constructor is an invariant gate, not a blocker)."""
    from app.assets.catalog import Catalog

    original = load_catalog(MANIFEST_PATH)
    rebuilt = Catalog(
        catalog_version=original.catalog_version,
        fallback_asset=original.fallback_asset,
        assets=original.assets,
    )
    assert rebuilt == original
    assert rebuilt.by_id.keys() == original.by_id.keys()


# --------------------------------------------------------------------------- #
# Phase 12 — 100-object showcase catalog contract
# --------------------------------------------------------------------------- #


def _walk_strings(node):
    """Yield every string (keys AND values) in a JSON tree."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from _walk_strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk_strings(value)


def test_phase12_catalog_contains_at_least_100_entries():
    """Required Phase 12 test: the catalog contains >= 100 distinct entries."""
    catalog = load_catalog(MANIFEST_PATH)
    ids = [a.asset_id for a in catalog.assets]
    assert len(ids) >= 100
    assert len(set(ids)) == len(ids)


def test_phase12_every_descriptor_validates_with_zero_issues():
    """Required Phase 12 test: every descriptor validates (zero load issues)."""
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert validate_catalog_data(raw) == ()
    assert load_catalog(MANIFEST_PATH) is not None


def test_phase12_every_composite_template_is_in_vocabulary_and_resolvable():
    """Required Phase 12 test: every composite render resource (templateId)
    exists in the frozen TEMPLATE_VOCABULARY, and every composite is
    data-wise resolvable through the resolver."""
    from app.assets.catalog import TEMPLATE_VOCABULARY
    from app.assets.resolver import AssetRequest, AssetResolver, Provenance

    catalog = load_catalog(MANIFEST_PATH)
    from app.assets import ASSET_VERSION_MIN  # noqa: F401 (smoke import)

    composites = [a for a in catalog.assets if a.render_kind == "composite"]
    assert len(composites) >= 40
    resolver = AssetResolver(catalog)
    for asset in composites:
        assert asset.template_id in TEMPLATE_VOCABULARY, asset.asset_id
        result = resolver.resolve_request(AssetRequest(requested_name=asset.asset_id))
        assert result.resolved and not result.ambiguous, asset.asset_id
        assert result.provenance in (
            Provenance.CATALOG_EXACT,
            Provenance.CATALOG_ALIAS,
        ), asset.asset_id


def test_phase12_no_duplicate_stable_ids_or_aliases():
    """Required Phase 12 test: no duplicate stable assetIds; no alias or
    canonical claim shares a normal-form identity with another asset."""
    catalog = load_catalog(MANIFEST_PATH)
    ids = [a.asset_id for a in catalog.assets]
    assert len(set(ids)) == len(ids)
    # The loader already rejects collisions; this asserts the real manifest
    # passes those cross-set identity checks with zero issues.
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    joined = "\n".join(validate_catalog_data(raw))
    assert "duplicate" not in joined
    assert "conflicting" not in joined


def test_phase12_no_forbidden_url_or_path_anywhere_including_variants():
    """Required Phase 12 test: no forbidden URL/path anywhere in the manifest
    (deep string scan over every key and value, including variant literals)."""
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    forbidden = (
        "http://",
        "https://",
        "data:",
        "file:",
        "javascript:",
        "/",
        "\\",
        "..",
    )
    for text in _walk_strings(raw):
        lowered = text.casefold()
        for token in forbidden:
            assert token not in lowered, f"forbidden token {token!r} in {text!r}"
        assert not text.startswith(("/", "\\")), text


def test_phase12_ten_untouched_legacy_entries_byte_identical():
    """The 10 non-composite Phase 10/11 entries keep their byte-stable shape:
    Phase 12 only ADDED the templateId/variants keys to the six composites."""
    from app.assets.catalog import REQUIRED_ASSET_KEYS

    legacy_non_composites = {
        "DOOR_APARTMENT_01",
        "PROP_LAMP_01",
        "PROP_VASE_01",
        "PROP_FALLBACK_01",
        "PROP_WINDOW_01",
        "PROP_WALL_01",
        "PROP_DESK_01",
        "PROP_HOTEL_BED_01",
        "PROP_WAREHOUSE_SHELF_01",
        "PROP_OFFICE_CHAIR_01",
    }
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    seen = set()
    for index, item in enumerate(raw["assets"]):
        if item["assetId"] in legacy_non_composites:
            seen.add(item["assetId"])
            # legacy non-composites: exactly the legacy key set (no new keys).
            assert set(item.keys()) == REQUIRED_ASSET_KEYS, (
                f"assets[{index}] {item['assetId']} changed shape: {sorted(item)}"
            )
        elif item["renderKind"] != "composite":
            # NEW non-composite entries: explicit null templateId + no variants.
            assert item.get("templateId") is None, item["assetId"]
            assert item.get("variants") in (None, []), item["assetId"]
    assert seen == legacy_non_composites


def test_phase12_category_distribution_targets():
    """The Phase 12 suggested distribution is covered by the manifest.

    Documents and forensic props live under the FROZEN 'evidence' category
    (the shared category vocabulary is frozen across the frontend contract and
    the environment kits), distinguished by subtype; the remaining category
    targets match exactly.
    """
    catalog = load_catalog(MANIFEST_PATH)
    from collections import Counter

    counts = Counter(a.category for a in catalog.assets)
    assert counts["furniture"] >= 20
    assert counts["electronics"] >= 10
    assert counts["structural"] >= 10
    assert counts["decor"] >= 15
    assert counts["utility"] >= 5  # generic fallbacks
    assert counts["evidence"] >= 40  # 20 weapon + 10 documents + 10 forensic
    by_subtype = Counter(a.subtype for a in catalog.assets if a.category == "evidence")
    assert by_subtype["document"] + by_subtype["record"] + by_subtype["notebook"] + by_subtype["folder"] + by_subtype["card"] + by_subtype["frame"] >= 10
    assert by_subtype["forensic"] >= 10


# --------------------------------------------------------------------------- #
# Phase 12 §Variant — bounded declarative variant SCHEMA validation
# --------------------------------------------------------------------------- #


def _variant_asset(**overrides) -> dict:
    base = _asset(
        "PROP_VARIANT_01",
        canonicalName="variant test object",
        category="utility",
        renderKind="composite",
        compositeKind=None,
        templateId="storage_box",
        variants=[],
    )
    base.update(overrides)
    return base


def test_variant_unknown_param_key_rejected(tmp_path):
    asset = _variant_asset(
        variants=[{"name": "bad", "params": {"texture": {"allowlist": ["grain"], "default": "grain"}}}]
    )
    issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
    assert any("unknown variant parameter 'texture'" in issue for issue in issues)


def test_variant_unknown_allowlist_member_rejected(tmp_path):
    asset = _variant_asset(
        variants=[{"name": "bad", "params": {"material": {"allowlist": ["obsidian"], "default": "obsidian"}}}]
    )
    issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
    assert any("not in MATERIAL_VOCABULARY" in issue for issue in issues)
    asset = _variant_asset(
        variants=[{"name": "bad", "params": {"state": {"allowlist": ["glowing"], "default": "glowing"}}}]
    )
    issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
    assert any("not in STATE_VOCABULARY" in issue for issue in issues)


def test_variant_out_of_range_scale_rejected(tmp_path):
    for bad_spec in (
        {"min": 0.1, "max": 1.0, "default": 0.5},   # min < 0.5
        {"min": 0.5, "max": 3.0, "default": 1.0},   # max > 2.0
        {"min": 1.0, "max": 0.5, "default": 0.7},   # min > max
        {"min": 0.8, "max": 1.2, "default": 2.0},   # default out of range
    ):
        asset = _variant_asset(variants=[{"name": "x", "params": {"scale": bad_spec}}])
        issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
        assert issues, f"scale spec {bad_spec!r} must be rejected"


def test_variant_non_hex_color_rejected(tmp_path):
    asset = _variant_asset(
        variants=[{"name": "bad", "params": {"color": {"allowlist": ["red"], "default": "red"}}}]
    )
    issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
    assert any("#RRGGBB" in issue for issue in issues)


def test_variant_unsafe_tokens_rejected(tmp_path):
    asset = _variant_asset(
        variants=[{"name": "bad", "params": {"material": {"allowlist": ["metal.steel"], "default": "https://evil.example/x"}}}]
    )
    issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
    assert any("URL scheme" in issue for issue in issues)


def test_variant_more_than_three_rejected(tmp_path):
    variants = [
        {"name": f"v{i}", "params": {"state": {"allowlist": ["clean"], "default": "clean"}}}
        for i in range(4)
    ]
    asset = _variant_asset(variants=variants)
    issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
    assert any("exceeds the maximum of 3 variants" in issue for issue in issues)


def test_variant_name_pattern_rejected(tmp_path):
    for bad_name in ("NotAllowed", "with space", "x" * 25, ""):
        asset = _variant_asset(variants=[{"name": bad_name, "params": {}}])
        issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
        assert issues, f"variant name {bad_name!r} must be rejected"


def test_variant_default_not_in_allowlist_rejected(tmp_path):
    asset = _variant_asset(
        variants=[{"name": "bad", "params": {"material": {"allowlist": ["metal.steel"], "default": "plastic"}}}]
    )
    issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
    assert any("must be present in the allowlist" in issue for issue in issues)


def test_variant_conflicting_specs_across_variants_rejected(tmp_path):
    asset = _variant_asset(
        variants=[
            {"name": "a", "params": {"material": {"allowlist": ["metal.steel"], "default": "metal.steel"}}},
            {"name": "b", "params": {"material": {"allowlist": ["plastic", "leather"], "default": "plastic"}}},
        ]
    )
    issues = validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")]))
    assert any("conflicts with an earlier variant" in issue for issue in issues)


def test_variant_all_params_null_is_valid_noop(tmp_path):
    asset = _variant_asset(
        variants=[{"name": "noop", "params": {"color": None, "material": None, "scale": None, "state": None}}]
    )
    assert validate_catalog_data(_catalog([asset, _asset("PROP_FALLBACK_X")])) == ()


def test_variant_typed_constructor_path_validates_variants():
    """DEF-063 parity: the typed constructor rejects a bad variant schema too."""
    from types import MappingProxyType

    from app.assets.catalog import AssetDescriptor, Dimensions, VariantParamSpec, VariantSpec

    bad_variant = VariantSpec(
        name="bad",
        params=MappingProxyType(
            {"material": VariantParamSpec(kind="material", allowlist=("obsidian",), default="obsidian")}
        ),
    )
    descriptor = AssetDescriptor(
        asset_id="PROP_TYPED_VARIANT_01",
        version=1,
        canonical_name="typed variant",
        aliases=(),
        category="utility",
        subtype="prop",
        tags=(),
        render_kind="composite",
        composite_kind=None,
        template_id="storage_box",
        dimensions=Dimensions(x=0.5, y=0.5, z=0.5),
        colors=MappingProxyType({"body": "#8d8d93"}),
        label="Typed variant",
        interactable=False,
        supported_interactions=(),
        evidence_capabilities=(),
        allowed_anchors=("GENERIC_PROP",),
        variants=(bad_variant,),
    )
    with pytest.raises(CatalogValidationError) as excinfo:
        Catalog(
            catalog_version=1,
            fallback_asset="PROP_TYPED_VARIANT_01",
            assets=(descriptor,),
        )
    joined = "\n".join(excinfo.value.issues)
    assert "MATERIAL_VOCABULARY" in joined


def test_phase12_render_kind_vocabulary_unchanged():
    """renderKind stays one of {box, cylinder, sphere, flat, composite}."""
    from app.assets.catalog import RENDER_KIND_ALLOWLIST

    catalog = load_catalog(MANIFEST_PATH)
    assert RENDER_KIND_ALLOWLIST == ("box", "cylinder", "sphere", "flat", "composite")
    for asset in catalog.assets:
        assert asset.render_kind in RENDER_KIND_ALLOWLIST
        assert asset.composite_kind is None or asset.composite_kind in (
            "kitchen_knife",
            "letter_opener",
            "scissors",
            "laptop",
            "victim",
            "table",
        )


# --------------------------------------------------------------------------- #
# DEF-067 — deep nesting bombs on the catalog load family (reject cleanly)
# --------------------------------------------------------------------------- #


def test_deeply_nested_catalog_file_rejected_cleanly(tmp_path):
    """DEF-067: a catalog manifest file nesting far deeper than
    ``MAX_STRUCT_NESTING`` is rejected with a clean ``CatalogError`` — never an
    uncaught ``RecursionError`` from ``json.loads``."""
    from app.assets.catalog import CatalogError

    deep: dict = {}
    cursor = deep
    for _ in range(300):
        cursor["a"] = {}
        cursor = cursor["a"]
    path = _write_catalog(tmp_path, deep)
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(path)
    assert "nesting" in str(excinfo.value)


def test_deeply_nested_catalog_data_returns_clean_issue():
    """DEF-067: ``validate_catalog_data`` on a deeply nested PYTHON structure
    returns a deterministic nesting issue — never a RecursionError."""
    deep: dict = {}
    cursor = deep
    for _ in range(300):
        cursor["a"] = {}
        cursor = cursor["a"]
    issues = validate_catalog_data(deep)
    assert any("nesting depth" in issue and "exceeds the maximum" in issue for issue in issues)
    # a normal manifest tree is unaffected by the guard.
    manifest = _valid_catalog()
    assert validate_catalog_data(manifest) == ()


# --------------------------------------------------------------------------- #
# DEF-068 — Unicode format/zero-width/Bidi/line-separator glyphs rejected
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "glyph,glyph_name",
    [
        ("\u200b", "U+200B"),  # zero-width space
        ("\u202e", "U+202E"),  # right-to-left override (bidi)
        ("\u2028", "U+2028"),  # line separator
        ("\ufeff", "U+FEFF"),  # byte-order mark
    ],
)
def test_unicode_format_control_glyphs_rejected(tmp_path, glyph, glyph_name):
    """DEF-068: U+200B / U+202E / U+2028 / U+FEFF in a catalog manifest string
    are deterministic load issues — the catalog string-safety scan rejects the
    whole glyph class (validator AND loader paths)."""
    poisoned = f"Name{glyph}Prop"
    assets = [_asset("PROP_GLYPH_01", canonicalName=poisoned), _asset("PROP_FALLBACK_X")]
    issues = validate_catalog_data(_catalog(assets))
    matches = [i for i in issues if "Unicode format/zero-width" in i and glyph_name in i]
    assert matches, f"expected a {glyph_name} glyph issue; got {issues}"
    # the loader path surfaces the same clean rejection.
    path = _write_catalog(tmp_path, _catalog(assets))
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog(path)
    assert any("Unicode format/zero-width" in issue for issue in excinfo.value.issues)