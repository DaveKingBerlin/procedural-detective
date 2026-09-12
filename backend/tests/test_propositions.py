"""Proposition semantics tests (REQUIREMENTS 31.2/31.3/48A, Phase3 test B).

Three-state semantics: ``supported | contradicted | unknown``. Unknown never
eliminates; a contradicted NON-necessary proposition (e.g. a false alibi)
never eliminates; only a necessary contradiction with an EXCLUDE_* effect may.
Reliability is a public source-quality label and is NOT consumed by any rule
in this phase, so flipping reliability does not change exclusion outcomes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    golden_evidence,
    golden_public,
)

from app.domain.rules import (  # noqa: E402
    EXCLUSION_EFFECTS,
    PropositionStatus,
    RuleEffect,
    RuleOutcome,
)
from app.domain.solver import solve_case  # noqa: E402


def _make_outcome(
    status, effect, necessary, rule_id="TEST_RULE", target="candidate",
    prop_type="TEST_PROPOSITION", evidence=(), version=1,
):
    return RuleOutcome(
        rule_id=rule_id,
        rule_version=version,
        proposition_type=prop_type,
        status=status,
        effect=effect,
        evidence_ids=evidence,
        target_candidate_id=target,
        necessary_for_candidate=necessary,
    )


def test_only_necessary_contradicted_exclusion_permits_elimination():
    ok = _make_outcome(
        PropositionStatus.CONTRADICTED, RuleEffect.EXCLUDE_SUSPECT, necessary=True
    )
    assert ok.permits_elimination()

    not_necessary = _make_outcome(
        PropositionStatus.CONTRADICTED, RuleEffect.EXCLUDE_SUSPECT, necessary=False
    )
    assert not not_necessary.permits_elimination()

    unknown_status = _make_outcome(
        PropositionStatus.UNKNOWN, RuleEffect.EXCLUDE_SUSPECT, necessary=True
    )
    assert not unknown_status.permits_elimination()

    supported_status = _make_outcome(
        PropositionStatus.SUPPORTED, RuleEffect.EXCLUDE_SUSPECT, necessary=True
    )
    assert not supported_status.permits_elimination()

    wrong_effect = _make_outcome(
        PropositionStatus.CONTRADICTED, RuleEffect.ALIBI_CREDIBILITY_DECREASE, necessary=True
    )
    assert wrong_effect.effect not in EXCLUSION_EFFECTS
    assert not wrong_effect.permits_elimination()


def test_unknown_never_eliminates_end_to_end():
    """All-evidence-unknown scenario: every candidate stays viable."""
    public = golden_public()
    evidence = [
        f
        for f in golden_evidence()
        if f.id
        not in {
            # Remove everything that drives exclusions or support.
            "cctv_michael_office_01",
            "cctv_anna_bar_01",
            "motive_no_affair_01",
            "motive_no_robbery_01",
            "forensic_letter_opener_01",
            "forensic_scissors_01",
            "motive_audit_01",
            "forensic_knife_match_01",
            "fingerprint_knife_01",
            "cctv_thomas_scene_01",
        }
    ]
    proof = solve_case(public, evidence)

    assert proof.who.unique is False
    assert set(proof.who.viable) == set(proof.who.universe)  # nothing excluded
    assert len(proof.who.unknown_remaining) == len(proof.who.universe)
    assert proof.who.excluded == ()
    assert not any(
        o.permits_elimination()
        for o in proof.rule_outcomes_by_candidate
        if o.effect in EXCLUSION_EFFECTS
    )


def test_contradicted_non_necessary_alibi_never_excludes():
    """Golden case: Thomas has a contradicted alibi but remains the winner."""
    proof = solve_case(golden_public(), golden_evidence())
    thomas_outcomes = [
        o
        for o in proof.rule_outcomes_by_candidate
        if o.target_candidate_id == "thomas_reed"
        and o.effect is RuleEffect.ALIBI_CREDIBILITY_DECREASE
    ]
    assert len(thomas_outcomes) == 1
    alibi = thomas_outcomes[0]
    assert alibi.status is PropositionStatus.CONTRADICTED
    assert alibi.necessary_for_candidate is False
    assert not alibi.permits_elimination()
    assert "thomas_reed" in proof.who.viable
    assert proof.who.winner == "thomas_reed"


def test_rule_status_and_effect_are_separate():
    outcome = _make_outcome(
        PropositionStatus.CONTRADICTED, RuleEffect.ALIBI_CREDIBILITY_DECREASE, False
    )
    assert outcome.status is PropositionStatus.CONTRADICTED
    assert outcome.effect is RuleEffect.ALIBI_CREDIBILITY_DECREASE
    # Status and effect are independent axes.
    assert outcome.status.value != outcome.effect.value


def test_reliability_flip_does_not_change_exclusion_outcomes():
    """No rule in this phase consumes reliability (documented in evidence.py
    and rules.py); flipping every evidence reliability must leave the deduction
    results identical."""
    public = golden_public()
    low_evidence = []
    for fact in golden_evidence():
        import dataclasses

        low_evidence.append(dataclasses.replace(fact, reliability="low"))

    proof_high = solve_case(public, golden_evidence())
    proof_low = solve_case(public, low_evidence)

    assert proof_high.who == proof_low.who
    assert proof_high.why == proof_low.why
    assert proof_high.weapon == proof_low.weapon
    assert proof_high.when == proof_low.when
    assert proof_high.evidence_ids_used == proof_low.evidence_ids_used


def test_supported_contradicted_unknown_are_exhaustive():
    for status in PropositionStatus:
        assert status.value in ("supported", "contradicted", "unknown")
    assert len(PropositionStatus) == 3