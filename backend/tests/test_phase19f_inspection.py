"""Phase 19F — UNIVERSAL OBJECT INSPECTION (backend half) regression suite.

Product rule (Phase19F.md): every player-visible SEMANTIC WORLD OBJECT must be
inspectable. Inspectability is derived from PUBLICATION SEMANTICS (a published
placement with a player-visible representation is inspectable by default),
NOT from an interactive role and NOT from manual per-catalog entries.
Evidence-relevance is a separate concept: a non-evidence object must NEVER
require evidence / CaseTruth relevance / candidate-universe membership to be
inspectable.

Backend interaction contract (this suite's pins):

1. NON-EVIDENCE published semantic object (published interaction "" OR a real
   interaction with no evidence link):
   POST /objects/{id}/interact -> 200
   {
     "objectId": "...",
     "interaction": "<published interaction>",
     "evidenceId": null,
     "discovery": null,
     "result": "interacted",
     "inspection": {"relevant": false, "label": "<humanized semantic label>"}
   }
   NO evidence invented, NO solver state touched, PlayerKnowledge only gains
   the visited marking (no discovered add).
2. EVIDENCE-LINKED object -> discovery flow unchanged:
   200 with evidenceId + discovery {evidenceId, kind, title, interaction,
   state} + inspection.relevant == true + label; repeated -> idempotent
   "already-discovered".
3. VICTIM: evidence-linked when a body record is legitimately linked (golden:
   body_found_01) -> discovery; otherwise -> neutral inspection. NEVER exposes
   CaseTruth/hidden solver output merely because the victim is inspected.
4. FAIL-CLOSED (no inspect-arbitrary-ID oracle):
   - object not in the pinned visible placements -> 404 generic;
   - unpublished/malformed id -> 404/422 generic;
   - a placement with NO player-visible representation (unknown object /
     unregistered or unprojectable asset / dangling evidence ref) -> 404;
   - invalid token / cross-playthrough -> existing auth (401/404) unchangeed.
5. label = the SAME semantic-label path the public model uses
   (``weapon_label_of`` over the semantic object id) — humanized, never the
   raw object_id and never a proc./render id.
6. Solver isolation: adding 20 decorative inspectable object REQUESTS to a
   generated world must produce the SAME solver result + candidate universe
   as the equivalent decoration-free world.
7. Security: no new leak surface — bootstrap carries no undiscovered evidence
   metadata and the inspection DTO adds only {relevant, label}.
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
from app.domain.eligibility import derive_universes  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.publication import (  # noqa: E402
    project_world_objects,
    visible_placement_for_object,
)
from phase5_helpers import (  # noqa: E402
    assert_no_hidden_leaks,
    assert_sanitized_error,
    auth,
)
from phase6_helpers import (  # noqa: E402
    BODY_OBJECT,
    EMAIL_EVIDENCE,
    KNIFE_EVIDENCE,
    KNIFE_OBJECT,
    LAPTOP_OBJECT,
    SCENE_LOCATION,
    case_for,
    client,
    interact,
    playthrough,
)
from test_ollama_driver import _case_people, _evidence, _j, _run, _alog_posts  # noqa: E402
from test_phase19e_semantic_pipeline import _weapon_prompt, _named_spec  # noqa: E402
from test_phase7_helpers import assert_no_pre_reveal_material  # noqa: E402


# --------------------------------------------------------------------------- #
# deterministic v2 publish + v2 playthrough helpers (no provider call)
# --------------------------------------------------------------------------- #


def _publish_v2(phase5_app, case_id, mutate):
    """Insert a deterministic v2 published row whose draft was deep-copied from
    the golden v1 payload and then mutated by ``mutate(payload)``. No provider
    call; matches the established phase6 ``publish_v2_with_extra_evidence``
    pattern."""
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
        playthrough_id=f"PT-19F-{int(now)}-{case_id[-6:]}",
        case_id=case_id,
        case_version=version,
        token_verifier=token_verifier(token),
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    return row.playthrough_id, token


def _procedural_fork_definition():
    """A valid CURRENT-version procedural definition for a fork render asset.

    Compiled through the real compiler (same path the Phase 19E driver uses),
    so ``validate_embedded_definition`` accepts it and the placement is
    player-visible (Phase 19F: procedural objects become inspectable after
    valid publication — no interaction whitelist is involved).
    """
    from app.assets.compiler import compile_asset_spec
    from app.assets.specs import parse_asset_spec
    from test_phase19e_semantic_pipeline import FORk_SPEC

    spec = parse_asset_spec(FORk_SPEC, non_throwing=False)
    return compile_asset_spec(spec).to_definition_json()


# --------------------------------------------------------------------------- #
# 1 — non-evidence structural/decorative objects: NEUTRAL 200 inspection
# --------------------------------------------------------------------------- #


def test_door_table_lamp_vase_neutral_inspection_golden(phase5_app):
    """The golden visible SEMANTIC structural/decorative objects (door, table,
    lamp, vase; published interaction "") return 200 with the NEUTRAL
    inspection {relevant:false, label}, evidenceId null, discovery null —
    no evidence invented, no solver state touched. (Phase 19F supersedes the
    DEF-062 409 for visible semantic placements.)"""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    for object_id, label in (
        ("apartment_door", "Apartment Door"),
        ("apartment_table", "Apartment Table"),
        ("apartment_lamp", "Apartment Lamp"),
        ("vase_01", "Vase"),
    ):
        res = interact(phase5_app, pt_id, pt_token, object_id, "inspect")
        assert res.status_code == 200, (object_id, res.json())
        body = res.json()
        assert body["objectId"] == object_id
        assert body["interaction"] == ""  # published decorative interaction
        assert body["evidenceId"] is None
        assert body["discovery"] is None
        assert body["result"] == "interacted"
        assert body["inspection"] == {"relevant": False, "label": label}
        assert set(body) == {
            "objectId",
            "interaction",
            "evidenceId",
            "discovery",
            "result",
            "inspection",
        }

    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()  # no discovered add
    assert snap.visited == (SCENE_LOCATION,)

    # Evidence-relevance and inspectability are SEPARATE (Phase19F.md core
    # invariant): these decorative objects are inspectable although NONE of
    # them is a candidate-universe member.
    payload = json.loads(
        phase5_app.state.store.get_published(case_id, 1).payload_json
    )
    universes = payload.get("universes") or {}
    candidate_ids = {
        str(i)
        for list_key in ("suspect_ids", "motive_ids", "weapon_ids")
        for i in (universes.get(list_key) or ())
    }
    for object_id in ("apartment_door", "apartment_table", "apartment_lamp", "vase_01"):
        assert object_id not in candidate_ids, object_id


def test_crafted_plant_is_neutral_inspectable(phase5_app):
    """A decorative object (plant) published in a v2 world is inspectable by
    default with a neutral inspection — no interaction whitelist entry
    anywhere (Phase 19E procedural-object compatibility)."""
    case_id, creator = case_for(phase5_app)

    def _add_plant(payload):
        payload["draft"]["objects"].append(
            {
                "object_id": "plant_01",
                "asset_id": "PROP_PLANT_POT_01",
                "affordances": ["INSPECTABLE"],
                "subtype": "plant",
            }
        )
        payload["draft"]["world_graph"]["placements"].append(
            {
                "object_id": "plant_01",
                "asset_id": "PROP_PLANT_POT_01",
                "location_id": SCENE_LOCATION,
                "anchor": "hall_wall_01",
                "interaction": "",
                "evidence_id": None,
            }
        )

    _publish_v2(phase5_app, case_id, _add_plant)
    pt_id, pt_token = _v2_playthrough(phase5_app, case_id, creator)

    res = interact(phase5_app, pt_id, pt_token, "plant_01", "inspect")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["evidenceId"] is None
    assert body["discovery"] is None
    assert body["inspection"] == {"relevant": False, "label": "Plant"}
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()


# --------------------------------------------------------------------------- #
# 2 — evidence-linked objects: discovery flow UNCHANGED
# --------------------------------------------------------------------------- #


def test_evidence_linked_objects_discovery_unchanged(phase5_app):
    """laptop / kitchen knife / letter opener / scissors (golden evidence
    links) keep the discovery flow byte-identical: discovery {evidenceId,
    kind, title, state} + evidenceId set, inspection.relevant true + label."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    expected = (
        (LAPTOP_OBJECT, "read", EMAIL_EVIDENCE, "email", "Apartment Laptop"),
        (KNIFE_OBJECT, "inspect", KNIFE_EVIDENCE, "forensic", "Kitchen Knife"),
        ("letter_opener", "inspect", "forensic_letter_opener_01", "forensic", "Letter Opener"),
        ("scissors", "inspect", "forensic_scissors_01", "forensic", "Scissors"),
    )
    for object_id, interaction, evidence_id, kind, label in expected:
        res = interact(phase5_app, pt_id, pt_token, object_id, interaction)
        assert res.status_code == 200, (object_id, res.json())
        body = res.json()
        assert body["evidenceId"] == evidence_id
        assert body["discovery"]["evidenceId"] == evidence_id
        assert body["discovery"]["kind"] == kind
        assert body["discovery"]["state"] == "discovered"
        assert body["inspection"]["relevant"] is True
        assert body["inspection"]["label"] == label
        assert_no_pre_reveal_material(
            body, canonical_time="2026-09-11T22:17:00+02:00"
        )
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert set(snap.discovered) == {
        EMAIL_EVIDENCE,
        KNIFE_EVIDENCE,
        "forensic_letter_opener_01",
        "forensic_scissors_01",
    }


