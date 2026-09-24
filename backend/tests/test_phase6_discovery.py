"""Phase 6 — evidence discovery (REQUIREMENTS 40.8, Phase6 B/I; Phase6 O items
5/6/9/10/11/12/14/18 + visited locations).

Phase 20 (PD-SEC-01) contract — the DIRECT client-facing route
``POST /evidence/{evidence_id}/discover`` has been REMOVED: a player can
never discover evidence by id without first performing a validated world
interaction. All discovery semantics below now run through the object
interact endpoint (``POST /objects/{object_id}/interact``), and the removed
route is asserted to answer 404 with zero state change.

- O5  valid discovery succeeds through object interaction (knife/laptop)
- O6  duplicate interaction is idempotent (state "already-discovered", one
      set entry)
- O9  the removed direct route rejects EVERY evidence id (404, no leak, no
      state change); a fabricated evidence id can never be reached via any
      object interaction either
- O10 evidence from ANOTHER CaseVersion rejected (a v1 playthrough has no
      placement linking the v2-only object -> 404; the same id IS
      discoverable on a v2 playthrough through the v2 object interaction)
- O11 v1 playthrough continues using v1 evidence after v2 publish
- O12 client cannot discover non-reachable evidence (an unlinked fact has NO
      placement, so no object interaction can ever return it; an unrelated /
      decorative object + wrong interaction -> 409)
- O14 discovered DTO contains only the allowed fields (exact key set)
- O18 concurrent duplicate interaction stays consistent (two threads -> one
      set entry, both 200)
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


def test_5_direct_discovery_route_is_removed(phase5_app):
    """PD-SEC-01: the direct client-facing discovery route no longer exists.
    A POST to it answers 404 (generic NOT_FOUND envelope, sanitized) for ANY
    evidence id — reachable included — and mutates NOTHING. Discovery is only
    possible through a validated world interaction (tested below)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    for evidence_id in (KNIFE_EVIDENCE, "ghost_evidence_99", "murdererId"):
        res = discover(phase5_app, pt_id, pt_token, evidence_id)
        assert res.status_code == 404, (evidence_id, res.json())
        assert res.json()["error"]["code"] == "NOT_FOUND"
        assert_sanitized_error(res.text)
    # Nothing was learned or marked by any direct-discover attempt.
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_5b_valid_discover_via_object_interact(phase5_app):
    """The SAME discovery logic runs through the object interact endpoint."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {
        "objectId",
        "interaction",
        "evidenceId",
        "discovery",
        "result",
        "inspection",  # Phase 19F additive: {relevant: true, label}
    }
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
    first = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert first.status_code == 200
    assert first.json()["discovery"]["state"] == "discovered"
    second = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert second.status_code == 200
    # The ONLY difference is the state label: idempotent, single set entry.
    assert second.json()["discovery"]["state"] == "already-discovered"
    same = {k: v for k, v in first.json()["discovery"].items() if k != "state"}
    assert {k: v for k, v in second.json()["discovery"].items() if k != "state"} == same
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (KNIFE_EVIDENCE,)


def test_9_fabricated_evidence_id_never_reachable(phase5_app):
    """O9: a fabricated evidence id is not reachable through any object
    interaction (the removed direct route already answers 404 for every id —
    covered by test_5). An interaction whose placement links a REAL evidence
    id can never be made to return a fabricated id."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    # Interacting with an unknown object (which could only ever carry a
    # fabricated evidence link) answers 404 with zero state change.
    res = interact(phase5_app, pt_id, pt_token, "ghost_object_99", "inspect")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert_sanitized_error(res.text)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_10_evidence_from_another_caseversion_rejected(phase5_app):
    """O10: a v1 playthrough MUST NOT reach the v2-only evidence — the v2-only
    OBJECT is not part of the v1 pinned world, so interacting with it answers
    the generic 404 (never revealed, never learned); the same id IS
    discoverable by a v2 playthrough through its OWN version's object."""
    case_id, creator = case_for(phase5_app)
    pt_v1, token_v1 = playthrough(phase5_app, case_id, creator)
    publish_v2_with_extra_evidence(phase5_app, case_id, V2_ONLY_EVIDENCE)

    # v1 playthrough: the v2-only object does not exist in the v1 pinned world
    # -> generic 404 with zero state change.
    res = interact(phase5_app, pt_v1, token_v1, "v2_object", "inspect")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_v1)
    assert V2_ONLY_EVIDENCE not in snap.discovered

    # A v2 playthrough CAN discover its own version's id through its object
    # (proves the 404 on v1 is a version-pin decision, not a global ban).
    pt_v2, token_v2 = _v2_playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_v2, token_v2, "v2_object", "inspect")
    assert res.status_code == 200
    assert res.json()["evidenceId"] == V2_ONLY_EVIDENCE


