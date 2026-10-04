"""Phase 26C3 — Provider Call Budget Must Permit a Valid Hard-Case Repair Path.

Backend regression + budget-alignment suite.

Reproduces, deterministically (MockOllamaTransport / canned fixtures, NO real
LLM, NO wall-clock sleeps), the exact live 12→13 failure:

    call 1   case_truth
    call 2   evidence
    call 3   activity_log
    call 4   activity_log
    call 5   activity_log_repair
    call 6   activity_log
    call 7   activity_log_repair
    call 8   activity_log
    call 9   activity_log_repair
    call 10  activity_log
    call 11  activity_log_repair
    call 12  activity_log
    call 13  world_graph   <-- PRE-fix rejected; POST-fix permitted

and pins the new DERIVED global CORE-budget model (Option A):

    parsed_log_facts        = (MAX_CHARACTERS + 1) = 9   (parsed-case algebra)
    model_evidence_log_facts = MAX_EVIDENCE_ITEMS = 50   (raw-evidence fallback)
    max_log_facts           = max(9, 50) = 50 → per pass = 5 + 50 × 3 = 155
    total_passes            = 1 + MAX_REPAIR_PASSES + MAX_FULL_REGENERATIONS = 4
    default                 = 155 × 4 = 620

The 6 live activity logs correspond to 5 eligible suspects; the PARSED-CASE
legal maximum is MAX_CHARACTERS + 1 = 9 (all 8 persons eligible) — the spec's
"6 logs" text was the live-observed count, not the source-derived bound — and
the RAW-MODEL-EVIDENCE fallback (case-stage parse failure) is bounded by
MAX_EVIDENCE_ITEMS = 50 facts, the binding path (adversarial F1a). The
derivation also covers every controller repair/regeneration pass (F1b), and
``Settings`` rejects any override below the derived legal maximum (F3).
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.time_interval import epoch_to_iso, parse_iso8601  # noqa: E402
from app.generation.budgets import (  # noqa: E402
    CORE_BUCKET,
    BudgetTracker,
    derive_core_call_budget_default,
    derive_global_call_budget_default,
)
from app.generation.failure_codes import (  # noqa: E402
    GenerationFailureCode,
    failure_code_for_budget_reason,
)
from app.generation.state_machine import GenerationState  # noqa: E402
from test_ollama_driver import (  # noqa: E402
    PROMPT,
    _admission,
    _alog,
    _case_people,
    _controller,
    _evidence,
    _j,
    _known_world,
    _make_driver,
    MockOllamaTransport,
)

CANONICAL = "2026-09-11T23:42:00+02:00"
KNIFE_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research "
    "data\nWeapon: kitchen knife\nTime: 23:42\nWitness: Lisa König\n"
    "Location: office\n"
)


# --------------------------------------------------------------------------- #
# deterministic fixtures: a 5-eligible-person world yields the live 6-log graph
# --------------------------------------------------------------------------- #


def _case_people_five_eligible(weapon="kitchen_knife"):
    """The live Hard world shape: FIVE SUSPECT_ELIGIBLE persons -> the
    canonical evidence algebra produces when_obs + 4 opp + presence = 6
    time-bearing cctv facts (one bounded ACTIVITY_LOG round-trip each)."""
    people = _case_people(weapon=weapon)
    people["persons"] = list(people["persons"]) + [
        {
            "personId": "david_kim",
            "name": "David Kim",
            "role": "suspect",
            "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"],
        },
        {
            "personId": "emily_reed",
            "name": "Emily Reed",
            "role": "suspect",
            "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"],
        },
    ]
    return people


def _six_log_anchors() -> tuple[str, str, str]:
    """(when_obs, opp, presence) locked instants of the canonical cctv facts."""
    tick, offset = parse_iso8601(CANONICAL)
    return (
        epoch_to_iso(tick - 10, offset),
        epoch_to_iso(tick - 120, offset),
        epoch_to_iso(tick - 20, offset),
    )


def _log_without_canonical(anchor: str) -> str:
    """A parsed-but-invalid log (the canonical instant removed) -> the repair
    loop fires (CANONICAL_TIME_MISSING), matching the live repair pattern.

    The replaced row keeps STRICT chronological order (anchor+90s slots
    between the +0 canonical row and the +180 next row) so the ONLY validator
    code is the canonical-missing bound — never a spurious time-order."""
    doc = _alog(anchor)
    tick, _offset = parse_iso8601(anchor)
    doc["entries"][len(doc["entries"]) // 2]["timestamp"] = epoch_to_iso(tick + 90, _offset)
    return _j(doc)


def _live_posts() -> list[str]:
    """The EXACT live 12→13 response queue (case, evidence, 6 logs with 4
    repairs, world). PRE-fix call 13 (world_graph) is blocked by the 12 cap;
    POST-fix it is permitted and the run publishes."""
    when_obs, opp, presence = _six_log_anchors()
    return [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_alog(when_obs)),                      # log 1 (valid)
        _log_without_canonical(opp),              # log 2 (1 repair)
        _j(_alog(opp)),
        _log_without_canonical(opp),              # log 3 (1 repair)
        _j(_alog(opp)),
        _log_without_canonical(opp),              # log 4 (1 repair)
        _j(_alog(opp)),
        _log_without_canonical(opp),              # log 5 (1 repair)
        _j(_alog(opp)),
        _j(_alog(presence)),                      # log 6 (valid)
        _j(_known_world()),                       # world_graph
    ]


def _run_live(max_core_llm_calls: int, *, posts=None, max_repair_passes=2,
              max_full_regenerations=1):
    """Driver-harness run over the live queue with an explicit CORE cap.

    Global ceiling is the DERIVED legal envelope (``derive_global_call_budget_default``
    = 720) so ONLY the CORE cap can bind — the tests isolate the CORE-budget
    behavior (Phase 26C4: the legacy literal-128 global is gone; the derived
    envelope is the canonical default).
    """
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource

    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=list(posts if posts is not None else _live_posts()))
    driver = _make_driver(transport)
    controller = _controller(
        driver,
        transport,
        admission,
        clock,
        ids,
        max_llm_calls_per_generation=derive_global_call_budget_default(),
        max_core_llm_calls=max_core_llm_calls,
        max_llm_calls_per_procedural_asset=5,
        max_procedural_assets_per_generation=20,
        max_failed_assets_per_generation=3,
        max_repair_passes=max_repair_passes,
        max_full_regenerations=max_full_regenerations,
    )
    handle = controller.start_generation(
        KNIFE_PROMPT, anonymous_quota_session_id=session.session_id
    )
    return controller.attempt(handle.attempt_id), transport


def _stage_of(prompt: str) -> str:
    """Closed human-readable stage tag of a built provider prompt (full-scan;
    the version marker may sit farther than the first 140 chars)."""
    for tag in (
        "case_people_v1",
        "evidence_v1",
        "activity_log_repair_v1",
        "activity_log_v1",
        "world_requirements_v1",
    ):
        if tag in prompt:
            return tag
    return "unknown"


# --------------------------------------------------------------------------- #
# §9 — the exact live 12→13 regression
# --------------------------------------------------------------------------- #


def test_live_12_to_13_pre_fix_world_graph_rejected_core_12():
    """PRE-fix reproduction (the old default 12): the exact 12-call live
    sequence completes, then call 13 (world_graph) is REJECTED with
    CORE_PROVIDER_CALL_BUDGET_EXHAUSTED — exactly the proven failure."""
    record, transport = _run_live(max_core_llm_calls=12)
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert record.budget.calls == 12
    assert record.budget.core_calls == 12
    # the transport made exactly the 12 live calls, no 13th.
    assert transport.call_count == 12
    sequence = [_stage_of(transport.prompt_of_call(i)) for i in range(transport.call_count)]
    assert sequence[:2] == ["case_people_v1", "evidence_v1"]
    log_count = sum(1 for s in sequence if s in ("activity_log_v1", "activity_log_repair_v1"))
    assert log_count == 10  # 6 initial + 4 repairs
    assert "world_requirements_v1" not in sequence


def test_live_12_to_13_post_fix_derived_budget_world_graph_permitted():
    """POST-fix (derived default 32): the SAME exact 12-call sequence then
    proceeds to world_graph (call 13), generation continues and PUBLISHES."""
    derived = derive_core_call_budget_default()
    record, transport = _run_live(max_core_llm_calls=derived)
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    assert record.failure_code is None
    assert transport.call_count == 13
    assert record.budget.calls == 13
    assert record.budget.core_calls == 13
    # call 13 IS world_graph (not privileged; simply now within the budget).
    assert _stage_of(transport.prompt_of_call(12)) == "world_requirements_v1"


def test_live_13_calls_have_budget_headroom_then_fail_closed_at_plus_one():
    """The derived cap is FINITE and fail-closed: the derived number of core
    calls fit, the next one is rejected with the narrow CORE code (wire +
    tracker levels). The global ceiling is set ABOVE the derived cap so ONLY
    the CORE budget can bind."""
    derived = derive_core_call_budget_default()
    budget = BudgetTracker(
        __import__("app.generation.clock", fromlist=["ManualClock"]).ManualClock(),
        deadline_seconds=300,
        max_calls=derived + 1,
        max_repairs=2,
        max_regenerations=1,
        max_core_calls=derived,
    )
    ok = all(budget.consume_core_call() for _ in range(derived))
    assert ok
    assert budget.core_calls == derived
    assert not budget.consume_core_call()
    assert budget.exhausted_reason(CORE_BUCKET) == "core model call budget exhausted"
    assert (
        failure_code_for_budget_reason(budget.exhausted_reason(CORE_BUCKET))
        is GenerationFailureCode.CORE_PROVIDER_CALL_BUDGET_EXHAUSTED
    )


# --------------------------------------------------------------------------- #
# §10 items 1..5 — legal bounded single-pass paths fit
# --------------------------------------------------------------------------- #


def test_normal_hard_path_fits_within_derived_budget():
    """6-log Hard world with NO repairs (6 initial logs + case + evidence +
    world = 9 calls) fits the derived budget and publishes."""
    when_obs, opp, presence = _six_log_anchors()
    posts = [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_alog(when_obs)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(presence)),
        _j(_known_world()),
    ]
    record, transport = _run_live(max_core_llm_calls=derive_core_call_budget_default(), posts=posts)
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 9
    assert record.budget.core_calls == 9
    assert record.budget.core_calls <= derive_core_call_budget_default()


def test_hard_path_with_1_repair_fits():
    """One bounded activity-log repair (10 calls) fits the derived budget."""
    when_obs, opp, presence = _six_log_anchors()
    posts = [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_alog(when_obs)),
        _log_without_canonical(opp),  # log 2 -> repair
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(presence)),
        _j(_known_world()),
    ]
    record, transport = _run_live(max_core_llm_calls=derive_core_call_budget_default(), posts=posts)
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 10
    assert record.budget.core_calls == 10


def test_hard_path_with_3_repairs_fits():
    """Three bounded activity-log repairs (12 calls) fit the derived budget."""
    when_obs, opp, presence = _six_log_anchors()
    posts = [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_alog(when_obs)),
        _log_without_canonical(opp),
        _j(_alog(opp)),
        _log_without_canonical(opp),
        _j(_alog(opp)),
        _log_without_canonical(opp),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(presence)),
        _j(_known_world()),
    ]
    record, transport = _run_live(max_core_llm_calls=derive_core_call_budget_default(), posts=posts)
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 12
    assert record.budget.core_calls == 12


def test_full_legal_per_item_repair_maximum_fits():
    """The FULL legal per-item repair maximum fits: all 6 logs consume their
    ENTIRE MAX_ACTIVITY_LOG_REPAIR_PASSES (2 repairs each) = 18 log calls + 3
    fixed = 21 calls — still under the derived 32."""
    from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES

    when_obs, opp, presence = _six_log_anchors()
    assert MAX_ACTIVITY_LOG_REPAIR_PASSES == 2
    posts = [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
    ]
    for anchor in (when_obs, opp, opp, opp, opp, presence):
        posts += [_log_without_canonical(anchor), _log_without_canonical(anchor), _j(_alog(anchor))]
    posts.append(_j(_known_world()))
    record, transport = _run_live(max_core_llm_calls=derive_core_call_budget_default(), posts=posts)
    assert record.state is GenerationState.PUBLISHED
    # 2 fixed + 6 logs × 3 calls + world = 21 calls.
    assert transport.call_count == 2 + 6 * (1 + MAX_ACTIVITY_LOG_REPAIR_PASSES) + 1 == 21
    assert record.budget.core_calls == 21
    assert record.budget.core_calls <= derive_core_call_budget_default()


def test_derived_budget_covers_both_evidence_paths_and_all_passes():
    """F1a+F1b — the derived budget provably covers the TRUE legal maximum:
    the worst of the parsed-case algebra ((MAX_CHARACTERS+1) facts) and the
    raw-model-evidence fallback (MAX_EVIDENCE_ITEMS facts — the binding path),
    each × (1 + MAX_ACTIVITY_LOG_REPAIR_PASSES), times ALL controller passes
    (1 + max_repair_passes + max_full_regenerations) plus the fixed
    case/evidence/world calls and bounded parse retries."""
    from app.generation.budgets import (
        DEFAULT_MAX_FULL_REGENERATIONS,
        DEFAULT_MAX_REPAIR_PASSES,
    )
    from app.generation.schemas import MAX_CHARACTERS, MAX_EVIDENCE_ITEMS
    from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES

    derived = derive_core_call_budget_default()
    # (a) parsed-case canonical algebra (when_obs + opp per other eligible +
    # presence = eligible+1; eligible capped by MAX_CHARACTERS).
    parsed_log_facts = MAX_CHARACTERS + 1
    # (b) raw-model-evidence fallback (case unparsed): MAX_EVIDENCE_ITEMS raw
    # items can each render as ACTIVITY_LOG (kind is not a closed set).
    model_evidence_log_facts = MAX_EVIDENCE_ITEMS
    assert parsed_log_facts == 9
    assert model_evidence_log_facts == 50
    assert model_evidence_log_facts > parsed_log_facts  # the binding path
    per_item = 1 + MAX_ACTIVITY_LOG_REPAIR_PASSES  # initial + bounded repairs
    assert per_item == 3
    # each single path fits INSIDE the derived whole-attempt maximum ...
    for fact_count in (parsed_log_facts, model_evidence_log_facts):
        single_pass = 5 + fact_count * per_item  # fixed(3)+retry(2)+logs
        assert single_pass <= derived
    # ... and the exact whole-attempt accounting is pinned (F1b).
    total_passes = 1 + DEFAULT_MAX_REPAIR_PASSES + DEFAULT_MAX_FULL_REGENERATIONS
    assert total_passes == 4
    per_pass = 5 + model_evidence_log_facts * per_item
    assert per_pass == 155
    assert derived == total_passes * per_pass == 620
    # Phase 26C4 derives the GLOBAL envelope separately so it contains this
    # complete 620-call CORE maximum plus the legal procedural-asset envelope
    # (20 assets x 5 calls = 100): MAX_LLM_CALLS_PER_GENERATION resolves to
    # 720. The global guard therefore remains finite and hard without stopping
    # a path permitted by the bounded CORE and asset policies.
    assert isinstance(derived, int) and derived > 0


# --------------------------------------------------------------------------- #
# F1a — the RAW-MODEL-EVIDENCE fallback path (case-stage strict-parse failure)
# --------------------------------------------------------------------------- #


def _raw_evidence_many_cctv(n):
    """A strict-parser-VALID raw evidence set with ``n`` ACTIVITY_LOG-rendering
    (cctv) facts, each carrying a time-bearing observed_at. The parser bounds
    the raw set at MAX_EVIDENCE_ITEMS and never restricts ``kind`` to a closed
    set — this is exactly the F1a fallback surface."""
    from test_ollama_driver import _fact, _p

    return {
        "evidence": [
            _fact(
                f"raw_cctv_{index:02d}",
                "cctv",
                [
                    _p(
                        "PERSON_OBSERVED_AT_LOCATION",
                        personId="paul_becker",
                        locationId="konsortium_office",
                        observedAt="2026-09-11T23:41:50+02:00",
                        uncertaintySeconds=60,
                    )
                ],
            )
            for index in range(n)
        ]
    }


def _raw_fallback_posts(n):
    """The F1a raw-fallback post queue: case stage fails its strict parse
    twice -> raw evidence with ``n`` cctv facts parses -> each fact spends its
    FULL per-item allowance (initial invalid, repair 1 invalid, repair 2
    VALID -> the item terminates successfully after exactly 1 +
    MAX_ACTIVITY_LOG_REPAIR_PASSES calls) -> world fails its strict parse
    twice. Total = 2 + 1 + n×3 + 2 calls, every one of them LEGAL."""
    from test_ollama_driver import _alog

    anchor = "2026-09-11T23:41:50+02:00"
    per_item = ["<not-json>", "<not-json>", _j(_alog(anchor))]
    return [
        "<not-json>",  # case_truth initial (strict-parse failure)
        "<not-json>",  # case_truth bounded retry (still fails -> raw path)
        _j(_raw_evidence_many_cctv(n)),  # evidence (strict parse OK)
        *[p for _ in range(n) for p in per_item],
        "<not-json>",  # world_graph initial
        "<not-json>",  # world_graph bounded retry
    ]


def test_f1a_raw_model_evidence_fallback_not_blocked_by_core_budget():
    """F1a (end-to-end): when the CASE stage fails to strictly parse,
    ``_evidence_gap_facts`` returns ``(None, (), ())`` and the driver keeps the
    RAW model evidence — up to MAX_EVIDENCE_ITEMS ACTIVITY_LOG-rendered facts
    each legitimately burning (1 + MAX_ACTIVITY_LOG_REPAIR_PASSES) calls. The
    OLD cap (32) would fail closed at call 33 mid-log; the DERIVED cap permits
    the whole raw path (the run then terminates through the lower-level repair
    budget, NEVER through the CORE budget)."""
    from app.generation.schemas import MAX_EVIDENCE_ITEMS
    from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES

    n = 11  # 11 facts × 3 worst-case log calls = 33 log calls > the old cap 32
    assert n < MAX_EVIDENCE_ITEMS  # strict parser accepts the raw set
    posts = _raw_fallback_posts(n)
    expected = 2 + 1 + n * (1 + MAX_ACTIVITY_LOG_REPAIR_PASSES) + 2
    assert expected == 38
    # no repair/regeneration: after the first run_into the incomplete draft is
    # a RECOVERABLE_REPAIR and the repair budget (0) fails the attempt — the
    # CORE budget must NOT have stopped it earlier.
    record, transport = _run_live(
        max_core_llm_calls=derive_core_call_budget_default(),
        posts=posts,
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    assert record.state is GenerationState.FAILED
    assert transport.call_count == expected == 38
    assert record.budget.core_calls == 38
    # the run consumed EVERY legal call (38 > the old 32 cap); it was stopped
    # by the LOWER-LEVEL repair budget — never by the CORE budget.
    assert record.failure_code != "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert record.failure_code == "REPAIR_BUDGET_EXHAUSTED"
    assert record.budget.core_calls <= derive_core_call_budget_default()


def test_f1a_old_fixed_32_would_have_blocked_the_raw_fallback_path():
    """F1a (counterfactual pinned): the OLD derived cap (32, single-pass-only,
    parsed-case-only) DOES block the same raw fallback path at call 32/33 with
    CORE_PROVIDER_CALL_BUDGET_EXHAUSTED — exactly the F1a defect. With the new
    whole-attempt two-path derivation the SAME post queue is not CORE-blocked
    (proven by ``test_f1a_raw_model_evidence_fallback_not_blocked_by_core_budget``)."""
    posts = _raw_fallback_posts(11)
    record, transport = _run_live(
        max_core_llm_calls=32,
        posts=posts,
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert record.budget.core_calls == 32
    assert transport.call_count == 32  # blocked BEFORE the 33rd (a log call)


# --------------------------------------------------------------------------- #
# F1b — controller repair/regeneration passes re-burn against the SAME budget
# --------------------------------------------------------------------------- #


def test_f1b_repair_pass_reruns_logs_inside_derived_budget():
    """F1b (end-to-end): a second controller pass (RECOVERABLE_REPAIR — the
    world stage failed the FIRST strict parse) re-invokes ``run_into`` and
    re-burns evidence + the 6 activity-log round-trips + world against the SAME
    per-attempt CORE counter; the case output is CACHED (0 extra case calls).
    The whole 2-pass chain fits the derived budget and publishes."""
    when_obs, opp, presence = _six_log_anchors()
    posts = [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_alog(when_obs)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(presence)),
        "<not-json>",  # pass-1 world (strict-parse failure -> repair)
        "<not-json>",  # pass-1 world bounded retry (still fails)
        # ---- repair pass (case cached; evidence + logs + world re-burn) ----
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_alog(when_obs)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(presence)),
        _j(_known_world()),
    ]
    record, transport = _run_live(
        max_core_llm_calls=derive_core_call_budget_default(), posts=posts
    )
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 1
    # pass 1: case + evidence + 6 logs + world×2 = 10; pass 2: evidence + 6
    # logs + world = 8 (the case was CACHED) → 18 CORE calls in one attempt.
    assert transport.call_count == 18
    assert record.budget.core_calls == 18
    assert record.budget.core_calls <= derive_core_call_budget_default()


def test_f1b_derived_budget_accounts_every_controller_pass():
    """F1b (accounting): the derived cap is EXACTLY the legal whole-attempt
    maximum — ``total_passes = 1 + MAX_REPAIR_PASSES + MAX_FULL_REGENERATIONS``
    full worst-case passes — and a BudgetTracker can spend the entire first
    pass AND still fit every remaining pass against the SAME monotonic CORE
    counter (repair/regeneration re-run evidence + logs + world; the case is
    cached only after a parsed first pass, so the worst case re-burns it too)."""
    from app.generation.budgets import (
        DEFAULT_MAX_FULL_REGENERATIONS,
        DEFAULT_MAX_REPAIR_PASSES,
    )
    from app.generation.clock import ManualClock
    from app.generation.schemas import MAX_EVIDENCE_ITEMS
    from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES

    total_passes = 1 + DEFAULT_MAX_REPAIR_PASSES + DEFAULT_MAX_FULL_REGENERATIONS
    per_pass = 5 + MAX_EVIDENCE_ITEMS * (1 + MAX_ACTIVITY_LOG_REPAIR_PASSES)
    derived = derive_core_call_budget_default()
    assert total_passes == 4
    assert per_pass == 155
    assert derived == total_passes * per_pass == 620

    budget = BudgetTracker(
        ManualClock(),
        deadline_seconds=300,
        max_calls=derived + 4,
        max_repairs=DEFAULT_MAX_REPAIR_PASSES,
        max_regenerations=DEFAULT_MAX_FULL_REGENERATIONS,
        max_core_calls=derived,
    )
    # the first THREE worst-case passes fully consume their allowance ...
    for _ in range(total_passes - 1):
        assert all(budget.consume_core_call() for _ in range(per_pass))
    # ... and the LAST pass still has its FULL allowance on the same counter.
    assert budget.remaining_core_calls() == per_pass
    assert all(budget.consume_core_call() for _ in range(per_pass))
    assert budget.remaining_core_calls() == 0
    assert not budget.consume_core_call()  # fail-closed at derived + 1


# --------------------------------------------------------------------------- #
# F3 — a below-legal override is a REJECTED configuration error (fail-fast)
# --------------------------------------------------------------------------- #


def test_f3_below_legal_override_rejected_and_at_above_accepted():
    """F3 — ``max_core_llm_calls_per_generation`` rejects a configured value
    BELOW the derived legal maximum with a configuration error (fail-fast),
    and accepts an at/above override. The default (unset) auto-fills with the
    derived value (init > env > dotenv > derived-default precedence)."""
    import pytest as _pytest

    from app.core.config import Settings

    derived = derive_core_call_budget_default()
    assert derived == 620

    # 12 (legacy default) and derived-1 are REJECTED as configuration errors.
    for below in (12, derived - 1):
        with _pytest.raises(Exception):
            Settings(max_core_llm_calls_per_generation=below)

    # the derived value itself and any higher override are ACCEPTED.
    assert (
        Settings(max_core_llm_calls_per_generation=derived)
        .max_core_llm_calls_per_generation
        == derived
    )
    assert (
        Settings(max_core_llm_calls_per_generation=derived + 10)
        .max_core_llm_calls_per_generation
        == derived + 10
    )

    # unset -> the derived default auto-fills (and follows the configured
    # repair/regeneration allowances, F1b).
    assert Settings().max_core_llm_calls_per_generation == derived
    higher = derive_core_call_budget_default(max_full_regenerations=3)
    assert (
        Settings(max_full_regenerations=3).max_core_llm_calls_per_generation
        == higher
        == 930
    )
    # raising repair/regeneration grows the required minimum: an override that
    # was legal before now FAILS fast instead of silently re-introducing the
    # contradiction.
    with _pytest.raises(Exception):
        Settings(max_full_regenerations=3, max_core_llm_calls_per_generation=derived)


def test_f3_env_override_precedence_kept_and_validated():
    """F3 — the env override stays authoritative (init > env) and is still
    validated: a below-legal MAX_CORE_LLM_CALLS_PER_GENERATION env variable is
    rejected, an at/above one wins over the dotenv/default."""
    import os

    from app.core.config import Settings

    derived = derive_core_call_budget_default()
    try:
        os.environ["MAX_CORE_LLM_CALLS_PER_GENERATION"] = str(derived + 5)
        assert Settings().max_core_llm_calls_per_generation == derived + 5
        # construction keyword still wins over the env var (init > env).
        assert (
            Settings(max_core_llm_calls_per_generation=derived + 1)
            .max_core_llm_calls_per_generation
            == derived + 1
        )
        with pytest.raises(Exception):
            Settings(max_core_llm_calls_per_generation=12)
    finally:
        os.environ.pop("MAX_CORE_LLM_CALLS_PER_GENERATION", None)


# --------------------------------------------------------------------------- #
# §10 items 6..8 — Easy / Medium bounded; one-call-beyond rejected
# --------------------------------------------------------------------------- #


def test_easy_budget_remains_bounded_and_fits():
    """Easy-class path (standard 4-log staged world) fits the same derived
    budget; Easy/Medium/Hard do NOT receive different budgets — the cap is
    per-attempt-global and finite."""
    derived = derive_core_call_budget_default()
    assert isinstance(derived, int) and derived > 0
    record, transport = _run_live(max_core_llm_calls=derived)
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.core_calls <= derived


def test_medium_budget_remains_bounded_and_fits():
    """Medium-class path (the C2 canonical fixture set) fits the derived cap."""
    from test_phase26_c2_hardcase_repair import _run_chain

    record, _transport = _run_chain(normal_entries=21, repair1_entries=18)
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.core_calls <= derive_core_call_budget_default()


def test_one_call_beyond_legal_maximum_rejected():
    """Item 6: one call beyond the derived legal maximum is rejected and the
    attempt fails closed with the narrow CORE code (tracker + budget reason)."""
    derived = derive_core_call_budget_default()
    budget = BudgetTracker(
        __import__("app.generation.clock", fromlist=["ManualClock"]).ManualClock(),
        deadline_seconds=300,
        max_calls=derived + 1,
        max_repairs=2,
        max_regenerations=1,
        max_core_calls=derived,
    )
    assert all(budget.consume_core_call() for _ in range(derived))
    assert budget.remaining_core_calls() == 0
    assert not budget.consume_core_call()
    reason = budget.exhausted_reason(CORE_BUCKET)
    assert reason == "core model call budget exhausted"
    assert (
        failure_code_for_budget_reason(reason)
        is GenerationFailureCode.CORE_PROVIDER_CALL_BUDGET_EXHAUSTED
    )


# --------------------------------------------------------------------------- #
# §10 items 9..10 — failures count according to existing semantics
# --------------------------------------------------------------------------- #


def test_failed_calls_count_against_budget():
    """A TIMED-OUT provider call that was granted a reservation still COUNTS
    (monotonic, one-way) — the attempt fails with PROVIDER_TIMEOUT and the
    consumed call is never refunded."""
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource

    class _FailOnWorld(MockOllamaTransport):
        """Mimic a provider-level failure (timeout) on the FIRST world call."""

        def __init__(self, posts):
            super().__init__(posts=posts)
            self._failed = False

        def post_json(self, url, payload, timeout):
            prompt = payload.get("messages", [{}])[0].get("content", "")
            if "world_requirements_v1" in prompt and not self._failed:
                self._failed = True
                raise TimeoutError("ollama request timed out")
            return super().post_json(url, payload, timeout)

    when_obs, opp, presence = _six_log_anchors()
    posts = [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_alog(when_obs)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(presence)),
        _j(_known_world()),
    ]
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = _FailOnWorld(posts=posts)
    driver = _make_driver(transport)
    controller = _controller(
        driver, transport, admission, clock, ids,
        max_llm_calls_per_generation=derive_global_call_budget_default(), max_core_llm_calls=derive_core_call_budget_default(),
    )
    handle = controller.start_generation(KNIFE_PROMPT, anonymous_quota_session_id=session.session_id)
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "PROVIDER_TIMEOUT"
    # case + evidence + 6 logs = 8 reservations BEFORE the world call; the
    # world call's own reservation (the 9th) is granted BEFORE the provider
    # invoke, so it COUNTS even though the call times out (monotonic, no
    # refund).
    assert record.budget.core_calls == 9
    assert record.budget.calls == 9


def test_parse_failed_calls_count_against_budget():
    """A STRICT-PARSE-FAILED stage response still consumed its provider call;
    the driver's bounded parse retry is a second REAL call. Both count."""
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource

    when_obs, opp, presence = _six_log_anchors()
    # world response is not-json -> strict parse fails -> bounded retry fires.
    posts = [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_alog(when_obs)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(opp)),
        _j(_alog(presence)),
        "<not-json>",   # world initial (parse failure)
        "<not-json>",   # world bounded retry
        _j(_known_world()),
    ]
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=posts)
    driver = _make_driver(transport)
    controller = _controller(
        driver, transport, admission, clock, ids,
        max_llm_calls_per_generation=derive_global_call_budget_default(), max_core_llm_calls=derive_core_call_budget_default(),
        max_repair_passes=2,
    )
    handle = controller.start_generation(KNIFE_PROMPT, anonymous_quota_session_id=session.session_id)
    record = controller.attempt(handle.attempt_id)
    # 8 pre-world + 2 world (initial + bounded retry) = 10 consumed calls; the
    # run then continues (repair or publish) without refunding anything.
    assert record.budget.core_calls >= 10
    assert record.budget.calls >= 10


