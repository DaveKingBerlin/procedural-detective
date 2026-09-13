"""Phase 7 — reveal endpoint (REQUIREMENTS 40.12 / 3.5, Phase7 D/E/F).

Numbered requirements covered:
  N6  reveal forbidden before an accusation (403 REVEAL_NOT_AVAILABLE)
  N7  reveal authorized only for the exact playthrough (foreign PT token 404)
  N8  foreign token cannot reveal (401 UNAUTHORIZED)
  N12/N13 correct/incorrect WHO
  N14/N15 correct/incorrect WHY
  N16/N17 correct/incorrect WEAPON
  N18 exact accepted WHEN (full ISO canonical)
  N19 boundary-second WHEN (T+N correct, T+N+1 incorrect — inclusive end)
  N20 a wrong accusation still reveals (200, overall incorrect, all False)
  N22 repeated reveal idempotent (byte-identical DTO)
  N28 REVEALED is terminal (accusation 409 after reveal; DTO repeats)

Extras: lower boundary seconds (T-N / T-N-1), bare "HH:MM[:SS]" anchoring,
same-instant full-ISO equivalence, per-dimension result/score mapping.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_helpers import assert_sanitized_error, auth
from phase6_helpers import client
from test_phase7_helpers import (
    accuse_then_reveal,
    assert_no_reveal_internal_material,
    create_published_case_and_playthrough,
    get_reveal,
    make_accusation,
    motive_out_of,
    new_playthrough,
    suspect_out_of,
    truth_bundle,
    weapon_out_of,
    winning_body,
)

REVEAL_KEYS = {
    "playthroughId", "caseId", "caseVersion", "status", "truth", "player",
    "result", "score", "timeline", "explanation",
}
TRUTH_KEYS = {
    "murdererId", "murdererName", "motiveId", "motiveLabel",
    "weaponId", "weaponName", "crimeTime",
}
RESULT_KEYS = {
    "murdererCorrect", "motiveCorrect", "weaponCorrect", "timeCorrect", "overall",
}


def _reveal(app, case_id, creator, version=1, **overrides):
    """One fresh playthrough, one accusation (overrides optional), one reveal."""
    pt_id, pt_token = new_playthrough(app, case_id, creator, version)
    truth = truth_bundle(app, case_id, version)
    body = winning_body(truth)
    body.update({k: v for k, v in overrides.items() if v is not None})
    return accuse_then_reveal(app, pt_id, pt_token, body), truth


def test_n6_reveal_forbidden_before_accusation(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    with client(phase5_app) as c:
        res = get_reveal(
            c, bundle["playthroughId"], bundle["playthroughToken"]
        )
    assert res.status_code == 403, res.json()
    assert res.json()["error"]["code"] == "REVEAL_NOT_AVAILABLE"
    assert_sanitized_error(res.text)
    # Still PLAYING — the 403 consumed nothing.
    assert phase5_app.state.store.get_playthrough_state(bundle["playthroughId"]) == "PLAYING"
    assert phase5_app.state.store.get_accusation(bundle["playthroughId"]) is None


def test_n7_reveal_authorized_only_exact_playthrough(phase5_app):
    """N7: playthrough B's token used on playthrough A's reveal path -> 404
    (the auth dependency binds the token to the exact path playthrough)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id, pt_token = new_playthrough(phase5_app, bundle["caseId"], bundle["creator"])
    with client(phase5_app) as c:
        res = get_reveal(c, bundle["playthroughId"], pt_token)
        assert res.status_code == 404, res.json()
        assert res.json()["error"]["code"] == "NOT_FOUND"
        assert_sanitized_error(res.text)


def test_n8_foreign_token_cannot_reveal(phase5_app):
    """N8: a random unknown token and a creator-class token on the reveal path
    are both rejected 401 — only the playthrough's own token opens reveal."""
    bundle = create_published_case_and_playthrough(phase5_app)
    with client(phase5_app) as c:
        res = get_reveal(c, bundle["playthroughId"], "x" * 43)
        assert res.status_code == 401, res.json()
        assert res.json()["error"]["code"] in ("UNAUTHORIZED", "SESSION_EXPIRED")
        # A creator credential is the WRONG token class for a playthrough path.
        res = get_reveal(c, bundle["playthroughId"], bundle["creator"])
        assert res.status_code == 401, res.json()
        assert res.json()["error"]["code"] == "SESSION_EXPIRED"
        assert_sanitized_error(res.text)


