"""Phase 7 reveal projection module — EVERY player-safe allowlist projection
over the pinned published payload (REQUIREMENTS 40.12 / 3.5, Phase7 E/F/J).

THE single source of the frozen Phase 7 projections:

- ``candidate_block_of``        — the player-safe ``candidates`` object of the
  investigation bootstrap (Phase7 J/K): the PUBLISHED candidate universes
  with their PUBLIC names/labels, sorted alphabetically by id, NEVER
  winner-marked and carrying NO canonical designation;
- ``truth_labels`` / ``reveal_dto_of`` — the frozen reveal allowlist: the
  canonical truth labels (ids + PUBLIC persons/motives/objects labels), the
  player's immutable accusation, per-dimension correctness + score (evaluated
  — never stored — by ``app.services.accusation``), a player-safe timeline
  derived from PUBLIC discoverable evidence, and the player-safe explanation;
- ``timeline_of``               — deterministic derive-from-evidence timeline;
- ``explanation_evidence_of``   — the FROZEN rule-evidence mapping
  (evidence kind/role -> short player-safe point phrase) applied to the
  published proof's usable evidence ids.

LEAK BOUNDARY (REQUIREMENTS 41.4/41.5, Phase7 J): every projection reads ONLY
the public sections of the payload (``draft``) plus the allowlisted proof
fields (usable evidence ids). The projection NEVER returns SolverProof /
SolutionProof internals, ``acceptedScoring``, prompts, diagnostics, tokens,
verifiers, generationAttemptId, private admission/quota state, or internal
database ids. Weapon display names are NOT stored on the public objects: they
are derived deterministically from the SEMANTIC weapon identity — the public
``object_id`` (``bronze_ceremonial_ice_pick`` renders
"Bronze Ceremonial Ice Pick"; ``kitchen_knife`` renders "Kitchen Knife";
DEF-081 — a procedural render id ``proc.decor.<hash>`` is a render-layer token
and can NEVER be a player-facing weapon label).
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.domain.time_interval import parse_iso8601_to_epoch

# --------------------------------------------------------------------------- #
# public label lookups over the serialized draft
# --------------------------------------------------------------------------- #


def _draft_of(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    draft = payload.get("draft")
    return draft if isinstance(draft, Mapping) else {}


def persons_by_id(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(p.get("person_id")): p
        for p in _draft_of(payload).get("persons") or ()
        if isinstance(p, Mapping) and p.get("person_id")
    }


def motives_by_id(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(m.get("motive_id")): m
        for m in _draft_of(payload).get("motives") or ()
        if isinstance(m, Mapping) and m.get("motive_id")
    }


def objects_by_id(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(o.get("object_id")): o
        for o in _draft_of(payload).get("objects") or ()
        if isinstance(o, Mapping) and o.get("object_id")
    }


def evidence_by_id(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(f.get("id")): f
        for f in _draft_of(payload).get("evidence") or ()
        if isinstance(f, Mapping) and f.get("id")
    }


_ASSET_PREFIXES = ("PROP_", "DOOR_", "FURN_", "TECH_")
_TRAILING_NUM_RE = re.compile(r"_\d+$")


def weapon_label_of(asset_id: Any, *, object_id: Any = None) -> str:
    """Deterministic PUBLIC display label of a world-object weapon.

    DEF-081 identity separation: the player-visible label is derived ONLY
    from the SEMANTIC object identity — the published public ``object_id``
    (e.g. ``bronze_ceremonial_ice_pick`` -> "Bronze Ceremonial Ice Pick") —
    NEVER from the render ``asset_id``. A procedural render id
    (``proc.decor.<hash>``) is a render-layer token and must never surface as
    a player-facing weapon label; the semantic id is what the player sees,
    submits and reveals. For catalog objects the semantic id is the same
    human-oriented slug as before (``kitchen_knife`` -> "Kitchen Knife"), so
    catalog rendering is byte-identical. The render ``asset_id`` is kept ONLY
    as a defensive fallback for legacy/crafted payloads that carry no
    ``object_id``.
    """
    source = (
        str(object_id)
        if object_id is not None and str(object_id)
        else str(asset_id if asset_id is not None else "")
    )
    for prefix in _ASSET_PREFIXES:
        if source.startswith(prefix):
            source = source[len(prefix):]
            break
    source = _TRAILING_NUM_RE.sub("", source)
    words = [w for w in source.split("_") if w]
    label = " ".join(word.capitalize() for word in words) or str(asset_id or "")
    # DEF-081 hard guarantee: a procedural render id can NEVER become the
    # player-facing label — even for a crafted payload whose semantic object
    # id is missing entirely, the label degrades to a neutral literal instead
    # of leaking the render asset id.
    if label.casefold().startswith("proc.") or label.casefold().startswith("proc_"):
        return "Object"
    return label


# --------------------------------------------------------------------------- #
# candidates block (Phase7 J/K)
# --------------------------------------------------------------------------- #


def candidate_block_of(payload: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Player-safe ``candidates`` object of the investigation bootstrap.

    - ``suspects`` from the published SUSPECT_ELIGIBLE universe + public
      persons (``{"id", "name"}``);
    - ``motives`` from the published MOTIVE_CANDIDATE universe + public
      motives (``{"id", "label"}``);
    - ``weapons`` from the published POTENTIAL_WEAPON universe + public
      objects (``{"id", "assetId", "name"}``).

    Every list is sorted alphabetically by ``id``. Candidate universes may
    contain the winning id, but NO field marks/ranks it and NO canonical
    designation is ever included (Phase7 P / test N24).
    """
    universes = payload.get("universes")
    if not isinstance(universes, Mapping):
        universes = {}
    persons = persons_by_id(payload)
    motives = motives_by_id(payload)
    objects = objects_by_id(payload)

    suspects: list[dict[str, Any]] = []
    for suspect_id in sorted(str(i) for i in (universes.get("suspect_ids") or ())):
        person = persons.get(suspect_id)
        if person is None:
            continue  # universe id without public person -> never expose
        suspects.append(
            {"id": suspect_id, "name": str(person.get("name") or suspect_id)}
        )

    candidate_motives: list[dict[str, Any]] = []
    for motive_id in sorted(str(i) for i in (universes.get("motive_ids") or ())):
        motive = motives.get(motive_id)
        if motive is None:
            continue
        candidate_motives.append(
            {"id": motive_id, "label": str(motive.get("label") or motive_id)}
        )

    weapons: list[dict[str, Any]] = []
    for weapon_id in sorted(str(i) for i in (universes.get("weapon_ids") or ())):
        obj = objects.get(weapon_id)
        if obj is None:
            continue
        asset_id = obj.get("asset_id")
        weapons.append(
            {
                "id": weapon_id,
                "assetId": str(asset_id),
                # DEF-081: the display label comes from the SEMANTIC object
                # identity (the public object id), never from the render
                # assetId (a procedural ``proc.decor.<hash>`` must never be a
                # player-facing weapon label).
                "name": weapon_label_of(asset_id, object_id=weapon_id),
            }
        )

    return {"suspects": suspects, "motives": candidate_motives, "weapons": weapons}


