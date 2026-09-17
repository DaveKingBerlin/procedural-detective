"""Phase 11 — Environment kit manifests: typed descriptors + strict validation.

``assets/environments/*.json`` is the SINGLE source of truth for the five
environment kits (apartment / office / hotel_suite / warehouse / mansion).
This module:

- defines the typed, frozen descriptor model (``EnvironmentKit`` /
  ``ZoneSpec`` / ``AnchorSpec`` / ``SpawnSpec`` / ``LightingSpec`` /
  ``Vec3``) and the documented ``ANCHOR_TYPES`` vocabulary (the Phase 11
  semantic-anchor list);
- validates the RAW manifest at load time with deterministic, sorted issue
  strings (mirroring ``app.assets.catalog``): duplicate environment/zone/
  anchor ids, unknown zone references, an invalid anchor type,
  position/rotation finiteness + bounds (|pos| <= 20; rotations in
  [-2pi, 2pi]), exclusive anchors never sharing a position (epsilon 1e-3),
  BODY anchors on the floor plane, DOCUMENT anchors near a DESK_EVIDENCE
  anchor, spawn validity (PLAYER_SPAWN anchor reference + >= 0.4 clearance
  from every other anchor), required anchor coverage via
  ``defaultAnchorCoverage``, structural asset ids cross-checked against the
  Asset Oracle catalog, ``allowedCategories`` inside the catalog category
  vocabulary, the lighting vocabulary/bounds, ASCII-safe strings (no control
  chars / URL / path tokens, max 80 chars) and array-size bounds
  (zones <= 8, anchors <= 40, aliases <= 8, tags <= 16, total rooms <= 8
  per kit);
- enforces the SAME invariants inside ``EnvironmentKit.__post_init__`` so
  direct construction can never bypass validation (the catalog.py pattern).

``load_environment`` / ``load_all_environments`` read the repo manifests from
``<repo>/assets/environments`` (resolved like ``app.core.config.REPO_ROOT``,
never the process CWD) and NEVER perform network I/O.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.assets.catalog import (
    CATEGORY_ALLOWLIST,
    Catalog,
    load_catalog_from_repo,
)

# --------------------------------------------------------------------------- #
# documented vocabulary + bounds
# --------------------------------------------------------------------------- #

# The Phase 11 semantic-anchor vocabulary (also the layout anchor types the
# world composer may address). PLAYER_SPAWN is the navigation spawn.
ANCHOR_TYPES: tuple[str, ...] = (
    "PLAYER_SPAWN",
    "BODY",
    "FLOOR_EVIDENCE",
    "DESK_EVIDENCE",
    "TABLE_PROP",
    "COMPUTER",
    "DOCUMENT",
    "WALL_EVIDENCE",
    "DOOR",
    "WINDOW",
    "STORAGE",
    "CCTV",
    "ACCESS_CONTROL",
    "GENERIC_PROP",
)

LIGHTING_PROFILES: tuple[str, ...] = ("warm_flat", "cool_dim", "neutral")

# Anchor types that MUST have at least one default-coverage anchor per kit
# (the Phase 11 content rules: BODY / FLOOR_EVIDENCE / DESK_EVIDENCE /
# GENERIC_PROP / DOOR / WINDOW, plus PLAYER_SPAWN which always exists).
REQUIRED_COVERAGE_TYPES: frozenset[str] = frozenset(
    {"PLAYER_SPAWN", "BODY", "FLOOR_EVIDENCE", "DESK_EVIDENCE", "GENERIC_PROP", "DOOR", "WINDOW"}
)
# Of these, at least TWO must have coverage (>= 1 anchor) per kit.
SECONDARY_COVERAGE_TYPES: tuple[str, ...] = (
    "COMPUTER",
    "DOCUMENT",
    "TABLE_PROP",
    "STORAGE",
    "CCTV",
    "ACCESS_CONTROL",
)
MIN_SECONDARY_COVERED = 2

MIN_ZONES = 5
MAX_ZONES = 8
MIN_ANCHORS = 10
MAX_ANCHORS = 40
MAX_ALIASES = 8
MAX_TAGS = 16
MAX_ROOMS_PER_KIT = 8
MAX_CATEGORIES_PER_ANCHOR = 8
MAX_STRING_LENGTH = 80
MAX_POSITION_BOUND = 20.0
ROTATION_BOUND = 2.0 * math.pi
POSITION_EPS = 1e-3
SPAWN_CLEARANCE = 0.4
DOCUMENT_DESK_DISTANCE = 3.0

# Lowercase snake identifier grammar for environment/zone/anchor ids.
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# RGB hex color grammar.
_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Windows drive-letter absolute-path prefix, e.g. "C:\" / "C:/".
_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
# Forbidden URL schemes scanned as substrings (defense in depth).
_FORBIDDEN_URL_TOKENS: tuple[str, ...] = (
    "http://",
    "https://",
    "data:",
    "file:",
    "javascript:",
)

_DOCUMENTED_MANIFEST_KEYS: frozenset[str] = frozenset(
    {
        "environmentId",
        "version",
        "canonicalName",
        "aliases",
        "tags",
        "zones",
        "anchors",
        "spawn",
        "lighting",
        "structuralAssets",
        "styleHint",
        "defaultAnchorCoverage",
    }
)
_DOCUMENTED_ZONE_KEYS: frozenset[str] = frozenset({"zoneId", "label", "rooms"})
_DOCUMENTED_ANCHOR_KEYS: frozenset[str] = frozenset(
    {
        "anchorId",
        "type",
        "zoneId",
        "position",
        "rotation",
        "allowedCategories",
        "exclusive",
        "required",
    }
)
_DOCUMENTED_SPAWN_KEYS: frozenset[str] = frozenset({"anchorId", "position", "rotation"})
_DOCUMENTED_LIGHTING_KEYS: frozenset[str] = frozenset(
    {"profile", "keyIntensity", "hemiIntensity", "accentColor"}
)
_VEC_KEYS: frozenset[str] = frozenset({"x", "y", "z"})


class EnvironmentError(Exception):
    """Base class for environment-kit loading/validation failures."""


class EnvironmentValidationError(EnvironmentError):
    """Kit manifest(s) failed load-time validation (deterministic issues)."""

    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = tuple(sorted(set(issues)))
        super().__init__("; ".join(self.issues))


class EnvironmentNotFoundError(EnvironmentError):
    """No kit with the requested environmentId is declared."""


# --------------------------------------------------------------------------- #
# typed descriptors
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Vec3:
    """One local-space vector; numeric components accepted as int/float only."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        for axis in ("x", "y", "z"):
            value = getattr(self, axis)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Vec3.{axis} must be a number")
            object.__setattr__(self, axis, float(value))

    def distance_to(self, other: "Vec3") -> float:
        """Euclidean distance between two local-space vectors."""
        return math.sqrt(
            (self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2
        )


@dataclass(frozen=True)
class LightingSpec:
    """One lighting profile (phase-independent deterministic values)."""

    profile: str
    key_intensity: float
    hemi_intensity: float
    accent_color: str


@dataclass(frozen=True)
class ZoneSpec:
    """One logical zone (renderable room group) of a kit."""

    zone_id: str
    label: str
    rooms: tuple[str, ...]


@dataclass(frozen=True)
class AnchorSpec:
    """One semantic anchor of a kit (never raw coordinates to the player).

    ``anchor_id`` is the world-graph placement anchor identifier; ``type`` is
    the semantic anchor type from ``ANCHOR_TYPES``; ``position``/``rotation``
    are deterministic local-space transforms used by the placer/geometry;
    ``allowed_categories`` bounds which catalog-asset categories may occupy
    the anchor; ``exclusive`` means at most one object may ever occupy the
    anchor; ``required`` marks default-coverage anchors.
    """

    anchor_id: str
    type: str
    zone_id: str
    position: Vec3
    rotation: Vec3
    allowed_categories: tuple[str, ...]
    exclusive: bool
    required: bool

    def vector_to(self) -> Any:
        """Convenience: this anchor as a (position, rotation) pair."""
        return (self.position, self.rotation)


@dataclass(frozen=True)
class SpawnSpec:
    """The player navigation spawn (references a PLAYER_SPAWN anchor)."""

    anchor_id: str
    position: Vec3
    rotation: Vec3


@dataclass(frozen=True)
class EnvironmentKit:
    """One fully validated, immutable environment kit.

    Construction re-runs the cross-set/global invariants (catalog.py pattern),
    so a kit with duplicate ids, broken references, bad coverage, unsafe
    strings or unverified structural assets cannot be built — whether it comes
    from ``load_environment`` / ``load_all_environments`` or a caller's own
    dataclass construction.
    """

    environment_id: str
    version: int
    canonical_name: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    zones: tuple[ZoneSpec, ...]
    anchors: tuple[AnchorSpec, ...]
    spawn: SpawnSpec
    lighting: LightingSpec
    structural_assets: tuple[str, ...]
    style_hint: str | None
    default_anchor_coverage: Mapping[str, tuple[str, ...]]

    by_id: Mapping[str, AnchorSpec] = field(init=False, repr=False, compare=False)
    zones_by_id: Mapping[str, ZoneSpec] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        issues = _validate_kit_invariants(self)
        if issues:
            raise EnvironmentValidationError(issues)
        object.__setattr__(
            self,
            "by_id",
            {a.anchor_id: a for a in self.anchors},
        )
        object.__setattr__(
            self,
            "zones_by_id",
            {z.zone_id: z for z in self.zones},
        )


# --------------------------------------------------------------------------- #
# string/geometry safety helpers
# --------------------------------------------------------------------------- #


def _string_issues(value: Any, where: str, *, allow_none: bool = False) -> list[str]:
    """Deterministic issue strings for one manifest string (empty when safe)."""
    if value is None:
        if allow_none:
            return []
        return [f"{where}: must be a non-empty string"]
    if not isinstance(value, str) or not value:
        return [f"{where}: must be a non-empty string"]
    issues: list[str] = []
    if len(value) > MAX_STRING_LENGTH:
        issues.append(f"{where}: string exceeds {MAX_STRING_LENGTH} characters")
    if any(ord(ch) < 0x20 for ch in value):
        issues.append(f"{where}: contains a control character")
    lower = unicodedata.normalize("NFKC", value).casefold()
    for scheme in _FORBIDDEN_URL_TOKENS:
        if scheme in lower:
            issues.append(f"{where}: contains a forbidden URL scheme {scheme!r}")
    if "/" in value or "\\" in value:
        issues.append(f"{where}: contains a path separator")
    if ".." in value:
        issues.append(f"{where}: contains path traversal '..'")
    if value.startswith(("/", "\\")) or _DRIVE_ABSOLUTE_RE.match(value):
        issues.append(f"{where}: is an absolute path")
    return issues


def _string_list_issues(
    value: Any, where: str, max_len: int | None
) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, (list, tuple)):
        return [f"{where}: must be an array of non-empty strings"]
    if max_len is not None and len(value) > max_len:
        issues.append(f"{where}: exceeds the maximum of {max_len} entries")
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry:
            issues.append(f"{where}[{index}]: must be a non-empty string")
        else:
            issues.extend(_string_issues(entry, f"{where}[{index}]"))
    return issues


