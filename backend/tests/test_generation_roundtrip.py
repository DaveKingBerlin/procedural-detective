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