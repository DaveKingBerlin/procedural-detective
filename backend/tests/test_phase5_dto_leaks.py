"""Phase 5 — public DTO leak prevention & robustness (M24, M25, M26, M27).

- M24 public CaseVersion DTO contains no CaseTruth
- M25 public Playthrough DTO contains no CaseTruth/proof/token verifier
- M26 deeply nested serialization leak regression (recursive key-path scan)
- M27 structured errors contain no SQL/internal traceback/paths
- Adversarial: known creation tokens never reappear; canonical crime time
  never appears as a value; verifier-shaped values never appear; raw response
  text carries no forbidden key names.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from phase5_helpers import (
    assert_no_hidden_leaks,
    assert_sanitized_error,
    auth,
    create_case,
    create_playthrough,
    create_session,
)

_FORBIDDEN_TEXT_KEYS = (
    "murdererId",
    "victimId",
    "weaponId",
    "truthfulness",
    "solutionProof",
    "acceptedScoring",
    "canonicalCrimeTime",
    "crimeTime",
    "diagnostics",
    "providerOutput",
    "tokenVerifier",
    "remainingCandidateIds",
)


def test_26_deep_nested_scan_across_every_phase5_endpoint(phase5_app):
    """End-to-end: every success response passes the recursive scanner.

    The scanner walks EVERY nesting level (persons/motives/objects/locations/
    travelRules/evidence/worldGraph placements) — deep-nesting regression.
    """
    known_tokens: set[str] = set()
    with TestClient(phase5_app) as client:
        session_token, body = create_session(client)
        known_tokens.add(session_token)
        assert_no_hidden_leaks(
            body,
            allow_token_keys=frozenset({"anonymousSessionToken"}),
            known_tokens=known_tokens,
        )

        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        known_tokens.add(creator)
        assert_no_hidden_leaks(
            case,
            allow_token_keys=frozenset({"creatorAccessToken"}),
            known_tokens=known_tokens,
        )

        # Progress: sanitized and token-free.
        res = client.get(
            f"/api/v1/generations/{case['generationId']}",
            headers=auth(creator),
        )
        assert res.status_code == 200
        assert_no_hidden_leaks(res.json(), known_tokens=known_tokens)

        # Public case: no truth anywhere, deeply nested.
        res = client.get(f"/api/v1/cases/{case['caseId']}", headers=auth(creator))
        assert res.status_code == 200
        body = res.json()
        assert_no_hidden_leaks(body, known_tokens=known_tokens)
        # The RAW bytes carry no forbidden key names either.
        for marker in _FORBIDDEN_TEXT_KEYS:
            assert marker not in res.text, f"raw response contains {marker!r}"

        # Playthrough creation: token once.
        _, created = create_playthrough(client, creator, case["caseId"], 1)
        pt_token = created["playthroughAccessToken"]
        known_tokens.add(pt_token)
        assert_no_hidden_leaks(
            created,
            allow_token_keys=frozenset({"playthroughAccessToken"}),
            known_tokens=known_tokens,
        )

        # Playthrough bootstrap: no truth/proof/verifier; token-free.
        res = client.get(
            f"/api/v1/playthroughs/{created['playthroughId']}",
            headers=auth(pt_token),
        )
        assert res.status_code == 200
        assert_no_hidden_leaks(res.json(), known_tokens=known_tokens)

        # Playthrough public-case: pinned version payload, still clean.
        res = client.get(
            f"/api/v1/playthroughs/{created['playthroughId']}/public-case",
            headers=auth(pt_token),
        )
        assert res.status_code == 200
        public_body = res.json()
        assert_no_hidden_leaks(public_body, known_tokens=known_tokens)
        # PD-SEC-01 (Phase 20): the PLAYTHROUGH-scoped public-case exposes NO
        # undiscovered evidence — a fresh playthrough carries an EMPTY
        # evidence list and null world-graph placement evidenceIds (the
        # creator-scoped GET /cases/{id} dossier keeps the 41.2 list).
        assert public_body["evidence"] == []
        assert len(public_body["worldGraph"]["placements"]) > 0
        for placement in public_body["worldGraph"]["placements"]:
            assert placement["evidenceId"] is None


def test_24_public_case_dto_contains_no_case_truth(phase5_app):
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        res = client.get(
            f"/api/v1/cases/{case['caseId']}", headers=auth(case["creatorAccessToken"])
        )
    body = res.json()
    assert "truth" not in body
    assert "crime" not in body and "crime" not in res.text
    for key in ("murdererId", "murderer", "victimId", "weaponId", "truthfulness"):
        assert key not in res.text, f"truth field {key!r} leaked"


def test_25_playthrough_dto_contains_no_hidden_material(phase5_app):
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        _, pt = create_playthrough(client, case["creatorAccessToken"], case["caseId"], 1)
        res = client.get(
            f"/api/v1/playthroughs/{pt['playthroughId']}",
            headers=auth(pt["playthroughAccessToken"]),
        )
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {
        "playthroughId",
        "caseId",
        "caseVersion",
        "status",
        "createdAt",
        "expiresAt",
    }
    for key in ("truth", "proof", "verifier", "murdererId", "solutionProof"):
        assert key not in res.text, f"{key!r} leaked"
    assert "tokenVerifier" not in res.text


def test_27_error_responses_are_sanitized(phase5_app):
    """M27: 4xx/5xx never leak stack traces, SQL, table/column names, paths."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        case_id = case["caseId"]

        # 401 paths.
        res = client.get(f"/api/v1/cases/{case_id}")
        assert res.status_code == 401
        assert_sanitized_error(res.text)
        res = client.get(
            f"/api/v1/cases/{case_id}", headers={"Authorization": "Bearer " + "x" * 10_000}
        )
        assert res.status_code == 401
        assert_sanitized_error(res.text)

        # 404 paths (wrong credentials / unknown resources).
        res = client.get(
            f"/api/v1/cases/{case_id}", headers=auth(create_case(client, session_token)["creatorAccessToken"])
        )
        assert res.status_code == 404
        assert_sanitized_error(res.text)
        res = client.get(
            f"/api/v1/cases/{case_id}?version=99", headers=auth(creator)
        )
        assert res.status_code == 404
        assert_sanitized_error(res.text)

        # 409 path (version exists, not published).
        store = phase5_app.state.store
        seeded = _seed_failed_version(store, phase5_app.state.clock.now())
        res = client.get(
            f"/api/v1/cases/{seeded['case_id']}?version=1",
            headers=auth(seeded["creator"]),
        )
        assert res.status_code == 409
        assert_sanitized_error(res.text)

        # 422 paths.
        res = client.get(
            f"/api/v1/cases/{case_id}?version=abc", headers=auth(creator)
        )
        assert res.status_code == 422
        assert_sanitized_error(res.text)
        too_long = "M" * 5000
        res = client.post(
            "/api/v1/cases",
            json={"prompt": too_long},
            headers=auth(session_token),
        )
        assert res.status_code == 422
        assert_sanitized_error(res.text)
        assert too_long not in res.text  # the prompt value is never echoed

        # 500 path via an injected service failure -> sanitized envelope.
        original = phase5_app.state.generation_service.start_case_generation
        try:

            def _boom(*_args, **_kwargs):
                raise RuntimeError("poisoned")

            phase5_app.state.generation_service.start_case_generation = _boom
            res = client.post(
                "/api/v1/cases",
                json={"prompt": "A mystery"},
                headers=auth(session_token),
            )
            assert res.status_code == 500
            assert res.json() == {
                "error": {"code": "INTERNAL_ERROR", "message": "Internal server error", "details": None}
            }
            assert_sanitized_error(res.text)
        finally:
            phase5_app.state.generation_service.start_case_generation = original


