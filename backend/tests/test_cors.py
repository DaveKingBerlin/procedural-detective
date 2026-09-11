"""CORS tests: preflight and simple-request behavior for allowed and
disallowed origins, configured from CORS_ALLOWED_ORIGINS."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

ALLOWED = "http://localhost:5173"
DISALLOWED = "https://evil.example"


def _preflight(c, origin, method="GET"):
    return c.options(
        "/api/v1/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
        },
    )


def test_preflight_allowed_origin_returns_cors_headers(client):
    res = _preflight(client, ALLOWED)
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == ALLOWED
    assert "GET" in res.headers.get("access-control-allow-methods", "")
    assert res.headers.get("access-control-allow-credentials") == "true"


def test_preflight_disallowed_origin_returns_error_envelope(client):
    """DEF-014: every non-2xx response uses the error envelope — including a
    disallowed CORS preflight (no Starlette plain-text 400, no ACAO echo)."""
    res = _preflight(client, DISALLOWED)
    assert res.status_code == 400
    assert res.headers.get("content-type", "").startswith("application/json")
    assert res.headers.get("access-control-allow-origin") is None
    assert res.headers.get("access-control-allow-credentials") is None
    assert res.json() == {
        "error": {
            "code": "CORS_ORIGIN_NOT_ALLOWED",
            "message": f"Origin {DISALLOWED} is not allowed",
            "details": None,
        }
    }


def test_preflight_disallowed_lookalike_origin_uses_envelope(client):
    """Origin <' *.evil.com' lookalike> and 'null' follow the same envelope."""
    for origin in ("http://localhost:5173.evil.com", "null"):
        res = _preflight(client, origin)
        assert res.status_code == 400
        body = res.json()
        assert body["error"]["code"] == "CORS_ORIGIN_NOT_ALLOWED"
        assert body["error"]["message"] == f"Origin {origin} is not allowed"
        assert res.headers.get("access-control-allow-origin") is None


def test_simple_request_echoes_origin_only_when_allowed(client):
    allowed = client.get(
        "/api/v1/health", headers={"Origin": ALLOWED}
    )
    assert allowed.headers.get("access-control-allow-origin") == ALLOWED

    disallowed = client.get(
        "/api/v1/health", headers={"Origin": DISALLOWED}
    )
    assert disallowed.headers.get("access-control-allow-origin") is None


def test_simple_request_contract_locked(client):
    """Lock the simple-request contract: an allowed GET keeps its 200 + ACAO; a
    disallowed GET stays 200 WITHOUT ACAO (browser-blocked by design)."""
    allowed = client.get("/api/v1/health", headers={"Origin": ALLOWED})
    assert allowed.status_code == 200
    assert allowed.headers.get("access-control-allow-origin") == ALLOWED

    disallowed = client.get("/api/v1/health", headers={"Origin": DISALLOWED})
    assert disallowed.status_code == 200
    assert disallowed.headers.get("access-control-allow-origin") is None


def test_cors_configuration_is_injectable_per_settings(database_url):
    custom = "https://custom.example"
    application = create_app(
        Settings(database_url=database_url, cors_allowed_origins=[custom])
    )
    try:
        with TestClient(application) as c:
            ok = _preflight(c, custom)
            assert ok.headers.get("access-control-allow-origin") == custom
            bad = _preflight(c, DISALLOWED)
            assert bad.headers.get("access-control-allow-origin") is None
    finally:
        application.state.engine.dispose()