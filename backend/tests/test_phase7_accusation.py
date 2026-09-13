"""Phase 7 — accusation endpoint (REQUIREMENTS 40.10/40.11, Phase7 B/C/H/I).

Numbered requirements covered:
  N1  valid first accusation accepted (200, ACCUSED, echo only)
  N2  duplicate SEQUENTIAL accusation rejected (409 CASE_ALREADY_SUBMITTED)
  N4  immutable persisted accusation (no store update path, DB trigger blocks
      raw SQL UPDATE/DELETE, row matches submission exactly)
  N9  candidate outside the pinned universe rejected (422, generic envelope)
  N10 cross-version candidate rejected (422, same envelope — no existence
      leak; the SAME id is accepted by a v2 playthrough)
  N11 malformed crimeTime rejected (422) + frozen body contract robustness
      (extra/missing fields)

Plus the frozen DTO shape checks: the 200 body echoes ONLY the submission.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import text

from phase5_helpers import assert_sanitized_error, auth
from phase6_helpers import client
from test_phase7_helpers import (
    V2_MURDERER_ID,
    V2_MOTIVE_ID,
    V2_WEAPON_ID,
    assert_no_pre_reveal_material,
    create_published_case_and_playthrough,
    make_accusation,
    new_playthrough,
    publish_v2,
    truth_bundle,
    winning_body,
)

ACCUSATION_BODY_KEYS = {"playthroughId", "caseId", "caseVersion", "status", "accusation"}
ECHO_KEYS = {"murdererId", "motiveId", "weaponId", "crimeTime"}

MALFORMED_TIMES = [
    "25:99:00",           # hour / minute out of range
    "24:00:00",           # hour out of range
    "22:60:00",           # minute out of range
    "22:17:60",           # second out of range
    "22-17:00",           # wrong separator
    "22:1700",            # no colon between minute/second
    "not-a-time",         # garbage
    "2026-09-11T22:17:00",  # ISO without an offset
    "22:17:00+02:00",     # offset without a date
    "T22:17:00",          # leading T
    "",                   # below the schema minimum length
    "2026-13-40T22:17:00+02:00",  # impossible calendar date
]


def _assert_422(res, message="Accusation is invalid"):
    assert res.status_code == 422, res.json()
    body = res.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    # Schema-level Pydantic rejections answer "Request validation failed";
    # service-level contract rejections answer "Accusation is invalid". The
    # frozen envelope code (VALIDATION_ERROR) is the stable assertion.
    assert body["error"]["message"] in ("Accusation is invalid", "Request validation failed")
    assert_sanitized_error(res.text)
    return body


def _base(truth):
    body = winning_body(truth)
    return {
        "murdererId": body["murdererId"],
        "motiveId": body["motiveId"],
        "weaponId": body["weaponId"],
        "crimeTime": body["crimeTime"],
    }


def test_n1_valid_first_accusation_accepted(phase5_app):
    """N1: a valid first accusation is accepted; the response echoes ONLY the
    submission (playthrough/case pin + status ACCUSED) — no truth, no scoring."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    truth = bundle["truth"]
    body = _base(truth)
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, body)
        assert res.status_code == 200, res.json()
        payload = res.json()

    assert set(payload.keys()) == ACCUSATION_BODY_KEYS
    assert payload["playthroughId"] == pt_id
    assert payload["caseId"] == bundle["caseId"]
    assert payload["caseVersion"] == 1
    assert payload["status"] == "ACCUSED"
    assert set(payload["accusation"].keys()) == ECHO_KEYS
    assert payload["accusation"] == body
    # Response carries NO truth / scoring / proof material (separation C).
    assert_no_pre_reveal_material(
        payload, echo=True, known_tokens={pt_token}
    )
    assert phase5_app.state.store.get_playthrough_state(pt_id) == "ACCUSED"
    row = phase5_app.state.store.get_accusation(pt_id)
    assert row is not None


