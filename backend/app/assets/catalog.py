"""Asset Oracle catalog: typed descriptors, manifest loader, load validation.

Phase 10 / REQUIREMENTS-compatible: the catalog manifest at
``assets/catalog/catalog.json`` is the SINGLE source of truth for logical
asset ids and their safe render metadata. This module:

- defines the typed, frozen descriptor model (``Catalog`` / ``AssetDescriptor``
  / ``Dimensions``) and the Phase 12 bounded variant model (``VariantSpec`` /
  ``VariantParamSpec``);
- validates the RAW manifest at load time with deterministic, sorted issue
  strings (duplicate ids, conflicting/confusable identity claims — canonical
  name / aliases / assetId normal forms all share ONE identity namespace, id
  pattern, string safety, dimension/color bounds, interaction + category +
  render-kind + composite-kind vocabularies, composite-kind consistency,
  array-size bounds, fallback neutrality, composite ``templateId`` membership
  in the FROZEN ``TEMPLATE_VOCABULARY`` and the bounded §Variant schema rules);
- enforces the SAME cross-set/global invariants inside ``Catalog.__post_init__``
  so direct construction can never bypass validation (DEF-063);
- NEVER performs network I/O and NEVER dynamically loads anything: the only
  input is a local JSON file, and unresolved requests always end in the
  explicit neutral fallback asset (never arbitrary content).

``load_catalog`` raises ``CatalogValidationError`` (a ``CatalogError``)
carrying the sorted issues when the manifest is invalid; unreadable/corrupt
JSON raises ``CatalogError``. ``validate_catalog_data`` is the non-raising
pure validator also used directly by tests.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.generation.constraints import normalize_motive_text
from app.generation.safety import INTERACTION_ALLOWLIST

# assetId grammar (mirrors app.generation.safety._ASSET_ID_PATTERN).
_ASSET_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+$")
# RGB hex color grammar, e.g. "#c8ccd4".
_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Windows drive-letter absolute path prefix, e.g. "C:\" / "C:/".
_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")

# The EXACT documented key set of every asset descriptor (schema contract).
DOCUMENTED_ASSET_KEYS: frozenset[str] = frozenset(
    {
        "assetId",
        "version",
        "canonicalName",
        "aliases",
        "category",
        "subtype",
        "tags",
        "renderKind",
        "compositeKind",
        "templateId",
        "dimensions",
        "colors",
        "label",
        "interactable",
        "supportedInteractions",
        "evidenceCapabilities",
        "allowedAnchors",
        "variants",
    }
)

# Phase 12 keys that MAY be absent from a manifest entry. The Phase 10/11
# non-composite entries keep their byte-stable shape and simply do not declare
# them; every COMPOSITE entry MUST declare ``templateId`` (and MAY declare
# ``variants``). ``DOCUMENTED_ASSET_KEYS`` is the full documented schema;
# anything outside it is still an unknown-key issue.
OPTIONAL_ASSET_KEYS: frozenset[str] = frozenset({"templateId", "variants"})
REQUIRED_ASSET_KEYS: frozenset[str] = DOCUMENTED_ASSET_KEYS - OPTIONAL_ASSET_KEYS

# Documented category vocabulary (Phase 12 will widen the ~20-object coverage;
# the manifest is the app-owned authority and must stay inside this set).
CATEGORY_ALLOWLIST: tuple[str, ...] = (
    "evidence",
    "electronics",
    "furniture",
    "structural",
    "character",
    "decor",
    "utility",
)

# Documented render-kind vocabulary — the primitive kinds the renderer knows
# (frontend/src/scene/assetRegistry.ts) plus the composite builder kind.
RENDER_KIND_ALLOWLIST: tuple[str, ...] = (
    "box",
    "cylinder",
    "sphere",
    "flat",
    "composite",
)

# Composite builders the frontend renderer can actually build (DEF-059). The
# set is the exact mirror of the frontend's compose-able composite kinds; any
# other value is a load issue. ``renderKind == "composite"`` REQUIRES a value
# from this vocabulary; the non-composite render kinds require ``null``.
COMPOSITE_KIND_ALLOWLIST: tuple[str, ...] = (
    "kitchen_knife",
    "letter_opener",
    "scissors",
    "laptop",
    "victim",
    "table",
)

# --------------------------------------------------------------------------- #
# Phase 12 — frozen template vocabulary + bounded declarative variants.
# --------------------------------------------------------------------------- #
#
# ``TEMPLATE_VOCABULARY`` is FROZEN (Phase 12 Track B builds the frontend
# renderer builders for EXACTLY these templates). A composite asset's
# ``templateId`` is a stable logical template the frontend compiles into
# primitives; every composite MUST reference one of these, and any
# non-composite asset MUST NOT declare a ``templateId``. Each template implies
# a silhouette; the frontend holds the exact part data.
TEMPLATE_VOCABULARY: tuple[str, ...] = (
    "blade_chef",
    "blade_bread",
    "blade_letter",
    "tool_screwdriver",
    "tool_hammer",
    "tool_wrench",
    "tool_bat",
    "blades_scissor",
    "bottle_glass",
    "rope_coil",
    "key_small",
    "usb_stick",
    "wallet_flat",
    "watch_round",
    "bottle_med",
    "glove_flat",
    "marker_flat",
    "phone_body",
    "camera_body",
    "box_jewelry",
    "table_form",
    "chair_frame",
    "sofa_form",
    "bed_form",
    "nightstand",
    "shelf_rack",
    "cabinet_box",
    "locker_box",
    "crate_box",
    "monitor_stand",
    "keyboard_slab",
    "tablet_flat",
    "cctv_cam",
    "router_box",
    "reader_panel",
    "printer_box",
    "tv_screen",
    "docs_flat",
    "folder_flat",
    "card_flat",
    "notebook_doc",
    "lamp_profile",
    "picture_frame",
    "plant_pot",
    "cup_form",
    "plate_flat",
    "clock_round",
    "pen_stick",
    "handbag_form",
    "coat_hang",
    "safe_box",
    "switch_panel",
    "trash_bin",
    "sink_bowl",
    "counter_top",
    "storage_box",
    "window_flat",
    "wall_panel",
    "door_slab",
)

# §Variant — the bounded declarative variant parameter surface. Every variant
# parameter key is one of these; the value is either null (not controlled) or
# a bounded spec object. Materials/states come ONLY from the frozen safe
# literal vocabularies below (no URLs, paths, scripts or arbitrary tokens).
VARIANT_PARAM_KINDS: tuple[str, ...] = ("color", "material", "scale", "state")

MATERIAL_VOCABULARY: tuple[str, ...] = (
    "wood.dark",
    "wood.light",
    "metal.brass",
    "metal.steel",
    "plastic",
    "fabric",
    "leather",
    "ceramic",
)

STATE_VOCABULARY: tuple[str, ...] = (
    "clean",
    "weathered",
    "damaged",
    "open",
    "closed",
    "on",
    "off",
)

# Variant-name grammar: ^[a-z0-9_]{1,24}$.
VARIANT_NAME_PATTERN = re.compile(r"^[a-z0-9_]{1,24}$")
MAX_VARIANTS_PER_ASSET = 3
MAX_VARIANT_ALLOWLIST = 16
# Scale bounds (declared min/max must stay inside these category-safe limits).
SCALE_MIN_BOUND = 0.5
SCALE_MAX_BOUND = 2.0

# Forbidden URL schemes checked as substring scans on catalog strings (the
# catalog is app-owned, but reject any embedding up front: no generated string
# may ever smuggle a URL/path into the manifest).
_FORBIDDEN_URL_TOKENS: tuple[str, ...] = (
    "http://",
    "https://",
    "data:",
    "file:",
    "javascript:",
)

MAX_CATALOG_STRING_LENGTH = 120
ASSET_VERSION_MIN = 1
DIMENSION_MIN_EXCLUSIVE = 0.0
DIMENSION_MAX_EXCLUSIVE = 100.0
# Array-size bounds for the manifest (DEF-060). ``styleHints`` is a REQUEST-
# side field (AssetRequest), NOT a manifest key; its limit lives in
# app.assets.validation (MAX_STYLE_HINTS = 16).
MAX_ALIASES = 16
MAX_TAGS = 16
MAX_SUPPORTED_INTERACTIONS = 8
MAX_EVIDENCE_CAPABILITIES = 8
MAX_ALLOWED_ANCHORS = 16
MAX_COLORS = 16

# The Phase 11 semantic-anchor TYPE vocabulary is defined in
# app.environments.manifests (which imports THIS module), so it is fetched
# lazily — never at module import time — avoiding an import cycle while still
# enforcing that every ``allowedAnchors`` member is a real anchor type.
_ANCHOR_TYPES_CACHE: tuple[str, ...] | None = None


def _anchor_type_vocabulary() -> tuple[str, ...]:
    global _ANCHOR_TYPES_CACHE
    if _ANCHOR_TYPES_CACHE is None:
        from app.environments.manifests import ANCHOR_TYPES

        _ANCHOR_TYPES_CACHE = ANCHOR_TYPES
    return _ANCHOR_TYPES_CACHE


def _anchor_list_issues(value: Any, where: str) -> list[str]:
    """allowedAnchors: non-empty strings, bounded, ANCHOR_TYPES membership."""
    issues = _string_list_issues(value, where, MAX_ALLOWED_ANCHORS)
    if isinstance(value, (list, tuple)):
        vocabulary = _anchor_type_vocabulary()
        for anchor in value:
            if isinstance(anchor, str) and anchor not in vocabulary:
                issues.append(
                    f"{where}: anchor {anchor!r} is not in ANCHOR_TYPES"
                )
    return issues


def normalize_asset_text(text: str) -> str:
    """The SINGLE identity-normalization form for the Asset Oracle.

    Shares the constraints engine's normalization concept
    (``normalize_motive_text``: Unicode casefold, then keep ONLY ASCII letters,
    digits and currency symbols) but FIRST canonically decomposes the text
    (NFKD). The decomposition step is required for deterministic, confusable-
    safe identity matching: an accented lookalike of an ASCII letter (e.g.
    "Kïtchen" — U+00EF) then maps to its ASCII base ("kitchen"), so the
    mandated Unicode normalization tests resolve deterministically and Unicode
    confusables can never silently change which asset matches. The constraints
    module itself is NOT changed; this engine localizes the refinement.

    "KITCHEN KNIFE" / "Kïtchen knife" / "kitchen_knife" all normalize to
    ``kitchenknife``; "€240,000 embezzlement" -> ``€240000embezzlement``.
    """
    if not isinstance(text, str):
        return ""
    return normalize_motive_text(unicodedata.normalize("NFKD", text))


class CatalogError(Exception):
    """Base class for catalog loading/validation failures."""


class CatalogValidationError(CatalogError):
    """The manifest failed load-time validation (deterministic sorted issues)."""

    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = tuple(sorted(set(issues)))
        super().__init__("; ".join(self.issues))


@dataclass(frozen=True)
class Dimensions:
    """Bounding-box dimensions in world metres (all > 0, < 100)."""

    x: float
    y: float
    z: float


@dataclass(frozen=True)
class VariantParamSpec:
    """One bounded variant parameter of an asset variant (§Variant).

    ``kind`` is one of ``VARIANT_PARAM_KINDS``. ``allowlist`` is the declared
    safe-literal set (hex colors for ``color``; frozen material/state tokens
    for ``material``/``state``; empty for ``scale``). ``default`` is the
    declared default (a hex, a token, or the scale float). ``min_value`` and
    ``max_value`` bound ``scale`` only (always None otherwise).
    """

    kind: str
    allowlist: tuple[str, ...]
    default: str | float | None
    min_value: float | None = None
    max_value: float | None = None


@dataclass(frozen=True)
class VariantSpec:
    """One named, bounded declarative variant of a composite asset (§Variant).

    ``params`` maps variant parameter keys (from ``VARIANT_PARAM_KINDS``) to
    their bounded specs. Adding a variant NEVER changes the assetId/version
    semantics: variants are purely descriptive render presets.
    """

    name: str
    params: Mapping[str, VariantParamSpec]


@dataclass(frozen=True)
class AssetDescriptor:
    """One typed, application-owned catalog asset (immutable)."""

    asset_id: str
    version: int
    canonical_name: str
    aliases: tuple[str, ...]
    category: str
    subtype: str
    tags: tuple[str, ...]
    render_kind: str
    composite_kind: str | None
    dimensions: Dimensions
    colors: Mapping[str, str]
    label: str
    interactable: bool
    supported_interactions: tuple[str, ...]
    evidence_capabilities: tuple[str, ...]
    allowed_anchors: tuple[str, ...]
    # Phase 12 — stable logical template (composite assets only) + bounded
    # declarative variants. Both default empty/None so the Phase 10/11 typed
    # construction paths and descriptor mutations keep working unchanged.
    template_id: str | None = None
    variants: tuple[VariantSpec, ...] = ()


@dataclass(frozen=True)
class Catalog:
    """A fully validated, immutable catalog.

    ``assets`` preserves the manifest's byte-stable order (deterministic
    resolution depends on it). ``by_id`` is a convenience index over the same
    assets (identity always agrees with the descriptor tuples).

    Construction is NOT bypassable (DEF-063): ``__post_init__`` re-runs the
    same cross-set/global invariant checks the raw validator applies, so a
    Catalog with colliding identities, a non-neutral fallback, an unknown
    composite kind or oversized arrays cannot be built — whether it comes from
    ``load_catalog`` or a caller's own dataclass construction.
    """

    catalog_version: int
    fallback_asset: str
    assets: tuple[AssetDescriptor, ...]

    by_id: Mapping[str, AssetDescriptor] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        issues = _validate_catalog_invariants(
            self.catalog_version, self.fallback_asset, self.assets
        )
        if issues:
            raise CatalogValidationError(issues)
        object.__setattr__(
            self, "by_id", MappingProxyType({a.asset_id: a for a in self.assets})
        )


# --------------------------------------------------------------------------- #
# string-safety helpers (catalog-side; the RAW request path has its own
# dedicated security validator in app.assets.validation)
# --------------------------------------------------------------------------- #


def _string_safety_issues(value: str, where: str) -> list[str]:
    """Return deterministic issues for one catalog string (empty when safe)."""
    issues: list[str] = []
    if len(value) > MAX_CATALOG_STRING_LENGTH:
        issues.append(
            f"{where}: string exceeds {MAX_CATALOG_STRING_LENGTH} characters"
        )
    if any(ord(ch) < 0x20 for ch in value):
        issues.append(f"{where}: contains control characters")
    lower = value.casefold()
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


def _dimension_issues(value: Any, where: str) -> list[str]:
    """Dimension tuple validation: {x, y, z} numeric, positive, bounded."""
    issues: list[str] = []
    if not isinstance(value, Mapping):
        issues.append(f"{where}: dimensions must be an object {{x, y, z}}")
        return issues
    if set(value.keys()) != {"x", "y", "z"}:
        issues.append(f"{where}: dimensions must have exactly the keys x, y, z")
        return issues
    for axis in ("x", "y", "z"):
        num = value[axis]
        if isinstance(num, bool) or not isinstance(num, (int, float)):
            issues.append(
                f"{where}.{axis}: must be a number (got {type(num).__name__})"
            )
            continue
        magnitude = float(num)
        if not (DIMENSION_MIN_EXCLUSIVE < magnitude < DIMENSION_MAX_EXCLUSIVE):
            issues.append(
                f"{where}.{axis}: must be > 0 and < {DIMENSION_MAX_EXCLUSIVE:.0f} "
                f"(got {magnitude:g})"
            )
    return issues


def _color_issues(value: Any, where: str) -> list[str]:
    """Color map validation: non-empty, bounded, keys+values safe, #RRGGBB."""
    issues: list[str] = []
    if not isinstance(value, Mapping) or not value:
        issues.append(f"{where}: colors must be a non-empty object")
        return issues
    if len(value) > MAX_COLORS:
        issues.append(f"{where}: colors exceeds the maximum of {MAX_COLORS} entries")
    for name, hex_value in value.items():
        if not isinstance(name, str) or not name:
            issues.append(f"{where}: color names must be non-empty strings")
        else:
            issues.extend(_string_safety_issues(name, f"{where} color {name!r}"))
        if not isinstance(hex_value, str):
            issues.append(f"{where} color {name!r}: must be a string")
        elif not _COLOR_PATTERN.match(hex_value):
            issues.append(
                f"{where} color {name!r}: value {hex_value!r} is not #RRGGBB"
            )
    return issues


