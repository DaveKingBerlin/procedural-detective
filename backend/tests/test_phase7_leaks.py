"""Phase 7 — leak boundaries (Phase7 E/F/J, REQUIREMENTS 41.4/41.5).

Numbered requirements covered:
  N24 pre-reveal responses carry NO canonical designation: the investigation /
       discover / read / accusation bodies are recursively scanned; winning
       candidate ids may appear inside candidates but NO key marks a winner,
       and no truth / acceptedScoring section exists anywhere;
  N25 the reveal DTO carries NO proof / provider / token / verifier / internal
       id material (recursive allowlist check);
  N26 nested leak regression: the scanner itself must walk to ANY depth (a
       forbidden key buried five levels deep is still caught) and the real
       responses are scanned at full depth.

Plus the candidates-block frozen contract (Phase7 J/K): alphabetically sorted
by id, exact allowlisted entry keys (an extra key would be a designation
marker), winning candidates present unmarked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from phase5_helpers import auth
from phase6_helpers import client, KNIFE_EVIDENCE
from test_phase7_helpers import (
    assert_candidates_unmarked,
    assert_no_pre_reveal_material,
    assert_no_reveal_internal_material,
    create_published_case_and_playthrough,
    get_reveal,
    iter_key_paths,
    make_accusation,
    truth_bundle,
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
PLAYER_KEYS = {"accusation"}
RESULT_KEYS = {
    "murdererCorrect", "motiveCorrect", "weaponCorrect", "timeCorrect", "overall",
}
SCORE_KEYS = {"correctDimensions", "totalDimensions"}
TIMELINE_ENTRY_KEYS = {"time", "description"}
EXPLANATION_KEYS = {"evidence", "dimensions"}
DIMENSION_KEYS = {"who", "why", "weapon", "when"}
EVIDENCE_POINT_KEYS = {"evidenceId", "title", "point"}


def test_n24_pre_reveal_no_canonical_designation(phase5_app):
    """N24: investigation / discover / read / accusation bodies — scanned at
    every nesting level — never designate the canonical solution: no truth
    section, no acceptedScoring, no winner-mark key. Winning ids may appear as
    plain candidate members only."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    truth = bundle["truth"]

    with client(phase5_app) as c:
        headers = auth(pt_token)

        res = c.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=headers)
        assert res.status_code == 200, res.json()
        bootstrap = res.json()
        assert "candidates" in bootstrap
        assert_candidates_unmarked(bootstrap["candidates"], truth)
        assert_no_pre_reveal_material(
            bootstrap, known_tokens={pt_token}, canonical_time=truth["canonical"]
        )

        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/objects/kitchen_knife/interact",
            json={"interaction": "inspect"},
            headers=headers,
        )
        assert res.status_code == 200, res.json()
        discover_body = res.json()
        assert_no_pre_reveal_material(
            discover_body, known_tokens={pt_token}, canonical_time=truth["canonical"]
        )

        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/records/{KNIFE_EVIDENCE}", headers=headers
        )
        assert res.status_code == 200, res.json()
        read_body = res.json()
        assert_no_pre_reveal_material(
            read_body, known_tokens={pt_token}, canonical_time=truth["canonical"]
        )

        res = make_accusation(c, pt_id, pt_token, winning_body(truth))
        assert res.status_code == 200, res.json()
        accusation_body = res.json()
        assert_no_pre_reveal_material(
            accusation_body, echo=True, known_tokens={pt_token}
        )

    # The winning ids appear in the candidates block — UNmarked.
    candidate_ids = {
        "suspects": {s["id"] for s in bootstrap["candidates"]["suspects"]},
        "motives": {m["id"] for m in bootstrap["candidates"]["motives"]},
        "weapons": {w["id"] for w in bootstrap["candidates"]["weapons"]},
    }
    assert truth["murdererId"] in candidate_ids["suspects"]
    assert truth["motiveId"] in candidate_ids["motives"]
    assert truth["weaponId"] in candidate_ids["weapons"]
    # NOT a single designation/scoring key anywhere in the response text.
    for body in (bootstrap, discover_body, read_body, accusation_body):
        text = json.dumps(body, sort_keys=True)
        assert "acceptedScoring" not in text
        assert "truth" not in text
        assert "correctDimensions" not in text


