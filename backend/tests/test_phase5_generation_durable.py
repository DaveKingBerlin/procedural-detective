"""Phase 5 — durable generation: admission, FAILED terminal, restart survival
(M8, M9, M11, M12, E.1.3/E.1.6, REQUIREMENTS 32.10).

- admission runs BEFORE any provider call; a denial performs ZERO provider
  calls and consumes no quota budget
- ``generations_count`` lives on the durable session row and survives restart
- generation progress (status/stage/progress) is persisted, so it and the
  terminal FAILED state survive an application restart
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.generation.fake_provider import CountingProvider, FakeProvider
from app.generation.provider import GenerationStage
from phase5_helpers import assert_sanitized_error

_MALFORMED_SCRIPT = {
    stage: ["malformed"]
    for stage in (
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
        GenerationStage.REPAIR,
    )
}


def _settings(url: str, **overrides):
    from app.core.config import Settings

    kwargs = dict(
        database_url=url,
        max_generations_per_session_per_window=8,
        max_concurrent_generations=2,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def _service(store, settings, provider_factory):
    from app.services.generation import GenerationService

    return GenerationService(settings=settings, store=store, provider_factory=provider_factory)


def test_admission_denied_makes_zero_provider_calls(store, database_url):
    """E.1.3: a rejected admission consumes ZERO provider calls."""
    from app.services.generation import AdmissionDeniedError

    provider = FakeProvider(
        {stage: ["pending"] for stage in (
            GenerationStage.CASE_TRUTH,
            GenerationStage.PUBLIC_WORLD,
        )}
    )
    counting = CountingProvider(provider)
    service = _service(
        store,
        _settings(database_url, max_generations_per_session_per_window=1),
        lambda: counting,
    )
    session = service.create_anonymous_quota_session()
    # First request fully admitted (and fails hard on script exhaustion —
    # irrelevant here; we only count).
    try:
        service.start_case_generation(
            "Victim: sarah_miller\nMurderer: thomas_reed\n",
            anonymous_quota_session_id=session.anonymous_quota_session_id,
        )
    except Exception:  # noqa: BLE001 - the run may fail; the count is what matters
        pass
    used_so_far = counting.call_count
    counting.call_count = 0
    with pytest.raises(AdmissionDeniedError):
        service.start_case_generation(
            "A second attempt over the exhausted window",
            anonymous_quota_session_id=session.anonymous_quota_session_id,
        )
    # Zero provider calls on the denied path (REQUIREMENTS 32.10).
    assert counting.call_count == 0
    assert used_so_far > 0  # the first request really did call the provider


def test_11_progress_survives_restart(store, database_url):
    """M11: generation progress is durable across a restart."""
    settings = _settings(database_url)
    service = _service(store, settings, None)
    session = service.create_anonymous_quota_session()
    started = service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert started.status == "PUBLISHED"
    progress_before = service.get_generation_progress(
        started.generation_id, case_id=started.case_id
    )
    assert progress_before["status"] == "PUBLISHED"
    assert progress_before["progress"] == 100
    store.dispose()

    from app.persistence.store import Store

    store2 = Store(database_url)
    service2 = _service(store2, settings, None)
    try:
        progress_after = service2.get_generation_progress(
            started.generation_id, case_id=started.case_id
        )
        assert progress_after == progress_before
        attempt = store2.get_generation_attempt_by_generation_id(
            started.generation_id, case_id=started.case_id
        )
        assert attempt.status == "PUBLISHED"
        version_row = store2.get_case_version(started.case_id, 1)
        assert version_row.state == "PUBLISHED"
    finally:
        store2.dispose()


def test_12_failed_is_terminal_and_survives_restart(store, database_url):
    """M12: FAILED remains terminal after restart (REQUIREMENTS 32.3)."""
    settings = _settings(database_url)
    service = _service(store, settings, lambda: FakeProvider(script=_MALFORMED_SCRIPT))
    session = service.create_anonymous_quota_session()
    started = service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert started.status == "FAILED"
    progress = service.get_generation_progress(
        started.generation_id, case_id=started.case_id
    )
    assert progress["status"] == "FAILED"
    assert progress["progress"] == 100
    store.dispose()

    from app.persistence.store import Store

    store2 = Store(database_url)
    service2 = _service(store2, settings, None)
    try:
        after = service2.get_generation_progress(
            started.generation_id, case_id=started.case_id
        )
        assert after["status"] == "FAILED"
        version_row = store2.get_case_version(started.case_id, 1)
        assert version_row.state == "FAILED"
        assert version_row.state_reason is not None  # sanitized terminal reason
    finally:
        store2.dispose()


def test_08_quota_keyed_by_anonymous_session_across_cases(store, database_url):
    """M8: multiple cases consume ONE shared session quota (32.9 shape)."""
    from app.services.generation import AdmissionDeniedError

    service = _service(
        store,
        _settings(database_url, max_generations_per_session_per_window=2),
        None,
    )
    session = service.create_anonymous_quota_session()
    first = service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    second = service.start_case_generation(
        "Another locked mystery",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert first.case_id != second.case_id
    assert store.get_session(session.anonymous_quota_session_id).generations_count == 2
    with pytest.raises(AdmissionDeniedError):
        service.start_case_generation(
            "Third attempt over the window",
            anonymous_quota_session_id=session.anonymous_quota_session_id,
        )
    # A FRESH session has its own window -> admitted.
    fresh = service.create_anonymous_quota_session()
    admitted = service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=fresh.anonymous_quota_session_id,
    )
    assert admitted.status == "PUBLISHED"


def test_api_failed_case_is_terminal_and_hidden(phase5_app):
    """FAILED through the API: status FAILED, not published, no leak."""
    def _failing_factory():
        return FakeProvider(script=_MALFORMED_SCRIPT)

    service = phase5_app.state.generation_service
    original = service._provider_factory
    service._provider_factory = _failing_factory
    try:
        from fastapi.testclient import TestClient

        from phase5_helpers import create_case, create_session

        with TestClient(phase5_app) as client:
            session_token, _ = create_session(client)
            created = create_case(client, session_token)
            assert created["status"] == "FAILED"
            case_id = created["caseId"]
            creator = created["creatorAccessToken"]
            res = client.get(
                f"/api/v1/cases/{case_id}",
                headers={"Authorization": f"Bearer {creator}"},
            )
            assert res.status_code == 404  # no published version exists
            res = client.get(
                f"/api/v1/cases/{case_id}?version=1",
                headers={"Authorization": f"Bearer {creator}"},
            )
            assert res.status_code == 409  # exists but not PUBLISHED
            assert res.json()["error"]["code"] == "VERSION_NOT_PUBLISHED"
            res = client.get(
                f"/api/v1/generations/{created['generationId']}",
                headers={"Authorization": f"Bearer {creator}"},
            )
            assert res.status_code == 200
            assert res.json()["status"] == "FAILED"
            assert_sanitized_error(res.text)
            # No silent resurrection: a retry creates a NEW attempt, never a
            # transition out of FAILED for the old version.
            second = create_case(client, session_token)
            assert second["caseId"] != case_id
    finally:
        service._provider_factory = original