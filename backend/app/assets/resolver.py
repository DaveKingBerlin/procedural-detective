"""Deterministic Asset Oracle resolver (Phase 10).

Resolution order (strict, deterministic for equal inputs + catalog version,
independent of dict ordering):

1. **EXACT** — ``requestedName`` equals an ``assetId`` (case-sensitive logical
   id)            -> provenance ``CATALOG_EXACT``
2. **CANONICAL** — ``normalize(requestedName) == normalize(canonicalName)``
                   -> provenance ``CATALOG_EXACT`` (roadmap: canonical names and
                   exact ids both report CATALOG_EXACT; only aliases report
                   CATALOG_ALIAS)
3. **ALIAS**     — ``normalize(requestedName)`` matches a normalized alias
                   -> provenance ``CATALOG_ALIAS`` (``matchedAlias`` is the
                   verbatim alias that matched)
4. **SEMANTIC**  — explicit scoring over ``categoryHint`` / ``subtypeHint`` /
                   ``tags``:

                   * +3.0 for every normalized tag of the request present in
                     the candidate's normalized tags,
                   * +2.0 when the normalized categoryHint equals the
                     candidate's normalized category,
                   * +1.0 when the normalized subtypeHint equals the
                     candidate's normalized subtype,
                   * confidence = total score; the documented minimum
                     confidence threshold is ``SEMANTIC_MIN_CONFIDENCE = 3.0``.
                   Candidates below threshold are discarded. A provided
                   ``requiredInteraction`` or non-empty
                   ``requiredEvidenceCapabilities`` further FILTER the
                   candidates (an asset that cannot support the requested
                   interaction/capability is not a valid candidate).

                   Exactly one candidate at the maximum score (>= threshold)
                   -> SEMANTIC_MATCH with that confidence. A TIE at the
                   maximum score -> an AMBIGUOUS result with ``ambiguous=True``
                   and the deterministic tied candidate list (score desc,
                   assetId asc) — NEVER an arbitrary winner.
5. **FALLBACK**  — every other request resolves to the catalog's
                   ``fallbackAsset`` with provenance ``FALLBACK`` (explicit,
                   never silent nonsense).

``normalize`` shares the constraints engine's normalization concept
(``normalize_motive_text``: casefold + keep ASCII letters/digits/currency
symbols, drop everything else) and adds an NFKD decomposition pre-step
(``app.assets.catalog.normalize_asset_text``) so accented lookalikes of ASCII
letters match their base letter. "KITCHEN KNIFE", "Kïtchen knife" and
"kitchen_knife" are the same token. The typed entry point is
``AssetResolver.resolve_request``; ``resolve`` additionally gates RAW payloads
through ``app.assets.validation`` before parsing them. Phase 12 adds the
bounded parametric-variant entry points (``apply_variant`` /
``resolve_with_variant``) which validate declarative variant params and emit
the ``PARAMETRIC_VARIANT`` provenance.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from app.assets.catalog import (
    Catalog,
    CatalogError,
    load_catalog_from_repo,
    normalize_asset_text,
)
from app.assets.validation import (
    AssetRequestValidationError,
    validate_asset_request,
)

# Documented semantic scoring weights / threshold (see module docstring).
SEMANTIC_TAG_WEIGHT = 3.0
SEMANTIC_CATEGORY_WEIGHT = 2.0
SEMANTIC_SUBTYPE_WEIGHT = 1.0
SEMANTIC_MIN_CONFIDENCE = 3.0

# Reserved provenance values (PARAMETRIC_VARIANT, PROCEDURAL_GENERATED,
# STATIC_GENERATED) exist so callers can pattern-match stable enum values
# across the roadmap. Phase 10 never emits them; Phase 12 emits
# PARAMETRIC_VARIANT through ``resolve_with_variant``.


class Provenance(Enum):
    """How an asset resolution was reached (Phase 10 + reserved values)."""

    CATALOG_EXACT = "CATALOG_EXACT"
    CATALOG_ALIAS = "CATALOG_ALIAS"
    SEMANTIC_MATCH = "SEMANTIC_MATCH"
    PARAMETRIC_VARIANT = "PARAMETRIC_VARIANT"
    PROCEDURAL_GENERATED = "PROCEDURAL_GENERATED"
    STATIC_GENERATED = "STATIC_GENERATED"
    FALLBACK = "FALLBACK"


class AssetVariantError(ValueError):
    """A variant request/parameter failed the Phase 12 bounded-variant rules.

    Raised by ``apply_variant`` / ``resolve_with_variant`` for unknown variant
    parameters, values outside the declared allowlists, non-numeric or
    non-finite (NaN/±Inf) scale values, or variant attempts against assets/
    resolutions that declare no variants (fallback / ambiguous / non-composite
    assets).
    """


# Phase 12 — the frozen primary-tone preference used to merge a variant color
# override into an asset's colors map (mirrors the frontend REGISTRY's
# PRIMARY_COLOR_KEYS so the merged view is deterministic and contract-stable).
_PRIMARY_COLOR_KEYS: tuple[str, ...] = (
    "shade",
    "base",
    "blade",
    "blades",
    "top",
    "body",
)


@dataclass(frozen=True)
class AssetVariantView:
    """The frozen, bounded view of one parametric variant (Phase 12 §Variant).

    Produced by ``apply_variant`` (and held by ``AssetVariantResolution``):
    ``colors`` is the asset's base colors MERGED with the applied color param
    (the primary tone key — the frontend's preferred key, else the first key —
    carries the applied hex, so the multi-tone object keeps its parts), and
    ``scale`` is the applied scale CLAMPED into the asset's declared [min,max]
    (it can never escape the declared bounds). ``material``/``state`` are the
    applied (or defaulted) frozen safe tokens; None when the asset declares no
    such variant parameter.
    """

    asset_id: str
    template_id: str | None
    colors: Mapping[str, str]
    scale: float
    material: str | None
    state: str | None


@dataclass(frozen=True)
class AssetVariantResolution:
    """A resolution that additionally applied a validated parametric variant.

    ``provenance`` is always ``PARAMETRIC_VARIANT`` (reserved Phase 12+
    provenance; emitted by ``resolve_with_variant``). The underlying asset was
    first resolved through the normal catalog order (exact/canonical/alias/
    semantic); the view carries the clamped, allowlist-validated variant.
    """

    asset_id: str
    catalog_version: int
    provenance: Provenance
    version: int
    view: AssetVariantView
    resolved: bool


@dataclass(frozen=True)
class AssetRequest:
    """A typed, trusted semantic asset request (Phase 10 AssetRequest)."""

    requested_name: str
    category_hint: str | None = None
    subtype_hint: str | None = None
    tags: tuple[str, ...] = ()
    required_interaction: str | None = None
    required_evidence_capabilities: tuple[str, ...] = ()
    style_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssetResolution:
    """The deterministic outcome of one asset request.

    ``resolved`` is True whenever a concrete asset was chosen — including the
    explicit fallback (``provenance == FALLBACK``). The only False case is an
    ambiguous semantic match: NO arbitrary winner is chosen, the qualified
    candidates are returned instead.

    ``confidence`` is the semantic score (float) for SEMANTIC_MATCH, the top
    (tied) score for an ambiguous result, and None otherwise.
    """

    asset_id: str
    catalog_version: int
    provenance: Provenance
    version: int
    confidence: float | None
    ambiguous: bool
    candidates: tuple[str, ...]
    matched_alias: str | None
    resolved: bool


def normalize(text: str) -> str:
    """Deterministic canonical matching form for names/aliases/vocabularies.

    The single Asset Oracle identity normalization (see
    ``app.assets.catalog.normalize_asset_text``): NFKD decomposition, then the
    constraints engine's concept (casefold + keep ASCII letters/digits/currency
    symbols). "KITCHEN KNIFE" / "Kïtchen knife" / "kitchen_knife" all normalize
    to ``kitchenknife``. Non-strings normalize to the empty string.
    """
    return normalize_asset_text(text)


class AssetResolver:
    """Stateless-per-catalog deterministic resolver."""

    def __init__(self, catalog: Catalog) -> None:
        if not isinstance(catalog, Catalog):
            raise TypeError("AssetResolver requires a Catalog")
        self._catalog = catalog
        self._by_id: dict[str, Any] = {a.asset_id: a for a in catalog.assets}
        # Normalized canonical names -> descriptor. Name collisions are
        # rejected at catalog load, so each normalized key maps to one asset.
        self._canonicals: dict[str, Any] = {
            normalize(a.canonical_name): a for a in catalog.assets
        }
        self._aliases: dict[str, tuple[Any, str]] = {}
        for asset in catalog.assets:
            for alias in asset.aliases:
                self._aliases.setdefault(normalize(alias), (asset, alias))
        if catalog.fallback_asset not in self._by_id:
            raise CatalogError(
                f"catalog fallbackAsset {catalog.fallback_asset!r} is not a "
                "declared assetId"
            )

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    def resolve_request(self, request: AssetRequest) -> AssetResolution:
        """Resolve one TYPED request (caller already validated raw input)."""
        if not isinstance(request, AssetRequest):
            raise TypeError("resolve_request requires an AssetRequest")
        name = request.requested_name

        # 1. EXACT logical id (case-sensitive).
        descriptor = self._by_id.get(name)
        if descriptor is not None:
            return self._exact(descriptor)

        norm = normalize(name)

        # 2. canonical name (normalized equality) -> CATALOG_EXACT.
        descriptor = self._canonicals.get(norm)
        if descriptor is not None:
            return self._exact(descriptor)

        # 3. alias -> CATALOG_ALIAS with the verbatim matched alias.
        alias_hit = self._aliases.get(norm)
        if alias_hit is not None:
            descriptor, raw_alias = alias_hit
            return AssetResolution(
                asset_id=descriptor.asset_id,
                catalog_version=self._catalog.catalog_version,
                provenance=Provenance.CATALOG_ALIAS,
                version=descriptor.version,
                confidence=None,
                ambiguous=False,
                candidates=(),
                matched_alias=raw_alias,
                resolved=True,
            )

        # 4. semantic scoring (explicit rule, min confidence threshold).
        #    Winner = the UNIQUE candidate with the MAXIMUM score at/above the
        #    threshold; candidates tied at the maximum yield AMBIGUOUS (never
        #    an arbitrary winner). Lower-scoring qualifiers do not create
        #    ambiguity against a strictly better asset.
        scored = self._semantic_candidates(request)
        if scored:
            top_score = scored[0][1]
            winners = [
                (descriptor, score)
                for descriptor, score in scored
                if score == top_score
            ]
            if len(winners) == 1:
                descriptor, score = winners[0]
                return AssetResolution(
                    asset_id=descriptor.asset_id,
                    catalog_version=self._catalog.catalog_version,
                    provenance=Provenance.SEMANTIC_MATCH,
                    version=descriptor.version,
                    confidence=float(score),
                    ambiguous=False,
                    candidates=(),
                    matched_alias=None,
                    resolved=True,
                )
            return AssetResolution(
                asset_id="",
                catalog_version=self._catalog.catalog_version,
                provenance=Provenance.SEMANTIC_MATCH,
                version=0,
                confidence=float(top_score),
                ambiguous=True,
                candidates=tuple(
                    descriptor.asset_id for descriptor, _score in winners
                ),
                matched_alias=None,
                resolved=False,
            )

        # 5. explicit fallback (never silent nonsense).
        fallback = self._by_id[self._catalog.fallback_asset]
        return AssetResolution(
            asset_id=fallback.asset_id,
            catalog_version=self._catalog.catalog_version,
            provenance=Provenance.FALLBACK,
            version=fallback.version,
            confidence=None,
            ambiguous=False,
            candidates=(),
            matched_alias=None,
            resolved=True,
        )

    def resolve_placements_provenance(
        self, placements: Iterable[Any]
    ) -> dict[str, str]:
        """Diagnostic: objectId -> provenance for a placements iterable.

        Accepts dataclass placements (``object_id`` / ``asset_id`` attributes,
        e.g. ``app.generation.schemas.PlacementSpec``) or serialized mappings
        (``object_id``/``assetId`` keys). Items without both ids are skipped.
        Deterministic: iterated in input order, each resolved via the catalog.
        """
        out: dict[str, str] = {}
        for placement in placements:
            asset_id = _placement_value(placement, "asset_id", "assetId")
            object_id = _placement_value(placement, "object_id", "objectId")
            if asset_id is None or object_id is None:
                continue
            resolution = self.resolve_request(
                AssetRequest(requested_name=str(asset_id))
            )
            out[str(object_id)] = resolution.provenance.value
        return out

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _exact(self, descriptor: Any) -> AssetResolution:
        return AssetResolution(
            asset_id=descriptor.asset_id,
            catalog_version=self._catalog.catalog_version,
            provenance=Provenance.CATALOG_EXACT,
            version=descriptor.version,
            confidence=None,
            ambiguous=False,
            candidates=(),
            matched_alias=None,
            resolved=True,
        )

    def _semantic_candidates(
        self, request: AssetRequest
    ) -> list[tuple[Any, float]]:
        """All catalog assets at/above the semantic threshold, deterministic.

        Filtering: a provided requiredInteraction must be supported by the
        candidate; every requiredEvidenceCapability must be present. Scoring:
        +3.0 per normalized tag hit, +2.0 categoryHint hit, +1.0 subtypeHint
        hit; only candidates scoring >= SEMANTIC_MIN_CONFIDENCE qualify.
        Sorted by (score desc, assetId asc) for a stable, dict-order-free
        ambiguity list.
        """
        norm_category = (
            normalize(request.category_hint) if request.category_hint else ""
        )
        norm_subtype = (
            normalize(request.subtype_hint) if request.subtype_hint else ""
        )
        norm_tags = {normalize(tag) for tag in request.tags}
        norm_required_interaction = (
            normalize(request.required_interaction)
            if request.required_interaction
            else ""
        )
        norm_required_capabilities = {
            normalize(cap) for cap in request.required_evidence_capabilities
        }

        scored: list[tuple[Any, float]] = []
        for descriptor in self._catalog.assets:
            if norm_required_interaction and norm_required_interaction not in {
                normalize(x) for x in descriptor.supported_interactions
            }:
                continue
            if norm_required_capabilities:
                owned = {normalize(x) for x in descriptor.evidence_capabilities}
                if not norm_required_capabilities.issubset(owned):
                    continue

            score = 0.0
            owned_tags = {normalize(tag) for tag in descriptor.tags}
            score += SEMANTIC_TAG_WEIGHT * float(len(norm_tags & owned_tags))
            if norm_category and norm_category == normalize(descriptor.category):
                score += SEMANTIC_CATEGORY_WEIGHT
            if norm_subtype and norm_subtype == normalize(descriptor.subtype):
                score += SEMANTIC_SUBTYPE_WEIGHT
            if score >= SEMANTIC_MIN_CONFIDENCE:
                scored.append((descriptor, score))

        scored.sort(key=lambda pair: (-pair[1], pair[0].asset_id))
        return scored


# --------------------------------------------------------------------------- #
# module-level convenience API
# --------------------------------------------------------------------------- #


def _placement_value(placement: Any, attr: str, key: str) -> Any:
    """Read ``object_id``/``asset_id`` from dataclasses or raw mappings."""
    if isinstance(placement, Mapping):
        return placement.get(key, placement.get(attr))
    return getattr(placement, attr, None)


# --------------------------------------------------------------------------- #
# Phase 12 §Variant — bounded parametric variants
# --------------------------------------------------------------------------- #


def _variant_surface(asset: Any) -> dict[str, Any]:
    """The deterministic per-asset variant-parameter surface.

    Merges the (load-validated, allowlist-consistent) variant specs of an
    asset into ONE key -> VariantParamSpec map. For each param key the first
    declared (manifest-order) spec wins; catalog validation guarantees every
    other variant's spec for that key shares the same allowlist/scale bounds.
    """
    surface: dict[str, Any] = {}
    for variant in asset.variants:
        for key, spec in variant.params.items():
            surface.setdefault(key, spec)
    return surface


def _primary_tone_key(colors: Mapping[str, str]) -> str | None:
    """Deterministic primary color key (frontend-REGISTRY preference order)."""
    for key in _PRIMARY_COLOR_KEYS:
        if key in colors:
            return key
    return next(iter(colors), None)


def apply_variant(
    asset: Any,
    variant_params: Mapping[str, Any],
    *,
    catalog: Catalog | None = None,
) -> AssetVariantView:
    """Validate + apply a bounded parametric variant to ONE asset.

    ``asset`` may be an ``AssetDescriptor`` or an assetId string (then looked
    up in ``catalog``, defaulting to the repo manifest). ``variant_params`` is
    the mapping of variant parameter values to apply, e.g. ``{"material":
    "wood.dark", "state": "clean", "scale": 1.1}``.

    Rules (bounded, never raising on ``None``/spec errors — those are raised
    as ``AssetVariantError``):
    - an asset with NO declared variants (non-composite assets and the neutral
      fallback) cannot be varianted -> ``AssetVariantError``;
    - unknown param key (not in the asset's variant surface) -> error;
    - color/material/state values must be members of the key's declared
      allowlist (a failed value is rejected, never coerced);
    - a scale value must be a FINITE number (NaN/±Inf raise ``AssetVariantError``,
      DEF-065) and is CLAMPED into the asset's declared [min, max], so an
      extreme scale can never escape the declared bounds;
    - the returned view carries the base ``colors`` MERGED with an applied
      color override on the deterministic primary-tone key.
    """
    if isinstance(asset, str):
        if catalog is None:
            from app.assets.catalog import load_catalog_from_repo

            catalog = load_catalog_from_repo()
        asset = catalog.by_id.get(asset)
        if asset is None:
            raise AssetVariantError(f"unknown asset {asset!r}")
    if not getattr(asset, "variants", None):
        raise AssetVariantError(
            f"asset {asset.asset_id!r} declares no variants"
        )
    if not isinstance(variant_params, Mapping):
        raise AssetVariantError("variant params must be a mapping")

    surface = _variant_surface(asset)
    for key in variant_params:
        if key not in surface:
            raise AssetVariantError(
                f"unknown variant parameter {key!r} for asset "
                f"{asset.asset_id!r}"
            )

    merged_colors = dict(asset.colors)
    scale_value = 1.0
    material: str | None = None
    state: str | None = None

    for key, spec in surface.items():
        value = variant_params.get(key, spec.default)
        if key == "color":
            if value not in spec.allowlist:
                raise AssetVariantError(
                    f"variant color {value!r} is not in the declared allowlist "
                    f"for asset {asset.asset_id!r}"
                )
            primary = _primary_tone_key(merged_colors)
            if primary is not None:
                merged_colors[primary] = value
        elif key == "material":
            if value not in spec.allowlist:
                raise AssetVariantError(
                    f"variant material {value!r} is not in the declared "
                    f"allowlist for asset {asset.asset_id!r}"
                )
            material = value
        elif key == "state":
            if value not in spec.allowlist:
                raise AssetVariantError(
                    f"variant state {value!r} is not in the declared allowlist "
                    f"for asset {asset.asset_id!r}"
                )
            state = value
        elif key == "scale":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AssetVariantError(
                    f"variant scale must be a number (got {value!r}) for asset "
                    f"{asset.asset_id!r}"
                )
            applied = float(value)
            # DEF-065: NaN/±Inf must NEVER reach the clamped view — Python's
            # min/max propagate NaN (``min(nan, max)`` -> nan), silently
            # violating "scale can never escape the declared bounds". Reject
            # any non-finite value BEFORE clamping; finite extremes clamp.
            if not math.isfinite(applied):
                raise AssetVariantError(
                    f"variant scale must be a finite number (got {value!r}) "
                    f"for asset {asset.asset_id!r}"
                )
            if (
                spec.min_value is not None
                and spec.max_value is not None
            ):
                scale_value = min(
                    max(applied, spec.min_value), spec.max_value
                )
            else:
                scale_value = applied

    from types import MappingProxyType

    return AssetVariantView(
        asset_id=asset.asset_id,
        template_id=asset.template_id,
        colors=MappingProxyType(merged_colors),
        scale=scale_value,
        material=material,
        state=state,
    )


def resolve_with_variant(
    request: AssetRequest | Mapping[str, Any],
    variant_params: Mapping[str, Any],
    *,
    catalog: Catalog | None = None,
) -> AssetVariantResolution:
    """Resolve an asset through the normal catalog order, then apply a variant.

    Accepts the same inputs as ``resolve`` (typed ``AssetRequest`` or a raw
    mapping; raw payloads pass through ``app.assets.validation`` first). The
    resolved asset's variant is validated + applied via ``apply_variant`` and
    the result is reported with provenance ``PARAMETRIC_VARIANT`` (the
    reserved Phase 12+ provenance — never emitted for non-variant resolves).

    Raises ``AssetVariantError`` when the request is unresolved (ambiguous) or
    falls back, when the resolved asset declares no variants, or when the
    variant params are invalid.
    """
    if catalog is None:
        catalog = load_catalog_from_repo()
    resolution = resolve(request, catalog=catalog)
    if not resolution.resolved:
        raise AssetVariantError(
            "cannot apply a variant to an unresolved (ambiguous) request"
        )
    if resolution.provenance is Provenance.FALLBACK:
        raise AssetVariantError(
            "cannot apply a variant to a FALLBACK resolution (the neutral "
            "fallback declares no variants)"
        )
    descriptor = catalog.by_id[resolution.asset_id]
    view = apply_variant(descriptor, variant_params)
    return AssetVariantResolution(
        asset_id=resolution.asset_id,
        catalog_version=resolution.catalog_version,
        provenance=Provenance.PARAMETRIC_VARIANT,
        version=resolution.version,
        view=view,
        resolved=True,
    )


def _asset_request_from_raw(payload: Any) -> AssetRequest:
    """Validate then parse a RAW (possibly untrusted) payload."""
    issues = validate_asset_request(payload)
    if issues:
        raise AssetRequestValidationError(issues)
    if not isinstance(payload, Mapping):
        raise AssetRequestValidationError(
            ("asset request must be a JSON object",)
        )

    def _pick(camel: str, snake: str, default: Any = None) -> Any:
        if camel in payload:
            return payload[camel]
        if snake in payload:
            return payload[snake]
        return default

    tags = _pick("tags", "tags", ()) or ()
    capabilities = _pick(
        "requiredEvidenceCapabilities", "required_evidence_capabilities", ()
    ) or ()
    style_hints = _pick("styleHints", "style_hints", ()) or ()
    return AssetRequest(
        requested_name=str(_pick("requestedName", "requested_name", "")),
        category_hint=_pick("categoryHint", "category_hint"),
        subtype_hint=_pick("subtypeHint", "subtype_hint"),
        tags=tuple(str(tag) for tag in tags),
        required_interaction=_pick(
            "requiredInteraction", "required_interaction"
        ),
        required_evidence_capabilities=tuple(
            str(cap) for cap in capabilities
        ),
        style_hints=tuple(str(hint) for hint in style_hints),
    )


def resolve(
    request: AssetRequest | Mapping[str, Any], *, catalog: Catalog | None = None
) -> AssetResolution:
    """Resolve a request against ``catalog`` (default: the repo manifest).

    Accepts a TYPED ``AssetRequest`` (trusted internal callers) or a RAW
    mapping. RAW payloads are first passed through security validation
    (``app.assets.validation``): an unsafe payload raises
    ``AssetRequestValidationError`` with the sorted issues BEFORE any
    resolution can load content from it.
    """
    if catalog is None:
        catalog = load_catalog_from_repo()
    resolver = AssetResolver(catalog)
    if isinstance(request, AssetRequest):
        return resolver.resolve_request(request)
    return resolver.resolve_request(_asset_request_from_raw(request))


def resolve_placements_provenance(
    placements: Iterable[Any], *, catalog: Catalog | None = None
) -> dict[str, str]:
    """INTERNAL diagnostic: ``{objectId: provenance}`` for a placements list.

    Runs every placement's assetId through the resolver (catalog default: the
    repo manifest). The result is diagnostic only — it is never part of any
    published payload or API DTO, and it never alters the placement ids.
    """
    if catalog is None:
        catalog = load_catalog_from_repo()
    return AssetResolver(catalog).resolve_placements_provenance(placements)


__all__ = [
    "SEMANTIC_CATEGORY_WEIGHT",
    "SEMANTIC_MIN_CONFIDENCE",
    "SEMANTIC_SUBTYPE_WEIGHT",
    "SEMANTIC_TAG_WEIGHT",
    "AssetRequest",
    "AssetResolution",
    "AssetResolver",
    "AssetVariantError",
    "AssetVariantResolution",
    "AssetVariantView",
    "Provenance",
    "apply_variant",
    "normalize",
    "resolve",
    "resolve_placements_provenance",
    "resolve_with_variant",
]