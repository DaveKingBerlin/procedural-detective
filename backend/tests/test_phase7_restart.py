"""Phase 7 — restart durability + version-pinning (Phase7 H, N5/N21/N23).

Authoritative accusation/reveal state lives in SQLite, never in process
memory. These tests dispose EVERY engine and rebuild the application on the
SAME sqlite file:

  N5  ACCUSED survives a restart and reveals with the IDENTICAL accusation;
  N21 the reveal DTO is byte-identical after a restart (REVEALED persists);
  N23 a v1 playthrough reveals v1 truth after v2 publication (pinned version,
      never "latest"; the v2 playthrough reveals v2's different truth).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from phase5_helpers import auth
from test_phase7_helpers import (
    create_published_case_and_playthrough,
    get_reveal,
    make_accusation,
    new_playthrough,
    publish_v2,
    reopen_app,
    truth_bundle,
    winning_body,
)

TRUE_BODY = {
    "murdererCorrect": True,
    "motiveCorrect": True,
    "weaponCorrect": True,
    "timeCorrect": True,
    "overall": "solved",
}


def _accuse(app, pt_id, pt_token, body):
    with TestClient(app) as c:
        res = make_accusation(c, pt_id, pt_token, body)
        assert res.status_code == 200, res.json()
    return res.json()


def test_n5_restart_preserves_accused(phase5_app, database_url):
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    body = winning_body(bundle["truth"])
    accused = _accuse(phase5_app, pt_id, pt_token, body)
    assert accused["status"] == "ACCUSED"

    # Shut down EVERY engine over the file.
    phase5_app.state.engine.dispose()
    phase5_app.state.store.dispose()

    restarted = reopen_app(database_url)
    try:
        with TestClient(restarted) as c:
            # ACCUSED state survived.
            res = c.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token))
            assert res.status_code == 200, res.json()
            assert res.json()["status"] == "ACCUSED"
            # Reveal works and echoes the IDENTICAL immutable accusation.
            res = get_reveal(c, pt_id, pt_token)
            assert res.status_code == 200, res.json()
            reveal = res.json()
            assert reveal["result"]["overall"] == "solved"
            assert reveal["result"] == TRUE_BODY
            assert reveal["player"]["accusation"] == body
    finally:
        restarted.state.engine.dispose()
        restarted.state.store.dispose()


def test_n21_reveal_persists_across_restart(phase5_app, database_url):
    """N21: a completed REVEALED is durable — after a full restart the second
    reveal answers the byte-identical DTO."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    with TestClient(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(bundle["truth"]))
        assert res.status_code == 200, res.json()
        res = get_reveal(c, pt_id, pt_token)
        assert res.status_code == 200, res.json()
        first = res.json()

    phase5_app.state.engine.dispose()
    phase5_app.state.store.dispose()

    restarted = reopen_app(database_url)
    try:
        with TestClient(restarted) as c:
            res = get_reveal(c, pt_id, pt_token)
            assert res.status_code == 200, res.json()
            assert res.json() == first, "reveal DTO changed across restart"
            res = c.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token))
            assert res.status_code == 200, res.json()
            assert res.json()["status"] == "REVEALED"
    finally:
        restarted.state.engine.dispose()
        restarted.state.store.dispose()


def test_n23_v1_reveals_v1_truth_after_v2(phase5_app):
    """N23: publishing v2 with a DIFFERENT truth never rewrites the v1 pin. The
    v1 playthrough reveals EXACTLY v1 truth (thomas_reed / v1 canonical time)
    and stays solved; a v2 playthrough reveals v2's different truth."""
    bundle = create_published_case_and_playthrough(phase5_app)
    case_id = bundle["caseId"]
    assert bundle["truth"]["murdererId"] == "thomas_reed"

    # Accuse v1 with the CORRECT v1 truth BEFORE v2 exists.
    v1_pt_id, v1_pt_token = bundle["playthroughId"], bundle["playthroughToken"]
    _accuse(phase5_app, v1_pt_id, v1_pt_token, winning_body(bundle["truth"]))

    # Publish v2 (different murderer + extra universe candidates).
    v2_payload = publish_v2(phase5_app, case_id)
    v2_truth = truth_bundle(phase5_app, case_id, 2)
    assert v2_truth["murdererId"] == "alex_carter"
    assert v2_truth["murdererId"] != bundle["truth"]["murdererId"]

    # v1 playthrough still reveals v1 truth (pinned version, never "latest").
    with TestClient(phase5_app) as c:
        res = get_reveal(c, v1_pt_id, v1_pt_token)
        assert res.status_code == 200, res.json()
        v1_reveal = res.json()
    assert v1_reveal["caseVersion"] == 1
    assert v1_reveal["truth"]["murdererId"] == "thomas_reed"
    assert v1_reveal["truth"]["murdererName"] == "Thomas Reed"
    assert v1_reveal["truth"]["crimeTime"] == bundle["truth"]["canonical"]
    assert v1_reveal["result"]["overall"] == "solved"
    assert v1_reveal["score"] == {"correctDimensions": 4, "totalDimensions": 4}

    # A NEW v2 playthrough reveals the DIFFERENT v2 truth.
    v2_pt_id, v2_pt_token = new_playthrough(phase5_app, case_id, bundle["creator"], 2)
    _accuse(
        phase5_app,
        v2_pt_id,
        v2_pt_token,
        {
            "murdererId": "alex_carter",
            "motiveId": v2_truth["motiveId"],
            "weaponId": v2_truth["weaponId"],
            "crimeTime": v2_truth["canonical"],
        },
    )
    with TestClient(phase5_app) as c:
        res = get_reveal(c, v2_pt_id, v2_pt_token)
        assert res.status_code == 200, res.json()
        v2_reveal = res.json()
    assert v2_reveal["caseVersion"] == 2
    assert v2_reveal["truth"]["murdererId"] == "alex_carter"
    assert v2_reveal["truth"]["crimeTime"] == v2_payload["truth"]["crime"]["crime_time"]["canonical"]
    assert v2_reveal["result"]["overall"] == "solved"