"""WHY solver — deterministic motive deduction (§31.11, Phase3 item 6).

Same supported/contradicted/unknown semantics over the MOTIVE_CANDIDATE
universe:

- ``MOTIVE_FACT_CONTRADICTED`` evidence for a motive contradicts a necessary
  fact of that motive's candidate solution -> ``EXCLUDE_MOTIVE`` with
  ``necessary_for_candidate=True``.
- ``MOTIVE_LINKED_TO_PERSON`` evidence supports the linked motive
  (``SUPPORT_CANDIDATE``); its absence leaves the motive ``unknown`` and the
  candidate viable.
- Unknown alternatives remain viable and block uniqueness.

Never reads hidden truth; never imports ``app.domain.truth``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.domain.eligibility import CandidateUniverses, derive_universes
from app.domain.evidence import (
    EvidenceFact,
    MOTIVE_FACT_CONTRADICTED,
    MOTIVE_LINKED_TO_PERSON,
    discoverable_facts,
)
from app.domain.inputs import ensure_solver_input
from app.domain.proof import (
    CandidateDimensionResult,
    DIMENSION_MOTIVE,
    Exclusion,
)
from app.domain.public import PublicCase
from app.domain.rules import (
    RULE_MOTIVE_FACT,
    RULE_MOTIVE_LINK,
    RULE_VERSION_FIRST,
    RuleEffect,
    RuleOutcome,
    PropositionStatus,
    sort_outcomes,
)


def _facts_for_motive(
    evidence: tuple[EvidenceFact, ...], motive_id: str, proposition_type: str
) -> tuple[EvidenceFact, ...]:
    return tuple(
        fact
        for fact in discoverable_facts(evidence)
        if any(
            p.type == proposition_type and p.motive_id == motive_id for p in fact.propositions
        )
    )


@dataclass(frozen=True)
class _MotiveEvaluation:
    excluded: bool
    supported: bool
    outcomes: tuple[RuleOutcome, ...]


def _evaluate_motive(
    evidence: tuple[EvidenceFact, ...], motive_id: str
) -> _MotiveEvaluation:
    outcomes: list[RuleOutcome] = []

    contradicted = _facts_for_motive(evidence, motive_id, MOTIVE_FACT_CONTRADICTED)
    contradicted_ids = tuple(sorted(f.id for f in contradicted))
    if contradicted_ids:
        outcomes.append(
            RuleOutcome(
                rule_id=RULE_MOTIVE_FACT,
                rule_version=RULE_VERSION_FIRST,
                proposition_type=MOTIVE_FACT_CONTRADICTED,
                status=PropositionStatus.CONTRADICTED,
                effect=RuleEffect.EXCLUDE_MOTIVE,
                evidence_ids=contradicted_ids,
                target_candidate_id=motive_id,
                necessary_for_candidate=True,
            )
        )

    links = _facts_for_motive(evidence, motive_id, MOTIVE_LINKED_TO_PERSON)
    link_ids = tuple(sorted(f.id for f in links))
    if link_ids:
        outcomes.append(
            RuleOutcome(
                rule_id=RULE_MOTIVE_LINK,
                rule_version=RULE_VERSION_FIRST,
                proposition_type=MOTIVE_LINKED_TO_PERSON,
                status=PropositionStatus.SUPPORTED,
                effect=RuleEffect.SUPPORT_CANDIDATE,
                evidence_ids=link_ids,
                target_candidate_id=motive_id,
                necessary_for_candidate=False,
            )
        )

    sorted_outcomes = sort_outcomes(outcomes)
    excluded = bool(contradicted_ids)
    supported = any(o.is_support() for o in sorted_outcomes)
    return _MotiveEvaluation(excluded=excluded, supported=supported, outcomes=sorted_outcomes)


def solve_why(
    public: PublicCase,
    evidence: Iterable[EvidenceFact],
    universes: CandidateUniverses | None = None,
) -> CandidateDimensionResult:
    """Deterministic motive deduction over the MOTIVE_CANDIDATE universe."""
    evidence = tuple(evidence)
    ensure_solver_input(public, evidence)
    universes = universes if universes is not None else derive_universes(public)

    all_outcomes: list[RuleOutcome] = []
    excluded: list[Exclusion] = []
    viable: list[str] = []
    supported: list[str] = []
    unknown: list[str] = []

    for motive_id in universes.motive_ids:
        evaluation = _evaluate_motive(evidence, motive_id)
        all_outcomes.extend(evaluation.outcomes)
        if evaluation.excluded:
            excluded.append(
                Exclusion(
                    candidate_id=motive_id,
                    rule_outcomes=tuple(
                        o for o in evaluation.outcomes if o.permits_elimination()
                    ),
                )
            )
        else:
            viable.append(motive_id)
            if evaluation.supported:
                supported.append(motive_id)
            else:
                unknown.append(motive_id)

    viable_sorted = tuple(sorted(viable))
    supported_sorted = tuple(sorted(supported))
    unknown_sorted = tuple(sorted(unknown))
    unique = (
        len(viable_sorted) == 1
        and len(unknown_sorted) == 0
        and len(excluded) == len(universes.motive_ids) - 1
    )
    return CandidateDimensionResult(
        dimension=DIMENSION_MOTIVE,
        universe=universes.motive_ids,
        viable=viable_sorted,
        supported=supported_sorted,
        excluded=tuple(excluded),
        unknown_remaining=unknown_sorted,
        unique=unique,
        winner=viable_sorted[0] if unique else None,
        rule_outcomes=sort_outcomes(all_outcomes),
    )