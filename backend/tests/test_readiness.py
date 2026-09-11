"""Readiness endpoint tests: 200 ready after migration, 503 NOT_READY envelope
for a missing migration state and for an unreachable database (sanitized)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_readiness_ready_when_migrated(migrated_client):
    res = migrated_client.get("/api/v1/readiness")
    assert res.status_code == 200
    assert res.json() == {"status": "ready", "database": "ok", "migrations": "ok"}


def test_readiness_not_ready_without_migrations(client):
    res = client.get("/api/v1/readiness")
    assert res.status_code == 503
    body = res.json()
    assert body == {
        "error": {
            "code": "NOT_READY",
            "message": "migrations not applied",
            "details": {"database": "ok", "migrations": "error"},
        }
    }


def test_readiness_not_ready_when_database_unreachable(database_url, tmp_path):
    missing_dir = tmp_path / "missing-dir"
    unreachable = f"sqlite:///{(missing_dir / 'db.sqlite').as_posix()}"
    application = create_app(Settings(database_url=unreachable))
    try:
        with TestClient(application) as c:
            res = c.get("/api/v1/readiness")
        assert res.status_code == 503
        body = res.json()
        assert body["error"]["code"] == "NOT_READY"
        assert body["error"]["details"] == {"database": "error", "migrations": "error"}
        # Sanitization: internal paths/URLs must never leak.
        assert "missing-dir" not in res.text
        assert "db.sqlite" not in res.text
        assert "Traceback" not in res.text
    finally:
        application.state.engine.dispose()