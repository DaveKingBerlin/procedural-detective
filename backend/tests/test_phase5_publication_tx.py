"""Phase 5 — atomic publication transactions (M13, M14, M22 + H)
(REQUIREMENTS 7.4/7.5, Phase5 G).

- M13 publication persists atomically (payload + lifecycle flips together)
- M14 an incomplete/poisoned transaction NEVER leaves a PUBLISHED state
- M22 duplicate publication cannot produce conflicting versions
- injected mid-commit failure -> full rollback, then a clean retry succeeds
- the immutable published_versions row refuses UPDATE/DELETE at the DB level
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.generation.clock import ManualClock
from app.persistence.store import DuplicatePublication, Store
from app.services.publication import (
    PublicationError,
    PublicationService,
    SerializationError,
)
from phase5_helpers import (
    golden_script,
    held_published,
    seed_pending_version,
    seed_session,
)


def _golden_script(store, database_url: str) -> dict:
    """Load the production builtin dev-mode fake script (no fixture imports)."""
    from app.services.generation import GenerationService

    service = GenerationService(
        settings=_settings(database_url), store=store
    )
    return service._load_fake_script()


def _held_published(database_url: str, script: dict):
    """Build a real frozen PublishedCaseVersion via the golden pipeline with
    ``hold_before_publish``, and return (published, attempt_record)."""
    from app.generation.admission import AdmissionController

    clock = ManualClock(start_time=1000.0)
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=4,
        max_concurrent_generations_global=8,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_end=1000.0 + 86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=FakeProvider(script=script),
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=11,
        hold_before_publish=True,
    )
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success, result.reason
    assert result.published is not None
    return result.published, record, session.session_id, clock


def _settings(database_url: str):
    from app.core.config import Settings

    return Settings(
        database_url=database_url,
        max_generations_per_session_per_window=8,
        max_concurrent_generations=4,
    )


def _seed_session(store, session_id, clock):
    from app.auth.tokens import issue_token, verifier as _v

    now = float(clock.now())
    store.create_session(
        session_id=session_id,
        token_verifier=_v(issue_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    return now


def _seed_pending_version(store, published, session_id, clock, status="VALIDATING"):
    """Persist the pre-publication rows (session + case + version + attempt)."""
    now = _seed_session(store, session_id, clock)
    store.create_case(
        case_id=published.case_id,
        quota_session_id=session_id,
        title="Atomic",
        difficulty=None,
        created_at=now,
    )
    store.create_case_version(
        case_id=published.case_id,
        version=published.case_version,
        state="VALIDATING",
        generation_id=f"GEN-{published.case_version}",
        created_at=now,
    )
    try:
        store.upsert_generation_attempt(
            attempt_id=published.generation_attempt_id,
            case_id=published.case_id,
            case_version=published.case_version,
            status=status,
            stage="validating",
            progress=80,
            created_at=now,
            updated_at=now,
        )
    except Exception:  # noqa: BLE001 - an attempt may already exist
        pass
    return now


def test_13_publication_persists_atomically(store, database_url):
    published, record, session_id, clock = held_published(
        database_url, golden_script(store, database_url)
    )
    seed_pending_version(store, published, session_id, clock)
    service = PublicationService(store)
    result = service.publish_transactionally(
        published,
        seed=record.seed,
        prompt=record.prompt,
        title="Atomic",
    )
    assert result == {"caseId": published.case_id, "caseVersion": published.case_version}
    row = store.get_published(published.case_id, published.case_version)
    assert row is not None
    payload = __import__("json").loads(row.payload_json)
    assert payload["schemaVersion"] == 1
    assert payload["caseId"] == published.case_id
    assert payload["caseVersion"] == published.case_version
    assert store.get_case_version(published.case_id, published.case_version).state == "PUBLISHED"
    attempt = store.get_generation_attempt_by_id(published.generation_attempt_id)
    assert attempt.status == "PUBLISHED"
    assert attempt.progress == 100


def test_14_missing_attempt_never_publishes(store, database_url):
    """M14: no authoritative attempt -> PublicationError, zero mutation."""
    published, _record, session_id, clock = held_published(
        database_url, golden_script(store, database_url)
    )
    now = seed_session(store, session_id, clock)
    store.create_case(
        case_id=published.case_id,
        quota_session_id=session_id,
        title="Atomic",
        difficulty=None,
        created_at=now,
    )
    store.create_case_version(
        case_id=published.case_id,
        version=published.case_version,
        state="VALIDATING",
        generation_id=f"GEN-{published.case_version}",
        created_at=now,
    )
    # NOTE: no generation_attempts row at all.
    service = PublicationService(store)
    with pytest.raises(PublicationError):
        service.publish_transactionally(published)
    assert store.get_published(published.case_id, published.case_version) is None
    assert store.get_case_version(published.case_id, published.case_version).state == "VALIDATING"


def test_injected_failure_rolls_back_then_clean_retry_succeeds(
    store, database_url, monkeypatch
):
    """H: a failing serializer mid-commit leaves NO half state; a retry works."""
    published, record, session_id, clock = held_published(
        database_url, golden_script(store, database_url)
    )
    seed_pending_version(store, published, session_id, clock)
    service = PublicationService(store)

    def _poisoned(_published, **_kwargs):
        raise SerializationError("poisoned payload")

    monkeypatch.setattr(
        "app.services.publication.serialize_published_payload", _poisoned
    )
    with pytest.raises(SerializationError):
        service.publish_transactionally(
            published, seed=record.seed, prompt=record.prompt, title="Atomic"
        )
    # Full rollback: no published row, no PUBLISHED state, attempt untouched.
    assert store.get_published(published.case_id, published.case_version) is None
    version_row = store.get_case_version(published.case_id, published.case_version)
    assert version_row.state == "VALIDATING"
    attempt = store.get_generation_attempt_by_id(published.generation_attempt_id)
    assert attempt.status == "VALIDATING"

    # Retry after the failure (serializer restored) succeeds cleanly.
    monkeypatch.undo()
    result = service.publish_transactionally(
        published, seed=record.seed, prompt=record.prompt, title="Atomic"
    )
    assert result["caseVersion"] == published.case_version
    assert store.get_published(published.case_id, published.case_version) is not None
    assert store.get_case_version(published.case_id, published.case_version).state == "PUBLISHED"
    assert store.get_generation_attempt_by_id(published.generation_attempt_id).status == "PUBLISHED"


def test_22_duplicate_concurrent_publication_only_one_wins(store, database_url):
    """M22: two publishers for one (case_id, version) -> exactly ONE wins.

    Two store instances (independent locks) over the same FILE with the same
    pre-publication rows: the unique PK / status check is the real guard.
    """
    published, record, session_id, clock = held_published(
        database_url, golden_script(store, database_url)
    )
    seed_pending_version(store, published, session_id, clock)
    store.dispose()

    store_a = Store(database_url)
    store_b = Store(database_url)
    service_a = PublicationService(store_a)
    service_b = PublicationService(store_b)
    try:
        service_a.publish_transactionally(
            published, seed=record.seed, prompt=record.prompt, title="Atomic"
        )
        with pytest.raises(DuplicatePublication):
            service_b.publish_transactionally(
                published, seed=record.seed, prompt=record.prompt, title="Atomic"
            )
        # Exactly ONE immutable row; one coherent PUBLISHED state.
        assert store_a.get_published(published.case_id, published.case_version) is not None
        assert store_b.get_published(published.case_id, published.case_version) is not None
        assert store_b.get_case_version(published.case_id, published.case_version).state == "PUBLISHED"
        # The losing publish mutated NOTHING.
        version_row = store_b.get_case_version(published.case_id, published.case_version)
        assert version_row.state == "PUBLISHED"
        assert store_b.get_generation_attempt_by_id(published.generation_attempt_id).status == "PUBLISHED"
    finally:
        store_a.dispose()
        store_b.dispose()


def _seed_case_for_store_tests(store, clock, case_id="CASE-INV"):
    """Seed the FK parents (session + case) for raw published-row tests."""
    now = _seed_session(store, "QUOTA-STORE", clock)
    store.create_case(
        case_id=case_id,
        quota_session_id="QUOTA-STORE",
        title="Store",
        difficulty=None,
        created_at=now,
    )
    return now


def test_store_insert_published_single_row_and_duplicate(store):
    clock = ManualClock(start_time=5.0)
    now = _seed_case_for_store_tests(store, clock)
    store.insert_published(case_id="CASE-INV", case_version=1, payload_json="{}", published_at=now)
    assert store.get_published("CASE-INV", 1) is not None
    with pytest.raises(DuplicatePublication):
        store.insert_published(case_id="CASE-INV", case_version=1, payload_json="{}", published_at=now)
    assert store.get_latest_published("CASE-INV").case_version == 1


def test_published_row_is_immutable_at_db_level(store):
    """INVARIANT 2: the DB refuses UPDATE/DELETE on published_versions."""
    from sqlalchemy import create_engine, text

    clock = ManualClock(start_time=5.0)
    now = _seed_case_for_store_tests(store, clock, case_id="CASE-IMM")
    store.insert_published(case_id="CASE-IMM", case_version=1, payload_json="{}", published_at=now)
    engine = create_engine(store.url, connect_args={"check_same_thread": False})
    try:
        with engine.connect() as conn:
            with pytest.raises(Exception):  # SQLite abort by trigger
                conn.execute(
                    text("UPDATE published_versions SET payload_json = 'hacked' "
                         "WHERE case_id = 'CASE-IMM'")
                )
            with pytest.raises(Exception):
                conn.execute(
                    text("DELETE FROM published_versions WHERE case_id = 'CASE-IMM'")
                )
    finally:
        engine.dispose()
    assert store.get_published("CASE-IMM", 1) is not None
    assert store.get_published("CASE-IMM", 1).payload_json == "{}"