def test_n2_duplicate_sequential_accusation_rejected(phase5_app):
    """N2: a second (sequential) accusation on the same playthrough is
    rejected 409 CASE_ALREADY_SUBMITTED; the first row + state stay intact."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    truth = bundle["truth"]
    first = _base(truth)
    with client(phase5_app) as c:
        r1 = make_accusation(c, pt_id, pt_token, first)
        assert r1.status_code == 200, r1.json()
        # A DIFFERENT, still-valid accusation must be refused (frozen).
        second = {
            "murdererId": truth["murdererId"],
            "motiveId": truth["motiveId"],
            "weaponId": truth["weaponId"],
            "crimeTime": "22:17:00",
        }
        r2 = make_accusation(c, pt_id, pt_token, second)
        assert r2.status_code == 409, r2.json()
        body = r2.json()
        assert body["error"]["code"] == "CASE_ALREADY_SUBMITTED"
        assert_sanitized_error(r2.text)
    assert phase5_app.state.store.get_playthrough_state(pt_id) == "ACCUSED"
    row = phase5_app.state.store.get_accusation(pt_id)
    assert row.murderer_id == first["murdererId"]
    assert row.motive_id == first["motiveId"]
    assert row.weapon_id == first["weaponId"]
    assert row.crime_time == first["crimeTime"]


def test_n4_immutable_persisted_accusation(phase5_app):
    """N4: the persisted accusation is immutable. The store exposes NO update
    / delete path; a raw SQL UPDATE and DELETE are aborted by the migration's
    database triggers; the row always matches the submission."""
    store = phase5_app.state.store
    assert not hasattr(store, "update_accusation")
    assert not hasattr(store, "delete_accusation")
    assert "update" not in {m for m in dir(store) if "accusation" in m.lower()}

    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    truth = bundle["truth"]
    body = _base(truth)
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, body)
        assert res.status_code == 200, res.json()

    row = store.get_accusation(pt_id)
    assert row.playthrough_id == pt_id
    assert row.murderer_id == body["murdererId"]
    assert row.motive_id == body["motiveId"]
    assert row.weapon_id == body["weaponId"]
    assert row.crime_time == body["crimeTime"]

    # Raw SQL UPDATE is aborted at the database level (migration 0004).
    blocked_update = False
    try:
        with store.transaction() as session:
            session.execute(
                text("UPDATE accusations SET motive_id = 'hacked' WHERE playthrough_id = :p"),
                {"p": pt_id},
            )
    except Exception as exc:  # noqa: BLE001 - assert the DB aborted it
        assert "immutable" in str(exc).lower(), exc
        blocked_update = True
    assert blocked_update, "raw SQL UPDATE to an accusation was NOT blocked"
    # Raw SQL DELETE is likewise aborted.
    blocked_delete = False
    try:
        with store.transaction() as session:
            session.execute(
                text("DELETE FROM accusations WHERE playthrough_id = :p"), {"p": pt_id}
            )
    except Exception as exc:  # noqa: BLE001 - assert the DB aborted it
        assert "immutable" in str(exc).lower(), exc
        blocked_delete = True
    assert blocked_delete, "raw SQL DELETE of an accusation was NOT blocked"
    # Still exactly one row with the ORIGINAL values.
    row = store.get_accusation(pt_id)
    assert row is not None
    assert row.motive_id == body["motiveId"]
    assert not hasattr(store, "update_accusation")


def test_n9_candidate_outside_universe_rejected(phase5_app):
    """N9: an id outside the pinned candidate universes is rejected with the
    GENERIC 422 (no existence leak); the playthrough stays PLAYING."""
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = bundle["truth"]
    with client(phase5_app) as c:
        body = _base(truth)
        body["murdererId"] = "nobody_in_universe_42"
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        _assert_422(res)
        body = _base(truth)
        body["motiveId"] = "motive_in_universe_42"
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        _assert_422(res)
        body = _base(truth)
        body["weaponId"] = "weapon_in_universe_42"
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        _assert_422(res)
    # No state change: still PLAYING, no accusation row.
    assert phase5_app.state.store.get_playthrough_state(bundle["playthroughId"]) == "PLAYING"
    assert phase5_app.state.store.get_accusation(bundle["playthroughId"]) is None


def test_n10_cross_version_candidate_rejected(phase5_app):
    """N10: a candidate that exists only in v2 is rejected for a PINNED v1
    playthrough (422, generic); the SAME id is accepted by a v2 playthrough —
    proving the rejection is version-pinning, not a broken id."""
    bundle = create_published_case_and_playthrough(phase5_app)
    publish_v2(phase5_app, bundle["caseId"])
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    # v2-only ids are genuinely NOT in the v1 universe...
    assert V2_MURDERER_ID not in truth["suspect_ids"]
    assert V2_MOTIVE_ID not in truth["motive_ids"]
    assert V2_WEAPON_ID not in truth["weapon_ids"]
    with client(phase5_app) as c:
        body = _base(truth)
        body["murdererId"] = V2_MURDERER_ID
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        _assert_422(res)
        body = _base(truth)
        body["motiveId"] = V2_MOTIVE_ID
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        _assert_422(res)
        body = _base(truth)
        body["weaponId"] = V2_WEAPON_ID
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        _assert_422(res)
    assert phase5_app.state.store.get_playthrough_state(bundle["playthroughId"]) == "PLAYING"
    assert phase5_app.state.store.get_accusation(bundle["playthroughId"]) is None

    # The v2 universe DOES contain them: a v2 playthrough accepts the suspect.
    v2_truth = truth_bundle(phase5_app, bundle["caseId"], 2)
    assert V2_MURDERER_ID in v2_truth["suspect_ids"]
    v2_pt_id, v2_pt_token = new_playthrough(phase5_app, bundle["caseId"], bundle["creator"], 2)
    with client(phase5_app) as c:
        body = {
            "murdererId": V2_MURDERER_ID,
            "motiveId": v2_truth["motiveId"],
            "weaponId": v2_truth["weaponId"],
            "crimeTime": v2_truth["canonical"],
        }
        res = make_accusation(c, v2_pt_id, v2_pt_token, body)
        assert res.status_code == 200, res.json()


def test_n11_malformed_time_rejected(phase5_app):
    """N11: every malformed crimeTime (both ISO and bare grammars) is rejected
    422 with the generic envelope; the playthrough is never consumed (still
    PLAYING, no accusation row)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = bundle["truth"]
    with client(phase5_app) as c:
        for bad in MALFORMED_TIMES:
            body = _base(truth)
            body["crimeTime"] = bad
            res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
            _assert_422(res)
        # Extra fields are forbidden by the frozen contract (extra="forbid").
        body = _base(truth)
        body["bogus"] = "sneaky"
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        assert res.status_code == 422, res.json()
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        # Missing field (schema) -> 422 envelope too.
        res = c.post(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/accusation",
            json={
                "murdererId": truth["murdererId"],
                "motiveId": truth["motiveId"],
                "weaponId": truth["weaponId"],
            },
            headers=auth(bundle["playthroughToken"]),
        )
        assert res.status_code == 422, res.json()
        assert_sanitized_error(res.text)
    assert phase5_app.state.store.get_playthrough_state(bundle["playthroughId"]) == "PLAYING"
    assert phase5_app.state.store.get_accusation(bundle["playthroughId"]) is None