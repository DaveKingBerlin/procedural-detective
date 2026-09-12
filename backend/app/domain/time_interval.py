"""Exact half-open integer-tick time algebra (REQUIREMENTS 31.4, 65.3).

Canonical solver time domain: discrete integer-second ticks in UTC
(``epochSecond: signed integer``). All solver time sets use half-open
intervals ``[startInclusive, endExclusive)`` so set difference, union and
intersection remain exact — no boundary instant is ever retained or discarded.

Canonical regression (65.3):
    [0,11) − [5,8) == [0,5) ∪ [8,11)

This module is pure; it never imports ``app.domain.truth``. The truth-aware
accepted-scoring reader is ``accepted_scoring_time_set`` and is used ONLY by
the validation/comparison stage (``app.validation.solution``), never by a
solver.
"""

from __future__ import annotations

import calendar
import re
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Tuple

# Bounded working time domain (epoch seconds). Signed because REQUIREMENTS
# allows signed integers. The domain is intentionally very wide (≈ ±2^62 ≈
# hundreds of thousands of years around the epoch) so that any real-world
# timestamp (including far-future/far-past case dates) always lands inside the
# domain. Out-of-domain ticks are rejected at evidence-validation time with a
# clean error (DEF-026 / DEF-034) rather than causing inconsistent
# HalfOpenInterval behaviour mid-solve.
SOLVER_TIME_MIN = -(2**62)
SOLVER_TIME_MAX = 2**62


@dataclass(frozen=True)
class HalfOpenInterval:
    """One half-open interval ``[start_inclusive, end_exclusive)`` of ticks."""

    start_inclusive: int
    end_exclusive: int

    def __post_init__(self) -> None:
        if not isinstance(self.start_inclusive, int) or isinstance(self.start_inclusive, bool):
            raise ValueError("start_inclusive must be an int")
        if not isinstance(self.end_exclusive, int) or isinstance(self.end_exclusive, bool):
            raise ValueError("end_exclusive must be an int")
        if not self.end_exclusive > self.start_inclusive:
            raise ValueError(
                "HalfOpenInterval requires end_exclusive > start_inclusive "
                f"(got [{self.start_inclusive}, {self.end_exclusive}))"
            )

    def contains(self, tick: int) -> bool:
        return self.start_inclusive <= tick < self.end_exclusive

    @property
    def duration(self) -> int:
        return self.end_exclusive - self.start_inclusive

    def as_ticks(self) -> Tuple[int, int]:
        return (self.start_inclusive, self.end_exclusive)


def _normalize_intervals(
    intervals: Iterable[HalfOpenInterval],
) -> Tuple[HalfOpenInterval, ...]:
    """Sort, drop empties, merge overlapping and directly-adjacent intervals.

    Half-open adjacency: ``[a, b)`` directly touches ``[b, c)`` (start == end)
    and must merge into ``[a, c)``.
    """
    kept = [
        iv for iv in intervals if iv.end_exclusive > iv.start_inclusive
    ]
    kept.sort(key=lambda iv: (iv.start_inclusive, iv.end_exclusive))
    merged: list[HalfOpenInterval] = []
    for iv in kept:
        if not merged or iv.start_inclusive > merged[-1].end_exclusive:
            merged.append(iv)
        else:
            last = merged[-1]
            if iv.end_exclusive > last.end_exclusive:
                merged[-1] = HalfOpenInterval(last.start_inclusive, iv.end_exclusive)
    return tuple(merged)


