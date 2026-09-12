"""Generation attempt race/CAS matrix (Phase4 K items 12-15, 27-28).

Deterministic race tests: ManualClock + FakeProvider pending futures drive
attempt supersede / stale-result / duplicate-completion / publish-CAS /
immutability scenarios with zero sleeps.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from dataclasses import FrozenInstanceError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_LOCKED,
    GOLDEN_STAGE_PAYLOADS,
)

from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage, ProviderResult  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

_G = GOLDEN_STAGE_PAYLOADS


def _permissive_admission(clock, ids, *, session_concurrency=2):
    return AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=session_concurrency,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )


def _controller(provider, admission, clock, ids, **overrides):
    kwargs = dict(
        deadline_seconds=60,
        max_llm_calls_per_generation=16,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=3,
    )
    kwargs.update(overrides)
    return GenerationController(
        provider=provider, admission=admission, clock=clock, ids=ids, **kwargs
    )


def test_12_stale_attempt_result_discarded():
    """A (pending) superseded by B (PUBLISHED); A's late result changes nothing."""
    clock = ManualClock()
    ids = IdSource()
    admission = _permissive_admission(clock, ids)
    session = admission.create_anonymous_quota_session()

    script = {
        GenerationStage.CASE_TRUTH: ["pending", _G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: [_G[GenerationStage.EVIDENCE]],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]],
    }
    fake = FakeProvider(script)
    controller = _controller(fake, admission, clock, ids)

    handle_a = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record_a = controller.attempt(handle_a.attempt_id)
    assert record_a.state is GenerationState.GENERATING
    assert len(fake.pending_ids()) == 1
    pending_a = fake.pending_ids()[0]

    handle_b = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record_a = controller.attempt(handle_a.attempt_id)
    assert record_a.state is GenerationState.FAILED
    assert record_a.reason == "superseded by newer attempt"
    record_b = controller.attempt(handle_b.attempt_id)
    assert record_b.state is GenerationState.PUBLISHED
    assert record_b.published is not None

    # Resolve A's stale pending result -> must be discarded (zero mutation).
    fake.resolve(
        pending_a, ProviderResult(content=_G[GenerationStage.CASE_TRUTH])
    )
    record_a = controller.attempt(handle_a.attempt_id)
    record_b = controller.attempt(handle_b.attempt_id)
    assert record_a.state is GenerationState.FAILED
    assert record_a.reason == "superseded by newer attempt"
    assert record_b.state is GenerationState.PUBLISHED
    assert record_b.published is not None
    assert handle_a.attempt_id != handle_b.attempt_id
    assert admission.global_active_concurrency == 0


def test_13_stale_repair_result_discarded():
    """A in REPAIRING (pending) superseded by B; A's repair completion is discarded."""
    clock = ManualClock()
    ids = IdSource()
    admission = _permissive_admission(clock, ids)
    session = admission.create_anonymous_quota_session()

    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]] * 2,
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]] * 2,
        GenerationStage.EVIDENCE: ["malformed", _G[GenerationStage.EVIDENCE]],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]] * 2,
        GenerationStage.REPAIR: ["pending", GOLDEN_FULL_DRAFT],
    }
    fake = FakeProvider(script)
    controller = _controller(fake, admission, clock, ids)
    fake._sink = controller

    handle_a = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record_a = controller.attempt(handle_a.attempt_id)
    # A: 4 stage calls then REPAIR -> pending (5th call) -> REPAIRING state.
    assert record_a.state is GenerationState.REPAIRING
    assert len(fake.pending_ids()) == 1
    pending_repair_a = fake.pending_ids()[0]

    handle_b = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record_a = controller.attempt(handle_a.attempt_id)
    assert record_a.state is GenerationState.FAILED
    assert record_a.reason == "superseded by newer attempt"
    record_b = controller.attempt(handle_b.attempt_id)
    assert record_b.state is GenerationState.PUBLISHED

    # Resolve A's stale REPAIR completing with the golden full draft: discarded.
    fake.resolve(pending_repair_a, ProviderResult(content=GOLDEN_FULL_DRAFT))
    record_a = controller.attempt(handle_a.attempt_id)
    record_b = controller.attempt(handle_b.attempt_id)
    assert record_a.state is GenerationState.FAILED
    assert record_b.state is GenerationState.PUBLISHED
    assert record_b.published is not None
    assert record_a.published is None


