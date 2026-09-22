"""Phase 18A + Phase 20 — security headers (CSP PD-SEC-08 hardening).

The app is served same-origin (single container: FastAPI + built SPA) and never
needs iframing. ``SecurityHeadersMiddleware`` (outermost) sends a header set on
EVERY response:

- Content-Security-Policy: the Phase 20 (PD-SEC-08) baseline
  ``default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
  img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none';
  base-uri 'self'; frame-ancestors 'none'; form-action 'self'`` — explicitly
  WITHOUT ``'unsafe-eval'`` (the production Vite/Babylon build contains no
  eval()/new Function/WebAssembly usage; see main.py).
- X-Frame-Options: DENY
- X-Content-Type-Options: nosniff
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy: camera=(), microphone=(), geolocation=()

These headers must exist on health/readiness/capabilities, on the normal
published-case flow, on sanitized error responses and on the served SPA.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import _CSP_BASELINE, create_app  # noqa: E402
from phase5_helpers import auth, create_case, create_session  # noqa: E402
from phase6_helpers import client as phase6_client  # noqa: E402


def _assert_security_headers(res) -> None:
    headers = res.headers
    assert headers["content-security-policy"] == _CSP_BASELINE
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_public_probes_carry_security_headers(phase5_migrated_client):
    c = phase5_migrated_client
    for path in ("/api/v1/health", "/api/v1/readiness", "/api/v1/generation-capabilities"):
        res = c.get(path)
        assert res.status_code == 200
        _assert_security_headers(res)


def test_normal_published_case_flow_carries_security_headers(phase5_app):
    with phase6_client(phase5_app) as c:
        session = c.post("/api/v1/sessions/anonymous")
        _assert_security_headers(session)
        session_token = session.json()["anonymousSessionToken"]

        case = c.post(
            "/api/v1/cases",
            json={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n"},
            headers=auth(session_token),
        )
        assert case.status_code == 201, case.json()
        _assert_security_headers(case)
        case_id = case.json()["caseId"]
        creator = case.json()["creatorAccessToken"]

        pt = c.post(
            f"/api/v1/cases/{case_id}/versions/1/playthroughs", headers=auth(creator)
        )
        assert pt.status_code == 201
        _assert_security_headers(pt)
        pt_id = pt.json()["playthroughId"]
        pt_token = pt.json()["playthroughAccessToken"]

        investigation = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
        )
        assert investigation.status_code == 200
        _assert_security_headers(investigation)


def test_sanitized_error_responses_carry_security_headers(phase5_app):
    with phase6_client(phase5_app) as c:
        unauth = c.get("/api/v1/playthroughs/nonexistent")
        assert unauth.status_code == 401
        _assert_security_headers(unauth)

        bad = c.get("/api/v1/playthroughs/nonexistent?version=abc")
        if bad.status_code == 401:  # auth runs first on that route
            bad = c.get("/api/v1/cases/nonexistent?version=abc")
        assert bad.status_code in (401, 404, 422)
        _assert_security_headers(bad)


def test_static_spa_serving_carries_security_headers(tmp_path, database_url):
    """With STATIC_DIR set, the served index.html carries the headers too
    (same-origin harden is active on the SPA shell as well)."""
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        "<!doctype html><html><body>Procedural Detective</body></html>\n",
        encoding="utf-8",
    )
    application = create_app(
        Settings(database_url=database_url, static_dir=str(build))
    )
    try:
        with TestClient(application) as c:
            res = c.get("/")
            assert res.status_code == 200
            assert "Procedural Detective" in res.text
            _assert_security_headers(res)
            unknown_api = c.get("/api/v1/not-a-route")
            assert unknown_api.status_code == 404
            _assert_security_headers(unknown_api)
    finally:
        application.state.engine.dispose()
        application.state.store.dispose()


def test_phase20_full_csp_baseline_is_sent_without_unsafe_eval():
    """PD-SEC-08 — the FULL source-restricting CSP baseline is now sent.

    This test REPLACES the pre-Phase-20 contract (which deliberately sent only
    ``frame-ancestors 'none'`` and asserted no full CSP). Phase 20 adds the
    full default-src baseline for defense-in-depth; the baseline is asserted
    EXACTLY — in particular it must contain ``script-src 'self'`` with NO
    ``'unsafe-eval'`` service (the production Vite/Babylon build was verified
    to contain no eval()/new Function/WebAssembly usage; a future change that
    adds unsafe-eval must carry an explicit justified whitelist decision)."""
    from app.main import _SECURITY_HEADERS
    from app.main import SecurityHeadersMiddleware

    assert SecurityHeadersMiddleware is not None
    csp = dict(_SECURITY_HEADERS)["content-security-policy"]
    assert csp == _CSP_BASELINE
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ):
        assert directive in csp, directive
    assert "'unsafe-eval'" not in csp
    assert "'unsafe-inline'" not in csp.replace("style-src 'self' 'unsafe-inline'", "")
    assert "http:" not in csp and "https:" not in csp
