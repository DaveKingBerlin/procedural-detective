"""Phase 23 — deterministic player-safe witness interview domain.

PURE domain module (no provider, no pydantic, no truth import; mirrors the
boundary discipline of ``app.domain.render`` / ``app.domain.evidence``).
Everything here is derived deterministically from the PUBLISHED payload's
allowlisted ``draft`` sections (persons / evidence presentation + proposition
id-references / world graph / locations / objects). The hidden sections
(truth / solverProof / universes / report / prompt / seed / model / locked)
are NEVER read.

Responsibilities:

- ``WitnessPresence`` — the CLOSED presence enum (ON_SCENE / REMOTE_STATEMENT).
  v1 rule (documented): a witness is ON_SCENE exactly when the published world
  graph contains a semantic person placement for that witness (a placement
  whose ``object_id`` equals the witness person_id, or a placement carrying an
  explicit ``person_id`` attribute equal to the witness person_id). Otherwise
  REMOTE_STATEMENT. No placement exists for persons in any current pipeline
  output, so every present case answers REMOTE_STATEMENT until a future
  composer emits a person placement; the rule itself is data-driven and never
  invents a mode.
- ``WitnessQuestionType`` — the CLOSED six-question interview enum plus its
  allowlisted display labels. The browser may only send one of these values
  (the API schema rejects anything else with 422). All six are ALWAYS offered:
  question AVAILABILITY never leaks clue semantics — every question returns
  either a grounded statement or the neutral statement, never a "this is the
  right question to ask" signal.
- ``project_witness_statement`` — the deterministic, ZERO-provider statement
  authoring. Grounding sources, in order (Phase23 §6):
    1. validated published witness-kind evidence ATTRIBUTED to the witness
       (kind in WITNESS_KINDS AND a proposition references the person OR the
       presentation ``speakerName`` normalizes to the witness name/id);
    2. deterministic projection from canonical person + evidence (the
       attributed fact's allowlisted proposition id-references — person
       references OTHER than the witness, location/object references);
    3. deterministic local synthesis from the approved observation fields
       (the allowlisted presentation statement/description text, including
       bounded ``HH:MM`` clock tokens for the TIME question).
  Every grounded statement reproduces ONLY allowlisted published text and
  concrete times/id-references that semantically exist in the published
  evidence — never CaseTruth, never a person/weapon/motive referenced "by
  role", never a widened candidate universe. A question with no grounded
  observation returns the NEUTRAL statement (``No. Nothing stood out to me.``)
  with empty observations and no state mutation.

All output strings are bounded; control characters are stripped. This module
never touches ``app.persistence`` / ``app.services`` / ``app.api``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from app.domain.time_interval import parse_iso8601_to_epoch

# --------------------------------------------------------------------------- #
# closed enums + allowlisted labels
# --------------------------------------------------------------------------- #


class WitnessPresence(str, Enum):
    """The CLOSED witness presence vocabulary (Phase23 §17).

    Values are the wire tokens; the set is frozen.
    """

    ON_SCENE = "ON_SCENE"
    REMOTE_STATEMENT = "REMOTE_STATEMENT"


# The witness-kind evidence classes that a discovered/re-read interview source
# record may carry (subset of ``WITNESS_KINDS`` scoped to the statement-like
# kinds the notebook classifies as interview sources — Phase23 §25).
INTERVIEW_STATEMENT_KINDS: frozenset[str] = frozenset(
    {
        "witness_statement",
        "statement",
        "testimonial",
        "suspect_statement",
    }
)


class WitnessQuestionType(str, Enum):
    """The CLOSED structured-interview question set (Phase23 §4).

    The enum VALUE is what is emitted in the DTO and accepted by the interview
    body; the set is closed — a question id can never be LLM/player-authored.
    """

    OBSERVATION = "OBSERVATION"
    TIME = "TIME"
    PERSON = "PERSON"
    OBJECT = "OBJECT"
    LOCATION = "LOCATION"
    SOUND = "SOUND"


# Frozen display labels (allowlisted; never from provider output).
QUESTION_LABELS: dict[WitnessQuestionType, str] = {
    WitnessQuestionType.OBSERVATION: "What did you see?",
    WitnessQuestionType.TIME: "When were you there?",
    WitnessQuestionType.PERSON: "Did you notice anyone?",
    WitnessQuestionType.OBJECT: "Did you notice any unusual objects?",
    WitnessQuestionType.LOCATION: "Where were you?",
    WitnessQuestionType.SOUND: "Did you hear anything?",
}

ALL_QUESTIONS: tuple[WitnessQuestionType, ...] = tuple(WitnessQuestionType)

# --------------------------------------------------------------------------- #
# bounds (all witness-facing strings are bounded — Phase23 §47)
# --------------------------------------------------------------------------- #

MAX_NAME_CHARS = 120
MAX_STATEMENT_SUMMARY_CHARS = 300
MAX_OBSERVATION_TEXT_CHARS = 400
MAX_OBSERVATIONS = 6
MAX_TIME_LEN = 64

# The neutral answer copy (Phase23 §10): a question with no grounded
# observation produces this EXACT summary, empty observations, zero discovery.
NEUTRAL_SUMMARY = "No. Nothing stood out to me."

# Witness-kind evidence kinds (REQUIREMENTS 13 testimonial class +
# witness_observation). Only these kinds can ground an interview statement.
WITNESS_KINDS: frozenset[str] = frozenset(
    {
        "witness_observation",
        "witness_statement",
        "statement",
        "testimonial",
        "suspect_statement",
    }
)

# The proposition type that structurally grounds the SOUND question. Do NOT
# keyword-match statement text for sound ("heard shouting" in a golden case
# must stay NEUTRAL per the golden interview matrix) — only an attributed
# NOISE_HEARD_AT proposition is a structured sound observation.
SOUND_PROPOSITION_TYPE = "NOISE_HEARD_AT"

# A bare clock token inside allowlisted statement text (TIME synthesis).
_CLOCK_TOKEN_RE = re.compile(r"\b([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)\b")

# Control characters stripped from every witness-facing string.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Epoch used ONLY as a sort-key for bare clock tokens (no date known), so
# they sort after the dated ISO anchors: ISO (full timestamps) first, bare
# clock tokens after — deterministic and chronologically natural.
_CLOCK_EPOCH = 10**18


@dataclass(frozen=True)
class WitnessObservation:
    """One observation of a witness statement ({time? optional, text})."""

    text: str
    time: str | None = None


@dataclass(frozen=True)
class WitnessStatement:
    """A bounded, player-safe witness answer (Phase23 §5)."""

    summary: str
    observations: tuple[WitnessObservation, ...]
    grounded: bool = False
    # The published witness-kind evidence ids that grounded this answer
    # (discoverable subset — used by the discovery path; NEVER hidden ids).
    evidence_ids: tuple[str, ...] = ()


class WitnessProjection:
    """Result of ``project_witness_statement`` (grounding contract)."""

    def __init__(self, statement: WitnessStatement, attribution: tuple[str, ...] = ()) -> None:
        self.statement = statement
        self.attribution = tuple(attribution)  # all attributed fact ids (deterministic)


# --------------------------------------------------------------------------- #
# text/identity helpers (deterministic, bounded)
# --------------------------------------------------------------------------- #


def _identity_norm(value: str | None) -> str:
    """DEF-054-style identity normalization (mirror of normalize_identity):
    casefold + keep only ASCII letters/digits. Local copy keeps the domain
    module free of cross-layer imports."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.casefold() if ch.isascii() and ch.isalnum())


