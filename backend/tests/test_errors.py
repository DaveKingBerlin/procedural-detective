"""Structured error envelope tests: 404/405 on the real app, and 422/500
through the registered handlers (exercised end-to-end on scratch apps)."""

from __future__ import annotations


def _envelope(res):
    body = res.json()
    assert set(body.keys()) == {"error"}
    error = body["error"]
    assert set(error.keys()) == {"code", "message", "details"}
    return error


def test_404_uses_envelope(client):
    res = client.get("/api/v1/does-not-exist")
    assert res.status_code == 404
    error = _envelope(res)
    assert error == {"code": "NOT_FOUND", "message": "Not Found", "details": None}
    assert "Traceback" not in res.text


def test_405_uses_envelope(client):
    res = client.post("/api/v1/health")
    assert res.status_code == 405
    error = _envelope(res)
    assert error["code"] == "METHOD_NOT_ALLOWED"
    assert error["details"] is None
    assert "Traceback" not in res.text


def test_422_validation_error_uses_envelope_and_strips_input(scratch_app):
    from fastapi.testclient import TestClient

    @scratch_app.get("/needs-int")
    def needs_int(count: int):  # noqa: F811
        return {"count": count}

    with TestClient(scratch_app) as c:
        res = c.get("/needs-int", params={"count": "not-an-int"})
    assert res.status_code == 422
    error = _envelope(res)
    assert error["code"] == "VALIDATION_ERROR"
    assert isinstance(error["message"], str) and error["message"]
    assert error["details"] is not None and "errors" in error["details"]
    # The submitted value must not be echoed back in the response.
    assert "not-an-int" not in res.text
    assert "Traceback" not in res.text


def test_500_internal_error_never_leaks(scratch_app):
    from fastapi.testclient import TestClient

    secret = "super-secret-internal-detail"

    @scratch_app.get("/boom")
    def boom():  # noqa: F811
        raise RuntimeError(secret)

    # Starlette 1.6's ServerErrorMiddleware re-raises the exception after
    # sending the 500 response so servers can log it; the test client must not
    # re-raise it in-process to observe the envelope that was actually sent.
    with TestClient(scratch_app, raise_server_exceptions=False) as c:
        res = c.get("/boom")
    assert res.status_code == 500
    error = _envelope(res)
    assert error == {"code": "INTERNAL_ERROR", "message": "Internal server error", "details": None}
    assert secret not in res.text
    assert "Traceback" not in res.text