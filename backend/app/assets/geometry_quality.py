"""Phase 17 — deterministic Geometry Quality Validator (procedural AssetSpecs).

``schema-valid != geometrically-valid``.

Local models (e.g. ``llama3.2:1b``) may return AssetSpecs that pass the Phase 13
schema/security validator but are geometrically implausible (25 meters vs
25 centimeters, parts parked meters away from a tiny object, all parts stacked
at the same origin, near-zero visible extent, unusable silhouettes). This module
owns the deterministic geometry-quality gate:

    LLM AssetSpec  -> strict Phase 13 parse/validation
                   -> validate_geometry (THIS module)   [PASS] -> trusted compiler
                                                       [FAIL] -> structured diagnostics
                                                              -> bounded LLM repair
                                                              -> BOTH validations again

The geometry layer ONLY ADDS rejection on top of Phase 13: every authoritative
Phase 13 bound (``app.assets.specs``) stays untouched and is never weakened.

Deterministic contract:

- ``validate_geometry(spec: AssetSpec) -> GeometryReport`` returns:
  - ``valid`` (bool);
  - ``issues`` — deterministic, sorted by ``(code, partId)`` (None orders as "");
  - ``metrics`` (``GeometryMetrics``): declared dims, part count, estimated
    composite bounding box, spans and silhouette state.
- ``GeometryIssue`` carries the four-class taxonomy (internal; the mapping table
  is documented in ``CLASSIFICATION_MAP`` — phase §8): ``STRUCTURAL_ERROR`` /
  ``GEOMETRY_ERROR`` / ``QUALITY_ERROR`` / ``SEMANTIC_ERROR``.

Required checks (numeric tolerances live as named constants below):

- 4.1/4.2  DECLARED_DIMENSIONS_IMPLAUSIBLE — hand-held plausibility + unit
  consistency (a bounded documented set; unknown objects get ONLY the generic
  absolute Phase 13 bounds, never a broad taxonomy);
- 4.3      PART_OUTSIDE_DECLARED_BOUNDS — conservative declared envelope;
- 4.4      COMPOSITE_BOUNDS_MISMATCH — estimated composite bbox vs declared
  (major contradiction: span > declared*3 or < declared/3 multi-part);
- 4.5      DEGENERATE_PART_LAYOUT — same-origin / near-identical stacking;
- 4.6      EXCESSIVE_PART_SEPARATION — parts beyond DECLARED*3 of the centroid;
- 4.7      PARENT_CHILD_SPATIAL_CONSISTENCY — coarse parent/child proximity;
- 4.8      VISUAL_EXTENT_TOO_SMALL — near-zero visible footprint/volume;
- 4.9      SILHOUETTE_HEURISTIC — hand-held objects need >= 2 separated parts;
- INVALID_IDENTIFIER — per-part id/role re-checked against the AUTHORITATIVE
  Phase 13 grammars (``app.assets.specs.PART_ID_PATTERN`` / ``ROLE_PATTERN``);
- MATERIAL_NOT_ALLOWED — structured structural diagnostic carrying the sanitized
  allowlist copy (phase §7; the AssetSpec validator already rejects — this layer
  surfaces it in repair diagnostics too).

Module-level helpers used by the driver/repair layer:

- ``is_handheld_object`` / ``silhouette_relevant`` (bounded category/subtype/name
  sets — no broad taxonomies);
- ``inspect_raw_spec_issues(raw)`` — structured repair diagnostics for a RAW
  possibly-Phase-13-invalid spec (identifier grammar + material allowlist);
- ``normalized_category_subtype(key)`` — the phase §6 application-owned mapping.

No network, no I/O; deterministic and pure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from app.assets.depthguard import BoundedJsonError, bounded_json_loads
from app.assets.materials import MATERIAL_VOCAB
from app.assets.specs import (
    AssetSpec,
    PART_ID_PATTERN,
    ROLE_PATTERN,
)

# --------------------------------------------------------------------------- #
# frozen geometry-quality bounds (named constants — the ONLY numeric sources)
# --------------------------------------------------------------------------- #

# 4.1/4.9 — documented hand-held sets (bounded, app-owned; NO broad taxonomy).
# Categories that, alone, make an object read as a hand-held-class item.
HANDHELD_CATEGORY_TAGS: frozenset[str] = frozenset({"evidence", "utility"})
# Sharp / weapon-like subtypes under ``category == "evidence"`` (or hand-held
# utility) that must read like a hand-held object.
HANDHELD_SUBTYPE_TAGS: frozenset[str] = frozenset(
    {
        "sharp_weapon",
        "ceremonial_ice_pick",
        "ceremonial_letter_opener",
        "kitchen_knife",
        "letter_opener",
        "ice_pick",
        "scissors",
        "scalpel",
        "dagger",
        "pocket_knife",
    }
)
# Canonical-name terms (casefolded, substring match) that identify a hand-held
# object even when its category reads decorative. DEF-078: "blade" / "cleaver"
# are added so a decorative "Ritual Blade" (or cleaver) cannot launder past the
# hand-held plausibility/silhouette gate; bounds stay finite and app-owned.
HANDHELD_CANONICAL_TERMS: tuple[str, ...] = (
    "ice pick",
    "letter opener",
    "knife",
    "scissors",
    "wrench",
    "hammer",
    "screwdriver",
    "razor",
    "scalpel",
    "dagger",
    "stiletto",
    "poker",
    "corkscrew",
    "blade",
    "cleaver",
)

# 4.1/4.2 hand-held plausibility: overall extent <= 0.5 m and no single dimension
# beyond 1.0 m (the "25 meters vs 0.25 meters" unit-confusion plausibility). The
# authoritative Phase 13 0.05..4 absolute bound stays in force for every object;
# this layer is strictly additive.
HANDHELD_MAX_DIMENSION = 0.5
HANDHELD_MAX_SINGLE_DIMENSION = 1.0

# 4.3 conservative allowed envelope beyond the declared half-dimensions (metres).
# Calibrated against the accepted golden composites (the trophy's parented stem
# legitimately pokes ~0.18 m past the declared half-extent), while a part parked
# metres away from a decimeters object is always rejected.
ENVELOPE_TOLERANCE = 0.2
# 4.4 composite-bounds: declared * (1 + COMPOSITE_TOLERANCE) is the nominal max
# span; a MAJOR contradiction (span > declared * COMPOSITE_MAJOR_FACTOR, or
# span < declared / COMPOSITE_MAJOR_FACTOR for multi-part objects) is rejected.
COMPOSITE_TOLERANCE = 0.5
COMPOSITE_MAJOR_FACTOR = 3.0
# DEF-077: for SINGLE-PART objects, each declared axis may never exceed the
# estimated visible span by more than this ratio (a single-part plinth declared
# {3.9,3.9,3.9} around a 0.5 m mesh would otherwise present a giant invisible
# pick target). Multi-part objects keep the existing *3 rules.
SINGLE_PART_DECLARED_MAX_RATIO = 2.0
# 4.5 degenerate stacking: pairwise center distance below DEGENERATE_DISTANCE for
# at least ceil(DEGENERATE_PART_FRACTION * N) parts, measured between parts that
# PHYSICALLY overlap by at least DEGENERATE_OVERLAP_FRACTION of the smaller part's
# volume (a plaque laid on its base keeps its distinct footprint; a same-origin
# pile of overlapping parts has no recognizable silhouette and is rejected).
DEGENERATE_DISTANCE = 0.02
DEGENERATE_PART_FRACTION = 0.6
DEGENERATE_OVERLAP_FRACTION = 0.6
# 4.6 excessive separation: |part - centroid| must stay within declared * F.
EXCESSIVE_SEPARATION_FACTOR = 3.0
# 4.7 parent/child coarse rule: distance from a child center to its parent center
# may not exceed max(0.4 * declared max dim, 0.05) PLUS the child's own half
# diagonal extent (a child attached to the far end of a parent still belongs to
# its object). A 3 m child inside a 0.3 m object is always rejected.
PARENT_CHILD_DISTANCE_FRACTION = 0.4
PARENT_CHILD_DISTANCE_MIN = 0.05
# 4.8 visible extent / volume gates. Physical-meter semantics (Phase17D B)
# make honest thin objects (ice picks, letter openers, blades) legitimately
# thin on ONE axis as a fraction of their overall size, so the gate is now
# SHAPE-AWARE instead of a coarse absolute-volume floor:
#   - the object must reach MIN_VISIBLE_EXTENT (>= 2 cm) in its LONGEST span
#     (anything smaller than that everywhere is not a usable interactive
#     object);
#   - an object whose composite collapses below MIN_VISIBLE_AXIS (6 cm) on
#     EVERY axis is a near-zero/collapsed clump (the old "~0.05 m clump")
#     and is rejected even when declared dimensions are generous.
# A thin-but-long object (blade/ice pick) is never "near-zero": one (or two)
# thin axes are physically realistic; only omnidirectional collapse fails.
MIN_VISIBLE_EXTENT = 0.02
MIN_VISIBLE_AXIS = 0.06
# 4.9 silhouette separation along one axis after parent-unwind.
SILHOUETTE_SEPARATION = 0.05

# phase §6 — application-owned semantic normalization (never silently
# substituted). Targets stay inside the Phase 13 catalog
# (CATEGORY_SPEC_ALLOWLIST: evidence, electronics, furniture, structural,
# character, decor, utility) AND inside the categories the world placer can
# actually anchor procedural assets to (procedural ``decor`` objects are the
# project's accepted showcase vocabulary for ceremonial props). A mapping is
# applied ONLY through the repair layer diagnostics; when the candidate's
# declared category/subtype would change the semantic class of a
# crime-critical request, the validator emits CRITICAL_CATEGORY_MISMATCH
# (SEMANTIC_ERROR) — never a silent substitution. DEF-078: "cleaver" maps to
# the kitchen-knife class; "blade" terms stay gate-only (no safe single target).
CATEGORY_SUBTYPE_NORMALIZATION: Mapping[str, tuple[str, str]] = {
    "ice pick": ("decor", "ceremonial_ice_pick"),
    "ice picks": ("decor", "ceremonial_ice_pick"),
    "ceremonial ice pick": ("decor", "ceremonial_ice_pick"),
    "bronze ceremonial ice pick": ("decor", "ceremonial_ice_pick"),
    "letter opener": ("decor", "ceremonial_letter_opener"),
    "kitchen knife": ("decor", "kitchen_knife"),
    "scissors": ("decor", "scissors"),
    "cleaver": ("decor", "kitchen_knife"),
    "cleavers": ("decor", "kitchen_knife"),
}

# phase §8 — documented issue-code -> four-class taxonomy mapping.
CLASSIFICATION_MAP: Mapping[str, str] = {
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

# Sanitized allowlist copy surfaced with MATERIAL_NOT_ALLOWED issues (phase §7).
ALLOWED_MATERIALS: tuple[str, ...] = tuple(sorted(MATERIAL_VOCAB))


# --------------------------------------------------------------------------- #
# typed shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GeometryIssue:
    """One deterministic structured quality diagnostic (internal, sanitized).

    ``allowed`` carries the sanitized allowlist copy for MATERIAL_NOT_ALLOWED
    (empty tuple otherwise). Messages and the allowlist are safe to render in a
    repair request; nothing else leaks.
    """

    code: str
    classification: str
    message: str
    partId: str | None = None
    allowed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.allowed:
            object.__setattr__(self, "allowed", tuple(sorted(set(self.allowed))))

    @property
    def part_id(self) -> str | None:
        return self.partId


@dataclass(frozen=True)
class GeometryMetrics:
    """Internal per-AssetSpec quality metrics (never player-facing)."""

    declared_dimensions: tuple[float, float, float]
    part_count: int
    estimated_bounding_box: tuple[
        tuple[float, float, float], tuple[float, float, float]
    ]
    span: tuple[float, float, float]
    silhouette_heuristic_applicable: bool
    silhouette_heuristic_passed: bool

    @property
    def silhouette_passed(self) -> bool:
        if not self.silhouette_heuristic_applicable:
            return True
        return self.silhouette_heuristic_passed


@dataclass(frozen=True)
class GeometryReport:
    """The deterministic validator outcome."""

    valid: bool
    issues: tuple[GeometryIssue, ...]
    metrics: GeometryMetrics

    def __post_init__(self) -> None:
        if self.valid and self.issues:
            raise ValueError("a valid report must carry zero issues")


# --------------------------------------------------------------------------- #
# classification helpers (bounded, deterministic)
# --------------------------------------------------------------------------- #


def is_handheld_object(spec: AssetSpec) -> bool:
    """True when the object belongs to the documented small hand-held set.

    Only ``category in HANDHELD_CATEGORY_TAGS`` with a sharp/weapon subtype, or a
    canonical-name term match, counts. Unknown categories are NOT classified
    hand-held — they keep generic absolute bounds only.
    """
    if spec.category in HANDHELD_CATEGORY_TAGS:
        subtype = (spec.subtype or "").casefold().replace("_", " ").strip()
        if any(tag.replace("_", " ") in subtype for tag in HANDHELD_SUBTYPE_TAGS):
            return True
    name = (spec.canonical_name or "").casefold()
    return any(term in name for term in HANDHELD_CANONICAL_TERMS)


def silhouette_relevant(spec: AssetSpec) -> bool:
    """4.9 applicability: evidence objects with a sharp/weapon subtype, or a
    canonical-name term matching the documented hand-held set."""
    if spec.category == "evidence":
        subtype = (spec.subtype or "").casefold().replace("_", " ").strip()
        if any(tag.replace("_", " ") in subtype for tag in HANDHELD_SUBTYPE_TAGS):
            return True
    name = (spec.canonical_name or "").casefold()
    return any(term in name for term in HANDHELD_CANONICAL_TERMS)


def _normalized_key(key: str) -> str:
    """Deterministic normal form for the phase §6 mapping keys."""
    return " ".join(key.casefold().replace("_", " ").split())


def normalized_category_subtype(key: str | None) -> tuple[str, str] | None:
    """phase §6 application-owned mapping (never a silent substitution on its own).

    Returns the canonical ``(category, subtype)`` for a matched key, else None.
    """
    if not isinstance(key, str) or not key.strip():
        return None
    return CATEGORY_SUBTYPE_NORMALIZATION.get(_normalized_key(key))


def _world_positions(spec: AssetSpec) -> list[list[float]]:
    """Per-part world position after parent-unwind (parent offsets summed)."""
    by_id = {part.id: part for part in spec.parts}
    out: list[list[float]] = []
    for part in spec.parts:
        pos = [float(v) for v in part.transform.position]
        probe = part
        seen: set[str] = set()
        while probe.parent_id is not None:
            if probe.parent_id in seen:
                break  # cycle guard (unreachable under Phase 13 ordering rules)
            seen.add(probe.parent_id)
            parent = by_id[probe.parent_id]
            pos = [pos[i] + float(parent.transform.position[i]) for i in range(3)]
            probe = parent
        out.append(pos)
    return out


def _composite_metrics(
    spec: AssetSpec, positions: list[list[float]]
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    """(min, max, span) corner tuples of the composite bounding box (parts at
    position +/- scale/2 on every axis, after parent-unwind)."""
    mins = [math.inf, math.inf, math.inf]
    maxs = [-math.inf, -math.inf, -math.inf]
    for part, pos in zip(spec.parts, positions):
        scale = part.transform.scale
        for axis in range(3):
            half = float(scale[axis]) / 2.0
            mins[axis] = min(mins[axis], pos[axis] - half)
            maxs[axis] = max(maxs[axis], pos[axis] + half)
    span = tuple(maxs[a] - mins[a] for a in range(3))
    mins_tuple = (float(mins[0]), float(mins[1]), float(mins[2]))
    maxs_tuple = (float(maxs[0]), float(maxs[1]), float(maxs[2]))
    return mins_tuple, maxs_tuple, span


def _part_boxes(
    spec: AssetSpec, positions: list[list[float]]
) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """Axis-aligned per-part world boxes (position +/- scale/2 per axis)."""
    boxes: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    for part, pos in zip(spec.parts, positions):
        scale = part.transform.scale
        box = tuple(
            (pos[a] - float(scale[a]) / 2.0, pos[a] + float(scale[a]) / 2.0)
            for a in range(3)
        )
        boxes.append(box)  # type: ignore[arg-type]
    return boxes


def _boxes_physically_overlap(
    box_a: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    box_b: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
) -> bool:
    """True when two aligned boxes overlap by at least DEGENERATE_OVERLAP_FRACTION
    of the smaller part's volume (a same-origin pile, not a distinct footprint)."""
    inter: float = 1.0
    vol_a: float = 1.0
    vol_b: float = 1.0
    for axis in range(3):
        lo = max(box_a[axis][0], box_b[axis][0])
        hi = min(box_a[axis][1], box_b[axis][1])
        inter *= max(0.0, hi - lo)
        vol_a *= box_a[axis][1] - box_a[axis][0]
        vol_b *= box_b[axis][1] - box_b[axis][0]
    if inter <= 0.0 or min(vol_a, vol_b) <= 0.0:
        return False
    return inter / min(vol_a, vol_b) >= DEGENERATE_OVERLAP_FRACTION


