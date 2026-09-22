"""Strict provider-output schema validation (Phase4 J, REQUIREMENTS 47A / 32.8).

``parse_stage`` / ``parse_full_draft`` parse provider output into the typed
generation models (``app.generation.schemas``). They NEVER coerce malformed
data: any violation produces a deterministic sorted issue tuple and the caller
either receives ``None`` (``non_throwing=True``) or a ``GenerationParseError``
carrying ``.issues`` (``non_throwing=False``). ``collect_issues`` is the
non-raising entry used by validation diagnostics.

STRICT rules (reject, never coerce):

1. unknown top-level and nested keys are rejected (documented allowlist per
   section);
2. duplicate ids rejected: persons / motives / objects / locations / evidence;
3. type checks: non-empty string ids; affordances from the documented
   vocabulary (``AFFORDANCE_VOCABULARY``); reliability in
   {high, medium, low}; discoverable bool; uncertainty int >= 0; ints never
   accept bools;
4. every ``observedAt`` + ``crimeTime.canonical`` + structured
   ``claimedDeparture`` must parse through
   ``app.domain.time_interval.parse_iso8601`` and lie inside
   ``[SOLVER_TIME_MIN, SOLVER_TIME_MAX)``;
5. proposition types restricted to ``app.domain.evidence.PROPOSITION_TYPES``;
6. FORENSIC_WEAPON_MATCH requires a real bool ``match``; ALIBI_TIME_CLAIM
   requires a valid ``claimedDeparture`` (Phase 3 TypedProposition validation
   is reused by constructing it);
7. size bounds: MAX_CHARACTERS=8, MAX_LOCATIONS=8, MAX_EVIDENCE_ITEMS=50,
   MAX_DOCUMENT_CHARS=12000 (presentation/description), 
   MAX_SINGLE_TEXT_FIELD_CHARS=4000 (every string field), whole provider output
   <= MAX_PROVIDER_OUTPUT_CHARS=200000;
8. cross-field reference checks are deferred to the assemble step (lifecycle
   task); within-stage crime ``*_id`` values only need to be non-empty strings;
9. JSON parse failure -> single issue; empty/whitespace content -> issue.
"""

from __future__ import annotations

import json
from typing import Any

# --------------------------------------------------------------------------- #
# PD-SEC-09 — bounded structured-provider JSON depth preflight.
#
# The provider body is already SIZE-bounded (MAX_PROVIDER_OUTPUT_CHARS below,
# and the transport caps at 256 KiB), but a size-bounded document can still be
# a deep NESTING BOMB ("[[[[...]]]]" 100k levels fits in 200k chars) that would
# blow the interpreter recursion limit inside ``json.loads`` with an uncaught
# ``RecursionError``. The same iterative bracket-depth preflight the Asset
# pipeline uses (``app.assets.depthguard.bounded_json_loads``) is therefore
# applied HERE, at the one generic entry every generation-stage document passes
# through: a too-deep document is rejected as a clean ``BoundedJsonError`` (a
# ``ValueError``) and flows through the normal sanitized "not valid JSON" issue,
# exactly like the AssetSpec path. ``MAX_STRUCT_NESTING`` = 32 is far above
# every legitimate generated document (stage payloads nest at a handful of
# levels) and far below CPython's recursion ceiling.
# ---------------------------------------------------------------------------

from app.assets.depthguard import BoundedJsonError, MAX_STRUCT_NESTING, bounded_json_loads
from app.domain.evidence import (
    ALIBI_TIME_CLAIM,
    FORENSIC_WEAPON_MATCH,
    PROPOSITION_TYPES,
)
from app.domain.time_interval import (
    SOLVER_TIME_MAX,
    SOLVER_TIME_MIN,
    assert_epoch_in_domain,
    parse_iso8601,
)

from app.generation.provider import GenerationStage
from app.generation.schemas import (
    AFFORDANCE_VOCABULARY,
    MAX_CHARACTERS,
    MAX_DOCUMENT_CHARS,
    MAX_EVIDENCE_ITEMS,
    MAX_LOCATIONS,
    MAX_SINGLE_TEXT_FIELD_CHARS,
    CrimeSpec,
    CrimeTimeSpec,
    EvidenceSetSpec,
    EvidenceSpec,
    GeneratedDraft,
    LocationSpec,
    MotiveSpec,
    ObjectSpec,
    PersonSpec,
    PlacementSpec,
    PropSpec,
    PublicWorldSpec,
    SceneSpec,
    TravelRuleSpec,
    WorldGraphLocationSpec,
    WorldGraphSpec,
)

MAX_PROVIDER_OUTPUT_CHARS = 200000

_RELIABILITY_VOCABULARY = frozenset({"high", "medium", "low"})

# --- documented key allowlists per section ---------------------------------

