"""Phase 4 generation lifecycle tests (Phase4 K items 1-11, 26).

Deterministic end-to-end controller tests: ManualClock + FakeProvider +
GOLDEN fixtures, zero sleeps, zero network, zero real provider calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_LOCKED,
    GOLDEN_STAGE_PAYLOADS,
)

from app.domain.evidence import EvidenceFact  # noqa: E402
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.pipeline import STAGE_ORDER, AttemptRecord  # noqa: E402
from app.generation.provider import GenerateRequest, GenerationStage, ProviderResult  # noqa: E402
from app.generation.state_machine import GenerationState, ValidationOutcome  # noqa: E402

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

_G = GOLDEN_STAGE_PAYLOADS


def _make_admission(clock, ids, **overrides):
    kwargs = dict(
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    kwargs.update(overrides)
    return AdmissionController(clock=clock, ids=ids, **kwargs)


def _make_controller(provider, admission, clock, ids, **overrides):
    kwargs = dict(
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=11,
    )
    kwargs.update(overrides)
    return GenerationController(
        provider=provider, admission=admission, clock=clock, ids=ids, **kwargs
    )


def _golden_sync_script():
    """Stage -> [payload] scripts: FakeProvider requires LIST values (a raw
    string would be iterated character-by-character)."""
    script = {stage: [payload] for stage, payload in _G.items()}
    script[GenerationStage.REPAIR] = [GOLDEN_FULL_DRAFT]
    return script


def _resolve_pending(fake, content):
    pending = fake.pending_ids()[0]
    fake.resolve(pending, ProviderResult(content=content))


def test_01_golden_happy_path_pending_driven_by_on_completion():
    """Golden four-stage script driven via pending + CompletionSink -> PUBLISHED."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()

    script = {
        GenerationStage.CASE_TRUTH: ["pending"],
        GenerationStage.PUBLIC_WORLD: ["pending"],
        GenerationStage.EVIDENCE: ["pending"],
        GenerationStage.WORLD_GRAPH: ["pending"],
    }
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    fake._sink = controller

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    assert handle.state is GenerationState.GENERATING
    assert len(fake.pending_ids()) == 1

    for stage in STAGE_ORDER:
        _resolve_pending(fake, _G[stage])

    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    assert record.last_validation is not None
    assert record.last_validation.valid is True
    assert record.last_validation.outcome is ValidationOutcome.VALID

    published = record.published
    assert published.case_id == handle.case_id == "CASE-1"
    assert published.generation_attempt_id == handle.attempt_id == "GA-1"
    assert published.truth.crime.murderer_id == "thomas_reed"
    assert published.truth.crime.motive_id == "cover_up_embezzlement"
    assert published.truth.crime.weapon_id == "kitchen_knife"
    assert admission.session_generations(session.session_id) == 1
    assert admission.global_active_concurrency == 0


def test_02_determinism_same_seed_same_script_identical_result():
    """Same seed + same prompt + same script -> identical published summary/draft."""
    summaries = []
    drafts = []
    truths = []
    for _ in range(2):
        clock = ManualClock()
        ids = IdSource()
        admission = _make_admission(clock, ids)
        session = admission.create_anonymous_quota_session()
        fake = FakeProvider(_golden_sync_script(), seed=42)
        controller = _make_controller(fake, admission, clock, ids, seed=42)
        handle = controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
        record = controller.attempt(handle.attempt_id)
        assert record.state is GenerationState.PUBLISHED
        summaries.append(record.published.to_dict_summary())
        drafts.append(record.published.draft)
        truths.append(record.published.truth)

    assert summaries[0] == summaries[1]
    assert drafts[0] == drafts[1]
    assert truths[0] == truths[1]