def test_evidence_linked_repeated_interact_idempotent(phase5_app):
    """Repeated interaction on an evidence-linked object is idempotent
    (state "already-discovered", single discovered set entry)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    first = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert first.status_code == 200
    assert first.json()["discovery"]["state"] == "discovered"
    second = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert second.status_code == 200
    body = second.json()
    assert body["discovery"]["state"] == "already-discovered"
    assert body["inspection"] == {"relevant": True, "label": "Apartment Laptop"}
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (EMAIL_EVIDENCE,)


def test_crafted_procedural_fork_evidence_linked_discovery(phase5_app):
    """A Phase-19E procedural fork becomes a normal evidence-linked object
    after valid publication: interacting discovers its linked record
    (fork_trace_01) with the same discovery contract, idempotent on repeat.
    The render asset is a proc.* id — it NEVER appears in the label."""
    case_id, creator = case_for(phase5_app)
    definition = _procedural_fork_definition()
    fork_asset_id = definition["assetId"]
    assert fork_asset_id.startswith("proc.")

    def _add_fork(payload):
        payload["draft"]["objects"].append(
            {
                "object_id": "dining_fork",
                "asset_id": fork_asset_id,
                "affordances": ["INSPECTABLE", "POTENTIAL_WEAPON"],
                "subtype": "fork",
            }
        )
        payload["draft"]["evidence"].append(
            {
                "id": "fork_trace_01",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "source_ref": {"kind": "forensic", "sourceId": "record_fork_trace_01"},
                "propositions": [],
                "presentation": {
                    "title": "Fork fingerprint",
                    "description": "A partial fingerprint on the fork.",
                },
            }
        )
        payload["draft"]["world_graph"]["placements"].append(
            {
                "object_id": "dining_fork",
                "asset_id": fork_asset_id,
                "location_id": SCENE_LOCATION,
                "anchor": "dining_table",
                "interaction": "inspect",
                "evidence_id": "fork_trace_01",
                "generated_definition": definition,
            }
        )

    _publish_v2(phase5_app, case_id, _add_fork)
    pt_id, pt_token = _v2_playthrough(phase5_app, case_id, creator)

    res = interact(phase5_app, pt_id, pt_token, "dining_fork", "inspect")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["evidenceId"] == "fork_trace_01"
    assert body["discovery"]["evidenceId"] == "fork_trace_01"
    assert body["discovery"]["state"] == "discovered"
    assert body["inspection"]["relevant"] is True
    assert body["inspection"]["label"] == "Dining Fork"
    # the procedural render id NEVER leaks into the player DTO.
    assert fork_asset_id not in json.dumps(body)

    again = interact(phase5_app, pt_id, pt_token, "dining_fork", "inspect")
    assert again.status_code == 200
    assert again.json()["discovery"]["state"] == "already-discovered"
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ("fork_trace_01",)


# --------------------------------------------------------------------------- #
# 3 — the victim
# --------------------------------------------------------------------------- #


def test_victim_golden_is_evidence_linked_discovery(phase5_app):
    """The golden victim body IS evidence-linked (body_found_01, ADV-222):
    inspecting the victim runs the normal discovery flow and never leaks
    CaseTruth (the pre-reveal + hidden-leak scans stay green)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    res = interact(phase5_app, pt_id, pt_token, BODY_OBJECT, "inspect")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["evidenceId"] == "body_found_01"
    assert body["discovery"]["evidenceId"] == "body_found_01"
    assert body["discovery"]["kind"] == "witness_observation"
    assert body["inspection"]["relevant"] is True
    assert body["inspection"]["label"] == "Victim Body Placeholder"
    assert_no_pre_reveal_material(body, canonical_time="2026-09-11T22:17:00+02:00")
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ("body_found_01",)
    # no hidden truth/proof material reaches the DTO at ANY depth.
    assert "murderer" not in json.dumps(body)
    assert "truth" not in json.dumps(body)
    assert "solver" not in json.dumps(body)


