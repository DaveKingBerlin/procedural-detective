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

from dataclasses import dataclass

from app.generation import pipeline
from app.generation.admission import AdmissionController, AdmissionDenied
from app.generation.budgets import BudgetTracker
from app.generation.clock import Clock
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
        self._current_attempt_id = attempt_id
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
            self._fail(attempt, "generation deadline exceeded")
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
        return PublishResult(True, published=payload)

    # -- driver loop -----------------------------------------------------------

    def _advance(self, attempt: AttemptRecord) -> None:
        """Synchronous lifecycle driver; returns on PUBLISHED/FAILED/pending."""
        while True:
            if attempt.attempt_id != self._current_attempt_id:
                return  # no longer authoritative
            if attempt.state in (GenerationState.PUBLISHED, GenerationState.FAILED):
                return
            if attempt.state is GenerationState.DRAFT:
                self._set_state(attempt, GenerationState.GENERATING)
                continue
            if attempt.budget is not None and attempt.budget.deadline_passed():
                self._fail(attempt, "generation deadline exceeded")
                return

            state = attempt.state
            if state is GenerationState.GENERATING:
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
                        self._fail(attempt, "repair budget exhausted")
                        return
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
                        self._fail(attempt, "regeneration budget exhausted")
                        return
                    self._reset_for_regeneration(attempt)
                    self._set_state(attempt, GenerationState.GENERATING)
                    continue
                # TERMINAL_FAILURE
                detail = (
                    "; ".join(report.locked_violations)
                    if report.locked_violations
                    else "terminal validation failure"
                )
                self._fail(attempt, f"terminal validation failure: {detail}")
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
            self._fail(attempt, "generation budget unavailable")
            return False
        if attempt.budget.deadline_passed():
            self._fail(attempt, "generation deadline exceeded")
            return False
        if not attempt.budget.consume_call():
            self._fail(attempt, "model call budget exhausted")
            return False
        try:
            request = pipeline.build_request(
                attempt, stage, diagnostics=diagnostics
            )
            result = self._provider.generate(request)
        except ProviderError:
            # Expected provider-level failures: terminal, sanitized, no retry.
            self._fail(attempt, _PROVIDER_FAILURE_REASON)
            return False
        except Exception:  # noqa: BLE001 - ANY provider-path failure must be
            # terminal AND release the admission reservation (ADV-125); the
            # original exception still propagates to the caller.
            self._fail(attempt, _PROVIDER_FAILURE_REASON)
            raise
        if result.pending:
            if not result.pending_id:
                self._fail(attempt, _PROVIDER_FAILURE_REASON)
                return False
            if result.pending_id in self._used_pending_ids:
                # Protocol violation: a pending id must be globally unique for
                # the controller lifetime (ADV-130) — re-registering one could
                # cross-route a newer attempt's completion into an older one.
                self._fail(attempt, _PROVIDER_FAILURE_REASON)
                return False
            self._used_pending_ids.add(result.pending_id)
            attempt.pending_id = result.pending_id
            attempt.pending_stage = stage
            self._pending_map[result.pending_id] = (attempt.attempt_id, stage)
            return False
        if result.content is None:
            # timed_out/error/protocol violation -> provider-level failure
            self._fail(attempt, _PROVIDER_FAILURE_REASON)
            return False
        self._apply_provider_result(attempt, stage, result)
        return True

    def _apply_provider_result(
        self, attempt: AttemptRecord, stage: GenerationStage, result: ProviderResult
    ) -> None:
        """Apply a completed provider result for its recorded stage."""
        if result.timed_out or result.error is not None or result.content is None:
            self._fail(attempt, _PROVIDER_FAILURE_REASON)
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

    def _fail(self, attempt: AttemptRecord, reason: str) -> None:
        """Terminal-fail an attempt (FAILED is terminal, §32.3)."""
        if attempt.state in (GenerationState.PUBLISHED, GenerationState.FAILED):
            return
        assert_transition(attempt.state, GenerationState.FAILED)
        attempt.reason = reason
        attempt.state = GenerationState.FAILED
        self._release_admission(attempt)
        if attempt.pending_id is not None:
            self._pending_map.pop(attempt.pending_id, None)
            attempt.pending_id = None
            attempt.pending_stage = None

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