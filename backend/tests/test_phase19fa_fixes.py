"""Phase 19F/19G adversarial-finding fixes (ADV-241, ADV-242, ADV-243).

This suite pins the three accepted backend dispositions from the Phase
19F/19G adversarial review:

- ADV-241 (HIGH, the DEF-102 blocker): ``POST .../objects/{id}/interact`` must
  accept the EMPTY interaction ``""`` — the Phase 19F decorative
  universal-inspection contract REQUIRES the browser to send the placement's
  OWN published interaction, which is ``""`` for every decorative placement
  (door/table/lamp/vase; victim when unlinked). The request schema previously
  rejected ``""`` with 422 ``string_too_short`` (``min_length=1``) BEFORE the
  service's decorative branch could run, dead-ending the browser flow on
  every decorative click. The fix allows ``""`` (``min_length=0``) while the
  SERVICE remains the interaction authority: an empty interaction is valid
  ONLY for a placement whose published interaction is ``""`` (the exact-match
  gate still answers 409 for an evidence/non-decorative placement), and
  ``null`` / numeric interactions stay 422.
- ADV-242 (MEDIUM): with a crafted DUPLICATE placement for one object where
  the FIRST entry is non-visible and the second is visible, the bootstrap
  projection showed the object but the interact gate answered 404
  (``visible_placement_for_object`` stopped at the first non-visible entry).
  The fix makes the gate skip non-visible duplicates and return the FIRST
  VISIBLE placement — byte-consistent with ``project_world_objects``, so
  VISIBLE WORLD OBJECT => INSPECTABLE holds for crafted/legacy payloads too.
  Real generations can never emit duplicate placements: the publish-time
  ``safety.validate_world_graph`` rejects ``duplicate objectId`` (DEF-050;
  pinned here).
- ADV-243 (LOW): a crafted VISIBLE placement with ``interaction: ""`` AND
  ``evidence_id`` set short-circuited at the decorative-neutral branch and
  NEVER discovered. The fix reorders ``interact_with_object`` to check the
  evidence link BEFORE the decorative-neutral return: an evidence-linked
  placement must DISCOVER regardless of the published interaction string (the
  decorative-with-evidence contradiction resolves in favor of the evidence),
  while the exact published-interaction gate stays authoritative and a truly
  decorative placement (no evidence) still answers the 200 neutral inspection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.tokens import (  # noqa: E402
    issue_playthrough_access_token,
    verifier as token_verifier,
)
from app.generation.safety import validate_world_graph  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    PlacementSpec,
    WorldGraphLocationSpec,
    WorldGraphSpec,
)
from app.services.publication import (  # noqa: E402
    project_world_objects,
    visible_placement_for_object,
)
from phase5_helpers import (  # noqa: E402
    assert_sanitized_error,
    auth,
)
from phase6_helpers import (  # noqa: E402
    EMAIL_EVIDENCE,
    LAPTOP_OBJECT,
    SCENE_LOCATION,
    case_for,
    client,
    interact,
    playthrough,
)
from test_phase7_helpers import assert_no_pre_reveal_material  # noqa: E402


# --------------------------------------------------------------------------- #
# deterministic v2 publish + v2 playthrough helpers (no provider call)
# --------------------------------------------------------------------------- #


def _publish_v2(phase5_app, case_id, mutate):
    """Insert a deterministic v2 published row whose draft was deep-copied from
    the golden v1 payload and then mutated by ``mutate(payload)``. No provider
    call; matches the established phase6 ``publish_v2_with_extra_evidence``
    and phase-19f ``_publish_v2`` patterns. The crafted payload BYPASSES the
    generation-pipeline publish-time validation (it is inserted straight into
    ``published_versions``), which is exactly the crafted/legacy surface the
    fail-closed Phase 19F gates exist for."""
    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())
    v1 = store.get_published(case_id, 1)
    assert v1 is not None
    payload = json.loads(v1.payload_json)
    payload["caseVersion"] = 2
    payload["publishedAt"] = now
    mutate(payload)
    store.create_case_version(
        case_id=case_id,
        version=2,
        state="PUBLISHED",
        generation_id="GEN-2",
        created_at=now,
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


def _v2_playthrough(phase5_app, case_id, creator, version=2):
    """A playthrough pinned to the (crafted) v2 published row."""
    store = phase5_app.state.store
    clock = phase5_app.state.clock
    now = float(clock.now())
    token = issue_playthrough_access_token()
    row = store.create_playthrough_if_published(
        playthrough_id=f"PT-19FA-{int(now)}-{case_id[-6:]}",
        case_id=case_id,
        case_version=version,
        token_verifier=token_verifier(token),
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    return row.playthrough_id, token


# --------------------------------------------------------------------------- #
# ADV-241 (HIGH) — the decorative universal-inspection contract: interaction ""
# --------------------------------------------------------------------------- #


def test_adv241_empty_interaction_is_legal_for_decorative_placements(phase5_app):
    """The EXACT payload the Phase 19F browser sends for every decorative
    placement — ``{"interaction": ""}`` — answers 200 with the NEUTRAL
    inspection instead of the 422 ``string_too_short`` dead-end.

    Before the fix this was rejected by the request schema's ``min_length=1``
    BEFORE the service's decorative branch could run, so the browser flow
    died on every door/table/lamp/vase click ("That interaction did not work.
    Please try again."). Now the empty interaction passes validation and the
    service-side decorative branch answers the Phase 19F inspection.
    """
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    for object_id, label in (
        ("apartment_door", "Apartment Door"),
        ("apartment_table", "Apartment Table"),
        ("apartment_lamp", "Apartment Lamp"),
        ("vase_01", "Vase"),
    ):
        res = interact(phase5_app, pt_id, pt_token, object_id, "")
        assert res.status_code == 200, (object_id, res.json())
        body = res.json()
        assert body["objectId"] == object_id
        assert body["interaction"] == ""  # the published decorative interaction
        assert body["evidenceId"] is None
        assert body["discovery"] is None
        assert body["result"] == "interacted"
        assert body["inspection"] == {"relevant": False, "label": label}

    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()  # neutral inspection never discovers
    assert snap.visited == (SCENE_LOCATION,)


def test_adv241_empty_interaction_on_evidence_placement_answers_409(phase5_app):
    """The service stays the interaction authority: an empty interaction is
    valid ONLY for a placement whose published interaction is "". On an
    evidence-linked non-decorative placement (published "read") it does NOT
    short-circuit to the decorative branch — the exact-match gate answers the
    existing 409 INTERACTION_NOT_ALLOWED with zero knowledge mutation."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "")
    assert res.status_code == 409, res.json()
    assert res.json()["error"]["code"] == "INTERACTION_NOT_ALLOWED"
    assert_sanitized_error(res.text)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_adv241_null_and_numeric_interactions_still_422(phase5_app):
    """The relaxed length bound does NOT weaken any other validation: the
    field stays a REQUIRED string type, so ``null`` and numeric payloads
    still answer the 422 VALIDATION_ERROR envelope (sanitized), and nothing
    is ever mutated."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    with client(phase5_app) as c:
        for evil in (None, 123, 3.5, True):
            res = c.post(
                f"/api/v1/playthroughs/{pt_id}/objects/vase_01/interact",
                json={"interaction": evil},
                headers=auth(pt_token),
            )
            assert res.status_code == 422, (evil, res.json())
            assert res.json()["error"]["code"] == "VALIDATION_ERROR"
            assert_sanitized_error(res.text)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


# --------------------------------------------------------------------------- #
# ADV-242 (MEDIUM) — duplicate placements: bootstrap shows it => interactable
# --------------------------------------------------------------------------- #


def _add_dup_placement_divergence(payload):
    """Craft the ADV-242 shape into a v2 payload:

    - ``dup_prop``: placement #1 references an UNREGISTERED asset
      (PROP_NOT_A_REAL_ASSET_99 — non-visible per ``_placement_is_visible``),
      placement #2 references a REGISTERED asset (PROP_PLANT_POT_01 —
      visible). The bootstrap projection skips #1 and SHOWS the object;
      the interact gate must resolve the SAME (visible) placement.
    - ``ghost_shadow_04``: a SINGLE non-visible placement — must stay 404.
    """
    payload["draft"]["objects"].append(
        {
            "object_id": "dup_prop",
            "asset_id": "PROP_PLANT_POT_01",
            "affordances": ["INSPECTABLE"],
            "subtype": "decor",
        }
    )
    payload["draft"]["world_graph"]["placements"].append(
        {
            "object_id": "dup_prop",
            "asset_id": "PROP_NOT_A_REAL_ASSET_99",  # non-visible (unregistered)
            "location_id": SCENE_LOCATION,
            "anchor": "hall_wall_01",
            "interaction": "inspect",
            "evidence_id": None,
        }
    )
    payload["draft"]["world_graph"]["placements"].append(
        {
            "object_id": "dup_prop",
            "asset_id": "PROP_PLANT_POT_01",  # visible (registered)
            "location_id": SCENE_LOCATION,
            "anchor": "shelf_01",
            "interaction": "inspect",
            "evidence_id": None,
        }
    )
    payload["draft"]["objects"].append(
        {
            "object_id": "ghost_shadow_04",
            "asset_id": "PROP_NOT_A_REAL_ASSET_99",
            "affordances": ["INSPECTABLE"],
            "subtype": "decor",
        }
    )
    payload["draft"]["world_graph"]["placements"].append(
        {
            "object_id": "ghost_shadow_04",
            "asset_id": "PROP_NOT_A_REAL_ASSET_99",
            "location_id": SCENE_LOCATION,
            "anchor": "floor_body_position",
            "interaction": "inspect",
            "evidence_id": None,
        }
    )


def test_adv242_bootstrap_shows_and_interact_gate_agree_on_duplicate_placement(phase5_app):
    """Crafted duplicate placement whose FIRST entry is non-visible:
    the bootstrap SHOWS the object (first VISIBLE placement wins) and the
    interact gate now accepts EXACTLY that placement (200) — the ADV-242
    divergence (bootstrap shows it, interact 404s) is gone. A single
    non-visible placement stays fail-closed 404, so no inspect-arbitrary-ID
    oracle appears."""
    case_id, creator = case_for(phase5_app)
    payload = _publish_v2(phase5_app, case_id, _add_dup_placement_divergence)
    pt_id, pt_token = _v2_playthrough(phase5_app, case_id, creator)

    # Unit level: the gate resolves the FIRST VISIBLE placement — i.e. the
    # SECOND published entry (the unregistered first one is skipped).
    gate_placement = visible_placement_for_object(payload, "dup_prop")
    assert gate_placement is not None
    assert gate_placement["asset_id"] == "PROP_PLANT_POT_01"
    assert visible_placement_for_object(payload, "ghost_shadow_04") is None

    # Bootstrap (what the player sees) emits the object exactly once and from
    # the VISIBLE placement.
    with client(phase5_app) as c:
        shown = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
        )
    assert shown.status_code == 200
    shown_by_id = {
        w["objectId"]: w for w in shown.json()["scene"]["worldObjects"]
    }
    assert "dup_prop" in shown_by_id
    assert shown_by_id["dup_prop"]["assetId"] == "PROP_PLANT_POT_01"
    assert shown_by_id["dup_prop"]["interaction"] == "inspect"

    # Interact with the placement the bootstrap shows -> 200 neutral.
    res = interact(phase5_app, pt_id, pt_token, "dup_prop", "inspect")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["evidenceId"] is None
    assert body["discovery"] is None
    assert body["inspection"]["relevant"] is False
    assert body["inspection"]["label"] == "Dup Prop"

    # Single non-visible placements stay fail-closed 404 with zero mutation.
    res = interact(phase5_app, pt_id, pt_token, "ghost_shadow_04", "inspect")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert_sanitized_error(res.text)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()


def test_adv242_publish_time_validation_forbids_duplicate_placements():
    """Note + pin: a REAL generation can never emit the ADV-242 crafted shape —
    ``safety.validate_world_graph`` (run by the generation pipeline's
    ``validate_draft`` before any publication) reports the duplicate
    objectId deterministically, so duplicate placements can never leave the
    pipeline. The crafted-payload gates are therefore pure defensive fail-
    closed depth against legacy/attacker-crafted published rows."""
    locations = (
        WorldGraphLocationSpec(
            location_id=SCENE_LOCATION, template="kitchen_template", rooms=("kitchen",)
        ),
    )
    wg = WorldGraphSpec(
        locations=locations,
        placements=(
            PlacementSpec(
                object_id="dup_prop",
                asset_id="PROP_NOT_A_REAL_ASSET_99",
                location_id=SCENE_LOCATION,
                anchor="hall_wall_01",
                interaction="inspect",
                evidence_id=None,
            ),
            PlacementSpec(
                object_id="dup_prop",
                asset_id="PROP_PLANT_POT_01",
                location_id=SCENE_LOCATION,
                anchor="shelf_01",
                interaction="inspect",
                evidence_id=None,
            ),
        ),
    )
    issues = validate_world_graph(wg, {"dup_prop"}, set())
    duplicates = [i for i in issues if "duplicate objectId" in i]
    assert len(duplicates) == 1
    assert "placements[1]: duplicate objectId 'dup_prop'" in duplicates[0]


# --------------------------------------------------------------------------- #
# ADV-243 (LOW) — evidence-linked decorative placement must DISCOVER
# --------------------------------------------------------------------------- #


def _add_evidence_linked_decorative_urn(payload):
    """Craft the ADV-243 shape into a v2 payload: `secret_urn` whose VISIBLE
    placement carries ``interaction: ""`` (decorative-published) AND
    ``evidence_id: "urn_evidence_01"`` — the contradiction the service must
    resolve in favor of the evidence."""
    payload["draft"]["evidence"].append(
        {
            "id": "urn_evidence_01",
            "kind": "document",
            "discoverable": True,
            "reliability": "high",
            "source_ref": {"kind": "record", "sourceId": "record_urn_evidence_01"},
            "propositions": [],
            "presentation": {
                "title": "Sealed urn note",
                "description": "A handwritten note hidden inside the urn.",
            },
        }
    )
    payload["draft"]["objects"].append(
        {
            "object_id": "secret_urn",
            "asset_id": "PROP_VASE_01",
            "affordances": ["INSPECTABLE"],
            "subtype": "decor",
        }
    )
    payload["draft"]["world_graph"]["placements"].append(
        {
            "object_id": "secret_urn",
            "asset_id": "PROP_VASE_01",
            "location_id": SCENE_LOCATION,
            "anchor": "dining_table",
            "interaction": "",
            "evidence_id": "urn_evidence_01",
        }
    )


def test_adv243_evidence_linked_with_empty_interaction_discovers(phase5_app):
    """Crafted ``interaction: ""`` + ``evidence_id``: the evidence link WINS —
    interacting with the object's own published "" runs the discovery flow
    (200, discovery DTO state "discovered", inspection.relevant true) and
    updates PlayerKnowledge. Before the branch-order fix the decorative-
    neutral return short-circuited first and the linked record was silently
    undiscoverable through the world."""
    case_id, creator = case_for(phase5_app)
    _publish_v2(phase5_app, case_id, _add_evidence_linked_decorative_urn)
    pt_id, pt_token = _v2_playthrough(phase5_app, case_id, creator)

    res = interact(phase5_app, pt_id, pt_token, "secret_urn", "")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["evidenceId"] == "urn_evidence_01"
    assert body["discovery"]["evidenceId"] == "urn_evidence_01"
    assert body["discovery"]["state"] == "discovered"
    assert body["discovery"]["kind"] == "document"
    assert body["inspection"]["relevant"] is True
    assert body["inspection"]["label"] == "Secret Urn"
    assert_no_pre_reveal_material(body, canonical_time="2026-09-11T22:17:00+02:00")
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ("urn_evidence_01",)

    # Idempotent on repeat.
    again = interact(phase5_app, pt_id, pt_token, "secret_urn", "")
    assert again.status_code == 200
    assert again.json()["discovery"]["state"] == "already-discovered"
    assert phase5_app.state.store.snapshot_player_knowledge(
        pt_id
    ).discovered == ("urn_evidence_01",)


def test_adv243_evidence_gate_stays_fail_closed_on_non_matching_interaction(phase5_app):
    """The reordered branch does NOT invent a discovery path: an evidence-
    linked placement is discovered ONLY on its exact published interaction
    ("" for the crafted decorative-with-evidence shape). ANY other requested
    interaction answers the existing 409 INTERACTION_NOT_ALLOWED with zero
    knowledge mutation — the 19F-3 "no branch yields discovery without a real
    published match" invariant is intact."""
    case_id, creator = case_for(phase5_app)
    _publish_v2(phase5_app, case_id, _add_evidence_linked_decorative_urn)
    pt_id, pt_token = _v2_playthrough(phase5_app, case_id, creator)

    for evil in ("inspect", "garbage_probe", "read"):
        res = interact(phase5_app, pt_id, pt_token, "secret_urn", evil)
        assert res.status_code == 409, (evil, res.json())
        assert res.json()["error"]["code"] == "INTERACTION_NOT_ALLOWED"
        assert_sanitized_error(res.text)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_adv243_truly_decorative_placement_stays_neutral_200(phase5_app):
    """A truly decorative placement (published "" AND no evidence link) is
    UNCHANGED after the branch reorder: any requested interaction — including
    the now-legal "" — answers the 200 NEUTRAL inspection and never touches
    PlayerKnowledge.discovered."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    for requested in ("", "inspect", "garbage_probe"):
        res = interact(phase5_app, pt_id, pt_token, "vase_01", requested)
        assert res.status_code == 200, (requested, res.json())
        body = res.json()
        assert body["evidenceId"] is None
        assert body["discovery"] is None
        assert body["inspection"] == {"relevant": False, "label": "Vase"}
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == (SCENE_LOCATION,)


__all__ = []  # pytest module: no accidental public names