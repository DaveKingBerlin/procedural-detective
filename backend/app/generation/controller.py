"""Generation lifecycle controller (Phase 4 A/B/C/G, REQUIREMENTS 7.3/32.1-32.7).

The controller is the only lifecycle driver. It:

- admits EVERY generation before the first provider call (§32.10/32.11) and
  rejects with zero provider calls;
- owns attempt identity + the explicit compare-and-set race protection
  (§32.1/32.4): a result is applied only when its pending entry still belongs
  to the CURRENT active attempt in a state that permits the transition;
- runs the deterministic stage pipeline and validation classification
  (VALID / RECOVERABLE_REPAIR / RECOVERABLE_REGENERATE / TERMINAL_FAILURE);
- enforces the per-attempt BudgetTracker (deadline, model calls, repairs,
  regenerations);
- publishes only through the CAS gate (current active + VALIDATING + VALID).

A late/stale provider result is DISCARDED with zero authoritative mutation
(§32.4); ``FAILED`` is terminal (§32.3).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from app.generation import pipeline
from app.generation.admission import AdmissionController, AdmissionDenied
from app.generation.budgets import BudgetTracker
from app.generation.clock import Clock
from app.generation.failure_codes import GenerationFailureCode, infer_failure_code, public_failure_code
from app.generation.ids import IdSource
from app.generation.pipeline import AttemptRecord
from app.generation.provider import (
    GenerationStage,
    Provider,
    ProviderError,
    ProviderResult,
)
from app.generation.publish import (
    PublishedCaseVersion,
    build_published_case_version,
)
from app.generation.state_machine import (
    GenerationState,
    ValidationOutcome,
    assert_transition,
)
from app.core.observability import emit_event

_PD_DEV_TRACE = os.environ.get("PD_DEV_TRACE") == "true"


def _dt(message: str) -> None:
    """DEV-ONLY structured trace (Phase17E PART B); gated, default OFF.

    Never logs prompts, truth, credentials or the Ollama URL — only elapsed /
    deadline / call counters (the phase-mandated reproducible evidence).
    """
    if _PD_DEV_TRACE:
        print(f"[PD-DEV-TRACE] {message}", flush=True)


def _dt_remaining(attempt: AttemptRecord) -> str:
    if attempt.budget is None:
        return "none"
    return str(int(attempt.budget.remaining_seconds() * 1000))


def _remaining_ms(attempt: AttemptRecord) -> int | None:
    if attempt.budget is None:
        return None
    return int(attempt.budget.remaining_seconds() * 1000)


_PROVIDER_FAILURE_REASON = "provider failure: generator unavailable"


@dataclass(frozen=True)
class GenerationHandle:
    """Immutable handle returned by ``start_generation`` (snapshot at return)."""

    attempt_id: str
    case_id: str
    state: GenerationState


@dataclass(frozen=True)
class PublishResult:
    """Outcome of an explicit publication attempt (CAS)."""

    success: bool
    reason: str | None = None
    published: PublishedCaseVersion | None = None


class GenerationController:
    """Owns zero-or-one active generation attempt and drives its lifecycle."""

    def __init__(
        self,
        *,
        provider: Provider,
        admission: AdmissionController,
        clock: Clock,
        ids: IdSource,
        deadline_seconds: int,
        max_llm_calls_per_generation: int,
        max_repair_passes: int,
        max_full_regenerations: int,
        max_prompt_chars: int,
        seed: int | None = None,
        hold_before_publish: bool = False,
        max_retained_attempts: int = 100,
        stage_driver: Any = None,
        provider_timeout_seconds: float | None = None,
        provider_name: str | None = None,
        provider_model: str | None = None,
    ) -> None:
        self._provider = provider
        self._admission = admission
        self._clock = clock
        self._ids = ids
        self._deadline_seconds = int(deadline_seconds)
        self._max_llm_calls = int(max_llm_calls_per_generation)
        self._max_repair_passes = int(max_repair_passes)
        self._max_full_regenerations = int(max_full_regenerations)
        self._max_prompt_chars = int(max_prompt_chars)
        self._seed = seed
        self._hold_before_publish = bool(hold_before_publish)
        # Phase 16_2: an optional OllamaStageDriver that owns the structured
        # per-stage CALLS and produces a full draft; when set the controller
        # delegates draft production to it (budget/repair/regenerate/publication
        # machinery here is unchanged — no parallel lifecycle).
        self._driver = stage_driver
        self._provider_timeout_seconds = (
            float(provider_timeout_seconds) if provider_timeout_seconds is not None else None
        )
        self._provider_name = provider_name
        self._provider_model = provider_model
        if isinstance(max_retained_attempts, bool) or not isinstance(
            max_retained_attempts, int
        ):
            raise TypeError("max_retained_attempts must be an int")
        if max_retained_attempts <= 0:
            raise ValueError("max_retained_attempts must be > 0")
        self._max_retained_attempts = int(max_retained_attempts)
        self._auto_seed = 0
        self._current_attempt_id: str | None = None
        self._attempts: dict[str, AttemptRecord] = {}
        self._wall_started: dict[str, float] = {}
        # pending_id -> (attempt_id, stage) — the ONLY way a deferred result is
        # routed back into the driver.
        self._pending_map: dict[str, tuple[str, GenerationStage]] = {}
        # Every pending_id ever registered by the provider for this controller.
        # Used pending ids stay used (globally unique for the controller
        # lifetime); a provider reusing an id is a protocol violation
        # (ADV-130) because it could cross-route completions between attempts.
        self._used_pending_ids: set[str] = set()

    # -- public surface --------------------------------------------------------

    @property
    def current_attempt_id(self) -> str | None:
        return self._current_attempt_id

    def attempt(self, attempt_id: str) -> AttemptRecord | None:
        """Inspection access for tests and later phases."""
        return self._attempts.get(attempt_id)

    def start_generation(
        self,
        prompt: str,
        *,
        anonymous_quota_session_id: str,
        creator_token: str | None = None,
    ) -> GenerationHandle:
        """Start a new generation attempt (admission first; supersede semantics).

        Admission happens BEFORE any provider call; a denial raises
        ``AdmissionDenied`` with zero provider calls. If another attempt is
        still active it is terminal-failed with ``"superseded by newer attempt"``
        and its admission reservation is released (§32.4/32.1).
        """
        # Input validation is purely local (no provider call, no reservation);
        # ordering it first keeps reservations exact on invalid input.
        locked, note = pipeline.normalize_prompt(
            prompt, max_chars=self._max_prompt_chars
        )
        decision = self._admission.admit_generation(
            anonymous_quota_session_id, creator_token=creator_token
        )
        if not decision.admitted:
            raise AdmissionDenied(decision)  # pragma: no cover - admit raises directly

        current = self._current_attempt()
        if current is not None and current.state not in (
            GenerationState.PUBLISHED,
            GenerationState.FAILED,
        ):
            self._fail(current, "superseded by newer attempt")

        attempt_id = self._ids.generation_attempt_id()
        case_id = self._ids.case_id()
        seed = self._seed if self._seed is not None else self._next_auto_seed()
        budget = BudgetTracker(
            self._clock,
            deadline_seconds=self._deadline_seconds,
            max_calls=self._max_llm_calls,
            max_repairs=self._max_repair_passes,
            max_regenerations=self._max_full_regenerations,
        )
        attempt = AttemptRecord(
            attempt_id=attempt_id,
            case_id=case_id,
            session_id=anonymous_quota_session_id,
            seed=seed,
            prompt=prompt,
            prompt_note=note,
            locked=locked,
            budget=budget,
        )
        self._attempts[attempt_id] = attempt
        self._wall_started[attempt_id] = time.perf_counter()
        self._current_attempt_id = attempt_id
        emit_event(
            "generation.started",
            caseId=case_id,
            generationAttemptId=attempt_id,
            configuredGenerationDeadlineMs=int(self._deadline_seconds * 1000),
            configuredProviderTimeoutMs=(
                int(self._provider_timeout_seconds * 1000)
                if self._provider_timeout_seconds is not None else None
            ),
            provider=self._provider_name,
            model=self._provider_model,
            providerCallCount=0,
            repairCount=0,
            regenerationCount=0,
            deadlineRemainingMs=int(budget.remaining_seconds() * 1000),
        )
        self._advance(attempt)
        self._prune_retained_attempts()
        return GenerationHandle(
            attempt_id=attempt.attempt_id,
            case_id=attempt.case_id,
            state=attempt.state,
        )

    def on_completion(self, pending_id: str, result: ProviderResult) -> None:
        """CompletionSink: route a deferred provider result back into the driver.

        Idempotent: an unknown or already-consumed ``pending_id`` is a no-op.
        A result whose owning attempt is no longer current (or is terminal) is
        DISCARDED with zero mutation (§32.4).
        """
        entry = self._pending_map.pop(pending_id, None)
        if entry is None:
            return
        attempt_id, stage = entry
        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            return
        if self._current_attempt_id != attempt_id:
            return  # stale attempt — discard, no mutation
        if attempt.state not in (
            GenerationState.GENERATING,
            GenerationState.REPAIRING,
            GenerationState.VALIDATING,
        ):
            return  # terminal attempt — discard, no mutation
        if attempt.pending_id != pending_id:
            return  # already consumed by another path — no-op
        # §32.6: a deferred result may never be applied after the attempt's
        # deadline has passed (ADV-124). Fail WITHOUT mutating the draft —
        # the pending slot was already popped above (idempotent pop semantics).
        if attempt.budget is not None and attempt.budget.deadline_passed():
            self._fail(attempt, "generation deadline exceeded")
            return
        attempt.pending_id = None
        attempt.pending_stage = None
        self._apply_provider_result(attempt, stage, result)
        self._advance(attempt)

    def publish(self, attempt_id: str, *, hold_ok: bool = False) -> PublishResult:
        """CAS publication gate (§32.4/§F).

        Publishes only when the attempt is the current active attempt AND its
        state is VALIDATING AND its validation outcome is VALID (and
        ``hold_ok`` when ``hold_before_publish`` is enabled). On success the
        frozen ``PublishedCaseVersion`` is built, the state becomes PUBLISHED
        and the admission reservation is released. Any failure returns
        ``PublishResult(success=False)`` with NO mutation.
        """
        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            return PublishResult(False, "unknown generation attempt")
        if self._current_attempt_id != attempt_id:
            return PublishResult(
                False, "attempt is not the current active generation attempt"
            )
        if attempt.state is not GenerationState.VALIDATING:
            return PublishResult(
                False,
                f"attempt state is {attempt.state.value}; publication requires "
                "the VALIDATING state",
            )
        report = attempt.last_validation
        if report is None or report.outcome is not ValidationOutcome.VALID:
            return PublishResult(False, "attempt has not passed full validation")
        # §32.6: the publication gate re-checks the generation deadline BEFORE
        # the CAS commit (ADV-123). A past-deadline attempt can never publish:
        # fail it (terminal) and refuse with no mutation.
        if attempt.budget is not None and attempt.budget.deadline_passed():
            self._fail(
                attempt,
                "generation deadline exceeded",
                code=GenerationFailureCode.GENERATION_DEADLINE_EXCEEDED,
            )
            return PublishResult(False, "generation deadline exceeded")
        if self._hold_before_publish and not hold_ok:
            return PublishResult(
                False, "publication gate refused: hold_before_publish is enabled"
            )
        attempt.published_at = float(self._clock.now())
        try:
            payload = build_published_case_version(attempt)
        except (TypeError, ValueError):
            attempt.published_at = None
            return PublishResult(
                False,
                "publication gate failed: cannot assemble the frozen case payload",
            )
        assert_transition(GenerationState.VALIDATING, GenerationState.PUBLISHED)
        attempt.state = GenerationState.PUBLISHED
        attempt.published = payload
        self._release_admission(attempt)
        emit_event(
            "generation.published",
            caseId=attempt.case_id,
            generationAttemptId=attempt.attempt_id,
            published=True,
            failureCode=None,
            deadlineRemainingMs=_remaining_ms(attempt),
            totalElapsedMs=self._elapsed_ms(attempt),
            providerCallCount=attempt.budget.calls if attempt.budget is not None else None,
            repairCount=attempt.budget.repair_passes if attempt.budget is not None else None,
            regenerationCount=attempt.budget.regenerations if attempt.budget is not None else None,
        )
        return PublishResult(True, published=payload)

    # -- driver loop -----------------------------------------------------------

    def _advance(self, attempt: AttemptRecord) -> None:
        """Synchronous lifecycle driver; returns on PUBLISHED/FAILED/pending."""
        while True:
            if _PD_DEV_TRACE:
                budget = attempt.budget
                _dt(
                    "advance.loop state=%s calls=%s repairs=%s regens=%s "
                    "deadlineRemainingMs=%s"
                    % (
                        attempt.state.value,
                        budget.calls if budget is not None else "?",
                        budget.repair_passes if budget is not None else "?",
                        budget.regenerations if budget is not None else "?",
                        _dt_remaining(attempt),
                    )
                )
            if attempt.attempt_id != self._current_attempt_id:
                return  # no longer authoritative
            if attempt.state in (GenerationState.PUBLISHED, GenerationState.FAILED):
                return
            if attempt.state is GenerationState.DRAFT:
                self._set_state(attempt, GenerationState.GENERATING)
                continue
            if attempt.budget is not None and attempt.budget.deadline_passed():
                self._fail(
                    attempt,
                    "generation deadline exceeded",
                    code=GenerationFailureCode.GENERATION_DEADLINE_EXCEEDED,
                )
                return

            state = attempt.state
            if state is GenerationState.GENERATING:
                if self._driver is not None:
                    # Phase 16_2: delegate draft production to the stage driver
                    # and move to VALIDATING (the driver consumed the per-call
                    # budget; a provider-level failure fails the attempt).
                    try:
                        self._driver.run_into(attempt)
                    except Exception as exc:  # noqa: BLE001 - provider path terminal
                        self._fail(
                            attempt,
                            _PROVIDER_FAILURE_REASON,
                            code=getattr(exc, "code", None),
                        )
                        return
                    self._set_state(attempt, GenerationState.VALIDATING)
                    continue
                stage = self._next_stage(attempt)
                if stage is None:
                    self._set_state(attempt, GenerationState.VALIDATING)
                    continue
                if not self._invoke_provider(attempt, stage):
                    return  # pending (paused) or terminal
                continue

            if state is GenerationState.VALIDATING:
                report = pipeline.validate_draft(attempt)
                outcome = report.outcome
                if outcome is not ValidationOutcome.VALID:
                    emit_event(
                        "generation.stage.validation_failed",
                        caseId=attempt.case_id,
                        generationAttemptId=attempt.attempt_id,
                        validationOutcome=outcome.value,
                        deadlineRemainingMs=_remaining_ms(attempt),
                        validatorIssueCodes=(
                            report.repair_diagnostics if report.repair_diagnostics else None
                        ),
                    )
                if outcome is ValidationOutcome.VALID:
                    if self._hold_before_publish:
                        return  # validated + stored; the caller publishes later
                    self.publish(attempt.attempt_id)
                    return
                if outcome is ValidationOutcome.RECOVERABLE_REPAIR:
                    if (
                        attempt.budget is None
                        or not attempt.budget.consume_repair_pass()
                    ):
                        self._fail(
                            attempt,
                            "repair budget exhausted",
                            code=GenerationFailureCode.REPAIR_BUDGET_EXHAUSTED,
                        )
                        return
                    emit_event(
                        "generation.repair.started",
                        caseId=attempt.case_id,
                        generationAttemptId=attempt.attempt_id,
                        repairCount=attempt.budget.repair_passes,
                        providerCallCount=attempt.budget.calls,
                        regenerationCount=attempt.budget.regenerations,
                        deadlineRemainingMs=_remaining_ms(attempt),
                    )
                    if self._driver is not None:
                        # driver repair: re-run the affected stages with the
                        # sanitized validation diagnostics (state stays
                        # VALIDATING; we re-validate on the next loop pass).
                        try:
                            self._driver.run_into(
                                attempt, diagnostics=report.repair_diagnostics
                            )
                        except Exception as exc:  # noqa: BLE001 - provider path terminal
                            self._fail(
                                attempt,
                                _PROVIDER_FAILURE_REASON,
                                code=getattr(exc, "code", None),
                            )
                            return
                        emit_event(
                            "generation.repair.complete",
                            caseId=attempt.case_id,
                            generationAttemptId=attempt.attempt_id,
                            repairCount=attempt.budget.repair_passes,
                            providerCallCount=attempt.budget.calls,
                            regenerationCount=attempt.budget.regenerations,
                            deadlineRemainingMs=_remaining_ms(attempt),
                        )
                        continue
                    self._set_state(attempt, GenerationState.REPAIRING)
                    if not self._invoke_provider(
                        attempt,
                        GenerationStage.REPAIR,
                        diagnostics=report.repair_diagnostics,
                    ):
                        return  # pending or terminal
                    # A synchronous REPAIR content was already applied.
                    self._set_state(attempt, GenerationState.VALIDATING)
                    continue
                if outcome is ValidationOutcome.RECOVERABLE_REGENERATE:
                    if (
                        attempt.budget is None
                        or not attempt.budget.consume_regeneration()
                    ):
                        self._fail(
                            attempt,
                            "regeneration budget exhausted",
                            code=GenerationFailureCode.REGENERATION_BUDGET_EXHAUSTED,
                        )
                        return
                    emit_event(
                        "generation.regeneration.started",
                        caseId=attempt.case_id,
                        generationAttemptId=attempt.attempt_id,
                        repairCount=attempt.budget.repair_passes,
                        providerCallCount=attempt.budget.calls,
                        regenerationCount=attempt.budget.regenerations,
                        deadlineRemainingMs=_remaining_ms(attempt),
                    )
                    self._reset_for_regeneration(attempt)
                    self._set_state(attempt, GenerationState.GENERATING)
                    continue
                # TERMINAL_FAILURE
                detail = (
                    "; ".join(report.locked_violations)
                    if report.locked_violations
                    else "terminal validation failure"
                )
                self._fail(
                    attempt,
                    f"terminal validation failure: {detail}",
                    code=self._validation_failure_code(report),
                )
                return

            if state is GenerationState.REPAIRING:
                self._set_state(attempt, GenerationState.VALIDATING)
                continue
            return  # pragma: no cover - defensive

    def _invoke_provider(
        self,
        attempt: AttemptRecord,
        stage: GenerationStage,
        *,
        diagnostics: tuple[str, ...] = (),
    ) -> bool:
        """Make exactly one provider call (budgeted). Returns True when applied.

        On a ``pending`` result the machinery pauses (returns False) until
        ``on_completion``; on ANY provider-level failure (returned
        ``timed_out``/``error`` result or raised ``ProviderError``/
        ``ProviderTimeout``) the attempt FAILS immediately without retry and
        without mutating the draft.
        """
        if attempt.budget is None:
            self._fail(attempt, "generation budget unavailable", code=GenerationFailureCode.INTERNAL_ERROR)
            return False
        if attempt.budget.deadline_passed():
            self._fail(
                attempt,
                "generation deadline exceeded",
                code=GenerationFailureCode.GENERATION_DEADLINE_EXCEEDED,
            )
            return False
        effective_timeout = None
        if self._provider_timeout_seconds is not None:
            effective_timeout = attempt.budget.effective_provider_timeout(
                self._provider_timeout_seconds
            )
            if effective_timeout <= 0:
                self._fail(
                    attempt,
                    "generation deadline exceeded",
                    code=GenerationFailureCode.GENERATION_DEADLINE_EXCEEDED,
                )
                return False
        if not attempt.budget.consume_call():
            self._fail(
                attempt,
                "model call budget exhausted",
                code=GenerationFailureCode.PROVIDER_CALL_BUDGET_EXHAUSTED,
            )
            return False
        emit_event(
            "provider.call.start",
            caseId=attempt.case_id,
            generationAttemptId=attempt.attempt_id,
            stage=stage.value,
            provider=self._provider_name,
            model=self._provider_model,
            configuredGenerationDeadlineMs=int(attempt.budget.deadline_seconds * 1000),
            deadlineRemainingMs=_remaining_ms(attempt),
            configuredProviderTimeoutMs=(
                int(self._provider_timeout_seconds * 1000)
                if self._provider_timeout_seconds is not None else None
            ),
            effectiveProviderTimeoutMs=(
                int(effective_timeout * 1000) if effective_timeout is not None else None
            ),
            providerCallCount=attempt.budget.calls,
            repairCount=attempt.budget.repair_passes,
            regenerationCount=attempt.budget.regenerations,
        )
        if _PD_DEV_TRACE:
            _dt(
                "provider.call.start stage=%s calls=%s deadlineRemainingMs=%s"
                % (stage.value, attempt.budget.calls, _dt_remaining(attempt))
            )
        _t0 = time.perf_counter()
        try:
            request_kwargs: dict[str, Any] = {"diagnostics": diagnostics}
            # Keep the legacy injectable seam compatible for non-timeout
            # providers/tests; real HTTP providers receive the runtime clamp.
            if effective_timeout is not None:
                request_kwargs["timeout_seconds"] = effective_timeout
            request = pipeline.build_request(attempt, stage, **request_kwargs)
            result = self._provider.generate(request)
        except ProviderError as exc:
            # Expected provider-level failures: terminal, sanitized, no retry.
            code = (
                GenerationFailureCode.PROVIDER_TIMEOUT
                if exc.__class__.__name__ == "ProviderTimeout"
                else GenerationFailureCode.PROVIDER_UNAVAILABLE
            )
            emit_event(
                "provider.call.timeout" if code is GenerationFailureCode.PROVIDER_TIMEOUT else "provider.call.error",
                caseId=attempt.case_id,
                generationAttemptId=attempt.attempt_id,
                stage=stage.value,
                provider=self._provider_name,
                failureCode=code.value,
                elapsedMs=int((time.perf_counter() - _t0) * 1000),
                configuredProviderTimeoutMs=(
                    int(self._provider_timeout_seconds * 1000)
                    if self._provider_timeout_seconds is not None else None
                ),
                effectiveProviderTimeoutMs=(
                    int(effective_timeout * 1000) if effective_timeout is not None else None
                ),
                deadlineRemainingMs=_remaining_ms(attempt),
                providerCallCount=attempt.budget.calls,
                repairCount=attempt.budget.repair_passes,
                regenerationCount=attempt.budget.regenerations,
            )
            self._fail(attempt, _PROVIDER_FAILURE_REASON, code=code)
            return False
        except Exception:  # noqa: BLE001 - ANY provider-path failure must be
            # terminal AND release the admission reservation (ADV-125); the
            # original exception still propagates to the caller.
            self._fail(
                attempt,
                _PROVIDER_FAILURE_REASON,
                code=GenerationFailureCode.PROVIDER_UNAVAILABLE,
            )
            emit_event(
                "provider.call.error",
                caseId=attempt.case_id,
                generationAttemptId=attempt.attempt_id,
                stage=stage.value,
                provider=self._provider_name,
                failureCode=GenerationFailureCode.PROVIDER_UNAVAILABLE.value,
                elapsedMs=int((time.perf_counter() - _t0) * 1000),
                configuredProviderTimeoutMs=(
                    int(self._provider_timeout_seconds * 1000)
                    if self._provider_timeout_seconds is not None else None
                ),
                effectiveProviderTimeoutMs=(
                    int(effective_timeout * 1000) if effective_timeout is not None else None
                ),
                deadlineRemainingMs=_remaining_ms(attempt),
                providerCallCount=attempt.budget.calls,
                repairCount=attempt.budget.repair_passes,
                regenerationCount=attempt.budget.regenerations,
            )
            raise
        if _PD_DEV_TRACE:
            if result.pending:
                summary = "pending"
            elif result.content is not None:
                summary = "ok(content)"
            elif result.timed_out:
                summary = "FAILED(timed_out)"
            else:
                summary = "FAILED(error=%s)" % (result.error or "unknown")
            _dt(
                "provider.call.done stage=%s %s deadlineRemainingMs=%s"
                % (stage.value, summary, _dt_remaining(attempt))
            )
        if result.pending:
            if not result.pending_id:
                self._fail(
                    attempt,
                    _PROVIDER_FAILURE_REASON,
                    code=GenerationFailureCode.PROVIDER_INVALID_RESPONSE,
                )
                return False
            if result.pending_id in self._used_pending_ids:
                # Protocol violation: a pending id must be globally unique for
                # the controller lifetime (ADV-130) — re-registering one could
                # cross-route a newer attempt's completion into an older one.
                self._fail(
                    attempt,
                    _PROVIDER_FAILURE_REASON,
                    code=GenerationFailureCode.PROVIDER_INVALID_RESPONSE,
                )
                return False
            self._used_pending_ids.add(result.pending_id)
            attempt.pending_id = result.pending_id
            attempt.pending_stage = stage
            self._pending_map[result.pending_id] = (attempt.attempt_id, stage)
            return False
        if result.content is None:
            # timed_out/error/protocol violation -> provider-level failure
            code = (
                GenerationFailureCode.PROVIDER_TIMEOUT
                if result.timed_out
                else infer_failure_code(result.error)
            )
            emit_event(
                "provider.call.timeout" if code is GenerationFailureCode.PROVIDER_TIMEOUT else "provider.call.error",
                caseId=attempt.case_id,
                generationAttemptId=attempt.attempt_id,
                stage=stage.value,
                provider=self._provider_name,
                failureCode=code.value,
                elapsedMs=int((time.perf_counter() - _t0) * 1000),
                configuredProviderTimeoutMs=(
                    int(self._provider_timeout_seconds * 1000)
                    if self._provider_timeout_seconds is not None else None
                ),
                effectiveProviderTimeoutMs=(
                    int(effective_timeout * 1000) if effective_timeout is not None else None
                ),
                deadlineRemainingMs=_remaining_ms(attempt),
                providerCallCount=attempt.budget.calls,
                repairCount=attempt.budget.repair_passes,
                regenerationCount=attempt.budget.regenerations,
            )
            self._fail(attempt, _PROVIDER_FAILURE_REASON, code=code)
            return False
        emit_event(
            "provider.call.complete",
            caseId=attempt.case_id,
            generationAttemptId=attempt.attempt_id,
            stage=stage.value,
            provider=self._provider_name,
            model=self._provider_model,
            success=True,
            elapsedMs=int((time.perf_counter() - _t0) * 1000),
            responseBytes=len(result.content.encode("utf-8")),
            structuredOutput=False,
            configuredProviderTimeoutMs=(
                int(self._provider_timeout_seconds * 1000)
                if self._provider_timeout_seconds is not None else None
            ),
            effectiveProviderTimeoutMs=(
                int(effective_timeout * 1000) if effective_timeout is not None else None
            ),
            deadlineRemainingMs=_remaining_ms(attempt),
            providerCallCount=attempt.budget.calls,
            repairCount=attempt.budget.repair_passes,
            regenerationCount=attempt.budget.regenerations,
        )
        self._apply_provider_result(attempt, stage, result)
        return True

    def _apply_provider_result(
        self, attempt: AttemptRecord, stage: GenerationStage, result: ProviderResult
    ) -> None:
        """Apply a completed provider result for its recorded stage."""
        if result.timed_out or result.error is not None or result.content is None:
            code = (
                GenerationFailureCode.PROVIDER_TIMEOUT
                if result.timed_out
                else infer_failure_code(result.error)
            )
            self._fail(attempt, _PROVIDER_FAILURE_REASON, code=code)
            return
        content = result.content
        if stage is GenerationStage.REPAIR:
            pipeline.apply_repair(attempt, content)
        else:
            pipeline.apply_stage_output(attempt, stage, content)

    # -- helpers ---------------------------------------------------------------

    def _next_stage(self, attempt: AttemptRecord) -> GenerationStage | None:
        for stage in pipeline.STAGE_ORDER:
            if stage not in attempt.stage_done:
                return stage
        return None

    @staticmethod
    def _validation_failure_code(report: Any) -> GenerationFailureCode:
        proof = getattr(report, "solver_result", None)
        if proof is not None:
            dimensions = (proof.who, proof.why, proof.weapon)
            if any(dimension is not None and not dimension.unique for dimension in dimensions):
                return GenerationFailureCode.SOLVER_AMBIGUOUS
            when = getattr(proof, "when", None)
            if when is not None and (when.ambiguous or when.overconstrained):
                return GenerationFailureCode.SOLVER_AMBIGUOUS
        diagnostics = tuple(getattr(report, "repair_diagnostics", ()) or ())
        if any("structured" in item.lower() or "parse" in item.lower() for item in diagnostics):
            return GenerationFailureCode.STRUCTURED_OUTPUT_INVALID
        if any("geometr" in item.lower() for item in diagnostics):
            return GenerationFailureCode.GEOMETRY_VALIDATION_FAILED
        return GenerationFailureCode.VALIDATION_FAILED

    def _reset_for_regeneration(self, attempt: AttemptRecord) -> None:
        """Reset staged outputs for a full regeneration (§32.2/§32.5).

        Keeps locked constraints, prompt, seed, attempt/case identity and the
        per-attempt budget; only generated stage material is discarded.
        """
        attempt.stage_outputs = {}
        attempt.stage_done = set()
        attempt.deferred_structural = ()
        attempt.draft = None
        attempt._phase3_cache = None
        attempt.solver_proof = None
        attempt.last_validation = None

    def _set_state(
        self, attempt: AttemptRecord, target: GenerationState
    ) -> None:
        assert_transition(attempt.state, target)
        attempt.state = target

    def _fail(
        self,
        attempt: AttemptRecord,
        reason: str,
        *,
        code: GenerationFailureCode | str | None = None,
    ) -> None:
        """Terminal-fail an attempt (FAILED is terminal, §32.3)."""
        if attempt.state in (GenerationState.PUBLISHED, GenerationState.FAILED):
            return
        assert_transition(attempt.state, GenerationState.FAILED)
        attempt.reason = reason
        attempt.failure_code = public_failure_code(code) or infer_failure_code(reason).value
        attempt.state = GenerationState.FAILED
        emit_event(
            "generation.failed",
            caseId=attempt.case_id,
            generationAttemptId=attempt.attempt_id,
            published=False,
            failureCode=attempt.failure_code,
            providerCallCount=attempt.budget.calls if attempt.budget is not None else None,
            repairCount=attempt.budget.repair_passes if attempt.budget is not None else None,
            regenerationCount=attempt.budget.regenerations if attempt.budget is not None else None,
            deadlineRemainingMs=_remaining_ms(attempt),
            totalElapsedMs=self._elapsed_ms(attempt),
        )
        _dt(
            "attempt.fail reason=%r state=%s calls=%s deadlineRemainingMs=%s"
            % (
                reason,
                attempt.state.value,
                attempt.budget.calls if attempt.budget is not None else "?",
                _dt_remaining(attempt),
            )
        )
        self._release_admission(attempt)
        if attempt.pending_id is not None:
            self._pending_map.pop(attempt.pending_id, None)
            attempt.pending_id = None
            attempt.pending_stage = None

    def _elapsed_ms(self, attempt: AttemptRecord) -> int | None:
        started = self._wall_started.get(attempt.attempt_id)
        if started is None:
            return None
        return int((time.perf_counter() - started) * 1000)

    def _release_admission(self, attempt: AttemptRecord) -> None:
        if attempt._admission_released:
            return
        attempt._admission_released = True
        self._admission.release_generation(attempt.session_id)

    def _current_attempt(self) -> AttemptRecord | None:
        if self._current_attempt_id is None:
            return None
        return self._attempts.get(self._current_attempt_id)

    def _prune_retained_attempts(self) -> None:
        """Bound the number of retained attempt records (ADV-126).

        When more than ``max_retained_attempts`` records exist, drop the OLDEST
        TERMINAL (PUBLISHED/FAILED) attempt (insertion order) until under the
        cap. The current attempt and every non-terminal attempt are NEVER
        pruned; if only non-terminal/current attempts remain the cap is not
        enforced below that floor.
        """
        if self._max_retained_attempts <= 0:
            return
        while len(self._attempts) > self._max_retained_attempts:
            pruned = False
            for attempt_id, candidate in list(self._attempts.items()):
                if attempt_id == self._current_attempt_id:
                    continue
                if candidate.state in (GenerationState.PUBLISHED, GenerationState.FAILED):
                    self._attempts.pop(attempt_id, None)
                    pruned = True
                    break
            if not pruned:
                break  # only non-terminal (or current) attempts remain

    def _next_auto_seed(self) -> int:
        self._auto_seed += 1
        return self._auto_seed