def _pairwise_close_count(
    positions: list[list[float]], boxes: list[Any]
) -> int:
    """Count of parts that sit within DEGENERATE_DISTANCE of ANY other part AND
    physically overlap it by at least DEGENERATE_OVERLAP_FRACTION (deterministic)."""
    close: set[int] = set()
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            dist = math.sqrt(
                sum((positions[i][a] - positions[j][a]) ** 2 for a in range(3))
            )
            if dist < DEGENERATE_DISTANCE and _boxes_physically_overlap(
                boxes[i], boxes[j]
            ):
                close.add(i)
                close.add(j)
    return len(close)


def _has_two_separated_parts(positions: list[list[float]]) -> bool:
    """4.9: at least TWO distinct parts whose positions differ by more than
    SILHOUETTE_SEPARATION along at least one axis after parent-unwind."""
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            for a in range(3):
                if abs(positions[i][a] - positions[j][a]) > SILHOUETTE_SEPARATION:
                    return True
    return False


def _semantic_normalization_issue(
    spec: AssetSpec, requested_name: str | None
) -> GeometryIssue | None:
    """phase §6: a crime-critical concept whose canonical class differs from the
    declared category/subtype is a SEMANTIC_ERROR (never silently substituted)."""
    concept = requested_name or spec.canonical_name
    canonical = normalized_category_subtype(concept if isinstance(concept, str) else None)
    if canonical is None:
        return None
    canonical_category, canonical_subtype = canonical
    declared_subtype = (spec.subtype or "").casefold()
    if spec.category != canonical_category or declared_subtype != canonical_subtype.casefold():
        return GeometryIssue(
            code="CRITICAL_CATEGORY_MISMATCH",
            classification=CLASSIFICATION_MAP["CRITICAL_CATEGORY_MISMATCH"],
            partId=None,
            message=(
                f"the declared category/subtype ({spec.category!r}/{spec.subtype!r}) "
                f"would change the semantic class of the crime-critical request "
                f"{concept!r}; use category {canonical_category!r} subtype "
                f"{canonical_subtype!r} — no silent substitution"
            ),
        )
    return None