# --------------------------------------------------------------------------- #
# Phase 12 §Variant — bounded declarative variant validation
# --------------------------------------------------------------------------- #
#
# Rules (identical for the RAW manifest form and the typed constructor form):
# - ``variants`` is an optional array of at most MAX_VARIANTS_PER_ASSET named
#   variants; every name matches ^[a-z0-9_]{1,24}$;
# - each variant's ``params`` object only accepts the four VARIANT_PARAM_KINDS
#   keys (unknown param key -> issue), each value either null or a bounded spec;
# - color/material/state specs require {allowlist, default}; every allowlist
#   entry is a DECLARED SAFE LITERAL (hex color, or a frozen material/state
#   token — no URLs/paths/scripts), the default must be one of the entries and
#   material/state entries must come from the frozen vocabularies;
# - scale specs require {min, max, default} with 0.5 <= min <= default <=
#   max <= 2.0 (category-safe limits; an extreme scale declaration is a load
#   issue, so a variant can never escape its bounds);
# - within ONE asset every variant must agree on a param key's allowlist (and
#   scale bounds); only the DEFAULT may differ between the named presets. This
#   keeps ``apply_variant`` unambiguous ("the whole catalog shares one schema").


def _variant_param_spec_issues(kind: str, spec: Any, where: str) -> list[str]:
    """Validate ONE non-null variant-parameter spec object."""
    issues: list[str] = []
    if not isinstance(spec, Mapping):
        return [f"{where}: variant parameter {kind!r} must be an object or null"]
    if kind == "scale":
        if set(spec.keys()) != {"min", "max", "default"}:
            issues.append(f"{where}: scale requires exactly the keys min, max, default")
            return issues
        numbers: dict[str, float] = {}
        for key in ("min", "max", "default"):
            num = spec[key]
            if isinstance(num, bool) or not isinstance(num, (int, float)):
                issues.append(f"{where}.{key}: must be a number")
                continue
            magnitude = float(num)
            if not (SCALE_MIN_BOUND <= magnitude <= SCALE_MAX_BOUND):
                issues.append(
                    f"{where}.{key}: must be within "
                    f"[{SCALE_MIN_BOUND:g}, {SCALE_MAX_BOUND:g}] (got {magnitude:g})"
                )
            else:
                numbers[key] = magnitude
        if len(numbers) == 3:
            min_value, max_value, default = (
                numbers["min"],
                numbers["max"],
                numbers["default"],
            )
            if min_value > max_value:
                issues.append(f"{where}: min must be <= max")
            if not (min_value <= default <= max_value):
                issues.append(f"{where}: default must be within [min, max]")
        return issues

    if set(spec.keys()) != {"allowlist", "default"}:
        issues.append(
            f"{where}: {kind!r} requires exactly the keys allowlist, default"
        )
        return issues

    allowlist = spec["allowlist"]
    if not isinstance(allowlist, (list, tuple)) or not allowlist:
        issues.append(f"{where}.allowlist: must be a non-empty array")
    elif len(allowlist) > MAX_VARIANT_ALLOWLIST:
        issues.append(
            f"{where}.allowlist: exceeds the maximum of "
            f"{MAX_VARIANT_ALLOWLIST} entries"
        )
    else:
        for index, entry in enumerate(allowlist):
            entry_where = f"{where}.allowlist[{index}]"
            if not isinstance(entry, str) or not entry:
                issues.append(f"{entry_where}: must be a non-empty string")
                continue
            issues.extend(_string_safety_issues(entry, entry_where))
            if kind == "color":
                if not _COLOR_PATTERN.match(entry):
                    issues.append(f"{entry_where}: {entry!r} is not a #RRGGBB color")
            elif kind == "material":
                if entry not in MATERIAL_VOCABULARY:
                    issues.append(
                        f"{entry_where}: {entry!r} is not in MATERIAL_VOCABULARY"
                    )
            elif kind == "state":
                if entry not in STATE_VOCABULARY:
                    issues.append(
                        f"{entry_where}: {entry!r} is not in STATE_VOCABULARY"
                    )

    default = spec["default"]
    if not isinstance(default, str) or not default:
        issues.append(f"{where}.default: must be a non-empty string")
    else:
        issues.extend(_string_safety_issues(default, f"{where}.default"))
        if kind == "color" and not _COLOR_PATTERN.match(default):
            issues.append(f"{where}.default: {default!r} is not a #RRGGBB color")
        elif kind == "material" and default not in MATERIAL_VOCABULARY:
            issues.append(f"{where}.default: {default!r} is not in MATERIAL_VOCABULARY")
        elif kind == "state" and default not in STATE_VOCABULARY:
            issues.append(f"{where}.default: {default!r} is not in STATE_VOCABULARY")
        if (
            isinstance(allowlist, (list, tuple))
            and allowlist
            and default not in allowlist
        ):
            issues.append(f"{where}.default: must be present in the allowlist")
    return issues