def test_n12_correct_who_reveals(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    reveal, truth = _reveal(phase5_app, bundle["caseId"], bundle["creator"])
    assert reveal["result"]["murdererCorrect"] is True
    assert reveal["result"]["overall"] == "solved"
    assert reveal["score"]["correctDimensions"] == 4
    assert reveal["truth"]["murdererId"] == truth["murdererId"]
    assert set(reveal["truth"].keys()) == TRUTH_KEYS
    assert reveal["truth"]["murdererName"]


def test_n13_incorrect_who_reveals(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    reveal, _ = _reveal(
        phase5_app, bundle["caseId"], bundle["creator"],
        murdererId=suspect_out_of(truth),
    )
    assert reveal["result"]["murdererCorrect"] is False
    assert reveal["result"]["motiveCorrect"] is True
    assert reveal["result"]["weaponCorrect"] is True
    assert reveal["result"]["timeCorrect"] is True
    assert reveal["result"]["overall"] == "incorrect"
    assert reveal["score"]["correctDimensions"] == 3


def test_n14_correct_why_reveals(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    reveal, truth = _reveal(phase5_app, bundle["caseId"], bundle["creator"])
    assert reveal["result"]["motiveCorrect"] is True
    assert reveal["truth"]["motiveLabel"]
    assert reveal["truth"]["motiveId"] == truth["motiveId"]


def test_n15_incorrect_why_reveals(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    reveal, _ = _reveal(
        phase5_app, bundle["caseId"], bundle["creator"],
        motiveId=motive_out_of(truth),
    )
    assert reveal["result"]["motiveCorrect"] is False
    assert reveal["result"]["murdererCorrect"] is True
    assert reveal["result"]["overall"] == "incorrect"
    assert reveal["score"]["correctDimensions"] == 3


def test_n16_correct_weapon_reveals(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    reveal, truth = _reveal(phase5_app, bundle["caseId"], bundle["creator"])
    assert reveal["result"]["weaponCorrect"] is True
    assert reveal["truth"]["weaponName"]
    assert reveal["truth"]["weaponId"] == truth["weaponId"]


def test_n17_incorrect_weapon_reveals(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    reveal, _ = _reveal(
        phase5_app, bundle["caseId"], bundle["creator"],
        weaponId=weapon_out_of(truth),
    )
    assert reveal["result"]["weaponCorrect"] is False
    assert reveal["result"]["murdererCorrect"] is True
    assert reveal["result"]["motiveCorrect"] is True
    assert reveal["result"]["overall"] == "incorrect"
    assert reveal["score"]["correctDimensions"] == 3


def test_n18_exact_accepted_when(phase5_app):
    """N18: the EXACT canonical time answers timeCorrect True (full ISO)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    reveal, truth = _reveal(phase5_app, bundle["caseId"], bundle["creator"])
    assert reveal["result"]["timeCorrect"] is True
    assert reveal["truth"]["crimeTime"] == truth["canonical"]
    assert reveal["result"]["overall"] == "solved"
    assert reveal["score"]["correctDimensions"] == 4


def test_n19_boundary_second_when_inclusive_end(phase5_app):
    """N19: the accepted time set is [T-N, T+N+1): T+N is CORRECT and
    T+N+1 (one second past the inclusive human end) is INCORRECT."""
    bundle = create_published_case_and_playthrough(phase5_app)

    def at(crime_time):
        return _reveal(
            phase5_app, bundle["caseId"], bundle["creator"], crimeTime=crime_time,
        )

    upper_inclusive, _ = at("2026-09-11T22:22:00+02:00")
    assert upper_inclusive["result"]["timeCorrect"] is True, "T+N must be correct"
    one_past, _ = at("2026-09-11T22:22:01+02:00")
    assert one_past["result"]["timeCorrect"] is False, "T+N+1 must be incorrect"
    assert one_past["result"]["overall"] == "incorrect"
    assert one_past["score"]["correctDimensions"] == 3


def test_boundary_seconds_lower_range(phase5_app):
    """Lower boundary: T-N is correct; one second earlier is not."""
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)

    def at(crime_time):
        return _reveal(
            phase5_app, bundle["caseId"], bundle["creator"],
            murdererId=suspect_out_of(truth), crimeTime=crime_time,
        )

    lower_inclusive, _ = at("2026-09-11T22:12:00+02:00")
    assert lower_inclusive["result"]["timeCorrect"] is True, "T-N must be correct"
    one_before, _ = at("2026-09-11T22:11:59+02:00")
    assert one_before["result"]["timeCorrect"] is False, "T-N-1 must be incorrect"


def test_n20_wrong_accusation_still_reveals(phase5_app):
    """N20: an entirely wrong accusation still answers 200 and reveals the full
    truth — overall "incorrect", every boolean False, score 0."""
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    pt_id, pt_token = new_playthrough(phase5_app, bundle["caseId"], bundle["creator"])
    body = {
        "murdererId": suspect_out_of(truth),
        "motiveId": motive_out_of(truth),
        "weaponId": weapon_out_of(truth),
        "crimeTime": "2026-09-11T08:00:00+02:00",
    }
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, body)
        assert res.status_code == 200, res.json()
        res = get_reveal(c, pt_id, pt_token)
        assert res.status_code == 200, res.json()
    reveal = res.json()
    assert set(reveal.keys()) == REVEAL_KEYS
    assert set(reveal["result"].keys()) == RESULT_KEYS
    assert reveal["result"] == {
        "murdererCorrect": False,
        "motiveCorrect": False,
        "weaponCorrect": False,
        "timeCorrect": False,
        "overall": "incorrect",
    }
    assert reveal["score"] == {"correctDimensions": 0, "totalDimensions": 4}
    assert reveal["truth"]["murdererId"] == truth["murdererId"]
    assert reveal["truth"]["crimeTime"] == truth["canonical"]
    # The player's accusation echoes VERBATIM (never corrected).
    assert reveal["player"]["accusation"] == body
    # The reveal DTO itself carries NO internal material.
    assert_no_reveal_internal_material(reveal, known_tokens={pt_token})


def test_n22_repeated_reveal_idempotent(phase5_app):
    """N22: repeat reveals return the structurally identical DTO; the ACCUSED
    -> REVEALED transition happens exactly once."""
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    pt_id, pt_token = new_playthrough(phase5_app, bundle["caseId"], bundle["creator"])
    with client(phase5_app) as c:
        body = winning_body(truth)
        res = make_accusation(c, pt_id, pt_token, body)
        assert res.status_code == 200, res.json()
        first = get_reveal(c, pt_id, pt_token)
        assert first.status_code == 200, first.json()
        second = get_reveal(c, pt_id, pt_token)
        assert second.status_code == 200, second.json()
    assert first.json() == second.json()
    assert phase5_app.state.store.get_playthrough_state(pt_id) == "REVEALED"


def test_n28_revealed_terminal(phase5_app):
    """N28: REVEALED is terminal — further accusations answer 409 and the
    playthrough answers REVEALED on GET; reveal stays idempotently readable."""
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    pt_id, pt_token = new_playthrough(phase5_app, bundle["caseId"], bundle["creator"])
    with client(phase5_app) as c:
        body = winning_body(truth)
        res = make_accusation(c, pt_id, pt_token, body)
        assert res.status_code == 200, res.json()
        reveal = get_reveal(c, pt_id, pt_token)
        assert reveal.status_code == 200, reveal.json()
        # Accusation after reveal -> 409 (REVEALED not in the pre-accusation states).
        res = make_accusation(c, pt_id, pt_token, body)
        assert res.status_code == 409, res.json()
        assert res.json()["error"]["code"] == "CASE_ALREADY_SUBMITTED"
        # GET playthrough still answers REVEALED.
        res = c.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token))
        assert res.status_code == 200, res.json()
        assert res.json()["status"] == "REVEALED"
        # Reveal remains readable and identical.
        again = get_reveal(c, pt_id, pt_token)
        assert again.status_code == 200, again.json()
        assert again.json() == reveal.json()
    assert phase5_app.state.store.get_playthrough_state(pt_id) == "REVEALED"


def test_bare_time_of_day_anchored_correct(phase5_app):
    """Extras: a bare "HH:MM[:SS]" is anchored to the canonical date + offset
    at evaluation (DEC-003) — the golden 22:17:00 (with and without seconds) is
    timeCorrect."""
    bundle = create_published_case_and_playthrough(phase5_app)
    for bare in ("22:17:00", "22:17"):
        reveal, _ = _reveal(
            phase5_app, bundle["caseId"], bundle["creator"], crimeTime=bare
        )
        assert reveal["result"]["timeCorrect"] is True, bare
        assert reveal["result"]["overall"] == "solved", bare
        assert reveal["player"]["accusation"]["crimeTime"] == bare


def test_same_instant_full_iso_equivalent(phase5_app):
    """Extras: a full ISO with a DIFFERENT offset that denotes the SAME instant
    is timeCorrect (UTC-tick equality of the evaluation)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    reveal, _ = _reveal(
        phase5_app, bundle["caseId"], bundle["creator"],
        crimeTime="2026-09-11T21:17:00+01:00",
    )
    assert reveal["result"]["timeCorrect"] is True
    assert reveal["result"]["overall"] == "solved"