def test_03_locked_constraints_unchanged():
    """GOLDEN_LOCKED passes through unrepaired; published truth matches locked ids."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    fake = FakeProvider(_golden_sync_script())
    controller = _make_controller(fake, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    assert record.locked == GOLDEN_LOCKED
    assert record.locked.violations_against(record.published.draft) == ()
    assert record.last_validation.locked_violations == ()

    truth = record.published.truth
    locked = record.published.locked
    assert truth.crime.victim_id == locked.victim
    assert truth.crime.murderer_id == locked.murderer
    assert truth.crime.motive_id == locked.motive
    assert truth.crime.weapon_id == locked.weapon
    assert truth.crime.crime_time.canonical == locked.crime_time
    witnesses = {p.person_id for p in record.published.public.persons if p.role == "witness"}
    assert locked.witness in witnesses


def test_04_malformed_evidence_triggers_repair_and_publishes():
    """EVIDENCE returns non-JSON -> RECOVERABLE_REPAIR -> REPAIR fixes -> PUBLISHED."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: ["malformed"],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]],
        GenerationStage.REPAIR: [GOLDEN_FULL_DRAFT],
    }
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    assert record.last_validation.valid is True
    assert record.budget.repair_passes == 1
    assert record.published.truth.crime.murderer_id == "thomas_reed"


def test_05_repair_succeeds_after_malformed_public_world():
    """Malformed PUBLIC_WORLD stage -> repair pass -> revalidation VALID -> PUBLISHED."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: ["malformed"],
        GenerationStage.EVIDENCE: [_G[GenerationStage.EVIDENCE]],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]],
        GenerationStage.REPAIR: [GOLDEN_FULL_DRAFT],
    }
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 1
    # The repaired payload is the FULL golden draft (evidence restored).
    assert len(record.published.evidence) == 16


def test_06_repair_budget_exhausted_fails():
    """EVIDENCE + REPAIR always malformed -> repair budget (2) exhausted -> FAILED."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: ["malformed"],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]],
        GenerationStage.REPAIR: ["malformed", "malformed"],
    }
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert "repair budget" in record.reason
    assert record.published is None
    assert record.last_validation is not None
    assert record.last_validation.outcome is ValidationOutcome.RECOVERABLE_REPAIR


def test_07_provider_exception_fails_sanitized():
    """Provider raising ProviderError -> FAILED with the sanitized reason."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {GenerationStage.CASE_TRUTH: ["exception"]}
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.reason == "provider failure: generator unavailable"
    assert "scripted failure" not in record.reason  # no raw provider message
    assert record.published is None
    assert admission.global_active_concurrency == 0


def test_08_provider_timeout_fails():
    """ProviderResult(timed_out=True) -> direct terminal FAILED, no retry."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [ProviderResult(timed_out=True)],
    }
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.reason == "provider failure: generator unavailable"
    assert record.published is None
    assert len(fake.calls) == 1  # no retry


def test_09_deadline_exceeded_on_resume_fails():
    """Deadline passes while a stage is pending; resolving later -> FAILED."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {GenerationStage.CASE_TRUTH: ["pending"]}
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    fake._sink = controller

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    assert handle.state is GenerationState.GENERATING
    clock.advance(120)
    _resolve_pending(fake, _G[GenerationStage.CASE_TRUTH])

    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert "deadline" in record.reason
    assert record.published is None
    assert record.budget.calls == 1  # the provider-call budget was consumed exactly once


def test_10_model_call_budget_exhausted_fails():
    """Endless repairable stream -> exactly max_llm_calls calls -> FAILED."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids, max_concurrent_generations=1)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: ["pending"],
        GenerationStage.PUBLIC_WORLD: ["pending"],
        GenerationStage.EVIDENCE: ["pending"],
        GenerationStage.WORLD_GRAPH: ["pending"],
        GenerationStage.REPAIR: ["pending"] * 4,
    }
    fake = FakeProvider(script)
    controller = _make_controller(
        fake, admission, clock, ids,
        max_llm_calls_per_generation=8,
        max_repair_passes=100,
        max_full_regenerations=100,
    )
    fake._sink = controller

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    content = [
        _G[GenerationStage.CASE_TRUTH],
        _G[GenerationStage.PUBLIC_WORLD],
        "<not-json>",
        _G[GenerationStage.WORLD_GRAPH],
        "<not-json>",
        "<not-json>",
        "<not-json>",
        "<not-json>",
    ]
    for chunk in content:
        _resolve_pending(fake, chunk)

    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert "model call budget" in record.reason
    assert len(fake.calls) == 8


