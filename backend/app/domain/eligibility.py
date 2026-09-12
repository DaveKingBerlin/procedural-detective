"""Candidate universes — derived ONLY from public eligibility predicates.

REQUIREMENTS 31.1 / 65.1: the persisted candidate universes must equal the
complete set of IDs satisfying the corresponding public eligibility predicate,
computed independently of any hidden solution label. These functions are that
independent derivation. ``CandidateUniverses`` must never be constructed from
``CaseTruth`` (enforced architecturally: this module never imports
``app.domain.truth``, and the import-graph tests in
``backend/tests/test_boundaries.py`` assert it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.domain.public import PublicCase

SUSPECT_ELIGIBLE = "SUSPECT_ELIGIBLE"
MOTIVE_CANDIDATE = "MOTIVE_CANDIDATE"
POTENTIAL_WEAPON = "POTENTIAL_WEAPON"

# Version of the eligibility derivation rules (part of the published snapshot,
# REQUIREMENTS 7.4: the candidate-universe/eligibility version is version-locked
# with the proof at publication).
ELIGIBILITY_VERSION = "1.0"


def suspect_universe(public: PublicCase) -> tuple[str, ...]:
    """Sorted ids of persons marked ``SUSPECT_ELIGIBLE`` (§31.1.1)."""
    return tuple(
        sorted(
            p.person_id for p in public.persons if SUSPECT_ELIGIBLE in p.public_affordances
        )
    )


def motive_universe(public: PublicCase) -> tuple[str, ...]:
    """Sorted ids of motives marked ``MOTIVE_CANDIDATE`` (§31.1.2)."""
    return tuple(
        sorted(
            m.motive_id for m in public.motives if MOTIVE_CANDIDATE in m.public_affordances
        )
    )


def weapon_universe(public: PublicCase) -> tuple[str, ...]:
    """Sorted ids of world objects marked ``POTENTIAL_WEAPON`` (§31.1.3)."""
    return tuple(
        sorted(
            o.object_id for o in public.objects if POTENTIAL_WEAPON in o.public_affordances
        )
    )


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


@dataclass(frozen=True)
class CandidateUniverses:
    """Immutable snapshot of the three candidate universes.

    ``__eq__`` is exact value equality and is tested against independently
    re-derived eligibility sets (REQUIREMENTS 31.1.4 / 65.1).
    """

    eligibility_version: str = ELIGIBILITY_VERSION
    suspect_ids: tuple[str, ...] = field(default_factory=tuple)
    motive_ids: tuple[str, ...] = field(default_factory=tuple)
    weapon_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.eligibility_version, str) or not self.eligibility_version:
            raise ValueError("eligibility_version must be a non-empty string")
        object.__setattr__(self, "suspect_ids", _unique_sorted(self.suspect_ids))
        object.__setattr__(self, "motive_ids", _unique_sorted(self.motive_ids))
        object.__setattr__(self, "weapon_ids", _unique_sorted(self.weapon_ids))


def derive_universes(
    public: PublicCase, eligibility_version: str = ELIGIBILITY_VERSION
) -> CandidateUniverses:
    """Derive candidate universes from public eligibility predicates only."""
    return CandidateUniverses(
        eligibility_version=eligibility_version,
        suspect_ids=suspect_universe(public),
        motive_ids=motive_universe(public),
        weapon_ids=weapon_universe(public),
    )