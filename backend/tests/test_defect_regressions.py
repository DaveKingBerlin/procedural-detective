"""Phase 3 defect regression tests (DEF-025 .. DEF-034).

Each test documents the original defect (reproduced in the QA report) and
locks the fixed behaviour. See the corresponding DEF entry in the task order.
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
    truth_variant_a,
)

from app.domain.evidence import (  # noqa: E402
    ALIBI_TIME_CLAIM,
    FORENSIC_WEAPON_MATCH,
    NOISE_HEARD_AT,
    PERSON_OBSERVED_AT_LOCATION,
    TIME_WINDOW_EXCLUSION,
    VICTIM_LAST_SEEN_ALIVE_AT,
    EvidenceFact,
    FrozenDict,
    Reliability,
    SourceRef,
    TypedProposition,
)
from app.domain.public import (  # noqa: E402
    PublicCase,
    PublicLocation,
    PublicMotive,
    PublicObject,
    PublicPerson,
    PublicScene,
    PublicTravelRule,
)
from app.domain.rules import RuleEffect, RuleOutcome, PropositionStatus  # noqa: E402
from app.domain.solver import solve_case  # noqa: E402
from app.domain.solvers import solve_weapon, solve_when, solve_who, solve_why  # noqa: E402
from app.domain.time_interval import (  # noqa: E402
    SOLVER_TIME_MAX,
    SOLVER_TIME_MIN,
    HalfOpenInterval,
    assert_epoch_in_domain,
    parse_iso8601,
    parse_iso8601_to_epoch,
)


# ---------------------------------------------------------------------------
# local builders
# ---------------------------------------------------------------------------

def _person(person_id: str, role: str = "suspect", eligible: bool = True):
    return PublicPerson(
        person_id=person_id,
        name=f"Person {person_id}",
        role=role,
        public_affordances=frozenset(
            {"SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"} if eligible else {"VISIBLE_CHARACTER"}
        ),
        presented_data={},
    )


def _mini_public(
    suspect_ids=("sus_1",),
    office_travel: int | None = 600,
    travel_office_scene: int | None = 600,
):
    travel_rules = []
    if travel_office_scene is not None:
        travel_rules.append(PublicTravelRule("office", SCENE_LOCATION, travel_office_scene))
    return PublicCase(
        case_id="MINI-DEF",
        case_version=1,
        persons=tuple(
            [_person("sarah_miller", role="victim", eligible=False)] + [_person(s) for s in suspect_ids]
        ),
        motives=(),
        objects=(),
        locations=(
            PublicLocation(location_id=SCENE_LOCATION, name="Kitchen"),
            PublicLocation(location_id="office", name="Office"),
        ),
        travel_rules=tuple(travel_rules),
        scene=PublicScene(location_id=SCENE_LOCATION, name="Kitchen"),
    )


def _observation(fact_id, person_id, location_id, observed_at, uncertainty=0):
    return EvidenceFact(
        id=fact_id,
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
        source_ref=SourceRef(kind="camera", source_id=f"camera_{fact_id}"),
        reliability=Reliability.HIGH,
        presentation={"title": fact_id, "description": "observation"},
    )


def _noise(fact_id, observed_at, uncertainty=0):
    return EvidenceFact(
        id=fact_id,
        kind="witness_observation",
        propositions=(
            TypedProposition(
                type=NOISE_HEARD_AT,
                location_id=SCENE_LOCATION,
                observed_at=observed_at,
                uncertainty_seconds=uncertainty,
            ),
        ),
        source_ref=SourceRef(kind="witness", source_id=f"witness_{fact_id}"),
        reliability=Reliability.MEDIUM,
        presentation={"title": fact_id, "description": "noise"},
    )


def _last_seen(fact_id, observed_at):
    return EvidenceFact(
        id=fact_id,
        kind="witness_observation",
        propositions=(
            TypedProposition(
                type=VICTIM_LAST_SEEN_ALIVE_AT,
                person_id="sarah_miller",
                location_id=SCENE_LOCATION,
                observed_at=observed_at,
            ),
        ),
        source_ref=SourceRef(kind="witness", source_id=f"witness_{fact_id}"),
        reliability=Reliability.HIGH,
        presentation={"title": fact_id, "description": "last seen"},
    )


def _exclusion_window(fact_id, observed_at, uncertainty):
    return EvidenceFact(
        id=fact_id,
        kind="cctv_coverage",
        propositions=(
            TypedProposition(
                type=TIME_WINDOW_EXCLUSION,
                location_id=SCENE_LOCATION,
                observed_at=observed_at,
                uncertainty_seconds=uncertainty,
            ),
        ),
        source_ref=SourceRef(kind="camera", source_id=f"camera_{fact_id}"),
        reliability=Reliability.HIGH,
        presentation={"title": fact_id, "description": "coverage"},
    )


# ===========================================================================
# DEF-025 — degenerate opportunity block must not crash solve_case
# ===========================================================================

def test_def025_zero_travel_zero_uncertainty_does_not_crash():
    """Original defect (ADV-112): travel=0 + u=0 produced HalfOpenInterval
    [t, t) -> ValueError from who_solver._opportunity_block."""
    public = _mini_public(travel_office_scene=0)
    evidence = [
        _observation("obs_office", "sus_1", "office", "2026-09-11T22:00:00+02:00", uncertainty=0),
        _noise("noise_scene", "2026-09-11T22:05:00+02:00"),
    ]
    proof = solve_case(public, evidence)  # must not raise
    who = proof.who
    assert who.excluded == ()       # degenerate block is unconstrained
    assert "sus_1" in who.viable    # not excluded
    assert who.unique is False      # viable but no support evidence -> unknown
    assert who.winner is None
    opp = [
        o
        for o in proof.rule_outcomes_by_candidate
        if o.target_candidate_id == "sus_1"
        and o.proposition_type == "CAN_REACH_CRIME_SCENE_IN_TIME"
    ]
    assert opp[0].status is PropositionStatus.UNKNOWN
    assert opp[0].effect is RuleEffect.NO_EFFECT
    assert not any(o.is_support() for o in opp)   # no support claim


def test_def025_real_travel_block_still_works():
    public = _mini_public(travel_office_scene=3)
    evidence = [
        _observation("obs_office", "sus_1", "office", "2026-09-11T22:00:00+02:00", uncertainty=0),
        # Feasible crime [22:00:02,22:00:03) — earliest arrival 22:00:03.
        _noise("noise_scene", "2026-09-11T22:00:02+02:00"),
    ]
    proof = solve_case(public, evidence)
    assert len(proof.who.excluded) == 1
    assert proof.who.excluded[0].candidate_id == "sus_1"


# ===========================================================================
# DEF-026 — no int32 time-domain cliff
# ===========================================================================

def test_def026_domain_bounds_widened_to_2_62():
    assert SOLVER_TIME_MIN == -(2**62)
    assert SOLVER_TIME_MAX == 2**62


@pytest.mark.parametrize(
    "timestamp",
    [
        "2038-01-19T03:14:07Z",  # tick == 2^31 - 1 (old cliff boundary)
        "2088-01-01T00:00:00+02:00",
        "2099-12-31T23:59:59Z",
        "2070-01-01T00:00:00Z",
        "1970-01-01T00:00:00Z",
        "1960-01-01T00:00:00Z",
    ],
)
def test_def026_future_and_past_timestamps_solve_cleanly(timestamp):
    """Original defect (ADV-113): ticks >= 2^31 raised (LAST_SEEN), silently
    emptied (NOISE) or no-opped (BODY_FOUND) inconsistently. After widening the
    domain all of these must solve without any HalfOpenInterval mid-solve
    error."""
    evidence = [_last_seen("last_iso", timestamp)]
    when = solve_when(golden_public(), evidence)
    assert when.feasible.is_empty is False
    assert when.connected_count == 1
    assert not when.overconstrained
    # Full solve_case path too (validation consumes every time stamp).
    proof = solve_case(golden_public(), evidence)
    assert proof.when.feasible.contains(parse_iso8601_to_epoch(timestamp))


def test_def026_out_of_domain_tick_rejected_cleanly():
    # Parsable strings can never exceed ±2^62 (4-digit years only), so the
    # domain guard is defense-in-depth; verify it directly and via validation.
    assert_epoch_in_domain(0)
    assert_epoch_in_domain(2**60)
    with pytest.raises(ValueError):
        assert_epoch_in_domain(2**70)
    with pytest.raises(ValueError):
        assert_epoch_in_domain(-(2**63))

    # Time-bearing facts inside the domain pass validation.
    public = golden_public()
    from app.domain.evidence import validate_evidence

    assert validate_evidence(public, [_last_seen("ok_ts", "2088-01-01T00:00:00Z")]) == ()


# ===========================================================================
# DEF-027 — empty feasible set is overconstrained, not ambiguous
# ===========================================================================

def _overconstrained_scenario():
    public = golden_public()
    evidence = [
        _noise("noise_w", "2026-09-11T21:00:00+02:00", 60),  # [20:59:00, 21:02:01)
        _exclusion_window("cover_w", "2026-09-11T21:00:00+02:00", 61),  # removes it all
    ]
    return public, evidence


def test_def027_when_result_reports_overconstrained_not_ambiguous():
    """Original defect: empty feasible -> ambiguous=True mislabel; WHO excluded
    everyone with empty evidence_ids; empty set vacuously passed feasible
    ⊆ scoring."""
    public, evidence = _overconstrained_scenario()
    when = solve_when(public, evidence)
    assert when.feasible.is_empty
    assert when.overconstrained is True
    assert when.ambiguous is False
    assert when.connected_count == 0
    d = when.to_dict()
    assert d["overconstrained"] is True and d["ambiguous"] is False

    proof = solve_case(public, evidence)
    assert proof.when.overconstrained is True

    # WHO must not fabricate exclusions on an empty feasible set.
    assert proof.who.excluded == ()
    assert proof.who.unique is False
    assert proof.who.winner is None
    for outcome in proof.rule_outcomes_by_candidate:
        assert not (
            outcome.evidence_ids == ()
            and outcome.necessary_for_candidate
            and outcome.effect in (
                RuleEffect.EXCLUDE_SUSPECT,
                RuleEffect.EXCLUDE_MOTIVE,
                RuleEffect.EXCLUDE_WEAPON,
            )
        ), f"exclusion without evidence on empty feasible: {outcome}"
    opp = [
        o
        for o in proof.rule_outcomes_by_candidate
        if o.target_candidate_id in proof.who.universe
        and o.proposition_type == "CAN_REACH_CRIME_SCENE_IN_TIME"
    ]
    assert opp and all(o.status is PropositionStatus.UNKNOWN for o in opp)


def test_def027_validation_exposes_overconstrained_and_fails():
    from app.validation.solution import assemble_solution_proof, evaluate_solution

    public, evidence = _overconstrained_scenario()
    proof = solve_case(public, evidence)
    truth = truth_variant_a()
    validation = evaluate_solution(proof, truth)
    assert validation.time_overconstrained is True
    assert validation.time_accepted is False
    assert validation.all_true is False
    vd = validation.to_dict()
    assert vd["timeOverconstrained"] is True and vd["timeAccepted"] is False

    solution = assemble_solution_proof(public, evidence, proof, truth)
    assert solution.time.overconstrained is True
    assert solution.time.ambiguous is False
    assert solution.time.feasible_intervals == ()
    sd = solution.to_dict()
    assert sd["crimeTime"]["overconstrained"] is True
    assert sd["crimeTime"]["ambiguous"] is False


# ===========================================================================
# DEF-028 — non-constraining presence evidence must not inflate support
# ===========================================================================

def test_def028_no_travel_rule_observation_is_unknown():
    """Original defect (ADV-115): an office observation with NO travel rule
    claimed SUPPORTED/SUPPORT_CANDIDATE."""
    public = _mini_public(travel_office_scene=None)  # no travel rule office->scene
    evidence = [
        _observation("obs_office", "sus_1", "office", "2026-09-11T22:00:00+02:00"),
        _noise("noise_scene", "2026-09-11T22:05:00+02:00"),
    ]
    proof = solve_case(public, evidence)
    who = proof.who
    assert who.unique is False          # susceptible and viable but unknown
    assert who.winner is None
    assert who.excluded == ()
    opp = [
        o
        for o in proof.rule_outcomes_by_candidate
        if o.target_candidate_id == "sus_1"
        and o.proposition_type == "CAN_REACH_CRIME_SCENE_IN_TIME"
    ]
    assert opp[0].status is PropositionStatus.UNKNOWN
    assert opp[0].effect is RuleEffect.NO_EFFECT
    assert not any(o.is_support() for o in opp)


def test_def028_scene_observation_is_supported():
    public = _mini_public(travel_office_scene=None)
    evidence = [
        _observation("obs_scene", "sus_1", SCENE_LOCATION, "2026-09-11T22:16:00+02:00"),
        _noise("noise_scene", "2026-09-11T22:05:00+02:00"),
    ]
    proof = solve_case(public, evidence)
    who = proof.who
    assert who.winner == "sus_1"
    opp = [
        o
        for o in proof.rule_outcomes_by_candidate
        if o.target_candidate_id == "sus_1"
        and o.proposition_type == "CAN_REACH_CRIME_SCENE_IN_TIME"
    ]
    assert opp[0].status is PropositionStatus.SUPPORTED
    assert opp[0].effect is RuleEffect.SUPPORT_CANDIDATE
    assert opp[0].evidence_ids == ("obs_scene",)


def test_def028_constraining_travel_rule_observation_is_supported():
    public = _mini_public(travel_office_scene=600)
    evidence = [
        _observation("obs_office", "sus_1", "office", "2026-09-11T22:00:00+02:00"),
        _noise("noise_scene", "2026-09-11T22:05:00+02:00"),
    ]
    proof = solve_case(public, evidence)
    who = proof.who
    # Feasible [22:05:00,22:05:01) and earliest arrival 22:10 -> no overlap
    # would EXCLUDE; so use a feasible window after arrival to keep it viable:
    public_viable = _mini_public(travel_office_scene=600)
    evidence_viable = [
        _observation("obs_office_v", "sus_1", "office", "2026-09-11T22:00:00+02:00"),
        _noise("noise_scene_v", "2026-09-11T22:15:00+02:00"),  # after arrival 22:10
    ]
    proof_viable = solve_case(public_viable, evidence_viable)
    assert "sus_1" in proof_viable.who.viable
    opp = [
        o
        for o in proof_viable.rule_outcomes_by_candidate
        if o.target_candidate_id == "sus_1"
        and o.proposition_type == "CAN_REACH_CRIME_SCENE_IN_TIME"
    ]
    assert opp[0].status is PropositionStatus.SUPPORTED
    assert opp[0].evidence_ids == ("obs_office_v",)
    assert proof_who_from_validation(proof_viable.who).unique is True


def proof_who_from_validation(who):
    return who


def test_def028_golden_unchanged():
    proof = solve_case(golden_public(), golden_evidence())
    assert proof.who.winner == "thomas_reed"
    assert proof.who.unique is True
    assert {e.candidate_id for e in proof.who.excluded} == {"anna_karlsson", "michael_carter"}


# ===========================================================================
# DEF-029 — evidence iteration contract (any Iterable of EvidenceFact)
# ===========================================================================

def test_def029_set_and_generator_evidence_accepted():
    public = golden_public()
    evidence_set = set(golden_evidence())
    proof_set = solve_case(public, evidence_set)
    assert proof_set.who.winner == "thomas_reed"

    generator = (fact for fact in golden_evidence())
    proof_gen = solve_case(public, generator)
    assert proof_gen.who.winner == "thomas_reed"
    assert proof_set == proof_gen


def test_def029_non_evidence_element_raises_typeerror():
    public = golden_public()
    truth = truth_variant_a()
    with pytest.raises(TypeError):
        solve_case(public, [truth])  # element-level check, not silent coercion
    with pytest.raises(TypeError):
        solve_case(truth, golden_evidence())
    with pytest.raises(TypeError):
        solve_case(public, truth)


# ===========================================================================
# DEF-030 — FrozenDict true immutability
# ===========================================================================

def test_def030_frozendict_mapping_view_cannot_mutate():
    fd = FrozenDict({"a": 1, "b": 2})
    with pytest.raises(TypeError):
        fd._data["a"] = 99
    assert fd["a"] == 1


def test_def030_hash_matches_equality_and_is_cached():
    fd1 = FrozenDict({"a": 1, "b": 2})
    fd2 = FrozenDict({"b": 2, "a": 1})  # different insertion order
    assert fd1 == fd2
    assert hash(fd1) == hash(fd2)
    assert hash(fd1) == fd1._hash  # cached/stable


def test_def030_content_equality_remains_order_insensitive():
    assert FrozenDict({"x": {"k": 1}}) == FrozenDict({"x": {"k": 1}})
    assert FrozenDict({"x": [1, 2]}) == {"x": [1, 2]}
    assert FrozenDict({"k": 1}) != FrozenDict({"k": 2})


# ===========================================================================
# DEF-031 — PublicCase duplicate id acceptance
# ===========================================================================

def test_def031_duplicate_person_ids_rejected():
    public = golden_public()
    duplicated = (public.persons[1], public.persons[1])
    with pytest.raises(ValueError, match="duplicate person_id"):
        PublicCase(
            case_id=public.case_id,
            case_version=public.case_version,
            persons=duplicated,
            motives=public.motives,
            objects=public.objects,
            locations=public.locations,
            travel_rules=public.travel_rules,
            scene=public.scene,
        )


def test_def031_duplicate_motive_object_location_rejected():
    public = golden_public()
    with pytest.raises(ValueError, match="duplicate motive_id"):
        PublicCase(
            case_id=public.case_id, case_version=1,
            persons=public.persons,
            motives=(public.motives[0], public.motives[0]),
            objects=public.objects, locations=public.locations,
            travel_rules=public.travel_rules, scene=public.scene,
        )
    with pytest.raises(ValueError, match="duplicate object_id"):
        PublicCase(
            case_id=public.case_id, case_version=1,
            persons=public.persons, motives=public.motives,
            objects=(public.objects[0], public.objects[0]),
            locations=public.locations, travel_rules=public.travel_rules,
            scene=public.scene,
        )
    with pytest.raises(ValueError, match="duplicate location_id"):
        PublicCase(
            case_id=public.case_id, case_version=1,
            persons=public.persons, motives=public.motives, objects=public.objects,
            locations=(public.locations[0], public.locations[0]),
            travel_rules=public.travel_rules, scene=public.scene,
        )


def test_def031_duplicate_travel_pair_rejected():
    public = golden_public()
    rule = public.travel_rules[0]
    with pytest.raises(ValueError, match="duplicate directed pairs"):
        PublicCase(
            case_id=public.case_id, case_version=1,
            persons=public.persons, motives=public.motives, objects=public.objects,
            locations=public.locations,
            travel_rules=(rule, rule),
            scene=public.scene,
        )


def test_def031_golden_valid_and_universes_deduplicated():
    public = golden_public()
    assert isinstance(public, PublicCase)  # construction OK
    from app.domain.eligibility import suspect_universe

    assert len(suspect_universe(public)) == len(set(suspect_universe(public)))


# ===========================================================================
# DEF-032 — FORENSIC_WEAPON_MATCH structured typing
# ===========================================================================

@pytest.mark.parametrize("bad_match", ["false", "true", 1, 0, None, []])
def test_def032_weapon_match_non_bool_rejected(bad_match):
    with pytest.raises(ValueError, match="match"):
        TypedProposition(
            type=FORENSIC_WEAPON_MATCH,
            object_id="kitchen_knife",
            structured={"match": bad_match},
        )


def test_def032_weapon_match_missing_key_rejected():
    with pytest.raises(ValueError, match="match"):
        TypedProposition(type=FORENSIC_WEAPON_MATCH, object_id="kitchen_knife", structured={})


def test_def032_weapon_match_bool_drives_solver():
    from app.domain.evidence import EvidenceFact as _F

    public = golden_public()
    neg = _F(
        id="n1", kind="forensic",
        propositions=(
            TypedProposition(
                type=FORENSIC_WEAPON_MATCH, object_id="letter_opener", structured={"match": False}
            ),
        ),
        source_ref=SourceRef(kind="lab", source_id="lab_1"), reliability=Reliability.HIGH,
        presentation={"title": "n", "description": "n"},
    )
    pos = _F(
        id="p1", kind="forensic",
        propositions=(
            TypedProposition(
                type=FORENSIC_WEAPON_MATCH, object_id="kitchen_knife", structured={"match": True}
            ),
        ),
        source_ref=SourceRef(kind="lab", source_id="lab_1"), reliability=Reliability.HIGH,
        presentation={"title": "p", "description": "p"},
    )
    proof = solve_case(public, golden_evidence() + [pos, neg])
    assert proof.weapon.unique is True
    assert proof.weapon.winner == "kitchen_knife"
    assert {e.candidate_id for e in proof.weapon.excluded} == {"letter_opener", "scissors"}


# ===========================================================================
# DEF-033 — ALIBI_TIME_CLAIM requires claimedDeparture
# ===========================================================================

def test_def033_alibi_claim_without_departure_rejected():
    with pytest.raises(ValueError, match="claimedDeparture"):
        TypedProposition(type=ALIBI_TIME_CLAIM, person_id="sus_1", structured={})


def test_def033_alibi_claim_with_invalid_departure_rejected():
    with pytest.raises(ValueError):
        TypedProposition(
            type=ALIBI_TIME_CLAIM,
            person_id="sus_1",
            structured={"claimedDeparture": "left at 9pm"},
        )


def test_def033_alibi_claim_valid_behaves_as_before():
    public = _mini_public()
    evidence = [
        EvidenceFact(
            id="claim_1",
            kind="suspect_statement",
            propositions=(
                TypedProposition(
                    type=ALIBI_TIME_CLAIM,
                    person_id="sus_1",
                    structured={"claimedDeparture": "2026-09-11T21:45:00+02:00"},
                ),
            ),
            source_ref=SourceRef(kind="statement", source_id="statement_1"),
            reliability=Reliability.LOW,
            presentation={"title": "claim", "description": "claim"},
        ),
    ]
    proof = solve_case(public, evidence)
    # Claim accepted; unverified -> unknown; suspect stays viable (no exclude).
    assert "sus_1" in proof.who.viable
    assert proof.who.excluded == ()
    assert not any(
        o.effect is RuleEffect.ALIBI_CREDIBILITY_DECREASE
        for o in proof.rule_outcomes_by_candidate
    )
    # Golden case alibi handling unchanged:
    golden = solve_case(golden_public(), golden_evidence())
    alibi = [
        o
        for o in golden.rule_outcomes_by_candidate
        if o.effect is RuleEffect.ALIBI_CREDIBILITY_DECREASE
    ]
    assert len(alibi) == 1 and alibi[0].target_candidate_id == "thomas_reed"


# ===========================================================================
# DEF-034 — ISO parser strictness + pre-parse at validation
# ===========================================================================

@pytest.mark.parametrize(
    "bad",
    [
        "٢٠٢٦-09-11T22:17:00+02:00",  # Arabic-Indic digits
        "2026-09-11T22:17:00+24:00",  # offset hour 24
        "2026-09-11T22:17:00+02:60",  # offset minute 60
        "2026-09-11T25:00:00Z",        # hour 25
        "2026-02-30T10:00:00Z",        # impossible calendar day
        "2026-09-11T22:17:61Z",        # second 61
    ],
)
def test_def034_parser_strictness(bad):
    with pytest.raises(ValueError):
        parse_iso8601(bad)


def test_def034_leap_second_deterministic_clamp():
    leap = parse_iso8601_to_epoch("2026-09-11T22:17:60+02:00")
    clamped = parse_iso8601_to_epoch("2026-09-11T22:17:59+02:00")
    assert leap == clamped


def test_def034_malformed_timestamp_caught_at_validation_entry():
    public = golden_public()
    evidence = [_last_seen("bad_ts_1", "2026-09-11T25:00:00Z")]
    with pytest.raises(ValueError, match="invalid or out-of-domain timestamp"):
        solve_case(public, evidence)
    # Also rejected by validate_evidence directly (no mid-solve crash).
    from app.domain.evidence import validate_evidence

    issues = validate_evidence(public, [_last_seen("bad_ts_2", "22:17:00")])
    assert issues and any("invalid or out-of-domain" in i for i in issues)


def test_def034_golden_still_passes():
    proof = solve_case(golden_public(), golden_evidence())
    assert proof.who.winner == "thomas_reed"
    assert proof.when.overconstrained is False