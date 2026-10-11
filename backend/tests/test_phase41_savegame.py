"""Phase41 — save/load + replay compatibility tests (§4/§24, §30, ADR-003).

RED scope:
- Legacy homicide fixtures load unchanged and solve (murdererId/Name exact).
- ``SavegameV1`` keeps ``formatVersion=1``; ``replayTruth.resolutionType`` is
  an OPTIONAL additive key present for SUICIDE (and omitted for legacy
  homicide — the exact-key legacy test stays green).
- A new SUICIDE ``.pdcase`` fixture round-trips the deterministic serializer
  byte-for-byte and mirrors the live projection.
- New suicide save round-trip: export -> import -> fresh replay -> solve ->
  reveal -> re-export -> re-import with ZERO provider calls.
- The savegame is reveal-gated: pre-reveal exports stay 403.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.services import savegame as sg  # noqa: E402

from phase5_helpers import auth  # noqa: E402
from phase6_helpers import client, playthrough  # noqa: E402
from test_phase7_helpers import (  # noqa: E402
    create_published_case_and_playthrough,
    make_accusation,
)
from phase41_helpers import (  # noqa: E402
    insert_variant,
    read_payload,
    suicide_body,
    to_suicide,
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SUICIDE_FIXTURE = (
    _BACKEND_DIR / "tests" / "fixtures" / "savegame" / "v1_demo_suicide.pdcase.json"
)


def _iter_key_paths(node, prefix: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path
            yield from _iter_key_paths(value, path)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _iter_key_paths(value, f"{prefix}.{index}")


def _all_docs(payload) -> str:
    return json.dumps(payload, sort_keys=True)


# --------------------------------------------------------------------------- #
# legacy fixtures — unchanged (§4/§30)
# --------------------------------------------------------------------------- #


def test_legacy_homicide_save_loads_and_solves(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = bundle["truth"]
    with client(phase5_app) as c:
        # Solve the legacy crime exactly as Phase-7 players did.
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], {
            "murdererId": truth["murdererId"],
            "motiveId": truth["motiveId"],
            "weaponId": truth["weaponId"],
            "crimeTime": truth["canonical"],
        })
        assert res.status_code == 200, res.text
        export = c.get(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/savegame",
            headers=auth(bundle["playthroughToken"]),
        )
        assert export.status_code == 200, export.text
        doc = json.loads(export.text)
        replay = doc["case"]["replayTruth"]
        # Legacy homicide: formatVersion 1, murderer id/name preserved EXACTLY
        # and NO resolutionType key (the additive suicide key is optional).
        assert doc["formatVersion"] == 1
        assert replay["murdererId"] == "thomas_reed"
        assert replay["murdererName"] == "Thomas Reed"
        assert "resolutionType" not in replay


# --------------------------------------------------------------------------- #
# new suicide save — round trip (§30)
# --------------------------------------------------------------------------- #


def test_suicide_save_round_trip_zero_provider_calls(phase5_app):
    """export -> import -> fresh replay -> solve -> reveal -> re-export ->
    re-import with zero provider calls (direct store reads; no generation)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    payload = to_suicide(read_payload(phase5_app, bundle["caseId"], 1))
    insert_variant(phase5_app, bundle["caseId"], 2, payload)

    def _new_playthrough():
        return playthrough(phase5_app, bundle["caseId"], bundle["creator"], version=2)

    # 1. export (reveal-gated -> accuse first).
    pt1_id, pt1_token = _new_playthrough()
    with client(phase5_app) as c:
        res = make_accusation(c, pt1_id, pt1_token, suicide_body(payload))
        assert res.status_code == 200, res.text
        export = c.get(
            f"/api/v1/playthroughs/{pt1_id}/savegame", headers=auth(pt1_token)
        )
        assert export.status_code == 200, export.text
        doc1 = json.loads(export.text)
        replay = doc1["case"]["replayTruth"]
        assert replay["resolutionType"] == "SUICIDE"
        assert replay["murdererId"] is None
        assert replay["murdererName"] is None
        assert doc1["formatVersion"] == 1

    # 2. "import" — the document re-reads as a valid SavegameV1.
    text2 = sg.serialize_savegame_v1(doc1)
    doc2 = json.loads(text2)
    assert doc2["case"]["replayTruth"] == replay

    # 3. fresh replay (second playthrough) — 0 answered, can solve + reveal.
    pt2_id, pt2_token = _new_playthrough()
    with client(phase5_app) as c:
        reveal_pre = c.get(
            f"/api/v1/playthroughs/{pt2_id}/reveal", headers=auth(pt2_token)
        )
        assert reveal_pre.status_code == 403, reveal_pre.text  # not accused yet
        res = make_accusation(c, pt2_id, pt2_token, suicide_body(payload))
        assert res.status_code == 200, res.text
        reveal = c.get(f"/api/v1/playthroughs/{pt2_id}/reveal", headers=auth(pt2_token))
        assert reveal.status_code == 200, reveal.text
        assert reveal.json()["result"]["overall"] == "solved"

    # 4. re-export from the second playthrough -> byte-identical (determinism).
    with client(phase5_app) as c:
        export2 = c.get(
            f"/api/v1/playthroughs/{pt2_id}/savegame", headers=auth(pt2_token)
        )
        assert export2.status_code == 200
        doc2b = json.loads(export2.text)
    assert _all_docs(doc2b["case"]["replayTruth"]) == _all_docs(replay)
    assert doc2b["case"]["metadata"]["sourceCaseId"] == bundle["caseId"]


