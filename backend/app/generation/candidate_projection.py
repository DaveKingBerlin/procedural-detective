"""Phase40 — trusted deterministic candidate-integrity projection.

The model authors public people and their ``SUSPECT_ELIGIBLE`` affordances
independently from the public roles. Three CLOSED candidate-integrity
invariants are enforced by the trusted backend (``case_quality``): a victim is
never a suspect candidate, a witness is never a suspect candidate, and the
trusted murderer (``CaseTruth.crime.murderer_id``) is always a suspect
candidate. Repeatedly repairing these three rules through a full CASE_PEOPLE
provider regeneration is slow, ambiguous (Phase-40 root cause: the repair
regressed the Easy Local-Ollama case) and unnecessary — the backend can
enforce exactly what the three rules demand.

This module is that projection: a PURE, deterministic, idempotent helper that
takes already-parsed/validated person records and the trusted murderer id and
corrects ONLY the impossible candidate-affordance combinations.

Contract (Phase40-Fix.md §7/§10/§11):

- preserve personId / name / role / presentedData / unrelated affordances /
  person ordering / every other field;
- ``role == victim``    -> remove ``SUSPECT_ELIGIBLE``;
- ``role == witness``   -> remove ``SUSPECT_ELIGIBLE``;
- the trusted murderer  -> ensure ``SUSPECT_ELIGIBLE`` exists;
- every other person    -> preserve the current ``SUSPECT_ELIGIBLE`` decision.

The projection NEVER invents content (§13): it does not manufacture red
herrings, does not auto-promote ``other``/``family`` people (Decision B —
``REQUIREMENTS`` treats the candidate count as a difficulty-varying suggestion;
only the prompt teaches a minimum, no canonical rule enforces it), never writes
a public ``murderer`` role (§8/§9) and never hides a genuine person/data defect
from the canonical validators (§35).

ESCALATION (decline — the caller falls back to the canonical CASE_PEOPLE
provider repair or lets the canonical validators classify):

- murderer id missing or unmatchable (``DECLINED_INSUFFICIENT_CONTEXT``);
- the murderer person record's role is itself ``victim``/``witness``
  (truth/public mismatch beyond candidate membership);
- candidate universe becomes empty after the closed corrections
  (``DECLINED_CANDIDATE_COUNT``);
- invalid person structure / duplicate ids / out-of-vocabulary public role
  (``FAILED_VALIDATION`` — left to the canonical validators unchanged).

Purity (§25): no provider call, no randomness, no clock, no network, no DB
mutation. Idempotent (§24): ``project(project(x)) == project(x)``. Person order
preserved (§26). Rebuilt records use a canonical sorted affordance tuple so no
provider-order-dependent diff can appear (§27).

REPAIR-COUNT SEMANTICS (§42): this projection is CANONICAL NORMALIZATION, not
a provider regeneration. It never consumes a provider call and never
increments ``repairCount`` — the controller's ``consume_repair_pass`` is the
only repair counter and is NOT touched here. When the projection runs inside a
controller-dispatched repair pass that pass is the controller's own accounting.
Telemetry stays truthful: ``candidate_integrity.projection`` reports the closed
outcome and bounded eligibility deltas only — never the hidden murderer id,
names, full person records, prompts or raw provider output.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from app.generation.case_quality import PUBLIC_ROLE_VOCABULARY

SUSPECT_ELIGIBLE: str = "SUSPECT_ELIGIBLE"

# Protected public roles that may never be candidate suspects.
_VICTIM = "victim"
_WITNESS = "witness"


class CandidateProjectionOutcome(str, Enum):
    """Closed projection outcome vocabulary (§44, existing naming conventions)."""

    NO_CHANGE = "NO_CHANGE"
    CORRECTED = "CORRECTED"
    DECLINED_INSUFFICIENT_CONTEXT = "DECLINED_INSUFFICIENT_CONTEXT"
    DECLINED_CANDIDATE_COUNT = "DECLINED_CANDIDATE_COUNT"
    FAILED_VALIDATION = "FAILED_VALIDATION"


@dataclass(frozen=True)
class CandidateProjectionResult:
    """Bounded, frozen projection result.

    ``corrected_persons`` for a succeeded outcome (``NO_CHANGE`` /
    ``CORRECTED``) is the rebuilt person tuple; for every decline/escalation
    outcome it is the ORIGINAL input tuple (so canonical validators see the
    untouched data). Only closed tokens, booleans and bounded counts — never
    hidden murderer identity, names, prompts or raw provider content.
    """

    corrected_persons: tuple[Any, ...]
    outcome: CandidateProjectionOutcome
    victim_eligibility_removed: bool
    witness_eligibility_removed_count: int
    murderer_eligibility_added: bool
    candidate_count_before: int
    candidate_count_after: int


def _person_fields(person: Any) -> tuple[str, str, Any] | None:
    """``(person_id, role, affordance_container)`` for a supported record.

    Accepts ``PersonSpec``-like records exposing ``.affordances`` and
    ``PublicPerson``-like records exposing ``.public_affordances``. Returns
    ``None`` when the record has no usable identity/role/affordance surface
    (invalid structure -> ``FAILED_VALIDATION``).
    """
    person_id = getattr(person, "person_id", None)
    role = getattr(person, "role", None)
    if not isinstance(person_id, str) or not person_id:
        return None
    if not isinstance(role, str) or not role:
        return None
    if hasattr(person, "affordances"):
        container: Any = tuple(getattr(person, "affordances", ()) or ())
    elif hasattr(person, "public_affordances"):
        container = frozenset(getattr(person, "public_affordances", ()) or ())
    else:
        return None
    return person_id, role, container


def _candidate_count(persons: Iterable[Any]) -> int:
    """Count of records carrying ``SUSPECT_ELIGIBLE`` (representation-agnostic)."""
    count = 0
    for person in persons:
        fields = _person_fields(person)
        if fields is None:
            continue
        _pid, _role, container = fields
        if SUSPECT_ELIGIBLE in container:
            count += 1
    return count


def _remove_eligible(container: Any) -> Any:
    if isinstance(container, frozenset):
        return frozenset(container - {SUSPECT_ELIGIBLE})
    return tuple(sorted(set(container) - {SUSPECT_ELIGIBLE}))


def _add_eligible(container: Any) -> Any:
    if isinstance(container, frozenset):
        return frozenset(container | {SUSPECT_ELIGIBLE})
    return tuple(sorted(set(container) | {SUSPECT_ELIGIBLE}))


def _rebuild(person: Any, container: Any) -> Any:
    """Rebuild one record with the corrected affordance container (frozen
    dataclass; never mutated in place). Everything else is preserved."""
    if hasattr(person, "affordances"):
        return dataclasses.replace(person, affordances=tuple(container))
    return dataclasses.replace(person, public_affordances=frozenset(container))


def _declined(
    outcome: CandidateProjectionOutcome,
    persons: Iterable[Any],
) -> CandidateProjectionResult:
    original = tuple(persons)
    count = _candidate_count(original)
    return CandidateProjectionResult(
        corrected_persons=original,
        outcome=outcome,
        victim_eligibility_removed=False,
        witness_eligibility_removed_count=0,
        murderer_eligibility_added=False,
        candidate_count_before=count,
        candidate_count_after=count,
    )


def project_candidate_integrity(
    persons: Iterable[Any],
    murderer_id: str | None,
) -> CandidateProjectionResult:
    """The trusted deterministic candidate-integrity projection (§7).

    Args:
        persons: already parsed/validated public person records (``PersonSpec``
            or ``PublicPerson``-compatible) in their original order.
        murderer_id: the trusted ``CaseTruth.crime.murderer_id`` (handed in by
            the trusted caller — this PURE module never imports CaseTruth).

    Returns a ``CandidateProjectionResult``. A succeeded outcome
    (``NO_CHANGE`` / ``CORRECTED``) carries the corrected person tuple; every
    decline/escalation outcome returns the original tuple UNCHANGED so the
    canonical validators/repair path stay authoritative.
    """
    original = tuple(persons)
    if not original:
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, original
        )

    # --- structure / role validation (escalate; never hide) -----------------
    parsed: list[tuple[Any, str, str, Any]] = []
    for person in original:
        fields = _person_fields(person)
        if fields is None:
            return _failed_validation(original)
        person_id, role, container = fields
        if role not in PUBLIC_ROLE_VOCABULARY:
            return _failed_validation(original)
        parsed.append((person, person_id, role, container))
    ids = [entry[1] for entry in parsed]
    if len(set(ids)) != len(ids):
        return _failed_validation(original)

    # --- murderer matching (decline; never guess) ---------------------------
    if not isinstance(murderer_id, str) or not murderer_id:
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, original
        )
    matches = [entry for entry in parsed if entry[1] == murderer_id]
    if not matches:
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, original
        )
    murderer_person, murderer_pid, murderer_role, _container = matches[0]
    if murderer_role in (_VICTIM, _WITNESS):
        # Cannot satisfy both sides of the rules (protected role IS the killer)
        # — a truth/public integrity mismatch beyond candidate membership.
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, original
        )

    # --- apply the closed three rules ---------------------------------------
    before_count = _candidate_count(original)
    corrected: list[Any] = []
    changed = False
    victim_removed = False
    witness_removed = 0
    murderer_added = False
    for person, person_id, role, container in parsed:
        new_container = container
        if role == _VICTIM and SUSPECT_ELIGIBLE in new_container:
            new_container = _remove_eligible(new_container)
            victim_removed = True
        elif role == _WITNESS and SUSPECT_ELIGIBLE in new_container:
            new_container = _remove_eligible(new_container)
            witness_removed += 1
        if person_id == murderer_id and SUSPECT_ELIGIBLE not in new_container:
            new_container = _add_eligible(new_container)
            murderer_added = True
        if new_container != container:
            changed = True
            corrected.append(_rebuild(person, new_container))
        else:
            corrected.append(person)

    after_count = _candidate_count(corrected)
    if after_count == 0:
        # Defensive: the closed rules cannot leave an empty universe — when
        # they would, the case must be escalated rather than auto-invented.
        return _declined(
            CandidateProjectionOutcome.DECLINED_CANDIDATE_COUNT, original
        )

    return CandidateProjectionResult(
        corrected_persons=tuple(corrected),
        outcome=(
            CandidateProjectionOutcome.CORRECTED
            if changed
            else CandidateProjectionOutcome.NO_CHANGE
        ),
        victim_eligibility_removed=victim_removed,
        witness_eligibility_removed_count=witness_removed,
        murderer_eligibility_added=murderer_added,
        candidate_count_before=before_count,
        candidate_count_after=after_count,
    )


def _failed_validation(original: tuple[Any, ...]) -> CandidateProjectionResult:
    return CandidateProjectionResult(
        corrected_persons=original,
        outcome=CandidateProjectionOutcome.FAILED_VALIDATION,
        victim_eligibility_removed=False,
        witness_eligibility_removed_count=0,
        murderer_eligibility_added=False,
        candidate_count_before=_candidate_count(original),
        candidate_count_after=_candidate_count(original),
    )


__all__ = [
    "CandidateProjectionOutcome",
    "CandidateProjectionResult",
    "SUSPECT_ELIGIBLE",
    "project_candidate_integrity",
]