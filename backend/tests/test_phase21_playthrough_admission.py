"""Phase 21 F-02 — bounded playthrough admission (durable-table DoS).

Required behavior (Phase21-PHC.md §1 F-02):

- ``MAX_ACTIVE_PLAYTHROUGHS_PER_CASE``: ACTIVE = state in {CREATED, PLAYING}
  (the non-terminal vocabulary; REQUIREMENTS 40.6 / Phase7 A). At the cap the
  next create answers the sanitized ``429 PLAYTHROUGH_LIMIT_EXCEEDED``
  envelope — NO row created, NO token issued, existing playthroughs preserved.
- ``MAX_RETAINED_PLAYTHROUGHS_PER_CASE``: total rows kept for the pinned
  (caseId, caseVersion). Above the ceiling the create first runs bounded
  retention (delete the OLDEST COMPLETED {ACCUSED, REVEALED} rows — with their
  player_knowledge/accusation rows via the governed trigger carve-out, never
  an active row) until the new row fits; when not enough completed rows exist
  the create is REJECTED (the case is at capacity).
- Optional in-memory creation budgets (1-hour rolling windows):
  ``PLAYTHROUGH_CREATE_LIMIT_PER_CREATOR`` (per credential verifier — never
  the raw identity) and ``PLAYTHROUGH_CREATE_LIMIT_PER_IP`` (Phase 20
  ``resolve_client_ip``; a spoofed X-Forwarded-For never bypasses while
  TRUST_PROXY=false).
- Concurrency: the per-case caps are enforced ATOMICALLY inside the store's
  create transaction (serialized by the store write lock + SQLite's exclusive
  write lock, documented single-process deployment), so a thread burst can
  never exceed a cap.

Also covers: unrelated cases unaffected, creator/session auth unchanged, and
the acceptance contract of the rejection (safe envelope, no internals).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.persistence.store import PlaythroughCapacityError
from phase5_helpers import (
    assert_no_hidden_leaks,
    assert_sanitized_error,
    auth,
    create_case,
    create_playthrough,
    create_session,
)
from test_phase7_helpers import truth_bundle, winning_body


def _build_app(database_url: str, **overrides):
    """Migrate + create_app with Phase 20-friendly baseline settings whose
    F-02 caps are overridable per test."""
    from conftest import upgrade_db
    from app.core.config import Settings
    from app.main import create_app

    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        max_concurrent_generations=4,
        max_generations_per_session_per_window=20,
        max_concurrent_generations_global=8,
        max_generations_global_per_window=50,
        **overrides,
    )
    return create_app(settings)


def _dispose(app) -> None:
    app.state.engine.dispose()
    app.state.store.dispose()


def _count_rows(app, case_id: str, case_version: int = 1) -> int:
    with app.state.store._read_session() as session:
        return int(
            session.execute(
                text(
                    "SELECT count(*) FROM playthroughs "
                    "WHERE case_id = :cid AND case_version = :v"
                ),
                {"cid": case_id, "v": case_version},
            ).scalar_one()
        )


def _complete_pt(client, pt_id: str, pt_token: str, truth) -> None:
    """Drive {PLAYING} -> ACCUSED through the frozen Phase 7 API contract."""
    res = client.post(
        f"/api/v1/playthroughs/{pt_id}/accusation",
        json=winning_body(truth),
        headers=auth(pt_token),
    )
    assert res.status_code == 200, res.json()


def _reveal_pt(client, pt_id: str, pt_token: str) -> None:
    """Drive ACCUSED -> REVEALED (idempotent Phase 7 reveal)."""
    res = client.get(
        f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(pt_token)
    )
    assert res.status_code == 200, res.json()


# --------------------------------------------------------------------------- #
# active cap (MAX_ACTIVE_PLAYTHROUGHS_PER_CASE)
# --------------------------------------------------------------------------- #


def test_active_cap_is_enforced_rejection_is_sanitized(database_url):
    """At MAX_ACTIVE the next create is a sanitized 429, no row, no token."""
    app = _build_app(
        database_url,
        MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=2,
        MAX_RETAINED_PLAYTHROUGHS_PER_CASE=20,
        PLAYTHROUGH_CREATE_LIMIT_PER_CREATOR=100,
        PLAYTHROUGH_CREATE_LIMIT_PER_IP=100,
    )
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            for _ in range(2):
                status, body = create_playthrough(client, creator, case_id, 1)
                assert status == 201, body
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 429
            body = res.json()
            assert body["error"]["code"] == "PLAYTHROUGH_LIMIT_EXCEEDED"
            assert_sanitized_error(res.text)
            # Safe envelope: no counters/limiter internals, no token material.
            assert_no_hidden_leaks(body)
            lower = res.text.lower()
            for leaked in ("active", "retained", "verifier", "per_ip", "window", "limiter"):
                assert leaked not in lower, f"429 leaks internal marker {leaked!r}"
            # NO row created and NO token issued after rejection.
            assert _count_rows(app, case_id) == 2
            assert "playthroughAccessToken" not in body
            assert "playthroughId" not in body
    finally:
        _dispose(app)


def test_active_cap_new_tokens_never_persisted(database_url):
    """After the cap each rejected create mints NO durable row or verifier."""
    app = _build_app(
        database_url,
        MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=3,
        MAX_RETAINED_PLAYTHROUGHS_PER_CASE=20,
    )
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            for _ in range(3):
                assert create_playthrough(client, creator, case_id, 1)[0] == 201
            rejected: list[int] = []
            for _ in range(3):
                res = client.post(
                    f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                    headers=auth(creator),
                )
                assert res.status_code == 429
                rejected.append(res.status_code)
            assert len(rejected) == 3
            assert _count_rows(app, case_id) == 3
            # Sequential rejected requests stay rejected (no state drift).
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 429
    finally:
        _dispose(app)


# --------------------------------------------------------------------------- #
# concurrency: a burst can never exceed the cap
# --------------------------------------------------------------------------- #


def test_concurrent_creation_never_exceeds_active_cap(database_url):
    """8 threads on a fresh case with MAX_ACTIVE=4 -> EXACTLY 4 rows."""
    app = _build_app(
        database_url,
        MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=4,
        MAX_RETAINED_PLAYTHROUGHS_PER_CASE=12,
        PLAYTHROUGH_CREATE_LIMIT_PER_CREATOR=100,
        PLAYTHROUGH_CREATE_LIMIT_PER_IP=100,
    )
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
        results: list[int] = []
        errors: list[BaseException] = []

        def _worker() -> None:
            try:
                with TestClient(app) as c:
                    status, _body = create_playthrough(c, creator, case_id, 1)
                    results.append(status)
            except BaseException as exc:  # noqa: BLE001 - recorded for assert
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert sorted(results) == [201, 201, 201, 201, 429, 429, 429, 429]
        assert _count_rows(app, case_id) == 4
    finally:
        _dispose(app)


# --------------------------------------------------------------------------- #
# retention (MAX_RETAINED_PLAYTHROUGHS_PER_CASE)
# --------------------------------------------------------------------------- #


def test_retention_prunes_oldest_completed_preserves_active(database_url):
    """At the retained ceiling the next create deletes the OLDEST COMPLETED
    rows only; active rows are untouched and the count stays <= the ceiling."""
    app = _build_app(
        database_url,
        MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=100,
        MAX_RETAINED_PLAYTHROUGHS_PER_CASE=4,
    )
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            truth = truth_bundle(app, case_id, 1)
            # PT-1: oldest, completed REVEALED.
            _, pt1 = create_playthrough(client, creator, case_id, 1)
            _complete_pt(client, pt1["playthroughId"], pt1["playthroughAccessToken"], truth)
            _reveal_pt(client, pt1["playthroughId"], pt1["playthroughAccessToken"])
            # PT-2: completed ACCUSED.
            _, pt2 = create_playthrough(client, creator, case_id, 1)
            _complete_pt(client, pt2["playthroughId"], pt2["playthroughAccessToken"], truth)
            # PT-3 / PT-4: ACTIVE (PLAYING).
            _, pt3 = create_playthrough(client, creator, case_id, 1)
            _, pt4 = create_playthrough(client, creator, case_id, 1)
            assert _count_rows(app, case_id) == 4  # the retained ceiling
            # Create one more -> prune the OLDEST completed (PT-1) only.
            status, pt5 = create_playthrough(client, creator, case_id, 1)
            assert status == 201, pt5
            store = app.state.store
            assert store.get_playthrough_by_id(pt1["playthroughId"]) is None
            assert store.get_playthrough_by_id(pt2["playthroughId"]) is not None
            assert store.get_playthrough_by_id(pt2["playthroughId"]).state == "ACCUSED"
            assert store.get_playthrough_by_id(pt3["playthroughId"]).state == "PLAYING"
            assert store.get_playthrough_by_id(pt4["playthroughId"]).state == "PLAYING"
            assert store.get_playthrough_by_id(pt5["playthroughId"]).state == "PLAYING"
            assert _count_rows(app, case_id) == 4
            # The knowledge + accusation rows of the pruned playthrough are
            # gone (cascade semantics, verified directly).
            with store._read_session() as session:
                assert (
                    session.execute(
                        text("SELECT count(*) FROM player_knowledge WHERE playthrough_id = :p"),
                        {"p": pt1["playthroughId"]},
                    ).scalar_one()
                    == 0
                )
                assert (
                    session.execute(
                        text("SELECT count(*) FROM accusations WHERE playthrough_id = :p"),
                        {"p": pt1["playthroughId"]},
                    ).scalar_one()
                    == 0
                )
            # Immutability guarantee restored: a raw DELETE of a REMAINING
            # accusation is still aborted at the database level (Phase7 H).
            with pytest.raises(Exception) as excinfo:
                with store.transaction() as session:
                    session.execute(
                        text("DELETE FROM accusations WHERE playthrough_id = :p"),
                        {"p": pt2["playthroughId"]},
                    )
            assert "immutable" in str(excinfo.value).lower()
            # The trigger is present again for future raw deletes too.
            with store._read_session() as session:
                present = session.execute(
                    text(
                        "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                        "AND name IN ('accusations_no_delete', 'accusations_no_update')"
                    )
                ).scalar_one()
            assert present == 2
    finally:
        _dispose(app)


def test_retained_cap_rejects_when_no_completed_rows_to_prune(database_url):
    """All-active case at the retained ceiling -> the honest at-capacity 429
    (no completed rows exist to garbage-collect; active rows are NEVER
    deleted)."""
    app = _build_app(
        database_url,
        MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=100,
        MAX_RETAINED_PLAYTHROUGHS_PER_CASE=3,
    )
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            for _ in range(3):
                assert create_playthrough(client, creator, case_id, 1)[0] == 201
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 429
            assert res.json()["error"]["code"] == "PLAYTHROUGH_LIMIT_EXCEEDED"
            assert_sanitized_error(res.text)
            assert _count_rows(app, case_id) == 3
            # Every original (active) row is preserved untouched (exact ids).
            with app.state.store._read_session() as session:
                ids = set(
                    session.execute(
                        text(
                            "SELECT playthrough_id FROM playthroughs "
                            "WHERE case_id = :cid AND case_version = 1"
                        ),
                        {"cid": case_id},
                    ).scalars()
                )
            assert len(ids) == 3
    finally:
        _dispose(app)


def test_retained_cap_documents_garbage_collection_operator_note(database_url):
    """Operator-visible note is a docstring contract: completed rows above the
    ceiling are garbage-collected at the NEXT create (proven here), which is
    exactly what test_retention_prunes_oldest_completed_preserves_active
    exercises. This test pins the bounded-batch size: only the needed rows are
    pruned (never a sweep of unrelated completed rows)."""
    app = _build_app(
        database_url,
        MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=100,
        MAX_RETAINED_PLAYTHROUGHS_PER_CASE=4,
    )
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            truth = truth_bundle(app, case_id, 1)
            tokens: list[dict] = []
            for i in range(4):
                _, pt = create_playthrough(client, creator, case_id, 1)
                if i < 2:
                    _complete_pt(client, pt["playthroughId"], pt["playthroughAccessToken"], truth)
                tokens.append(pt)
            assert _count_rows(app, case_id) == 4
            # One more create prunes ONLY the single oldest completed row.
            status, _new = create_playthrough(client, creator, case_id, 1)
            assert status == 201
            gone = tokens[0]["playthroughId"]
            store = app.state.store
            assert store.get_playthrough_by_id(gone) is None
            # The second completed row survived (batch bounded to `needed`).
            assert store.get_playthrough_by_id(tokens[1]["playthroughId"]) is not None
    finally:
        _dispose(app)


# --------------------------------------------------------------------------- #
# isolation: unrelated cases / auth surface
# --------------------------------------------------------------------------- #


def test_unrelated_cases_are_unaffected(database_url):
    """Filling case A's active cap never touches case B."""
    app = _build_app(database_url, MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=2)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case_a = create_case(client, session_token)
            case_b = create_case(
                client, session_token, prompt="Victim: sarah_miller\nMurderer: thomas_reed\nSecond"
            )
            for _ in range(2):
                assert create_playthrough(client, case_a["creatorAccessToken"], case_a["caseId"], 1)[0] == 201
            # Case B (own creator + version) is independent.
            status, body = create_playthrough(client, case_b["creatorAccessToken"], case_b["caseId"], 1)
            assert status == 201, body
            assert _count_rows(app, case_a["caseId"]) == 2
            assert _count_rows(app, case_b["caseId"]) == 1
            # Case A remains capped.
            res = client.post(
                f"/api/v1/cases/{case_a['caseId']}/versions/1/playthroughs",
                headers=auth(case_a["creatorAccessToken"]),
            )
            assert res.status_code == 429
    finally:
        _dispose(app)


