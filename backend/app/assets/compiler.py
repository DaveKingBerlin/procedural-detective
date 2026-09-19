"""Phase 13 — trusted procedural asset compiler.

``compile_asset_spec`` turns a validated, immutable ``AssetSpec`` into a frozen
``GeneratedAssetDefinition`` — the player-safe declarative render metadata the
frontend renders. This is the ONLY path from a declarative spec to published
geometry; the AI never produces executable code, and the compiler never executes
anything.

Key properties:

- **Content-addressed identity**: ``assetId = "proc." + category + "." +
  sha256(normalize_spec(spec) + ":compilerVersion:N:schemaVersion:M")[:16]``.
  The same normalized spec + the same (compilerVersion, schemaVersion) ALWAYS
  yields the same id — inside and across processes. A different compiler or
  schema version changes the id (versioned cache semantics).
- **Deterministic order**: definition parts are ordered by the fixed
  ``part_00..part_23`` id sequence, independent of the input order.
- **Resolved colors**: each part receives the RESOLVED ``#RRGGBB`` hex — the
  explicit ``sourceColor`` when provided (validated hex), else the backend
  material table's ``color`` for the part's material token. The client never
  interprets material tokens.
- **Immutable**: every definition is a frozen dataclass; the same frozen
  instance is shared by the cache and the published payload and can never be
  mutated (an attempted ``setattr`` raises).
- **Bounded hitbox**: per axis = max(parts extent union declared dimension),
  min-clamped to ``HITBOX_MIN`` (>= 0.15) and capped at the theoretical
  maximum (``HITBOX_MAX`` = 10.0).

``validate_embedded_definition`` re-validates a RAW definition JSON form found
in a published payload against the CURRENT compiler/schema versions (the
projection gate rejects schema-version confusion, tampered parts and out-of-range
geometry without ever crashing).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.assets.specs import (
    AssetSpec,
    AssetSpecError,
    CATEGORY_SPEC_ALLOWLIST,
    DIMENSION_MAX,
    DIMENSION_MIN,
    MAX_CANONICAL_NAME_LENGTH,
    MAX_PART_SCALE,
    MAX_PARTS,
    MAX_POSITION_BOUND,
    MAX_ROTATION_BOUND,
    MAX_SUBTYPE_LENGTH,
    MIN_PART_SCALE,
    normalize_spec,
)

COMPILER_VERSION = 1
SCHEMA_VERSION = 1

# The procedural asset id grammar: proc.<category>.<16 lowercase hex>.
# DEF-069: the category segment is bounded (<= 64 chars) and the whole id is
# bounded (<= 128 chars) — enforced by ``definition_json_issues`` (gate) and
# ``asset_id_for`` (compiler guarantee; the REAL compiled ids are <= 33).
PROCEDURAL_ASSET_PATTERN = re.compile(r"^proc\.[a-z0-9_]+\.[a-f0-9]{16}$")
PROCEDURAL_ASSET_ID_MAX_LENGTH = 128
PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH = 64

# Minimum (pickable) hitbox component after the extent derivation.
HITBOX_MIN = 0.15
# Theoretical maximum hitbox component: 2 * (MAX_POSITION_BOUND +
# MAX_PART_SCALE / 2) = 10.0 (defensive ceiling; documented bound).
HITBOX_MAX = 10.0
# DEF-077: the derived pick-hitbox extent may never exceed the estimated
# VISIBLE span of the compiled geometry by more than this ratio (a tiny visible
# mesh can never present a giant invisible pick target; the value is also
# floored at HITBOX_MIN so legitimate small objects stay directly clickable).
HITBOX_VISIBLE_MAX_RATIO = 2.0

_EVENT_HANDLER_ROLE_RE = re.compile(r"^on[a-z_]+$")

_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
# DEF-076: ASCII-only part ids. The compiler resolves parts by the fixed ASCII
# part_00..part_23 sequence — a Unicode/fullwidth id could never map and would
# be SILENTLY DROPPED. The read/validation side rejects non-ASCII ids; this
# projection gate re-checks the same ASCII grammar for published definitions.
_PART_ID_PATTERN = re.compile(r"^part_[0-9]{2}$")
_ROLE_PATTERN = re.compile(r"^[a-z0-9_]+$")
# Documented key allowlists of the serialized definition document.
_DEFINITION_KEYS = frozenset(
    {
        "compilerVersion",
        "schemaVersion",
        "assetId",
        "canonicalName",
        "category",
        "subtype",
        "dimensions",
        "parts",
        "hitbox",
    }
)
_DEFINITION_PART_KEYS = frozenset(
    {"id", "role", "primitive", "transform", "color", "parentId"}
)
_TRANSFORM_KEYS = frozenset({"position", "rotation", "scale"})
_VEC_KEYS = frozenset({"x", "y", "z"})
_HITBOX_KEYS = frozenset({"scale"})

# The fixed part id sequence the compiler applies (deterministic order).
_PART_IDS: tuple[str, ...] = tuple(f"part_{i:02d}" for i in range(MAX_PARTS))


def is_procedural_asset_id(asset_id: object) -> bool:
    """True when ``asset_id`` matches the procedural asset id grammar."""
    return isinstance(asset_id, str) and bool(PROCEDURAL_ASSET_PATTERN.match(asset_id))


def asset_id_for(
    spec: AssetSpec,
    *,
    compiler_version: int = COMPILER_VERSION,
    schema_version: int = SCHEMA_VERSION,
) -> str:
    """Content-addressed procedural assetId of one spec (+ versions).

    Same normalized spec + same versions -> the same id inside and across
    processes. ``spec_hash_for_cache`` holds the version-free content hash that
    the bounded cache keys on.
    """
    if not isinstance(spec, AssetSpec):
        raise TypeError("asset_id_for requires an AssetSpec")
    category = spec.category
    if len(category) > PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH:
        raise AssetSpecError(
            f"category segment of {len(category)} chars exceeds the maximum "
            f"{PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH}"
        )
    digest = hashlib.sha256(
        (
            f"{normalize_spec(spec)}:compilerVersion:{int(compiler_version)}:"
            f"schemaVersion:{int(schema_version)}"
        ).encode("utf-8")
    ).hexdigest()[:16]
    asset_id = f"proc.{category}.{digest}"
    # DEF-069 compiler guarantee: every emitted id must fit the client grammar
    # and stay inside the documented length bounds (the longest REAL id is 33).
    if len(asset_id) > PROCEDURAL_ASSET_ID_MAX_LENGTH:
        raise AssetSpecError(
            f"assetId of {len(asset_id)} chars exceeds the maximum "
            f"{PROCEDURAL_ASSET_ID_MAX_LENGTH}"
        )
    if not PROCEDURAL_ASSET_PATTERN.match(asset_id):
        raise AssetSpecError(
            f"compiled assetId {asset_id!r} does not match the proc.* grammar"
        )
    return asset_id


def spec_hash_for_cache(spec: AssetSpec) -> str:
    """Version-free content hash of one normalized spec (cache key basis)."""
    return hashlib.sha256(normalize_spec(spec).encode("utf-8")).hexdigest()


def material_color(token: str) -> str:
    """Resolved base color of one validated material token (raises ``KeyError``
    for tokens outside the frozen vocabulary — callers validate membership)."""
    from app.assets.materials import MATERIAL_COLORS

    return MATERIAL_COLORS[token]["color"]


# --------------------------------------------------------------------------- #
# typed definition model (frozen, immutable)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GeneratedVec3:
    """One bounded {x, y, z} vector component of a definition."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y), "z": float(self.z)}