# --------------------------------------------------------------------------- #
# timeline (REQUIREMENTS 3.5 key timeline; derive from PUBLIC discoverable
# evidence only — max 12, sorted by time, deterministic)
# --------------------------------------------------------------------------- #


def _presentation_title(fact: Mapping[str, Any]) -> str:
    presentation = fact.get("presentation")
    title = presentation.get("title") if isinstance(presentation, Mapping) else None
    return str(title) if isinstance(title, str) and title else str(fact.get("id") or "Evidence")


def _timeline_candidates_of(fact: Mapping[str, Any]) -> tuple[tuple[int, str], ...]:
    """(epoch, iso-string) timestamps publicly attached to one evidence fact:
    the presentation ``timestamp``, ``events[*].time`` and every proposition
    ``observed_at``. Only ISO-8601-with-offset values parse (bare/unknown
    values are skipped — the timeline stays verifiable and deterministic)."""
    out: list[tuple[int, str]] = []
    presentation = fact.get("presentation")
    if isinstance(presentation, Mapping):
        raw_ts = presentation.get("timestamp")
        if isinstance(raw_ts, str) and raw_ts:
            try:
                out.append((parse_iso8601_to_epoch(raw_ts), raw_ts))
            except ValueError:
                pass
        events = presentation.get("events")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                raw_time = event.get("time")
                if isinstance(raw_time, str) and raw_time:
                    try:
                        out.append((parse_iso8601_to_epoch(raw_time), raw_time))
                    except ValueError:
                        pass
    for prop in fact.get("propositions") or ():
        if not isinstance(prop, Mapping):
            continue
        raw_at = prop.get("observed_at")
        if isinstance(raw_at, str) and raw_at:
            try:
                out.append((parse_iso8601_to_epoch(raw_at), raw_at))
            except ValueError:
                pass
    return tuple(out)