def _evidence_without(*evidence_ids):
    doc = json.loads(_G[GenerationStage.EVIDENCE])
    doc["evidence"] = [e for e in doc["evidence"] if e["id"] not in evidence_ids]
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def test_11_regeneration_budget_exhausted_fails():
    """Ambiguous evidence twice -> single full regeneration allowed -> FAILED."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    ambiguous = _evidence_without("cctv_michael_office_01")
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]] * 2,
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]] * 2,
        GenerationStage.EVIDENCE: [ambiguous, ambiguous],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]] * 2,
    }
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert "regeneration budget" in record.reason
    assert record.budget.regenerations == 1
    assert record.published is None
    assert len(fake.calls) == 8


def test_26_golden_published_full_assertion_list():
    """Named golden PUBLISHED test with the full gate assertion list."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    fake = FakeProvider(_golden_sync_script())
    controller = _make_controller(fake, admission, clock, ids)

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    assert controller.current_attempt_id == handle.attempt_id
    published = record.published
    assert published is not None
    # Genuine four-stage golden path: no recovery was needed.
    assert record.budget.repair_passes == 0
    assert record.budget.regenerations == 0
    assert len(fake.calls) == 4

    # identity + version lock (§7.1)
    assert published.case_id == "CASE-1"
    assert published.case_version == 1
    assert published.generation_attempt_id == "GA-1"
    assert isinstance(published.published_at, float)

    # report fully clean
    report = record.last_validation
    assert report.valid is True
    assert report.outcome is ValidationOutcome.VALID
    assert report.structural_issues == ()
    assert report.safety_issues == ()
    assert report.universe_issues == ()
    assert report.locked_violations == ()
    assert report.validation is not None and report.validation.all_true is True

    # winner ids (thomas / embezzlement / kitchen knife)
    assert published.solver_proof.winners == (
        "thomas_reed",
        "cover_up_embezzlement",
        "kitchen_knife",
    )
    assert published.truth.crime.murderer_id == "thomas_reed"
    assert published.truth.crime.motive_id == "cover_up_embezzlement"
    assert published.truth.crime.weapon_id == "kitchen_knife"

    # universes non-empty (§31.1/31.1.4)
    assert published.universes.suspect_ids == (
        "anna_karlsson",
        "michael_carter",
        "thomas_reed",
    )
    assert len(published.universes.motive_ids) == 3
    assert len(published.universes.weapon_ids) == 3

    # time proof (§31.8/31.14)
    assert published.solver_proof.time.connected_count == 1
    assert published.solver_proof.time.ambiguous is False
    assert published.solver_proof.time.overconstrained is False
    assert published.validation.time_accepted is True

    # structural/safety validation exercised (golden round-trip)
    assert all(isinstance(fact, EvidenceFact) for fact in published.evidence)
    assert len(published.evidence) == 16
    assert published.locked == GOLDEN_LOCKED

    # admission released after publication
    assert admission.session_generations(session.session_id) == 1
    assert admission.global_active_concurrency == 0


def test_golden_draft_assembly_mapping_equivalence():
    """pipeline.assemble over the golden draft matches the GOLDEN draft + truth.

    Guards the phase-3 reassembly used for publication against silent drift.
    """
    from app.generation.pipeline import AttemptRecord, apply_stage_output, assemble

    attempt = AttemptRecord(
        attempt_id="GA-x", case_id="CASE-001", session_id="QUOTA-x"
    )
    for stage in STAGE_ORDER:
        apply_stage_output(attempt, stage, _G[stage])
    public, evidence, truth, draft = assemble(attempt)
    assert draft == attempt.draft
    # The draft's crime IS the reassembled truth's crime (mapping equivalence).
    assert truth.crime.murderer_id == draft.crime.murderer_id
    assert truth.crime.motive_id == draft.crime.motive_id
    assert truth.crime.weapon_id == draft.crime.weapon_id
    assert public.case_id == "CASE-001"
    assert public.case_version == 1


# ---------------------------------------------------------------------------
# DEF-035 — validate_draft's incomplete-draft path must store last_validation
# ---------------------------------------------------------------------------


