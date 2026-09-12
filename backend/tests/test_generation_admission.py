"""Admission / anonymous quota identity tests (Phase4 K items 16-18).

Prove, via a CountingProvider, that rejected admission consumes ZERO provider
calls and that quota identity is the anonymousQuotaSessionId ONLY — a new
creator token or a different case/attempt identity NEVER resets a session's
quota (REQUIREMENTS 32.9/32.10/32.11).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_STAGE_PAYLOADS,
)

from app.generation.admission import (  # noqa: E402
    AdmissionController,
    AdmissionDenied,
)
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import CountingProvider, FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
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


def _admission(clock, ids, **overrides):
    kwargs = dict(
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    kwargs.update(overrides)
    return AdmissionController(clock=clock, ids=ids, **kwargs)


def _golden_sync_script():
    """Stage -> [payload]: FakeProvider requires LIST values (a raw string
    would be iterated character-by-character)."""
    return {stage: [payload] for stage, payload in _G.items()}


def _counting_setup(clock, ids, admission, **overrides):
    counting = CountingProvider(FakeProvider(_golden_sync_script()))
    controller = GenerationController(
        provider=counting,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=9,
        **overrides,
    )
    return counting, controller


def test_16_admission_denied_zero_provider_calls():
    """Session window exhausted -> AdmissionDenied and call_count unchanged."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock, ids, max_generations_per_session_per_window=1
    )
    session = admission.create_anonymous_quota_session()
    counting, controller = _counting_setup(clock, ids, admission)

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    assert controller.attempt(handle.attempt_id).state is GenerationState.PUBLISHED
    calls_after_first = counting.call_count
    assert calls_after_first == 4  # four stage calls, no repairs needed

    with pytest.raises(AdmissionDenied) as excinfo:
        controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
    assert excinfo.value.decision.admitted is False
    assert "per-session generation window" in excinfo.value.decision.reason
    # The rejected admission consumed ZERO provider calls.
    assert counting.call_count == calls_after_first


def test_18_global_concurrency_denied_zero_provider_calls():
    """MAX_CONCURRENT_GENERATIONS_GLOBAL=1 -> second concurrent start denied."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock,
        ids,
        max_concurrent_generations_global=1,
        max_concurrent_generations=2,
    )
    session = admission.create_anonymous_quota_session()
    script = {GenerationStage.CASE_TRUTH: ["pending"]}
    counting = CountingProvider(FakeProvider(script))
    controller = GenerationController(
        provider=counting,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=9,
    )

    handle_a = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    assert controller.attempt(handle_a.attempt_id).state is GenerationState.GENERATING
    calls_after_a = counting.call_count
    assert calls_after_a == 1  # A holds the single global concurrency slot

    with pytest.raises(AdmissionDenied) as excinfo:
        controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
    assert "global concurrency" in excinfo.value.decision.reason
    # B made ZERO provider calls.
    assert counting.call_count == calls_after_a


def test_17_quota_identity_not_reset_by_new_creator_identity():
    """New creator_token (and new case ids) never reset per-session quota."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock, ids, max_generations_per_session_per_window=1
    )
    session = admission.create_anonymous_quota_session()
    counting, controller = _counting_setup(clock, ids, admission)

    handle_a = controller.start_generation(
        GOLDEN_PROMPT,
        anonymous_quota_session_id=session.session_id,
        creator_token="creator-alice",
    )
    case_a = controller.attempt(handle_a.attempt_id).case_id
    calls_after_first = counting.call_count

    # Same session + a DIFFERENT creator identity -> still denied.
    with pytest.raises(AdmissionDenied):
        controller.start_generation(
            GOLDEN_PROMPT,
            anonymous_quota_session_id=session.session_id,
            creator_token="creator-eve-entirely-different",
        )
    assert counting.call_count == calls_after_first

    # A FRESH anonymous session is admitted (quota identity is the session).
    fresh_session = admission.create_anonymous_quota_session()
    assert fresh_session.session_id != session.session_id
    fresh_counting, fresh_controller = _counting_setup(clock, ids, admission)
    handle_b = fresh_controller.start_generation(
        GOLDEN_PROMPT,
        anonymous_quota_session_id=fresh_session.session_id,
        creator_token="creator-alice",  # same creator, fresh session
    )
    record_b = fresh_controller.attempt(handle_b.attempt_id)
    assert record_b.state is GenerationState.PUBLISHED
    assert fresh_counting.call_count == 4  # fresh session admitted and fully ran
    assert record_b.case_id != case_a  # case/attempt identity never a quota key
    assert admission.session_generations(fresh_session.session_id) == 1
    # The original session's quota was NOT reset by the fresh-session case.
    assert admission.session_generations(session.session_id) == 1


def test_18b_session_concurrency_limit_denies():
    """MAX_CONCURRENT_GENERATIONS (per session) denies a second concurrent start."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock, ids, max_concurrent_generations=1,
    )
    session = admission.create_anonymous_quota_session()
    script = {GenerationStage.CASE_TRUTH: ["pending"]}
    counting = CountingProvider(FakeProvider(script))
    controller = GenerationController(
        provider=counting,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=9,
    )
    controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    calls_after_a = counting.call_count
    with pytest.raises(AdmissionDenied) as excinfo:
        controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
    assert "per-session concurrency" in excinfo.value.decision.reason
    assert counting.call_count == calls_after_a


def test_18c_creator_token_never_used_as_quota_key():
    """Direct admission-level proof: creator identity is ignored for quota."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids, max_generations_per_session_per_window=1)
    session = admission.create_anonymous_quota_session()
    assert admission.admit_generation(
        session.session_id, creator_token="one"
    ).admitted is True
    with pytest.raises(AdmissionDenied):
        admission.admit_generation(session.session_id, creator_token="two")
    # Reservation was released; the session window is still consumed exactly once.
    admission.release_generation(session.session_id)
    assert admission.session_generations(session.session_id) == 1
    assert admission.global_active_concurrency == 0