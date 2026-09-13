"""Phase 7 — concurrency & rollback safety (Phase7 C/I, N3/N27 + idempotent
concurrent reveal).

Thread-based over a REAL sqlite FILE. Deterministic: a threading.Barrier
replaces any sleep; no network. The store serializes writes with its RLock and
SQLite's rowcount CAS is the real guard:

- N3: two threads accuse the SAME playthrough simultaneously -> exactly ONE
  succeeds (200), the loser answers 409 CASE_ALREADY_SUBMITTED, exactly one
  accusation row exists;
- concurrent reveal: two threads reveal simultaneously -> BOTH return the
  byte-identical DTO and the ACCUSED -> REVEALED transition is idempotent;
- N27: when the accusation INSERT fails after the CAS state UPDATE, the whole
  transaction rolls back — no half-ACCUSED state, no partial row.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import text

from test_phase7_helpers import (
    create_published_case_and_playthrough,
    get_reveal,
    make_accusation,
    truth_bundle,
    winning_body,
)


def test_n3_concurrent_accusation_exactly_one_winner(phase5_app):
    """N3: two threads accuse simultaneously; the CAS guarantees exactly one
    accepted accusation and one 409 loser — never a corrupt half-state."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    body = winning_body(bundle["truth"])

    start = threading.Barrier(2)
    outcomes: list[tuple[int, dict]] = []
    errors: list[BaseException] = []

    def _worker(index: int):
        try:
            with TestClient(phase5_app) as c:
                start.wait()
                res = make_accusation(c, pt_id, pt_token, body)
                outcomes.append((res.status_code, res.json()))
        except BaseException as exc:  # noqa: BLE001 - recorded for assert
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert sorted(code for code, _ in outcomes) == [200, 409], outcomes
    loser = next(body for code, body in outcomes if code == 409)
    assert loser["error"]["code"] == "CASE_ALREADY_SUBMITTED"
    # Exactly ONE accusation row, one ACCUSED playthrough.
    assert phase5_app.state.store.get_playthrough_state(pt_id) == "ACCUSED"
    row = phase5_app.state.store.get_accusation(pt_id)
    assert row is not None
    assert (row.murderer_id, row.motive_id, row.weapon_id, row.crime_time) == (
        body["murdererId"],
        body["motiveId"],
        body["weaponId"],
        body["crimeTime"],
    )


def test_n27_rollback_leaves_no_half_accused(phase5_app):
    """N27: if the accusation INSERT fails right after the CAS state UPDATE,
    the whole transaction rolls back: the playthrough is NOT left ACCUSED and
    the original immutable row is untouched."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    store = phase5_app.state.store
    truth = bundle["truth"]

    first = winning_body(truth)
    with TestClient(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, first)
        assert res.status_code == 200, res.json()

    # Fabricate the inconsistent DB state the CAS must survive: playthrough
    # back in PLAYING while its accusation row already exists.
    with store.transaction() as session:
        session.execute(
            text("UPDATE playthroughs SET state = 'PLAYING' WHERE playthrough_id = :p"),
            {"p": pt_id},
        )
    assert store.get_playthrough_state(pt_id) == "PLAYING"

    # A second, DIFFERENT accusation: CAS matches (PLAYING), the INSERT then
    # collides on the primary key inside the SAME transaction -> rollback.
    second = dict(first)
    second["crimeTime"] = "22:17:00"
    with TestClient(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, second)
        assert res.status_code == 409, res.json()
        assert res.json()["error"]["code"] == "CASE_ALREADY_SUBMITTED"

    # NO half-ACCUSED: the playthrough is still exactly PLAYING and the row
    # still carries the ORIGINAL values (nothing partially written).
    assert store.get_playthrough_state(pt_id) == "PLAYING"
    row = store.get_accusation(pt_id)
    assert row is not None
    assert row.crime_time == first["crimeTime"], "rollback did not restore the row"
    assert row.motive_id == first["motiveId"]


def test_concurrent_reveal_idempotent(phase5_app):
    """Two threads reveal the same playthrough simultaneously: BOTH receive the
    identical DTO and the ACCUSED -> REVEALED transition persists exactly once."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    with TestClient(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(bundle["truth"]))
        assert res.status_code == 200, res.json()

    start = threading.Barrier(2)
    outcomes: list[tuple[int, dict]] = []
    errors: list[BaseException] = []

    def _worker(index: int):
        try:
            with TestClient(phase5_app) as c:
                start.wait()
                res = get_reveal(c, pt_id, pt_token)
                outcomes.append((res.status_code, res.json()))
        except BaseException as exc:  # noqa: BLE001 - recorded for assert
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert [code for code, _ in outcomes] == [200, 200], outcomes
    assert outcomes[0][1] == outcomes[1][1], "concurrent reveals diverged"
    reveal = outcomes[0][1]
    assert reveal["status"] == "REVEALED"
    assert reveal["result"]["overall"] == "solved"
    assert phase5_app.state.store.get_playthrough_state(pt_id) == "REVEALED"