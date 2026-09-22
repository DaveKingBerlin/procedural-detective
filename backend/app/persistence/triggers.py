"""Canonical SQLite immutability-trigger DDL (Phase 5 INVARIANT 2 / Phase 7 H/N4).

This module is the SINGLE source of truth for the two immutability trigger sets
that protect published payloads and accusations at the DATABASE level:

- ``published_versions`` — BEFORE UPDATE + BEFORE DELETE (migration 0002);
- ``accusations``        — BEFORE UPDATE + BEFORE DELETE (migration 0004).

Migrations 0002/0004 register these exact triggers. The audited operator
deletion command (``tools/delete_case.py``) and
``Store.delete_case_cascade`` REUSE these definitions so removing a case can
never leave the guards missing (Phase 21 F-07): the trigger DDL is dropped and
re-created IDENTICALLY inside the deletion transaction, restoring the
immutability guarantee for every remaining row before the transaction commits.

Identifier hygiene: every name/message here is an internal constant (never
operator/player input), so interpolating them into DDL is injection-free. The
message strings contain no single quotes.
"""

from __future__ import annotations

from typing import Any

# (trigger_name, event, table, raise_message) — the canonical 4.
IMMUTABILITY_TRIGGERS: tuple[tuple[str, str, str, str], ...] = (
    (
        "published_versions_no_update",
        "UPDATE",
        "published_versions",
        "published_versions is immutable",
    ),
    (
        "published_versions_no_delete",
        "DELETE",
        "published_versions",
        "published_versions is immutable",
    ),
    (
        "accusations_no_update",
        "UPDATE",
        "accusations",
        "accusations is immutable",
    ),
    (
        "accusations_no_delete",
        "DELETE",
        "accusations",
        "accusations is immutable",
    ),
)

IMMUTABILITY_TRIGGER_NAMES: frozenset[str] = frozenset(
    name for name, _event, _table, _message in IMMUTABILITY_TRIGGERS
)


def ensure_immutability_triggers(session: Any) -> int:
    """Drop-and-recreate the immutability triggers IDENTICALLY (idempotent).

    Runs inside the caller's transaction (SQLite DDL is transactional): every
    trigger is dropped ``IF EXISTS`` and re-created with the exact DDL of
    migrations 0002/0004. Returns the number of triggers created (always 4 on
    success). Aborts (raising) on ANY SQL error — the caller's transaction
    rolls back as a unit, so a failed re-creation can never leave the DB with
    a missing guard.
    """
    for name, _event, _table, _message in IMMUTABILITY_TRIGGERS:
        session.execute(
            _sql(f"DROP TRIGGER IF EXISTS {name}")
        )
    for name, event, table, message in IMMUTABILITY_TRIGGERS:
        session.execute(
            _sql(
                f"CREATE TRIGGER {name} BEFORE {event} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
            )
        )
    return len(IMMUTABILITY_TRIGGERS)


def registered_immutability_trigger_names(session: Any) -> set[str]:
    """Names of the immutability triggers currently registered in sqlite_master.

    Read-only introspection used to VALIDATE that a deletion/cascade completed
    with every guard in place (F-07 step "recreates/validates" and the
    post-cleanup immutability check).
    """
    rows = session.execute(_sql("SELECT name FROM sqlite_master WHERE type = 'trigger'"))
    return {str(row[0]) for row in rows}


def missing_immutability_trigger_names(session: Any) -> frozenset[str]:
    """The subset of the canonical 4 that is NOT currently registered."""
    registered = registered_immutability_trigger_names(session)
    return frozenset(name for name in IMMUTABILITY_TRIGGER_NAMES if name not in registered)


def _sql(statement: str) -> Any:
    """Build an SQLAlchemy Core text statement (imported lazily for tooling)."""
    from sqlalchemy import text

    return text(statement)