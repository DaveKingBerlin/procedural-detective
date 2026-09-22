"""Audited operator deletion command (Phase 21 F-07).

Replaces the raw-SQL single-case procedure of ``docs/PRIVACY.md`` §3.2 (whose
"alembic upgrade head re-runs recreate missing triggers" claim is NOT reliable
once the migration head is already recorded) with a single audited
maintenance command:

    python -m tools.delete_case <case_id> [--yes]

Behavior (Phase21-PHC.md §2 F-07):

1. **Validates** the target ``cases`` row exists; a missing case ABORTS with a
   sanitized message (even with ``--yes``) and changes nothing.
2. **Deletes atomically** inside ONE transaction, in foreign-key order, the
   EXACT table set of the documented procedure (player_knowledge, accusations,
   playthroughs, creator_credentials, generation_attempts,
   published_versions, case_versions, cases) via ``Store.delete_case_cascade``
   (the store exposes no other delete path — this is the single audited unit).
3. **Re-creates/validates the immutability triggers** inside the same
   transaction using the SAME DDL as migrations 0002/0004
   (``app.persistence.triggers``): the ``published_versions`` /
   ``accusations`` BEFORE UPDATE/DELETE guards are dropped, the deletion runs,
   and the guards are re-created IDENTICALLY and verified present (4/4)
   before commit. SQLite DDL is transactional, so ANY failure rolls the whole
   operation back to the original guard set.
4. **Aborts on ANY mismatch** — missing case, foreign-key remnants, missing
   triggers — with a sanitized message and a non-zero exit code.
5. **Verifies immutability protections hold AFTER deletion**: the command
   re-checks that every trigger is registered post-cleanup; the hermetic test
   (``backend/tests/test_phase21_delete_case.py``) additionally asserts that a
   raw UPDATE/DELETE on a REMAINING published case / accusation is still
   rejected at the database level.

Safety: the command never runs anything on the default/repository DB unless
``DATABASE_URL`` (or the canonical Settings chain) points at that database —
it uses the exact same single configuration source as the app
(``app.core.config.Settings``). Tests run the command only against a
disposable scratch SQLite file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.persistence.store import (  # noqa: E402
    CaseNotFoundError,
    Store,
    StoreError,
)
from app.persistence.triggers import IMMUTABILITY_TRIGGER_NAMES  # noqa: E402


def _sanitize(text: str) -> str:
    """Operator-facing text: strip control characters/NUL; never tracebacks."""
    return "".join(ch for ch in text if ch == "\t" or ch == "\n" or ord(ch) >= 32)


def run_delete_case(case_id: str, database_url: str, *, yes: bool) -> int:
    """Validate + atomically delete one case. Returns the process exit code.

    Exit codes: 0 = deleted + verified; 1 = abort (missing case / any
    mismatch); 2 = refused (validation passed but ``--yes`` was not given).
    """
    if not case_id or not isinstance(case_id, str):
        print("error: case_id must be a non-empty string", file=sys.stderr)
        return 1
    store = Store(database_url)
    try:
        preview = store.count_case_references(case_id)
        if preview["cases"] == 0:
            print(
                "error: case %r does not exist; nothing was changed" % _sanitize(case_id),
                file=sys.stderr,
            )
            return 1
        if not yes:
            rows = ", ".join(f"{table}={n}" for table, n in sorted(preview.items()))
            print(
                "case %r would delete: %s; refusing (pass --yes to confirm)"
                % (_sanitize(case_id), rows)
            )
            return 2
        deleted = store.delete_case_cascade(case_id)
        # Post-cleanup trigger verification (F-07 step 5): every immutability
        # guard must be registered after the deletion committed.
        missing = sorted(store.missing_immutability_triggers())
        if missing:
            print(
                "error: deletion committed but immutability triggers are missing: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            return 1
        registered = len(IMMUTABILITY_TRIGGER_NAMES) - len(missing)
        print(
            "deleted case %r atomically: %s"
            % (
                _sanitize(case_id),
                ", ".join(f"{table}={n}" for table, n in sorted(deleted.items())),
            )
        )
        print("immutability triggers verified: %d/%d" % (registered, len(IMMUTABILITY_TRIGGER_NAMES)))
        return 0
    except CaseNotFoundError as exc:
        print("error: %s; nothing was changed" % _sanitize(str(exc)), file=sys.stderr)
        return 1
    except StoreError as exc:
        print(
            "error: %s; the whole deletion was rolled back" % _sanitize(str(exc)),
            file=sys.stderr,
        )
        return 1
    finally:
        store.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.delete_case",
        description=(
            "Audited operator deletion of ONE generated case (docs/PRIVACY.md "
            "§3.2 replacement). Validates, deletes atomically, re-creates and "
            "verifies the publication/accusation immutability triggers."
        ),
    )
    parser.add_argument("case_id", help="the case_id to delete")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm the deletion (without it the command only validates/previews)",
    )
    args = parser.parse_args(argv)

    from app.core.config import Settings  # noqa: PLC0415

    settings = Settings()  # canonical single config source (env/.env/DATABASE_URL)
    return run_delete_case(args.case_id, settings.database_url, yes=args.yes)


if __name__ == "__main__":
    raise SystemExit(main())