def test_admission_denied_answers_429_sanitized(store, database_url):
    """429 ADMISSION_DENIED envelope (zero provider calls), no leak."""
    from app.services.generation import GenerationService

    settings = Settings(
        database_url=database_url,
        max_generations_per_session_per_window=1,
        max_concurrent_generations=1,
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            first = create_case(client, session_token)
            assert first["status"] == "PUBLISHED"
            res = client.post(
                "/api/v1/cases",
                json={"prompt": "Another mystery"},
                headers=auth(session_token),
            )
            assert res.status_code == 429
            body = res.json()
            assert body["error"]["code"] == "ADMISSION_DENIED"
            assert_sanitized_error(res.text)
    finally:
        app.state.engine.dispose()
        store_dispose_safe(app)


def store_dispose_safe(app):
    store = getattr(app.state, "store", None)
    if store is not None:
        try:
            store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask failures
            pass


def _seed_failed_version(store, now):
    """Seed a case with a FAILED version + valid creator credential."""
    import secrets

    from app.auth.tokens import (
        issue_creator_access_token,
        issue_token,
        verifier as _v,
    )

    suffix = secrets.token_urlsafe(6)
    session_id = f"QUOTA-DTO-{suffix}"
    store.create_session(
        session_id=session_id,
        token_verifier=_v(issue_token()),
        quota_window_end=float(now) + 3600,
        created_at=float(now),
    )
    case_id = f"CASE-DTO-{suffix}"
    store.create_case(
        case_id=case_id,
        quota_session_id=session_id,
        title="Dto",
        difficulty=None,
        created_at=float(now),
    )
    store.create_case_version(
        case_id=case_id,
        version=1,
        state="FAILED",
        generation_id="GEN-1",
        created_at=float(now),
    )
    creator = issue_creator_access_token()
    store.create_creator_credential(
        case_id=case_id,
        token_verifier=_v(creator),
        created_at=float(now),
        expires_at=float(now) + 3600,
    )
    return {"case_id": case_id, "creator": creator}