# --------------------------------------------------------------------------- #
# §10 items 11..14 — repairs/regeneration/attempt/concurrency isolation
# --------------------------------------------------------------------------- #


def test_activity_log_repairs_remain_bounded_independently():
    """The driver-local activity-log repair loop uses its OWN bounded counter
    (MAX_ACTIVITY_LOG_REPAIR_PASSES) and never touches the controller's
    global repair-pass budget (repairCount stays 0 for stage repairs)."""
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource
    from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES

    when_obs, _opp, _presence = _six_log_anchors()
    bad = _log_without_canonical(when_obs)
    posts = [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        bad,  # ACTIVITY_LOG (invalid)
        bad,  # repair 1 (invalid)
        bad,  # repair 2 (invalid -> terminal; never a 4th)
    ]
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=posts)
    driver = _make_driver(transport)
    controller = _controller(
        driver, transport, admission, clock, ids,
        max_llm_calls_per_generation=derive_global_call_budget_default(), max_core_llm_calls=derive_core_call_budget_default(),
        max_repair_passes=2, max_full_regenerations=1,
    )
    handle = controller.start_generation(KNIFE_PROMPT, anonymous_quota_session_id=session.session_id)
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_CANONICAL_TIME_MISSING"
    # exactly the bound + 1 calls; never a 4th.
    log_calls = sum(
        1
        for i in range(transport.call_count)
        if "activity_log" in transport.prompt_of_call(i)[:80]
    )
    assert log_calls == MAX_ACTIVITY_LOG_REPAIR_PASSES + 1 == 3
    # the driver-local repair loop did NOT consume the controller repair-pass
    # budget (separate-stage accounting, Phase19J-RI).
    assert record.budget.repair_passes == 0


