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

PHASE 41 (§10 / ADV-41-07) — ``MURDERER_NOT_SUSPECT_CANDIDATE`` NARROWING
RATIONALE: Phase41 retired the Phase-40 UNIVERSAL "murderer must be in the
SUSPECT_ELIGIBLE candidate universe" rule and replaced it with the FINAL
TARGET invariant (``HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET`` — the murderer must
be a public suspect OR witness, never the victim). The ``MURDERER_NOT_SUSPECT_
CANDIDATE`` code is deliberately RETAINED in a NARROWED form: it now fires ONLY
for a public ``role=suspect`` murderer that is missing from the derived
``SUSPECT_ELIGIBLE`` universe. The narrowing is CORRECT because the frozen
REQUIREMENTS (§17 "exactly one remaining suspect after deterministic
deduction"; §31.1 candidate universes = public eligibility predicates) tie the
DEDUCTION universe to ``SUSPECT_ELIGIBLE`` — a classical investigative suspect
who is also the hidden killer MUST be a valid deduction candidate, so its
absence is an investigative-integrity defect. A WITNESS murderer is
deliberately NOT covered: a witness is a final homicide target but is never a
``SUSPECT_ELIGIBLE`` deduction candidate (extending the deduction universe to
witnesses would drift the frozen REQUIREMENTS), so the witness-murderer shape
is governed by the PRIMARY ``HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET``
invariant alone (a witness murderer IS a final target and therefore never
fires that code either).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.domain import witness as witness_domain
from app.domain.eligibility import (
    final_homicide_target_ids,
    has_public_victim,
)
from app.domain.resolution import ResolutionType
from app.generation.constraints import normalize_identity
from app.generation.evidence_facts import mechanical_contradiction_fact_ids

# --------------------------------------------------------------------------- #
# closed public role vocabulary (the ONLY public pre-reveal roles)
# --------------------------------------------------------------------------- #

PUBLIC_ROLE_VOCABULARY: frozenset[str] = frozenset(
    {"victim", "suspect", "witness", "family", "other"}
)

# --------------------------------------------------------------------------- #
# closed Phase35/Phase41 issue codes
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

# Phase41 — the resolution / final-target integrity codes (Phase41.md §10).
# Truth-shape defects (HOMICIDE murderer missing / SUICIDE murderer present /
# victim-as-murderer / suicide option missing) are CASE_TRUTH-owned in repair
# routing; the final-target code is the deterministic projection path.
HOMICIDE_MURDERER_MISSING = "HOMICIDE_MURDERER_MISSING"
SUICIDE_MURDERER_PRESENT = "SUICIDE_MURDERER_PRESENT"
HOMICIDE_VICTIM_AS_MURDERER = "HOMICIDE_VICTIM_AS_MURDERER"
SUICIDE_OPTION_MISSING = "SUICIDE_OPTION_MISSING"
FINAL_TARGET_UNIVERSE_EMPTY = "FINAL_TARGET_UNIVERSE_EMPTY"
HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET = "HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET"

# Phase41 — a materially deceptive claim with NO discoverable contradiction
# path (the player would have to guess hidden truthfulness) is rejected.
DECEPTIVE_CLAIM_UNCONTRADICTED = "DECEPTIVE_CLAIM_UNCONTRADICTED"


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
    public: Any, truth: Any, universes: Mapping[str, Any]
) -> tuple[str, ...]:
    """Phase41 — resolution / final-target integrity (replaces the Phase-40
    universal "murderer must be SUSPECT_ELIGIBLE" rule).

    HOMICIDE:
      - ``HOMICIDE_MURDERER_MISSING``       murderer id absent/null;
      - ``HOMICIDE_VICTIM_AS_MURDERER``     murderer == victim (never legal);
      - ``FINAL_TARGET_UNIVERSE_EMPTY``     no public suspect/witness at all;
      - ``HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET``  murderer has no public
        suspect/witness role (family/other/victim) and can never be accused;
      - ``MURDERER_NOT_SUSPECT_CANDIDATE``  (KEPT for the classical suspect
        role) a public ``role=suspect`` murderer is missing from the derived
        ``SUSPECT_ELIGIBLE`` universe — an investigative-suspect integrity gap
        (a witness murderer is deliberately NOT covered by this code).

    SUICIDE:
      - ``SUICIDE_MURDERER_PRESENT``        a murderer id on a suicide truth;
      - ``SUICIDE_OPTION_MISSING``          no public victim person exists, so
        the UI can never show the self-inflicted option.

    Everything is deterministically derivable from the canonical domain model
    (``CaseTruth.crime`` + the public persons + the derived universes).
    """
    crime = getattr(truth, "crime", None)
    if crime is None:
        return ()
    resolution = getattr(crime, "resolution_type", None)
    victim_id = str(getattr(crime, "victim_id", "") or "")
    murderer_id = getattr(crime, "murderer_id", None)
    suspect_ids = frozenset(getattr(universes, "suspect_ids", ()) or ())

    codes: list[str] = []
    if resolution is ResolutionType.SUICIDE:
        if murderer_id is not None:
            codes.append(SUICIDE_MURDERER_PRESENT)
        if not _has_public_victim(public):
            codes.append(SUICIDE_OPTION_MISSING)
        return tuple(codes)

    # HOMICIDE (or legacy with a non-null murderer).
    if not isinstance(murderer_id, str) or not murderer_id:
        codes.append(HOMICIDE_MURDERER_MISSING)
        return tuple(codes)
    murderer_role = _role_of(public, murderer_id)
    if murderer_id == victim_id:
        codes.append(HOMICIDE_VICTIM_AS_MURDERER)
    final_targets = _final_targets(public)
    if not final_targets:
        codes.append(FINAL_TARGET_UNIVERSE_EMPTY)
    if murderer_id not in final_targets:
        # Only a CLOSED-vocabulary role can be "not a final target": a
        # ``role=murderer`` / out-of-vocabulary role is already the primary
        # PUBLIC_ROLE_TRUTH_LEAK defect and must not double-classify with the
        # final-target code (keeps the confirmed negative corpus exact).
        if murderer_role in PUBLIC_ROLE_VOCABULARY:
            codes.append(HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET)
    elif murderer_role == "suspect" and murderer_id not in suspect_ids:
        # KEPT (Phase35 semantics): a classical public suspect who is the
        # killer but is not an investigative SUSPECT_ELIGIBLE candidate.
        codes.append(MURDERER_NOT_SUSPECT_CANDIDATE)
    return tuple(codes)