_TL_MAX = 12


def timeline_of(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """Player-safe deterministic timeline from PUBLIC discoverable evidence.

    For every discoverable evidence fact, the publicly attached timestamps
    (presentation timestamp / cctv event times / proposition observed_at) are
    combined with the fact's public presentation title and the result is
    sorted by time (ties broken by description) and capped at 12 entries.
    Facts with no public timestamp are skipped. Never touches truth/proof.
    """
    entries: list[tuple[int, str, str]] = []
    for fact in _draft_of(payload).get("evidence") or ():
        if not isinstance(fact, Mapping):
            continue
        if fact.get("discoverable") is False:
            continue  # only PUBLICLY discoverable evidence may appear
        description = _presentation_title(fact)
        for epoch, iso_time in _timeline_candidates_of(fact):
            entries.append((epoch, iso_time, description))
    # Deduplicate exact (epoch, description) pairs.
    seen: set[tuple[int, str]] = set()
    unique: list[tuple[int, str, str]] = []
    for entry in sorted(entries, key=lambda e: (e[0], e[2], e[1])):
        key = (entry[0], entry[2])
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
        if len(unique) >= _TL_MAX:
            break
    return [{"time": iso_time, "description": description} for _, iso_time, description in unique]


# --------------------------------------------------------------------------- #
# explanation / proof projection (FROZEN rule-evidence mapping — Phase7 F)
# --------------------------------------------------------------------------- #

# Frozen mapping: evidence kind/role -> short player-safe "point" phrase. The
# strings are the documented explainer vocabulary (Phase7 F examples); raw
# rule IDs, propositions, status/effect internals never appear.
POINT_BY_KIND: dict[str, str] = {
    "forensic": "Forensic link to the weapon",
    "physical": "Forensic link to the weapon",
    "financial": "Financial evidence",
    "email": "Document evidence",
    "digital": "Document evidence",
    "document": "Document evidence",
    "cctv": "Observation / timing",
    "cctv_observation": "Observation / timing",
    "view_record": "Observation / timing",
    "witness_observation": "Observation / timing",
    "testimonial": "Alibi contradiction",
    "statement": "Alibi contradiction",
    "witness_statement": "Alibi contradiction",
    "suspect_statement": "Alibi contradiction",
    "object": "Observation / timing",
}
_DEFAULT_POINT = "Evidence"


def point_for_kind(kind: str) -> str:
    return POINT_BY_KIND.get(str(kind), _DEFAULT_POINT)


def _solver_proof_of(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    proof = payload.get("solverProof")
    return proof if isinstance(proof, Mapping) else {}


def _string_ref_id(value: Any, *, what: str) -> str:
    """One proof-reference element -> its string id.

    Only strings and numbers are usable evidence ids. ANY other element type
    (dict/list/bool/None/object) is corrupt proof material (ADV-210 "garbage
    types") -> ``RevealProjectionError`` -> the sanitized 500: a
    ``str(dict)`` id could never match a published evidence fact and silently
    producing one would only mask an inconsistent payload.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise RevealProjectionError(
            f"solver proof {what} carries a boolean ref element"
        )
    if isinstance(value, (int, float)):
        return str(value)
    raise RevealProjectionError(
        f"solver proof {what} carries a non-id element ({type(value).__name__})"
    )


def _clean_ref_list(values: Any, *, what: str) -> tuple[str, ...]:
    """Stringified, SORTED, DEDUPLICATED evidence-ref tuple (fail-closed).

    ADV-210 (a): duplicate ids inside one reference list are DEDUPED (a
    dimension list may never carry the same evidence point twice). ``values``
    must be a list/tuple of strings/numbers; anything else is corrupt.
    """
    if not isinstance(values, (list, tuple)):
        raise RevealProjectionError(
            f"solver proof {what} must be a list of evidence ids"
        )
    return tuple(sorted({_string_ref_id(v, what=what) for v in values}))


def evidence_ids_used_of(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """The published proof's usable evidence ids: the solver's
    ``evidence_ids_used`` (the sorted union of proof.when.critical_evidence_ids
    and every rule-outcome evidence id — see app.domain.proof.build_proof),
    falling back to the WHEN-critical ids for crafted payloads.

    IDs are stringified, SORTED and DEDUPLICATED; corrupt element types raise
    (fail closed, ADV-210).
    """
    proof = _solver_proof_of(payload)
    used = proof.get("evidence_ids_used")
    if isinstance(used, list) and used:
        return _clean_ref_list(used, what="evidence_ids_used")
    when = proof.get("time")
    critical = when.get("critical_evidence_ids") if isinstance(when, Mapping) else None
    if isinstance(critical, (list, tuple)) and critical:
        return _clean_ref_list(critical, what="time.critical_evidence_ids")
    return ()


def _evidence_point_of(
    evidence_id: str, fact: Mapping[str, Any] | None
) -> dict[str, str] | None:
    """One player-safe EvidencePointDTO dict for an evidence id, or None when
    the fact is unknown / not publicly discoverable / carries no public
    presentation title (the SAME allowlist filter as ``explanation_evidence_of``
    and the Phase18C dimension projection — one shared rule)."""
    if fact is None:
        return None
    if fact.get("discoverable") is False:
        return None
    presentation = fact.get("presentation")
    title = presentation.get("title") if isinstance(presentation, Mapping) else None
    if not isinstance(title, str) or not title:
        return None
    return {
        "evidenceId": evidence_id,
        "title": title,
        "point": point_for_kind(str(fact.get("kind"))),
    }


def explanation_evidence_of(
    payload: Mapping[str, Any],
) -> list[dict[str, str]]:
    """The player-safe explainer references (Phase7 F).

    Selects the published proof's usable evidence ids, FILTERED to
    discoverable facts with a presentation title, and maps each to the frozen
    rule-evidence ``point`` phrase by its kind. Deterministic order: by
    evidence id.
    """
    facts = evidence_by_id(payload)
    out: list[dict[str, str]] = []
    for evidence_id in evidence_ids_used_of(payload):
        point = _evidence_point_of(evidence_id, facts.get(evidence_id))
        if point is not None:
            out.append(point)
    return out


_DIMENSION_NAMES = ("who", "why", "weapon", "when")

# The serialized ``solverProof`` block carries the per-dimension usable
# evidence ids under these snake_case keys (serialized dataclass fields of
# ``app.validation.solution.SolutionProof``; ``when`` reuses the already
# persisted ``time.critical_evidence_ids``).
_DIMENSION_REF_KEYS = {
    "who": "who_evidence_ids",
    "why": "why_evidence_ids",
    "weapon": "weapon_evidence_ids",
}


def _dimension_refs_of(payload: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Per-dimension usable evidence id references from the pinned payload's
    ``solverProof`` block (Phase18C).

    WHO/WHY/WEAPON read the per-dimension refs serialized at publish time from
    the deduction proof's rule-outcome evidence ids; WHEN reuses
    ``time.critical_evidence_ids``. Every list is sorted, stringified and
    DEDUPLICATED (ADV-210). A MISSING per-dimension key degrades to an empty
    tuple (the empty-proof resilience contract AND the pre-18C shape: a payload
    published before Phase 18C carries ``solverProof`` + ``evidence_ids_used``
    but none of the new per-dimension keys). A per-dimension key whose VALUE is
    not a list, or ``time`` whose value is not a mapping, is garbage proof
    material -> fail closed (ADV-210).
    """
    proof = _solver_proof_of(payload)

    def _ids(key: str) -> tuple[str, ...]:
        value = proof.get(key)
        if value is None:
            return ()
        return _clean_ref_list(value, what=key)

    refs = {
        name: _ids(key) for name, key in _DIMENSION_REF_KEYS.items()
    }
    when_block = proof.get("time")
    if when_block is None:
        refs["when"] = ()
    elif isinstance(when_block, Mapping):
        refs["when"] = _clean_ref_list(
            when_block.get("critical_evidence_ids") or (),
            what="time.critical_evidence_ids",
        )
    else:
        raise RevealProjectionError("solver proof time section is not an object")
    return refs


def _assert_dimension_flat_consistency(
    refs: Mapping[str, tuple[str, ...]],
    flat_ids: tuple[str, ...],
) -> None:
    """Phase18C cache-consistency invariant, fail-closed (DEF-053 pattern).

    The union of the four per-dimension id sets must equal the flat
    ``evidence_ids_used`` set when the flat list is non-empty; empty lists are
    allowed ONLY when the flat list is also empty. A violation means the pinned
    payload's proof material is internally inconsistent -> the reveal projection
    raises (sanitized 500 INTERNAL_ERROR) instead of rendering a partial
    dimension mapping.
    """
    union: set[str] = set()
    for ids in refs.values():
        union.update(ids)
    flat = set(flat_ids)
    if flat:
        if union != flat:
            raise RevealProjectionError(
                "solver proof dimension refs are inconsistent with evidence_ids_used"
            )
    elif union:
        raise RevealProjectionError(
            "solver proof carries dimension refs but no flat evidence ids"
        )


def dimensions_of(payload: Mapping[str, Any]) -> dict[str, list[dict[str, str]]]:
    """The Phase18C per-dimension proof board (WHO/WHY/WEAPON/WHEN -> supporting
    discovered evidence), built ONLY from the server-side proof references.

    Each dimension maps its usable evidence ids through the SAME public filter
    and frozen ``point`` vocabulary as the flat explainer. Fail-closed guards
    (ADV-210):

    - duplicates inside one dimension list are DEDUPED (never the same point
      twice);
    - a dimension ref list that exceeds the published evidence universe size
      (``len(refs) > len(evidence_by_id)``) is an impossible/corrupt proof ->
      ``RevealProjectionError`` -> the sanitized 500 (no unbounded response
      amplification);
    - garbage element types and mismatched id sets fail closed to the sanitized
      500, never a partial map;
    - PRE-18C compatibility: a payload published before Phase 18C carries
      ``solverProof`` + ``evidence_ids_used`` but NONE of the new per-dimension
      keys (WHO/WHY/WEAPON absent; WHEN reuses ``time.critical_evidence_ids``,
      a subset of the flat list). Such a payload previously raised (union of
      the when-only refs can never equal the larger flat list) and reveal
      answered a sanitized 500 for ANY old case. Now, when the new per-dimension
      refs are entirely absent/empty while ``evidence_ids_used`` is non-empty,
      the flat list is deterministically distributed to ALL FOUR dimensions
      (every usable id is shown under every dimension — there is no
      per-dimension attribution left in an 18C-less proof) so the reveal still
      200s with a populated board and the flat evidence list intact.
    """
    if not isinstance(payload.get("solverProof"), Mapping):
        raise RevealProjectionError("payload carries no solver proof section")
    facts = evidence_by_id(payload)
    universe_size = len(facts)
    refs = _dimension_refs_of(payload)
    flat = evidence_ids_used_of(payload)
    # ADV-210 (b): bounded output — a ref list larger than the whole published
    # evidence universe can never describe a real proof (any referenced id must
    # exist in the published evidence set, enforced at publish time).
    if len(flat) > universe_size:
        raise RevealProjectionError(
            "solver proof evidence_ids_used exceed the published evidence universe"
        )
    if any(len(ids) > universe_size for ids in refs.values()):
        raise RevealProjectionError(
            "solver proof dimension refs exceed the published evidence universe"
        )
    if flat and not (refs["who"] or refs["why"] or refs["weapon"]):
        # Pre-18C shape: none of the Phase-18C per-dimension keys carry any
        # ids -> no per-dimension attribution exists. Deterministic fallback:
        # every usable evidence id goes to ALL FOUR dimensions (documented in
        # the docstring). When the when-only refs already cover the flat list
        # this is harmless; when they don't (the normal 18C-less case) it is
        # what keeps the reveal on a populated board instead of a 500.
        refs = {name: flat for name in _DIMENSION_NAMES}
    _assert_dimension_flat_consistency(refs, flat)
    return {
        name: [
            point
            for point in (
                _evidence_point_of(evidence_id, facts.get(evidence_id))
                for evidence_id in refs.get(name, ())
            )
            if point is not None
        ]
        for name in _DIMENSION_NAMES
    }


# --------------------------------------------------------------------------- #
# reveal DTO assembly (Phase7 E — frozen allowlist)
# --------------------------------------------------------------------------- #


class RevealProjectionError(ValueError):
    """The pinned published payload's truth section is missing/invalid.

    DEF-053: the reveal projection must NEVER render partial canonical truth
    (a missing ``murderer_id`` previously rendered ``"murdererId": "None"``).
    Raised BEFORE any DTO field is built. The API layer maps it to the
    sanitized ``500 INTERNAL_ERROR`` envelope — the SAME status the
    pre-existing corrupt variants (missing crime_time / non-string canonical)
    already answer, and a status that exposes nothing about which field is
    broken (404 would mislead: the playthrough and its accusation DO exist).
    """


def truth_labels(payload: Mapping[str, Any]) -> dict[str, str]:
    """The canonical truth labels: ids from the truth section + PUBLIC names/
    labels from the published public persons/motives/objects. The DESIGNATION
    (which id is the winner) comes from the truth section at reveal time —
    this function is reveal-only and never feeds a pre-reveal response.

    DEF-053: the truth section is validated FIRST and any missing/invalid
    required field raises ``RevealProjectionError`` (-> sanitized 500) BEFORE
    str(None) can ever be rendered — zero partial truth is exposed.
    """
    truth = payload.get("truth")
    if not isinstance(truth, Mapping):
        raise RevealProjectionError("payload carries no truth section")
    crime = truth.get("crime")
    if not isinstance(crime, Mapping):
        raise RevealProjectionError("payload carries no truth/crime section")
    for name in ("murderer_id", "motive_id", "weapon_id"):
        value = crime.get(name)
        if not isinstance(value, str) or not value:
            raise RevealProjectionError(
                f"truth.crime.{name} is missing or invalid"
            )
    crime_time = crime.get("crime_time")
    if not isinstance(crime_time, Mapping):
        raise RevealProjectionError("truth.crime.crime_time is missing or invalid")
    canonical = crime_time.get("canonical")
    if not isinstance(canonical, str) or not canonical:
        raise RevealProjectionError(
            "truth.crime.crime_time.canonical is missing or invalid"
        )
    persons = persons_by_id(payload)
    motives = motives_by_id(payload)
    objects = objects_by_id(payload)
    murderer_id = str(crime["murderer_id"])
    motive_id = str(crime["motive_id"])
    weapon_id = str(crime["weapon_id"])
    person = persons.get(murderer_id, {})
    motive = motives.get(motive_id, {})
    obj = objects.get(weapon_id, {})
    asset_id = obj.get("asset_id")
    return {
        "murdererId": murderer_id,
        "murdererName": str(person.get("name") or murderer_id),
        "motiveId": motive_id,
        "motiveLabel": str(motive.get("label") or motive_id),
        "weaponId": weapon_id,
        # DEF-081: the canonical weapon label comes from the SEMANTIC weapon
        # identity (the truth's public weapon id), never from the render
        # assetId (a procedural ``proc.decor.<hash>`` must never be shown to
        # the player as the weapon name).
        "weaponName": weapon_label_of(asset_id, object_id=weapon_id),
        "crimeTime": canonical,
    }


def reveal_dto_of(
    payload: Mapping[str, Any],
    playthrough: Any,
    accusation_row: Any,
    evaluation: dict[str, bool],
) -> dict[str, Any]:
    """The frozen reveal response dict (Phase7 E).

    ``evaluation`` carries the derived (never stored) per-dimension booleans
    computed by ``app.services.accusation.evaluate_accusation``. The DTO is
    an explicit allowlist built only from public payload material, the
    immutable accusation row and those booleans: repeat reveals assemble the
    byte-identical DTO (idempotent, Phase7 N22).
    """
    truth = truth_labels(payload)
    overall = "solved" if all(
        evaluation.get(key)
        for key in ("murdererCorrect", "motiveCorrect", "weaponCorrect", "timeCorrect")
    ) else "incorrect"
    correct = sum(
        1
        for key in ("murdererCorrect", "motiveCorrect", "weaponCorrect", "timeCorrect")
        if evaluation.get(key)
    )
    return {
        "playthroughId": str(playthrough.playthrough_id),
        "caseId": str(playthrough.case_id),
        "caseVersion": int(playthrough.case_version),
        "status": "REVEALED",
        "truth": truth,
        "player": {
            "accusation": {
                "murdererId": str(accusation_row.murderer_id),
                "motiveId": str(accusation_row.motive_id),
                "weaponId": str(accusation_row.weapon_id),
                "crimeTime": str(accusation_row.crime_time),
            }
        },
        "result": {
            "murdererCorrect": bool(evaluation.get("murdererCorrect")),
            "motiveCorrect": bool(evaluation.get("motiveCorrect")),
            "weaponCorrect": bool(evaluation.get("weaponCorrect")),
            "timeCorrect": bool(evaluation.get("timeCorrect")),
            "overall": overall,
        },
        "score": {"correctDimensions": correct, "totalDimensions": 4},
        "timeline": timeline_of(payload),
        "explanation": {
            "evidence": explanation_evidence_of(payload),
            "dimensions": dimensions_of(payload),
        },
    }


__all__ = [
    "POINT_BY_KIND",
    "candidate_block_of",
    "dimensions_of",
    "evidence_ids_used_of",
    "explanation_evidence_of",
    "point_for_kind",
    "reveal_dto_of",
    "timeline_of",
    "truth_labels",
    "weapon_label_of",
]