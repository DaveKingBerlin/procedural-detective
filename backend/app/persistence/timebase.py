"""Shared wall-clock time base (UTC epoch seconds, float).

The persistence schema stores every timestamp as UTC epoch seconds (float).
The generation lifecycle (Phase 4) reads time exclusively through a ``Clock``
object; the store-facing services use ``EpochClock`` so the DB rows and the
admission controller agree on the SAME epoch time base (a monotonic clock
would make persisted ``quota_window_end`` values incomparable with epoch
based expiry checks in the auth layer after a restart).
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """The only time source the persistence-facing services may read."""

    def now(self) -> float: ...


class EpochClock:
    """Wall-clock epoch seconds (UTC). Can never be confused with monotonic."""

    def now(self) -> float:
        return time.time()