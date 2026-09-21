"""Phase 16_2 — the Local-Llama (Ollama) Stage Driver.

``OllamaStageDriver`` walks the prompt-to-world pipeline with STRUCTURED
per-stage LLM calls over the SAME ``GenerateRequest`` / ``ProviderResult``
boundary (``OllamaProvider`` is the transport; nothing Ollama-specific leaks
out of that adapter):

    CASE/PEOPLE (CASE_TRUTH stage surface)
      -> EVIDENCE (EVIDENCE stage surface)
      -> WORLD_REQUIREMENTS (WORLD_REQUIREMENTS stage surface)
      -> Environment Resolver -> Asset Oracle -> World Composer
      -> ASSET_SPEC (only for unknown/REQUIRED objects the Oracle cannot
         resolve) with a bounded ASSET_SPEC_REPAIR loop

The driver PROPOSES structured facts; it NEVER decides who the murderer is or
whether a case is solvable. It assembles a normal ``GeneratedDraft`` so the
EXISTING deterministic validation suite (safety, solver, world, truth,
publication gate) runs unchanged, and it is invoked from inside the EXISTING
``GenerationController`` lifecycle (budget, repair/regenerate classification,
publication CAS) — no parallel lifecycle is introduced.

The driver's outcome when any stage yields terminal-unrepairable output is
classified by the existing controller: a provider-level failure
(``StageDriverProviderFailure``) fails the attempt; a stage parse/validation
failure leaves ``attempt.deferred_structural`` populated so
``pipeline.validate_draft`` classifies RECOVERABLE_REPAIR / REGENERATE /
TERMINAL exactly as today.

``OllamaAssetSpecProvider`` adapts the narrow ``AssetSpecProvider`` (used by
``app.world.composer.compose_world``) to the Ollama provider: an ASSET_SPEC
call followed by at most ``MAX_SPEC_REPAIR_PASSES`` ASSET_SPEC_REPAIR calls
each re-parzed and re-validated with the strict Phase 13 validator before being
returned. All calls consume the per-attempt provider-call budget.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.generation.provider import (
    GenerateRequest,
    GenerationStage,
    ProviderResult,
    StageDriverProviderFailure,
)
from app.generation.budgets import (
    CORE_BUCKET,
    PROVIDER_CALL_SAFETY_MARGIN_SECONDS,
)
from app.generation.failure_codes import (
    GenerationFailureCode,
    failure_code_for_budget_reason,
    infer_failure_code,
)
from app.core.observability import emit_event
from app.generation import prompts
from app.generation import parser as stage_parser
from app.domain.time_interval import (
    epoch_to_iso,
    parse_iso8601,
)
from app.world.composer import SemanticObjectResolutionError
from app.world.environment import canonicalize_environment_hint
from app.world.requirements import (
    CRITICALITY_DECORATIVE,
    CRITICALITY_REQUIRED,
    ObjectRequest,
    PlacementRelation,
    RELATION_KINDS,
    WorldRequirements,
)

_PD_DEV_TRACE = os.environ.get("PD_DEV_TRACE") == "true"


def _dt(message: str) -> None:
    """DEV-ONLY structured trace (Phase17E PART B); gated, default OFF.

    Never logs prompts, truth, credentials or the Ollama URL.
    """
    if _PD_DEV_TRACE:
        print(f"[PD-DEV-TRACE] {message}", flush=True)


def _dt_remaining(attempt: Any) -> str:
    budget = getattr(attempt, "budget", None)
    if budget is None or not hasattr(budget, "remaining_seconds"):
        return "none"
    return str(int(budget.remaining_seconds() * 1000))


def _remaining_ms(attempt: Any) -> int | None:
    budget = getattr(attempt, "budget", None)
    if budget is None or not hasattr(budget, "remaining_seconds"):
        return None
    return int(budget.remaining_seconds() * 1000)

# Bounded AssetSpec repair passes INSIDE one AssetSpec round-trip (Phase16_2
# §13: "bounded ≤2 per driver").
MAX_SPEC_REPAIR_PASSES = 2

# The four (non-AssetSpec) stage surfaces the driver walks (in order).
_DRIVER_STAGES = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _consumer_accepts_bucket(consumer: Callable[..., Any]) -> bool:
    """Whether a budget consumer accepts a positional bucket argument.

    Phase 19 Fix C callers pass ``consumer(object_id)`` (ASSET:<objectId>
    attribution); legacy zero-arg consumers (tests, older callers) still work
    via the zero-arg invocation. Introspected once at construction.
    """
    import inspect

    try:
        signature = inspect.signature(consumer)
    except (TypeError, ValueError):
        return True  # flexible callable (partial/builtin): assume bucket-aware
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return True
    return False


def _parse_doc(content: str) -> dict[str, Any]:
    """Strict single-document parse with duplicate-key rejection (mirror of the
    strict generation parser) — returns the dict or raises ``ValueError``."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("provider output is empty")
    return json.loads(content, object_pairs_hook=_reject_duplicate_keys)


# --------------------------------------------------------------------------- #
# CASE/PEOPLE + WORLD_REQUIREMENTS strict parsing (reuses the authoritative
# stage parsers where a section matches one; never a lenient analogue).
# --------------------------------------------------------------------------- #


def parse_case_people(content: str) -> tuple[Any, dict[str, Any]]:
    """Strict-parse a CASE/PEOPLE stage response.

    Returns ``(crime_spec, public_world_spec)`` where ``crime_spec`` is a
    ``CrimeSpec`` and ``public_world_spec`` is a ``PublicWorldSpec`` (built by
    the authoritative PUBLIC_WORLD parser from the persons/motives/locations/
    travelRules/scene sections). NEVER coerces; raises ``ValueError`` carrying
    the parsed structure on success only when the structure is valid.
    """
    data = _parse_doc(content)
    if not isinstance(data, dict):
        raise ValueError("case_people root must be a JSON object")
    extra = set(data) - {"crime", "persons", "motives", "locations", "travelRules", "scene"}
    if extra:
        raise ValueError(f"case_people: unknown keys {sorted(extra)!r}")
    if "crime" not in data:
        raise ValueError("case_people: missing required key 'crime'")
    crime = stage_parser.parse_stage(
        GenerationStage.CASE_TRUTH,
        json.dumps({"crime": data["crime"]}, sort_keys=True),
        non_throwing=False,
    )
    public_payload = {key: val for key, val in data.items() if key in (
        "persons", "motives", "locations", "travelRules", "scene"
    )}
    public_payload["objects"] = public_payload.get("objects", [])
    public = stage_parser.parse_stage(
        GenerationStage.PUBLIC_WORLD,
        json.dumps(public_payload, sort_keys=True),
        non_throwing=False,
    )
    return crime, public


# --------------------------------------------------------------------------- #
# cross-stage locked identity sheet (Phase17D integration)
# --------------------------------------------------------------------------- #
#
# Hermes invents a fresh opaque id namespace in every independent stage call,
# which makes the DETERMINISTIC engine's cross-stage referential integrity
# impossible (crime.weaponId must resolve to an object, evidence person/location
# ids must exist, world evidenceIds must be known evidence ids). The driver
# projects ONE app-owned ID NAMESPACE derived from the LOCKED prompt into every
# stage prompt, so every stage references the same tokens
# (``anna_weiss``, ``paul_becker``, ``bronze_ceremonial_ice_pick``, ``office``,
# ...) by construction. Prompts only; parsers/validators are untouched.

_HONORIFICS: frozenset[str] = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "sir", "madam", "her", "his"}
)


def _identity_slug(value: str | None) -> str:
    """Deterministic lowercase snake id from a locked display value.

    ASCII letters/digits only (mirrors ``normalize_identity``), honorifics
    dropped, words joined with ``_``. ``"Dr. Anna Weiss"`` ->
    ``"anna_weiss"``.
    """
    words = [
        word
        for word in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if word not in _HONORIFICS
    ]
    return "_".join(words)


def _locked_id_sheet(attempt: Any) -> str:
    """The app-owned cross-stage id sheet (empty when nothing is locked)."""
    locked = getattr(attempt, "locked", None)
    if locked is None:
        return ""
    fields = {
        key: value for key, value in locked.locked_fields() if value is not None
    }
    mapping = (
        ("victim", "victim_id"),
        ("murderer", "murderer_id"),
        ("motive", "motive_id"),
        ("weapon", "weapon_id"),
        ("location", "location_id"),
        ("witness", "witness_id"),
    )
    lines = [
        "Locked identity sheet — use THESE exact id tokens for the locked "
        "people/objects in your JSON (never invent alternative ids):"
    ]
    any_token = False
    for key, id_name in mapping:
        value = fields.get(key)
        if value is None:
            continue
        lines.append(f"- {id_name}: {_identity_slug(value)}")
        any_token = True
    time_value = fields.get("crime_time")
    if time_value and any_token:
        lines.append(
            f"- crime_time: the locked time is {time_value} — write "
            f"crimeTime.canonical EXACTLY as {_today_date()}T{time_value}:00+02:00 "
            "(today's date at the locked time, timezone +02:00)"
        )
    return "\n".join(lines) if any_token else ""


