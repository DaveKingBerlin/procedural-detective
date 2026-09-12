"""Phase 5 — anonymous quota sessions (M1, M4, M8, M9).

- M1  anonymous session creation
- M4  token stored only as a verifier, never recoverably in plaintext
- M8  generation admission still keyed by anonymousQuotaSessionId
- M9  a new creator credential NEVER resets the anonymous-session quota
- 401 SESSION_EXPIRED for expired sessions
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.auth.tokens import (
    issue_anonymous_session_token,
    verifier as token_verifier,
)
from phase5_helpers import (
    assert_no_hidden_leaks,
    assert_sanitized_error,
    create_case,
    create_session,
)


def test_01_anonymous_session_creation_returns_token_in_window(phase5_app):
    with TestClient(phase5_app) as client:
        token, body = create_session(client)
    assert set(body.keys()) == {"anonymousSessionToken", "quotaWindowEndsAt"}
    assert 20 <= len(token) <= 256
    assert body["quotaWindowEndsAt"] > 0
    # An anonymous session token leaks no hidden fields (creation time).
    assert_no_hidden_leaks(
        body,
        allow_token_keys=frozenset({"anonymousSessionToken"}),
        known_tokens={token},
    )


def test_02_session_token_stored_only_as_verifier_db_row(phase5_app):
    with TestClient(phase5_app) as client:
        token, _body = create_session(client)
    store = phase5_app.state.store
    row = store.get_session_by_verifier(token_verifier(token))
    assert row is not None
    # Raw token is NOT stored recoverably (M4); only sha256 hex lives there.
    assert row.token_verifier != token
    assert row.token_verifier == token_verifier(token)
    assert row.generations_count == 0


def test_03_expired_session_answers_401_session_expired(phase5_app, database_url):
    store = phase5_app.state.store
    clock = phase5_app.state.clock
    token = issue_anonymous_session_token()
    now = float(clock.now())
    store.create_session(
        session_id="QUOTA-EXPIRED-1",
        token_verifier=token_verifier(token),
        quota_window_end=now - 60,  # already expired
        created_at=now - 7200,
        generations_count=0,
    )
    with TestClient(phase5_app) as client:
        res = client.post(
            "/api/v1/cases",
            json={"prompt": "A mystery"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "SESSION_EXPIRED"
    assert_sanitized_error(res.text)


def test_04_unknown_session_token_answers_401_unauthorized(phase5_app):
    with TestClient(phase5_app) as client:
        res = client.post(
            "/api/v1/cases",
            json={"prompt": "A mystery"},
            headers={"Authorization": f"Bearer {issue_anonymous_session_token()}"},
        )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


def test_05_missing_or_malformed_bearer_answers_401(phase5_app):
    with TestClient(phase5_app) as client:
        res = client.post("/api/v1/cases", json={"prompt": "A mystery"})
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "UNAUTHORIZED"
        res = client.post(
            "/api/v1/cases",
            json={"prompt": "A mystery"},
            headers={"Authorization": "Basic abc"},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "UNAUTHORIZED"


def test_08_quota_durable_and_keyed_by_session_through_api(phase5_app):
    """M8: creating a case increments the durable session generation counter."""
    store = phase5_app.state.store
    with TestClient(phase5_app) as client:
        token, _body = create_session(client)
        row_before = store.get_session_by_verifier(token_verifier(token))
        create_case(client, token)
    row_after = store.get_session_by_verifier(token_verifier(token))
    assert row_before.generations_count == 0
    assert row_after.generations_count == 1


def test_09_new_creator_credential_does_not_reset_quota(phase5_app):
    """M9: issuing creator credentials (cases) never resets the session quota.

    A dedicated service with a per-session window of 1 (over the same file) is
    used to prove that a THIRD admitted case is impossible even though the
    second request would mint brand-new creator credentials.
    """
    from app.core.config import Settings
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    store = Store(phase5_app.state.store.url)
    service = GenerationService(
        settings=Settings(
            database_url=phase5_app.state.store.url,
            max_generations_per_session_per_window=1,
            max_concurrent_generations=1,
        ),
        store=store,
    )
    try:
        session = service.create_anonymous_quota_session()
        first = service.start_case_generation(
            "Victim: sarah_miller\nMurderer: thomas_reed\n",
            anonymous_quota_session_id=session.anonymous_quota_session_id,
        )
        assert first.status == "PUBLISHED"
        # A second case under the SAME session is denied (quota exhausted),
        # even though it would create a NEW creator credential.
        from app.services.generation import AdmissionDeniedError

        with pytest.raises(AdmissionDeniedError):
            service.start_case_generation(
                "A hotel mystery",
                anonymous_quota_session_id=session.anonymous_quota_session_id,
            )
        # No partial case row for the denied attempt.
        assert store.get_case("CASE-2") is None
        row = store.get_session(session.anonymous_quota_session_id)
        assert row.generations_count == 1
    finally:
        store.dispose()


def test_10_session_counters_survive_restart_and_stay_capped(phase5_app, database_url):
    """A fresh service + store over the same file rehydrates the quota.

    Session created and one generation used; dispose; reopen; the second
    generation is still admitted with persisted count=1 under a window of 2,
    and the third is denied.
    """
    from app.core.config import Settings
    from app.persistence.store import Store
    from app.services.generation import AdmissionDeniedError, GenerationService

    settings = Settings(
        database_url=database_url,
        max_generations_per_session_per_window=2,
        max_concurrent_generations=2,
    )

    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    session = service.create_anonymous_quota_session()
    first = service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert first.status == "PUBLISHED"
    store.dispose()

    # Restart: fresh store + service over the SAME file.
    store2 = Store(database_url)
    service2 = GenerationService(settings=settings, store=store2)
    second = service2.start_case_generation(
        "Murderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert second.status == "PUBLISHED"
    with pytest.raises(AdmissionDeniedError):
        service2.start_case_generation(
            "A different boat mystery",
            anonymous_quota_session_id=session.anonymous_quota_session_id,
        )
    assert store2.get_session(session.anonymous_quota_session_id).generations_count == 2
    store2.dispose()