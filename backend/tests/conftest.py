"""Shared fixtures for the backend test suite.

Every test gets its own temporary SQLite file under tmp_path, injected through
``Settings`` (the app's single configuration source) and ``TestClient``.
Migrations are applied programmatically via the Alembic command API.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

DEFAULT_CORS = ["http://localhost:5173"]


def make_alembic_config(database_url: str) -> AlembicConfig:
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_db(database_url: str) -> None:
    command.upgrade(make_alembic_config(database_url), "head")


def downgrade_db(database_url: str) -> None:
    command.downgrade(make_alembic_config(database_url), "base")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def database_url(db_path):
    return f"sqlite:///{db_path.as_posix()}"


@pytest.fixture
def settings(database_url):
    from app.core.config import Settings

    return Settings(database_url=database_url)


@pytest.fixture
def app(database_url):
    """Unmigrated application (no DB/readiness guarantees)."""
    from app.core.config import Settings
    from app.main import create_app

    application = create_app(
        Settings(database_url=database_url, cors_allowed_origins=DEFAULT_CORS)
    )
    yield application
    application.state.engine.dispose()


@pytest.fixture
def migrated_app(database_url):
    """Application whose database has been migrated to head."""
    upgrade_db(database_url)
    from app.core.config import Settings
    from app.main import create_app

    application = create_app(
        Settings(database_url=database_url, cors_allowed_origins=DEFAULT_CORS)
    )
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def migrated_client(migrated_app):
    from fastapi.testclient import TestClient

    with TestClient(migrated_app) as test_client:
        yield test_client


@pytest.fixture
def scratch_app():
    """Minimal FastAPI with only the structured-error handlers registered.

    Used to exercise 422/500 error paths end-to-end without adding routes to
    the production application surface.
    """
    from fastapi import FastAPI

    from app.main import register_exception_handlers

    application = FastAPI(title="scratch")
    register_exception_handlers(application)
    return application