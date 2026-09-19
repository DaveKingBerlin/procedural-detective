"""Phase 13 — declarative AssetSpec schema + strict, bounded parser.

The AI never returns executable code: it returns a ``AssetSpec`` — a bounded
declarative geometry data object. Trusted code (``parse_asset_spec`` here and
``app.assets.compiler``) validates and compiles it. This module:

- defines the typed, frozen spec model (``AssetSpec`` / ``AssetSpecPart`` /
  ``SpecTransform``) whose constructors re-validate on build (the catalog.py
  pattern), so direct construction can never bypass the bounds;
- validates the RAW (possibly untrusted) JSON spec with deterministic, sorted
  issue strings and NEVER coerces anything (unknown keys, unknown primitives,
  oversized arrays, non-finite numbers, out-of-range values, unsafe strings —
  all rejected, never repaired);
- exposes the documented bound constants below as frozen constants.

Frozen Phase 13 primitive surface (renderer-supported ONLY): ``box``,
``cylinder``, ``sphere``, ``plane``. Anything else — including ``capsule``,
``extruded_polygon`` and unknown/unknown-word primitives — is REJECTED with an
explicit parse issue (never coerced, never silently mapped onto another shape).

Bound summary (all inclusive):

+---------------------------------+----------------------------------------+
| canonicalName                   | non-empty str, 1..80 chars            |
| category                        | CATEGORY_SPEC_ALLOWLIST (catalog       |
|                                 | vocabulary; "generated" is NOT added — |
|                                 | the placer needs a category an anchor  |
|                                 | allows, which the catalog vocabulary  |
|                                 | already provides)                    |
| subtype                         | None or str 1..80                     |
| dimensions (x/y/z)              | 0.05 <= d <= 4.0, finite              |
| parts                           | 1..24 parts                          |
| part id                         | ^part_[0-9]{2}$ within part_00..part_23  |
| part role                       | ^[a-z0-9_]+$ 1..24 chars              |
| part primitive                 | box|cylinder|sphere|plane            |
| part position (x/y/z)           | |v| <= 4.0, finite                   |
| part rotation (x/y/z)           | |r| <= 2*pi, finite                  |
| part scale (x/y/z)              | 0.05 <= s <= 2.0, finite            |
| part material                   | frozen MATERIAL_VOCABULARY token        |
| part sourceColor (optional)     | #RRGGBB hex                          |
| part parentId (optional)        | id of an EARLIER part (depth <= 2)    |

Every string is additionally content-safety scanned: no control characters, no
URL schemes, no path separators/traversal/absolute paths, no forbidden
``script``/``handler``/``shader``/``function``/``eval`` word tokens, no
oversized strings.

``parse_asset_spec`` mirrors the catalog.py/parser.py style: it either raises a
typed ``AssetSpecError`` carrying the sorted ``.issues`` (``non_throwing=False``)
or returns ``None`` (``non_throwing=True``). ``validate_asset_spec`` is the
pure non-raising validator. ``normalize_spec``/``spec_hash`` produce the
deterministic canonical form used by the content-addressed compiler.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.assets.catalog import (
    CATEGORY_ALLOWLIST,
    MATERIAL_VOCABULARY,
    normalize_asset_text,
)
from app.assets.depthguard import (
    MAX_STRUCT_NESTING,
    BoundedJsonError,
    bounded_json_loads,
    bounded_structure_depth,
)
from app.assets.glyphs import format_glyph_issues

# --------------------------------------------------------------------------- #
# frozen bounds + vocabularies
# --------------------------------------------------------------------------- #

PRIMITIVE_ALLOWLIST: frozenset[str] = frozenset(
    {"box", "cylinder", "sphere", "plane"}
)
# Alpha-ordered documented display of the allowlist (UI/QA readable).
PRIMITIVE_ALLOWLIST_ORDERED: tuple[str, ...] = ("box", "cylinder", "plane", "sphere")

# Category vocabulary for a generated asset: the catalog category vocabulary
# (NOT a new "generated" category — the placer derives anchor compatibility from
# the exact same category an anchor's allowedCategories already enumerates).
CATEGORY_SPEC_ALLOWLIST: tuple[str, ...] = CATEGORY_ALLOWLIST

MAX_CANONICAL_NAME_LENGTH = 80
MAX_SUBTYPE_LENGTH = 80
MAX_PARTS = 24
MAX_PART_ROLE_LENGTH = 24

DIMENSION_MIN = 0.05
DIMENSION_MAX = 4.0
MAX_POSITION_BOUND = 4.0
MAX_ROTATION_BOUND = 2.0 * math.pi
MIN_PART_SCALE = 0.05
MAX_PART_SCALE = 2.0

# "part_00" .. "part_23" (id pattern + numeric range). DEF-076: the digits are
# ASCII-ONLY ([0-9]) — Python ``\\d`` matches Unicode/fullwidth decimal digits
# (e.g. "part_０１"), which would pass validation but silently DROP the part at
# compile time (the compiler's ASCII-only part_00..part_23 sequence could never
# map it). Non-ASCII ids are rejected here with a clean issue so a validated
# spec ALWAYS compiles to ALL of its parts (the compiler additionally asserts
# the count — never a silent drop).
PART_ID_PATTERN = re.compile(r"^part_([0-9]{2})$")
MIN_PART_ID = 0
MAX_PART_ID = MAX_PARTS - 1  # 23
ROLE_PATTERN = re.compile(r"^[a-z0-9_]+$")
# Event-handler-shaped roles ("onload", "onclick", "onerror", ...) are REJECTED
# (Phase 13: a part role must never look like an executable event handler).
_EVENT_HANDLER_ROLE_RE = re.compile(r"^on[a-z_]+$")
_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Documented key allowlists (strict: anything else is an unknown-key issue).
_SPEC_KEYS = frozenset({"canonicalName", "category", "subtype", "dimensions", "parts"})
_PART_KEYS = frozenset(
    {"id", "role", "primitive", "transform", "material", "sourceColor", "parentId"}
)
_TRANSFORM_KEYS = frozenset({"position", "rotation", "scale"})
_VEC_KEYS = frozenset({"x", "y", "z"})

# Forbidden URL-scheme tokens (substring scan, as in app.assets.validation).
_FORBIDDEN_URL_TOKENS: tuple[str, ...] = (
    "http:",
    "https:",
    "data:",
    "file:",
    "javascript:",
)
# Executable/handler/shader word tokens at word boundaries.
_FORBIDDEN_TOKEN_RE = re.compile(r"\b(?:script|handler|shader|function|eval)\b", re.IGNORECASE)
# Windows drive-letter absolute-path prefix.
_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")

# The strict document key set of the OUTER payload that carries an AssetSpec when
# a provider supplies it as a wrapper (documented; parse_asset_spec also accepts
# the bare spec document). Providers/tests may send either shape; the wrapper
# keys are validated just as strictly.
_ASSET_SPEC_WRAPPER_KEYS = frozenset({"assetSpec"})

MAX_SPEC_DOCUMENT_CHARS = 262144  # whole-document ceiling (defense in depth)


class AssetSpecError(ValueError):
    """The AssetSpec failed validation (deterministic sorted issues)."""

    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = tuple(sorted(set(issues)))
        super().__init__("asset spec errors: " + "; ".join(self.issues))


class _DuplicateKeyError(ValueError):
    """Raised by the JSON object-pairs hook on the FIRST repeated key (mirror of
    ``app.generation.parser``: duplicate JSON keys must never silently last-win)."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"asset spec document: first duplicate key {key!r}")
        result[key] = value
    return result


