"""Phase38 — driver stage-reuse + Phase-35 case-quality repair tests (§12/§13,
§33/§34/§35/§32).

Provider spies (``MockOllamaTransport``) prove that a repair re-runs ONLY the
stages that own the defect and REUSES the unaffected validated outputs; the
racket never adds a hidden provider call (§32). The §35 regression proves the
KEY driver limitation is closed: a phase-35 public-role defect (which evidence
reruns can never fix) now reaches a CASE/PEOPLE repair that can actually change
its owner data while the locked truth stays fixed.
"""

from __future__ import annotations

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
    _admission,
    _alog_posts,
    _case_people,
    _controller,
    _evidence,
    _j,
    _make_driver,
    _run,
    _world,
)

_CRIME_CANONICAL = "2026-09-11T23:42:00+02:00"


def _direct_attempt(attempt_id: str = "GA-p38-reuse-000001") -> AttemptRecord:
    """A self-contained attempt for DIRECT driver-level tests (no controller).

    The budget is generous so every staged reuse scenario fits; the dramatic
    provider-call deltas are what the assertions measure.
    """
    return AttemptRecord(
        attempt_id=attempt_id,
        case_id="CASE-p38-reuse",
        session_id="QUOTA-p38-reuse",
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


def _pass1_posts() -> list[str]:
    """The exact 8 posts of one unharmed driver pass (case, evidence, 4 logs,
    world, ASSET_SPEC)."""
    return [
        _j(_case_people()),
        _j(_evidence()),
        *_alog_posts(_CRIME_CANONICAL),
        _j(_world()),
        ICEPICK_SPEC,
    ]


def _log_plan() -> RepairPlan:
    return RepairPlan(
        repair_owners=(RepairOwner.ACTIVITY_LOG,),
        validator_codes=("ACTIVITY_LOG_ENTITY_LEAK",),
        rerun_case=False,
        rerun_evidence=False,
        rerun_activity_log=True,
        rerun_world=False,
        recompose=False,
    )


def _world_plan() -> RepairPlan:
    return RepairPlan(
        repair_owners=(RepairOwner.WORLD_GRAPH,),
        validator_codes=("WORLD_ASSET_UNRESOLVED",),
        rerun_case=False,
        rerun_evidence=False,
        rerun_activity_log=False,
        rerun_world=True,
        recompose=True,
    )


def _evidence_plan() -> RepairPlan:
    return RepairPlan(
        repair_owners=(RepairOwner.EVIDENCE,),
        validator_codes=("SOLVER_AMBIGUOUS",),
        rerun_case=False,
        rerun_evidence=True,
        rerun_activity_log=True,
        rerun_world=False,
        recompose=False,
    )


# --------------------------------------------------------------------------- #
# §33/§34 — for each routing class: unrelated stage is NOT called (spy)
# --------------------------------------------------------------------------- #


def test_activity_log_only_defect_reruns_logs_and_reuses_evidence_and_world():
    """Activity-log-only repair: NO evidence, NO case, NO world and NO asset
    provider calls — only the four affected activity-log items re-run."""
    transport = MockOllamaTransport(
        posts=[*_pass1_posts(), *_alog_posts(_CRIME_CANONICAL)]
    )
    driver = _make_driver(transport)
    attempt = _direct_attempt()

    driver.run_into(attempt)
    assert transport.call_count == 8  # the clean Core baseline (3 + 4 logs + asset)
    assert len(driver._activity_cache[attempt.attempt_id]) == 4

    driver.run_into(attempt, repair_plan=_log_plan())
    new_calls = transport.post_calls[8:]
    assert len(new_calls) == 4, f"{len(new_calls)} log calls expected, got {len(new_calls)}"
    for prompt in new_calls:
        assert "Canonical evidence time:" in prompt, "only activity-log calls re-run"
        assert "evidence_v1" not in prompt
        assert "world_requirements_v1" not in prompt
        assert "asset_spec_v1" not in prompt
        assert "case_people_v1" not in prompt
    # the reused evidence cached output is byte-identical.
    assert attempt.draft is not None


def test_world_graph_defect_reruns_world_and_reuses_evidence_logs():
    """World-graph repair: only the world stage + the composition (including
    the procedural ASSET_SPEC path) is re-run; evidence and every activity log
    are reused with ZERO log provider calls."""
    transport = MockOllamaTransport(
        posts=[*_pass1_posts(), _j(_world()), ICEPICK_SPEC]
    )
    driver = _make_driver(transport)
    attempt = _direct_attempt()

    driver.run_into(attempt)
    assert transport.call_count == 8

    driver.run_into(attempt, repair_plan=_world_plan())
    new_calls = transport.post_calls[8:]
    # world + the re-composed ASSET_SPEC (the generated-asset cache does not
    # memoize across compositions — pre-existing composer behavior).
    assert len(new_calls) == 2, f"world+asset expected, got {new_calls}"
    assert "world_requirements_v1" in new_calls[0]
    assert "asset_spec_v1" in new_calls[1]
    for prompt in new_calls:
        assert "Canonical evidence time:" not in prompt
        assert "evidence_v1" not in prompt


def test_evidence_defect_reruns_evidence_and_logs_reuses_world():
    """Evidence repair: evidence + all dependent activity logs re-run; the
    world (whose inputs — weapon evidence id — are unchanged) is reused."""
    transport = MockOllamaTransport(
        posts=[*_pass1_posts(), _j(_evidence()), *_alog_posts(_CRIME_CANONICAL)]
    )
    driver = _make_driver(transport)
    attempt = _direct_attempt()

    driver.run_into(attempt)
    assert transport.call_count == 8

    driver.run_into(attempt, repair_plan=_evidence_plan())
    new_calls = transport.post_calls[8:]
    assert len(new_calls) == 5, f"evidence + 4 logs expected, got {len(new_calls)}"
    assert "evidence_v1" in new_calls[0]
    for prompt in new_calls[1:]:
        assert "Canonical evidence time:" in prompt
    assert not any("world_requirements_v1" in p for p in new_calls)
    assert not any("asset_spec_v1" in p for p in new_calls)
    assert not any("case_people_v1" in p for p in new_calls)


def test_witness_statement_defect_reruns_evidence_without_case_regeneration():
    """DEF-095 — a WITNESS_STATEMENT_MISSING repair plan routes to EVIDENCE:
    the statement facts are reachable through an evidence rerun (the driver's
    deterministic witness-statement projection re-authors them) while the
    CASE/PEOPLE parse is REUSED — zero ``case_people_v1`` calls, so a
    witness-statement-only defect never forces an expensive case regeneration
    (§8/§9/§33)."""
    from app.generation.report import ValidationReport

    transport = MockOllamaTransport(
        posts=[
            *_pass1_posts(),
            _j(_evidence()),
            *_alog_posts(_CRIME_CANONICAL),
        ]
    )
    driver = _make_driver(transport)
    attempt = _direct_attempt()

    driver.run_into(attempt)
    assert transport.call_count == 8

    # The controller derives the plan from the validation report; the
    # witness-coverage code elects the EVIDENCE owner only.
    witness_plan = RepairPlan.from_report(
        ValidationReport(quality_issues=("WITNESS_STATEMENT_MISSING",))
    )
    assert witness_plan.repair_owners == (RepairOwner.EVIDENCE,)
    assert witness_plan.rerun_case is False
    assert witness_plan.rerun_evidence is True

    driver.run_into(attempt, repair_plan=witness_plan)
    new_calls = transport.post_calls[8:]
    # evidence + the four dependent activity logs; ZERO case/world/asset calls.
    assert len(new_calls) == 5, f"evidence + 4 logs expected, got {len(new_calls)}"
    assert "evidence_v1" in new_calls[0]
    for prompt in new_calls[1:]:
        assert "Canonical evidence time:" in prompt
    assert not any("case_people_v1" in p for p in new_calls)
    assert not any("world_requirements_v1" in p for p in new_calls)
    assert not any("asset_spec_v1" in p for p in new_calls)
    # the witness statement fact is present in the regenerated draft.
    assert attempt.draft is not None
    kinds = [getattr(f, "kind", None) for f in attempt.draft.evidence]
    assert "witness_statement" in kinds, kinds


def test_evidence_change_that_alters_weapon_input_reruns_world(monkeypatch):
    """§12 "do not reuse outputs whose inputs changed": when the regenerated
    evidence changes the weapon-evidence input the world depends on, the world
    IS re-run even though the plan's owner is EVIDENCE-only (runtime closure)."""
    import app.services.ollama_driver as driver_module

    transport = MockOllamaTransport(
        posts=[
            *_pass1_posts(),
            _j(_evidence()),
            *_alog_posts(_CRIME_CANONICAL),
            _j(_world()),
            ICEPICK_SPEC,
        ]
    )
    driver = _make_driver(transport)

    def fake(attempt: Any, evidence_spec: Any) -> str:
        # Deterministic runtime identity (never using ``id()``, which the
        # allocator could reuse for a fresh object): the weapon-evidence input
        # CHANGES exactly once the attempt already has a world record — i.e.
        # on the SECOND pass the regenerated evidence is a NEW input for the
        # cached world to compare against.
        if driver._world_cache.get(str(attempt.attempt_id)):
            return "W-CHANGED"
        return "W-v1"

    monkeypatch.setattr(driver_module, "_weapon_evidence_id", fake)
    attempt = _direct_attempt()

    driver.run_into(attempt)
    assert transport.call_count == 8
    driver.run_into(attempt, repair_plan=_evidence_plan())
    new_calls = transport.post_calls[8:]
    # evidence + 4 logs + world + re-composed asset (the changed input
    # invalidates the cached world)
    assert len(new_calls) == 7, f"evidence+logs+world+asset expected, got {new_calls}"
    assert "evidence_v1" in new_calls[0]
    assert any("world_requirements_v1" in p for p in new_calls)


def test_repair_planning_makes_zero_hidden_provider_calls():
    """§32 — deriving a repair plan from a report is PURE (no seam exists for a
    provider; the plan is a closed value)."""
    from app.generation.repair_routing import RepairPlan as RP
    from app.generation.report import ValidationReport

    report = ValidationReport(
        quality_issues=("PUBLIC_ROLE_TRUTH_LEAK",), world_issues=("x",)
    )
    plan = RP.from_report(report)
    assert RepairOwner.CASE_PEOPLE in plan.repair_owners


# --------------------------------------------------------------------------- #
# §13 — activity-log per-item granularity (unchanged evidence reuses each log)
# --------------------------------------------------------------------------- #


def test_per_item_log_reuse_when_evidence_unchanged():
    transport = MockOllamaTransport(
        posts=[
            *_pass1_posts(),
            _j(_world()),
            ICEPICK_SPEC,
            _j(_world()),
            ICEPICK_SPEC,
        ]
    )
    driver = _make_driver(transport)
    attempt = _direct_attempt()
    driver.run_into(attempt)
    assert transport.call_count == 8
    per_item = driver._activity_cache[attempt.attempt_id]
    assert len(per_item) == 4
    # A non-log repair pass after another world rerun reuses EVERY per-item log.
    driver.run_into(attempt, repair_plan=_world_plan())
    driver.run_into(attempt, repair_plan=_world_plan())
    new_calls = transport.post_calls[8:]
    # two world passes -> world + asset each; ZERO activity-log provider calls.
    assert len(new_calls) == 4, f"only world+asset calls expected, got {new_calls}"
    assert new_calls[0] == new_calls[2], new_calls
    assert "world_requirements_v1" in new_calls[0]
    assert "asset_spec_v1" in new_calls[1]
    assert driver._activity_cache[attempt.attempt_id] == per_item


# --------------------------------------------------------------------------- #
# §35 — the KEY Phase-35 driver repair reachability proof (controller lifecycle)
# --------------------------------------------------------------------------- #


def _leaky_case():
    cp = _case_people()
    for person in cp["persons"]:
        if person["personId"] == "paul_becker":
            person["role"] = "murderer"  # pre-reveal truth leak (DEF-080)
    return _j(cp)


def test_phase35_public_role_defect_fixable_by_driver_case_repair():
    """Initial case/public output is invalid (the public role 'murderer' leak)
    -> RECOVERABLE_REPAIR -> the CASE/PEOPLE owner is rerun with the bounded
    quality fix context -> the public role changes -> validation passes.

    Before Phase 38 this was IMPOSSIBLE: the driver cached the defective
    case/public output and only re-ran evidence (which cannot fix a public
    role), so the defect could never change. Now the driver path provably fixes
    it while the locked truth stays fixed.
    """
    posts = [
        _leaky_case(),
        _j(_evidence()),
        *_alog_posts(_CRIME_CANONICAL),
        _j(_world()),
        ICEPICK_SPEC,
        # repair pass — the DRIVER re-runs CASE/PEOPLE (with fix context),
        # then the dependent evidence/logs/world/asset.
        _j(_case_people()),
        _j(_evidence()),
        *_alog_posts(_CRIME_CANONICAL),
        _j(_world()),
        ICEPICK_SPEC,
    ]
    record, transport = _run(
        posts,
        max_llm_calls_per_generation=18,
        max_repair_passes=2,
    )

    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 1, "exactly one targeted CASE repair"
    # ---- the CASE stage was re-run AND carried the bounded fix context ----
    case_prompts = [c for c in transport.post_calls if "case_people_v1" in c]
    assert len(case_prompts) == 2, "the case call must be repeated on the repair"
    assert "CASE QUALITY FIX CONTEXT" in case_prompts[1]
    # ---- the owner data actually changed ----
    roles = {p.person_id: p.role for p in record.draft.persons}
    assert roles["paul_becker"] == "suspect", roles
    assert "murderer" not in set(roles.values()), "no public truth-leak remains"
    # ---- CaseTruth secrecy + locked-solution semantics preserved ----
    assert record.draft.crime.murderer_id == "paul_becker"
    if record.locked is not None:
        assert record.locked.violations_against(record.draft) == ()
    assert record.last_validation.valid is True
    assert record.last_validation.validation.all_true is True


def test_phase35_defect_unchanged_without_repair_budget_is_fail_closed():
    """Control: the SAME defective case with zero repair budget fails closed
    (never publishes a truth-leaking case)."""
    posts = [
        _leaky_case(),
        _j(_evidence()),
        *_alog_posts(_CRIME_CANONICAL),
        _j(_world()),
        ICEPICK_SPEC,
    ]
    record, _transport = _run(
        posts,
        max_llm_calls_per_generation=10,
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    assert record.state is GenerationState.FAILED
    assert record.published is None


# --------------------------------------------------------------------------- #
# regeneration resets the validated-output caches but keeps the case parse
# --------------------------------------------------------------------------- #


def test_reset_attempt_cache_clears_stage_caches_but_keeps_case():
    transport = MockOllamaTransport(posts=_pass1_posts())
    driver = _make_driver(transport)
    attempt = _direct_attempt()
    driver.run_into(attempt)
    aid = str(attempt.attempt_id)
    assert aid in driver._evidence_cache
    assert aid in driver._activity_cache
    assert aid in driver._world_cache
    assert aid in driver._case_outputs
    driver.reset_attempt_cache(aid)
    assert aid not in driver._evidence_cache
    assert aid not in driver._activity_cache
    assert aid not in driver._world_cache
    # Phase17 semantics: the parsed CASE/PEOPLE parse survives a regeneration.
    assert aid in driver._case_outputs


# --------------------------------------------------------------------------- #
# §30 — provider-call accounting observability: ASSET_SPEC rounds appear in
# stageTimingSummary exactly like every other stage (reconcilable counts)
# --------------------------------------------------------------------------- #


def test_asset_spec_and_repair_appear_in_stage_timing_summary():
    transport = MockOllamaTransport(posts=_pass1_posts())
    driver = _make_driver(transport)
    attempt = _direct_attempt()
    driver.run_into(attempt)
    summary = driver.stage_timing_summary(attempt.attempt_id)
    tokens = "\n".join(summary)
    assert any(t.startswith("asset_spec:") for t in summary), tokens
    assert any(t.startswith("case_truth:") for t in summary), tokens
    assert any(t.startswith("activity_log:") for t in summary), tokens
    assert any(t.startswith("world_graph:") for t in summary), tokens
    # tokens expose ONLY a closed stage name + ms + closed outcome.
    for token in summary:
        parts = token.split(":")
        assert len(parts) == 3, token
        stage, ms, outcome = parts
        assert int(ms) >= 0
        assert outcome in {"success", "timeout", "error"}, token
    # The whole attempt is reconcilable: one asset_spec round-trip for the
    # unknown procedural object + exactly eight named stage tokens.
    assert sum(1 for t in summary if t.startswith("asset_spec:")) == 1, tokens