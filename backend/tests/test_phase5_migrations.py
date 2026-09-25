"""Phase 5/6/7 + Phase 22 migration behavior (M28, M29, Phase5 L + Phase6 A +
Phase7 A + Phase22 bridge persistence).

- empty database -> head (alembic_version == 0005)
- Phase 2 baseline (0001) -> head succeeds
- constraint/index/trigger inventory exists (PKs, unique constraints,
  token_verifier indexes, published_versions/accusations immutability
  triggers, player_knowledge/accusations FK + (case_id, case_version) index,
  bridge pairing/session tables)
- downgrade drops every Phase 5/6/7/22 table (registered downgrade policy)
- application readiness confirms the expected migration head
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text

from app.db.session import migration_head
from conftest import downgrade_db, upgrade_db

EXPECTED_HEAD = "0005"

TABLES = (
    "anonymous_quota_sessions",
    "creator_credentials",
    "cases",
    "case_versions",
    "generation_attempts",
    "published_versions",
    "playthroughs",
    "player_knowledge",
    "accusations",
    "bridge_pairing_records",
    "bridge_sessions",
)


def _engine(database_url):
    return create_engine(database_url, connect_args={"check_same_thread": False})


def _table_names(engine):
    return set(inspect(engine).get_table_names())


def test_empty_database_upgrades_to_head(database_url):
    with _engine(database_url).connect() as conn:
        assert not conn.dialect.has_table(conn, "alembic_version")
    upgrade_db(database_url)
    assert migration_head() == EXPECTED_HEAD
    with _engine(database_url).connect() as conn:
        rows = list(
            conn.execute(text("SELECT version_num FROM alembic_version"))
        )
        assert [r[0] for r in rows] == [EXPECTED_HEAD]
    assert TABLES == tuple(t for t in TABLES if t in _table_names(_engine(database_url)))  # noqa: C419


def test_baseline_0001_upgrades_to_head(database_url):
    """M28: the Phase 2 baseline (0001) upgrades cleanly to Phase 5 head."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from conftest import REPO_ROOT, make_alembic_config

    _ = REPO_ROOT  # kept for parity with existing migration tests
    # Pin the DB at the 0001 baseline revision first.
    command.upgrade(make_alembic_config(database_url), "0001")
    upgrade_db(database_url)
    with _engine(database_url).connect() as conn:
        rows = list(
            conn.execute(text("SELECT version_num FROM alembic_version"))
        )
        assert [r[0] for r in rows] == [EXPECTED_HEAD]


