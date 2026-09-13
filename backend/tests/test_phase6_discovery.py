"""Phase 6 — evidence discovery (REQUIREMENTS 40.8, Phase6 B/I; Phase6 O items
5/6/9/10/11/12/14/18 + visited locations).

- O5  valid discover succeeds (placement-reachable endpoint AND object interact)
- O6  duplicate discover is idempotent (state "already-discovered", one entry)
- O9  fabricated evidence id rejected (404, no leak, no state change)
- O10 evidence from ANOTHER CaseVersion rejected (v1 pt + v2-only id -> 404;
      the same id IS discoverable on a v2 playthrough)
- O11 v1 playthrough continues using v1 evidence after v2 publish
- O12 client cannot discover non-reachable evidence (unlinked fact -> 404;
      unrelated object + wrong interaction -> 409)
- O14 discovered DTO contains only the allowed fields (exact key set)
- O18 concurrent duplicate discovery stays consistent (two threads -> one set
      entry, both 200)
- visitedLocationIds are populated ONLY through valid interactions
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_helpers import (
    assert_no_hidden_leaks,
    assert_sanitized_error,
    auth,
)
from phase6_helpers import (
    EMAIL_EVIDENCE,
    KNIFE_EVIDENCE,
    KNIFE_OBJECT,
    LAPTOP_OBJECT,
    SCENE_LOCATION,
    UNLINKED_EVIDENCE,
    V2_ONLY_EVIDENCE,
    case_for,
    client,
    discover,
    interact,
    playthrough,
    publish_v2_with_extra_evidence,
)

DISCOVERY_KEYS = {"evidenceId", "kind", "title", "interaction", "state"}


def _v2_playthrough(phase5_app, case_id, creator):
    """Create a playthrough pinned to the v2 published row."""
    from app.auth.tokens import issue_playthrough_access_token, verifier as v

    store = phase5_app.state.store
    clock = phase5_app.state.clock
    now = float(clock.now())
    token = issue_playthrough_access_token()
    row = store.create_playthrough_if_published(
        playthrough_id=f"PT-V2-{int(now)}-{case_id[-6:]}",
        case_id=case_id,
        case_version=2,
        token_verifier=v(token),
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    return row.playthrough_id, token


def test_5_valid_discover_via_placement_reachable_endpoint(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = discover(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == DISCOVERY_KEYS
    assert body["evidenceId"] == KNIFE_EVIDENCE
    assert body["kind"] == "forensic"
    assert body["title"] == "Blood on the kitchen knife matches the victim"
    assert body["interaction"] == "inspect"
    assert body["state"] == "discovered"
    assert_no_hidden_leaks(body)
    # The placement's location is now visited.
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (KNIFE_EVIDENCE,)
    assert snap.visited == (SCENE_LOCATION,)


def test_5b_valid_discover_via_object_interact(phase5_app):
    """The same discovery logic runs through the object interact endpoint."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"objectId", "interaction", "evidenceId", "discovery", "result"}
    assert body["objectId"] == KNIFE_OBJECT
    assert body["interaction"] == "inspect"
    assert body["evidenceId"] == KNIFE_EVIDENCE
    assert body["result"] == "interacted"
    assert set(body["discovery"].keys()) == DISCOVERY_KEYS
    assert body["discovery"]["evidenceId"] == KNIFE_EVIDENCE
    assert body["discovery"]["state"] == "discovered"
    # Laptop interaction discovers the email record.
    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200
    assert res.json()["discovery"]["evidenceId"] == EMAIL_EVIDENCE
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert set(snap.discovered) == {KNIFE_EVIDENCE, EMAIL_EVIDENCE}
    assert set(snap.visited) == {SCENE_LOCATION}


