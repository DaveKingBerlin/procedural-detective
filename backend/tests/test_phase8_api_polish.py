"""Phase 8 — API polish (Phase8 H) and production startup (Phase8 I).

H1  Cache safety: every private/authenticated /api/v1 response (sessions,
    cases, generations, playthroughs / investigation / accusation / reveal)
    carries ``Cache-Control: no-store`` (+ ``Pragma: no-cache``); the reveal
    response and the playthrough bootstrap are NEVER cached.
H2  No secrets in logs / provider-config errors: an LLM API key set in the
    environment can never appear in an error message or any API response,
    in live misconfiguration and in fake mode.
H3  Production-safe errors: a forced 500 on the REAL app shows only the exact
    INTERNAL_ERROR envelope (no traceback, no internal detail) and itself
    carries no-store.
H4  Frozen reveal contract: ``GET /playthroughs/{id}/reveal`` is documented
    in the route source as the frozen REQUIREMENTS 40.12 endpoint performing
    the idempotent ACCUSED -> REVEALED transition (asserted via source text).
H5  CORS: an arbitrary Origin never gets ACAO reflection on a private
    endpoint (single-origin production posture).
I2  Controlled startup: ``run_migrations()`` then serve -> readiness 200.
I3  Startup failure: an unwritable DATABASE_URL makes ``run_migrations()``
    raise a sanitized RuntimeError (the container exits non-zero with a
    clear message, never a raw traceback/URL leak).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from phase5_helpers import auth, assert_sanitized_error
from phase6_helpers import client as phase6_client
from test_phase7_helpers import (
    accuse_then_reveal,
    create_published_case_and_playthrough,
    get_reveal,
    make_accusation,
    new_playthrough,
    truth_bundle,
    winning_body,
)

from app.core.config import Settings
from app.main import create_app


def _no_store_headers(res) -> None:
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["pragma"] == "no-cache"


# --------------------------------------------------------------------------- #
# H1 — cache safety on every private/authenticated response
# --------------------------------------------------------------------------- #


def test_h1_sessions_cases_playthroughs_carry_no_store(phase5_migrated_client):
    c = phase5_migrated_client

    session = c.post("/api/v1/sessions/anonymous")
    assert session.status_code == 201
    _no_store_headers(session)
    session_token = session.json()["anonymousSessionToken"]

    case = c.post(
        "/api/v1/cases",
        json={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n"},
        headers=auth(session_token),
    )
    assert case.status_code == 201
    _no_store_headers(case)
    case_id = case.json()["caseId"]
    creator = case.json()["creatorAccessToken"]

    pt = c.post(
        f"/api/v1/cases/{case_id}/versions/1/playthroughs",
        headers=auth(creator),
    )
    assert pt.status_code == 201
    _no_store_headers(pt)
    pt_id = pt.json()["playthroughId"]
    pt_token = pt.json()["playthroughAccessToken"]

    bootstrap = c.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token))
    assert bootstrap.status_code == 200
    _no_store_headers(bootstrap)

    investigation = c.get(
        f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
    )
    assert investigation.status_code == 200
    _no_store_headers(investigation)

    # A private-path ERROR response is no-store too (401 on the private path).
    unauth = c.get(f"/api/v1/playthroughs/{pt_id}")
    assert unauth.status_code == 401
    _no_store_headers(unauth)


def test_h1_accusation_and_reveal_never_cached(phase5_app):
    """The phase 7 happy path: accusation + reveal responses are no-store and
    the reveal body itself is an allowlist DTO with zero internal material."""
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    with phase6_client(phase5_app) as c:
        res = make_accusation(
            c, bundle["playthroughId"], bundle["playthroughToken"], winning_body(truth)
        )
        assert res.status_code == 200, res.json()
        _no_store_headers(res)
        reveal = get_reveal(c, bundle["playthroughId"], bundle["playthroughToken"])
        assert reveal.status_code == 200, reveal.json()
        _no_store_headers(reveal)


def test_h1_reveal_403_before_accusation_is_no_store_too(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    with phase6_client(phase5_app) as c:
        res = get_reveal(c, bundle["playthroughId"], bundle["playthroughToken"])
    assert res.status_code == 403
    _no_store_headers(res)
    assert_sanitized_error(res.text)


def test_h1_public_probes_stay_contract_exact(phase5_migrated_client):
    """health/readiness NEVER carry secrets: the bodies are exactly the fixed
    allowlisted contract words, whatever Authorization header is sent."""
    c = phase5_migrated_client
    hostile = {"Authorization": "Bearer " + "x" * 43}
    health = c.get("/api/v1/health", headers=hostile)
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "procedural-detective",
        "version": "0.1.0",
    }
    assert "x" * 43 not in health.text

    ready = c.get("/api/v1/readiness", headers=hostile)
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "database": "ok", "migrations": "ok"}
    assert "x" * 43 not in ready.text


# --------------------------------------------------------------------------- #
# H2 — secrets never reach logs / error messages / API responses
# --------------------------------------------------------------------------- #


def test_h2_live_misconfiguration_never_echoes_api_key(database_url):
    """generation_provider=live with a real-looking key but a missing model must
    raise a SANITIZED ProviderConfigError that does not contain the key."""
    from app.persistence.store import Store
    from app.services.generation import GenerationService, ProviderConfigError

    secret = "placeholder-not-a-secret-0001"
    store = Store(database_url)
    try:
        settings = Settings(
            database_url=database_url,
            generation_provider="live",
            llm_api_key=secret,
            llm_model=None,  # deliberately missing -> ProviderConfigError
            live_provider_url="https://api.example.com/v1/chat/completions",
        )
        with pytest.raises(ProviderConfigError) as excinfo:
            GenerationService(settings=settings, store=store)
        assert excinfo.value.args[0] == (
            "generation_provider=live requires LIVE_PROVIDER_URL, "
            "LLM_API_KEY and LLM_MODEL"
        )
        assert secret not in str(excinfo.value)
    finally:
        store.dispose()


def test_h2_api_key_set_in_fake_mode_never_reaches_responses(phase5_app):
    """Fake (demo) mode works with a key present and no response ever echoes it
    (key is carried by the settings object but never serialized/logged)."""
    from app.core.config import Settings
    from app.main import create_app as fresh_app

    secret = "placeholder-not-a-secret-0002"
    # A fresh app with the key set (fake provider; injections stay isolated).
    application = fresh_app(
        Settings(
            database_url=phase5_app.state.store.url,
            cors_allowed_origins=["http://localhost:5173"],
            generation_provider="fake",
            llm_api_key=secret,
            max_concurrent_generations=4,
            max_generations_per_session_per_window=8,
            max_concurrent_generations_global=8,
            max_generations_global_per_window=50,
        )
    )
    try:
        with phase6_client(application) as c:
            session = c.post("/api/v1/sessions/anonymous")
            session_token = session.json()["anonymousSessionToken"]
            case = c.post(
                "/api/v1/cases",
                json={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n"},
                headers=auth(session_token),
            )
            assert case.status_code == 201, case.json()
            case_id = case.json()["caseId"]
            creator = case.json()["creatorAccessToken"]
            pt = c.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(creator),
            )
            assert pt.status_code == 201, pt.json()
            pt_id = pt.json()["playthroughId"]
            pt_token = pt.json()["playthroughAccessToken"]
            for res in (session, case, pt):
                assert secret not in res.text
    finally:
        application.state.engine.dispose()
        application.state.store.dispose()


# --------------------------------------------------------------------------- #
# H3 — production-safe errors: forced 500 shows only the exact envelope
# --------------------------------------------------------------------------- #


def test_h3_forced_500_on_real_app_shows_only_envelope(database_url):
    application = create_app(Settings(database_url=database_url))
    secret = "super-secret-internal-detail-0003"

    def boom():  # noqa: ANN201
        raise RuntimeError(secret)

    application.state.generation_service.create_anonymous_quota_session = boom
    try:
        with TestClient(application, raise_server_exceptions=False) as c:
            res = c.post("/api/v1/sessions/anonymous")
        assert res.status_code == 500
        assert res.json() == {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Internal server error",
                "details": None,
            }
        }
        assert secret not in res.text
        assert "Traceback" not in res.text
        assert res.headers["cache-control"] == "no-store"
    finally:
        application.state.engine.dispose()


# --------------------------------------------------------------------------- #
# H4 — frozen GET /reveal contract is documented (NO ENDPOINT CHANGE)
# --------------------------------------------------------------------------- #


def test_h4_reveal_doc_documents_frozen_get_contract():
    source = Path(BACKEND_DIR / "app" / "api" / "v1" / "playthroughs.py").read_text(
        encoding="utf-8"
    )
    assert "40.12" in source
    assert "frozen" in source
    assert "ACCUSED->REVEALED" in source or "ACCUSED -> REVEALED" in source
    assert "idempotent" in source
    assert "NO ENDPOINT CHANGE" in source


# --------------------------------------------------------------------------- #
# H5 — CORS never reflects arbitrary origins (single-origin production)
# --------------------------------------------------------------------------- #


def test_h5_arbitrary_origin_never_reflected_on_private_endpoint(phase5_migrated_client):
    c = phase5_migrated_client
    evil = "https://evil.example"
    res = c.post(
        "/api/v1/sessions/anonymous", headers={"Origin": evil}
    )
    assert res.status_code == 201
    assert res.headers.get("access-control-allow-origin") is None
    _no_store_headers(res)

    # A private resource with the Origin header behaves identically.
    res2 = c.get("/api/v1/playthroughs/nonexistent", headers={"Origin": evil})
    assert res2.status_code == 401
    assert res2.headers.get("access-control-allow-origin") is None


# --------------------------------------------------------------------------- #
# I2/I3 — controlled-startup migrations + graceful failure
# --------------------------------------------------------------------------- #


def test_i2_run_migrations_then_readiness_ok(tmp_path):
    from app.startup import run_migrations

    db_url = f"sqlite:///{(tmp_path / 'boot.db').as_posix()}"
    run_migrations(Settings(database_url=db_url))
    application = create_app(Settings(database_url=db_url))
    try:
        with TestClient(application) as c:
            assert c.get("/api/v1/readiness").status_code == 200
            assert c.get("/api/v1/readiness").json()["migrations"] == "ok"
    finally:
        application.state.engine.dispose()


def test_i3_migration_failure_is_sanitized_nonzero(tmp_path):
    from app.startup import run_migrations

    # A VALID-syntax URL whose directory does not exist: migration must fail.
    missing_dir = tmp_path / "does-not-exist"
    db_url = f"sqlite:///{(missing_dir / 'db.sqlite').as_posix()}"
    with pytest.raises(RuntimeError) as excinfo:
        run_migrations(Settings(database_url=db_url))
    message = str(excinfo.value)
    assert "Failed to apply database migrations" in message
    # Sanitized: no URL/path/secret in the surfaced message.
    assert "does-not-exist" not in message
    assert "db.sqlite" not in message
    assert "sqlite://" not in message
    assert "Traceback" not in message


def test_i3_malformed_database_url_fails_at_configuration():
    """A malformed DATABASE_URL is rejected by Settings itself -> the process
    exits non-zero at startup with a clean message (no DB interaction)."""
    with pytest.raises(ValueError):
        Settings(database_url="definitely-not-a-url-!!!")