# --------------------------------------------------------------------------- #
# the validator
# --------------------------------------------------------------------------- #


def validate_geometry(
    spec: AssetSpec,
    *,
    requested_name: str | None = None,
) -> GeometryReport:
    """Deterministic geometry-quality gate over a PARSED, Phase 13-valid spec.

    Issues are sorted by ``(code, partId)`` so the SAME spec ALWAYS yields the
    same tuple (determinism). ``requested_name`` is the original prompt concept
    used for the phase §6 semantic-classification check; the validator NEVER
    mutates the spec and NEVER substitutes categories.
    """
    if not isinstance(spec, AssetSpec):
        raise TypeError(
            "validate_geometry requires an AssetSpec (already Phase 13-validated)"
        )
    classification = dict(CLASSIFICATION_MAP)

    def _geom(code: str, message: str, part_id: str | None = None) -> GeometryIssue:
        return GeometryIssue(
            code=code,
            classification=classification[code],
            message=message,
            partId=part_id,
        )

    issues: list[GeometryIssue] = []
    dimensions = tuple(float(v) for v in spec.dimensions)
    part_count = len(spec.parts)
    positions = _world_positions(spec)
    boxes = _part_boxes(spec, positions)
    mins, maxs, span = _composite_metrics(spec, positions)
    handheld = is_handheld_object(spec)

    # ---- 4.1/4.2 declared-dimension plausibility + unit consistency ----------
    if handheld:
        max_dim = max(dimensions)
        if max_dim > HANDHELD_MAX_DIMENSION:
            issues.append(
                _geom(
                    "DECLARED_DIMENSIONS_IMPLAUSIBLE",
                    f"declared maximum dimension {max_dim:g}m is inconsistent with "
                    f"a hand-held object (must be <= {HANDHELD_MAX_DIMENSION:g}m)",
                )
            )
        for axis, value in zip(("width", "height", "depth"), dimensions):
            if value > HANDHELD_MAX_SINGLE_DIMENSION:
                issues.append(
                    _geom(
                        "DECLARED_DIMENSIONS_IMPLAUSIBLE",
                        f"declared {axis} {value:g}m exceeds "
                        f"{HANDHELD_MAX_SINGLE_DIMENSION:g}m — dimensions are in "
                        "METERS (0.25 means 25cm, 25 means 25m); this is "
                        "implausible for a hand-held object",
                    )
                )

    # ---- 4.3 PART_OUTSIDE_DECLARED_BOUNDS -----------------------------------
    envelope = [dimensions[axis] / 2.0 + ENVELOPE_TOLERANCE for axis in range(3)]
    for part, pos in zip(spec.parts, positions):
        scale = part.transform.scale
        for axis, axis_name in enumerate(("x", "y", "z")):
            offset = abs(pos[axis]) + float(scale[axis]) / 2.0
            if offset > envelope[axis]:
                issues.append(
                    _geom(
                        "PART_OUTSIDE_DECLARED_BOUNDS",
                        f"part position {pos[axis]:g} on {axis_name} exceeds the "
                        f"declared object envelope (allowed |v| <= "
                        f"{envelope[axis]:g}m including the part's own half-scale)",
                        part_id=part.id,
                    )
                )
                break  # one issue per part is enough (the strongest axis)

    # ---- 4.4 COMPOSITE_BOUNDS_MISMATCH --------------------------------------
    if part_count > 0:
        for axis, axis_name in enumerate(("width", "height", "depth")):
            declared = dimensions[axis]
            # 4.4a — an axis whose estimated span exceeds declared*3 always
            # contradicts the declared dimensions (per-axis upper rule).
            if span[axis] > declared * COMPOSITE_MAJOR_FACTOR:
                issues.append(
                    _geom(
                        "COMPOSITE_BOUNDS_MISMATCH",
                        f"estimated composite span {span[axis]:g}m on "
                        f"{axis_name} contradicts the declared dimension "
                        f"{declared:g}m (span > declared*{COMPOSITE_MAJOR_FACTOR:g})",
                    )
                )
        # 4.4b — for a multi-part object, a declaration whose EVERY axis span is
        # smaller than a third of its declared dimension grossly overstates the
        # object (the "massive declared dimensions still within broad absolute
        # bounds" adversarial case). A single thin axis (e.g. a flat rack or a
        # coin) is NOT a contradiction.
        if part_count > 1 and all(
            span[a] < dimensions[a] / COMPOSITE_MAJOR_FACTOR for a in range(3)
        ):
            issues.insert(
                0,
                _geom(
                    "COMPOSITE_BOUNDS_MISMATCH",
                    f"estimated composite spans "
                    f"({span[0]:g}x{span[1]:g}x{span[2]:g}m) are far smaller than "
                    "the declared dimensions (all axes < declared/3) — the "
                    "declared bounding box grossly overstates the object",
                ),
            )
        # 4.4c — DEF-077: a SINGLE-PART object may not declare an axis more than
        # SINGLE_PART_DECLARED_MAX_RATIO beyond its estimated visible span (the
        # tiny-mesh / giant-invisible-pickbox attack). Legitimate one-part
        # objects whose declared dimensions are ~1.5x their visible span pass.
        if part_count == 1:
            for axis, axis_name in enumerate(("width", "height", "depth")):
                if dimensions[axis] > span[axis] * SINGLE_PART_DECLARED_MAX_RATIO:
                    issues.append(
                        _geom(
                            "COMPOSITE_BOUNDS_MISMATCH",
                            f"declared {axis_name} {dimensions[axis]:g}m exceeds "
                            f"the single-part estimated span {span[axis]:g}m by "
                            f"more than {SINGLE_PART_DECLARED_MAX_RATIO:g}x — the "
                            "declared bounding box grossly overstates this "
                            "one-part object",
                        )
                    )

    # ---- 4.5 DEGENERATE_PART_LAYOUT (multi-part only) ------------------------
    if part_count > 1:
        threshold = math.ceil(DEGENERATE_PART_FRACTION * part_count)
        close_count = _pairwise_close_count(positions, boxes)
        if close_count >= threshold:
            issues.append(
                _geom(
                    "DEGENERATE_PART_LAYOUT",
                    f"{close_count} of {part_count} parts occupy effectively the "
                    f"same position (pairwise distance < {DEGENERATE_DISTANCE:g}m) "
                    "— the object has no recognizable silhouette; do not place "
                    "all parts at the same origin",
                )
            )

    # ---- 4.6 EXCESSIVE_PART_SEPARATION (composite center reference) ----------
    centroid = [(mins[a] + maxs[a]) / 2.0 for a in range(3)]
    for part, pos in zip(spec.parts, positions):
        for axis, axis_name in enumerate(("x", "y", "z")):
            offset = abs(pos[axis] - centroid[axis])
            limit = dimensions[axis] * EXCESSIVE_SEPARATION_FACTOR
            if offset > limit:
                issues.append(
                    _geom(
                        "EXCESSIVE_PART_SEPARATION",
                        f"part position {pos[axis]:g} on {axis_name} is "
                        f"{offset:g}m from the object centroid, beyond the "
                        f"allowed {limit:g}m (declared*{EXCESSIVE_SEPARATION_FACTOR:g})",
                        part_id=part.id,
                    )
                )
                break

    # ---- 4.7 PARENT_CHILD_SPATIAL_CONSISTENCY --------------------------------
    if part_count > 1:
        max_declared = max(dimensions)
        base_limit = max(
            PARENT_CHILD_DISTANCE_MIN,
            PARENT_CHILD_DISTANCE_FRACTION * max_declared,
        )
        by_index = {part.id: i for i, part in enumerate(spec.parts)}
        for part, pos in zip(spec.parts, positions):
            if part.parent_id is None:
                continue
            parent_index = by_index.get(part.parent_id)
            if parent_index is None:
                continue
            parent_pos = positions[parent_index]
            distance = math.sqrt(
                sum((pos[a] - parent_pos[a]) ** 2 for a in range(3))
            )
            scale = part.transform.scale
            half = [float(s) / 2.0 for s in scale]
            half_diag = math.sqrt(sum(h ** 2 for h in half))
            allowed = base_limit + half_diag
            if distance > allowed:
                issues.append(
                    _geom(
                        "PARENT_CHILD_SPATIAL_CONSISTENCY",
                        f"child part {part.id!r} is {distance:g}m from its parent "
                        f"{part.parent_id!r}, beyond the allowed {allowed:g}m "
                        "for this object",
                        part_id=part.id,
                    )
                )
    # ---- 4.8 VISUAL_EXTENT_TOO_SMALL -----------------------------------------
    largest = max(span)
    if (
        largest < MIN_VISIBLE_EXTENT
        or all(axis < MIN_VISIBLE_AXIS for axis in span)
    ):
        issues.append(
            _geom(
                "VISUAL_EXTENT_TOO_SMALL",
                f"the object's visible geometry is too small or collapsed "
                f"(largest span {largest:g}m; spans "
                f"{span[0]:g}x{span[1]:g}x{span[2]:g}m) — an LLM-proposed tiny "
                "or collapsed mesh cannot be a usable interactive object",
            )
        )
    # ---- 4.9 SILHOUETTE_HEURISTIC --------------------------------------------
    silhouette_applicable = silhouette_relevant(spec)
    silhouette_passed = True
    if silhouette_applicable:
        silhouette_passed = _has_two_separated_parts(positions)
        if not silhouette_passed:
            issues.append(
                _geom(
                    "SILHOUETTE_HEURISTIC",
                    "this hand-held object has no recognizable silhouette: at "
                    "least TWO distinct parts whose positions differ by more than "
                    f"{SILHOUETTE_SEPARATION:g}m are required",
                )
            )
    # ---- INVALID_IDENTIFIER -------------------------------------------------
    for part in spec.parts:
        if not PART_ID_PATTERN.match(part.id):
            issues.append(
                _geom(
                    "INVALID_IDENTIFIER",
                    f"part id {part.id!r} must match the authoritative Phase 13 "
                    "pattern ^part_[0-9]{2}$ (ASCII digits only; no "
                    "dashes/spaces/uppercase/Unicode ids)",
                    part_id=part.id,
                )
            )
        if not ROLE_PATTERN.match(part.role):
            issues.append(
                _geom(
                    "INVALID_IDENTIFIER",
                    f"role {part.role!r} must match the authoritative Phase 13 "
                    "grammar ^[a-z0-9_]+$ (no dashes/spaces/uppercase roles)",
                    part_id=part.id,
                )
            )
    # ---- phase §6 semantic normalization --------------------------------------
    semantic_issue = _semantic_normalization_issue(spec, requested_name)
    if semantic_issue is not None:
        issues.append(semantic_issue)

    issues.sort(key=lambda i: (i.code, i.partId or ""))
    metrics = GeometryMetrics(
        declared_dimensions=dimensions,
        part_count=part_count,
        estimated_bounding_box=(mins, maxs),
        span=span,
        silhouette_heuristic_applicable=silhouette_applicable,
        silhouette_heuristic_passed=silhouette_passed,
    )
    return GeometryReport(valid=not issues, issues=tuple(issues), metrics=metrics)


