"""Phase 13 — narrow declarative AssetSpec provider abstraction.

Mirrors the Phase 4 provider shape (``app.generation.provider``) but for the
Phase 13 declarative-spec path: a provider returns RAW optional AssetSpec JSON
text for a bounded request; the trusted Oracle (``app.assets.oracle``) parses,
compiles and caches it. NO live adapter ships in Phase 13 (a later phase may add
one behind this narrow interface); mandatory tests use the deterministic
``FakeAssetSpecProvider`` and the ``CountingSpecProvider`` call counter.

Contract (bounded, deterministic):

- ``AssetSpecRequest`` carries ``requestedName <= 120`` chars, an optional
  ``categoryHint`` and bounded ``tags``;
- ``AssetSpecResponse`` mirrors the Phase 4 ProviderResult shape: exactly ONE
  of ``content`` / ``pending`` / ``error`` may be set. ``pending`` is reserved
  and ALWAYS ``False`` in Phase 13 (no deferred completion path yet);
- ``FakeAssetSpecProvider(scripted)`` returns the scripted spec JSON for the
  casefold-normalized requestedName key, or ``content=None`` when unknown;
- ``CountingSpecProvider(inner)`` wraps any provider and counts
  ``call_count``/``call_log`` (proves cache hits never call the provider).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

MAX_SPEC_REQUEST_NAME_LENGTH = 120
MAX_SPEC_REQUEST_TAGS = 16
MAX_SPEC_RESPONSE_CHARS = 262144  # whole-document ceiling (defense in depth)


class AssetSpecProviderError(Exception):
    """Base class for spec-provider failures."""


class SpecRequestBoundError(ValueError):
    """A request exceeded the documented AssetSpecRequest bounds."""


@dataclass(frozen=True)
class AssetSpecRequest:
    """One bounded declarative-spec provider invocation."""

    requested_name: str
    category_hint: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.requested_name, str) or not self.requested_name:
            raise SpecRequestBoundError(
                "AssetSpecRequest.requested_name must be a non-empty string"
            )
        if len(self.requested_name) > MAX_SPEC_REQUEST_NAME_LENGTH:
            raise SpecRequestBoundError(
                f"requested_name exceeds {MAX_SPEC_REQUEST_NAME_LENGTH} characters"
            )
        if self.category_hint is not None and (
            not isinstance(self.category_hint, str) or not self.category_hint
        ):
            raise SpecRequestBoundError(
                "AssetSpecRequest.category_hint must be a non-empty string or None"
            )
        object.__setattr__(self, "tags", tuple(self.tags or ()))
        if len(self.tags) > MAX_SPEC_REQUEST_TAGS:
            raise SpecRequestBoundError(
                f"tags exceeds the maximum of {MAX_SPEC_REQUEST_TAGS} entries"
            )
        for tag in self.tags:
            if not isinstance(tag, str) or not tag:
                raise SpecRequestBoundError("tags must contain non-empty strings")


@dataclass(frozen=True)
class AssetSpecResponse:
    """The single provider outcome (raw optional spec JSON text or failure).

    Invariant: at most ONE of ``content`` / ``pending`` / ``error`` is set.
    ``pending`` is reserved for the future deferred-completion path and is
    ALWAYS ``False`` in Phase 13.
    """

    content: str | None = None
    pending: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        labels = int(
            (self.content is not None)
            + int(self.pending)
            + int(self.error is not None)
        )
        if labels > 1:
            raise ValueError(
                "AssetSpecResponse must declare at most one outcome "
                "(content | pending | error)"
            )
        if self.content is not None and len(self.content) > MAX_SPEC_RESPONSE_CHARS:
            raise AssetSpecProviderError(
                f"spec provider response exceeds {MAX_SPEC_RESPONSE_CHARS} chars"
            )


class AssetSpecProvider(Protocol):
    """Structural protocol every spec provider implements."""

    def generate(self, request: AssetSpecRequest) -> AssetSpecResponse: ...


class FakeAssetSpecProvider:
    """Deterministic scripted provider: requestedName (casefold-normalized) ->
    raw spec JSON. Unknown keys return ``content=None`` (a miss)."""

    def __init__(self, scripted: Mapping[str, str]) -> None:
        if not isinstance(scripted, Mapping):
            raise TypeError("FakeAssetSpecProvider requires a scripted mapping")
        self._scripted: dict[str, str] = {
            str(key).casefold().strip(): str(value) for key, value in scripted.items()
        }
        self.calls: list[AssetSpecRequest] = []

    def generate(self, request: AssetSpecRequest) -> AssetSpecResponse:
        if not isinstance(request, AssetSpecRequest):
            raise TypeError("generate requires an AssetSpecRequest")
        self.calls.append(request)
        content = self._scripted.get(request.requested_name.casefold().strip())
        if content is None:
            return AssetSpecResponse(content=None)
        return AssetSpecResponse(content=content)


class CountingSpecProvider:
    """Wraps any spec provider; counts calls and logs requests.

    Proves the bounded cache: after two resolves of the SAME requestedName,
    ``call_count == 1`` (the second resolve is served from the cache).
    """

    def __init__(self, inner: AssetSpecProvider) -> None:
        self.inner = inner
        self.call_count = 0
        self.call_log: list[AssetSpecRequest] = []

    def generate(self, request: AssetSpecRequest) -> AssetSpecResponse:
        self.call_count += 1
        self.call_log.append(request)
        return self.inner.generate(request)


# Documented per-generation spec-provider call budget (Phase 14_5): an unseen
# prompt object may consult the provider at most once per name plus limited
# retries — the composer/service wrap the provider in ``BoundedSpecProvider``
# so a single generation attempt can never burn unlimited provider calls.
MAX_SPEC_PROVIDER_CALLS_PER_GENERATION = 6


class BoundedSpecProvider:
    """Deterministic per-generation provider-call budget wrapper.

    After ``call_limit`` invocations the wrapper answers with an explicit
    ``error`` response (never a crash, never a fabricated spec), so a broken
    or adversarial provider cannot exceed the documented budget. Cache hits
    resolve inside the Oracle and never reach this wrapper (zero calls).
    """

    def __init__(
        self,
        inner: AssetSpecProvider,
        call_limit: int = MAX_SPEC_PROVIDER_CALLS_PER_GENERATION,
    ) -> None:
        if not isinstance(call_limit, int) or isinstance(call_limit, bool) or call_limit < 1:
            raise ValueError("BoundedSpecProvider.call_limit must be a positive integer")
        self.inner = inner
        self.call_limit = int(call_limit)
        self.calls = 0

    def generate(self, request: AssetSpecRequest) -> AssetSpecResponse:
        if self.calls >= self.call_limit:
            return AssetSpecResponse(
                error="spec provider call budget exhausted for this generation"
            )
        self.calls += 1
        return self.inner.generate(request)


__all__ = [
    "AssetSpecProvider",
    "AssetSpecProviderError",
    "AssetSpecRequest",
    "AssetSpecResponse",
    "BoundedSpecProvider",
    "CountingSpecProvider",
    "FakeAssetSpecProvider",
    "MAX_SPEC_PROVIDER_CALLS_PER_GENERATION",
    "MAX_SPEC_REQUEST_NAME_LENGTH",
    "MAX_SPEC_REQUEST_TAGS",
    "MAX_SPEC_RESPONSE_CHARS",
    "SpecRequestBoundError",
]