def _vec_data_issues(value: Any, where: str) -> list[str]:
    """Raw vector {x,y,z} validation: exact keys, finite numbers."""
    issues: list[str] = []
    if not isinstance(value, Mapping):
        return [f"{where}: must be an object {{x, y, z}}"]
    if set(value.keys()) != set(_VEC_KEYS):
        return [f"{where}: must have exactly the keys x, y, z"]
    for axis in ("x", "y", "z"):
        num = value[axis]
        if isinstance(num, bool) or not isinstance(num, (int, float)):
            issues.append(
                f"{where}.{axis}: must be a number (got {type(num).__name__})"
            )
        elif not math.isfinite(float(num)):
            issues.append(f"{where}.{axis}: must be finite")
    return issues


def _position_issues(value: Any, where: str) -> list[str]:
    """Position: finite and bounded to the local space (|v| <= 20)."""
    issues = _vec_data_issues(value, where)
    if not isinstance(value, Mapping):
        return issues
    for axis in ("x", "y", "z"):
        num = value.get(axis)
        if isinstance(num, bool) or not isinstance(num, (int, float)):
            continue
        magnitude = float(num)
        if not math.isfinite(magnitude) or abs(magnitude) > MAX_POSITION_BOUND:
            issues.append(
                f"{where}.{axis}: must satisfy |v| <= {MAX_POSITION_BOUND:.0f}"
            )
    return issues


