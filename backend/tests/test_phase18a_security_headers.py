"""Phase 18A — minimal security headers (CSP frame-ancestors hardening).

The app is served same-origin (single container: FastAPI + built SPA) and never
needs iframing. A minimal, non-restrictive header set is added by
``SecurityHeadersMiddleware`` (outermost) so it reaches EVERY response:

- Content-Security-Policy: frame-ancestors 'none'  (CSP level-3 framing deny)
- X-Frame-Options: DENY                             (older-browser equivalent)
- X-Content-Type-Options: nosniff
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy: camera=(), microphone=(), geolocation=()

A FULL CSP (default-src ...) is deliberately NOT set: the Babylon.js renderer
needs data:/blob: URLs and generated inline material, so a restrictive
default-src would risk breaking the shipped demo (documented in main.py).

These headers must exist on health/readiness/capabilities, on the normal
published-case flow, on sanitized error responses and on the served SPA.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from phase5_helpers import auth, create_case, create_session  # noqa: E402
from phase6_helpers import client as phase6_client  # noqa: E402


def _assert_security_headers(res) -> None:
    headers = res.headers
    assert headers["content-security-policy"] == "frame-ancestors 'none'"
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


def test_no_full_default_src_csp_present():
    """The phase deliberately avoids a full CSP: only the framing directive is
    sent. Asserting this contract guards against a future default-src that
    would silently break the Babylon.js renderer in the shipped demo."""
    from app.main import _SECURITY_HEADERS
    from app.main import SecurityHeadersMiddleware

    assert SecurityHeadersMiddleware is not None
    csp = dict(_SECURITY_HEADERS)["content-security-policy"]
    assert csp == "frame-ancestors 'none'"      # framing-only
    assert "default-src" not in csp             # never a full restrictive CSP
    assert "frame-ancestors" in csp