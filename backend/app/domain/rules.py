"""Deduction rule model (REQUIREMENTS 31.3 / 48A).

Three-state proposition semantics:
    supported | contradicted | unknown

Every evaluated rule produces exactly one ``RuleOutcome`` carrying:

    ruleId, ruleVersion, propositionType, status, evidenceIds, effect,
    targetCandidateId, necessaryForCandidate

ELIMINATION INVARIANTS (self-evident in the data — documented here and
enforced by tests in ``backend/tests/test_propositions.py``):

1. Only a ``necessary_for_candidate=True`` contradiction with an ``EXCLUDE_*``
   effect may eliminate a candidate. A contradicted NON-necessary proposition
   (e.g. a false/weak alibi -> ``ALIBI_CREDIBILITY_DECREASE``) NEVER
   eliminates.
2. ``unknown`` never eliminates — absence of evidence is never contradiction;
   an unknown alternative remains viable and blocks uniqueness (31.9 / 48A.2).
3. No heuristic confidence/threshold may substitute for deterministic
   elimination. Reliability (evidence.py) is a public source-quality label and
   is NOT consumed by any rule in this phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

# Rule identifiers (stable, versioned). These names are part of the published
# proof surface (§7.4: deduction-rule version is version-locked at publication).
RULE_OPPORTUNITY_PRESENCE = "OPPORTUNITY_PRESENCE_INTERSECTION"
RULE_EARLIEST_TRAVEL_BOUND = "EARLIEST_ARRIVAL_TRAVEL_BOUND"
RULE_ALIBI_CREDIBILITY = "ALIBI_CREDIBILITY"
RULE_MOTIVE_LINK = "MOTIVE_LINKED_SUPPORT"
RULE_MOTIVE_FACT = "MOTIVE_FACT_CONTRADICTION"
RULE_WEAPON_FORENSIC_MATCH = "WEAPON_FORENSIC_MATCH"
RULE_WEAPON_TRACE = "WEAPON_TRACE_EVIDENCE"

RULE_VERSION_FIRST = 1


class PropositionStatus(Enum):
    """Three-state proposition semantics (§31.2 / 48A)."""

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class RuleEffect(Enum):
    """Rule effect, separated from status (§48A)."""

    NO_EFFECT = "NO_EFFECT"
    SUPPORT_CANDIDATE = "SUPPORT_CANDIDATE"
    EXCLUDE_SUSPECT = "EXCLUDE_SUSPECT"
    EXCLUDE_MOTIVE = "EXCLUDE_MOTIVE"
    EXCLUDE_WEAPON = "EXCLUDE_WEAPON"
    EXCLUDE_TIME_WINDOW = "EXCLUDE_TIME_WINDOW"
    ALIBI_CREDIBILITY_DECREASE = "ALIBI_CREDIBILITY_DECREASE"
    INCREASE_SUSPICION = "INCREASE_SUSPICION"
    ADD_TIME_CONSTRAINT = "ADD_TIME_CONSTRAINT"


EXCLUSION_EFFECTS: frozenset[RuleEffect] = frozenset(
    {
        RuleEffect.EXCLUDE_SUSPECT,
        RuleEffect.EXCLUDE_MOTIVE,
        RuleEffect.EXCLUDE_WEAPON,
        RuleEffect.EXCLUDE_TIME_WINDOW,
    }
)


def _status(value: PropositionStatus | str) -> PropositionStatus:
    if isinstance(value, PropositionStatus):
        return value
    return PropositionStatus(value)


def _effect(value: RuleEffect | str) -> RuleEffect:
    if isinstance(value, RuleEffect):
        return value
    return RuleEffect(value)


@dataclass(frozen=True)
class RuleOutcome:
    """Result of evaluating one rule against one candidate (§48A model)."""

    rule_id: str
    rule_version: int
    proposition_type: str
    status: PropositionStatus
    effect: RuleEffect
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    target_candidate_id: str = ""
    necessary_for_candidate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id:
            raise ValueError("rule_id must be a non-empty string")
        if not isinstance(self.rule_version, int) or isinstance(self.rule_version, bool):
            raise ValueError("rule_version must be an int")
        if not isinstance(self.proposition_type, str) or not self.proposition_type:
            raise ValueError("proposition_type must be a non-empty string")
        if not isinstance(self.target_candidate_id, str) or not self.target_candidate_id:
            raise ValueError("target_candidate_id must be a non-empty string")
        object.__setattr__(self, "status", _status(self.status))
        object.__setattr__(self, "effect", _effect(self.effect))
        object.__setattr__(self, "evidence_ids", tuple(sorted(set(self.evidence_ids))))
        if not isinstance(self.necessary_for_candidate, bool):
            raise ValueError("necessary_for_candidate must be a bool")

    def permits_elimination(self) -> bool:
        """True only when this outcome may eliminate its target (§48A critical
        rule): a contradiction of a necessary condition whose effect is an
        EXCLUDE_* effect."""
        return (
            self.status is PropositionStatus.CONTRADICTED
            and self.necessary_for_candidate
            and self.effect in EXCLUSION_EFFECTS
        )

    def is_support(self) -> bool:
        """Positive support for the target candidate."""
        return self.status is PropositionStatus.SUPPORTED and self.effect in (
            RuleEffect.SUPPORT_CANDIDATE,
            RuleEffect.INCREASE_SUSPICION,
        )

    # Deterministic ordering for flattened proof output.
    def _sort_key(self) -> tuple:
        return (
            self.target_candidate_id,
            self.rule_id,
            self.rule_version,
            self.effect.value,
            self.status.value,
            self.proposition_type,
            self.evidence_ids,
        )


def sort_outcomes(outcomes: Iterable[RuleOutcome]) -> tuple[RuleOutcome, ...]:
    """Deterministic canonical ordering of rule outcomes for proofs."""
    return tuple(sorted(outcomes, key=lambda o: o._sort_key()))


def dedupe_outcomes(outcomes: Iterable[RuleOutcome]) -> tuple[RuleOutcome, ...]:
    """Remove exact duplicates (same target/rule/status/effect/evidence)."""
    unique: dict[tuple, RuleOutcome] = {}
    for outcome in outcomes:
        unique[outcome._sort_key()] = outcome
    return tuple(sorted(unique.values(), key=lambda o: o._sort_key()))