def test_constraints_and_indexes_exist(database_url):
    upgrade_db(database_url)
    engine = _engine(database_url)
    insp = inspect(engine)

    # Composite PKs (collision backstops).
    assert {"constrained_columns": ["case_id", "version"]} in [
        insp.get_pk_constraint("case_versions")["constrained_columns"]
    ] or insp.get_pk_constraint("case_versions") == {
        "name": "pk_case_versions",
        "constrained_columns": ["case_id", "version"],
    }
    pk = insp.get_pk_constraint("published_versions")
    assert set(pk["constrained_columns"]) == {"case_id", "case_version"}

    # One authoritative attempt per version.
    unique_keys = [
        set(u.get("column_names", []))
        for u in insp.get_unique_constraints("generation_attempts")
    ]
    assert {"case_id", "case_version"} in unique_keys

    # Token verifier columns are unique (separate stores). SQLite expresses
    # UNIQUE as a unique INDEX (not a "unique constraint").
    session_indexes = {
        (tuple(ix["column_names"]), bool(ix.get("unique")))
        for ix in insp.get_indexes("anonymous_quota_sessions")
    }
    assert (("token_verifier",), True) in session_indexes
    cred_indexes = {tuple(ix["column_names"]) for ix in insp.get_indexes("creator_credentials")}
    assert ("case_id",) in cred_indexes
    cred_unique = {
        (tuple(ix["column_names"]), bool(ix.get("unique")))
        for ix in insp.get_indexes("creator_credentials")
    }
    assert (("token_verifier",), True) in cred_unique
    pt_indexes = {tuple(ix["column_names"]) for ix in insp.get_indexes("playthroughs")}
    assert ("case_id", "case_version") in pt_indexes
    assert ("case_id",) in pt_indexes
    pt_unique = {
        (tuple(ix["column_names"]), bool(ix.get("unique")))
        for ix in insp.get_indexes("playthroughs")
    }
    assert (("token_verifier",), True) in pt_unique

    # Playthroughs allow MANY rows per version (no unique on the pair).
    pt_uk = {tuple(u.get("column_names", [])) for u in insp.get_unique_constraints("playthroughs")}
    assert ("case_id", "case_version") not in pt_uk

    # PlayerKnowledge: playthrough FK + (case_id, case_version) index.
    pk_names = _table_names(engine)
    assert "player_knowledge" in pk_names
    pk_pk = insp.get_pk_constraint("player_knowledge")
    assert set(pk_pk["constrained_columns"]) == {"playthrough_id"}
    pk_fks = insp.get_foreign_keys("player_knowledge")
    assert any(
        fk.get("referred_table") == "playthroughs"
        and fk.get("constrained_columns") == ["playthrough_id"]
        and fk.get("referred_columns") == ["playthrough_id"]
        for fk in pk_fks
    )
    pk_indexes = {tuple(ix["column_names"]) for ix in insp.get_indexes("player_knowledge")}
    assert ("case_id", "case_version") in pk_indexes

    # Accusations: playthrough FK + (case_id, case_version) index + one-row
    # primary key (Phase7 A/H/N4).
    assert "accusations" in _table_names(engine)
    acc_pk = insp.get_pk_constraint("accusations")
    assert set(acc_pk["constrained_columns"]) == {"playthrough_id"}
    acc_fks = insp.get_foreign_keys("accusations")
    assert any(
        fk.get("referred_table") == "playthroughs"
        and fk.get("constrained_columns") == ["playthrough_id"]
        and fk.get("referred_columns") == ["playthrough_id"]
        and fk.get("options", {}).get("ondelete") == "CASCADE"
        for fk in acc_fks
    )
    acc_indexes = {tuple(ix["column_names"]) for ix in insp.get_indexes("accusations")}
    assert ("case_id", "case_version") in acc_indexes

    # Immutability triggers on published_versions and accusations.
    with engine.connect() as conn:
        triggers = [
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        ]
    assert "published_versions_no_update" in triggers
    assert "published_versions_no_delete" in triggers
    assert "accusations_no_update" in triggers
    assert "accusations_no_delete" in triggers
    engine.dispose()


def test_downgrade_drops_phase5_tables(database_url):
    """The registered downgrade policy drops every Phase 5 table."""
    upgrade_db(database_url)
    downgrade_db(database_url)
    engine = _engine(database_url)
    names = _table_names(engine)
    for table in TABLES:
        assert table not in names, f"{table} survived downgrade"
    with engine.connect() as conn:
        triggers = list(
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        )
    assert triggers == []
    with engine.connect() as conn:
        rows = list(
            conn.execute(text("SELECT version_num FROM alembic_version"))
        )
    assert rows == []
    engine.dispose()


def test_29_readiness_confirms_head_after_upgrade(migrated_client):
    """M29: readiness reports migrations ok once the DB is at head (0004)."""
    res = migrated_client.get("/api/v1/readiness")
    assert res.status_code == 200
    assert res.json() == {"status": "ready", "database": "ok", "migrations": "ok"}


def test_reupgrade_after_downgrade_is_idempotent(database_url):
    upgrade_db(database_url)
    downgrade_db(database_url)
    upgrade_db(database_url)
    with _engine(database_url).connect() as conn:
        rows = list(
            conn.execute(text("SELECT version_num FROM alembic_version"))
        )
    assert [r[0] for r in rows] == [EXPECTED_HEAD]