def _role_of(public: Any, person_id: str) -> str | None:
    for person in getattr(public, "persons", ()) or ():
        if str(getattr(person, "person_id", "")) == person_id:
            return str(getattr(person, "role", ""))
    return None


def _final_targets(public: Any) -> frozenset[str]:
    if public is None:
        return frozenset()
    return frozenset(final_homicide_target_ids(public))


def _has_public_victim(public: Any) -> bool:
    if public is None:
        return False
    return has_public_victim(public)


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


def _placement_linked_evidence_ids(draft: Any) -> frozenset[str]:
    """The evidence ids the world graph actually links to a placement.

    Runtime discoverability (``app.services.investigation.discover_evidence`` /
    ``app.services.publication.project_discovery``) requires a placement whose
    ``evidence_id`` references the record — a ``discoverable`` flag alone does
    NOT make a record reachable by the player. Returns the empty set when the
    draft (or its world graph) is unavailable.
    """
    if draft is None:
        return frozenset()
    world_graph = getattr(draft, "world_graph", None)
    if world_graph is None:
        return frozenset()
    return frozenset(
        str(getattr(placement, "evidence_id", "") or "")
        for placement in (getattr(world_graph, "placements", ()) or ())
        if getattr(placement, "evidence_id", None) is not None
    )


def _deceptive_claim_issues(
    truth: Any, evidence: Iterable[Any], draft: Any = None
) -> tuple[str, ...]:
    """Phase41 §14 — every materially deceptive claim needs a contradiction path.

    Reads the trusted ``CaseTruth.witness_truthfulness`` map (statement-level,
    hidden) and requires each entry with ``truthfulness == "DECEPTIVE"`` to
    have at least one DISCOVERABLE mechanical contradiction fact referencing
    the witness (``app.generation.evidence_facts``).

    Phase41 FIX-3 (ADV-41-05) — "exists on paper" is not enough: the
    contradiction record must ALSO be REACHABLE through a world placement
    (the player can only discover evidence linked to a placement via the
    object-interaction path). A deceptive claim whose contradiction is NOT
    placement-linked — or whose draft carries no world-graph placement data to
    prove reachability — is rejected deterministically (``DECEPTIVE_CLAIM_
    UNCONTRADICTED``). The generation pipeline always passes the draft, so
    generated cases are fully reachability-checked.
    """
    entries = tuple(getattr(truth, "witness_truthfulness", ()) or ())
    deceptive = [e for e in entries if e.truthfulness == "DECEPTIVE"]
    if not deceptive:
        return ()
    evidence_tuple = tuple(evidence)
    reachable = _placement_linked_evidence_ids(draft)
    for entry in deceptive:
        contradiction_ids = mechanical_contradiction_fact_ids(
            evidence_tuple, entry.witness_id
        )
        if not contradiction_ids:
            return (DECEPTIVE_CLAIM_UNCONTRADICTED,)
        # FIX-3: at least one contradiction record must be bound to a world
        # placement — otherwise the player can never discover it and the lie
        # is unchallengeable (fail-closed when no placement data is available).
        if not any(cid in reachable for cid in contradiction_ids):
            return (DECEPTIVE_CLAIM_UNCONTRADICTED,)
    return ()


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
    codes.update(_murderer_candidate_issue(public, truth, universes))
    evidence_tuple = tuple(evidence)
    view = _payload_view(public, evidence_tuple)
    codes.update(_witness_complete_issues(public, evidence_tuple, view))
    codes.update(_deceptive_claim_issues(truth, evidence_tuple, draft))
    return tuple(sorted(codes))


__all__ = [
    "DECEPTIVE_CLAIM_UNCONTRADICTED",
    "FINAL_TARGET_UNIVERSE_EMPTY",
    "HOMICIDE_MURDERER_MISSING",
    "HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET",
    "HOMICIDE_VICTIM_AS_MURDERER",
    "MURDERER_NOT_SUSPECT_CANDIDATE",
    "PUBLIC_ROLE_TRUTH_LEAK",
    "PUBLIC_ROLE_VOCABULARY",
    "SUICIDE_MURDERER_PRESENT",
    "SUICIDE_OPTION_MISSING",
    "VICTIM_IN_SUSPECT_CANDIDATES",
    "WITNESS_IN_SUSPECT_CANDIDATES",
    "WITNESS_STATEMENT_EMPTY",
    "WITNESS_STATEMENT_MISSING",
    "WITNESS_STATEMENT_SPEAKER_MISMATCH",
    "WITNESS_STATEMENT_UNKNOWN_WITNESS",
    "WITNESS_STATEMENT_WITNESS_ID_MISMATCH",
    "validate_case_quality",
]
