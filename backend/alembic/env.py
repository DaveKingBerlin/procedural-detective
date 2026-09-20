"""Alembic environment.

Reads DATABASE_URL from the application Settings (backend/app/core/config.py)
so the one configuration source is never duplicated. A ``sqlalchemy.url`` set
programmatically (tests) takes precedence.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

# Make both the repo root and the backend package importable regardless of the
# working directory (migrations run from the repo root or from backend/).
ALEMBIC_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ALEMBIC_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
for _path in (str(REPO_ROOT), str(BACKEND_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.core.config import Settings  # noqa: E402

config = context.config
if config.config_file_name is not None:
    # ``disable_existing_loggers=False``: alembic's logging config must not
    # DISABLE the application's own loggers.
    #
    # With the default ``True``, ``fileConfig`` marks every pre-existing,
    # unconfigured logger as ``disabled`` (Python 3.12 ``Logger.disabled`` is
    # honored by ``isEnabledFor``, so a disabled logger drops EVERY record
    # regardless of level). In this repo the "procedural-detective" logger is
    # created as soon as ``app.main`` is imported, and alembic runs IN-PROCESS
    # in the backend test suite (conftest ``upgrade_db``) — so a migration
    # executed after an ``app.main`` import silently disabled the app's
    # structured events for the rest of the process (order-dependent caplog
    # failures in test_ollama_driver.py). Alembic's own root/sqlalchemy/alembic
    # loggers are still configured exactly as before.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

_url = config.get_main_option("sqlalchemy.url")
if not _url:
    _url = Settings().database_url
    config.set_main_option("sqlalchemy.url", _url)

# Phase 2 has no ORM metadata yet; future domain tables supply target_metadata.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL without a DBURL)."""
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the configured database."""
    connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
    connectable = create_engine(_url, connect_args=connect_args)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()