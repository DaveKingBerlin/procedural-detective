"""Phase43 — provider-neutral trusted context helpers (P43-FR-01/02/06).

This module is the canonical home of the deterministic, app-owned context
projections the generation paths use to preserve canonical identity across
stages.  The implementations are behavior-identical to the private helpers
they were extracted from (``app.services.ollama_driver``), which now delegate
here so both the Ollama driver and future Frontier wiring consume ONE source.

Nothing in this module trusts provider output: every token is derived
deterministically from the locked ``LockedConstraints`` and/or the already
parsed canonical structures.  Ids are the project's single slug sources
(``identity_slug`` for identity fields, ``semantic_object_id`` for weapons);
display names are human-readable context only and are NEVER legal id values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.domain.eligibility import final_homicide_target_ids
from app.domain.time_interval import epoch_to_iso, parse_iso8601
from app.generation.constraints import normalize_identity
from app.world.requirements import semantic_object_id

# --------------------------------------------------------------------------- #
# canonical id helpers
# --------------------------------------------------------------------------- #

_HONORIFICS: frozenset[str] = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "sir", "madam", "her", "his"}
)

# The app-owned "other sharp weapon" candidates the weapon solver tests
# against the locked weapon (mirror of the kit base objects).
_BASE_SHARP_WEAPON_IDS: tuple[str, ...] = (
    "kitchen_knife",
    "letter_opener",
    "scissors",
)

# Default distant-location travel rule the driver may deterministically add so
# an alternative suspect's observation actually proves the scene unreachable.
_DEFAULT_DISTANT_TRAVEL_SECONDS = 1200


def _today_date() -> str:
    """Deterministic ISO date of the run day (``YYYY-MM-DD``)."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")


def identity_slug(value: str | None) -> str:
    """Deterministic lowercase snake id from a locked display value.

    ASCII letters/digits only (mirrors ``normalize_identity``), honorifics
    dropped, words joined with ``_``. ``"Dr. Anna Weiss"`` ->
    ``"anna_weiss"``. Long values are truncated to the SAME 40-character
    bound as ``semantic_object_id``/``composer._slugify`` (ADV-237: the id
    sheet must NEVER diverge from the composed semantic object id for a
    >40-char locked weapon — the weapon line uses ``semantic_object_id``
    directly so sheet and composer ALWAYS agree).
    """
    words = [
        word
        for word in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if word not in _HONORIFICS
    ]
    joined = " ".join(words)
    return joined[:40].replace(" ", "_").strip("_")


# --------------------------------------------------------------------------- #
# provider-neutral locked-identity data (P43-FR-01)
# --------------------------------------------------------------------------- #

# The canonical locked entity fields and the ``*Id`` name each renders under.
_LOCKED_FIELD_ID_NAMES: tuple[tuple[str, str], ...] = (
    ("victim", "victim_id"),
    ("murderer", "murderer_id"),
    ("motive", "motive_id"),
    ("weapon", "weapon_id"),
    ("location", "location_id"),
    ("witness", "witness_id"),
)


@dataclass(frozen=True)
class LockedIdentity:
    """One locked entity carrying BOTH canonical identity and display value.

    ``canonical_id`` is the ONLY legal value for the matching ``*Id`` field;
    ``display_name`` is the raw locked human text and is human-readable
    context only — it is NEVER a legal id value (P43-FR-01).
    """

    field: str
    canonical_id: str
    display_name: str


def locked_identities(locked: Any) -> tuple[LockedIdentity, ...]:
    """Deterministic locked entities from a ``LockedConstraints`` (or ``()``).

    Identity fields are reduced through ``identity_slug`` and the weapon
    through ``semantic_object_id`` — the SAME single slug sources the rest of
    the pipeline uses.  ``display_name`` is the raw locked human text.
    """
    if locked is None or not hasattr(locked, "locked_fields"):
        return ()
    # DEF-067 (ADV-43-08): an empty/whitespace locked value is ABSENT — it must
    # never become a locked identity with an empty ``canonicalId``/``displayName``
    # in the authoritative sheet. ``parse_prompt`` normalizes empties to ``None``
    # too; this defensive filter also covers a directly-constructed
    # ``LockedConstraints(...='')``.
    fields = {
        key: value
        for key, value in locked.locked_fields()
        if value is not None and str(value).strip()
    }
    identities: list[LockedIdentity] = []
    for field, _id_name in _LOCKED_FIELD_ID_NAMES:
        value = fields.get(field)
        if value is None:
            continue
        canonical_id = (
            semantic_object_id(value) if field == "weapon" else identity_slug(value)
        )
        identities.append(
            LockedIdentity(
                field=field, canonical_id=canonical_id, display_name=value
            )
        )
    return tuple(identities)


