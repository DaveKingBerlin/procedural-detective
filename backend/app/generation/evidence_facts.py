"""Phase41 — deterministic deceptive-witness evidence support (§14).

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


def deceptive_witness_truths(crime: Any, public: Any) -> tuple[WitnessStatementTruth, ...]:
    """Deterministic statement-level truthfulness for the DRIVER's canonical
    witness projections (§12).

    A HOMICIDE witness murderer lies to protect themselves: their OBSERVATION
    statement is ``DECEPTIVE`` with reason ``SELF_PROTECTION``. No meta-rule
    "lying witness == murderer" exists — this ONLY marks the witness who IS
    the hidden killer; an innocent witness is never auto-flagged here (the
    innocent-deceptive-witness scenario is fixture-driven per §13/§30).

    Deterministic and idempotent: the same ``(crime, public)`` always yields
    the same statement-level map.
    """
    witness_murderer = witness_murderer_of(crime, public)
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
    "deceptive_statement_text",
    "deceptive_witness_truths",
    "mechanical_contradiction_fact_ids",
    "witness_murderer_of",
]