def test_victim_unlinked_is_safe_neutral_observation(phase5_app):
    """When the victim body has NO linked body record, inspecting the victim
    returns the player-safe observation inspection (relevant false) — a
    player can never read CaseTruth/hidden solver output merely by inspecting
    the victim."""
    case_id, creator = case_for(phase5_app)

    def _delink_victim(payload):
        for placement in payload["draft"]["world_graph"]["placements"]:
            if placement["object_id"] == BODY_OBJECT:
                placement["interaction"] = ""
                placement["evidence_id"] = None

    _publish_v2(phase5_app, case_id, _delink_victim)
    pt_id, pt_token = _v2_playthrough(phase5_app, case_id, creator)

    res = interact(phase5_app, pt_id, pt_token, BODY_OBJECT, "inspect")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["evidenceId"] is None
    assert body["discovery"] is None
    assert body["inspection"] == {
        "relevant": False,
        "label": "Victim Body Placeholder",
    }
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert_no_pre_reveal_material(body, canonical_time="2026-09-11T22:17:00+02:00")


# --------------------------------------------------------------------------- #
# 4 — fail-closed (no inspect-arbitrary-ID oracle)
# --------------------------------------------------------------------------- #


def test_cross_playthrough_token_answers_404_no_leak(phase5_app):
    """A playthrough A credential used on playthrough B's interact path
    answers 404 (existing auth contract, unchanged) with zero knowledge
    mutation — no cross-playthrough inspection oracle."""
    case_id, creator = case_for(phase5_app)
    pt_a, token_a = playthrough(phase5_app, case_id, creator)
    pt_b, token_b = playthrough(phase5_app, case_id, creator)

    with client(phase5_app) as c:
        res = c.post(
            f"/api/v1/playthroughs/{pt_b}/objects/{KNIFE_OBJECT}/interact",
            json={"interaction": "inspect"},
            headers=auth(token_a),  # A's credential on B's path
        )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert_sanitized_error(res.text)
    snap_b = phase5_app.state.store.snapshot_player_knowledge(pt_b)
    assert snap_b.discovered == ()
    assert snap_b.visited == ()


