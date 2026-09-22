"""Phase 21 F-07 — audited operator deletion command (tools/delete_case.py).

Replaces the raw-SQL single-case procedure of docs/PRIVACY.md §3.2 with an
audited maintenance command that: validates the target, deletes atomically
(case + published versions + playthroughs + player_knowledge + accusations +
sessions linked), re-creates the immutability triggers IDENTICALLY (migrations
0002/0004 DDL via app.persistence.triggers), aborts on ANY mismatch, and
verifies immutability protections still hold AFTER cleanup.

This suite is a DUMMY-RUN: it never touches the real dev database. The CLI is
exercised ONLY against a per-test disposable scratch SQLite file (the
conftest ``database_url``/``db_path`` fixtures + monkeypatched DATABASE_URL
for the end-to-end ``main()`` path). The autouse conftest network block is
active.

Regression matrix (Phase21-PHC.md §2 F-07):
  - target data deleted
  - unrelated data preserved
  - every immutability trigger exists afterward
  - a forbidden mutation (UPDATE/DELETE on a published case / accusation) is
    still rejected at the database level AFTER the cleanup
  - refusal without --yes; abort on a missing case; sanitized CLI output
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.persistence.store import (  # noqa: E402
    CaseNotFoundError,
    Store,
    StoreError,
)
from app.persistence.triggers import (  # noqa: E402
    IMMUTABILITY_TRIGGER_NAMES,
    registered_immutability_trigger_names,
)

# Relative import works because the backend tests dir is on sys.path.
from conftest import upgrade_db  # noqa: E402


def _tokens():
    from app.auth.tokens import issue_token, verifier as _verifier

    return _verifier(issue_token())


def _seed_case(store, *, case_id, session_id, now, playthrough_id, version=1):
    """Persist a full published case (rows for every table F-07 deletes)."""
    store.create_case(
        case_id=case_id,
        quota_session_id=session_id,
        title=f"Case {case_id}",
        difficulty="medium",
        created_at=now,
        next_version=version + 1,
    )
    store.create_case_version(
        case_id=case_id,
        version=version,
        state="PUBLISHED",
        generation_id=f"GEN-{version}",
        created_at=now,
    )
    store.upsert_generation_attempt(
        attempt_id=f"att-{case_id}-{version}",
        case_id=case_id,
        case_version=version,
        status="PUBLISHED",
        stage="published",
        progress=100,
        created_at=now,
        updated_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=version,
        payload_json='{"scene": "A"}',
        published_at=now,
    )
    store.create_creator_credential(
        case_id=case_id,
        token_verifier=_tokens(),
        created_at=now,
        expires_at=now + 86400,
    )
    store.create_playthrough(
        playthrough_id=playthrough_id,
        case_id=case_id,
        case_version=version,
        token_verifier=_tokens(),
        state="PLAYING",
        created_at=now,
        expires_at=now + 86400,
    )
    store.get_or_create_player_knowledge(playthrough_id, case_id, version, at=now)
    store.insert_accusation_if_unaccused(
        playthrough_id=playthrough_id,
        case_id=case_id,
        case_version=version,
        murderer_id="thomas_reed",
        motive_id="cover_up_embezzlement",
        weapon_id="kitchen_knife",
        crime_time="2026-09-11T22:17:00+02:00",
        created_at=now,
    )


@pytest.fixture
def seeded_db(database_url):
    """Scratch migrated DB with TWO full published cases (A = target, B = kept)."""
    upgrade_db(database_url)
    store = Store(database_url)
    now = 1_000_000.0
    store.create_session(
        session_id="quota-1",
        token_verifier=_tokens(),
        quota_window_end=now + 3600,
        created_at=now,
    )
    _seed_case(
        store,
        case_id="case-a-target",
        session_id="quota-1",
        now=now,
        playthrough_id="pt-a-1",
    )
    _seed_case(
        store,
        case_id="case-b-unrelated",
        session_id="quota-1",
        now=now,
        playthrough_id="pt-b-1",
    )
    yield store, now
    store.dispose()


def _sqlite3(db_path):
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys=ON")
    return con


# --------------------------------------------------------------------------- #
# the audited deletion
# --------------------------------------------------------------------------- #


def test_delete_case_removes_target_and_preserves_unrelated(database_url, db_path, seeded_db):
    from tools.delete_case import run_delete_case

    store, _ = seeded_db
    before_a = store.count_case_references("case-a-target")
    assert before_a["cases"] == 1 and before_a["published_versions"] == 1
    before_b = store.count_case_references("case-b-unrelated")
    assert before_b["published_versions"] == 1 and before_b["playthroughs"] == 1

    code = run_delete_case("case-a-target", database_url, yes=True)
    assert code == 0

    # target data deleted
    assert store.get_case("case-a-target") is None
    after_a = store.count_case_references("case-a-target")
    assert after_a == {table: 0 for table in after_a}
    # unrelated data preserved entirely
    after_b = store.count_case_references("case-b-unrelated")
    assert after_b == before_b
    assert store.get_case("case-b-unrelated") is not None
    assert store.get_published("case-b-unrelated", 1) is not None
    assert store.get_playthrough_by_id("pt-b-1") is not None
    assert store.get_accusation("pt-b-1") is not None


def test_every_immutability_trigger_exists_after_delete(db_path, seeded_db):
    from tools.delete_case import run_delete_case

    store, _ = seeded_db
    assert run_delete_case("case-a-target", "sqlite:///" + db_path.as_posix(), yes=True) == 0
    con = _sqlite3(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
        registered = {row[0] for row in rows}
    finally:
        con.close()
    assert IMMUTABILITY_TRIGGER_NAMES <= registered
    assert store.missing_immutability_triggers() == frozenset()


def test_forbidden_mutation_still_rejected_after_delete(db_path, seeded_db):
    """Immutability protections hold AFTER cleanup: raw UPDATE/DELETE on the
    REMAINING published case / accusation is aborted at the database level."""
    from tools.delete_case import run_delete_case

    store, _ = seeded_db
    assert run_delete_case("case-a-target", "sqlite:///" + db_path.as_posix(), yes=True) == 0

    con = _sqlite3(db_path)
    try:
        # UPDATE of a remaining published payload is rejected by the trigger.
        with pytest.raises(sqlite3.Error) as excinfo:
            con.execute(
                "UPDATE published_versions SET published_at = 0 WHERE case_id = ?",
                ("case-b-unrelated",),
            )
        assert "immutable" in str(excinfo.value)
        con.rollback()
        # DELETE of a remaining published payload is rejected.
        with pytest.raises(sqlite3.Error) as excinfo:
            con.execute(
                "DELETE FROM published_versions WHERE case_id = ?",
                ("case-b-unrelated",),
            )
        assert "immutable" in str(excinfo.value)
        con.rollback()
        # UPDATE / DELETE of a remaining accusation are rejected.
        with pytest.raises(sqlite3.Error) as excinfo:
            con.execute(
                "UPDATE accusations SET weapon_id = 'x' WHERE playthrough_id = ?",
                ("pt-b-1",),
            )
        assert "immutable" in str(excinfo.value)
        con.rollback()
        with pytest.raises(sqlite3.Error) as excinfo:
            con.execute(
                "DELETE FROM accusations WHERE playthrough_id = ?",
                ("pt-b-1",),
            )
        assert "immutable" in str(excinfo.value)
        con.rollback()
        # And the surviving data is truly intact.
        assert con.execute(
            "SELECT count(*) FROM published_versions WHERE case_id = ?",
            ("case-b-unrelated",),
        ).fetchone()[0] == 1
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# CLI surface: --yes refusal, missing-case abort, sanitized output, main()
# --------------------------------------------------------------------------- #


def test_cli_refuses_without_yes_leave_data_unchanged(database_url, db_path, seeded_db):
    from tools.delete_case import run_delete_case

    store, _ = seeded_db
    code = run_delete_case("case-a-target", database_url, yes=False)
    assert code == 2  # validated but refused
    assert store.get_case("case-a-target") is not None
    assert store.count_case_references("case-a-target")["published_versions"] == 1
    # a second run without --yes still refuses identically (idempotent refusal)
    assert run_delete_case("case-a-target", database_url, yes=False) == 2


def test_cli_aborts_on_missing_case(database_url, db_path, seeded_db):
    from tools.delete_case import run_delete_case

    store, _ = seeded_db
    code = run_delete_case("does-not-exist", database_url, yes=True)
    assert code == 1
    # nothing changed: the unrelated case is intact and every trigger remains.
    assert store.count_case_references("case-b-unrelated")["published_versions"] == 1
    assert store.get_case("case-b-unrelated") is not None
    assert store.missing_immutability_triggers() == frozenset()
    assert store.count_case_references("does-not-exist")["cases"] == 0


def test_store_delete_case_cascade_aborts_when_target_missing(database_url, seeded_db):
    store, _ = seeded_db
    with pytest.raises(CaseNotFoundError):
        store.delete_case_cascade("does-not-exist")
    # Rolled back cleanly: triggers untouched, unrelated case intact.
    assert store.missing_immutability_triggers() == frozenset()
    assert store.get_case("case-b-unrelated") is not None


def test_main_end_to_end_scratch_via_env(database_url, db_path, seeded_db, monkeypatch, capsys):
    """DUMMY-RUN: ``python -m tools.delete_case`` through ``main()`` with the
    canonical DATABASE_URL env pointed at the SCRATCH file only. The real dev
    DB is never referenced (monkeypatched env keeps Settings() off it)."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    from tools.delete_case import main

    # without --yes: exit 2, sanitized refusal, nothing deleted
    code = main(["case-a-target"])
    assert code == 2
    out = capsys.readouterr()
    assert "pass --yes" in out.out
    assert "case-a-target" in out.out
    store, _ = seeded_db
    assert store.get_case("case-a-target") is not None

    # missing case with --yes: exit 1 with a sanitized error
    code = main(["nope-missing", "--yes"])
    assert code == 1
    err = capsys.readouterr().err
    assert "does not exist" in err
    assert "Traceback" not in err and "sqlite" not in err.lower()

    # with --yes: exit 0, target gone, triggers verified in output
    code = main(["case-a-target", "--yes"])
    assert code == 0
    out = capsys.readouterr().out
    assert "immutability triggers verified: 4/4" in out
    assert store.get_case("case-a-target") is None
    assert store.get_case("case-b-unrelated") is not None


def test_cli_output_is_sanitized_no_internals(database_url, db_path, seeded_db, capsys):
    from tools.delete_case import run_delete_case

    code = run_delete_case("case-a-target", database_url, yes=True)
    assert code == 0
    out_err = capsys.readouterr()
    text = (out_err.out + out_err.err).lower()
    for leaked in ("traceback", "line 1", "sqlalchemy", "operationalerror", "c:", "\\backend\\"):
        assert leaked not in text
    assert "immutability triggers verified: 4/4" in text  # honest post-cleanup report