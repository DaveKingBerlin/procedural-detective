"""Phase40 — candidate-integrity projection integration (driver + controller).

Proves the Phase-40 guarantees at the driver/controller level:

- §29/§37: the Easy regression shape self-corrects BEFORE evidence — a victim
  who carries ``SUSPECT_ELIGIBLE`` never produces candidate-derived opportunity
  evidence and the case publishes with ZERO repair passes.
- §40/§54: a candidate-integrity-only repair performs ZERO CASE_PEOPLE provider
  calls (the main performance guarantee).
- §39: world graph is reused for a strict affordance-only correction (0 world
  provider calls).
- §38: unaffected per-item activity logs are reused (0 log provider calls).
- §41: when the deterministic projection declines, the canonical CASE_PEOPLE
  provider repair still runs.
- §20: the fallback repair context no longer echoes the invalid candidate set.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.budgets import BudgetTracker  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.constraints import LockedConstraints  # noqa: E402
from app.generation.pipeline import AttemptRecord  # noqa: E402
from app.generation.repair_routing import RepairOwner, RepairPlan  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402

from test_ollama_driver import (  # noqa: E402
    ICEPICK_SPEC,
    MockOllamaTransport,
    PROMPT,
    _alog_posts,
    _case_people,
    _evidence,
    _j,
    _make_driver,
    _run,
    _world,
)

# flake8: noqa

_CRIME_CANONICAL = "2026-09-11T23:42:00+02:00"


def _pass1_posts():
    """The exact 8 posts of one unharmed driver pass (case, evidence, 4 logs,
    world, ASSET_SPEC)."""

    return [
        _j(_case_people()),
        _j(_evidence()),
        *_alog_posts(_CRIME_CANONICAL),
        _j(_world()),
        ICEPICK_SPEC,
    ]


def _direct_attempt(attempt_id: str) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=attempt_id,
        case_id=f"CASE-{attempt_id}",
        session_id=f"QUOTA-{attempt_id}",
        prompt=PROMPT,
        locked=LockedConstraints(
            victim="Dr. Anna Weiss",
            murderer="Paul Becker",
            motive="stolen research data",
            weapon="bronze ceremonial ice pick",
            crime_time="23:42",
            witness="Lisa König",
        ),
        seed=11,
        budget=BudgetTracker(
            ManualClock(),
            deadline_seconds=300,
            max_calls=40,
            max_repairs=2,
            max_regenerations=1,
            max_core_calls=40,
        ),
    )


def _candidate_only_plan() -> RepairPlan:
    return RepairPlan(
        repair_owners=(RepairOwner.CASE_PEOPLE,),
        validator_codes=("VICTIM_IN_SUSPECT_CANDIDATES",),
        rerun_case=True,
        rerun_evidence=True,
        rerun_activity_log=True,
        rerun_world=False,
        recompose=False,
        candidate_integrity_only=True,
    )


def _victim_eligible_case():
    cp = _case_people()
    for person in cp["persons"]:
        if person["personId"] == "anna_weiss":
            person["affordances"] = ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"]
    return _j(cp)


def _murderer_role_victim_case():
    """§41 — the murderer person record itself carries a protected role: the
    deterministic projection cannot honour the closed rules and must DECLINE
    (falling back to the canonical CASE_PEOPLE provider repair)."""
    cp = _case_people()
    for person in cp["persons"]:
        if person["personId"] == "paul_becker":
            person["role"] = "victim"
    return _j(cp)


def _poisoned_public(public):
    """A public whose victim got SUSPECT_ELIGIBLE (invalid candidate universe)."""
    persons = []
    for spec in public.persons:
        if spec.person_id == "anna_weiss":
            persons.append(
                dataclasses.replace(
                    spec, affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER")
                )
            )
        else:
            persons.append(spec)
    return dataclasses.replace(public, persons=tuple(persons))


# --------------------------------------------------------------------------- #
# §29 + §37 — the exact Easy regression: victim eligible self-corrects before
# evidence, publishes with ZERO repair passes, no victim opportunity evidence
# --------------------------------------------------------------------------- #


def test_easy_victim_eligible_case_publishes_with_zero_repairs_and_no_victim_evidence():
    posts = [
        _victim_eligible_case(),
        _j(_evidence()),
        *_alog_posts(_CRIME_CANONICAL),
        _j(_world()),
        ICEPICK_SPEC,
    ]
    record, transport = _run(posts, max_llm_calls_per_generation=12)

    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 0, "the projection fixed it before validation"
    assert transport.call_count == 8, "no CASE_PEOPLE provider repair call"
    case_prompts = [c for c in transport.post_calls if "case_people_v1" in c]
    assert len(case_prompts) == 1, "the case stage must NOT be re-run"

    # §29 expected public persons
    draft = record.draft
    by_id = {p.person_id: p for p in draft.persons}
    assert "SUSPECT_ELIGIBLE" not in by_id["anna_weiss"].affordances, (
        "victim candidate token removed"
    )
    assert "SUSPECT_ELIGIBLE" not in by_id["lisa_koenig"].affordances
    assert "SUSPECT_ELIGIBLE" in by_id["paul_becker"].affordances
    assert "SUSPECT_ELIGIBLE" in by_id["marcus_fischer"].affordances
    assert "SUSPECT_ELIGIBLE" in by_id["sophie_hoffmann"].affordances

    # §37 — no victim opportunity evidence; valid red herrings keep theirs
    evidence_ids = {f.id for f in draft.evidence}
    observed_persons: set[str] = set()
    for fact in draft.evidence:
        for prop in fact.propositions:
            if prop.type == "PERSON_OBSERVED_AT_LOCATION" and prop.person_id:
                observed_persons.add(prop.person_id)
    assert "anna_weiss" not in observed_persons, observed_persons
    assert {"marcus_fischer", "sophie_hoffmann"} <= observed_persons

    # the three candidate-integrity codes are absent from the final validation
    from app.domain.eligibility import derive_universes
    from app.generation import pipeline

    public, evidence, _truth, _draft = pipeline.assemble(record)
    from app.generation.case_quality import (
        MURDERER_NOT_SUSPECT_CANDIDATE,
        VICTIM_IN_SUSPECT_CANDIDATES,
        WITNESS_IN_SUSPECT_CANDIDATES,
        validate_case_quality,
    )

    codes = validate_case_quality(public, _truth, evidence, derive_universes(public))
    assert set(codes) & {
        VICTIM_IN_SUSPECT_CANDIDATES,
        WITNESS_IN_SUSPECT_CANDIDATES,
        MURDERER_NOT_SUSPECT_CANDIDATE,
    } == set()


def _evidence():
    """The model evidence fixture for the poisoned easy case (identical to
    ``test_ollama_driver._evidence`` — reuse is not possible across modules)."""
    from test_ollama_driver import _evidence

    return _evidence()


# --------------------------------------------------------------------------- #
# §38/§39/§40/§54 — candidate-only repair: 0 CASE_PEOPLE + 0 world + 0 logs;
# only the candidate-dependent evidence re-runs
# --------------------------------------------------------------------------- #


def test_candidate_only_repair_makes_zero_case_world_and_log_calls():
    transport = MockOllamaTransport(
        posts=[*_pass1_posts(), _j(_evidence())]
    )
    driver = _make_driver(transport)
    attempt = _direct_attempt("GA-p40-repair-0001")

    driver.run_into(attempt)
    assert transport.call_count == 8  # clean baseline

    aid = str(attempt.attempt_id)
    assert aid in driver._case_outputs
    crime, public = driver._case_outputs[aid]
    driver._case_outputs[aid] = (crime, _poisoned_public(public))

    driver.run_into(attempt, repair_plan=_candidate_only_plan())

    new_calls = transport.post_calls[8:]
    assert len(new_calls) == 1, f"only the evidence rerun expected, got {new_calls}"
    assert "evidence_v1" in new_calls[0]
    # §40 — the headline guarantee
    assert not any("case_people_v1" in p for p in new_calls)
    # §39 — world + composition reused
    assert not any("world_requirements_v1" in p for p in new_calls)
    assert not any("asset_spec_v1" in p for p in new_calls)
    # §38 — unaffected per-item activity logs reused
    assert not any("Canonical evidence time:" in p for p in new_calls)

    # the assembled draft carries the corrected universe
    assert attempt.draft is not None
    by_id = {p.person_id: p for p in attempt.draft.persons}
    assert "SUSPECT_ELIGIBLE" not in by_id["anna_weiss"].affordances
    observed_persons: set[str] = set()
    for fact in attempt.draft.evidence:
        for prop in fact.propositions:
            if prop.type == "PERSON_OBSERVED_AT_LOCATION" and prop.person_id:
                observed_persons.add(prop.person_id)
    assert "anna_weiss" not in observed_persons, observed_persons
    assert {"marcus_fischer", "sophie_hoffmann"} <= observed_persons
    # the world is the cached reusable output (identity proven by zero calls)
    assert aid in driver._world_cache


def test_candidate_only_plan_without_evidence_flag_still_reruns_evidence():
    """§16 defense in depth — the projection changes candidate membership, so
    even a hand-built candidate-only plan that omitted ``rerun_evidence`` gets
    its evidence force-rerun from the corrected universe (never reused from the
    invalid one)."""
    transport = MockOllamaTransport(
        posts=[*_pass1_posts(), _j(_evidence())]
    )
    driver = _make_driver(transport)
    attempt = _direct_attempt("GA-p40-defence-0001")

    driver.run_into(attempt)
    assert transport.call_count == 8
    aid = str(attempt.attempt_id)
    crime, public = driver._case_outputs[aid]
    driver._case_outputs[aid] = (crime, _poisoned_public(public))

    forgetful_plan = RepairPlan(
        repair_owners=(RepairOwner.CASE_PEOPLE,),
        validator_codes=("WITNESS_IN_SUSPECT_CANDIDATES",),
        rerun_case=True,
        rerun_evidence=False,  # hand-built plan forgot the dependency
        rerun_activity_log=False,
        rerun_world=False,
        recompose=False,
        candidate_integrity_only=True,
    )
    driver.run_into(attempt, repair_plan=forgetful_plan)

    new_calls = transport.post_calls[8:]
    assert len(new_calls) == 1, new_calls
    assert "evidence_v1" in new_calls[0], "evidence was force-rerun (§16)"
    assert not any("case_people_v1" in p for p in new_calls)


def test_candidate_only_plan_from_report_keeps_evidence_drops_world():
    """§14/§18 — a VICTIM_IN_SUSPECT_CANDIDATES-only report derives a
    candidate-integrity-only plan: evidence+logs rerun, world/composition do
    NOT force a rerun."""
    from app.generation.report import ValidationReport

    plan = RepairPlan.from_report(
        ValidationReport(quality_issues=("VICTIM_IN_SUSPECT_CANDIDATES",))
    )
    assert plan.candidate_integrity_only is True
    assert plan.repair_owners == (RepairOwner.CASE_PEOPLE,)
    assert plan.rerun_case is True
    assert plan.rerun_evidence is True
    assert plan.rerun_activity_log is True
    assert plan.rerun_world is False
    assert plan.recompose is False
    # A PUBLIC_ROLE_TRUTH_LEAK report is NOT candidate-only (role data itself
    # is broken) — it stays the full CASE_PEOPLE closure.
    leak_plan = RepairPlan.from_report(
        ValidationReport(quality_issues=("PUBLIC_ROLE_TRUTH_LEAK",))
    )
    assert leak_plan.candidate_integrity_only is False
    assert leak_plan.rerun_world is True


# --------------------------------------------------------------------------- #
# §41 — when the projection declines, the canonical CASE_PEOPLE provider repair
# still runs (genuine person defects reach the provider)
# --------------------------------------------------------------------------- #


def test_declined_projection_falls_back_to_case_people_provider_repair():
    """The murderer person record has a protected public role — the projection
    CANNOT fix it deterministically (no role rewrite) and must DECLINE. The
    controller's candidate-only plan then falls back to the canonical
    CASE_PEOPLE provider repair which rewrites the role; the case publishes."""
    posts = [
        _murderer_role_victim_case(),
        _j(_evidence()),
        *_alog_posts(_CRIME_CANONICAL),
        _j(_world()),
        ICEPICK_SPEC,
        # repair pass — the DRIVER falls back to the provider CASE repair.
        _j(_case_people()),
        _j(_evidence()),
        *_alog_posts(_CRIME_CANONICAL),
    ]
    record, transport = _run(posts, max_llm_calls_per_generation=14)

    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 1, "one targeted CASE repair pass"
    case_prompts = [c for c in transport.post_calls if "case_people_v1" in c]
    assert len(case_prompts) == 2, (
        "the CASE_PEOPLE stage must be re-run by the provider fallback"
    )
    roles = {p.person_id: p.role for p in record.draft.persons}
    assert roles["paul_becker"] == "suspect", roles
    assert "murderer" not in set(roles.values())
    assert record.last_validation.valid is True


# --------------------------------------------------------------------------- #
# §20 — the fallback repair context no longer echoes the invalid candidate set
# --------------------------------------------------------------------------- #


def test_repair_context_omits_the_invalid_candidate_set_for_candidate_codes():
    """When the report carries a candidate-integrity code the bounded repair
    context prints the REQUIRED CANDIDATE-INTEGRITY RULES and NEVER echoes the
    current (invalid) suspect candidate ids as the authoritative accuseable
    set (§20)."""
    from app.generation.parser import parse_full_draft  # noqa: PLC0415

    from fixtures.golden_generation import GOLDEN_FULL_DRAFT  # noqa: PLC0415
    from app.generation.pipeline import (  # noqa: PLC0415
        AttemptRecord,
        case_quality_repair_context,
        validate_draft,
    )

    raw = json.loads(GOLDEN_FULL_DRAFT)
    for person in raw["persons"]:
        if person.get("personId") == "sarah_miller":
            person["affordances"] = ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"]
    draft = parse_full_draft(json.dumps(raw))
    assert draft is not None
    attempt = AttemptRecord(
        attempt_id="GA-p40-context", case_id="CASE-p40", session_id="QUOTA-p40"
    )
    attempt.draft = draft
    report = validate_draft(attempt)
    assert "VICTIM_IN_SUSPECT_CANDIDATES" in report.quality_issues

    context = case_quality_repair_context(attempt)
    # the invalid current candidate set is NEVER echoed as authoritative
    assert "accuseable set" not in context
    assert "suspect candidate ids" not in context
    # the closed rules ARE the guidance
    assert "REQUIRED CANDIDATE-INTEGRITY RULES" in context
    assert "victim" in context and "witness" in context

    # control — a PUBLIC_ROLE_TRUTH_LEAK-only report still prints the valid
    # current accuseable set (no candidate-integrity defect present).
    raw2 = json.loads(GOLDEN_FULL_DRAFT)
    for person in raw2["persons"]:
        if person.get("personId") == "michael_carter":
            person["role"] = "murderer"
    draft2 = parse_full_draft(json.dumps(raw2))
    assert draft2 is not None
    attempt2 = AttemptRecord(
        attempt_id="GA-p40-context-2",
        case_id="CASE-p40b",
        session_id="QUOTA-p40b",
    )
    attempt2.draft = draft2
    report2 = validate_draft(attempt2)
    assert "PUBLIC_ROLE_TRUTH_LEAK" in report2.quality_issues
    assert not any(
        c in report2.quality_issues
        for c in (
            "VICTIM_IN_SUSPECT_CANDIDATES",
            "WITNESS_IN_SUSPECT_CANDIDATES",
            "MURDERER_NOT_SUSPECT_CANDIDATE",
        )
    )
    context2 = case_quality_repair_context(attempt2)
    assert "accuseable set" in context2


# --------------------------------------------------------------------------- #
# telemetry — the bounded candidate_integrity.projection event (§43)
# --------------------------------------------------------------------------- #


def test_candidate_integrity_projection_telemetry_is_bounded_and_truthful(caplog):
    from test_ollama_driver import _evidence

    posts = [
        _victim_eligible_case(),
        _j(_evidence()),
        *_alog_posts(_CRIME_CANONICAL),
        _j(_world()),
        ICEPICK_SPEC,
    ]
    with caplog.at_level("INFO", logger="procedural-detective"):
        record, _transport = _run(posts, max_llm_calls_per_generation=12)
    assert record.state is GenerationState.PUBLISHED

    events = [
        event
        for event in caplog.records
        if getattr(event, "pd_event", None) == "candidate_integrity.projection"
    ]
    assert len(events) == 1, "exactly one projection event for the fresh pass"
    fields = getattr(events[0], "pd_fields")
    assert fields["projectionOutcome"] == "CORRECTED"
    assert fields["victimEligibilityRemoved"] is True
    assert fields["witnessEligibilityRemovedCount"] == 0
    assert fields["murdererEligibilityAdded"] is False
    assert fields["candidateCountBefore"] == 4
    assert fields["candidateCountAfter"] == 3
    assert fields["generationAttemptId"] == record.attempt_id
    # never the hidden murderer id / names / raw content
    assert "paul_becker" not in json.dumps(fields)
    assert "Dr. Anna Weiss" not in json.dumps(fields)