def _variant_spec_signature(kind: str, spec: Any) -> tuple[Any, ...] | None:
    """Canonical per-param-key consistency signature (defaults NOT included:
    named presets may differ only in their defaults)."""
    try:
        if kind == "scale":
            return ("scale", float(spec["min"]), float(spec["max"]))
        return (kind, tuple(spec["allowlist"]))
    except (KeyError, TypeError, ValueError):
        return None


def _variant_issues(value: Any, where: str) -> list[str]:
    """Phase 12 §Variant — validate an asset's ``variants`` array."""
    issues: list[str] = []
    if value is None:
        return issues
    if not isinstance(value, (list, tuple)):
        issues.append(f"{where}.variants: must be an array")
        return issues
    if len(value) > MAX_VARIANTS_PER_ASSET:
        issues.append(
            f"{where}.variants: exceeds the maximum of "
            f"{MAX_VARIANTS_PER_ASSET} variants"
        )
    signatures: dict[str, tuple[Any, ...]] = {}
    for variant_index, variant in enumerate(value):
        vwhere = f"{where}.variants[{variant_index}]"
        if not isinstance(variant, Mapping):
            issues.append(f"{vwhere}: variant must be an object {{name, params}}")
            continue
        missing = sorted({"name", "params"} - set(variant.keys()))
        extra = sorted(set(variant.keys()) - {"name", "params"})
        if missing:
            issues.append(f"{vwhere}: missing required keys {missing!r}")
        if extra:
            issues.append(f"{vwhere}: unknown keys {extra!r}")
        name = variant.get("name")
        if not isinstance(name, str) or not VARIANT_NAME_PATTERN.match(name):
            issues.append(
                f"{vwhere}: variant name {name!r} must match "
                "^[a-z0-9_]{1,24}$"
            )
        params = variant.get("params")
        if not isinstance(params, Mapping):
            issues.append(f"{vwhere}.params: must be an object")
            continue
        for param_key, spec in params.items():
            pwhere = f"{vwhere}.params.{param_key}"
            if param_key not in VARIANT_PARAM_KINDS:
                issues.append(f"{pwhere}: unknown variant parameter {param_key!r}")
                continue
            if spec is None:
                continue
            issues.extend(_variant_param_spec_issues(param_key, spec, pwhere))
            signature = _variant_spec_signature(param_key, spec)
            if signature is None:
                continue
            if param_key in signatures and signatures[param_key] != signature:
                issues.append(
                    f"{pwhere}: conflicts with an earlier variant's "
                    f"{param_key!r} spec (allowlist/scale bounds must agree)"
                )
            else:
                signatures.setdefault(param_key, signature)
    return issues


