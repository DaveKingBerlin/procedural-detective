"""DEF-053 — reveal never fabricates partial truth (e.g. "murdererId": "None")
from a corrupted published payload.

Repro: a published payload missing ``truth.crime.murderer_id`` answered
``GET /reveal`` with 200 and ``truth.murdererId="None"``
(``str(None)`` in the reveal label projection). The other corrupt variants
(non-mapping crime_time, canonical deleted, whole truth block deleted)
already answered the sanitized 500.

Fix (PRODUCTION): ``app.services.reveal.truth_labels`` now VALIDATES the
truth section BEFORE building any label — ``truth`` must be a mapping with a
mapping ``crime`` whose ``murderer_id`` / ``motive_id`` / ``weapon_id`` and
``crime_time.canonical`` are all present, non-empty strings. Any violation
raises ``RevealProjectionError`` which the API maps to the SANITIZED
``500 INTERNAL_ERROR`` envelope — the same status the pre-existing corrupt
variants answered (chosen + documented over 404: the playthrough and its
accusation DO exist, and 500 exposes nothing about which field is broken).
Zero partial truth is ever rendered.

Locked here: each corrupt variant -> sanitized 500 with NO "None" and NO
partial truth in the body; a clean payload reveals normally.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.auth.tokens import issue_playthrough_access_token, verifier as _verifier

from phase5_helpers import assert_sanitized_error, auth
from phase6_helpers import case_for, client, playthrough
from test_phase7_helpers import accuse_then_reveal, truth_bundle, winning_body

CANONICAL_CRIME_TIME = "2026-09-11T22:17:00+02:00"

# Each mutator receives the fresh parsed v1 payload and corrupts a DIFFERENT
# part of the truth section (DEF-053 variants).
CORRUPTIONS = {
    "murderer_id_missing": lambda p: p["truth"]["crime"].pop("murderer_id"),
    "motive_id_missing": lambda p: p["truth"]["crime"].pop("motive_id"),
    "weapon_id_missing": lambda p: p["truth"]["crime"].pop("weapon_id"),
    "canonical_missing": lambda p: p["truth"]["crime"]["crime_time"].pop("canonical"),
    "crime_time_missing": lambda p: p["truth"]["crime"].pop("crime_time"),
    "crime_missing": lambda p: p["truth"].pop("crime"),
    "truth_missing": lambda p: p.pop("truth"),  # whole truth block deleted
    "murderer_id_not_str": lambda p: p["truth"]["crime"].__setitem__("murderer_id", 123),
}


def _corrupt_v2_payload(app, case_id, mutate):
    """Clone the clean v1 payload into a PUBLISHED v2 whose truth section is
    corrupted by ``mutate`` (deterministic; no provider call)."""
    store = app.state.store
    now = float(app.state.clock.now())
    v1 = store.get_published(case_id, 1)
    assert v1 is not None
    payload = json.loads(v1.payload_json)
    payload["caseVersion"] = 2
    payload["publishedAt"] = now
    mutate(payload)  # apply the corruption to the freshly parsed copy
    store.create_case_version(
        case_id=case_id, version=2, state="PUBLISHED", generation_id="GEN-2", created_at=now
    )
    store.insert_published(
        case_id=case_id,
        case_version=2,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )
    return payload


def _reveal_on_corrupt_v2(app, case_id, variant, index):
    """A v2 playthrough with an ACCUSED state over the corrupted payload,
    then GET /reveal. Returns (status_code, body_text, body_dict)."""
    store = app.state.store
    now = float(app.state.clock.now())
    token = issue_playthrough_access_token()
    pt_id = f"PT-CORRUPT-{variant}-{index}"
    store.create_playthrough(
        playthrough_id=pt_id,
        case_id=case_id,
        case_version=2,
        token_verifier=_verifier(token),
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    store.insert_accusation_if_unaccused(
        playthrough_id=pt_id,
        case_id=case_id,
        case_version=2,
        murderer_id="thomas_reed",
        motive_id="cover_up_embezzlement",
        weapon_id="kitchen_knife",
        crime_time=CANONICAL_CRIME_TIME,
        created_at=now,
    )
    with client(app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(token))
    return res.status_code, res.text, res.json()


@pytest.mark.parametrize("variant", sorted(CORRUPTIONS.keys()))
def test_corrupt_truth_variant_never_renders_partial_truth(phase5_app, variant):
    """Every corrupt variant -> sanitized 500 INTERNAL_ERROR with NO "None"
    and NO partial canonical truth in the body (DEF-053)."""
    case_id, creator = case_for(phase5_app)
    _corrupt_v2_payload(phase5_app, case_id, CORRUPTIONS[variant])
    status, text, body = _reveal_on_corrupt_v2(phase5_app, case_id, variant, 0)

    assert status == 500, (text, variant)
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Internal server error"
    assert_sanitized_error(text)
    # Zero partial truth: no str(None) fabrication and no rendered ids.
    assert "None" not in text, (text, variant)
    assert '"murdererId": "None"' not in text
    assert "murdererName" not in text
    assert "canonical" not in text
    # The sanitized envelope is exactly the three error fields.
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message", "details"}


def test_clean_payload_reveals_normally(phase5_app):
    """Control: an UNcorrupted payload reveals the full truth (200, solved)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    truth = truth_bundle(phase5_app, case_id, 1)
    reveal = accuse_then_reveal(phase5_app, pt_id, pt_token, winning_body(truth))
    assert reveal["status"] == "REVEALED"
    assert reveal["truth"]["murdererId"] == truth["murdererId"]
    assert reveal["truth"]["murdererName"] == "Thomas Reed"
    assert reveal["truth"]["crimeTime"] == truth["canonical"]
    assert reveal["result"]["overall"] == "solved"
    assert reveal["score"]["correctDimensions"] == 4