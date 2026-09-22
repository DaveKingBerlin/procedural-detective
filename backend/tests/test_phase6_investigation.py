"""Phase 6 — investigation bootstrap (REQUIREMENTS 40.7, Phase6 B/D; Phase6 O
items 1/3/11/13/16 + robustness).

- O13 public bootstrap contains no undiscovered evidence content (deep-scan:
  no description / body / propositions / truth)
- O16 nested truth/proof leak regression over every Phase 6 response
- O1  empty PlayerKnowledge on a new playthrough (API-level)
- O3  foreign playthrough token on the bootstrap -> 404
- O11 v1 playthrough continues using v1 evidence after v2 publish
- world object projection: sorted by objectId, >= 6 entries, knife+laptop+body
  scene, unknown references skipped, no coordinates / no hidden fields
- robustness: unknown object 404, interaction mismatch 409 without state
  change, expired / invalid playthrough token 401, missing pinned version 404
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_helpers import (
    assert_no_hidden_leaks,
    assert_sanitized_error,
    auth,
    create_case,
    create_playthrough,
    create_session,
)
from phase6_helpers import (
    BODY_OBJECT,
    KNIFE_OBJECT,
    LAPTOP_OBJECT,
    SCENE_LOCATION,
    SCENE_NAME,
    bootstrap,
    case_for,
    client,
    interact,
    playthrough,
    publish_v2_with_extra_evidence,
)

BOOTSTRAP_KEYS = {
    "playthroughId",
    "caseId",
    "caseVersion",
    "state",
    "playerKnowledge",
    "scene",
    # Phase 7 J/K frozen addition: the player-safe accusation candidate
    # universes of the pinned CaseVersion (alphabetical, never marked).
    "candidates",
}
WORLD_OBJECT_KEYS = {
    "objectId",
    "assetId",
    "assetType",
    "subtype",
    "locationId",
    "anchor",
    "interaction",
    "evidenceId",
    "discovered",
    "read",
}


def test_1_01_api_bootstrap_empty_knowledge_and_pin(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = bootstrap(phase5_app, pt_id, pt_token)
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == BOOTSTRAP_KEYS
    assert body["playthroughId"] == pt_id
    assert body["caseId"] == case_id
    assert body["caseVersion"] == 1
    assert body["state"] == "PLAYING"
    assert body["playerKnowledge"] == {
        "discoveredEvidenceIds": [],
        "readEvidenceIds": [],
        "visitedLocationIds": [],
    }
    assert_no_hidden_leaks(body)


def test_1_03_foreign_playthrough_token_cannot_bootstrap(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_a, token_a = playthrough(phase5_app, case_id, creator)
    pt_b, _ = playthrough(phase5_app, case_id, creator)
    res = bootstrap(phase5_app, pt_b, token_a)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert_sanitized_error(res.text)


def test_13_bootstrap_contains_no_undiscovered_evidence_content(phase5_app):
    """Deep scan: the bootstrap carries world objects, never ANY evidence
    content. No presentation title/description/body/subject of ANY evidence
    fact (discovered or not) may appear anywhere in the response."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = bootstrap(phase5_app, pt_id, pt_token)
    assert res.status_code == 200
    body = res.json()
    assert_no_hidden_leaks(body)
    # Every string in every evidence presentation is banned from the bootstrap
    # — EXCEPT the published candidate-universe ids (Phase 7 J/K frozen
    # addition): those ids are PUBLIC universe material announced by the
    # candidates block (a suspect id may coincidentally equal an evidence
    # field value such as fromPersonId), NOT undiscovered evidence content.
    store = phase5_app.state.store
    published = store.get_published(case_id, 1)
    payload = json.loads(published.payload_json)
    universes = payload.get("universes") or {}
    public_ids = {
        str(i)
        for list_key in ("suspect_ids", "motive_ids", "weapon_ids")
        for i in (universes.get(list_key) or ())
    }
    forbidden_strings = []
    for fact in payload["draft"]["evidence"]:
        presentation = fact.get("presentation") or {}
        for value in presentation.values():
            if isinstance(value, str) and value:
                if value in public_ids:
                    continue  # public candidate-universe id, not content
                forbidden_strings.append(value)
    body_text = res.text
    for needle in forbidden_strings:
        assert needle not in body_text, f"evidence content leaked: {needle!r}"
    # Structural markers of raw evidence material never appear.
    assert "description" not in body_text
    assert "propositions" not in body_text