def test_n25_reveal_dto_no_internal_material(phase5_app):
    """N25: the reveal DTO is an explicit allowlist — no solver proof, no
    accepted scoring, no provider/prompt/diagnostics, no tokens/verifiers, no
    internal ids, no sourceRef/propositions at ANY nesting level."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(bundle["truth"]))
        assert res.status_code == 200, res.json()
        res = get_reveal(c, pt_id, pt_token)
        assert res.status_code == 200, res.json()
        reveal = res.json()

    assert set(reveal.keys()) == REVEAL_KEYS
    assert set(reveal["truth"].keys()) == TRUTH_KEYS
    assert set(reveal["player"].keys()) == PLAYER_KEYS
    assert set(reveal["result"].keys()) == RESULT_KEYS
    assert set(reveal["score"].keys()) == SCORE_KEYS
    for entry in reveal["timeline"]:
        assert set(entry.keys()) == TIMELINE_ENTRY_KEYS
        assert isinstance(entry["time"], str) and entry["time"]
        assert isinstance(entry["description"], str) and entry["description"]
    assert set(reveal["explanation"].keys()) == EXPLANATION_KEYS
    for point in reveal["explanation"]["evidence"]:
        assert set(point.keys()) == EVIDENCE_POINT_KEYS
    # Phase18C per-dimension proof board: exactly the four dimensions, every
    # entry an EvidencePointDTO with a non-empty public title and a map phrase.
    dimensions = reveal["explanation"]["dimensions"]
    assert set(dimensions.keys()) == DIMENSION_KEYS
    for dimension_points in dimensions.values():
        assert isinstance(dimension_points, list)
        for point in dimension_points:
            assert set(point.keys()) == EVIDENCE_POINT_KEYS
            assert point["evidenceId"] and point["title"] and point["point"]

    assert_no_reveal_internal_material(reveal, known_tokens={pt_token})
    text = json.dumps(reveal, sort_keys=True)
    for banned in (
        "solverProof", "solutionProof", "acceptedScoring", "prompt",
        "providerOutput", "diagnostics", "verifier", "tokenVerifier",
        "sourceRef", "propositions", "universe", "schemaVersion",
    ):
        assert banned not in text, f"reveal DTO leaks {banned!r}"
    assert pt_token not in text, "reveal DTO echoes the playthrough token"
    assert "generationAttemptId" not in text


def test_n26_nested_leak_regression(phase5_app):
    """N26: the recursive scanners reach ANY nesting depth — a forbidden key
    five levels down (and a token value five levels down) is still caught, and
    every real pre-reveal + reveal response is scanned at full depth."""
    # The scanner itself must walk deep (regression: a shallow top-level scan
    # would pass these synthetic payloads).
    deep_proof = {
        "a": {"b": {"c": {"d": {"e": {"solverProof": {"internals": True}}}}}}
    }
    paths = [p for p, _ in iter_key_paths(deep_proof)]
    assert "a.b.c.d.e.solverProof" in paths
    with pytest.raises(AssertionError) as exc:
        assert_no_pre_reveal_material(deep_proof)
    assert "solverProof" in str(exc.value)

    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    truth = bundle["truth"]

    deep_token = {"l1": {"l2": {"l3": {"value": pt_token}}}}
    with pytest.raises(AssertionError) as exc:
        assert_no_pre_reveal_material(deep_token, known_tokens={pt_token})
    assert "token" in str(exc.value)

    deep_nested_reveal_leak = {
        "a": {"b": {"c": {"d": {"accept": {"acceptedScoring": [0, 1]}}}}}
    }
    with pytest.raises(AssertionError) as exc:
        assert_no_reveal_internal_material(deep_nested_reveal_leak)
    assert "acceptedScoring" in str(exc.value)

    # Real responses, scanned at full depth.
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token))
        assert res.status_code == 200
        assert_no_pre_reveal_material(
            res.json(), known_tokens={pt_token}, canonical_time=truth["canonical"]
        )
        res = make_accusation(c, pt_id, pt_token, winning_body(truth))
        assert res.status_code == 200, res.json()
        assert_no_pre_reveal_material(res.json(), echo=True, known_tokens={pt_token})
        res = get_reveal(c, pt_id, pt_token)
        assert res.status_code == 200, res.json()
        assert_no_reveal_internal_material(res.json(), known_tokens={pt_token})


def test_candidates_block_alphabetical_unmarked(phase5_app):
    """Phase7 J/K frozen contract: candidates is a single block with exactly
    suspects/motives/weapons, each sorted alphabetically by id, each entry with
    EXACTLY the allowlisted keys, every universe member present, and the
    winning candidate present but carrying NO marker at all."""
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    with client(phase5_app) as c:
        res = c.get(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/investigation",
            headers=auth(bundle["playthroughToken"]),
        )
        assert res.status_code == 200, res.json()
        candidates = res.json()["candidates"]

    assert set(candidates.keys()) == {"suspects", "motives", "weapons"}
    assert_candidates_unmarked(candidates, truth)

    suspects = [s["id"] for s in candidates["suspects"]]
    motives = [m["id"] for m in candidates["motives"]]
    weapons = [w["id"] for w in candidates["weapons"]]
    # Every universe member is exposed (golden case: all have public labels).
    assert suspects == truth["suspect_ids"]
    assert motives == truth["motive_ids"]
    assert weapons == truth["weapon_ids"]
    # Winner ids are present and NEVER distinguished from the other candidates.
    assert truth["murdererId"] in suspects
    assert truth["motiveId"] in motives
    assert truth["weaponId"] in weapons
    for entry in candidates["suspects"]:
        assert set(entry.keys()) == {"id", "name"}
    for entry in candidates["motives"]:
        assert set(entry.keys()) == {"id", "label"}
    for entry in candidates["weapons"]:
        assert set(entry.keys()) == {"id", "assetId", "name"}