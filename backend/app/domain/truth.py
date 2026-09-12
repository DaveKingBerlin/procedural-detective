"""CaseTruth — server/internal-only canonical truth value objects.

SECURITY CONTRACT (REQUIREMENTS sections 7.6, 41, and Phase3.md):

- ``CaseTruth`` is the canonical hidden solution. It MUST be immutable and it
  MUST be impossible to serialize it accidentally as a public DTO.
- For that reason these are plain ``frozen dataclasses`` (NOT Pydantic
  ``BaseModel``). Pydantic/OpenAPI can never pick them up.
- The deduction solver MUST NEVER access this module. Only
  ``app.validation.solution`` (truth-aware comparison / accepted-scoring
  computation) and truth-independence tests may import it. This boundary is
  enforced by the import-graph tests in ``backend/tests/test_boundaries.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CrimeTime:
    """Canonical crime time (REQUIREMENTS 10.5) with accusation tolerance.

    ``canonical`` is an ISO-8601-with-offset string, e.g.
    ``"2026-09-11T22:17:00+02:00"``.
    ``accusation_tolerance_seconds`` is the player scoring tolerance ``N``
    used by the ACCEPTED-scoring stage (REQUIREMENTS 10.6 / 31.7) — it is
    hidden data and must never reach a solver.
    """

    canonical: str
    accusation_tolerance_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.canonical, str) or not self.canonical:
            raise ValueError("CrimeTime.canonical must be a non-empty ISO-8601 string")
        if not isinstance(self.accusation_tolerance_seconds, int) or isinstance(
            self.accusation_tolerance_seconds, bool
        ):
            raise ValueError("CrimeTime.accusation_tolerance_seconds must be an int")
        if self.accusation_tolerance_seconds < 0:
            raise ValueError("CrimeTime.accusation_tolerance_seconds must be >= 0")


@dataclass(frozen=True)
class Crime:
    """Canonical hidden crime facts (REQUIREMENTS 7.6)."""

    type: str
    victim_id: str
    murderer_id: str
    motive_id: str
    weapon_id: str
    location_id: str
    crime_time: CrimeTime

    def __post_init__(self) -> None:
        for name in ("type", "victim_id", "murderer_id", "motive_id", "weapon_id", "location_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"Crime.{name} must be a non-empty string")
        if not isinstance(self.crime_time, CrimeTime):
            raise ValueError("Crime.crime_time must be a CrimeTime")


@dataclass(frozen=True)
class CaseTruth:
    """Canonical authoritative truth of one published CaseVersion (§7).

    Hidden from players until reveal; used by the deduction engine NEVER as a
    premise — only by the post-hoc truth-aware validation in
    ``app.validation.solution``.
    """

    case_id: str
    case_version: int
    title: str
    crime: Crime
    timeline: tuple[Any, ...] = field(default_factory=tuple)
    persons: tuple[Any, ...] = field(default_factory=tuple)
    relationships: tuple[Any, ...] = field(default_factory=tuple)
    facts: tuple[Any, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("case_id", "title"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"CaseTruth.{name} must be a non-empty string")
        if not isinstance(self.case_version, int) or isinstance(self.case_version, bool):
            raise ValueError("CaseTruth.case_version must be an int")
        if not isinstance(self.crime, Crime):
            raise ValueError("CaseTruth.crime must be a Crime")
        for name in ("timeline", "persons", "relationships", "facts"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                object.__setattr__(self, name, tuple(value))