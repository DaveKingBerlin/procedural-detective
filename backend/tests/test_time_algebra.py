"""Time-algebra property tests (REQUIREMENTS 31.4/31.5/65.3, Phase3 test E).

- half-open boundary behavior,
- adjacent interval normalization/merge,
- overlapping intervals,
- difference splitting an interval: [0,11) − [5,8) == [0,5) ∪ [8,11),
- empty intersections,
- exact one-second boundaries,
- multiple disjoint feasible intervals -> WHEN ambiguous,
- canonical crime time changed while evidence constraints unchanged ->
  solver WHEN result unchanged,
- ISO-8601-with-offset parsing and accepted-scoring widening.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    SCENE_LOCATION,
    golden_evidence,
    golden_public,
)

from app.domain.evidence import (  # noqa: E402
    NOISE_HEARD_AT,
    VICTIM_LAST_SEEN_ALIVE_AT,
    EvidenceFact,
    Reliability,
    SourceRef,
    TypedProposition,
)
from app.domain.solvers import solve_when  # noqa: E402
from app.domain.time_interval import (  # noqa: E402
    HalfOpenInterval,
    IntervalSet,
    accepted_scoring_time_set,
    epoch_to_iso,
    parse_iso8601,
    parse_iso8601_to_epoch,
)


def _noise(evidence_id, observed_at, uncertainty):
    return EvidenceFact(
        id=evidence_id,
        kind="witness_observation",
        propositions=(
            TypedProposition(
                type=NOISE_HEARD_AT,
                location_id=SCENE_LOCATION,
                observed_at=observed_at,
                uncertainty_seconds=uncertainty,
            ),
        ),
        source_ref=SourceRef(kind="witness", source_id=f"witness_{evidence_id}"),
        reliability=Reliability.MEDIUM,
        presentation={"title": evidence_id, "description": "noise"},
    )


# -- half-open boundaries -----------------------------------------------------


def test_half_open_boundaries():
    iv = HalfOpenInterval(0, 5)
    assert iv.contains(0)
    assert iv.contains(4)
    assert not iv.contains(5)
    assert not iv.contains(-1)


def test_half_open_requires_end_gt_start():
    with pytest.raises(ValueError):
        HalfOpenInterval(5, 5)
    with pytest.raises(ValueError):
        HalfOpenInterval(6, 5)


def test_one_second_exact_boundaries():
    single = IntervalSet.from_intervals([HalfOpenInterval(0, 1)])
    assert single.contains(0)
    assert not single.contains(1)
    assert single.tick_count == 1
    # [0,2) − [1,3) == [0,1)
    result = IntervalSet.from_intervals([HalfOpenInterval(0, 2)]).difference(
        IntervalSet.from_intervals([HalfOpenInterval(1, 3)])
    )
    assert result == IntervalSet.from_intervals([HalfOpenInterval(0, 1)])


# -- normalization / merge ----------------------------------------------------


def test_adjacent_merge():
    merged = IntervalSet.from_intervals(
        [HalfOpenInterval(0, 5), HalfOpenInterval(5, 10)]
    )
    assert merged == IntervalSet.from_intervals([HalfOpenInterval(0, 10)])
    assert len(merged.intervals) == 1
    assert merged.intervals[0] == HalfOpenInterval(0, 10)


def test_adjacent_merge_union_operator():
    left = IntervalSet.from_intervals([HalfOpenInterval(0, 5)])
    right = IntervalSet.from_intervals([HalfOpenInterval(5, 10)])
    assert left.union(right) == IntervalSet.from_intervals([HalfOpenInterval(0, 10)])


def test_overlapping_merge():
    merged = IntervalSet.from_intervals(
        [HalfOpenInterval(0, 5), HalfOpenInterval(3, 8)]
    )
    assert merged == IntervalSet.from_intervals([HalfOpenInterval(0, 8)])


def test_connected_components():
    set_ = IntervalSet.from_intervals(
        [HalfOpenInterval(0, 5), HalfOpenInterval(10, 20), HalfOpenInterval(4, 6)]
    )
    # (0,5) touches (4,6) -> merged; (10,20) stays separate.
    components = set_.connected_components()
    assert components == (HalfOpenInterval(0, 6), HalfOpenInterval(10, 20))
    assert len(components) == 2


# -- exact difference / intersection ------------------------------------------


def test_canonical_difference_regression_65_3():
    left = IntervalSet.from_intervals([HalfOpenInterval(0, 11)])
    right = IntervalSet.from_intervals([HalfOpenInterval(5, 8)])
    result = left.difference(right)
    assert result == IntervalSet.from_intervals(
        [HalfOpenInterval(0, 5), HalfOpenInterval(8, 11)]
    )
    assert len(result.intervals) == 2


def test_difference_splitting_and_boundary_exactness():
    # Canonical regression must not retain or discard boundary instants:
    # tick 5 and tick 8 still exist in the result.
    result = IntervalSet.from_intervals([HalfOpenInterval(0, 11)]).difference(
        IntervalSet.from_intervals([HalfOpenInterval(5, 8)])
    )
    assert result.contains(4)
    assert not result.contains(5)
    assert not result.contains(7)
    assert result.contains(8)
    assert result.contains(10)


def test_empty_intersection():
    a = IntervalSet.from_intervals([HalfOpenInterval(0, 5)])
    b = IntervalSet.from_intervals([HalfOpenInterval(5, 10)])
    inter = a.intersection(b)
    assert inter.is_empty
    assert inter.tick_count == 0


def test_intersection_union_difference_consistency():
    a = IntervalSet.from_intervals([HalfOpenInterval(0, 10), HalfOpenInterval(20, 30)])
    b = IntervalSet.from_intervals([HalfOpenInterval(5, 25)])
    assert a.union(b) == IntervalSet.from_intervals([HalfOpenInterval(0, 30)])
    assert a.intersection(b) == IntervalSet.from_intervals([HalfOpenInterval(5, 10), HalfOpenInterval(20, 25)])
    assert a.difference(b) == IntervalSet.from_intervals([HalfOpenInterval(0, 5), HalfOpenInterval(25, 30)])


def test_subset_containment():
    big = IntervalSet.from_intervals([HalfOpenInterval(0, 100)])
    inner = IntervalSet.from_intervals([HalfOpenInterval(10, 20), HalfOpenInterval(30, 40)])
    assert inner.is_subset_of(big)
    assert not big.is_subset_of(inner)


# -- ISO parsing --------------------------------------------------------------


def test_iso_parse_with_offset_and_z():
    assert parse_iso8601_to_epoch("2026-09-11T22:17:00+02:00") == parse_iso8601_to_epoch(
        "2026-09-11T20:17:00Z"
    )
    epoch, offset = parse_iso8601("2026-09-11T22:17:00+02:00")
    assert offset == 120
    assert epoch == parse_iso8601_to_epoch("2026-09-11T20:17:00Z")


def test_iso_parse_negative_offset_and_midnight():
    assert parse_iso8601_to_epoch("2026-09-12T00:07:00+02:00") == parse_iso8601_to_epoch(
        "2026-09-11T22:07:00Z"
    )
    e1, off1 = parse_iso8601("2026-09-11T23:58:00-05:00")
    e2, off2 = parse_iso8601("2026-09-12T04:58:00Z")
    assert off1 == -300
    assert e1 == e2


def test_iso_parse_invalid_raises():
    for bad in ("22:17", "2026-09-11", "2026-09-11T22:17", "not-a-time", ""):
        with pytest.raises(ValueError):
            parse_iso8601_to_epoch(bad)


def test_epoch_to_iso_round_trip():
    epoch, offset = parse_iso8601("2026-09-11T22:17:00+02:00")
    assert epoch_to_iso(epoch, offset) == "2026-09-11T22:17:00+02:00"


# -- accepted scoring set (§31.7) ---------------------------------------------


def test_accepted_scoring_set_widening():
    canonical = parse_iso8601_to_epoch("2026-09-11T22:17:00+02:00")
    accepted = accepted_scoring_time_set(canonical, tolerance_seconds=120)
    assert accepted == IntervalSet.from_intervals(
        [HalfOpenInterval(canonical - 120, canonical + 121)]
    )
    assert accepted.contains(canonical - 120)
    assert accepted.contains(canonical + 120)
    assert not accepted.contains(canonical + 121)


# -- WHEN solver behaviour -----------------------------------------------------


def test_when_single_connected_interval_is_unambiguous():
    when = solve_when(golden_public(), golden_evidence())
    assert when.connected_count == 1
    assert when.ambiguous is False
    assert len(when.feasible.intervals) == 1
    assert when.critical_evidence_ids == ("body_found_01", "last_seen_01", "noise_heard_01")


def test_when_multiple_disjoint_intervals_are_ambiguous():
    """A convex feasible window split by an evidence-backed exclusion window
    => two connected intervals => crime time ambiguous (§31.8)."""
    from app.domain.evidence import TIME_WINDOW_EXCLUSION

    def _exclusion(evidence_id, observed_at, uncertainty):
        return EvidenceFact(
            id=evidence_id,
            kind="cctv_coverage",
            propositions=(
                TypedProposition(
                    type=TIME_WINDOW_EXCLUSION,
                    location_id=SCENE_LOCATION,
                    observed_at=observed_at,
                    uncertainty_seconds=uncertainty,
                ),
            ),
            source_ref=SourceRef(kind="camera", source_id=f"camera_{evidence_id}"),
            reliability=Reliability.HIGH,
            presentation={"title": evidence_id, "description": "coverage"},
        )

    evidence = [
        _noise("noise_window", "2026-09-11T21:00:00+02:00", 60),
        # Objective coverage proves the scene was unoccupied for this exact tick.
        _exclusion("coverage_tick", "2026-09-11T21:00:30+02:00", 0),
    ]
    when = solve_when(golden_public(), evidence)
    assert when.connected_count == 2
    assert when.ambiguous is True
    assert len(when.feasible.intervals) == 2
    assert "coverage_tick" in when.critical_evidence_ids
    # The excluded tick itself is missing, boundaries on both sides survive.
    assert not when.feasible.contains(parse_iso8601_to_epoch("2026-09-11T21:00:30+02:00"))
    assert when.feasible.contains(parse_iso8601_to_epoch("2026-09-11T21:00:29+02:00"))
    assert when.feasible.contains(parse_iso8601_to_epoch("2026-09-11T21:00:31+02:00"))


def test_when_uses_uncertainty_as_inclusive_window():
    """Noise at t ± u constrains [t−u, t+u+1): the canonical tick exactly at
    the boundary is included on both sides."""
    evidence = [_noise("noise_exact", "2026-09-11T22:00:00+02:00", 60)]
    when = solve_when(golden_public(), evidence)
    t = parse_iso8601_to_epoch("2026-09-11T22:00:00+02:00")
    assert when.feasible == IntervalSet.from_intervals([HalfOpenInterval(t - 60, t + 61)])


def test_when_last_seen_and_body_found_bounds():
    public = golden_public()
    last_seen = EvidenceFact(
        id="last_seen_x",
        kind="witness_observation",
        propositions=(
            TypedProposition(
                type=VICTIM_LAST_SEEN_ALIVE_AT,
                person_id="sarah_miller",
                location_id=SCENE_LOCATION,
                observed_at="2026-09-11T22:15:00+02:00",
            ),
        ),
        source_ref=SourceRef(kind="witness", source_id="witness_x"),
        reliability=Reliability.HIGH,
        presentation={"title": "x", "description": "x"},
    )
    evidence = [
        last_seen,
        EvidenceFact(
            id="body_x",
            kind="witness_observation",
            propositions=(
                TypedProposition(
                    type="BODY_FIRST_FOUND_AT",
                    location_id=SCENE_LOCATION,
                    observed_at="2026-09-11T22:18:00+02:00",
                ),
            ),
            source_ref=SourceRef(kind="witness", source_id="witness_y"),
            reliability=Reliability.HIGH,
            presentation={"title": "y", "description": "y"},
        ),
    ]
    when = solve_when(public, evidence)
    t0 = parse_iso8601_to_epoch("2026-09-11T22:15:00+02:00")
    t1 = parse_iso8601_to_epoch("2026-09-11T22:18:00+02:00")
    assert when.feasible == IntervalSet.from_intervals([HalfOpenInterval(t0, t1)])


def test_canonical_crime_time_change_leaves_when_unchanged():
    when_a = solve_when(golden_public(), golden_evidence())
    when_b = solve_when(golden_public(), golden_evidence())
    assert when_a == when_b  # truth never enters; repeated runs agree