def test_creator_session_auth_surface_is_unchanged(database_url):
    """The admission caps never weaken 401/404/429 auth ordering."""
    app = _build_app(database_url, MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=1)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case_a = create_case(client, session_token)
            case_b = create_case(client, session_token, prompt="Victim: sarah_miller\nMurderer: thomas_reed\nB")
            assert create_playthrough(client, case_a["creatorAccessToken"], case_a["caseId"], 1)[0] == 201
            # No credential -> 401 (auth still first).
            res = client.post(f"/api/v1/cases/{case_a['caseId']}/versions/1/playthroughs")
            assert res.status_code == 401
            # A foreign case's creator on case A -> generic 404 (no existence leak).
            res = client.post(
                f"/api/v1/cases/{case_a['caseId']}/versions/1/playthroughs",
                headers=auth(case_b["creatorAccessToken"]),
            )
            assert res.status_code == 404
            assert res.json()["error"]["code"] == "NOT_FOUND"
            # A valid creator at the cap -> the SANITIZED 429 (never a 500).
            res = client.post(
                f"/api/v1/cases/{case_a['caseId']}/versions/1/playthroughs",
                headers=auth(case_a["creatorAccessToken"]),
            )
            assert res.status_code == 429
            assert res.json()["error"]["code"] == "PLAYTHROUGH_LIMIT_EXCEEDED"
            assert_sanitized_error(res.text)
    finally:
        _dispose(app)


