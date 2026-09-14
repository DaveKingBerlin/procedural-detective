"""Controlled-startup migration runner (Phase8 I2).

The container/production entrypoint calls ``run_migrations()`` exactly ONCE
before the server starts: migrations run over a short-lived connection, the
server boots afterwards, and only then does ``/api/v1/readiness`` report ok
(it compares the database revision against the Alembic head dynamically,
REQUIREMENTS 42 / backend/app/db/session.py).

Startup-failure contract (Phase8 I3):

- a malformed ``DATABASE_URL`` is rejected by ``Settings`` itself at
  construction (config validator -> ValueError -> process exits non-zero);
- a correct-URL-but-unwritable database (e.g. missing volume directory)
  makes ``command.upgrade`` raise; this module re-raises a clear SANITIZED
  ``RuntimeError`` so the container/process exits non-zero with a readable
  message instead of serving a never-ready application. The original
  underlying error is logged server-side for the operator.
"""

from __future__ import annotations

import logging

from alembic import command
from alembic.config import Config as AlembicConfig

from app.core.config import Settings
from app.db.session import ALEMBIC_INI

logger = logging.getLogger("procedural-detective.startup")

# Public, sanitized failure message: no URL, no filesystem path, no secret.
_MIGRATION_FAILED_MESSAGE = (
    "Failed to apply database migrations: check DATABASE_URL and ensure the "
    "database directory exists and is writable."
)


def run_migrations(settings: Settings | None = None) -> None:
    """Run ``alembic upgrade head`` against the app's configured database.

    Raises a sanitized ``RuntimeError`` (propagating out -> non-zero process
    exit for the container entrypoint) when the migration fails.
    """
    settings = settings if settings is not None else Settings()
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    try:
        command.upgrade(cfg, "head")
    except Exception:  # noqa: BLE001 - every migration failure aborts startup
        logger.exception("Database migration failed at startup")
        raise RuntimeError(_MIGRATION_FAILED_MESSAGE) from None


__all__ = ["run_migrations"]