def test_14_old_attempt_cannot_publish_after_newer():
    """Hold-mode: A validated (awaiting), B supersedes+publishes; publish(A) refused."""
    clock = ManualClock()
    ids = IdSource()
    admission = _permissive_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]] * 2,
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]] * 2,
        GenerationStage.EVIDENCE: [_G[GenerationStage.EVIDENCE]] * 2,
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]] * 2,
    }
    fake = FakeProvider(script)
    controller = _controller(fake, admission, clock, ids, hold_before_publish=True)

    handle_a = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record_a = controller.attempt(handle_a.attempt_id)
    # A reached VALIDATING and is HELD (no PUBLISHED yet).
    assert record_a.state is GenerationState.VALIDATING
    assert record_a.last_validation.valid is True

    # B supersedes A and publishes.
    handle_b = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record_a = controller.attempt(handle_a.attempt_id)
    assert record_a.state is GenerationState.FAILED

    result_b = controller.publish(handle_b.attempt_id, hold_ok=True)
    assert result_b.success is True
    assert controller.attempt(handle_b.attempt_id).state is GenerationState.PUBLISHED

    # A can never publish later (not active / not VALIDATING).
    result_a = controller.publish(handle_a.attempt_id, hold_ok=True)
    assert result_a.success is False
    assert controller.attempt(handle_a.attempt_id).state is GenerationState.FAILED
    assert controller.attempt(handle_a.attempt_id).published is None

    # Exactly one published payload (B's).
    published_attempts = [
        a for a in (record_a, controller.attempt(handle_b.attempt_id)) if a.published is not None
    ]
    assert len(published_attempts) == 1
    assert published_attempts[0].attempt_id == handle_b.attempt_id


def test_15_duplicate_completion_is_idempotent():
    """The same pending resolved twice -> the second completion is a no-op."""
    clock = ManualClock()
    ids = IdSource()
    admission = _permissive_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: ["pending"],
        GenerationStage.PUBLIC_WORLD: ["pending"],
        GenerationStage.EVIDENCE: [_G[GenerationStage.EVIDENCE]],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]],
    }
    fake = FakeProvider(script)
    controller = _controller(fake, admission, clock, ids)
    fake._sink = controller

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    pending_first = fake.pending_ids()[0]
    fake.resolve(
        pending_first, ProviderResult(content=_G[GenerationStage.CASE_TRUTH])
    )
    # Machine advanced to the SECOND pending (PUBLIC_WORLD).
    assert len(fake.pending_ids()) == 1
    assert len(fake.calls) == 2
    pending_second = fake.pending_ids()[0]
    assert pending_second != pending_first

    # Duplicate completion of the consumed first pending: harmless no-op.
    controller.on_completion(
        pending_first, ProviderResult(content="<not-json>")
    )
    assert len(fake.calls) == 2  # no new provider call
    assert fake.pending_ids() == (pending_second,)

    # Drive to completion exactly once and confirm PUBLISHED.
    controller.on_completion(
        pending_second, ProviderResult(content=_G[GenerationStage.PUBLIC_WORLD])
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None


def test_27_late_provider_result_after_failed_is_discarded():
    """A FAILED (provider-level failure); outstanding completion cannot revive it."""
    clock = ManualClock()
    ids = IdSource()
    admission = _permissive_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {GenerationStage.CASE_TRUTH: ["pending"]}
    fake = FakeProvider(script)
    controller = _controller(fake, admission, clock, ids)
    fake._sink = controller

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    pending = fake.pending_ids()[0]

    # The outstanding completion itself carries a provider-level failure ->
    # the attempt FAILS directly with the sanitized reason (§32.4).
    fake.resolve(pending, ProviderResult(timed_out=True))
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.reason == "provider failure: generator unavailable"
    assert record.published is None

    # A LATER completion for the same (already consumed) pending with valid
    # content arrives -> discarded; FAILED remains terminal.
    controller.on_completion(
        pending, ProviderResult(content=GOLDEN_FULL_DRAFT)
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.draft is None or record.state is GenerationState.FAILED


def test_28_published_payload_is_immutable():
    """Published payload + truth + evidence are frozen; late callbacks are no-ops."""
    clock = ManualClock()
    ids = IdSource()
    admission = _permissive_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: [_G[GenerationStage.EVIDENCE]],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]],
    }
    fake = FakeProvider(script)
    controller = _controller(fake, admission, clock, ids)

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    published = record.published
    assert published is not None

    with pytest.raises(FrozenInstanceError):
        published.case_id = "HACKED"
    with pytest.raises(FrozenInstanceError):
        published.draft = None
    with pytest.raises(FrozenInstanceError):
        published.evidence = ()
    with pytest.raises(FrozenInstanceError):
        published.truth = None
    with pytest.raises(FrozenInstanceError):
        published.report = None
    with pytest.raises(FrozenInstanceError):
        published.truth.case_id = "HACKED"
    with pytest.raises(FrozenInstanceError):
        published.truth.crime.murderer_id = "anna_karlsson"
    with pytest.raises(FrozenInstanceError):
        published.draft.crime = None
    with pytest.raises(TypeError):
        published.evidence[0] = published.evidence[0]  # tuple is immutable

    # A late completion for the published attempt is a no-op; payload identical.
    controller.on_completion(
        "never-a-real-pending", ProviderResult(content=GOLDEN_FULL_DRAFT)
    )
    assert controller.attempt(handle.attempt_id).published is published
    assert controller.attempt(handle.attempt_id).state is GenerationState.PUBLISHED