def test_16_nested_leak_regression_every_phase6_response(phase5_app):
    """O16: recursive key scan of EVERY Phase 6 response (bootstrap, interact,
    read) — no truth / murderer / weapon / time / proof / diagnostics /
    verifier / token / internal ids, at ANY nesting level. (The direct
    discover endpoint was removed in Phase 20 / PD-SEC-01; discovery is
    exercised through the interact response below.)"""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    responses = []
    with client(phase5_app) as c:
        headers = auth(pt_token)
        res = c.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=headers)
        assert res.status_code == 200
        responses.append(res.json())
        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/objects/{KNIFE_OBJECT}/interact",
            json={"interaction": "inspect"},
            headers=headers,
        )
        assert res.status_code == 200
        responses.append(res.json())
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/records/forensic_knife_match_01",
            headers=headers,
        )
        assert res.status_code == 200
        responses.append(res.json())
    for response in responses:
        assert_no_hidden_leaks(response)
        text_repr = json.dumps(response, sort_keys=True)
        # Canonical hidden truth time never appears.
        assert "22:17" not in text_repr
    # The raw playthrough token is never echoed by any response.
    assert pt_token not in json.dumps(responses)


def test_11_v1_bootstrap_unchanged_after_v2_publish(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    before = bootstrap(phase5_app, pt_id, pt_token)
    assert before.status_code == 200
    before_body = before.json()

    publish_v2_with_extra_evidence(phase5_app, case_id)

    after = bootstrap(phase5_app, pt_id, pt_token)
    assert after.status_code == 200
    after_body = after.json()
    # Still pinned to v1 and byte-identical (v2 changes nothing for v1).
    assert after_body == before_body
    assert after_body["caseVersion"] == 1


def test_bootstrap_world_objects_sorted_with_golden_scene(phase5_app):
    """The Milestone-1 scene: >= 6 world objects, sorted by objectId, with the
    kitchen knife + laptop + victim body present. PD-SEC-01 (Phase 20): no
    UNDISCOVERED evidence id is exposed — every object's ``evidenceId`` is
    None before the player has discovered the linked evidence (the
    ``discovered``/``read`` flags are the player-safe PlayerKnowledge mirror)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = bootstrap(phase5_app, pt_id, pt_token)
    body = res.json()
    world_objects = body["scene"]["worldObjects"]
    assert len(world_objects) >= 6
    assert all(set(w.keys()) == WORLD_OBJECT_KEYS for w in world_objects)
    ids = [w["objectId"] for w in world_objects]
    assert ids == sorted(ids)
    by_id = {w["objectId"]: w for w in world_objects}
    # PD-SEC-01: no undiscovered evidence id may be exposed pre-discovery.
    assert by_id[KNIFE_OBJECT]["evidenceId"] is None
    assert by_id[KNIFE_OBJECT]["locationId"] == SCENE_LOCATION
    assert by_id[KNIFE_OBJECT]["discovered"] is False
    assert by_id[KNIFE_OBJECT]["read"] is False
    assert by_id[LAPTOP_OBJECT]["evidenceId"] is None
    assert by_id[LAPTOP_OBJECT]["interaction"] == "read"
    # ADV-222: the victim body is evidence-linked to the time-bearing
    # body_found_01 record (discoverable WHEN fact on a placed object) — but
    # the id is NOT exposed before the player interacts with the body.
    assert by_id[BODY_OBJECT]["evidenceId"] is None
    assert by_id[BODY_OBJECT]["interaction"] == "inspect"
    # Scene location from the pinned payload (never "latest").
    assert body["scene"]["location"] == {
        "locationId": SCENE_LOCATION,
        "name": SCENE_NAME,
    }
    assert_no_hidden_leaks(body)


def test_bootstrap_exposes_evidence_id_only_after_discovery(phase5_app):
    """PD-SEC-01: after a validated world interaction discovers the linked
    evidence, the evidenceId IS player-known and appears on the object (the
    same discovery that the interact endpoint returns); before that it is
    None."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    by_id = _world_objects_by_id(phase5_app, pt_id, pt_token)
    assert by_id[KNIFE_OBJECT]["evidenceId"] is None
    assert by_id[KNIFE_OBJECT]["discovered"] is False
    # Interact with the knife -> the server discovers the linked evidence.
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    assert res.json()["evidenceId"] == "forensic_knife_match_01"
    # A fresh bootstrap now carries the player-known evidenceId + flag.
    by_id = _world_objects_by_id(phase5_app, pt_id, pt_token)
    assert by_id[KNIFE_OBJECT]["evidenceId"] == "forensic_knife_match_01"
    assert by_id[KNIFE_OBJECT]["discovered"] is True


def test_unknown_object_interact_answers_404(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_id, pt_token, "ghost_object_99", "inspect")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert_sanitized_error(res.text)
    # no state change
    assert phase5_app.state.store.snapshot_player_knowledge(pt_id).visited == ()


def test_interaction_mismatch_answers_409_without_state_change(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    # The knife's published interaction is "inspect" — "read" mismatches.
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "read")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INTERACTION_NOT_ALLOWED"
    assert_sanitized_error(res.text)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_invalid_or_expired_playthrough_token_answers_401(phase5_app):
    case_id, _ = case_for(phase5_app)
    with client(phase5_app) as c:
        # Unknown token on a real playthrough id path.
        res = c.get(
            f"/api/v1/playthroughs/{case_id}/investigation",
            headers=auth("x" * 43),
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] in ("UNAUTHORIZED", "SESSION_EXPIRED")
        assert_sanitized_error(res.text)


def test_missing_pinned_version_answers_404(phase5_app):
    """A playthrough pinned to a version WITHOUT a published payload answers
    the 404 envelope (never "latest", never a crash)."""
    store = phase5_app.state.store
    clock = phase5_app.state.clock
    from app.auth.tokens import issue_playthrough_access_token, verifier as v

    now = float(clock.now())
    case_id, creator = case_for(phase5_app)
    # Seed a version 2 that is PUBLISHED in case_versions but has NO
    # published_versions row (crafted DB state), then pin a playthrough to it.
    store.create_case_version(
        case_id=case_id, version=2, state="PUBLISHED", generation_id="GEN-2", created_at=now
    )
    token = issue_playthrough_access_token()
    store.create_playthrough(
        playthrough_id="PT-MISSING-V2",
        case_id=case_id,
        case_version=2,
        token_verifier=v(token),
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    res = bootstrap(phase5_app, "PT-MISSING-V2", token)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert_sanitized_error(res.text)


# --------------------------------------------------------------------------- #
# DEF-062 — payload-driven interaction affordances (the published interaction
# is the single source: "" = decorative / NOT interactable, non-empty =
# clickable, evidence-linked placements keep their interaction).
# --------------------------------------------------------------------------- #

ENV_OBJECT_IDS = (
    "apartment_table",
    "apartment_door",
    "apartment_lamp",
    "vase_01",
)
EVIDENCE_OBJECT_INTERACTIONS = {
    "kitchen_knife": "inspect",
    "letter_opener": "inspect",
    "scissors": "inspect",
    "apartment_laptop": "read",
    # ADV-222: the victim body carries the time-bearing body_found_01 record.
    "victim_body_placeholder": "inspect",
}


def _world_objects_by_id(phase5_app, pt_id, pt_token):
    body = bootstrap(phase5_app, pt_id, pt_token).json()
    return {w["objectId"]: w for w in body["scene"]["worldObjects"]}


def test_bootstrap_carries_empty_interaction_for_env_objects(phase5_app):
    """The published WorldGraph DTO is the single affordance source: the
    decorative env objects carry interaction "" while evidence objects keep
    their published non-empty interaction."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    by_id = _world_objects_by_id(phase5_app, pt_id, pt_token)
    for object_id in ENV_OBJECT_IDS:
        assert by_id[object_id]["interaction"] == "", object_id
    for object_id, interaction in EVIDENCE_OBJECT_INTERACTIONS.items():
        assert by_id[object_id]["interaction"] == interaction, object_id


def test_decorative_env_objects_are_not_interactable(phase5_app):
    """Interacting with a decorative env object answers the safe 409
    INTERACTION_NOT_ALLOWED envelope for ANY requested interaction, with NO
    state change (nothing discovered, nothing visited)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    for object_id in ENV_OBJECT_IDS:
        for requested in ("inspect", "read", "open"):
            res = interact(phase5_app, pt_id, pt_token, object_id, requested)
            assert res.status_code == 409, (object_id, requested)
            assert res.json()["error"]["code"] == "INTERACTION_NOT_ALLOWED"
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_evidence_objects_still_interactable(phase5_app):
    """The evidence-reachable objects keep working: knife+opener+scissors
    (inspect) and the laptop (read) all interact successfully."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    assert res.json()["evidenceId"] == "forensic_knife_match_01"
    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200
    assert res.json()["evidenceId"] == "email_thomas_01"
    res = interact(phase5_app, pt_id, pt_token, "letter_opener", "inspect")
    assert res.status_code == 200
    res = interact(phase5_app, pt_id, pt_token, "scissors", "inspect")
    assert res.status_code == 200


def test_interact_decorative_env_object_never_mutates_knowledge(phase5_app):
    """A decorative-object 409 does not disturb existing knowledge: after a
    successful discovery the player's discovered/visited sets stay exact when
    a later env-object interaction is refused."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    res = interact(phase5_app, pt_id, pt_token, "apartment_table", "inspect")
    assert res.status_code == 409
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ("forensic_knife_match_01",)
    assert snap.visited == (SCENE_LOCATION,)


def test_crafted_payload_decorative_vs_nonevidence_interactable(phase5_app):
    """The payload contract holds for arbitrary crafted payloads too: a
    placement with interaction '' is 409 not-interactable; a NON-evidence
    placement with a real interaction stays interactable (result 'interacted',
    visited marked) — the branch the golden no longer exercises."""
    store = phase5_app.state.store
    clock = phase5_app.state.clock
    from app.auth.tokens import issue_playthrough_access_token, verifier as v

    now = float(clock.now())
    with client(phase5_app) as c:
        session_token, _ = create_session(c)
        res = c.post(
            "/api/v1/cases",
            json={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n"},
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert res.status_code == 201
        case_id = res.json()["caseId"]
    v1 = store.get_published(case_id, 1)
    payload = json.loads(v1.payload_json)
    payload["caseVersion"] = 99
    payload["publishedAt"] = now
    payload["draft"]["objects"] = [
        {
            "object_id": "btn_thing",
            "asset_id": "PROP_VASE_01",
            "affordances": ["INSPECTABLE"],
            "subtype": None,
        },
        {
            "object_id": "dec_thing",
            "asset_id": "PROP_LAMP_01",
            "affordances": ["INSPECTABLE"],
            "subtype": "light",
        },
    ]
    payload["draft"]["world_graph"]["placements"] = [
        {
            "object_id": "btn_thing",
            "asset_id": "PROP_VASE_01",
            "location_id": SCENE_LOCATION,
            "anchor": "desk_main",
            "interaction": "inspect",
            "evidence_id": None,
        },
        {
            "object_id": "dec_thing",
            "asset_id": "PROP_LAMP_01",
            "location_id": SCENE_LOCATION,
            "anchor": "shelf_01",
            "interaction": "",
            "evidence_id": None,
        },
    ]
    store.create_case_version(
        case_id=case_id, version=99, state="PUBLISHED",
        generation_id="GEN-99", created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=99,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )
    pt_id = f"PT-DEF062-{int(now)}"
    pt_token = issue_playthrough_access_token()
    store.create_playthrough_if_published(
        playthrough_id=pt_id, case_id=case_id, case_version=99,
        token_verifier=v(pt_token), state="PLAYING",
        created_at=now, expires_at=now + 3600,
    )

    # Decorative placement: NEVER interactable.
    res = interact(phase5_app, pt_id, pt_token, "dec_thing", "inspect")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INTERACTION_NOT_ALLOWED"
    # Non-evidence interactive placement: still works (no discovery, visited).
    res = interact(phase5_app, pt_id, pt_token, "btn_thing", "inspect")
    assert res.status_code == 200
    body = res.json()
    assert body["evidenceId"] is None
    assert body["discovery"] is None
    assert body["result"] == "interacted"
    snap = store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == (SCENE_LOCATION,)