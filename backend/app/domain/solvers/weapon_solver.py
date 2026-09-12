"""WEAPON solver — deterministic weapon deduction (§31.12, Phase3 item 7).

Semantics over the POTENTIAL_WEAPON universe (``weaponId`` is an eligible
world-object ID):

- A ``FORENSIC_WEAPON_MATCH`` proposition whose structured result is
  ``{"match": false}`` contradicts a necessary condition of that weapon ->
  ``EXCLUDE_WEAPON`` with ``necessary_for_candidate=True``.
- ``{"match": true}`` supports the weapon (``SUPPORT_CANDIDATE``).
- Conflicting match evidence for the same weapon (both true and false) leaves
  the proposition ``unknown`` (no decisive outcome; candidate stays viable).
- ``OBJECT_CONTAINS_FINGERPRINT`` / ``OBJECT_CONTAINS_BLOOD`` traces support
  the weapon; their absence is ``unknown`` and never excludes.
- No heuristic thresholds; unknown alternatives remain viable and block
  uniqueness.

Never reads hidden truth; never imports ``app.domain.truth``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.domain.eligibility import CandidateUniverses, derive_universes
from app.domain.evidence import (
    EvidenceFact,
    FORENSIC_WEAPON_MATCH,
    OBJECT_CONTAINS_BLOOD,
    OBJECT_CONTAINS_FINGERPRINT,
    discoverable_facts,
)
from app.domain.inputs import ensure_solver_input
from app.domain.proof import (
    CandidateDimensionResult,
    DIMENSION_WEAPON,
    Exclusion,
)
from app.domain.public import PublicCase
from app.domain.rules import (
    RULE_VERSION_FIRST,
    RULE_WEAPON_FORENSIC_MATCH,
    RULE_WEAPON_TRACE,
    RuleEffect,
    RuleOutcome,
    PropositionStatus,
    sort_outcomes,
)

_FORENSIC_TRACE_TYPES = frozenset({OBJECT_CONTAINS_FINGERPRINT, OBJECT_CONTAINS_BLOOD})


def _object_facts(
    evidence: tuple[EvidenceFact, ...],
    object_id: str,
    proposition_types: frozenset[str] | None = None,
) -> tuple[EvidenceFact, ...]:
    return tuple(
        fact
        for fact in discoverable_facts(evidence)
        if any(
            p.object_id == object_id
            and (proposition_types is None or p.type in proposition_types)
            for p in fact.propositions
        )
    )


@dataclass(frozen=True)
class _WeaponEvaluation:
    excluded: bool
    supported: bool
    outcomes: tuple[RuleOutcome, ...]


def _evaluate_weapon(
    evidence: tuple[EvidenceFact, ...], weapon_id: str
) -> _WeaponEvaluation:
    outcomes: list[RuleOutcome] = []

    match_facts = _object_facts(evidence, weapon_id, frozenset({FORENSIC_WEAPON_MATCH}))
    positives: list[str] = []
    negatives: list[str] = []
    for fact in match_facts:
        for p in fact.propositions:
            if p.type != FORENSIC_WEAPON_MATCH or p.object_id != weapon_id:
                continue
            result = p.structured.get("match")
            if result is True:
                positives.append(fact.id)
            elif result is False:
                negatives.append(fact.id)
    positives = sorted(set(positives))
    negatives = sorted(set(negatives))

    if negatives and not positives:
        outcomes.append(
            RuleOutcome(
                rule_id=RULE_WEAPON_FORENSIC_MATCH,
                rule_version=RULE_VERSION_FIRST,
                proposition_type=FORENSIC_WEAPON_MATCH,
                status=PropositionStatus.CONTRADICTED,
                effect=RuleEffect.EXCLUDE_WEAPON,
                evidence_ids=tuple(negatives),
                target_candidate_id=weapon_id,
                necessary_for_candidate=True,
            )
        )
    elif negatives and positives:
        # Unresolvable conflict (§47A.5): proposition unknown, candidate viable.
        outcomes.append(
            RuleOutcome(
                rule_id=RULE_WEAPON_FORENSIC_MATCH,
                rule_version=RULE_VERSION_FIRST,
                proposition_type=FORENSIC_WEAPON_MATCH,
                status=PropositionStatus.UNKNOWN,
                effect=RuleEffect.NO_EFFECT,
                evidence_ids=tuple(sorted(positives + negatives)),
                target_candidate_id=weapon_id,
                necessary_for_candidate=False,
            )
        )
    elif positives:
        outcomes.append(
            RuleOutcome(
                rule_id=RULE_WEAPON_FORENSIC_MATCH,
                rule_version=RULE_VERSION_FIRST,
                proposition_type=FORENSIC_WEAPON_MATCH,
                status=PropositionStatus.SUPPORTED,
                effect=RuleEffect.SUPPORT_CANDIDATE,
                evidence_ids=tuple(positives),
                target_candidate_id=weapon_id,
                necessary_for_candidate=False,
            )
        )

    trace_ids = tuple(f.id for f in _object_facts(evidence, weapon_id, _FORENSIC_TRACE_TYPES))
    if trace_ids:
        outcomes.append(
            RuleOutcome(
                rule_id=RULE_WEAPON_TRACE,
                rule_version=RULE_VERSION_FIRST,
                proposition_type=OBJECT_CONTAINS_FINGERPRINT,
                status=PropositionStatus.SUPPORTED,
                effect=RuleEffect.SUPPORT_CANDIDATE,
                evidence_ids=tuple(sorted(set(trace_ids))),
                target_candidate_id=weapon_id,
                necessary_for_candidate=False,
            )
        )

    sorted_outcomes = sort_outcomes(outcomes)
    excluded = bool(negatives) and not bool(positives)
    supported = any(o.is_support() for o in sorted_outcomes)
    return _WeaponEvaluation(excluded=excluded, supported=supported, outcomes=sorted_outcomes)


def solve_weapon(
    public: PublicCase,
    evidence: Iterable[EvidenceFact],
    universes: CandidateUniverses | None = None,
) -> CandidateDimensionResult:
    """Deterministic weapon deduction over the POTENTIAL_WEAPON universe."""
    evidence = tuple(evidence)
    ensure_solver_input(public, evidence)
    universes = universes if universes is not None else derive_universes(public)

    all_outcomes: list[RuleOutcome] = []
    excluded: list[Exclusion] = []
    viable: list[str] = []
    supported: list[str] = []
    unknown: list[str] = []

    for weapon_id in universes.weapon_ids:
        evaluation = _evaluate_weapon(evidence, weapon_id)
        all_outcomes.extend(evaluation.outcomes)
        if evaluation.excluded:
            excluded.append(
                Exclusion(
                    candidate_id=weapon_id,
                    rule_outcomes=tuple(
                        o for o in evaluation.outcomes if o.permits_elimination()
                    ),
                )
            )
        else:
            viable.append(weapon_id)
            if evaluation.supported:
                supported.append(weapon_id)
            else:
                unknown.append(weapon_id)

    viable_sorted = tuple(sorted(viable))
    supported_sorted = tuple(sorted(supported))
    unknown_sorted = tuple(sorted(unknown))
    unique = (
        len(viable_sorted) == 1
        and len(unknown_sorted) == 0
        and len(excluded) == len(universes.weapon_ids) - 1
    )
    return CandidateDimensionResult(
        dimension=DIMENSION_WEAPON,
        universe=universes.weapon_ids,
        viable=viable_sorted,
        supported=supported_sorted,
        excluded=tuple(excluded),
        unknown_remaining=unknown_sorted,
        unique=unique,
        winner=viable_sorted[0] if unique else None,
        rule_outcomes=sort_outcomes(all_outcomes),
    )