# --------------------------------------------------------------------------- #
# in-memory creation budgets (per-IP / per-creator)
# --------------------------------------------------------------------------- #


def test_per_ip_budget_and_xff_spoof_never_bypass_when_trust_off(database_url):
    """PLAYTHROUGH_CREATE_LIMIT_PER_IP with TRUST_PROXY=false: the direct
    socket peer is the identity; rotating X-Forwarded-For values can never
    reset the budget."""
    app = _build_app(
        database_url,
        PLAYTHROUGH_CREATE_LIMIT_PER_IP=2,
        PLAYTHROUGH_CREATE_LIMIT_PER_CREATOR=100,
        MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=100,
        MAX_RETAINED_PLAYTHROUGHS_PER_CASE=100,
        TRUST_PROXY=False,
    )
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            assert create_playthrough(client, creator, case_id, 1)[0] == 201
            assert create_playthrough(client, creator, case_id, 1)[0] == 201
            # Bucket spent (peer "testclient"): the third create is a 429 even
            # though the per-case/per-creator budgets are generous.
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 429
            assert res.json()["error"]["code"] == "TOO_MANY_REQUESTS"
            assert_sanitized_error(res.text)
            # A hostile X-Forwarded-For (fresh values each time) does NOT reset
            # the identity while TRUST_PROXY=false.
            for spoof in ("203.0.113.7", "198.51.100.9", "192.0.2.1"):
                res = client.post(
                    f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                    headers={**auth(creator), "X-Forwarded-For": spoof},
                )
                assert res.status_code == 429, spoof
    finally:
        _dispose(app)