def test_11_v1_evidence_still_discoverable_and_readable_after_v2(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    publish_v2_with_extra_evidence(phase5_app, case_id)
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    assert res.json()["discovery"]["state"] == "discovered"


def test_12_client_cannot_discover_non_reachable_evidence(phase5_app):
    """O12a: an UNLINKED evidence fact has NO placement, so NO object
    interaction in the world can ever return it (the removed direct route
    already 404s every id). O12b: the vase is a player-VISIBLE semantic
    object (Phase 19F) so it returns the neutral INSPECTION 200 for ANY
    requested interaction — but it carries NO evidence association, so it can
    NEVER discover anything (no leak, no fabricated evidence)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    # The vase is DECORATIVE (published interaction "") yet VISIBLE — any
    # interaction is a neutral inspection, never a discovery.
    for requested in ("read", "inspect"):
        res = interact(phase5_app, pt_id, pt_token, "vase_01", requested)
        assert res.status_code == 200, (requested, res.json())
        body = res.json()
        assert body["evidenceId"] is None
        assert body["discovery"] is None
        assert body["inspection"]["relevant"] is False
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    # Evidence objects remain interactable: the knife's inspect discovers it.
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    assert res.json()["evidenceId"] == KNIFE_EVIDENCE
    # No client can claim an arbitrary id as discovered — the discovered id is
    # exactly the placement-linked one, never the unlinked fact.
    assert res.json()["evidenceId"] != UNLINKED_EVIDENCE


def test_14_discovery_dto_exact_key_set(phase5_app):
    """O14: only the five documented discovery fields; nothing else at any
    depth (the discovery block of the interact response)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    for object_id, interaction in (
        (KNIFE_OBJECT, "inspect"),
        (LAPTOP_OBJECT, "read"),
    ):
        res = interact(phase5_app, pt_id, pt_token, object_id, interaction)
        assert res.status_code == 200
        discovery = res.json()["discovery"]
        assert set(discovery.keys()) == DISCOVERY_KEYS
        assert_no_hidden_leaks(discovery)


def test_18_concurrent_duplicate_discovery_consistent(phase5_app):
    """O18: two threads interact with the SAME object. Both requests must 200
    and the discovered set must contain EXACTLY one entry."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    results: list[tuple[int, dict]] = []
    errors: list[BaseException] = []

    def _interact_knife():
        try:
            with client(phase5_app) as c:
                res = c.post(
                    f"/api/v1/playthroughs/{pt_id}/objects/{KNIFE_OBJECT}/interact",
                    json={"interaction": "inspect"},
                    headers=auth(pt_token),
                )
                results.append((res.status_code, res.json()))
        except BaseException as exc:  # noqa: BLE001 - recorded for assert
            errors.append(exc)

    threads = [threading.Thread(target=_interact_knife) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(results) == 2
    for status, body in results:
        assert status == 200, body
        assert body["evidenceId"] == KNIFE_EVIDENCE
        assert body["discovery"]["state"] in ("discovered", "already-discovered")
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (KNIFE_EVIDENCE,)


def test_visited_locations_only_via_valid_interactions(phase5_app):
    """visitedLocationIds grow ONLY through valid interactions: failed
    interactions (409/404) add NOTHING, while every SUCCESSFUL interaction —
    evidence discovery AND the Phase 19F neutral inspection of a visible
    semantic object — marks the location visited."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.visited == ()
    # Failed attempts add nothing: interaction mismatch 409, unknown object
    # 404 and the removed direct-discover route (404).
    interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "read")  # 409 mismatch
    interact(phase5_app, pt_id, pt_token, "ghost_object_99", "inspect")  # 404
    discover(phase5_app, pt_id, pt_token, "EV-FAKE")  # removed route -> 404
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.visited == ()
    # A neutral inspection of a visible semantic object (vase, decorative) is
    # a SUCCESSFUL interaction (Phase 19F) -> the location is visited.
    res = interact(phase5_app, pt_id, pt_token, "vase_01", "inspect")
    assert res.status_code == 200
    assert res.json()["inspection"]["relevant"] is False
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.visited == (SCENE_LOCATION,)
    # A successful evidence discovery marks the location visited (already set).
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.visited == (SCENE_LOCATION,)