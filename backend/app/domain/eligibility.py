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
from typing import Any, Iterable, Mapping

from app.domain.public import PublicCase

SUSPECT_ELIGIBLE = "SUSPECT_ELIGIBLE"
MOTIVE_CANDIDATE = "MOTIVE_CANDIDATE"
POTENTIAL_WEAPON = "POTENTIAL_WEAPON"

# Version of the eligibility derivation rules (part of the published snapshot,
# REQUIREMENTS 7.4: the candidate-universe/eligibility version is version-locked
# with the proof at publication).
ELIGIBILITY_VERSION = "1.0"

# Phase41 — the CLOSED public roles that make a person a FINAL HOMICIDE TARGET
# (§8 Option B): suspects AND witnesses. ``family``/``other``/``victim`` are
# intentionally excluded (the victim is represented separately via the suicide
# option).
FINAL_HOMICIDE_TARGET_ROLES = frozenset({"suspect", "witness"})
_VICTIM_ROLE = "victim"


def suspect_universe(public: PublicCase) -> tuple[str, ...]:
    """Sorted ids of persons marked ``SUSPECT_ELIGIBLE`` (§31.1.1)."""
    return tuple(
        sorted(
            p.person_id for p in public.persons if SUSPECT_ELIGIBLE in p.public_affordances
        )
    )


def final_homicide_target_ids(public: PublicCase) -> tuple[str, ...]:
    """Phase41 — the deterministic FINAL HOMICIDE TARGET universe (§8 Option B).

    ``(person_id, role)`` pairs from the PUBLIC model: every public person with
    role ``suspect`` OR ``witness``, excluding the victim and any invalid /
    hidden-only record. Truth-independent, stable, deterministic, deduplicated
    and sorted. This universe is BROADER than ``suspect_universe`` (witnesses
    are final targets without being investigative suspects) and is bound by the
    architecture boundary: this module NEVER imports ``app.domain.truth``
    (asserted by ``tests/test_boundaries.py``).
    """
    return final_homicide_target_ids_of_records(
        ({"person_id": p.person_id, "role": p.role} for p in public.persons)
    )


def final_homicide_target_ids_of_records(
    records: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """The same derivation over serialized person records (``{"person_id",
    "role"}``) — used by the payload-dict projections (reveal / accusation /
    savegame). Records with an unknown/absent role, an empty person id, or the
    victim are never final targets.
    """
    records = tuple(records)
    victim_ids = {
        str(record.get("person_id"))
        for record in records
        if str(record.get("role")) == _VICTIM_ROLE
    }
    targets = {
        str(record.get("person_id"))
        for record in records
        if str(record.get("person_id"))
        and str(record.get("role")) in FINAL_HOMICIDE_TARGET_ROLES
        and str(record.get("person_id")) not in victim_ids
    }
    return tuple(sorted(targets))


def has_public_victim(public: PublicCase) -> bool:
    """True when a public ``role == "victim"`` person exists — the suicide
    option is present exactly then (§20/§23 ``hasSuicideOption``)."""
    return any(p.role == _VICTIM_ROLE for p in public.persons)


def has_public_victim_of_records(records: Iterable[Mapping[str, Any]]) -> bool:
    """Payload-dict variant of ``has_public_victim``."""
    return any(str(record.get("role")) == _VICTIM_ROLE for record in records)


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