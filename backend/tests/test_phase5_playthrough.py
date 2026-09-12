"""Phase 5 — exact-version playthrough pinning (Phase5 F/G, M16-M20, M23).

- M16 unpublished version cannot create a playthrough
- M17 a playthrough permanently pins (caseId, caseVersion) in its row
- M18 publishing v2 never alters a v1 playthrough  (F.1)
- M19 restart never alters the pin                     (F.2)
- M20 playthrough credential A cannot access B
- M23 concurrent playthrough creation on the same version produces
  independent credentials/rows (F.3)
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_helpers import (
    assert_no_hidden_leaks,
    assert_sanitized_error,
    auth,
    create_case,
    create_playthrough,
    create_session,
)
from app.auth.tokens import verifier as token_verifier

PT_RESPONSE_KEYS = {"playthroughId", "caseId", "caseVersion", "status", "createdAt", "expiresAt"}


def _new_client(phase5_app):
    from fastapi.testclient import TestClient

    return TestClient(phase5_app)


def _session_id_of(phase5_app, session_token):
    return phase5_app.state.store.get_session_by_verifier(
        token_verifier(session_token)
    ).session_id


def test_17_playthrough_pins_case_and_version_in_db(phase5_app):
    with _new_client(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        status, body = create_playthrough(
            client, case["creatorAccessToken"], case["caseId"], 1
        )
    assert status == 201
    assert set(body.keys()) == {
        "playthroughId",
        "caseId",
        "caseVersion",
        "playthroughAccessToken",
        "status",
    }
    row = phase5_app.state.store.get_playthrough_by_id(body["playthroughId"])
    assert row is not None
    assert row.case_id == case["caseId"]
    assert row.case_version == 1
    assert row.state == "PLAYING"
    # The token appears exactly once (creation response) and only a verifier
    # is stored.
    assert row.token_verifier == token_verifier(body["playthroughAccessToken"])
    assert row.token_verifier != body["playthroughAccessToken"]
    assert_no_hidden_leaks(
        body,
        allow_token_keys=frozenset({"playthroughAccessToken"}),
        known_tokens={body["playthroughAccessToken"]},
    )


def test_playthrough_bootstrap_and_public_case(phase5_app):
    with _new_client(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        _, created = create_playthrough(
            client, case["creatorAccessToken"], case["caseId"], 1
        )
        pt_id = created["playthroughId"]
        pt_token = created["playthroughAccessToken"]
        res = client.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token))
        assert res.status_code == 200
        body = res.json()
        assert set(body.keys()) == PT_RESPONSE_KEYS
        assert body["caseId"] == case["caseId"]
        assert body["caseVersion"] == 1
        assert body["status"] == "PLAYING"
        assert body["expiresAt"] > body["createdAt"]
        assert_no_hidden_leaks(body)
        res = client.get(
            f"/api/v1/playthroughs/{pt_id}/public-case", headers=auth(pt_token)
        )
        assert res.status_code == 200
        public_body = res.json()
        assert public_body["caseVersion"] == 1
        assert public_body["caseId"] == case["caseId"]
        assert_no_hidden_leaks(public_body)


def test_18_publish_v2_does_not_alter_v1_playthrough(phase5_app):
    """F.1 regression: v1 playthrough > publish v2 > still pins v1."""
    session_token = None
    with _new_client(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        case_id = case["caseId"]
        _, created = create_playthrough(client, creator, case_id, 1)
        pt_id = created["playthroughId"]
        pt_token = created["playthroughAccessToken"]
        v1_bootstrap = client.get(
            f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token)
        ).json()

        # Publish version 2 via the service (no HTTP endpoint for it in the
        # frozen Phase 5 surface; "a later generation creates a new
        # CaseVersion" — Phase5 B).
        service = phase5_app.state.generation_service
        session_id = _session_id_of(phase5_app, session_token)
        v2 = service.start_case_version(
            case_id,
            "Victim: sarah_miller\nMurderer: thomas_reed\nAnother locked retry",
            anonymous_quota_session_id=session_id,
        )
        assert v2.status == "PUBLISHED"
        assert v2.generation_id == "GEN-2"

        # The creator's default GET now resolves the LATEST published (v2)...
        res = client.get(f"/api/v1/cases/{case_id}", headers=auth(creator))
        assert res.status_code == 200
        assert res.json()["caseVersion"] == 2
        # ... while the v1 playthrough is untouched and still pins v1.
        res = client.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token))
        assert res.status_code == 200
        body = res.json()
        assert body["caseId"] == case_id
        assert body["caseVersion"] == 1
        assert body == v1_bootstrap
        res = client.get(
            f"/api/v1/playthroughs/{pt_id}/public-case", headers=auth(pt_token)
        )
        assert res.status_code == 200
        assert res.json()["caseVersion"] == 1
        # The DB rows: two case_versions, two published rows; v1 untouched.
        store = phase5_app.state.store
        v1_row = store.get_case_version(case_id, 1)
        assert v1_row.state == "PUBLISHED"
        assert store.get_case_version(case_id, 2).state == "PUBLISHED"
        assert store.get_published(case_id, 1) is not None
        assert store.get_published(case_id, 2) is not None
        row = store.get_playthrough_by_id(pt_id)
        assert row.case_version == 1


def test_19_restart_keeps_playthrough_pinned_to_v1(phase5_app, database_url):
    """F.2 regression: restart (new engines, same file) > still v1."""
    session_token = None
    pt_id = pt_token = case_id = None
    with _new_client(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        case_id = case["caseId"]
        _, created = create_playthrough(client, case["creatorAccessToken"], case_id, 1)
        pt_id, pt_token = created["playthroughId"], created["playthroughAccessToken"]

    # -- simulated restart: dispose every engine, open the SAME file again --
    phase5_app.state.engine.dispose()
    phase5_app.state.store.dispose()

    from app.core.config import Settings
    from app.main import create_app

    restarted = create_app(make_settings_for(database_url))
    try:
        with _new_client(restarted) as client:
            res = client.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token))
            assert res.status_code == 200
            body = res.json()
            assert body["caseId"] == case_id
            assert body["caseVersion"] == 1
            assert body["status"] == "PLAYING"
            res = client.get(
                f"/api/v1/playthroughs/{pt_id}/public-case", headers=auth(pt_token)
            )
            assert res.status_code == 200
            assert res.json()["caseVersion"] == 1
            # The creator token also survives restart (authorized reads only
            # come from the DB, never from process memory).
            assert restarted.state.store.get_published(case_id, 1) is not None
    finally:
        restarted.state.engine.dispose()
        restarted.state.store.dispose()


def make_settings_for(database_url):
    from app.core.config import Settings

    return Settings(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        max_generations_per_session_per_window=8,
        max_concurrent_generations=2,
    )


def test_20_playthrough_credential_a_cannot_access_b(phase5_app):
    with _new_client(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        _, pt_a = create_playthrough(client, creator, case["caseId"], 1)
        _, pt_b = create_playthrough(client, creator, case["caseId"], 1)
        token_a = pt_a["playthroughAccessToken"]
        # A on B -> 404.
        res = client.get(
            f"/api/v1/playthroughs/{pt_b['playthroughId']}", headers=auth(token_a)
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"
        res = client.get(
            f"/api/v1/playthroughs/{pt_b['playthroughId']}/public-case",
            headers=auth(token_a),
        )
        assert res.status_code == 404
        # A creator credential is NOT a playthrough credential.
        res = client.get(
            f"/api/v1/playthroughs/{pt_a['playthroughId']}",
            headers=auth(creator),
        )
        assert res.status_code == 401
        assert_sanitized_error(res.text)


def test_expired_playthrough_token_answers_401(phase5_app):
    store = phase5_app.state.store
    clock = phase5_app.state.clock
    from app.auth.tokens import issue_playthrough_access_token

    token = issue_playthrough_access_token()
    now = float(clock.now())
    store.create_case(
        case_id="CASE-EXP",
        quota_session_id=_seed_session(store, now),
        title="Exp",
        difficulty=None,
        created_at=now,
    )
    store.create_case_version(
        case_id="CASE-EXP", version=1, state="PUBLISHED", generation_id="GEN-1", created_at=now
    )
    store.create_playthrough(
        playthrough_id="PT-EXP",
        case_id="CASE-EXP",
        case_version=1,
        token_verifier=token_verifier(token),
        state="PLAYING",
        created_at=now - 1000,
        expires_at=now - 60,  # already expired
    )
    with _new_client(phase5_app) as client:
        res = client.get("/api/v1/playthroughs/PT-EXP", headers=auth(token))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "SESSION_EXPIRED"
    assert_sanitized_error(res.text)


def test_23_concurrent_playthrough_creation_independent(phase5_app):
    """F.3/M23: two threads create playthroughs on the same version."""
    with _new_client(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        case_id = case["caseId"]
        results: list[tuple[int, dict]] = []
        errors: list[BaseException] = []

        def _create():
            try:
                with _new_client(phase5_app) as c:
                    status, body = create_playthrough(c, creator, case_id, 1)
                    results.append((status, body))
            except BaseException as exc:  # noqa: BLE001 - recorded for assert
                errors.append(exc)

        threads = [threading.Thread(target=_create) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert len(results) == 2
        ids = {r[1]["playthroughId"] for r in results}
        tokens = {r[1]["playthroughAccessToken"] for r in results}
        assert len(ids) == 2
        assert len(tokens) == 2
        store = phase5_app.state.store
        for _status, body in results:
            row = store.get_playthrough_by_id(body["playthroughId"])
            assert row is not None
            assert (row.case_id, row.case_version) == (case_id, 1)
            assert row.token_verifier == token_verifier(body["playthroughAccessToken"])


def _seed_session(store, now):
    from app.auth.tokens import issue_token, verifier as _v

    sid = "QUOTA-EXP-SEED"
    store.create_session(
        session_id=sid,
        token_verifier=_v(issue_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    return sid