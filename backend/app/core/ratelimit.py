"""Phase 20 — bounded public admission rate limiting (PD-SEC-02).

Two building blocks live here:

- ``SlidingWindowRateLimiter`` — a thread-safe, in-memory rolling-window
  counter per string key, driven by an injectable ``Clock`` so tests are
  deterministic (``ManualClock``) and production uses the app clock.
- ``AnonymousSessionLimiter`` — the bounded public admission policy for
  ``POST /sessions/anonymous``: a per-IP rolling window PLUS a global ceiling.

Trusted-proxy client-IP resolution (``resolve_client_ip``) is also here so the
rate-limit identity rules live next to the limits themselves.

Launch-path authority (DEF-094): the app is the SOLE authority over forwarded-
header trust, but only when uvicorn never rewrites ``request.client`` first.
uvicorn's platform default ``--proxy-headers`` (trusting loopback) replaces
``scope["client"]`` from ``X-Forwarded-For`` BEFORE the ASGI app runs — so
every documented launch path (scripts/start-demo.ps1, docker/entrypoint.sh,
README/docs) passes ``--no-proxy-headers``. ``resolve_client_ip`` warns once
per process if a forwarded header still reaches it while ``TRUST_PROXY=false``
(an operator has likely left uvicorn's proxy-header rewriting on).

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

Phase 21 F-05 (LOW — bounded key cardinality): the per-key map is now bounded
by ``max_keys`` (``DEFAULT_RATELIMIT_MAX_KEYS``, 10 000). Three mechanisms
keep memory bounded WITHOUT ever evicting an active window to make room:

- **Lazy per-key eviction** on ``allow()``: a key whose latest hits have fully
  aged out of its window is removed from the map on its own next ``allow()``
  (an expired identity is immediately reusable — a fresh empty window).
- **Bounded periodic sweep**: at most every ``cleanup_cadence_seconds``
  (default 60s) a full sweep removes every fully-expired key, so identities
  that NEVER return cannot accumulate (never an O(n) pass per request).
- **Hard key ceiling, fail closed**: a NEW key beyond ``max_keys`` is denied
  (the same sanitized 429-style denial as a full window) until a sweep frees a
  slot — an ACTIVE window is never evicted to admit a new identity. This is a
  documented policy: under sustained distinct-identity pressure the LIMITER
  denies new peers (no map growth), and existing in-window peers keep their
  exact Phase 20 behavior.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# DEF-094: when ``TRUST_PROXY=false`` but a forwarded header still reached the
# app, the only way it could have changed the identity is uvicorn double-
# processing (uvicorn's platform default ``--proxy-headers`` rewrites
# ``request.client`` from ``X-Forwarded-For`` BEFORE the ASGI app runs). The
# app cannot recover the true peer at that point, so the fix is the launcher
# flag; the operator warning below makes the misconfiguration LOUD exactly
# once per process (never per-request log spam).
_forwarded_header_warned = False
_forwarded_header_warn_lock = threading.Lock()

_TRUSTED_FORWARDED_HEADER = "x-forwarded-for"

# Sentinel key for the GLOBAL (peer-independent) window of
# ``AnonymousSessionLimiter``. It can never collide with a real peer identity
# (peer IPs are validated ASCII; this is a control byte).
_GLOBAL_KEY = "\x00global"

# Phase 21 F-05 — bounded identity-map cardinality. ``SlidingWindowRateLimiter``
# keeps one in-memory bucket per key; without a bound a hostile peer rotation
# (fresh per-IP identities) could grow the dict without limit. 10 000 distinct
# concurrent identities is far beyond any legitimate hackathon/demo peer set
# while comfortably bounded for a single-process deployment (Phase 20 §8.1
# explicitly accepts in-process rate state for the single-process deployment
# mode). The ceiling is fail-closed: a new identity beyond it is DENIED (the
# route maps the denial to the same sanitized 429 envelope), never recorded,
# and never admitted by evicting an ACTIVE window.
DEFAULT_RATELIMIT_MAX_KEYS = 10_000

# Bounded cleanup cadence (seconds): the full stale-key sweep runs at most this
# often per limiter instance (default 60s), never on every ``allow()``. The
# per-key lazy eviction still frees a key the moment its own window has fully
# expired on its own next use, so in-window correctness never depends on the
# sweep.
DEFAULT_RATELIMIT_CLEANUP_CADENCE_SECONDS = 60.0


class Clock(Protocol):
    """The only time source the rate limiters may read (test seam)."""

    def now(self) -> float: ...


class SlidingWindowRateLimiter:
    """Thread-safe in-memory rolling-window rate limiter keyed by string.

    ``allow(key)`` records the current timestamp when the key has fewer than
    ``limit`` hits inside ``(now - window_seconds, now]``; otherwise it denies
    WITHOUT recording. Older timestamps are evicted on every call so a window
    always rolls forward — a key recovers as soon as its oldest hits age out.

    Phase 21 F-05 bounding (see the module docstring for the full policy):

    - ``max_keys`` (default ``DEFAULT_RATELIMIT_MAX_KEYS``, 10 000): the hard
      ceiling on distinct keys. A NEW key beyond it is denied fail-closed until
      a sweep frees a slot.
    - ``cleanup_cadence_seconds`` (default 60): a full stale-key sweep runs at
      most this often per instance (never an O(n) pass on every request).
    - Lazy eviction on ``allow()`` removes a key as soon as ITS OWN window has
      fully expired (expired identities are immediately reusable).
    - An ACTIVE window is NEVER evicted to make room for a new key.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        limit: int,
        window_seconds: float,
        max_keys: int = DEFAULT_RATELIMIT_MAX_KEYS,
        cleanup_cadence_seconds: float = DEFAULT_RATELIMIT_CLEANUP_CADENCE_SECONDS,
    ) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("rate limiter limit must be an int > 0")
        if isinstance(window_seconds, bool) or not isinstance(
            window_seconds, (int, float)
        ) or float(window_seconds) <= 0:
            raise ValueError("rate limiter window_seconds must be a number > 0")
        if isinstance(max_keys, bool) or not isinstance(max_keys, int) or max_keys <= 0:
            raise ValueError("rate limiter max_keys must be an int > 0")
        if isinstance(cleanup_cadence_seconds, bool) or not isinstance(
            cleanup_cadence_seconds, (int, float)
        ) or float(cleanup_cadence_seconds) <= 0:
            raise ValueError(
                "rate limiter cleanup_cadence_seconds must be a number > 0"
            )
        self._clock = clock
        self._limit = int(limit)
        self._window = float(window_seconds)
        self._max_keys = int(max_keys)
        self._cadence = float(cleanup_cadence_seconds)
        self._last_sweep_at = 0.0
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_seconds(self) -> float:
        return self._window

    @property
    def max_keys(self) -> int:
        """Hard ceiling on distinct live keys (F-05)."""
        return self._max_keys

    @property
    def cleanup_cadence_seconds(self) -> float:
        """How often the full stale-key sweep may run (F-05)."""
        return self._cadence

    @property
    def key_count(self) -> int:
        """Number of distinct keys currently tracked (F-05 inspection/tests)."""
        with self._lock:
            return len(self._hits)

    def _prune_key(self, key: str, current: float) -> None:
        """Drop one key's aged-out hits; remove the key when its window has
        fully expired (lazy eviction — the F-05 stale-key path)."""
        bucket = self._hits.get(key)
        if not bucket:
            return
        cutoff = current - self._window
        # The bucket is append-only FIFO with monotonic timestamps, so every
        # aged-out hit sits at the front; scanning from the front is enough.
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if not bucket:
            del self._hits[key]

    def _full_sweep(self, current: float) -> int:
        """Unconditional stale-key eviction (the F-05 full map pass).

        Removes every key whose latest window has fully expired. An ACTIVE
        window is never touched. Returns the number of evicted keys.
        """
        evicted = 0
        cutoff = current - self._window
        for key in tuple(self._hits.keys()):
            bucket = self._hits.get(key)
            if not bucket:
                del self._hits[key]
                evicted += 1
                continue
            while bucket and bucket[0] < cutoff:
                bucket.pop(0)
            if not bucket:
                del self._hits[key]
                evicted += 1
        return evicted

    def _sweep_expired(self, current: float) -> int:
        """Cadence-gated full sweep: runs at most every ``cleanup_cadence_seconds``."""
        if current - self._last_sweep_at < self._cadence:
            return 0
        self._last_sweep_at = float(current)
        return self._full_sweep(current)

    def sweep_expired(self, now: float | None = None) -> int:
        """Public deterministic stale-key eviction (F-05; tests/inspection).

        Runs the full sweep unconditionally, records the sweep time, and
        returns how many keys were evicted. Same atomicity/lock as ``allow``.
        """
        with self._lock:
            current = float(self._clock.now() if now is None else now)
            self._last_sweep_at = float(current)
            return self._full_sweep(current)

    def allow(self, key: str, now: float | None = None) -> bool:
        """Atomically reserve one window slot for ``key`` when under the limit.

        Returns True (slot recorded) or False (denied, nothing recorded).
        ``now`` is the caller-provided time; the injected clock is used when
        None. Deterministic under concurrency: serialized by the internal lock.

        F-05 denial surface: a FALSE return is either a full in-window bucket or
        the hard key ceiling (a NEW identity beyond ``max_keys``). Both map to
        the SAME sanitized 429 envelope at the route layer — the reason is never
        exposed. An active window is never evicted to admit a new identity.
        """
        with self._lock:
            current = float(self._clock.now() if now is None else now)
            key = str(key)
            # Bounded cadence sweep first: frees slots for fully-expired keys at
            # most every cleanup_cadence_seconds (never per-request O(n)).
            self._sweep_expired(current)
            # Lazy per-key eviction: an arriving key's expired hits are dropped;
            # a fully-expired key is removed from the map (immediately reusable).
            self._prune_key(key, current)
            bucket = self._hits.get(key)
            if bucket is None:
                if len(self._hits) >= self._max_keys:
                    # HARD key ceiling: fail closed for a NEW identity. Never
                    # evict an active window to admit it; the bounded sweep (or
                    # the key owner's own expiry) frees the slot.
                    return False
                bucket = []
                self._hits[key] = bucket
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

    def record(self, key: str, now: float | None = None) -> None:
        """Best-effort accounting: record ONE hit for ``key`` (no under-limit gate).

        Used for FAILED-attempt accounting (ADV-252): the caller records a
        failure AFTER it happened; the pre-accept refusal is a separate
        ``over_limit`` query that records NOTHING. Honors the same F-05 bounds
        as ``allow``: expired hits are lazily pruned and an active window is
        never evicted to admit a new identity. When the hard key ceiling is
        reached, a NEW key's failure is simply NOT recorded (the map stays
        bounded; the key identities are already denied by ``allow`` semantics).
        """
        with self._lock:
            current = float(self._clock.now() if now is None else now)
            key = str(key)
            self._sweep_expired(current)
            self._prune_key(key, current)
            bucket = self._hits.get(key)
            if bucket is None:
                if len(self._hits) >= self._max_keys:
                    return
                bucket = []
                self._hits[key] = bucket
            bucket.append(current)

    def over_limit(self, key: str, now: float | None = None) -> bool:
        """True when ``key`` already has >= ``limit`` in-window hits (query only).

        Records NOTHING — used for the pre-accept gate so a refused attempt
        does not itself consume a budget slot (ADV-252)."""
        return self.count(key, now) >= self._limit

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

    Launch-path authority (DEF-094): this function can only be authoritative
    if nothing BELOW the app rewrote ``request.client`` first. uvicorn's
    platform default is ``--proxy-headers`` (trusting loopback ``127.0.0.1``),
    which replaces ``scope["client"]`` from a hostile ``X-Forwarded-For``
    before the ASGI app is invoked — by the time the route runs, the true peer
    is unrecoverable (the app never sees the pre-rewrite scope). Every
    DOCUMENTED launch path therefore runs uvicorn with ``--no-proxy-headers``
    (scripts/start-demo.ps1, docker/entrypoint.sh, README/docs); the app-level
    trust decision then always sees the true socket peer. When
    ``TRUST_PROXY=false`` and a forwarded header is still observed here, a
    one-time operator warning is emitted (the deployment is likely leaving
    uvicorn's proxy-header rewriting on).
    """
    peer = request.client.host if getattr(request, "client", None) is not None else ""
    peer = str(peer) if peer else "<unknown>"
    if not trust_proxy:
        if _request_carries_forwarded_header(request):
            _warn_forwarded_header_while_trust_off()
        return peer
    forwarded = request.headers.get(_TRUSTED_FORWARDED_HEADER) if request.headers else None
    if forwarded:
        first = str(forwarded).split(",", 1)[0].strip()
        if first:
            return first
    return peer


def _request_carries_forwarded_header(request: Any) -> bool:
    """True when the request carries an ``X-Forwarded-For`` header.

    Deliberately minimal (DEF-094 vector): ``Forwarded`` / ``X-Real-IP`` are
    equally ignored by this module while ``TRUST_PROXY=false``, but only
    ``X-Forwarded-For`` is what uvicorn's ``--proxy-headers`` reads to rewrite
    ``request.client`` — that is the header whose presence signals the risky
    launcher configuration.
    """
    headers = getattr(request, "headers", None)
    if not headers:
        return False
    try:
        return bool(headers.get(_TRUSTED_FORWARDED_HEADER))
    except (AttributeError, TypeError):
        return False


def _warn_forwarded_header_while_trust_off() -> None:
    """Emit ONE process-wide operator warning about the DEF-094 hazard.

    Behavior is never changed by the warning: while ``TRUST_PROXY=false`` the
    direct peer is used regardless. The flag exists so log output can never be
    spammed by an attacker rotating ``X-Forwarded-For`` values.
    """
    global _forwarded_header_warned
    with _forwarded_header_warn_lock:
        if _forwarded_header_warned:
            return
        _forwarded_header_warned = True
    logger.warning(
        "TRUST_PROXY=false but an X-Forwarded-For header reached the app; the "
        "rate-limit identity is the direct socket peer (forwarded headers are "
        "ignored). If uvicorn is running with --proxy-headers (its platform "
        "default trusts loopback) it rewrites request.client from that header "
        "BEFORE the app's identity gate, letting hostile headers mint fresh "
        "per-IP budgets. Every documented launch path runs uvicorn with "
        "--no-proxy-headers — keep it that way whenever TRUST_PROXY=false."
    )


__all__ = [
    "AnonymousSessionLimiter",
    "SlidingWindowRateLimiter",
    "resolve_client_ip",
]
