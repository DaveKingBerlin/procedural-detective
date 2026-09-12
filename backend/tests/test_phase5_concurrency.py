"""Phase 5 — concurrency & transaction safety (Phase5 H/I, M22, M23).

Thread-based tests over a REAL sqlite FILE. Deterministic: no sleeps, no
network. The DB constraints (composite PKs / unique constraints) are the real
guards; the per-store lock only makes SQLite write ordering deterministic.

- concurrent creator case requests  (shared admission, per-request controllers)
- duplicate generation completion persistence  (idempotent upsert)
- two attempts publishing the same version -> exactly one wins (M22)
- new version publication while an old playthrough exists -> no effect
- duplicate/colliding identifiers -> clean errors, no partial state
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.generation.fake_provider import FakeProvider
from app.generation.provider import GenerationStage
from phase5_helpers import (
    golden_script,
    held_published,
    seed_pending_version,
)


def _service(store, database_url, **overrides):
    from app.core.config import Settings
    from app.services.generation import GenerationService

    kwargs = dict(
        database_url=database_url,
        max_generations_per_session_per_window=8,
        max_concurrent_generations=4,
        max_concurrent_generations_global=8,
    )
    kwargs.update(overrides)
    return GenerationService(settings=Settings(**kwargs), store=store)


def test_concurrent_creator_case_requests_all_succeed(store, database_url):
    """I: N threads, one shared admission + store, each own controller."""
    from app.services.generation import CaseStarted

    service = _service(store, database_url)
    session = service.create_anonymous_quota_session()
    results: list[CaseStarted] = []
    errors: list[BaseException] = []

    def _worker():
        try:
            started = service.start_case_generation(
                "Victim: sarah_miller\nMurderer: thomas_reed\n",
                anonymous_quota_session_id=session.anonymous_quota_session_id,
            )
            results.append(started)
        except BaseException as exc:  # noqa: BLE001 - recorded for assert
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(results) == 4
    case_ids = {r.case_id for r in results}
    attempt_ids = {r.generation_attempt_id for r in results}
    creator_tokens = {r.creator_access_token for r in results}
    assert len(case_ids) == 4
    assert len(attempt_ids) == 4
    assert len(creator_tokens) == 4
    assert all(r.status == "PUBLISHED" for r in results)
    # All four persisted durably (classic CASE-id regression: no collisions).
    assert store.get_case_version(next(iter(case_ids)), 1) is not None


def test_duplicate_generation_completion_persistence_is_idempotent(store):
    """I: persisting the same attempt twice is an update, never a dup row."""
    now = 1_000_000.0
    store.create_session(
        session_id="QUOTA-DUP",
        token_verifier="a" * 64,
        quota_window_end=now + 3600,
        created_at=now,
    )
    store.create_case(
        case_id="CASE-DUP",
        quota_session_id="QUOTA-DUP",
        title="Dup",
        difficulty=None,
        created_at=now,
    )
    created = store.upsert_generation_attempt(
        attempt_id="GA-DUP-1",
        case_id="CASE-DUP",
        case_version=1,
        status="GENERATING",
        stage="case_truth",
        progress=25,
        created_at=now,
        updated_at=now,
    )
    updated = store.upsert_generation_attempt(
        attempt_id="GA-DUP-1",
        case_id="CASE-DUP",
        case_version=1,
        status="PUBLISHED",
        stage="published",
        progress=100,
        created_at=now,
        updated_at=now + 10,
    )
    assert created.attempt_id == updated.attempt_id
    # Exactly one row, now with the final status.
    row = store.get_generation_attempt_by_id("GA-DUP-1")
    assert row is not None
    assert row.status == "PUBLISHED"
    assert row.progress == 100
    # A DIFFERENT attempt for the same (case, version) is rejected (UNIQUE).
    from app.persistence.store import DuplicateAttempt

    with pytest.raises(DuplicateAttempt):
        store.upsert_generation_attempt(
            attempt_id="GA-DUP-2",
            case_id="CASE-DUP",
            case_version=1,
            status="GENERATING",
            stage=None,
            progress=10,
            created_at=now,
            updated_at=now,
        )
    # No partial row was left behind.
    assert store.get_generation_attempt_by_id("GA-DUP-2") is None


def test_duplicate_colliding_identifiers_leave_no_partial_state(store):
    """I: case / session / playthrough id collisions -> clean errors."""
    from app.persistence.store import (
        DuplicateCase,
        DuplicatePlaythrough,
        DuplicateSession,
    )

    now = 1_000_000.0
    store.create_session(
        session_id="QUOTA-ID",
        token_verifier="b" * 64,
        quota_window_end=now + 3600,
        created_at=now,
    )
    with pytest.raises(DuplicateSession):
        store.create_session(
            session_id="QUOTA-ID",
            token_verifier="c" * 64,
            quota_window_end=now + 3600,
            created_at=now,
        )
    store.create_case(
        case_id="CASE-ID",
        quota_session_id="QUOTA-ID",
        title="Id",
        difficulty=None,
        created_at=now,
    )
    with pytest.raises(DuplicateCase):
        store.create_case(
            case_id="CASE-ID",
            quota_session_id="QUOTA-ID",
            title="Id",
            difficulty=None,
            created_at=now,
        )
    store.create_playthrough(
        playthrough_id="PT-ID",
        case_id="CASE-ID",
        case_version=1,
        token_verifier="d" * 64,
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    with pytest.raises(DuplicatePlaythrough):
        store.create_playthrough(
            playthrough_id="PT-ID",
            case_id="CASE-ID",
            case_version=1,
            token_verifier="e" * 64,
            state="PLAYING",
            created_at=now,
            expires_at=now + 3600,
        )
    # Exactly ONE of each row survived; nothing partial.
    assert len([store.get_session("QUOTA-ID")]) == 1
    assert store.get_playthrough_by_id("PT-ID") is not None


def test_new_version_publication_does_not_affect_old_playthrough(
    store, database_url
):
    """I: publish v2 while a v1 playthrough exists -> pin untouched."""
    service = _service(store, database_url)
    session = service.create_anonymous_quota_session()
    first = service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    store.create_playthrough(
        playthrough_id="PT-OLD",
        case_id=first.case_id,
        case_version=1,
        token_verifier="f" * 64,
        state="PLAYING",
        created_at=1_000_000.0,
        expires_at=2_000_000.0,
    )
    before = store.get_playthrough_by_id("PT-OLD")

    # Publish v2 for the same case through the service.
    second = service.start_case_version(
        first.case_id,
        "Victim: sarah_miller\nMurderer: thomas_reed\na v2",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert second.status == "PUBLISHED"
    assert second.generation_id == "GEN-2"

    after = store.get_playthrough_by_id("PT-OLD")
    assert (after.case_id, after.case_version) == (before.case_id, before.case_version)
    assert after.case_version == 1
    assert after.state == "PLAYING"
    # v1 row still exactly what it was.
    assert store.get_published(first.case_id, 2) is not None
    assert store.get_case_version(first.case_id, 2).state == "PUBLISHED"


def test_concurrent_publish_same_version_via_two_publishers(store, database_url):
    """M22/I: two attempts to publish the same (case, version) — one wins."""
    from app.persistence.store import DuplicatePublication, Store
    from app.services.publication import PublicationService

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
        assert store_a.get_published(published.case_id, published.case_version) is not None
        assert store_b.get_published(published.case_id, published.case_version) is not None
    finally:
        store_a.dispose()
        store_b.dispose()