def test_per_creator_budget_is_per_credential(database_url):
    """PLAYTHROUGH_CREATE_LIMIT_PER_CREATOR binds per creator credential; a
    different case's creator has its OWN budget (and the raw identity is never
    echoed in the denial)."""
    app = _build_app(
        database_url,
        PLAYTHROUGH_CREATE_LIMIT_PER_CREATOR=2,
        PLAYTHROUGH_CREATE_LIMIT_PER_IP=100,
        MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=100,
        MAX_RETAINED_PLAYTHROUGHS_PER_CASE=100,
    )
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case_a = create_case(client, session_token)
            case_b = create_case(client, session_token, prompt="Victim: sarah_miller\nMurderer: thomas_reed\nB")
            assert create_playthrough(client, case_a["creatorAccessToken"], case_a["caseId"], 1)[0] == 201
            assert create_playthrough(client, case_a["creatorAccessToken"], case_a["caseId"], 1)[0] == 201
            res = client.post(
                f"/api/v1/cases/{case_a['caseId']}/versions/1/playthroughs",
                headers=auth(case_a["creatorAccessToken"]),
            )
            assert res.status_code == 429
            assert res.json()["error"]["code"] == "TOO_MANY_REQUESTS"
            # The denial never exposes the creator identity/verifier.
            assert_sanitized_error(res.text)
            assert "verifier" not in res.text.lower()
            # Case B's creator has an independent budget.
            assert create_playthrough(client, case_b["creatorAccessToken"], case_b["caseId"], 1)[0] == 201
            assert create_playthrough(client, case_b["creatorAccessToken"], case_b["caseId"], 1)[0] == 201
    finally:
        _dispose(app)