def test_unknown_and_malformed_object_ids_404_no_mutation(phase5_app):
    """Unknown / malformed object ids answer the generic 404 and mutate NO
    PlayerKnowledge — never an id oracle."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    for evil in ("ghost_object_99", "proc.decor.00000000", "truth", ""):
        res = interact(phase5_app, pt_id, pt_token, evil, "inspect")
        assert res.status_code == 404, (evil, res.status_code)
        assert res.json()["error"]["code"] == "NOT_FOUND"
        assert_sanitized_error(res.text)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_placement_without_visible_representation_is_fail_closed_404(phase5_app):
    """A crafted placement with NO player-visible representation is NEVER
    interactable (Phase 19F fail-closed rule 4):

    - a placement whose object is not a semantic public object (invisible
      collision/helper non-object) -> 404;
    - a placement whose render asset is unregistered/unprojectable -> 404;
    - a procedural placement whose embedded definition is invalid -> 404.
    The bootstrap projection skips exactly the same placements (no divergence
    between what is shown and what is interactable).
    """
    case_id, creator = case_for(phase5_app)

    def _add_invisible(payload):
        # (a) non-object environmental helper placement (no draft.objects entry).
        payload["draft"]["world_graph"]["placements"].append(
            {
                "object_id": "collision_vol_01",
                "asset_id": "PROP_FALLBACK_01",
                "location_id": SCENE_LOCATION,
                "anchor": "floor_body_position",
                "interaction": "inspect",
                "evidence_id": None,
            }
        )
        # (b) semantic object whose render asset is unregistered.
        payload["draft"]["objects"].append(
            {
                "object_id": "ghost_wardrobe_02",
                "asset_id": "PROP_NOT_A_REAL_ASSET_99",
                "affordances": ["INSPECTABLE"],
                "subtype": "storage",
            }
        )
        payload["draft"]["world_graph"]["placements"].append(
            {
                "object_id": "ghost_wardrobe_02",
                "asset_id": "PROP_NOT_A_REAL_ASSET_99",
                "location_id": SCENE_LOCATION,
                "anchor": "hall_wall_01",
                "interaction": "inspect",
                "evidence_id": None,
            }
        )
        # (c) procedural placement with an INVALID embedded definition.
        payload["draft"]["objects"].append(
            {
                "object_id": "broken_prop_03",
                "asset_id": "proc.decor.deadbeef",
                "affordances": ["INSPECTABLE"],
                "subtype": "prop",
            }
        )
        payload["draft"]["world_graph"]["placements"].append(
            {
                "object_id": "broken_prop_03",
                "asset_id": "proc.decor.deadbeef",
                "location_id": SCENE_LOCATION,
                "anchor": "shelf_01",
                "interaction": "inspect",
                "evidence_id": None,
                "generated_definition": {"compilerVersion": 0, "schemaVersion": 0},
            }
        )

    _publish_v2(phase5_app, case_id, _add_invisible)
    pt_id, pt_token = _v2_playthrough(phase5_app, case_id, creator)

    # The bootstrap NEVER shows the invisible placements (same visibility
    # rule the interact gate mirrors).
    with client(phase5_app) as c:
        shown = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
        )
    assert shown.status_code == 200
    shown_ids = {
        w["objectId"] for w in shown.json()["scene"]["worldObjects"]
    }
    for hidden in ("collision_vol_01", "ghost_wardrobe_02", "broken_prop_03"):
        assert hidden not in shown_ids

    # The interact endpoint answers the SAME generic 404 with zero mutation.
    for hidden in ("collision_vol_01", "ghost_wardrobe_02", "broken_prop_03"):
        res = interact(phase5_app, pt_id, pt_token, hidden, "inspect")
        assert res.status_code == 404, (hidden, res.status_code)
        assert res.json()["error"]["code"] == "NOT_FOUND"
        assert_sanitized_error(res.text)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_published_placements_are_semantic_objects_only(phase5_app):
    """Architecture note + assertion (Phase19F.md fail-closed): properly
    published payloads contain ONLY semantic-object placements — every
    placement references a known public object with a registered/projectable
    render asset (enforced at publish time by
    ``pipeline._world_graph_resolution_issues`` and
    ``safety.validate_world_graph``). The visibility gate is therefore
    defensive fail-closed depth against crafted/legacy payloads, not a
    whitelist of real-world cases."""
    case_id, creator = case_for(phase5_app)
    payload = json.loads(
        phase5_app.state.store.get_published(case_id, 1).payload_json
    )
    semantic_ids = {
        str(o.get("object_id"))
        for o in payload["draft"]["objects"]
        if o.get("object_id")
    }
    for placement in payload["draft"]["world_graph"]["placements"]:
        assert str(placement["object_id"]) in semantic_ids
    # every golden placement is player-visible (bootstrap emits each exactly
    # once) and visible_placement_for_object agrees with the projection.
    visible = project_world_objects(payload, discovered=set(), read=set())
    assert len(visible) == len({p["objectId"] for p in visible})
    assert {p["objectId"] for p in visible} == {
        str(p.get("object_id")) for p in payload["draft"]["world_graph"]["placements"]
    }
    for placement in payload["draft"]["world_graph"]["placements"]:
        assert visible_placement_for_object(payload, str(placement["object_id"])) is not None
    # an object in draft but WITHOUT any placement is not interactable either.
    assert visible_placement_for_object(payload, "plant_01") is None


# --------------------------------------------------------------------------- #
# 5 — label correctness (humanized semantic label, never raw/proc ids)
# --------------------------------------------------------------------------- #


def test_inspection_label_is_humanized_never_raw_or_proc(phase5_app):
    """For EVERY visible golden object the inspection label is the humanized
    SEMANTIC label (the reveal module's semantic-label path) — the raw
    object_id is never echoed and no proc./render id ever appears."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    with client(phase5_app) as c:
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
        )
    assert res.status_code == 200
    objects = res.json()["scene"]["worldObjects"]
    assert objects, "world must contain visible objects"

    for world_object in objects:
        object_id = world_object["objectId"]
        interaction = world_object["interaction"] or "inspect"
        res = interact(phase5_app, pt_id, pt_token, object_id, interaction)
        assert res.status_code == 200, (object_id, res.status_code)
        label = res.json()["inspection"]["label"]
        assert isinstance(label, str) and label, (object_id, label)
        # the raw object_id (underscore form) is never the label, never a
        # substring of it, and proc./render ids never appear.
        assert label != object_id, (object_id, label)
        assert object_id not in label, (object_id, label)
        assert "proc" not in label.casefold(), (object_id, label)
        assert "decor" not in label.casefold(), (object_id, label)


