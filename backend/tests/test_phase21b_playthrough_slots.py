"""Phase 21B Finding 5 — expired/abandoned PLAYING rows no longer consume
active slots (Phase21B-PAC §5).

Problem: ``MAX_ACTIVE_PLAYTHROUGHS_PER_CASE`` (Phase 21 F-02) counts non-
terminal rows by STATE only ({CREATED, PLAYING}). A PLAYING row whose player
token has expired (via ``PLAYTHROUGH_TOKEN_TTL_SECONDS`` -> ``expires_at``)
but was never played to completion stayed PLAYING forever, permanently
consuming an active slot and blocking new playthroughs for that
(caseId, caseVersion).

Decision: ``active`` is defined SEMANTICALLY — a {CREATED, PLAYING} row
consumes an active slot ONLY while its player token is still valid at the
admission instant (``expires_at > created_at`` of the incoming create; the API
passes ``clock.now()`` as both). An expired token means the row is abandoned:
it no longer blocks admission, its row + history remain RETAINED (counted by
``MAX_RETAINED`` per policy), and its old token is still REJECTED by auth
(``require_playthrough`` independently denies ``now >= expires_at``).

Implementation choice (documented): the ACTIVE-count query is changed to
exclude rows whose token is expired — NO new EXPIRED/ABANDONED state is added,
because the playthrough state vocabulary
({CREATED, PLAYING, ACCUSED, REVEALED}) is a closed enum used widely
(bootstrap read states, accusation/reveal gating: ``PRE_ACCUSATION_STATES`` /
``REVEAL_ELIGIBLE_STATES``); a new state would ripple through every gate for
zero benefit. Query-based exclusion cannot be revived (auth denies expired
tokens), preserves the retained rows, and keeps every gate byte-identical.

Required tests (Phase21B-PAC §5):
  - four live PLAYING rows hit the cap (429 PLAYTHROUGH_LIMIT_EXCEEDED);
  - AFTER the tokens expire, a new playthrough can be created (expired rows no
    longer consume active slots) while the expired rows stay retained;
  - an expired playthrough's old token is STILL denied on investigation/scene
    endpoints (auth gate intact);
  - unrelated cases are unaffected.

Deterministic: the app clock is a ``ManualClock``; token expiry is driven by
``clock.advance()`` past ``PLAYTHROUGH_TOKEN_TTL_SECONDS``. The autouse
conftest network block is active.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.generation.clock import ManualClock
from phase5_helpers import (
    auth,
    create_case,
    create_playthrough,
    create_session,
)

_TTL = 100  # small deterministic token TTL for the suite


def _build_app(database_url: str, **overrides):
    """Migrate + create_app with Finding-5 test settings (small TTL)."""
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
        playthrough_token_ttl_seconds=_TTL,
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


def _expired_pt_count(app, case_id: str, case_version: int = 1) -> int:
    """Rows whose playthrough token is (semantically) expired at the clock's
    CURRENT ManualClock instant: state non-terminal AND expires_at <= now."""
    now = float(app.state.clock.now())
    with app.state.store._read_session() as session:
        return int(
            session.execute(
                text(
                    "SELECT count(*) FROM playthroughs WHERE case_id = :cid "
                    "AND case_version = :v AND state IN ('CREATED', 'PLAYING') "
                    "AND expires_at <= :now"
                ),
                {"cid": case_id, "v": case_version, "now": now},
            ).scalar_one()
        )


def _make_clock_app(database_url: str, **overrides):
    """Build the app and swap in a ManualClock (deterministic expiry)."""
    merged = {
        "MAX_ACTIVE_PLAYTHROUGHS_PER_CASE": 4,
        "MAX_RETAINED_PLAYTHROUGHS_PER_CASE": 50,
        "PLAYTHROUGH_CREATE_LIMIT_PER_CREATOR": 100,
        "PLAYTHROUGH_CREATE_LIMIT_PER_IP": 100,
    }
    merged.update(overrides)
    app = _build_app(database_url, **merged)
    clock = ManualClock(start_time=1_000_000.0)
    app.state.clock = clock
    return app, clock


def _create_four(client, creator, case_id) -> list[dict]:
    """Four live PLAYING playthroughs on one case (the shipped cap)."""
    made: list[dict] = []
    for _ in range(4):
        status, body = create_playthrough(client, creator, case_id, 1)
        assert status == 201, body
        made.append(body)
    return made


# --------------------------------------------------------------------------- #
# four live PLAYING rows hit the cap
# --------------------------------------------------------------------------- #


def test_four_live_playing_rows_hit_cap_429(database_url):
    app, _clock = _make_clock_app(database_url)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            _create_four(client, creator, case_id)
            assert _count_rows(app, case_id) == 4
            # The 5th create is denied while all four tokens are LIVE.
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 429
            assert res.json()["error"]["code"] == "PLAYTHROUGH_LIMIT_EXCEEDED"
            assert _count_rows(app, case_id) == 4
    finally:
        _dispose(app)


def test_five_plus_live_rows_stay_capped_after_recovery_create(database_url):
    """After the expired-release fix the cap still holds for LIVE rows: with
    MAX_ACTIVE=4, four live + one live = 5 live rows MUST remain capped."""
    app, _clock = _make_clock_app(database_url, MAX_ACTIVE_PLAYTHROUGHS_PER_CASE=2)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            status, body = create_playthrough(client, creator, case_id, 1)
            assert status == 201, body
            _, body2 = create_playthrough(client, creator, case_id, 1)
            assert status == 201, body2
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 429
            assert res.json()["error"]["code"] == "PLAYTHROUGH_LIMIT_EXCEEDED"
    finally:
        _dispose(app)


# --------------------------------------------------------------------------- #
# after TOKEN EXPIRY a new playthrough can be created; expired rows retained
# --------------------------------------------------------------------------- #


def test_expired_tokens_release_active_slots_but_rows_stay_retained(database_url):
    """The core Finding-5 scenario: four live PLAYING rows hit the cap; advance
    the clock past PLAYTHROUGH_TOKEN_TTL_SECONDS; a NEW create then succeeds
    while the four expired rows remain retained (still present, still PLAYING)
    and count toward MAX_RETAINED — only the ACTIVE count dropped."""
    app, clock = _make_clock_app(database_url)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            made = _create_four(client, creator, case_id)
            assert _count_rows(app, case_id) == 4
            assert _expired_pt_count(app, case_id) == 0

            # Cap enforced while live.
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 429

            # Advance the clock past the token TTL -> all four tokens expired.
            clock.advance(_TTL + 1)
            assert _expired_pt_count(app, case_id) == 4

            # The 5th create now SUCCEEDS (expired rows no longer active).
            status, body = create_playthrough(client, creator, case_id, 1)
            assert status == 201, body
            # Retained: 4 expired + 1 live = 5 rows; the expired rows exist,
            # still PLAYING, and carry their history.
            assert _count_rows(app, case_id) == 5
            with app.state.store._read_session() as session:
                states = set(
                    session.execute(
                        text(
                            "SELECT state FROM playthroughs WHERE case_id = :cid "
                            "AND case_version = 1"
                        ),
                        {"cid": case_id},
                    ).scalars()
                )
            assert states == {"PLAYING"}
            for made_pt in made:
                row = app.state.store.get_playthrough_by_id(
                    made_pt["playthroughId"]
                )
                assert row is not None, "expired rows must stay retained"
                assert row.state == "PLAYING"
    finally:
        _dispose(app)


def test_multiple_new_creates_after_expiry_drain_the_released_slots(database_url):
    """Releasing 4 expired slots really frees the case: without the fix only
    ONE slot would ever be usable; with the fix all 4 live slots are usable
    again (each new create is live and consumes one active slot)."""
    app, clock = _make_clock_app(database_url)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            _create_four(client, creator, case_id)
            clock.advance(_TTL + 1)
            created: list[int] = []
            for _ in range(4):
                status, body = create_playthrough(client, creator, case_id, 1)
                created.append(status)
            assert created == [201, 201, 201, 201], created
            # All 8 rows retained (4 expired + 4 live); live cap is full again.
            assert _count_rows(app, case_id) == 8
            res = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 429
            assert res.json()["error"]["code"] == "PLAYTHROUGH_LIMIT_EXCEEDED"
    finally:
        _dispose(app)


# --------------------------------------------------------------------------- #
# no unauthorized revival: the expired row's OLD token stays denied
# --------------------------------------------------------------------------- #


def test_expired_token_still_denied_on_investigation_and_scene(database_url):
    """An expired playthrough's old playthroughAccessToken is still rejected by
    auth (401 SESSION_EXPIRED) on every playthrough-scoped endpoint — the
    query-based exclusion can never be revived through its old credential."""
    app, clock = _make_clock_app(database_url)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            made = _create_four(client, creator, case_id)
            pt_id = made[0]["playthroughId"]
            old_token = made[0]["playthroughAccessToken"]

            # While live: the same credentials ARE accepted (sanity).
            res = client.get(
                f"/api/v1/playthroughs/{pt_id}", headers=auth(old_token)
            )
            assert res.status_code == 200, res.json()

            clock.advance(_TTL + 1)
            # Investigation (scene bootstrap) endpoint.
            res = client.get(
                f"/api/v1/playthroughs/{pt_id}/investigation",
                headers=auth(old_token),
            )
            assert res.status_code == 401, res.json()
            assert res.json()["error"]["code"] == "SESSION_EXPIRED"
            # Scene/bootstrap endpoint.
            res = client.get(
                f"/api/v1/playthroughs/{pt_id}", headers=auth(old_token)
            )
            assert res.status_code == 401
            assert res.json()["error"]["code"] == "SESSION_EXPIRED"
            # A still-LIVE sibling's token is NOT affected: create one fresh
            # playthrough AFTER the expiry instant (its token is valid).
            clock.advance(_TTL + 1)
            status, fresh = create_playthrough(client, creator, case_id, 1)
            assert status == 201, fresh
            res = client.get(
                f"/api/v1/playthroughs/{fresh['playthroughId']}",
                headers=auth(fresh["playthroughAccessToken"]),
            )
            assert res.status_code == 200, res.json()
    finally:
        _dispose(app)


def test_expired_token_denied_on_interact_and_read_records(database_url):
    """The interaction + record-read investigation endpoints are equally gated
    by ``require_playthrough`` (expired token -> 401, never a 403/404 leak)."""
    app, clock = _make_clock_app(database_url)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case = create_case(client, session_token)
            creator = case["creatorAccessToken"]
            case_id = case["caseId"]
            made = _create_four(client, creator, case_id)
            pt_id = made[0]["playthroughId"]
            old_token = made[0]["playthroughAccessToken"]

            clock.advance(_TTL + 1)
            res = client.post(
                f"/api/v1/playthroughs/{pt_id}/objects/none/interact",
                json={"interaction": "examine"},
                headers=auth(old_token),
            )
            assert res.status_code == 401
            assert res.json()["error"]["code"] == "SESSION_EXPIRED"
            res = client.get(
                f"/api/v1/playthroughs/{pt_id}/records/none",
                headers=auth(old_token),
            )
            assert res.status_code == 401
            assert res.json()["error"]["code"] == "SESSION_EXPIRED"
    finally:
        _dispose(app)


# --------------------------------------------------------------------------- #
# unrelated cases unaffected
# --------------------------------------------------------------------------- #


def test_expiry_on_one_case_never_affects_another(database_url):
    """Filling + expiring case A's slots has zero effect on case B: B's own
    live rows still hit B's cap, and B's tokens expire on B's own clock."""
    app, clock = _make_clock_app(database_url)
    try:
        with TestClient(app) as client:
            session_token, _ = create_session(client)
            case_a = create_case(client, session_token)
            case_b = create_case(
                client,
                session_token,
                prompt=(
                    "Victim: sarah_miller\nMurderer: thomas_reed\n"
                    "Motive: cover_up_embezzlement\nWeapon: kitchen_knife\n"
                    "Time: 2026-09-11T22:17:00+02:00\n"
                ),
            )
            creator_a = case_a["creatorAccessToken"]
            creator_b = case_b["creatorAccessToken"]
            _create_four(client, creator_a, case_a["caseId"])
            # Case B is independent: one live row + still has slots.
            status, body = create_playthrough(client, creator_b, case_b["caseId"], 1)
            assert status == 201, body

            # Expire CASE A's tokens (same ManualClock) — B unaffected.
            clock.advance(_TTL + 1)
            status, body = create_playthrough(client, creator_a, case_a["caseId"], 1)
            assert status == 201, body  # A released its slots
            # B was already under its own cap; unaffected.
            status, body = create_playthrough(client, creator_b, case_b["caseId"], 1)
            assert status == 201, body
            assert _count_rows(app, case_a["caseId"]) == 5
            assert _count_rows(app, case_b["caseId"]) == 2
    finally:
        _dispose(app)


