"""Deterministic machine-readable proof/result model (server/internal only).

``SolverProof`` is the full deduction result assembled from the WHEN/WHO/WHY/
WEAPON solvers. It records — for every candidate — the rule outcomes (status,
effect, evidence ids, necessary flag) that explain why the candidate survived
or was excluded, the candidate-universe eligibility snapshot, and the set of
evidence ids the deduction used.

PROOF-INDEPENDENCE CONTRACT (§31.15): the proof must never contain a
CaseTruth-derived premise. The truth comparison happens AFTER deduction, in
``app.validation.solution``. This module never imports ``app.domain.truth``
(asserted by ``backend/tests/test_boundaries.py``).

Structural equality (frozen dataclasses) is exact, enabling the
truth-independence and determinism tests (``==`` is meaningful).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Tuple

from app.domain.eligibility import CandidateUniverses
from app.domain.rules import RuleOutcome, dedupe_outcomes

# ``WhenResult`` is defined by ``app.domain.solvers.when_solver`` and is only
# referenced in string annotations here (future-annotations enabled), so there
# is no runtime import and no circular dependency.

PROOF_VERSION = "1.0"
DIMENSION_SUSPECT = "suspect"
DIMENSION_MOTIVE = "motive"
DIMENSION_WEAPON = "weapon"


@dataclass(frozen=True)
class Exclusion:
    """One evidence-backed exclusion of a discrete candidate."""

    candidate_id: str
    rule_outcomes: Tuple[RuleOutcome, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError("candidate_id must be a non-empty string")
        object.__setattr__(self, "rule_outcomes", tuple(self.rule_outcomes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "ruleOutcomes": [outcome_to_dict(o) for o in self.rule_outcomes],
        }


@dataclass(frozen=True)
class CandidateDimensionResult:
    """Survivor/exclusion semantics for one discrete dimension (§31.9/31.10-12).

    ``viable`` = supported ∪ unknown (anything NOT evidence-backed excluded).
    ``unique`` is True only when exactly one candidate is viable AND every other
    universe member is evidence-backed excluded; any un-excluded unknown/viable
    alternative blocks uniqueness.
    """

    dimension: str
    universe: Tuple[str, ...] = field(default_factory=tuple)
    viable: Tuple[str, ...] = field(default_factory=tuple)
    supported: Tuple[str, ...] = field(default_factory=tuple)
    excluded: Tuple[Exclusion, ...] = field(default_factory=tuple)
    unknown_remaining: Tuple[str, ...] = field(default_factory=tuple)
    unique: bool = False
    winner: str | None = None
    rule_outcomes: Tuple[RuleOutcome, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.dimension not in (DIMENSION_SUSPECT, DIMENSION_MOTIVE, DIMENSION_WEAPON):
            raise ValueError(f"unknown dimension {self.dimension!r}")
        for name in (
            "universe",
            "viable",
            "supported",
            "excluded",
            "unknown_remaining",
            "rule_outcomes",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not isinstance(self.unique, bool):
            raise ValueError("unique must be a bool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "universe": list(self.universe),
            "viable": list(self.viable),
            "supported": list(self.supported),
            "unknownRemaining": list(self.unknown_remaining),
            "excluded": [e.to_dict() for e in self.excluded],
            "unique": self.unique,
            "winner": self.winner,
        }


@dataclass(frozen=True)
class SolverProof:
    """The complete deterministic deduction proof (§31.14, §7.4 snapshot)."""

    version: str = PROOF_VERSION
    eligibility_snapshot: CandidateUniverses | None = None
    who: CandidateDimensionResult | None = None
    why: CandidateDimensionResult | None = None
    weapon: CandidateDimensionResult | None = None
    when: WhenResult | None = None
    rule_outcomes_by_candidate: Tuple[RuleOutcome, ...] = field(default_factory=tuple)
    evidence_ids_used: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "eligibilitySnapshot": {
                "eligibilityVersion": self.eligibility_snapshot.eligibility_version,
                "suspectIds": list(self.eligibility_snapshot.suspect_ids),
                "motiveIds": list(self.eligibility_snapshot.motive_ids),
                "weaponIds": list(self.eligibility_snapshot.weapon_ids),
            }
            if self.eligibility_snapshot is not None
            else None,
            "who": self.who.to_dict() if self.who is not None else None,
            "why": self.why.to_dict() if self.why is not None else None,
            "weapon": self.weapon.to_dict() if self.weapon is not None else None,
            "when": self.when.to_dict() if self.when is not None else None,
            "ruleOutcomesByCandidate": [
                outcome_to_dict(o) for o in self.rule_outcomes_by_candidate
            ],
            "evidenceIdsUsed": list(self.evidence_ids_used),
        }


def outcome_to_dict(outcome: RuleOutcome) -> dict[str, Any]:
    return {
        "ruleId": outcome.rule_id,
        "ruleVersion": outcome.rule_version,
        "propositionType": outcome.proposition_type,
        "status": outcome.status.value,
        "effect": outcome.effect.value,
        "evidenceIds": list(outcome.evidence_ids),
        "targetCandidateId": outcome.target_candidate_id,
        "necessaryForCandidate": outcome.necessary_for_candidate,
        "permitsElimination": outcome.permits_elimination(),
    }


def build_proof(
    eligibility: CandidateUniverses,
    when: WhenResult,
    who: CandidateDimensionResult,
    why: CandidateDimensionResult,
    weapon: CandidateDimensionResult,
) -> SolverProof:
    """Assemble the flattened proof from the four solver results.

    The flattened ``rule_outcomes_by_candidate`` is deterministically ordered
    (see ``rules.dedupe_outcomes``); ``evidence_ids_used`` is the sorted union
    of every evidence id referenced by any rule outcome plus the WHEN-critical
    evidence ids.
    """
    flattened = dedupe_outcomes(
        outcome
        for dim in (who, why, weapon)
        for outcome in dim.rule_outcomes
    )
    used: set[str] = set(when.critical_evidence_ids)
    for outcome in flattened:
        used.update(outcome.evidence_ids)
    return SolverProof(
        version=PROOF_VERSION,
        eligibility_snapshot=eligibility,
        who=who,
        why=why,
        weapon=weapon,
        when=when,
        rule_outcomes_by_candidate=flattened,
        evidence_ids_used=tuple(sorted(used)),
    )