# --------------------------------------------------------------------------- #
# 6 — solver isolation (Phase 19F core invariant)
# --------------------------------------------------------------------------- #


def _driver_solver_snapshot(record):
    """The deterministic solver signature + candidate-universe snapshot of a
    published driver record (the surfaces universal inspection must NEVER
    change: candidate universes, solver inputs, uniqueness proof, WHEN
    feasibility)."""
    from app.generation import pipeline

    proof = record.solver_proof
    assert proof is not None
    public, _facts, _truth, _draft = pipeline.assemble(record)
    signature = (
        proof.who.winner,
        proof.who.unique,
        proof.why.winner,
        proof.why.unique,
        proof.weapon.winner,
        proof.weapon.unique,
        proof.when.ambiguous,
        proof.when.overconstrained,
        tuple(proof.when.critical_evidence_ids),
        tuple(sorted(proof.evidence_ids_used)),
    )
    return public, signature, derive_universes(public)


def test_solver_isolation_decorative_objects_do_not_change_the_case():
    """Phase 19F critical regression: adding 20 decorative inspectable object
    REQUESTS to a generated kitchen-knife world produces the SAME solver
    result (unique winner per dimension), the SAME WHEN feasibility and an
    IDENTICAL candidate universe as the decoration-free equivalent world.

    Uses the Phase 19E driver harness (real controller + mocked transport).
    The office kit's anchor inventory materializes a subset of the 20
    decorations (the remainder are safely dropped per the Phase19E
    decorative-drop bounds — the drop NEVER invalidates a case); the world
    therefore gains visible decorative objects while the logical case stays
    identical."""
    def _run_world(decor_names):
        world = {
            "environmentHint": "office",
            "locationTokens": ["office"],
            "objects": [
                {"name": "kitchen knife", "criticality": "required"}
            ] + [
                {"name": name, "criticality": "decorative"} for name in decor_names
            ],
            "relations": [],
            "unsafeUnsupported": [],
        }
        posts = [
            _j(_case_people(weapon="kitchen_knife")),
            _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
            *_alog_posts('2026-09-11T23:42:00+02:00'),
            _j(world),
        ]
        for name in decor_names:
            posts.append(_named_spec(name))
        # 7 core calls (case/evidence/4 activity logs/world) + one ASSET_SPEC
        # per distinct decorative object — a 20-decor world needs a matching
        # global ceiling (the core + asset budgets are unchanged).
        record, _transport = _run(
            posts, prompt=_weapon_prompt("kitchen knife"),
            max_llm_calls_per_generation=40,
        )
        assert record.state is GenerationState.PUBLISHED
        return record

    plain = _run_world([])
    rich = _run_world([f"decor prop {index}" for index in range(20)])

    plain_public, plain_sig, plain_universe = _driver_solver_snapshot(plain)
    rich_public, rich_sig, rich_universe = _driver_solver_snapshot(rich)

    # the rich world REALLY gained visible decorative objects.
    plain_ids = {o.object_id for o in plain_public.objects}
    rich_ids = {o.object_id for o in rich_public.objects}
    added = rich_ids - plain_ids
    assert added, "no decorative object was materialized — strengthen the test"
    assert all(o.object_id in added for o in rich_public.objects if o.object_id in added)

    # solver + universe IDENTICAL.
    assert rich_sig == plain_sig, (
        "decorative inspectable objects changed the solver result",
        plain_sig,
        rich_sig,
    )
    assert rich_universe == plain_universe, (
        "decorative inspectable objects changed the candidate universe",
        plain_universe,
        rich_universe,
    )
    # the added decorative objects are NEVER solver candidates / required.
    for object_id in added:
        assert object_id not in rich_universe.weapon_ids, object_id
        assert object_id not in rich_universe.suspect_ids, object_id
        assert object_id not in rich_universe.motive_ids, object_id


