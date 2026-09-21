"""Phase 13 — procedural Asset Oracle facade.

The Oracle closes the resolution loop for assets the CATALOG cannot cover:
after the normal resolver reports an explicit FALLBACK (or the caller requests
generation with ``force_generate=True``), a configured declarative
``AssetSpecProvider`` is consulted ONCE (bounded request). Its raw spec JSON is
strictly parsed (``parse_asset_spec``) and compiled by the trusted compiler;
the result is cached (bounded, versioned) so the SAME requestedName never calls
the provider twice.

Reference resolution path (Phase 13 / Phase_POST_MVP_ROADMAP):

    AssetRequest -> Asset Oracle
        -> no adequate catalog match (FALLBACK)
        -> declarative AssetSpec provider (bounded)
        -> strict parser + safety validator
        -> trusted ProceduralAssetCompiler
        -> GeneratedAssetDefinition (frozen, player-safe)
        -> proc.<category>.<hash> assetId (PROCEDURAL_GENERATED)

Contract:

- ``resolve_or_generate(request, spec_provider=None)`` first runs the NORMAL
  catalog resolver. Only a FALLBACK result (or ``force_generate=True``) with a
  configured provider triggers the generation path. A provider that returns
  nothing/invalid content ALWAYS resolves back to the ORIGINAL FALLBACK
  (explicit, never a crash). When NO provider is configured the path is simply
  NOT available (documented — generation is opt-in, never the default).
- The provider request is bounded (``requestedName <= 120``, bounded tags).
- ``generate_and_stage(asset_request, spec_provider)`` is the explicit staging
  helper (service + Phase 14): compile + cache ONE unknown asset and return its
  ``proc.*`` assetId (raises ``AssetGenerationError`` on invalid input —
  staging never silently degrades).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Mapping

from app.assets.catalog import Catalog
from app.assets.compiler import (
    COMPILER_VERSION,
    SCHEMA_VERSION,
    GeneratedAssetDefinition,
    compile_asset_spec,
    spec_hash_for_cache,
)
from app.assets.generated_cache import GeneratedAssetCache
from app.assets.resolver import (
    AssetRequest,
    AssetResolution,
    Provenance,
    resolve,
)
from app.assets.spec_provider import (
    AssetSpecProvider,
    AssetSpecRequest,
    AssetSpecResponse,
)
from app.assets.specs import (
    AssetSpecError,
    normalize_request_key,
    normalize_spec,
    parse_asset_spec,
)
from app.assets.validation import (
    AssetRequestValidationError,
    validate_asset_request,
)


class AssetGenerationError(ValueError):
    """The explicit staging path could not produce a generated asset.

    Raised by ``generate_and_stage`` for invalid/absent provider content or
    parse/compile failures — the caller may not fall back silently on this path.
    """


@dataclass(frozen=True)
class GeneratedAssetResolution:
    """A successful procedural-generation outcome (provenance locked)."""

    asset_id: str
    provenance: Provenance = Provenance.PROCEDURAL_GENERATED
    definition: GeneratedAssetDefinition | None = None
    compiler_version: int = COMPILER_VERSION
    schema_version: int = SCHEMA_VERSION
    source_canonical_name: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.definition, GeneratedAssetDefinition):
            raise TypeError("GeneratedAssetResolution.definition must be a definition")
        if self.provenance is not Provenance.PROCEDURAL_GENERATED:
            raise ValueError("generated resolutions always carry PROCEDURAL_GENERATED")


@dataclass(frozen=True)
class AssetResolutionOrError:
    """The unified oracle outcome.

    ``resolution`` is ALWAYS the normal catalog resolver's result — for a
    generated path it is the ORIGINAL FALLBACK (so callers can degrade
    explicitly); ``generated`` carries the generated resolution when the
    generation path succeeded; ``error`` is a sanitized message when the
    generation path was attempted but failed (never an exception).
    """

    resolution: AssetResolution | None = None
    generated: GeneratedAssetResolution | None = None
    error: str | None = None


def _asset_request_from_raw(request: AssetRequest | Mapping[str, Any]) -> AssetRequest:
    """Normalize a typed or raw request.

    RAW payloads are routed through the resolver's security-gate validation
    (``app.assets.validation``); a payload that fails the gate raises
    ``AssetRequestValidationError`` (the same behavior as ``resolve``).
    """
    if isinstance(request, AssetRequest):
        return request
    if not isinstance(request, Mapping):
        raise AssetGenerationError("asset request must be a JSON object")
    issues = validate_asset_request(request)
    if issues:
        raise AssetRequestValidationError(issues)

    def _pick(camel: str, snake: str, default: Any = None) -> Any:
        if camel in request:
            return request[camel]
        if snake in request:
            return request[snake]
        return default

    tags = _pick("tags", "tags", ()) or ()
    return AssetRequest(
        requested_name=str(_pick("requestedName", "requested_name", "")),
        category_hint=_pick("categoryHint", "category_hint"),
        subtype_hint=_pick("subtypeHint", "subtype_hint"),
        tags=tuple(str(tag) for tag in tags),
        required_interaction=_pick("requiredInteraction", "required_interaction"),
        required_evidence_capabilities=tuple(
            str(cap)
            for cap in (
                _pick("requiredEvidenceCapabilities", "required_evidence_capabilities", ()) or ()
            )
        ),
        style_hints=tuple(
            str(hint) for hint in (_pick("styleHints", "style_hints", ()) or ())
        ),
    )


def _build_spec_request(request: AssetRequest) -> AssetSpecRequest | str:
    """Bounded provider request; returns the request or an error message."""
    name = request.requested_name
    if not isinstance(name, str) or not name:
        return "generation requested a non-string requested name"
    if len(name) > 120:
        return "generation requested name exceeds the 120 character bound"
    return AssetSpecRequest(
        requested_name=name,
        category_hint=request.category_hint,
        tags=tuple(request.tags),
    )


class GeneratedAssetOracle:
    """Stateful oracle: bounded generated-definition cache + request index.

    ``request_key -> spec-hash`` index is what makes the second resolve of the
    same requestedName a pure cache hit (zero provider calls); all state is
    thread-safe and bounded by ``GeneratedAssetCache``.
    """

    def __init__(
        self,
        *,
        catalog: Catalog | None = None,
        cache: GeneratedAssetCache | None = None,
    ) -> None:
        self._catalog = catalog
        self._cache = cache if cache is not None else GeneratedAssetCache()
        self._request_hash: dict[str, str] = {}
        self._last_response: AssetSpecResponse | None = None
        self._lock = threading.Lock()

    @property
    def cache(self) -> GeneratedAssetCache:
        return self._cache

    # ------------------------------------------------------------------ #
    # resolution
    # ------------------------------------------------------------------ #

    def resolve_or_generate(
        self,
        request: AssetRequest | Mapping[str, Any],
        *,
        spec_provider: AssetSpecProvider | None = None,
        force_generate: bool = False,
        catalog: Catalog | None = None,
    ) -> AssetResolutionOrError:
        """Normal catalog resolution, then OPT-IN declarative generation.

        Generation runs ONLY when (a) the normal resolver produced FALLBACK or
        ``force_generate`` is True, AND (b) a ``spec_provider`` is configured.
        Any provider-path failure (absent/invalid content, parse/compile issues,
        oversized request) yields the ORIGINAL resolution with ``error`` set —
        never a crash, never a silently fabricated asset.
        """
        catalog = catalog if catalog is not None else self._catalog
        resolution = resolve(request, catalog=catalog)
        needs_generation = force_generate or (
            resolution is not None and resolution.provenance is Provenance.FALLBACK
        )
        if spec_provider is None or not needs_generation:
            return AssetResolutionOrError(resolution=resolution)

        typed = _asset_request_from_raw(request)
        key = normalize_request_key(typed.requested_name)

        # 1. cache first (hit -> ZERO provider calls).
        cached_hash = self._hash_for(key)
        if cached_hash is not None:
            cached = self._cache.get(cached_hash)
            if cached is not None:
                definition, _metadata = cached
                return AssetResolutionOrError(
                    resolution=resolution,
                    generated=self._to_generated(definition, definition.canonical_name),
                )

        # 2. provider call (bounded).
        spec_request_or_error = _build_spec_request(typed)
        if isinstance(spec_request_or_error, str):
            return AssetResolutionOrError(resolution=resolution, error=spec_request_or_error)
        error = self._invoke_provider(spec_provider, spec_request_or_error)
        if error is not None:
            return AssetResolutionOrError(resolution=resolution, error=error)
        content = self._last_response.content if self._last_response is not None else None

        # 3. strict parse + compile + cache.
        try:
            spec = parse_asset_spec(content, non_throwing=False)
            definition = compile_asset_spec(spec)
        except AssetSpecError as exc:
            return AssetResolutionOrError(
                resolution=resolution,
                error="spec provider returned an invalid asset spec: "
                + "; ".join(exc.issues[:4]),
            )
        except RecursionError:  # DEF-067 belt-and-braces: never a raw traceback
            return AssetResolutionOrError(
                resolution=resolution,
                error="spec provider returned an excessively nested asset spec",
            )
        self._stage(definition, spec, key)
        return AssetResolutionOrError(
            resolution=resolution,
            generated=self._to_generated(definition, definition.canonical_name),
        )

    # ------------------------------------------------------------------ #
    # explicit staging (used by the service helper + Phase 14)
    # ------------------------------------------------------------------ #

    def generate_and_stage(
        self,
        asset_request: AssetRequest | Mapping[str, Any],
        spec_provider: AssetSpecProvider,
        *,
        catalog: Catalog | None = None,
    ) -> str:
        """Compile + cache one unknown asset; returns its ``proc.*`` assetId.

        Raises ``AssetGenerationError`` when the provider returns nothing/
        invalid content or the spec fails parsing/compilation — the staging
        path NEVER degrades silently. Cache hits (same requestedName) require
        no provider call.
        """
        typed = _asset_request_from_raw(asset_request)
        key = normalize_request_key(typed.requested_name)
        cached_hash = self._hash_for(key)
        if cached_hash is not None:
            cached = self._cache.get(cached_hash)
            if cached is not None:
                definition, _metadata = cached
                return definition.asset_id

        spec_request_or_error = _build_spec_request(typed)
        if isinstance(spec_request_or_error, str):
            raise AssetGenerationError(spec_request_or_error)
        error = self._invoke_provider(spec_provider, spec_request_or_error)
        if error is not None:
            raise AssetGenerationError(error)
        content = self._last_response.content if self._last_response is not None else None
        if content is None:
            raise AssetGenerationError("spec provider returned no content")
        try:
            spec = parse_asset_spec(content, non_throwing=False)
            definition = compile_asset_spec(spec)
        except AssetSpecError as exc:
            raise AssetGenerationError(
                "spec provider returned an invalid asset spec: "
                + "; ".join(exc.issues[:4])
            ) from None
        except RecursionError as exc:  # DEF-067 belt-and-braces
            raise AssetGenerationError(
                "spec provider returned an excessively nested asset spec"
            ) from None
        self._stage(definition, spec, key)
        return definition.asset_id

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _hash_for(self, key: str) -> str | None:
        with self._lock:
            return self._request_hash.get(key)

    def _stage(
        self,
        definition: GeneratedAssetDefinition,
        spec: Any,
        request_key: str,
    ) -> None:
        """Shared cache put; cache-bound refusal never breaks resolution."""
        spec_hash = spec_hash_for_cache(spec)
        try:
            self._cache.put(
                spec_hash,
                definition,
                spec_bytes=normalize_spec(spec).encode("utf-8"),
                canonical_name=definition.canonical_name,
                category=definition.category,
            )
            with self._lock:
                self._request_hash[request_key] = spec_hash
        except ValueError:
            # Cache-bound refusal (oversized spec / stale versions): the
            # definition still resolves; it is simply not cached.
            return

    def _invoke_provider(
        self, spec_provider: AssetSpecProvider, spec_request: AssetSpecRequest
    ) -> str | None:
        """Call the provider once; return a sanitized error message or None on a
        usable content response (stored on ``self._last_response``).

        ADV-213 (Phase 19B): a TYPED provider failure
        (``StageDriverProviderFailure`` — the Ollama stage driver's budget/
        deadline/timeout signals) is RE-RAISED so the driver/generation
        controller can classify it (per-asset exhaustion attributable to the
        semantic asset, global exhaustion terminal, essential evidence fail
        closed, decorative per the bounded fallback policy). UNKNOWN
        exceptions keep the generic sanitized message — never a raw leak.
        """
        from app.generation.provider import StageDriverProviderFailure

        self._last_response = None
        try:
            response = spec_provider.generate(spec_request)
        except StageDriverProviderFailure:
            # Typed driver/classification failure: propagate unchanged.
            self._last_response = None
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize any OTHER failure
            self._last_response = None
            return f"spec provider failed: {type(exc).__name__}"
        if not isinstance(response, AssetSpecResponse):
            self._last_response = None
            return "spec provider returned an invalid response type"
        if response.error is not None:
            self._last_response = None
            return "spec provider reported an error"
        if response.pending:
            self._last_response = None
            return "spec provider returned a pending result (unexpected in Phase 13)"
        content = response.content
        if content is None or not content.strip():
            self._last_response = None
            return "spec provider returned no content"
        self._last_response = response
        return None

    def _to_generated(
        self, definition: GeneratedAssetDefinition, source_canonical_name: str
    ) -> GeneratedAssetResolution:
        return GeneratedAssetResolution(
            asset_id=definition.asset_id,
            provenance=Provenance.PROCEDURAL_GENERATED,
            definition=definition,
            compiler_version=definition.compiler_version,
            schema_version=definition.schema_version,
            source_canonical_name=source_canonical_name,
        )


# --------------------------------------------------------------------------- #
# module-level convenience API (shared default oracle / cache)
# --------------------------------------------------------------------------- #

_DEFAULT_ORACLE = GeneratedAssetOracle()


def resolve_or_generate(
    request: AssetRequest | Mapping[str, Any],
    spec_provider: AssetSpecProvider | None = None,
    *,
    force_generate: bool = False,
    cache: GeneratedAssetCache | None = None,
    catalog: Catalog | None = None,
) -> AssetResolutionOrError:
    """Module-level ``resolve_or_generate`` over the shared default oracle.

    Tests that need controlled provider-call counting use their own
    ``GeneratedAssetOracle`` instance; production callers may use the shared
    defaults (bounded LRU cache; with NO provider configured the generation
    path is simply unavailable and every request resolves exactly as before).
    """
    oracle = (
        GeneratedAssetOracle(catalog=catalog, cache=cache)
        if cache is not None
        else _DEFAULT_ORACLE
    )
    return oracle.resolve_or_generate(
        request,
        spec_provider=spec_provider,
        force_generate=force_generate,
        catalog=catalog,
    )


def generate_and_stage(
    asset_request: AssetRequest | Mapping[str, Any],
    spec_provider: AssetSpecProvider,
    *,
    cache: GeneratedAssetCache | None = None,
    catalog: Catalog | None = None,
) -> str:
    """Module-level explicit staging helper (returns the ``proc.*`` assetId)."""
    oracle = (
        GeneratedAssetOracle(catalog=catalog, cache=cache)
        if cache is not None
        else _DEFAULT_ORACLE
    )
    return oracle.generate_and_stage(asset_request, spec_provider)


__all__ = [
    "AssetGenerationError",
    "AssetResolutionOrError",
    "GeneratedAssetOracle",
    "GeneratedAssetResolution",
    "generate_and_stage",
    "resolve_or_generate",
]