def locked_id_sheet(attempt: Any) -> str:
    """The app-owned cross-stage id sheet (empty when nothing is locked).

    Rendered from the ONE canonical ``locked_identities`` source so it can
    never drift from ``render_locked_identity_sheet``.
    """
    locked = getattr(attempt, "locked", None)
    if locked is None:
        return ""
    identities = locked_identities(locked)
    if not identities:
        return ""
    id_name = {field: name for field, name in _LOCKED_FIELD_ID_NAMES}
    lines = [
        "Locked identity sheet â€” use THESE exact id tokens for the locked "
        "people/objects in your JSON (never invent alternative ids):"
    ]
    for identity in identities:
        lines.append(f"- {id_name[identity.field]}: {identity.canonical_id}")
    time_value = {
        key: value
        for key, value in locked.locked_fields()
        if value is not None and str(value).strip()
    }.get("crime_time")
    if time_value:
        lines.append(
            f"- crime_time: the locked time is {time_value} â€” write "
            f"crimeTime.canonical EXACTLY as {_today_date()}T{time_value}:00+02:00 "
            "(today's date at the locked time, timezone +02:00)"
        )
    return "\n".join(lines)


def render_locked_identity_sheet(locked: Any) -> str:
    """AUTHORITATIVE locked-identity block (P43-FR-01 / P43-FR-02).

    Lists every locked entity with its ``canonicalId`` (the ONLY legal value
    for the matching ``*Id`` field) and, clearly separated, its human-readable
    ``displayName``.  The block states explicitly that display names are never
    legal id values.  The locked location is included whenever present (it is
    part of ``locked_identities``).  Empty when nothing is locked.
    """
    identities = locked_identities(locked)
    if not identities:
        return ""
    lines = [
        "LOCKED CANONICAL IDENTITY SHEET (AUTHORITATIVE — every *Id field MUST "
        "use the canonicalId below; display names are NEVER legal id values):"
    ]
    for identity in identities:
        lines.append(
            f"- {identity.field}:\n"
            f"    canonicalId: {identity.canonical_id}\n"
            f"    displayName (human-readable context ONLY, never a legal id): "
            f"{identity.display_name}"
        )
    lines.append(
        "- RULE: use each canonicalId for the matching *Id field (including the "
        "locationId). NEVER put a displayName in an *Id field, and never "
        "invent, rename, translate, slug or expand an id."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# approved public material / travel rules (parsed-case projection)
# --------------------------------------------------------------------------- #


def approved_public_material(public: Any) -> str:
    """The approved public-id sheet from the PARSED CASE output (stage 2..N).

    The evidence stage must reference the SAME person/location/motive ids the
    CASE stage produced (a fresh id namespace per stage would break the
    deterministic engine's referential integrity). The driver passes the
    parsed public people/locations/motives forward as an approved allowlist,
    PLUS the approved travel rules (the who solver can only exclude a suspect
    through travel-rule-backed opportunity observations â€” a missing rule
    makes an observation useless): sanitized public draft material (id +
    name + role), never truth, never ids from hidden sections. Returns an
    empty string when nothing parsed.
    """
    lines = [
        "APPROVED PUBLIC MATERIAL (reference ONLY these ids in your "
        "propositions â€” never invent ids):"
    ]
    any_entry = False
    for tag, entries, id_attr, name_attr in (
        ("person", getattr(public, "persons", ()) or (), "person_id", "name"),
        ("location", getattr(public, "locations", ()) or (), "location_id", "name"),
        ("motive", getattr(public, "motives", ()) or (), "motive_id", "label"),
    ):
        for entry in entries:
            value = getattr(entry, id_attr, None)
            label = ""
            if name_attr:
                label = getattr(entry, name_attr, None)
            if not value:
                continue
            lines.append(
                f"- {tag} id {value}"
                + (f": {label}" if label else "")
            )
            any_entry = True
    for rule in getattr(public, "travel_rules", ()) or ():
        frm = getattr(rule, "from_location_id", None)
        to = getattr(rule, "to_location_id", None)
        travel = getattr(rule, "travel_time_seconds", None)
        if not frm or not to:
            continue
        lines.append(
            f"- travel rule {frm} -> {to}: {travel}s"
        )
        any_entry = True
    if not any_entry:
        return ""
    return "\n".join(lines)


def approved_travel_sheet(public: Any) -> str:
    """The focused APPROVED TRAVEL RULES block for the evidence prompt.

    The who solver's impossible-opportunity exclusion requires a public
    travel rule FROM the observation location TO the scene; a fact is useless
    without the rule. This projection forwards every parsed travel rule so the
    model can pick a genuinely distant location and the exclusion algebra
    works. Empty when nothing parsed.
    """
    rules = [
        (
            getattr(r, "from_location_id"),
            getattr(r, "to_location_id"),
            getattr(r, "travel_time_seconds"),
        )
        for r in (getattr(public, "travel_rules", ()) or ())
        if getattr(r, "from_location_id") and getattr(r, "to_location_id")
    ]
    if not rules:
        return ""
    lines = ["APPROVED TRAVEL RULES (the travel times between locations):"]
    for frm, to, seconds in rules:
        lines.append(f"- {frm} -> {to}: {seconds} seconds")
    return "\n".join(lines)


def weapon_evidence_id(attempt: Any, evidence_spec: Any) -> str:
    """The sealed evidence item id that references the LOCKED WEAPON object.

    The world stage can then bind the weapon placement's ``evidenceId`` to a
    REAL evidence id (data-level clickability: clicking the pick opens the
    weapon evidence). Empty when no evidence references the weapon.
    """
    locked = getattr(attempt, "locked", None)
    weapon = getattr(locked, "weapon", None) if locked is not None else None
    if not isinstance(weapon, str) or not weapon:
        return ""

    # ADV-237 — the needle is the SINGLE slug source: evidence propositions
    # reference the id-sheet token = ``semantic_object_id(weapon)`` (the same
    # 40-char-bounded slug the composer materializes). ``normalize_identity``
    # is invariant to "_"/" ", so this equals the raw locked text for short
    # weapons and only differs when the 40-char truncation bites (the exact
    # ADV-237 divergence: a >40-char locked weapon must still find its sealed
    # evidence).
    needle = normalize_identity(semantic_object_id(weapon))
    for item in getattr(evidence_spec, "evidence", ()) or ():
        item_id = getattr(item, "id", None)
        for prop in getattr(item, "propositions", ()) or ():
            object_id = getattr(prop, "object_id", None)
            if isinstance(object_id, str) and normalize_identity(object_id) == needle:
                return str(item_id) if item_id else ""
    return ""


# --------------------------------------------------------------------------- #
# evidence algebra seeding (deterministic; app-owned)
# --------------------------------------------------------------------------- #


def evidence_timeline(canonical: str) -> dict[str, str]:
    """The six deterministic evidence timestamps (same date/+02:00 as crime).

    Derived by OFFSET from the locked canonical crime time so the evidence
    window always bounds the canonical tick (the truth comparison stays
    all_true) and the opportunity exclusions cover the whole window:
    victim last seen 110s before, body found 91s after (23:43:31 for a
    23:42:00 crime), scene observation 10s before (uncertainty 90 =>
    [canonical-100, canonical+81) — the fixture-verified narrow window),
    other-suspect observations 120s before (u 30), locked-person scene
    presence 20s before (u 60), alibi departure 32 minutes before.
    """
    tick, offset = parse_iso8601(canonical)
    return {
        "last_seen": epoch_to_iso(tick - 110, offset),
        "body_found": epoch_to_iso(tick + 91, offset),
        "scene_observation": epoch_to_iso(tick - 10, offset),
        "alternate_observed": epoch_to_iso(tick - 120, offset),
        "presence": epoch_to_iso(tick - 20, offset),
        "alibi_departure": epoch_to_iso(tick - 1920, offset),
    }


def deduction_seed(attempt: Any, crime: Any, public: Any) -> str:
    """Concrete MUST-EMIT fact seed for the evidence prompt (app-owned).

    Lists the EXACT ids and timestamps the evidence facts should reference:
    the model's OWN person/motive/location tokens (parsed from its case
    output) plus the locked ids and locked canonical time. Instructs FACTS
    only, never conclusions, never the hidden answer. Empty when the parsed
    case cannot seed (the model then works from the generic contract).
    """
    if crime is None:
        return ""
    scene_id = getattr(getattr(public, "scene", None), "location_id", None) or crime.location_id
    murderer_id = crime.murderer_id
    motive_id = crime.motive_id
    weapon_id = crime.weapon_id
    victim_id = crime.victim_id
    canonical = getattr(getattr(crime, "crime_time", None), "canonical", None)
    if not (scene_id and murderer_id and motive_id and weapon_id and victim_id and canonical):
        return ""
    try:
        timeline = evidence_timeline(canonical)
    except (TypeError, ValueError):
        return ""
    # Phase41A — every NON-MURDERER FINAL HOMICIDE TARGET (public suspects AND
    # witnesses, victim excluded) gets the impossible-opportunity exclusion, so
    # classic suspect cases stay uniquely solvable once witnesses enter the WHO
    # candidate universe and a witness-murderer case becomes uniquely solvable
    # (all other targets excluded, murderer viable).
    other_suspects = sorted(
        target
        for target in final_homicide_target_ids(public)
        if target != murderer_id
    )
    other_motives = sorted(
        m.motive_id
        for m in (getattr(public, "motives", ()) or ())
        if m.motive_id != motive_id
    )
    sharp_base = [
        oid
        for oid in _BASE_SHARP_WEAPON_IDS
        if normalize_identity(oid) != normalize_identity(weapon_id)
    ]
    travel = approved_travel_sheet(public)
    lines = [
        "DEDUCTION SEED (use THESE EXACT ids and timestamps â€” emit FACTS for "
        "them, never conclusions):",
        f"- locked person (place AT the scene around the crime time): {murderer_id}",
        f"- scene location id: {scene_id}; victim id: {victim_id}",
        f"- locked motive id: {motive_id}; locked weapon id: {weapon_id}",
        (
            f"- exclude these motives (MOTIVE_FACT_CONTRADICTED): "
            f"{', '.join(other_motives) if other_motives else '(none yet â€” the case needs at least two alt motives)'}"
        ),
        (
            f"- observe these other suspects at DISTANT locations (impossible "
            f"opportunity, PERSON_OBSERVED_AT_LOCATION): "
            f"{', '.join(other_suspects) if other_suspects else '(none yet â€” the case needs at least two alt suspects)'}"
        ),
        (
            f"- other sharp weapons needing FORENSIC_WEAPON_MATCH "
            f"match:false: {', '.join(sharp_base)}"
        ),
        (
            f"- timestamps (ALL with the SAME date and +02:00 as the locked "
            f"crime time {canonical}): victim last seen {timeline['last_seen']}; "
            f"body found {timeline['body_found']}; scene observation "
            f"{timeline['scene_observation']} (uncertaintySeconds 90); each "
            f"other suspect observed at their distant location "
            f"{timeline['alternate_observed']} (uncertaintySeconds 30); "
            f"{murderer_id} at the scene {timeline['presence']} "
            f"(uncertaintySeconds 60); alibi claimedDeparture "
            f"{timeline['alibi_departure']}."
        ),
    ]
    if travel:
        lines.append(
            "  Pick a DIFFERENT travel-rule location from this list for every "
            "other suspect (the rule proves the scene is unreachable):\n" + travel
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Phase 41A — shared provider-neutral case-quality rules (P43-FR-06)
# --------------------------------------------------------------------------- #

PHASE41A_QUALITY_RULES = (
    "PHASE 41A QUALITY RULES (deterministic, validated after every pass):\n"
    "- The victim MUST NOT carry SUSPECT_ELIGIBLE (the victim is never a "
    "suspect candidate).\n"
    "- A witness MUST NOT carry SUSPECT_ELIGIBLE (witnesses keep role "
    "'witness' with only VISIBLE_CHARACTER).\n"
    "- A witness MAY be a final homicide target (the true murderer may be a "
    "public witness); witness-ness is neither an alibi nor a disqualifier.\n"
    "- Public roles and public text MUST NOT reveal hidden truth: never label "
    "or describe a public person as the murderer and never expose the answer.\n"
    "- The victim is represented through the suicide option, never as the "
    "homicide murderer: a HOMICIDE murderer is a NON-victim final target, "
    "while a SUICIDE names no murderer.\n"
    "- WITNESS_CLAIMS are interview attribution only and MUST NOT establish "
    "WHO/WHY/WEAPON; witness claims are never deduction premises.\n"
    "- Hidden witness truthfulness/deception is server-owned: never emit any "
    "truthful/deceptive status or marker in public output.\n"
    "- Solver-critical conclusions come from the canonical evidence and "
    "deduction structures, never from unsupported witness claims."
)


__all__ = [
    "LockedIdentity",
    "PHASE41A_QUALITY_RULES",
    "approved_public_material",
    "approved_travel_sheet",
    "deduction_seed",
    "evidence_timeline",
    "identity_slug",
    "locked_id_sheet",
    "locked_identities",
    "render_locked_identity_sheet",
    "weapon_evidence_id",
]