def test_6_duplicate_discover_is_idempotent(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    first = discover(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert first.status_code == 200
    assert first.json()["state"] == "discovered"
    second = discover(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert second.status_code == 200
    # The ONLY difference is the state label: idempotent, single set entry.
    assert second.json()["state"] == "already-discovered"
    assert {k: v for k, v in second.json().items() if k != "state"} == {
        k: v for k, v in first.json().items() if k != "state"
    }
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (KNIFE_EVIDENCE,)


def test_9_fabricated_evidence_id_rejected(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    for fabricated in ("EV-FAKE-999", "email_thomas_02", "", ".." * 20):
        res = discover(phase5_app, pt_id, pt_token, fabricated)
        assert res.status_code == 404, fabricated
        assert res.json()["error"]["code"] == "NOT_FOUND"
        assert_sanitized_error(res.text)
    # Nothing was learned or marked by fabricated requests.
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_10_evidence_from_another_caseversion_rejected(phase5_app):
    """O10: a v1 playthrough MUST NOT discover a v2-only evidence id (404);
    the same id IS discoverable by a v2 playthrough."""
    case_id, creator = case_for(phase5_app)
    pt_v1, token_v1 = playthrough(phase5_app, case_id, creator)
    publish_v2_with_extra_evidence(phase5_app, case_id, V2_ONLY_EVIDENCE)

    # v1 playthrough -> 404 for the v2-only id (never revealed).
    res = discover(phase5_app, pt_v1, token_v1, V2_ONLY_EVIDENCE)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_v1)
    assert V2_ONLY_EVIDENCE not in snap.discovered

    # A v2 playthrough CAN discover its own version's id (proves the 404 on v1
    # is a version-pin decision, not a globally-fabricated id).
    pt_v2, token_v2 = _v2_playthrough(phase5_app, case_id, creator)
    res = discover(phase5_app, pt_v2, token_v2, V2_ONLY_EVIDENCE)
    assert res.status_code == 200
    assert res.json()["evidenceId"] == V2_ONLY_EVIDENCE


def test_11_v1_evidence_still_discoverable_and_readable_after_v2(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    publish_v2_with_extra_evidence(phase5_app, case_id)
    res = discover(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert res.status_code == 200
    assert res.json()["state"] == "discovered"


def test_12_client_cannot_discover_non_reachable_evidence(phase5_app):
    """O12a: a VALID evidence id that no placement links cannot be discovered
    (404, no leak). O12b: an interaction on an unrelated object with the wrong
    interaction answers 409 with no state change."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    # cctv_michael_office_01 exists in the evidence set but NO placement links it.
    res = discover(phase5_app, pt_id, pt_token, UNLINKED_EVIDENCE)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    # The vase has no evidence; interacting with the WRONG interaction fails.
    res = interact(phase5_app, pt_id, pt_token, "vase_01", "read")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INTERACTION_NOT_ALLOWED"
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()
    # But the SAME object with the CORRECT interaction works (no evidence id).
    res = interact(phase5_app, pt_id, pt_token, "vase_01", "inspect")
    assert res.status_code == 200
    assert res.json()["evidenceId"] is None
    assert res.json()["discovery"] is None
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    # No client can claim an arbitrary id as discovered.
    assert res.json()["evidenceId"] != UNLINKED_EVIDENCE


def test_14_discovery_dto_exact_key_set(phase5_app):
    """O14: only the five documented fields; nothing else at any depth."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    for evidence_id, interaction in (
        (KNIFE_EVIDENCE, "inspect"),
        (EMAIL_EVIDENCE, "read"),
    ):
        res = discover(phase5_app, pt_id, pt_token, evidence_id)
        assert res.status_code == 200
        body = res.json()
        assert set(body.keys()) == DISCOVERY_KEYS
        assert_no_hidden_leaks(body)


def test_18_concurrent_duplicate_discovery_consistent(phase5_app):
    """O18: two threads discover the same evidence. Both requests must 200 and
    the discovered set must contain EXACTLY one entry."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    results: list[tuple[int, dict]] = []
    errors: list[BaseException] = []

    def _discover():
        try:
            with client(phase5_app) as c:
                res = c.post(
                    f"/api/v1/playthroughs/{pt_id}/evidence/{KNIFE_EVIDENCE}/discover",
                    headers=auth(pt_token),
                )
                results.append((res.status_code, res.json()))
        except BaseException as exc:  # noqa: BLE001 - recorded for assert
            errors.append(exc)

    threads = [threading.Thread(target=_discover) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(results) == 2
    for status, body in results:
        assert status == 200, body
        assert body["evidenceId"] == KNIFE_EVIDENCE
        assert body["state"] in ("discovered", "already-discovered")
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (KNIFE_EVIDENCE,)


def test_visited_locations_only_via_valid_interactions(phase5_app):
    """visitedLocationIds grow ONLY through valid interactions: failed
    interactions (409) and failed discoveries (404) add nothing."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.visited == ()
    # Failed attempts add nothing.
    interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "read")  # 409
    discover(phase5_app, pt_id, pt_token, "EV-FAKE")  # 404
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.visited == ()
    # A successful interaction (no evidence) marks the location visited.
    res = interact(phase5_app, pt_id, pt_token, "vase_01", "inspect")
    assert res.status_code == 200
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.visited == (SCENE_LOCATION,)