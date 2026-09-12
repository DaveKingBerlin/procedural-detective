"""Orchestration: ``solve_case`` — the public solver entry point (Phase 3).

Pipeline:

    WHEN  -> FeasibleCrimeTimeSet                    (time constraints from evidence)
    WHO   -> murderer dimension (uses WHEN feasible) (opportunity + alibi + support)
    WHY   -> motive dimension                        (contradicted necessary facts)
    WEAPON-> weapon dimension                        (contradicted necessary forensics)

All four dimensions are derived EXCLUSIVELY from the public model + discoverable
evidence + deterministic rules. ``CaseTruth`` is never an input: calling this
function with a ``CaseTruth`` object raises ``TypeError`` before deduction.
"""

from __future__ import annotations

from typing import Iterable

from app.domain.eligibility import CandidateUniverses, derive_universes
from app.domain.evidence import EvidenceFact
from app.domain.inputs import ensure_solver_input
from app.domain.proof import SolverProof, build_proof
from app.domain.public import PublicCase
from app.domain.solvers.weapon_solver import solve_weapon
from app.domain.solvers.when_solver import solve_when
from app.domain.solvers.who_solver import solve_who
from app.domain.solvers.why_solver import solve_why


def solve_case(public: PublicCase, evidence: Iterable[EvidenceFact]) -> SolverProof:
    """Run the full deterministic deduction pipeline over one public case.

    Args:
        public: the purely public case model (PublicCase).
        evidence: discoverable structured evidence facts (list/tuple of
            EvidenceFact).

    Returns:
        A deterministic ``SolverProof`` with the WHEN/WHO/WHY/WEAPON results.
        The proof contains no CaseTruth-derived premise.

    Raises:
        TypeError: if ``public`` is not a PublicCase or ``evidence`` is not a
            list/tuple of EvidenceFact (e.g. a CaseTruth passed by mistake).
        ValueError: if evidence references unknown public ids or duplicates
            evidence ids.
    """
    evidence = tuple(evidence)
    ensure_solver_input(public, evidence)
    universes: CandidateUniverses = derive_universes(public)

    when = solve_when(public, evidence)
    who = solve_who(public, evidence, when.feasible, universes)
    why = solve_why(public, evidence, universes)
    weapon = solve_weapon(public, evidence, universes)
    return build_proof(
        eligibility=universes,
        when=when,
        who=who,
        why=why,
        weapon=weapon,
    )