"""WHY solver tests (Phase3 test D — motive dimension).

Same supported/contradicted/unknown semantics over the MOTIVE_CANDIDATE
universe: contradicted necessary motive facts exclude; absent evidence leaves
the motive unknown and viable; unknown alternatives block uniqueness; hidden
truth changes never alter the deduction.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    golden_evidence,
    golden_public,
)

from app.domain.proof import DIMENSION_MOTIVE  # noqa: E402
from app.domain.rules import RuleEffect  # noqa: E402
from app.domain.solver import solve_case  # noqa: E402


def test_golden_unique_motive_solution():
    proof = solve_case(golden_public(), golden_evidence())
    why = proof.why

    assert why.dimension == DIMENSION_MOTIVE
    assert why.unique is True
    assert why.winner == "cover_up_embezzlement"
    assert why.viable == ("cover_up_embezzlement",)
    assert why.unknown_remaining == ()
    assert {e.candidate_id for e in why.excluded} == {
        "revenge_for_affair",
        "robbery_gone_wrong",
    }
    for exclusion in why.excluded:
        assert all(o.effect is RuleEffect.EXCLUDE_MOTIVE for o in exclusion.rule_outcomes)
        assert all(o.necessary_for_candidate for o in exclusion.rule_outcomes)


def test_alternative_motive_unknown_is_ambiguous():
    evidence = [f for f in golden_evidence() if f.id != "motive_no_affair_01"]
    proof = solve_case(golden_public(), evidence)
    why = proof.why
    assert why.unique is False
    assert why.winner is None
    assert "revenge_for_affair" in why.viable
    assert "revenge_for_affair" in why.unknown_remaining


def test_motive_linked_support_never_excludes_and_absence_is_unknown():
    """GOLDEN minus all contradiction evidence: all motives stay viable."""
    evidence = [
        f
        for f in golden_evidence()
        if f.id not in {"motive_no_affair_01", "motive_no_robbery_01"}
    ]
    proof = solve_case(golden_public(), evidence)
    why = proof.why
    assert set(why.viable) == set(why.universe)
    assert why.excluded == ()
    assert why.unique is False
    support = [
        o
        for o in proof.rule_outcomes_by_candidate
        if o.target_candidate_id == "cover_up_embezzlement" and o.is_support()
    ]
    assert support, "motive link evidence must register as support"


def test_truth_change_produces_identical_why_result():
    from fixtures.golden import truth_variant_a, truth_variant_b

    proof_a = solve_case(golden_public(), golden_evidence())
    proof_b = solve_case(golden_public(), golden_evidence())
    # Different hidden truths; solver input identical -> structurally identical.
    assert truth_variant_a().crime.motive_id != truth_variant_b().crime.motive_id
    assert proof_a.why == proof_b.why


def test_exclusions_are_evidence_backed():
    proof = solve_case(golden_public(), golden_evidence())
    for exclusion in proof.why.excluded:
        for outcome in exclusion.rule_outcomes:
            assert outcome.evidence_ids, "exclusion must cite evidence"
            assert outcome.permits_elimination()