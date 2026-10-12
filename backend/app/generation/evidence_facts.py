"""Phase41 + Phase41A — deterministic deceptive-witness evidence support (§14,
§16-§18).

A materially deceptive witness claim must always be checkable: the case must
carry at least one DISCOVERABLE contradiction path. ``MECHANICAL_CONTRADICTION_KINDS``
is the CLOSED set of non-statement evidence kinds that can serve as that path —
activity logs / physical evidence / location-timeline / device records / door
or access records / document time-stamps (Phase41.md §14 examples).

The case-quality gate ``DECEPTIVE_CLAIM_UNCONTRADICTED``
(``app.generation.case_quality``) requires every DECEPTIVE statement-level
truth entry to have at least one discoverable mechanical fact attributing the
witness — so a generated case where the player would have to guess whether a
witness is lying is rejected deterministically.

Phase41A: ``deceptive_witness_truths`` now ALSO procedurally generates a
bounded INNOCENT deceptive witness (``PROTECT_OTHER`` / ``HIDE_UNRELATED_SECRET``)
via a pure seed-branch (see the module-level constants inside that function) —
the earlier FIX-4 "fixture-only innocent liar" boundary is replaced by the
explicit procedural distribution while ``seed=None`` keeps the legacy guilty-only
rule for seed-less callers.
"""

from __future__ import annotations

from typing import Any

from app.domain.resolution import ResolutionType
from app.domain.witness_truth import SELF_PROTECTION, WitnessStatementTruth

SELF_PROTECTION = "SELF_PROTECTION"
PROTECT_OTHER = "PROTECT_OTHER"
HIDE_UNRELATED_SECRET = "HIDE_UNRELATED_SECRET"


def witness_murderer_of(crime: Any, public: Any) -> str | None:
    """The hidden HOMICIDE murderer when they are a public ``witness`` (a
    witness killer is a legal Phase-41 shape: role=witness, no
    SUSPECT_ELIGIBLE, final homicide target, interviewable), or None.

    SUICIDE truths have no murderer, so they can never yield a witness
    murderer here.
    """
    if getattr(crime, "resolution_type", None) is ResolutionType.SUICIDE:
        return None
    murderer_id = getattr(crime, "murderer_id", None)
    if not isinstance(murderer_id, str) or not murderer_id:
        return None
    for person in (getattr(public, "persons", ()) or ()):
        if (
            str(getattr(person, "person_id", "")) == murderer_id
            and str(getattr(person, "role", "")) == "witness"
        ):
            return murderer_id
    return None


# Phase41A — bounded deterministic DECEPTION distribution (documented rule):
#
#   * a HOMICIDE witness murderer is DECEPTIVE (SELF_PROTECTION) in 1 of
#     ``WITNESS_MURDERER_DECEPTION_MOD`` seeds and TRUTHFUL in the others
#     (Phase41A §18/§31 — "a guilty witness always lies" is NOT an invariant);
#
#   * in 1 of ``INNOCENT_DECEPTION_MOD`` seeds, ONE non-murderer witness is
#     DECEPTIVE (``PROTECT_OTHER`` or ``HIDE_UNRELATED_SECRET``); in the rest
#     no innocent witness lies (Phase41A §17/§18 — "the liar is always the
#     murderer" is NOT an invariant either);
#
#   * the selection is a pure ``seed -> stamps`` function (SHA-256 over
#     ``"<seed>:<salt>"``): NO global RNG, NO clock, provider-neutral,
#     reproducible under a fixed seed, and NOT difficulty-weighted (a
#     difficulty signal would deterministically leak the murderer role —
#     Phase41A §20).
#
# ``seed=None`` (legacy callers: fixtures, savegame replay, unit tests) keeps
# the exact Phase-41 FIX-4 semantics: the ONLY generated deceptive path is the
# guilty witness murderer, which preserves `test_fix4_only_guilty_deceptive_
# witness_is_generated` byte-for-byte.
WITNESS_MURDERER_DECEPTION_MOD = 2
INNOCENT_DECEPTION_MOD = 4