def test_regeneration_remains_bounded_independently():
    """REGENERATION is bounded by its OWN counter (max_full_regenerations),
    independent of the call budget: a BudgetTracker that already fully used
    its regeneration allowance cannot regenerate again."""
    budget = BudgetTracker(
        __import__("app.generation.clock", fromlist=["ManualClock"]).ManualClock(),
        deadline_seconds=300,
        max_calls=128,
        max_repairs=2,
        max_regenerations=1,
        max_core_calls=derive_core_call_budget_default(),
    )
    assert budget.consume_regeneration()
    assert not budget.consume_regeneration()
    # regeneration allowance is independent of the remaining core calls.
    assert budget.remaining_core_calls() == derive_core_call_budget_default()


def test_budget_resets_per_generation_attempt():
    """A fresh attempt always receives a FRESH BudgetTracker (counters never
    leak across attempts on the same controller)."""
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource

    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=_live_posts())
    driver = _make_driver(transport)
    controller = _controller(
        driver, transport, admission, clock, ids,
        max_llm_calls_per_generation=derive_global_call_budget_default(), max_core_llm_calls=derive_core_call_budget_default(),
    )
    handle1 = controller.start_generation(KNIFE_PROMPT, anonymous_quota_session_id=session.session_id)
    record1 = controller.attempt(handle1.attempt_id)
    assert record1.state is GenerationState.PUBLISHED
    assert record1.budget.calls == 13

    transport2 = MockOllamaTransport(posts=_live_posts())
    driver2 = _make_driver(transport2)
    controller2 = _controller(
        driver2, transport2, admission, clock, ids,
        max_llm_calls_per_generation=derive_global_call_budget_default(), max_core_llm_calls=derive_core_call_budget_default(),
    )
    handle2 = controller2.start_generation(KNIFE_PROMPT, anonymous_quota_session_id=session.session_id)
    record2 = controller2.attempt(handle2.attempt_id)
    # the second controller's first attempt starts fresh at 0 calls.
    assert record2.state is GenerationState.PUBLISHED
    assert record2.budget.calls == 13


