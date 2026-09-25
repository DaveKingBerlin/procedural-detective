"""Migration tests: upgrade->head sets alembic_version, downgrade->base empties
it, re-running upgrade is idempotent, and the documented CLI path works from
the repo root (REQUIREMENTS 42/47).

The migration chain head is 0005 as of Phase 22 (0001 baseline + 0002 cases /
auth / playthroughs + 0003 player_knowledge + 0004 accusations + 0005 BYO-
Ollama bridge). ``migration_head()`` is read dynamically everywhere possible;
the two explicit head literals below assert the exact current head so a
regression cannot silently shift it.
"""

from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import create_engine, text

from app.db.session import migration_head
from conftest import downgrade_db, upgrade_db, REPO_ROOT

# The exact chain head this phase delivers (0001 baseline -> 0002 Phase 5 ->
# 0003 Phase 6 player knowledge -> 0004 Phase 7 accusations -> 0005 Phase 22
# BYO-Ollama bridge).
EXPECTED_HEAD = "0005"


def _engine(database_url):
    return create_engine(database_url, connect_args={"check_same_thread": False})


def _versions(database_url):
    with _engine(database_url).connect() as conn:
        return [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]


def test_upgrade_head_records_head_revision(database_url):
    engine = _engine(database_url)
    try:
        with engine.connect() as conn:
            assert not conn.dialect.has_table(conn, "alembic_version")
    finally:
        engine.dispose()

    head = migration_head()
    assert head == EXPECTED_HEAD

    upgrade_db(database_url)
    assert _versions(database_url) == [head]


def test_downgrade_base_empties_version(database_url):
    upgrade_db(database_url)
    assert _versions(database_url) == [EXPECTED_HEAD]

    downgrade_db(database_url)
    assert _versions(database_url) == []


def test_reupgrade_head_is_idempotent(database_url):
    upgrade_db(database_url)
    upgrade_db(database_url)  # must not raise
    assert _versions(database_url) == [migration_head()]


def test_cli_path_from_repo_root(tmp_path):
    """python -m alembic -c backend/alembic.ini upgrade head from the repo root."""
    db_url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    env.pop("ENV_FILE", None)

    proc = subprocess.run(
        [
            sys.executable, "-m", "alembic",
            "-c", "backend/alembic.ini",
            "upgrade", "head",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _versions(db_url) == [migration_head()]

    # The env-provided DATABASE_URL must have been used: no default db created.
    assert not (REPO_ROOT / "procedural_detective.db").exists()