def _clean_text(value: Any, max_chars: int) -> str:
    """A plain bounded string: control chars stripped, truncated. Non-str
    values become '' (hostile content stays inert)."""
    if not isinstance(value, str):
        return ""
    cleaned = _CONTROL_RE.sub("", value)
    return cleaned[:max_chars]


def _parse_time_token(raw: Any) -> str | None:
    """Concrete ISO-8601-with-offset string or None (unverifiable times are
    never invented)."""
    if not isinstance(raw, str) or not raw or len(raw) > 64:
        return None
    try:
        parse_iso8601_to_epoch(raw)
    except (ValueError, TypeError):
        return None
    return raw


def _clock_tokens(text: str) -> tuple[str, ...]:
    """Bare HH:MM[:SS] clock tokens in allowlisted text, in first-appearance
    order (deduplicated). Never a date, never an offset — only the concrete
    clock the witness text itself states."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _CLOCK_TOKEN_RE.finditer(text):
        token = match.group(1)
        if token in seen:
            continue
        seen.add(token)
        out.append(token[:MAX_TIME_LEN])
    return tuple(out)


# --------------------------------------------------------------------------- #
# payload accessors (allowlisted draft sections ONLY)
# --------------------------------------------------------------------------- #


def _draft_of(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    draft = payload.get("draft")
    return draft if isinstance(draft, Mapping) else {}


def _persons_of(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    persons = _draft_of(payload).get("persons")
    return tuple(p for p in (persons or ()) if isinstance(p, Mapping))


def _evidence_of(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    evidence = _draft_of(payload).get("evidence")
    return tuple(e for e in (evidence or ()) if isinstance(e, Mapping))


def _world_graph_of(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    wg = _draft_of(payload).get("world_graph")
    return wg if isinstance(wg, Mapping) else {}


def _locations_of(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    locations = _draft_of(payload).get("locations")
    return tuple(l for l in (locations or ()) if isinstance(l, Mapping))


def _objects_of(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    objects = _draft_of(payload).get("objects")
    return tuple(o for o in (objects or ()) if isinstance(o, Mapping))


def _presentation_of(fact: Mapping[str, Any]) -> Mapping[str, Any]:
    presentation = fact.get("presentation")
    return presentation if isinstance(presentation, Mapping) else {}


def _propositions_of(fact: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    propositions = fact.get("propositions")
    return tuple(p for p in (propositions or ()) if isinstance(p, Mapping))


def _public_person_name_tokens(payload: Mapping[str, Any]) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """(person_id, normalized full name, normalized word tokens) for every
    public person — used ONLY for PERSON text-mention grounding against the
    player-visible public persons. Never used to reference truth."""
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for person in _persons_of(payload):
        person_id = str(person.get("person_id") or "")
        raw_name = str(person.get("name") or "")
        if not person_id or not raw_name:
            continue
        words = tuple(
            _identity_norm(word)
            for word in raw_name.split()
            if _identity_norm(word)
        )
        out.append((person_id, _identity_norm(raw_name), words))
    return tuple(out)


def _public_location_name_tokens(payload: Mapping[str, Any]) -> frozenset[str]:
    """Word tokens of every public location name (+ the scene name) — for
    LOCATION text-mention grounding against the player-visible public model.
    Tokenized from the ORIGINAL space-separated name (normalization keeps
    full-name substrings; word tokens catch "the apartment" mentions)."""
    tokens: set[str] = set()
    for location in _locations_of(payload):
        raw_name = str(location.get("name") or "")
        tokens.update(
            word for word in (_identity_norm(w) for w in raw_name.split()) if word
        )
    scene = _draft_of(payload).get("scene")
    if isinstance(scene, Mapping):
        raw_name = str(scene.get("name") or "")
        tokens.update(
            word for word in (_identity_norm(w) for w in raw_name.split()) if word
        )
    return frozenset(tokens)


def _public_object_labels(payload: Mapping[str, Any]) -> frozenset[str]:
    """Normalized identity labels of every public object (object_id +
    asset_id) — for OBJECT text-mention grounding."""
    labels: set[str] = set()
    for obj in _objects_of(payload):
        for key in ("object_id", "asset_id"):
            value = obj.get(key)
            norm = _identity_norm(value)
            if norm:
                labels.add(norm)
    return frozenset(labels)


# --------------------------------------------------------------------------- #
# witness person lookup (role == "witness" only)
# --------------------------------------------------------------------------- #


def witness_person_of(
    payload: Mapping[str, Any], witness_id: str
) -> Mapping[str, Any] | None:
    """The published person with ``person_id == witness_id`` AND role
    'witness', or None. A non-witness public person (suspect/victim/family)
    is NOT interviewable — the same 404 the unknown-id path uses."""
    for person in _persons_of(payload):
        if str(person.get("person_id")) != str(witness_id):
            continue
        if str(person.get("role")) != "witness":
            return None
        return person
    return None


def witness_presence(payload: Mapping[str, Any], witness_id: str) -> tuple[WitnessPresence, bool]:
    """Deterministic presence of one witness (Phase23 §17).

    ON_SCENE  — the published world graph contains a semantic person placement
                for the witness (placement.object_id == witness person_id, or
                a placement carrying an explicit ``person_id`` attribute equal
                to the witness person_id). Such a placement is the ONLY signal;
                no current pipeline composes persons, so present cases default
                REMOTE_STATEMENT.
    REMOTE_STATEMENT — everything else (the interview is available through the
                investigation UI, the person is not rendered at the scene).

    Never model-invented: the flag is a pure function of the published world.
    """
    witness = witness_person_of(payload, witness_id)
    if witness is None:
        return WitnessPresence.REMOTE_STATEMENT, False
    placements = _world_graph_of(payload).get("placements")
    for placement in placements or ():
        if not isinstance(placement, Mapping):
            continue
        if str(placement.get("object_id")) == str(witness_id):
            return WitnessPresence.ON_SCENE, True
        if str(placement.get("person_id") or "") == str(witness_id):
            return WitnessPresence.ON_SCENE, True
    return WitnessPresence.REMOTE_STATEMENT, False


def witness_scene_object_id(
    payload: Mapping[str, Any], witness_id: str
) -> str | None:
    """The world-object id of the witness's ON_SCENE person placement (None
    when REMOTE_STATEMENT).

    Applies the IDENTICAL placement rule as ``witness_presence`` (Phase23 §17):
    ON_SCENE exactly when the published world graph contains a placement whose
    ``object_id`` equals the witness person_id (how a semantic person is
    published), or a placement carrying an explicit ``person_id`` attribute
    equal to the witness person_id. The returned value is that placement's
    object_id (== witness_id in the first form; the placement's own object_id
    otherwise), used by the bootstrap ``witnesses[].sceneObjectId`` wire field.
    Never model-invented: it is a pure function of the published world.
    """
    if witness_person_of(payload, witness_id) is None:
        return None
    placements = _world_graph_of(payload).get("placements")
    for placement in placements or ():
        if not isinstance(placement, Mapping):
            continue
        if str(placement.get("object_id")) == str(witness_id):
            return str(witness_id)
        if str(placement.get("person_id") or "") == str(witness_id):
            scene_object_id = placement.get("object_id")
            if scene_object_id is not None:
                return str(scene_object_id)
    return None


# --------------------------------------------------------------------------- #
# statement authoring (deterministic, ZERO provider calls)
# --------------------------------------------------------------------------- #


def _attributed_facts(
    payload: Mapping[str, Any], witness: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    """Published witness-kind evidence attributed to the witness.

    Attribution rule (documented, deterministic, never invented):
      kind in WITNESS_KINDS AND
        (any proposition.person_id == witness.person_id  OR
         presentation.speakerName normalizes to the witness name/id).
    Sorted by evidence id for determinism.
    """
    witness_id = str(witness.get("person_id"))
    name = str(witness.get("name") or "")
    out: list[Mapping[str, Any]] = []
    for fact in _evidence_of(payload):
        kind = str(fact.get("kind") or "")
        if kind not in WITNESS_KINDS:
            continue
        attributed = False
        for prop in _propositions_of(fact):
            if str(prop.get("person_id") or "") == witness_id:
                attributed = True
                break
        if not attributed:
            speaker = _presentation_of(fact).get("speakerName")
            if speaker:
                norm_speaker = _identity_norm(speaker)
                if norm_speaker in (_identity_norm(witness_id), _identity_norm(name)):
                    attributed = True
        if attributed:
            out.append(fact)
    return tuple(sorted(out, key=lambda f: str(f.get("id") or "")))


def _time_anchors_of(fact: Mapping[str, Any]) -> tuple[str, ...]:
    """Concrete time anchors of one attributed fact: parseable proposition
    observed_at strings + presentation timestamps + bare clock tokens in the
    allowlisted statement/description text. Sorted by parsed epoch then raw
    string; deduplicated."""
    anchors: list[tuple[int, str]] = []
    presentation = _presentation_of(fact)
    for prop in _propositions_of(fact):
        raw = _parse_time_token(prop.get("observed_at"))
        if raw is not None:
            anchors.append((parse_iso8601_to_epoch(raw), raw))
    raw_ts = _parse_time_token(presentation.get("timestamp"))
    if raw_ts is not None:
        anchors.append((parse_iso8601_to_epoch(raw_ts), raw_ts))
    text_source = str(presentation.get("statement") or "") + " " + str(
        presentation.get("description") or ""
    )
    for token in _clock_tokens(text_source):
        anchors.append((_CLOCK_EPOCH, token))
    seen: set[str] = set()
    out: list[str] = []
    for _epoch, raw in sorted(anchors, key=lambda item: (item[0], item[1])):
        if raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return tuple(out)


def _fact_fallback_text(fact: Mapping[str, Any]) -> str:
    """The bounded allowlisted observation text of one attributed fact
    (statement > description > title; never empty by the grounding guards)."""
    presentation = _presentation_of(fact)
    for key in ("statement", "description", "title"):
        value = presentation.get(key)
        if isinstance(value, str) and value:
            return _clean_text(value, MAX_OBSERVATION_TEXT_CHARS)
    return ""


def _fact_grounds(
    payload: Mapping[str, Any],
    witness: Mapping[str, Any],
    fact: Mapping[str, Any],
    question: WitnessQuestionType,
) -> bool:
    """Does ONE attributed fact ground the given question? (deterministic).

    Per-question grounding — structured/allowlisted-evidence ONLY:

    - OBSERVATION: non-empty allowlisted statement/description;
    - TIME:     >= 1 concrete time anchor (observed_at / timestamp / clock);
    - PERSON:   a proposition referencing ANOTHER public person (person_id in
                the public persons, != witness), OR the allowlisted text
                mentions a public person's name (full name or a >= 3-char name
                word); the witness's own references never ground;
    - OBJECT:   a proposition object_id that exists in the public objects OR
                the text mentions a public object semantic label;
    - LOCATION: a proposition location_id in the public locations OR the text
                mentions a word of a public location name;
    - SOUND:    an attributed NOISE_HEARD_AT proposition (never text keywords).
    """
    if question == WitnessQuestionType.OBSERVATION:
        return bool(_fact_fallback_text(fact))
    if question == WitnessQuestionType.TIME:
        return bool(_time_anchors_of(fact))
    if question == WitnessQuestionType.SOUND:
        return any(
            str(p.get("type") or "") == SOUND_PROPOSITION_TYPE
            for p in _propositions_of(fact)
        )
    if question == WitnessQuestionType.PERSON:
        return _grounds_person(payload, witness, fact)
    if question == WitnessQuestionType.OBJECT:
        return _grounds_object(payload, fact)
    if question == WitnessQuestionType.LOCATION:
        return _grounds_location(payload, fact)
    raise ValueError(f"unexpected question {question!r}")


def _grounds_person(
    payload: Mapping[str, Any], witness: Mapping[str, Any], fact: Mapping[str, Any]
) -> bool:
    witness_id = str(witness.get("person_id") or "")
    public = _public_person_name_tokens(payload)
    public_ids = {pid for pid, _name, _words in public}
    for prop in _propositions_of(fact):
        person_id = str(prop.get("person_id") or "")
        if person_id and person_id != witness_id and person_id in public_ids:
            return True
    text = _fact_fallback_text(fact)
    norm_text = _identity_norm(text)
    if not norm_text:
        return False
    for person_id, norm_name, words in public:
        if person_id == witness_id:
            continue
        if norm_name and norm_name in norm_text:
            return True
        for word in words:
            if len(word) >= 3 and word in norm_text:
                return True
    return False


def _grounds_object(payload: Mapping[str, Any], fact: Mapping[str, Any]) -> bool:
    public_object_ids = {
        str(obj.get("object_id")) for obj in _objects_of(payload) if obj.get("object_id")
    }
    for prop in _propositions_of(fact):
        object_id = prop.get("object_id")
        if object_id is not None and str(object_id) in public_object_ids:
            return True
    text = _fact_fallback_text(fact)
    norm_text = _identity_norm(text)
    if not norm_text:
        return False
    for label in _public_object_labels(payload):
        if label and len(label) >= 3 and label in norm_text:
            return True
    return False


def _grounds_location(payload: Mapping[str, Any], fact: Mapping[str, Any]) -> bool:
    public_location_ids = {
        str(loc.get("location_id")) for loc in _locations_of(payload) if loc.get("location_id")
    }
    scene = _draft_of(payload).get("scene")
    if isinstance(scene, Mapping) and scene.get("location_id"):
        public_location_ids.add(str(scene.get("location_id")))
    for prop in _propositions_of(fact):
        location_id = prop.get("location_id")
        if location_id is not None and str(location_id) in public_location_ids:
            return True
    text = _fact_fallback_text(fact)
    norm_text = _identity_norm(text)
    for token in _public_location_name_tokens(payload):
        if token and len(token) >= 3 and token in norm_text:
            return True
    return False


def witness_attributed_to(
    payload: Mapping[str, Any], fact: Mapping[str, Any]
) -> str | None:
    """The public witness person_id that one published witness-kind evidence
    fact is attributed to, or None when the fact is not an interview source.

    The INVERSE of the ``_attributed_facts`` attribution rule (documented
    there): kind in WITNESS_KINDS AND (a proposition person_id equals a
    role=='witness' person's person_id, OR the presentation speakerName
    normalizes to that witness's id or name). Deterministic in the published
    persons order (the FIRST matching witness wins). None — a fact attributed
    only to a non-witness person (e.g. a suspect's own alibi statement) can
    never be an interview source.
    """
    if not isinstance(fact, Mapping):
        return None
    if str(fact.get("kind") or "") not in WITNESS_KINDS:
        return None
    speaker = _presentation_of(fact).get("speakerName")
    norm_speaker = _identity_norm(speaker) if isinstance(speaker, str) else ""
    prop_ids = {
        str(prop.get("person_id"))
        for prop in _propositions_of(fact)
        if prop.get("person_id") is not None
    }
    for person in _persons_of(payload):
        if str(person.get("role")) != "witness":
            continue
        witness_id = str(person.get("person_id") or "")
        if not witness_id:
            continue
        if witness_id in prop_ids:
            return witness_id
        if norm_speaker and norm_speaker in (
            _identity_norm(witness_id),
            _identity_norm(str(person.get("name") or "")),
        ):
            return witness_id
    return None


def witness_question_types_of(
    payload: Mapping[str, Any], fact: Mapping[str, Any]
) -> tuple[WitnessQuestionType, ...]:
    """The CLOSED question types (frozen ``ALL_QUESTIONS`` order) for which
    one published fact grounds the deterministic projection of its attributed
    witness (empty tuple when the fact is not an interview source).

    Exact same grounding matrix as ``project_witness_statement``: the fact's
    id appears in ``statement.evidence_ids`` of a grounded projection. The
    FIRST element is the deterministic ``questionType`` a re-read record
    carries (the interview response overrides it with the asked type).
    """
    witness_id = witness_attributed_to(payload, fact)
    if witness_id is None:
        return ()
    witness = witness_person_of(payload, witness_id)
    if witness is None:
        return ()
    fact_id = str(fact.get("id") or "")
    grounded: list[WitnessQuestionType] = []
    for question in ALL_QUESTIONS:
        projection = project_witness_statement(payload, witness, question)
        if fact_id in projection.statement.evidence_ids:
            grounded.append(question)
    return tuple(grounded)


def project_witness_statement(
    payload: Mapping[str, Any],
    witness: Mapping[str, Any],
    question: WitnessQuestionType,
) -> WitnessProjection:
    """Deterministic, ZERO-provider witness answer for one question type.

    Returns a ``WitnessProjection`` whose ``statement`` is either GROUNDED
    (allowlisted evidence text / concrete time anchors only) or NEUTRAL
    (``No. Nothing stood out to me.``, empty observations). ``evidence_ids``
    of a grounded statement are the ATTRIBUTED witness-kind facts that ground
    this question (the discoverable subset is what an interview may discover).
    """
    facts = _attributed_facts(payload, witness)
    attribution = tuple(str(f.get("id") or "") for f in facts)
    grounding: list[Mapping[str, Any]] = [
        fact for fact in facts if _fact_grounds(payload, witness, fact, question)
    ]
    if not grounding:
        neutral = WitnessStatement(
            summary=NEUTRAL_SUMMARY,
            observations=(),
            grounded=False,
            evidence_ids=(),
        )
        return WitnessProjection(neutral, attribution=attribution)

    statement = _build_statement(payload, witness, question, tuple(grounding))
    return WitnessProjection(statement, attribution=attribution)


def _build_statement(
    payload: Mapping[str, Any],
    witness: Mapping[str, Any],
    question: WitnessQuestionType,
    grounding: tuple[Mapping[str, Any], ...],
) -> WitnessStatement:
    """Assemble the grounded statement (bounded, allowlisted, deterministic).

    Every text fragment comes from the allowlisted presentation of the
    attributed evidence; the witness's own id is NEVER rendered. Concrete
    times are the actual anchors of the published propositions/statement.
    """
    del payload, witness  # unused; text/synthesis is fact-local
    primary = grounding[0]
    text = _fact_fallback_text(primary)
    presentation = _presentation_of(primary)
    summary = _clean_text(
        presentation.get("description")
        or presentation.get("title")
        or text,
        MAX_STATEMENT_SUMMARY_CHARS,
    )

    observations: list[WitnessObservation] = []
    if question == WitnessQuestionType.TIME:
        # Pair each concrete anchor with its owning fact's allowlisted text.
        anchor_pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for fact in grounding:
            fact_text = _fact_fallback_text(fact)
            for anchor in _time_anchors_of(fact):
                if anchor in seen:
                    continue
                seen.add(anchor)
                anchor_pairs.append((anchor, fact_text))
        for anchor, fact_text in anchor_pairs[:MAX_OBSERVATIONS]:
            observations.append(
                WitnessObservation(text=fact_text, time=anchor[:MAX_TIME_LEN])
            )
    elif question == WitnessQuestionType.SOUND:
        for fact in grounding:
            for prop in _propositions_of(fact):
                if str(prop.get("type") or "") != SOUND_PROPOSITION_TYPE:
                    continue
                raw = _parse_time_token(prop.get("observed_at"))
                observations.append(
                    WitnessObservation(text=_fact_fallback_text(fact), time=raw)
                )
                break
            if len(observations) >= MAX_OBSERVATIONS:
                break
        if not observations:
            observations.append(WitnessObservation(text=text))
    else:
        # OBSERVATION / PERSON / OBJECT / LOCATION: the allowlisted text the
        # witness gave (bounded). One observation; never invented prose.
        observations.append(WitnessObservation(text=text))

    return WitnessStatement(
        summary=summary,
        observations=tuple(observations),
        grounded=True,
        evidence_ids=tuple(str(f.get("id") or "") for f in grounding),
    )


__all__ = [
    "ALL_QUESTIONS",
    "INTERVIEW_STATEMENT_KINDS",
    "MAX_NAME_CHARS",
    "MAX_OBSERVATIONS",
    "MAX_STATEMENT_SUMMARY_CHARS",
    "MAX_TIME_LEN",
    "NEUTRAL_SUMMARY",
    "QUESTION_LABELS",
    "WITNESS_KINDS",
    "WitnessObservation",
    "WitnessPresence",
    "WitnessProjection",
    "WitnessQuestionType",
    "WitnessStatement",
    "project_witness_statement",
    "witness_attributed_to",
    "witness_person_of",
    "witness_presence",
    "witness_question_types_of",
    "witness_scene_object_id",
]