def _rotation_issues(value: Any, where: str) -> list[str]:
    """Rotation: finite and inside [-2pi, 2pi] per component."""
    issues = _vec_data_issues(value, where)
    if not isinstance(value, Mapping):
        return issues
    for axis in ("x", "y", "z"):
        num = value.get(axis)
        if isinstance(num, bool) or not isinstance(num, (int, float)):
            continue
        magnitude = float(num)
        if not math.isfinite(magnitude) or not (-ROTATION_BOUND - 1e-9 <= magnitude <= ROTATION_BOUND + 1e-9):
            issues.append(
                f"{where}.{axis}: must be within [-2*pi, 2*pi]"
            )
    return issues


# --------------------------------------------------------------------------- #
# raw manifest validation (deterministic, never raises)
# --------------------------------------------------------------------------- #


def _validate_number_in_range(
    node: Any, key: str, where: str, low: float, high: float
) -> list[str]:
    if not isinstance(node, Mapping) or key not in node:
        return [f"{where}: missing required key {key!r}"]
    value = node[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return [f"{where}.{key} must be a finite number"]
    if not (low <= float(value) <= high):
        return [f"{where}.{key} must be within [{low:g}, {high:g}]"]
    return []


def _validate_zone(item: Any, zone_id: str, where: str) -> list[str]:
    issues: list[str] = []
    issues += (
        ["{where} must be a JSON object".replace("{where}", where)]
        if not isinstance(item, Mapping)
        else [f"{where}: unknown key {key!r}" for key in sorted(set(item) - _DOCUMENTED_ZONE_KEYS)]
    )
    if not isinstance(item, Mapping):
        return issues
    issues += _string_issues(item.get("zoneId"), f"{where}.zoneId")
    issues += _string_issues(item.get("label"), f"{where}.label")
    issues += _string_list_issues(item.get("rooms"), f"{where}.rooms", None)
    return issues


def _validate_anchor(item: Any, where: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(item, Mapping):
        return [f"{where} must be a JSON object"]
    issues += [f"{where}: unknown key {key!r}" for key in sorted(set(item) - _DOCUMENTED_ANCHOR_KEYS)]
    anchor_id = item.get("anchorId")
    issues += _string_issues(anchor_id, f"{where}.anchorId")
    if isinstance(anchor_id, str) and anchor_id and not _ID_PATTERN.match(anchor_id):
        issues.append(f"{where}.anchorId {anchor_id!r} must match ^[a-z][a-z0-9_]*$")
    anchor_type = item.get("type")
    if not isinstance(anchor_type, str) or not anchor_type:
        issues.append(f"{where}.type must be a non-empty string")
    elif anchor_type not in ANCHOR_TYPES:
        issues.append(
            f"{where}.type {anchor_type!r} is not in the ANCHOR_TYPES vocabulary "
            f"{list(ANCHOR_TYPES)!r}"
        )
    issues += _string_issues(item.get("zoneId"), f"{where}.zoneId")
    issues += _position_issues(item.get("position"), f"{where}.position")
    issues += _rotation_issues(item.get("rotation"), f"{where}.rotation")
    categories = item.get("allowedCategories")
    issues += _string_list_issues(
        categories, f"{where}.allowedCategories", MAX_CATEGORIES_PER_ANCHOR
    )
    if not isinstance(item.get("exclusive"), bool):
        issues.append(f"{where}.exclusive must be a boolean")
    if not isinstance(item.get("required"), bool):
        issues.append(f"{where}.required must be a boolean")
    return issues


def _validate_spawn(item: Any, where: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(item, Mapping):
        return [f"{where} must be a JSON object"]
    issues += [f"{where}: unknown key {key!r}" for key in sorted(set(item) - _DOCUMENTED_SPAWN_KEYS)]
    issues += _string_issues(item.get("anchorId"), f"{where}.anchorId")
    issues += _position_issues(item.get("position"), f"{where}.position")
    issues += _rotation_issues(item.get("rotation"), f"{where}.rotation")
    return issues


def _validate_lighting(item: Any, where: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(item, Mapping):
        return [f"{where} must be a JSON object"]
    issues += [f"{where}: unknown key {key!r}" for key in sorted(set(item) - _DOCUMENTED_LIGHTING_KEYS)]
    profile = item.get("profile")
    if not isinstance(profile, str) or not profile:
        issues.append(f"{where}.profile must be a non-empty string")
    elif profile not in LIGHTING_PROFILES:
        issues.append(
            f"{where}.profile {profile!r} is not in the lighting vocabulary "
            f"{list(LIGHTING_PROFILES)!r}"
        )
    issues += _validate_number_in_range(item, "keyIntensity", where, 0.0, 1.0)
    issues += _validate_number_in_range(item, "hemiIntensity", where, 0.0, 1.0)
    accent = item.get("accentColor")
    if not isinstance(accent, str) or not _COLOR_PATTERN.match(accent):
        issues.append(f"{where}.accentColor must be a #RRGGBB hex color")
    return issues


def validate_environment_data(
    data: Any, *, catalog: Catalog | None = None
) -> tuple[str, ...]:
    """Validate one raw kit manifest; return deterministic sorted issues.

    Never raises and performs no I/O. When ``catalog`` is None the catalog
    cross-checks (structural asset existence, allowedCategories vocabulary)
    are skipped (the loaders always pass the Asset Oracle catalog).
    """
    issues: list[str] = []
    if not isinstance(data, Mapping):
        return ("environment document must be a JSON object",)

    issues += [
        f"environment document: unknown key {key!r}"
        for key in sorted(set(data) - _DOCUMENTED_MANIFEST_KEYS)
    ]

    zone_ids: list[str] = []
    anchor_ids: list[str] = []
    environment_id = data.get("environmentId")
    issues += _string_issues(environment_id, "environmentId")
    if isinstance(environment_id, str) and environment_id and not _ID_PATTERN.match(environment_id):
        issues.append(
            f"environmentId {environment_id!r} must match ^[a-z][a-z0-9_]*$"
        )

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        issues.append("version must be a positive integer")

    issues += _string_issues(data.get("canonicalName"), "canonicalName")
    issues += _string_list_issues(data.get("aliases"), "aliases", MAX_ALIASES)
    issues += _string_list_issues(data.get("tags"), "tags", MAX_TAGS)

    zones = data.get("zones")
    if not isinstance(zones, (list, tuple)):
        issues.append("zones must be an array")
    else:
        if len(zones) < MIN_ZONES:
            issues.append(f"zones must contain at least {MIN_ZONES} entries")
        if len(zones) > MAX_ZONES:
            issues.append(f"zones exceeds the maximum of {MAX_ZONES} entries")
        total_rooms = 0
        for index, item in enumerate(zones):
            where = f"zones[{index}]"
            issues += _validate_zone(item, environment_id, where)
            if isinstance(item, Mapping):
                zone_id = item.get("zoneId")
                if isinstance(zone_id, str) and zone_id:
                    if not _ID_PATTERN.match(zone_id):
                        issues.append(
                            f"{where}.zoneId {zone_id!r} must match ^[a-z][a-z0-9_]*$"
                        )
                    else:
                        zone_ids.append(zone_id)
                        rooms = item.get("rooms")
                        if isinstance(rooms, (list, tuple)):
                            total_rooms += sum(
                                1 for room in rooms if isinstance(room, str) and room
                            )
        if total_rooms > MAX_ROOMS_PER_KIT:
            issues.append(
                f"kit declares more than {MAX_ROOMS_PER_KIT} rooms in total"
            )
        for index, zone_id in enumerate(zone_ids):
            if zone_id in zone_ids[:index]:
                issues.append(
                    f"duplicate zoneId {zone_id!r}: declared at zones[{zone_ids.index(zone_id)}] "
                    f"and again at zones[{index}]"
                )

    anchors = data.get("anchors")
    if not isinstance(anchors, (list, tuple)):
        issues.append("anchors must be an array")
    else:
        if len(anchors) < MIN_ANCHORS:
            issues.append(f"anchors must contain at least {MIN_ANCHORS} entries")
        if len(anchors) > MAX_ANCHORS:
            issues.append(f"anchors exceeds the maximum of {MAX_ANCHORS} entries")
        anchor_types: list[str] = []
        anchor_zones: list[str] = []
        for index, item in enumerate(anchors):
            where = f"anchors[{index}]"
            issues += _validate_anchor(item, where)
            if isinstance(item, Mapping):
                anchor_id = item.get("anchorId")
                anchor_type = item.get("type")
                zone_id = item.get("zoneId")
                if isinstance(anchor_id, str) and anchor_id and _ID_PATTERN.match(anchor_id):
                    anchor_ids.append(anchor_id)
                    anchor_types.append(anchor_type if isinstance(anchor_type, str) else "")
                    anchor_zones.append(zone_id if isinstance(zone_id, str) else "")
        for index, anchor_id in enumerate(anchor_ids):
            if anchor_id in anchor_ids[:index]:
                issues.append(
                    f"duplicate anchorId {anchor_id!r}: declared at anchors[{anchor_ids.index(anchor_id)}] "
                    f"and again at anchors[{index}]"
                )
        zone_id_set = set(zone_ids) if isinstance(zones, (list, tuple)) else set()
        # Unknown zone references + local-space + exclusive-position checks.
        exclusive_positions: list[tuple[str, float, float, float]] = []
        for index, item in enumerate(anchors):
            if not isinstance(item, Mapping):
                continue
            anchor_id = item.get("anchorId")
            zone_id = item.get("zoneId")
            if isinstance(anchor_id, str) and anchor_id and _ID_PATTERN.match(anchor_id):
                if zone_id not in zone_id_set:
                    issues.append(f"anchors[{index}]: unknown zoneId {zone_id!r}")
            if item.get("exclusive") is True and isinstance(position := item.get("position"), Mapping):
                exclusive_positions.append(
                    (
                        str(anchor_id),
                        float(position["x"]),
                        float(position["y"]),
                        float(position["z"]),
                    )
                )
        for index, (anchor_id, x, y, z) in enumerate(exclusive_positions):
            for other_index, (other_id, ox, oy, oz) in enumerate(exclusive_positions):
                if other_index >= index:
                    continue
                if (
                    abs(x - ox) <= POSITION_EPS
                    and abs(y - oy) <= POSITION_EPS
                    and abs(z - oz) <= POSITION_EPS
                ):
                    issues.append(
                        f"exclusive anchors {other_id!r} and {anchor_id!r} share "
                        f"position ({x:g}, {y:g}, {z:g})"
                    )

    spawn = data.get("spawn")
    spawn_issues = _validate_spawn(spawn, "spawn")
    issues += spawn_issues
    if isinstance(spawn, Mapping) and isinstance(anchors, (list, tuple)):
        spawn_anchor_id = spawn.get("anchorId")
        if isinstance(spawn_anchor_id, str):
            spawn_anchor = None
            for item in anchors:
                if isinstance(item, Mapping) and item.get("anchorId") == spawn_anchor_id:
                    spawn_anchor = item
                    break
            if spawn_anchor is None:
                issues.append(
                    f"spawn.anchorId {spawn_anchor_id!r} does not reference a declared anchor"
                )
            elif spawn_anchor.get("type") != "PLAYER_SPAWN":
                issues.append(
                    f"spawn.anchorId {spawn_anchor_id!r} must reference an anchor of "
                    "type PLAYER_SPAWN"
                )
        spawn_position = spawn.get("position")
        if isinstance(spawn_position, Mapping) and set(spawn_position) == set(_VEC_KEYS):
            spawn_vec = (
                float(spawn_position["x"]),
                float(spawn_position["y"]),
                float(spawn_position["z"]),
            )
            for index, item in enumerate(anchors):
                if not isinstance(item, Mapping):
                    continue
                anchor_id = item.get("anchorId")
                if anchor_id == spawn_anchor_id:
                    continue
                position = item.get("position")
                if not isinstance(position, Mapping) or set(position) != set(_VEC_KEYS):
                    continue
                distance = math.sqrt(
                    (spawn_vec[0] - float(position["x"])) ** 2
                    + (spawn_vec[1] - float(position["y"])) ** 2
                    + (spawn_vec[2] - float(position["z"])) ** 2
                )
                if distance < SPAWN_CLEARANCE:
                    issues.append(
                        f"spawn is within {SPAWN_CLEARANCE:g} of anchor {anchor_id!r} "
                        "(spawn must not intersect geometry)"
                    )

    lighting_issues = _validate_lighting(data.get("lighting"), "lighting")
    issues += lighting_issues

    structural = data.get("structuralAssets")
    if not isinstance(structural, (list, tuple)):
        issues.append("structuralAssets must be an array")
    else:
        if len(structural) < 6:
            issues.append("structuralAssets must contain at least 6 entries")
        for index, asset_id in enumerate(structural):
            if not isinstance(asset_id, str) or not asset_id:
                issues.append(f"structuralAssets[{index}] must be a non-empty string")
            elif catalog is not None and asset_id not in catalog.by_id:
                issues.append(
                    f"structuralAssets[{index}]: asset {asset_id!r} does not exist "
                    "in the asset catalog"
                )

    style_hint = data.get("styleHint")
    issues += _string_issues(style_hint, "styleHint", allow_none=True)

    coverage = data.get("defaultAnchorCoverage")
    if not isinstance(coverage, Mapping):
        issues.append("defaultAnchorCoverage must be an object")
    else:
        anchor_id_set = set(anchor_ids) if isinstance(anchors, (list, tuple)) else set()
        covered_types: set[str] = set()
        for anchor_type, anchors_for_type in coverage.items():
            if anchor_type not in ANCHOR_TYPES:
                issues.append(
                    f"defaultAnchorCoverage: unknown anchor type {anchor_type!r}"
                )
                continue
            if not isinstance(anchors_for_type, (list, tuple)):
                issues.append(
                    f"defaultAnchorCoverage[{anchor_type}] must be an array of anchorIds"
                )
                continue
            seen: set[str] = set()
            for index, anchor_id in enumerate(anchors_for_type):
                if not isinstance(anchor_id, str) or not anchor_id:
                    issues.append(
                        f"defaultAnchorCoverage[{anchor_type}][{index}] must be a "
                        "non-empty string"
                    )
                elif anchor_id in seen:
                    issues.append(
                        f"defaultAnchorCoverage[{anchor_type}]: duplicate anchorId "
                        f"{anchor_id!r}"
                    )
                elif anchor_id not in anchor_id_set:
                    issues.append(
                        f"defaultAnchorCoverage[{anchor_type}]: unknown anchorId "
                        f"{anchor_id!r}"
                    )
                else:
                    seen.add(anchor_id)
            if anchors_for_type:
                covered_types.add(anchor_type)
        if catalog is not None:
            for anchor_type in sorted(REQUIRED_COVERAGE_TYPES - covered_types):
                issues.append(
                    f"defaultAnchorCoverage: required anchor type {anchor_type!r} "
                    "has no default coverage"
                )
            secondary_covered = len(
                set(SECONDARY_COVERAGE_TYPES) & covered_types
            )
            if secondary_covered < MIN_SECONDARY_COVERED:
                issues.append(
                    f"defaultAnchorCoverage: at least {MIN_SECONDARY_COVERED} of the "
                    f"secondary types {list(SECONDARY_COVERAGE_TYPES)!r} must have "
                    "default coverage"
                )
        # Validate allowedCategories against the catalog category vocabulary.
        if catalog is not None and isinstance(anchors, (list, tuple)):
            for index, item in enumerate(anchors):
                if not isinstance(item, Mapping):
                    continue
                categories = item.get("allowedCategories")
                if isinstance(categories, (list, tuple)):
                    for cat in categories:
                        if isinstance(cat, str) and cat not in CATEGORY_ALLOWLIST:
                            issues.append(
                                f"anchors[{index}]: category {cat!r} is not in the "
                                f"catalog category vocabulary {list(CATEGORY_ALLOWLIST)!r}"
                            )
                # BODY anchors must lie on the floor plane.
                if item.get("type") == "BODY" and isinstance(
                    position := item.get("position"), Mapping
                ):
                    y = position.get("y")
                    if (
                        isinstance(y, (int, float))
                        and not isinstance(y, bool)
                        and abs(float(y)) > POSITION_EPS
                    ):
                        issues.append(
                            f"anchors[{index}]: BODY anchor must lie on the floor "
                            "plane (y == 0)"
                        )
        # DOCUMENT anchors must sit near a DESK_EVIDENCE anchor.
        if isinstance(anchors, (list, tuple)):
            desk_positions: list[tuple[float, float, float]] = []
            for item in anchors:
                if (
                    isinstance(item, Mapping)
                    and item.get("type") == "DESK_EVIDENCE"
                    and isinstance(position := item.get("position"), Mapping)
                    and set(position) == set(_VEC_KEYS)
                ):
                    desk_positions.append(
                        (
                            float(position["x"]),
                            float(position["y"]),
                            float(position["z"]),
                        )
                    )
            for index, item in enumerate(anchors):
                if not isinstance(item, Mapping) or item.get("type") != "DOCUMENT":
                    continue
                position = item.get("position")
                if not isinstance(position, Mapping) or set(position) != set(_VEC_KEYS):
                    continue
                if not desk_positions:
                    issues.append(
                        f"anchors[{index}]: DOCUMENT anchor declared but the kit has "
                        "no DESK_EVIDENCE anchor to host it near"
                    )
                    break
                near = any(
                    math.sqrt(
                        (float(position["x"]) - dx) ** 2
                        + (float(position["y"]) - dy) ** 2
                        + (float(position["z"]) - dz) ** 2
                    )
                    <= DOCUMENT_DESK_DISTANCE
                    for dx, dy, dz in desk_positions
                )
                if not near:
                    issues.append(
                        f"anchors[{index}]: DOCUMENT anchor must be within "
                        f"{DOCUMENT_DESK_DISTANCE:g} of a DESK_EVIDENCE anchor"
                    )

    return tuple(sorted(set(issues)))


# --------------------------------------------------------------------------- #
# typed invariant validation (construction cannot bypass validation)
# --------------------------------------------------------------------------- #


def _typed_vec_issues(vec: Any, where: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(vec, Vec3):
        return [f"{where} must be a Vec3"]
    if not math.isfinite(vec.x) or not math.isfinite(vec.y) or not math.isfinite(vec.z):
        issues.append(f"{where}: must be finite")
    return issues


def _validate_kit_invariants(kit: EnvironmentKit) -> tuple[str, ...]:
    """The typed twin of ``validate_environment_data`` used by construction."""
    issues: list[str] = []
    if not isinstance(kit.environment_id, str) or not _ID_PATTERN.match(kit.environment_id):
        issues.append(
            f"environmentId {kit.environment_id!r} must match ^[a-z][a-z0-9_]*$"
        )
    if isinstance(kit.version, bool) or not isinstance(kit.version, int) or kit.version < 1:
        issues.append("version must be a positive integer")
    issues += _string_issues(kit.canonical_name, "canonicalName")
    issues += _string_list_issues(kit.aliases, "aliases", MAX_ALIASES)
    issues += _string_list_issues(kit.tags, "tags", MAX_TAGS)

    if not isinstance(kit.zones, (list, tuple)) or not kit.zones:
        issues.append("zones must be a non-empty sequence of ZoneSpec")
    else:
        if len(kit.zones) < MIN_ZONES:
            issues.append(f"zones must contain at least {MIN_ZONES} entries")
        if len(kit.zones) > MAX_ZONES:
            issues.append(f"zones exceeds the maximum of {MAX_ZONES} entries")
        zone_ids: list[str] = []
        total_rooms = 0
        for index, zone in enumerate(kit.zones):
            where = f"zones[{index}]"
            if not isinstance(zone, ZoneSpec):
                issues.append(f"{where} must be a ZoneSpec")
                continue
            if not _ID_PATTERN.match(zone.zone_id):
                issues.append(f"{where}.zone_id {zone.zone_id!r} must match ^[a-z][a-z0-9_]*$")
            issues += _string_issues(zone.label, f"{where}.label")
            issues += _string_list_issues(zone.rooms, f"{where}.rooms", None)
            zone_ids.append(zone.zone_id)
            total_rooms += len(zone.rooms)
        for index, zone_id in enumerate(zone_ids):
            if zone_id in zone_ids[:index]:
                issues.append(f"duplicate zoneId {zone_id!r} at zones[{index}]")
        if total_rooms > MAX_ROOMS_PER_KIT:
            issues.append(f"kit declares more than {MAX_ROOMS_PER_KIT} rooms in total")

    if not isinstance(kit.anchors, (list, tuple)) or not kit.anchors:
        issues.append("anchors must be a non-empty sequence of AnchorSpec")
    else:
        if len(kit.anchors) < MIN_ANCHORS:
            issues.append(f"anchors must contain at least {MIN_ANCHORS} entries")
        if len(kit.anchors) > MAX_ANCHORS:
            issues.append(f"anchors exceeds the maximum of {MAX_ANCHORS} entries")
        anchor_ids: list[str] = []
        exclusive_positions: list[tuple[str, Vec3]] = []
        body_anchors: list[tuple[int, Vec3]] = []
        doc_anchors: list[tuple[int, Vec3]] = []
        desk_anchors: list[Vec3] = []
        for index, anchor in enumerate(kit.anchors):
            where = f"anchors[{index}]"
            if not isinstance(anchor, AnchorSpec):
                issues.append(f"{where} must be an AnchorSpec")
                continue
            if not _ID_PATTERN.match(anchor.anchor_id):
                issues.append(f"{where}.anchor_id {anchor.anchor_id!r} must match ^[a-z][a-z0-9_]*$")
            if anchor.type not in ANCHOR_TYPES:
                issues.append(
                    f"{where}.type {anchor.type!r} is not in the ANCHOR_TYPES vocabulary"
                )
            vector_issues = _typed_vec_issues(anchor.position, f"{where}.position")
            vector_issues += _typed_vec_issues(anchor.rotation, f"{where}.rotation")
            issues += vector_issues
            if not isinstance(anchor.position, Vec3):
                continue
            issues += _position_issues(
                {"x": anchor.position.x, "y": anchor.position.y, "z": anchor.position.z},
                f"{where}.position",
            )
            issues += _rotation_issues(
                {"x": anchor.rotation.x, "y": anchor.rotation.y, "z": anchor.rotation.z},
                f"{where}.rotation",
            )
            issues += _string_list_issues(
                anchor.allowed_categories, f"{where}.allowed_categories", MAX_CATEGORIES_PER_ANCHOR
            )
            if not isinstance(anchor.exclusive, bool):
                issues.append(f"{where}.exclusive must be a boolean")
            if not isinstance(anchor.required, bool):
                issues.append(f"{where}.required must be a boolean")
            anchor_ids.append(anchor.anchor_id)
            if anchor.exclusive:
                exclusive_positions.append((anchor.anchor_id, anchor.position))
            if anchor.type == "BODY":
                body_anchors.append((index, anchor.position))
            if anchor.type == "DOCUMENT":
                doc_anchors.append((index, anchor.position))
            if anchor.type == "DESK_EVIDENCE":
                desk_anchors.append(anchor.position)
        for index, anchor_id in enumerate(anchor_ids):
            if anchor_id in anchor_ids[:index]:
                issues.append(f"duplicate anchorId {anchor_id!r} at anchors[{index}]")
        for index, (anchor_id, position) in enumerate(exclusive_positions):
            for other_index, (other_id, other_position) in enumerate(exclusive_positions):
                if other_index >= index:
                    continue
                if position.distance_to(other_position) <= POSITION_EPS:
                    issues.append(
                        f"exclusive anchors {other_id!r} and {anchor_id!r} share position "
                        f"({position.x:g}, {position.y:g}, {position.z:g})"
                    )
        for index, position in body_anchors:
            if abs(position.y) > POSITION_EPS:
                issues.append(
                    f"anchors[{index}]: BODY anchor must lie on the floor plane (y == 0)"
                )
        if desk_anchors:
            for index, position in doc_anchors:
                if not any(position.distance_to(desk) <= DOCUMENT_DESK_DISTANCE for desk in desk_anchors):
                    issues.append(
                        f"anchors[{index}]: DOCUMENT anchor must be within "
                        f"{DOCUMENT_DESK_DISTANCE:g} of a DESK_EVIDENCE anchor"
                    )
        elif doc_anchors:
            issues.append(
                "DOCUMENT anchor declared but the kit has no DESK_EVIDENCE anchor to host it near"
            )

    if not isinstance(kit.spawn, SpawnSpec):
        issues.append("spawn must be a SpawnSpec")
    else:
        issues += _string_issues(kit.spawn.anchor_id, "spawn.anchor_id")
        issues += _typed_vec_issues(kit.spawn.position, "spawn.position")
        issues += _typed_vec_issues(kit.spawn.rotation, "spawn.rotation")
        issues += _position_issues(
            {"x": kit.spawn.position.x, "y": kit.spawn.position.y, "z": kit.spawn.position.z},
            "spawn.position",
        )
        issues += _rotation_issues(
            {"x": kit.spawn.rotation.x, "y": kit.spawn.rotation.y, "z": kit.spawn.rotation.z},
            "spawn.rotation",
        )
        spawn_anchor = next(
            (a for a in kit.anchors if a.anchor_id == kit.spawn.anchor_id), None
        )
        if spawn_anchor is None:
            issues.append(
                f"spawn.anchor_id {kit.spawn.anchor_id!r} does not reference a declared anchor"
            )
        elif spawn_anchor.type != "PLAYER_SPAWN":
            issues.append(
                f"spawn.anchor_id {kit.spawn.anchor_id!r} must reference an anchor of "
                "type PLAYER_SPAWN"
            )
        for anchor in kit.anchors:
            if anchor.anchor_id == kit.spawn.anchor_id:
                continue
            if kit.spawn.position.distance_to(anchor.position) < SPAWN_CLEARANCE:
                issues.append(
                    f"spawn is within {SPAWN_CLEARANCE:g} of anchor {anchor.anchor_id!r} "
                    "(spawn must not intersect geometry)"
                )

    if not isinstance(kit.lighting, LightingSpec):
        issues.append("lighting must be a LightingSpec")
    else:
        if kit.lighting.profile not in LIGHTING_PROFILES:
            issues.append(
                f"lighting.profile {kit.lighting.profile!r} is not in "
                f"{list(LIGHTING_PROFILES)!r}"
            )
        for name in ("key_intensity", "hemi_intensity"):
            value = getattr(kit.lighting, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (
                0.0 <= float(value) <= 1.0
            ):
                issues.append(f"lighting.{name} must be within [0, 1]")
        if not isinstance(kit.lighting.accent_color, str) or not _COLOR_PATTERN.match(
            kit.lighting.accent_color
        ):
            issues.append("lighting.accent_color must be a #RRGGBB hex color")

    if not isinstance(kit.structural_assets, (list, tuple)) or not kit.structural_assets:
        issues.append("structural_assets must be a non-empty sequence of asset ids")
    else:
        if len(kit.structural_assets) < 6:
            issues.append("structural_assets must contain at least 6 entries")
        try:
            catalog = load_catalog_from_repo()
        except Exception:  # noqa: BLE001 - validation must never raise
            catalog = None
        if catalog is not None:
            for asset_id in kit.structural_assets:
                if not isinstance(asset_id, str) or asset_id not in catalog.by_id:
                    issues.append(
                        f"structural asset {asset_id!r} does not exist in the asset catalog"
                    )
        for asset_id in kit.structural_assets:
            issues += _string_issues(asset_id, "structural_assets entry")

    issues += _string_issues(kit.style_hint, "style_hint", allow_none=True)

    if not isinstance(kit.default_anchor_coverage, Mapping):
        issues.append("default_anchor_coverage must be a mapping")
    elif not isinstance(kit.anchors, (list, tuple)):
        issues.append("default_anchor_coverage cannot be checked without anchors")
    else:
        anchor_id_set = {a.anchor_id for a in kit.anchors}
        covered_types: set[str] = set()
        for anchor_type, anchor_list in kit.default_anchor_coverage.items():
            if anchor_type not in ANCHOR_TYPES:
                issues.append(
                    f"default_anchor_coverage: unknown anchor type {anchor_type!r}"
                )
                continue
            if not isinstance(anchor_list, (list, tuple)):
                issues.append(
                    f"default_anchor_coverage[{anchor_type}] must be a sequence of anchorIds"
                )
                continue
            seen: set[str] = set()
            for anchor_id in anchor_list:
                if not isinstance(anchor_id, str) or not anchor_id:
                    issues.append(
                        f"default_anchor_coverage[{anchor_type}] entry must be a non-empty string"
                    )
                elif anchor_id in seen:
                    issues.append(
                        f"default_anchor_coverage[{anchor_type}]: duplicate anchorId {anchor_id!r}"
                    )
                elif anchor_id not in anchor_id_set:
                    issues.append(
                        f"default_anchor_coverage[{anchor_type}]: unknown anchorId {anchor_id!r}"
                    )
                else:
                    seen.add(anchor_id)
            if anchor_list:
                covered_types.add(anchor_type)
        for anchor_type in sorted(REQUIRED_COVERAGE_TYPES - covered_types):
            issues.append(
                f"default_anchor_coverage: required anchor type {anchor_type!r} "
                "has no default coverage"
            )
        secondary_covered = len(set(SECONDARY_COVERAGE_TYPES) & covered_types)
        if secondary_covered < MIN_SECONDARY_COVERED:
            issues.append(
                f"default_anchor_coverage: at least {MIN_SECONDARY_COVERED} of the "
                f"secondary types {list(SECONDARY_COVERAGE_TYPES)!r} must have "
                "default coverage"
            )

    return tuple(sorted(set(issues)))


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def _default_environments_dir() -> Path:
    """<repo>/assets/environments resolved from the package location."""
    from app.core.config import REPO_ROOT

    return REPO_ROOT / "assets" / "environments"


def _descriptor_from(item: Mapping[str, Any]) -> EnvironmentKit:
    """Build a typed descriptor from an already-validated manifest entry."""
    return EnvironmentKit(
        environment_id=str(item["environmentId"]),
        version=int(item["version"]),
        canonical_name=str(item["canonicalName"]),
        aliases=tuple(str(a) for a in item["aliases"]),
        tags=tuple(str(t) for t in item["tags"]),
        zones=tuple(
            ZoneSpec(
                zone_id=str(zone["zoneId"]),
                label=str(zone["label"]),
                rooms=tuple(str(room) for room in zone["rooms"]),
            )
            for zone in item["zones"]
        ),
        anchors=tuple(
            AnchorSpec(
                anchor_id=str(anchor["anchorId"]),
                type=str(anchor["type"]),
                zone_id=str(anchor["zoneId"]),
                position=Vec3(
                    x=float(anchor["position"]["x"]),
                    y=float(anchor["position"]["y"]),
                    z=float(anchor["position"]["z"]),
                ),
                rotation=Vec3(
                    x=float(anchor["rotation"]["x"]),
                    y=float(anchor["rotation"]["y"]),
                    z=float(anchor["rotation"]["z"]),
                ),
                allowed_categories=tuple(
                    str(c) for c in anchor["allowedCategories"]
                ),
                exclusive=bool(anchor["exclusive"]),
                required=bool(anchor["required"]),
            )
            for anchor in item["anchors"]
        ),
        spawn=SpawnSpec(
            anchor_id=str(item["spawn"]["anchorId"]),
            position=Vec3(
                x=float(item["spawn"]["position"]["x"]),
                y=float(item["spawn"]["position"]["y"]),
                z=float(item["spawn"]["position"]["z"]),
            ),
            rotation=Vec3(
                x=float(item["spawn"]["rotation"]["x"]),
                y=float(item["spawn"]["rotation"]["y"]),
                z=float(item["spawn"]["rotation"]["z"]),
            ),
        ),
        lighting=LightingSpec(
            profile=str(item["lighting"]["profile"]),
            key_intensity=float(item["lighting"]["keyIntensity"]),
            hemi_intensity=float(item["lighting"]["hemiIntensity"]),
            accent_color=str(item["lighting"]["accentColor"]),
        ),
        structural_assets=tuple(str(a) for a in item["structuralAssets"]),
        style_hint=(
            str(item["styleHint"]) if item.get("styleHint") is not None else None
        ),
        default_anchor_coverage={
            str(k): tuple(str(v) for v in value)
            for k, value in item["defaultAnchorCoverage"].items()
        },
    )


def load_environment(
    environment_id: str, directory: str | Path | None = None
) -> EnvironmentKit:
    """Load (and fully validate) one kit by its exact environmentId.

    Raises ``EnvironmentValidationError`` when any shipped manifest fails
    validation and ``EnvironmentNotFoundError`` when no kit declares
    ``environment_id``.
    """
    kits = load_all_environments(directory=directory)
    for kit in kits:
        if kit.environment_id == environment_id:
            return kit
    raise EnvironmentNotFoundError(
        f"no environment kit declares environmentId {environment_id!r}"
    )


@lru_cache(maxsize=8)
def _load_kits_cached(dir_text: str) -> tuple[EnvironmentKit, ...]:
    directory = Path(dir_text)
    issues: list[str] = []
    kits: list[EnvironmentKit] = []
    try:
        catalog = load_catalog_from_repo()
    except Exception:  # noqa: BLE001 - surfaces as a validation issue
        catalog = None
        issues.append("asset catalog unavailable for environment cross-checks")
    try:
        manifest_paths = sorted(
            path for path in directory.glob("*.json") if path.is_file()
        )
    except OSError as exc:
        raise EnvironmentError(f"cannot list environment manifests: {exc}") from exc
    for path in manifest_paths:
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            issues.append(f"{path.name}: cannot read manifest: {exc}")
            continue
        try:
            data = json.loads(raw_text)
        except ValueError as exc:
            issues.append(f"{path.name}: not valid JSON: {exc}")
            continue
        file_issues = validate_environment_data(data, catalog=catalog)
        if file_issues:
            issues.extend(f"{path.name}: {issue}" for issue in file_issues)
            continue
        try:
            kit = _descriptor_from(data)
        except (TypeError, ValueError, EnvironmentValidationError) as exc:
            issues.append(f"{path.name}: invalid kit: {exc}")
            continue
        kits.append(kit)
    # Cross-kit identity: duplicate environmentIds across manifests.
    seen_ids: set[str] = set()
    for kit in sorted(kits, key=lambda k: k.environment_id):
        if kit.environment_id in seen_ids:
            issues.append(
                f"duplicate environmentId {kit.environment_id!r} across manifests"
            )
        seen_ids.add(kit.environment_id)
    if issues:
        raise EnvironmentValidationError(tuple(sorted(set(issues))))
    return tuple(sorted(kits, key=lambda kit: kit.environment_id))


def load_all_environments(
    directory: str | Path | None = None,
) -> tuple[EnvironmentKit, ...]:
    """Load and validate every kit in the repo environments directory.

    Deterministic: kits are returned sorted by ``environmentId``. Real
    manifests must load with ZERO issues (enforced); the result is cached per
    directory (manifests are immutable in practice).
    """
    if directory is None:
        directory = _default_environments_dir()
    return _load_kits_cached(str(Path(directory)))


__all__ = [
    "ANCHOR_TYPES",
    "AnchorSpec",
    "EnvironmentError",
    "EnvironmentKit",
    "EnvironmentNotFoundError",
    "EnvironmentValidationError",
    "LIGHTING_PROFILES",
    "LightingSpec",
    "MAX_ANCHORS",
    "MAX_STRING_LENGTH",
    "MAX_ZONES",
    "SpawnSpec",
    "Vec3",
    "ZoneSpec",
    "load_all_environments",
    "load_environment",
    "validate_environment_data",
]