def test_concurrent_attempts_do_not_share_counters():
    """Two FULL live 12→13 chains run SIMULTANEOUSLY (barrier-synchronized
    threads, no sleeps): each attempt keeps its OWN budget counters."""
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _run_in_thread(key):
        barrier.wait()
        try:
            results[key] = _run_live(max_core_llm_calls=derive_core_call_budget_default())
        except Exception as exc:  # pragma: no cover - surfaced below
            results[key] = exc

    threads = [
        threading.Thread(target=_run_in_thread, args=("A",)),
        threading.Thread(target=_run_in_thread, args=("B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for key in ("A", "B"):
        result = results[key]
        assert not isinstance(result, Exception), result
        record, _transport = result
        assert record.state is GenerationState.PUBLISHED, key
        assert record.budget.calls == 13, key


# --------------------------------------------------------------------------- #
# §10 items 15..16 — bridge/direct parity + fake/frontier unaffected
# --------------------------------------------------------------------------- #


def test_bridge_and_direct_use_identical_global_accounting(database_url):
    """Bridge (remote_client) and Direct (ollama driver) consume the SAME
    hierarchical BudgetTracker through the same driver path. The bridge run and
    the direct canonical-equivalent reach IDENTICAL snapshot counters."""
    from conftest import upgrade_db as _upgrade_db
    from app.persistence.store import Store as _Store
    from app.services.generation import GenerationService as _GenerationService

    from bridge_harness import make_bridge_settings, make_scripted_bridge

    _upgrade_db(database_url)
    bridge_url = database_url
    settings = make_bridge_settings(
        bridge_url,
        max_core_llm_calls_per_generation=derive_core_call_budget_default(),
        # Phase 26C4 — the GLOBAL ceiling must be the DERIVED legal envelope;
        # the legacy literal 128 would now be a REJECTED configuration error.
        max_llm_calls_per_generation=derive_global_call_budget_default(),
    )
    store = _Store(bridge_url)
    from app.services.bridge import BridgeRegistry

    registry = BridgeRegistry(settings=settings, store=store)
    service = _GenerationService(settings=settings, store=store, bridge_registry=registry)
    session = service.create_anonymous_quota_session()

    from app.domain.time_interval import epoch_to_iso as _iso, parse_iso8601 as _p8

    tick, _off = _p8(CANONICAL)
    anchors = (
        _iso(tick - 10, _off),
        _iso(tick - 120, _off),
        _iso(tick - 120, _off),
        _iso(tick - 120, _off),
        _iso(tick - 120, _off),
        _iso(tick - 20, _off),
    )
    cassette = [
        _case_people_five_eligible(),
        _evidence(weapon_obj="kitchen_knife", murderer="paul_becker"),
        *[_alog(anchor) for anchor in anchors],
        _known_world(),
    ]
    import json as _json

    conn, sock, loop_thread = make_scripted_bridge(
        registry,
        session_scope=session.anonymous_quota_session_id,
        model="hermes3:8b",
        outputs=cassette,
        settings=settings,
    )
    try:
        handle, record, _now = service._run_generation(
            KNIFE_PROMPT,
            anonymous_quota_session_id=session.anonymous_quota_session_id,
            creator_token=None,
        )
        assert record.state is GenerationState.PUBLISHED
        from app.services.generation import GenerationState as _GS
        assert record.state.value == "PUBLISHED"
        bridge_snapshot = record.budget.snapshot()
    finally:
        loop_thread.close()
        store.dispose()

    # Direct path: the SAME derived cap + the SAME 6 valid activity logs
    # (2 fixed + 6 logs + world = 9 calls — a canonical-equivalent run).
    valid_posts = [
        _j(_case_people_five_eligible()),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *[_j(_alog(anchor)) for anchor in anchors],
        _j(_known_world()),
    ]
    direct_record, _transport = _run_live(
        max_core_llm_calls=derive_core_call_budget_default(), posts=valid_posts
    )
    assert direct_record.state is GenerationState.PUBLISHED
    assert direct_record.budget.snapshot() == bridge_snapshot
    assert bridge_snapshot["globalCallCount"] == 9
    assert bridge_snapshot["coreCallCount"] == 9
    assert bridge_snapshot["assetCallCount"] == 0


def test_fake_provider_behavior_unchanged():
    """The deterministic FakeProvider (demo mode) publishes with exact
    core-only accounting under the derived CORE cap — no new provider-calling
    behavior, no bridge probe."""
    from fixtures.golden_generation import (
        GOLDEN_FULL_DRAFT,
        GOLDEN_STAGE_PAYLOADS,
    )
    from app.generation.clock import ManualClock
    from app.generation.controller import GenerationController
    from app.generation.fake_provider import FakeProvider
    from app.generation.ids import IdSource

    script = {stage: [payload] for stage, payload in GOLDEN_STAGE_PAYLOADS.items()}
    script[__import__("app.generation.provider", fromlist=["GenerationStage"]).GenerationStage.REPAIR] = [
        GOLDEN_FULL_DRAFT
    ]
    fake = FakeProvider(script)
    clock, ids = ManualClock(), IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=fake, admission=admission, clock=clock, ids=ids,
        deadline_seconds=60, max_llm_calls_per_generation=derive_global_call_budget_default(),
        max_core_llm_calls=derive_core_call_budget_default(),
        max_repair_passes=2, max_full_regenerations=1, max_prompt_chars=4000,
        seed=11,
    )
    handle = controller.start_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
        "Weapon: kitchen_knife\nTime: 22:17\nWitness: emily_reed\n",
        anonymous_quota_session_id=session.session_id,
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    budget = record.budget
    assert budget.calls == budget.core_calls + budget.asset_calls
    assert budget.asset_calls == 0
    assert budget.core_calls <= derive_core_call_budget_default()
    assert budget.core_calls >= 4


# --------------------------------------------------------------------------- #
# §14 — observability
# --------------------------------------------------------------------------- #


def test_provider_call_budget_safe_field_on_lifecycle_events(caplog):
    """generation.started / failed / published carry the SAFE configured
    integer cap (providerCallBudget) and only that number — never topology."""
    from app.core.observability import _SAFE_FIELDS

    assert "providerCallBudget" in _SAFE_FIELDS
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, _transport = _run_live(max_core_llm_calls=derive_core_call_budget_default())
    assert record.state is GenerationState.PUBLISHED

    by_name = {}
    for event in caplog.records:
        ev = getattr(event, "pd_event", None)
        fields = dict(getattr(event, "pd_fields", {}) or {})
        if ev in ("generation.started", "generation.published", "generation.failed"):
            by_name[ev] = fields
    assert "generation.started" in by_name
    assert "generation.published" in by_name
    for name in ("generation.started", "generation.published"):
        assert by_name[name]["providerCallBudget"] == derive_core_call_budget_default()
    # failed event is NOT emitted on the success path; forced here separately
    # by the pre-fix run.
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        failed_record, _t2 = _run_live(max_core_llm_calls=12)
    assert failed_record.state is GenerationState.FAILED
    assert failed_record.failure_code == "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED"
    for event in caplog.records:
        ev = getattr(event, "pd_event", None)
        if ev == "generation.failed":
            assert getattr(event, "pd_fields", {}).get("providerCallBudget") == 12


# --------------------------------------------------------------------------- #
# §10 items 19..20 — derived cap relationship
# --------------------------------------------------------------------------- #


def test_world_graph_is_not_privileged_or_bypassed():
    """world_graph is an ordinary CORE-bucket call: with a 12 cap exactly the
    pre-fix sequence exhausts at the SAME call (13) that POST-fix permits —
    there is no stage-specific bypass."""
    pre_record, _t1 = _run_live(max_core_llm_calls=12)
    assert pre_record.state is GenerationState.FAILED
    assert pre_record.failure_code == "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert pre_record.budget.core_calls == 12
    # the 13th call (world_graph) was attempted only POST-fix.
    derived = derive_core_call_budget_default()
    post_record, t2 = _run_live(max_core_llm_calls=derived)
    assert post_record.state is GenerationState.PUBLISHED
    assert t2.call_count == 13
    assert _stage_of(t2.prompt_of_call(12)) == "world_requirements_v1"
    # both observations are consistent: world_graph counted as a normal call.
    assert post_record.budget.core_calls == 13


def test_derived_budget_auto_updates_when_canonical_constant_changes(monkeypatch):
    """Item 20 — the derived default follows the canonical repair-limit
    constant (MAX_ACTIVITY_LOG_REPAIR_PASSES), the person parser cap
    (MAX_CHARACTERS) and MAX_EVIDENCE_ITEMS automatically: no magic number to
    re-tune."""
    import app.generation.schemas as schemas_mod
    import app.services.ollama_driver as driver_mod

    base = derive_core_call_budget_default()
    assert base == 620  # 4 passes × (5 + max(9,50)×3)

    monkeypatch.setattr(driver_mod, "MAX_ACTIVITY_LOG_REPAIR_PASSES", 3)
    raised = derive_core_call_budget_default()
    assert raised > base
    # per fact = 1+3; binding path = MAX_EVIDENCE_ITEMS=50 → per pass = 5+200
    assert raised == 4 * (5 + 50 * (1 + 3)) == 820

    monkeypatch.setattr(schemas_mod, "MAX_CHARACTERS", 10)
    raised_facts = derive_core_call_budget_default()
    # parsed path grows to 11×4=44 but the RAW path (200) stays the binding one.
    assert raised_facts == 4 * (5 + 50 * (1 + 3)) == 820

    # a Settings() default reads the same derived value (no stale literal) and
    # BELLOW-legal overrides stay rejected (same validator math).
    from app.core.config import Settings

    assert Settings().max_core_llm_calls_per_generation == raised_facts
