"""DEF-051 — investigation BOOTSTRAP stays readable after accusation/reveal
(Phase7 A / REQUIREMENTS 40.6-40.7 regression).

Root cause: the Phase 6 bootstrap gate ``_require_playing`` allowed ONLY
PLAYING, so once a playthrough reached ACCUSED, the reload of
/accuse (bootstrap) answered 409 NOT_PLAYING and the reveal view lost its
player-safe candidate-name source (and the /scene reload showed a dead-end).

Fix (PRODUCTION): GET /investigation is now readable from EVERY Milestone-1
lifecycle state ({CREATED, PLAYING, ACCUSED, REVEALED}) via the new
``_require_bootstrap_readable`` guard; the response is unchanged otherwise.
The GAMEPLAY MUTATION endpoints (discover / interact / read-record) stay
PLAYING-only — after accusation they keep answering the existing 409
NOT_PLAYING envelope and never mutate.

Regressions locked here:
  (a) bootstrap 200 for PLAYING, ACCUSED and REVEALED playthroughs with
      byte-identical candidates / worldObjects / playerKnowledge blocks
      (knowledge no longer changes after accusation);
  (b) discover / interact / read AFTER accusation -> 409 NOT_PLAYING and
      NO knowledge mutation;
  (c) the ACCUSED bootstrap contains no truth fields and no correctness
      marker (phase-7 nested leak scan + candidates unmarked check);
  (d) after reveal the bootstrap is STILL 200 (REVEALED) with the same
      player-safe payload.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_helpers import auth
from phase6_helpers import (
    case_for,
    client,
    discover,
    interact,
    playthrough,
    read_record,
)

from test_phase7_helpers import (
    assert_candidates_unmarked,
    assert_no_pre_reveal_material,
    make_accusation,
    truth_bundle,
    winning_body,
)

KNIFE_OBJECT = "kitchen_knife"
KNIFE_EVIDENCE = "forensic_knife_match_01"
EMAIL_EVIDENCE = "email_thomas_01"


def _bootstrap(app, pt_id, pt_token):
    from phase6_helpers import bootstrap

    res = bootstrap(app, pt_id, pt_token)
    assert res.status_code == 200, res.json()
    return res.json()


def _assert_same_safe_payload(first: dict, second: dict) -> None:
    """The player-safe payload (candidates + worldObjects + playerKnowledge)
    is byte-identical across lifecycle states; only ``state`` differs."""
    assert first["candidates"] == second["candidates"]
    assert first["scene"] == second["scene"]
    assert first["playerKnowledge"] == second["playerKnowledge"]


def _accuse(app, pt_id, pt_token, truth):
    with client(app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(truth))
    assert res.status_code == 200, res.json()
    return res.json()


def test_a_bootstrap_readable_in_all_lifecycle_states(phase5_app):
    """(a) BOOTSTRAP returns 200 with an identical player-safe payload for
    PLAYING, ACCUSED and REVEALED; only ``state`` changes."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    truth = truth_bundle(phase5_app, case_id, 1)

    playing = _bootstrap(phase5_app, pt_id, pt_token)
    assert playing["state"] == "PLAYING"

    body = _accuse(phase5_app, pt_id, pt_token, truth)
    assert body["status"] == "ACCUSED"
    accused = _bootstrap(phase5_app, pt_id, pt_token)
    assert accused["state"] == "ACCUSED"
    _assert_same_safe_payload(playing, accused)

    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(pt_token))
    assert res.status_code == 200, res.json()
    revealed = _bootstrap(phase5_app, pt_id, pt_token)
    assert revealed["state"] == "REVEALED"
    _assert_same_safe_payload(accused, revealed)
    _assert_same_safe_payload(playing, revealed)


def test_b_knowledge_frozen_after_accusation(phase5_app):
    """(a/b) knowledge discovered/read BEFORE accusation is preserved and
    repeat bootstraps AFTER accusation never change it."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    truth = truth_bundle(phase5_app, case_id, 1)

    # Normal PLAYING gameplay: discover the knife and read its record.
    res = discover(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert res.status_code == 200, res.json()
    res = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert res.status_code == 200, res.json()
    before = _bootstrap(phase5_app, pt_id, pt_token)
    assert before["playerKnowledge"]["discoveredEvidenceIds"] == [KNIFE_EVIDENCE]
    assert before["playerKnowledge"]["readEvidenceIds"] == [KNIFE_EVIDENCE]

    _accuse(phase5_app, pt_id, pt_token, truth)

    first_after = _bootstrap(phase5_app, pt_id, pt_token)
    second_after = _bootstrap(phase5_app, pt_id, pt_token)
    assert first_after["playerKnowledge"] == before["playerKnowledge"]
    assert second_after["playerKnowledge"] == first_after["playerKnowledge"]
    assert first_after["scene"] == before["scene"]
    assert first_after["candidates"] == before["candidates"]


def test_c_gameplay_mutations_stay_playing_only(phase5_app):
    """(b) discover / interact / read-record AFTER accusation answer the frozen
    409 NOT_PLAYING envelope and mutate NOTHING (knowledge stays frozen)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    truth = truth_bundle(phase5_app, case_id, 1)

    # PLAYING gameplay first, then accuse.
    res = discover(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert res.status_code == 200, res.json()
    _accuse(phase5_app, pt_id, pt_token, truth)
    frozen = _bootstrap(phase5_app, pt_id, pt_token)
    kb_knife = frozen["playerKnowledge"]

    # discover a NEW record -> 409, no knowledge change.
    res = discover(phase5_app, pt_id, pt_token, EMAIL_EVIDENCE)
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "NOT_PLAYING"

    # interact with an object -> 409, no knowledge change.
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "NOT_PLAYING"

    # read a record -> 409, no knowledge change.
    res = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "NOT_PLAYING"

    after = _bootstrap(phase5_app, pt_id, pt_token)
    assert after["playerKnowledge"] == kb_knife
    assert after["playerKnowledge"] == frozen["playerKnowledge"]
    assert after["candidates"] == frozen["candidates"]


def test_d_accused_bootstrap_leak_scan(phase5_app):
    """(c) the ACCUSED bootstrap carries no canonical truth fields and no
    correctness/designation marker (nested scan + candidates-unmarked)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    truth = truth_bundle(phase5_app, case_id, 1)

    _accuse(phase5_app, pt_id, pt_token, truth)
    body = _bootstrap(phase5_app, pt_id, pt_token)

    assert_no_pre_reveal_material(
        body, canonical_time=truth["canonical"]
    )
    assert_candidates_unmarked(body["candidates"], truth)
    # Explicit no-designation spot checks on the raw payload text.
    import json as _json

    text_repr = _json.dumps(body, sort_keys=True)
    for marker in ("winner", "isCorrect", "acceptedScoring", "canonical"):
        assert marker not in text_repr, f"designation/internal marker leaked: {marker}"


def test_d2_revealed_bootstrap_still_200_and_same(phase5_app):
    """(d) after reveal the bootstrap is STILL 200 (REVEALED) with the same
    player-safe payload as the ACCUSED bootstrap."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    truth = truth_bundle(phase5_app, case_id, 1)

    _accuse(phase5_app, pt_id, pt_token, truth)
    accused = _bootstrap(phase5_app, pt_id, pt_token)
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(pt_token))
    assert res.status_code == 200, res.json()

    revealed = _bootstrap(phase5_app, pt_id, pt_token)
    assert revealed["state"] == "REVEALED"
    _assert_same_safe_payload(accused, revealed)