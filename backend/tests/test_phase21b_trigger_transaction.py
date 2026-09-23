"""Phase 21B Finding 1 — trigger DDL is rollback-safe under failure injection.

The audit found that the store's governed maintenance paths (playthrough
retention prune + operator ``delete_case_cascade``) DROP the immutability
triggers and re-create them inside their transactions, but SQLAlchemy's ORM
"autobegin" alone does NOT guarantee a real SQLite transaction has begun:
under the pysqlite legacy isolation mode (``isolation_level=''``) the DBAPI
driver stays in AUTOCOMMIT until the first DML statement, so a
``DROP TRIGGER IF EXISTS`` executed as (or among) the first statements is
committed IMMEDIATELY by SQLite and can never be rolled back. A failure after
the DROP would roll back the row changes but leave the immutability trigger
MISSING (core persistence invariant violation).

Fix (this phase): ``Store._ensure_explicit_sqlite_transaction`` emits a
literal, driver-level ``BEGIN`` before ANY trigger DDL in both governed paths
and FAILS CLOSED when the boundary cannot be proven open. SQLite DDL stays
transactional inside that real transaction — the fix only guarantees the
transaction exists.

Required regression (Phase21B-PAC §1):
  - inject a controlled exception IMMEDIATELY AFTER the first ``DROP TRIGGER``
    in BOTH paths;
  - assert after rollback: original rows still exist, all required immutability
    triggers exist (registered names), and a forbidden mutation (raw
    UPDATE/DELETE) is STILL REJECTED at the database level;
  - process/interruption-recovery: an interruption before re-create leaves
    consistent state and the NEXT governed operation restores/completes.

Deterministic: the ``store`` fixture uses a per-test scratch SQLite FILE (never
``:memory:``), and all timestamps are explicit numbers — no wall clock is read.
The autouse conftest network block is active.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session as _SASession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.tokens import issue_token, verifier as _verifier  # noqa: E402
from app.persistence.triggers import (  # noqa: E402
    IMMUTABILITY_TRIGGER_NAMES,
    missing_immutability_trigger_names,
    registered_immutability_trigger_names,
)

_NOW = 1_000_000.0


class _InjectedTriggerFailure(RuntimeError):
    """Controlled mid-path failure injected by the regression provider."""


def _tokens():
    return _verifier(issue_token())


def _seed_published_case(store, *, case_id, session_id, now, version=1):
    """Persist a full published case (target seed shared by both paths)."""
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
        generation_id=f"GEN-{case_id}-{version}",
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


def _seed_playthrough(store, *, playthrough_id, case_id, version, state, now):
    """One pinned playthrough (+ knowledge) at the requested lifecycle state.
    Completed states go through the REAL phase-7 CAS transitions
    (PLAYING -> ACCUSED -> REVEALED) so the accusation row exists exactly as
    the store would have created it."""
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
    if state in ("ACCUSED", "REVEALED"):
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
    if state == "REVEALED":
        assert store.mark_playthrough_revealed(playthrough_id) is True
    assert store.get_playthrough_state(playthrough_id) == state
    return playthrough_id


def _inject_failure_after_first_drop(monkeypatch):
    """Wrap ``Session.execute``: execute the FIRST ``DROP TRIGGER IF EXISTS``
    statement for real, then raise — a controlled interruption immediately
    AFTER the DROP took effect at the driver level (the audit's exact window).

    Every other statement (SELECTs, DELETEs, CREATE TRIGGER, DDL guards) is
    passed through untouched, so the injection is precisely scoped.
    """
    real_execute = _SASession.execute
    state = {"drops_seen": 0}

    def _wrapped(self, statement, *args, **kwargs):
        sql = str(statement)
        if "DROP TRIGGER IF EXISTS" in sql and state["drops_seen"] == 0:
            state["drops_seen"] += 1
            result = real_execute(self, statement, *args, **kwargs)
            raise _InjectedTriggerFailure(
                "injected failure immediately after the first DROP TRIGGER"
            )
        return real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(_SASession, "execute", _wrapped)
    return state


def _inject_failure_before_recreate(monkeypatch):
    """Wrap ``Session.execute``: raise when the first ``CREATE TRIGGER``
    statement is about to run (before executing it) — a controlled
    interruption mid-path, after the DROP and the row deletions, BEFORE the
    re-create loop makes the guards visible again.
    """
    real_execute = _SASession.execute
    state = {"creates_seen": 0}

    def _wrapped(self, statement, *args, **kwargs):
        sql = str(statement)
        if "CREATE TRIGGER" in sql and state["creates_seen"] == 0:
            state["creates_seen"] += 1
            raise _InjectedTriggerFailure(
                "injected failure before the first CREATE TRIGGER (re-create)"
            )
        return real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(_SASession, "execute", _wrapped)
    return state


def _assert_triggers_present(store) -> None:
    with store._read_session() as session:
        registered = registered_immutability_trigger_names(session)
        assert IMMUTABILITY_TRIGGER_NAMES <= registered, (
            f"missing triggers after rollback: "
            f"{sorted(IMMUTABILITY_TRIGGER_NAMES - registered)}"
        )
        assert missing_immutability_trigger_names(session) == frozenset()


def _assert_forbidden_mutations_rejected(store, *, accusation_pt_id, kid=None):
    """Raw UPDATE/DELETE at the database level is STILL aborted by the guards:
    the accusation row (drop/re-create path) and — when ``kid`` given — the
    published payload (full 4-trigger path). Each attempt MUST raise."""
    from sqlalchemy import text

    with store._read_session() as session:
        with pytest.raises(Exception) as excinfo:
            session.execute(
                text("UPDATE accusations SET weapon_id = 'x' WHERE playthrough_id = :p"),
                {"p": accusation_pt_id},
            )
        assert "immutable" in str(excinfo.value).lower()
        with pytest.raises(Exception) as excinfo:
            session.execute(
                text("DELETE FROM accusations WHERE playthrough_id = :p"),
                {"p": accusation_pt_id},
            )
        assert "immutable" in str(excinfo.value).lower()
        if kid is not None:
            with pytest.raises(Exception) as excinfo:
                session.execute(
                    text("UPDATE published_versions SET published_at = 0 WHERE case_id = :c"),
                    {"c": kid},
                )
            assert "immutable" in str(excinfo.value).lower()
            with pytest.raises(Exception) as excinfo:
                session.execute(
                    text("DELETE FROM published_versions WHERE case_id = :c"),
                    {"c": kid},
                )
            assert "immutable" in str(excinfo.value).lower()


def _retention_counts(store, case_id, version=1):
    with store._read_session() as session:
        from sqlalchemy import text

        rows = session.execute(
            text(
                "SELECT state, count(*) FROM playthroughs "
                "WHERE case_id = :cid AND case_version = :v GROUP BY state"
            ),
            {"cid": case_id, "v": version},
        ).all()
        return dict(rows)


# --------------------------------------------------------------------------- #
# RETENTION PATH — failure immediately after the first DROP TRIGGER
# --------------------------------------------------------------------------- #


@pytest.fixture
def retention_store(store):
    """A published case with 2 completed (prune-eligible) rows + 1 active row."""
    store.create_session(
        session_id="quota-21b-retention",
        token_verifier=_tokens(),
        quota_window_end=_NOW + 3600,
        created_at=_NOW,
    )
    _seed_published_case(
        store, case_id="case-retention", session_id="quota-21b-retention", now=_NOW
    )
    _seed_playthrough(store, playthrough_id="pt-done-1", case_id="case-retention", version=1, state="ACCUSED", now=_NOW)
    _seed_playthrough(store, playthrough_id="pt-done-2", case_id="case-retention", version=1, state="REVEALED", now=_NOW + 1.0)
    _seed_playthrough(store, playthrough_id="pt-active-1", case_id="case-retention", version=1, state="PLAYING", now=_NOW + 2.0)
    return store


def test_retention_drop_failure_rolls_back_rows_and_preserves_triggers(
    retention_store, monkeypatch
):
    """Injected failure right after the FIRST DROP TRIGGER during bounded
    retention: the whole transaction rolls back atomically — original rows
    exist, all required triggers exist, and a forbidden mutation is STILL
    rejected at the database level."""
    store = retention_store
    case_id, version = "case-retention", 1

    before_done = {
        "ACCUSED": 1,
        "REVEALED": 1,
        "PLAYING": 1,
    }
    assert _retention_counts(store, case_id) == before_done
    _assert_triggers_present(store)

    state = _inject_failure_after_first_drop(monkeypatch)
    from sqlalchemy.exc import SQLAlchemyError as _Compat  # noqa: F401 (clarity)

    with pytest.raises(_InjectedTriggerFailure):
        # retained == 3, max_retained == 2 -> excess = 3 - 2 + 1 = 2 prune slots;
        # the prune DROPs accusation triggers first -> injected failure fires.
        store.create_playthrough_if_published(
            playthrough_id="pt-new-1",
            case_id=case_id,
            case_version=version,
            token_verifier=_tokens(),
            state="PLAYING",
            created_at=_NOW + 3.0,
            expires_at=_NOW + 3.0 + 86400,
            max_active=100,
            max_retained=2,
        )
    assert state["drops_seen"] == 1, "injection must actually have fired"

    # 1) original rows still exist (nothing deleted, nothing inserted).
    assert _retention_counts(store, case_id) == before_done
    with store._read_session() as session:
        from sqlalchemy import text

        assert (
            session.execute(
                text("SELECT count(*) FROM player_knowledge WHERE playthrough_id = :p"),
                {"p": "pt-done-1"},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM accusations WHERE playthrough_id = :p"),
                {"p": "pt-done-1"},
            ).scalar_one()
            == 1
        )
    # 2) every required immutability trigger exists.
    _assert_triggers_present(store)
    # 3) a forbidden mutation is STILL rejected at the database level.
    _assert_forbidden_mutations_rejected(store, accusation_pt_id="pt-done-1")


def test_retention_interruption_before_recreate_next_operation_recovers(
    store, monkeypatch
):
    """Process/interruption-recovery: an interruption AFTER the drop+delete but
    BEFORE the re-create (injected at the first CREATE TRIGGER) rolls back to a
    consistent state — and the NEXT governed operation (a real bounded create)
    succeeds, restoring/keeping every immutability guard."""
    # Seed: 3 completed rows + 1 active. With max_retained=3, a create needs to
    # prune 4 - 3 + 1 = 2 rows -> the 2 OLDEST completed rows are pruned and
    # pt-done-3 (newest completed, ACCUSED) survives with its accusation.
    store.create_session(
        session_id="quota-21b-recovery",
        token_verifier=_tokens(),
        quota_window_end=_NOW + 3600,
        created_at=_NOW,
    )
    _seed_published_case(
        store, case_id="case-recovery", session_id="quota-21b-recovery", now=_NOW
    )
    _seed_playthrough(store, playthrough_id="pt-done-1", case_id="case-recovery", version=1, state="ACCUSED", now=_NOW)
    _seed_playthrough(store, playthrough_id="pt-done-2", case_id="case-recovery", version=1, state="REVEALED", now=_NOW + 1.0)
    _seed_playthrough(store, playthrough_id="pt-done-3", case_id="case-recovery", version=1, state="ACCUSED", now=_NOW + 2.0)
    _seed_playthrough(store, playthrough_id="pt-active-1", case_id="case-recovery", version=1, state="PLAYING", now=_NOW + 3.0)

    case_id, version = "case-recovery", 1
    _assert_triggers_present(store)

    state = _inject_failure_before_recreate(monkeypatch)
    with pytest.raises(_InjectedTriggerFailure):
        store.create_playthrough_if_published(
            playthrough_id="pt-new-1",
            case_id=case_id,
            case_version=version,
            token_verifier=_tokens(),
            state="PLAYING",
            created_at=_NOW + 4.0,
            expires_at=_NOW + 4.0 + 86400,
            max_active=100,
            max_retained=3,
        )
    assert state["creates_seen"] == 1

    # Rollback restored the original rows AND every guard (atomic unit):
    # nothing was deleted and no partial row was inserted.
    assert _retention_counts(store, case_id) == {
        "ACCUSED": 2,
        "REVEALED": 1,
        "PLAYING": 1,
    }
    assert store.get_playthrough_by_id("pt-new-1") is None
    _assert_triggers_present(store)

    # The next governed operation re-establishes/finishes consistent state:
    # a normal bounded create at the retained ceiling prunes the OLDEST
    # completed rows, re-creates the guards, verifies them and commits.
    row = store.create_playthrough_if_published(
        playthrough_id="pt-new-2",
        case_id=case_id,
        case_version=version,
        token_verifier=_tokens(),
        state="PLAYING",
        created_at=_NOW + 4.0,
        expires_at=_NOW + 4.0 + 86400,
        max_active=100,
        max_retained=3,
    )
    assert row.playthrough_id == "pt-new-2"
    # Exactly the 2 oldest completed rows were pruned; the newest ACCUSED
    # (pt-done-3) survives with its accusation row.
    counts = _retention_counts(store, case_id)
    assert counts == {"ACCUSED": 1, "PLAYING": 2}, counts
    assert store.get_playthrough_by_id("pt-done-3") is not None
    assert store.get_playthrough_by_id("pt-done-1") is None
    assert store.get_playthrough_by_id("pt-done-2") is None
    _assert_triggers_present(store)
    _assert_forbidden_mutations_rejected(store, accusation_pt_id="pt-done-3")


# --------------------------------------------------------------------------- #
# DELETION PATH — failure immediately after the first DROP TRIGGER
# --------------------------------------------------------------------------- #


@pytest.fixture
def deletion_store(store):
    """Two full published cases: A = deletion target, B = must stay untouched."""
    store.create_session(
        session_id="quota-21b-del",
        token_verifier=_tokens(),
        quota_window_end=_NOW + 3600,
        created_at=_NOW,
    )
    _seed_published_case(
        store, case_id="case-a-target", session_id="quota-21b-del", now=_NOW
    )
    _seed_playthrough(
        store, playthrough_id="pt-a-1", case_id="case-a-target", version=1,
        state="PLAYING", now=_NOW,
    )
    _seed_published_case(
        store, case_id="case-b-unrelated", session_id="quota-21b-del", now=_NOW
    )
    _seed_playthrough(
        store, playthrough_id="pt-b-1", case_id="case-b-unrelated", version=1,
        state="ACCUSED", now=_NOW,
    )
    return store


def test_delete_case_cascade_drop_failure_rolls_back_atomically(
    deletion_store, monkeypatch
):
    """Injected failure right after the first DROP TRIGGER inside
    ``delete_case_cascade``: the deletion transaction rolls back atomically —
    the target case and ALL of its rows still exist, the unrelated case is
    untouched, every immutability trigger exists, and forbidden mutations are
    STILL rejected at the database level."""
    store = deletion_store
    before_a = store.count_case_references("case-a-target")
    assert before_a["cases"] == 1 and before_a["published_versions"] == 1
    before_b = store.count_case_references("case-b-unrelated")
    assert before_b["playthroughs"] == 1 and before_b["accusations"] == 1

    state = _inject_failure_after_first_drop(monkeypatch)
    with pytest.raises(_InjectedTriggerFailure):
        store.delete_case_cascade("case-a-target")
    assert state["drops_seen"] == 1, "injection must actually have fired"

    # 1) original rows still exist — the target was NOT deleted at all.
    after_a = store.count_case_references("case-a-target")
    assert after_a == before_a
    assert store.get_case("case-a-target") is not None
    assert store.get_published("case-a-target", 1) is not None
    assert store.get_playthrough_by_id("pt-a-1") is not None
    # 2) unrelated case entirely untouched.
    assert store.count_case_references("case-b-unrelated") == before_b
    # 3) every required immutability trigger exists.
    _assert_triggers_present(store)
    # 4) a forbidden mutation is STILL rejected at the database level (both
    #    trigger sets: published_versions AND accusations).
    _assert_forbidden_mutations_rejected(
        store, accusation_pt_id="pt-b-1", kid="case-b-unrelated"
    )


def test_delete_case_tool_rerun_after_injection_completes(
    database_url, deletion_store, monkeypatch
):
    """Operator/tool recovery: after a mid-path interruption inside the
    audited deletion unit, a re-run of the SAME command succeeds and the
    remaining case keeps every immutability guarantee — the store/CLI never
    observes a missing guard."""
    store = deletion_store
    before_b = store.count_case_references("case-b-unrelated")

    state = _inject_failure_after_first_drop(monkeypatch)
    with pytest.raises(_InjectedTriggerFailure):
        store.delete_case_cascade("case-a-target")
    assert state["drops_seen"] == 1
    _assert_triggers_present(store)

    # The operator re-runs the audited deletion: it must now COMPLETE.
    deleted = store.delete_case_cascade("case-a-target")
    assert deleted["cases"] == 1
    assert store.get_case("case-a-target") is None
    assert store.count_case_references("case-a-target") == {
        table: 0 for table in store.count_case_references("case-a-target")
    }
    assert store.count_case_references("case-b-unrelated") == before_b
    _assert_triggers_present(store)
    _assert_forbidden_mutations_rejected(
        store, accusation_pt_id="pt-b-1", kid="case-b-unrelated"
    )