def _today_date() -> str:
    """Deterministic ISO date of the run day (``YYYY-MM-DD``)."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")


def _approved_public_material(public: Any) -> str:
    """The approved public-id sheet from the PARSED CASE output (stage 2..N).

    The evidence stage must reference the SAME person/location/motive ids the
    CASE stage produced (a fresh id namespace per stage would break the
    deterministic engine's referential integrity). The driver passes the
    parsed public people/locations/motives forward as an approved allowlist,
    PLUS the approved travel rules (the who solver can only exclude a suspect
    through travel-rule-backed opportunity observations — a missing rule
    makes an observation useless): sanitized public draft material (id +
    name + role), never truth, never ids from hidden sections. Returns an
    empty string when nothing parsed.
    """
    lines = [
        "APPROVED PUBLIC MATERIAL (reference ONLY these ids in your "
        "propositions — never invent ids):"
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


def _approved_travel_sheet(public: Any) -> str:
    """The focused APPROVED TRAVEL RULES block for the evidence prompt.

    The who solver's impossible-opportunity exclusion requires a public
    travel rule FROM the observation location TO the scene; a fact is useless
    without the rule. This projection forwards every parsed travel rule so the
    model can pick a genuinely distant location and the exclusion algebra
    works. Empty when nothing parsed.
    """
    rules = [
        (getattr(r, "from_location_id"), getattr(r, "to_location_id"), getattr(r, "travel_time_seconds"))
        for r in (getattr(public, "travel_rules", ()) or ())
        if getattr(r, "from_location_id") and getattr(r, "to_location_id")
    ]
    if not rules:
        return ""
    lines = ["APPROVED TRAVEL RULES (the travel times between locations):"]
    for frm, to, seconds in rules:
        lines.append(f"- {frm} -> {to}: {seconds} seconds")
    return "\n".join(lines)


def _weapon_evidence_id(attempt: Any, evidence_spec: Any) -> str:
    """The sealed evidence item id that references the LOCKED WEAPON object.

    The world stage can then bind the weapon placement's ``evidenceId`` to a
    REAL evidence id (data-level clickability: clicking the pick opens the
    weapon evidence). Empty when no evidence references the weapon.
    """
    locked = getattr(attempt, "locked", None)
    weapon = getattr(locked, "weapon", None) if locked is not None else None
    if not isinstance(weapon, str) or not weapon:
        return ""
    from app.generation.constraints import normalize_identity

    needle = normalize_identity(weapon)
    for item in getattr(evidence_spec, "evidence", ()) or ():
        item_id = getattr(item, "id", None)
        for prop in getattr(item, "propositions", ()) or ():
            object_id = getattr(prop, "object_id", None)
            if isinstance(object_id, str) and normalize_identity(object_id) == needle:
                return str(item_id) if item_id else ""
    return ""


# --------------------------------------------------------------------------- #
# Phase17 Wave-2 — evidence algebra seeding + deterministic gap completion
# --------------------------------------------------------------------------- #
#
# The WHO/WHY/WEAPON solvers need a COMPLETE fact algebra before any dimension
# can be unique (a missing fact leaves a candidate viable and blocks the case).
# An 8B-class model cannot be relied on to emit all ~16 solver-critical
# propositions in one pass, so the driver does two things:
#
#  1. ``_deduction_seed`` — projects the EXACT ids/timestamps the evidence
#     facts must reference into the evidence prompt (app-owned; derived from
#     the model's own parsed people/motives/locations + the locked ids + the
#     locked canonical time). Facts, never conclusions; no reveal.
#
#  2. ``_evidence_gap_facts`` — the deterministic acceptance path: after the
#     model's evidence is parsed, the driver checks the required algebra and
#     appends ONLY the missing solver-critical facts, derived exclusively from
#     the model's own public tokens (persons/locations/motives/objects it
#     chose), the locked ids and the locked canonical time. The appended facts
#     are REAL discoverable evidence that still passes the strict parser, the
#     full referential validation and the deterministic solver (which derives
#     the unique winner from the assembled set). Nothing hidden, never faked,
#     fully solver-validated.

# The app-owned "other sharp weapon" candidates the weapon solver tests
# against the locked weapon (mirror of the kit base objects in ``_BASE``).
_BASE_SHARP_WEAPON_IDS: tuple[str, ...] = ("kitchen_knife", "letter_opener", "scissors")

# The FULL golden base object id set carried by EVERY driver-assembled draft
# (identical to the fake/live golden world's object set). Phase 19 Fix B
# (§4.4): object IDENTITY exists independently of the per-kit PLACEMENT subset
# (KIT_BASE_OBJECT_IDS) — a kit that cannot place letter_opener/scissors still
# has them as world OBJECTS so weapon/evidence references stay valid.
OBJECTS_BASE_ALL_KITS: tuple[str, ...] = (
    "kitchen_knife",
    "letter_opener",
    "scissors",
    "vase_01",
    "apartment_table",
    "apartment_door",
    "apartment_lamp",
    "apartment_laptop",
    "victim_body_placeholder",
)

# Phase 19B ADV-219: the spare sharp objects whose PLACEMENT is per-kit
# optional (composer ``KIT_BASE_OBJECT_IDS`` omits them on hotel_suite /
# warehouse). They remain WORLD identity objects everywhere so forensic/
# evidence references stay valid, but they are only WEAPON CANDIDATES on kits
# that actually place them (the candidate universe mirrors the placed scene).
_PER_KIT_SHARP_WEAPON_IDS: tuple[str, ...] = ("letter_opener", "scissors")

# Default distant-location travel rule the driver may deterministically add so
# an alternative suspect's observation actually proves the scene unreachable.
_DEFAULT_DISTANT_TRAVEL_SECONDS = 1200


def _evidence_timeline(canonical: str) -> dict[str, str]:
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


def _deduction_seed(attempt: Any, crime: Any, public: Any) -> str:
    """Concrete MUST-EMIT fact seed for the evidence prompt (app-owned).

    Lists the EXACT ids and timestamps the evidence facts should reference:
    the model's OWN person/motive/location tokens (parsed from its case
    output) plus the locked ids and locked canonical time. Instructs FACTS
    only, never conclusions, never the hidden answer. Empty when the parsed
    case cannot seed (the model then works from the generic contract).
    """
    from app.domain.eligibility import SUSPECT_ELIGIBLE
    from app.generation.constraints import normalize_identity

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
        timeline = _evidence_timeline(canonical)
    except (TypeError, ValueError):
        return ""
    other_suspects = sorted(
        p.person_id
        for p in (getattr(public, "persons", ()) or ())
        if SUSPECT_ELIGIBLE in (getattr(p, "affordances", ()) or ())
        and p.person_id != murderer_id
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
    travel = _approved_travel_sheet(public)
    lines = [
        "DEDUCTION SEED (use THESE EXACT ids and timestamps — emit FACTS for "
        "them, never conclusions):",
        f"- locked person (place AT the scene around the crime time): {murderer_id}",
        f"- scene location id: {scene_id}; victim id: {victim_id}",
        f"- locked motive id: {motive_id}; locked weapon id: {weapon_id}",
        (
            f"- exclude these motives (MOTIVE_FACT_CONTRADICTED): "
            f"{', '.join(other_motives) if other_motives else '(none yet — the case needs at least two alt motives)'}"
        ),
        (
            f"- observe these other suspects at DISTANT locations (impossible "
            f"opportunity, PERSON_OBSERVED_AT_LOCATION): "
            f"{', '.join(other_suspects) if other_suspects else '(none yet — the case needs at least two alt suspects)'}"
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


def _evidence_gap_facts(
    attempt: Any, crime: Any, public: Any, evidence_spec: Any
) -> tuple[Any, tuple[Any, ...], tuple[str, ...]]:
    """The deterministic evidence projection (Phase17 Wave-2 acceptance path).

    Hermes3:8b cannot be relied on to emit the complete solver-critical fact
    algebra in one stable pass (its fact subset/timestamps vary run to run;
    a single contradictory proposition — a ``match:true`` on an alternative
    weapon, a false match on the locked weapon, an observation of the locked
    person at a distant travel-rule-backed location, an off-seed time fact —
    is enough to make the assembled evidence non-unique). The driver is the
    sole trust boundary, so under the accepted Wave-2 contract the model emits
    the CASE SKELETON and the driver OWNS the complete evidence algebra:

    - the published evidence set is built deterministically HERE from the
      model's OWN public tokens (the persons/motives/locations/objects it
      chose), the locked ids and the locked canonical time;
    - the model's raw evidence propositions are NOT copied into the published
      draft (nothing the model emits can silently weaken or poison the
      deduction); the evidence stage still runs, parses and validates through
      the same strict parser, and its prompts/projections are the shaping
      surface;
    - every generated fact is REAL discoverable evidence that passes the
      strict parser, the full referential validation AND the deterministic
      solver — the solver still derives the unique winner from the assembled
      set (injection never bypasses the solver, never fakes: every fact
      references model-chosen people/locations/motives/objects).

    Returns ``(canonical_evidence_spec, extra_travel_rules, notes)``:

    - ``canonical_evidence_spec`` — the complete EvidenceSetSpec (WHEN;
      impossible-opportunity for every other suspect; presence + alibi for
      the locked person; motive link + exclusions; weapon matches +
      fingerprint);
    - ``extra_travel_rules`` — ``TravelRuleSpec`` objects the driver appends
      to the draft public so an opportunity observation is travel-rule-backed
      (the who solver refuses to exclude without a rule);
    - ``notes`` — sanitized audit lines (evidence id + fact kind) listing
      exactly what was built and why (the operator/QA trace; never served).
    """
    from app.domain.eligibility import SUSPECT_ELIGIBLE
    from app.generation.constraints import normalize_identity
    from app.generation.schemas import EvidenceSetSpec, PropSpec, TravelRuleSpec

    if crime is None or public is None:
        return None, (), ()
    scene_id = getattr(getattr(public, "scene", None), "location_id", None) or crime.location_id
    murderer_id = crime.murderer_id
    motive_id = crime.motive_id
    weapon_id = crime.weapon_id
    victim_id = crime.victim_id
    canonical = getattr(getattr(crime, "crime_time", None), "canonical", None)
    if not (scene_id and murderer_id and motive_id and weapon_id and victim_id and canonical):
        return None, (), ()
    try:
        timeline = _evidence_timeline(canonical)
    except (TypeError, ValueError):
        return None, (), ()

    persons = {
        p.person_id: getattr(p, "name", None)
        for p in (getattr(public, "persons", ()) or ())
    }
    other_suspects = sorted(
        p.person_id
        for p in (getattr(public, "persons", ()) or ())
        if SUSPECT_ELIGIBLE in (getattr(p, "affordances", ()) or ())
        and p.person_id != murderer_id
    )
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

    def _p(eid: str, title: str, description: str) -> dict[str, str]:
        return {"title": title, "description": description}

    def _person_label(person_id: str) -> str:
        return persons.get(person_id) or person_id

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
                propositions=(_canonical_prop(ptype, location_id=scene_id, observed_at=observed_at, uncertainty_seconds=uncertainty, **kwargs),),
                title=title,
                description=description,
            )
        )
        notes.append(f"{eid}: canonical {ptype} ({observed_at})")

    # ---- impossible-opportunity for every OTHER suspect --------------------
    travel_notes: list[str] = []
    for suspect in other_suspects:
        distant = _canonical_distant_location(travel_lookup, non_scene_locations, scene_id)
        if distant is None:
            continue
        if (distant, scene_id) not in travel_lookup:
            extra_rules.append(
                TravelRuleSpec(
                    from_location_id=distant,
                    to_location_id=scene_id,
                    travel_time_seconds=_DEFAULT_DISTANT_TRAVEL_SECONDS,
                )
            )
            travel_lookup[(distant, scene_id)] = _DEFAULT_DISTANT_TRAVEL_SECONDS
            travel_notes.append(
                f"travel rule {distant}->{scene_id}={_DEFAULT_DISTANT_TRAVEL_SECONDS}s added"
            )
        eid = f"d_ev_opp_{normalize_identity(suspect) or 'suspect'}"
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
        notes.append(f"{eid}: canonical PERSON_OBSERVED_AT_LOCATION for {suspect} (opportunity exclusion)")

    # ---- presence + alibi for the LOCKED person ----------------------------
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

    # ---- motive link + exclusions ------------------------------------------
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
    notes.append(f"d_ev_motive_link: canonical MOTIVE_LINKED_TO_PERSON {murderer_id}->{motive_id}")
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
    for obj in _BASE_SHARP_WEAPON_IDS:
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
    notes.append(f"d_ev_fp: canonical OBJECT_CONTAINS_FINGERPRINT on {weapon_id} for {murderer_id}")

    if travel_notes:
        notes.append("; ".join(travel_notes))
    notes.append(
        "the published evidence is the deterministic canonical algebra built "
        "from the model's case skeleton (model evidence propositions are not "
        "copied into the draft)"
    )
    return EvidenceSetSpec(evidence=tuple(extras)), tuple(extra_rules), tuple(notes)


def _canonical_prop(ptype: str, **kwargs: Any) -> Any:
    """One deterministic canonical proposition (PropSpec)."""
    from app.generation.schemas import PropSpec

    return PropSpec(type=ptype, **{k: v for k, v in kwargs.items() if v is not None})


def _canonical_evidence_item(
    eid: str, kind: str, propositions: tuple[Any, ...], title: str, description: str
) -> Any:
    """One deterministic canonical evidence item (EvidenceSpec)."""
    from app.generation.schemas import EvidenceSpec

    seen: set[str] = set()
    unique: list[Any] = []
    for prop in propositions:
        key = repr(tuple(sorted(getattr(prop, "structured", {}).items()))) + str(getattr(prop, "type", ""))
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
        if seconds is not None and seconds >= 180:
            return loc
    return non_scene_locations[0] if non_scene_locations else None


def _evidence_set_summary(evidence_spec: Any) -> dict[str, Any]:
    """Deterministic sanitized evidence summary for operator diagnostics.

    Counts per proposition type PLUS per-object forensics match values PLUS
    the evidence-item count — structure only, never content, never ids."
    """
    counts: dict[str, int] = {}
    match_vals: dict[str, list[bool]] = {}
    item_count = 0
    for item in getattr(evidence_spec, "evidence", ()) or ():
        item_count += 1
        if not getattr(item, "discoverable", True):
            continue
        for prop in getattr(item, "propositions", ()) or ():
            ptype = getattr(prop, "type", "")
            counts[ptype] = counts.get(ptype, 0) + 1
            if ptype == "FORENSIC_WEAPON_MATCH":
                obj = getattr(prop, "object_id", None)
                match = getattr(prop, "structured", {}) if hasattr(prop, "structured") else {}
                value = match.get("match") if isinstance(match, Mapping) else None
                if isinstance(value, bool) and isinstance(obj, str) and obj:
                    match_vals.setdefault(obj, []).append(value)
    return {
        "evidenceItems": item_count,
        "propositionTypeCounts": dict(sorted(counts.items())),
        "forensicMatchValues": {k: v for k, v in sorted(match_vals.items())},
    }


def parse_world_requirements(
    content: str,
    canonical_fallback: str | None = None,
    *,
    case_id: str | None = None,
    attempt_id: str | None = None,
) -> WorldRequirements:
    """Strict-parse a WORLD_REQUIREMENTS stage response into the typed
    ``WorldRequirements`` (bounded ObjectRequests / PlacementRelations).

    Phase 19 Fix A — canonical environment handling: the raw
    ``environmentHint`` is canonicalized through ``app.world.environment``
    (trim/lowercase/space->underscore/hyphen->underscore + the closed five-id
    alias map). A REJECTED hint (path-like / ``..`` / absolute / URL scheme /
    unsupported token) NEVER fails the stage and NEVER enters the remote-repair
    path: the deterministic user/prompt-derived fallback
    (``canonical_fallback``, e.g. "hotel_suite" from the prompt's "Location:
    hotel suite" line) is used when available, otherwise the hint stays None
    (the environment resolver falls back to the documented default kit). All
    of this is LOCAL — zero provider calls, zero budget consumption. Only
    genuine semantic content gaps (e.g. a malformed object/relation structure)
    raise and route through the bounded retry.
    """
    data = _parse_doc(content)
    if not isinstance(data, dict):
        raise ValueError("world_requirements root must be a JSON object")
    objects: list[ObjectRequest] = []
    for index, item in enumerate(data.get("objects") or ()):
        if not isinstance(item, dict):
            raise ValueError(f"world_requirements.objects[{index}] must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"world_requirements.objects[{index}]: name required")
        criticality = item.get("criticality")
        if criticality not in (CRITICALITY_REQUIRED, CRITICALITY_DECORATIVE):
            criticality = CRITICALITY_DECORATIVE
        try:
            objects.append(
                ObjectRequest(
                    requested_name=name,
                    category_hint=item.get("categoryHint"),
                    subtype_hint=item.get("subtypeHint"),
                    tags=tuple(item.get("tags") or ()),
                    required_interaction=item.get("requiredInteraction"),
                    evidence_id=item.get("evidenceId"),
                    criticality=criticality,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"world_requirements.objects[{index}] invalid: {exc}"
            ) from None

    relations: list[PlacementRelation] = []
    for index, item in enumerate(data.get("relations") or ()):
        if not isinstance(item, dict):
            raise ValueError(f"world_requirements.relations[{index}] must be an object")
        kind = item.get("kind")
        if kind not in RELATION_KINDS:
            raise ValueError(
                f"world_requirements.relations[{index}]: kind {kind!r} not in "
                f"{list(RELATION_KINDS)!r}"
            )
        try:
            relations.append(PlacementRelation(kind=kind, target=item.get("target") or ""))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"world_requirements.relations[{index}] invalid: {exc}"
            ) from None

    # ---- Phase 19 Fix A: deterministic LOCAL environmentHint handling ------
    # Canonicalize the raw hint. A rejected value is repaired locally (prompt
    # fallback / default kit) and NEVER triggers a remote repair provider call.
    raw_hint = data.get("environmentHint")
    environment_hint, env_issues = canonicalize_environment_hint(raw_hint)
    if environment_hint is None and raw_hint is not None and env_issues:
        # Deterministic local recovery: the already-known canonical location
        # extracted from the USER PROMPT is authoritative when available.
        if canonical_fallback is not None:
            fallback_hint, _fallback_issues = canonicalize_environment_hint(
                canonical_fallback
            )
            if fallback_hint is not None:
                environment_hint = fallback_hint
                emit_event(
                    "environment.fallback.used",
                    caseId=case_id,
                    generationAttemptId=attempt_id,
                    stage=GenerationStage.WORLD_GRAPH.value,
                    environmentId=fallback_hint,
                    reasonCode="ENVIRONMENT_HINT_REJECTED",
                    fallbackSource="USER_LOCATION",
                )
        if environment_hint is None:
            # No authoritative recovery is available: the hint stays None and
            # the environment resolver falls back to the default kit. Local,
            # zero provider calls.
            emit_event(
                "environment.fallback.used",
                caseId=case_id,
                generationAttemptId=attempt_id,
                stage=GenerationStage.WORLD_GRAPH.value,
                environmentId="",
                reasonCode="ENVIRONMENT_HINT_REJECTED",
                fallbackSource="DEFAULT_KIT",
            )
    elif raw_hint is not None and environment_hint != raw_hint:
        emit_event(
            "environment.canonicalized",
            caseId=case_id,
            generationAttemptId=attempt_id,
            stage=GenerationStage.WORLD_GRAPH.value,
            environmentId=environment_hint or "",
            reasonCode="ENVIRONMENT_HINT_CANONICALIZED",
        )

    try:
        return WorldRequirements(
            environment_hint=environment_hint,
            location_tokens=tuple(data.get("locationTokens") or ()),
            objects=tuple(objects),
            relations=tuple(sorted(set(relations), key=lambda r: (r.kind, r.target))),
            unsafe_unsupported=tuple(data.get("unsafeUnsupported") or ()),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"world_requirements invalid: {exc}") from None


# --------------------------------------------------------------------------- #
# AssetSpec adapter (implements app.assets.spec_provider.AssetSpecProvider)
# --------------------------------------------------------------------------- #


class OllamaAssetSpecProvider:
    """Bridges the world composer's narrow spec-provider to the Ollama adapter.

    One ASSET_SPEC call + at most ``MAX_SPEC_REPAIR_PASSES`` ASSET_SPEC_REPAIR
    calls. Every response is strictly Phase-13 parsed and validated BEFORE the
    Phase 17 geometry-quality validator runs (``schema-valid !=
    geometrically-valid``); each call consumes the per-attempt provider-call
    budget via a provided ``budget_consumer`` (returns True when a call is
    available).

    Phase 17 flow (Deterministic Geometry Quality Gate — Phase17 §2/§10):

        LLM AssetSpec
          -> strict Phase 13 parse/validation
          -> Geometry Quality Validator (app.assets.geometry_quality)
          -> PASS  -> raw candidate is returned for the trusted compiler
             FAIL  -> structured sanitized geometry diagnostics ->
                      ASSET_SPEC_REPAIR (bounded) -> re-run BOTH validations

    A repair result is NEVER trusted incrementally: the full Phase 13 + Phase 17
    validation run again before any candidate is accepted. The budget stays the
    existing ``MAX_SPEC_REPAIR_PASSES`` (<=2); the global per-attempt provider
    and model-call budgets are authoritative (this provider consumes them via
    ``budget_consumer`` inside ``_roundtrip``).

    Internal (non-API, non-player-facing) trace:

    - ``last_geometry_report`` — the final ``GeometryReport`` (None when the
      round-trip never reached the geometry stage with a parsed spec);
    - ``last_geometry_metrics`` — the sanitized metrics dict (see
      ``_record_geometry_metrics``): issueCountBeforeRepair, repairAttempts,
      finalPartCount, finalBoundingBox, declaredDimensions,
      silhouettePassed, generatedOnFirstPass / repaired;
    - ``last_repair_trace`` — the sanitized per-pass diagnostics list (one
      entry per validation pass, order preserved): every entry carries the
      Phase 13 structural issue strings and the Phase 17 geometry issues
      (code/classification/message/partId) for THAT pass. Never serialized,
      never served; read by the smoke CLI to report "each repair attempt's
      issues".
    - ``request_calls`` — the true request-level provider-call count for the
      last round-trip (initial + repairs); the smoke CLI reports this as
      ``providerCalls`` (Phase17C §8).
    """

    def __init__(
        self,
        *,
        provider: Any,
        attempt_id: str,
        budget_consumer: Callable[..., bool],
        locked: Any = None,
        seed: int | None = None,
        model_label: str = "",
        configured_timeout_seconds: float | None = None,
        timeout_provider: Callable[[], float] | None = None,
        metrics_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._provider = provider
        self._attempt_id = attempt_id
        self._budget_consumer = budget_consumer
        self._locked = locked
        self._seed = seed
        self._model_label = model_label
        self._configured_timeout_seconds = configured_timeout_seconds
        self._timeout_provider = timeout_provider
        self._metrics_provider = metrics_provider
        # Phase 19 Fix C: the budget consumer may be bucket-aware
        # (``consumer(object_id)`` -> ASSET:<objectId> accounting) or a legacy
        # zero-arg consumer (tests/older callers) — introspected once here.
        self._bucket_aware = _consumer_accepts_bucket(budget_consumer)
        # Phase 19 Fix C ceiling flags: a procedural-asset-count ceiling or a
        # failed-asset ceiling hit is recorded here (the driver reads the flag
        # AFTER composition and raises the specific typed code — the composer
        # itself can never see the raw provider).
        self.procedural_asset_ceiling_hit = False
        self.failed_asset_threshold_hit = False
        # ``calls``: number of ``generate()`` invocations on THIS spec provider
        # (always 1 — the outer AssetSpec round-trip). ``request_calls``: the
        # number of REAL request-level provider calls (each ASSET_SPEC/REPAIR
        # attempt is exactly one ``provider.generate`` transport request) —
        # initial + N repairs = N + 1 (Phase17C §8 accounting). The smoke tool
        # reports ``request_calls`` so provider-consumption accounting matches
        # the true Ollama request count while ``calls`` keeps the outer
        # round-trip semantics.
        self.calls: int = 0
        self.request_calls: int = 0
        # Phase 17 internal trace (never serialized, never served).
        self.last_geometry_report: Any = None
        self.last_geometry_metrics: dict[str, Any] | None = None
        # Phase17B: sanitized per-pass diagnostics trace (repair-path
        # diagnostics for the smoke CLI; never serialized, never served).
        self.last_repair_trace: list[dict[str, Any]] = []

    def generate(self, request: Any) -> Any:
        from app.assets.spec_provider import (
            AssetSpecRequest,
            AssetSpecResponse,
        )
        from app.assets.specs import parse_asset_spec, validate_asset_spec
        from app.assets.geometry_quality import validate_geometry

        request = (
            request if isinstance(request, AssetSpecRequest) else AssetSpecRequest(**dict(request))
        )
        self.calls += 1
        self.last_repair_trace = []
        concept = request.requested_name
        category_hint = request.category_hint or None
        self._last_concept = concept

        # Phase 19 Fix C: record this semantic object's entry into the
        # procedural-asset path (a DISTINCT-object ceiling guard, never a
        # model call). When the ceiling is already reached the object cannot
        # enter the provider path — fail this asset, the driver sees the flag.
        budget = self._metrics_provider() if self._metrics_provider is not None else None
        if budget is not None and hasattr(budget, "consume_procedural_asset"):
            if not budget.consume_procedural_asset(concept):
                self.procedural_asset_ceiling_hit = True
                return AssetSpecResponse(
                    error="procedural asset count ceiling exceeded for this generation"
                )

        # initial ASSET_SPEC call
        prompt = prompts.build_asset_spec_prompt(concept, category_hint)
        try:
            content = self._roundtrip(prompt, GenerationStage.ASSET_SPEC, concept)
        except StageDriverProviderFailure as exc:
            self._record_budget_failure(exc)
            raise
        if content is None:
            return self._fail_asset("asset spec generation unavailable")

        candidate = content
        repair_attempts = 0
        issue_count_before_repair: int | None = None
        final_spec: Any = None
        final_report: Any = None

        for _pass in range(MAX_SPEC_REPAIR_PASSES + 1):
            # ---- 1. Phase 13 schema/security validation FIRST ----------------
            struct_issues = validate_asset_spec(candidate)
            geometry_report = None
            spec = None
            if not struct_issues:
                spec = parse_asset_spec(candidate)
            if spec is not None:
                # ---- 2. Phase 17 geometry-quality gate SECOND ----------------
                geometry_report = validate_geometry(spec, requested_name=concept)

            # Sanitized per-pass diagnostics (order preserved; never raw text).
            trace_entry = self._trace_entry(struct_issues, geometry_report)
            trace_entry["pass"] = _pass
            self.last_repair_trace.append(trace_entry)

            accepted = (
                not struct_issues
                and geometry_report is not None
                and geometry_report.valid
            )
            if accepted:
                if issue_count_before_repair is None:
                    issue_count_before_repair = 0
                final_spec = spec
                final_report = geometry_report
                break
            if issue_count_before_repair is None:
                issue_count_before_repair = len(struct_issues) + (
                    len(geometry_report.issues) if geometry_report is not None else 0
                )
                final_report = geometry_report  # first failing report (trace)
            if _pass >= MAX_SPEC_REPAIR_PASSES:
                break

            # bounded repair: structured sanitized diagnostics into the repair
            # prompt; the repair decides NOTHING about trust.
            diagnostics = self._repair_diagnostics(candidate, struct_issues, geometry_report)
            repair_prompt = prompts.build_asset_spec_repair_prompt(
                concept, candidate, diagnostics
            )
            try:
                content = self._roundtrip(
                    repair_prompt, GenerationStage.ASSET_SPEC_REPAIR, concept
                )
            except StageDriverProviderFailure as exc:
                self._record_budget_failure(exc)
                raise
            if content is None:
                self._record_geometry_metrics(
                    issue_count_before_repair, repair_attempts, final_report
                )
                return self._fail_asset("asset spec repair unavailable")
            repair_attempts += 1
            candidate = content

        if final_spec is None or final_report is None or not final_report.valid:
            self._record_geometry_metrics(
                issue_count_before_repair, repair_attempts, final_report
            )
            return self._fail_asset(
                "asset spec could not be made geometrically valid within "
                "the repair budget"
            )
        self._record_geometry_metrics(
            issue_count_before_repair, repair_attempts, final_report
        )
        return AssetSpecResponse(content=candidate)

    def _record_failed_asset(self, reason_code: str) -> bool:
        """Shared failed-asset accounting + sanitized event.

        Used by BOTH the return path (``_fail_asset``) and the exception path
        (ADV-213 ``_record_budget_failure``) so a per-asset budget exhaustion
        counts toward ``MAX_FAILED_ASSETS_PER_GENERATION`` exactly like any
        other failed procedural asset. Returns True when recording this NEW
        failure exceeded the failed-asset threshold (the caller raises the
        narrowed flag / code).
        """
        concept = getattr(self, "_last_concept", "")
        budget = self._metrics_provider() if self._metrics_provider is not None else None
        exceeded = False
        if budget is not None and hasattr(budget, "mark_failed_asset"):
            exceeded = not budget.mark_failed_asset(concept)
        if exceeded:
            self.failed_asset_threshold_hit = True
        emit_event(
            "generation.asset.failed",
            generationAttemptId=self._attempt_id,
            semanticObjectId=concept,
            reasonCode=reason_code,
            failedAssetCount=(
                budget.failed_asset_count
                if budget is not None and hasattr(budget, "failed_asset_count")
                else None
            ),
            assetCallCount=(
                budget.asset_call_count(concept)
                if budget is not None and hasattr(budget, "asset_call_count")
                else None
            ),
        )
        return exceeded

    def _record_budget_failure(self, exc: StageDriverProviderFailure) -> None:
        """ADV-213/ADV-220 — classified failed-asset accounting for an
        exception-style budget failure raised inside ``_roundtrip``.

        A PER-ASSET exhaustion is attributable to THIS semantic object: record
        the failed asset (mark_failed_asset + sanitized event) BEFORE the typed
        failure propagates — exactly once per failed object (the tracker's
        set-membership no-op prevents double counting). GLOBAL exhaustion is a
        terminal attempt condition never attributed to one object (no
        accounting). The typed failure then continues to the composer, which
        classifies it BY OBJECT CRITICALITY (ADV-220): a DECORATIVE object
        below the failed-asset ceiling follows the bounded fallback (skip with
        a player-safe note; never a silent partial world), a DECORATIVE object
        at/over the ceiling surfaces the terminal ``MAX_FAILED_ASSETS_EXCEEDED``
        code, and a REQUIRED/essential object keeps the narrow
        ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED code.
        """
        if (
            getattr(exc, "code", None)
            == GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED.value
        ):
            self._record_failed_asset(
                GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED.value
            )

    def _fail_asset(self, message: str) -> Any:
        """Record ONE failed procedural asset + emit the sanitized event.

        ``budget.mark_failed_asset`` tracks DISTINCT failed semantic objects
        against ``MAX_FAILED_ASSETS_PER_GENERATION``; when recording this NEW
        failure would exceed the threshold, the flag is raised so the driver
        fails the attempt with the narrow ``MAX_FAILED_ASSETS_EXCEEDED`` code
        (an essential/evidence asset must never silently disappear). Determin-
        istic local repairs never reach this path — only a failed PROVIDER
        round-trip does.
        """
        from app.assets.spec_provider import AssetSpecResponse

        self._record_failed_asset("ASSET_SPEC_FAILED")
        return AssetSpecResponse(error=message)

    def _repair_diagnostics(
        self, candidate: str, struct_issues: Any, geometry_report: Any
    ) -> tuple[str, ...]:
        """Sanitized structured diagnostics for one repair request.

        Includes the structured INVALID_IDENTIFIER / MATERIAL_NOT_ALLOWED
        diagnostics for the RAW candidate (Phase 13 already validates grammar and
        materials — this deterministic scan surfaces them in repair diagnostics
        too, Phase17 §5/§7) plus the geometry-quality issues when the candidate
        parsed, plus the Phase 13 structural issue strings when it did not.
        """
        from app.assets.geometry_quality import (
            GeometryIssue,
            inspect_raw_spec_issues,
        )

        lines: list[str] = []

        def _render(issue: Any) -> str:
            where = f" part {issue.partId}" if getattr(issue, "partId", None) else ""
            allowed = ""
            if getattr(issue, "allowed", ()):
                allowed = " (allowed: " + ", ".join(issue.allowed) + ")"
            return f"[{issue.classification} {issue.code}]{where}: {issue.message}{allowed}"

        raw_issues = inspect_raw_spec_issues(candidate) or ()
        for issue in raw_issues:
            lines.append(_render(issue))
        if geometry_report is not None:
            for issue in geometry_report.issues:
                lines.append(_render(issue))
        if struct_issues:
            lines.extend(str(issue) for issue in struct_issues)
        # deterministic, de-duplicated
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            if line not in seen:
                seen.add(line)
                out.append(line)
        return tuple(out)

    def _trace_entry(
        self, struct_issues: Any, geometry_report: Any
    ) -> dict[str, Any]:
        """Sanitized per-pass diagnostics (never raw candidate text, never
        player-facing). Structural issue strings are app-owned deterministic
        messages; geometry issues carry only code/classification/message/partId.
        """
        geometry: list[dict[str, Any]] = []
        if geometry_report is not None:
            geometry = [
                {
                    "code": issue.code,
                    "classification": issue.classification,
                    "message": issue.message,
                    "partId": issue.partId,
                }
                for issue in geometry_report.issues
            ]
        return {
            "structuralIssues": [str(issue) for issue in (struct_issues or ())],
            "geometryIssues": geometry,
        }

    def _record_geometry_metrics(
        self,
        issue_count_before_repair: int | None,
        repair_attempts: int,
        final_report: Any,
    ) -> None:
        """Internal per-AssetSpec quality metrics (never player-facing, never
        serialized). Used for QA/showcase evidence and the smoke CLI output."""
        report = final_report
        if report is None:
            self.last_geometry_metrics = {
                "issueCountBeforeRepair": int(issue_count_before_repair or 0),
                "repairAttempts": int(repair_attempts),
                "finalPartCount": 0,
                "finalBoundingBox": None,
                "declaredDimensions": None,
                "silhouettePassed": False,
                "generatedOnFirstPass": False,
                "repaired": False,
            }
            return
        metrics = report.metrics
        bbox = (
            {
                "min": list(metrics.estimated_bounding_box[0]),
                "max": list(metrics.estimated_bounding_box[1]),
                "span": list(metrics.span),
            }
            if metrics.estimated_bounding_box is not None
            else None
        )
        self.last_geometry_report = report
        self.last_geometry_metrics = {
            "issueCountBeforeRepair": int(issue_count_before_repair or 0),
            "repairAttempts": int(repair_attempts),
            "finalPartCount": int(metrics.part_count),
            "finalBoundingBox": bbox,
            "declaredDimensions": list(metrics.declared_dimensions),
            "silhouettePassed": bool(metrics.silhouette_passed),
            "generatedOnFirstPass": bool(issue_count_before_repair == 0),
            "repaired": bool(repair_attempts > 0),
        }

    def _roundtrip(
        self, prompt: str, stage: GenerationStage, concept: str
    ) -> str | None:
        """One bounded budgeted Ollama call; returns raw content or None.

        Every invocation consumes exactly one modeling request: consumed the
        per-attempt provider-call budget (CORE or ASSET:<objectId> bucket —
        Phase 19 Fix C) AND counted on ``self.request_calls`` (Phase17C §8 — a
        repair attempt is a REAL request-level provider call, never a free
        local reprocessing). A per-asset or global exhaustion surfaces the
        narrowest failure code (ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED vs
        PROVIDER_CALL_BUDGET_EXHAUSTED).
        """
        effective_timeout = self._effective_timeout()
        if effective_timeout <= 0:
            raise StageDriverProviderFailure(
                "generation deadline exceeded",
                code=GenerationFailureCode.GENERATION_DEADLINE_EXCEEDED,
            )
        if self._bucket_aware:
            available = bool(self._budget_consumer(concept))
        else:
            available = bool(self._budget_consumer())
        if not available:
            reason = "model call budget exhausted"
            budget = self._metrics_provider() if self._metrics_provider is not None else None
            if budget is not None and hasattr(budget, "exhausted_reason"):
                reason = budget.exhausted_reason(concept) or reason
            if "asset model call budget" in (reason or ""):
                emit_event(
                    "provider.asset_budget.exhausted",
                    generationAttemptId=self._attempt_id,
                    semanticObjectId=concept,
                    reasonCode="ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED",
                    assetCallCount=(
                        budget.asset_call_count(concept)
                        if budget is not None and hasattr(budget, "asset_call_count")
                        else None
                    ),
                    remainingGlobalCalls=(
                        budget.remaining_global_calls()
                        if budget is not None and hasattr(budget, "remaining_global_calls")
                        else None
                    ),
                )
            raise StageDriverProviderFailure(
                reason,
                code=failure_code_for_budget_reason(reason),
            )
        self.request_calls += 1
        request = GenerateRequest(
            attempt_id=self._attempt_id,
            stage=stage,
            prompt_context=prompt,
            locked=self._locked,
            seed=self._seed,
            timeout_seconds=effective_timeout,
        )
        budget = self._metrics_provider() if self._metrics_provider is not None else None
        configured_timeout_ms = (
            int(float(self._configured_timeout_seconds) * 1000)
            if self._configured_timeout_seconds is not None else None
        )
        emit_event(
            "provider.call.start",
            generationAttemptId=self._attempt_id,
            stage=stage.value,
            provider="ollama",
            model=self._model_label or None,
            configuredProviderTimeoutMs=configured_timeout_ms,
            effectiveProviderTimeoutMs=int(effective_timeout * 1000),
            deadlineRemainingMs=(
                int(budget.remaining_seconds() * 1000)
                if budget is not None and hasattr(budget, "remaining_seconds") else None
            ),
            providerCallCount=getattr(budget, "calls", None),
            repairCount=getattr(budget, "repair_passes", None),
            regenerationCount=getattr(budget, "regenerations", None),
            requestBytes=len(prompt.encode("utf-8")),
        )
        _t0 = time.perf_counter()
        result = self._provider.generate(request)
        if _PD_DEV_TRACE:
            if result.content is not None:
                summary = "ok(content)"
            elif result.timed_out:
                summary = "FAILED(timed_out)"
            else:
                summary = "FAILED(error=%s)" % (result.error or "unknown")
            _dt(
                "assetspec.call.done stage=%s %s elapsedMs=%d requestCalls=%d"
                % (
                    stage.value,
                    summary,
                    int((time.perf_counter() - _t0) * 1000),
                    self.request_calls,
                )
            )
        if result.timed_out:
            emit_event(
                "provider.call.timeout",
                generationAttemptId=self._attempt_id,
                stage=stage.value,
                provider="ollama",
                failureCode=GenerationFailureCode.PROVIDER_TIMEOUT.value,
                elapsedMs=int((time.perf_counter() - _t0) * 1000),
                configuredProviderTimeoutMs=configured_timeout_ms,
                effectiveProviderTimeoutMs=int(effective_timeout * 1000),
                deadlineRemainingMs=(
                    int(budget.remaining_seconds() * 1000)
                    if budget is not None and hasattr(budget, "remaining_seconds") else None
                ),
            )
            raise StageDriverProviderFailure(
                "provider request timed out",
                code=GenerationFailureCode.PROVIDER_TIMEOUT,
            )
        if result.error is not None:
            code = infer_failure_code(result.error)
            emit_event(
                "provider.call.error",
                generationAttemptId=self._attempt_id,
                stage=stage.value,
                provider="ollama",
                failureCode=code.value,
                elapsedMs=int((time.perf_counter() - _t0) * 1000),
                configuredProviderTimeoutMs=configured_timeout_ms,
                effectiveProviderTimeoutMs=int(effective_timeout * 1000),
                deadlineRemainingMs=(
                    int(budget.remaining_seconds() * 1000)
                    if budget is not None and hasattr(budget, "remaining_seconds") else None
                ),
            )
            raise StageDriverProviderFailure(
                "provider returned an unusable response",
                code=infer_failure_code(result.error),
            )
        if result.content is None:
            emit_event(
                "provider.call.error",
                generationAttemptId=self._attempt_id,
                stage=stage.value,
                provider="ollama",
                failureCode=GenerationFailureCode.PROVIDER_INVALID_RESPONSE.value,
                elapsedMs=int((time.perf_counter() - _t0) * 1000),
                configuredProviderTimeoutMs=configured_timeout_ms,
                effectiveProviderTimeoutMs=int(effective_timeout * 1000),
                deadlineRemainingMs=(
                    int(budget.remaining_seconds() * 1000)
                    if budget is not None and hasattr(budget, "remaining_seconds") else None
                ),
            )
            raise StageDriverProviderFailure(
                "provider returned an invalid response",
                code=GenerationFailureCode.PROVIDER_INVALID_RESPONSE,
            )
        emit_event(
            "provider.call.complete",
            generationAttemptId=self._attempt_id,
            stage=stage.value,
            provider="ollama",
            model=self._model_label or None,
            success=True,
            elapsedMs=int((time.perf_counter() - _t0) * 1000),
            configuredProviderTimeoutMs=configured_timeout_ms,
            effectiveProviderTimeoutMs=int(effective_timeout * 1000),
            responseBytes=len(result.content.encode("utf-8")),
            structuredOutput=True,
deadlineRemainingMs=(
                int(budget.remaining_seconds() * 1000)
                if budget is not None and hasattr(budget, "remaining_seconds") else None
            ),
            providerCallCount=getattr(budget, "calls", None),
            repairCount=getattr(budget, "repair_passes", None),
            regenerationCount=getattr(budget, "regenerations", None),
        )
        return result.content

    def _effective_timeout(self) -> float:
        if self._timeout_provider is None:
            return max(0.0, float(self._configured_timeout_seconds or 60.0))
        return max(0.0, float(self._timeout_provider()))


# --------------------------------------------------------------------------- #
# ADV-218 — sanitized semantic-object-id rendering for internal validation
# messages. Ids are only length-bounded upstream (``_str_field``), so hostile
# weaponId / evidence object_id values (URLs, control characters) are scrubbed
# before they are embedded in exception/log text. Failure-code semantics are
# untouched — only the message text is sanitized and bounded.
# --------------------------------------------------------------------------- #

_SAFE_MESSAGE_ID_CHARS: frozenset[str] = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789_-."
)
# URL-scheme tokens never echoed into an internal validation message.
_FORBIDDEN_URL_TOKENS_IN_MESSAGE: tuple[str, ...] = (
    "http:",
    "https:",
    "ftp:",
    "data:",
    "file:",
    "javascript:",
    "//",
)
_MAX_MESSAGE_IDS = 4
_MAX_MESSAGE_ID_LENGTH = 48


def _sanitize_object_id_for_message(
    raw: Any, *, max_len: int = _MAX_MESSAGE_ID_LENGTH
) -> str:
    """ADV-218 — sanitize ONE semantic object id before embedding it in an
    internal validation/log message.

    The sanitizer never echoes control characters or URL-scheme / scheme-slash
    tokens (a hostile URL-bearing id degrades to the deterministic
    ``"<url-suppressed>"`` placeholder), projects the remainder onto the safe
    id alphabet, bounds length, and returns a deterministic placeholder when
    nothing safe survives. The failure-code semantics (VALIDATION_FAILED) are
    unchanged — only the message text is scrubbed.
    """
    if not isinstance(raw, str) or not raw:
        return "<unknown>"
    raw_lowered = raw.casefold()
    if any(token in raw_lowered for token in _FORBIDDEN_URL_TOKENS_IN_MESSAGE):
        return "<url-suppressed>"
    scrubbed: list[str] = []
    for ch in raw:
        codepoint = ord(ch)
        if codepoint < 0x20 or codepoint == 0x7F:
            # control characters are never echoed
            continue
        scrubbed.append(ch if ch in _SAFE_MESSAGE_ID_CHARS else "_")
    text = "".join(scrubbed)
    text = "_".join(part for part in text.split("_") if part)
    if not text:
        return "<unprintable>"
    return text[:max_len]


def _sanitize_object_ids_for_message(missing: Iterable[Any]) -> str:
    """ADV-218 — join the sanitized missing ids (bounded count + length)."""
    rendered = ", ".join(
        _sanitize_object_id_for_message(oid) for oid in missing[:_MAX_MESSAGE_IDS]
    )
    if len(missing) > _MAX_MESSAGE_IDS:
        rendered += ", ..."
    return rendered


# --------------------------------------------------------------------------- #
# the stage driver
# --------------------------------------------------------------------------- #


class OllamaStageDriver:
    """Walks the structured per-stage pipeline and assembles a ``GeneratedDraft``.

    ``provider_factory`` returns a FRESH ``OllamaProvider`` per attempt (the
    same factory the service uses for ollama) so transport state never leaks
    across attempts. ``oracle`` (Asset Oracle), ``spec_provider`` (an
    ``AssetSpecProvider`` for the ASSET_SPEC path — normally an
    ``OllamaAssetSpecProvider``) and ``cache`` are shared.
    """

    def __init__(
        self,
        *,
        settings: Any,
        provider_factory: Callable[[], Any],
        generated_cache: Any = None,
        catalog: Any = None,
        spec_provider: Any = None,
    ) -> None:
        from app.assets.generated_cache import GeneratedAssetCache

        self._settings = settings
        self._provider_factory = provider_factory
        # Fresh bounded generated-asset cache per driver so repeated prompt runs
        # (and tests) stay isolated and deterministic — no cross-run pollution.
        self._generated_cache = generated_cache if generated_cache is not None else GeneratedAssetCache()
        self._catalog = catalog
        self._spec_provider = spec_provider
        # Phase17 Wave-2: parsed CASE/PEOPLE outputs cached per attempt id so a
        # second pass (repair/regeneration) never re-pays the case call — the
        # public world the evidence was regenerated against stays identical.
        self._case_outputs: dict[str, tuple[Any, Any]] = {}
        # Phase17 Wave-2: audit trace of the deterministic evidence completion
        # (sanitized "id: injected fact kind" lines; never serialized/served —
        # read by the smoke CLI and operator tests).
        self.last_evidence_injections: tuple[str, ...] = ()
        self.last_evidence_completed: bool = False
        # Phase17 Wave-2: deterministic per-pass evidence summary (fact-type
        # counts + match-value counts ONLY — never raw content, never the
        # prompt); read by the smoke CLI/operator tests.
        self.last_evidence_summary: dict[str, Any] = {}

    # -- public driver entry points (controller lifecycle) -------------------

    def run_into(self, attempt: Any, diagnostics: tuple[str, ...] = ()) -> None:
        """Fresh (or repair) staged run; sets ``attempt.draft`` on success.

        Provider-level failures raise ``StageDriverProviderFailure`` (the
        controller fails the attempt). Parse/validation failures leave
        ``attempt.deferred_structural`` populated and ``attempt.draft`` set to
        whatever assembled, so ``pipeline.validate_draft`` classifies them via
        the EXISTING controller.
        """
        from app.generation import pipeline

        _t0 = time.perf_counter()
        if _PD_DEV_TRACE:
            _dt(
                "driver.run_into.start repairDiagnostics=%d deadlineRemainingMs=%s"
                % (len(diagnostics), _dt_remaining(attempt))
            )
        deferred: list[str] = []
        provider = self._provider_factory()
        budget_consumer = self._make_budget_consumer(attempt)

        locked_map = self._locked_map(attempt)
        id_sheet = _locked_id_sheet(attempt)

        # --- 1. CASE/PEOPLE (cached across repair/regeneration passes) ------
        cached_case = self._case_outputs.get(attempt.attempt_id)
        if cached_case is not None:
            crime, public = cached_case
        else:
            case_prompt = prompts.build_case_people_prompt(
                attempt.prompt, locked_map, id_sheet=id_sheet
            )
            parsed_case = self._stage_parse(
                provider,
                attempt,
                GenerationStage.CASE_TRUTH,
                case_prompt,
                budget_consumer,
                parse_case_people,
                "case_people",
                deferred,
            )
            crime, public = (parsed_case or (None, None))
            if crime is not None and public is not None:
                # Only a VALID parsed case is cached (a broken first pass must
                # be re-tried); the cache makes regeneration free of the case
                # call so the 8-call budget has room for the evidence loop.
                self._case_outputs[attempt.attempt_id] = (crime, public)

        # --- 2. EVIDENCE ----------------------------------------------------
        approved_material = _approved_public_material(public)
        deduction_seed = _deduction_seed(attempt, crime, public)
        is_rerun = cached_case is not None
        deduction_feedback = self._evidence_feedback(attempt, diagnostics, is_rerun)
        evidence_prompt = prompts.build_evidence_prompt(
            attempt.prompt,
            locked_map,
            id_sheet=id_sheet,
            approved_people=approved_material,
            deduction_seed=deduction_seed,
            deduction_feedback=deduction_feedback,
        )
        evidence_spec = self._stage_parse(
            provider,
            attempt,
            GenerationStage.EVIDENCE,
            evidence_prompt,
            budget_consumer,
            lambda content: stage_parser.parse_stage(
                GenerationStage.EVIDENCE, content, non_throwing=False
            ),
            "evidence",
            deferred,
            retry_on_parse_failure=False,
        )

        # --- 2b. deterministic evidence completion (Phase17 Wave-2) ----------
        # app-owned projection: policy-sanitizes contradictory model facts and
        # closes ONLY the solver-critical gaps; facts reference the model's
        # own public tokens + locked ids + locked canonical time; still fully
        # solver-validated. Never hides, never fakes, never bypasses the
        # solver.
        projection_started = time.perf_counter()
        completed_spec, extra_rules, injected = _evidence_gap_facts(
            attempt, crime, public, evidence_spec
        )
        if evidence_spec is None:
            # The rejected raw evidence is never retained in ``deferred`` and
            # never enters the draft.  The existing driver-owned projection is
            # the local recovery path; normal draft/solver validation below
            # remains the authority for whether it can publish.
            emit_event(
                "evidence.local_projection.used",
                generationAttemptId=getattr(attempt, "attempt_id", None),
                reasonCode=GenerationFailureCode.STRUCTURED_OUTPUT_INVALID.value,
                originalStage=GenerationStage.EVIDENCE.value,
                providerCallCount=getattr(attempt.budget, "calls", None),
                projectionValid=completed_spec is not None,
                elapsedMs=int((time.perf_counter() - projection_started) * 1000),
            )
        self.last_evidence_injections = tuple(injected)
        self.last_evidence_completed = bool(injected or extra_rules)
        if completed_spec is not None:
            evidence_spec = completed_spec
        self.last_evidence_summary = _evidence_set_summary(evidence_spec)

        # --- 3. WORLD_REQUIREMENTS --------------------------------------------
        weapon_evidence_id = _weapon_evidence_id(attempt, evidence_spec)
        world_prompt = prompts.build_world_requirements_prompt(
            attempt.prompt,
            locked_map,
            id_sheet=id_sheet,
            weapon_evidence_id=weapon_evidence_id,
        )
        # Phase 19 Fix A: the DETERMINISTIC prompt extractor is the
        # authoritative fallback for a rejected LLM environmentHint (the user
        # prompt's "Location: ..." line). This is LOCAL — zero provider calls.
        from app.world.extract import extract_world_requirements

        deterministic_hint = None
        try:
            deterministic_world = extract_world_requirements(
                attempt.prompt, attempt.locked
            )
            deterministic_hint = (
                deterministic_world.environment_hint
                if deterministic_world is not None
                else None
            )
        except Exception:  # noqa: BLE001 - extraction never breaks generation
            deterministic_hint = None
        world_reqs = WorldRequirements()
        parsed_world = self._stage_parse(
            provider,
            attempt,
            GenerationStage.WORLD_GRAPH,
            world_prompt,
            budget_consumer,
            lambda content: parse_world_requirements(
                content,
                canonical_fallback=deterministic_hint,
                case_id=getattr(attempt, "case_id", None),
                attempt_id=getattr(attempt, "attempt_id", None),
            ),
            "world_requirements",
            deferred,
        )
        if parsed_world is not None:
            world_reqs = parsed_world

        # --- 4. world composition (Environment Resolver + Oracle + placer) ---
        spec_adapter = (
            self._spec_adapter(provider, attempt, budget_consumer)
            if self._spec_provider is None
            else self._spec_provider
        )
        composition = self._compose_world(attempt, world_reqs, spec_adapter, deferred)

        # Phase 19 Fix C: a procedural-asset-count ceiling or the failed-asset
        # threshold that was hit INSIDE the asset provider is a terminal,
        # narrow cause (never repaired, never published). The provider records
        # the flag because the composer must keep its degrade-safe contract.
        if getattr(spec_adapter, "procedural_asset_ceiling_hit", False):
            raise StageDriverProviderFailure(
                "procedural asset count ceiling exceeded",
                code=GenerationFailureCode.MAX_PROCEDURAL_ASSETS_EXCEEDED,
            )
        if getattr(spec_adapter, "failed_asset_threshold_hit", False):
            raise StageDriverProviderFailure(
                "maximum failed assets exceeded",
                code=GenerationFailureCode.MAX_FAILED_ASSETS_EXCEEDED,
            )

        # --- 4b. deterministic placement-evidence projection + reconciliation
        # The composer flags an evidence-linked placement with an empty
        # interaction BEFORE the driver's app-owned projection grants the
        # documented 'inspect' interaction; once the projection ran, those
        # stale issues are satisfied and must not block the pass.
        projected_placements = None
        if composition is not None and evidence_spec is not None:
            weapon_evidence_id = _weapon_evidence_id(attempt, evidence_spec)
            projected_placements = _project_placement_evidence(
                composition.placements,
                evidence_spec,
                weapon_evidence_id,
            )
            deferred = _reconcile_evidence_interaction(deferred, projected_placements)

        # --- 5. assemble the GeneratedDraft -----------------------------------
        attempt.deferred_structural = tuple(sorted(set(deferred)))
        try:
            attempt.draft = self._assemble(
                attempt,
                crime,
                public,
                evidence_spec,
                world_reqs,
                composition,
                projected_placements=projected_placements,
                extra_travel_rules=tuple(extra_rules),
            )
        except SemanticObjectResolutionError as exc:
            # Phase 19 Fix B.3 — fail closed with a SANITIZED typed message
            # (public identifiers only) and a canonical code; never publish a
            # case with a dangling weapon/evidence id.
            raise StageDriverProviderFailure(
                str(exc),
                code=GenerationFailureCode.VALIDATION_FAILED,
            ) from None
        attempt._phase3_cache = None
        # Phase 19 Fix C §8/§13 — sanitized monotonic accounting snapshot
        # (structure only; never raw prompts / truth / provider responses).
        budget = getattr(attempt, "budget", None)
        if budget is not None and hasattr(budget, "snapshot"):
            snap = budget.snapshot()
            emit_event(
                "provider.budget.snapshot",
                caseId=getattr(attempt, "case_id", None),
                generationAttemptId=getattr(attempt, "attempt_id", None),
                globalCallCount=snap.get("globalCallCount"),
                coreCallCount=snap.get("coreCallCount"),
                assetCallCount=snap.get("assetCallCount"),
                remainingGlobalCalls=snap.get("remainingGlobalCalls"),
                remainingCoreCalls=snap.get("remainingCoreCalls"),
                proceduralAssetCount=snap.get("proceduralAssetCount"),
                failedAssetCount=snap.get("failedAssetCount"),
            )
        if _PD_DEV_TRACE:
            _dt(
                "driver.run_into.end elapsedMs=%d deferredStructural=%d "
                "draftSet=%s deadlineRemainingMs=%s"
                % (
                    int((time.perf_counter() - _t0) * 1000),
                    len(attempt.deferred_structural),
                    attempt.draft is not None,
                    _dt_remaining(attempt),
                )
            )

    def _make_budget_consumer(self, attempt: Any) -> Callable[[str | None], bool]:
        """Budget consumer with Phase 19 Fix C bucket attribution.

        ``consumer(None)`` / ``consumer(CORE_BUCKET)`` reserves a CORE bucket
        call (case/evidence/world + repair/regeneration). The CORE bucket is
        the NON-STRING sentinel ONLY (ADV-216): ``consumer(object_id)`` with
        ANY string — even the literal ``"core"`` — reserves an ASSET:<objectId>
        bucket call (procedural ASSET_SPEC / ASSET_SPEC_REPAIR / geometry).
        Deterministic local repairs never touch this consumer.
        """

        def _consume(bucket: str | None = None) -> bool:
            budget = attempt.budget
            if budget is None:
                return False
            if budget.deadline_passed():
                return False
            return budget.consume_call(bucket=bucket)

        return _consume

    def _effective_timeout(self, attempt: Any | None = None) -> float:
        budget = getattr(attempt, "budget", None)
        if budget is None:
            return max(0.0, float(getattr(self._settings, "ollama_timeout_seconds", 60.0)))
        return budget.effective_provider_timeout(
            float(getattr(self._settings, "ollama_timeout_seconds", 60.0)),
            safety_margin_seconds=PROVIDER_CALL_SAFETY_MARGIN_SECONDS,
        )

    def _evidence_feedback(
        self,
        attempt: Any,
        diagnostics: tuple[str, ...],
        is_rerun: bool,
    ) -> str:
        """Sanitized re-run feedback token for the EVIDENCE prompt.

        On a repair/regeneration pass the previous validation's sanitized
        diagnostics are appended, plus the still-viable/unknown candidate ids
        (PUBLIC candidate ids only — the id sheet/approved material already
        carry them, never truth), so the second pass targets exactly the fact
        groups that were missing. Empty on the first run.
        """
        if not is_rerun or not diagnostics:
            return ""
        lines = [
            "PREVIOUS-PASS FEEDBACK (from the deterministic validation of "
            "your LAST evidence — re-emit the COMPLETE fact set, fixing "
            "exactly these gaps):"
        ]
        for line in diagnostics:
            lines.append(f"- {line}")
        proof = getattr(attempt, "solver_proof", None)
        if proof is not None:
            for name, dim in (
                ("suspect", proof.who),
                ("motive", proof.why),
                ("weapon", proof.weapon),
            ):
                if dim is None or getattr(dim, "unique", False):
                    continue
                viable = list(getattr(dim, "viable", ()) or ())
                unknown = list(getattr(dim, "unknown_remaining", ()) or ())
                lines.append(
                    f"- {name} dimension was NOT unique: viable "
                    f"{viable}; unknown/unsupported {unknown}"
                )
        return "\n".join(lines)

    def _spec_adapter(self, provider: Any, attempt: Any, budget: Callable[[], bool]) -> Any:
        return OllamaAssetSpecProvider(
            provider=provider,
            attempt_id=attempt.attempt_id,
            budget_consumer=budget,
            locked=attempt.locked,
            seed=attempt.seed,
            model_label=getattr(self._settings, "ollama_model", ""),
            configured_timeout_seconds=float(
                getattr(self._settings, "ollama_timeout_seconds", 60.0)
            ),
            timeout_provider=lambda: self._effective_timeout(attempt),
metrics_provider=lambda: attempt.budget,
        )

    def _call(
        self,
        provider: Any,
        attempt: Any,
        stage: GenerationStage,
        prompt: str,
        budget: Callable[[str | None], bool],
    ) -> str | None:
        effective_timeout = self._effective_timeout(attempt)
        configured_timeout_ms = int(
            float(getattr(self._settings, "ollama_timeout_seconds", 60.0)) * 1000
        )
        if effective_timeout <= 0:
            raise StageDriverProviderFailure(
                "generation deadline exceeded",
                code=GenerationFailureCode.GENERATION_DEADLINE_EXCEEDED,
            )
        if not budget(CORE_BUCKET):
            raise StageDriverProviderFailure(
                "model call budget exhausted",
                code=failure_code_for_budget_reason(
                    attempt.budget.exhausted_reason(CORE_BUCKET)
                    if getattr(attempt, "budget", None) is not None
                    else "model call budget exhausted"
                ),
            )
        request = GenerateRequest(
            attempt_id=attempt.attempt_id,
            stage=stage,
            prompt_context=prompt,
            locked=attempt.locked,
            diagnostics=(),
            seed=attempt.seed,
            timeout_seconds=effective_timeout,
        )
        emit_event(
            "provider.call.start",
            caseId=getattr(attempt, "case_id", None),
            generationAttemptId=getattr(attempt, "attempt_id", None),
            stage=stage.value,
            provider="ollama",
            model=getattr(self._settings, "ollama_model", None),
            configuredGenerationDeadlineMs=(
                int(attempt.budget.deadline_seconds * 1000)
                if getattr(attempt, "budget", None) is not None else None
            ),
            deadlineRemainingMs=_remaining_ms(attempt),
            configuredProviderTimeoutMs=int(float(getattr(self._settings, "ollama_timeout_seconds", 60.0)) * 1000),
            effectiveProviderTimeoutMs=int(effective_timeout * 1000),
            providerCallCount=getattr(attempt.budget, "calls", None),
            repairCount=getattr(attempt.budget, "repair_passes", None),
            regenerationCount=getattr(attempt.budget, "regenerations", None),
            requestBytes=len(prompt.encode("utf-8")),
        )
        _t0 = time.perf_counter()
        result: ProviderResult = self._invoke(provider, request)
        if _PD_DEV_TRACE:
            if result.content is not None:
                summary = "ok(content)"
            elif result.timed_out:
                summary = "FAILED(timed_out)"
            else:
                summary = "FAILED(error=%s)" % (result.error or "unknown")
            _dt(
                "driver.call.done stage=%s %s elapsedMs=%d calls=%s "
                "deadlineRemainingMs=%s"
                % (
                    stage.value,
                    summary,
                    int((time.perf_counter() - _t0) * 1000),
                    getattr(attempt.budget, "calls", "?"),
                    _dt_remaining(attempt),
                )
            )
        if result.timed_out:
            emit_event(
                "provider.call.timeout",
                caseId=getattr(attempt, "case_id", None),
                generationAttemptId=getattr(attempt, "attempt_id", None),
                stage=stage.value,
                provider="ollama",
                failureCode=GenerationFailureCode.PROVIDER_TIMEOUT.value,
                configuredProviderTimeoutMs=configured_timeout_ms,
                effectiveProviderTimeoutMs=int(effective_timeout * 1000),
                deadlineRemainingMs=_remaining_ms(attempt),
            )
            raise StageDriverProviderFailure(
                "provider request timed out",
                code=GenerationFailureCode.PROVIDER_TIMEOUT,
            )
        if result.error is not None:
            code = infer_failure_code(result.error)
            emit_event(
                "provider.call.error",
                caseId=getattr(attempt, "case_id", None),
                generationAttemptId=getattr(attempt, "attempt_id", None),
                stage=stage.value,
                provider="ollama",
                failureCode=code.value,
                configuredProviderTimeoutMs=configured_timeout_ms,
                effectiveProviderTimeoutMs=int(effective_timeout * 1000),
                deadlineRemainingMs=_remaining_ms(attempt),
            )
            raise StageDriverProviderFailure(
                "provider returned an unusable response", code=code
            )
        if result.content is None:
            raise StageDriverProviderFailure(
                "provider returned an invalid response",
                code=GenerationFailureCode.PROVIDER_INVALID_RESPONSE,
            )
        emit_event(
            "provider.call.complete",
            caseId=getattr(attempt, "case_id", None),
            generationAttemptId=getattr(attempt, "attempt_id", None),
            stage=stage.value,
            provider="ollama",
            model=getattr(self._settings, "ollama_model", None),
            success=True,
            requestBytes=len(prompt.encode("utf-8")),
            responseBytes=len(result.content.encode("utf-8")),
            structuredOutput=True,
            configuredProviderTimeoutMs=configured_timeout_ms,
            effectiveProviderTimeoutMs=int(effective_timeout * 1000),
            deadlineRemainingMs=_remaining_ms(attempt),
        )
        return result.content

    def _stage_parse(
        self,
        provider: Any,
        attempt: Any,
        stage: GenerationStage,
        prompt: str,
        budget: Callable[[], bool],
        parse_fn: Callable[[str], Any],
        label: str,
        deferred: list[str],
        *,
        retry_on_parse_failure: bool = True,
    ) -> Any:
        """ONE stage call + strict parse, with an optional bounded retry.

        Real Hermes occasionally slips a single stage contract (e.g. omits a
        required proposition field) even after the smoke determinism fixes;
        a stage REPEAT with the sanitized strict-parse issue as feedback fixes
        those without weakening the parser. Every attempt is a REAL budgeted
        provider call; the global 8-call budget stays authoritative. Provider
        failures are never retried (terminal per attempt).  Callers with an
        application-owned deterministic replacement may set
        ``retry_on_parse_failure=False``: the rejected raw response is then
        neither retried nor recorded as a deferred draft defect.  The caller
        must still route its replacement through normal validation.
        """
        content = self._call(provider, attempt, stage, prompt, budget)
        if content is None:
            deferred.append(f"{label} stage produced no usable content")
            return None
        try:
            return parse_fn(content)
        except (TypeError, ValueError) as exc:
            if not retry_on_parse_failure:
                return None
            retry_prompt = (
                f"{prompt}\n\nThe previous response failed the strict "
                f"application parser: {exc}\n"
                "Return ONLY a corrected, complete JSON document for this "
                "stage that fixes every issue listed above."
            )
            retry_content = self._call(provider, attempt, stage, retry_prompt, budget)
            if retry_content is not None:
                try:
                    return parse_fn(retry_content)
                except (TypeError, ValueError) as exc2:
                    deferred.append(f"{label} stage parse failed: {exc2}")
                    return None
            deferred.append(f"{label} stage parse failed: {exc}")
            return None

    @staticmethod
    def _invoke(provider: Any, request: GenerateRequest) -> ProviderResult:
        result = provider.generate(request)
        if not isinstance(result, ProviderResult):
            raise StageDriverProviderFailure("provider returned an invalid result type")
        return result

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _locked_map(attempt: Any) -> Mapping[str, Any] | None:
        locked = getattr(attempt, "locked", None)
        if locked is None:
            return None
        return {key: value for key, value in locked.locked_fields() if value is not None}

    def _compose_world(
        self,
        attempt: Any,
        world_reqs: WorldRequirements,
        spec_adapter: Any,
        deferred: list[str],
    ) -> Any:
        """Environment Resolver -> Asset Oracle -> World Composer."""
        from app.environments.compose import scene_for_kit
        from app.environments.manifests import load_environment
        from app.environments.resolver import FALLBACK_ENVIRONMENT_ID
        from app.world.composer import WorldComposition, compose_world

        try:
            hint = world_reqs.environment_hint or FALLBACK_ENVIRONMENT_ID
            # Phase 19 Fix A: an already-canonical value passes cleanly; a
            # hand-built WorldRequirements with a path-like hint is rejected
            # here deterministically (never a file path, never a provider
            # call) and falls back to the default kit.
            canonical, _env_issues = canonicalize_environment_hint(hint)
            if canonical is not None:
                hint = canonical
            else:
                hint = FALLBACK_ENVIRONMENT_ID
            kit = load_environment(str(hint))
        except Exception:  # noqa: BLE001 - environment failure degrades safe
            deferred.append("world_requirements stage: unsupported environment")
            return None
        try:
            composition = compose_world(
                world_reqs,
                env_resolver=None,
                oracle=None,
                spec_provider=spec_adapter,
                cache=self._generated_cache,
                evidence_placements=(),
                catalog=self._catalog,
                kit=kit,
            )
        except StageDriverProviderFailure:
            # ADV-213: a TYPED provider failure (per-asset/global budget
            # exhaustion, deadline, timeout) is the attempt's narrow cause —
            # never absorbed into a generic "world composition failed" deferral.
            raise
        except Exception:  # noqa: BLE001 - compose degrades safe
            deferred.append("world composition failed for the requested world")
            return None
        if not isinstance(composition, WorldComposition):
            deferred.append("world composition produced no usable result")
            return None
        if composition.issues:
            deferred.extend(composition.issues)
        return composition

    def _assemble(
        self,
        attempt: Any,
        crime: Any,
        public: Any,
        evidence_spec: Any,
        world_reqs: WorldRequirements,
        composition: Any,
        *,
        projected_placements: Any = None,
        extra_travel_rules: tuple[Any, ...] = (),
    ) -> Any:
        """Build the ``GeneratedDraft`` from the staged outputs + composition.

        ``projected_placements`` (when supplied by ``run_into``) are the
        already-projected placements — the driver's single trust boundary
        computes them ONCE and reconciles the composer's stale interaction
        issues against them. ``extra_travel_rules`` are the deterministic
        travel rules the evidence completion added (merged into the draft's
        public travel rules)."""
        from app.environments.compose import scene_for_kit
        from app.environments.manifests import load_environment
        from app.environments.resolver import FALLBACK_ENVIRONMENT_ID
        from app.generation.publish import build_published_case_version  # noqa: F401
        from app.generation.schemas import (
            GeneratedDraft,
            LocationSpec,
            ObjectSpec,
            SceneSpec,
            WorldGraphLocationSpec,
            WorldGraphSpec,
        )

        if crime is None:
            # no case truth -> validate_draft reports "incomplete" (controller
            # classifies TERMINAL/REPAIR through the normal path).
            return None

        # Resolve the final kit for the scene + world graph.
        hint = (world_reqs.environment_hint if world_reqs else None) or FALLBACK_ENVIRONMENT_ID
        canonical, _env_issues = canonicalize_environment_hint(hint)
        if canonical is not None:
            hint = canonical
        else:
            hint = FALLBACK_ENVIRONMENT_ID
        try:
            kit = load_environment(str(hint))
        except Exception:  # noqa: BLE001
            kit = None

        # composition placements -> world graph + new public objects.
        placements: list[Any] = []
        new_objects: list[ObjectSpec] = []
        if composition is not None:
            if projected_placements is not None:
                placements = list(projected_placements)
            else:
                weapon_evidence_id = _weapon_evidence_id(attempt, evidence_spec)
                placements = list(
                    _project_placement_evidence(
                        composition.placements,
                        evidence_spec,
                        weapon_evidence_id,
                    )
                )

        # base public objects (full golden base set). The OBJECTS list carries
        # the complete golden base identity set on EVERY kit (the same set the
        # fake/live golden world uses) so every evidence/identity reference —
        # including the sharp-weapon forensics — stays valid on kits whose
        # PLACEMENT subset omits a spare weapon (hotel_suite/warehouse). The
        # per-kit placement subset (KIT_BASE_OBJECT_IDS) is a PLACEMENT concern
        # (anchor capacity), the object identity set is not.
        #
        # ADV-219 (Phase 19B): the player-visible WEAPON CANDIDATE universe is
        # derived from the PUBLIC world objects actually PLACED in the scene.
        # A spare sharp object (letter_opener / scissors) that this kit cannot
        # place stays a WORLD identity object (forensic/evidence references on
        # those kits keep resolving, the presence guard stays clean) BUT is
        # demoted to INSPECTABLE-only — it can never be a POTENTIAL_WEAPON
        # candidate the player could not find in the scene. Kits that DO place
        # them (apartment/office/mansion) keep them as weapon candidates.
        placed_object_ids = {p.object_id for p in placements if getattr(p, "object_id", None)}
        objects: list[ObjectSpec] = []
        if kit is not None:
            for object_id in OBJECTS_BASE_ALL_KITS:
                spec = _base_object_spec(object_id)
                if spec is None or any(o.object_id == spec.object_id for o in objects):
                    continue
                if object_id in _PER_KIT_SHARP_WEAPON_IDS and object_id not in placed_object_ids:
                    spec = dataclasses.replace(spec, affordances=("INSPECTABLE",))
                objects.append(spec)
        if composition is not None:
            for obj in composition.new_objects:
                if not any(o.object_id == obj.object_id for o in objects):
                    new_objects.append(_enhance_weapon(attempt, obj))
        objects = [*objects, *new_objects]

        scene: SceneSpec | None = None
        world_graph = WorldGraphSpec()
        if kit is not None:
            scene = scene_for_kit(kit, public.scene if public is not None else None)
            if composition is not None:
                world_graph = WorldGraphSpec(
                    locations=tuple(
                        WorldGraphLocationSpec(
                            location_id=zone.zone_id,
                            template=f"{kit.environment_id}_template",
                            rooms=zone.rooms,
                        )
                        for zone in kit.zones
                    ),
                    placements=tuple(placements),
                )

        merged_travel = tuple(public.travel_rules) if public is not None else ()
        if extra_travel_rules:
            merged_travel = tuple(
                dict.fromkeys(merged_travel + tuple(extra_travel_rules))
            )

        # ---- Phase 19 Fix B.3 — fail-closed OBJECT PRESENCE GUARANTEE ----
        # Every semantic object the truth/evidence algebra references MUST be
        # present as exactly one public object. The canonical evidence algebra
        # is deterministic and complete by construction, so a missing reference
        # is a genuine contract violation — never repairable, never silently
        # dropped. Raise a TYPED sanitized error (the caller fails closed and
        # nothing is published). Catalog alias / procedural resolution already
        # ran above (in the composer), so this guard only fires when no safe
        # representation could be produced.
        semantic_object_ids: set[str] = {
            o.object_id for o in objects if o.object_id
        }
        referenced: set[str] = set()
        if getattr(crime, "weapon_id", None):
            referenced.add(crime.weapon_id)
        if evidence_spec is not None:
            for item in getattr(evidence_spec, "evidence", ()) or ():
                for proposition in getattr(item, "propositions", ()) or ():
                    object_id = getattr(proposition, "object_id", None)
                    if isinstance(object_id, str) and object_id:
                        referenced.add(object_id)
        missing = sorted(r for r in referenced if r not in semantic_object_ids)
        if missing:
            # ADV-218: the referenced ids are only length-bounded upstream
            # (``_str_field``), so they are SANITIZED before embedding in this
            # internal validation message — never a raw hostile URL / control
            # character in the typed error text. The failure-code semantics
            # (VALIDATION_FAILED, fail closed, nothing published) are unchanged.
            raise SemanticObjectResolutionError(
                "essential semantic object(s) referenced by the crime/evidence "
                "algebra could not be represented in the world: "
                + _sanitize_object_ids_for_message(missing)
            )

        return GeneratedDraft(
            crime=crime,
            persons=tuple(public.persons) if public is not None and public.persons else (),
            motives=tuple(public.motives) if public is not None and public.motives else (),
            objects=tuple(objects),
            locations=(
                tuple(public.locations)
                if public is not None and public.locations
                else (LocationSpec(location_id=crime.location_id, name=crime.location_id),)
            ),
            travel_rules=merged_travel,
            scene=scene,
            evidence=tuple(evidence_spec.evidence) if evidence_spec is not None else (),
            world_graph=world_graph,
            # ADV-153: the DECORATIVE-unresolved warnings are stored with the
            # published draft (player-safe, bounded, sanitized) exactly like
            # the fake/live composition path.
            composition_notes=(
                tuple(composition.composition_notes)
                if composition is not None
                else ()
            ),
        )


# --------------------------------------------------------------------------- #
# base kit object specs (app-owned mirror of the golden base set so the weapon/
# evidence candidate universes stay correct for a driver-produced draft).
# --------------------------------------------------------------------------- #


_EVIDENCE_INTERACTION_ISSUE_RE = re.compile(
    r"^world\.evidence-interaction: evidence-linked object '([^']*)' requires a "
    r"non-empty interaction$"
)


def _reconcile_evidence_interaction(
    deferred: list[str], projected_placements: Any
) -> list[str]:
    """Drop stale evidence-interaction issues the driver's projection resolved.

    The world composer validates BEFORE the app-owned ``_project_placement_
    evidence`` runs and flags any evidence-linked placement whose interaction
    is still empty. That same projection deterministically grants the
    documented ``inspect`` interaction to every evidence-linked placement, so
    once the projection ran the invariant ('an evidence-linked placement must
    have a non-empty interaction') IS satisfied. This reconciliation removes
    only the issues for placements the projection actually fixed (deferred
    world issues are the DRIVER's own sanitized bucket — the composer and the
    validators are untouched)."""
    if not projected_placements:
        return list(deferred)
    fixed: set[str] = set()
    for placement in projected_placements:
        if (
            getattr(placement, "evidence_id", None) is not None
            and getattr(placement, "interaction", "")
        ):
            fixed.add(str(getattr(placement, "object_id", "")))
    out: list[str] = []
    for line in deferred:
        match = _EVIDENCE_INTERACTION_ISSUE_RE.match(line)
        if match is not None and match.group(1) in fixed:
            continue
        out.append(line)
    return out


# ADV-222 (Phase 19C §5): the universally-placed DEVICE object of every kit
# base world (the golden "laptop") whose authored association does not survive
# into a driver world (the canonical algebra carries no email read) is re-bound
# to the canonical scene activity-log record (``d_ev_when_obs``,
# CRIME_SCENE_OBSERVATION_AT). This makes a TIME-BEARING fact PLAYER-REACHABLE
# in every published driver world — WHEN becomes derivable from discoverable
# evidence exactly like WHO/WHY/WEAPON. No showcase special-casing: the rule is
# the general device-log anchor for the kit base world (laptop/terminal in
# every kit), gated on the placement being AUTHORED as evidence-bearing.
_WHEN_ACTIVITY_LOG_ANCHOR_OBJECTS: frozenset[str] = frozenset(
    {"apartment_laptop"}
)
_CANONICAL_WHEN_ACTIVITY_EVIDENCE_ID = "d_ev_when_obs"


def _when_activity_log_evidence_id(evidence_spec: Any) -> str | None:
    """The canonical scene activity-log evidence id when the published world
    carries it (the driver's deterministic WHEN algebra always does), else
    ``None`` (the device placement then stays decorative — never fabricated)."""
    if evidence_spec is None:
        return None
    for item in getattr(evidence_spec, "evidence", ()) or ():
        if str(getattr(item, "id", "")) == _CANONICAL_WHEN_ACTIVITY_EVIDENCE_ID:
            return _CANONICAL_WHEN_ACTIVITY_EVIDENCE_ID
    return None


def _evidence_bind_rank(item: Any, needle: str) -> int:
    """The SEMANTIC priority of ``item`` as the published evidence binding for
    the object ``needle`` (ADV-223).

    When TWO canonical facts reference the same object the resolve rule must
    pick the fact whose KIND/ROLE matches the object's published role, not an
    arbitrary id order. The documented priority list (lower wins):

      0 — the evidence carries a ``FORENSIC_WEAPON_MATCH`` proposition for the
          object (the weapon-match record — what a sharp weapon's published
          "forensic comparison" role points at);
      1 — the evidence carries an ``OBJECT_CONTAINS_FINGERPRINT`` proposition
          for the object (the latent-print forensics role);
      2 — any other FORENSIC-kind evidence referencing the object;
      3 — any other evidence (generic cctv/witness/email extras).

    Ties break by evidence id order (``_first_evidence_referencing_object``).
    """
    from app.generation.constraints import normalize_identity

    for prop in getattr(item, "propositions", ()) or ():
        if normalize_identity(getattr(prop, "object_id", None)) != needle:
            continue
        ptype = str(getattr(prop, "type", "") or "")
        if ptype == "FORENSIC_WEAPON_MATCH":
            return 0
        if ptype == "OBJECT_CONTAINS_FINGERPRINT":
            return 1
    if str(getattr(item, "kind", "") or "") == "forensic":
        return 2
    return 3


def _first_evidence_referencing_object(
    evidence_spec: Any, object_id: str
) -> str | None:
    """The ROLE-MATCHED (semantic) real evidence id whose propositions
    reference ``object_id`` (normalized comparison), or ``None`` when no
    published evidence references the object.

    Phase 19C: the driver's canonical evidence algebra always emits forensic
    comparison records for the kit base sharp weapons
    (``d_ev_weapon_false_kitchenknife`` etc.), so a base sharp-object
    placement whose composer evidence id (``forensic_knife_match_01`` ...)
    does not exist in the driver world is re-bound to that REAL record — the
    same experience the golden world gives (knife -> forensic comparison).

    ADV-223: the pick is SEMANTIC, not alphabetical — among the candidate
    facts referencing the object the one whose kind/role best matches the
    object's published role wins (see ``_evidence_bind_rank`` for the
    documented priority list); ties break by evidence id. Deterministic and
    stable across runs (evidence ORDER independent); never fabricates.
    """
    from app.generation.constraints import normalize_identity

    needle = normalize_identity(str(object_id or ""))
    if not needle:
        return None
    matches: list[tuple[int, str]] = []
    for item in getattr(evidence_spec, "evidence", ()) or ():
        item_id = str(getattr(item, "id", ""))
        if not item_id:
            continue
        for prop in getattr(item, "propositions", ()) or ():
            prop_id = normalize_identity(getattr(prop, "object_id", None))
            if prop_id and prop_id == needle:
                matches.append((_evidence_bind_rank(item, needle), item_id))
                break
    if not matches:
        return None
    return sorted(matches)[0][1]


def _project_placement_evidence(
    placements: Any,
    evidence_spec: Any,
    weapon_evidence_id: str,
) -> tuple[Any, ...]:
    """Deterministic placement→evidence referential projection (Phase17D).

    The model's world output may attach fabricated evidenceIds (or copy a
    sheet line) to placements; the strict engine REJECTS any evidenceId that
    is not a real parsed evidence id. The DRIVER is the sole trust boundary
    and projects the reference exactly as it projects weapon affordances
    (``_enhance_weapon``): every placement keeps its evidenceId ONLY when it
    is a REAL parsed evidence id; the LOCKED weapon placement is bound to the
    SEALED weapon evidence id when one exists; everything else degrades to
    ``None`` (a decorative placement). Never fabricates evidence, never drops
    a placement, never serializes.

    Phase 19C general rule (no object is special-cased): the composer wires
    the GOLDEN evidence associations onto the kit base placements
    (``_GOLDEN_OBJECT_FACTS``: knife -> forensic_knife_match_01, laptop ->
    email_thomas_01, ...) but the DRIVER replaces the published evidence with
    its canonical ``d_ev_*`` algebra — those golden ids do not exist in the
    driver world. A placement whose composer evidence id is dropped must
    NEVER publish as an interactable-but-evidence-less dead-end. Resolution:

    - the placement is re-bound to the ROLE-MATCHED real canonical evidence
      fact whose propositions reference the object (``kitchen_knife`` ->
      ``d_ev_weapon_false_kitchenknife``, ``letter_opener`` ->
      ``d_ev_weapon_false_letteropener``, ``scissors`` ->
      ``d_ev_weapon_false_scissors``), exactly like the golden experience;
    - a placement that was AUTHORED as evidence-bearing (composer evidence id
      non-empty) but for which NO canonical evidence references the object
      (e.g. a LAPTOP — a driver world carries no laptop email) is bound to
      the canonical scene activity-log record when one exists
      (``d_ev_when_obs`` — ADV-222: the player can then derive WHEN from
      discoverable evidence), otherwise it is published DECORATIVE
      (interaction "") so it never misleads the player into an interaction
      with nothing to find;
    - a placement that was NEVER authored as evidence-bearing (composer
      evidence id ``None``) and carries an explicit requested interaction is
      an INFORMATIONAL object (ADV-224): it is NEVER rebound/upgraded to an
      evidence object — its interaction stays (the frontend renders the
      Phase 19C "Nothing relevant was found on <X>." feedback for a 200
      ``discovery: null`` response), exactly as before.
    """
    from app.generation.constraints import normalize_identity
    from app.generation.schemas import PlacementSpec

    valid_ids = {
        str(getattr(item, "id", ""))
        for item in (getattr(evidence_spec, "evidence", ()) or ())
    }
    locked_weapon_slug = (
        normalize_identity(_weapon_object_id_from_evidence(evidence_spec, weapon_evidence_id))
        if weapon_evidence_id
        else ""
    )
    projected: list[Any] = []
    for placement in placements:
        existing = getattr(placement, "evidence_id", None)
        evidence_id = existing if existing in valid_ids else None
        if (
            evidence_id is None
            and weapon_evidence_id
            and weapon_evidence_id in valid_ids
            and locked_weapon_slug
            and normalize_identity(str(getattr(placement, "object_id", "")))
            == locked_weapon_slug
        ):
            evidence_id = weapon_evidence_id
        if not isinstance(placement, PlacementSpec):
            projected.append(placement)
            continue
        interaction = getattr(placement, "interaction", "")
        if evidence_id is None and interaction:
            # Phase 19C resolution (see docstring): the composer's evidence
            # association does not survive into the driver world.
            #
            # ADV-224: only placements AUTHORED as evidence-bearing (composer
            # evidence id non-empty, dropped by the driver) — or the locked
            # weapon object — may be rebound/upgraded. A NEVER-authored
            # informational object that a canonical fact happens to reference
            # MUST stay informational (interaction unchanged, discovery null).
            is_locked_weapon_object = (
                bool(weapon_evidence_id)
                and weapon_evidence_id in valid_ids
                and bool(locked_weapon_slug)
                and normalize_identity(str(getattr(placement, "object_id", "")))
                == locked_weapon_slug
            )
            if existing is not None or is_locked_weapon_object:
                rebound = _first_evidence_referencing_object(
                    evidence_spec, str(getattr(placement, "object_id", ""))
                )
                if rebound is not None:
                    evidence_id = rebound
                elif existing is not None:
                    # ADV-222: an authored device placement with no referencing
                    # fact keeps WHEN derivable from discoverable evidence by
                    # binding the canonical scene activity-log record.
                    if (
                        str(getattr(placement, "object_id", ""))
                        in _WHEN_ACTIVITY_LOG_ANCHOR_OBJECTS
                        and _when_activity_log_evidence_id(evidence_spec)
                    ):
                        evidence_id = _CANONICAL_WHEN_ACTIVITY_EVIDENCE_ID
                    else:
                        # Authored as evidence-bearing but no real association
                        # exists in THIS world -> decorative, never a
                        # misleading dead-end.
                        interaction = ""
        # An evidence-linked placement MUST be directly interactable (the
        # safety engine refuses an evidence-linked object with an empty
        # interaction). The locked weapon placement (and any other
        # evidence-linked placement) receives the deterministic inspect
        # interaction exactly as ``_enhance_weapon`` grants affordances.
        if evidence_id is not None and not interaction:
            interaction = "inspect"
        projected.append(
            dataclasses.replace(placement, evidence_id=evidence_id, interaction=interaction)
        )
    return tuple(projected)


def _weapon_object_id_from_evidence(evidence_spec: Any, weapon_evidence_id: str) -> str:
    """The locked-weapon object id the sealed weapon evidence references."""
    for item in getattr(evidence_spec, "evidence", ()) or ():
        if str(getattr(item, "id", "")) != weapon_evidence_id:
            continue
        for prop in getattr(item, "propositions", ()) or ():
            object_id = getattr(prop, "object_id", None)
            if isinstance(object_id, str) and object_id:
                return object_id
        return ""
    return ""


def _enhance_weapon(attempt: Any, obj: ObjectSpec) -> ObjectSpec:
    """Deterministically grant weapon affordances to a crime-critical unknown
    object that IS the locked weapon.

    The LLM proposes the object; the DETERMINISTIC driver decides universe
    membership. When a procedural object's id normalizes to the LOCKED weapon
    it enters the weapon universe (POTENTIAL_WEAPON + POTENTIAL_SHARP_WEAPON)
    so the solver can derive it — this is the showcase path that lets a
    crime-critical unseen object be the actual weapon the deduction proves.
    """
    from app.generation.constraints import normalize_identity
    from app.generation.schemas import ObjectSpec

    locked = getattr(attempt, "locked", None)
    weapon = getattr(locked, "weapon", None) if locked is not None else None
    if not isinstance(weapon, str) or not weapon:
        return obj
    if normalize_identity(weapon) != normalize_identity(obj.object_id):
        return obj
    return ObjectSpec(
        object_id=obj.object_id,
        asset_id=obj.asset_id,
        affordances=("INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
        subtype=obj.subtype,
    )


def _base_object_spec(object_id: str) -> ObjectSpec | None:
    from app.generation.schemas import ObjectSpec

    _BASE: dict[str, tuple[str, tuple[str, ...], str | None]] = {
        "kitchen_knife": (
            "PROP_KITCHEN_KNIFE_01",
            ("INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
            "sharp_weapon",
        ),
        "letter_opener": (
            "PROP_LETTER_OPENER_01",
            ("INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
            "sharp_weapon",
        ),
        "scissors": (
            "PROP_SCISSORS_01",
            ("INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
            "sharp_weapon",
        ),
        "vase_01": ("PROP_VASE_01", ("INSPECTABLE",), None),
        "apartment_table": ("PROP_TABLE_01", ("INSPECTABLE",), "furniture"),
        "apartment_door": ("DOOR_APARTMENT_01", ("INSPECTABLE",), "door"),
        "apartment_lamp": ("PROP_LAMP_01", ("INSPECTABLE",), "light"),
        "apartment_laptop": ("PROP_LAPTOP_01", ("INSPECTABLE",), "electronics"),
        "victim_body_placeholder": ("PROP_BODY_PLACEHOLDER_01", ("INSPECTABLE",), "victim_body"),
    }
    entry = _BASE.get(object_id)
    if entry is None:
        return None
    asset_id, affordances, subtype = entry
    return ObjectSpec(
        object_id=object_id,
        asset_id=asset_id,
        affordances=affordances,
        subtype=subtype,
    )


__all__ = [
    "MAX_SPEC_REPAIR_PASSES",
    "OllamaAssetSpecProvider",
    "OllamaStageDriver",
    "StageDriverProviderFailure",
    "parse_case_people",
    "parse_world_requirements",
    "_BASE_SHARP_WEAPON_IDS",
    "_approved_travel_sheet",
    "_deduction_seed",
    "_evidence_gap_facts",
    "_evidence_timeline",
    "_reconcile_evidence_interaction",
]