# --------------------------------------------------------------------------- #
# store-level atomic admission (the durable path behind the route)
# --------------------------------------------------------------------------- #


def test_store_level_caps_are_atomic_and_side_effect_free(store):
    """The store unit enforces the caps inside the same transaction: no partial
    state, no side effects on rejection."""
    from app.auth.tokens import issue_token, verifier as _v

    now = 1_000_000.0
    store.create_session(
        session_id="QUOTA-F02",
        token_verifier=_v(issue_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    store.create_case(
        case_id="CASE-F02", quota_session_id="QUOTA-F02", title="t", difficulty=None, created_at=now
    )
    store.create_case_version(case_id="CASE-F02", version=1, state="PUBLISHED", generation_id="G", created_at=now)
    first = store.create_playthrough_if_published(
        playthrough_id="PT-F02-1", case_id="CASE-F02", case_version=1,
        token_verifier=_v(issue_token()), state="PLAYING", created_at=now, expires_at=now + 3600,
        max_active=1, max_retained=1,
    )
    assert first.playthrough_id == "PT-F02-1"
    with pytest.raises(PlaythroughCapacityError) as excinfo:
        store.create_playthrough_if_published(
            playthrough_id="PT-F02-2", case_id="CASE-F02", case_version=1,
            token_verifier=_v(issue_token()), state="PLAYING", created_at=now + 1, expires_at=now + 3600,
            max_active=1, max_retained=1,
        )
    assert excinfo.value.kind == "active"
    # No partial/no rejected row.
    assert store.get_playthrough_by_id("PT-F02-2") is None
    assert store.get_playthrough_by_id("PT-F02-1") is not None


def test_store_level_unlimited_callers_are_unchanged(store):
    """Low-level callers that pass NO caps keep the previous unbounded store
    semantics (seeding paths elsewhere depend on it)."""
    from app.auth.tokens import issue_token, verifier as _v

    now = 1_000_000.0
    store.create_session(
        session_id="QUOTA-F02U",
        token_verifier=_v(issue_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    store.create_case(
        case_id="CASE-F02U", quota_session_id="QUOTA-F02U", title="t", difficulty=None, created_at=now
    )
    store.create_case_version(case_id="CASE-F02U", version=1, state="PUBLISHED", generation_id="G", created_at=now)
    for i in range(6):  # beyond every shipped default cap
        store.create_playthrough_if_published(
            playthrough_id=f"PT-F02U-{i}", case_id="CASE-F02U", case_version=1,
            token_verifier=_v(issue_token()), state="PLAYING", created_at=now + i, expires_at=now + 3600,
        )
    with store._read_session() as session:
        total = session.execute(
            text("SELECT count(*) FROM playthroughs WHERE case_id = 'CASE-F02U'")
        ).scalar_one()
    assert total == 6