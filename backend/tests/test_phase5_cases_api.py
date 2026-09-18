"""Phase 5 — cases API, ownership, version resolution, robustness
(M5, M6, M7, M10, M15, M16, F.2-F.4, INVARIANT 1).

- M5  creator A cannot read creator B's case
- M6  knowing caseId without a credential fails
- M7  malformed/foreign creator credentials fail cleanly
- M10 case generation API uses the Phase 4 deterministic pipeline
- M15 exact published version retrieval
- M16 unpublished version cannot create a playthrough
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from phase5_helpers import (
    assert_no_hidden_leaks,
    assert_sanitized_error,
    create_case,
    create_playthrough,
    create_session,
)

PUBLIC_CASE_KEYS = {
    "caseId",
    "caseVersion",
    "title",
    "scene",
    "persons",
    "motives",
    "objects",
    "locations",
    "travelRules",
    "evidence",
    "worldGraph",
    "compositionNotes",  # ADV-153: player-safe bounded notes (browser seam)
}


def _seed_unpublished_version(phase5_app, *, version: int = 1) -> tuple[str, str]:
    """Seed a case with a non-PUBLISHED version + a valid creator credential.

    Returns (case_id, creator_token). Used ONLY for failure-path tests that a
    healthy app cannot produce (the app always publishes or fails). Every call
    seeds a FRESH session/case so repeated calls never collide.
    """
    import secrets

    from app.auth.tokens import (
        issue_creator_access_token,
        issue_token,
        verifier as token_verifier,
    )

    store = phase5_app.state.store
    clock = phase5_app.state.clock
    now = float(clock.now())
    suffix = secrets.token_urlsafe(6)
    session_id = f"QUOTA-SEED-{suffix}"
    store.create_session(
        session_id=session_id,
        token_verifier=token_verifier(issue_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    case_id = f"CASE-SEED-{suffix}"
    store.create_case(
        case_id=case_id,
        quota_session_id=session_id,
        title="Seeded",
        difficulty=None,
        created_at=now,
    )
    store.create_case_version(
        case_id=case_id,
        version=version,
        state="FAILED",
        generation_id=f"GEN-{version}",
        created_at=now,
    )
    token = issue_creator_access_token()
    store.create_creator_credential(
        case_id=case_id,
        token_verifier=token_verifier(token),
        created_at=now,
        expires_at=now + 3600,
    )
    return case_id, token


def test_10_case_generation_api_uses_phase4_pipeline(phase5_app):
    """M10 + golden evidence: the Phase 4 pipeline produced the whole case."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        created = create_case(client, session_token)
        assert created["status"] == "PUBLISHED"
        case_id = created["caseId"]
        creator = created["creatorAccessToken"]
        res = client.get(
            f"/api/v1/cases/{case_id}", headers={"Authorization": f"Bearer {creator}"}
        )
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == PUBLIC_CASE_KEYS
    assert body["caseId"] == case_id
    assert body["caseVersion"] == 1
    # Golden dev-mode case content proves the real Phase 4 pipeline ran.
    assert body["scene"]["locationId"] == "miller_apartment_kitchen"
    person_ids = {p["personId"] for p in body["persons"]}
    assert {"sarah_miller", "thomas_reed", "emily_reed"} <= person_ids
    assert len(body["evidence"]) == 16
    assert len(body["worldGraph"]["placements"]) == 9
    assert "murdererId" not in res.text
    assert_no_hidden_leaks(body)
    assert_sanitized_error(res.text)


def test_case_creation_response_shape_and_creator_token_once(phase5_app):
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        created = create_case(client, session_token)
    assert set(created.keys()) == {
        "caseId",
        "generationId",
        "generationAttemptId",
        "creatorAccessToken",
        "status",
    }
    assert created["generationId"] == "GEN-1"
    assert created["status"] == "PUBLISHED"
    # Token leak scan: the creator token appears ONLY here at creation.
    assert_no_hidden_leaks(
        created,
        allow_token_keys=frozenset({"creatorAccessToken"}),
        known_tokens={created["creatorAccessToken"]},
    )


