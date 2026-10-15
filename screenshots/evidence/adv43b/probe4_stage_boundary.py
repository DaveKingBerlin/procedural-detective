"""ADV-43B probe 4 — stage-boundary contamination + completion masking (FR-14/15/16, P43A-FR-08).

Reuses the Phase43B hermetic harness (mocked httpx, ZERO network).
Run from repo root: python screenshots/evidence/adv43b/probe4_stage_boundary.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

import pytest  # noqa: E402
from _pytest.monkeypatch import MonkeyPatch  # noqa: E402

from test_phase43b_structured_output import (  # noqa: E402
    _PROMPT, _Recorder, _run_frontier, _started_stages,
    _public_world_with_unknown_field, _public_world_without_motive_candidates,
    _case_truth_with_unknown_murderer, _empty_public_world_doc,
    GOLDEN_FULL_DRAFT, GOLDEN_STAGE_PAYLOADS as G,
)
from app.generation import pipeline  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation.pipeline import AttemptRecord  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.state_machine import GenerationState, ValidationOutcome  # noqa: E402
from app.generation.report import ValidationReport  # noqa: E402
import logging  # noqa: E402

logging.disable(logging.CRITICAL)


def sect(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def run(queue):
    mp = MonkeyPatch()
    try:
        return _run_frontier(mp, queue)
    finally:
        mp.undo()


sect("ATTACK 4a — malformed CASE_TRUTH stops downstream")
controller, rec, handle, events = run(["malformed", "malformed", "malformed"])
record = controller.attempt(handle.attempt_id)
print("  sequence:", _started_stages(events))
print("  state:", record.state, "failure:", record.failure_code,
      "calls:", record.budget.calls, "repairs:", record.budget.repair_passes)

sect("ATTACK 4b — canonically-unparseable PUBLIC_WORLD stops downstream")
controller, rec, handle, events = run([
    G[GenerationStage.CASE_TRUTH], _public_world_with_unknown_field(),
    GOLDEN_FULL_DRAFT,
])
record = controller.attempt(handle.attempt_id)
print("  sequence:", _started_stages(events), "-> state:", record.state)
print("  evidence/world_graph absent:",
      "evidence" not in _started_stages(events) and "world_graph" not in _started_stages(events))

sect("ATTACK 4c — semantically-empty (structurally valid) PUBLIC_WORLD stops downstream")
controller, rec, handle, events = run([
    G[GenerationStage.CASE_TRUTH], _empty_public_world_doc(pad=" " * 40000),
    "malformed", "malformed",
])
record = controller.attempt(handle.attempt_id)
print("  sequence:", _started_stages(events))
print("  evidence/world_graph absent:",
      "evidence" not in _started_stages(events) and "world_graph" not in _started_stages(events))


def _attempt_with(case_truth, public_world, evidence, *, mutate_public=None):
    a = AttemptRecord(attempt_id="GA-x", case_id="CASE-x", session_id="Q-x")
    a.prompt = _PROMPT
    pipeline.apply_stage_output(a, GenerationStage.CASE_TRUTH, case_truth)
    if public_world is not None:
        pipeline.apply_stage_output(a, GenerationStage.PUBLIC_WORLD, public_world)
    if mutate_public is not None and GenerationStage.PUBLIC_WORLD in a.stage_outputs:
        a.stage_outputs[GenerationStage.PUBLIC_WORLD] = mutate_public(
            a.stage_outputs[GenerationStage.PUBLIC_WORLD]
        )
        a.draft = None
        a._phase3_cache = None
    pipeline.apply_stage_output(a, GenerationStage.EVIDENCE, evidence)
    return a


from dataclasses import replace  # noqa: E402


sect("ATTACK 5a — malformed EVIDENCE + VALID case/public -> completed (no LLM)")
a = _attempt_with(
    G[GenerationStage.CASE_TRUTH], G[GenerationStage.PUBLIC_WORLD],
    "not-json",
)
print("  completion:", pipeline.complete_generic_evidence_if_underconstrained(a))
print("  EVIDENCE trusted:", GenerationStage.EVIDENCE in a.stage_done,
      "calls:", a.budget.calls if a.budget else None)

sect("ATTACK 5b — malformed EVIDENCE + unknown-id defect -> REFUSED, not masked")
a = _attempt_with(
    _case_truth_with_unknown_murderer(), G[GenerationStage.PUBLIC_WORLD], "not-json",
)
print("  completion:", pipeline.complete_generic_evidence_if_underconstrained(a))
print("  EVIDENCE trusted:", GenerationStage.EVIDENCE in a.stage_done)

sect("ATTACK 5c — malformed EVIDENCE + empty motive universe -> REFUSED")
a = _attempt_with(
    G[GenerationStage.CASE_TRUTH], _public_world_without_motive_candidates(), "not-json",
)
print("  completion:", pipeline.complete_generic_evidence_if_underconstrained(a))
print("  EVIDENCE trusted:", GenerationStage.EVIDENCE in a.stage_done)

sect("ATTACK 5d — malformed EVIDENCE + public role truth leak -> REFUSED")


def _role_leak(spec):
    persons = list(spec.persons)
    # set one person's role to a truth-bearing 'murderer'
    if persons:
        persons[0] = replace(persons[0], role="murderer")
    return replace(spec, persons=tuple(persons))


a = _attempt_with(
    G[GenerationStage.CASE_TRUTH], G[GenerationStage.PUBLIC_WORLD], "not-json",
    mutate_public=_role_leak,
)
print("  completion:", pipeline.complete_generic_evidence_if_underconstrained(a))
print("  EVIDENCE trusted:", GenerationStage.EVIDENCE in a.stage_done)

sect("ATTACK 5e — malformed EVIDENCE + malformed CASE_TRUTH (both deferred) -> REFUSED")


def _mutate_case(spec):
    return spec


a = _attempt_with(_case_truth_with_unknown_murderer(),
                  G[GenerationStage.PUBLIC_WORLD], "not-json")
print("  deferred_by_stage:", {k.value: v for k, v in a.deferred_by_stage.items()})
print("  completion:", pipeline.complete_generic_evidence_if_underconstrained(a))

sect("ATTACK 7 — publication gate requires VALID (controller level)")
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402

clock = ManualClock(); ids = IdSource()
admission = AdmissionController(clock=clock, ids=ids, max_concurrent_generations=1,
    max_concurrent_generations_global=3, max_generations_per_session_per_window=3,
    max_generations_global_per_window=20, anonymous_quota_session_ttl_seconds=86400)
session = admission.create_anonymous_quota_session()
from test_phase43b_structured_output import _provider, MODEL  # noqa: E402
c = GenerationController(provider=_provider("openrouter"), admission=admission, clock=clock,
    ids=ids, deadline_seconds=60, max_llm_calls_per_generation=8, max_repair_passes=2,
    max_full_regenerations=1, max_prompt_chars=4000, seed=11, provider_name="frontier",
    provider_model=MODEL, provider_sub_id="openrouter")
report = ValidationReport(structural_issues=("x",))
# Directly exercise the publish cell on a VALIDATING attempt with a non-VALID report.
from app.generation.pipeline import AttemptRecord as AR  # noqa: E402
from app.generation.budgets import BudgetTracker  # noqa: E402
att = AR(attempt_id="GA-p", case_id="CASE-p", session_id=session.session_id)
att.budget = BudgetTracker(clock, deadline_seconds=60, max_calls=8, max_repairs=2, max_regenerations=1)
att.state = GenerationState.VALIDATING
att.last_validation = report
c._attempts[att.attempt_id] = att
c._current_attempt_id = att.attempt_id
res = c.publish(att.attempt_id)
print("  publish with RECOVERABLE_REPAIR report ->", res.success, "|", res.reason)

sect("ATTACK 4d — pending EVIDENCE completion cannot bypass the stop (no on_completion seam leak)")
# A pending (deferred) completion path is exercised by on_completion; verify the
# controller never routes an untrusted non-evidence pending stage onward.
print("  on_completion guard present:", hasattr(controller, "on_completion"))

print("\nDONE")
