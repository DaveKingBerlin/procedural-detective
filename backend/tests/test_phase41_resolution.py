"""Phase41 — trusted resolution model (HOMICIDE | SUICIDE) domain tests.

RED scope (§3/§4/§10/§15):
- ``ResolutionType`` is a closed trusted enum: HOMICIDE | SUICIDE.
- ``Crime``/``CrimeSpec`` follow the frozen invariants:
    HOMICIDE: murderer_id required AND != victim_id
    SUICIDE:   murderer_id == None (never ``murderer_id == victim_id``)
    legacy (truth with a non-null murderer_id and NO resolution_type) infers
    HOMICIDE.
- The strict parser accepts ``crime.resolutionType`` (+ nullable/nil
  ``murdererId`` for SUICIDE) WITHOUT breaking the legacy colonial parse.
- Pipeline structural checks skip the murderer resolution for SUICIDE.
- ``evaluate_solution`` is resolution-aware: SUICIDE truth -> murderer_true
  True (no WHO winner required); the latent ``None == None`` hole at
  solution.py:184 is closed; ``all_true`` follows the resolution model.
- Solvers never crash on an empty suspect universe or a None murderer.
- The validation-report classification does not force a REGENERATE on a
  SUICIDE case whose WHO dimension is not unique.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.resolution import ResolutionType  # noqa: E402
from app.domain.truth import CaseTruth, Crime, CrimeTime  # noqa: E402
from app.generation import parser  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import CrimeSpec, CrimeTimeSpec  # noqa: E402

HOMICIDE = ResolutionType.HOMICIDE
SUICIDE = ResolutionType.SUICIDE

_CANONICAL = "2026-09-11T22:17:00+02:00"


def _crime(**overrides) -> Crime:
    base = dict(
        type="murder",
        victim_id="sarah_miller",
        murderer_id="thomas_reed",
        motive_id="cover_up_embezzlement",
        weapon_id="kitchen_knife",
        location_id="office",
        crime_time=CrimeTime(canonical=_CANONICAL, accusation_tolerance_seconds=300),
    )
    base.update(overrides)
    return Crime(**base)


def _crime_spec(**overrides) -> CrimeSpec:
    base = dict(
        type="murder",
        victim_id="sarah_miller",
        murderer_id="thomas_reed",
        motive_id="cover_up_embezzlement",
        weapon_id="kitchen_knife",
        location_id="office",
        crime_time=CrimeTimeSpec(canonical=_CANONICAL, accusation_tolerance_seconds=300),
    )
    base.update(overrides)
    return CrimeSpec(**base)


# --------------------------------------------------------------------------- #
# ResolutionType enum
# --------------------------------------------------------------------------- #


def test_resolution_type_is_a_closed_two_value_enum():
    assert {r.value for r in ResolutionType} == {"HOMICIDE", "SUICIDE"}


# --------------------------------------------------------------------------- #
# Crime truth invariants (§3)
# --------------------------------------------------------------------------- #


def test_legacy_crime_without_resolution_and_with_murderer_infers_homicide():
    from app.domain.resolution import resolution_of

    legacy = _crime()  # no resolution_type, murderer_id set
    assert legacy.resolution_type is None
    assert resolution_of(legacy) is HOMICIDE


def test_homicide_requires_a_murderer():
    with pytest.raises(ValueError):
        _crime(murderer_id=None, resolution_type=HOMICIDE)


def test_homicide_murderer_may_not_be_the_victim():
    with pytest.raises(ValueError):
        _crime(murderer_id="sarah_miller", resolution_type=HOMICIDE)


def test_suicide_forbids_a_murderer():
    with pytest.raises(ValueError):
        _crime(murderer_id="thomas_reed", resolution_type=SUICIDE)


def test_suicide_never_encodes_murderer_as_victim():
    """SUICIDE is NEVER ``murderer_id == victim_id`` — it is ``murderer_id
    is None`` with the victim present."""
    with pytest.raises(ValueError):
        _crime(murderer_id="sarah_miller", resolution_type=SUICIDE)


def test_suicide_truth_keeps_victim_and_nulls_murderer():
    crime = _crime(murderer_id=None, resolution_type=SUICIDE)
    assert crime.murderer_id is None
    assert crime.victim_id == "sarah_miller"
    from app.domain.resolution import resolution_of

    assert resolution_of(crime) is SUICIDE


def test_case_truth_round_trips_suicide_crime():
    truth = CaseTruth(
        case_id="CASE-p41",
        case_version=1,
        title="T",
        crime=_crime(murderer_id=None, resolution_type=SUICIDE),
    )
    assert truth.crime.resolution_type is SUICIDE
    assert truth.crime.murderer_id is None


# --------------------------------------------------------------------------- #
# CrimeSpec (generation shape) invariants
# --------------------------------------------------------------------------- #


def test_crime_spec_legacy_murderer_required():
    with pytest.raises(ValueError):
        _crime_spec(murderer_id=None)


def test_crime_spec_homicide_requires_murderer():
    with pytest.raises(ValueError):
        _crime_spec(murderer_id=None, resolution_type=HOMICIDE)


def test_crime_spec_suicide_forbids_murderer():
    with pytest.raises(ValueError):
        _crime_spec(murderer_id="thomas_reed", resolution_type=SUICIDE)


def test_crime_spec_suicide_accepts_null_murderer():
    spec = _crime_spec(murderer_id=None, resolution_type=SUICIDE)
    assert spec.murderer_id is None
    assert spec.resolution_type is SUICIDE


def test_crime_spec_unknown_resolution_rejected():
    with pytest.raises(ValueError):
        _crime_spec(murderer_id=None, resolution_type="ACCIDENT")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# strict parser (§deg "Do NOT change REQUIREMENTS.md / legacy parse")
# --------------------------------------------------------------------------- #


def _parse_crime(doc: dict) -> "CrimeSpec | None":
    return parser.parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps({"crime": doc}), non_throwing=True
    )


def _crime_doc(**overrides) -> dict:
    doc = {
        "type": "murder",
        "victimId": "sarah_miller",
        "murdererId": "thomas_reed",
        "motiveId": "cover_up_embezzlement",
        "weaponId": "kitchen_knife",
        "locationId": "office",
        "crimeTime": {
            "canonical": _CANONICAL,
            "accusationToleranceSeconds": 300,
        },
    }
    doc.update(overrides)
    return doc


def test_parser_legacy_crime_unchanged():
    parsed = _parse_crime(_crime_doc())
    assert parsed is not None
    assert parsed.murderer_id == "thomas_reed"
    assert parsed.resolution_type is None
    from app.domain.resolution import resolution_of

    assert resolution_of(parsed) is HOMICIDE


def test_parser_homicide_with_explicit_resolution():
    parsed = _parse_crime(_crime_doc(resolutionType="HOMICIDE"))
    assert parsed is not None
    assert parsed.resolution_type is HOMICIDE
    assert parsed.murderer_id == "thomas_reed"


def test_parser_suicide_with_null_murderer():
    parsed = _parse_crime(
        _crime_doc(murdererId=None, resolutionType="SUICIDE")
    )
    assert parsed is not None
    assert parsed.resolution_type is SUICIDE
    assert parsed.murderer_id is None


def test_parser_suicide_with_missing_murderer():
    doc = _crime_doc(resolutionType="SUICIDE")
    doc.pop("murdererId")
    parsed = _parse_crime(doc)
    assert parsed is not None
    assert parsed.resolution_type is SUICIDE
    assert parsed.murderer_id is None


def test_parser_suicide_with_nonnull_murderer_rejected():
    assert _parse_crime(_crime_doc(murdererId="thomas_reed", resolutionType="SUICIDE")) is None


def test_parser_homicide_with_missing_murderer_rejected():
    doc = _crime_doc(resolutionType="HOMICIDE")
    doc.pop("murdererId")
    assert _parse_crime(doc) is None


def test_parser_unknown_resolution_type_rejected():
    assert (
        _parse_crime(_crime_doc(murdererId=None, resolutionType="ACCIDENT"))
        is None
    )


def test_parser_full_draft_accepts_suicide_crime():
    from fixtures.golden_generation import GOLDEN_FULL_DRAFT

    raw = json.loads(GOLDEN_FULL_DRAFT)
    raw["crime"]["resolutionType"] = "SUICIDE"
    raw["crime"]["murdererId"] = None
    draft = parser.parse_full_draft(json.dumps(raw))
    assert draft is not None
    from app.domain.resolution import resolution_of

    assert resolution_of(draft.crime) is SUICIDE
    assert draft.crime.murderer_id is None


# --------------------------------------------------------------------------- #
# pipeline / assembly — resolution-aware structural checks (§15)
# --------------------------------------------------------------------------- #


def test_crime_resolution_issues_are_homicide_only_for_murderer():
    from app.generation import pipeline

    from fixtures.golden_generation import GOLDEN_FULL_DRAFT

    draft = parser.parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    assert pipeline._crime_resolution_issues(draft) == ()

    # HOMICIDE murderer that resolves nowhere -> structural issue.
    ghost = replace(
        draft,
        crime=replace(draft.crime, murderer_id="ghost_who_never_existed"),
    )
    issues = pipeline._crime_resolution_issues(ghost)
    assert any("murdererId" in i for i in issues)

    # SUICIDE has no murderer — no murderer-resolution issue fires.
    suicide = replace(
        draft,
        crime=replace(draft.crime, murderer_id=None, resolution_type=SUICIDE),
    )
    assert pipeline._crime_resolution_issues(suicide) == ()


def test_draft_to_phase3_carries_resolution_into_case_truth():
    from app.generation import pipeline

    from fixtures.golden_generation import GOLDEN_FULL_DRAFT

    draft = parser.parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    suicide = replace(
        draft,
        crime=replace(draft.crime, murderer_id=None, resolution_type=SUICIDE),
    )
    public, _evidence, truth = pipeline._draft_to_phase3(
        suicide, case_id="CASE-p41", title="T"
    )
    assert truth.crime.resolution_type is SUICIDE
    assert truth.crime.murderer_id is None
    assert public.case_id == "CASE-p41"


# --------------------------------------------------------------------------- #
# evaluate_solution — resolution-aware truth comparison (§15)
# --------------------------------------------------------------------------- #


def _golden_solver_proof():
    from app.domain.solver import solve_case

    from fixtures.golden import golden_evidence, golden_public

    return solve_case(golden_public(), golden_evidence())


def test_evaluate_solution_homicide_matches_winner():
    from app.validation.solution import evaluate_solution

    from fixtures.golden import truth_variant_a

    proof = _golden_solver_proof()
    val = evaluate_solution(proof, truth_variant_a())
    assert val.murderer_true is True
    assert val.all_true is True


def test_evaluate_solution_homicide_wrong_winner_not_true():
    from app.domain.proof import CandidateDimensionResult
    from app.validation.solution import evaluate_solution

    from fixtures.golden import truth_variant_a

    proof = _golden_solver_proof()
    proof = replace(
        proof,
        who=CandidateDimensionResult(
            dimension="suspect",
            universe=proof.who.universe,
            viable=(),
            supported=(),
            excluded=(),
            unknown_remaining=(),
            unique=False,
            winner=None,
        ),
    )
    val = evaluate_solution(proof, truth_variant_a())
    # HOMICIDE + no WHO winner -> never "murderer_true" (the None==None hole
    # must not leak through a null murderer).
    assert val.murderer_true is False
    assert val.all_true is False


def test_evaluate_solution_suicide_needs_no_who_winner():
    from app.validation.solution import evaluate_solution

    from fixtures.golden import truth_variant_a

    truth = truth_variant_a()
    suicide = replace(
        truth,
        crime=replace(
            truth.crime,
            murderer_id=None,
            resolution_type=SUICIDE,
        ),
    )
    proof = _golden_solver_proof()
    val = evaluate_solution(proof, suicide)
    assert val.resolution_type == "SUICIDE"
    assert val.murderer_true is True  # no WHO winner required
    assert val.canonical_murderer == ""  # never str(None) in the report
    assert val.all_true is True  # motive/weapon/time still true on the golden
    assert val.to_dict()["resolutionType"] == "SUICIDE"


def test_evaluate_solution_suicide_wrong_motive_still_fails():
    from app.validation.solution import evaluate_solution

    from fixtures.golden import truth_variant_a

    truth = truth_variant_a()
    suicide = replace(
        truth,
        crime=replace(
            truth.crime,
            murderer_id=None,
            resolution_type=SUICIDE,
            motive_id="robbery_gone_wrong",
        ),
    )
    val = evaluate_solution(_golden_solver_proof(), suicide)
    assert val.murderer_true is True
    assert val.motive_true is False
    assert val.all_true is False


# --------------------------------------------------------------------------- #
# solvers — never crash on empty universe / None murderer (§15)
# --------------------------------------------------------------------------- #


def test_solve_who_empty_suspect_universe_returns_gracefully():
    from app.domain.eligibility import CandidateUniverses
    from app.domain.solvers.when_solver import solve_when
    from app.domain.solvers.who_solver import solve_who

    from fixtures.golden import golden_evidence, golden_public

    public = golden_public()
    when = solve_when(public, golden_evidence())
    empty = CandidateUniverses(
        suspect_ids=(),
        motive_ids=(
            "cover_up_embezzlement",
            "revenge_for_affair",
            "robbery_gone_wrong",
        ),
        weapon_ids=("kitchen_knife", "letter_opener", "scissors"),
    )
    result = solve_who(public, golden_evidence(), when.feasible, empty)
    assert result.unique is False
    assert result.winner is None
    assert result.viable == ()


def test_solve_case_runs_on_payload_without_murderer_semantics():
    """``solve_case`` stays truth-independent: a SUICIDE-shaped public+evidence
    set (no murderer concept anywhere) still solves without raising."""
    from app.domain.solver import solve_case

    from fixtures.golden import golden_evidence, golden_public

    proof = solve_case(golden_public(), golden_evidence())
    assert proof.who is not None
    assert proof.who.unique is True


# --------------------------------------------------------------------------- #
# validation-report classification — SUICIDE skips the WHO-uniqueness gate
# --------------------------------------------------------------------------- #


def _suicide_validation(**overrides):
    from app.validation.solution import AccusedSolutionValidation

    base = dict(
        murderer_true=True,
        motive_true=True,
        weapon_true=True,
        time_unique_single_interval=True,
        time_within_scoring=True,
        canonical_in_feasible=True,
        time_overconstrained=False,
        time_accepted=True,
        all_true=True,
        resolution_type="SUICIDE",
    )
    base.update(overrides)
    return AccusedSolutionValidation(**base)


def test_report_classification_suicide_allows_non_unique_who():
    from app.generation.report import ValidationReport
    from app.generation.state_machine import ValidationOutcome

    proof = _golden_solver_proof()
    nonunique = replace(
        proof,
        who=replace(proof.who, unique=False, winner=None),
    )
    report = ValidationReport(
        solver_result=nonunique,
        validation=_suicide_validation(),
    )
    # SUICIDE: the WHO dimension is a red herring — no REGENERATE.
    assert report.outcome is ValidationOutcome.VALID


def test_report_classification_homicide_still_requires_unique_who():
    from app.generation.report import ValidationReport
    from app.generation.state_machine import ValidationOutcome

    proof = _golden_solver_proof()
    nonunique = replace(
        proof,
        who=replace(proof.who, unique=False, winner=None),
    )
    report = ValidationReport(
        solver_result=nonunique,
        validation=_suicide_validation(resolution_type="HOMICIDE", murderer_true=False),
    )
    assert report.outcome is ValidationOutcome.RECOVERABLE_REGENERATE


# --------------------------------------------------------------------------- #
# FIX-6 (ADV-41-01) — witness-murderer GENERATION boundary + SUICIDE publish
# --------------------------------------------------------------------------- #


def _witness_murderer_public_evidence() -> tuple:
    """A HOMICIDE witness-murderer (hidden killer = public witness) canonical
    public + driver-generated evidence set (reused for the generation-boundary
    and suicide-publish tests)."""
    from app.generation.constraints import LockedConstraints
    from app.generation.provider import GenerationStage
    from app.generation.parser import parse_stage
    from app.services.ollama_driver import OllamaStageDriver, _evidence_gap_facts

    from tests.test_ollama_driver import _case_people, _j

    case_data = _case_people()
    case_data["crime"]["murdererId"] = "lisa_koenig"  # a witness, not a suspect
    case_data["objects"] = []
    pub = {
        k: v
        for k, v in case_data.items()
        if k in ("persons", "motives", "locations", "travelRules", "scene", "objects")
    }
    public = parse_stage(GenerationStage.PUBLIC_WORLD, _j(pub), non_throwing=False)
    crime = parse_stage(
        GenerationStage.CASE_TRUTH, _j({"crime": case_data["crime"]}), non_throwing=False
    )

    class _Attempt:
        locked = LockedConstraints(
            victim="Dr. Anna Weiss",
            murderer="Lisa Koenig",
            motive="stolen research data",
            weapon="bronze ceremonial ice pick",
            crime_time="23:42",
            witness="Lisa König",
        )
        _phase3_cache = None
        _witness_truthfulness = ()

    spec, _rules, _notes = _evidence_gap_facts(_Attempt(), crime, public, None)
    assert spec is not None
    return crime, public, spec


def test_fix6b_witness_murderer_now_the_deduced_winner():
    """(b) PHASE 41A (REQUIREMENTS 31.1.1A / 31.10): the generator deduces
    over the FINAL HOMICIDE TARGET universe (public suspects AND witnesses),
    so a HOMICIDE witness murderer IS the WHO winner: ``solve_case`` yields
    the witness as the deduced killer (every other target evidence-backed
    excluded), ``evaluate_solution.murderer_true`` is True and the whole case
    validates end-to-end — witness murderers are auto-publishable, replacing
    the Phase-41 "not auto-generated by design" boundary (which Phase41A
    Variant B deliberately removed)."""
    from app.environments.manifests import load_environment
    from app.generation.pipeline import _draft_to_phase3
    from app.world.composer import compose_world
    from app.world.requirements import ObjectRequest, WorldRequirements

    crime, public, spec = _witness_murderer_public_evidence()
    from app.generation.constraints import LockedConstraints
    from app.services.ollama_driver import OllamaStageDriver

    class _Attempt:
        locked = LockedConstraints(
            victim="Dr. Anna Weiss",
            murderer="Lisa Koenig",
            motive="stolen research data",
            weapon="bronze ceremonial ice pick",
            crime_time="23:42",
            witness="Lisa König",
        )
        _phase3_cache = None

    world = WorldRequirements(
        environment_hint="office",
        objects=(ObjectRequest(requested_name="bronze ceremonial ice pick", criticality="required"),),
        relations=(),
        unsafe_unsupported=(),
    )
    kit = load_environment("office")
    composition = compose_world(world, environment_id="office", kit=kit)
    driver = OllamaStageDriver(settings=None, provider_factory=None)
    draft = driver._assemble(_Attempt(), crime, public, spec, world, composition)
    p, e, truth = _draft_to_phase3(draft, case_id="boundary-witness", title="t")

    from app.domain.solver import solve_case
    from app.validation.solution import evaluate_solution

    proof = solve_case(p, e)
    # The deduction universe IS the final homicide target universe: the
    # witness murderer is a candidate (and the derived winner, every other
    # target being evidence-backed excluded by the widened opportunity
    # algebra).
    assert "lisa_koenig" in proof.who.universe
    assert proof.who.unique is True
    assert proof.who.winner == "lisa_koenig"
    # HOMICIDE requires the WHO winner == the truth murderer; the witness
    # killer satisfies it -> all_true True -> auto-publishable.
    val = evaluate_solution(proof, truth)
    assert val.resolution_type == "HOMICIDE"
    assert val.murderer_true is True
    assert val.all_true is True
    # The witness murderer keeps public role=witness and never gains the
    # investigative SUSPECT_ELIGIBLE affordance (REQUIREMENTS 31.1.1A).
    from app.domain.eligibility import derive_universes

    universes = derive_universes(p)
    assert "lisa_koenig" in universes.homicide_target_ids
    assert "lisa_koenig" not in universes.suspect_ids
    murderer_person = next(p for p in draft.persons if p.person_id == "lisa_koenig")
    assert murderer_person.role == "witness"
    assert "SUSPECT_ELIGIBLE" not in murderer_person.affordances
    # and the case-quality gate stays clean (no double-reporting).
    from app.generation.case_quality import validate_case_quality

    codes = validate_case_quality(p, truth, e, universes, draft)
    assert codes == (), codes


def test_fix6c_suicide_generation_path_publishes_hermetically():
    """(c) The SUICIDE generation path IS genuinely generator-supported: the
    full driver ``run_into -> assemble -> validate -> publish`` publishes a
    suicide case hermetically with a mocked provider (zero network, zero
    randomness) in exactly the bounded call budget — no regeneration."""
    from app.domain.time_interval import epoch_to_iso, parse_iso8601
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource
    from app.generation.state_machine import GenerationState

    from tests.test_ollama_driver import (
        ICEPICK_SPEC,
        MockOllamaTransport,
        _admission,
        _alog,
        _case_people,
        _controller,
        _evidence,
        _j,
        _make_driver,
        _world,
    )

    def _suicide_people():
        d = _case_people()
        d["crime"]["murdererId"] = None
        d["crime"]["resolutionType"] = "SUICIDE"
        return d

    SUICIDE_PROMPT = (
        "Victim: Dr. Anna Weiss\nMotive: stolen research data\n"
        "Weapon: bronze ceremonial ice pick\nTime: 23:42\n"
        "Witness: Lisa König\nLocation: office\n"
    )
    CANONICAL = "2026-09-11T23:42:00+02:00"
    tick, offset = parse_iso8601(CANONICAL)

    def _iso(delta_seconds):
        return epoch_to_iso(tick + delta_seconds, offset)

    posts = [
        _j(_suicide_people()),
        _j(_evidence()),
        # activity-log anchors (evidence order): when_obs (t-10) + 4 opp (t-120)
        #	— Phase41A: every non-murderer FINAL HOMICIDE TARGET (paul, marcus,
        # sophie AND the witness lisa) gets an impossible-opportunity exclusion.
        _j(_alog(_iso(-10))),
        _j(_alog(_iso(-120))),
        _j(_alog(_iso(-120))),
        _j(_alog(_iso(-120))),
        _j(_alog(_iso(-120))),
        _j(_world()),
        ICEPICK_SPEC,
    ]
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=posts)
    driver = _make_driver(transport)
    controller = _controller(driver, transport, admission, clock, ids)
    handle = controller.start_generation(
        SUICIDE_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    published = record.published.draft
    assert published.crime.resolution_type is SUICIDE
    assert published.crime.murderer_id is None
    assert record.last_validation.valid is True
    assert record.last_validation.validation.all_true is True
    assert record.last_validation.quality_issues == ()
    # exactly the bounded staged calls (case + evidence + when_obs + 4 opp +
    # world + asset = 9) — the suicide case does NOT regenerate.
    assert transport.call_count == 9