"""Configuration tests (REQUIREMENTS 45): single Settings source, canonical
environment names, dotenv handling, no duplicate aliases, and the DEF-015/018/
020/022/023 configuration guards."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine.url import make_url

from app.core.config import REPO_ROOT, Settings

CANONICAL_ENV_NAMES = {
    "DATABASE_URL",
    "API_HOST",
    "API_PORT",
    "CORS_ALLOWED_ORIGINS",
    "ENV_FILE",
}

BACKEND_DIR = Path(__file__).resolve().parents[1]

EXPECTED_DEFAULT_URL = f"sqlite:///{(REPO_ROOT / 'procedural_detective.db').as_posix()}"


def _clear_env(monkeypatch) -> None:
    for key in CANONICAL_ENV_NAMES:
        monkeypatch.delenv(key, raising=False)


def test_defaults(monkeypatch):
    _clear_env(monkeypatch)
    s = Settings()
    # DEF-018: the default database URL is anchored at the repo root and is
    # identical regardless of the process working directory.
    assert s.database_url == EXPECTED_DEFAULT_URL
    assert s.api_host == "127.0.0.1"
    assert s.api_port == 8000
    assert s.cors_allowed_origins == ["http://localhost:5173"]
    assert s.env_file is None


def test_environment_overrides(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./override.db")
    monkeypatch.setenv("API_HOST", "0.0.0.0")
    monkeypatch.setenv("API_PORT", "9000")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://a.example, http://b.example")
    s = Settings()
    assert s.database_url == "sqlite:///./override.db"
    assert s.api_host == "0.0.0.0"
    assert s.api_port == 9000
    assert s.cors_allowed_origins == ["http://a.example", "http://b.example"]


def test_cors_single_origin_and_injected_list(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    assert Settings().cors_allowed_origins == ["http://localhost:5173"]
    injected = Settings(cors_allowed_origins=["https://custom.example"])
    assert injected.cors_allowed_origins == ["https://custom.example"]


def test_dotenv_loaded_via_env_file(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    dotenv = tmp_path / "custom.env"
    dotenv.write_text("DATABASE_URL=sqlite:///./from_dotenv.db\nAPI_PORT=9001\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(dotenv))
    s = Settings()
    assert s.database_url == "sqlite:///./from_dotenv.db"
    assert s.api_port == 9001
    assert s.env_file == str(dotenv)


def test_real_environment_wins_over_dotenv(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    dotenv = tmp_path / "custom.env"
    dotenv.write_text("DATABASE_URL=sqlite:///./dotenv.db\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(dotenv))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./env_wins.db")
    s = Settings()
    assert s.database_url == "sqlite:///./env_wins.db"


def test_missing_explicit_env_file_is_ignored(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "does-not-exist.env"))
    s = Settings()
    assert s.database_url == EXPECTED_DEFAULT_URL


def test_invalid_api_port_raises(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("API_PORT", "not-a-port")
    with pytest.raises(ValidationError):
        Settings()


def test_exactly_one_canonical_name_per_setting(monkeypatch):
    """Each canonical env var maps to exactly one field (no duplicate aliases).

    Behavioral proof: setting one canonical variable changes exactly that
    setting and no other; setting all five drives all five fields.
    """
    _clear_env(monkeypatch)
    probes = {
        "DATABASE_URL": "sqlite:///./probe.db",
        "API_HOST": "1.2.3.4",
        "API_PORT": "1234",
        "CORS_ALLOWED_ORIGINS": "http://probe.example",
        "ENV_FILE": "unused.env",  # non-existent path: dotenv ignored
    }
    for key, value in probes.items():
        monkeypatch.setenv(key, value)
    s = Settings()
    assert s.database_url == probes["DATABASE_URL"]
    assert s.api_host == probes["API_HOST"]
    assert s.api_port == 1234
    assert s.cors_allowed_origins == ["http://probe.example"]
    assert s.env_file == probes["ENV_FILE"]

    # Setting one variable must not alias into another field.
    monkeypatch.delenv("API_HOST", raising=False)
    _clear_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./alpha.db")
    baseline = Settings()
    monkeypatch.setenv("API_HOST", "9.9.9.9")
    changed = Settings()
    assert changed.database_url == baseline.database_url  # untouched by API_HOST


def test_unknown_environment_variables_are_ignored(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sekret-value")
    monkeypatch.setenv("MAX_LLM_CALLS_PER_GENERATION", "8")
    s = Settings()
    assert s.database_url == EXPECTED_DEFAULT_URL


# ---------------------------------------------------------------------------
# DEF-015 — wildcard / null CORS origins are rejected up front
# ---------------------------------------------------------------------------
def test_cors_wildcard_is_rejected(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(ValidationError) as excinfo:
        Settings(cors_allowed_origins=["*"])
    assert "CORS_ALLOWED_ORIGINS must not use '*' (credentials are enabled)" in str(
        excinfo.value
    )
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ValidationError):
        Settings()


def test_cors_null_origin_is_rejected(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(ValidationError):
        Settings(cors_allowed_origins=["null"])
    with pytest.raises(ValidationError):
        Settings(cors_allowed_origins=["http://a.example", "null", "http://b.example"])


def test_cors_explicit_origins_still_accepted(monkeypatch):
    _clear_env(monkeypatch)
    s = Settings(cors_allowed_origins=["http://localhost:5173", "https://prod.example"])
    assert s.cors_allowed_origins == ["http://localhost:5173", "https://prod.example"]


# ---------------------------------------------------------------------------
# DEF-018 — default DATABASE_URL is CWD-independently anchored at the repo root
# ---------------------------------------------------------------------------
def test_default_database_url_is_absolute_and_repo_rooted(monkeypatch):
    _clear_env(monkeypatch)
    url = Settings().database_url
    assert url == EXPECTED_DEFAULT_URL
    assert url.startswith("sqlite:///")
    assert "\\" not in url  # platform-correct: no Windows backslashes in the URL
    parsed = make_url(url)
    assert parsed.get_backend_name() == "sqlite"
    db_path = Path(parsed.database)
    assert db_path.is_absolute()
    assert db_path == REPO_ROOT / "procedural_detective.db"


def test_default_database_url_is_cwd_independent(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    base = Settings().database_url
    monkeypatch.chdir(tmp_path)
    assert Settings().database_url == base
    monkeypatch.chdir(BACKEND_DIR)
    assert Settings().database_url == base


def test_migrate_then_serve_roundtrip_from_different_cwds(monkeypatch, tmp_path):
    """DEF-018 live proof: migrate from one working directory, then serve from a
    different one with the same configuration — the readiness roundtrip must see
    the same (migrated) database instead of a second, empty file."""
    from app.db.session import readiness_status
    from app.main import create_app
    from conftest import upgrade_db

    db_file = tmp_path / "shared.db"
    url = f"sqlite:///{db_file.as_posix()}"
    _clear_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "unused.env"))  # ensure no dotenv

    monkeypatch.chdir(tmp_path)  # "repo root" CWD
    assert Settings().database_url == url
    upgrade_db(url)
    monkeypatch.chdir(BACKEND_DIR)  # "serving" CWD
    s = Settings()
    assert s.database_url == url
    application = create_app(s)
    try:
        assert readiness_status(application.state.engine) == {
            "database": "ok",
            "migrations": "ok",
        }
    finally:
        application.state.engine.dispose()


# ---------------------------------------------------------------------------
# DEF-020 — in-memory SQLite is rejected, not silently never-ready
# ---------------------------------------------------------------------------
def test_in_memory_sqlite_rejected(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(ValidationError) as excinfo:
        Settings(database_url="sqlite:///:memory:")
    assert "must use a persistent SQLite file" in str(excinfo.value)
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///file::memory:?cache=shared")


def test_sqlite_file_url_still_accepted(monkeypatch):
    _clear_env(monkeypatch)
    url = f"sqlite:///{(Path.home() / 'procedural_detective_test.db').as_posix()}"
    assert Settings(database_url=url).database_url == url


# ---------------------------------------------------------------------------
# DEF-022 — malformed DATABASE_URL fails cleanly (no raw SQLAlchemy traceback)
# ---------------------------------------------------------------------------
def test_malformed_database_url_fails_cleanly(monkeypatch):
    _clear_env(monkeypatch)
    for bad in ("not a valid url at all", "", "bad\x00url"):
        with pytest.raises(ValidationError) as excinfo:
            Settings(database_url=bad)
        text = str(excinfo.value)
        assert "Traceback" not in text
        assert any(
            needle in text
            for needle in (
                "DATABASE_URL is not a valid SQLAlchemy URL",
                "DATABASE_URL must not be empty",
                "DATABASE_URL must not contain NUL characters",
            )
        )


def test_module_import_path_default_url_ok_and_garbage_clean(monkeypatch):
    """import app.main must not raise for the DEFAULT good URL, and a garbage
    DATABASE_URL must surface the clean Settings error — never a raw
    sqlalchemy.exc.ArgumentError from the engine construction."""
    _clear_env(monkeypatch)
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env.pop("ENV_FILE", None)
    ok = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=120,
    )
    assert ok.returncode == 0, ok.stderr

    bad_env = os.environ.copy()
    bad_env.pop("ENV_FILE", None)
    bad_env["DATABASE_URL"] = "not a valid url at all"
    bad = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(BACKEND_DIR), env=bad_env, capture_output=True, text=True, timeout=120,
    )
    assert bad.returncode != 0
    assert "sqlalchemy.exc.ArgumentError" not in bad.stderr
    assert "DATABASE_URL is not a valid SQLAlchemy URL" in bad.stderr


# ---------------------------------------------------------------------------
# DEF-023 — API_PORT must be a real TCP port (1-65535)
# ---------------------------------------------------------------------------
def test_api_port_range_is_validated(monkeypatch):
    _clear_env(monkeypatch)
    for bad in (0, 65536, -1):
        with pytest.raises(ValidationError) as excinfo:
            Settings(api_port=bad)
        assert "API_PORT must be an integer between 1 and 65535" in str(excinfo.value)
    assert Settings(api_port=8000).api_port == 8000