# ---------------------------------------------------------------------------
# DEF-043 (ADV-130) — pending_id uniqueness / cross-attempt poisoning
# ---------------------------------------------------------------------------


class _ReusingPendingIdProvider:
    """Returns the SAME pending_id for every call (protocol violation)."""

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return ProviderResult(pending=True, pending_id="shared")


def test_def043_pending_id_reuse_is_a_protocol_violation():
    """DEF-043: a provider reusing a pending id may never poison a newer
    attempt — the second registration fails that attempt instead of linking,
    and a late completion of the consumed id is a zero-mutation no-op."""
    clock = ManualClock()
    ids = IdSource()
    admission = _permissive_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    provider = _ReusingPendingIdProvider()
    controller = _controller(provider, admission, clock, ids)

    handle_a = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record_a = controller.attempt(handle_a.attempt_id)
    assert record_a.state is GenerationState.GENERATING  # "shared" registered once

    # B supersedes A; B's provider call returns the SAME pending id -> the
    # controller must treat it as a protocol violation (never cross-link).
    handle_b = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record_a = controller.attempt(handle_a.attempt_id)
    assert record_a.state is GenerationState.FAILED
    assert record_a.reason == "superseded by newer attempt"
    record_b = controller.attempt(handle_b.attempt_id)
    assert record_b.state is GenerationState.FAILED
    assert record_b.reason == "provider failure: generator unavailable"
    assert record_b.pending_id is None
    assert "shared" in controller._used_pending_ids

    # A late completion for the consumed id is a no-op: the CURRENT attempt
    # (B) is never mutated by A's deferred result.
    controller.on_completion("shared", ProviderResult(content=GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH]))
    record_b = controller.attempt(handle_b.attempt_id)
    assert record_b.state is GenerationState.FAILED
    assert record_b.draft is None
    assert record_b.stage_outputs == {}
    assert record_b.published is None

    # The golden happy path still works with globally-fresh pending ids.
    clock2 = ManualClock()
    ids2 = IdSource()
    admission2 = _permissive_admission(clock2, ids2)
    session2 = admission2.create_anonymous_quota_session()
    fake = FakeProvider(
        {
            GenerationStage.CASE_TRUTH: ["pending"],
            GenerationStage.PUBLIC_WORLD: ["pending"],
            GenerationStage.EVIDENCE: ["pending"],
            GenerationStage.WORLD_GRAPH: ["pending"],
        }
    )
    controller2 = _controller(fake, admission2, clock2, ids2)
    fake._sink = controller2
    handle = controller2.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session2.session_id
    )
    for stage in (GenerationStage.CASE_TRUTH, GenerationStage.PUBLIC_WORLD,
                  GenerationStage.EVIDENCE, GenerationStage.WORLD_GRAPH):
        pending = fake.pending_ids()[0]
        fake.resolve(pending, ProviderResult(content=_G[stage]))
    assert controller2.attempt(handle.attempt_id).state is GenerationState.PUBLISHED


def test_def043_pending_id_none_is_rejected_and_released():
    """DEF-043: a pending result without a pending id can never be registered.

    The provider primitive already forbids constructing such a result, so a
    provider attempting it raises ValueError — which must fail the attempt,
    release its reservation, and propagate (DEF-038 semantics).
    """
    clock = ManualClock()
    ids = IdSource()
    admission = _permissive_admission(clock, ids)
    session = admission.create_anonymous_quota_session()

    class _NoIdProvider:
        def generate(self, request):
            return ProviderResult(pending=True, pending_id=None)  # raises ValueError

    controller = _controller(_NoIdProvider(), admission, clock, ids)
    with pytest.raises(ValueError):
        controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
    record = controller.attempt("GA-1")
    assert record.state is GenerationState.FAILED
    assert record.reason == "provider failure: generator unavailable"
    assert record.published is None
    assert admission.global_active_concurrency == 0