def _typed_variant_issues(variants: Any, where: str) -> list[str]:
    """Typed-constructor twin of ``_variant_issues`` (VariantSpec objects)."""
    if variants is None:
        return []
    if not isinstance(variants, (list, tuple)):
        return [f"{where}.variants: must be an array"]
    raw: list[dict[str, Any]] = []
    for variant in variants:
        if not isinstance(variant, VariantSpec):
            return [f"{where}.variants: must contain VariantSpec objects"]
        params: dict[str, Any] = {}
        for key, spec in variant.params.items():
            if spec.kind == "scale":
                params[key] = {
                    "min": spec.min_value,
                    "max": spec.max_value,
                    "default": spec.default,
                }
            else:
                params[key] = {
                    "allowlist": list(spec.allowlist),
                    "default": spec.default,
                }
        raw.append({"name": variant.name, "params": params})
    return _variant_issues(raw, where)


def _string_list_issues(
    value: Any, where: str, max_len: int | None = None
) -> list[str]:
    """List-of-non-empty-strings validation with string-safety scans.

    When ``max_len`` is provided (DEF-060) an oversized array is an issue.
    """
    issues: list[str] = []
    if not isinstance(value, (list, tuple)):
        issues.append(f"{where}: must be an array of non-empty strings")
        return issues
    if max_len is not None and len(value) > max_len:
        issues.append(f"{where}: exceeds the maximum of {max_len} entries")
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry:
            issues.append(f"{where}[{index}]: must be a non-empty string")
        else:
            issues.extend(_string_safety_issues(entry, f"{where}[{index}]"))
    return issues


# --------------------------------------------------------------------------- #
# shared cross-asset identity checks (DEF-057)
# --------------------------------------------------------------------------- #
#
# Every identity-forming string of every asset shares ONE normal-form
# namespace: the assetId (by its own normal form), the canonicalName and each
# alias. Two different assets claiming the SAME normal form — alias/alias,
# canonical/canonical, alias vs another asset's canonical, alias vs another
# asset's assetId, canonical vs another asset's assetId — is an identity
# collision and a load issue. The SAME functions drive both the raw manifest
# validator and ``Catalog.__post_init__`` so the checks can never drift.


