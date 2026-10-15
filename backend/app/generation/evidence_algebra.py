"""Phase 43A — provider-neutral deterministic evidence algebra (P43A-AC-01/02).

This module is the canonical, provider-neutral home of the deterministic
evidence projection the Ollama stage driver historically owned as
``_evidence_gap_facts`` (Phase17 Wave-2). It is split into three layers:

* :func:`canonical_evidence_algebra` — the complete canonical evidence synthesis
  (WHEN bounds, red-herring opportunity exclusions, locked-person presence +
  alibi, motive link + contradiction, weapon forensics + fingerprint, the
  witness statements, and the travel-rule completion). It is behavior-identical
  to the driver implementation: for the same inputs it returns the same
  ``(EvidenceSetSpec, extra_travel_rules, notes)`` triples. The Ollama driver
  now delegates here (P43A-AC-02), so both paths consume ONE source.

* :func:`analyze_evidence_gaps` — a provider-neutral analyzer that, from
  VALIDATED canonical state ONLY, determines which solver dimensions
  (WHO/WHY/WEAPON/time) are underconstrained and whether the ambiguity is
  closable through the deterministic algebra. It NEVER reads raw provider text.

* :func:`complete_evidence_if_ambiguous` — the deterministic, idempotent
  completion entry point used by the generic (Frontier) path. Applying it twice
  yields the same result (no duplicate facts; the result is a fixed point).

Hard invariants (Phase43A §3): no ``CaseTruth`` is ever read or mutated; a
``WITNESS_CLAIMS`` proposition is never a WHO/WHY/WEAPON premise (the algebra
only emits WORLD/forensic/attribution facts the solvers consume); the analyzer
and completion operate exclusively on already-parsed canonical structures.

Nothing here trusts provider output.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from app.domain.eligibility import derive_universes
from app.domain.evidence import EvidenceFact, SourceRef, validate_evidence
from app.domain.solver import solve_case
from app.generation import trusted_context as _trusted_context
from app.generation.constraints import normalize_identity
from app.generation.schemas import (
    EvidenceSetSpec,
    EvidenceSpec,
    PropSpec,
    TravelRuleSpec,
)

# --------------------------------------------------------------------------- #
# canonical constants (single source: ``trusted_context``)
# --------------------------------------------------------------------------- #

# The app-owned "other sharp weapon" candidates the weapon solver tests
# against the locked weapon (mirror of the kit base objects).
_BASE_SHARP_WEAPON_IDS: tuple[str, ...] = _trusted_context._BASE_SHARP_WEAPON_IDS

# Default distant-location travel rule the algebra may deterministically add so
# an alternative suspect's observation actually proves the scene unreachable.
_DEFAULT_DISTANT_TRAVEL_SECONDS = _trusted_context._DEFAULT_DISTANT_TRAVEL_SECONDS

# The minimum L->scene travel time that makes a distant observation actually
# prove the scene unreachable over the canonical feasible window (the block
# [t-u, t+u+T) must cover the ~[tick-100, tick+81) window). A MODEL-AUTHORED
# public world may declare a too-short rule between the scene and the location
# it chooses for an alt-suspect observation; the algebra then replaces it with
# the deterministic distant rule so the opportunity exclusion is backed (the
# world geometry, not hidden truth, is what the algebra owns here).
_MIN_OPPORTUNITY_TRAVEL_SECONDS = 180

# --------------------------------------------------------------------------- #
# internal classification vocabulary (internal ONLY — no user-facing code)
# --------------------------------------------------------------------------- #

EVIDENCE_UNDERCONSTRAINED = "EVIDENCE_UNDERCONSTRAINED"
NOT_AMBIGUOUS = "NOT_AMBIGUOUS"
STRUCTURAL_DEFECT = "STRUCTURAL_DEFECT"
UNIVERSE_INCOMPLETE = "UNIVERSE_INCOMPLETE"
SOLVER_UNAVAILABLE = "SOLVER_UNAVAILABLE"
# A contradictory (empty) feasible time set is a DEFECT, not an evidence gap:
# it is deliberately NOT closable by completion (completion adds facts; it must
# not paper over contradictory evidence). It keeps its repair/regeneration owner.
TIME_OVERCONSTRAINED = "TIME_OVERCONSTRAINED"


# --------------------------------------------------------------------------- #
# deterministic canonical helpers (extracted verbatim from the driver)
# --------------------------------------------------------------------------- #


def _evidence_timeline(canonical: str) -> dict[str, str]:
    """The six deterministic evidence timestamps (canonical: trusted_context)."""
    return _trusted_context.evidence_timeline(canonical)


def _driver_clock_token(canonical: str) -> str:
    """The deterministic ``HH:MM`` clock token of the case's canonical time.

    The witness-grounding model recognises a bare clock token inside
    allowlisted statement/description text as a TIME anchor, so a driver
    witness statement that states the time derived from the case's OWN
    canonical crime/window data grounds the TIME interview question. Never a
    golden/truth sentence — purely the case's own canonical clock (DEF-079).
    """
    import datetime

    try:
        dt = datetime.datetime.fromisoformat(canonical)
    except (TypeError, ValueError):
        return ""
    return f"{dt.hour:02d}:{dt.minute:02d}"


def _driver_scene_label(public: Any, scene_id: str) -> str:
    """A deterministic public scene NAME for a driver witness statement.

    Prefers the scene location's PUBLIC name; falls back to the public
    location whose ``location_id`` equals the scene id, then to the first
    public location name — only the case's OWN public tokens are ever used
    (never a golden/truth label, never random text).
    """
    scene_name = str(
        getattr(getattr(public, "scene", None), "name", None) or ""
    ).strip()
    if scene_name:
        return scene_name
    for loc in (getattr(public, "locations", ()) or ()):
        if str(getattr(loc, "location_id", "") or "") == str(scene_id):
            name = str(getattr(loc, "name", "") or "").strip()
            if name:
                return name
    for loc in (getattr(public, "locations", ()) or ()):
        name = str(getattr(loc, "name", "") or "").strip()
        if name:
            return name
    return ""


def _canonical_prop(ptype: str, **kwargs: Any) -> Any:
    """One deterministic canonical proposition (PropSpec)."""
    return PropSpec(type=ptype, **{k: v for k, v in kwargs.items() if v is not None})


def _canonical_evidence_item(
    eid: str, kind: str, propositions: tuple[Any, ...], title: str, description: str
) -> Any:
    """One deterministic canonical evidence item (EvidenceSpec)."""
    seen: set[str] = set()
    unique: list[Any] = []
    for prop in propositions:
        key = repr(
            tuple(sorted(getattr(prop, "structured", {}).items()))
        ) + str(getattr(prop, "type", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(prop)
    return EvidenceSpec(
        id=eid,
        kind=kind,
        propositions=tuple(unique),
        source_ref={"kind": "record", "sourceId": f"record_{eid}"},
        reliability="high",
        presentation={"title": title, "description": description},
        discoverable=True,
    )


def _canonical_distant_location(
    travel_lookup: dict[tuple[str, str], int],
    non_scene_locations: list[str],
    scene_id: str,
) -> str | None:
    """Deterministic non-scene location for an opportunity fact: prefers a
    location ALREADY carrying a travel rule to the scene (>= 180s), then any
    non-scene location (the caller adds its rule)."""
    for loc in non_scene_locations:
        seconds = travel_lookup.get((loc, scene_id))
        if seconds is not None and seconds >= _MIN_OPPORTUNITY_TRAVEL_SECONDS:
            return loc
    return non_scene_locations[0] if non_scene_locations else None


# --------------------------------------------------------------------------- #
# canonical evidence algebra (behavior-identical extraction — P43A-AC-02)
# --------------------------------------------------------------------------- #


def canonical_evidence_algebra(
    public: Any,
    crime: Any,
    *,
    witness_truthfulness: Iterable[Any] = (),
) -> tuple[Any, tuple[Any, ...], tuple[str, ...]]:
    """The deterministic provider-neutral evidence projection.

    Returns ``(canonical_evidence_spec, extra_travel_rules, notes)``:

    - ``canonical_evidence_spec`` — the complete ``EvidenceSetSpec`` (WHEN;
      impossible-opportunity for every other final target; presence + alibi for
      the locked person; motive link + exclusions; weapon matches +
      fingerprint; one witness statement per public witness);
    - ``extra_travel_rules`` — ``TravelRuleSpec`` objects the caller appends to
      the draft public so an opportunity observation is travel-rule-backed;
    - ``notes`` — sanitized audit lines (evidence id + fact kind).

    ``crime`` is a ``Crime`` (or a duck-typed equivalent); ``public`` is a
    ``PublicCase`` / ``PublicWorldSpec`` (duck-typed). ``witness_truthfulness``
    is the hidden statement-level stamp tuple (never a solver premise). A
    HOMICIDE and a SUICIDE truth are both handled exactly as the Ollama driver
    did: a SUICIDE truth emits NO murderer/killer-placement/guilt facts and
    links the locked motive to the VICTIM, with weapon forensics consistent
    with self-infliction.
    """
    from app.domain.eligibility import final_homicide_target_ids

    if crime is None or public is None:
        return None, (), ()
    scene_id = (
        getattr(getattr(public, "scene", None), "location_id", None)
        or getattr(crime, "location_id", None)
    )
    murderer_id = getattr(crime, "murderer_id", None)
    motive_id = getattr(crime, "motive_id", None)
    weapon_id = getattr(crime, "weapon_id", None)
    victim_id = getattr(crime, "victim_id", None)
    canonical = getattr(getattr(crime, "crime_time", None), "canonical", None)
    # Phase41 (§16-§19): a SUICIDE truth has NO murderer by contract. The
    # deterministic evidence algebra MUST still run (WHEN + red-herring
    # opportunity exclusions + motive contradictions + weapon forensics +
    # witness statements) — a bail on a missing murderer would make a SUICIDE
    # case impossible to generate. A HOMICIDE truth still requires a murderer
    # (a missing one is a truth-shape defect -> bail to the canonical
    # validators).
    is_suicide = _is_suicide(crime)
    if not (scene_id and motive_id and weapon_id and victim_id and canonical):
        return None, (), ()
    if not is_suicide and not murderer_id:
        return None, (), ()
    try:
        timeline = _evidence_timeline(canonical)
    except (TypeError, ValueError):
        return None, (), ()

    persons = {
        p.person_id: getattr(p, "name", None)
        for p in (getattr(public, "persons", ()) or ())
    }
    # Phase41A — the impossible-opportunity enumeration covers every OTHER
    # FINAL HOMICIDE TARGET (public suspects AND witnesses, victim excluded),
    # not only ``SUSPECT_ELIGIBLE`` suspects (REQUIREMENTS 31.1.1A / 31.10).
    other_suspects = sorted(
        target
        for target in final_homicide_target_ids(public)
        if target != murderer_id
    )
    # The hidden statement-level witness-truthfulness stamps (Phase41A): the
    # caller supplies a bounded deterministic set per seed; the evidence
    # algebra consumes it for statement text + contradiction records. Pure.
    from app.domain.witness_truth import WitnessStatementTruth

    _stamped_deceptive: dict[str, str] = {}  # witness_id -> reason
    for entry in (witness_truthfulness or ()):
        if isinstance(entry, WitnessStatementTruth) and entry.truthfulness == "DECEPTIVE":
            _stamped_deceptive[entry.witness_id] = entry.deception_reason or ""
    other_motives = sorted(
        m.motive_id
        for m in (getattr(public, "motives", ()) or ())
        if m.motive_id != motive_id
    )
    non_scene_locations = sorted(
        loc.location_id
        for loc in (getattr(public, "locations", ()) or ())
        if loc.location_id != scene_id
    )
    travel_lookup: dict[tuple[str, str], int] = {}
    for rule in (getattr(public, "travel_rules", ()) or ()):
        frm = getattr(rule, "from_location_id", None)
        to = getattr(rule, "to_location_id", None)
        seconds = getattr(rule, "travel_time_seconds", None)
        if frm and to and isinstance(seconds, int):
            travel_lookup[(frm, to)] = seconds

    extras: list[Any] = []
    extra_rules: list[Any] = []
    notes: list[str] = []

    def _person_label(person_id: str) -> str:
        return persons.get(person_id) or person_id

    def _location_label(location_id: str) -> str:
        for loc in (getattr(public, "locations", ()) or ()):
            if str(getattr(loc, "location_id", "") or "") == str(location_id):
                return str(getattr(loc, "name", "") or "") or location_id
        return location_id

    # ---- WHEN (bounds the feasible window; canonical tick stays inside) ----
    for eid, ptype, person, title, description in (
        (
            "d_ev_when_last_seen",
            "VICTIM_LAST_SEEN_ALIVE_AT",
            victim_id,
            "Victim last seen",
            "The victim was seen alive at the scene shortly before the incident.",
        ),
        (
            "d_ev_when_body",
            "BODY_FIRST_FOUND_AT",
            None,
            "Body discovered",
            "The body was found at the scene shortly after the incident.",
        ),
        (
            "d_ev_when_obs",
            "CRIME_SCENE_OBSERVATION_AT",
            None,
            "Activity logged at the scene",
            "A monitoring log records activity at the scene around the locked time.",
        ),
    ):
        kwargs = {}
        if person is not None:
            kwargs["person_id"] = person
        if ptype == "VICTIM_LAST_SEEN_ALIVE_AT":
            observed_at, uncertainty = timeline["last_seen"], 0
        elif ptype == "BODY_FIRST_FOUND_AT":
            observed_at, uncertainty = timeline["body_found"], 0
        else:
            observed_at, uncertainty = timeline["scene_observation"], 90
        extras.append(
            _canonical_evidence_item(
                eid=eid,
                kind="witness_observation" if ptype != "CRIME_SCENE_OBSERVATION_AT" else "cctv",
                propositions=(
                    _canonical_prop(
                        ptype,
                        location_id=scene_id,
                        observed_at=observed_at,
                        uncertainty_seconds=uncertainty,
                        **kwargs,
                    ),
                ),
                title=title,
                description=description,
            )
        )
        notes.append(f"{eid}: canonical {ptype} ({observed_at})")

    # ---- impossible-opportunity for every OTHER FINAL HOMICIDE TARGET -------
    # Phase41A (REQUIREMENTS 31.1.1A / 31.10): every non-murderer final target
    # gets an evidence-backed distant observation (kind ``cctv``) so the WHO
    # solver keeps ONLY the murderer viable.
    travel_notes: list[str] = []
    opportunity_location: dict[str, str] = {}
    for suspect in other_suspects:
        distant = _canonical_distant_location(travel_lookup, non_scene_locations, scene_id)
        if distant is None:
            continue
        opportunity_location[suspect] = distant
        current_seconds = travel_lookup.get((distant, scene_id))
        if current_seconds is None or current_seconds < _MIN_OPPORTUNITY_TRAVEL_SECONDS:
            # Add (or REPLACE a too-short rule with) the deterministic distant
            # rule so the observation genuinely excludes the suspect. The caller
            # merges by endpoint, replacing any existing same-endpoint rule.
            action = "added" if current_seconds is None else "replaced"
            extra_rules.append(
                TravelRuleSpec(
                    from_location_id=distant,
                    to_location_id=scene_id,
                    travel_time_seconds=_DEFAULT_DISTANT_TRAVEL_SECONDS,
                )
            )
            travel_lookup[(distant, scene_id)] = _DEFAULT_DISTANT_TRAVEL_SECONDS
            travel_notes.append(
                f"travel rule {distant}->{scene_id}="
                f"{_DEFAULT_DISTANT_TRAVEL_SECONDS}s {action}"
            )
        eid = f"d_ev_opp_{normalize_identity(suspect) or 'target'}"
        extras.append(
            _canonical_evidence_item(
                eid=eid,
                kind="cctv",
                propositions=(
                    _canonical_prop(
                        "PERSON_OBSERVED_AT_LOCATION",
                        person_id=suspect,
                        location_id=distant,
                        observed_at=timeline["alternate_observed"],
                        uncertainty_seconds=30,
                    ),
                ),
                title="Person logged away from the scene",
                description=(
                    f"A monitoring log places {_person_label(suspect)} at a "
                    "location away from the scene at the crime time."
                ),
            )
        )
        notes.append(
            f"{eid}: canonical PERSON_OBSERVED_AT_LOCATION for {suspect} "
            "(opportunity exclusion)"
        )

    # ---- presence + alibi for the LOCKED person (HOMICIDE only) -----------
    if not is_suicide:
        extras.append(
            _canonical_evidence_item(
                eid="d_ev_presence",
                kind="cctv",
                propositions=(
                    _canonical_prop(
                        "PERSON_OBSERVED_AT_LOCATION",
                        person_id=murderer_id,
                        location_id=scene_id,
                        observed_at=timeline["presence"],
                        uncertainty_seconds=60,
                    ),
                ),
                title="Person logged at the scene",
                description=(
                    f"A monitoring log places {_person_label(murderer_id)} at the "
                    "scene around the locked time."
                ),
            )
        )
        notes.append(f"d_ev_presence: canonical scene presence for {murderer_id}")
        extras.append(
            _canonical_evidence_item(
                eid="d_ev_alibi",
                kind="testimonial",
                propositions=(
                    _canonical_prop(
                        "ALIBI_TIME_CLAIM",
                        person_id=murderer_id,
                        structured={"claimedDeparture": timeline["alibi_departure"]},
                    ),
                ),
                title="Alibi statement",
                description="A statement claims the person had left before the locked time.",
            )
        )
        notes.append(f"d_ev_alibi: canonical ALIBI_TIME_CLAIM for {murderer_id}")

    # ---- motive: the locked motive + red-herring exclusions ---------------
    if is_suicide:
        # FIX-6 (§15/§19): a SUICIDE truth still needs a UNIQUE motive
        # deduction. The WHY solver only certifies a motive when it is
        # SUPPORTED and every alternative is contradicted. The victim is the
        # subject of a self-inflicted resolution, so the locked motive is
        # deterministically linked to the VICTIM (never a murderer trace).
        extras.append(
            _canonical_evidence_item(
                eid="d_ev_motive_link",
                kind="email",
                propositions=(
                    _canonical_prop(
                        "MOTIVE_LINKED_TO_PERSON",
                        person_id=victim_id,
                        motive_id=motive_id,
                    ),
                ),
                title="Recorded statement",
                description="A record connects the victim to the locked motive.",
            )
        )
        notes.append(
            f"d_ev_motive_link: canonical MOTIVE_LINKED_TO_PERSON {victim_id}->{motive_id} (suicide)"
        )
    else:
        extras.append(
            _canonical_evidence_item(
                eid="d_ev_motive_link",
                kind="email",
                propositions=(
                    _canonical_prop(
                        "MOTIVE_LINKED_TO_PERSON",
                        person_id=murderer_id,
                        motive_id=motive_id,
                    ),
                ),
                title="Recorded statement",
                description=(
                    f"A record connects {_person_label(murderer_id)} to the locked motive."
                ),
            )
        )
        notes.append(
            f"d_ev_motive_link: canonical MOTIVE_LINKED_TO_PERSON {murderer_id}->{motive_id}"
        )
    for motive in other_motives:
        eid = f"d_ev_motive_x_{normalize_identity(motive) or 'motive'}"
        extras.append(
            _canonical_evidence_item(
                eid=eid,
                kind="email",
                propositions=(_canonical_prop("MOTIVE_FACT_CONTRADICTED", motive_id=motive),),
                title="Recorded statement",
                description="A record contradicts the candidate motive.",
            )
        )
        notes.append(f"{eid}: canonical MOTIVE_FACT_CONTRADICTED for {motive}")

    # ---- weapon matches + fingerprint --------------------------------------
    # Phase43A (generic-world completion): the base sharp-weapon candidates are
    # the Ollama kit's guaranteed objects; a MODEL-AUTHORED public world may
    # declare ADDITIONAL ``POTENTIAL_WEAPON`` objects (e.g. a baseball bat). The
    # weapon solver's universe is the public ``POTENTIAL_WEAPON`` set, so every
    # member except the locked weapon must be forensically excluded for the
    # dimension to be unique. Deriving the set from the validated public is
    # provider-neutral and a NO-OP for the Ollama kit (whose weapon universe is
    # already a subset of ``_BASE_SHARP_WEAPON_IDS``).
    try:
        other_weapons = sorted(
            set(_BASE_SHARP_WEAPON_IDS)
            | set(derive_universes(public).weapon_ids)
        )
    except (TypeError, ValueError, AttributeError):
        other_weapons = list(_BASE_SHARP_WEAPON_IDS)
    for obj in other_weapons:
        if normalize_identity(obj) == normalize_identity(weapon_id):
            continue
        eid = f"d_ev_weapon_false_{normalize_identity(obj) or 'weapon'}"
        extras.append(
            _canonical_evidence_item(
                eid=eid,
                kind="forensic",
                propositions=(
                    _canonical_prop(
                        "FORENSIC_WEAPON_MATCH", object_id=obj, structured={"match": False}
                    ),
                ),
                title="Forensic comparison",
                description="Forensic comparison excludes this object as the source.",
            )
        )
        notes.append(f"{eid}: canonical FORENSIC_WEAPON_MATCH false for {obj}")
    extras.append(
        _canonical_evidence_item(
            eid="d_ev_weapon_true",
            kind="forensic",
            propositions=(
                _canonical_prop(
                    "FORENSIC_WEAPON_MATCH", object_id=weapon_id, structured={"match": True}
                ),
            ),
            title="Forensic comparison",
            description="Forensic comparison identifies the locked object as the source.",
        )
    )
    notes.append(f"d_ev_weapon_true: canonical FORENSIC_WEAPON_MATCH true for {weapon_id}")
    if not is_suicide:
        # A victim fingerprint on the object is a normal self-inflicted-scene
        # fact and is deliberately NOT asserted as a killer trace for SUICIDE.
        extras.append(
            _canonical_evidence_item(
                eid="d_ev_fp",
                kind="forensic",
                propositions=(
                    _canonical_prop(
                        "OBJECT_CONTAINS_FINGERPRINT", object_id=weapon_id, person_id=murderer_id
                    ),
                ),
                title="Latent fingerprint",
                description="A latent fingerprint was lifted from the locked object.",
            )
        )
        notes.append(
            f"d_ev_fp: canonical OBJECT_CONTAINS_FINGERPRINT on {weapon_id} for {murderer_id}"
        )

    # Phase35 witness completeness — the canonical layer OWNS a usable witness
    # statement for every public witness. WITNESS_CLAIMS is NOT consumed by any
    # Phase 3 deduction rule, so the solver signature is unaffected; the fact
    # is real discoverable player-visible evidence (DEF-079).
    from app.generation.evidence_facts import (
        deceptive_presence_statement_text,
        deceptive_statement_text,
    )

    clock_token = _driver_clock_token(canonical)
    scene_label = _driver_scene_label(public, scene_id)
    for witness in sorted(
        (
            p
            for p in (getattr(public, "persons", ()) or ())
            if getattr(p, "role", None) == "witness"
        ),
        key=lambda p: str(getattr(p, "person_id", "") or ""),
    ):
        wid = getattr(witness, "person_id", None)
        wname = getattr(witness, "name", "") or wid or "witness"
        if not wid:
            continue
        eid = f"d_ev_witness_{normalize_identity(wid) or 'witness'}"
        if any(str(getattr(e, "id", "")) == eid for e in extras):
            continue

        deceptive = wid in _stamped_deceptive
        is_murderer = wid == murderer_id
        truthful_location_id = None if is_murderer else opportunity_location.get(wid)
        truthful_location_label = (
            _location_label(truthful_location_id) if truthful_location_id else ""
        )
        if deceptive and is_murderer and scene_label and clock_token:
            statement, description = deceptive_statement_text(
                wid, str(wname), scene_label, clock_token
            )
        elif deceptive and scene_label and clock_token:
            statement, description = deceptive_presence_statement_text(
                wid, str(wname), scene_label, clock_token
            )
        elif truthful_location_label and clock_token:
            statement = (
                f"As {wname}, I was at the {truthful_location_label} around "
                f"{clock_token} and noticed a few things from there."
            )
            description = (
                f"{wname} was at the {truthful_location_label} around {clock_token}."
            )
        elif scene_label and clock_token:
            statement = (
                f"As {wname}, I was at the {scene_label} around {clock_token} "
                "and noticed a few things that did not sit right."
            )
            description = (
                f"{wname} was near the {scene_label} around {clock_token}."
            )
        elif clock_token:
            statement = (
                f"As {wname}, I was nearby around {clock_token} and noticed a "
                "few things that did not sit right."
            )
            description = f"{wname} recounted the course of the evening."
        else:
            statement = (
                "I was nearby during the evening and noticed a few "
                "things that did not sit right."
            )
            description = f"{wname} recounted the course of the evening."
        extras.append(
            EvidenceSpec(
                id=eid,
                kind="witness_statement",
                propositions=(_canonical_prop("WITNESS_CLAIMS", person_id=wid),),
                source_ref={"kind": "record", "sourceId": f"record_{eid}"},
                reliability="high",
                presentation={
                    "title": "Witness statement",
                    "description": description,
                    "speakerName": str(wname),
                    "statement": statement,
                },
                discoverable=True,
            )
        )
        notes.append(f"{eid}: canonical witness statement for {wid}")

    if travel_notes:
        notes.append("; ".join(travel_notes))
    notes.append(
        "the published evidence is the deterministic canonical algebra built "
        "from the model's case skeleton (model evidence propositions are not "
        "copied into the draft)"
    )
    return EvidenceSetSpec(evidence=tuple(extras)), tuple(extra_rules), tuple(notes)


# --------------------------------------------------------------------------- #
# provider-neutral gap analysis (P43A-FR-01)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EvidenceGapAnalysis:
    """Provider-neutral classification of an evidence set's solver gaps.

    ``classification`` is one of the closed internal tokens above.
    ``ambiguous_dimensions`` lists the underconstrained solver dimensions
    (``"who"`` / ``"why"`` / ``"weapon"`` / ``"when"``). ``solver_outcome`` is a
    bounded label (``"unique"`` / ``"ambiguous"`` / ``"overconstrained"`` /
    ``"unavailable"``). ``closable`` is True only for
    ``EVIDENCE_UNDERCONSTRAINED``.
    """

    classification: str
    ambiguous_dimensions: tuple[str, ...] = ()
    solver_outcome: str = "unavailable"
    closable: bool = False


def _resolution_token(crime: Any) -> str | None:
    """The trusted resolution token of a crime-like object (or None)."""
    raw = getattr(crime, "resolution_type", None)
    value = getattr(raw, "value", raw)  # unwrap ``ResolutionType`` -> its value
    if value in ("HOMICIDE", "SUICIDE"):
        return str(value)
    if value is None and getattr(crime, "murderer_id", None):
        return "HOMICIDE"  # documented legacy inference
    return None


def _is_suicide(crime: Any) -> bool:
    return _resolution_token(crime) == "SUICIDE"


def _crime_reference_issues(public: Any, crime: Any) -> tuple[str, ...]:
    """Structural: every crime id must resolve in the public model."""
    person = getattr(public, "person", None)
    if not callable(person):
        return ()
    issues: list[str] = []
    checks = [
        ("victimId", getattr(crime, "victim_id", None), person),
        ("motiveId", getattr(crime, "motive_id", None), getattr(public, "motive", None)),
        ("weaponId", getattr(crime, "weapon_id", None), getattr(public, "object", None)),
        ("locationId", getattr(crime, "location_id", None), getattr(public, "location", None)),
    ]
    if not _is_suicide(crime):
        checks.insert(1, ("murdererId", getattr(crime, "murderer_id", None), person))
    for name, value, resolver in checks:
        if value and callable(resolver) and resolver(value) is None:
            issues.append(f"crime.{name} {value!r} does not resolve in the public case")
    return tuple(sorted(issues))


def _ambiguous_dimensions(proof: Any, *, is_suicide: bool) -> tuple[str, ...]:
    dims: list[str] = []
    if not is_suicide:
        who = getattr(proof, "who", None)
        if who is None or not getattr(who, "unique", False):
            dims.append("who")
    why = getattr(proof, "why", None)
    if why is None or not getattr(why, "unique", False):
        dims.append("why")
    weapon = getattr(proof, "weapon", None)
    if weapon is None or not getattr(weapon, "unique", False):
        dims.append("weapon")
    when = getattr(proof, "when", None)
    if when is None or getattr(when, "ambiguous", False):
        dims.append("when")
    return tuple(dims)


def _outcome_label(proof: Any, dims: tuple[str, ...], overconstrained: bool) -> str:
    if overconstrained:
        return "overconstrained"
    if not dims:
        return "unique"
    return "ambiguous"


def _as_domain_evidence(items: Iterable[Any]) -> tuple[EvidenceFact, ...]:
    """Project canonical evidence items to the domain ``EvidenceFact`` shape.

    Accepts either domain facts (returned unchanged) or generation
    ``EvidenceSpec`` items. Never re-reads provider text.
    """
    items = _evidence_items(items)
    if not items or isinstance(items[0], EvidenceFact):
        return items
    converted: list[EvidenceFact] = []
    for spec in items:
        propositions = tuple(
            p._as_typed_proposition() if hasattr(p, "_as_typed_proposition") else p
            for p in (getattr(spec, "propositions", ()) or ())
        )
        source_ref = getattr(spec, "source_ref", None)
        if isinstance(source_ref, Mapping):
            source_ref = SourceRef(
                kind=str(source_ref.get("kind", "") or ""),
                source_id=str(source_ref.get("sourceId", "") or ""),
            )
        converted.append(
            EvidenceFact(
                id=str(getattr(spec, "id", "")),
                kind=str(getattr(spec, "kind", "")),
                propositions=propositions,
                source_ref=source_ref,
                reliability=getattr(spec, "reliability", "high"),
                presentation=dict(getattr(spec, "presentation", {}) or {}),
                discoverable=bool(getattr(spec, "discoverable", True)),
            )
        )
    return tuple(converted)


def _solver_state(
    public: Any, crime: Any, items: Iterable[Any]
) -> tuple[tuple[str, ...], str, bool]:
    """Solver dimensions still underconstrained by ``items`` (canonical only).

    Returns ``(ambiguous_dimensions, outcome_label, overconstrained)``.
    """
    is_suicide = _is_suicide(crime)
    domain = _as_domain_evidence(items)
    try:
        proof = solve_case(public, domain)
    except (TypeError, ValueError):
        dims = ("why", "weapon", "when") if is_suicide else ("who", "why", "weapon", "when")
        return dims, "unavailable", False
    when = getattr(proof, "when", None)
    overconstrained = bool(when is not None and getattr(when, "overconstrained", False))
    dims = _ambiguous_dimensions(proof, is_suicide=is_suicide)
    return dims, _outcome_label(proof, dims, overconstrained), overconstrained


def analyze_evidence_gaps(
    public: Any, truth_or_crime: Any, evidence: Iterable[Any]
) -> EvidenceGapAnalysis:
    """Provider-neutral evidence-gap analysis over VALIDATED canonical state.

    ``public`` is a domain ``PublicCase``; ``truth_or_crime`` is a ``CaseTruth``
    (its ``.crime``) or a ``Crime``; ``evidence`` is canonical evidence (domain
    ``EvidenceFact`` items or generation ``EvidenceSpec`` items). Never reads
    raw provider text. ``closable`` is True only when the failure is
    specifically evidence-underconstrained: structural/reference checks pass,
    the required universes are non-empty, and the solver still leaves at least
    one required dimension ambiguous.
    """
    crime = getattr(truth_or_crime, "crime", truth_or_crime)
    if public is None or crime is None:
        return EvidenceGapAnalysis(STRUCTURAL_DEFECT)

    structural = tuple(validate_evidence(public, _as_domain_evidence(evidence)))
    structural += _crime_reference_issues(public, crime)
    if structural:
        return EvidenceGapAnalysis(STRUCTURAL_DEFECT)

    universes = derive_universes(public)
    is_suicide = _is_suicide(crime)
    if not is_suicide and not universes.homicide_target_ids:
        return EvidenceGapAnalysis(UNIVERSE_INCOMPLETE)
    if not universes.motive_ids or not universes.weapon_ids:
        return EvidenceGapAnalysis(UNIVERSE_INCOMPLETE)

    dims, outcome, overconstrained = _solver_state(public, crime, evidence)
    if overconstrained:
        return EvidenceGapAnalysis(
            TIME_OVERCONSTRAINED,
            ambiguous_dimensions=dims,
            solver_outcome=outcome,
            closable=False,
        )
    if outcome == "unavailable":
        return EvidenceGapAnalysis(
            SOLVER_UNAVAILABLE, ambiguous_dimensions=dims, solver_outcome=outcome
        )
    if not dims:
        return EvidenceGapAnalysis(
            NOT_AMBIGUOUS, ambiguous_dimensions=(), solver_outcome=outcome
        )
    return EvidenceGapAnalysis(
        EVIDENCE_UNDERCONSTRAINED,
        ambiguous_dimensions=dims,
        solver_outcome=outcome,
        closable=True,
    )


# --------------------------------------------------------------------------- #
# deterministic completion (P43A-FR-02/05/10)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CompletionResult:
    """The deterministic completion outcome (P43A §13 bounded report)."""

    evidence: EvidenceSetSpec
    extra_travel_rules: tuple[TravelRuleSpec, ...] = ()
    facts_added: int = 0
    dimensions_before: tuple[str, ...] = ()
    dimensions_after: tuple[str, ...] = ()
    changed: bool = False
    solver_outcome_before: str = "unique"
    solver_outcome_after: str = "unique"
    idempotent: bool = True
    notes: tuple[str, ...] = ()


def _evidence_items(evidence: Any) -> tuple[Any, ...]:
    if evidence is None:
        return ()
    items = getattr(evidence, "evidence", None)
    if items is not None:
        return tuple(items)
    return tuple(evidence)


def _fact_references_resolve(public: Any, fact: Any) -> bool:
    """Whether every proposition reference of ``fact`` resolves in ``public``.

    A deterministic guard so the completion never emits a fact referencing a
    world entity the public model does not contain (e.g. a base sharp weapon
    the case's public omitted). Ollama goldens resolve fully, so the completion
    output matches ``canonical_evidence_algebra`` byte-for-byte there.
    """
    person = getattr(public, "person", None)
    if not callable(person):
        return True  # cannot evaluate (non-domain public): keep the fact
    for prop in (getattr(fact, "propositions", ()) or ()):
        pid = getattr(prop, "person_id", None)
        if pid and person(pid) is None:
            return False
        lid = getattr(prop, "location_id", None)
        if lid and public.location(lid) is None:
            return False
        oid = getattr(prop, "object_id", None)
        if oid and public.object(oid) is None:
            return False
        mid = getattr(prop, "motive_id", None)
        if mid and public.motive(mid) is None:
            return False
    return True


def _augment_public_with_rules(public: Any, extra_rules: Iterable[Any]) -> Any:
    """A copy of ``public`` with ``extra_rules`` appended (deduped by endpoints).

    The completion does NOT mutate the public; it returns the extra travel
    rules for the CALLER to apply. This helper only lets the completion report
    an ACCURATE post-completion solver outcome (the opportunity exclusions are
    only usable once the travel rule exists). Non-dataclass publics are
    returned unchanged.
    """
    extra_rules = tuple(extra_rules or ())
    if not extra_rules:
        return public
    try:
        existing = tuple(getattr(public, "travel_rules", ()) or ())
        # Replace same-endpoint rules (an override) while appending genuinely
        # new ones; preserves the pre-Phase43A append-only behavior whenever no
        # endpoint collides.
        override_endpoints = {
            (rule.from_location_id, rule.to_location_id) for rule in extra_rules
        }
        merged = [
            rule
            for rule in existing
            if (rule.from_location_id, rule.to_location_id) not in override_endpoints
        ]
        merged.extend(extra_rules)
        return replace(public, travel_rules=tuple(merged))
    except (TypeError, ValueError):
        return public


def _complete_core(
    public: Any,
    crime: Any,
    evidence: Any,
    witness_truthfulness: Iterable[Any],
) -> CompletionResult:
    input_items = _evidence_items(evidence)
    dims_before, outcome_before, over_before = _solver_state(public, crime, input_items)
    if over_before:
        # A contradictory (overconstrained) time set is a DEFECT, not an
        # evidence gap: completion never papers over contradictory evidence.
        unchanged = (
            evidence
            if isinstance(evidence, EvidenceSetSpec)
            else EvidenceSetSpec(evidence=tuple(input_items))
        )
        return CompletionResult(
            evidence=unchanged,
            facts_added=0,
            dimensions_before=dims_before,
            dimensions_after=dims_before,
            changed=False,
            solver_outcome_before=outcome_before,
            solver_outcome_after=outcome_before,
            idempotent=True,
        )
    if not dims_before:
        # Already uniquely constraining: return the input UNCHANGED (zero facts).
        unchanged = (
            evidence
            if isinstance(evidence, EvidenceSetSpec)
            else EvidenceSetSpec(evidence=tuple(input_items))
        )
        return CompletionResult(
            evidence=unchanged,
            facts_added=0,
            dimensions_before=(),
            dimensions_after=(),
            changed=False,
            solver_outcome_before=outcome_before,
            solver_outcome_after=outcome_before,
            idempotent=True,
        )

    canonical_spec, extra_rules, notes = canonical_evidence_algebra(
        public, crime, witness_truthfulness=witness_truthfulness
    )
    if canonical_spec is None:
        unchanged = (
            evidence
            if isinstance(evidence, EvidenceSetSpec)
            else EvidenceSetSpec(evidence=tuple(input_items))
        )
        return CompletionResult(
            evidence=unchanged,
            facts_added=0,
            dimensions_before=dims_before,
            dimensions_after=dims_before,
            changed=False,
            solver_outcome_before=outcome_before,
            solver_outcome_after=outcome_before,
            idempotent=True,
            notes=tuple(notes),
        )

    filtered = tuple(
        f for f in canonical_spec.evidence if _fact_references_resolve(public, f)
    )
    completed = EvidenceSetSpec(evidence=filtered)
    input_ids = {str(getattr(f, "id", "")) for f in input_items}
    facts_added = sum(1 for f in filtered if str(getattr(f, "id", "")) not in input_ids)
    changed = tuple(completed.evidence) != tuple(input_items)
    augmented = _augment_public_with_rules(public, extra_rules)
    dims_after, outcome_after, _over_after = _solver_state(public=augmented, crime=crime, items=filtered)
    return CompletionResult(
        evidence=completed,
        extra_travel_rules=tuple(extra_rules),
        facts_added=facts_added,
        dimensions_before=dims_before,
        dimensions_after=dims_after,
        changed=changed,
        solver_outcome_before=outcome_before,
        solver_outcome_after=outcome_after,
        idempotent=False,
        notes=tuple(notes),
    )


def complete_evidence_if_ambiguous(
    public: Any,
    crime: Any,
    evidence: Any,
    *,
    witness_truthfulness: Iterable[Any] = (),
) -> CompletionResult:
    """Deterministically complete an underconstrained evidence set.

    If the current evidence already uniquely constrains the solver, the input
    is returned UNCHANGED (zero facts added). Otherwise the complete canonical
    algebra is returned (filtered to references the public model actually
    contains), plus any travel rules the caller must append to the public.

    Deterministic and idempotent: ``complete(complete(state)) ==
    complete(state)`` — the result is a fixed point (no duplicate facts; stable
    ordering by canonical id + proposition type/kind).
    """
    first = _complete_core(public, crime, evidence, witness_truthfulness)
    if not first.changed:
        return replace(first, idempotent=True)
    second = _complete_core(
        public, crime, first.evidence, witness_truthfulness
    )
    idempotent = (
        tuple(second.evidence.evidence) == tuple(first.evidence.evidence)
        and second.facts_added == 0
    )
    return replace(first, idempotent=idempotent)


__all__ = [
    "CompletionResult",
    "EVIDENCE_UNDERCONSTRAINED",
    "EvidenceGapAnalysis",
    "NOT_AMBIGUOUS",
    "SOLVER_UNAVAILABLE",
    "STRUCTURAL_DEFECT",
    "TIME_OVERCONSTRAINED",
    "UNIVERSE_INCOMPLETE",
    "analyze_evidence_gaps",
    "canonical_evidence_algebra",
    "complete_evidence_if_ambiguous",
]