@dataclass(frozen=True, init=False)
class IntervalSet:
    """Immutable, normalized, sorted, disjoint set of half-open intervals."""

    _intervals: Tuple[HalfOpenInterval, ...]

    def __init__(self, *intervals: HalfOpenInterval) -> None:
        object.__setattr__(self, "_intervals", _normalize_intervals(intervals))

    # -- constructors --------------------------------------------------------

    @classmethod
    def empty(cls) -> "IntervalSet":
        return cls()

    @classmethod
    def full_domain(cls) -> "IntervalSet":
        return cls(HalfOpenInterval(SOLVER_TIME_MIN, SOLVER_TIME_MAX))

    @classmethod
    def from_intervals(cls, intervals: Iterable[HalfOpenInterval]) -> "IntervalSet":
        return cls(*tuple(intervals))

    # -- accessors -----------------------------------------------------------

    @property
    def intervals(self) -> Tuple[HalfOpenInterval, ...]:
        return self._intervals

    @property
    def is_empty(self) -> bool:
        return not self._intervals

    @property
    def tick_count(self) -> int:
        return sum(iv.duration for iv in self._intervals)

    def contains(self, tick: int) -> bool:
        return any(iv.contains(tick) for iv in self._intervals)

    def connected_components(self) -> Tuple[HalfOpenInterval, ...]:
        """Maximal connected components (adjacency-merged by normalization).

        A normalized set is already adjacency-merged, so each stored interval
        IS one connected component. Exactly one component is required for a
        unique time solution (§31.8); two or more disconnected components make
        the crime time ambiguous.
        """
        return self._intervals

    # -- set algebra (exact) --------------------------------------------------

    def union(self, other: "IntervalSet") -> "IntervalSet":
        return IntervalSet(*(self._intervals + other._intervals))

    def intersection(self, other: "IntervalSet") -> "IntervalSet":
        out: list[HalfOpenInterval] = []
        i = j = 0
        a, b = self._intervals, other._intervals
        while i < len(a) and j < len(b):
            lo = max(a[i].start_inclusive, b[j].start_inclusive)
            hi = min(a[i].end_exclusive, b[j].end_exclusive)
            if hi > lo:
                out.append(HalfOpenInterval(lo, hi))
            if a[i].end_exclusive < b[j].end_exclusive:
                i += 1
            else:
                j += 1
        return IntervalSet(*out)

    def difference(self, other: "IntervalSet") -> "IntervalSet":
        out: list[HalfOpenInterval] = []
        for iv in self._intervals:
            cur: HalfOpenInterval | None = iv
            for b in other._intervals:
                if b.end_exclusive <= cur.start_inclusive:
                    continue
                if b.start_inclusive >= cur.end_exclusive:
                    break
                if b.start_inclusive > cur.start_inclusive:
                    out.append(HalfOpenInterval(cur.start_inclusive, b.start_inclusive))
                if b.end_exclusive < cur.end_exclusive:
                    cur = HalfOpenInterval(b.end_exclusive, cur.end_exclusive)
                else:
                    cur = None
                    break
            if cur is not None:
                out.append(cur)
        return IntervalSet(*out)

    def is_subset_of(self, other: "IntervalSet") -> bool:
        """``self ⊆ other`` — content containment over every tick."""
        return self.difference(other).is_empty

    def __bool__(self) -> bool:
        return not self.is_empty


# ---------------------------------------------------------------------------
# ISO-8601-with-offset parsing (deterministic, handles 'Z' and ±HH:MM).
# ---------------------------------------------------------------------------

# ASCII [0-9] only: Python's "\d" would otherwise accept Arabic-Indic and
# other Unicode decimal digits, which we reject (DEF-034).
_ISO_RE = re.compile(
    r"^(?P<y>[0-9]{4})-(?P<mo>[0-9]{2})-(?P<d>[0-9]{2})"
    r"T(?P<h>[0-9]{2}):(?P<mi>[0-9]{2}):(?P<s>[0-9]{2})"
    r"(?:\.(?P<frac>[0-9]+))?"
    r"(?P<zone>Z|z|[+-][0-9]{2}:?[0-9]{2})$"
)


