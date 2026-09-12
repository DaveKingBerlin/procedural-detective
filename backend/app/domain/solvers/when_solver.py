"""WHEN solver — evidence-derived feasible crime-time interval (§31.5/31.8).

Derives ``FeasibleCrimeTimeSet`` EXCLUSIVELY from discoverable structured
evidence plus the public model. The canonical (hidden) crime time NEVER
participates: opportunity, interval membership and ambiguity are computed
without it.

Deterministic semantics for time-constraining proposition types:

- ``VICTIM_LAST_SEEN_ALIVE_AT`` at ``t``      -> lower bound ``[t, +∞)``
- ``BODY_FIRST_FOUND_AT`` at ``t``           -> upper bound ``(−∞, t)``
- ``NOISE_HEARD_AT`` / ``CRIME_SCENE_OBSERVATION_AT`` at ``t`` with
  ``uncertainty_seconds == u``               -> window ``[t−u, t+u+1)``
  (the inclusive ``t−u .. t+u`` tick range in half-open form — the same
  convention used for the accepted-scoring set in §31.7, so a zero-uncertainty
  observation constrains the crime to the single tick ``t``).
- ``TIME_WINDOW_EXCLUSION`` at ``t`` with ``uncertainty_seconds == u``
  -> the window ``[t−u, t+u+1)`` is EXCLUDED from the feasible set (objective
  coverage showing the scene was unoccupied / no relevant activity during the
  window). This is the one constraint that can split the feasible set into two
  or more disjoint connected intervals (the §31.8 ambiguity case).

Crime time is unique only when the normalized feasible set is exactly one
connected interval; two or more disjoint connected intervals make it ambiguous
(§31.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.domain.evidence import (
    BODY_FIRST_FOUND_AT,
    CRIME_SCENE_OBSERVATION_AT,
    EvidenceFact,
    NOISE_HEARD_AT,
    TIME_WINDOW_EXCLUSION,
    TypedProposition,
    VICTIM_LAST_SEEN_ALIVE_AT,
    discoverable_facts,
)
from app.domain.inputs import ensure_solver_input
from app.domain.public import PublicCase
from app.domain.time_interval import (
    SOLVER_TIME_MAX,
    SOLVER_TIME_MIN,
    HalfOpenInterval,
    IntervalSet,
    epoch_to_iso,
    parse_iso8601_to_epoch,
)


def _constraint_window(proposition: TypedProposition) -> IntervalSet | None:
    """Map a time-constraining proposition to its feasible-window constraint."""
    ptype = proposition.type
    if ptype not in (
        VICTIM_LAST_SEEN_ALIVE_AT,
        BODY_FIRST_FOUND_AT,
        NOISE_HEARD_AT,
        CRIME_SCENE_OBSERVATION_AT,
    ):
        return None
    tick = parse_iso8601_to_epoch(proposition.observed_at)
    if ptype == VICTIM_LAST_SEEN_ALIVE_AT:
        return IntervalSet.from_intervals([HalfOpenInterval(tick, SOLVER_TIME_MAX)])
    if ptype == BODY_FIRST_FOUND_AT:
        return IntervalSet.from_intervals([HalfOpenInterval(SOLVER_TIME_MIN, tick)])
    # NOISE / CRIME_SCENE_OBSERVATION share the same inclusive ±u window.
    u = proposition.uncertainty_seconds
    return IntervalSet.from_intervals([HalfOpenInterval(tick - u, tick + u + 1)])


def _exclusion_window(proposition: TypedProposition) -> IntervalSet | None:
    """Windows that are FORBIDDEN for the crime time (difference constraint)."""
    if proposition.type != TIME_WINDOW_EXCLUSION:
        return None
    u = proposition.uncertainty_seconds
    tick = parse_iso8601_to_epoch(proposition.observed_at)
    return IntervalSet.from_intervals([HalfOpenInterval(tick - u, tick + u + 1)])


@dataclass(frozen=True)
class WhenResult:
    """Feasible crime-time set + uniqueness/ambiguity summary (§31.5/31.8).

    ``overconstrained`` is True when the evidence constraints contradict each
    other so hard that no tick survives (``feasible`` is empty). This is a
    DISTINCT state from ambiguity (DEF-027): ``ambiguous`` means >= 2 disjoint
    connected intervals in a NON-EMPTY feasible set; an empty set is
    overconstrained, never ambiguous.
    """

    feasible: IntervalSet
    connected_count: int
    ambiguous: bool
    critical_evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    overconstrained: bool = False

    def to_dict(self, utc_offset_minutes: int = 0) -> dict[str, Any]:
        return {
            "connectedCount": self.connected_count,
            "ambiguous": self.ambiguous,
            "overconstrained": self.overconstrained,
            "criticalEvidenceIds": list(self.critical_evidence_ids),
            "feasibleIntervals": [
                {
                    "startInclusive": epoch_to_iso(iv.start_inclusive, utc_offset_minutes),
                    "endExclusive": epoch_to_iso(iv.end_exclusive, utc_offset_minutes),
                }
                for iv in self.feasible.intervals
            ],
            "feasibleTicks": [[iv.start_inclusive, iv.end_exclusive] for iv in self.feasible.intervals],
        }


def solve_when(public: PublicCase, evidence: Iterable[EvidenceFact]) -> WhenResult:
    """Compute the evidence-derived feasible crime-time set.

    Rejects non-PublicCase/non-evidence input and evidence with unresolvable
    references (raising ``TypeError``/``ValueError``). Never reads hidden truth.
    """
    evidence = tuple(evidence)
    ensure_solver_input(public, evidence)

    critical_evidence_ids: set[str] = set()
    feasible: IntervalSet = IntervalSet.full_domain()
    exclusions: list[IntervalSet] = []
    for fact in sorted(discoverable_facts(evidence), key=lambda f: f.id):
        for proposition in fact.propositions:
            exclusion = _exclusion_window(proposition)
            if exclusion is not None:
                # TIME_WINDOW_EXCLUSION forbids a window (difference, applied
                # after the intersection pass below).
                exclusions.append(exclusion)
                critical_evidence_ids.add(fact.id)
                continue
            window = _constraint_window(proposition)
            if window is None:
                continue
            feasible = feasible.intersection(window)
            critical_evidence_ids.add(fact.id)

    for exclusion in exclusions:
        feasible = feasible.difference(exclusion)

    overconstrained = feasible.is_empty
    components = feasible.connected_components()
    ambiguous = (not overconstrained) and len(components) != 1
    return WhenResult(
        feasible=feasible,
        connected_count=len(components),
        ambiguous=ambiguous,
        critical_evidence_ids=tuple(sorted(critical_evidence_ids)),
        overconstrained=overconstrained,
    )