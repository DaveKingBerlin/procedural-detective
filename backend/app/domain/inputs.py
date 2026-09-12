"""Shared deterministic input guards for solver entry points.

Every public solver entry point (``solve_case`` and the WHEN/WHO/WHY/WEAPON
functions) validates its inputs before doing any deduction:

- the public argument must be a ``PublicCase``;
- the evidence argument must be any ``Iterable[EvidenceFact]`` — list, tuple,
  set or generator are all accepted, and every element must be an
  ``EvidenceFact`` (otherwise ``TypeError``);
- every typed id referenced by the evidence must resolve in the public model,
  evidence ids must be unique, and every time-bearing timestamp must parse and
  lie inside the solver time domain (otherwise ``ValueError``).

Because these guards are exact ``isinstance`` checks, accidentally passing a
``CaseTruth`` object (from outside, e.g. a test or a buggy caller) raises
``TypeError`` before any deduction can touch it.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.domain.evidence import EvidenceFact, ensure_valid_evidence
from app.domain.public import PublicCase


def ensure_public_case(public: Any, param: str = "public") -> None:
    if not isinstance(public, PublicCase):
        raise TypeError(
            f"{param} must be a PublicCase (solver-visible model); "
            f"got {type(public).__name__}. CaseTruth is never a solver input."
        )


def ensure_evidence_facts(evidence: Any, param: str = "evidence") -> None:
    """Check ``evidence`` is an iterable of EvidenceFact elements.

    Accepts any iterable (list, tuple, set, generator, ...). A non-iterable
    (e.g. a bare CaseTruth) or a non-EvidenceFact element raises ``TypeError``.
    """
    if isinstance(evidence, (str, bytes)) or not hasattr(evidence, "__iter__"):
        raise TypeError(
            f"{param} must be an Iterable of EvidenceFact; got {type(evidence).__name__}."
        )
    for index, fact in enumerate(evidence):
        if not isinstance(fact, EvidenceFact):
            raise TypeError(
                f"{param}[{index}] must be an EvidenceFact; got {type(fact).__name__}."
            )


def ensure_solver_input(public: Any, evidence: Any) -> None:
    ensure_public_case(public)
    ensure_evidence_facts(evidence)
    ensure_valid_evidence(public, evidence)


def evidence_by_id(evidence: Iterable[EvidenceFact]) -> dict[str, EvidenceFact]:
    return {fact.id: fact for fact in evidence}