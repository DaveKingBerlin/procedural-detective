"""Injectable wall-clock seam (REQUIREMENTS 32.6, Phase4 K).

All generation deadlines read time exclusively through the ``Clock`` protocol
so tests can drive deterministic time (``ManualClock``) and production uses
monotonic wall time (``RealClock``). ``time.monotonic`` is used because it can
never jump backwards and is unaffected by NTP/clock adjustments.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """The only time source the generation lifecycle may read."""

    def now(self) -> float: ...


class RealClock:
    """Production clock: monotonic wall time in seconds."""

    def now(self) -> float:
        return time.monotonic()


class ManualClock:
    """Deterministic test clock; time only moves when ``advance`` is called.

    ``start_time`` defaults to ``0.0`` so every timestamp derived from a
    freshly constructed clock is byte-identical across test runs.
    """

    def __init__(self, start_time: float = 0.0) -> None:
        if isinstance(start_time, bool) or not isinstance(start_time, (int, float)):
            raise TypeError(
                f"start_time must be a number; got {type(start_time).__name__}"
            )
        self._now = float(start_time)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by ``seconds`` (must be non-negative)."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise TypeError(
                f"advance requires a numeric seconds value; got {type(seconds).__name__}"
            )
        if seconds < 0:
            raise ValueError("advance requires a non-negative seconds value")
        self._now += float(seconds)

    def __repr__(self) -> str:
        return f"ManualClock(now={self._now!r})"