def _identity_issues(
    id_claims: list[tuple[int, str]],
    canonical_claims: list[tuple[int, str, str]],
    alias_claims: list[tuple[int, str, str]],
) -> list[str]:
    """Deterministic cross-asset identity-collision issues (empty when clean)."""
    issues: list[str] = []

    seen_ids: dict[str, int] = {}
    for index, asset_id in id_claims:
        if asset_id in seen_ids:
            issues.append(
                f"duplicate assetId {asset_id!r}: declared at assets[{seen_ids[asset_id]}] "
                f"and again at assets[{index}]"
            )
        else:
            seen_ids[asset_id] = index

    # Normal forms of assetIds (unique per id so far; collisions are also
    # self-reports, deterministic in manifest order).
    id_norms: dict[str, str] = {}
    id_norm_owners: dict[str, int] = {}
    for index, asset_id in id_claims:
        norm = normalize_asset_text(asset_id)
        if not norm:
            continue
        if norm in id_norms and id_norms[norm] != asset_id:
            issues.append(
                f"conflicting assetIds: {id_norms[norm]!r} (assets[{id_norm_owners[norm]}]) "
                f"and {asset_id!r} (assets[{index}]) both normalize to {norm!r}"
            )
        else:
            id_norms.setdefault(norm, asset_id)
            id_norm_owners.setdefault(norm, index)

    canonical_norms: dict[str, tuple[int, str, str]] = {}
    for index, asset_id, raw in canonical_claims:
        norm = normalize_asset_text(raw)
        if not norm:
            continue
        if norm in canonical_norms:
            other_index, other_id, other_raw = canonical_norms[norm]
            issues.append(
                f"conflicting canonical names: {raw!r} (assets[{index}] {asset_id!r}) "
                f"and {other_raw!r} (assets[{other_index}] {other_id!r}) both "
                f"normalize to {norm!r}"
            )
        else:
            canonical_norms[norm] = (index, asset_id, raw)
        if norm in id_norms and id_norms[norm] != asset_id:
            issues.append(
                f"conflicting identities: canonicalName {raw!r} "
                f"(assets[{index}] {asset_id!r}) normalizes to {norm!r}, which is "
                f"the normal form of assetId {id_norms[norm]!r} "
                f"(assets[{id_norm_owners[norm]}])"
            )

    alias_norms: dict[str, list[tuple[int, str, str]]] = {}
    for index, asset_id, raw in alias_claims:
        norm = normalize_asset_text(raw)
        if not norm:
            continue
        alias_norms.setdefault(norm, []).append((index, asset_id, raw))

    for norm in sorted(alias_norms):
        claims = alias_norms[norm]
        # Aliases that normalize identically WITHIN one asset are redundant
        # but harmless (e.g. "letteropener" and "letter_opener"): only claims
        # spanning DIFFERENT assets are a confusable conflict.
        if len({asset_id for _index, asset_id, _raw in claims}) > 1:
            described = " and ".join(
                f"{raw!r} (assets[{index}] {asset_id!r})"
                for index, asset_id, raw in claims
            )
            issues.append(
                f"conflicting aliases: {described} all normalize to {norm!r}"
            )
            continue
        index, asset_id, raw = claims[0]
        if norm in id_norms and id_norms[norm] != asset_id:
            issues.append(
                f"conflicting identities: alias {raw!r} (assets[{index}] {asset_id!r}) "
                f"normalizes to {norm!r}, which is the normal form of assetId "
                f"{id_norms[norm]!r} (assets[{id_norm_owners[norm]}])"
            )
            continue
        canonical_claim = canonical_norms.get(norm)
        if canonical_claim is not None and canonical_claim[1] != asset_id:
            other_index, other_id, _other_raw = canonical_claim
            issues.append(
                f"conflicting aliases: alias {raw!r} (assets[{index}] {asset_id!r}) "
                f"normalizes to {norm!r}, which is the canonical name of "
                f"assets[{other_index}] {other_id!r}"
            )

    return issues


def _fallback_issues(
    fallback_asset: Any,
    declared_meta: Mapping[str, tuple[Any, Any]],
) -> list[str]:
    """DEF-058: the fallback must be a declared, NEUTRAL asset.

    ``declared_meta`` maps every declared assetId to its raw
    ``(category, interactable)`` values. A usable fallback must be an actual
    asset with ``category == "utility"`` and ``interactable is False`` —
    otherwise an unresolved request would resolve to an interactable,
    renderable object instead of a neutral placeholder.
    """
    issues: list[str] = []
    if not isinstance(fallback_asset, str) or not fallback_asset:
        return ["catalog document: fallbackAsset must be a non-empty string"]
    if fallback_asset not in declared_meta:
        return [
            f"catalog document: fallbackAsset {fallback_asset!r} is not a "
            "declared assetId"
        ]
    category, interactable = declared_meta[fallback_asset]
    if category != "utility":
        issues.append(
            f"catalog document: fallbackAsset {fallback_asset!r} has category "
            f"{category!r}; a neutral fallback must have category 'utility'"
        )
    if interactable is not False:
        issues.append(
            f"catalog document: fallbackAsset {fallback_asset!r} is interactable; "
            "a neutral fallback must be non-interactable"
        )
    return issues


# --------------------------------------------------------------------------- #
# deterministic raw-manifest validation
# --------------------------------------------------------------------------- #


