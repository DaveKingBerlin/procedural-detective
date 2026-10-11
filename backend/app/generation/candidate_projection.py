"""Phase40/41 — trusted deterministic candidate-integrity projection.

Phase 40 (unchanged) enforces the two CLOSED protected-role invariants at the
trusted backend: a victim is never a suspect candidate and a witness is never
a suspect candidate. Phase 40's THIRD rule — the trusted murderer always gets
``SUSPECT_ELIGIBLE`` — is exhausted by Phase 41 and replaced with the
resolution-aware final-target rule (Phase41.md §9):

    HOMICIDE:  the murderer must be a FINAL HOMICIDE TARGET (a public suspect
               OR witness). A SUSPECT-role murderer keeps ``SUSPECT_ELIGIBLE``
               (investigative suspect semantics); a WITNESS-role murderer is
               deliberately NOT forced into ``SUSPECT_ELIGIBLE`` (the witness
               stays interviewable + a final target without becoming an
               investigative suspect).
    SUICIDE:   there is no murderer; only the victim/witness eligibility
               removals apply.

This module is a PURE, deterministic, idempotent helper that takes
already-parsed/validated person records and the trusted crime shape
(``murderer_id`` + ``resolution_type``) and corrects ONLY the impossible
candidate-affordance combinations.

Contract (Phase40-Fix.md §7/§10/§11 + Phase41.md §9):

- preserve personId / name / role / presentedData / unrelated affordances /
  person ordering / every other field;
- ``role == victim``    -> remove ``SUSPECT_ELIGIBLE``;
- ``role == witness``   -> remove ``SUSPECT_ELIGIBLE``;
- HOMICIDE murderer     -> must be a final homicide target; a SUSPECT-role
  murderer gets ``SUSPECT_ELIGIBLE`` re-added; a WITNESS-role murderer does
  NOT (the universal murderer-in-suspect rule is retired);
- every other person    -> preserve the current ``SUSPECT_ELIGIBLE`` decision.

ESCALATION (decline — the caller falls back to the canonical CASE_PEOPLE
provider repair or lets the canonical validators classify):

- murderer id missing or unmatchable for HOMICIDE (``DECLINED_INSUFFICIENT_
  CONTEXT``);
- the murderer person record's role is ``victim`` (truth/public mismatch
  beyond candidate membership) or ``family``/``other`` (NOT a final homicide
  target — the projection must never turn a non-target into one);
- candidate universe becomes empty after the closed corrections
  (``DECLINED_CANDIDATE_COUNT``);
- invalid person structure / duplicate ids / out-of-vocabulary public role
  (``FAILED_VALIDATION`` — left to the canonical validators unchanged).

The Phase-40 tests keep passing: a witness murderer was previously DECLINED,
now it is a legal CORRECTED shape (the ONLY behavior change outside the
legacy/suicide paths).
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

# Phase41 — the CLOSED public roles a HOMICIDE murderer may legitimately hold
# (the FINAL HOMICIDE TARGET roles of ``app.domain.eligibility``). A murderer
# with any other public role is not accuseable in the final theory and the
# projection must escalate ($8/$9).
_TARGET_ROLES = frozenset({"suspect", "witness"})


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
    resolution_type: Any = None,
) -> CandidateProjectionResult:
    """The trusted deterministic candidate-integrity projection (§7).

    Args:
        persons: already parsed/validated public person records (``PersonSpec``
            or ``PublicPerson``-compatible) in their original order.
        murderer_id: the trusted ``CaseTruth.crime.murderer_id`` (handed in by
            the trusted caller — this PURE module never imports CaseTruth).
        resolution_type: the trusted resolution (``ResolutionType.HOMICIDE`` /
            ``ResolutionType.SUICIDE`` or their str values). ``None`` == legacy
            homicide (a non-null murderer id).

    Returns a ``CandidateProjectionResult``. A succeeded outcome
    (``NO_CHANGE`` / ``CORRECTED``) carries the corrected person tuple; every
    decline/escalation outcome returns the original tuple UNCHANGED so the
    canonical validators/repair path stay authoritative.
    """
    from app.domain.resolution import ResolutionType

    resolution_token = _resolution_token(resolution_type)
    is_suicide = resolution_token == "SUICIDE"
    require_murderer = resolution_token in ("HOMICIDE", None)
    if require_murderer and not isinstance(murderer_id, str) or not murderer_id:
        if not is_suicide:
            return _declined(
                CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, persons
            )
    # SUICIDE has no murderer: only the two protected-role removals apply.
    if is_suicide:
        return _project_protected_roles(persons, murderer_id=None, murderer_role=None)

    try:
        original = tuple(persons)
    except TypeError:
        # ADV-40-02 — ``persons=None`` / a non-iterable value is a boundary
        # input like the empty tuple: closed ``DECLINED_INSUFFICIENT_CONTEXT``
        # with an empty universe, never a raised ``TypeError``.
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, ()
        )
    if not original:
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, original
        )

    # --- structure / role validation (escalate; never hide) -----------------
    parsed: list[tuple[Any, str, str, Any]] = []
    for person in original:
        if not dataclasses.is_dataclass(person):
            # ADV-40-01 — a duck-typed record exposing the right attribute
            # surface but NOT a dataclass cannot be rebuilt by
            # ``dataclasses.replace`` later. Invalid structure -> closed
            # ``FAILED_VALIDATION`` with the original tuple untouched; never
            # raise.
            return _failed_validation(original)
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
    if murderer_role == _VICTIM:
        # Cannot satisfy both sides of the rules (protected victim role IS the
        # killer) — a truth/public integrity mismatch beyond candidate
        # membership.
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, original
        )
    if murderer_role == _WITNESS:
        # Phase41 — WITNESS MURDERERS ARE LEGAL: the witness is a final
        # homicide target by public rule; the universal murderer-in-suspect
        # rule is retired for witnesses (a witness murderer is NOT forced into
        # SUSPECT_ELIGIBLE). The witness-eligibility removal below runs as the
        # only correction; the murderer add-eligibility step is skipped.
        pass
    elif murderer_role not in _TARGET_ROLES:
        # A family/other (or any non-suspect/witness) murderer is NOT a final
        # homicide target — the projection cannot create one deterministically
        # and must escalate (truth/public mismatch beyond candidate
        # membership). This is the final-target invariant (§8/§9).
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, original
        )

    # --- apply the closed project rules ------------------------------------
    murderer_target_role = (
        murderer_role
        if murderer_role in _TARGET_ROLES
        else None
    )
    return _project_protected_roles(
        original, murderer_id=murderer_id, murderer_role=murderer_target_role
    )


def _resolution_token(resolution_type: Any) -> str | None:
    """The closed str token of a resolution value (``HOMICIDE`` | ``SUICIDE``
    | None for legacy)."""
    if resolution_type is None or resolution_type == "HOMICIDE":
        return None
    value = getattr(resolution_type, "value", resolution_type)
    if value == "SUICIDE":
        return "SUICIDE"
    raise ValueError(f"unsupported resolution_type: {resolution_type!r}")


def _project_protected_roles(
    persons: Iterable[Any],
    *,
    murderer_id: str | None,
    murderer_role: str | None,
) -> CandidateProjectionResult:
    """Apply the two protected-role removals + the HOMICIDE murderer add-rule.

    ``murderer_role`` is ``"suspect"`` for a suspect murderer (eligibility is
    re-added) or ``"witness"`` for a witness murderer (eligibility is NOT
    re-added — the retired universal rule), ``None`` for SUICIDE / no
    murderer-derived addition.

    Structure validation is performed here too (SUICIDE calls this helper
    directly without the HOMICIDE murderer-matching block): invalid records,
    non-dataclass duck-typed records and out-of-vocabulary roles always close
    ``FAILED_VALIDATION`` and never raise (ADV-40-01/-02).
    """
    try:
        original = tuple(persons)
    except TypeError:
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, ()
        )
    if not original:
        return _declined(
            CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT, original
        )
    parsed: list[tuple[Any, str, str, Any]] = []
    for person in original:
        if not dataclasses.is_dataclass(person):
            return _failed_validation(original)
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
        if (
            person_id == murderer_id
            and murderer_role == "suspect"
            and SUSPECT_ELIGIBLE not in new_container
        ):
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
        # ADV-40-03 — a NO_CHANGE pass returns the EXACT input tuple object
        # (never a fresh ``tuple(corrected)``) so the caller's identity guard
        # (``corrected_persons is not original_persons``) skips the pointless
        # ``dataclasses.replace``/allocation churn on unchanged universes.
        corrected_persons=original if not changed else tuple(corrected),
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