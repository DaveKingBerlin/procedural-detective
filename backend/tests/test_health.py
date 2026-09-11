"""Health endpoint tests: the exact shared contract body, and proof that the
liveness probe never touches the database."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health_returns_exact_contract(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json() == {
        "status": "ok",
        "service": "procedural-detective",
        "version": "0.1.0",
    }
    assert res.headers["content-type"] == "application/json"


def test_health_does_not_touch_the_database(database_url, tmp_path):
    # Point the app at a database that cannot possibly be reached.
    missing_dir = tmp_path / "missing-dir"
    unreachable = f"sqlite:///{(missing_dir / 'db.sqlite').as_posix()}"
    application = create_app(Settings(database_url=unreachable))
    try:
        with TestClient(application) as c:
            res = c.get("/api/v1/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
    finally:
        application.state.engine.dispose()