def validate_catalog_data(data: Any) -> tuple[str, ...]:
    """Validate a raw catalog manifest; return deterministic sorted issues.

    Never raises. The full rule set:

    - schema keys, JSON types, string safety, dimensions, colors, vocabularies
      (category / interaction / render kind / composite kind), array-size
      bounds and the composite-kind consistency rule;
    - ONE identity normal-form namespace: duplicate assetIds and any
      canonical/alias/id cross-collision between DIFFERENT assets (DEF-057);
    - the fallback invariant (DEF-058): fallbackAsset must be a declared,
      ``utility``, non-interactable asset.
    """
    issues: list[str] = []
    if not isinstance(data, Mapping):
        return ("catalog document must be a JSON object",)

    if "catalogVersion" not in data:
        issues.append("catalog document: missing required key 'catalogVersion'")
    else:
        version = data["catalogVersion"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            issues.append(
                "catalog document: catalogVersion must be a positive integer"
            )

    fallback_asset = data.get("fallbackAsset")
    if not isinstance(fallback_asset, str) or not fallback_asset:
        issues.append(
            "catalog document: fallbackAsset must be a non-empty string"
        )

    assets = data.get("assets")
    if not isinstance(assets, list):
        issues.append("catalog document: 'assets' must be an array")
        return tuple(sorted(set(issues)))

    # Per-asset field validation; cross-asset identity checks run afterwards.
    id_claims: list[tuple[int, str]] = []
    canonical_claims: list[tuple[int, str, str]] = []  # (index, asset_id, raw)
    alias_claims: list[tuple[int, str, str]] = []  # (index, asset_id, raw)
    declared_meta: dict[str, tuple[Any, Any]] = {}  # id -> (category, interactable)

    for index, item in enumerate(assets):
        where = f"assets[{index}]"
        if not isinstance(item, Mapping):
            issues.append(f"{where}: asset entry must be a JSON object")
            continue

        missing = sorted(REQUIRED_ASSET_KEYS - set(item.keys()))
        extra = sorted(set(item.keys()) - DOCUMENTED_ASSET_KEYS)
        if missing:
            issues.append(f"{where}: missing required keys {missing!r}")
        if extra:
            issues.append(f"{where}: unknown keys {extra!r}")

        asset_id = item.get("assetId")
        if not isinstance(asset_id, str) or not _ASSET_ID_PATTERN.match(asset_id):
            issues.append(
                f"{where}: assetId {asset_id!r} must match ^[A-Z][A-Z0-9_]+$"
            )
        else:
            id_claims.append((index, asset_id))
            declared_meta[asset_id] = (item.get("category"), item.get("interactable"))

        version = item.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            issues.append(f"{where}: version must be a positive integer")

        canonical = item.get("canonicalName")
        if not isinstance(canonical, str) or not canonical:
            issues.append(f"{where}: canonicalName must be a non-empty string")
        else:
            issues.extend(_string_safety_issues(canonical, f"{where}.canonicalName"))
            canonical_claims.append((index, asset_id, canonical))

        aliases = item.get("aliases")
        if not isinstance(aliases, (list, tuple)):
            issues.append(f"{where}: aliases must be an array of non-empty strings")
        else:
            issues.extend(
                _string_list_issues(aliases, f"{where}.aliases", MAX_ALIASES)
            )
            for raw in aliases:
                if isinstance(raw, str) and raw:
                    alias_claims.append((index, asset_id, raw))

        category = item.get("category")
        if not isinstance(category, str) or not category:
            issues.append(f"{where}: category must be a non-empty string")
        elif category not in CATEGORY_ALLOWLIST:
            issues.append(
                f"{where}: category {category!r} is not in the documented "
                f"category vocabulary {list(CATEGORY_ALLOWLIST)!r}"
            )

        subtype = item.get("subtype")
        if not isinstance(subtype, str) or not subtype:
            issues.append(f"{where}: subtype must be a non-empty string")
        else:
            issues.extend(_string_safety_issues(subtype, f"{where}.subtype"))

        issues.extend(
            _string_list_issues(item.get("tags"), f"{where}.tags", MAX_TAGS)
        )

        render_kind = item.get("renderKind")
        if not isinstance(render_kind, str) or not render_kind:
            issues.append(f"{where}: renderKind must be a non-empty string")
        elif render_kind not in RENDER_KIND_ALLOWLIST:
            issues.append(
                f"{where}: renderKind {render_kind!r} is not in the documented "
                f"render vocabulary {list(RENDER_KIND_ALLOWLIST)!r}"
            )
        composite_kind = item.get("compositeKind")
        if render_kind == "composite":
            # Phase 12: the composite builder is identified by ``templateId``
            # (frozen TEMPLATE_VOCABULARY). ``compositeKind`` is null unless
            # one of the six Phase 10/11 special builders is referenced.
            if composite_kind is not None:
                if not isinstance(composite_kind, str) or (
                    composite_kind not in COMPOSITE_KIND_ALLOWLIST
                ):
                    issues.append(
                        f"{where}: compositeKind {composite_kind!r} is not in "
                        f"the documented vocabulary "
                        f"{list(COMPOSITE_KIND_ALLOWLIST)!r}"
                    )
                else:
                    issues.extend(
                        _string_safety_issues(
                            composite_kind, f"{where}.compositeKind"
                        )
                    )
        elif composite_kind is not None:
            issues.append(
                f"{where}: compositeKind must be null unless renderKind is "
                "'composite'"
            )

        template_id = item.get("templateId")
        if render_kind == "composite":
            if not isinstance(template_id, str) or (
                template_id not in TEMPLATE_VOCABULARY
            ):
                issues.append(
                    f"{where}: renderKind 'composite' requires a templateId "
                    f"from TEMPLATE_VOCABULARY"
                )
            else:
                issues.extend(
                    _string_safety_issues(template_id, f"{where}.templateId")
                )
        elif template_id is not None:
            issues.append(
                f"{where}: templateId must be null unless renderKind is "
                "'composite'"
            )

        issues.extend(_variant_issues(item.get("variants"), where))

        issues.extend(_dimension_issues(item.get("dimensions"), where))
        issues.extend(_color_issues(item.get("colors"), where))

        label = item.get("label")
        if not isinstance(label, str) or not label:
            issues.append(f"{where}: label must be a non-empty string")
        else:
            issues.extend(_string_safety_issues(label, f"{where}.label"))

        interactable = item.get("interactable")
        if isinstance(interactable, bool) is False:
            issues.append(f"{where}: interactable must be a boolean")

        interactions = item.get("supportedInteractions")
        issues.extend(
            _string_list_issues(
                interactions, f"{where}.supportedInteractions",
                MAX_SUPPORTED_INTERACTIONS,
            )
        )
        if isinstance(interactions, (list, tuple)):
            for interaction in interactions:
                if isinstance(interaction, str) and interaction not in INTERACTION_ALLOWLIST:
                    issues.append(
                        f"{where}: interaction {interaction!r} is not in "
                        "INTERACTION_ALLOWLIST"
                    )

        issues.extend(
            _string_list_issues(
                item.get("evidenceCapabilities"),
                f"{where}.evidenceCapabilities",
                MAX_EVIDENCE_CAPABILITIES,
            )
        )
        issues.extend(
            _anchor_list_issues(item.get("allowedAnchors"), f"{where}.allowedAnchors")
        )

    issues.extend(_identity_issues(id_claims, canonical_claims, alias_claims))
    issues.extend(_fallback_issues(fallback_asset, declared_meta))

    return tuple(sorted(set(issues)))


# --------------------------------------------------------------------------- #
# typed invariant validation (DEF-063: construction cannot bypass validation)
# --------------------------------------------------------------------------- #


def _typed_descriptor_issues(descriptor: AssetDescriptor, index: int) -> list[str]:
    """Per-descriptor typed checks mirroring the raw validator's rules."""
    issues: list[str] = []
    where = f"assets[{index}]"
    if not isinstance(descriptor.asset_id, str) or not _ASSET_ID_PATTERN.match(
        descriptor.asset_id
    ):
        issues.append(
            f"{where}: assetId {descriptor.asset_id!r} must match ^[A-Z][A-Z0-9_]+$"
        )
    if (
        isinstance(descriptor.version, bool)
        or not isinstance(descriptor.version, int)
        or descriptor.version < 1
    ):
        issues.append(f"{where}: version must be a positive integer")
    canonical = descriptor.canonical_name
    if not isinstance(canonical, str) or not canonical:
        issues.append(f"{where}: canonicalName must be a non-empty string")
    else:
        issues.extend(_string_safety_issues(canonical, f"{where}.canonicalName"))
    issues.extend(
        _string_list_issues(descriptor.aliases, f"{where}.aliases", MAX_ALIASES)
    )
    category = descriptor.category
    if not isinstance(category, str) or not category:
        issues.append(f"{where}: category must be a non-empty string")
    elif category not in CATEGORY_ALLOWLIST:
        issues.append(
            f"{where}: category {category!r} is not in the documented "
            f"category vocabulary {list(CATEGORY_ALLOWLIST)!r}"
        )
    subtype = descriptor.subtype
    if not isinstance(subtype, str) or not subtype:
        issues.append(f"{where}: subtype must be a non-empty string")
    else:
        issues.extend(_string_safety_issues(subtype, f"{where}.subtype"))
    issues.extend(_string_list_issues(descriptor.tags, f"{where}.tags", MAX_TAGS))

    render_kind = descriptor.render_kind
    if not isinstance(render_kind, str) or not render_kind:
        issues.append(f"{where}: renderKind must be a non-empty string")
    elif render_kind not in RENDER_KIND_ALLOWLIST:
        issues.append(
            f"{where}: renderKind {render_kind!r} is not in the documented "
            f"render vocabulary {list(RENDER_KIND_ALLOWLIST)!r}"
        )
    composite_kind = descriptor.composite_kind
    if render_kind == "composite":
        if composite_kind is not None:
            if not isinstance(composite_kind, str) or (
                composite_kind not in COMPOSITE_KIND_ALLOWLIST
            ):
                issues.append(
                    f"{where}: compositeKind {composite_kind!r} is not in the "
                    f"documented vocabulary "
                    f"{list(COMPOSITE_KIND_ALLOWLIST)!r}"
                )
            else:
                issues.extend(
                    _string_safety_issues(
                        composite_kind, f"{where}.compositeKind"
                    )
                )
    elif composite_kind is not None:
        issues.append(
            f"{where}: compositeKind must be null unless renderKind is 'composite'"
        )

    template_id = descriptor.template_id
    if render_kind == "composite":
        if not isinstance(template_id, str) or template_id not in TEMPLATE_VOCABULARY:
            issues.append(
                f"{where}: renderKind 'composite' requires a templateId "
                f"from TEMPLATE_VOCABULARY"
            )
        else:
            issues.extend(
                _string_safety_issues(template_id, f"{where}.templateId")
            )
    elif template_id is not None:
        issues.append(
            f"{where}: templateId must be null unless renderKind is 'composite'"
        )
    issues.extend(_typed_variant_issues(descriptor.variants, where))

    dims = descriptor.dimensions
    if not isinstance(dims, Dimensions):
        issues.append(f"{where}: dimensions must be a Dimensions object {{x, y, z}}")
    else:
        for axis in ("x", "y", "z"):
            num = getattr(dims, axis)
            if isinstance(num, bool) or not isinstance(num, (int, float)):
                issues.append(
                    f"{where}.{axis}: must be a number (got {type(num).__name__})"
                )
                continue
            magnitude = float(num)
            if not (DIMENSION_MIN_EXCLUSIVE < magnitude < DIMENSION_MAX_EXCLUSIVE):
                issues.append(
                    f"{where}.{axis}: must be > 0 and < {DIMENSION_MAX_EXCLUSIVE:.0f} "
                    f"(got {magnitude:g})"
                )

    issues.extend(_color_issues(descriptor.colors, where))
    label = descriptor.label
    if not isinstance(label, str) or not label:
        issues.append(f"{where}: label must be a non-empty string")
    else:
        issues.extend(_string_safety_issues(label, f"{where}.label"))
    if not isinstance(descriptor.interactable, bool):
        issues.append(f"{where}: interactable must be a boolean")

    interactions = descriptor.supported_interactions
    issues.extend(
        _string_list_issues(
            interactions, f"{where}.supportedInteractions",
            MAX_SUPPORTED_INTERACTIONS,
        )
    )
    for interaction in interactions:
        if isinstance(interaction, str) and interaction not in INTERACTION_ALLOWLIST:
            issues.append(
                f"{where}: interaction {interaction!r} is not in INTERACTION_ALLOWLIST"
            )
    issues.extend(
        _string_list_issues(
            descriptor.evidence_capabilities,
            f"{where}.evidenceCapabilities",
            MAX_EVIDENCE_CAPABILITIES,
        )
    )
    issues.extend(
        _anchor_list_issues(
            descriptor.allowed_anchors, f"{where}.allowedAnchors"
        )
    )
    return issues


def _validate_catalog_invariants(
    catalog_version: Any, fallback_asset: Any, assets: Any
) -> tuple[str, ...]:
    """The typed twin of ``validate_catalog_data`` used by ``Catalog``.

    Runs on constructed descriptors (not raw JSON dicts), so every public and
    internal construction path — ``load_catalog``, ``dataclasses.replace``,
    direct ``Catalog(...)`` — is held to the SAME cross-set/global invariants.
    """
    issues: list[str] = []
    if (
        isinstance(catalog_version, bool)
        or not isinstance(catalog_version, int)
        or catalog_version < 1
    ):
        issues.append("catalog document: catalogVersion must be a positive integer")
    if not isinstance(assets, (list, tuple)):
        issues.append("catalog document: 'assets' must be a sequence of descriptors")
        return tuple(sorted(set(issues)))

    id_claims: list[tuple[int, str]] = []
    canonical_claims: list[tuple[int, str, str]] = []
    alias_claims: list[tuple[int, str, str]] = []
    declared_meta: dict[str, tuple[Any, Any]] = {}

    for index, descriptor in enumerate(assets):
        if not isinstance(descriptor, AssetDescriptor):
            issues.append(
                f"assets[{index}]: asset entry must be an AssetDescriptor"
            )
            continue
        issues.extend(_typed_descriptor_issues(descriptor, index))
        if not isinstance(descriptor.asset_id, str) or not _ASSET_ID_PATTERN.match(
            descriptor.asset_id
        ):
            continue
        id_claims.append((index, descriptor.asset_id))
        declared_meta[descriptor.asset_id] = (
            descriptor.category,
            descriptor.interactable,
        )
        if isinstance(descriptor.canonical_name, str) and descriptor.canonical_name:
            canonical_claims.append(
                (index, descriptor.asset_id, descriptor.canonical_name)
            )
        for alias in descriptor.aliases:
            if isinstance(alias, str) and alias:
                alias_claims.append((index, descriptor.asset_id, alias))

    issues.extend(_identity_issues(id_claims, canonical_claims, alias_claims))
    issues.extend(_fallback_issues(fallback_asset, declared_meta))
    return tuple(sorted(set(issues)))


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def _variant_spec_from(item: Mapping[str, Any]) -> VariantSpec:
    """Build a typed VariantSpec from an already-validated manifest entry."""
    params: dict[str, VariantParamSpec] = {}
    for key, spec in item["params"].items():
        if spec is None:
            continue
        if key == "scale":
            params[key] = VariantParamSpec(
                kind="scale",
                allowlist=(),
                default=float(spec["default"]),
                min_value=float(spec["min"]),
                max_value=float(spec["max"]),
            )
        else:
            params[key] = VariantParamSpec(
                kind=key,
                allowlist=tuple(str(entry) for entry in spec["allowlist"]),
                default=str(spec["default"]),
            )
    return VariantSpec(name=str(item["name"]), params=MappingProxyType(params))


def _descriptor_from(item: Mapping[str, Any]) -> AssetDescriptor:
    """Build a typed descriptor from an already-validated manifest entry."""
    return AssetDescriptor(
        asset_id=str(item["assetId"]),
        version=int(item["version"]),
        canonical_name=str(item["canonicalName"]),
        aliases=tuple(str(alias) for alias in item["aliases"]),
        category=str(item["category"]),
        subtype=str(item["subtype"]),
        tags=tuple(str(tag) for tag in item["tags"]),
        render_kind=str(item["renderKind"]),
        composite_kind=(
            str(item["compositeKind"]) if item["compositeKind"] is not None else None
        ),
        dimensions=Dimensions(
            x=float(item["dimensions"]["x"]),
            y=float(item["dimensions"]["y"]),
            z=float(item["dimensions"]["z"]),
        ),
        colors=MappingProxyType(
            {str(k): str(v) for k, v in item["colors"].items()}
        ),
        label=str(item["label"]),
        interactable=bool(item["interactable"]),
        supported_interactions=tuple(
            str(x) for x in item["supportedInteractions"]
        ),
        evidence_capabilities=tuple(
            str(x) for x in item["evidenceCapabilities"]
        ),
        allowed_anchors=tuple(str(x) for x in item["allowedAnchors"]),
        template_id=(
            str(item["templateId"]) if item.get("templateId") is not None else None
        ),
        variants=tuple(
            _variant_spec_from(variant) for variant in (item.get("variants") or ())
        ),
    )


def load_catalog(path: str | Path) -> Catalog:
    """Read + fully validate the manifest at ``path``; return a typed Catalog.

    Raises ``CatalogError`` when the file is unreadable or not valid JSON, and
    ``CatalogValidationError`` (with the sorted ``.issues`` tuple) when the
    manifest fails validation. Deterministic: the same file always yields an
    equal ``Catalog``. The returned ``Catalog`` was validated TWICE by design:
    the raw manifest validator covers schema/JSON-level rules and the typed
    constructor invariants re-run the cross-set/global rules (DEF-063).
    """
    manifest_path = Path(path)
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(
            f"cannot read asset catalog {manifest_path}: {exc}"
        ) from exc
    try:
        data = json.loads(raw_text)
    except ValueError as exc:
        raise CatalogError(
            f"asset catalog {manifest_path} is not valid JSON: {exc}"
        ) from exc
    issues = validate_catalog_data(data)
    if issues:
        raise CatalogValidationError(issues)
    return Catalog(
        catalog_version=int(data["catalogVersion"]),
        fallback_asset=str(data["fallbackAsset"]),
        assets=tuple(_descriptor_from(item) for item in data["assets"]),
    )


@lru_cache(maxsize=16)
def _load_catalog_cached(path_text: str) -> Catalog:
    return load_catalog(Path(path_text))


def load_catalog_from_repo(path: str | Path | None = None) -> Catalog:
    """Load the catalog, defaulting to the repo-root manifest location.

    The default path is derived from the package location exactly like
    ``app.core.config.REPO_ROOT`` (``<repo>/assets/catalog/catalog.json``),
    independent of the process CWD. Results are cached per path (the manifest
    is immutable in practice); pass an explicit ``path`` to load any other
    (e.g. test) manifest without touching the cache.
    """
    if path is not None:
        return _load_catalog_cached(str(Path(path)))
    from app.core.config import REPO_ROOT

    return _load_catalog_cached(str(REPO_ROOT / "assets" / "catalog" / "catalog.json"))


__all__ = [
    "ASSET_VERSION_MIN",
    "CATEGORY_ALLOWLIST",
    "COMPOSITE_KIND_ALLOWLIST",
    "Catalog",
    "CatalogError",
    "CatalogValidationError",
    "Dimensions",
    "DOCUMENTED_ASSET_KEYS",
    "AssetDescriptor",
    "MATERIAL_VOCABULARY",
    "MAX_ALLOWED_ANCHORS",
    "MAX_ALIASES",
    "MAX_COLORS",
    "MAX_CATALOG_STRING_LENGTH",
    "MAX_EVIDENCE_CAPABILITIES",
    "MAX_SUPPORTED_INTERACTIONS",
    "MAX_TAGS",
    "MAX_VARIANTS_PER_ASSET",
    "MAX_VARIANT_ALLOWLIST",
    "OPTIONAL_ASSET_KEYS",
    "REQUIRED_ASSET_KEYS",
    "RENDER_KIND_ALLOWLIST",
    "SCALE_MAX_BOUND",
    "SCALE_MIN_BOUND",
    "STATE_VOCABULARY",
    "TEMPLATE_VOCABULARY",
    "VARIANT_PARAM_KINDS",
    "VariantParamSpec",
    "VariantSpec",
    "load_catalog",
    "load_catalog_from_repo",
    "normalize_asset_text",
    "validate_catalog_data",
]