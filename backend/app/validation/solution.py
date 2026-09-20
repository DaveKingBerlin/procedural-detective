"""Truth-aware validation of a SolverProof against CaseTruth (§10.6/31.x).

THIS is the ONLY place allowed to touch ``CaseTruth``:

1. ``evaluate_solution`` computes the per-dimension survivor == canonical check
   and the accepted-scoring time check:
       - ``when.unique`` (exactly one connected feasible interval)
       - ``canonicalTick ∈ feasible``                 (§10.7 / 31.8)
       - ``feasible ⊆ AcceptedScoringTimeSet``         (§10.7 / 31.8)
2. ``assemble_solution_proof`` produces the final machine-readable
   ``SolutionProof`` with the ``acceptedScoring`` section (§31.14 example) and
   performs the light §31.16 reachability static check (every excluded or
   time-critical evidence id referenced by the proof exists and is
   discoverable).

The deduction itself (``app.domain.solver.solve_case``) runs without any truth
input; comparison happens only here, afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Tuple

from app.domain.evidence import EvidenceFact
from app.domain.inputs import ensure_public_case, evidence_by_id
from app.domain.proof import CandidateDimensionResult, SolverProof
from app.domain.public import PublicCase
from app.domain.time_interval import (
    IntervalSet,
    accepted_scoring_time_set,
    epoch_to_iso,
    iso_offset_minutes,
    parse_iso8601_to_epoch,
)
from app.domain.truth import CaseTruth


@dataclass(frozen=True)
class AccusedSolutionValidation:
    """Per-dimension truth comparison + accepted-scoring time check.

    ``time_accepted`` requires all four: exactly one connected feasible
    interval, the single interval within the AcceptedScoringTimeSet, the
    canonical tick inside the feasible set, and the feasible set NOT being
    overconstrained (empty — an empty set must not vacuously pass containment).
    """

    murderer_true: bool
    motive_true: bool
    weapon_true: bool
    time_unique_single_interval: bool
    time_within_scoring: bool
    canonical_in_feasible: bool
    time_overconstrained: bool
    time_accepted: bool
    all_true: bool
    deducted_murderer: str | None = None
    deducted_motive: str | None = None
    deducted_weapon: str | None = None
    canonical_murderer: str = ""
    canonical_motive: str = ""
    canonical_weapon: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "murdererTrue": self.murderer_true,
            "motiveTrue": self.motive_true,
            "weaponTrue": self.weapon_true,
            "timeUniqueSingleInterval": self.time_unique_single_interval,
            "timeWithinScoring": self.time_within_scoring,
            "canonicalInFeasible": self.canonical_in_feasible,
            "timeOverconstrained": self.time_overconstrained,
            "timeAccepted": self.time_accepted,
            "allTrue": self.all_true,
            "deductedMurderer": self.deducted_murderer,
            "deductedMotive": self.deducted_motive,
            "deductedWeapon": self.deducted_weapon,
            "canonicalMurderer": self.canonical_murderer,
            "canonicalMotive": self.canonical_motive,
            "canonicalWeapon": self.canonical_weapon,
        }


@dataclass(frozen=True)
class AcceptedScoring:
    """§31.14 ``acceptedScoring`` block, rendered in the case timezone."""

    canonical: str
    start_inclusive: str
    end_exclusive: str


@dataclass(frozen=True)
class TimeProof:
    """§31.14 ``crimeTime`` proof block."""

    canonical: str
    accepted_scoring: AcceptedScoring
    feasible_intervals: Tuple[Tuple[str, str], ...]
    critical_evidence_ids: Tuple[str, ...]
    connected_count: int
    ambiguous: bool
    overconstrained: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "acceptedScoring": {
                "startInclusive": self.accepted_scoring.start_inclusive,
                "endExclusive": self.accepted_scoring.end_exclusive,
            },
            "feasibleIntervals": [
                {"startInclusive": s, "endExclusive": e} for s, e in self.feasible_intervals
            ],
            "criticalEvidenceIds": list(self.critical_evidence_ids),
            "connectedCount": self.connected_count,
            "ambiguous": self.ambiguous,
            "overconstrained": self.overconstrained,
        }


@dataclass(frozen=True)
class SolutionProof:
    """Final machine-readable, server-only solution proof (§31.14 / 41.5).

    This is the internal model; a public DTO for the browser must be assembled
    from an allowlist on top of it and must NOT include the hidden fields.

    NEW Phase18C: the per-dimension usable evidence ids (``who_evidence_ids`` /
    ``why_evidence_ids`` / ``weapon_evidence_ids``) are the DETERMINISTIC
    per-dimension projection of the deduction proof's rule-outcome evidence
    refs. They are computed ONCE at publish time (from the domain
    ``SolverProof``, which is NOT persisted) so the post-reveal proof board can
    map WHO/WHY/WEAPON -> supporting discovered evidence WITHOUT re-running the
    solver and without exposing rule/outcome internals. ``when`` reuses the
    already-persisted ``time.critical_evidence_ids``. By construction
    (``build_proof``) the union of the four per-dimension id sets equals
    ``evidence_ids_used``.
    """

    case_id: str
    case_version: int
    solver_proof_version: str
    validation: AccusedSolutionValidation
    winners: Tuple[str, str, str]
    time: TimeProof
    evidence_ids_used: Tuple[str, ...] = field(default_factory=tuple)
    who_evidence_ids: Tuple[str, ...] = field(default_factory=tuple)
    why_evidence_ids: Tuple[str, ...] = field(default_factory=tuple)
    weapon_evidence_ids: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id,
            "caseVersion": self.case_version,
            "solverProofVersion": self.solver_proof_version,
            "validation": self.validation.to_dict(),
            "winners": {"murderer": self.winners[0], "motive": self.winners[1],
                        "weapon": self.winners[2]},
            "crimeTime": self.time.to_dict(),
            "evidenceIdsUsed": list(self.evidence_ids_used),
            "whoEvidenceIds": list(self.who_evidence_ids),
            "whyEvidenceIds": list(self.why_evidence_ids),
            "weaponEvidenceIds": list(self.weapon_evidence_ids),
        }


def evaluate_solution(proof: SolverProof, truth: CaseTruth) -> AccusedSolutionValidation:
    """Per-dimension survivor == canonical + accepted-scoring time checks.

    Truth-aware, strictly post-deduction (§31.10 "CaseTruth is used only for
    final comparison").
    """
    if not isinstance(truth, CaseTruth):
        raise TypeError(f"truth must be a CaseTruth; got {type(truth).__name__}")
    if proof.who is None or proof.why is None or proof.weapon is None or proof.when is None:
        raise ValueError("proof is incomplete (missing a dimension result)")

    crime = truth.crime
    canonical_tick = parse_iso8601_to_epoch(crime.crime_time.canonical)
    tolerance = crime.crime_time.accusation_tolerance_seconds

    murderer_true = proof.who.winner == crime.murderer_id
    motive_true = proof.why.winner == crime.motive_id
    weapon_true = proof.weapon.winner == crime.weapon_id

    single_interval = proof.when.connected_count == 1
    overconstrained = getattr(proof.when, "overconstrained", proof.when.feasible.is_empty)
    accepted: IntervalSet = accepted_scoring_time_set(canonical_tick, tolerance)
    within_scoring = proof.when.feasible.is_subset_of(accepted)
    canonical_in = proof.when.feasible.contains(canonical_tick)
    time_accepted = (
        single_interval and within_scoring and canonical_in and not overconstrained
    )

    return AccusedSolutionValidation(
        murderer_true=murderer_true,
        motive_true=motive_true,
        weapon_true=weapon_true,
        time_unique_single_interval=single_interval,
        time_within_scoring=within_scoring,
        canonical_in_feasible=canonical_in,
        time_overconstrained=overconstrained,
        time_accepted=time_accepted,
        all_true=murderer_true and motive_true and weapon_true and time_accepted,
        deducted_murderer=proof.who.winner,
        deducted_motive=proof.why.winner,
        deducted_weapon=proof.weapon.winner,
        canonical_murderer=crime.murderer_id,
        canonical_motive=crime.motive_id,
        canonical_weapon=crime.weapon_id,
    )


def _assert_critical_evidence_reachable(proof: SolverProof, evidence: Iterable[EvidenceFact]) -> None:
    """Light §31.16 / 65 reachability-as-of-publication static check.

    Every excluded/time-critical evidence id referenced by the proof must
    exist in the published evidence set and be discoverable.
    """
    by_id = evidence_by_id(evidence)
    referenced: set[str] = set(proof.when.critical_evidence_ids if proof.when else ())
    for outcome in proof.rule_outcomes_by_candidate:
        referenced.update(outcome.evidence_ids)
    missing = sorted(i for i in referenced if i not in by_id)
    if missing:
        raise ValueError(
            "reachability failure: proof references evidence ids not present in "
            f"the published evidence set: {missing}"
        )
    hidden = sorted(i for i in referenced if not by_id[i].discoverable)
    if hidden:
        raise ValueError(
            "reachability failure: proof relies on non-discoverable evidence: "
            f"{hidden}"
        )


def _dimension_evidence_ids(
    dim: CandidateDimensionResult | None,
) -> Tuple[str, ...]:
    """Sorted union of the rule-outcome evidence ids of ONE deduction dimension.

    Phase18C: the reveal-safe per-dimension proof mapping is built ONLY from
    these server-side references (plus ``when.critical_evidence_ids``) — never
    from re-running the solver or from rule/outcome internals. A ``None``
    dimension contributes nothing.
    """
    used: set[str] = set()
    if dim is not None:
        for outcome in dim.rule_outcomes:
            used.update(str(i) for i in (outcome.evidence_ids or ()))
    return tuple(sorted(used))


def assemble_solution_proof(
    public: PublicCase,
    evidence: Iterable[EvidenceFact],
    proof: SolverProof,
    truth: CaseTruth,
) -> SolutionProof:
    """Assemble the final server-only SolutionProof (§31.14) from proof + truth.

    Runs the AccusedSolutionValidation, the reachability static check, and
    renders the accepted-scoring + feasible time intervals in the truth's
    timezone. Additionally snapshots the per-dimension usable evidence ids
    (WHO/WHY/WEAPON from the deduction rule outcomes; WHEN reuses the time
    proof's critical ids) so the post-reveal proof board can map each dimension
    to supporting discovered evidence WITHOUT re-running the solver.
    """
    ensure_public_case(public)
    evidence = tuple(evidence)
    validation = evaluate_solution(proof, truth)
    _assert_critical_evidence_reachable(proof, evidence)

    offset = iso_offset_minutes(truth.crime.crime_time.canonical)
    canonical_tick = parse_iso8601_to_epoch(truth.crime.crime_time.canonical)
    tolerance = truth.crime.crime_time.accusation_tolerance_seconds
    accepted = accepted_scoring_time_set(canonical_tick, tolerance)

    accepted_interval = accepted.intervals[0]
    accepted_scoring = AcceptedScoring(
        canonical=truth.crime.crime_time.canonical,
        start_inclusive=epoch_to_iso(accepted_interval.start_inclusive, offset),
        end_exclusive=epoch_to_iso(accepted_interval.end_exclusive, offset),
    )
    feasible_intervals: Tuple[Tuple[str, str], ...] = tuple(
        (epoch_to_iso(iv.start_inclusive, offset), epoch_to_iso(iv.end_exclusive, offset))
        for iv in proof.when.feasible.intervals
    )
    time_proof = TimeProof(
        canonical=truth.crime.crime_time.canonical,
        accepted_scoring=accepted_scoring,
        feasible_intervals=feasible_intervals,
        critical_evidence_ids=proof.when.critical_evidence_ids,
        connected_count=proof.when.connected_count,
        ambiguous=proof.when.ambiguous,
        overconstrained=getattr(proof.when, "overconstrained", proof.when.feasible.is_empty),
    )
    return SolutionProof(
        case_id=public.case_id,
        case_version=public.case_version,
        solver_proof_version=proof.version,
        validation=validation,
        winners=(proof.who.winner or "", proof.why.winner or "", proof.weapon.winner or ""),
        time=time_proof,
        evidence_ids_used=proof.evidence_ids_used,
        who_evidence_ids=_dimension_evidence_ids(proof.who),
        why_evidence_ids=_dimension_evidence_ids(proof.why),
        weapon_evidence_ids=_dimension_evidence_ids(proof.weapon),
    )