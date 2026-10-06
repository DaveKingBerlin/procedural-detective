"""Phase31A — ADV-31A-H01 regression: InProcessDriver Settings binding.

``tools.frontier_benchmark.InProcessDriver`` builds ``Settings(**kwargs)`` from
the operator CLI flags. ``Settings.generation_deadline_seconds`` is bound
through the CANONICAL alias ``CASE_GENERATION_DEADLINE_SECONDS``
(``app/core/config.py``, DEF-080 single-name rule), so the constructor keyword
MUST use the alias: a non-canonical field-name kwarg is silently ignored by the
pydantic-settings constructor and the field stays at its 60s default (which is
why the Phase31A live probes were capped at 60s regardless of
``--generation-deadline-seconds``).

These tests pin the harness-side fix:

  - ``generation_deadline_seconds=300`` actually lands as 300 on the driver's
    resolved ``Settings`` (the CLI flag name is unchanged);
  - the flag-absent path resolves to the documented 60s default;
  - ``frontier_timeout_seconds`` (a field WITHOUT an alias) still binds when
    supplied.

Every test is hermetic: the driver is constructed with a throwaway local
SQLite database (the same ``_upgrade_database`` + ``Store`` path the real
benchmark uses) and NO provider is ever called — ``run_case`` is never
invoked. The relevant process-environment names are cleared so the host shell
cannot influence the assertions (construction kwargs still win over env and
the repo ``.env`` per ``Settings.settings_customise_sources``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import frontier_benchmark as fb  # noqa: E402


def _clear_timeout_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the deadline/timeout env vars so only construction args decide."""
    monkeypatch.delenv("CASE_GENERATION_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("GENERATION_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("FRONTIER_TIMEOUT_SECONDS", raising=False)


def _sqlite_url(tmp_path: Path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def test_inprocess_driver_generation_deadline_flag_binds_canonical_alias(
    tmp_path, monkeypatch
):
    """ADV-31A-H01 — ``generation_deadline_seconds=300`` (the value behind
    ``--generation-deadline-seconds 300``) reaches ``driver.settings`` as 300,
    not the silent 60s default the non-canonical kwarg produced pre-fix."""
    _clear_timeout_env(monkeypatch)
    driver = fb.InProcessDriver(
        concurrency=1,
        generation_deadline_seconds=300,
        database_url=_sqlite_url(tmp_path, "deadline-300.db"),
    )
    try:
        assert driver.settings.generation_deadline_seconds == 300
    finally:
        driver.close()


def test_inprocess_driver_default_deadline_stays_60(tmp_path, monkeypatch):
    """The flag-absent path still resolves to the documented 60s default (the
    fix must not silently change the default behaviour)."""
    _clear_timeout_env(monkeypatch)
    driver = fb.InProcessDriver(
        concurrency=1,
        database_url=_sqlite_url(tmp_path, "deadline-default.db"),
    )
    try:
        assert driver.settings.generation_deadline_seconds == 60
    finally:
        driver.close()


def test_inprocess_driver_frontier_timeout_still_binds_when_supplied(
    tmp_path, monkeypatch
):
    """``frontier_timeout_seconds`` binds through its (alias-free) field name
    as before, alongside the canonical deadline alias."""
    _clear_timeout_env(monkeypatch)
    driver = fb.InProcessDriver(
        concurrency=1,
        frontier_timeout_seconds=120.0,
        generation_deadline_seconds=300,
        database_url=_sqlite_url(tmp_path, "timeout-120.db"),
    )
    try:
        assert driver.settings.frontier_timeout_seconds == 120.0
        assert driver.settings.generation_deadline_seconds == 300
    finally:
        driver.close()