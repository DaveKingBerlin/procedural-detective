"""Golden generation round-trip proof (Phase 4 section 8/9).

Parses the GOLDEN stage payloads -> GeneratedDraft -> assemble_phase3 and
proves the result is STRUCTURALLY EQUAL to the Phase 3 golden objects and that
the Phase 3 solver produces the required unique solution
(all_true == True). Also proves GOLDEN_FULL_DRAFT parses to the identical
draft.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    golden_evidence,
    golden_public,
    truth_variant_a,
)
from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_LOCKED,
    GOLDEN_STAGE_PAYLOADS,
    assemble_phase3,
)

from app.domain.solver import solve_case  # noqa: E402
from app.domain.time_interval import (  # noqa: E402
    accepted_scoring_time_set,
    parse_iso8601_to_epoch,
)
from app.generation.parser import (  # noqa: E402
    parse_full_draft,
    parse_stage,
)
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.safety import validate_world_graph  # noqa: E402
from app.generation.schemas import GeneratedDraft  # noqa: E402
from app.validation.solution import evaluate_solution  # noqa: E402


def _draft_from_stages() -> GeneratedDraft:
    crime = parse_stage(
        GenerationStage.CASE_TRUTH, GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH]
    )
    public_world = parse_stage(
        GenerationStage.PUBLIC_WORLD, GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD]
    )
    evidence = parse_stage(
        GenerationStage.EVIDENCE, GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE]
    )
    world_graph = parse_stage(
        GenerationStage.WORLD_GRAPH, GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH]
    )
    assert crime is not None and public_world is not None
    assert evidence is not None and world_graph is not None
    return GeneratedDraft(
        crime=crime,
        persons=public_world.persons,
        motives=public_world.motives,
        objects=public_world.objects,
        locations=public_world.locations,
        travel_rules=public_world.travel_rules,
        scene=public_world.scene,
        evidence=evidence.evidence,
        world_graph=world_graph,
    )


def test_parse_stages_assemble_equals_phase3_golden():
    draft = _draft_from_stages()
    public, evidence, truth = assemble_phase3(draft)

    assert public == golden_public()
    assert evidence == tuple(golden_evidence())
    assert truth == truth_variant_a()


def test_full_draft_parse_identical_to_stages():
    from_stages = _draft_from_stages()
    from_full = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert from_full is not None
    assert from_full == from_stages


def test_golden_locked_respected_by_full_draft():
    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    assert GOLDEN_LOCKED.violations_against(draft) == ()


def test_golden_world_graph_safety_validates():
    draft = _draft_from_stages()
    object_ids = {o.object_id for o in draft.objects}
    evidence_ids = {e.id for e in draft.evidence}
    assert validate_world_graph(draft.world_graph, object_ids, evidence_ids) == ()


def test_solver_solves_golden_with_all_true():
    draft = _draft_from_stages()
    public, evidence, truth = assemble_phase3(draft)
    proof = solve_case(public, evidence)
    validation = evaluate_solution(proof, truth)

    # single unique survivor per dimension
    assert proof.who.unique is True
    assert proof.why.unique is True
    assert proof.weapon.unique is True
    # single connected time interval containing 22:17
    assert proof.when.connected_count == 1
    assert proof.when.ambiguous is False
    assert proof.when.overconstrained is False
    tick = parse_iso8601_to_epoch("2026-09-11T22:17:00+02:00")
    assert proof.when.feasible.contains(tick)
    # ... within tolerance 120 seconds of 22:17
    assert proof.when.feasible.is_subset_of(accepted_scoring_time_set(tick, 120))

    assert validation.murderer_true is True
    assert validation.motive_true is True
    assert validation.weapon_true is True
    assert validation.time_accepted is True
    assert validation.all_true is True


def test_solver_survivors_match_golden_truth():
    draft = _draft_from_stages()
    public, evidence, truth = assemble_phase3(draft)
    proof = solve_case(public, evidence)
    assert proof.who.winner == truth.crime.murderer_id
    assert proof.why.winner == truth.crime.motive_id
    assert proof.weapon.winner == truth.crime.weapon_id


def test_phase6_golden_scene_present_in_roundtrip_draft():
    """The Milestone-1 investigation scene survives the round trip: >= 6
    placements with the knife + laptop + victim body, the laptop links the
    email record, and the email typed presentation survives strict parsing."""
    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    placements = draft.world_graph.placements
    assert len(placements) >= 6
    by_object = {p.object_id: p for p in placements}
    assert by_object["kitchen_knife"].evidence_id == "forensic_knife_match_01"
    assert by_object["apartment_laptop"].evidence_id == "email_thomas_01"
    assert by_object["apartment_laptop"].interaction == "read"
    # ADV-222: the victim body is evidence-linked to the golden time-bearing
    # BODY_FIRST_FOUND_AT record (body_found_01) so a player can discover a
    # WHEN fact by interacting with a placed object.
    assert by_object["victim_body_placeholder"].evidence_id == "body_found_01"
    assert by_object["victim_body_placeholder"].interaction == "inspect"
    email = next(f for f in draft.evidence if f.id == "email_thomas_01")
    assert email.kind == "email"
    presentation = dict(email.presentation)
    assert presentation["fromPersonId"] == "thomas_reed"
    assert presentation["toPersonIds"] == ["sarah_miller"]
    assert presentation["subject"]
    assert presentation["body"]
    assert presentation["timestamp"] == "2026-09-11T21:04:00+02:00"