# --------------------------------------------------------------------------- #
# 7 — security regression (no new leak surface)
# --------------------------------------------------------------------------- #


def test_bootstrap_and_inspection_add_no_evidence_metadata_or_leak_surface(phase5_app):
    """Universal inspection adds NO leak surface: the bootstrap still carries
    no undiscovered evidence metadata, and the full investigation flight
    (bootstrap + neutral-inspection DTOs + discovery DTOs) stays clean under
    the existing deep leak scanners (hidden leak scan + pre-reveal scan + raw
    presentation-string scan)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    payload = json.loads(
        phase5_app.state.store.get_published(case_id, 1).payload_json
    )
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
                    continue
                forbidden_strings.append(value)

    # bootstrap: no hidden material, no evidence content, no raw markers.
    with client(phase5_app) as c:
        boot = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
        ).json()
    assert_no_hidden_leaks(boot)
    boot_text = json.dumps(boot, sort_keys=True)
    for needle in forbidden_strings:
        assert needle not in boot_text, f"undiscovered evidence leaked: {needle!r}"
    for marker in ("propositions", "sourceRef", "observedAt"):
        assert marker not in boot_text

    # inspect every decoration: the DTO is lean, sanitized and leak-free.
    dto_bodies = []
    for object_id in ("vase_01", "apartment_door", "apartment_table", "apartment_lamp"):
        res = interact(phase5_app, pt_id, pt_token, object_id, "inspect")
        assert res.status_code == 200
        body = res.json()
        assert_no_hidden_leaks(body)
        assert_no_pre_reveal_material(
            body, canonical_time="2026-09-11T22:17:00+02:00"
        )
        dto_bodies.append(body)
    # the natural "nothing relevant was found on <label>" copy can only use
    # the neutral label — no evidence presentation string may appear.
    for body in dto_bodies:
        text = json.dumps(body, sort_keys=True)
        for needle in forbidden_strings:
            assert needle not in text, f"undiscovered evidence leaked in inspection: {needle!r}"

    # evidence DTOs expose ONLY the newly player-known record's own title.
    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200
    dto = res.json()
    assert_no_hidden_leaks(dto)
    dto_text = json.dumps(dto, sort_keys=True)
    for needle in forbidden_strings:
        if needle == dto["discovery"]["title"]:
            continue
        assert needle not in dto_text, f"other undiscovered evidence leaked: {needle!r}"

    # bootstrap after discovery shows ONLY the discovered evidenceId.
    with client(phase5_app) as c:
        boot2 = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
        ).json()
    by_id = {w["objectId"]: w for w in boot2["scene"]["worldObjects"]}
    assert by_id[LAPTOP_OBJECT]["evidenceId"] == EMAIL_EVIDENCE
    assert by_id["vase_01"]["evidenceId"] is None  # decorative still no metadata
    assert by_id[KNIFE_OBJECT]["evidenceId"] is None  # undiscovered stays hidden


__all__ = []  # pytest module: no accidental public names