@dataclass(frozen=True)
class GeneratedTransform:
    """One bounded part transform (local space)."""

    position: GeneratedVec3
    rotation: GeneratedVec3
    scale: GeneratedVec3

    def to_dict(self) -> dict[str, dict[str, float]]:
        return {
            "position": self.position.to_dict(),
            "rotation": self.rotation.to_dict(),
            "scale": self.scale.to_dict(),
        }


@dataclass(frozen=True)
class GeneratedPart:
    """One resolved, immutable definition part (renderer-facing)."""

    id: str
    role: str
    primitive: str
    transform: GeneratedTransform
    color: str
    parent_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "primitive": self.primitive,
            "transform": self.transform.to_dict(),
            "color": self.color,
            "parentId": self.parent_id,
        }


@dataclass(frozen=True)
class GeneratedHitbox:
    """The derived bounded picking box (scale only)."""

    scale: GeneratedVec3

    def to_dict(self) -> dict[str, dict[str, float]]:
        return {"scale": self.scale.to_dict()}


@dataclass(frozen=True)
class GeneratedAssetDefinition:
    """A fully validated, immutable generated asset definition.

    ``compiler_version``/``schema_version`` are the exact compiler/schema
    versions that produced the definition; the projection gate re-checks them
    against the CURRENT constants (schema-version confusion is rejected).
    """

    compiler_version: int = COMPILER_VERSION
    schema_version: int = SCHEMA_VERSION
    asset_id: str = ""
    canonical_name: str = ""
    category: str = ""
    subtype: str | None = None
    dimensions: GeneratedVec3 | None = None
    parts: tuple[GeneratedPart, ...] = ()
    hitbox: GeneratedHitbox | None = None

    def to_definition_json(self) -> dict[str, Any]:
        """The EXACT camelCase contract document the frontend renders.

        Deterministic: same definition -> the same dict, whose ``json.dumps``
        (sort_keys) is byte-identical across calls and processes.
        """
        return {
            "compilerVersion": self.compiler_version,
            "schemaVersion": self.schema_version,
            "assetId": self.asset_id,
            "canonicalName": self.canonical_name,
            "category": self.category,
            "subtype": self.subtype,
            "dimensions": self.dimensions.to_dict() if self.dimensions else None,
            "parts": [part.to_dict() for part in self.parts],
            "hitbox": self.hitbox.to_dict() if self.hitbox else None,
        }

    def to_json_bytes(self) -> bytes:
        """Deterministic byte form (identical input -> identical output bytes)."""
        return json.dumps(
            self.to_definition_json(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")


# --------------------------------------------------------------------------- #
# compile + hitbox derivation
# --------------------------------------------------------------------------- #


class AssetSpecCompileError(AssetSpecError):
    """A validated AssetSpec could NOT be compiled to its complete part set.

    Raised (never a silent drop) when a part id cannot be mapped onto the fixed
    ASCII ``part_00..part_23`` sequence — DEF-076. It subclasses
    ``AssetSpecError`` so the Oracle/controller handle it as a sanitized
    internal diagnostic and the generation degrades safely (never publishes
    partial geometry).
    """


def _derive_hitbox(
    parts: tuple[GeneratedPart, ...],
    dimensions: tuple[float, float, float],
) -> GeneratedHitbox:
    """Per-axis picking extent = max(parts extent, declared dimension).

    Parts extent per axis = 2 * max(|part.position| + part.scale/2). The result
    is min-clamped to HITBOX_MIN (always pickable) and capped at HITBOX_MAX
    (bounded; the theoretical max is 2 * (4.0 + 2.0 / 2) = 10.0).
    """
    extents = [0.0, 0.0, 0.0]
    for part in parts:
        position = part.transform.position
        scale = part.transform.scale
        extents[0] = max(extents[0], abs(position.x) + scale.x / 2)
        extents[1] = max(extents[1], abs(position.y) + scale.y / 2)
        extents[2] = max(extents[2], abs(position.z) + scale.z / 2)
    values: list[float] = []
    for axis in range(3):
        visible_span = extents[axis] * 2.0
        full = max(visible_span, float(dimensions[axis]))
        value = min(max(full, HITBOX_MIN), HITBOX_MAX)
        # DEF-077: the pick box is a defensive bound on the VISIBLE geometry —
        # never larger than visible_span * HITBOX_VISIBLE_MAX_RATIO (floored at
        # HITBOX_MIN so small objects stay pickable). A tiny visible mesh can
        # therefore never present a giant invisible pick target, even when the
        # declared dimensions grossly overstate the object.
        cap = max(visible_span * HITBOX_VISIBLE_MAX_RATIO, HITBOX_MIN)
        values.append(min(value, cap))
    return GeneratedHitbox(
        scale=GeneratedVec3(x=values[0], y=values[1], z=values[2])
    )


def compile_asset_spec(
    spec: AssetSpec,
    *,
    revalidate: bool = True,
) -> GeneratedAssetDefinition:
    """Compile a validated spec into the frozen generated definition.

    ``AssetSpec`` construction already validates every bound; ``revalidate``
    adds an explicit defensive re-validation pass so even a hand-built spec is
    rejected here (never coerced).
    """
    if not isinstance(spec, AssetSpec):
        raise TypeError("compile_asset_spec requires an AssetSpec")
    if revalidate:
        if spec.category not in CATEGORY_SPEC_ALLOWLIST:
            raise AssetSpecError(("compile rejected: category is not in the vocabulary",))
        if len(spec.parts) == 0 or len(spec.parts) > MAX_PARTS:
            raise AssetSpecError(("compile rejected: parts out of bounds",))

    resolved: list[GeneratedPart] = []
    by_id = {part.id: part for part in spec.parts}
    for part_id in _PART_IDS:
        part = by_id.get(part_id)
        if part is None:
            continue
        color = (
            part.source_color
            if part.source_color is not None
            else material_color(part.material)
        )
        position = part.transform.position
        rotation = part.transform.rotation
        scale = part.transform.scale
        resolved.append(
            GeneratedPart(
                id=part.id,
                role=part.role,
                primitive=part.primitive,
                transform=GeneratedTransform(
                    position=GeneratedVec3(x=float(position[0]), y=float(position[1]), z=float(position[2])),
                    rotation=GeneratedVec3(x=float(rotation[0]), y=float(rotation[1]), z=float(rotation[2])),
                    scale=GeneratedVec3(x=float(scale[0]), y=float(scale[1]), z=float(scale[2])),
                ),
                color=color,
                parent_id=part.parent_id,
            )
        )

    # DEF-076: the compiler NEVER silently drops a validated part. Every part id
    # is resolved against the fixed ASCII part_00..part_23 sequence; if any
    # validated id cannot be mapped (or the compiled count would differ), fail
    # fast with a typed diagnostic instead of publishing partial geometry.
    if len(resolved) != len(spec.parts):
        unmapped = sorted(
            pid for pid in by_id if pid not in _PART_IDS
        )
        raise AssetSpecCompileError(
            (
                "compile refused to drop validated parts: compiled "
                f"{len(resolved)} of {len(spec.parts)} parts; unmappable part "
                f"ids {unmapped}",
            )
        )

    parts_tuple = tuple(resolved)
    hitbox = _derive_hitbox(parts_tuple, tuple(float(v) for v in spec.dimensions))
    return GeneratedAssetDefinition(
        asset_id=asset_id_for(spec),
        canonical_name=spec.canonical_name,
        category=spec.category,
        subtype=spec.subtype,
        dimensions=GeneratedVec3(
            x=float(spec.dimensions[0]),
            y=float(spec.dimensions[1]),
            z=float(spec.dimensions[2]),
        ),
        parts=parts_tuple,
        hitbox=hitbox,
    )


# --------------------------------------------------------------------------- #
# raw definition JSON validation (projection gate)
# --------------------------------------------------------------------------- #


def _vec_json_issues(value: Any, where: str, low: float, high: float) -> list[str]:
    """{x, y, z} JSON validation: exact keys, finite, per-axis within bounds."""
    if not isinstance(value, Mapping):
        return [f"{where}: must be an object {{x, y, z}}"]
    if set(value.keys()) != set(_VEC_KEYS):
        return [f"{where}: must have exactly the keys x, y, z"]
    issues: list[str] = []
    for axis in ("x", "y", "z"):
        num = value[axis]
        if isinstance(num, bool) or not isinstance(num, (int, float)):
            issues.append(f"{where}.{axis}: must be a number")
            continue
        magnitude = float(num)
        if not math.isfinite(magnitude):
            issues.append(f"{where}.{axis}: must be finite")
        elif not (low <= magnitude <= high):
            issues.append(
                f"{where}.{axis}: must be within [{low:g}, {high:g}]"
            )
    return issues


def definition_json_issues(
    raw: Any, *, expected_asset_id: str | None = None
) -> tuple[str, ...]:
    """Deterministic sorted issues of ONE serialized definition document.

    Enforces the frozen contract: exact key set, exact current compiler/schema
    versions, proc.* assetId pattern (+ caller-supplied expected id match),
    bounds for dimensions/parts/transforms/colors, parent ordering (earlier
    part only) and parent chain depth <= 2. Empty tuple when valid.
    """
    issues: list[str] = []
    if not isinstance(raw, Mapping):
        return ("generatedDefinition must be a JSON object",)
    issues.extend(
        f"generatedDefinition: unknown key {key!r}"
        for key in sorted(set(raw) - _DEFINITION_KEYS)
    )
    for key in ("compilerVersion", "schemaVersion"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            issues.append(f"generatedDefinition.{key} must be an integer")
    compiler_version = raw.get("compilerVersion")
    schema_version = raw.get("schemaVersion")
    if compiler_version != COMPILER_VERSION:
        issues.append(
            f"generatedDefinition.compilerVersion must be {COMPILER_VERSION}"
        )
    if schema_version != SCHEMA_VERSION:
        issues.append(
            f"generatedDefinition.schemaVersion must be {SCHEMA_VERSION}"
        )
    asset_id = raw.get("assetId")
    if not isinstance(asset_id, str) or not is_procedural_asset_id(asset_id):
        issues.append("generatedDefinition.assetId must match the proc.* pattern")
    elif expected_asset_id is not None and asset_id != expected_asset_id:
        issues.append("generatedDefinition.assetId does not match the placement assetId")
    if isinstance(asset_id, str) and asset_id.startswith("proc."):
        # DEF-069: the grammar bound is length-bounded END to END — a tampered
        # long assetId (client grammar is `proc.<category>.<16hex>` with a
        # bounded category and a bounded total length) yields a clean issue and
        # the placement is skipped.
        if len(asset_id) > PROCEDURAL_ASSET_ID_MAX_LENGTH:
            issues.append(
                f"generatedDefinition.assetId exceeds {PROCEDURAL_ASSET_ID_MAX_LENGTH} "
                "characters"
            )
        segments = asset_id.split(".")
        if len(segments) == 3 and len(segments[1]) > PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH:
            issues.append(
                f"generatedDefinition.assetId category segment exceeds "
                f"{PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH} characters"
            )
    canonical_name = raw.get("canonicalName")
    if not isinstance(canonical_name, str) or not canonical_name:
        issues.append("generatedDefinition.canonicalName must be a non-empty string")
    elif len(canonical_name) > MAX_CANONICAL_NAME_LENGTH:
        issues.append(
            f"generatedDefinition.canonicalName exceeds {MAX_CANONICAL_NAME_LENGTH} chars"
        )
    category = raw.get("category")
    if not isinstance(category, str) or category not in CATEGORY_SPEC_ALLOWLIST:
        issues.append(
            "generatedDefinition.category is not in the category vocabulary"
        )
    subtype = raw.get("subtype")
    if subtype is not None and (
        not isinstance(subtype, str) or len(subtype) > MAX_SUBTYPE_LENGTH
    ):
        issues.append("generatedDefinition.subtype is invalid")
    dimensions = raw.get("dimensions")
    issues.extend(
        _vec_json_issues(
            dimensions, "generatedDefinition.dimensions", DIMENSION_MIN, DIMENSION_MAX
        )
    )

    parts = raw.get("parts")
    if not isinstance(parts, list):
        issues.append("generatedDefinition.parts must be an array")
    else:
        if not parts:
            issues.append("generatedDefinition.parts must contain at least 1 part")
        elif len(parts) > MAX_PARTS:
            issues.append(
                f"generatedDefinition.parts exceeds the maximum of {MAX_PARTS} parts"
            )
        index_of: dict[str, int] = {}
        for index_value, part in enumerate(parts):
            where = f"generatedDefinition.parts[{index_value}]"
            if not isinstance(part, Mapping):
                issues.append(f"{where} must be a JSON object")
                continue
            issues.extend(
                f"{where}: unknown key {key!r}"
                for key in sorted(set(part) - _DEFINITION_PART_KEYS)
            )
            part_id = part.get("id")
            if not isinstance(part_id, str) or not _PART_ID_PATTERN.match(part_id):
                issues.append(
                    f"{where}.id must match ^part_[0-9]{{2}}$ (ASCII digits only)"
                )
            if part_id in index_of:
                issues.append(f"{where}.id {part_id!r} is a duplicate part id")
            else:
                index_of[part_id] = index_value
            role = part.get("role")
            if not isinstance(role, str) or not _ROLE_PATTERN.match(role):
                issues.append(f"{where}.role must match ^[a-z0-9_]+$")
            elif len(role) > 24:
                issues.append(f"{where}.role exceeds 24 characters")
            elif _EVENT_HANDLER_ROLE_RE.match(role):
                issues.append(f"{where}.role looks like an event handler and is rejected")
            primitive = part.get("primitive")
            if primitive not in ("box", "cylinder", "sphere", "plane"):
                issues.append(f"{where}.primitive is not a supported primitive")
            color = part.get("color")
            if not isinstance(color, str) or not _COLOR_PATTERN.match(color):
                issues.append(f"{where}.color must be a #RRGGBB hex color")
            parent_id = part.get("parentId")
            if parent_id is not None and not isinstance(parent_id, str):
                issues.append(f"{where}.parentId must be a string or null")
            transform = part.get("transform")
            if not isinstance(transform, Mapping):
                issues.append(f"{where}.transform must be an object")
            else:
                issues.extend(
                    f"{where}.transform: unknown key {key!r}"
                    for key in sorted(set(transform) - _TRANSFORM_KEYS)
                )
                issues.extend(
                    _vec_json_issues(
                        transform.get("position"),
                        f"{where}.transform.position",
                        -MAX_POSITION_BOUND,
                        MAX_POSITION_BOUND,
                    )
                )
                issues.extend(
                    _vec_json_issues(
                        transform.get("rotation"),
                        f"{where}.transform.rotation",
                        -MAX_ROTATION_BOUND,
                        MAX_ROTATION_BOUND,
                    )
                )
                issues.extend(
                    _vec_json_issues(
                        transform.get("scale"),
                        f"{where}.transform.scale",
                        MIN_PART_SCALE,
                        MAX_PART_SCALE,
                    )
                )
        for index_value, part in enumerate(parts):
            if not isinstance(part, Mapping):
                continue
            part_id = part.get("id")
            parent_id = part.get("parentId")
            if parent_id is None or not isinstance(parent_id, str):
                continue
            parent_index = index_of.get(parent_id)
            if parent_index is None:
                continue  # unknown parent already reported
            if parent_index >= index_value or parent_id == part_id:
                issues.append(
                    f"generatedDefinition.parts[{index_value}].parentId must "
                    "reference an EARLIER part"
                )
        for index_value, part in enumerate(parts):
            if not isinstance(part, Mapping) or not isinstance(part.get("parentId"), str):
                continue
            depth = 0
            probe_id = part.get("parentId")
            seen_depth: set[str] = set()
            while isinstance(probe_id, str):
                if probe_id in seen_depth:
                    break  # cycle guard (unreachable with earlier-parent rule)
                seen_depth.add(probe_id)
                depth += 1
                if depth > 2:
                    issues.append(
                        f"generatedDefinition.parts[{index_value}]: parent chain "
                        f"of {part.get('id')!r} exceeds the maximum nesting depth of 2"
                    )
                    break
                parent = parts[index_of[probe_id]] if probe_id in index_of else None
                if parent is None:
                    break
                probe_id = parent.get("parentId")

    hitbox = raw.get("hitbox")
    if not isinstance(hitbox, Mapping):
        issues.append("generatedDefinition.hitbox must be an object")
    else:
        issues.extend(
            f"generatedDefinition.hitbox: unknown key {key!r}"
            for key in sorted(set(hitbox) - _HITBOX_KEYS)
        )
        issues.extend(
            _vec_json_issues(
                hitbox.get("scale"),
                "generatedDefinition.hitbox.scale",
                HITBOX_MIN,
                HITBOX_MAX,
            )
        )
    return tuple(sorted(set(issues)))


def validate_embedded_definition(
    asset_id: str, raw: object
) -> dict[str, Any] | None:
    """Projection gate for a payload-embedded generatedDefinition.

    Returns a fresh copy of the validated camelCase definition document (safe
    declarative render metadata) when ``raw`` is a fully valid definition for
    the CURRENT compiler/schema versions AND its ``assetId`` equals
    ``asset_id``; returns ``None`` otherwise (the caller skips the placement
    with a sanitized log, never a crash).
    """
    if not isinstance(raw, Mapping):
        return None
    issues = definition_json_issues(raw, expected_asset_id=asset_id)
    if issues:
        return None
    return dict(raw)


__all__ = [
    "AssetSpecCompileError",
    "COMPILER_VERSION",
    "SCHEMA_VERSION",
    "GeneratedAssetDefinition",
    "GeneratedHitbox",
    "GeneratedPart",
    "GeneratedTransform",
    "GeneratedVec3",
    "HITBOX_MAX",
    "HITBOX_MIN",
    "HITBOX_VISIBLE_MAX_RATIO",
    "PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH",
    "PROCEDURAL_ASSET_ID_MAX_LENGTH",
    "PROCEDURAL_ASSET_PATTERN",
    "asset_id_for",
    "compile_asset_spec",
    "definition_json_issues",
    "is_procedural_asset_id",
    "spec_hash_for_cache",
    "validate_embedded_definition",
]