def parse_iso8601(timestamp: str) -> Tuple[int, int]:
    """Parse an ISO-8601-with-offset timestamp.

    Returns ``(epoch_seconds_utc, utc_offset_minutes)``. Fractional seconds,
    when present, are floored (integer-second domain per §31.4).

    Strictness (DEF-034):
    - ASCII digits only (Unicode/India-Arabic digits are rejected);
    - offset hours <= 23 and offset minutes <= 59 (``+24:00``/``+02:60`` are
      rejected);
    - ``second == 60`` (a leap second) is deterministicly clamped to 59
      (documented behaviour; the extra tick is out of the integer-second
      domain);
    - the calendar-day/time fields are validated by ``datetime`` (e.g. an
      impossible day like 2026-02-30 raises ``ValueError``).

    Raises ``ValueError`` for malformed/out-of-range input.
    """
    if not isinstance(timestamp, str):
        raise TypeError(f"timestamp must be a str, got {type(timestamp).__name__}")
    match = _ISO_RE.match(timestamp.strip())
    if not match:
        raise ValueError(f"invalid ISO-8601 timestamp: {timestamp!r}")
    y = int(match.group("y"))
    mo = int(match.group("mo"))
    d = int(match.group("d"))
    h = int(match.group("h"))
    mi = int(match.group("mi"))
    s = int(match.group("s"))
    if not 1 <= mo <= 12:
        raise ValueError(f"invalid ISO-8601 timestamp (month out of range): {timestamp!r}")
    if not 1 <= d <= 31:
        raise ValueError(f"invalid ISO-8601 timestamp (day out of range): {timestamp!r}")
    if not 0 <= h <= 23:
        raise ValueError(f"invalid ISO-8601 timestamp (hour out of range): {timestamp!r}")
    if not 0 <= mi <= 59:
        raise ValueError(f"invalid ISO-8601 timestamp (minute out of range): {timestamp!r}")
    if s > 60:
        raise ValueError(f"invalid ISO-8601 timestamp (second out of range): {timestamp!r}")
    if s == 60:
        # Leap second: clamp to 59 (integer-second tick domain).
        s = 59
    zone = match.group("zone").upper()
    if zone == "Z":
        offset_minutes = 0
    else:
        sign = 1 if zone[0] == "+" else -1
        offset_hours = int(zone[1:3])
        offset_minutes_part = int(zone[-2:])
        if offset_hours > 23:
            raise ValueError(
                f"invalid ISO-8601 timestamp (offset hour out of range): {timestamp!r}"
            )
        if offset_minutes_part > 59:
            raise ValueError(
                f"invalid ISO-8601 timestamp (offset minute out of range): {timestamp!r}"
            )
        offset_minutes = sign * (offset_hours * 60 + offset_minutes_part)
    dt = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    epoch = calendar.timegm(dt.utctimetuple()) - offset_minutes * 60
    return epoch, offset_minutes


def parse_iso8601_to_epoch(timestamp: str) -> int:
    """Epoch-seconds-UTC view (for solver arithmetic)."""
    return parse_iso8601(timestamp)[0]


def assert_epoch_in_domain(epoch_seconds: int) -> None:
    """Reject ticks outside the solver time domain with a clean ValueError.

    Called by evidence validation (DEF-026/DEF-034) so the solvers can never
    observe an out-of-domain tick (which would otherwise cause inconsistent
    HalfOpenInterval behaviour mid-solve).
    """
    if not isinstance(epoch_seconds, int) or isinstance(epoch_seconds, bool):
        raise ValueError(f"epoch tick must be an int, got {type(epoch_seconds).__name__}")
    if not SOLVER_TIME_MIN <= epoch_seconds < SOLVER_TIME_MAX:
        raise ValueError(
            f"epoch tick {epoch_seconds} is outside the solver time domain "
            f"[{SOLVER_TIME_MIN}, {SOLVER_TIME_MAX})"
        )


def iso_offset_minutes(timestamp: str) -> int:
    return parse_iso8601(timestamp)[1]


def epoch_to_iso(epoch_seconds: int, utc_offset_minutes: int = 0) -> str:
    """Render an epoch tick back into the case's timezone as ISO-8601-with-offset.

    Used only by the truth-aware validation stage to build the published
    proof's time section (§31.14) and by tests for human-readable assertions.
    """
    local = epoch_seconds + utc_offset_minutes * 60
    t = _time.gmtime(local)
    sign = "+" if utc_offset_minutes >= 0 else "-"
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}"
        f"{sign}{abs(utc_offset_minutes) // 60:02d}:{abs(utc_offset_minutes) % 60:02d}"
    )


def accepted_scoring_time_set(canonical_tick: int, tolerance_seconds: int) -> IntervalSet:
    """Truth-aware accepted scoring set (§31.7): ``[T−N, T+N+1)``.

    Given canonical ``T`` and tolerance ``N``, the inclusive human-readable
    range ``T−N through T+N`` is represented internally as
    ``[T−N, T+N+1second)``. This helper is used ONLY by the truth-aware
    comparison/scoring stage in ``app.validation.solution`` — never inside the
    WHEN/WHO/WHY/WEAPON solvers.
    """
    if not isinstance(tolerance_seconds, int) or isinstance(tolerance_seconds, bool):
        raise ValueError("tolerance_seconds must be an int")
    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds must be >= 0")
    return IntervalSet.from_intervals(
        [
            HalfOpenInterval(
                canonical_tick - tolerance_seconds,
                canonical_tick + tolerance_seconds + 1,
            )
        ]
    )