# --------------------------------------------------------------------------- #
# deterministic raw-spec inspection (repair-diagnostics layer)
# --------------------------------------------------------------------------- #


def _unwrap_document(raw: Any) -> Mapping[str, Any] | None:
    """Lightweight mirror of the documented wrapper acceptance; never raises."""
    if isinstance(raw, str):
        try:
            data = bounded_json_loads(raw)
        except (BoundedJsonError, ValueError):
            return None
        raw = data
    if not isinstance(raw, Mapping):
        return None
    if set(raw) == {"assetSpec"}:
        inner = raw.get("assetSpec")
        return inner if isinstance(inner, Mapping) else None
    return raw


def inspect_raw_spec_issues(raw: Any) -> tuple[GeometryIssue, ...]:
    """Deterministic structured repair-diagnostics for a RAW (possibly Phase 13
    invalid) spec: per-part authoritative identifier grammar (part id/role) and
    material allowlist membership. Returns an empty tuple for inspectsable
    documents."""
    data = _unwrap_document(raw)
    if data is None:
        return ()
    parts = data.get("parts") if isinstance(data.get("parts"), (list, tuple)) else ()
    issues: list[GeometryIssue] = []
    seen_ids: set[str] = set()
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            continue
        part_id = part.get("id") if isinstance(part.get("id"), str) else None
        if part_id is None:
            continue
        if part_id in seen_ids or not PART_ID_PATTERN.match(part_id):
            issues.append(
                GeometryIssue(
                    code="INVALID_IDENTIFIER",
                    classification="STRUCTURAL_ERROR",
                    partId=part_id,
                    message=(
                        f"part id {part_id!r} must match the authoritative "
                        "Phase 13 pattern ^part_[0-9]{2} and be unique (ids are "
                        "part_00..part_23; ASCII digits only; no "
                        "dashes/spaces/uppercase/Unicode ids)"
                    ),
                )
            )
        seen_ids.add(part_id)
        role = part.get("role")
        if isinstance(role, str) and not ROLE_PATTERN.match(role):
            issues.append(
                GeometryIssue(
                    code="INVALID_IDENTIFIER",
                    classification="STRUCTURAL_ERROR",
                    partId=part_id,
                    message=(
                        f"role {role!r} must match the authoritative Phase 13 "
                        "role grammar ^[a-z0-9_]+$ (dash/space/uppercase roles "
                        "are invalid — use underscores, e.g. main_body)"
                    ),
                )
            )
        material = part.get("material")
        if isinstance(material, str) and material not in MATERIAL_VOCAB:
            issues.append(
                GeometryIssue(
                    code="MATERIAL_NOT_ALLOWED",
                    classification="STRUCTURAL_ERROR",
                    partId=part_id,
                    message=(
                        f"material {material!r} is not allowlisted; use one of "
                        "the allowed materials"
                    ),
                    allowed=ALLOWED_MATERIALS,
                )
            )
    issues.sort(key=lambda i: (i.code, i.partId or ""))
    return tuple(issues)


