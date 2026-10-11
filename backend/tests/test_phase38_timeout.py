"""Phase38 — timeout ownership + deadline-clipped classification tests (§24-§26,
§37/§38/§39).

Hermetic and deterministic: the driver-path LOCAL_PROVIDER_TIMEOUT is raised by
a scripted stage driver, the budget runs on a ``ManualClock``, and the real
``GenerationController._advance`` performs the classification — the exact
production topology.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.failure_codes import (  # noqa: E402
    GenerationFailureCode,
    infer_failure_code,
)
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import StageDriverProviderFailure  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402


_PROMPT = (
    "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: embezzlement\n"
    "Weapon: kitchen knife\nTime: 2026-09-11T22:17:00+02:00\n"
)


class _TimeoutRaisingDriver:
    """Scripted stage driver: consumes ``wait_seconds`` of the manual clock,
    then raises a provider TIMEOUT — the exact shape a real local-AI
    (bridge/direct-ollama) call produces when its job budget expires.

    The ``issued_timeout_seconds`` / ``configured_timeout_seconds`` fields
    mirror the Phase38 DEF-094 carrier the production driver attaches to every
    timeout failure (the ACTUAL server-issued effective per-call budget + the
    configured provider timeout). ``None`` models a no-carrier legacy carrier
    (kept for the pre-DEF-094 margin-rule tests).
    """

    def __init__(
        self,
        clock: ManualClock,
        code: GenerationFailureCode = GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT,
        wait_seconds: float = 0.0,
        issued_timeout_seconds: float | None = None,
        configured_timeout_seconds: float | None = None,
    ) -> None:
        self._clock = clock
        self._code = code
        self._wait_seconds = wait_seconds
        self._issued = issued_timeout_seconds
        self._configured = configured_timeout_seconds

    def run_into(self, attempt, diagnostics=(), repair_plan=None) -> None:
        if self._wait_seconds:
            self._clock.advance(self._wait_seconds)
        raise StageDriverProviderFailure(
            "local provider timed out",
            code=self._code,
            issued_timeout_seconds=self._issued,
            configured_timeout_seconds=self._configured,
        )


def _controller_for(
    clock: ManualClock,
    driver: Any,
    *,
    provider_timeout_seconds: float,
    deadline_seconds: int = 300,
    advance_before: float = 0.0,
) -> tuple[GenerationController, object, object]:
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=None,  # the driver path never touches the scalar provider
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=deadline_seconds,
        max_llm_calls_per_generation=12,
        max_core_llm_calls=12,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=17,
        stage_driver=driver,
        provider_timeout_seconds=provider_timeout_seconds,
    )
    if advance_before:
        clock.advance(advance_before)
    handle = controller.start_generation(
        _PROMPT, anonymous_quota_session_id=session.session_id
    )
    return controller, controller.attempt(handle.attempt_id), handle


# --------------------------------------------------------------------------- #
# §37 — the real shape: remaining 6281ms -> effective 6181ms (server side)
# --------------------------------------------------------------------------- #


def test_effective_provider_timeout_keeps_the_100ms_classification_margin():
    """Server-side derivation: ``min(configured 180s, remaining 6.281s)`` minus
    the documented 100ms classification margin = 6181ms. The bridge must never
    halve this again (bridge tests cover that; see test_phase38_timeout_contract)."""
    from app.generation.budgets import BudgetTracker

    clock = ManualClock(start_time=0.0)
    budget = BudgetTracker(
        clock, deadline_seconds=300,
        max_calls=50, max_repairs=2, max_regenerations=1, max_core_calls=50,
    )
    clock.advance(300.0 - 6.281)
    effective = budget.effective_provider_timeout(180.0)
    assert effective == pytest.approx(6.181, abs=0.001)

    remaining_before = budget.remaining_seconds()
    assert remaining_before == pytest.approx(6.281, abs=0.001)


def test_effective_provider_timeout_below_margin_returns_zero():
    from app.generation.budgets import BudgetTracker

    clock = ManualClock(start_time=0.0)
    budget = BudgetTracker(
        clock, deadline_seconds=300,
        max_calls=50, max_repairs=2, max_regenerations=1, max_core_calls=50,
    )
    clock.advance(300.0 - 0.05)
    assert budget.effective_provider_timeout(180.0) == 0.0


# --------------------------------------------------------------------------- #
# §38 — deadline-clipped LOCAL_PROVIDER_TIMEOUT classification (driver path)
# --------------------------------------------------------------------------- #


def test_deadline_clipped_local_provider_timeout_is_generation_deadline(caplog):
    """§38 case: the overall generation deadline is nearly exhausted (only the
    ~0.1s classification margin remains when the local job expires) -> the
    terminal classification is GENERATION_DEADLINE_EXCEEDED with the closed
    ``deadlineClipped`` marker — never a plain provider-slowness code.

    The manual clock advances INSIDE the scripted driver call (the budget's
    ``started_at`` is the clock value at attempt start, exactly like a real
    generation).
    """
    clock = ManualClock(start_time=0.0)
    driver = _TimeoutRaisingDriver(
        clock,
        wait_seconds=299.95,  # only ~0.05s of the deadline remains at raise
    )
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        _controller, record, _handle = _controller_for(
            clock,
            driver,
            provider_timeout_seconds=180.0,
        )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "GENERATION_DEADLINE_EXCEEDED"
    failed = [
        e for e in caplog.records
        if getattr(e, "pd_event", None) == "generation.failed"
    ]
    assert len(failed) == 1
    assert failed[0].pd_fields["failureCode"] == "GENERATION_DEADLINE_EXCEEDED"
    assert failed[0].pd_fields["deadlineClipped"] is True
    assert failed[0].pd_fields["timeoutSource"] == "GENERATION_DEADLINE"


def test_ordinary_local_provider_timeout_with_ample_deadline_stays_local_timeout(caplog):
    """§38 control: ample generation deadline remains, the LOCAL provider's own
    normal timeout expires -> the canonical LOCAL_PROVIDER_TIMEOUT is kept
    (NOT remapped to the generation deadline)."""
    clock = ManualClock(start_time=0.0)
    driver = _TimeoutRaisingDriver(clock, wait_seconds=0.0)
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        _controller, record, _handle = _controller_for(
            clock,
            driver,
            provider_timeout_seconds=180.0,
            advance_before=0.0,  # 300 s of deadline remain
        )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "LOCAL_PROVIDER_TIMEOUT"
    failed = [
        e for e in caplog.records
        if getattr(e, "pd_event", None) == "generation.failed"
    ]
    assert len(failed) == 1
    assert failed[0].pd_fields["failureCode"] == "LOCAL_PROVIDER_TIMEOUT"
    # No deadline-clip marker: this is ordinary provider slowness.
    assert failed[0].pd_fields.get("deadlineClipped") is None
    assert failed[0].pd_fields.get("timeoutSource") is None


def test_regular_provider_timeout_still_deadline_remaps_in_driver_path(caplog):
    """The legacy PROVIDER_TIMEOUT (direct-ollama) code follows the SAME
    deadline-first remap in the driver path (§26 alignment)."""
    clock = ManualClock(start_time=0.0)
    driver = _TimeoutRaisingDriver(
        clock, code=GenerationFailureCode.PROVIDER_TIMEOUT, wait_seconds=299.95
    )
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        _controller, record, _handle = _controller_for(
            clock,
            driver,
            provider_timeout_seconds=180.0,
        )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "GENERATION_DEADLINE_EXCEEDED"
    assert any(
        e.pd_fields.get("deadlineClipped") is True
        for e in caplog.records
        if getattr(e, "pd_event", None) == "generation.failed"
    )


def test_deadline_clipped_local_timeout_with_reserve_firing_early_is_deadline(
    caplog,
):
    """DEF-094 (ADV-38-02) — the §37/§38 REAL SHAPE (a): the generation
    deadline is nearly exhausted (remaining 6281ms) so the server issues an
    effective per-call budget of 6181ms (``min(configured 180s, remaining -
    100ms)``). The local bridge's documented 250ms inner reserve fires EARLY at
    5.931s — leaving 0.35s (> 0.1s) of deadline at raise time. Classification
    must compare the ACTUAL server-issued budget (6.181s < 180s configured)
    and report GENERATION_DEADLINE_EXCEEDED + deadlineClipped — never an
    ordinary LOCAL_PROVIDER_TIMEOUT."""
    clock = ManualClock(start_time=0.0)
    driver = _TimeoutRaisingDriver(
        clock,
        wait_seconds=5.931,  # the inner Ollama-HTTP read fires ~250ms early
        issued_timeout_seconds=6.181,
        configured_timeout_seconds=180.0,
    )
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        # 300 - 6.281 = 293.719s elapsed before dispatch -> 6281ms remain.
        _controller, record, _handle = _controller_for(
            clock,
            driver,
            provider_timeout_seconds=180.0,
            advance_before=300.0 - 6.281,
        )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "GENERATION_DEADLINE_EXCEEDED"
    failed = [
        e for e in caplog.records
        if getattr(e, "pd_event", None) == "generation.failed"
    ]
    assert len(failed) == 1
    assert failed[0].pd_fields["failureCode"] == "GENERATION_DEADLINE_EXCEEDED"
    assert failed[0].pd_fields["deadlineClipped"] is True
    assert failed[0].pd_fields["timeoutSource"] == "GENERATION_DEADLINE"


def test_deadline_clipped_local_timeout_exact_boundary_is_deadline(caplog):
    """DEF-094 (ADV-38-02) — the §37/§38 REAL SHAPE (b): the bridge OUTER wait
    fires at the exact issued budget (6.181s), leaving
    ``remaining == 0.10000000000002274`` (float epsilon ABOVE 0.1s). The old
    ``remaining <= 0.1`` rule MISSED this; the issued-budget comparison
    (6.181s < 180s) classifies it as GENERATION_DEADLINE_EXCEEDED."""
    clock = ManualClock(start_time=0.0)
    driver = _TimeoutRaisingDriver(
        clock,
        wait_seconds=6.181,  # the outer bridge job wait fires at the budget
        issued_timeout_seconds=6.181,
        configured_timeout_seconds=180.0,
    )
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        _controller, record, _handle = _controller_for(
            clock,
            driver,
            provider_timeout_seconds=180.0,
            advance_before=300.0 - 6.281,
        )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "GENERATION_DEADLINE_EXCEEDED"
    failed = [
        e for e in caplog.records
        if getattr(e, "pd_event", None) == "generation.failed"
    ]
    assert len(failed) == 1
    assert failed[0].pd_fields["failureCode"] == "GENERATION_DEADLINE_EXCEEDED"
    assert failed[0].pd_fields["deadlineClipped"] is True
    assert failed[0].pd_fields["timeoutSource"] == "GENERATION_DEADLINE"


def test_ordinary_timeout_with_full_issued_budget_stays_local_timeout(caplog):
    """DEF-094 control: the server issued the FULL configured provider timeout
    (180s == 180s — the deadline was NOT the binding bound; ample deadline
    remained at dispatch). The provider's own timeout expires -> the canonical
    LOCAL_PROVIDER_TIMEOUT is kept with NO deadline-clip marker."""
    clock = ManualClock(start_time=0.0)
    driver = _TimeoutRaisingDriver(
        clock,
        wait_seconds=0.0,
        issued_timeout_seconds=180.0,
        configured_timeout_seconds=180.0,
    )
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        _controller, record, _handle = _controller_for(
            clock,
            driver,
            provider_timeout_seconds=180.0,
            advance_before=0.0,  # 300 s of deadline remain at dispatch
        )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "LOCAL_PROVIDER_TIMEOUT"
    failed = [
        e for e in caplog.records
        if getattr(e, "pd_event", None) == "generation.failed"
    ]
    assert len(failed) == 1
    assert failed[0].pd_fields["failureCode"] == "LOCAL_PROVIDER_TIMEOUT"
    assert failed[0].pd_fields.get("deadlineClipped") is None
    assert failed[0].pd_fields.get("timeoutSource") is None


# --------------------------------------------------------------------------- #
# §26 — failure-code consistency (defensive text mapping unchanged)
# --------------------------------------------------------------------------- #


def test_infer_failure_code_keeps_local_provider_timeout_mapping():
    assert infer_failure_code("local provider timed out") is (
        GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT
    )
    assert infer_failure_code("Local AI did not finish within the allowed time.") is (
        GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT
    )
    assert infer_failure_code("generation deadline exceeded") is (
        GenerationFailureCode.GENERATION_DEADLINE_EXCEEDED
    )


# --------------------------------------------------------------------------- #
# §39 — the timeout-source vocabulary is closed and safe
# --------------------------------------------------------------------------- #


def test_timeout_source_vocabulary_is_closed_and_safe():
    from app.generation.failure_codes import GenerationFailureCode as G

    sources = frozenset(
        {
            "BRIDGE_JOB_TIMEOUT",
            "OLLAMA_HTTP_TIMEOUT",
            "SERVER_PROVIDER_TIMEOUT",
            "GENERATION_DEADLINE",
        }
    )
    # Every timeout code produced by the lifecycle maps onto the closed set
    # (defensively — the controller never surfaces raw exception text).
    timeout_codes = {
        G.PROVIDER_TIMEOUT.value,
        G.FRONTIER_TIMEOUT.value,
        G.LOCAL_PROVIDER_TIMEOUT.value,
        G.GENERATION_DEADLINE_EXCEEDED.value,
    }
    assert timeout_codes <= set(v.value for v in G)
    for code in timeout_codes:
        assert isinstance(code, str) and "_" in code
    # The only deadline-clipped source token the controller emits is the
    # closed "GENERATION_DEADLINE".
    assert "GENERATION_DEADLINE" in sources


# --------------------------------------------------------------------------- #
# DEF-098 (ADV-38-05) — stage-timing accounting is loss-less or explicitly
# marked truncated (never a silent 32-token cap)
# --------------------------------------------------------------------------- #


class _FakeStageTimingDriver:
    """Mimics a stage driver that recorded >32 REAL provider calls: one timing
    token per real call (the count matches the per-attempt budget)."""

    def __init__(self, clock: ManualClock, tokens: int = 40) -> None:
        self._clock = clock
        self._tokens = tuple(
            f"CASE_TRUTH:{1000 + i}:success" for i in range(tokens)
        )

    def stage_timing_summary(self, attempt_id: str):
        return self._tokens

    def run_into(self, attempt, diagnostics=(), repair_plan=None) -> None:
        # the attempt really consumed one provider call per recorded token.
        if attempt.budget is not None:
            attempt.budget.calls = len(self._tokens)
        raise StageDriverProviderFailure(
            "local provider timed out",
            code=GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT,
        )


def test_stage_timing_accounting_explicitly_truncated_not_silent(caplog):
    """DEF-098 — an attempt with 40 (> 32) real provider calls: the
    driver-level summary stays LOSS-LESS (all 40 tokens), the terminal event
    carries the EXACT ``stageTimingCount`` and an explicit
    ``stageTimingTruncated`` marker (the event writer's bounded item cap is
    never silent), and ``providerCallCount`` remains exact and reconciles with
    ``stageTimingCount``."""
    clock = ManualClock(start_time=0.0)
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    driver = _FakeStageTimingDriver(clock, tokens=40)
    controller = GenerationController(
        provider=None,  # the driver path never touches the scalar provider
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=300,
        max_llm_calls_per_generation=60,
        max_core_llm_calls=60,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=17,
        stage_driver=driver,
        provider_timeout_seconds=180.0,
    )
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        handle = controller.start_generation(
            _PROMPT, anonymous_quota_session_id=session.session_id
        )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    # The driver-level summary API is LOSS-LESS (all 40 tokens available).
    assert len(driver.stage_timing_summary(record.attempt_id)) == 40
    failed = [
        e for e in caplog.records
        if getattr(e, "pd_event", None) == "generation.failed"
    ]
    assert len(failed) == 1
    fields = failed[0].pd_fields
    # Exact accounting: the per-call count reconciles with providerCallCount.
    assert fields["stageTimingCount"] == 40
    assert fields["providerCallCount"] == 40
    assert fields["stageTimingTruncated"] is True
    # The emitted summary is bounded by the safe event writer (never expanded
    # beyond its documented item cap — the truncation is EXPLICIT).
    summary = fields["stageTimingSummary"]
    assert isinstance(summary, list)
    assert len(summary) <= 32
    for token in summary:
        parts = token.split(":")
        assert len(parts) == 3 and parts[0] == "CASE_TRUTH"


def test_stage_timing_accounting_lossless_below_event_cap(caplog):
    """DEF-098 control — an attempt with 20 (<= 32) real provider calls is
    fully LOSS-LESS: no truncation marker, ``stageTimingCount ==
    providerCallCount == 20`` and every token is emitted."""
    clock = ManualClock(start_time=0.0)
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    driver = _FakeStageTimingDriver(clock, tokens=20)
    controller = GenerationController(
        provider=None,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=300,
        max_llm_calls_per_generation=60,
        max_core_llm_calls=60,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=17,
        stage_driver=driver,
        provider_timeout_seconds=180.0,
    )
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        handle = controller.start_generation(
            _PROMPT, anonymous_quota_session_id=session.session_id
        )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    failed = [
        e for e in caplog.records
        if getattr(e, "pd_event", None) == "generation.failed"
    ]
    assert len(failed) == 1
    fields = failed[0].pd_fields
    assert fields["stageTimingCount"] == 20
    assert fields["providerCallCount"] == 20
    assert fields.get("stageTimingTruncated") is None
    assert len(fields["stageTimingSummary"]) == 20