# --------------------------------------------------------------------------- #
# string safety (deterministic, mirrors app.assets.validation style)
# --------------------------------------------------------------------------- #


def _string_issues(value: str, where: str, max_len: int) -> list[str]:
    """Deterministic issue strings for one spec string (empty when safe)."""
    issues: list[str] = []
    norm = unicodedata.normalize("NFKC", value)
    forms = (value, norm)
    if max_len and len(value) > max_len:
        issues.append(f"{where}: string exceeds {max_len} characters")
    if any(ord(ch) < 0x20 for ch in value):
        issues.append(f"{where}: contains a control character")
    issues.extend(format_glyph_issues(value, where))
    lowered = [form.casefold() for form in forms]
    for scheme in _FORBIDDEN_URL_TOKENS:
        if any(scheme in form for form in lowered):
            issues.append(f"{where}: contains a forbidden URL scheme {scheme!r}")
            break
    token = _FORBIDDEN_TOKEN_RE.search(value) or _FORBIDDEN_TOKEN_RE.search(norm)
    if token:
        issues.append(
            f"{where}: contains a forbidden token {token.group(0).lower()!r}"
        )
    if any(("/" in form) or ("\\" in form) for form in forms):
        issues.append(f"{where}: contains a path separator")
    if any(".." in form for form in forms):
        issues.append(f"{where}: contains path traversal '..'")
    if any(
        form.startswith(("/", "\\")) or _DRIVE_ABSOLUTE_RE.match(form)
        for form in forms
    ):
        issues.append(f"{where}: is an absolute path")
    return issues


