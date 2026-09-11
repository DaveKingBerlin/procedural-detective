"""Database engine/session helpers and readiness primitives (REQUIREMENTS 42).

Phase 2 owns only the migration chain. No ORM/domain tables exist yet. Future
immutable case versions and playthroughs are added as numbered Alembic
revisions on top of the 0001 baseline; the revision chain itself is the
versioning mechanism — released revisions are never edited after release.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from app.core.config import Settings

# backend/app/db/session.py -> 2 levels up is the backend/ directory.
BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def _sqlite_connect_args(url: str) -> dict[str, Any]:
    """SQLite needs check_same_thread=False for FastAPI's threadpool workers."""
    if make_url(url).get_backend_name().startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def create_db_engine(settings: Settings) -> Engine:
    """Build the application engine from a single Settings object."""
    return create_engine(
        settings.database_url,
        connect_args=_sqlite_connect_args(settings.database_url),
    )


def database_ok(engine: Engine) -> bool:
    """True when a real connection can execute ``SELECT 1``.

    Readiness must open a real connection; it must never be inferred from
    mere engine construction (SQLAlchemy connects lazily).
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def migration_head() -> str | None:
    """Head revision id from the Alembic script directory (None on any error)."""
    try:
        cfg = AlembicConfig(str(ALEMBIC_INI))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        return None


def migration_current(engine: Engine) -> str | None:
    """Revision currently recorded in the target database.

    ``None`` for a database that has never been migrated (no alembic_version
    entry) or on any error.
    """
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            return context.get_current_revision()
    except Exception:
        return None


def readiness_status(engine: Engine) -> dict[str, str]:
    """Return ``{"database": "ok"|"error", "migrations": "ok"|"error"}``.

    Only contains sanitized status words — never paths, URLs or secrets.
    """
    db_ok = database_ok(engine)
    if db_ok:
        current = migration_current(engine)
        head = migration_head()
        migrations_ok = head is not None and current == head
    else:
        migrations_ok = False
    return {
        "database": "ok" if db_ok else "error",
        "migrations": "ok" if migrations_ok else "error",
    }