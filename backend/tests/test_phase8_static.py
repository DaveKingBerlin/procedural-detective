"""Phase 8 J2/J5 — static/SPA serving tests.

With ``STATIC_DIR`` set (the production/container mode) the backend must:

- serve the built index.html at ``/`` (no-store headers),
- fall back to index.html for SPA routes (/scene, /accuse, /reveal) so a
  browser refresh works,
- serve built assets under ``/assets`` with immutable caching,
- keep the API surface untouched (health 200; unknown /api paths keep the
  404 error envelope instead of an HTML page),
- never leak a file OUTSIDE the static root (path traversal -> 404).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings
from app.main import create_app

INDEX_BODY = "<html><body>procedural-detective root</body></html>"
ASSET_BODY = "console.log('built asset');"
RELEASE_MANIFEST_BODY = '{"version":"1.0.0","dirty":false}\n'
_SPA_ROUTES = ("/scene", "/accuse", "/reveal")


@pytest.fixture
def static_dir(tmp_path):
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "releases").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_BODY, encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text(ASSET_BODY, encoding="utf-8")
    (root / "releases" / "release-manifest.json").write_bytes(
        RELEASE_MANIFEST_BODY.encode("utf-8")
    )
    # A secret placed OUTSIDE the static root must never be reachable.
    (tmp_path / "secret-outside.txt").write_text("TOP SECRET", encoding="utf-8")
    return root


@pytest.fixture
def static_app(database_url, static_dir):
    application = create_app(
        Settings(database_url=database_url, static_dir=str(static_dir))
    )
    yield application
    application.state.engine.dispose()


@pytest.fixture
def static_client(static_app):
    with TestClient(static_app) as c:
        yield c


def test_root_serves_index_with_no_store(static_client):
    res = static_client.get("/")
    assert res.status_code == 200
    assert res.text == INDEX_BODY
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["pragma"] == "no-cache"


@pytest.mark.parametrize("route", _SPA_ROUTES)
def test_spa_fallback_serves_index(static_client, route):
    res = static_client.get(route)
    assert res.status_code == 200, route
    assert res.text == INDEX_BODY
    assert res.headers["cache-control"] == "no-store"


def test_spa_fallback_nested_and_query(static_client):
    for path in ("/scene/", "/reveal?caseId=x"):
        res = static_client.get(path)
        assert res.status_code == 200, path
        assert res.text == INDEX_BODY


def test_assets_served_with_immutable_cache(static_client):
    res = static_client.get("/assets/index-abc123.js")
    assert res.status_code == 200
    assert res.text == ASSET_BODY
    assert res.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_release_manifest_served_as_json_not_spa(static_client):
    res = static_client.get("/releases/release-manifest.json")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert res.headers["cache-control"] == "no-store"
    assert res.text == RELEASE_MANIFEST_BODY
    assert res.text != INDEX_BODY


def test_missing_release_file_is_not_spa(static_client):
    res = static_client.get("/releases/does-not-exist.json")
    assert res.status_code == 404
    assert res.json() == {
        "error": {"code": "NOT_FOUND", "message": "Not found", "details": None}
    }
    assert INDEX_BODY not in res.text


def test_releases_prefix_does_not_fall_back_to_spa(static_client):
    for path in ("/releases", "/releases/"):
        res = static_client.get(path)
        assert res.status_code == 404, path
        assert INDEX_BODY not in res.text


def test_api_health_still_works_with_static_enabled(static_client):
    res = static_client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    # The API surface is BEFORE the catch-all: unknown /api paths keep the 404
    # JSON error envelope (SEP: never an HTML SPA page, never index.html).
    missing = static_client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {"code": "NOT_FOUND", "message": "Not found", "details": None}
    }
    assert INDEX_BODY not in missing.text


def test_missing_asset_is_404_not_index(static_client):
    res = static_client.get("/assets/nope.js")
    assert res.status_code == 404
    assert INDEX_BODY not in res.text


def test_path_traversal_never_leaks_outside_static_root(static_client):
    """Client-side normalization happens in httpx, so the security property is
    asserted on the CONTENT: the outside secret never appears in any response."""
    for attempt in (
        "/assets/../secret-outside.txt",
        "/assets/..%2Fsecret-outside.txt",
        "/assets/%2e%2e/secret-outside.txt",
        "/assets/../../secret-outside.txt",
        "/assets/..%2F..%2Fsecret-outside.txt",
        "/releases/..%2Fsecret-outside.txt",
        "/releases/%2e%2e/secret-outside.txt",
        "/releases/foo/..%2F..%2Fsecret-outside.txt",
        "/releases/%5C..%5Csecret-outside.txt",
    ):
        res = static_client.get(attempt)
        assert "TOP SECRET" not in res.text, attempt


def test_encoded_release_traversal_fails_closed(static_client):
    for attempt in (
        "/releases/..%2Fsecret-outside.txt",
        "/releases/%2e%2e%2Fsecret-outside.txt",
        "/releases/foo/..%2F..%2Fsecret-outside.txt",
        "/releases/%5C..%5Csecret-outside.txt",
    ):
        res = static_client.get(attempt)
        assert res.status_code == 404, attempt
        assert INDEX_BODY not in res.text, attempt
        assert "TOP SECRET" not in res.text, attempt


def test_release_path_guard_rejects_raw_traversal_and_bounded_long_paths(static_dir):
    from app.main import _safe_static_path

    releases = static_dir.resolve() / "releases"
    for relative in (
        "../secret-outside.txt",
        "foo/../../secret-outside.txt",
        "..%2Fsecret-outside.txt",
        r"..\secret-outside.txt",
        "C:/secret-outside.txt",
        "a" * 4096,
    ):
        assert _safe_static_path(releases, relative) is None, relative[:80]


def test_raw_traversal_is_rejected_server_side(static_dir):
    """Direct unit of the server-side guard: RAW traversal relative paths
    (which no client normalization can strip) resolve to nothing."""
    from app.main import _safe_static_path

    root = static_dir.resolve()
    assert _safe_static_path(root, "assets/../../secret-outside.txt") is None
    assert _safe_static_path(root, "../secret-outside.txt") is None
    assert _safe_static_path(root, "assets/..%2F..%2Fsecret-outside.txt") is None
    assert _safe_static_path(root, "assets/index-abc123.js") is not None
    assert _safe_static_path(root, "index.html") is not None
    # A directory is not a file -> None (no directory listing or index dupes).
    assert _safe_static_path(root, "assets") is None


def test_dev_mode_without_static_dir_is_unchanged(client):
    """No STATIC_DIR (local dev) -> the API-only surface is untouched: /
    answers the 404 envelope, /scene is a 404, no index.html anywhere."""
    res = client.get("/")
    assert res.status_code == 404
    assert client.get("/scene").status_code == 404