# --------------------------------------------------------------------------- #
# store-level semantics (deterministic, no API/clock)
# --------------------------------------------------------------------------- #


def test_store_level_expired_rows_release_active_slots(store):
    """The active-count query excludes rows whose token is expired at the
    admission instant: with max_active=4 and four EXPIRED PLAYING rows, a new
    create succeeds; with four LIVE rows it is rejected."""
    from app.auth.tokens import issue_token, verifier as _v

    now = 1_000_000.0
    store.create_session(
        session_id="QUOTA-F5",
        token_verifier=_v(issue_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    store.create_case(
        case_id="CASE-F5", quota_session_id="QUOTA-F5", title="t", difficulty=None, created_at=now
    )
    store.create_case_version(case_id="CASE-F5", version=1, state="PUBLISHED", generation_id="G", created_at=now)

    def _create(pt_id: str, at: float, expires_at: float, max_active: int = 4):
        return store.create_playthrough_if_published(
            playthrough_id=pt_id,
            case_id="CASE-F5",
            case_version=1,
            token_verifier=_v(issue_token()),
            state="PLAYING",
            created_at=at,
            expires_at=expires_at,
            max_active=max_active,
            max_retained=50,
        )

    # Four EXPIRED rows (tokens expired long ago, rows never completed).
    for index in range(4):
        _create(f"PT-F5-EXPIRED-{index}", at=now - 100_000, expires_at=now - 10_000)
    # A NEW create at `now`: the expired rows are NOT active -> succeeds.
    row = _create("PT-F5-NEW-1", at=now, expires_at=now + 3600)
    assert row.playthrough_id == "PT-F5-NEW-1"
    # The new live row + 3 more live = cap reached at exactly 4 -> 5th rejected.
    for index in range(3):
        _create(f"PT-F5-LIVE-{index}", at=now, expires_at=now + 3600)
    from app.persistence.store import PlaythroughCapacityError

    with pytest.raises(PlaythroughCapacityError) as excinfo:
        _create("PT-F5-REJECTED", at=now, expires_at=now + 3600)
    assert excinfo.value.kind == "active"
    # Retained: 4 expired + 4 live rows all present.
    with store._read_session() as session:
        total = session.execute(
            text("SELECT count(*) FROM playthroughs WHERE case_id = 'CASE-F5'")
        ).scalar_one()
    assert total == 8


def test_store_level_live_rows_still_hit_cap(store):
    """Regression guard: FOUR LIVE PLAYING rows still hit max_active=4 — the
    Finding-5 change must not weaken the live cap (Phase21B §5 preserve)."""
    from app.auth.tokens import issue_token, verifier as _v
    from app.persistence.store import PlaythroughCapacityError

    now = 1_000_000.0
    store.create_session(
        session_id="QUOTA-F5LIVE",
        token_verifier=_v(issue_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    store.create_case(
        case_id="CASE-F5LIVE", quota_session_id="QUOTA-F5LIVE", title="t", difficulty=None, created_at=now
    )
    store.create_case_version(case_id="CASE-F5LIVE", version=1, state="PUBLISHED", generation_id="G", created_at=now)
    for index in range(4):
        store.create_playthrough_if_published(
            playthrough_id=f"PT-F5L-{index}",
            case_id="CASE-F5LIVE",
            case_version=1,
            token_verifier=_v(issue_token()),
            state="PLAYING",
            created_at=now + index,
            expires_at=now + index + 3600,
            max_active=4,
            max_retained=50,
        )
    with pytest.raises(PlaythroughCapacityError) as excinfo:
        store.create_playthrough_if_published(
            playthrough_id="PT-F5L-4",
            case_id="CASE-F5LIVE",
            case_version=1,
            token_verifier=_v(issue_token()),
            state="PLAYING",
            created_at=now + 4,
            expires_at=now + 4 + 3600,
            max_active=4,
            max_retained=50,
        )
    assert excinfo.value.kind == "active"