def _case_truth_with_bogus_key():
    doc = json.loads(_G[GenerationStage.CASE_TRUTH])
    doc["weaponHint"] = "a sharp object"
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def test_def035_case_truth_parse_failure_sets_last_validation():
    """DEF-035: CASE_TRUTH parse failure -> FAILED keeps an observable report.

    The attempted repair budget is exhausted after 2 malformed REPAIR passes,
    but the FAILED attempt must still carry the last RECOVERABLE_REPAIR report
    (the incomplete-draft early-return must store it) and the failure reason
    must be consistent with that stored report.
    """
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [_case_truth_with_bogus_key()],
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: [_G[GenerationStage.EVIDENCE]],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]],
        GenerationStage.REPAIR: ["malformed", "malformed"],
    }
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert "repair budget" in record.reason
    # Observability: the stored report reflects the deterministic recovery path.
    assert record.last_validation is not None
    assert record.last_validation.outcome is ValidationOutcome.RECOVERABLE_REPAIR
    assert any("unknown key" in issue for issue in record.last_validation.structural_issues)
    assert record.last_validation.safety_issues == ()
    assert record.published is None


# ---------------------------------------------------------------------------
# DEF-036 — publish() must re-check the generation deadline before the CAS
# ---------------------------------------------------------------------------


def test_def036_publish_after_deadline_is_refused_and_fails_attempt():
    """DEF-036: a held VALIDATING attempt past its deadline can never publish."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: [_G[GenerationStage.EVIDENCE]],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]],
    }
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids, hold_before_publish=True)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.VALIDATING
    assert record.last_validation.valid is True

    clock.advance(1000)  # far past the 60s deadline
    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success is False
    assert result.reason == "generation deadline exceeded"
    assert result.published is None
    # The attempt is terminal-failed and nothing was published.
    assert record.state is GenerationState.FAILED
    assert "deadline" in record.reason
    assert record.published is None
    assert record.published_at is None
    assert admission.global_active_concurrency == 0


def test_def036_publish_before_deadline_still_succeeds():
    """DEF-036: hold-publish BEFORE the deadline remains successful."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: [_G[GenerationStage.EVIDENCE]],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]],
    }
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids, hold_before_publish=True)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    clock.advance(30)  # still inside the 60s deadline
    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success is True
    assert result.published is not None
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None


# ---------------------------------------------------------------------------
# DEF-037 — on_completion must not mutate the draft after the deadline
# ---------------------------------------------------------------------------


def test_def037_on_completion_after_deadline_does_not_mutate_draft():
    """DEF-037: resolving a pending result after the deadline FAILs WITHOUT
    applying the result to the draft (no stage_outputs mutation)."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {GenerationStage.CASE_TRUTH: ["pending"]}
    fake = FakeProvider(script)
    controller = _make_controller(fake, admission, clock, ids)
    fake._sink = controller

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    assert handle.state is GenerationState.GENERATING
    pending = fake.pending_ids()[0]

    clock.advance(120)  # deadline passed while the stage was pending
    fake.resolve(pending, ProviderResult(content=_G[GenerationStage.CASE_TRUTH]))

    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert "deadline" in record.reason
    # The stage output must NOT have been applied.
    assert GenerationStage.CASE_TRUTH not in record.stage_outputs
    assert record.draft is None
    assert record.published is None


# ---------------------------------------------------------------------------
# DEF-038 — generic provider-path exceptions must not leak the reservation
# ---------------------------------------------------------------------------


class _ExplodingProvider:
    """Raises ``exc`` on the first call, then serves content for every stage."""

    def __init__(self, exc, stage_payloads):
        self._exc = exc
        self._payloads = stage_payloads
        self.fail_on_first = True
        self.calls = 0

    def generate(self, request: GenerateRequest) -> ProviderResult:
        self.calls += 1
        if self.fail_on_first:
            self.fail_on_first = False
            raise self._exc("scripted generic failure")
        return ProviderResult(content=self._payloads[request.stage])


@pytest.mark.parametrize("exc", [KeyError, ValueError])
def test_def038_generic_provider_exception_releases_reservation_and_propagates(exc):
    """DEF-038: ANY non-ProviderError exception in the provider path must FAIL
    the attempt, release its admission reservation, and still propagate to the
    caller — so later generations in a fresh session are admitted again."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(
        clock,
        ids,
        max_concurrent_generations_global=1,  # one leaked slot would deadlock
        max_generations_per_session_per_window=10,
    )
    session1 = admission.create_anonymous_quota_session()
    provider = _ExplodingProvider(exc, _G)
    controller = _make_controller(provider, admission, clock, ids)

    with pytest.raises(exc):
        controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session1.session_id
        )
    record = controller.attempt("GA-1")
    assert record is not None
    assert record.state is GenerationState.FAILED
    assert record.reason == "provider failure: generator unavailable"
    assert record.published is None
    # Reservation must be fully released despite the propagated exception.
    assert admission.global_active_concurrency == 0
    assert admission.active_concurrency(session1.session_id) == 0

    # A subsequent generation in a NEW session is admitted and completes.
    session2 = admission.create_anonymous_quota_session()
    handle2 = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session2.session_id
    )
    record2 = controller.attempt(handle2.attempt_id)
    assert record2.state is GenerationState.PUBLISHED
    assert record2.published is not None


