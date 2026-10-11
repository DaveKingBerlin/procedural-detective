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