# --------------------------------------------------------------------------- #
# numeric/geometry validation helpers
# --------------------------------------------------------------------------- #


def _number_issues(
    value: Any, where: str, low: float, high: float, label: str
) -> list[str]:
    """One scalar number: real (not bool), finite, inside [low, high]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return [f"{where}: {label} must be a number (got {type(value).__name__})"]
    magnitude = float(value)
    if not math.isfinite(magnitude):
        return [f"{where}: {label} must be finite (got {value!r})"]
    if not (low <= magnitude <= high):
        return [
            f"{where}: {label} must be within [{low:g}, {high:g}] (got {magnitude:g})"
        ]
    return []


def _vec_issues(
    value: Any, where: str, low: float, high: float, label: str, empty_ok: bool = False
) -> list[str]:
    """A {x,y,z} vector: exact keys, finite, per-axis within [low, high]."""
    if not isinstance(value, Mapping):
        return [f"{where}: {label} must be an object {{x, y, z}}"]
    if set(value.keys()) != set(_VEC_KEYS):
        return [f"{where}: {label} must have exactly the keys x, y, z"]
    issues: list[str] = []
    for axis in ("x", "y", "z"):
        issues.extend(_number_issues(value[axis], f"{where}.{axis}", low, high, label))
    return issues


# --------------------------------------------------------------------------- #
# raw document validation (deterministic, never raises)
# --------------------------------------------------------------------------- #


def _document_root(data: Any) -> tuple[Mapping[str, Any] | None, list[str]]:
    """Accept a bare spec document or the documented ``{assetSpec: {...}}``
    wrapper; returns the inner document mapping plus any issues.

    ``data`` may be a JSON dict OR a raw JSON string (the provider returns
    text; duplicate keys anywhere in the document are rejected — the JSON
    parser never silently last-wins, mirroring ``app.generation.parser``).
    """
    if isinstance(data, str):
        if len(data) > MAX_SPEC_DOCUMENT_CHARS:
            return None, [
                f"asset spec document exceeds {MAX_SPEC_DOCUMENT_CHARS} characters"
            ]
        try:
            decoded = bounded_json_loads(
                data, object_pairs_hook=_reject_duplicate_keys
            )
        except BoundedJsonError as exc:
            return None, [str(exc)]
        except _DuplicateKeyError as exc:
            return None, [str(exc)]
        except (json.JSONDecodeError, ValueError) as exc:
            message = getattr(exc, "msg", None) or str(exc)
            if len(message) > 120:
                message = message[:120] + "..."
            return None, [f"asset spec is not valid JSON: {message}"]
        if not isinstance(decoded, Mapping):
            return None, ["asset spec document must decode to a JSON object"]
        data = decoded
    if not isinstance(data, Mapping):
        return None, ["asset spec must be a JSON object"]
    if set(data) == set(_ASSET_SPEC_WRAPPER_KEYS):
        inner = data.get("assetSpec")
        if not isinstance(inner, Mapping):
            return None, ["assetSpec: must be a JSON object"]
        return inner, []
    return data, []


def _part_issues(item: Any, where: str, seen_ids: list[str]) -> list[str]:
    issues: list[str] = []
    if not isinstance(item, Mapping):
        return [f"{where}: part must be a JSON object"]
    issues.extend(f"{where}: unknown key {key!r}" for key in sorted(set(item) - _PART_KEYS))

    part_id = item.get("id")
    if not isinstance(part_id, str) or not part_id:
        issues.append(f"{where}.id must be a non-empty string")
    else:
        match = PART_ID_PATTERN.match(part_id)
        if not match:
            issues.append(
                f"{where}.id {part_id!r} must match ^part_[0-9]{{2}}$ "
                "(ASCII digits only: part_00..part_23)"
            )
        else:
            part_number = int(match.group(1))
            if not (MIN_PART_ID <= part_number <= MAX_PART_ID):
                issues.append(
                    f"{where}.id {part_id!r} must be within "
                    f"part_{MIN_PART_ID:02d}..part_{MAX_PART_ID:02d}"
                )
        if part_id in seen_ids:
            issues.append(f"{where}.id {part_id!r} is a duplicate part id")
        seen_ids.append(part_id)

    role = item.get("role")
    if not isinstance(role, str) or not role:
        issues.append(f"{where}.role must be a non-empty string")
    else:
        issues.extend(_string_issues(role, f"{where}.role", MAX_PART_ROLE_LENGTH))
        if not ROLE_PATTERN.match(role):
            issues.append(f"{where}.role {role!r} must match ^[a-z0-9_]+$")
        elif _EVENT_HANDLER_ROLE_RE.match(role):
            issues.append(
                f"{where}.role {role!r} looks like an event handler and is rejected"
            )

    primitive = item.get("primitive")
    if not isinstance(primitive, str) or not primitive:
        issues.append(f"{where}.primitive must be a non-empty string")
    elif primitive not in PRIMITIVE_ALLOWLIST:
        issues.append(
            f"{where}.primitive {primitive!r} is not in the renderer-supported "
            f"primitive vocabulary {sorted(PRIMITIVE_ALLOWLIST)!r}"
        )
        issues.append(f"{where}.primitive {primitive!r} is not supported")
    if isinstance(primitive, str) and primitive:
        issues.extend(_string_issues(primitive, f"{where}.primitive", 24))

    transform = item.get("transform")
    if not isinstance(transform, Mapping):
        issues.append(f"{where}.transform must be an object {{position, rotation, scale}}")
    else:
        issues.extend(
            f"{where}.transform: unknown key {key!r}"
            for key in sorted(set(transform) - _TRANSFORM_KEYS)
        )
        position = transform.get("position") if isinstance(transform, Mapping) else None
        rotation = transform.get("rotation") if isinstance(transform, Mapping) else None
        scale = transform.get("scale") if isinstance(transform, Mapping) else None
        issues.extend(
            _vec_issues(position, f"{where}.transform.position", -MAX_POSITION_BOUND, MAX_POSITION_BOUND, "position")
        )
        issues.extend(
            _vec_issues(rotation, f"{where}.transform.rotation", -MAX_ROTATION_BOUND, MAX_ROTATION_BOUND, "rotation")
        )
        issues.extend(
            _vec_issues(scale, f"{where}.transform.scale", MIN_PART_SCALE, MAX_PART_SCALE, "scale")
        )

    material = item.get("material")
    if not isinstance(material, str) or not material:
        issues.append(f"{where}.material must be a non-empty string")
    else:
        issues.extend(_string_issues(material, f"{where}.material", 24))
        if material not in MATERIAL_VOCABULARY:
            issues.append(
                f"{where}.material {material!r} is not in MATERIAL_VOCABULARY"
            )

    source_color = item.get("sourceColor")
    if source_color is not None:
        if not isinstance(source_color, str) or not _COLOR_PATTERN.match(source_color):
            issues.append(f"{where}.sourceColor {source_color!r} is not a #RRGGBB color")
        else:
            issues.extend(_string_issues(source_color, f"{where}.sourceColor", 16))

    parent_id = item.get("parentId")
    if parent_id is not None:
        if not isinstance(parent_id, str) or not parent_id:
            issues.append(f"{where}.parentId must be a non-empty string when present")
        elif parent_id not in seen_ids:
            issues.append(
                f"{where}.parentId {parent_id!r} references an unknown part id"
            )
        elif parent_id == part_id:
            issues.append(f"{where}.parentId {parent_id!r} cannot reference itself")
    return issues


def _spec_document_issues(data: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    issues.extend(
        f"asset spec: unknown key {key!r}" for key in sorted(set(data) - _SPEC_KEYS)
    )

    canonical = data.get("canonicalName")
    if not isinstance(canonical, str) or not canonical:
        issues.append("canonicalName must be a non-empty string")
    else:
        issues.extend(_string_issues(canonical, "canonicalName", MAX_CANONICAL_NAME_LENGTH))

    category = data.get("category")
    if not isinstance(category, str) or not category:
        issues.append("category must be a non-empty string")
    else:
        issues.extend(_string_issues(category, "category", 32))
        if category not in CATEGORY_SPEC_ALLOWLIST:
            issues.append(
                f"category {category!r} is not in the catalog category "
                f"vocabulary {list(CATEGORY_SPEC_ALLOWLIST)!r}"
            )

    subtype = data.get("subtype")
    if subtype is not None:
        if not isinstance(subtype, str) or not subtype:
            issues.append("subtype must be a non-empty string when present")
        else:
            issues.extend(_string_issues(subtype, "subtype", MAX_SUBTYPE_LENGTH))

    issues.extend(
        _vec_issues(data.get("dimensions"), "dimensions", DIMENSION_MIN, DIMENSION_MAX, "dimension")
    )

    parts = data.get("parts")
    if not isinstance(parts, (list, tuple)):
        issues.append("parts must be an array")
    else:
        if not parts:
            issues.append("parts must contain at least 1 part")
        if len(parts) > MAX_PARTS:
            issues.append(f"parts exceeds the maximum of {MAX_PARTS} parts")
        seen_ids: list[str] = []
        for index, item in enumerate(parts):
            issues.extend(_part_issues(item, f"parts[{index}]", seen_ids))

    # Parent ordering + nesting-depth rule (a parent must be an EARLIER part;
    # max chain depth <= 2). Deterministic and checked AFTER id collection.
    if isinstance(parts, (list, tuple)):
        index_of_id: dict[str, int] = {}
        for index, item in enumerate(parts):
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                index_of_id[item["id"]] = index
        for index, item in enumerate(parts):
            if not isinstance(item, Mapping):
                continue
            where = f"parts[{index}]"
            parent_id = item.get("parentId")
            if parent_id is None or not isinstance(parent_id, str):
                continue
            parent_index = index_of_id.get(parent_id)
            if parent_index is None:
                continue  # already reported as unknown
            if parent_index >= index:
                issues.append(
                    f"{where}: parentId {parent_id!r} must reference an EARLIER "
                    "(earlier-indexed) part"
                )
        # Depth <= 2: every part's ancestor chain from its root has <= 2 hops.
        depth_of: dict[str, int] = {}
        for index, item in enumerate(parts):
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            current = item["id"]
            depth = 0
            probe = item
            while isinstance(probe, Mapping) and probe.get("parentId") is not None:
                parent = probe.get("parentId")
                depth += 1
                if depth > 2:
                    issues.append(
                        f"parts[{index}]: parent chain of {current!r} exceeds "
                    "the maximum nesting depth of 2"
                    )
                    break
                probe = parts[index_of_id[parent]] if parent in index_of_id else None
            depth_of[current] = depth
    return issues


def validate_asset_spec(raw: Any) -> tuple[str, ...]:
    """Never-raising deterministic validator: sorted issue strings (empty=safe).

    DEF-067: a nesting bomb (a deeply nested Python structure that bypassed
    the JSON decoder, or a deep JSON string rejected by ``bounded_json_loads``
    inside ``_document_root``) is reported as a deterministic issue — the
    validator NEVER recurses beyond ``MAX_STRUCT_NESTING`` and never raises
    ``RecursionError``.
    """
    data, issues = _document_root(raw)
    if data is None:
        return tuple(sorted(set(issues)))
    depth = bounded_structure_depth(data)
    if depth > MAX_STRUCT_NESTING:
        issues.append(
            f"spec nesting depth {depth} exceeds the maximum {MAX_STRUCT_NESTING}"
        )
    return tuple(sorted(set(issues + _spec_document_issues(data))))


# --------------------------------------------------------------------------- #
# typed model (constructors re-validate — direct construction cannot bypass)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SpecTransform:
    """One bounded part transform (validated on construction)."""

    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    scale: tuple[float, float, float]

    def __post_init__(self) -> None:
        issues: list[str] = []
        for axis_index, axis in enumerate(("x", "y", "z")):
            for label, value, low, high in (
                ("position", self.position[axis_index], -MAX_POSITION_BOUND, MAX_POSITION_BOUND),
                ("rotation", self.rotation[axis_index], -MAX_ROTATION_BOUND, MAX_ROTATION_BOUND),
                ("scale", self.scale[axis_index], MIN_PART_SCALE, MAX_PART_SCALE),
            ):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    issues.append(f"transform.{label}.{axis} must be a number")
                    continue
                magnitude = float(value)
                if not math.isfinite(magnitude):
                    issues.append(f"transform.{label}.{axis} must be finite")
                elif not (low <= magnitude <= high):
                    issues.append(
                        f"transform.{label}.{axis} must be within [{low:g}, {high:g}]"
                    )
        if issues:
            raise AssetSpecError(tuple(sorted(set(issues))))


@dataclass(frozen=True)
class AssetSpecPart:
    """One bounded declarative part (validated on construction)."""

    id: str
    role: str
    primitive: str
    transform: SpecTransform
    material: str
    source_color: str | None = None
    parent_id: str | None = None

    def __post_init__(self) -> None:
        issues: list[str] | None = None

        def _bad(message: str) -> None:
            nonlocal issues
            if issues is None:
                issues = []
            issues.append(message)

        if not isinstance(self.id, str) or not PART_ID_PATTERN.match(self.id):
            _bad(f"part id {self.id!r} must match ^part_[0-9]{{2}}$ (ASCII digits only)")
        if not isinstance(self.role, str) or not ROLE_PATTERN.match(self.role):
            _bad(f"role {self.role!r} must match ^[a-z0-9_]+$")
        elif _EVENT_HANDLER_ROLE_RE.match(self.role):
            _bad(f"role {self.role!r} looks like an event handler and is rejected")
        if len(self.role) > MAX_PART_ROLE_LENGTH:
            _bad(f"role exceeds {MAX_PART_ROLE_LENGTH} characters")
        if self.primitive not in PRIMITIVE_ALLOWLIST:
            _bad(f"primitive {self.primitive!r} is not supported")
        if self.material not in MATERIAL_VOCABULARY:
            _bad(f"material {self.material!r} is not in MATERIAL_VOCABULARY")
        if self.source_color is not None and not _COLOR_PATTERN.match(self.source_color):
            _bad(f"sourceColor {self.source_color!r} is not a #RRGGBB color")
        if self.parent_id is not None and not isinstance(self.parent_id, str):
            _bad("parentId must be a string or None")
        if issues:
            raise AssetSpecError(tuple(sorted(set(issues))))


@dataclass(frozen=True)
class AssetSpec:
    """A fully validated, immutable declarative asset specification."""

    canonical_name: str
    category: str
    subtype: str | None
    dimensions: tuple[float, float, float]
    parts: tuple[AssetSpecPart, ...] = ()

    def __post_init__(self) -> None:
        issues: list[str] = []

        def _bad(message: str) -> None:
            issues.append(message)

        if not isinstance(self.canonical_name, str) or not self.canonical_name:
            _bad("canonicalName must be a non-empty string")
        if len(self.canonical_name) > MAX_CANONICAL_NAME_LENGTH:
            _bad(f"canonicalName exceeds {MAX_CANONICAL_NAME_LENGTH} characters")
        if self.category not in CATEGORY_SPEC_ALLOWLIST:
            _bad(f"category {self.category!r} is not in the catalog category vocabulary")
        if self.subtype is not None and (
            not isinstance(self.subtype, str) or not self.subtype
        ):
            _bad("subtype must be a non-empty string")
        if len(self.subtype or "") > MAX_SUBTYPE_LENGTH:
            _bad(f"subtype exceeds {MAX_SUBTYPE_LENGTH} characters")
        if not isinstance(self.dimensions, tuple) or len(self.dimensions) != 3:
            _bad("dimensions must be a 3-tuple (x, y, d)")
        else:
            for axis_index, axis in enumerate(("x", "y", "z")):
                value = self.dimensions[axis_index]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    _bad(f"dimensions.{axis} must be a number")
                else:
                    magnitude = float(value)
                    if not math.isfinite(magnitude):
                        _bad(f"dimensions.{axis} must be finite")
                    if not (DIMENSION_MIN <= magnitude <= DIMENSION_MAX):
                        _bad(
                            f"dimensions.{axis} must be within "
                            f"[{DIMENSION_MIN:g}, {DIMENSION_MAX:g}]"
                        )
        if not isinstance(self.parts, tuple) or not self.parts:
            _bad("parts must contain at least 1 part")
        elif len(self.parts) > MAX_PARTS:
            _bad(f"parts exceeds the maximum of {MAX_PARTS} parts")
        else:
            seen_ids: list[str] = []
            index_of_id: dict[str, int] = {}
            for index, part in enumerate(self.parts):
                if not isinstance(part, AssetSpecPart):
                    _bad(f"parts[{index}] must be an AssetSpecPart")
                    continue
                part_id = part.id
                if part.id in seen_ids:
                    _bad(f"duplicate part id {part.id!r}")
                seen_ids.append(part.id)
                index_of_id[part.id] = index
            for index, part in enumerate(self.parts):
                parent_id = part.parent_id
                if parent_id is None:
                    continue
                if parent_id not in index_of_id:
                    _bad(f"parts[{index}].parentId references an unknown part id")
                elif index_of_id[parent_id] >= index:
                    _bad(
                        f"parts[{index}].parentId {parent_id!r} must reference an "
                        "earlier part"
                    )
            depths: dict[str, int] = {}
            for index, part in enumerate(self.parts):
                depth = 0
                current = part
                while current.parent_id is not None:
                    depth += 1
                    if depth > 2:
                        _bad(
                            f"parts[{index}]: parent chain of {part.id!r} exceeds "
                            "the maximum nesting depth of 2"
                        )
                        break
                    current = self.parts[index_of_id[current.parent_id]]
                depths[part.id] = depth
        if issues:
            raise AssetSpecError(tuple(sorted(set(issues))))


# --------------------------------------------------------------------------- #
# parse entry point
# --------------------------------------------------------------------------- #


def _parse_part(item: Mapping[str, Any], index: int) -> AssetSpecPart:
    transform = item.get("transform")
    if not isinstance(transform, Mapping):
        raise AssetSpecError((f"parts[{index}].transform must be an object",))
    position = transform.get("position")
    rotation = transform.get("rotation")
    scale = transform.get("scale")
    if not isinstance(position, Mapping) or not isinstance(rotation, Mapping) or not isinstance(scale, Mapping):
        raise AssetSpecError(
            (f"parts[{index}].transform must declare position/rotation/scale",)
        )

    def _triple(vec: Mapping[str, Any]) -> tuple[float, float, float]:
        return (float(vec["x"]), float(vec["y"]), float(vec["z"]))

    return AssetSpecPart(
        id=str(item["id"]),
        role=str(item["role"]),
        primitive=str(item["primitive"]),
        transform=SpecTransform(
            position=_triple(position),
            rotation=_triple(rotation),
            scale=_triple(scale),
        ),
        material=str(item["material"]),
        source_color=item.get("sourceColor"),
        parent_id=item.get("parentId"),
    )


def parse_asset_spec(
    raw: Any, *, non_throwing: bool = True
) -> "AssetSpec | None":
    """Parse an untrusted AssetSpec into the typed model (reject, never coerce).

    ``non_throwing=True`` returns ``None`` on issues; ``False`` raises
    ``AssetSpecError`` carrying the deterministic sorted ``.issues`` tuple.
    """
    issues = validate_asset_spec(raw)
    if issues:
        if non_throwing:
            return None
        raise AssetSpecError(issues)
    data, _root_issues = _document_root(raw)
    parts = tuple(
        _parse_part(item, index) for index, item in enumerate(data["parts"])
    )
    dimensions = data["dimensions"]
    return AssetSpec(
        canonical_name=str(data["canonicalName"]),
        category=str(data["category"]),
        subtype=data.get("subtype"),
        dimensions=(float(dimensions["x"]), float(dimensions["y"]), float(dimensions["z"])),
        parts=parts,
    )


# --------------------------------------------------------------------------- #
# canonical deterministic form (content-addressed identity basis)
# --------------------------------------------------------------------------- #


def _spec_to_canonical_dict(spec: "AssetSpec") -> dict[str, Any]:
    """Deterministic, key-sorted canonical JSON-ready dict of a spec.

    Parts are sorted by their part id (part_00 < part_01 < ...), so the SAME
    part set in a different input order yields the SAME canonical form (the
    identity basis of content addressing).
    """

    def _triple(values: tuple[float, float, float]) -> list[float]:
        return [float(values[0]), float(values[1]), float(values[2])]

    ordered = sorted(spec.parts, key=lambda part: part.id)
    parts = [
        {
            "id": part.id,
            "role": part.role,
            "primitive": part.primitive,
            "transform": {
                "position": _triple(part.transform.position),
                "rotation": _triple(part.transform.rotation),
                "scale": _triple(part.transform.scale),
            },
            "material": part.material,
            "sourceColor": part.source_color,
            "parentId": part.parent_id,
        }
        for part in ordered
    ]
    return {
        "canonicalName": spec.canonical_name,
        "category": spec.category,
        "dimensions": _triple(spec.dimensions),
        "parts": parts,
        "subtype": spec.subtype,
    }


def normalize_spec(spec: "AssetSpec") -> str:
    """The canonical deterministic JSON text of one spec (sorted keys, compact).

    Same spec (by value) ALWAYS produces byte-identical text within and across
    processes; this is the string the compiler hashes.
    """
    canonical = _spec_to_canonical_dict(spec)
    return json.dumps(
        canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def spec_hash(spec: "AssetSpec") -> str:
    """The sha256 hex digest of the canonical spec text (cache key basis)."""
    return hashlib.sha256(normalize_spec(spec).encode("utf-8")).hexdigest()


def normalize_request_key(text: str | None) -> str:
    """Deterministic request-key normalization (identity namespace)."""
    return normalize_asset_text(text or "")


__all__ = [
    "ASSET_SPEC_WRAPPER_KEYS",
    "AssetSpec",
    "AssetSpecError",
    "AssetSpecPart",
    "CATEGORY_SPEC_ALLOWLIST",
    "DIMENSION_MAX",
    "DIMENSION_MIN",
    "MAX_CANONICAL_NAME_LENGTH",
    "MAX_PART_ID",
    "MAX_PART_ROLE_LENGTH",
    "MAX_PART_SCALE",
    "MAX_PARTS",
    "MAX_POSITION_BOUND",
    "MAX_ROTATION_BOUND",
    "MAX_SUBTYPE_LENGTH",
    "MIN_PART_ID",
    "MIN_PART_SCALE",
    "PART_ID_PATTERN",
    "PRIMITIVE_ALLOWLIST",
    "PRIMITIVE_ALLOWLIST_ORDERED",
    "ROLE_PATTERN",
    "SpecTransform",
    "normalize_request_key",
    "normalize_spec",
    "parse_asset_spec",
    "spec_hash",
    "validate_asset_spec",
]