__all__ = [
    "ALLOWED_MATERIALS",
    "CATEGORY_SUBTYPE_NORMALIZATION",
    "CLASSIFICATION_MAP",
    "COMPOSITE_MAJOR_FACTOR",
    "COMPOSITE_TOLERANCE",
    "DEGENERATE_DISTANCE",
    "DEGENERATE_OVERLAP_FRACTION",
    "DEGENERATE_PART_FRACTION",
    "ENVELOPE_TOLERANCE",
    "EXCESSIVE_SEPARATION_FACTOR",
    "GeometryIssue",
    "GeometryMetrics",
    "GeometryReport",
    "HANDHELD_CANONICAL_TERMS",
    "HANDHELD_CATEGORY_TAGS",
    "HANDHELD_MAX_DIMENSION",
    "HANDHELD_MAX_SINGLE_DIMENSION",
    "HANDHELD_SUBTYPE_TAGS",
    "MIN_VISIBLE_EXTENT",
    "MIN_VISIBLE_AXIS",
    "PARENT_CHILD_DISTANCE_FRACTION",
    "PARENT_CHILD_DISTANCE_MIN",
    "SILHOUETTE_SEPARATION",
    "SINGLE_PART_DECLARED_MAX_RATIO",
    "inspect_raw_spec_issues",
    "is_handheld_object",
    "normalized_category_subtype",
    "silhouette_relevant",
    "validate_geometry",
]