_CASE_TRUTH_TOP = frozenset({"crime"})
_CRIME_KEYS = frozenset(
    {"type", "victimId", "murdererId", "motiveId", "weaponId", "locationId", "crimeTime"}
)
_CRIME_TIME_KEYS = frozenset({"canonical", "accusationToleranceSeconds"})
_PUBLIC_WORLD_TOP = frozenset(
    {"persons", "motives", "objects", "locations", "travelRules", "scene"}
)
_PERSON_KEYS = frozenset({"personId", "name", "role", "affordances", "presentedData"})
_MOTIVE_KEYS = frozenset({"motiveId", "label", "affordances"})
_OBJECT_KEYS = frozenset({"objectId", "assetId", "affordances", "subtype"})
_LOCATION_KEYS = frozenset({"locationId", "name"})
_TRAVEL_RULE_KEYS = frozenset({"fromLocationId", "toLocationId", "travelTimeSeconds"})
# ``environmentId`` is the Phase 11 additive player-safe kit identity: OPTIONAL
# in provider output (the golden scene has none) and parsed when present.
_SCENE_KEYS = frozenset({"locationId", "name", "environmentId"})
_EVIDENCE_TOP = frozenset({"evidence"})
_EVIDENCE_KEYS = frozenset(
    {"id", "kind", "reliability", "discoverable", "sourceRef", "propositions", "presentation"}
)
_PROPOSITION_KEYS = frozenset(
    {
        "type",
        "personId",
        "locationId",
        "objectId",
        "motiveId",
        "observedAt",
        "uncertaintySeconds",
        "structured",
    }
)
_SOURCE_REF_KEYS = frozenset({"kind", "sourceId"})
# Evidence presentation allowlist. The base contract is {title, description};
# Phase 6 adds the documented TYPED PUBLIC presentation fields of the
# player-read contract (REQUIREMENTS 40.9 / Phase6 H): the read DTO content
# allowlist per kind (object/email/financial/cctv/testimonial) is derived ONLY
# from these fields, so they must survive strict parsing. Every value is still
# bounded (MAX_SINGLE_TEXT_FIELD_CHARS) and content-safety scanned; nested
# rows/events maps are bounded per-string by the same length rule.
_PRESENTATION_KEYS = frozenset(
    {
        "title",
        "description",
        # "object" kind
        "subtype",
        "locationId",
        # "email" kind
        "fromPersonId",
        "toPersonIds",
        "subject",
        "body",
        "timestamp",
        # "financial" kind
        "rows",
        "suspicious",
        # "cctv"/"cctv_observation"/"view_record" kind
        "events",
        "cameraId",
        # "testimonial"/"witness_statement"/"statement" kind
        "speakerName",
        "statement",
    }
)
_WORLD_GRAPH_TOP = frozenset({"worldGraph"})
_WORLD_GRAPH_KEYS = frozenset({"locations", "placements"})
_WG_LOCATION_KEYS = frozenset({"locationId", "template", "rooms"})
_PLACEMENT_KEYS = frozenset(
    {"objectId", "assetId", "locationId", "anchor", "interaction", "evidenceId"}
)
_FULL_DRAFT_TOP = frozenset(
    {
        "crime",
        "persons",
        "motives",
        "objects",
        "locations",
        "travelRules",
        "scene",
        "evidence",
        "worldGraph",
    }
)


class GenerationParseError(ValueError):
    """Carries the deterministic sorted issue tuple."""

    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = tuple(sorted(set(issues)))
        super().__init__("generation parse errors: " + "; ".join(self.issues))


