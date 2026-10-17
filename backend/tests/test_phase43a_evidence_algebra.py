"""Phase43A — Frontier evidence-algebra completion (DEF-071).

Hermetic (ZERO provider / LLM calls; zero network). Covers:

15.1 DEF-071 regression     ambiguous canonical evidence -> solver-unique (no LLM);
15.2 idempotence            complete(complete(x)) == complete(x);
15.3 already-solvable no-op zero facts added, input unchanged;
15.4 structural-defect refusal (unknown person id / invalid object ref /
     witness-candidate violation are NOT "fixed");
15.5 witness non-premise     a WITNESS_CLAIMS statement alone never proves WHO;
15.6 homicide dimension gaps WHO/WHY/WEAPON/time + combinations;
15.7 witness-murderer        role stays witness, no SUSPECT_ELIGIBLE, WHO unique;
15.8 suicide                 murderer_id None, no murderer fact, victim-linked
     motive/weapon, no false conviction.

Plus the provider-neutral module contracts (canonical_evidence_algebra
delegation, analyzer classification, bounded completion report) and the generic
controller integration (P43A-FR-07/09/10).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from app.domain.public import (  # noqa: E402
    PublicCase,
    PublicLocation,
    PublicMotive,
    PublicObject,
    PublicPerson,
    PublicScene,
    PublicTravelRule,
)
from app.domain.resolution import ResolutionType  # noqa: E402
from app.domain.solver import solve_case  # noqa: E402
from app.domain.truth import Crime, CrimeTime  # noqa: E402
from app.generation import evidence_algebra as ea  # noqa: E402
from app.generation import pipeline  # noqa: E402
from app.generation.pipeline import AttemptRecord  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    CrimeSpec,
    CrimeTimeSpec,
    EvidenceSetSpec,
    EvidenceSpec,
    LocationSpec,
    MotiveSpec,
    ObjectSpec,
    PersonSpec,
    PropSpec,
    PublicWorldSpec,
    SceneSpec,
    TravelRuleSpec,
    WorldGraphSpec,
)
from app.validation.solution import evaluate_solution  # noqa: E402

_CANONICAL = "2026-09-11T22:17:00+02:00"
_G = GOLDEN_STAGE_PAYLOADS


# --------------------------------------------------------------------------- #
# domain fixtures (canonical PublicCase + Crime — no provider)
# --------------------------------------------------------------------------- #


def _person(pid, name, role, affordances):
    # DEF-115: a victim person must carry a complete identity profile for the
    # quality gate (name + age + gender + occupation).
    presented = (
        {"age": 40, "gender": "female", "occupation": "Victim Occupation"}
        if role == "victim"
        else {}
    )
    return PublicPerson(
        person_id=pid,
        name=name,
        role=role,
        public_affordances=frozenset(affordances),
        presented_data=presented,
    )


def _public_homicide(*, witness_eligible: bool = False, murderer: str = "murderer_01"):
    witness_aff = (
        ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER")
        if witness_eligible
        else ("VISIBLE_CHARACTER",)
    )
    return PublicCase(
        case_id="CASE-X",
        case_version=1,
        persons=(
            _person("victim_01", "Victim One", "victim", ("VISIBLE_CHARACTER",)),
            _person(
                "murderer_01", "Murderer One", "suspect",
                ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"),
            ),
            _person(
                "suspect_b", "Suspect B", "suspect",
                ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"),
            ),
            _person("witness_a", "Witness A", "witness", witness_aff),
        ),
        motives=(
            PublicMotive("motive_a", "Motive A", frozenset({"MOTIVE_CANDIDATE"})),
            PublicMotive("motive_b", "Motive B", frozenset({"MOTIVE_CANDIDATE"})),
        ),
        objects=(
            PublicObject(
                "kitchen_knife", "PROP_KITCHEN_KNIFE_01",
                frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"}),
            ),
            PublicObject(
                "letter_opener", "PROP_LETTER_OPENER_01",
                frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"}),
            ),
            PublicObject(
                "scissors", "PROP_SCISSORS_01",
                frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"}),
            ),
        ),
        locations=(
            PublicLocation("apartment", "Apartment"),
            PublicLocation("office", "Office"),
            PublicLocation("bar", "Bar"),
        ),
        travel_rules=(
            PublicTravelRule("office", "apartment", 1200),
            PublicTravelRule("bar", "apartment", 2700),
        ),
        scene=PublicScene("apartment", "Apartment"),
    )


def _crime_homicide(*, murderer: str = "murderer_01"):
    return Crime(
        type="murder",
        victim_id="victim_01",
        murderer_id=murderer,
        motive_id="motive_a",
        weapon_id="kitchen_knife",
        location_id="apartment",
        crime_time=CrimeTime(_CANONICAL, 120),
        resolution_type=ResolutionType.HOMICIDE,
    )


def _public_witness_murderer():
    """A HOMICIDE whose hidden murderer is the public WITNESS ``witness_a``."""
    base = _public_homicide()
    persons = tuple(
        p for p in base.persons if p.person_id != "murderer_01"
    )
    return PublicCase(
        case_id=base.case_id,
        case_version=base.case_version,
        persons=persons,
        motives=base.motives,
        objects=base.objects,
        locations=base.locations,
        travel_rules=base.travel_rules,
        scene=base.scene,
    )


def _public_suicide():
    base = _public_homicide()
    return PublicCase(
        case_id=base.case_id,
        case_version=base.case_version,
        persons=base.persons,
        motives=base.motives,
        objects=base.objects,
        locations=base.locations,
        travel_rules=base.travel_rules,
        scene=base.scene,
    )


def _crime_suicide():
    return Crime(
        type="suicide",
        victim_id="victim_01",
        murderer_id=None,
        motive_id="motive_a",
        weapon_id="kitchen_knife",
        location_id="apartment",
        crime_time=CrimeTime(_CANONICAL, 120),
        resolution_type=ResolutionType.SUICIDE,
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _empty_spec() -> EvidenceSetSpec:
    return EvidenceSetSpec(evidence=())


def _facts(evidence) -> tuple:
    items = getattr(evidence, "evidence", evidence)
    return ea._as_domain_evidence(items)


def _ids(spec: EvidenceSetSpec) -> tuple[str, ...]:
    return tuple(f.id for f in spec.evidence)


def _without(spec: EvidenceSetSpec, drop_ids: set[str]) -> EvidenceSetSpec:
    return EvidenceSetSpec(
        evidence=tuple(f for f in spec.evidence if f.id not in drop_ids)
    )


def _full(public, crime) -> EvidenceSetSpec:
    spec, _rules, _notes = ea.canonical_evidence_algebra(public, crime)
    assert spec is not None
    return spec


def _dims(public, crime, evidence):
    return ea.analyze_evidence_gaps(public, crime, evidence)


# --------------------------------------------------------------------------- #
# 15.1 DEF-071 regression: ambiguous -> unique, no LLM
# --------------------------------------------------------------------------- #


def test_def071_ambiguous_evidence_completes_to_unique():
    public = _public_homicide()
    crime = _crime_homicide()

    # The pre-completion canonical state is structurally valid but ambiguous on
    # WHO/WHY/WEAPON (the exact live DEF-071 class).
    analysis = _dims(public, crime, _empty_spec())
    assert analysis.classification == ea.EVIDENCE_UNDERCONSTRAINED
    assert analysis.closable is True
    assert set(analysis.ambiguous_dimensions) == {"who", "why", "weapon"}

    proof_before = solve_case(public, _facts(_empty_spec()))
    assert proof_before.who.unique is False
    assert proof_before.why.unique is False
    assert proof_before.weapon.unique is False

    result = ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    assert result.changed is True
    assert result.facts_added > 0
    assert result.dimensions_after == ()
    assert result.solver_outcome_after == "unique"

    proof_after = solve_case(public, _facts(result.evidence))
    assert proof_after.who.unique is True
    assert proof_after.who.winner == "murderer_01"
    assert proof_after.why.unique is True
    assert proof_after.why.winner == "motive_a"
    assert proof_after.weapon.unique is True
    assert proof_after.weapon.winner == "kitchen_knife"
    assert proof_after.when.ambiguous is False
    assert proof_after.when.overconstrained is False


def test_def071_pre_change_code_would_have_no_completion_module():
    """The completion entry point is the Phase43A addition: the module/API did
    not exist before this phase (this test fails with ImportError pre-change)."""
    assert callable(ea.complete_evidence_if_ambiguous)
    assert callable(ea.analyze_evidence_gaps)
    assert callable(ea.canonical_evidence_algebra)


# --------------------------------------------------------------------------- #
# 15.2 idempotence
# --------------------------------------------------------------------------- #


def test_completion_is_idempotent_same_set_order_ids():
    public = _public_homicide()
    crime = _crime_homicide()

    first = ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    assert first.changed is True
    assert first.idempotent is True

    second = ea.complete_evidence_if_ambiguous(public, crime, first.evidence)
    assert second.changed is False
    assert second.facts_added == 0
    assert second.idempotent is True
    # same evidence set / order / ids, no duplicates.
    assert _ids(second.evidence) == _ids(first.evidence)
    assert len(set(_ids(first.evidence))) == len(_ids(first.evidence))
    assert second.evidence.evidence == first.evidence.evidence
    # same solver proof.
    assert solve_case(public, _facts(first.evidence)) == solve_case(
        public, _facts(second.evidence)
    )


def test_canonical_algebra_is_deterministic():
    public = _public_homicide()
    crime = _crime_homicide()
    a = ea.canonical_evidence_algebra(public, crime)
    b = ea.canonical_evidence_algebra(public, crime)
    assert a == b


# --------------------------------------------------------------------------- #
# 15.3 already-solvable no-op
# --------------------------------------------------------------------------- #


def test_already_solvable_adds_zero_facts_and_is_unchanged():
    public = _public_homicide()
    crime = _crime_homicide()
    full = _full(public, crime)

    result = ea.complete_evidence_if_ambiguous(public, crime, full)
    assert result.changed is False
    assert result.facts_added == 0
    assert result.dimensions_before == ()
    assert result.evidence is full  # the SAME object — untouched

    # The analyzer agrees this is not an evidence gap.
    analysis = _dims(public, crime, full)
    assert analysis.classification == ea.NOT_AMBIGUOUS
    assert analysis.closable is False


# --------------------------------------------------------------------------- #
# 15.4 structural-defect refusal
# --------------------------------------------------------------------------- #


def _gen_crime_homicide() -> CrimeSpec:
    return CrimeSpec(
        type="murder",
        victim_id="victim_01",
        murderer_id="murderer_01",
        motive_id="motive_a",
        weapon_id="kitchen_knife",
        location_id="apartment",
        crime_time=CrimeTimeSpec(_CANONICAL, 120),
        resolution_type=ResolutionType.HOMICIDE,
    )


def _gen_world_homicide(*, witness_eligible: bool = False) -> PublicWorldSpec:
    witness_aff = (
        ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER")
        if witness_eligible
        else ("VISIBLE_CHARACTER",)
    )
    return PublicWorldSpec(
        persons=(
            PersonSpec(
                "victim_01", "Victim One", "victim", ("VISIBLE_CHARACTER",),
                presented_data={"age": 40, "gender": "female", "occupation": "Victim Occupation"},
            ),
            PersonSpec(
                "murderer_01", "Murderer One", "suspect",
                ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"),
            ),
            PersonSpec(
                "suspect_b", "Suspect B", "suspect",
                ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"),
            ),
            PersonSpec("witness_a", "Witness A", "witness", witness_aff),
        ),
        motives=(
            MotiveSpec("motive_a", "Motive A", ("MOTIVE_CANDIDATE",)),
            MotiveSpec("motive_b", "Motive B", ("MOTIVE_CANDIDATE",)),
        ),
        objects=(
            ObjectSpec(
                "kitchen_knife", "PROP_KITCHEN_KNIFE_01",
                ("POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
            ),
            ObjectSpec(
                "letter_opener", "PROP_LETTER_OPENER_01",
                ("POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
            ),
            ObjectSpec(
                "scissors", "PROP_SCISSORS_01",
                ("POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
            ),
        ),
        locations=(
            LocationSpec("apartment", "Apartment"),
            LocationSpec("office", "Office"),
            LocationSpec("bar", "Bar"),
        ),
        travel_rules=(
            TravelRuleSpec("office", "apartment", 1200),
            TravelRuleSpec("bar", "apartment", 2700),
        ),
        scene=SceneSpec("apartment", "Apartment"),
    )


def _attempt(public_world, crime, evidence, world_graph=None) -> AttemptRecord:
    attempt = AttemptRecord(attempt_id="GA-x", case_id="CASE-x", session_id="Q-x")
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = crime
    attempt.stage_outputs[GenerationStage.PUBLIC_WORLD] = public_world
    attempt.stage_outputs[GenerationStage.EVIDENCE] = evidence
    attempt.stage_outputs[GenerationStage.WORLD_GRAPH] = world_graph or WorldGraphSpec()
    attempt.stage_done = {
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
    }
    return attempt


def test_helper_completes_evidence_only_ambiguity():
    """The generic-path hook DOES complete a pure evidence gap (empty evidence)."""
    attempt = _attempt(_gen_world_homicide(), _gen_crime_homicide(), _empty_spec())
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is True
    assert attempt._evidence_completed is True
    # The completion is deterministic canonical evidence.
    ids = _ids(attempt.stage_outputs[GenerationStage.EVIDENCE])
    assert "d_ev_presence" in ids
    assert "d_ev_weapon_true" in ids
    # Canonical validation + solver rerun succeeded (sole authority).
    assert attempt.last_validation is not None
    assert attempt.last_validation.valid is True


def test_helper_refuses_unknown_person_id():
    bad = EvidenceSetSpec(
        evidence=(
            EvidenceSpec(
                id="bad_ev_person",
                kind="cctv",
                propositions=(
                    PropSpec(
                        "PERSON_OBSERVED_AT_LOCATION",
                        person_id="ghost_person",
                        location_id="bar",
                        observed_at=_CANONICAL,
                        uncertainty_seconds=30,
                    ),
                ),
                source_ref={"kind": "record", "sourceId": "record_bad"},
                presentation={"title": "t", "description": "d"},
            ),
        )
    )
    attempt = _attempt(_gen_world_homicide(), _gen_crime_homicide(), bad)
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is False
    # The defect was NOT "fixed": the evidence is unchanged.
    assert attempt.stage_outputs[GenerationStage.EVIDENCE] is bad


def test_helper_refuses_invalid_object_reference():
    bad = EvidenceSetSpec(
        evidence=(
            EvidenceSpec(
                id="bad_ev_object",
                kind="forensic",
                propositions=(
                    PropSpec(
                        "FORENSIC_WEAPON_MATCH",
                        object_id="ghost_object",
                        structured={"match": True},
                    ),
                ),
                source_ref={"kind": "record", "sourceId": "record_bad"},
                presentation={"title": "t", "description": "d"},
            ),
        )
    )
    attempt = _attempt(_gen_world_homicide(), _gen_crime_homicide(), bad)
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is False
    assert attempt.stage_outputs[GenerationStage.EVIDENCE] is bad


def test_helper_refuses_witness_candidate_violation():
    """A witness carrying SUSPECT_ELIGIBLE is a model-owned candidate defect; the
    deterministic completion must not be used to paper over it."""
    attempt = _attempt(
        _gen_world_homicide(witness_eligible=True),
        _gen_crime_homicide(),
        _empty_spec(),
    )
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is False
    # the evidence stage spec is the untouched input.
    assert isinstance(attempt.stage_outputs[GenerationStage.EVIDENCE], EvidenceSetSpec)
    assert attempt.stage_outputs[GenerationStage.EVIDENCE].evidence == ()


def test_analyzer_classifies_structural_defect():
    public = _public_homicide()
    crime = _crime_homicide()
    bad = EvidenceSetSpec(
        evidence=(
            EvidenceSpec(
                id="bad_ev_person",
                kind="cctv",
                propositions=(
                    PropSpec(
                        "WITNESS_CLAIMS",
                        person_id="ghost_person",
                    ),
                ),
                source_ref={"kind": "record", "sourceId": "record_bad"},
                presentation={"title": "t", "description": "d"},
            ),
        )
    )
    analysis = ea.analyze_evidence_gaps(public, crime, bad)
    assert analysis.classification == ea.STRUCTURAL_DEFECT
    assert analysis.closable is False


# --------------------------------------------------------------------------- #
# 15.5 witness non-premise
# --------------------------------------------------------------------------- #


def test_witness_claim_alone_never_proves_who():
    public = _public_homicide()
    crime = _crime_homicide()
    claim_only = EvidenceSetSpec(
        evidence=(
            EvidenceSpec(
                id="d_ev_witness_witnessa",
                kind="witness_statement",
                propositions=(PropSpec("WITNESS_CLAIMS", person_id="witness_a"),),
                source_ref={"kind": "record", "sourceId": "record_w"},
                presentation={
                    "title": "Witness statement",
                    "description": "Witness A recounts the evening.",
                    "speakerName": "Witness A",
                    "statement": "Witness A recounts the evening.",
                },
            ),
        )
    )
    # The claim is NOT sufficient solver proof: WHO stays ambiguous.
    proof = solve_case(public, _facts(claim_only))
    assert proof.who.unique is False
    analysis = ea.analyze_evidence_gaps(public, crime, claim_only)
    assert analysis.classification == ea.EVIDENCE_UNDERCONSTRAINED

    # The deterministic completion proves WHO from WORLD/forensic facts, never
    # from the claim text.
    result = ea.complete_evidence_if_ambiguous(public, crime, claim_only)
    after = solve_case(public, _facts(result.evidence))
    assert after.who.unique is True
    for outcome in after.who.rule_outcomes:
        for eid in outcome.evidence_ids:
            assert not str(eid).startswith("d_ev_witness_"), eid
    assert not any(
        str(eid).startswith("d_ev_witness_") for eid in after.evidence_ids_used
    )


# --------------------------------------------------------------------------- #
# 15.6 homicide dimension gaps
# --------------------------------------------------------------------------- #


def _assert_gap_closed(public, crime, evidence, expected_dim):
    analysis = ea.analyze_evidence_gaps(public, crime, evidence)
    assert analysis.classification == ea.EVIDENCE_UNDERCONSTRAINED, analysis
    assert expected_dim in analysis.ambiguous_dimensions
    result = ea.complete_evidence_if_ambiguous(public, crime, evidence)
    assert result.changed is True
    assert result.dimensions_after == ()
    after = solve_case(public, _facts(result.evidence))
    assert after.who.unique is True and after.who.winner == "murderer_01"
    assert after.why.unique is True and after.why.winner == "motive_a"
    assert after.weapon.unique is True and after.weapon.winner == "kitchen_knife"
    assert after.when.ambiguous is False and after.when.overconstrained is False


def test_who_gap():
    public, crime = _public_homicide(), _crime_homicide()
    full = _full(public, crime)
    gap = _without(full, {f.id for f in full.evidence if f.id.startswith("d_ev_opp_")})
    _assert_gap_closed(public, crime, gap, "who")


def test_why_gap():
    public, crime = _public_homicide(), _crime_homicide()
    full = _full(public, crime)
    gap = _without(
        full,
        {f.id for f in full.evidence if f.id.startswith("d_ev_motive")},
    )
    _assert_gap_closed(public, crime, gap, "why")


def test_weapon_gap():
    public, crime = _public_homicide(), _crime_homicide()
    full = _full(public, crime)
    gap = _without(
        full,
        {f.id for f in full.evidence if f.id.startswith("d_ev_weapon") or f.id == "d_ev_fp"},
    )
    _assert_gap_closed(public, crime, gap, "weapon")


def test_time_gap():
    public, crime = _public_homicide(), _crime_homicide()
    full = _full(public, crime)
    splitter = EvidenceSpec(
        id="d_ev_split_window",
        kind="cctv",
        propositions=(
            PropSpec(
                "TIME_WINDOW_EXCLUSION",
                observed_at=_CANONICAL,
                uncertainty_seconds=0,
            ),
        ),
        source_ref={"kind": "record", "sourceId": "record_split"},
        presentation={"title": "Coverage", "description": "coverage gap"},
    )
    gap = EvidenceSetSpec(evidence=full.evidence + (splitter,))
    analysis = ea.analyze_evidence_gaps(public, crime, gap)
    assert analysis.classification == ea.EVIDENCE_UNDERCONSTRAINED
    assert "when" in analysis.ambiguous_dimensions
    proof_before = solve_case(public, _facts(gap))
    assert proof_before.when.ambiguous is True
    assert proof_before.when.overconstrained is False
    result = ea.complete_evidence_if_ambiguous(public, crime, gap)
    assert result.dimensions_after == ()
    after = solve_case(public, _facts(result.evidence))
    assert after.when.ambiguous is False
    assert after.when.overconstrained is False


def test_combined_gaps():
    public, crime = _public_homicide(), _crime_homicide()
    analysis = ea.analyze_evidence_gaps(public, crime, _empty_spec())
    assert set(analysis.ambiguous_dimensions) == {"who", "why", "weapon"}
    result = ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    assert result.dimensions_before == analysis.ambiguous_dimensions
    assert result.dimensions_after == ()
    after = solve_case(public, _facts(result.evidence))
    assert after.who.unique and after.why.unique and after.weapon.unique


def test_overconstrained_time_is_not_closable():
    """A contradictory (overconstrained) time set keeps its repair/regenerate
    owner — the deterministic completion must NOT paper over it."""
    public, crime = _public_homicide(), _crime_homicide()
    seen_alive = EvidenceSpec(
        id="d_ev_contradict_seen",
        kind="witness_observation",
        propositions=(
            PropSpec(
                "VICTIM_LAST_SEEN_ALIVE_AT",
                person_id="victim_01",
                location_id="apartment",
                observed_at="2026-09-11T22:18:00+02:00",
                uncertainty_seconds=0,
            ),
        ),
        source_ref={"kind": "record", "sourceId": "record_seen"},
        presentation={"title": "Seen", "description": "seen alive late"},
    )
    found = EvidenceSpec(
        id="d_ev_contradict_found",
        kind="witness_observation",
        propositions=(
            PropSpec(
                "BODY_FIRST_FOUND_AT",
                location_id="apartment",
                observed_at="2026-09-11T22:16:00+02:00",
                uncertainty_seconds=0,
            ),
        ),
        source_ref={"kind": "record", "sourceId": "record_found"},
        presentation={"title": "Found", "description": "found early"},
    )
    spec = EvidenceSetSpec(evidence=(seen_alive, found))
    proof = solve_case(public, _facts(spec))
    assert proof.when.overconstrained is True
    analysis = ea.analyze_evidence_gaps(public, crime, spec)
    assert analysis.classification == ea.TIME_OVERCONSTRAINED
    assert analysis.closable is False
    result = ea.complete_evidence_if_ambiguous(public, crime, spec)
    assert result.changed is False
    assert result.evidence is spec


# --------------------------------------------------------------------------- #
# 15.7 witness-murderer
# --------------------------------------------------------------------------- #


def test_witness_murderer_completion_keeps_role_and_unique_who():
    public = _public_witness_murderer()
    crime = _crime_homicide(murderer="witness_a")

    witness = next(p for p in public.persons if p.person_id == "witness_a")
    assert witness.role == "witness"
    assert "SUSPECT_ELIGIBLE" not in witness.public_affordances

    analysis = ea.analyze_evidence_gaps(public, crime, _empty_spec())
    assert analysis.classification == ea.EVIDENCE_UNDERCONSTRAINED
    assert "who" in analysis.ambiguous_dimensions

    result = ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    after = solve_case(public, _facts(result.evidence))
    assert after.who.unique is True
    assert after.who.winner == "witness_a"
    # role/branch membership unchanged by the completion.
    witness_after = next(p for p in public.persons if p.person_id == "witness_a")
    assert witness_after.role == "witness"
    assert "SUSPECT_ELIGIBLE" not in witness_after.public_affordances
    from app.domain.eligibility import suspect_universe

    assert "witness_a" not in suspect_universe(public)
    assert "witness_a" in after.who.universe


# --------------------------------------------------------------------------- #
# 15.8 suicide
# --------------------------------------------------------------------------- #


def test_suicide_completion_preserves_no_murderer_and_no_guilt():
    public = _public_suicide()
    crime = _crime_suicide()
    assert crime.murderer_id is None

    result = ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    assert result.changed is True
    ids = _ids(result.evidence)
    # No homicide perpetrator / killer-placement / guilt facts.
    assert "d_ev_presence" not in ids
    assert "d_ev_alibi" not in ids
    assert "d_ev_fp" not in ids
    # Victim-linked motive + weapon forensics consistent with self-infliction.
    link = next(f for f in result.evidence.evidence if f.id == "d_ev_motive_link")
    assert any(
        p.type == "MOTIVE_LINKED_TO_PERSON" and p.person_id == "victim_01"
        for p in link.propositions
    )
    weapon_true = next(f for f in result.evidence.evidence if f.id == "d_ev_weapon_true")
    assert any(
        p.type == "FORENSIC_WEAPON_MATCH" and p.structured.get("match") is True
        for p in weapon_true.propositions
    )

    from app.domain.truth import CaseTruth

    proof = solve_case(public, _facts(result.evidence))
    truth = CaseTruth(
        case_id="CASE-X",
        case_version=1,
        title="T",
        crime=crime,
        witness_truthfulness=(),
    )
    validation = evaluate_solution(proof, truth)
    assert validation.murderer_true is True  # suicide: WHO is vacuous
    assert validation.motive_true is True
    assert validation.weapon_true is True
    assert validation.all_true is True
    # No innocent red-herring suspect is convicted (WHO is not unique in the
    # homicide sense; the suicide option is the resolution).
    assert proof.who.winner != "victim_01"


def test_crime_is_never_mutated_by_completion():
    """CaseTruth / Crime identity and values are never touched."""
    public = _public_homicide()
    crime = _crime_homicide()
    before = (crime.victim_id, crime.murderer_id, crime.motive_id, crime.weapon_id,
              crime.location_id, crime.resolution_type, crime.crime_time.canonical)
    ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    after = (crime.victim_id, crime.murderer_id, crime.motive_id, crime.weapon_id,
             crime.location_id, crime.resolution_type, crime.crime_time.canonical)
    assert before == after


# --------------------------------------------------------------------------- #
# travel-rule completion + bounded report
# --------------------------------------------------------------------------- #


def test_travel_rule_completion_and_report():
    """When the public has a distant location with no travel rule, the algebra
    deterministically adds the rule so the opportunity exclusion is backed."""
    public = PublicCase(
        case_id="CASE-X",
        case_version=1,
        persons=_public_homicide().persons,
        motives=_public_homicide().motives,
        objects=_public_homicide().objects,
        locations=(
            PublicLocation("apartment", "Apartment"),
            PublicLocation("office", "Office"),
        ),
        travel_rules=(),  # NO rule: the algebra must add one
        scene=PublicScene("apartment", "Apartment"),
    )
    crime = _crime_homicide()
    result = ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    assert result.changed is True
    rules = result.extra_travel_rules
    assert rules and all(
        r.travel_time_seconds == ea._DEFAULT_DISTANT_TRAVEL_SECONDS for r in rules
    )
    assert all(r.to_location_id == "apartment" for r in rules)
    # The bounded report carries the closed observable fields.
    assert result.facts_added > 0
    assert result.dimensions_before == ("who", "why", "weapon")
    assert result.solver_outcome_before == "ambiguous"
    assert result.solver_outcome_after == "unique"
    assert result.idempotent is True


# --------------------------------------------------------------------------- #
# generic controller integration (P43A-FR-07/09/10)
# --------------------------------------------------------------------------- #


def _world_graph_without_evidence_refs() -> str:
    doc = json.loads(_G[GenerationStage.WORLD_GRAPH])
    for placement in doc["worldGraph"]["placements"]:
        placement["evidenceId"] = None
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def _empty_evidence_doc() -> str:
    return json.dumps({"evidence": []}, indent=2, ensure_ascii=False, sort_keys=True)


def test_generic_controller_completes_before_world_graph_and_publishes():
    """DEF-071 (no LLM): a generic-path draft with EMPTY (ambiguous) evidence is
    completed deterministically before the WORLD_GRAPH stage and publishes —
    zero repairs, zero regenerations, no extra provider calls beyond the four
    stages."""
    from app.generation.admission import AdmissionController
    from app.generation.clock import ManualClock
    from app.generation.controller import GenerationController
    from app.generation.fake_provider import FakeProvider
    from app.generation.ids import IdSource
    from app.generation.state_machine import GenerationState, ValidationOutcome

    prompt = (
        "Victim: sarah_miller\n"
        "Murderer: thomas_reed\n"
        "Motive: cover_up_embezzlement\n"
        "Weapon: kitchen_knife\n"
        "Time: 2026-09-11T22:17:00+02:00\n"
        "Witness: emily_reed\n"
    )
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: [_empty_evidence_doc()],
        GenerationStage.WORLD_GRAPH: [_world_graph_without_evidence_refs()],
    }
    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    fake = FakeProvider(script)
    controller = GenerationController(
        provider=fake,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=11,
    )
    handle = controller.start_generation(
        prompt, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    assert record.last_validation is not None
    assert record.last_validation.outcome is ValidationOutcome.VALID
    assert record.budget.repair_passes == 0
    assert record.budget.regenerations == 0
    assert len(fake.calls) == 4  # exactly the four GENERATING stages
    assert record._evidence_completed is True
    # the published evidence is the deterministic canonical algebra.
    ids_out = {str(e.id) for e in record.published.evidence}
    assert "d_ev_presence" in ids_out
    assert "d_ev_weapon_true" in ids_out
    assert record.published.solver_proof.validation.all_true is True


def test_generic_controller_pending_evidence_still_completes_and_publishes():
    """The completion hook also fires for a PENDING EVIDENCE result (the generic
    path fires it when WORLD_GRAPH is next, not only right after the EVIDENCE
    provider call). DEF-071 remains closed on the async/pending transport."""
    from app.generation.admission import AdmissionController
    from app.generation.clock import ManualClock
    from app.generation.controller import GenerationController
    from app.generation.fake_provider import FakeProvider
    from app.generation.ids import IdSource
    from app.generation.provider import GenerationStage, ProviderResult
    from app.generation.state_machine import GenerationState

    prompt = (
        "Victim: sarah_miller\n"
        "Murderer: thomas_reed\n"
        "Motive: cover_up_embezzlement\n"
        "Weapon: kitchen_knife\n"
        "Time: 2026-09-11T22:17:00+02:00\n"
        "Witness: emily_reed\n"
    )
    contents = {
        GenerationStage.CASE_TRUTH: _G[GenerationStage.CASE_TRUTH],
        GenerationStage.PUBLIC_WORLD: _G[GenerationStage.PUBLIC_WORLD],
        GenerationStage.EVIDENCE: _empty_evidence_doc(),
        GenerationStage.WORLD_GRAPH: _world_graph_without_evidence_refs(),
    }
    order = (
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
    )
    script = {stage: ["pending"] for stage in order}
    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    fake = FakeProvider(script)
    controller = GenerationController(
        provider=fake,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=11,
    )
    fake._sink = controller
    handle = controller.start_generation(
        prompt, anonymous_quota_session_id=session.session_id
    )
    for stage in order:
        pending = fake.pending_ids()[0]
        fake.resolve(pending, ProviderResult(content=contents[stage]))
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    assert record._evidence_completed is True
    assert record.published.solver_proof.validation.all_true is True


# --------------------------------------------------------------------------- #
# per-stage deferred ownership (P43A-FR-08): EVIDENCE parse failure is
# algebra-owned; every other stage's defect still blocks.
# --------------------------------------------------------------------------- #


def _attempt_with_deferred_evidence_failure():
    """A valid CASE_TRUTH + PUBLIC_WORLD with a FAILED EVIDENCE stage parse."""
    attempt = AttemptRecord(attempt_id="GA-e", case_id="CASE-e", session_id="Q-e")
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = _gen_crime_homicide()
    attempt.stage_outputs[GenerationStage.PUBLIC_WORLD] = _gen_world_homicide()
    pipeline.apply_stage_output(attempt, GenerationStage.EVIDENCE, "not-json-at-all")
    # A real parse failure was recorded, attributed to the EVIDENCE stage and to
    # the flat union (which stays the validation/classification authority).
    assert attempt.deferred_by_stage.get(GenerationStage.EVIDENCE)
    assert attempt.deferred_structural
    assert GenerationStage.EVIDENCE not in attempt.stage_outputs
    return attempt


def test_evidence_parse_failure_is_algebra_owned_and_validates():
    """15.4b — an EVIDENCE-stage parse failure does NOT block the deterministic
    completion: the algebra supplies the evidence, its deferred issues are
    cleared, and the canonical validation passes (no LLM)."""
    attempt = _attempt_with_deferred_evidence_failure()
    required = set(attempt.deferred_by_stage[GenerationStage.EVIDENCE])

    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is True
    assert attempt._evidence_completed is True
    # The EVIDENCE-owned issues were cleared (per-stage map + flat union).
    assert GenerationStage.EVIDENCE not in attempt.deferred_by_stage
    assert not (required & set(attempt.deferred_structural))
    # The algebra supplied the evidence and canonical validation passed.
    ids = _ids(attempt.stage_outputs[GenerationStage.EVIDENCE])
    assert "d_ev_presence" in ids and "d_ev_weapon_true" in ids
    assert attempt.last_validation is not None
    assert attempt.last_validation.valid is True


def test_case_truth_structural_defect_is_not_masked():
    """P43A-FR-08 — a non-evidence (CASE_TRUTH) structural defect alongside an
    EVIDENCE failure makes the completion REFUSE: the evidence is untouched."""
    attempt = _attempt_with_deferred_evidence_failure()
    pipeline._record_deferred_issues(
        attempt,
        GenerationStage.CASE_TRUTH,
        ("case_truth: unknown key 'unexpectedField'",),
    )
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is False
    assert GenerationStage.EVIDENCE not in attempt.stage_outputs
    # The EVIDENCE deferred issues were NOT cleared (nothing was completed).
    assert "case_truth: unknown key 'unexpectedField'" in attempt.deferred_structural


def test_public_world_structural_defect_is_not_masked():
    """P43A-FR-08 — a non-evidence (PUBLIC_WORLD) structural defect alongside an
    EVIDENCE failure makes the completion REFUSE."""
    attempt = _attempt_with_deferred_evidence_failure()
    pipeline._record_deferred_issues(
        attempt,
        GenerationStage.PUBLIC_WORLD,
        ("public_world: unknown key 'bad'",),
    )
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is False
    assert GenerationStage.EVIDENCE not in attempt.stage_outputs
    assert "public_world: unknown key 'bad'" in attempt.deferred_structural


def test_world_graph_structural_defect_is_not_masked():
    """P43A-FR-08 — a WORLD_GRAPH structural defect alongside an EVIDENCE
    failure also blocks the completion (never masked)."""
    attempt = _attempt_with_deferred_evidence_failure()
    attempt.deferred_by_stage[GenerationStage.WORLD_GRAPH] = (
        "world_graph: parse failed",
    )
    attempt.deferred_structural = tuple(
        sorted(set(attempt.deferred_structural) | {"world_graph: parse failed"})
    )
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is False
    assert GenerationStage.EVIDENCE not in attempt.stage_outputs


def test_witness_candidate_defect_blocks_evidence_completion():
    """A model-owned quality defect (witness carrying SUSPECT_ELIGIBLE) alongside
    an EVIDENCE failure still blocks the deterministic completion."""
    attempt = _attempt_with_deferred_evidence_failure()
    attempt.stage_outputs[GenerationStage.PUBLIC_WORLD] = _gen_world_homicide(
        witness_eligible=True
    )
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is False
    assert GenerationStage.EVIDENCE not in attempt.stage_outputs


def test_helper_noop_preserves_already_unique_evidence():
    """AC-09 — valid, already-unique evidence is left UNCHANGED by the helper."""
    attempt = _attempt(_gen_world_homicide(), _gen_crime_homicide(), _empty_spec())
    public, _ev, truth, _draft = pipeline.assemble(attempt)
    full, _rules, _notes = ea.canonical_evidence_algebra(public, truth.crime)
    attempt.stage_outputs[GenerationStage.EVIDENCE] = full
    attempt.draft = None  # as apply_stage_output would
    attempt._phase3_cache = None  # as apply_stage_output would

    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is False
    assert attempt.stage_outputs[GenerationStage.EVIDENCE] is full
    assert attempt.last_validation is not None
    assert attempt.last_validation.valid is True


# --------------------------------------------------------------------------- #
# model-authored world generalization: extra POTENTIAL_WEAPON objects and a
# too-short travel rule are deterministically closed by the algebra.
# --------------------------------------------------------------------------- #


def _public_with_extra_weapon():
    """A homicide public whose world declares an EXTRA POTENTIAL_WEAPON object."""
    return PublicCase(
        case_id="CASE-X",
        case_version=1,
        persons=_public_homicide().persons,
        motives=_public_homicide().motives,
        objects=_public_homicide().objects
        + (
            PublicObject(
                "baseball_bat",
                "PROP_BASEBALL_BAT_01",
                frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_BLUNT_WEAPON"}),
            ),
        ),
        locations=_public_homicide().locations,
        travel_rules=_public_homicide().travel_rules,
        scene=_public_homicide().scene,
    )


def test_extra_model_weapon_is_forensically_excluded():
    """A model-declared extra weapon enters the solver universe, so the algebra
    must emit a false forensic match for it (weapon unique afterwards)."""
    public = _public_with_extra_weapon()
    crime = _crime_homicide()
    assert "baseball_bat" in {
        o.object_id
        for o in public.objects
        if "POTENTIAL_WEAPON" in o.public_affordances
    }
    proof_before = solve_case(public, _facts(_empty_spec()))
    assert proof_before.weapon.unique is False

    result = ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    assert result.changed is True
    ids = _ids(result.evidence)
    assert "d_ev_weapon_false_baseballbat" in ids
    proof_after = solve_case(public, _facts(result.evidence))
    assert proof_after.weapon.unique is True
    assert proof_after.weapon.winner == "kitchen_knife"


def _public_close_locations():
    """A homicide public whose only non-scene location is a 20s walk away."""
    return PublicCase(
        case_id="CASE-X",
        case_version=1,
        persons=_public_homicide().persons,
        motives=_public_homicide().motives,
        objects=_public_homicide().objects,
        locations=(
            PublicLocation("apartment", "Apartment"),
            PublicLocation("hallway", "Hallway"),
        ),
        travel_rules=(
            PublicTravelRule("hallway", "apartment", 20),
            PublicTravelRule("apartment", "hallway", 20),
        ),
        scene=PublicScene("apartment", "Apartment"),
    )


def test_too_short_travel_rule_is_overridden_to_prove_unreachability():
    """The model's 20s rule cannot prove unreachability; the algebra replaces it
    with the deterministic distant rule so WHO becomes unique."""
    public = _public_close_locations()
    crime = _crime_homicide()
    proof_before = solve_case(public, _facts(_empty_spec()))
    assert proof_before.who.unique is False

    result = ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    assert result.changed is True
    overrides = [r for r in result.extra_travel_rules if r.from_location_id == "hallway"]
    assert overrides and all(
        r.travel_time_seconds == ea._DEFAULT_DISTANT_TRAVEL_SECONDS for r in overrides
    )
    augmented = ea._augment_public_with_rules(public, result.extra_travel_rules)
    proof_after = solve_case(augmented, _facts(result.evidence))
    assert proof_after.who.unique is True
    assert proof_after.who.winner == "murderer_01"

