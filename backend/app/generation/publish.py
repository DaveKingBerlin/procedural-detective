"""Publication gate — the frozen published CaseVersion aggregate (§7.4/§7.5, F).

``PublishedCaseVersion`` is the atomic frozen payload of a successful
generation: the draft, the hidden ``CaseTruth``, the public model, the
evidence set, the candidate-universe snapshot, the final solver proof and the
report that cleared publication. EXACTLY what §34.2/§7.4 version-locks at
``VALIDATING -> PUBLISHED``.

The gallery always unlocks through the compare-and-set gate in the controller
(current active attempt + VALIDATING + VALID); the aggregate itself is
immutable by construction (frozen dataclass over frozen Phase 3 objects).

``to_dict_summary`` is SERVER-ONLY (not an HTTP DTO — publication reveal is a
later phase): it exposes only allowlisted public material (case identity,
universe sizes, solver winners, time interval summary) and never the hidden
truth winners or canonical crime time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

from app.domain.eligibility import CandidateUniverses, derive_universes
from app.domain.evidence import EvidenceFact
from app.domain.public import PublicCase
from app.domain.truth import CaseTruth
from app.generation.constraints import LockedConstraints
from app.generation.pipeline import AttemptRecord, assemble
from app.generation.report import ValidationReport
from app.generation.schemas import GeneratedDraft
from app.validation.solution import (
    AccusedSolutionValidation,
    SolutionProof,
    assemble_solution_proof,
)


@dataclass(frozen=True)
class PublishedCaseVersion:
    """Immutable published generation payload (§7.4 atomically frozen)."""

    case_id: str
    case_version: int
    generation_attempt_id: str
    published_at: float
    draft: GeneratedDraft
    truth: CaseTruth
    public: PublicCase
    evidence: Tuple[EvidenceFact, ...]
    universes: CandidateUniverses
    solver_proof: SolutionProof
    validation: AccusedSolutionValidation
    report: ValidationReport
    locked: LockedConstraints

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("case_id must be a non-empty string")
        if (
            not isinstance(self.case_version, int)
            or isinstance(self.case_version, bool)
            or self.case_version != 1
        ):
            raise ValueError("case_version must be 1 (MVP publishes version 1 only)")
        if not isinstance(self.generation_attempt_id, str) or not self.generation_attempt_id:
            raise ValueError("generation_attempt_id must be a non-empty string")
        if not isinstance(self.published_at, (int, float)) or isinstance(
            self.published_at, bool
        ):
            raise ValueError("published_at must be a float epoch")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        for name, expected in (
            ("draft", GeneratedDraft),
            ("truth", CaseTruth),
            ("public", PublicCase),
            ("universes", CandidateUniverses),
            ("solver_proof", SolutionProof),
            ("validation", AccusedSolutionValidation),
            ("report", ValidationReport),
            ("locked", LockedConstraints),
        ):
            if not isinstance(getattr(self, name), expected):
                raise TypeError(
                    f"published.{name} must be a {expected.__name__}; "
                    f"got {type(getattr(self, name)).__name__}"
                )

    # -- server-internal summary (NOT an HTTP DTO) ---------------------------

    def to_dict_summary(self) -> dict[str, Any]:
        """Allowlisted server-only summary.

        Contains NO hidden truth winners, NO canonical crime time and NO
        comparison internals — only public/solver-derived material.
        """
        proof = self.solver_proof
        return {
            "caseId": self.case_id,
            "caseVersion": self.case_version,
            "generationAttemptId": self.generation_attempt_id,
            "publishedAt": self.published_at,
            "universes": {
                "suspects": len(self.universes.suspect_ids),
                "motives": len(self.universes.motive_ids),
                "weapons": len(self.universes.weapon_ids),
            },
            "solverWinners": {  # deduction winners (public solver material)
                "murderer": proof.winners[0],
                "motive": proof.winners[1],
                "weapon": proof.winners[2],
            },
            "time": {
                "connectedCount": proof.time.connected_count,
                "ambiguous": proof.time.ambiguous,
                "overconstrained": proof.time.overconstrained,
                "feasibleIntervalCount": len(proof.time.feasible_intervals),
            },
            "evidenceCount": len(self.evidence),
            "playersVisible": all(fact.discoverable for fact in self.evidence),
        }


def build_published_case_version(attempt: AttemptRecord) -> PublishedCaseVersion:
    """Build (but do NOT commit) the frozen published payload for an attempt.

    Reassembles the Phase 3 objects, derives universes, and assembles the
    final server-only ``SolutionProof`` (§31.14) with the accepted-scoring time
    block and the light §31.16 reachability check.
    """
    if attempt.draft is None:
        raise ValueError("cannot publish an attempt with no validated draft")
    public, evidence, truth, _draft = assemble(attempt)
    universes = derive_universes(public)
    if attempt.solver_proof is None:
        raise ValueError("cannot publish an attempt with no solver proof")
    if attempt.last_validation is None:
        raise ValueError("cannot publish an attempt with no validation report")
    proof = assemble_solution_proof(
        public, evidence, attempt.solver_proof, truth
    )
    return PublishedCaseVersion(
        case_id=attempt.case_id,
        case_version=1,
        generation_attempt_id=attempt.attempt_id,
        published_at=float(attempt.published_at),
        draft=attempt.draft,
        truth=truth,
        public=public,
        evidence=evidence,
        universes=universes,
        solver_proof=proof,
        validation=proof.validation,
        report=attempt.last_validation,
        locked=attempt.locked,
    )