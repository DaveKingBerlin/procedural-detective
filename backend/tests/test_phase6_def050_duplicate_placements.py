"""DEF-050 regression — duplicate world-graph placements of ONE object id.

Reproduction (QA): a published payload whose ``draft.world_graph.placements``
contains TWO entries for the same ``object_id`` (kitchen_knife at
``kitchen_counter/inspect`` AND ``desk_main/read``) currently:

- (a) ``validate_world_graph`` returns no issues (no per-object_id seen-set);
- (b) ``project_world_objects`` emits TWO WorldObjectDTOs with the same
  objectId;
- (c) the browser sends the first DTO's interaction and the server answers 409
  for the other (``placement_for_object`` resolved the alphabetically-first).

Fix (defense in depth):
1. VALIDATION — ``validate_world_graph`` reports every duplicate object_id
   placement (deterministic first-duplicate ordering), so the generation
   pipeline can no longer PUBLISH such a payload.
2. PROJECTION — ``project_world_objects`` dedupes by object_id (keeps the
   FIRST placement in published order) and ``placement_for_object`` resolves
   the SAME placement, so the bootstrap never emits duplicate objectIds and
   the shown interaction is the accepted one.

Tests (a) validation issue, (b) projection/bootstraps dedupe,
(c) golden v1 unchanged, (d) pipeline refuses duplicate-placement drafts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_helpers import auth, create_session
from phase6_helpers import client

from app.generation.admission import AdmissionController
from app.generation.clock import ManualClock
from app.generation.controller import GenerationController
from app.generation.fake_provider import FakeProvider
from app.generation.ids import IdSource
from app.generation.parser import parse_full_draft
from app.generation.pipeline import AttemptRecord, apply_stage_output, validate_draft
from app.generation.provider import GenerationStage
from app.generation.safety import validate_world_graph
from app.generation.schemas import (
    PlacementSpec,
    WorldGraphLocationSpec,
    WorldGraphSpec,
)
from app.generation.state_machine import GenerationState, ValidationOutcome
from app.services.publication import placement_for_object, project_world_objects

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_LOCKED,
    GOLDEN_STAGE_PAYLOADS,
)

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)
_G = GOLDEN_STAGE_PAYLOADS
SCENE = "miller_apartment_kitchen"


# --------------------------------------------------------------------------- #
# (a) validate_world_graph — duplicate object_id placements
# --------------------------------------------------------------------------- #


def _placement(object_id, anchor, interaction, asset_id="PROP_KITCHEN_KNIFE_01"):
    return PlacementSpec(
        object_id=object_id,
        asset_id=asset_id,
        location_id=SCENE,
        anchor=anchor,
        interaction=interaction,
        evidence_id="forensic_knife_match_01",
    )


def _wg(*placements):
    locations = (
        WorldGraphLocationSpec(
            location_id=SCENE, template="kitchen_template", rooms=("kitchen",)
        ),
    )
    return WorldGraphSpec(locations=locations, placements=tuple(placements))


def test_a_duplicate_object_id_placement_reported():
    """QA shape: kitchen_knife placed twice (kitchen_counter/inspect AND
    desk_main/read) -> exactly one duplicate-objectId issue naming the first
    occurrence."""
    wg = _wg(
        _placement("kitchen_knife", "kitchen_counter", "inspect"),
        _placement("kitchen_knife", "desk_main", "read"),
    )
    issues = validate_world_graph(wg, {"kitchen_knife"}, {"forensic_knife_match_01"})
    duplicate = [i for i in issues if "duplicate objectId" in i]
    assert len(duplicate) == 1
    assert "placements[1]: duplicate objectId 'kitchen_knife'" in duplicate[0]
    assert "first seen at placements[0]" in duplicate[0]


def test_a_three_plus_duplicates_list_first_deterministically():
    """A/B/A/A -> BOTH duplicates are reported and both name the SAME first
    occurrence (placements[0]); ordering of the reported indices is exact."""
    wg = _wg(
        _placement("kitchen_knife", "kitchen_counter", "inspect"),
        _placement("apartment_lamp", "shelf_01", "inspect", asset_id="PROP_LAMP_01"),
        _placement("kitchen_knife", "desk_main", "read"),
        _placement("kitchen_knife", "dining_table", "inspect"),
    )
    issues = validate_world_graph(
        wg, {"kitchen_knife", "apartment_lamp"}, {"forensic_knife_match_01"}
    )
    duplicates = sorted(i for i in issues if "duplicate objectId" in i)
    assert len(duplicates) == 2
    assert "placements[2]: duplicate objectId 'kitchen_knife'" in duplicates[0]
    assert "placements[3]: duplicate objectId 'kitchen_knife'" in duplicates[1]
    for issue in duplicates:
        assert "first seen at placements[0]" in issue


def test_a_unique_placements_produce_no_duplicate_issue():
    wg = _wg(
        _placement("kitchen_knife", "kitchen_counter", "inspect"),
        _placement("apartment_lamp", "shelf_01", "inspect", asset_id="PROP_LAMP_01"),
    )
    issues = validate_world_graph(
        wg, {"kitchen_knife", "apartment_lamp"}, {"forensic_knife_match_01"}
    )
    assert not any("duplicate objectId" in issue for issue in issues)


# --------------------------------------------------------------------------- #
# (b) projection / bootstrap — dedupe by objectId
# --------------------------------------------------------------------------- #


def _payload_with_duplicate_placements() -> dict:
    return {
        "draft": {
            "objects": [
                {
                    "object_id": "kitchen_knife",
                    "asset_id": "PROP_KITCHEN_KNIFE_01",
                    "affordances": ["INSPECTABLE"],
                    "subtype": "sharp_weapon",
                },
                {
                    "object_id": "vase_01",
                    "asset_id": "PROP_VASE_01",
                    "affordances": ["INSPECTABLE"],
                    "subtype": None,
                },
            ],
            "evidence": [
                {
                    "id": "forensic_knife_match_01",
                    "kind": "forensic",
                    "presentation": {"title": "Blood", "description": "d"},
                }
            ],
            "world_graph": {
                "placements": [
                    {
                        "object_id": "kitchen_knife",
                        "asset_id": "PROP_KITCHEN_KNIFE_01",
                        "location_id": SCENE,
                        "anchor": "kitchen_counter",
                        "interaction": "inspect",
                        "evidence_id": "forensic_knife_match_01",
                    },
                    {
                        "object_id": "kitchen_knife",
                        "asset_id": "PROP_KITCHEN_KNIFE_01",
                        "location_id": SCENE,
                        "anchor": "desk_main",
                        "interaction": "read",
                        "evidence_id": "forensic_knife_match_01",
                    },
                    {
                        "object_id": "vase_01",
                        "asset_id": "PROP_VASE_01",
                        "location_id": SCENE,
                        "anchor": "dining_table",
                        "interaction": "inspect",
                        "evidence_id": None,
                    },
                ]
            },
        }
    }


def test_b_project_world_objects_dedupes_duplicate_placements():
    payload = _payload_with_duplicate_placements()
    world_objects = project_world_objects(payload)
    object_ids = [w["objectId"] for w in world_objects]
    assert len(object_ids) == len(set(object_ids)) == 2
    knife = next(w for w in world_objects if w["objectId"] == "kitchen_knife")
    # The FIRST placement in published order wins (kitchen_counter/inspect).
    assert knife["anchor"] == "kitchen_counter"
    assert knife["interaction"] == "inspect"
    assert knife["evidenceId"] == "forensic_knife_match_01"
    assert knife["subtype"] == "sharp_weapon"


def test_b_placement_for_object_matches_projected_dto():
    """The interaction endpoint resolves the SAME placement the bootstrap keeps
    (the QA 409 mismatch is impossible after the fix)."""
    payload = _payload_with_duplicate_placements()
    placement = placement_for_object(payload, "kitchen_knife")
    assert placement is not None
    assert placement["anchor"] == "kitchen_counter"
    assert placement["interaction"] == "inspect"


def test_b_duplicate_with_invalid_first_is_still_single():
    """If the FIRST placement is invalid (and therefore skipped), the next
    VALID placement is the one emitted — still exactly one DTO per objectId."""
    payload = _payload_with_duplicate_placements()
    payload["draft"]["world_graph"]["placements"][0]["asset_id"] = "PROP_UNKNOWN_99"
    world_objects = project_world_objects(payload)
    knives = [w for w in world_objects if w["objectId"] == "kitchen_knife"]
    assert len(knives) == 1
    assert knives[0]["anchor"] == "desk_main"
    assert knives[0]["interaction"] == "read"


def test_b_investigation_bootstrap_dedupes_duplicate_placements(phase5_app):
    """End-to-end: a legacy published payload with duplicate placements yields
    exactly ONE WorldObjectDTO per objectId from /investigation; the DTO's
    interaction is accepted (200) and the dropped duplicate's interaction is
    rejected (409) — the old DTO/409 mismatch is gone."""
    store = phase5_app.state.store
    clock = phase5_app.state.clock
    from app.auth.tokens import issue_playthrough_access_token, verifier as v

    now = float(clock.now())
    with client(phase5_app) as c:
        session_token, _ = create_session(c)
    # Build a clean base case via the API so all FK parents exist.
    with client(phase5_app) as c:
        res = c.post(
            "/api/v1/cases",
            json={"prompt": GOLDEN_PROMPT, "difficulty": "medium"},
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert res.status_code == 201
        case_id = res.json()["caseId"]
        creator = res.json()["creatorAccessToken"]
    # Duplicate the CLEAN v1 payload and publish it as version 99 with an
    # extra kitchen_knife placement (the QA shape).
    v1 = store.get_published(case_id, 1)
    payload = json.loads(v1.payload_json)
    payload["caseVersion"] = 99
    payload["publishedAt"] = now
    payload["draft"]["world_graph"]["placements"].append(
        {
            "object_id": "kitchen_knife",
            "asset_id": "PROP_KITCHEN_KNIFE_01",
            "location_id": SCENE,
            "anchor": "desk_main",
            "interaction": "read",
            "evidence_id": "forensic_knife_match_01",
        }
    )
    store.create_case_version(
        case_id=case_id,
        version=99,
        state="PUBLISHED",
        generation_id="GEN-99",
        created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=99,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )
    pt_token = issue_playthrough_access_token()
    store.create_playthrough_if_published(
        playthrough_id=f"PT-DEF050-{int(now)}",
        case_id=case_id,
        case_version=99,
        token_verifier=v(pt_token),
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    with client(phase5_app) as c:
        res = c.get(
            f"/api/v1/playthroughs/PT-DEF050-{int(now)}/investigation",
            headers=auth(pt_token),
        )
    assert res.status_code == 200
    body = res.json()
    object_ids = [w["objectId"] for w in body["scene"]["worldObjects"]]
    assert len(object_ids) == len(set(object_ids)), "duplicate objectIds emitted"
    assert object_ids == sorted(object_ids)
    by_id = {w["objectId"]: w for w in body["scene"]["worldObjects"]}
    assert by_id["kitchen_knife"]["anchor"] == "kitchen_counter"
    assert by_id["kitchen_knife"]["interaction"] == "inspect"
    # The interaction shown in the bootstrap now works...
    with client(phase5_app) as c:
        res = c.post(
            f"/api/v1/playthroughs/PT-DEF050-{int(now)}/objects/kitchen_knife/interact",
            json={"interaction": "inspect"},
            headers=auth(pt_token),
        )
        assert res.status_code == 200, res.text
        assert res.json()["evidenceId"] == "forensic_knife_match_01"
        # ... while the DROPPED duplicate's interaction never matches.
        res = c.post(
            f"/api/v1/playthroughs/PT-DEF050-{int(now)}/objects/kitchen_knife/interact",
            json={"interaction": "read"},
            headers=auth(pt_token),
        )
        assert res.status_code == 409, res.text
        assert res.json()["error"]["code"] == "INTERACTION_NOT_ALLOWED"


# --------------------------------------------------------------------------- #
# (c) clean golden v1 unchanged
# --------------------------------------------------------------------------- #


def test_c_golden_v1_unique_object_ids_unchanged():
    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    ids = [p.object_id for p in draft.world_graph.placements]
    assert len(ids) == 9
    assert len(set(ids)) == 9
    object_ids = {o.object_id for o in draft.objects}
    evidence_ids = {e.id for e in draft.evidence}
    assert validate_world_graph(draft.world_graph, object_ids, evidence_ids) == ()


# --------------------------------------------------------------------------- #
# (d) pipeline validate_draft refuses duplicate-placement drafts
# --------------------------------------------------------------------------- #


def _duplicate_world_graph_stage_payload() -> str:
    doc = json.loads(_G[GenerationStage.WORLD_GRAPH])
    doc["worldGraph"]["placements"].append(
        {
            "objectId": "kitchen_knife",
            "assetId": "PROP_KITCHEN_KNIFE_01",
            "locationId": SCENE,
            "anchor": "desk_main",
            "interaction": "read",
            "evidenceId": "forensic_knife_match_01",
        }
    )
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def test_d_validate_draft_refuses_duplicate_placement_draft():
    """validate_draft reports the duplicate as a safety issue and classifies
    RECOVERABLE_REPAIR — such a draft can never be VALID/published."""
    attempt = AttemptRecord(
        attempt_id="GA-DEF050-UNIT",
        case_id="CASE-DEF050-UNIT",
        session_id="QUOTA-DEF050-UNIT",
        locked=GOLDEN_LOCKED,
    )
    apply_stage_output(attempt, GenerationStage.CASE_TRUTH, _G[GenerationStage.CASE_TRUTH])
    apply_stage_output(attempt, GenerationStage.PUBLIC_WORLD, _G[GenerationStage.PUBLIC_WORLD])
    apply_stage_output(attempt, GenerationStage.EVIDENCE, _G[GenerationStage.EVIDENCE])
    apply_stage_output(
        attempt, GenerationStage.WORLD_GRAPH, _duplicate_world_graph_stage_payload()
    )
    report = validate_draft(attempt)
    assert not report.valid
    assert report.outcome is ValidationOutcome.RECOVERABLE_REPAIR
    assert any("duplicate objectId" in issue for issue in report.safety_issues)
    assert any(
        "first seen at placements[0]" in issue for issue in report.safety_issues
    )


def test_d_controller_never_publishes_duplicate_placement_draft():
    """End-to-end lifecycle: a GENERATING script that produces a duplicate
    placement is repaired/exhausted but NEVER reaches PUBLISHED."""
    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: [_G[GenerationStage.EVIDENCE]],
        GenerationStage.WORLD_GRAPH: [_duplicate_world_graph_stage_payload()],
        GenerationStage.REPAIR: ["malformed", "malformed"],
    }
    fake = FakeProvider(script)
    controller = GenerationController(
        provider=fake,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=11,
    )
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.last_validation is not None
    assert record.last_validation.outcome is ValidationOutcome.RECOVERABLE_REPAIR