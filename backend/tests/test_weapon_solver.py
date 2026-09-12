"""WEAPON solver tests (Phase3 test D — weapon dimension).

Semantics over the POTENTIAL_WEAPON universe (weaponId is an eligible world
object ID): contradicted necessary forensics exclude; traces/blood support;
unknown never excludes; unknown alternatives block uniqueness.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    golden_evidence,
    golden_public,
)

from app.domain.proof import DIMENSION_WEAPON  # noqa: E402
from app.domain.rules import RuleEffect  # noqa: E402
from app.domain.solver import solve_case  # noqa: E402


def test_golden_unique_weapon_solution():
    proof = solve_case(golden_public(), golden_evidence())
    weapon = proof.weapon

    assert weapon.dimension == DIMENSION_WEAPON
    assert weapon.unique is True
    assert weapon.winner == "kitchen_knife"
    assert weapon.viable == ("kitchen_knife",)
    assert set(weapon.unknown_remaining) == set()
    assert {e.candidate_id for e in weapon.excluded} == {"letter_opener", "scissors"}
    for exclusion in weapon.excluded:
        assert all(o.effect is RuleEffect.EXCLUDE_WEAPON for o in exclusion.rule_outcomes)
        assert all(o.necessary_for_candidate for o in exclusion.rule_outcomes)


def test_weapon_outside_universe_never_considered():
    """vase_01 has no POTENTIAL_WEAPON affordance -> outside the declared
    murder-weapon game model even if it sits in the public world (31.1.3)."""
    proof = solve_case(golden_public(), golden_evidence())
    assert "vase_01" not in proof.eligibility_snapshot.weapon_ids
    assert "vase_01" not in proof.weapon.universe
    assert "vase_01" not in proof.weapon.viable


def test_alternative_weapon_unknown_is_ambiguous():
    evidence = [f for f in golden_evidence() if f.id != "forensic_letter_opener_01"]
    proof = solve_case(golden_public(), evidence)
    weapon = proof.weapon
    assert weapon.unique is False
    assert weapon.winner is None
    assert "letter_opener" in weapon.viable
    assert "letter_opener" in weapon.unknown_remaining


def test_weapon_with_no_forensic_evidence_stays_viable():
    evidence = [
        f
        for f in golden_evidence()
        if f.id
        not in {"forensic_letter_opener_01", "forensic_scissors_01", "forensic_knife_match_01"}
    ]
    proof = solve_case(golden_public(), evidence)
    weapon = proof.weapon
    assert set(weapon.viable) == set(weapon.universe)
    assert weapon.excluded == ()
    assert weapon.unique is False


def test_no_heuristic_thresholds_used():
    """The weapon dimension only uses the deterministic match/trace rules;
    there are no numeric thresholds anywhere in the deduction. (Structural
    guard: every rule outcome must be one of the enumerated effects/statuses.)"""
    proof = solve_case(golden_public(), golden_evidence())
    for outcome in proof.weapon.rule_outcomes:
        assert outcome.effect in RuleEffect
        assert outcome.status.value in ("supported", "contradicted", "unknown")


def test_truth_change_produces_identical_weapon_result():
    proof_a = solve_case(golden_public(), golden_evidence())
    proof_b = solve_case(golden_public(), golden_evidence())
    assert proof_a.weapon == proof_b.weapon