def test_def038_build_request_exception_also_releases_reservation(monkeypatch):
    """DEF-038: an exception raised while BUILDING the request (inside the same
    try) must behave identically: terminal FAILED + released + propagated."""
    from app.generation import pipeline as pipeline_module

    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(
        clock,
        ids,
        max_concurrent_generations_global=1,
        max_generations_per_session_per_window=10,
    )
    session1 = admission.create_anonymous_quota_session()

    provider = FakeProvider({})
    controller = _make_controller(provider, admission, clock, ids)
    requested = []

    def _exploding_build_request(attempt, stage, *, diagnostics=()):
        requested.append(stage)
        raise ValueError("request build exploded")

    monkeypatch.setattr(pipeline_module, "build_request", _exploding_build_request)

    with pytest.raises(ValueError):
        controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session1.session_id
        )
    record = controller.attempt("GA-1")
    assert record.state is GenerationState.FAILED
    assert record.reason == "provider failure: generator unavailable"
    assert admission.global_active_concurrency == 0
    # The provider itself was never reached (the failure happened pre-call).
    assert len(provider.calls) == 0


# ---------------------------------------------------------------------------
# DEF-039 — bound retained attempt records
# ---------------------------------------------------------------------------


def test_def039_retention_cap_bounds_attempt_records():
    """DEF-039: >100 start/fail cycles keep at most max_retained_attempts."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(
        clock,
        ids,
        max_generations_per_session_per_window=500,
        max_generations_global_per_window=1000,
    )
    session = admission.create_anonymous_quota_session()
    fake = FakeProvider({GenerationStage.CASE_TRUTH: ["exception"] * 300})
    controller = _make_controller(fake, admission, clock, ids)
    last = None
    for _ in range(150):
        last = controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
    assert len(controller._attempts) <= 100
    # The current (most recent) attempt is always retained.
    current = controller.attempt(controller.current_attempt_id)
    assert current is not None
    assert current.attempt_id == last.attempt_id
    assert current.state is GenerationState.FAILED
    # Every retained record is terminal or the current attempt.
    for record in controller._attempts.values():
        assert record.state in (GenerationState.PUBLISHED, GenerationState.FAILED)
        assert record.attempt_id in controller._attempts


def test_def039_never_prunes_non_terminal_current_attempt():
    """DEF-039: the prune keeps the current non-terminal attempt and only ever
    drops terminal records."""
    clock = ManualClock()
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    fake = FakeProvider({GenerationStage.CASE_TRUTH: ["pending"]})
    controller = _make_controller(fake, admission, clock, ids, max_retained_attempts=5)
    fake._sink = controller
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    current = controller.attempt(handle.attempt_id)
    assert current.state is GenerationState.GENERATING  # non-terminal + current

    # Inflate the map with far older TERMINAL records, then prune.
    for i in range(150):
        controller._attempts[f"OLD-{i}"] = AttemptRecord(
            attempt_id=f"OLD-{i}",
            case_id="CASE-x",
            session_id="QUOTA-x",
            state=GenerationState.FAILED,
        )
    controller._prune_retained_attempts()

    assert controller.attempt(handle.attempt_id) is current  # never pruned
    assert len(controller._attempts) <= 5
    assert GenerationState.GENERATING in {r.state for r in controller._attempts.values()}