def _seed_branch_hash(seed: Any, salt: str) -> int:
    """Pure deterministic ``seed -> int`` branch selector (SHA-256)."""
    import hashlib

    raw = f"{seed}:{salt}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _public_witness_ids(public: Any) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(getattr(p, "person_id", ""))
            for p in (getattr(public, "persons", ()) or ())
            if getattr(p, "person_id") and str(getattr(p, "role", "")) == "witness"
        )
    )


def deceptive_witness_truths(
    crime: Any, public: Any, *, seed: Any = None
) -> tuple[WitnessStatementTruth, ...]:
    """Deterministic statement-level truthfulness for the DRIVER's canonical
    witness projections (§12 / Phase41A §16-§18).

    A HOMICIDE witness murderer MAY lie to protect themselves
    (``SELF_PROTECTION``) — and a non-murderer witness MAY lie too
    (``PROTECT_OTHER`` / ``HIDE_UNRELATED_SECRET``). No meta-rule "lying
    witness == murderer" exists: across seeds BOTH a deceptive guilty witness
    and a deceptive innocent witness are reachable, and truthful witnesses
    (guilty or innocent) are also reachable.

    ``seed`` (the case generation seed) selects the branch deterministically
    (see the module-level constants). When ``seed`` is None the legacy Phase-41
    FIX-4 rule applies (guilty witness murderer only) so seed-less callers and
    fixtures keep their exact behavior.

    Deterministic and idempotent: the same ``(crime, public, seed)`` always
    yields the same statement-level map.
    """
    if getattr(crime, "resolution_type", None) is ResolutionType.SUICIDE:
        return ()
    witness_murderer = witness_murderer_of(crime, public)
    if seed is None:
        # Legacy Phase-41 FIX-4 deterministic rule (see ``_seed_branch_hash``
        # note): the ONLY generated deceptive path is the guilty witness
        # murderer.
        if witness_murderer is None:
            return ()
        return (
            WitnessStatementTruth(
                witness_id=witness_murderer,
                question_type="OBSERVATION",
                truthfulness="DECEPTIVE",
                deception_reason=SELF_PROTECTION,
            ),
        )

    entries: list[WitnessStatementTruth] = []
    # (1) GUILTY witness murderer — deceptive in 1 of WITNESS_MURDERER_
    # DECEPTION_MOD seeds, truthful otherwise (Phase41A §31).
    if witness_murderer is not None and (
        _seed_branch_hash(seed, "witness.murderer.deception")
        % WITNESS_MURDERER_DECEPTION_MOD
        == 0
    ):
        entries.append(
            WitnessStatementTruth(
                witness_id=witness_murderer,
                question_type="OBSERVATION",
                truthfulness="DECEPTIVE",
                deception_reason=SELF_PROTECTION,
            )
        )

    # (2) INNOCENT deceptive witness — in 1 of INNOCENT_DECEPTION_MOD seeds a
    # single non-murderer witness lies (``PROTECT_OTHER`` / ``HIDE_UNRELATED_
    # SECRET``); usually nobody innocent lies (Phase41A §17/§18).
    innocent_witnesses = tuple(
        wid for wid in _public_witness_ids(public) if wid != witness_murderer
    )
    if innocent_witnesses and (
        _seed_branch_hash(seed, "witness.innocent.deception")
        % INNOCENT_DECEPTION_MOD
        == 0
    ):
        liar = innocent_witnesses[
            _seed_branch_hash(seed, "witness.innocent.which")
            % len(innocent_witnesses)
        ]
        reason = (
            HIDE_UNRELATED_SECRET
            if _seed_branch_hash(seed, "witness.innocent.reason") % 2
            else PROTECT_OTHER
        )
        entries.append(
            WitnessStatementTruth(
                witness_id=liar,
                question_type="OBSERVATION",
                truthfulness="DECEPTIVE",
                deception_reason=reason,
            )
        )
    return tuple(entries)


def deceptive_statement_text(
    witness_id: str,
    witness_name: str,
    scene_label: str,
    clock_token: str,
) -> tuple[str, str]:
    """The deterministic PLAYER-VISIBLE deceptive statement text for a lying
    witness (Phase41 §11/§12/§34).

    The text is a bounded, non-graphic investigative claim: the witness claims
    to have left the scene well before the crime time. The contradiction path
    (a door/access record placing them at the scene) is a separate evidence
    fact. The same inputs always yield the same text (idempotence).
    """
    del witness_id  # the id is never rendered in the witness's own words
    statement = (
        f"As {witness_name}, I left the {scene_label} well before {clock_token} "
        "and was nowhere near it for the rest of the evening."
    )
    description = (
        f"{witness_name} claims to have left the {scene_label} before {clock_token}."
    )
    return statement, description


