"""Per-attempt resource budgets (REQUIREMENTS 32.5/32.6/32.7, Phase4 C).

One ``BudgetTracker`` belongs to exactly one ``generationAttemptId``: the
initial generation, retries, repairs and regenerations all draw on the same
deadline, model-call count, repair-pass count and regeneration count. A new
attempt always gets a fresh tracker, so recovery counters can never leak
across attempts (REQUIREMENTS 32.5: "All recovery counters belong to one
generationAttemptId").
"""

from __future__ import annotations

from typing import Any

from app.generation.clock import Clock


class BudgetTracker:
    """Deadline + model-call + repair/regeneration budgets for one attempt."""

    def __init__(
        self,
        clock: Clock,
        *,
        deadline_seconds: int,
        max_calls: int,
        max_repairs: int,
        max_regenerations: int,
        started_at: float | None = None,
    ) -> None:
        if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, int):
            raise TypeError("deadline_seconds must be an int")
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be > 0")
        if isinstance(max_calls, bool) or not isinstance(max_calls, int):
            raise TypeError("max_calls must be an int")
        if max_calls <= 0:
            raise ValueError("max_calls must be > 0")
        if isinstance(max_repairs, bool) or not isinstance(max_repairs, int):
            raise TypeError("max_repairs must be an int")
        if max_repairs < 0:
            raise ValueError("max_repairs must be >= 0")
        if isinstance(max_regenerations, bool) or not isinstance(max_regenerations, int):
            raise TypeError("max_regenerations must be an int")
        if max_regenerations < 0:
            raise ValueError("max_regenerations must be >= 0")
        self._clock = clock
        self._deadline_seconds = int(deadline_seconds)
        self._max_calls = int(max_calls)
        self._max_repairs = int(max_repairs)
        self._max_regenerations = int(max_regenerations)
        self._started_at = (
            float(started_at) if started_at is not None else float(clock.now())
        )
        # Public counters (readable for tests/inspection).
        self.calls = 0
        self.repair_passes = 0
        self.regenerations = 0

    # -- accessors -----------------------------------------------------------

    @property
    def started_at(self) -> float:
        return self._started_at

    @property
    def deadline_seconds(self) -> int:
        return self._deadline_seconds

    # -- budget checks -------------------------------------------------------

    def deadline_passed(self) -> bool:
        """True when ``clock.now() >= started_at + deadline_seconds``."""
        return self._clock.now() >= self._started_at + self._deadline_seconds

    def consume_call(self) -> bool:
        """Reserve one model call.

        Returns False (with NO reservation) when the call budget is already
        exhausted OR the deadline has passed — the caller must treat False as
        a terminal condition for the attempt.
        """
        if self.deadline_passed():
            return False
        if self.calls >= self._max_calls:
            return False
        self.calls += 1
        return True

    def consume_repair_pass(self) -> bool:
        """Reserve one repair pass; False when exhausted (with no reservation)."""
        if self.repair_passes >= self._max_repairs:
            return False
        self.repair_passes += 1
        return True

    def consume_regeneration(self) -> bool:
        """Reserve one full regeneration; False when exhausted (no reservation)."""
        if self.regenerations >= self._max_regenerations:
            return False
        self.regenerations += 1
        return True

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"BudgetTracker(calls={self.calls}/{self._max_calls}, "
            f"repairs={self.repair_passes}/{self._max_repairs}, "
            f"regenerations={self.regenerations}/{self._max_regenerations})"
        )


def is_budget_tracker(value: Any) -> bool:  # pragma: no cover - isinstance helper
    return isinstance(value, BudgetTracker)