def test_06_caseid_alone_grants_nothing(phase5_app):
    """INVARIANT 1 / M6: caseId / caseVersion / playthroughId alone fail."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        created = create_case(client, session_token)
        case_id = created["caseId"]
        # No Authorization header at all.
        res = client.get(f"/api/v1/cases/{case_id}")
        assert res.status_code == 401
        # An anonymous SESSION token is not a creator credential.
        res = client.get(
            f"/api/v1/cases/{case_id}",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert res.status_code == 401
        assert_sanitized_error(res.text)


def test_05_creator_a_cannot_read_creator_b_case(phase5_app):
    with TestClient(phase5_app) as client:
        token_a, _ = create_session(client)
        token_b, _ = create_session(client)
        case_b = create_case(client, token_b)
        creator_b = case_b["creatorAccessToken"]
        # A still creates their own case so its token is valid-but-foreign.
        case_a = create_case(client, token_a)
        creator_a = case_a["creatorAccessToken"]
        res = client.get(
            f"/api/v1/cases/{case_b['caseId']}",
            headers={"Authorization": f"Bearer {creator_a}"},
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"
        # Sanity: each creator CAN read their own case.
        res = client.get(
            f"/api/v1/cases/{case_a['caseId']}",
            headers={"Authorization": f"Bearer {creator_a}"},
        )
        assert res.status_code == 200
        res = client.get(
            f"/api/v1/cases/{case_b['caseId']}",
            headers={"Authorization": f"Bearer {creator_b}"},
        )
        assert res.status_code == 200
        assert_sanitized_error(res.text)


def test_guessed_case_id_with_valid_token_answers_404(phase5_app):
    with TestClient(phase5_app) as client:
        _, creator = _creator_for_one_case(client)
        for guessed in ("CASE-UNKNOWN", "CASE-0", "nonexistent-id", "9" * 30):
            res = client.get(
                f"/api/v1/cases/{guessed}",
                headers={"Authorization": f"Bearer {creator}"},
            )
            assert res.status_code == 404, guessed
            assert_sanitized_error(res.text)


def _creator_for_one_case(client):
    session_token, _ = create_session(client)
    case = create_case(client, session_token)
    return case["caseId"], case["creatorAccessToken"]


def test_07_malformed_foreign_creator_credentials_fail(phase5_app):
    with TestClient(phase5_app) as client:
        case_id, creator = _creator_for_one_case(client)
        # Oversized bearer (10_000 chars) must 401, not crash.
        huge = "Bearer " + "x" * 10_000
        res = client.get(
            f"/api/v1/cases/{case_id}", headers={"Authorization": huge}
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "UNAUTHORIZED"
        # A well-formed but unknown creator token (foreign/guessed).
        from app.auth.tokens import issue_creator_access_token

        res = client.get(
            f"/api/v1/cases/{case_id}",
            headers={"Authorization": f"Bearer {issue_creator_access_token()}"},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "SESSION_EXPIRED"
        # A playthrough credential is never a creator credential.
        from app.auth.tokens import issue_playthrough_access_token

        res = client.get(
            f"/api/v1/cases/{case_id}",
            headers={"Authorization": f"Bearer {issue_playthrough_access_token()}"},
        )
        assert res.status_code == 401
        assert_sanitized_error(res.text)
        # NOTE: non-ASCII / control-char bearer values cannot be transmitted by
        # the HTTP client (httpx refuses them at encode time); the server-side
        # 401 rejection of those tokens is covered at the parse level in
        # test_phase5_auth_tokens.py::test_bounds_parse_bearer_rejections.


def test_15_exact_version_retrieval_and_validation(phase5_app):
    """M15: ?version= exact; garbage/negative/zero -> 422; unknown -> 404."""
    with TestClient(phase5_app) as client:
        case_id, creator = _creator_for_one_case(client)
        res = client.get(
            f"/api/v1/cases/{case_id}",
            headers={"Authorization": f"Bearer {creator}"},
        )
        assert res.status_code == 200
        default_body = res.json()
        res = client.get(
            f"/api/v1/cases/{case_id}?version=1",
            headers={"Authorization": f"Bearer {creator}"},
        )
        assert res.status_code == 200
        assert res.json() == default_body
        assert res.json()["caseVersion"] == 1
        # Nonexistent version -> 404.
        res = client.get(
            f"/api/v1/cases/{case_id}?version=9",
            headers={"Authorization": f"Bearer {creator}"},
        )
        assert res.status_code == 404
        # Invalid version literals -> 422 envelope (no leak).
        for bad in ("0", "-1", "abc", "1.5", "007junk"):
            res = client.get(
                f"/api/v1/cases/{case_id}?version={bad}",
                headers={"Authorization": f"Bearer {creator}"},
            )
            assert res.status_code == 422, bad
            assert res.json()["error"]["code"] == "VALIDATION_ERROR"
            assert_sanitized_error(res.text)


def test_unpublished_requested_version_answers_409(phase5_app):
    """A version that EXISTS but is not PUBLISHED answers 409."""
    case_id, creator = _seed_unpublished_version(phase5_app)
    with TestClient(phase5_app) as client:
        res = client.get(
            f"/api/v1/cases/{case_id}?version=1",
            headers={"Authorization": f"Bearer {creator}"},
        )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "VERSION_NOT_PUBLISHED"
    assert_sanitized_error(res.text)


def test_16_unpublished_version_cannot_create_playthrough(phase5_app):
    for seeded_state in ("FAILED", "DRAFT"):
        case_id, creator = _seed_unpublished_version(phase5_app)
        with TestClient(phase5_app) as client:
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers={"Authorization": f"Bearer {creator}"},
            )
        assert res.status_code == 409, seeded_state
        assert res.json()["error"]["code"] == "VERSION_NOT_PUBLISHED"
        assert_sanitized_error(res.text)


def test_playthrough_creation_nonexistent_version_answers_404(phase5_app):
    with TestClient(phase5_app) as client:
        case_id, creator = _creator_for_one_case(client)
        for version in (7, 2):
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/{version}/playthroughs",
                headers={"Authorization": f"Bearer {creator}"},
            )
            assert res.status_code == 404, version
            assert_sanitized_error(res.text)


def test_cross_case_version_mismatch_is_isolated(phase5_app):
    with TestClient(phase5_app) as client:
        session, _ = create_session(client)
        case_a = create_case(client, session)
        case_b = create_case(client, session)
        creator_a = case_a["creatorAccessToken"]
        creator_b = case_b["creatorAccessToken"]
        # case X version 1 must never return case Y's payload.
        body_a = client.get(
            f"/api/v1/cases/{case_a['caseId']}?version=1",
            headers={"Authorization": f"Bearer {creator_a}"},
        ).json()
        body_b = client.get(
            f"/api/v1/cases/{case_b['caseId']}?version=1",
            headers={"Authorization": f"Bearer {creator_b}"},
        ).json()
        assert body_a["caseId"] == case_a["caseId"]
        assert body_b["caseId"] == case_b["caseId"]
        assert body_a["caseId"] != body_b["caseId"]


def test_generations_progress_endpoint_ownership(phase5_app):
    """F.3: progress resolves ONLY within the caller's own case.

    generationId is a per-case monotonic public label (GEN-1, GEN-2, ...).
    A foreign creator querying a label RESOLVES within their OWN case — they
    can never see another case's progress; an unknown label in their case
    answers 404.
    """
    with TestClient(phase5_app) as client:
        session_a, _ = create_session(client)
        session_b, _ = create_session(client)
        case_a = create_case(client, session_a)
        creator_a = case_a["creatorAccessToken"]
        gen_a = case_a["generationId"]
        case_b = create_case(client, session_b)
        creator_b = case_b["creatorAccessToken"]
        # Owner can read it.
        res = client.get(
            f"/api/v1/generations/{gen_a}",
            headers={"Authorization": f"Bearer {creator_a}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert set(body.keys()) == {"caseId", "generationId", "status", "progress", "stage"}
        assert body["caseId"] == case_a["caseId"]
        assert body["status"] == "PUBLISHED"
        assert body["progress"] == 100
        assert_no_hidden_leaks(body)
        # Foreign creator querying the same label sees ONLY their OWN data.
        res = client.get(
            f"/api/v1/generations/{gen_a}",
            headers={"Authorization": f"Bearer {creator_b}"},
        )
        assert res.status_code == 200
        assert res.json()["caseId"] == case_b["caseId"]  # never case_a's data
        assert res.json()["caseId"] != case_a["caseId"]
        # A label their own case does NOT have -> 404 (not owned/unknown).
        res = client.get(
            f"/api/v1/generations/GEN-99",
            headers={"Authorization": f"Bearer {creator_a}"},
        )
        assert res.status_code == 404
        # An anonymous SESSION token is not a creator credential -> 401.
        res = client.get(
            f"/api/v1/generations/{gen_a}",
            headers={"Authorization": f"Bearer {session_a}"},
        )
        assert res.status_code == 401
        assert_sanitized_error(res.text)