def deceptive_presence_statement_text(
    witness_id: str,
    witness_name: str,
    scene_label: str,
    clock_token: str,
) -> tuple[str, str]:
    """The deterministic PLAYER-VISIBLE deceptive statement text for an
    INNOCENT lying witness (Phase41A §17/§18).

    The bounded claim is the mirror image of ``deceptive_statement_text``: the
    witness falsely claims to have BEEN at the scene around the crime time.
    The contradiction path is the distant access/visual record produced by the
    driver's opportunity algebra (the record places them ELSEWHERE at that
    time), so the same inputs always yield the same text (idempotence) and the
    claim is always discoverably challengeable.
    """
    del witness_id  # the id is never rendered in the witness's own words
    statement = (
        f"As {witness_name}, I was at the {scene_label} around {clock_token} "
        "and remained there for the rest of the evening."
    )
    description = (
        f"{witness_name} claims to have been at the {scene_label} around {clock_token}."
    )
    return statement, description


# The closed mechanical contradiction sources (Phase41.md §14):
#   cctv / cctv_observation / view_record  camera + access records
#   object                                  physical evidence
#   digital / document                      device + door/time records
# ``witness_statement/statement/testimonial/suspect_statement/…`` are the
# WITNESS-KIND claim sources (the thing being contradicted) and are by
# construction never a contradiction path — a claim can never contradict
# itself on authority.
MECHANICAL_CONTRADICTION_KINDS: frozenset[str] = frozenset(
    {
        "cctv",
        "cctv_observation",
        "view_record",
        "object",
        "digital",
        "document",
    }
)


def mechanical_contradiction_fact_ids(
    facts, witness_id: str, *, discoverable_facts: bool = True
) -> tuple[str, ...]:
    """Sorted, deduped ids of the discoverable mechanical facts that reference
    ``witness_id`` — the contradiction paths for a deceptive claim.

    ``facts`` are PARSED canonical evidence objects exposing ``id`` / ``kind``
    / ``presentation`` (Mapping) / ``propositions`` (objects with
    ``person_id``). A fact qualifies when:

    - ``kind`` is a member of ``MECHANICAL_CONTRADICTION_KINDS``;
    - ``discoverable`` is not False;
    - a proposition ``person_id`` equals the witness id, OR the presentation
      carries an explicit ``personId``/``fromPersonId`` equal to the witness id
      (a mechanical record that deterministically implicates the witness).

    Deterministic and pure; used by the ``DECEPTIVE_CLAIM_UNCONTRADICTED``
    gate.
    """
    hits: set[str] = set()
    for fact in facts or ():
        kind = str(getattr(fact, "kind", "") or "")
        if kind not in MECHANICAL_CONTRADICTION_KINDS:
            continue
        if discoverable_facts and getattr(fact, "discoverable", True) is False:
            continue
        witness = False
        for prop in getattr(fact, "propositions", ()) or ():
            if str(getattr(prop, "person_id", "") or "") == witness_id:
                witness = True
                break
        if not witness:
            presentation = getattr(fact, "presentation", None)
            if isinstance(presentation, dict) or hasattr(presentation, "get"):
                for key in ("personId", "fromPersonId"):
                    if str(presentation.get(key) or "") == witness_id:
                        witness = True
                        break
        if witness:
            hits.add(str(getattr(fact, "id", "")))
    return tuple(sorted(h for h in hits if h))


__all__ = [
    "HIDE_UNRELATED_SECRET",
    "MECHANICAL_CONTRADICTION_KINDS",
    "PROTECT_OTHER",
    "SELF_PROTECTION",
    "deceptive_presence_statement_text",
    "deceptive_statement_text",
    "deceptive_witness_truths",
    "mechanical_contradiction_fact_ids",
    "witness_murderer_of",
]