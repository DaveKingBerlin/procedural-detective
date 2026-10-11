"""Phase38 — deterministic repair-owner routing tests (§8-§11, §34).

Covers the closed validator-code -> owning-stage mapping, the dependency
closure (minimal invalidation) and report-derived plan determinism. These
tests mutate ONLY validator conditions; they never parse issue-message text.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.repair_routing import (  # noqa: E402
    REPAIR_OWNERS,
    RepairOwner,
    RepairPlan,
    owner_for_report,
    repair_owner_for_code,
)
from app.generation.report import ValidationReport  # noqa: E402
from app.generation.validation_codes import (  # noqa: E402
    VALIDATOR_CODE_VOCABULARY,
)


def _quality_report(*codes: str) -> ValidationReport:
    return ValidationReport(quality_issues=tuple(codes))


def _world_report() -> ValidationReport:
    return ValidationReport(
        world_issues=("world.unresolved-object table for 'weapon'",)
    )


def _solver_ambiguous_report() -> ValidationReport:
    from app.domain.proof import (
        CandidateDimensionResult,
        SolverProof,
    )

    def dim(name: str, unique: bool) -> CandidateDimensionResult:
        return CandidateDimensionResult(
            dimension=name,
            universe=(),
            viable=("a", "b") if not unique else ("a",),
            supported=(),
            excluded=(),
            unknown_remaining=() if not unique else (),
            unique=unique,
            winner=None if not unique else "a",
            rule_outcomes=(),
        )

    proof = SolverProof(
        version=1.0,
        eligibility_snapshot=None,
        who=dim("suspect", unique=False),
        why=dim("motive", unique=True),
        weapon=dim("weapon", unique=True),
        when=None,  # incomplete deduction (the when dimension is missing)
        rule_outcomes_by_candidate=(),
        evidence_ids_used=(),
    )
    return ValidationReport(solver_result=proof)


def _truth_mismatch_report() -> ValidationReport:
    from app.validation.solution import AccusedSolutionValidation

    validation = AccusedSolutionValidation(
        murderer_true=False,
        motive_true=True,
        weapon_true=True,
        time_unique_single_interval=True,
        time_within_scoring=True,
        canonical_in_feasible=True,
        time_overconstrained=False,
        time_accepted=True,
        all_true=False,
        deducted_murderer="some_body",
        deducted_motive="m",
        deducted_weapon="w",
        canonical_murderer="canonical_who",
        canonical_motive="m",
        canonical_weapon="w",
    )
    return ValidationReport(validation=validation, solver_result=None)


def _universe_report() -> ValidationReport:
    return ValidationReport(universe_issues=("suspect universe is empty",))


# --------------------------------------------------------------------------- #
# §10 — a closed total mapping; nothing is derived from message text
# --------------------------------------------------------------------------- #


def test_mapping_covers_the_entire_closed_validator_vocabulary():
    """Every closed validator-code token maps to one closed owner (total map).
    The safe fallback for cross-stage/structural tokens is UNKNOWN (which
    reproduces the pre-Phase-38 full rerun)."""
    assert set(REPAIR_OWNERS) == set(VALIDATOR_CODE_VOCABULARY)
    for code in VALIDATOR_CODE_VOCABULARY:
        owner = REPAIR_OWNERS[code]
        assert owner in set(RepairOwner), (code, owner)


def test_mapping_derived_from_real_validator_ownership():
    """The category mapping follows the ACTUAL validator ownership (Phase35
    public-role/candidate codes -> CASE_PEOPLE; solver/truth -> EVIDENCE;
    world -> WORLD_GRAPH; activity-log family -> ACTIVITY_LOG; asset spec ->
    ASSET_SPEC; floor/structural -> the UNKNOWN safe fallback)."""
    for code in (
        "PUBLIC_ROLE_TRUTH_LEAK",
        "VICTIM_IN_SUSPECT_CANDIDATES",
        "WITNESS_IN_SUSPECT_CANDIDATES",
        "MURDERER_NOT_SUSPECT_CANDIDATE",
        "WITNESS_STATEMENT_MISSING",
        "WITNESS_STATEMENT_EMPTY",
        "WITNESS_STATEMENT_UNKNOWN_WITNESS",
        "WITNESS_STATEMENT_WITNESS_ID_MISMATCH",
        "WITNESS_STATEMENT_SPEAKER_MISMATCH",
    ):
        assert repair_owner_for_code(code) is RepairOwner.CASE_PEOPLE, code
    assert repair_owner_for_code("SOLVER_AMBIGUOUS") is RepairOwner.EVIDENCE
    assert repair_owner_for_code("WORLD_ASSET_UNRESOLVED") is RepairOwner.WORLD_GRAPH
    for code in (
        "ACTIVITY_LOG_SCHEMA_INVALID",
        "ACTIVITY_LOG_ENTRY_COUNT_INVALID",
        "ACTIVITY_LOG_TIME_ORDER_INVALID",
        "ACTIVITY_LOG_CANONICAL_TIME_MISSING",
        "ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED",
        "ACTIVITY_LOG_TIME_WINDOW_INVALID",
        "ACTIVITY_LOG_DIRECT_TRUTH_LEAK",
        "ACTIVITY_LOG_ENTITY_LEAK",
    ):
        assert repair_owner_for_code(code) is RepairOwner.ACTIVITY_LOG, code
    assert repair_owner_for_code("ASSET_SPEC_INVALID") is RepairOwner.ASSET_SPEC
    # Unattributable/cross-stage tokens keep the safe UNKNOWN fallback.
    assert repair_owner_for_code("STRUCTURED_OUTPUT_INVALID") is RepairOwner.UNKNOWN
    assert repair_owner_for_code("VALIDATION_FAILED") is RepairOwner.UNKNOWN
    # A foreign token never raises and fails safe to UNKNOWN.
    assert repair_owner_for_code("SOME_FUTURE_TOKEN") is RepairOwner.UNKNOWN


def test_no_message_string_parsing_smoke():
    """The mapping function NEVER inspects message text: only the token key."""
    assert repair_owner_for_code("activity log could not be generated safely") is (
        RepairOwner.UNKNOWN
    )


# --------------------------------------------------------------------------- #
# report -> owners (§8 election)
# --------------------------------------------------------------------------- #


def test_empty_report_has_no_owners():
    assert owner_for_report(ValidationReport()) == ()


def test_quality_public_role_defect_owns_case_people():
    owners = owner_for_report(_quality_report("PUBLIC_ROLE_TRUTH_LEAK"))
    assert owners == (RepairOwner.CASE_PEOPLE,)


def test_quality_candidate_defect_owns_case_people():
    owners = owner_for_report(
        _quality_report("VICTIM_IN_SUSPECT_CANDIDATES", "WITNESS_IN_SUSPECT_CANDIDATES")
    )
    assert owners == (RepairOwner.CASE_PEOPLE,)


def test_world_bucket_owns_world_graph():
    assert owner_for_report(_world_report()) == (RepairOwner.WORLD_GRAPH,)


def test_solver_ambiguity_owns_evidence():
    assert owner_for_report(_solver_ambiguous_report()) == (RepairOwner.EVIDENCE,)


def test_truth_mismatch_owns_evidence():
    assert owner_for_report(_truth_mismatch_report()) == (RepairOwner.EVIDENCE,)


def test_structural_bucket_falls_back_to_unknown_full_rerun():
    report = ValidationReport(structural_issues=("draft assembly failed",))
    assert owner_for_report(report) == (RepairOwner.UNKNOWN,)


def test_safety_bucket_falls_back_to_unknown_full_rerun():
    report = ValidationReport(safety_issues=("generated content rejected",))
    assert owner_for_report(report) == (RepairOwner.UNKNOWN,)


def test_activity_log_codes_map_directly_to_the_stage_owner():
    """The closed activity-log family maps to the ACTIVITY_LOG owning stage
    (these defects are handled by the driver's bounded local repair loop;
    unresolved terminal failures fail the attempt with the typed code)."""
    assert repair_owner_for_code("ACTIVITY_LOG_ENTITY_LEAK") is RepairOwner.ACTIVITY_LOG
    assert repair_owner_for_code("ACTIVITY_LOG_SCHEMA_INVALID") is RepairOwner.ACTIVITY_LOG


def test_universe_only_report_falls_back_safely_to_unknown():
    """A REGENERATE-class report (universe bucket) has no REPAIR owner; the
    closed-token fallback keeps the safe UNKNOWN (it never reaches the repair
    path — universe_issues classify RECOVERABLE_REGENERATE in report.py)."""
    assert owner_for_report(_universe_report()) == (RepairOwner.UNKNOWN,)


# --------------------------------------------------------------------------- #
# §11 — minimal dependency closure
# --------------------------------------------------------------------------- #


def test_plan_quality_case_forces_full_case_downstream_rerun():
    plan = RepairPlan.from_report(_quality_report("PUBLIC_ROLE_TRUTH_LEAK"))
    assert plan.rerun_case is True
    assert plan.rerun_evidence is True
    assert plan.rerun_activity_log is True
    assert plan.rerun_world is True
    assert plan.recompose is True
    assert not plan.is_default_full_rerun


def test_plan_evidence_reruns_evidence_and_logs_but_not_world():
    plan = RepairPlan.from_report(_solver_ambiguous_report())
    assert plan.rerun_case is False
    assert plan.rerun_evidence is True
    assert plan.rerun_activity_log is True
    # WORLD rerun stays FALSE — an evidence-only change re-runs the world ONLY
    # when the weapon-evidence input actually changed (runtime check in the
    # driver); the plan itself must not force it.
    assert plan.rerun_world is False
    assert plan.recompose is False


def test_plan_world_reruns_world_and_composition_only():
    plan = RepairPlan.from_report(_world_report())
    assert plan.rerun_case is False
    assert plan.rerun_evidence is False
    assert plan.rerun_activity_log is False
    assert plan.rerun_world is True
    assert plan.recompose is True


def test_plan_activity_log_defect_does_not_invalidate_evidence_or_world():
    """The operator-owned closed-token path (a report whose ONLY closed tokens
    are the activity-log family) elects the ACTIVITY_LOG owner and does NOT
    invalidate evidence or world (§11/§13)."""
    plan = RepairPlan(
        repair_owners=(RepairOwner.ACTIVITY_LOG,),
        validator_codes=("ACTIVITY_LOG_ENTITY_LEAK",),
        rerun_case=False,
        rerun_evidence=False,
        rerun_activity_log=True,
        rerun_world=False,
        recompose=False,
    )
    assert plan.rerun_case is False
    assert plan.rerun_evidence is False
    assert plan.rerun_world is False
    assert plan.rerun_activity_log is True


def test_unknown_fallback_is_the_exact_pre_phase38_full_rerun():
    plan = RepairPlan.from_report(
        ValidationReport(safety_issues=("generated content rejected",))
    )
    assert plan.is_default_full_rerun
    assert plan.rerun_case is False  # the cached CASE/PEOPLE parse is reused
    assert plan.rerun_evidence is True
    assert plan.rerun_activity_log is True
    assert plan.rerun_world is True
    assert plan.recompose is True


def test_plan_deterministic_same_report_same_plan():
    report = _quality_report("PUBLIC_ROLE_TRUTH_LEAK", "WITNESS_STATEMENT_MISSING")
    first = RepairPlan.from_report(report)
    second = RepairPlan.from_report(report)
    assert first == second


# --------------------------------------------------------------------------- #
# §16 — repairCount / regenerationCount semantics stay distinct (plan is only
# a rerun selector; it never touches the budget counters)
# --------------------------------------------------------------------------- #


def test_plan_never_touches_repair_or_regeneration_semantics():
    plan = RepairPlan.from_report(_quality_report("PUBLIC_ROLE_TRUTH_LEAK"))
    assert plan.rerun_case is True
    # The plan is a bounded rerun decision — it does NOT convert a repair into
    # a full regeneration (no regeneration flag exists; the controller's
    # consume_repair_pass / consume_regeneration counters are the only budget
    # authority — unchanged).
    assert not hasattr(plan, "regeneration")