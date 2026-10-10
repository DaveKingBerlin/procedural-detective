"""Phase35 — deterministic generated-case quality validator (CLOSED codes).

Pure, ZERO-provider, deterministic quality gates over the PARSED canonical
objects of a generation draft. The phase objective (Phase35-Fix-GCQ.md §6) is
generated cases that are structurally valid, candidate-consistent, truth-
consistent and witness-complete. The three Demo golden exports each satisfy
every rule here; the committed negative Ollama export (``CASE-LZig0W97AIyW``)
triggers exactly the four confirmed codes:

    PUBLIC_ROLE_TRUTH_LEAK            daniel_roth role="murderer"
    VICTIM_IN_SUSPECT_CANDIDATES      laura_stein (role victim) SUSPECT_ELIGIBLE
    WITNESS_IN_SUSPECT_CANDIDATES     nina_weber (role witness) SUSPECT_ELIGIBLE
    WITNESS_STATEMENT_MISSING         nina_weber has ZERO attributed witness-kind evidence

Design rules:

- The single entry ``validate_case_quality`` operates on PARSED canonical
  objects (``PublicCase``, ``CaseTruth``, evidence facts, ``CandidateUniverses``
  and the ``GeneratedDraft``) — never on raw provider JSON, never re-parses.
- Candidate membership is the DERIVED universe (``derive_universes`` /
  ``SUSPECT_ELIGIBLE``); ``app.domain.eligibility`` is NOT changed.
- Public person roles are a CLOSED vocabulary: ``victim|suspect|witness|
  family|other`` (the exact contract the prompts teach — prompts.py ``_CASE_
  FIELD_RULES`` / ``_stage_contract("case_people")``). ``role == "murderer"``
  and any other token is a pre-reveal truth leak.
- Witness completeness reuses the CANONICAL attribution helpers
  (``app.domain.witness.witness_attributed_to`` — kind in ``WITNESS_KINDS`` AND
  a proposition ``person_id`` OR the presentation ``speakerName`` normalizing to
  the witness id/name under DEF-054 identity normalization). A REMOTE_STATEMENT
  witness with ZERO attributed witness-kind facts answers NEUTRAL to all six
  questions forever (``project_witness_statement`` witness.py:686-714 returns
  a NEUTRAL ``WitnessStatement`` with ``grounded=False`` and empty
  ``evidence_ids``) and the interview DISCOVERS nothing (witnesses.py:183-190
  only calls ``_discover_grounding`` for ``statement.grounded``). A published
  case with an interviewable witness that can never provide a usable statement
  is a Phase35 primary-requirement defect (Phase35 §8) — a HARD quality gate.
  GROUNDED-COVERAGE DEPTH (DEF-077): "answers NEUTRAL to all six questions
  forever = defect", but the gate also requires a MINIMUM answer surface — an
  attributed fact whose ONLY text is a ``title`` never certifies a witness
  (the canonical grounding ``_time_anchors_of`` reads only statement +
  description for clock tokens, so a bare title can ground at best a single
  generic OBSERVATION and never TIME/LOCATION/… depth; the goldens' witnesses
  ground 4/6). ``_usable_statement_text`` therefore accepts ONLY non-empty
  ``statement``/``description`` — never a plain ``title``.

Issue codes are CLOSED and safe (never statement text, never murderer names in
diagnostics; the codes themselves are the repair guidance — §37 bounded
context carries the allowed ids separately).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.domain import witness as witness_domain
from app.generation.constraints import normalize_identity

# --------------------------------------------------------------------------- #
# closed public role vocabulary (the ONLY public pre-reveal roles)
# --------------------------------------------------------------------------- #

PUBLIC_ROLE_VOCABULARY: frozenset[str] = frozenset(
    {"victim", "suspect", "witness", "family", "other"}
)

# --------------------------------------------------------------------------- #
# closed Phase35 issue codes
# --------------------------------------------------------------------------- #

PUBLIC_ROLE_TRUTH_LEAK = "PUBLIC_ROLE_TRUTH_LEAK"
VICTIM_IN_SUSPECT_CANDIDATES = "VICTIM_IN_SUSPECT_CANDIDATES"
WITNESS_IN_SUSPECT_CANDIDATES = "WITNESS_IN_SUSPECT_CANDIDATES"
MURDERER_NOT_SUSPECT_CANDIDATE = "MURDERER_NOT_SUSPECT_CANDIDATE"
WITNESS_STATEMENT_MISSING = "WITNESS_STATEMENT_MISSING"
WITNESS_STATEMENT_EMPTY = "WITNESS_STATEMENT_EMPTY"
WITNESS_STATEMENT_UNKNOWN_WITNESS = "WITNESS_STATEMENT_UNKNOWN_WITNESS"
WITNESS_STATEMENT_WITNESS_ID_MISMATCH = "WITNESS_STATEMENT_WITNESS_ID_MISMATCH"
WITNESS_STATEMENT_SPEAKER_MISMATCH = "WITNESS_STATEMENT_SPEAKER_MISMATCH"


# --------------------------------------------------------------------------- #
# canonical payload-view builder (never re-parses provider JSON)
# --------------------------------------------------------------------------- #


def _payload_view(
    public: Any, evidence: Iterable[Any]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """A minimal deterministic payload-view of the PARSED canonical objects.

    Reuses the EXACT serialized shape ``app.domain.witness`` helpers consume
    (``draft.persons`` / ``draft.evidence`` with ``kind``, ``propositions``
    ``person_id`` refs and the allowlisted ``presentation``) so attribution is
    computed by the canonical helper — never re-invented, never a raw provider
    re-parse.
    """
    persons = [
        {
            "person_id": getattr(person, "person_id"),
            "name": getattr(person, "name"),
            "role": getattr(person, "role"),
            "affordances": sorted(getattr(person, "public_affordances") or ()),
        }
        for person in getattr(public, "persons", ())
        if getattr(person, "person_id", None) is not None
    ]
    facts: list[dict[str, Any]] = []
    for fact in evidence:
        presentation = dict(getattr(fact, "presentation", None) or {})
        propositions = tuple(getattr(fact, "propositions", ()) or ())
        fact_view: dict[str, Any] = {
            "id": str(getattr(fact, "id", "")),
            "kind": str(getattr(fact, "kind", "") or ""),
            "presentation": presentation,
            "propositions": [
                {"person_id": getattr(prop, "person_id", None)}
                for prop in propositions
            ],
        }
        discoverable = getattr(fact, "discoverable", None)
        if discoverable is not None:
            fact_view["discoverable"] = discoverable
        facts.append(fact_view)
    return {"draft": {"persons": persons, "evidence": facts}}


def _id_by_role(public: Any, role: str) -> frozenset[str]:
    """Public person ids with exactly ``role`` (closed-role membership)."""
    return frozenset(
        str(getattr(person, "person_id"))
        for person in getattr(public, "persons", ())
        if str(getattr(person, "role", "")) == role and getattr(person, "person_id")
    )


def _witness_ids(public: Any) -> frozenset[str]:
    return _id_by_role(public, "witness")


def _usable_statement_text(presentation: Mapping[str, Any]) -> str:
    """The canonical 'usable statement text' of one witness-kind fact.

    Requires a non-empty ``statement`` or ``description`` (DEF-077): a bare
    ``title`` is NOT an interview surface. The canonical TIME grounding
    (``app.domain.witness._time_anchors_of``) and the fallback observation
    text (``_fact_fallback_text``) read only statement/description for clock
    tokens and text-mention grounding, so a title-only fact can never support
    the grounded-coverage depth the goldens satisfy (4/6 questions). Empty
    when the fact has no usable answer surface.
    """
    for key in ("statement", "description"):
        value = presentation.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _attributed_witness_id(
    view: Mapping[str, Any], fact_view: Mapping[str, Any]
) -> str | None:
    """The canonical witness the fact is ATTRIBUTED to, or None.

    Reuses ``app.domain.witness.witness_attributed_to`` (witness.py:618-656):
    kind in ``WITNESS_KINDS`` AND (proposition ``person_id`` equals a public
    role=='witness' person's id OR ``speakerName`` normalizes to the witness
    id/name).
    """
    return witness_domain.witness_attributed_to(view, fact_view)


# --------------------------------------------------------------------------- #
# rules (closed codes only; sorted unique output)
# --------------------------------------------------------------------------- #


def _public_role_truth_leak(public: Any) -> tuple[str, ...]:
    """A public person role outside the CLOSED public role vocabulary.

    ``role == "murderer"`` (or any non-vocabulary token) exposes/reveals the
    killer before THE TRUTH — the golden exports all keep the true murderer
    publicly ``role=suspect``. Phase35 §11 / §10 expected security outcome:
    ``role=murderer`` is NEVER permitted in a pre-reveal public DTO.
    """
    offending = [
        str(getattr(person, "person_id"))
        for person in getattr(public, "persons", ())
        if str(getattr(person, "role", "")) not in PUBLIC_ROLE_VOCABULARY
    ]
    if offending:
        return (PUBLIC_ROLE_TRUTH_LEAK,)
    return ()


def _candidate_role_issues(
    public: Any, universes: Mapping[str, Any]
) -> tuple[str, ...]:
    """Victim/witness in the DERIVED suspect candidates.

    ``universes.suspect_ids`` is the SUSPECT_ELIGIBLE derivation
    (``derive_universes``); a victim/witness carrying ``SUSPECT_ELIGIBLE`` is a
    candidate the player could accuse as the killer — phase phase-10/§10 hard
    invariants ("victim is never a suspect candidate; witness is never a
    suspect candidate"). Ghosts of the golden files: victims/witnesses carry
    ONLY ``VISIBLE_CHARACTER``.
    """
    suspect_ids = frozenset(getattr(universes, "suspect_ids", ()) or ())
    victims = _id_by_role(public, "victim") & suspect_ids
    witnesses = _id_by_role(public, "witness") & suspect_ids
    codes: list[str] = []
    if victims:
        codes.append(VICTIM_IN_SUSPECT_CANDIDATES)
    if witnesses:
        codes.append(WITNESS_IN_SUSPECT_CANDIDATES)
    return tuple(codes)


def _murderer_candidate_issue(
    truth: Any, universes: Mapping[str, Any]
) -> tuple[str, ...]:
    """The CaseTruth murderer must be one of the accuseable candidate suspects.

    The player must be able to accuse the actual murderer (phase-10/§10 §16).
    NOTE (diagnostic-quality): the solver gate independently guarantees
    accusal — a canonical murderer OUTSIDE the SUSPECT_ELIGIBLE universe can
    never be the deduced solver winner, so ``evaluate_solution`` reports the
    truth mismatch (all_true False) and the case is already REPAIR-class. This
    code therefore fires EARLY with a precise targeted code and does NOT
    double-fail the classification (both avenues classify RECOVERABLE_REPAIR;
    the solver bucket is skipped on a quality-broken draft).
    """
    murderer_id = None
    crime = getattr(truth, "crime", None)
    if crime is not None:
        murderer_id = getattr(crime, "murderer_id", None)
    suspect_ids = frozenset(getattr(universes, "suspect_ids", ()) or ())
    if murderer_id and murderer_id not in suspect_ids:
        return (MURDERER_NOT_SUSPECT_CANDIDATE,)
    return ()


def _witness_complete_issues(
    public: Any, evidence: Iterable[Any], view: Mapping[str, Any]
) -> tuple[str, ...]:
    """Witness completeness (the primary Phase35 requirement, §8).

    For every public role=='witness' person (interviewable — witness.py
    ``witness_person_of``):

    - ``WITNESS_STATEMENT_MISSING``: the witness has ZERO attributed witness-
      kind facts (canonical ``witness_attributed_to``). Such a witness answers
      NEUTRAL to all six questions forever and the interview performs ZERO
      discovery (witnesses.py:183-190) — a published interviewable witness with
      no usable statement.
    - ``WITNESS_STATEMENT_EMPTY``: attributed witness-kind facts exist but
      NONE carry usable statement text (a non-empty ``statement`` or
      ``description`` — a bare ``title`` does NOT count, DEF-077) — the
      witness cannot ground a single interview answer.
    - Per witness-kind fact the EXPLICIT identity tags must agree with the
      canonical attribution:
        ``WITNESS_STATEMENT_UNKNOWN_WITNESS``       a witnessId tag that is
            not a public role=='witness' person id (dangling reference);
        ``WITNESS_STATEMENT_WITNESS_ID_MISMATCH``   an explicit witnessId that
            resolves to a DIFFERENT public witness than the attribution;
        ``WITNESS_STATEMENT_SPEAKER_MISMATCH``      an explicit speakerName that
            does not normalize (DEF-054) to the attributed witness id/name
            (proposition-attributed facts whose tag contradicts the speaker).

    sceneObjectId=null for a REMOTE_STATEMENT witness is canonical and VALID
    (goldens: sceneObjectId null) — never a code here.
    """
    witnesses = _witness_ids(public)
    if not witnesses:
        return ()
    codes: list[str] = []
    facts = tuple(_evidence_of(view))

    # per-fact explicit identity tags
    for fact in facts:
        kind = str(fact.get("kind") or "")
        if kind not in witness_domain.WITNESS_KINDS:
            continue
        presentation = fact.get("presentation") or {}
        explicit_wid = presentation.get("witnessId")
        speaker = presentation.get("speakerName")
        attributed = _attributed_witness_id(view, fact)
        if isinstance(explicit_wid, str) and explicit_wid:
            if explicit_wid not in witnesses:
                codes.append(WITNESS_STATEMENT_UNKNOWN_WITNESS)
            elif attributed is not None and explicit_wid != attributed:
                codes.append(WITNESS_STATEMENT_WITNESS_ID_MISMATCH)
        if isinstance(speaker, str) and speaker and attributed is not None:
            norm_speaker = normalize_identity(speaker)
            attributed_name = _person_name(view, attributed)
            if norm_speaker and norm_speaker not in {
                normalize_identity(attributed),
                normalize_identity(attributed_name or ""),
            }:
                codes.append(WITNESS_STATEMENT_SPEAKER_MISMATCH)

    # per-witness coverage
    for witness_id in sorted(witnesses):
        attributed_facts = [
            fact
            for fact in facts
            if str(fact.get("kind") or "") in witness_domain.WITNESS_KINDS
            and _attributed_witness_id(view, fact) == witness_id
        ]
        if not attributed_facts:
            codes.append(WITNESS_STATEMENT_MISSING)
            continue
        if not any(
            _usable_statement_text(fact.get("presentation") or {})
            for fact in attributed_facts
        ):
            codes.append(WITNESS_STATEMENT_EMPTY)
    return tuple(codes)


def _evidence_of(view: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(view.get("draft", {}).get("evidence") or ())


def _person_name(view: Mapping[str, Any], person_id: str) -> str | None:
    for person in view.get("draft", {}).get("persons") or ():
        if str(person.get("person_id")) == person_id:
            return person.get("name")
    return None


# --------------------------------------------------------------------------- #
# single deterministic entry point
# --------------------------------------------------------------------------- #


def validate_case_quality(
    public: Any,
    truth: Any,
    evidence: Iterable[Any],
    universes: Any,
    draft: Any = None,
) -> tuple[str, ...]:
    """The Phase35 deterministic case-quality gate (CLOSED issue codes).

    Args:
        public: the parsed ``PublicCase`` (persons with role/affordances/docs).
        truth: the parsed ``CaseTruth`` (``.crime.murderer_id`` consumed).
        evidence: iterable of PARSED canonical evidence facts exposing
            ``id``/``kind``/``presentation`` (Mapping)/``propositions``
            (objects with ``person_id``). ``EvidenceFact`` satisfies this.
        universes: the ``CandidateUniverses`` (derived suspect ids).
        draft: the parsed ``GeneratedDraft`` (unused by the current closed
            rules; kept in the signature per the pipeline contract).

    Returns the sorted, de-duplicated tuple of closed code strings. Empty
    tuple == PASS. ZERO provider calls; fully deterministic; safe to rerun
    after every repair (REQUIREMENTS 32.13).
    """
    codes: set[str] = set()
    codes.update(_public_role_truth_leak(public))
    codes.update(_candidate_role_issues(public, universes))
    codes.update(_murderer_candidate_issue(truth, universes))
    evidence_tuple = tuple(evidence)
    view = _payload_view(public, evidence_tuple)
    codes.update(_witness_complete_issues(public, evidence_tuple, view))
    return tuple(sorted(codes))


__all__ = [
    "MURDERER_NOT_SUSPECT_CANDIDATE",
    "PUBLIC_ROLE_TRUTH_LEAK",
    "PUBLIC_ROLE_VOCABULARY",
    "VICTIM_IN_SUSPECT_CANDIDATES",
    "WITNESS_IN_SUSPECT_CANDIDATES",
    "WITNESS_STATEMENT_EMPTY",
    "WITNESS_STATEMENT_MISSING",
    "WITNESS_STATEMENT_SPEAKER_MISMATCH",
    "WITNESS_STATEMENT_UNKNOWN_WITNESS",
    "WITNESS_STATEMENT_WITNESS_ID_MISMATCH",
    "validate_case_quality",
]