def test_suicide_savegame_is_reveal_gated(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    payload = to_suicide(read_payload(phase5_app, bundle["caseId"], 1))
    insert_variant(phase5_app, bundle["caseId"], 2, payload)
    pt_id, pt_token = playthrough(phase5_app, bundle["caseId"], bundle["creator"], version=2)
    with client(phase5_app) as c:
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/savegame", headers=auth(pt_token)
        )
        assert res.status_code == 403, res.text
        assert res.json()["error"]["code"] == "REVEAL_NOT_AVAILABLE"


def test_suicide_savegame_never_leaks_hidden_murderer_marker(phase5_app):
    """The exported suicide document carries NO murderer marker in any of the
    player-facing sections (candidates/publicCase/scene/witnesses/evidence)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    payload = to_suicide(read_payload(phase5_app, bundle["caseId"], 1))
    # Stamp a sentinel into the hidden truth ONLY.
    payload["truth"]["crime"]["internal_secret_note"] = "SECRET-KILLER-MARKER-X"
    insert_variant(phase5_app, bundle["caseId"], 2, payload)
    pt_id, pt_token = playthrough(phase5_app, bundle["caseId"], bundle["creator"], version=2)
    with client(phase5_app) as c:
        make_accusation(c, pt_id, pt_token, suicide_body(payload))
        export = c.get(
            f"/api/v1/playthroughs/{pt_id}/savegame", headers=auth(pt_token)
        )
        assert export.status_code == 200, export.text
        assert "SECRET-KILLER-MARKER-X" not in export.text
        assert "murdererName" in json.loads(export.text)["case"]["replayTruth"]


# --------------------------------------------------------------------------- #
# committed suicide fixture
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _SUICIDE_FIXTURE.is_file(),
    reason="suicide savegame fixture is created by the phase-41 setup step",
)
def test_committed_suicide_fixture_round_trips_the_serializer():
    fixture_text = _SUICIDE_FIXTURE.read_text(encoding="utf-8")
    doc = json.loads(fixture_text)
    assert doc["formatVersion"] == sg.SAVEGAME_FORMAT_VERSION
    replay = doc["case"]["replayTruth"]
    assert replay["resolutionType"] == "SUICIDE"
    assert replay["murdererId"] is None
    assert sg.serialize_savegame_v1(doc) + "\n" == fixture_text
    assert "resolutionType" in _all_docs(doc)


def test_savegame_replay_truth_carries_witness_deceptions_only_when_present(phase5_app):
    """The reveal-gated replay truth carries the OPTIONAL witnessDeceptions
    list exactly when a witness lied — the browser-local reveal can explain the
    deception identically to the server; a deception-free save omits the key."""
    bundle = create_published_case_and_playthrough(phase5_app)
    payload = read_payload(phase5_app, bundle["caseId"], 1)
    payload["truth"]["witness_truthfulness"] = [
        {
            "witness_id": "emily_reed",
            "question_type": "OBSERVATION",
            "truthfulness": "DECEPTIVE",
            "deception_reason": "SELF_PROTECTION",
        }
    ]
    insert_variant(phase5_app, bundle["caseId"], 2, payload)
    pt_id, pt_token = playthrough(phase5_app, bundle["caseId"], bundle["creator"], version=2)
    body = {
        "murdererId": payload["truth"]["crime"]["murderer_id"],
        "motiveId": payload["truth"]["crime"]["motive_id"],
        "weaponId": payload["truth"]["crime"]["weapon_id"],
        "crimeTime": payload["truth"]["crime"]["crime_time"]["canonical"],
    }
    with client(phase5_app) as c:
        make_accusation(c, pt_id, pt_token, body)
        export = c.get(
            f"/api/v1/playthroughs/{pt_id}/savegame", headers=auth(pt_token)
        )
        assert export.status_code == 200, export.text
        replay = json.loads(export.text)["case"]["replayTruth"]
        assert replay["witnessDeceptions"] == [
            {
                "witnessId": "emily_reed",
                "witnessName": "Emily Reed",
                "deceptionReason": "SELF_PROTECTION",
            }
        ]
    # A deception-free case never carries the key (exact legacy shape).
    pt2_id, pt2_token = playthrough(phase5_app, bundle["caseId"], bundle["creator"], version=1)
    with client(phase5_app) as c:
        make_accusation(c, pt2_id, pt2_token, {
            "murdererId": "thomas_reed",
            "motiveId": "cover_up_embezzlement",
            "weaponId": "kitchen_knife",
            "crimeTime": "2026-09-11T22:17:00+02:00",
        })
        export2 = c.get(
            f"/api/v1/playthroughs/{pt2_id}/savegame", headers=auth(pt2_token)
        )
        assert export2.status_code == 200, export2.text
        replay2 = json.loads(export2.text)["case"]["replayTruth"]
        assert "witnessDeceptions" not in replay2
