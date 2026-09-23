"""Player-safe generation capability service seam (Phase16 D / Phase21B §4).

The API layer must stay free of ``app.generation`` imports (hard boundary
contract, ``test_boundaries.py`` — API/DTOs stay schema/service-only). This
service owns the availability-probe seam: it exposes the SANITIZED Ollama
probe from the adapter so ``app.api.v1.generation_capabilities`` only ever
sees capability metadata. Nothing here reveals URLs, credentials, prompts,
network details or internal errors.

Phase21B Finding 4 — bounded probing (the authoritative service seam):

When ``GENERATION_PROVIDER=ollama``, every public
``GET /api/v1/generation-capabilities`` used to trigger a FRESH synchronous
``/api/tags`` probe on every request, with NO cache and NO single-flight, and
the transport used the GENERATION timeout (up to 300s). This service now
bounds that surface:

- **SHORT-TTL result cache** (in-memory, single-process — the documented
  deployment mode; identical to the Phase 20/21 in-memory rate state). The
  cache key is ``(settings identity, transport identity)`` so two different
  provider configs never collide.
- **Single-flight / concurrency deduplication**: concurrent requests for the
  same key share ONE in-flight probe (a ``threading.Condition`` + an
  in-flight counter bounded by ``MAX_CONCURRENT_CAPABILITY_PROBES``), so a
  burst of 100 concurrent requests runs exactly one active provider probe per
  cache window and all 100 receive the same cached result.
- **Dedicated small probe timeout**: the adapter probe runs with
  ``CAPABILITY_PROBE_TIMEOUT_SECONDS`` (default 5s), INDEPENDENT of the
  generation timeout (``OLLAMA_TIMEOUT_SECONDS``), so a hung/unavailable
  Ollama holds a capability request for at most the small probe ceiling.
- **Stale-safe negative cache**: a failure/timeout result is cached for the
  SAME short TTL (``CAPABILITY_CACHE_TTL_SECONDS`` — a single TTL knob is
  reused for positive and negative results; documented) so a hung Ollama is
  re-probed at most once per window, never by every request.
- **Fail closed and sanitized**: the DTO shape is unchanged; every failure
  degrades to ``(False, "not available")`` exactly as before — never the
  Ollama URL, LAN IP, raw provider error or credentials.
- **Bounded cache cardinality** (F-05-style, fail closed): the result map is
  capped (``_MAX_CACHE_KEYS``). Entries hold public-safe config tuples only;
  a NEW key beyond the ceiling is probed (single-flighted) but NOT cached
  until a live window lapses — the map never grows without bound and a live
  window is never evicted to admit a new key.

The probe feeds ONLY the capability DTO. Generation requests keep their OWN
normal provider checks (``OllamaProvider.generate`` / ``BudgetTracker`` /
``ollama_structured_output_supported`` in the generation factory): nothing
here short-circuits ``GenerationService``.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable

from app.generation.ollama_provider import (
    ollama_available as _probe_ollama,
)
from app.persistence.timebase import EpochClock

# Sentinel identity for the DEFAULT httpx transport (the API never passes one).
_DEFAULT_TRANSPORT_IDENTITY = object()

# Bounded cache cardinality (single-process; mirror of the Phase 21 F-05
# fail-closed key ceiling). Only an OPERATOR rotating the provider config at
# runtime can create distinct keys — a handful of entries, far below this
# ceiling. A NEW key beyond the ceiling is probed uncached (never evicting an
# existing live window) until a window lapses.
_MAX_CACHE_KEYS = 256


class _CacheEntry:
    """One cached probe result with the stored-at timestamp."""

    __slots__ = ("result", "stored_at")

    def __init__(self, result: tuple[bool, str], stored_at: float) -> None:
        self.result = result
        self.stored_at = float(stored_at)


class CapabilityProbeCache:
    """Bounded, stale-safe, single-flight Ollama availability probe cache.

    Single-process, in-memory, thread-safe (one ``threading.Condition``
    guards the result map, the in-flight counter and every probe-lifecycle
    transition). The probe itself runs OUTSIDE the lock so a hung transport
    never blocks other cache keys' bookkeeping.
    """

    def __init__(
        self,
        *,
        clock: Any | None = None,
        probe: Callable[..., tuple[bool, str]] | None = None,
    ) -> None:
        self._cond = threading.Condition()
        self._cache: "OrderedDict[tuple[Any, ...], _CacheEntry]" = OrderedDict()
        self._in_flight: dict[tuple[Any, ...], int] = {}
        self._clock = clock if clock is not None else EpochClock()
        # Fall back to the module-level probe binding at call time so tests can
        # monkeypatch ``app.services.generation_capabilities._probe_ollama``.
        self._probe = probe

    # -- public -----------------------------------------------------------

    def ollama_available(
        self, settings: Any, transport: Any = None
    ) -> tuple[bool, str]:
        """Bounded, stale-safe sanitized Ollama availability probe.

        Result is ALWAYS the sanitized ``(True, "")`` / ``(False, "not
        available")`` tuple (the probe adapter NEVER raises; a defensive
        catch here keeps that contract even for a hostile injected probe).
        See the module docstring for the cache / single-flight / negative-TTL
        / dedicated-timeout semantics.
        """
        key = self._cache_key(settings, transport)
        ttl_seconds = float(
            getattr(settings, "capability_cache_ttl_seconds", 15.0) or 15.0
        )
        ceiling = int(
            getattr(settings, "max_concurrent_capability_probes", 1) or 1
        )
        ceiling = max(1, ceiling)

        with self._cond:
            while True:
                now = float(self._clock.now())
                entry = self._cache.get(key)
                if entry is not None and (now - entry.stored_at) < ttl_seconds:
                    return entry.result
                if self._in_flight.get(key, 0) < ceiling:
                    break
                # Another thread is probing this key; wait for it and re-check
                # the (now freshly written) cache entry.
                self._cond.wait()
            self._in_flight[key] = self._in_flight.get(key, 0) + 1
            started_at = float(self._clock.now())

        try:
            result = self._probe_impl(settings, transport)
        except Exception:  # noqa: BLE001 - availability never raises/leaks
            result = (False, "not available")

        with self._cond:
            # Cache the write only when the key already has a live window OR the
            # map has room: a NEW key beyond the bounded ceiling is probed
            # uncached (fail closed; never evict a live window to admit it).
            if key in self._cache or len(self._cache) < _MAX_CACHE_KEYS:
                self._cache[key] = _CacheEntry(result=result, stored_at=started_at)
                self._cache.move_to_end(key)
            remaining = self._in_flight[key] - 1
            if remaining > 0:
                self._in_flight[key] = remaining
            else:
                del self._in_flight[key]
            self._cond.notify_all()
        return result

    def reset(self) -> None:
        """Test seam: drop every cached result and in-flight marker."""
        with self._cond:
            self._cache.clear()
            self._in_flight.clear()
            self._cond.notify_all()

    # -- internal ---------------------------------------------------------

    def _probe_impl(
        self, settings: Any, transport: Any = None
    ) -> tuple[bool, str]:
        probe = self._probe if self._probe is not None else _probe_ollama
        return probe(settings, transport)

    @staticmethod
    def _settings_identity(settings: Any) -> tuple[Any, ...]:
        """The provider-config identity that can change a probe outcome."""
        return (
            getattr(settings, "generation_provider", None),
            getattr(settings, "ollama_base_url", None),
            getattr(settings, "ollama_model", None),
            getattr(settings, "capability_probe_timeout_seconds", 5.0),
        )

    @staticmethod
    def _transport_identity(transport: Any) -> Any:
        if transport is None:
            return _DEFAULT_TRANSPORT_IDENTITY
        return id(transport)

    def _cache_key(
        self, settings: Any, transport: Any = None
    ) -> tuple[Any, ...]:
        return (
            self._settings_identity(settings),
            self._transport_identity(transport),
        )


# Module-level singleton is the production path (the API calls the function
# below; it never builds its own cache instance). Tests can replace/reset it
# via ``reset_capability_probe_cache``.
_probe_cache = CapabilityProbeCache()


def ollama_available(settings: Any, transport: Any = None) -> tuple[bool, str]:
    """Bounded, stale-safe, sanitized Ollama availability probe.

    Returns ``(True, "")`` when the probe succeeds; ``(False, "not available")``
    for every failure — never raises, never reveals network details, the base
    URL, LAN IP or internal errors. Cache TTL + single-flight + dedicated
    small probe timeout + negative caching are handled by the shared
    ``CapabilityProbeCache`` (see the module docstring).
    """
    return _probe_cache.ollama_available(settings, transport)


def reset_capability_probe_cache(
    *,
    clock: Any | None = None,
    probe: Callable[..., tuple[bool, str]] | None = None,
) -> None:
    """Test seam: replace the shared cache (fresh state, optional clock/probe).

    Each test that exercises the shared singleton starts from a clean, empty
    cache with the given deterministic clock, so cached probe results never
    leak across tests.
    """
    global _probe_cache
    _probe_cache = CapabilityProbeCache(clock=clock, probe=probe)


__all__ = ["CapabilityProbeCache", "ollama_available", "reset_capability_probe_cache"]