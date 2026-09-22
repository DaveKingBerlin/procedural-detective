"""Durable, thread-safe AdmissionController wrapper (Phase5 E step 6).

The Phase 4 ``AdmissionController`` owns per-session + global in-memory
counters. Phase 5 makes the anonymous quota session rows durable in SQLite
(the authoritative state) and rehydrates the in-memory controller from the DB
when a session was created before the current process started.

This wrapper adds:

- a reentrant lock around every counter mutation (``create_anonymous_quota_session``,
  ``admit_generation``, ``release_generation``) so concurrent creator requests
  on the shared controller are safe;
- ``rehydrate_session`` — registers a persisted session (with its durable
  ``quota_window_end`` and ``generations_count``) when the current process has
  not seen it, so a session created before a restart is still admitted with
  its persisted window/count. The service rehydrates every persisted session
  at startup via ``Store.list_sessions``.

The semantic guarantees NEVER depend on this lock: quota enforcement lives in
``admit_generation`` and the durable rows are authoritative across restarts.
"""

from __future__ import annotations

import threading
from typing import Any

from app.generation.admission import (
    AdmissionController,
    AnonymousQuotaSession,
    _SessionState,
)
from app.generation.clock import Clock
from app.generation.ids import IdSource


class DurableAdmissionController(AdmissionController):
    """Thread-safe admission with a durable rehydration seam."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdSource,
        max_concurrent_generations: int,
        max_concurrent_generations_global: int,
        max_generations_per_session_per_window: int,
        max_generations_global_per_window: int,
        anonymous_quota_session_ttl_seconds: int,
        global_window_end: float | None = None,
        global_window_seconds: int = 60,
        max_sessions: int = 10_000,
        lock: threading.RLock | None = None,
    ) -> None:
        super().__init__(
            clock=clock,
            ids=ids,
            max_concurrent_generations=max_concurrent_generations,
            max_concurrent_generations_global=max_concurrent_generations_global,
            max_generations_per_session_per_window=max_generations_per_session_per_window,
            max_generations_global_per_window=max_generations_global_per_window,
            anonymous_quota_session_ttl_seconds=anonymous_quota_session_ttl_seconds,
            global_window_end=global_window_end,
            global_window_seconds=global_window_seconds,
            max_sessions=max_sessions,
        )
        self._lock = lock if lock is not None else threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        """The lock every mutation runs under (shared by the service)."""
        return self._lock

    def is_known(self, session_id: str) -> bool:
        """True when the in-memory controller tracks this session."""
        with self._lock:
            return session_id in self._sessions

    # -- locking wrappers over the mutable counter surface ------------------

    def create_anonymous_quota_session(self) -> AnonymousQuotaSession:
        with self._lock:
            return super().create_anonymous_quota_session()

    def admit_generation(
        self, session_id: str, *, creator_token: str | None = None
    ) -> Any:
        with self._lock:
            return super().admit_generation(session_id, creator_token=creator_token)

    def release_generation(self, session_id: str) -> None:
        with self._lock:
            return super().release_generation(session_id)

    def rehydrate_session(
        self,
        session_id: str,
        created_at: float,
        quota_window_end: float,
        generations_count: int,
    ) -> None:
        """Register a persisted session the current process has not seen yet.

        Idempotent: when the in-memory controller already tracks the session,
        the live counters are authoritative and are never overwritten.
        """
        with self._lock:
            if session_id in self._sessions:
                return
            self._sessions[session_id] = _SessionState(
                session=AnonymousQuotaSession(
                    session_id=session_id,
                    created_at=float(created_at),
                    quota_window_end=float(quota_window_end),
                ),
                generations_count=int(generations_count),
            )
            # ADV-229: keep the creation-order deque consistent so the lazy
            # eviction pass sees rehydrated sessions too (they stay live until
            # their persisted window is past the grace cutoff).
            self._session_order.append(session_id)
