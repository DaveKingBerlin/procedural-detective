"""Phase 17 — Geometry Quality Validator & Self-Repair tests.

Covers the FULL Phase17 section-16 automated list as named tests, the four
showcase cases (A-D) through the REAL ``OllamaAssetSpecProvider`` /
``OllamaStageDriver`` with a mocked transport, determinism, immutability, the
section-12 internal metrics trace and the golden-fixture regression guarantee
(existing valid Phase 13 fixtures pass the geometry gate with ZERO issues — no
drift).

All transport interaction is mocked (the autouse ``_network_block`` here and in
``test_ollama_driver`` enforces zero real network/process/port use).
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.asset_specs import GOLDEN_SPEC_CONTENT, GOLDEN_SPEC_NAMES
from test_ollama_driver import _case_people, _evidence, _j, _run, _world

import app.assets.compiler as compiler_mod

from app.assets.compiler import (
    HITBOX_MAX,
    HITBOX_MIN,
    AssetSpecCompileError,
    asset_id_for,
    compile_asset_spec,
)
from app.assets.geometry_quality import (
    ALLOWED_MATERIALS,
    CLASSIFICATION_MAP,
    SINGLE_PART_DECLARED_MAX_RATIO,
    GeometryIssue,
    inspect_raw_spec_issues,
    is_handheld_object,
    normalized_category_subtype,
    validate_geometry,
)
from app.assets.spec_provider import AssetSpecRequest
from app.assets.specs import (
    MAX_PART_SCALE,
    MAX_POSITION_BOUND,
    MIN_PART_SCALE,
    AssetSpecError,
    AssetSpecPart,
    SpecTransform,
    parse_asset_spec,
    validate_asset_spec,
)
from app.generation import prompts
from app.generation.provider import ProviderResult
from app.services.ollama_driver import MAX_SPEC_REPAIR_PASSES, OllamaAssetSpecProvider


# --------------------------------------------------------------------------- #
# fixtures + helpers
# --------------------------------------------------------------------------- #


def _coherent(
    canonical="Bronze Ceremonial Ice Pick",
    category="decor",
    subtype="ceremonial_ice_pick",
    dims=(0.12, 0.5, 0.1),
    parts=None,
):
    """The coherent Phase-17-clean ice pick (decor: the placer's accepted
    procedural category; canonical name matches the app-normalized concept)."""
    if parts is None:
        parts = [
            {
                "id": "part_00",
                "role": "shaft",
                "primitive": "cylinder",
                "transform": {
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.05, "y": 0.18, "z": 0.05},
                },
                "material": "metal.brass",
            },
            {
                "id": "part_01",
                "role": "point",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": 0.2, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.05, "y": 0.05, "z": 0.05},
                },
                "material": "metal.brass",
            },
            {
                "id": "part_02",
                "role": "handle",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": -0.19, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.05, "y": 0.06, "z": 0.05},
                },
                "material": "wood.dark",
            },
        ]
    return {
        "canonicalName": canonical,
        "category": category,
        "subtype": subtype,
        "dimensions": {"x": float(dims[0]), "y": float(dims[1]), "z": float(dims[2])},
        "parts": parts,
    }


def _single_part(
    role="tip",
    primitive="sphere",
    position=(0.0, 0.0, 0.0),
    scale=(0.2, 0.2, 0.2),
    material="metal.brass",
    part_id="part_00",
):
    return {
        "id": part_id,
        "role": role,
        "primitive": primitive,
        "transform": {
            "position": {"x": position[0], "y": position[1], "z": position[2]},
            "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
            "scale": {"x": scale[0], "y": scale[1], "z": scale[2]},
        },
        "material": material,
    }


def _part_with(raw, index, **overrides):
    """Deep copy of one raw spec with a single part overridden."""
    out = copy.deepcopy(raw)
    part = out["parts"][index]
    for key, value in overrides.items():
        if key == "transform":
            part["transform"] = {**part["transform"], **value}
        else:
            part[key] = value
    return out


def _parsed(raw):
    return parse_asset_spec(raw, non_throwing=False)


def _issue_codes(raw):
    return {issue.code for issue in validate_geometry(_parsed(raw)).issues}


def _postscript(*specs):
    """Driver post list: case/evidence/world + the given spec responses."""
    return [_j(_case_people()), _j(_evidence()), _j(_world()), *[_j(s) for s in specs]]


class _ScriptedProvider:
    """Tiny deterministic provider adapter scripted with raw spec texts."""

    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        content = self.contents.pop(0) if self.contents else "<not-json>"
        return ProviderResult(content=content)


# --------------------------------------------------------------------------- #
# 4.1/4.2 declared-dimension plausibility / unit consistency
# --------------------------------------------------------------------------- #


def test_plausible_handheld_dimensions_pass():
    """section16-1: hand-held dimensions within 0.5 m (and no axis > 1.0 m) pass."""
    report = validate_geometry(_parsed(_coherent()))
    assert report.valid
    assert report.issues == ()
    assert report.metrics.silhouette_passed is True


def test_oversized_handheld_object_fails():
    """section16-2: a 2.5 m ice pick (within Phase 13's broad absolute bound) is
    still geometrically implausible and rejected."""
    report = validate_geometry(_parsed(_coherent(dims=(2.5, 0.5, 0.1))))
    assert not report.valid
    assert any(i.code == "DECLARED_DIMENSIONS_IMPLAUSIBLE" for i in report.issues)
    messages = [
        i.message
        for i in report.issues
        if i.code == "DECLARED_DIMENSIONS_IMPLAUSIBLE"
    ]
    assert any("METERS" in m or "0.25" in m or "meters" in m for m in messages)


def test_unit_confusion_25_vs_025_regression():
    """section16-3: '25' is unit confusion; a plausible ~25 cm object passes.
    Also guards the '10' vs '0.10' regression (section 4.2)."""
    # 25 m literal dimensions violate the AUTHORITATIVE Phase 13 absolute bound.
    assert validate_asset_spec(_coherent(dims=(25.0, 0.1, 0.1))) != ()
    # 10 m likewise leaves Phase 13 bounds (10-vs-0.10 regression).
    assert validate_asset_spec(_coherent(dims=(10, 0.1, 0.1))) != ()
    # within Phase 13, the geometry gate still refuses a 2.5 m ice pick:
    assert "DECLARED_DIMENSIONS_IMPLAUSIBLE" in _issue_codes(
        _coherent(dims=(2.5, 0.5, 0.1))
    )
    # a plausible ~25 cm-25 cm-10 cm object passes BOTH gates:
    spec = _coherent(dims=(0.25, 0.5, 0.1))
    assert validate_asset_spec(spec) == ()
    assert validate_geometry(_parsed(spec)).valid


# --------------------------------------------------------------------------- #
# 4.3 parts vs the declared envelope
# --------------------------------------------------------------------------- #


def test_part_inside_envelope_passes():
    """section16-4: inner parts pass the declared-envelope gate."""
    report = validate_geometry(_parsed(_coherent()))
    assert report.valid
    assert not any(i.code == "PART_OUTSIDE_DECLARED_BOUNDS" for i in report.issues)


def test_part_far_outside_declared_envelope_fails():
    """section16-5: [-4, 0, 0.05] inside a ~0.25 m object is PART_OUTSIDE."""
    bad = _coherent(dims=(0.06, 0.25, 0.06))
    bad = _part_with(bad, 1, transform={"position": {"x": -4.0, "y": 0.0, "z": 0.05}})
    report = validate_geometry(_parsed(bad))
    assert any(i.code == "PART_OUTSIDE_DECLARED_BOUNDS" for i in report.issues)
    assert any("envelope" in i.message for i in report.issues)


# --------------------------------------------------------------------------- #
# 4.4 / 4.5 / 4.6 composite bounds, degenerate layout, separation
# --------------------------------------------------------------------------- #


def test_composite_bounds_mismatch_rejected():
    """section 4.4: a multi-part object whose span contradicts the declared
    dimensions by > 3x is rejected."""
    bad = _coherent(dims=(0.12, 0.5, 0.1))
    bad = _part_with(bad, 1, transform={"position": {"x": 4.0, "y": 4.0, "z": 0.0}})
    report = validate_geometry(_parsed(bad))
    assert any(i.code == "COMPOSITE_BOUNDS_MISMATCH" for i in report.issues)


def test_all_parts_same_origin_fails():
    """section16-8: every part at [0,0,0] is a degenerate pile."""
    bad = _coherent()
    for p in bad["parts"]:
        p["transform"]["position"] = {"x": 0.0, "y": 0.0, "z": 0.0}
    codes = _issue_codes(bad)
    assert "DEGENERATE_PART_LAYOUT" in codes
    assert "SILHOUETTE_HEURISTIC" in codes  # no recognizable silhouette either


def test_near_identical_overlap_threshold_is_deterministic():
    """section16-9: 0.019 m separation is degenerate, 0.021 m is not; identical
    inputs ALWAYS produce the identical outcome."""

    def _two(separation):
        half = separation / 2.0
        return _coherent(
            canonical="Base Module",
            category="decor",
            subtype=None,
            dims=(0.5, 0.5, 0.5),
            parts=[
                _single_part(role="cell", position=(-half, 0.0, 0.0), scale=(0.4, 0.4, 0.4), part_id="part_00"),
                _single_part(role="cell", position=(half, 0.0, 0.0), scale=(0.4, 0.4, 0.4), part_id="part_01"),
            ],
        )

    near_codes = {i.code for i in validate_geometry(_parsed(_two(0.019))).issues}
    assert "DEGENERATE_PART_LAYOUT" in near_codes
    far_codes = {i.code for i in validate_geometry(_parsed(_two(0.021))).issues}
    assert "DEGENERATE_PART_LAYOUT" not in far_codes
    # determinism: identical inputs -> an identical issue set.
    again = validate_geometry(_parsed(_two(0.019)))
    assert {i.code for i in again.issues} == near_codes


def test_excessive_separation_fails():
    """section16-6: parts separated metres apart relative to a decimeters object."""
    bad = _coherent()
    bad = _part_with(bad, 2, transform={"position": {"x": 3.0, "y": 3.0, "z": 3.0}})
    assert "EXCESSIVE_PART_SEPARATION" in _issue_codes(bad)


def test_parent_child_distance_inconsistency_fails():
    """section16-7: a child 3.0 m from its parent inside a ~0.5 m object fails."""
    bad = _coherent()
    bad["parts"][1]["parentId"] = "part_00"
    bad = _part_with(bad, 1, transform={"position": {"x": 0.0, "y": 3.0, "z": 0.0}})
    codes = _issue_codes(bad)
    assert "PARENT_CHILD_SPATIAL_CONSISTENCY" in codes


def test_single_part_object_allowed():
    """section16-10: a valid single-part object passes (no stacking pile)."""
    spec = _coherent(
        canonical="Carved Oak Block",
        category="decor",
        subtype=None,
        dims=(0.3, 0.3, 0.3),
        parts=[
            _single_part(
                role="block", primitive="box", scale=(0.3, 0.3, 0.3), material="wood.dark"
            )
        ],
    )
    report = validate_geometry(_parsed(spec))
    assert report.valid
    assert report.metrics.part_count == 1


# --------------------------------------------------------------------------- #
# 4.8 visible extent
# --------------------------------------------------------------------------- #


def test_near_zero_visible_extent_fails():
    """section16-14: near-zero visible geometry fails even with a large hitbox."""
    bad = _coherent(dims=(0.2, 0.2, 0.2))
    for p in bad["parts"]:
        p["transform"]["position"] = {"x": 0.0, "y": 0.0, "z": 0.0}
        p["transform"]["scale"] = {"x": 0.05, "y": 0.05, "z": 0.05}
    codes = _issue_codes(bad)
    assert "VISUAL_EXTENT_TOO_SMALL" in codes


def test_interactive_object_retains_pickable_visible_geometry():
    """section16-15: a valid interactive object keeps visible parts AND a
    pickable bounded hitbox (direct-click geometry is preserved)."""
    definition = compile_asset_spec(_parsed(_coherent()))
    assert len(definition.parts) == 3
    hit = definition.hitbox.scale
    assert all(getattr(hit, axis) >= HITBOX_MIN for axis in ("x", "y", "z"))
    for part in definition.parts:
        assert MIN_PART_SCALE <= part.transform.scale.x <= MAX_PART_SCALE
        assert math.isfinite(part.transform.position.x)


# --------------------------------------------------------------------------- #
# 4.9 silhouette heuristic
# --------------------------------------------------------------------------- #


def test_simple_knife_like_role_layout_passes():
    """section16-16: a knife-like multi-part layout passes the silhouette gate."""
    report = validate_geometry(_parsed(_coherent(canonical="Wooden Kitchen Knife")))
    assert report.valid
    assert not any(i.code == "SILHOUETTE_HEURISTIC" for i in report.issues)


def test_ice_pick_like_role_layout_passes():
    """section16-17: the ice-pick-like layout (shaft/point/handle) passes."""
    assert validate_geometry(_parsed(_coherent())).valid


def test_single_sphere_ice_pick_fails_silhouette():
    """section16-18: a single-sphere 'ice pick' fails the silhouette quality check."""
    report = validate_geometry(
        _parsed(_coherent(parts=[_single_part(role="tip", primitive="sphere")]))
    )
    assert not report.valid
    assert any(i.code == "SILHOUETTE_HEURISTIC" for i in report.issues)


def test_unknown_generic_skips_silhouette_but_validates():
    """Unknown category/semantics skip the silhouette camera but still run
    through 4.1..4.8."""
    generic = _coherent(
        canonical="Abstract Rackett",
        category="utility",
        subtype=None,
        dims=(0.4, 0.4, 0.4),
        parts=[
            _single_part(
                role="base", primitive="box", position=(0.0, -0.1, 0.0),
                scale=(0.3, 0.08, 0.3), material="plastic", part_id="part_00",
            ),
            _single_part(
                role="cap", primitive="box", position=(0.0, 0.1, 0.0),
                scale=(0.2, 0.12, 0.2), material="plastic", part_id="part_01",
            ),
        ],
    )
    report = validate_geometry(_parsed(generic))
    assert report.valid
    assert report.metrics.silhouette_heuristic_applicable is False
    assert report.metrics.silhouette_passed is True


# --------------------------------------------------------------------------- #
# identifier/role grammar (REUSED from the Phase 13 source of truth) + materials
# --------------------------------------------------------------------------- #


def test_dash_identifier_rejected_as_structural():
    """section16-11: 'main-body' role fails the authoritative grammar as a
    STRUCTURAL_ERROR (Phase17 section 5 — no second grammar)."""
    bad = _part_with(_coherent(), 0, role="main-body")
    issues = inspect_raw_spec_issues(bad)
    hits = [i for i in issues if i.code == "INVALID_IDENTIFIER"]
    assert hits
    assert all(i.classification == "STRUCTURAL_ERROR" for i in hits)


def test_whitespace_role_rejected():
    """section16-12: 'cross guard' (space) is rejected."""
    bad = _part_with(_coherent(), 1, role="cross guard")
    issues = inspect_raw_spec_issues(bad)
    assert any(i.code == "INVALID_IDENTIFIER" for i in issues)


def test_underscore_identifier_accepted():
    """section16-13: underscore roles / part ids are accepted."""
    issues = inspect_raw_spec_issues(_coherent())
    assert issues == ()
    assert "INVALID_IDENTIFIER" not in {i.code for i in issues}


def test_uppercase_identifier_rejected():
    """'MAIN_BODY' is outside ^[a-z0-9_]+$ — rejected as STRUCTURAL_ERROR."""
    bad = _part_with(_coherent(), 0, role="MAIN_BODY")
    issues = inspect_raw_spec_issues(bad)
    assert any(i.code == "INVALID_IDENTIFIER" for i in issues)


def test_disallowed_material_surfaced_with_allowlist():
    """section 7: a disallowed material surfaces in the repair diagnostics with
    the sanitized allowlist copy (Phase17 section 7)."""
    bad = _part_with(_coherent(), 0, material="bronze")
    issues = inspect_raw_spec_issues(bad)
    hits = [i for i in issues if i.code == "MATERIAL_NOT_ALLOWED"]
    assert hits
    assert hits[0].allowed == ALLOWED_MATERIALS


# --------------------------------------------------------------------------- #
# section 8 classification + section 6 normalization
# --------------------------------------------------------------------------- #


def test_classification_mapping_exact():
    """The documented issue-code -> four-class taxonomy mapping is EXACT
    (Phase17 section 8)."""
    assert CLASSIFICATION_MAP == {
        "INVALID_IDENTIFIER": "STRUCTURAL_ERROR",
        "MATERIAL_NOT_ALLOWED": "STRUCTURAL_ERROR",
        "DECLARED_DIMENSIONS_IMPLAUSIBLE": "GEOMETRY_ERROR",
        "PART_OUTSIDE_DECLARED_BOUNDS": "GEOMETRY_ERROR",
        "COMPOSITE_BOUNDS_MISMATCH": "GEOMETRY_ERROR",
        "EXCESSIVE_PART_SEPARATION": "GEOMETRY_ERROR",
        "PARENT_CHILD_SPATIAL_CONSISTENCY": "GEOMETRY_ERROR",
        "VISUAL_EXTENT_TOO_SMALL": "QUALITY_ERROR",
        "DEGENERATE_PART_LAYOUT": "QUALITY_ERROR",
        "SILHOUETTE_HEURISTIC": "QUALITY_ERROR",
        "CRITICAL_CATEGORY_MISMATCH": "SEMANTIC_ERROR",
    }


def test_semantic_normalization_mapping():
    """section 6: 'ice picks' normalizes to the app vocabulary
    (application-owned mapping only)."""
    assert normalized_category_subtype("ice picks") == ("decor", "ceremonial_ice_pick")
    assert normalized_category_subtype("bronze ceremonial ice pick") == (
        "decor",
        "ceremonial_ice_pick",
    )
    assert normalized_category_subtype("kitchen knife") == ("decor", "kitchen_knife")
    assert normalized_category_subtype("utter nonsense") is None


def test_critical_category_mismatch_is_semantic_error():
    """section 6: changing the semantic class of a crime-critical request is a
    SEMANTIC_ERROR — never a silent substitution."""
    bad = _coherent(subtype="decorative_sword")
    report = validate_geometry(_parsed(bad), requested_name="bronze ceremonial ice pick")
    assert any(i.code == "CRITICAL_CATEGORY_MISMATCH" for i in report.issues)
    assert any(i.classification == "SEMANTIC_ERROR" for i in report.issues)


def test_mapped_concept_is_not_an_error():
    """A candidate whose category/subtype ALREADY matches the mapped canonical
    class produces no semantic issue."""
    report = validate_geometry(_parsed(_coherent()), requested_name="bronze ceremonial ice pick")
    assert report.valid
    assert not any(i.code == "CRITICAL_CATEGORY_MISMATCH" for i in report.issues)


# --------------------------------------------------------------------------- #
# determinism (section16-25/26/27)
# --------------------------------------------------------------------------- #


def test_same_spec_same_quality_result():
    """section16-25: same spec -> identical report (issues + metrics)."""
    spec = _parsed(_coherent())
    r1 = validate_geometry(spec)
    r2 = validate_geometry(spec)
    assert [(i.code, i.partId) for i in r1.issues] == [(i.code, i.partId) for i in r2.issues]
    assert r1.metrics == r2.metrics


def test_issue_ordering_deterministic():
    """section16-26: issues sorted by (code, partId); partId-None first."""
    bad = _coherent(dims=(2.5, 0.5, 0.1))
    bad = _part_with(bad, 1, transform={"position": {"x": -4.0, "y": 0.0, "z": 0.05}})
    report = validate_geometry(_parsed(bad))
    keys = [(i.code, i.partId or "") for i in report.issues]
    assert keys == sorted(keys)


def test_same_repaired_spec_same_proc_id():
    """section16-27: the same repaired spec -> the same content-addressed
    proc.* id, also through an identical driver publication."""
    spec_a = _parsed(_coherent())
    spec_b = _parsed(_coherent())
    assert asset_id_for(spec_a) == asset_id_for(spec_b)
    record, _t = _run([*_postscript(_coherent())])
    published = record.published.draft if record.published else record.draft
    proc = next(
        p.asset_id
        for p in published.world_graph.placements
        if p.asset_id.startswith("proc.")
    )
    assert proc == asset_id_for(spec_a)


def test_published_proc_object_immutable_across_runs():
    """section16-30 (data level): two identical runs publish byte-identical
    worlds (the repaired proc.* object is immutable across reloads)."""

    def _payload():
        record, _t = _run([*_postscript(_coherent())])
        return json.dumps(
            record.published.draft.to_dict()
            if hasattr(record.published.draft, "to_dict")
            else repr(record.published.draft),
            sort_keys=True,
        )

    assert _payload() == _payload()


# --------------------------------------------------------------------------- #
# golden fixtures regression (no drift)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", GOLDEN_SPEC_NAMES, ids=GOLDEN_SPEC_NAMES)
def test_golden_fixtures_pass_geometry_zero_issues(name):
    """A genuinely valid spec (the existing GOLDEN Phase 13 fixtures) passes the
    geometry gate with ZERO issues — no drift/regression on current fixtures."""
    raw = GOLDEN_SPEC_CONTENT[name.casefold().strip()]
    report = validate_geometry(_parsed(raw))
    assert report.valid, f"{name} geometry issues: {[i.code for i in report.issues]}"
    assert report.issues == ()


# --------------------------------------------------------------------------- #
# the repair contract (section16-19..24) + metrics (section12)
# --------------------------------------------------------------------------- #


def test_structured_diagnostics_generated():
    """section16-19: structured deterministic diagnostics are generated and
    carry the internal four-class taxonomy."""
    report = validate_geometry(_parsed(_coherent(dims=(2.5, 0.5, 0.1))))
    assert isinstance(report.issues, tuple)
    assert all(isinstance(i, GeometryIssue) for i in report.issues)
    assert all(i.code and i.classification and i.message for i in report.issues)


def test_repair_prompt_contains_authoritative_units_and_bounds():
    """section16-20: the driver repair prompt carries units + bounds + the Phase 17
    geometry instruction."""
    bad = _coherent(dims=(2.5, 0.5, 0.1))
    blob = prompts.build_asset_spec_repair_prompt(
        "bronze ceremonial ice pick",
        _j(bad),
        ("[GEOMETRY_ERROR DECLARED_DIMENSIONS_IMPLAUSIBLE]: declared width 2.5m exceeds 1.0m",),
    )
    assert "0.25 means 25 centimeters" in blob
    assert "25 means 25 meters" in blob
    assert "Geometry-quality instructions" in blob
    assert "declared object envelope" in blob
    assert "recognizable silhouette" in blob
    assert "part_00" in blob


def test_case_truth_absent_from_repair_prompt():
    """section16-21: CaseTruth / solver internals never reach the repair request."""
    blob = prompts.build_asset_spec_repair_prompt(
        "bronze ceremonial ice pick",
        _j(_coherent(dims=(2.5, 0.5, 0.1))),
        ("[GEOMETRY_ERROR DECLARED_DIMENSIONS_IMPLAUSIBLE]: implausible",),
    )
    for token in ("caseTruth", "solverProof", "murdererId", "crimeTime", "timeline", "relationships"):
        assert token not in blob


def test_valid_repaired_spec_accepted_through_driver():
    """section16-22: a broken-then-repaired spec is fully re-validated and
    published."""
    record, transport = _run([*_postscript(_coherent(dims=(2.5, 0.5, 0.1)), _coherent())])
    assert record.state.value == "PUBLISHED"
    assert transport.call_count == 5


def test_second_invalid_repair_still_rejected():
    """section16-23: a repair that stays invalid is REJECTED (never trusted
    incrementally; full validation runs after every repair)."""
    served = _ScriptedProvider(
        [
            _j(_coherent(dims=(2.5, 0.5, 0.1))),  # ASSET_SPEC (bad)
            _j(_coherent(dims=(1.9, 0.5, 0.1))),  # repair 1 (still bad)
            _j(_coherent(dims=(1.5, 0.5, 0.1))),  # repair 2 (still bad)
        ]
    )
    provider = OllamaAssetSpecProvider(
        provider=served, attempt_id="reject-test", budget_consumer=lambda: True
    )
    result = provider.generate(AssetSpecRequest(requested_name="bronze ceremonial ice pick"))
    assert result.error is not None
    assert served.calls == 1 + MAX_SPEC_REPAIR_PASSES  # 1 + 2


def test_repair_budget_enforced():
    """section16-24: bounded repair budget; exhaustion -> error, no unbounded
    loop, and the internal trace records the attempts."""
    served = _ScriptedProvider(
        [
            _j(_coherent(dims=(2.5, 0.5, 0.1))),
            _j(_coherent(dims=(1.9, 0.5, 0.1))),
            _j(_coherent(dims=(1.5, 0.5, 0.1))),
        ]
    )
    provider = OllamaAssetSpecProvider(
        provider=served, attempt_id="budget-test", budget_consumer=lambda: True
    )
    result = provider.generate(AssetSpecRequest(requested_name="bronze ceremonial ice pick"))
    assert result.error is not None
    assert served.calls == 1 + MAX_SPEC_REPAIR_PASSES
    assert provider.last_geometry_metrics["repairAttempts"] == MAX_SPEC_REPAIR_PASSES


def test_geometry_metrics_recorded_on_driver():
    """The driver records the internal (non-API, non-player-facing) metrics dict
    for both the repaired and the first-pass paths."""
    served = _ScriptedProvider(
        [_j(_coherent(dims=(2.5, 0.5, 0.1))), _j(_coherent())]
    )
    provider = OllamaAssetSpecProvider(
        provider=served, attempt_id="metric-test", budget_consumer=lambda: True
    )
    result = provider.generate(AssetSpecRequest(requested_name="bronze ceremonial ice pick"))
    assert result.error is None
    metrics = provider.last_geometry_metrics
    assert metrics is not None
    assert metrics["issueCountBeforeRepair"] == 2  # two implausible-dimension issues
    assert metrics["repairAttempts"] == 1
    assert metrics["finalPartCount"] == 3
    assert metrics["declaredDimensions"] == [0.12, 0.5, 0.1]
    assert metrics["silhouettePassed"] is True
    assert metrics["generatedOnFirstPass"] is False
    assert metrics["repaired"] is True
    # a fully valid spec records the first-pass success path:
    served2 = _ScriptedProvider([_j(_coherent())])
    provider2 = OllamaAssetSpecProvider(
        provider=served2, attempt_id="metric-first", budget_consumer=lambda: True
    )
    provider2.generate(AssetSpecRequest(requested_name="bronze ceremonial ice pick"))
    m2 = provider2.last_geometry_metrics
    assert m2["issueCountBeforeRepair"] == 0
    assert m2["generatedOnFirstPass"] is True
    assert m2["repaired"] is False


# --------------------------------------------------------------------------- #
# showcase cases A-D through the REAL driver + mocked Llama transport (section13)
# --------------------------------------------------------------------------- #


def test_showcase_A_unit_confusion_driver_repair():
    """Case A - unit confusion: {10,5,0.15} is rejected, the repair prompt
    restates meters, and the repaired ice pick publishes."""
    bad = _coherent(dims=(10, 5, 0.15))
    record, transport = _run([*_postscript(bad, _coherent())])
    assert record.state.value == "PUBLISHED"
    assert transport.call_count == 5
    repair_prompt = transport.prompt_of_call(4)
    assert "25 means 25 meters" in repair_prompt
    assert "Geometry-quality instructions" in repair_prompt
    proc = next(
        p
        for p in (record.published.draft if record.published else record.draft).world_graph.placements
        if p.asset_id.startswith("proc.")
    )
    defn = proc.generated_definition
    assert defn is not None
    dims = defn["dimensions"]
    assert all(0.05 <= v <= 0.5 for v in (dims["x"], dims["y"], dims["z"]))


def test_showcase_B_part_outside_driver_repair():
    """Case B - [-4,0,0.05] inside a ~0.25m object: PART_OUTSIDE detected and a
    repair brings the parts into a coherent envelope."""
    bad = _coherent(dims=(0.06, 0.25, 0.06))
    bad = _part_with(bad, 1, transform={"position": {"x": -4.0, "y": 0.0, "z": 0.05}})
    record, transport = _run([*_postscript(bad, _coherent())])
    assert record.state.value == "PUBLISHED"
    assert transport.call_count == 5
    repair_prompt = transport.prompt_of_call(4)
    assert "PART_OUTSIDE_DECLARED_BOUNDS" in repair_prompt
    assert "Geometry-quality instructions" in repair_prompt


def test_showcase_C_invalid_identifiers_driver_repair():
    """Case C - main-body / cross guard: grammar rejected, repaired to underscore
    identifiers."""
    bad = _coherent()
    bad = _part_with(bad, 0, role="main-body")
    bad = _part_with(bad, 1, role="cross guard")
    record, transport = _run([*_postscript(bad, _coherent())])
    assert record.state.value == "PUBLISHED"
    assert transport.call_count == 5
    repair_prompt = transport.prompt_of_call(4)
    assert "INVALID_IDENTIFIER" in repair_prompt
    assert "a-z0-9_" in repair_prompt


def test_showcase_D_degenerate_origin_driver_repair():
    """Case D - all parts [0,0,0]: DEGENERATE detected; repair produces a
    recognizable spatial layout."""
    bad = _coherent()
    for p in bad["parts"]:
        p["transform"]["position"] = {"x": 0.0, "y": 0.0, "z": 0.0}
    record, transport = _run([*_postscript(bad, _coherent())])
    assert record.state.value == "PUBLISHED"
    assert transport.call_count == 5
    repair_prompt = transport.prompt_of_call(4)
    assert "DEGENERATE_PART_LAYOUT" in repair_prompt
    assert "recognizable" in repair_prompt.lower()


# --------------------------------------------------------------------------- #
# section15 browser-validation support - data level (QA renders this)
# --------------------------------------------------------------------------- #


def test_repaired_proc_object_data_level_clickability():
    """section16-28/29 (data level): the repaired proc.* object renders with
    visible bounded finite parts and a pickable bounded hitbox (direct-click
    geometry), byte-identical to a fresh compile of the repaired spec."""
    record, _t = _run([*_postscript(_coherent(dims=(2.5, 0.5, 0.1)), _coherent())])
    assert record.state.value == "PUBLISHED"
    published = record.published.draft
    proc = next(p for p in published.world_graph.placements if p.asset_id.startswith("proc."))
    defn = proc.generated_definition
    assert defn is not None
    assert len(defn["parts"]) == 3
    for part in defn["parts"]:
        t = part["transform"]
        for axis in ("x", "y", "z"):
            assert math.isfinite(t["position"][axis])
            assert math.isfinite(t["scale"][axis])
            assert MIN_PART_SCALE <= t["scale"][axis] <= MAX_PART_SCALE
            assert abs(t["position"][axis]) <= MAX_POSITION_BOUND
    hit = defn["hitbox"]["scale"]
    assert all(hit[axis] >= HITBOX_MIN for axis in ("x", "y", "z"))
    assert all(hit[axis] <= HITBOX_MAX for axis in ("x", "y", "z"))
    # the published definition is the EXACT camelCase contract document: byte-
    # identical to a fresh compile of the repaired spec.
    expected = json.loads(compile_asset_spec(_parsed(_coherent())).to_json_bytes())
    assert dict(defn) == expected


# --------------------------------------------------------------------------- #
# DEF-076 — ASCII-only part-id grammar; the compiler never silently drops parts
# --------------------------------------------------------------------------- #


def _spec_with_part_ids(ids):
    """A Phase-13-valid multi-part raw spec using exactly ``ids``."""
    return _coherent(
        canonical="Multi Unit Rack",
        category="utility",
        subtype=None,
        dims=(0.6, 0.6, 0.6),
        parts=[
            {
                "id": pid,
                "role": "segment",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": float(index) * 0.05, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.1, "y": 0.1, "z": 0.1},
                },
                "material": "metal.steel",
            }
            for index, pid in enumerate(ids)
        ],
    )


def test_def076_fullwidth_part_id_rejected_at_parse():
    """DEF-076: 'part_０１' (Unicode fullwidth digits) must be rejected cleanly
    by the Phase 13 validator — never parsed, so it can never reach the
    compiler's ASCII part_00..part_23 sequence."""
    rejected_ids = ("part_０１", "part_0１", "part_１0")
    for bad_id in rejected_ids:
        raw = _spec_with_part_ids(["part_00", bad_id])
        issues = validate_asset_spec(raw)
        assert issues, f"fullwidth id {bad_id!r} must be rejected"
        assert any("ASCII" in issue or "part_id" in issue or "id" in issue for issue in issues)
        with pytest.raises(AssetSpecError):
            parse_asset_spec(raw, non_throwing=False)


def test_def076_fullwidth_id_clean_geometry_diagnostic():
    """DEF-076: the repair-diagnostics path surfaces a fullwidth id as a clean
    STRUCTURAL INVALID_IDENTIFIER (never a silent drop)."""
    bad = _spec_with_part_ids(["part_００"])
    issues = inspect_raw_spec_issues(bad)
    hits = [i for i in issues if i.code == "INVALID_IDENTIFIER"]
    assert hits
    assert hits[0].classification == "STRUCTURAL_ERROR"
    assert "ASCII" in hits[0].message


def test_def076_direct_construction_rejects_fullwidth_id():
    """DEF-076: even direct AssetSpecPart construction cannot smuggle a
    non-ASCII part id past the typed constructors."""
    with pytest.raises(AssetSpecError):
        AssetSpecPart(
            id="part_０１",
            role="shaft",
            primitive="cylinder",
            transform=SpecTransform(
                position=(0.0, 0.0, 0.0),
                rotation=(0.0, 0.0, 0.0),
                scale=(0.1, 0.1, 0.1),
            ),
            material="metal.brass",
        )


def test_def076_compiler_never_drops_parts(monkeypatch):
    """DEF-076: the compiler asserts the compiled part COUNT matches the spec
    and raises the typed AssetSpecCompileError instead of silently dropping a
    validated part (defense-in-depth guard, unit-tested deterministically)."""
    spec = _parsed(_coherent())
    definition = compile_asset_spec(spec)
    assert len(definition.parts) == len(spec.parts) == 3

    # Force the (now-unreachable-via-parse) collision: the fixed ASCII sequence
    # is missing part_05 while the validated spec declares it.
    monkeypatch.setattr(
        compiler_mod,
        "_PART_IDS",
        tuple(pid for pid in compiler_mod._PART_IDS if pid != "part_05"),
    )
    raw = _spec_with_part_ids([f"part_{i:02d}" for i in range(6)])
    assert validate_asset_spec(raw) == ()  # structurally valid
    with pytest.raises(AssetSpecCompileError) as excinfo:
        compile_asset_spec(_parsed(raw))
    error = excinfo.value
    assert isinstance(error, AssetSpecError)
    assert "refused to drop" in str(error) and "unmappable" in str(error)


# --------------------------------------------------------------------------- #
# DEF-077 — declared/visible consistency for single parts + bounded pick hitbox
# --------------------------------------------------------------------------- #


def _plinth():
    """The QA repro: a SINGLE-PART object declaring {3.9^3} around a 0.5 m box."""
    return _coherent(
        canonical="Granite Plinth",
        category="decor",
        subtype=None,
        dims=(3.9, 3.9, 3.9),
        parts=[
            _single_part(role="block", primitive="box", scale=(0.5, 0.5, 0.5), material="ceramic")
        ],
    )


def test_def077_single_part_plinth_fails_composite_gate():
    """DEF-077: a single-part object whose declared bbox exceeds ~2x its visible
    span fails the geometry gate (no giant-invisible-pickbox attack) while still
    being Phase-13 valid."""
    raw = _plinth()
    assert validate_asset_spec(raw) == ()
    report = validate_geometry(_parsed(raw))
    assert not report.valid
    assert any(i.code == "COMPOSITE_BOUNDS_MISMATCH" for i in report.issues)
    composite_messages = [
        i.message for i in report.issues if i.code == "COMPOSITE_BOUNDS_MISMATCH"
    ]
    assert any("3.9m" in m and "single-part" in m for m in composite_messages)


def test_def077_plinth_driver_repairs_or_fails_safely():
    """DEF-077: the QA repro reaches the bounded repair instead of publishing
    with a VALID geometry outcome; the repaired object passes the gate."""
    served = _ScriptedProvider([_j(_plinth()), _j(_coherent())])
    provider = OllamaAssetSpecProvider(
        provider=served, attempt_id="def077-plinth", budget_consumer=lambda: True
    )
    result = provider.generate(AssetSpecRequest(requested_name="bronze ceremonial ice pick"))
    assert result.error is None  # repaired, not published-invalid
    metrics = provider.last_geometry_metrics
    assert metrics["repaired"] is True
    assert metrics["issueCountBeforeRepair"] >= 3  # one per declared axis
    assert metrics["finalPartCount"] == 3


def test_def077_legitimate_single_part_passes_and_is_clickable():
    """DEF-077: a legitimate single-part object (declared ~1.5x visible span)
    passes the gate and keeps a pickable bounded hitbox."""
    raw = _coherent(
        canonical="Carved Block",
        category="decor",
        subtype=None,
        dims=(0.6, 0.6, 0.6),
        parts=[
            _single_part(role="block", primitive="box", scale=(0.4, 0.4, 0.4), material="wood.dark")
        ],
    )
    report = validate_geometry(_parsed(raw))
    assert report.valid
    assert all(
        v <= singular * SINGLE_PART_DECLARED_MAX_RATIO
        for singular, v in zip(report.metrics.span, raw["dimensions"].values())
    )
    definition = compile_asset_spec(_parsed(raw))
    hit = definition.hitbox.scale
    for axis in ("x", "y", "z"):
        assert getattr(hit, axis) >= HITBOX_MIN
        assert getattr(hit, axis) <= 0.8  # <= visible span * HITBOX_VISIBLE_MAX_RATIO


def test_def077_compiled_hitbox_never_exceeds_visible_span_ratio():
    """DEF-077 (defensive compiled bound): even if the geometry gate were
    bypassed, the compiled pick hitbox of the plinth is clamped to the visible
    span * HITBOX_VISIBLE_MAX_RATIO (floored at HITBOX_MIN) — a tiny visible
    mesh can never present a giant invisible pick target."""
    definition = compile_asset_spec(_parsed(_plinth()))
    hit = definition.hitbox.scale
    for axis in ("x", "y", "z"):
        assert pytest.approx(getattr(hit, axis), abs=1e-9) == 1.0  # 0.5 * 2.0
        assert HITBOX_MIN <= getattr(hit, axis) <= HITBOX_MAX


# --------------------------------------------------------------------------- #
# DEF-078 — "blade"/"cleaver" terms reach the hand-held / silhouette gate
# --------------------------------------------------------------------------- #


def test_def078_ritual_blade_hits_handheld_silhouette_gate():
    """DEF-078: a decorative 'Ritual Blade' that is a single sphere can no
    longer launder past the hand-held plausibility/silhouette gate."""
    raw = _coherent(
        canonical="Ritual Blade",
        category="decor",
        subtype=None,
        dims=(0.12, 0.5, 0.1),
        parts=[_single_part(role="blade", primitive="sphere", scale=(0.2, 0.2, 0.2))],
    )
    spec = _parsed(raw)
    assert is_handheld_object(spec) is True
    report = validate_geometry(spec)
    assert not report.valid
    assert any(i.code == "SILHOUETTE_HEURISTIC" for i in report.issues)


def test_def078_ritual_blade_driver_repairs():
    """DEF-078: the gated 'Ritual Blade' is repaired through the bounded driver
    path (never published invalid)."""
    bad = _coherent(
        canonical="Ritual Blade",
        category="decor",
        subtype=None,
        dims=(0.12, 0.5, 0.1),
        parts=[_single_part(role="blade", primitive="sphere", scale=(0.2, 0.2, 0.2))],
    )
    served = _ScriptedProvider([_j(bad), _j(_coherent())])
    provider = OllamaAssetSpecProvider(
        provider=served, attempt_id="def078-ritual-blade", budget_consumer=lambda: True
    )
    result = provider.generate(AssetSpecRequest(requested_name="Ritual Blade"))
    assert result.error is None
    assert provider.last_geometry_metrics["repaired"] is True


def test_def078_one_axis_stick_still_passes():
    """DEF-078: an innocent decor object ('One-Axis Stick') with no hand-held
    term keeps passing through the whole geometry gate."""
    raw = _coherent(
        canonical="One-Axis Stick",
        category="decor",
        subtype=None,
        dims=(0.3, 0.3, 0.3),
        parts=[
            _single_part(role="segment", primitive="box", position=(0.0, -0.05, 0.0), scale=(0.05, 0.06, 0.05), material="wood.light", part_id="part_00"),
            _single_part(role="segment", primitive="box", position=(0.0, 0.05, 0.0), scale=(0.05, 0.06, 0.05), material="wood.light", part_id="part_01"),
        ],
    )
    spec = _parsed(raw)
    assert is_handheld_object(spec) is False
    report = validate_geometry(spec)
    assert report.valid
    assert not any(i.code == "SILHOUETTE_HEURISTIC" for i in report.issues)


def test_def078_cleaver_normalization_maps_to_kitchen_knife():
    """DEF-078: 'cleaver' normalizes to the kitchen-knife class through the
    application-owned mapping (semantic class is never silently invented)."""
    assert normalized_category_subtype("cleaver") == ("decor", "kitchen_knife")
    assert normalized_category_subtype("cleavers") == ("decor", "kitchen_knife")
    # existing controls are unchanged:
    assert normalized_category_subtype("kitchen knife") == ("decor", "kitchen_knife")
    assert normalized_category_subtype("ice picks") == ("decor", "ceremonial_ice_pick")


@pytest.fixture(autouse=True)
def _network_block():
    """Enforce zero real network/process/port use while these tests run."""
    import socket

    original = socket.socket

    def _deny(*args, **kwargs):
        raise RuntimeError("network access blocked during ollama driver tests")

    socket.socket = _deny
    yield
    socket.socket = original