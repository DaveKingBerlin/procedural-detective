"""Admission control and stable anonymous quota identity (REQUIREMENTS 32.9-32.11).

Quota identity is the ``anonymousQuotaSessionId`` ONLY: creator credentials,
caseId and generationAttemptId are never quota keys (REQUIREMENTS 32.9).
``creator_token`` is accepted for interface compatibility but is IGNORED for
quota decisions — obtaining a new creator token cannot reset a session's
quota.

Semantics per REQUIREMENTS 32.10/32.11:

- admission is checked BEFORE the first provider/model call;
- a rejected admission makes ZERO provider calls (the controller calls
  ``admit_generation`` before constructing/building any provider request);
- per-session AND aggregate (global) concurrency limits are enforced;
- a ROLLING global window starts at controller construction (or an injected
  ``global_window_end``) and — PD-SEC-02 fix — RENEWS automatically: when the
  current time reaches ``window_end`` a new window starts
  (``window_end = now + global_window_seconds``) and the rolled counters are
  reset atomically. The window can NEVER reject-forever after an expiry.
  Deterministic with a ``ManualClock``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.generation.clock import Clock
from app.generation.ids import IdSource


@dataclass(frozen=True)
class AnonymousQuotaSession:
    """The immutable public identity of one anonymous quota session.

    A session is valid until ``quota_window_end``
    (``created_at + anonymous_quota_session_ttl_seconds``).
    """

    session_id: str
    created_at: float
    quota_window_end: float


@dataclass(frozen=True)
class AdmissionDecision:
    """Result of one admission check (``admitted=True`` or a denial reason)."""

    admitted: bool
    reason: str | None = None


class AdmissionDenied(Exception):
    """Raised (carrying the decision) when admission is denied.

    The controller propagates this out of ``start_generation`` so the caller
    sees a terminal user-safe error; rejected admission consumes no provider
    call budget (REQUIREMENTS 32.10).
    """

    def __init__(self, decision: AdmissionDecision) -> None:
        self.decision = decision
        self.reason = decision.reason
        super().__init__(decision.reason or "admission denied")


@dataclass
class _SessionState:
    """Mutable per-session counters (generation quota)."""

    session: AnonymousQuotaSession
    generations_count: int = 0
    active_concurrency: int = 0


class AdmissionController:
    """Per-session + global admission gates; reserves and releases concurrency."""

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
    ) -> None:
        if isinstance(max_concurrent_generations, bool) or not isinstance(
            max_concurrent_generations, int
        ):
            raise TypeError("max_concurrent_generations must be an int")
        if max_concurrent_generations <= 0:
            raise ValueError("max_concurrent_generations must be > 0")
        if isinstance(max_concurrent_generations_global, bool) or not isinstance(
            max_concurrent_generations_global, int
        ):
            raise TypeError("max_concurrent_generations_global must be an int")
        if max_concurrent_generations_global <= 0:
            raise ValueError("max_concurrent_generations_global must be > 0")
        if isinstance(max_generations_per_session_per_window, bool) or not isinstance(
            max_generations_per_session_per_window, int
        ):
            raise TypeError("max_generations_per_session_per_window must be an int")
        if max_generations_per_session_per_window <= 0:
            raise ValueError("max_generations_per_session_per_window must be > 0")
        if isinstance(max_generations_global_per_window, bool) or not isinstance(
            max_generations_global_per_window, int
        ):
            raise TypeError("max_generations_global_per_window must be an int")
        if max_generations_global_per_window <= 0:
            raise ValueError("max_generations_global_per_window must be > 0")
        if isinstance(anonymous_quota_session_ttl_seconds, bool) or not isinstance(
            anonymous_quota_session_ttl_seconds, int
        ):
            raise TypeError("anonymous_quota_session_ttl_seconds must be an int")
        if anonymous_quota_session_ttl_seconds <= 0:
            raise ValueError("anonymous_quota_session_ttl_seconds must be > 0")
        if isinstance(global_window_seconds, bool) or not isinstance(
            global_window_seconds, int
        ):
            raise TypeError("global_window_seconds must be an int")
        if global_window_seconds <= 0:
            raise ValueError("global_window_seconds must be > 0")
        if global_window_end is not None and (
            isinstance(global_window_end, bool)
            or not isinstance(global_window_end, (int, float))
        ):
            raise TypeError("global_window_end must be a number or None")
        self._clock = clock
        self._ids = ids
        self._max_session_concurrency = int(max_concurrent_generations)
        self._max_global_concurrency = int(max_concurrent_generations_global)
        self._max_session_window = int(max_generations_per_session_per_window)
        self._max_global_window = int(max_generations_global_per_window)
        self._ttl_seconds = float(anonymous_quota_session_ttl_seconds)
        # PD-SEC-02: the ROLLING global generation window. It starts at
        # controller construction (or the injected end) and RENEWS forever:
        # when ``now >= window_end`` the admission step starts a fresh window
        # of ``global_window_seconds`` and resets the rolled counters. The
        # window is measured in ``global_window_seconds`` (a short rolling
        # quota, sensible default 60s) — NOT the anonymous-session TTL.
        self._global_window_seconds = float(global_window_seconds)
        now = float(clock.now())
        self._global_window_end = (
            float(global_window_end)
            if global_window_end is not None
            else now + self._global_window_seconds
        )
        self._global_generations_in_window = 0
        self._global_active = 0
        self._sessions: dict[str, _SessionState] = {}

    # -- session management --------------------------------------------------

    def create_anonymous_quota_session(self) -> AnonymousQuotaSession:
        """Create a fresh quota session valid for ``anonymous_quota_session_ttl``."""
        now = float(self._clock.now())
        session = AnonymousQuotaSession(
            session_id=self._ids.session_id(),
            created_at=now,
            quota_window_end=now + self._ttl_seconds,
        )
        self._sessions[session.session_id] = _SessionState(session=session)
        return session

    # -- admission -----------------------------------------------------------

    def admit_generation(
        self, session_id: str, *, creator_token: str | None = None
    ) -> AdmissionDecision:
        """Check admission IN ORDER; reserve on success, raise on denial.

        ``creator_token`` is deliberately unused for quota (REQUIREMENTS 32.9).
        Order: session exists & within its window; per-session window count;
        per-session concurrency; global concurrency; global window count.
        """
        state = self._sessions.get(session_id)
        if state is None:
            raise AdmissionDenied(
                AdmissionDecision(False, "admission denied: unknown anonymous quota session")
            )
        now = float(self._clock.now())
        if now >= state.session.quota_window_end:
            raise AdmissionDenied(
                AdmissionDecision(
                    False,
                    "admission denied: anonymous quota session window expired",
                )
            )
        if state.generations_count >= self._max_session_window:
            raise AdmissionDenied(
                AdmissionDecision(
                    False,
                    "admission denied: per-session generation window exhausted",
                )
            )
        if state.active_concurrency >= self._max_session_concurrency:
            raise AdmissionDenied(
                AdmissionDecision(
                    False,
                    "admission denied: per-session concurrency limit reached",
                )
            )
        if self._global_active >= self._max_global_concurrency:
            raise AdmissionDenied(
                AdmissionDecision(
                    False,
                    "admission denied: global concurrency limit reached",
                )
            )
        # PD-SEC-02 global quota-window fix: when the current time has reached
        # the window end, START A NEW WINDOW and RESET the rolled counters
        # atomically — then evaluate the FRESH window below. This is a single
        # deterministic step: the ``DurableAdmissionController`` wrapper runs
        # every ``admit_generation`` under one RLock (FastAPI serves sync
        # handlers from a thread pool), and the base controller is
        # single-threaded under the ``ManualClock`` used by tests. Never
        # reject-forever after an expiry.
        if now >= self._global_window_end:
            self._global_window_end = now + self._global_window_seconds
            self._global_generations_in_window = 0
        if self._global_generations_in_window >= self._max_global_window:
            raise AdmissionDenied(
                AdmissionDecision(
                    False,
                    "admission denied: global generation window exhausted",
                )
            )
        # Reserve: session count++, global window count++, both concurrency++.
        state.generations_count += 1
        state.active_concurrency += 1
        self._global_generations_in_window += 1
        self._global_active += 1
        return AdmissionDecision(True)

    def release_generation(self, session_id: str) -> None:
        """Decrement active concurrency (session + global) for a finished attempt.

        Idempotent-safe: unknown sessions and empty counters are no-ops.
        """
        state = self._sessions.get(session_id)
        if state is None:
            return
        if state.active_concurrency > 0:
            state.active_concurrency -= 1
        if self._global_active > 0:
            self._global_active -= 1

    # -- inspection (tests) --------------------------------------------------

    def session_generations(self, session_id: str) -> int:
        """Return the reserved generation count of a session (0 when unknown)."""
        state = self._sessions.get(session_id)
        return state.generations_count if state is not None else 0

    def active_concurrency(self, session_id: str) -> int:
        """Return the active concurrency of a session (0 when unknown)."""
        state = self._sessions.get(session_id)
        return state.active_concurrency if state is not None else 0

    @property
    def global_active_concurrency(self) -> int:
        return self._global_active

    @property
    def global_window_end(self) -> float:
        """Current rolling global window end (PD-SEC-02; rolls forward)."""
        return self._global_window_end

    @property
    def global_window_generations(self) -> int:
        return self._global_generations_in_window
