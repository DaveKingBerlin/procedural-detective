"""Order-independent logging isolation regression.

Defect: 3 caplog assertions in ``test_ollama_driver.py``
(``test_18a_...`` both parametrizations and ``test_18c_...``) failed
order-dependently whenever an EARLIER test in the same pytest process had
imported ``app.main`` and then run an alembic migration in-process (any
conftest ``upgrade_db`` / ``run_migrations`` path).

Root cause chain (Python 3.12):

1. ``app.main`` creates the ``"procedural-detective"`` logger at import time
   (module-level ``logging.getLogger(SERVICE_NAME)``).
2. Alembic's ``backend/alembic/env.py`` calls
   ``logging.config.fileConfig(config.config_file_name)``. ``fileConfig``
   defaults to ``disable_existing_loggers=True``, which sets
   ``logger.disabled = True`` on every pre-existing logger that alembic's
   config does not name.
3. Python 3.12's ``Logger.isEnabledFor()`` short-circuits on ``Logger.disabled``
   — it returns ``False`` for EVERY level, so
   ``caplog.at_level(INFO, logger="procedural-detective")`` could no longer
   rescue the events: ``emit_event``'s records were silently dropped before
   reaching any handler and the assert ``len(projection_events) == 1``
   observed ``0``.

The two tests below reproduce that cross-test shape exactly — test A performs
the in-process migration (any earlier test), test B verifies the app logger
survived it and that structured events are still capturable through caplog.
Both fail on the old ``env.py``; both pass with
``disable_existing_loggers=False``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Mirror the production/test sequence: importing app.main CREATES the
# "procedural-detective" logger (main.py module-level getLogger) before any
# migration runs. create_app() is lazy (no DB file is created at import).
import app.main  # noqa: E402,F401

from app.core.observability import SERVICE_LOGGER_NAME, emit_event  # noqa: E402
from conftest import upgrade_db  # noqa: E402

SERVICE_EVENT = "evidence.local_projection.used"


def test_a_inprocess_alembic_migration_runs(database_url):
    """Temporary DB migrated the same way any earlier fixture-driven test does."""
    upgrade_db(database_url)


def test_b_migration_does_not_disable_app_logging(caplog):
    """After an in-process migration, the app logger is enabled AND capturable."""
    service_logger = logging.getLogger(SERVICE_LOGGER_NAME)
    # The logger existed before the migration (created by the app.main import
    # above); it must NOT have been disabled by alembic's logging config.
    assert service_logger.disabled is False, (
        "alembic env.py disabled the service logger (disable_existing_loggers "
        "default True); Python 3.12 isEnabledFor then drops EVERY record — "
        "this is the order-dependent test_18a/18c caplog failure"
    )

    # The exact failure mode the ollama tests hit: an INFO pd_event must still
    # reach caplog via caplog.at_level.
    with caplog.at_level(logging.INFO, logger=SERVICE_LOGGER_NAME):
        emit_event(SERVICE_EVENT, generationAttemptId="regression-isolation-g1")

    projection_events = [
        event
        for event in caplog.records
        if getattr(event, "pd_event", None) == SERVICE_EVENT
    ]
    assert len(projection_events) == 1, caplog.records
    assert getattr(projection_events[0], "pd_fields") == {
        "generationAttemptId": "regression-isolation-g1",
    }