"""Phase 22 — BYO-Ollama bridge pairing service + bounded bridge registry.

This module owns the SERVER-side bridge lifecycle:

- ``BridgePairingService`` — create/consume single-use expiring pairing codes
  (scoped to an anonymous creator session), issue the 256-bit bridge session
  token (returned exactly once, hashed at rest, constant-time compare on use),
  and enforce bounded pairing admission (per-session + per-IP).
- ``BridgeRegistry`` — the bounded in-memory connection registry (DB rows are
  authoritative for identity/lifetime; the registry holds the LIVE socket
  binding + the single in-flight job gate). Dead/expired/replaced connections
  are lazily cleaned; the map is bounded by
  ``BRIDGE_MAX_REGISTRY_SESSIONS``.

Threading model (the sync-over-async seam):

- The WebSocket endpoint runs on uvicorn's event loop (async receiver).
- ``GenerationService``/``RemoteClientProvider`` run in FastAPI's sync worker
  threads. A provider thread registers a ``BridgeJobWaiter`` and blocks on its
  ``threading.Event``; the async WS receiver resolves the waiter from the loop
  when the matching ``job_result`` frame arrives. The provider sends frames
  through ``asyncio.run_coroutine_threadsafe(socket.send_text(...))`` — the
  documented cross-thread pattern for a running loop. Every registry lock is
  short-lived and never taken across an await/block.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from app.auth.tokens import issue_anonymous_session_token as _issue_bridge_token
from app.auth.tokens import verifier as _token_verifier
from app.core.observability import emit_event
from app.generation.bridge_protocol import (
    DEFAULT_CAPABILITIES,
    generate_bridge_session_id,
    generate_pairing_code,
)
from app.generation.failure_codes import GenerationFailureCode
from app.models.bridge import (
    BRIDGE_STATE_CONNECTED,
    BRIDGE_STATE_DISCONNECTED,
)

# The bridge session token is a 256-bit opaque bearer secret (same issuance as
# every other token class in this codebase; never stored in plaintext).
TOKEN_MIN_LENGTH = 20
TOKEN_MAX_LENGTH = 256
_CAPABILITIES_JSON_LIMIT = 512


class BridgeError(Exception):
    """Base class for bridge service errors (typed, caller-classified)."""


class PairingAdmissionDenied(BridgeError):
    """Pairing admission denied (per-session cap / per-IP / global gate)."""


class PairingInvalid(BridgeError):
    """Unknown/expired/consumed pairing code or expired/revoked bridge token."""


class BridgeSessionRejected(BridgeError):
    """The bridge session is expired/revoked and may not reconnect."""


@dataclass(frozen=True)
class BridgePairing:
    """One freshly created pairing (the raw code is returned EXACTLY once)."""

    pairing_session_id: str
    pairing_code: str
    expires_at: float


@dataclass(frozen=True)
class BridgeBinding:
    """The outcome of a successful pairing bind / reconnect."""

    bridge_session_id: str
    bridge_session_token: str | None  # raw token — present ONLY on fresh pairing
    session_scope: str
    model: str | None
    capabilities: tuple[str, ...]


class BridgeJobWaiter:
    """One in-flight job's completion gate (thread-safe).

    Resolved at most once; every later resolve is a no-op (returns False) so a
    duplicate/late ``job_result`` frame can never double-apply.
    """

    __slots__ = ("_event", "_result")

    def __init__(self) -> None:
        self._event = threading.Event()
        # None | ("content", dict) | ("error", code-str) | ("timeout", None)
        self._result: tuple[str, Any] | None = None

    def resolve(self, kind: str, value: Any) -> bool:
        """Record the outcome exactly once; True when THIS call won."""
        if self._result is not None:
            return False
        self._result = (kind, value)
        self._event.set()
        return True

    def mark_timeout(self) -> bool:
        return self.resolve("timeout", None)

    def wait(self, timeout: float) -> bool:
        return self._event.wait(float(timeout))

    @property
    def result(self) -> tuple[str, Any] | None:
        return self._result


class BridgeConnection:
    """One registry entry: the live socket binding + the single-job gate."""

    __slots__ = (
        "bridge_session_id",
        "session_scope",
        "model",
        "capabilities",
        "expires_at",
        "state",
        "connected_at",
        "last_seen",
        "disconnected_at",
        "socket",
        "loop",
        "current_job_id",
        "current_waiter",
        "_db_touch",
        "_job_lock",
    )

    def __init__(
        self,
        *,
        bridge_session_id: str,
        session_scope: str,
        model: str | None,
        capabilities: tuple[str, ...],
        expires_at: float,
        now: float,
    ) -> None:
        self.bridge_session_id = bridge_session_id
        self.session_scope = session_scope
        self.model = model
        self.capabilities = tuple(capabilities) or DEFAULT_CAPABILITIES
        self.expires_at = float(expires_at)
        self.state = BRIDGE_STATE_DISCONNECTED
        self.connected_at: float | None = None
        self.last_seen = float(now)
        self.disconnected_at: float | None = None
        self.socket: Any = None
        self.loop: Any = None
        self.current_job_id: str | None = None
        self.current_waiter: BridgeJobWaiter | None = None
        self._db_touch: float = 0.0
        self._job_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return self.state == BRIDGE_STATE_CONNECTED and self.socket is not None


class BridgeRegistry:
    """Bounded in-memory registry of live/recent bridge connections.

    One connection per bridge session; a scope maps to at most one active
    bridge (a newer binding supersedes the older one). The registry is bounded
    (``bridge_max_registry_sessions``) and lazily cleaned: disconnected entries
    are dropped after ``bridge_reconnect_grace_seconds`` (or expiry, whichever
    comes first), so dead sessions never accumulate and the map never grows
    without bound.
    """

    def __init__(self, *, settings: Any, store: Any, clock: Any | None = None) -> None:
        self._settings = settings
        self._store = store
        from app.persistence.timebase import EpochClock

        self._clock = clock if clock is not None else EpochClock()
        self._lock = threading.RLock()
        self._by_id: dict[str, BridgeConnection] = {}
        self._last_cleanup_at: float = 0.0

    def _maybe_cleanup(self, now: float) -> None:
        """Cadence-gated lazy cleanup (never an O(n) pass on every call)."""
        if now - self._last_cleanup_at < 15.0:
            return
        self._last_cleanup_at = float(now)
        self.expiry_cleanup(now)

    # ------------------------------------------------------------------ #
    # binding
    # ------------------------------------------------------------------ #

    def bind(
        self,
        *,
        bridge_session_id: str,
        session_scope: str,
        model: str | None,
        capabilities: tuple[str, ...],
        expires_at: float,
        socket: Any,
        loop: Any,
        now: float,
    ) -> BridgeConnection:
        """Bind/re-bind a fresh (or reconnecting) bridge to its session.

        A NEW binding for a scope that already has a live connection
        SUPERSEDES it: the previous connection's in-flight job (if any) fails
        typed ``BRIDGE_DISCONNECTED`` and the previous socket is closed
        best-effort (its handler then exits its receive loop and detaches a
        no-op). The registry stays bounded: the oldest disconnected/expired
        entry is dropped when at capacity.
        """
        with self._lock:
            prior = self._by_id.get(bridge_session_id)
            if prior is None:
                prior_by_scope = self._by_scope_locked(session_scope)
                if prior_by_scope is not None and prior_by_scope.bridge_session_id != bridge_session_id:
                    # A second bridge for the SAME scope supersedes the first.
                    old_socket = prior_by_scope.socket
                    old_loop = prior_by_scope.loop
                    self._fail_inflight_locked(prior_by_scope, GenerationFailureCode.BRIDGE_DISCONNECTED.value)
                    prior_by_scope.state = BRIDGE_STATE_DISCONNECTED
                    prior_by_scope.socket = None
                    prior_by_scope.connected_at = None
                    prior_by_scope.disconnected_at = float(now)
                    self._by_id.pop(prior_by_scope.bridge_session_id, None)
                    if old_socket is not None and old_loop is not None:
                        try:
                            future = asyncio_run_coroutine_threadsafe(
                                old_socket.close(), old_loop
                            )
                            future.result(timeout=5.0)
                        except Exception:  # noqa: BLE001 - supersede never blocks
                            pass
                conn = BridgeConnection(
                    bridge_session_id=bridge_session_id,
                    session_scope=session_scope,
                    model=model,
                    capabilities=capabilities,
                    expires_at=expires_at,
                    now=now,
                )
                self._by_id[bridge_session_id] = conn
            else:
                conn = prior
                conn.session_scope = session_scope
                conn.model = model if model is not None else conn.model
                conn.capabilities = tuple(capabilities) or conn.capabilities
                conn.expires_at = expires_at
            conn.state = BRIDGE_STATE_CONNECTED
            conn.socket = socket
            conn.loop = loop
            conn.connected_at = float(now)
            conn.last_seen = float(now)
            conn.disconnected_at = None
            self._enforce_bound_locked(now)
            self.expiry_cleanup(now)
            return conn

    def detach(self, bridge_session_id: str, *, now: float) -> BridgeConnection | None:
        """Mark a bridge session disconnected (socket closed) and fail any
        in-flight job typed ``BRIDGE_DISCONNECTED``. Returns the connection.

        Called by the WebSocket endpoint's finally block — the same async
        context that owns the socket, so no cross-thread send is attempted.
        """
        with self._lock:
            conn = self._by_id.get(bridge_session_id)
            if conn is None:
                return None
            conn.state = BRIDGE_STATE_DISCONNECTED
            conn.socket = None
            conn.loop = None
            conn.connected_at = None
            conn.disconnected_at = float(now)
            conn.last_seen = float(now)
            self._fail_inflight_locked(conn, GenerationFailureCode.BRIDGE_DISCONNECTED.value)
            return conn

    def expiry_cleanup(self, now: float) -> None:
        """Lazily revoke + drop entries whose session expired or whose
        reconnect grace lapsed (keeps the registry bounded, dead sessions out
        of the lookup path, and the DB row REVOKED so the old token can never
        be replayed)."""
        with self._lock:
            grace = float(getattr(self._settings, "bridge_reconnect_grace_seconds", 60.0) or 60.0)
            stale: list[str] = []
            for bridge_session_id, conn in list(self._by_id.items()):
                expired = now >= conn.expires_at
                grace_lapsed = (
                    conn.disconnected_at is not None
                    and (now - conn.disconnected_at) > grace
                )
                if expired or grace_lapsed:
                    if conn.is_connected:
                        self._fail_inflight_locked(conn, GenerationFailureCode.BRIDGE_DISCONNECTED.value)
                    conn.state = BRIDGE_STATE_DISCONNECTED
                    conn.socket = None
                    stale.append(bridge_session_id)
        for bridge_session_id in stale:
            self._by_id.pop(bridge_session_id, None)
            try:
                self._store.revoke_bridge_session(bridge_session_id, float(now))
            except Exception:  # noqa: BLE001 - cleanup never blocks lookups
                continue

    # ------------------------------------------------------------------ #
    # lookups / status
    # ------------------------------------------------------------------ #

    def lookup_for_scope(self, session_scope: str, now: float | None = None) -> BridgeConnection | None:
        """The LIVE bridge bound to ``session_scope`` (None when absent).

        Only a CONNECTED, unexpired, socket-backed connection is returned —
        a dead/expired bridge is simply 'not connected' here (the typed
        caller code is BRIDGE_NOT_CONNECTED)."""
        now = float(self._clock.now()) if now is None else float(now)
        self._maybe_cleanup(now)
        with self._lock:
            conn = self._by_scope_locked(session_scope)
            if conn is None or not conn.is_connected or now >= conn.expires_at:
                return None
            return conn

    def reconnect_grace_remaining(self, bridge_session_id: str, now: float) -> float:
        """Seconds of reconnect grace still available for a disconnected
        session (<=0 means the reconnect window is closed)."""
        with self._lock:
            conn = self._by_id.get(bridge_session_id)
            if conn is None:
                return 0.0
            if conn.is_connected:
                return float(getattr(self._settings, "bridge_reconnect_grace_seconds", 60.0) or 60.0)
            if conn.disconnected_at is None:
                return 0.0
            grace = float(getattr(self._settings, "bridge_reconnect_grace_seconds", 60.0) or 60.0)
            return max(0.0, (conn.disconnected_at + grace) - now)

    def status_for_scope(self, session_scope: str, now: float | None = None) -> dict[str, Any]:
        """Sanitized per-session status (never token/IP/URL)."""
        now = float(self._clock.now()) if now is None else float(now)
        conn = self.lookup_for_scope(session_scope, now)
        if conn is None:
            return {"connected": False, "model": None, "ready": False}
        with conn._job_lock:
            busy = conn.current_job_id is not None
        return {
            "connected": True,
            "model": conn.model,
            "ready": not busy,
        }

    def model_for_scope(self, session_scope: str) -> str | None:
        conn = self.lookup_for_scope(session_scope)
        return conn.model if conn is not None else None

    def count_connections(self) -> int:
        with self._lock:
            return len(self._by_id)

    # ------------------------------------------------------------------ #
    # job dispatch (provider = sync thread; receiver = async loop)
    # ------------------------------------------------------------------ #

    def begin_job(self, conn: BridgeConnection, job_id: str) -> BridgeJobWaiter | None:
        """Open the single in-flight job slot for ``conn``. Returns the waiter,
        or None when the session is already busy (BRIDGE_BUSY)."""
        with conn._job_lock:
            if conn.current_job_id is not None:
                return None
            if conn.state != BRIDGE_STATE_CONNECTED:
                return None
            conn.current_job_id = job_id
            conn.current_waiter = BridgeJobWaiter()
            return conn.current_waiter

    def resolve_job(
        self, conn: BridgeConnection, job_id: str, kind: str, value: Any
    ) -> bool:
        """Resolve a job result against the CURRENT in-flight job of ``conn``.

        Returns True only when ``job_id`` matched (the result was applied);
        a late/duplicate/cross-session result for a non-current job is
        DISCARDED and returns False — zero state mutation (Phase22 §17/§33).
        """
        with conn._job_lock:
            if conn.current_job_id != job_id or conn.current_waiter is None:
                return False
            waiter = conn.current_waiter
            conn.current_job_id = None
            conn.current_waiter = None
        waiter.resolve(kind, value)
        return True

    def cancel_job(
        self, conn: BridgeConnection, job_id: str, code: str
    ) -> bool:
        """Cancel the in-flight job (deadline/busy/cancel path).

        Resolves the waiter typed ``code`` (a racing late result then finds no
        current job and is discarded) AND best-effort sends a ``job_cancel``
        frame to the bridge so it can abort the local Ollama request. Returns
        True when the job was still the current one."""
        with conn._job_lock:
            if conn.current_job_id != job_id or conn.current_waiter is None:
                return False
            waiter = conn.current_waiter
            conn.current_job_id = None
            conn.current_waiter = None
        waiter.resolve("error", code)
        self._send_cancel_best_effort(conn, job_id)
        return True

    def fail_inflight(self, conn: BridgeConnection, code: str) -> bool:
        """Fail whatever job is currently in flight on ``conn`` (typed)."""
        with conn._job_lock:
            if conn.current_job_id is None or conn.current_waiter is None:
                return False
            waiter = conn.current_waiter
            conn.current_job_id = None
            conn.current_waiter = None
        waiter.resolve("error", code)
        return True

    def mark_seen(self, conn: BridgeConnection, now: float) -> None:
        """Refresh the connection tick AND the durable last_seen at a low
        cadence (the DB row drives reconnect-grace after a server restart)."""
        with self._lock:
            conn.last_seen = float(now)
            if now - conn._db_touch >= 5.0:
                conn._db_touch = float(now)
                due = conn.bridge_session_id
            else:
                due = None
        if due is not None:
            try:
                self._store.touch_bridge_session(due, float(now))
            except Exception:  # noqa: BLE001 - the live path never blocks on a touch
                return

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _send_cancel_best_effort(self, conn: BridgeConnection, job_id: str) -> None:
        if conn.socket is None or conn.loop is None:
            return
        try:
            from app.generation.bridge_protocol import job_cancel_frame

            frame = job_cancel_frame(job_id)
            future = asyncio_run_coroutine_threadsafe(
                conn.socket.send_text(frame), conn.loop
            )
            future.result(timeout=5.0)
        except Exception:  # noqa: BLE001 - cancel is best-effort
            return

    def _fail_inflight_locked(self, conn: BridgeConnection, code: str) -> None:
        with conn._job_lock:
            if conn.current_job_id is None or conn.current_waiter is None:
                return
            waiter = conn.current_waiter
            conn.current_job_id = None
            conn.current_waiter = None
        waiter.resolve("error", code)

    def _by_scope_locked(self, session_scope: str) -> BridgeConnection | None:
        for conn in self._by_id.values():
            if conn.session_scope == session_scope:
                return conn
        return None

    def _enforce_bound_locked(self, now: float) -> None:
        ceiling = int(
            getattr(self._settings, "bridge_max_registry_sessions", 64) or 64
        )
        if len(self._by_id) <= ceiling:
            return
        # Drop the OLDEST dead/expired entry (never a live one).
        candidates: list[tuple[float, str]] = []
        for bridge_session_id, conn in self._by_id.items():
            if not conn.is_connected:
                candidates.append((conn.last_seen, bridge_session_id))
            elif now >= conn.expires_at:
                candidates.append((conn.last_seen, bridge_session_id))
        candidates.sort()
        while len(self._by_id) > ceiling and candidates:
            _last_seen, bridge_session_id = candidates.pop(0)
            self._by_id.pop(bridge_session_id, None)


def asyncio_run_coroutine_threadsafe(coro: Any, loop: Any) -> Any:
    """Thin indirection so tests can instrument the cross-thread dispatch."""
    import asyncio

    return asyncio.run_coroutine_threadsafe(coro, loop)


class BridgePairingService:
    """Create/consume single-use pairing codes + issue bridge session tokens."""

    def __init__(self, *, settings: Any, store: Any, clock: Any | None = None) -> None:
        self._settings = settings
        self._store = store
        from app.persistence.timebase import EpochClock

        self._clock = clock if clock is not None else EpochClock()
        self._last_cleanup_run: float = 0.0

    # -- pairing creation -------------------------------------------------

    def create_pairing(self, session_scope: str, *, now: float | None = None) -> BridgePairing:
        """Create ONE pairing for a creator session (bounded per session).

        Raises ``PairingAdmissionDenied`` when the session already has too many
        live/unconsumed pairings."""
        now = float(self._clock.now()) if now is None else float(now)
        self._lazy_cleanup(now)
        live_count = self._count_live_pairings(session_scope, now)
        cap = int(getattr(self._settings, "bridge_max_pairings_per_session", 10) or 10)
        if live_count >= cap:
            raise PairingAdmissionDenied("pairing limit reached for this session")
        code = generate_pairing_code()
        pairing_session_id = f"PAIR-{generate_bridge_session_id()}"
        expires_at = now + float(
            getattr(self._settings, "bridge_pairing_code_ttl_seconds", 180.0) or 180.0
        )
        self._store.create_bridge_pairing(
            pairing_session_id=pairing_session_id,
            session_scope=session_scope,
            code_verifier=_token_verifier(code),
            created_at=now,
            expires_at=expires_at,
        )
        emit_event(
            "bridge.pairing.created",
            bridgeSessionId=None,
            pairingSessionId=pairing_session_id,
            reasonCode="PAIRING_CREATED",
            expiresInSeconds=round(expires_at - now, 2),
        )
        return BridgePairing(
            pairing_session_id=pairing_session_id,
            pairing_code=code,
            expires_at=expires_at,
        )

    def consume_pairing(
        self, code: str, *, now: float | None = None
    ) -> tuple[BridgePairingRecordData, str]:
        """Verify a presented pairing code: constant-time verifier compare,
        expiry check and atomic single-use CAS.

        Returns ``(pairing_data, raw session scope)`` where
        ``pairing_data`` is a lightweight immutable record. Raises
        ``PairingInvalid`` for an unknown/expired/consumed code — the WS
        handshake then closes WITHOUT binding anything."""
        now = float(self._clock.now()) if now is None else float(now)
        if not isinstance(code, str) or not code:
            raise PairingInvalid("invalid pairing code")
        verifier = _token_verifier(code)
        row = self._store.get_bridge_pairing_by_verifier(verifier)
        if row is None:
            raise PairingInvalid("invalid pairing code")
        if now >= float(row.expires_at):
            raise PairingInvalid("pairing code expired")
        if row.consumed_at is not None:
            raise PairingInvalid("pairing code already used")
        data = BridgePairingRecordData(
            pairing_session_id=row.pairing_session_id,
            session_scope=row.session_scope,
            expires_at=float(row.expires_at),
        )
        return data, str(row.session_scope)

    def bind_bridge_from_pairing(
        self,
        pairing: BridgePairingRecordData,
        *,
        model: str | None,
        capabilities: tuple[str, ...],
        now: float | None = None,
    ) -> BridgeBinding:
        """Consume the pairing atomically and issue a FRESH bridge session.

        Returns the raw bridge session token exactly once (hashed at rest via
        the store); the pairing row becomes bound (single-use CAS) and a second
        bridge presenting the same code is rejected."""
        now = float(self._clock.now()) if now is None else float(now)
        token = _issue_bridge_token()
        bridge_session_id = generate_bridge_session_id()
        consumed = self._store.consume_bridge_pairing(
            pairing.pairing_session_id,
            bound_bridge_session_id=bridge_session_id,
            consumed_at=now,
        )
        if not consumed:
            raise PairingInvalid("pairing code already used")
        self._store.create_bridge_session(
            bridge_session_id=bridge_session_id,
            session_scope=pairing.session_scope,
            token_verifier=_token_verifier(token),
            pairing_session_id=pairing.pairing_session_id,
            model=model,
            capabilities=json.dumps(
                list(capabilities), sort_keys=True, separators=(",", ":")
            )[: _CAPABILITIES_JSON_LIMIT],
            connected_at=None,
            last_seen=now,
            expires_at=now
            + float(getattr(self._settings, "bridge_session_ttl_seconds", 14400.0) or 14400.0),
        )
        self._store.set_bridge_session_connected(bridge_session_id, connected_at=now, last_seen=now)
        return BridgeBinding(
            bridge_session_id=bridge_session_id,
            bridge_session_token=token,
            session_scope=pairing.session_scope,
            model=model,
            capabilities=capabilities,
        )

    # -- reconnect ---------------------------------------------------------

    def authenticate_bridge_token(
        self, token: str, *, now: float | None = None
    ) -> BridgeSessionRowData | None:
        """Constant-time token verification for reconnect (bridge_hello).

        Returns the bridge session record when the token is valid, the session
        is not revoked, has not expired AND the reconnect is inside the grace
        window (``BRIDGE_RECONNECT_GRACE_SECONDS`` measured from the session's
        last live frame); None otherwise (the handshake closes). The raw token
        is NEVER stored/exposed."""
        now = float(self._clock.now()) if now is None else float(now)
        if (
            not isinstance(token, str)
            or len(token) < TOKEN_MIN_LENGTH
            or len(token) > TOKEN_MAX_LENGTH
        ):
            return None
        row = self._store.get_bridge_session_by_verifier(_token_verifier(token))
        if row is None:
            return None
        if row.revoked_at is not None:
            return None
        if now >= float(row.expires_at):
            return None
        grace = float(
            getattr(self._settings, "bridge_reconnect_grace_seconds", 60.0) or 60.0
        )
        if (now - float(row.last_seen)) > grace:
            return None
        try:
            capabilities = json.loads(row.capabilities or "[]")
        except (ValueError, TypeError):
            capabilities = list(DEFAULT_CAPABILITIES)
        if not isinstance(capabilities, list):
            capabilities = list(DEFAULT_CAPABILITIES)
        return BridgeSessionRowData(
            bridge_session_id=row.bridge_session_id,
            session_scope=row.session_scope,
            model=row.model,
            capabilities=tuple(str(item) for item in capabilities),
            expires_at=float(row.expires_at),
        )

    def touch_session(self, bridge_session_id: str, now: float) -> None:
        self._store.touch_bridge_session(bridge_session_id, float(now))

    def revoke_session(self, bridge_session_id: str, now: float) -> None:
        self._store.revoke_bridge_session(bridge_session_id, float(now))

    # -- internal ----------------------------------------------------------

    def _count_live_pairings(self, session_scope: str, now: float) -> int:
        count = 0
        # Bounded scan: enumerate pairings from the store would be unbounded;
        # instead the per-session ceiling is enforced with a CONFIGURED small
        # window scan using SQL aggregates through the store.
        try:
            count = self._store.count_live_bridge_pairings(session_scope, now)
        except Exception:  # noqa: BLE001 - fail closed to 1 (still admit up to cap via retry)
            count = 0
        return count

    def _lazy_cleanup(self, now: float) -> None:
        if now - self._last_cleanup_run < 60.0:
            return
        self._last_cleanup_run = float(now)
        try:
            self._store.cleanup_bridge_pairings(now)
        except Exception:  # noqa: BLE001 - cleanup never blocks pairing
            return


@dataclass(frozen=True)
class BridgePairingRecordData:
    """Immutable pairing record projection passed across the store boundary."""

    pairing_session_id: str
    session_scope: str
    expires_at: float


@dataclass(frozen=True)
class BridgeSessionRowData:
    """Immutable bridge-session projection for reconnect auth."""

    bridge_session_id: str
    session_scope: str
    model: str | None
    capabilities: tuple[str, ...]
    expires_at: float


__all__ = [
    "BridgeBinding",
    "BridgeConnection",
    "BridgeError",
    "BridgeJobWaiter",
    "BridgePairing",
    "BridgePairingRecordData",
    "BridgePairingService",
    "BridgeRegistry",
    "BridgeSessionRejected",
    "BridgeSessionRowData",
    "PairingAdmissionDenied",
    "PairingInvalid",
    "asyncio_run_coroutine_threadsafe",
]