class _DuplicateKeyError(ValueError):
    """Raised by the JSON object-pairs hook on the FIRST repeated key (DEF-041).

    ``json.loads`` re-raises this exception type unchanged, so the parser can
    translate it into a deterministic parse issue (first duplicate in document
    order). This mirrors the stdlib behaviour that duplicate JSON keys would
    otherwise silently last-win.
    """

    def __init__(self, key: str) -> None:
        super().__init__(f"(duplicate key {key!r})")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` rejecting any repeated key anywhere in the tree."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


# ---------------------------------------------------------------------------
# generic field helpers
# ---------------------------------------------------------------------------


def _check_keys(node: Any, allowed: frozenset[str], where: str) -> list[str]:
    if not isinstance(node, dict):
        return [f"{where} must be a JSON object"]
    return [f"{where}: unknown key {key!r}" for key in sorted(set(node) - allowed)]


def _require_key(node: Any, key: str, where: str) -> list[str]:
    if not isinstance(node, dict) or key not in node:
        return [f"{where}: missing required key {key!r}"]
    return []


def _str_field(
    node: Any,
    key: str,
    where: str,
    *,
    required: bool = True,
    max_len: int = MAX_SINGLE_TEXT_FIELD_CHARS,
    allow_empty: bool = False,
) -> tuple[str | None, list[str]]:
    if not isinstance(node, dict):
        return None, [f"{where} must be a JSON object"]
    value = node.get(key)
    if value is None:
        if required:
            return None, [f"{where}: missing required key {key!r}"]
        return None, []
    if not isinstance(value, str):
        return None, [f"{where}.{key} must be a string"]
    if not value:
        # allow_empty is used for fields where the empty string is a legal
        # value (e.g. a placement interaction "" = decorative / not
        # interactable, DEF-062).
        if allow_empty:
            return "", []
        if required:
            return None, [f"{where}.{key} must be a non-empty string"]
        return None, [f"{where}.{key} must be a non-empty string when present"]
    if len(value) > max_len:
        return None, [f"{where}.{key} exceeds {max_len} chars"]
    return value, []


def _optional_str_field(
    node: Any, key: str, where: str
) -> tuple[str | None, list[str]]:
    return _str_field(node, key, where, required=False)


def _int_field(
    node: Any, key: str, where: str, *, default: int | None, min_value: int
) -> tuple[int | None, list[str]]:
    if not isinstance(node, dict):
        return None, [f"{where} must be a JSON object"]
    value = node.get(key)
    if value is None:
        if default is None:
            return None, [f"{where}: missing required key {key!r}"]
        return default, []
    if isinstance(value, bool) or not isinstance(value, int):
        return None, [f"{where}.{key} must be an integer"]
    if value < min_value:
        return None, [f"{where}.{key} must be >= {min_value}"]
    return value, []


def _bool_field(node: Any, key: str, where: str) -> tuple[bool | None, list[str]]:
    if not isinstance(node, dict):
        return None, [f"{where} must be a JSON object"]
    value = node.get(key)
    if value is None:
        return None, [f"{where}: missing required key {key!r}"]
    if not isinstance(value, bool):
        return None, [f"{where}.{key} must be a boolean"]
    return value, []


def _mapping_field(
    node: Any, key: str, where: str, *, required: bool
) -> tuple[dict | None, list[str]]:
    if not isinstance(node, dict):
        return None, [f"{where} must be a JSON object"]
    value = node.get(key)
    if value is None:
        if required:
            return None, [f"{where}: missing required key {key!r}"]
        return None, []
    if not isinstance(value, dict):
        return None, [f"{where}.{key} must be a JSON object"]
    return value, []


def _string_list_field(
    node: Any,
    key: str,
    where: str,
    *,
    required: bool,
    vocabulary: frozenset[str] | None = None,
) -> tuple[tuple[str, ...] | None, list[str]]:
    if not isinstance(node, dict):
        return (), [f"{where} must be a JSON object"]
    value = node.get(key)
    if value is None:
        if required:
            return None, [f"{where}: missing required key {key!r}"]
        return (), []
    if not isinstance(value, list):
        return None, [f"{where}.{key} must be a JSON array"]
    issues: list[str] = []
    out: list[str] = []
    for index, item in enumerate(value):
        loc = f"{where}.{key}[{index}]"
        if not isinstance(item, str) or not item:
            issues.append(f"{loc} must be a non-empty string")
            continue
        if len(item) > MAX_SINGLE_TEXT_FIELD_CHARS:
            issues.append(f"{loc} exceeds {MAX_SINGLE_TEXT_FIELD_CHARS} chars")
            continue
        if vocabulary is not None and item not in vocabulary:
            issues.append(
                f"{loc} value {item!r} is not in the documented vocabulary"
            )
            continue
        out.append(item)
    if issues:
        return None, issues
    return tuple(out), []


def _check_timestamp(value: str, where: str) -> list[str]:
    try:
        tick, _offset = parse_iso8601(value)
        assert_epoch_in_domain(tick)
    except (TypeError, ValueError) as exc:
        return [
            f"{where}: invalid or out-of-domain timestamp {value!r}: {exc}"
        ]
    return []


def _walk_mapping_string_lengths(node: Mapping[str, Any], where: str) -> list[str]:
    issues: list[str] = []
    for key, value in node.items():
        loc = f"{where}.{key}"
        if isinstance(value, str):
            if len(value) > MAX_SINGLE_TEXT_FIELD_CHARS:
                issues.append(f"{loc} exceeds {MAX_SINGLE_TEXT_FIELD_CHARS} chars")
    return issues


# ---------------------------------------------------------------------------
# section validators (issue collection + whole-tree construction)
# ---------------------------------------------------------------------------


def _validate_person(item: Any, where: str) -> tuple[PersonSpec | None, list[str]]:
    issues = _check_keys(item, _PERSON_KEYS, where)
    person_id, more = _str_field(item, "personId", where)
    issues += more
    name, more = _str_field(item, "name", where)
    issues += more
    role, more = _str_field(item, "role", where)
    issues += more
    affordances, more = _string_list_field(
        item, "affordances", where, required=True, vocabulary=AFFORDANCE_VOCABULARY
    )
    issues += more
    presented, more = _mapping_field(item, "presentedData", where, required=False)
    issues += more
    if presented:
        issues += _walk_mapping_string_lengths(presented, f"{where}.presentedData")
    if issues or person_id is None or name is None or role is None or affordances is None:
        return None, issues
    try:
        spec = PersonSpec(
            person_id=person_id,
            name=name,
            role=role,
            affordances=affordances,
            presented_data=presented or {},
        )
    except ValueError as exc:
        return None, [f"{where}: invalid person: {exc}"]
    return spec, issues


def _validate_persons(
    node: Any, where: str, *, max_items: int = MAX_CHARACTERS
) -> tuple[tuple[PersonSpec, ...], list[str]]:
    issues: list[str] = []
    if not isinstance(node, list):
        return (), issues + [f"{where} must be a JSON array"]
    if len(node) > max_items:
        issues.append(
            f"{where} exceeds MAX_CHARACTERS ({MAX_CHARACTERS}): got {len(node)}"
        )
    specs: list[PersonSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(node):
        spec, more = _validate_person(item, f"{where}[{index}]")
        issues += more
        if spec is not None:
            if spec.person_id in seen:
                issues.append(f"duplicate person id: {spec.person_id!r}")
            seen.add(spec.person_id)
            specs.append(spec)
    return tuple(specs), issues


def _validate_motive(item: Any, where: str) -> tuple[MotiveSpec | None, list[str]]:
    issues = _check_keys(item, _MOTIVE_KEYS, where)
    motive_id, more = _str_field(item, "motiveId", where)
    issues += more
    label, more = _str_field(item, "label", where)
    issues += more
    affordances, more = _string_list_field(
        item, "affordances", where, required=True, vocabulary=AFFORDANCE_VOCABULARY
    )
    issues += more
    if issues or motive_id is None or label is None or affordances is None:
        return None, issues
    try:
        spec = MotiveSpec(
            motive_id=motive_id, label=label, affordances=affordances
        )
    except ValueError as exc:
        return None, [f"{where}: invalid motive: {exc}"]
    return spec, issues


def _validate_motives(node: Any, where: str) -> tuple[tuple[MotiveSpec, ...], list[str]]:
    issues: list[str] = []
    if not isinstance(node, list):
        return (), issues + [f"{where} must be a JSON array"]
    specs: list[MotiveSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(node):
        spec, more = _validate_motive(item, f"{where}[{index}]")
        issues += more
        if spec is not None:
            if spec.motive_id in seen:
                issues.append(f"duplicate motive id: {spec.motive_id!r}")
            seen.add(spec.motive_id)
            specs.append(spec)
    return tuple(specs), issues


def _validate_object(item: Any, where: str) -> tuple[ObjectSpec | None, list[str]]:
    issues = _check_keys(item, _OBJECT_KEYS, where)
    object_id, more = _str_field(item, "objectId", where)
    issues += more
    asset_id, more = _str_field(item, "assetId", where)
    issues += more
    affordances, more = _string_list_field(
        item, "affordances", where, required=True, vocabulary=AFFORDANCE_VOCABULARY
    )
    issues += more
    subtype, more = _optional_str_field(item, "subtype", where)
    issues += more
    if issues or object_id is None or asset_id is None or affordances is None:
        return None, issues
    try:
        spec = ObjectSpec(
            object_id=object_id,
            asset_id=asset_id,
            affordances=affordances,
            subtype=subtype,
        )
    except ValueError as exc:
        return None, [f"{where}: invalid object: {exc}"]
    return spec, issues


def _validate_objects(node: Any, where: str) -> tuple[tuple[ObjectSpec, ...], list[str]]:
    issues: list[str] = []
    if not isinstance(node, list):
        return (), issues + [f"{where} must be a JSON array"]
    specs: list[ObjectSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(node):
        spec, more = _validate_object(item, f"{where}[{index}]")
        issues += more
        if spec is not None:
            if spec.object_id in seen:
                issues.append(f"duplicate object id: {spec.object_id!r}")
            seen.add(spec.object_id)
            specs.append(spec)
    return tuple(specs), issues


def _validate_location(item: Any, where: str) -> tuple[LocationSpec | None, list[str]]:
    issues = _check_keys(item, _LOCATION_KEYS, where)
    location_id, more = _str_field(item, "locationId", where)
    issues += more
    name, more = _str_field(item, "name", where)
    issues += more
    if issues or location_id is None or name is None:
        return None, issues
    try:
        spec = LocationSpec(location_id=location_id, name=name)
    except ValueError as exc:
        return None, [f"{where}: invalid location: {exc}"]
    return spec, issues


def _validate_locations(
    node: Any, where: str, *, max_items: int = MAX_LOCATIONS
) -> tuple[tuple[LocationSpec, ...], list[str]]:
    issues: list[str] = []
    if not isinstance(node, list):
        return (), issues + [f"{where} must be a JSON array"]
    if len(node) > max_items:
        issues.append(
            f"{where} exceeds MAX_LOCATIONS ({MAX_LOCATIONS}): got {len(node)}"
        )
    specs: list[LocationSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(node):
        spec, more = _validate_location(item, f"{where}[{index}]")
        issues += more
        if spec is not None:
            if spec.location_id in seen:
                issues.append(f"duplicate location id: {spec.location_id!r}")
            seen.add(spec.location_id)
            specs.append(spec)
    return tuple(specs), issues


def _validate_travel_rule(
    item: Any, where: str
) -> tuple[TravelRuleSpec | None, list[str]]:
    issues = _check_keys(item, _TRAVEL_RULE_KEYS, where)
    from_id, more = _str_field(item, "fromLocationId", where)
    issues += more
    to_id, more = _str_field(item, "toLocationId", where)
    issues += more
    seconds, more = _int_field(item, "travelTimeSeconds", where, default=None, min_value=0)
    issues += more
    if issues or from_id is None or to_id is None or seconds is None:
        return None, issues
    try:
        spec = TravelRuleSpec(
            from_location_id=from_id, to_location_id=to_id, travel_time_seconds=seconds
        )
    except ValueError as exc:
        return None, [f"{where}: invalid travel rule: {exc}"]
    return spec, issues


def _validate_travel_rules(
    node: Any, where: str
) -> tuple[tuple[TravelRuleSpec, ...], list[str]]:
    issues: list[str] = []
    if not isinstance(node, list):
        return (), issues + [f"{where} must be a JSON array"]
    specs: list[TravelRuleSpec] = []
    for index, item in enumerate(node):
        spec, more = _validate_travel_rule(item, f"{where}[{index}]")
        issues += more
        if spec is not None:
            specs.append(spec)
    return tuple(specs), issues


def _validate_scene_object(
    item: Any, where: str
) -> tuple[SceneSpec | None, list[str]]:
    issues = _check_keys(item, _SCENE_KEYS, where)
    location_id, more = _str_field(item, "locationId", where)
    issues += more
    name, more = _str_field(item, "name", where)
    issues += more
    environment_id, more = _optional_str_field(item, "environmentId", where)
    issues += more
    if issues or location_id is None or name is None:
        return None, issues
    try:
        spec = SceneSpec(
            location_id=location_id,
            name=name,
            environment_id=environment_id,
        )
    except ValueError as exc:
        return None, [f"{where}: invalid scene: {exc}"]
    return spec, issues


def _validate_crime_time(
    node: Any, where: str
) -> tuple[CrimeTimeSpec | None, list[str]]:
    issues = _check_keys(node, _CRIME_TIME_KEYS, where)
    canonical, more = _str_field(node, "canonical", where)
    issues += more
    tolerance, more = _int_field(
        node, "accusationToleranceSeconds", where, default=None, min_value=0
    )
    issues += more
    if canonical is not None:
        issues += _check_timestamp(canonical, f"{where}.canonical")
    if issues or canonical is None or tolerance is None:
        return None, issues
    try:
        spec = CrimeTimeSpec(
            canonical=canonical, accusation_tolerance_seconds=tolerance
        )
    except ValueError as exc:
        return None, [f"{where}: invalid crimeTime: {exc}"]
    return spec, issues


def _validate_crime(node: Any, where: str) -> tuple[CrimeSpec | None, list[str]]:
    issues = _check_keys(node, _CRIME_KEYS, where)
    ctype, more = _str_field(node, "type", where)
    issues += more
    victim_id, more = _str_field(node, "victimId", where)
    issues += more
    murderer_id, more = _str_field(node, "murdererId", where)
    issues += more
    motive_id, more = _str_field(node, "motiveId", where)
    issues += more
    weapon_id, more = _str_field(node, "weaponId", where)
    issues += more
    location_id, more = _str_field(node, "locationId", where)
    issues += more
    crime_time: CrimeTimeSpec | None = None
    if not isinstance(node, dict) or "crimeTime" not in node:
        issues.append(f"{where}: missing required key 'crimeTime'")
    else:
        crime_time, more = _validate_crime_time(node["crimeTime"], f"{where}.crimeTime")
        issues += more
    if issues:
        return None, issues
    try:
        spec = CrimeSpec(
            type=ctype,
            victim_id=victim_id,
            murderer_id=murderer_id,
            motive_id=motive_id,
            weapon_id=weapon_id,
            location_id=location_id,
            crime_time=crime_time,
        )
    except ValueError as exc:
        return None, [f"{where}: invalid crime: {exc}"]
    return spec, issues


def _validate_proposition(
    item: Any, where: str
) -> tuple[PropSpec | None, list[str]]:
    issues = _check_keys(item, _PROPOSITION_KEYS, where)
    ptype, more = _str_field(item, "type", where)
    issues += more
    person_id, more = _optional_str_field(item, "personId", where)
    issues += more
    location_id, more = _optional_str_field(item, "locationId", where)
    issues += more
    object_id, more = _optional_str_field(item, "objectId", where)
    issues += more
    motive_id, more = _optional_str_field(item, "motiveId", where)
    issues += more
    observed_at, more = _optional_str_field(item, "observedAt", where)
    issues += more
    uncertainty, more = _int_field(
        item, "uncertaintySeconds", where, default=0, min_value=0
    )
    issues += more
    structured, more = _mapping_field(item, "structured", where, required=False)
    issues += more
    if structured:
        issues += _walk_mapping_string_lengths(structured, f"{where}.structured")
    if issues or ptype is None or uncertainty is None:
        return None, issues
    if observed_at is not None:
        issues += _check_timestamp(observed_at, f"{where}.observedAt")
    if ptype not in PROPOSITION_TYPES:
        issues.append(f"{where}: unknown proposition type {ptype!r}")
    structured_map: dict = structured or {}
    if ptype == FORENSIC_WEAPON_MATCH:
        match = structured_map.get("match")
        if not isinstance(match, bool):
            issues.append(
                f"{where}: FORENSIC_WEAPON_MATCH requires structured 'match' to be "
                "a real bool"
            )
    elif ptype == ALIBI_TIME_CLAIM:
        claimed = structured_map.get("claimedDeparture")
        if not isinstance(claimed, str) or not claimed:
            issues.append(
                f"{where}: ALIBI_TIME_CLAIM requires structured 'claimedDeparture' "
                "(non-empty ISO-8601 string)"
            )
        else:
            issues += _check_timestamp(claimed, f"{where}.structured.claimedDeparture")
    if issues:
        return None, issues
    try:
        spec = PropSpec(
            type=ptype,
            person_id=person_id,
            location_id=location_id,
            object_id=object_id,
            motive_id=motive_id,
            observed_at=observed_at,
            uncertainty_seconds=uncertainty,
            structured=structured_map,
        )
    except ValueError as exc:
        return None, [f"{where}: invalid proposition: {exc}"]
    return spec, issues


def _validate_evidence(item: Any, where: str) -> tuple[EvidenceSpec | None, list[str]]:
    issues = _check_keys(item, _EVIDENCE_KEYS, where)
    eid, more = _str_field(item, "id", where)
    issues += more
    kind, more = _str_field(item, "kind", where)
    issues += more
    reliability, more = _str_field(item, "reliability", where)
    issues += more
    if reliability is not None and reliability not in _RELIABILITY_VOCABULARY:
        issues.append(
            f"{where}.reliability must be one of high|medium|low; got "
            f"{reliability!r}"
        )
    discoverable, more = _bool_field(item, "discoverable", where)
    issues += more

    propositions_node: Any = None
    if not isinstance(item, dict) or "propositions" not in item:
        issues.append(f"{where}: missing required key 'propositions'")
    else:
        propositions_node = item["propositions"]
        if not isinstance(propositions_node, list) or not propositions_node:
            issues.append(f"{where}.propositions must be a non-empty JSON array")

    source_ref: dict | None = None
    if not isinstance(item, dict) or "sourceRef" not in item:
        issues.append(f"{where}: missing required key 'sourceRef'")
    elif not isinstance(item["sourceRef"], dict):
        issues.append(f"{where}.sourceRef must be a JSON object")
    else:
        source_ref = item["sourceRef"]
        issues += _check_keys(source_ref, _SOURCE_REF_KEYS, f"{where}.sourceRef")
        _kind, more = _str_field(source_ref, "kind", f"{where}.sourceRef")
        issues += more
        _sid, more = _str_field(source_ref, "sourceId", f"{where}.sourceRef")
        issues += more

    presentation: dict | None = None
    if not isinstance(item, dict) or "presentation" not in item:
        issues.append(f"{where}: missing required key 'presentation'")
    elif not isinstance(item["presentation"], dict):
        issues.append(f"{where}.presentation must be a JSON object")
    else:
        presentation = item["presentation"]
        issues += _check_keys(presentation, _PRESENTATION_KEYS, f"{where}.presentation")
        # Every top-level presentation string (incl. the Phase 6 typed public
        # fields such as body/statement/subject) is length-bounded per
        # REQUIREMENTS 32.8 (MAX_SINGLE_TEXT_FIELD_CHARS).
        issues += _walk_mapping_string_lengths(presentation, f"{where}.presentation")
        title, more = _str_field(presentation, "title", f"{where}.presentation")
        issues += more
        description, more = _str_field(
            presentation, "description", f"{where}.presentation", max_len=MAX_DOCUMENT_CHARS
        )
        issues += more

    props: list[PropSpec] = []
    if isinstance(propositions_node, list):
        for index, prop_node in enumerate(propositions_node):
            spec, more = _validate_proposition(
                prop_node, f"{where}.propositions[{index}]"
            )
            issues += more
            if spec is not None:
                props.append(spec)
    if issues or eid is None or kind is None or reliability is None or discoverable is None:
        return None, issues
    try:
        spec = EvidenceSpec(
            id=eid,
            kind=kind,
            propositions=tuple(props),
            source_ref=source_ref or None,
            reliability=reliability,
            presentation=presentation or {},
            discoverable=discoverable,
        )
    except ValueError as exc:
        return None, [f"{where}: invalid evidence: {exc}"]
    return spec, issues


def _validate_evidence_items(
    node: Any, where: str, *, max_items: int = MAX_EVIDENCE_ITEMS
) -> tuple[tuple[EvidenceSpec, ...], list[str]]:
    issues: list[str] = []
    if not isinstance(node, list):
        return (), issues + [f"{where} must be a JSON array"]
    if len(node) > max_items:
        issues.append(
            f"{where} exceeds MAX_EVIDENCE_ITEMS ({MAX_EVIDENCE_ITEMS}): got {len(node)}"
        )
    specs: list[EvidenceSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(node):
        spec, more = _validate_evidence(item, f"{where}[{index}]")
        issues += more
        if spec is not None:
            if spec.id in seen:
                issues.append(f"duplicate evidence id: {spec.id!r}")
            seen.add(spec.id)
            specs.append(spec)
    return tuple(specs), issues


def _validate_wg_location(
    item: Any, where: str
) -> tuple[WorldGraphLocationSpec | None, list[str]]:
    issues = _check_keys(item, _WG_LOCATION_KEYS, where)
    location_id, more = _str_field(item, "locationId", where)
    issues += more
    template, more = _str_field(item, "template", where)
    issues += more
    rooms, more = _string_list_field(item, "rooms", where, required=False)
    issues += more
    if issues or location_id is None or template is None or rooms is None:
        return None, issues
    try:
        spec = WorldGraphLocationSpec(
            location_id=location_id, template=template, rooms=rooms
        )
    except ValueError as exc:
        return None, [f"{where}: invalid world-graph location: {exc}"]
    return spec, issues


def _validate_placement(
    item: Any, where: str
) -> tuple[PlacementSpec | None, list[str]]:
    issues = _check_keys(item, _PLACEMENT_KEYS, where)
    object_id, more = _str_field(item, "objectId", where)
    issues += more
    asset_id, more = _str_field(item, "assetId", where)
    issues += more
    location_id, more = _str_field(item, "locationId", where)
    issues += more
    anchor, more = _str_field(item, "anchor", where)
    issues += more
    interaction, more = _str_field(
        item, "interaction", where, allow_empty=True
    )
    issues += more
    evidence_id, more = _optional_str_field(item, "evidenceId", where)
    issues += more
    if (
        issues
        or object_id is None
        or asset_id is None
        or location_id is None
        or anchor is None
        or interaction is None
    ):
        return None, issues
    try:
        spec = PlacementSpec(
            object_id=object_id,
            asset_id=asset_id,
            location_id=location_id,
            anchor=anchor,
            interaction=interaction,
            evidence_id=evidence_id,
        )
    except ValueError as exc:
        return None, [f"{where}: invalid placement: {exc}"]
    return spec, issues


# ---------------------------------------------------------------------------
# stage-level validators
# ---------------------------------------------------------------------------


def _validate_case_truth(data: Any) -> tuple[CrimeSpec | None, list[str]]:
    issues = _check_keys(data, _CASE_TRUTH_TOP, "case_truth")
    crime: CrimeSpec | None = None
    if not isinstance(data, dict) or "crime" not in data:
        issues.append("case_truth: missing required key 'crime'")
    else:
        crime, more = _validate_crime(data["crime"], "case_truth.crime")
        issues += more
    if issues:
        return None, issues
    return crime, []


def _validate_public_world(data: Any) -> tuple[PublicWorldSpec | None, list[str]]:
    issues = _check_keys(data, _PUBLIC_WORLD_TOP, "public_world")
    if not isinstance(data, dict):
        return None, issues
    persons: tuple = ()
    motives: tuple = ()
    objects: tuple = ()
    locations: tuple = ()
    travel_rules: tuple = ()
    scene: SceneSpec | None = None
    for key, walker, where in (
        ("persons", _validate_persons, "public_world.persons"),
        ("motives", _validate_motives, "public_world.motives"),
        ("objects", _validate_objects, "public_world.objects"),
        ("locations", _validate_locations, "public_world.locations"),
        ("travelRules", _validate_travel_rules, "public_world.travelRules"),
    ):
        if key not in data:
            issues.append(f"public_world: missing required key {key!r}")
            continue
        value, more = walker(data[key], where)
        issues += more
        if key == "persons":
            persons = value
        elif key == "motives":
            motives = value
        elif key == "objects":
            objects = value
        elif key == "locations":
            locations = value
        else:
            travel_rules = value
    if "scene" not in data:
        issues.append("public_world: missing required key 'scene'")
    else:
        scene, more = _validate_scene_object(data["scene"], "public_world.scene")
        issues += more
    if issues:
        return None, issues
    try:
        spec = PublicWorldSpec(
            persons=persons,
            motives=motives,
            objects=objects,
            locations=locations,
            travel_rules=travel_rules,
            scene=scene,
        )
    except ValueError as exc:
        return None, [f"public_world: invalid world: {exc}"]
    return spec, []


def _validate_evidence_set(data: Any) -> tuple[EvidenceSetSpec | None, list[str]]:
    issues = _check_keys(data, _EVIDENCE_TOP, "evidence")
    if not isinstance(data, dict):
        return None, issues
    if "evidence" not in data:
        return None, issues + ["evidence: missing required key 'evidence'"]
    entry, more = _validate_evidence_items(data["evidence"], "evidence.evidence")
    issues += more
    if issues:
        return None, issues
    try:
        spec = EvidenceSetSpec(evidence=entry)
    except ValueError as exc:
        return None, [f"evidence: invalid evidence set: {exc}"]
    return spec, []


def _validate_world_graph(data: Any) -> tuple[WorldGraphSpec | None, list[str]]:
    issues = _check_keys(data, _WORLD_GRAPH_TOP, "world_graph")
    if not isinstance(data, dict):
        return None, issues
    if "worldGraph" not in data:
        return None, issues + ["world_graph: missing required key 'worldGraph'"]
    wg = data["worldGraph"]
    if not isinstance(wg, dict):
        return None, issues + ["world_graph.worldGraph must be a JSON object"]
    issues += _check_keys(wg, _WORLD_GRAPH_KEYS, "world_graph.worldGraph")
    locations: tuple = ()
    placements: tuple = ()
    if "locations" not in wg:
        issues.append("world_graph.worldGraph: missing required key 'locations'")
    else:
        locs: list[WorldGraphLocationSpec] = []
        if not isinstance(wg["locations"], list):
            issues.append("world_graph.worldGraph.locations must be a JSON array")
        else:
            for index, item in enumerate(wg["locations"]):
                spec, more = _validate_wg_location(
                    item, f"world_graph.worldGraph.locations[{index}]"
                )
                issues += more
                if spec is not None:
                    locs.append(spec)
        locations = tuple(locs)
    if "placements" not in wg:
        issues.append("world_graph.worldGraph: missing required key 'placements'")
    else:
        placed: list[PlacementSpec] = []
        if not isinstance(wg["placements"], list):
            issues.append("world_graph.worldGraph.placements must be a JSON array")
        else:
            for index, item in enumerate(wg["placements"]):
                spec, more = _validate_placement(
                    item, f"world_graph.worldGraph.placements[{index}]"
                )
                issues += more
                if spec is not None:
                    placed.append(spec)
        placements = tuple(placed)
    if issues:
        return None, issues
    try:
        spec = WorldGraphSpec(locations=locations, placements=placements)
    except ValueError as exc:
        return None, [f"world_graph: invalid world graph: {exc}"]
    return spec, []


def _validate_full_draft(data: Any) -> tuple[GeneratedDraft | None, list[str]]:
    issues = _check_keys(data, _FULL_DRAFT_TOP, "draft")
    if not isinstance(data, dict):
        return None, issues
    crime: CrimeSpec | None = None
    if "crime" not in data:
        issues.append("draft: missing required key 'crime'")
    else:
        crime, more = _validate_crime(data["crime"], "draft.crime")
        issues += more
    sections: dict[str, tuple] = {}
    for key, walker, where, fallback in (
        ("persons", _validate_persons, "draft.persons", ()),
        ("motives", _validate_motives, "draft.motives", ()),
        ("objects", _validate_objects, "draft.objects", ()),
        ("locations", _validate_locations, "draft.locations", ()),
        ("travelRules", _validate_travel_rules, "draft.travelRules", ()),
        ("evidence", _validate_evidence_items, "draft.evidence", ()),
    ):
        if key not in data:
            issues.append(f"draft: missing required key {key!r}")
            sections[key] = fallback
            continue
        value, more = walker(data[key], where)
        issues += more
        sections[key] = value
    scene: SceneSpec | None = None
    if "scene" not in data:
        issues.append("draft: missing required key 'scene'")
    else:
        scene, more = _validate_scene_object(data["scene"], "draft.scene")
        issues += more
    world_graph: WorldGraphSpec | None = None
    if "worldGraph" not in data:
        issues.append("draft: missing required key 'worldGraph'")
    else:
        wg_payload, more = _validate_world_graph({"worldGraph": data["worldGraph"]})
        issues += more
        if wg_payload is not None:
            world_graph = wg_payload
    if issues or crime is None or world_graph is None:
        return None, issues
    try:
        draft = GeneratedDraft(
            crime=crime,
            persons=sections["persons"],
            motives=sections["motives"],
            objects=sections["objects"],
            locations=sections["locations"],
            travel_rules=sections["travelRules"],
            scene=scene,
            evidence=sections["evidence"],
            world_graph=world_graph,
        )
    except ValueError as exc:
        return None, [f"draft: invalid generated draft: {exc}"]
    return draft, []


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


def _parse_document(content: str) -> tuple[dict | None, list[str]]:
    issues: list[str] = []
    if not isinstance(content, str):
        return None, ["provider output must be a string"]
    if not content.strip():
        return None, ["provider output is empty or whitespace"]
    if len(content) > MAX_PROVIDER_OUTPUT_CHARS:
        issues.append(
            f"provider output exceeds MAX_PROVIDER_OUTPUT_CHARS "
            f"({MAX_PROVIDER_OUTPUT_CHARS}): got {len(content)} chars"
        )
    try:
        # PD-SEC-09: bounded depth preflight BEFORE the decoder — a deep
        # nested document raises BoundedJsonError (a ValueError) instead of an
        # uncaught RecursionError. Duplicate keys are still rejected via the
        # same hook.
        data = bounded_json_loads(
            content, object_pairs_hook=_reject_duplicate_keys, limit=MAX_STRUCT_NESTING
        )
    except _DuplicateKeyError as exc:
        # Deterministic first-duplicate reporting (DEF-041/ADV-128): JSON
        # silently last-wins on duplicate keys, which must never reach the
        # typed models. Kept BEFORE the generic JSON error handler so the
        # duplicate-key issue survives.
        return None, issues + [str(exc)]
    except (json.JSONDecodeError, ValueError) as exc:
        message = getattr(exc, "msg", None) or str(exc)
        if len(message) > 120:
            message = message[:120] + "..."
        # Keep any earlier (e.g. length) issues; a short non-JSON payload
        # still yields the documented single JSON issue. BoundedJsonError is a
        # ValueError subclass, so a depth-bomb lands here too (sanitized:
        # only the documented bound is ever mentioned).
        return None, issues + [f"provider output is not valid JSON: {message}"]
    if not isinstance(data, dict):
        return None, issues + ["provider output root must be a JSON object"]
    return data, issues


def collect_issues(stage: GenerationStage, content: str) -> tuple[str, ...]:
    """Non-raising issue collector (used by validation diagnostics)."""
    if stage == GenerationStage.REPAIR:
        return collect_full_draft_issues(content)
    data, issues = _parse_document(content)
    if data is None:
        return tuple(sorted(set(issues)))
    if stage == GenerationStage.CASE_TRUTH:
        _spec, more = _validate_case_truth(data)
    elif stage == GenerationStage.PUBLIC_WORLD:
        _spec, more = _validate_public_world(data)
    elif stage == GenerationStage.EVIDENCE:
        _spec, more = _validate_evidence_set(data)
    elif stage == GenerationStage.WORLD_GRAPH:
        _spec, more = _validate_world_graph(data)
    else:  # pragma: no cover — GenerationStage is a closed enum
        more = [f"unsupported stage: {stage!r}"]
    issues += more
    return tuple(sorted(set(issues)))


def collect_full_draft_issues(content: str) -> tuple[str, ...]:
    """Non-raising issue collector for a REPAIR (full-draft) provider output."""
    data, issues = _parse_document(content)
    if data is None:
        return tuple(sorted(set(issues)))
    _spec, more = _validate_full_draft(data)
    issues += more
    return tuple(sorted(set(issues)))


def _finalize(
    spec: Any, issues: list[str], non_throwing: bool
) -> Any:
    final = tuple(sorted(set(issues)))
    if final:
        if non_throwing:
            return None
        raise GenerationParseError(final)
    return spec


def parse_stage(
    stage: GenerationStage, content: str, *, non_throwing: bool = True
) -> "GenerationStageOutput | None":
    """Parse one stage's provider output into its typed spec (or None).

    ``non_throwing=False`` raises ``GenerationParseError`` (carrying ``.issues``)
    instead of returning ``None`` when issues exist.
    """
    if stage == GenerationStage.REPAIR:
        return parse_full_draft(content, non_throwing=non_throwing)
    data, issues = _parse_document(content)
    spec: Any = None
    if data is not None:
        if stage == GenerationStage.CASE_TRUTH:
            spec, more = _validate_case_truth(data)
        elif stage == GenerationStage.PUBLIC_WORLD:
            spec, more = _validate_public_world(data)
        elif stage == GenerationStage.EVIDENCE:
            spec, more = _validate_evidence_set(data)
        elif stage == GenerationStage.WORLD_GRAPH:
            spec, more = _validate_world_graph(data)
        else:  # pragma: no cover — GenerationStage is a closed enum
            more = [f"unsupported stage: {stage!r}"]
        issues += more
    return _finalize(spec, issues, non_throwing)


def parse_full_draft(
    content: str, *, non_throwing: bool = True
) -> "GeneratedDraft | None":
    """Parse a REPAIR (full-draft) provider output into a ``GeneratedDraft``.

    ``non_throwing=False`` raises ``GenerationParseError`` instead of returning
    ``None`` when issues exist.
    """
    data, issues = _parse_document(content)
    spec: Any = None
    if data is not None:
        spec, more = _validate_full_draft(data)
        issues += more
    return _finalize(spec, issues, non_throwing)
