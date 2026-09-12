"""WHO solver tests (Phase3 test C, REQUIREMENTS 31.9/31.10, 65.4).

- unique evidence-backed solution (golden: exactly Thomas-like suspect remains),
- alternative left unknown -> ambiguous,
- false alibi alone does not exclude,
- impossible opportunity DOES exclude,
- hidden truth changed while public facts unchanged -> identical solver result.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    golden_evidence,
    golden_public,
)

from app.domain.evidence import (  # noqa: E402
    ALIBI_TIME_CLAIM,
    NOISE_HEARD_AT,
    PERSON_OBSERVED_AT_LOCATION,
    EvidenceFact,
    Reliability,
    SourceRef,
    TypedProposition,
)
from app.domain.proof import DIMENSION_SUSPECT  # noqa: E402
from app.domain.public import (  # noqa: E402
    PublicCase,
    PublicLocation,
    PublicPerson,
    PublicScene,
    PublicTravelRule,
)
from app.domain.rules import RuleEffect  # noqa: E402
from app.domain.solver import solve_case  # noqa: E402
from app.domain.solvers import solve_when, solve_who  # noqa: E402
from app.domain.time_interval import IntervalSet  # noqa: E402


def _observation(
    evidence_id,
    person_id,
    location_id,
    observed_at,
    uncertainty=0,
    reliability=Reliability.HIGH,
):
    return EvidenceFact(
        id=evidence_id,
        kind="cctv_observation",
        propositions=(
            TypedProposition(
                type=PERSON_OBSERVED_AT_LOCATION,
                person_id=person_id,
                location_id=location_id,
                observed_at=observed_at,
                uncertainty_seconds=uncertainty,
            ),
        ),
        source_ref=SourceRef(kind="camera", source_id=f"camera_{evidence_id}"),
        reliability=reliability,
        presentation={"title": evidence_id, "description": "observation"},
    )


def _noise(evidence_id, observed_at, uncertainty=0):
    return EvidenceFact(
        id=evidence_id,
        kind="witness_observation",
        propositions=(
            TypedProposition(
                type=NOISE_HEARD_AT,
                location_id="scene_room",
                observed_at=observed_at,
                uncertainty_seconds=uncertainty,
            ),
        ),
        source_ref=SourceRef(kind="witness", source_id=f"witness_{evidence_id}"),
        reliability=Reliability.MEDIUM,
        presentation={"title": evidence_id, "description": "noise"},
    )


def _alibi_claim(evidence_id, person_id, departure_iso):
    return EvidenceFact(
        id=evidence_id,
        kind="suspect_statement",
        propositions=(
            TypedProposition(
                type=ALIBI_TIME_CLAIM,
                person_id=person_id,
                structured={"claimedDeparture": departure_iso},
            ),
        ),
        source_ref=SourceRef(kind="statement", source_id=f"statement_{evidence_id}"),
        reliability=Reliability.LOW,
        presentation={"title": evidence_id, "description": "alibi claim"},
    )


def _mini_public(suspect_ids=("sus_1",)):
    persons = [
        PublicPerson(
            person_id="victim_1",
            name="Victim",
            role="victim",
            public_affordances=frozenset(),
            presented_data={},
        )
    ]
    persons.extend(
        PublicPerson(
            person_id=sid,
            name=f"Suspect {sid}",
            role="suspect",
            public_affordances=frozenset({"SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"}),
            presented_data={},
        )
        for sid in suspect_ids
    )
    return PublicCase(
        case_id="MINI-1",
        case_version=1,
        persons=tuple(persons),
        motives=(),
        objects=(),
        locations=(
            PublicLocation(location_id="scene_room", name="Scene Room"),
            PublicLocation(location_id="office", name="Office"),
        ),
        travel_rules=(PublicTravelRule("office", "scene_room", 600),),
        scene=PublicScene(location_id="scene_room", name="Scene Room"),
    )


def test_golden_unique_evidence_backed_solution():
    proof = solve_case(golden_public(), golden_evidence())
    who = proof.who

    assert who.dimension == DIMENSION_SUSPECT
    assert who.unique is True
    assert who.winner == "thomas_reed"
    assert who.viable == ("thomas_reed",)
    assert set(who.unknown_remaining) == set()
    assert {e.candidate_id for e in who.excluded} == {"anna_karlsson", "michael_carter"}
    for exclusion in who.excluded:
        assert all(o.effect is RuleEffect.EXCLUDE_SUSPECT for o in exclusion.rule_outcomes)
        assert all(o.necessary_for_candidate for o in exclusion.rule_outcomes)
        assert all(o.permits_elimination() for o in exclusion.rule_outcomes)


def test_alternative_left_unknown_is_ambiguous():
    evidence = [f for f in golden_evidence() if f.id != "cctv_michael_office_01"]
    proof = solve_case(golden_public(), evidence)
    who = proof.who

    assert who.unique is False
    assert who.winner is None
    assert "michael_carter" in who.viable
    assert "michael_carter" in who.unknown_remaining
    assert "thomas_reed" in who.viable


def test_false_alibi_alone_does_not_exclude():
    public = _mini_public(suspect_ids=("sus_1",))
    evidence = [
        _alibi_claim("alibi_sus_1", "sus_1", "2026-09-11T21:45:00+02:00"),
        _observation(
            "cctv_sus_scene",
            "sus_1",
            "scene_room",
            "2026-09-11T22:16:40+02:00",
            uncertainty=60,
        ),
    ]
    proof = solve_case(public, evidence)
    who = proof.who
    # False alibi => ALIBI_CREDIBILITY_DECREASE only; never exclusion.
    assert who.unique is True
    assert who.winner == "sus_1"
    assert who.excluded == ()
    alibi_outcomes = [
        o
        for o in proof.rule_outcomes_by_candidate
        if o.effect is RuleEffect.ALIBI_CREDIBILITY_DECREASE
    ]
    assert len(alibi_outcomes) == 1
    assert alibi_outcomes[0].necessary_for_candidate is False


def test_impossible_opportunity_does_exclude():
    public = _mini_public(suspect_ids=("sus_1",))
    evidence = [
        _observation("cctv_sus_office", "sus_1", "office", "2026-09-11T22:00:00+02:00"),
        # Feasible crime window [22:05:00, 22:05:01) — the suspect cannot reach
        # the scene before 22:10 (22:00 + 600s travel).
        _noise("noise_scene", "2026-09-11T22:05:00+02:00"),
    ]
    proof = solve_case(public, evidence)
    who = proof.who

    # The only suspect is evidence-backed excluded -> no survivor exists.
    assert who.unique is False
    assert who.viable == ()
    assert who.winner is None
    assert who.unknown_remaining == ()
    assert len(who.excluded) == 1
    exclusion = who.excluded[0]
    assert exclusion.candidate_id == "sus_1"
    assert exclusion.rule_outcomes[0].effect is RuleEffect.EXCLUDE_SUSPECT
    assert exclusion.rule_outcomes[0].necessary_for_candidate is True


def test_opportunity_feasible_when_arrival_fits_65_4():
    """REQUIREMENTS 65.4: feasible crime set through 22:18:30; suspect can
    arrive at 22:18:00 -> suspect REMAINS feasible."""
    public = _mini_public(suspect_ids=("sus_1",))
    evidence = [
        _observation("cctv_sus_office", "sus_1", "office", "2026-09-11T22:00:00+02:00"),
        # Noise window [22:17:00 .. 22:18:31) — arrival at 22:18:00 fits.
        _noise("noise_scene", "2026-09-11T22:17:00+02:00", uncertainty=90),
    ]
    proof = solve_case(public, evidence)
    who = proof.who
    # Earliest arrival after office at 22:00 + 600s travel = 22:10; feasible
    # [22:17:00, 22:18:31) overlaps presence [after 22:10] => viable.
    assert who.winner == "sus_1"
    assert who.excluded == ()


def test_who_uses_feasible_time_from_when():
    public = _mini_public(suspect_ids=("sus_1",))
    evidence = [
        _observation("cctv_sus_office", "sus_1", "office", "2026-09-11T22:00:00+02:00"),
        _noise("noise_scene", "2026-09-11T22:05:00+02:00"),
    ]
    when = solve_when(public, evidence)
    who = solve_who(public, evidence, when.feasible)
    assert isinstance(when.feasible, IntervalSet)
    assert who.winner is None  # excluded by impossible opportunity
    assert len(who.excluded) == 1