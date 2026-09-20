"""DEF-081 — procedural render asset ids must NEVER leak into the semantic
weapon identity of the published world (regression suite).

Trace (CaseTruth -> PublicCase -> candidate universe -> WorldRequirements ->
Asset Oracle -> WorldGraph -> accusation candidates -> accusation POST ->
reveal DTO -> frontend display):

  truth.crime.weapon_id      == "bronze_ceremonial_ice_pick"   (SEMANTIC id)
  PublicObject.object_id     == "bronze_ceremonial_ice_pick"   (SEMANTIC id)
  PublicObject.asset_id      == "proc.decor.<16hex>"           (RENDER id)
  universes.weapon_ids       == [...object_ids...]             (SEMANTIC ids)
  accusation POST weaponId   == object id (asset id -> 422)    (SEMANTIC id)
  reveal.truth.weaponId      == object id                      (SEMANTIC id)
  reveal.truth.weaponName    == "Bronze Ceremonial Ice Pick"   (HUMAN label)
  candidates.weapons[].name  == HUMAN label (NEVER proc.*)     (HUMAN label)
  world_graph placement      == assetId proc.* (render layer)

The bug was: ``weapon_label_of`` derived the player-visible label from the
RENDER asset id, so a procedural id ``proc.decor.4551660f4a46b2eb`` rendered
"Proc.decor.4551660f4a46b2eb" in the accusation picker and the reveal screen.
The fix derives the label from the SEMANTIC object id (the published public
``object_id``): ``bronze_ceremonial_ice_pick`` -> "Bronze Ceremonial Ice Pick";
catalog weapons keep their human labels (``kitchen_knife`` -> "Kitchen Knife").
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.generation.state_machine import GenerationState
from app.schemas.accusation import AccusationRequest
from app.services.accusation import (
    AccusationValidationError,
    validate_universe_membership,
)
from app.services.publication import (
    project_world_objects,
    serialize_published_payload,
)
from app.services.reveal import candidate_block_of, truth_labels
from test_ollama_driver import _run, _staged  # hermetic driver harness (mocked transport)


# --------------------------------------------------------------------------- #
# the REAL ice-pick published world (mock-transport OllamaStageDriver run)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def icepick_payload():
    """The serialized published payload of a REAL full-chain driver run with
    the "bronze ceremonial ice pick" showcase (Anna Weiss / Paul Becker /
    stolen research data / 23:42 / Lisa Koenig / office). The Ollama transport
    is MOCKED (never a network call); every other stage is the real
    controller/driver/solver pipeline."""
    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    payload = json.loads(
        serialize_published_payload(
            record.published,
            seed=getattr(record, "seed", None),
            prompt="showcase",
            model="hermes3:8b (mock)",
            title="Showcase",
        )
    )
    # tracer for the runtime object ids (the REAL driver world).
    return payload


def _icepick_asset_id(payload: dict) -> str:
    for obj in payload["draft"]["objects"]:
        if obj.get("object_id") == "bronze_ceremonial_ice_pick":
            return str(obj.get("asset_id") or "")
    raise AssertionError("no bronze_ceremonial_ice_pick object in the published world")


def _weapon_entry(candidates: dict, weapon_id: str) -> dict:
    for entry in candidates["weapons"]:
        if entry["id"] == weapon_id:
            return entry
    raise AssertionError(f"no candidate weapon entry for {weapon_id!r}")


# --------------------------------------------------------------------------- #
# identity contract — candidates block
# --------------------------------------------------------------------------- #


def test_def081_candidates_weapon_entry_real_icepick_is_semantic(icepick_payload):
    """The accusation-candidates weapon entry for the REAL ice-pick published
    world carries: id == the SEMANTIC public object id, assetId == the render
    proc.* id and name == the HUMAN label ("Bronze Ceremonial Ice Pick")."""
    candidates = candidate_block_of(icepick_payload)
    entry = _weapon_entry(candidates, "bronze_ceremonial_ice_pick")
    assert entry["id"] == "bronze_ceremonial_ice_pick"
    assert isinstance(entry["assetId"], str)
    assert entry["assetId"].startswith("proc.decor.")
    assert entry["name"] == "Bronze Ceremonial Ice Pick"
    # the render id is SEPARATE from the semantic id and NEVER the label
    assert entry["assetId"] != entry["id"]
    assert "proc." not in entry["name"]


def test_def081_candidates_catalog_weapons_unchanged(icepick_payload):
    """Catalog weapons (knife / letter opener / scissors) keep their exact
    human labels and catalog asset ids — identity separation only changes the
    label source, never catalog rendering."""
    candidates = candidate_block_of(icepick_payload)
    assert _weapon_entry(candidates, "kitchen_knife") == {
        "id": "kitchen_knife",
        "assetId": "PROP_KITCHEN_KNIFE_01",
        "name": "Kitchen Knife",
    }
    assert _weapon_entry(candidates, "letter_opener") == {
        "id": "letter_opener",
        "assetId": "PROP_LETTER_OPENER_01",
        "name": "Letter Opener",
    }
    assert _weapon_entry(candidates, "scissors") == {
        "id": "scissors",
        "assetId": "PROP_SCISSORS_01",
        "name": "Scissors",
    }
    # every published weapon candidate label stays human (no proc.* anywherer)
    for entry in candidates["weapons"]:
        assert "proc." not in entry["name"]


# --------------------------------------------------------------------------- #
# identity contract — reveal truth
# --------------------------------------------------------------------------- #


def test_def081_reveal_truth_weapon_label_is_human(icepick_payload):
    """The reveal truth shows the CANONICAL human label while the semantic
    weapon id stays distinct from the render asset id."""
    labels = truth_labels(icepick_payload)
    assert labels["weaponId"] == "bronze_ceremonial_ice_pick"
    assert labels["weaponName"] == "Bronze Ceremonial Ice Pick"
    assert "proc." not in labels["weaponName"]
    assert labels["weaponId"] != _icepick_asset_id(icepick_payload)


# --------------------------------------------------------------------------- #
# identity contract — world graph keeps the render id at the RENDER layer
# --------------------------------------------------------------------------- #


def test_def081_world_graph_object_keeps_render_asset_id(icepick_payload):
    """The world-graph placement still carries assetId=proc.* (the renderer
    identity) + the validated generated definition — the fix never touches the
    render layer."""
    world = project_world_objects(icepick_payload)
    ice = next(
        (o for o in world if o["objectId"] == "bronze_ceremonial_ice_pick"),
        None,
    )
    assert ice is not None
    assert ice["assetId"].startswith("proc.decor.")
    assert ice["generated"] is not None
    assert ice["generated"]["canonicalName"] == "Bronze Ceremonial Ice Pick"


# --------------------------------------------------------------------------- #
# accusation: weapon universe is the SEMANTIC object ids
# --------------------------------------------------------------------------- #


def test_def081_universe_membership_is_semantic_object_ids(icepick_payload):
    """The accusation weapon universe IS the published object ids: submitting
    the semantic id validates; submitting the RENDER asset id is an unknown id
    (documented -> 422 at the API, see the API-level test below)."""
    truth_crime = icepick_payload["truth"]["crime"]
    body_semantic = AccusationRequest(
        murdererId=str(truth_crime["murderer_id"]),
        motiveId=str(truth_crime["motive_id"]),
        weaponId="bronze_ceremonial_ice_pick",
        crimeTime="23:42:00",
    )
    validate_universe_membership(icepick_payload, body_semantic)  # no raise

    asset_id = _icepick_asset_id(icepick_payload)
    assert asset_id.startswith("proc.decor.")
    body_asset = AccusationRequest(
        murdererId=str(truth_crime["murderer_id"]),
        motiveId=str(truth_crime["motive_id"]),
        weaponId=asset_id,
        crimeTime="23:42:00",
    )
    with pytest.raises(AccusationValidationError):
        validate_universe_membership(icepick_payload, body_asset)


# --------------------------------------------------------------------------- #
# hosted DTO-level leak scan: no proc.* in any player-facing string field
# --------------------------------------------------------------------------- #


def _player_facing_strings(node, path: str = "") -> list[tuple[str, str]]:
    """(key-path, value) for every string leaf NOT under an ``assetId`` key
    (the render id is a render-layer identity, never a player-facing label)."""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if key == "assetId":
                continue
            out.extend(_player_facing_strings(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(_player_facing_strings(value, f"{path}.{index}"))
    elif isinstance(node, str):
        out.append((path, node))
    return out


def test_def081_dto_level_leak_scan_no_proc_label_in_player_facing_fields(icepick_payload):
    """No procedural render id may appear in any player-facing string field of
    the published world: accusation-candidates labels, reveal truth labels and
    the bootstrap world-object label-ish fields."""
    candidates = candidate_block_of(icepick_payload)
    labels = truth_labels(icepick_payload)
    world = project_world_objects(icepick_payload)
    for blob in (candidates, labels, world):
        for _path, value in _player_facing_strings(blob):
            assert not value.startswith("proc."), f"proc.* leaked at {_path!r}"
            assert "proc.decor." not in value, f"proc.* leaked at {_path!r}"
    # the asset LAYER still carries the proc.* id (exactly the separation)
    assert _icepick_asset_id(icepick_payload).startswith("proc.decor.")


# --------------------------------------------------------------------------- #
# reload preserves the mapping (payload round-trip + real store restart)
# --------------------------------------------------------------------------- #


def test_def081_reload_preserves_identity_mapping(icepick_payload):
    """The published payload itself stores the mapping (semantic id + human
    label + render id): JSON round-trip (exactly what a reload re-reads from
    published_versions.payload_json) yields the SAME candidate entry, the SAME
    truth label and the SAME world-graph asset id."""
    round_trip = json.loads(json.dumps(icepick_payload, sort_keys=True))
    before_candidates = candidate_block_of(icepick_payload)
    after_candidates = candidate_block_of(round_trip)
    assert before_candidates == after_candidates
    assert truth_labels(icepick_payload) == truth_labels(round_trip)
    assert _icepick_asset_id(icepick_payload) == _icepick_asset_id(round_trip)
    assert truth_labels(round_trip)["weaponName"] == "Bronze Ceremonial Ice Pick"
    assert _weapon_entry(after_candidates, "bronze_ceremonial_ice_pick")[
        "name"
    ] == "Bronze Ceremonial Ice Pick"


# --------------------------------------------------------------------------- #
# API-level: accusation POST + reveal against a REAL store-published world
# --------------------------------------------------------------------------- #


def test_def081_api_accusation_and_reveal_with_real_icepick_world(phase5_app, icepick_payload):
    """Full accusation POST + reveal over a REAL store row built from the
    driver-world payload: semantic-id accusation succeeds, the reveal truth
    shows the human label, and submitting the render asset id answers 422."""
    from phase5_helpers import assert_sanitized_error, auth
    from phase6_helpers import case_for, client
    from test_phase7_helpers import new_playthrough, truth_bundle

    # A REAL golden v1 case (persisted through the API) — the ice-pick world is
    # then pinned onto the SAME test case as an immutable v2 published row
    # (never overwrites v1; the playthrough pins version 2 only).
    case_id, creator_token = case_for(phase5_app)
    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())
    version = 2
    payload = json.loads(json.dumps(icepick_payload, sort_keys=True))
    payload["caseId"] = case_id
    payload["caseVersion"] = version
    payload["publishedAt"] = now
    store.create_case_version(
        case_id=case_id,
        version=version,
        state="PUBLISHED",
        generation_id=f"GEN-{version}",
        created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=version,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )
    truth = truth_bundle(phase5_app, case_id, version)
    pt_id, pt_token = new_playthrough(phase5_app, case_id, creator_token, version=version)

    with client(phase5_app) as c:
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation",
            headers=auth(pt_token),
        )
        assert res.status_code == 200, res.json()
        candidates = res.json()["candidates"]
        weapon_entry = _weapon_entry(candidates, "bronze_ceremonial_ice_pick")
        assert weapon_entry["name"] == "Bronze Ceremonial Ice Pick"
        assert weapon_entry["assetId"].startswith("proc.decor.")

        # ---- semantic id accusation succeeds --------------------------------
        acc = {
            "murdererId": truth["murdererId"],
            "motiveId": truth["motiveId"],
            "weaponId": "bronze_ceremonial_ice_pick",
            "crimeTime": truth["canonical"],
        }
        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/accusation",
            json=acc,
            headers=auth(pt_token),
        )
        assert res.status_code == 200, res.json()
        assert res.json()["accusation"]["weaponId"] == "bronze_ceremonial_ice_pick"

        # ---- reveal shows the human label ----------------------------------
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(pt_token)
        )
        assert res.status_code == 200, res.json()
        reveal = res.json()
        assert reveal["truth"]["weaponName"] == "Bronze Ceremonial Ice Pick"
        assert reveal["truth"]["weaponId"] == "bronze_ceremonial_ice_pick"
        assert reveal["result"]["weaponCorrect"] is True

    # ---- submitting the RENDER asset id is an unknown weapon id -> 422 -------
    asset_id = _icepick_asset_id(payload)
    pt2_id, pt2_token = new_playthrough(phase5_app, case_id, creator_token, version=version)
    with client(phase5_app) as c:
        res = c.post(
            f"/api/v1/playthroughs/{pt2_id}/accusation",
            json={
                "murdererId": truth["murdererId"],
                "motiveId": truth["motiveId"],
                "weaponId": asset_id,
                "crimeTime": truth["canonical"],
            },
            headers=auth(pt2_token),
        )
        assert res.status_code == 422, res.json()
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert_sanitized_error(res.text)


__all__ = ["icepick_payload"]