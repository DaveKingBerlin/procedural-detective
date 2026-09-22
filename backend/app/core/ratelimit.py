"""Phase 20 — bounded public admission rate limiting (PD-SEC-02).

Two building blocks live here:

- ``SlidingWindowRateLimiter`` — a thread-safe, in-memory rolling-window
  counter per string key, driven by an injectable ``Clock`` so tests are
  deterministic (``ManualClock``) and production uses the app clock.
- ``AnonymousSessionLimiter`` — the bounded public admission policy for
  ``POST /sessions/anonymous``: a per-IP rolling window PLUS a global ceiling.

Trusted-proxy client-IP resolution (``resolve_client_ip``) is also here so the
rate-limit identity rules live next to the limits themselves.

Deployment mode (§8.1): hackathon hosting is a SINGLE-PROCESS deployment. The
rate state below is in-process memory (which the phase explicitly accepts:
"single-process durable in-memory or SQLite-backed rate state may be
acceptable") and NO multi-replica claim is made — a multi-replica deployment
must share the admission/rate state atomically (out of scope here). Memory is
bounded: each key keeps at most ``limit`` timestamps and stale keys are only
bounded by the number of distinct peers seen (a few thousand worst-case for a
hackathon; the global key adds one entry).

Concurrency/atomicity guarantee: every mutation runs under a single
``threading.Lock`` per limiter, so ``allow`` is atomic — a burst of concurrent
requests can NEVER double-spend a window slot (no TOCTOU).
"""

from __future__ import annotations

import threading
from typing import Any, Protocol

_TRUSTED_FORWARDED_HEADER = "x-forwarded-for"

# Sentinel key for the GLOBAL (peer-independent) window of
# ``AnonymousSessionLimiter``. It can never collide with a real peer identity
# (peer IPs are validated ASCII; this is a control byte).
_GLOBAL_KEY = "\x00global"


class Clock(Protocol):
    """The only time source the rate limiters may read (test seam)."""

    def now(self) -> float: ...


class SlidingWindowRateLimiter:
    """Thread-safe in-memory rolling-window rate limiter keyed by string.

    ``allow(key)`` records the current timestamp when the key has fewer than
    ``limit`` hits inside ``(now - window_seconds, now]``; otherwise it denies
    WITHOUT recording. Older timestamps are evicted on every call so a window
    always rolls forward — a key recovers as soon as its oldest hits age out.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        limit: int,
        window_seconds: float,
    ) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("rate limiter limit must be an int > 0")
        if isinstance(window_seconds, bool) or not isinstance(
            window_seconds, (int, float)
        ) or float(window_seconds) <= 0:
            raise ValueError("rate limiter window_seconds must be a number > 0")
        self._clock = clock
        self._limit = int(limit)
        self._window = float(window_seconds)
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_seconds(self) -> float:
        return self._window

    def allow(self, key: str, now: float | None = None) -> bool:
        """Atomically reserve one window slot for ``key`` when under the limit.

        Returns True (slot recorded) or False (denied, nothing recorded).
        ``now`` is the caller-provided time; the injected clock is used when
        None. Deterministic under concurrency: serialized by the internal lock.
        """
        with self._lock:
            current = float(self._clock.now() if now is None else now)
            bucket = self._hits.setdefault(str(key), [])
            cutoff = current - self._window
            # Rolling window: drop hits that have already aged out (the bucket
            # is FIFO, so scanning from the front is enough).
            while bucket and bucket[0] < cutoff:
                bucket.pop(0)
            if len(bucket) >= self._limit:
                return False
            bucket.append(current)
            return True

    def count(self, key: str, now: float | None = None) -> int:
        """Current in-window hit count for ``key`` (inspection/tests)."""
        with self._lock:
            current = float(self._clock.now() if now is None else now)
            bucket = self._hits.get(str(key), [])
            cutoff = current - self._window
            return sum(1 for ts in bucket if ts >= cutoff)

    def remaining(self, key: str, now: float | None = None) -> int:
        """Remaining allowance for ``key`` inside the current window."""
        return max(0, self._limit - self.count(key, now))


class AnonymousSessionLimiter:
    """PD-SEC-02 §6 — bounded public admission for anonymous session creation.

    ``allow(ip)`` enforces BOTH the per-IP rolling window and the global
    ceiling. Policy:

    - the per-IP window is checked (and reserved) FIRST;
    - the global ceiling is checked (and reserved) second;
    - every check is atomic; a denied attempt consumes NO provider/session
      resources (rejection happens before ``GenerationService`` is invoked);
    - a request that is allowed per-IP but denied globally still consumed its
      own per-IP slot (the attempted operation is counted — same documented
      "reservation attempts count" policy as generation admission).
    """

    def __init__(
        self,
        *,
        clock: Clock,
        per_ip_limit: int,
        per_ip_window_seconds: float,
        global_limit: int,
        global_window_seconds: float,
    ) -> None:
        self._per_ip = SlidingWindowRateLimiter(
            clock=clock, limit=per_ip_limit, window_seconds=per_ip_window_seconds
        )
        self._global = SlidingWindowRateLimiter(
            clock=clock, limit=global_limit, window_seconds=global_window_seconds
        )

    @property
    def per_ip_limiter(self) -> SlidingWindowRateLimiter:
        return self._per_ip

    @property
    def global_limiter(self) -> SlidingWindowRateLimiter:
        return self._global

    def allow(self, ip: str, now: float | None = None) -> tuple[bool, str]:
        """Reserve anonymous-session admission slot(s); returns (allowed, reason).

        ``reason`` is ``""`` when allowed, else ``"per_ip"`` or ``"global"`` —
        the route maps either denial to the same sanitized 429 envelope.
        """
        key = str(ip) if ip else "<unknown>"
        if not self._per_ip.allow(key, now):
            return False, "per_ip"
        if not self._global.allow(_GLOBAL_KEY, now):
            return False, "global"
        return True, ""


def resolve_client_ip(request: Any, *, trust_proxy: bool) -> str:
    """Rate-limit identity of one request (PD-SEC-02 §6.2).

    DEFAULT: the direct socket peer address (``request.client.host``). When
    ``TRUST_PROXY=true`` the deployment is documented to sit behind the trusted
    TLS reverse proxy (Internet -> HTTPS proxy -> private Uvicorn), and the
    LEFT-MOST entry of ``X-Forwarded-For`` is honored as the original client IP.

    Documented caveats (never softened):

    - forwarded headers are NEVER trusted while ``TRUST_PROXY=false`` — a
      hostile ``X-Forwarded-For: 192.0.2.1`` must not change the rate-limit
      identity (the direct peer is always used);
    - with a chain of proxies each appends the previous hop to
      ``X-Forwarded-For`` (left = original client, then each proxy). Only the
      LEFT-MOST entry is used, and only when the deployment's single trusted
      TLS edge rewrites the header; a deployment with multiple untrusted hops
      must keep ``TRUST_PROXY=false`` (the peer address of the trusted edge);
    - a missing/malformed header falls back to the direct peer address.
    """
    peer = request.client.host if getattr(request, "client", None) is not None else ""
    peer = str(peer) if peer else "<unknown>"
    if not trust_proxy:
        return peer
    forwarded = request.headers.get(_TRUSTED_FORWARDED_HEADER) if request.headers else None
    if forwarded:
        first = str(forwarded).split(",", 1)[0].strip()
        if first:
            return first
    return peer


__all__ = [
    "AnonymousSessionLimiter",
    "SlidingWindowRateLimiter",
    "resolve_client_ip",
]
