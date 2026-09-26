"""Versioned prompt templates for the local-Llama stage driver (Phase 16_2).

These are STATIC application-owned strings (bounded, never player-facing, never
serialized into any DTO). They are the ONLY place the Ollama stage driver
derives its prompts; the deterministic engine remains authoritative over every
value they request.

Design rules (frozen contract):

- VERSIONED: every template is a module constant named ``..._v1`` (the version
  is embedded verbatim in the template text so the provider request can record
  which template version produced it).
- SCHEMA-DERIVED: the per-stage JSON skeletons are rendered at build time from
  the ACTUAL authoritative schema constants (``app.generation.schemas`` bounds
  and ``app.assets.specs`` bounds) — never a hand-maintained approximation. The
  ``schema_contract`` helper reads the frozen bound constants and turns them
  into deterministic JSON so model output and the strict parser can never drift
  from the real schema. A unit test asserts the rendered contract matches the
  authoritative bounds (no drift).
- LOCKED: templates embed the sanitized user prompt, the locked constraints,
  "no truth / no secrets / no internals" instructions, explicit meter units
  (``0.25 means 25 centimeters; 25 means 25 meters``), bounds ranges, the
  material allowlist (from ``app.assets.materials.MATERIAL_VOCAB``) and the
  primitive allowlist (Phase 13 exports). The repair templates additionally
  embed the ORIGINAL object concept / previous candidate / SANITIZED structured
  issues / exact schema / bounds / materials / units and the exact repair
  instruction from Phase16_2 §9/§13.
- Templates record (internally, as text) the template version + stage + model +
  attempt id — never sent to the player.

Placeholders use ``__UPPER_SNAKE__`` tokens substituted with ``.replace()`` so
the JSON braces embedded by ``schema_contract`` never collide with the template
mechanism.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from app.assets.catalog import CATEGORY_ALLOWLIST
from app.assets.materials import MATERIAL_VOCAB
from app.assets.specs import (
    DIMENSION_MAX,
    DIMENSION_MIN,
    MAX_PART_ROLE_LENGTH,
    MAX_PARTS,
    MAX_POSITION_BOUND,
    MAX_ROTATION_BOUND,
    MAX_PART_SCALE,
    MIN_PART_SCALE,
    PRIMITIVE_ALLOWLIST,
)
from app.domain.activity_log import (
    ACTIVITY_LOG_ACTIVITY_TYPES,
    MAX_ACTIVITY_TEXT_CHARS,
    MAX_ACTIVITY_LOG_ENTRIES,
    MIN_ACTIVITY_LOG_ENTRIES,
    WINDOW_DEFAULT_AFTER_MINUTES,
    WINDOW_DEFAULT_BEFORE_MINUTES,
    WINDOW_HARD_MAX_TOTAL_MINUTES,
    activity_log_window_bounds,
    entity_leak_tokens,
)
from app.domain.evidence import PROPOSITION_TYPES
from app.domain.time_interval import epoch_to_iso, parse_iso8601
from app.world.environment import ENVIRONMENT_IDS

# --- schema-contract builder (deterministic, authoritative) ----------------
#
# ``schema_contract`` renders the JSON skeleton the stage templates embed. It
# reads ONLY the frozen constants (number bounds, allowlists) from the
# authoritative schema modules so the prompt and the strict parser share one
# source of truth. Every bound in the rendered text is literally the constant
# value (interpolated, never a copy).

_PROPOSITION_TYPE_HINT_PREFIX = "use EXACTLY one of the proposition type tokens:"

# The closed ``environmentHint`` enum (Phase 19 Fix A). The hint text carries
# this exact ``enum <tokens>`` marker so the derived transport JSON Schema
# constrains ``environmentHint`` to the five canonical ids (the model can never
# suggest a path-like or free-text location).
#
# Phase 19B ADV-214: the hint text deliberately NEVER contains the literal
# word "null" — ``_key_required``/``_hint_nullable`` treat any hint mentioning
# "null" as a nullable value-slot, which would demote ``environmentHint`` from
# REQUIRED to OPTIONAL in the transport JSON Schema (letting structured-output
# mode silently omit the location — the exact determinism loss Fix A removed).
# The enum constraint AND the requiredness both stay authoritative.
_ENVIRONMENT_ENUM_HINT = (
    "canonical environment token; enum " + ",".join(ENVIRONMENT_IDS) +
    " (use EXACTLY one of those EXACT tokens — never a path, never a location "
    "phrase like 'hotel suite', never a free text word)"
)


def _canonical_concept_sentence(concept: str | None) -> str:
    """Deterministic application-owned canonical (category, subtype) sentence.

    Phase17D B: the AssetSpec prompts state the EXPECTED category/subtype for
    a concept the application owns (``geometry_quality`` phase §6 mapping) so
    a Hermes-class model never leaves ``subtype`` null for a crime-critical
    ceremonial object (the semantic gate then passes on the first pass).
    Returns an empty sentence for unmapped concepts.
    """
    from app.assets.geometry_quality import normalized_category_subtype

    canonical = normalized_category_subtype(concept) if concept else None
    if canonical is None:
        return ""
    expected_category, expected_subtype = canonical
    return (
        f"\nFor the object concept {concept!r} the application REQUIRES "
        f"category {expected_category!r} and subtype {expected_subtype!r} — "
        "declare those EXACT values (never null, never a different token)."
    )

# Meter-units statement used by every numeric contract (Phase16_2 §15).
# Phase17B: the worked examples make the meter/centimeter mapping concrete so a
# Hermes-class model cannot return "25" for a 25-centimetre value.
_METERS_STATEMENT = (
    "ALL dimensions, positions, rotations and scales are in METERS. "
    "0.25 means 25 centimeters; 25 means 25 meters. Use 0.001..4 for "
    "dimensions and 0.001..2 for part scale.\n"
    "METER WORKED EXAMPLES: an ice pick is about 0.25 meters long overall "
    "(declare dimensions that COVER all its parts, e.g. x: 0.05, y: 0.3, "
    "z: 0.05 — never a smaller box than the parts need); a desk lamp is "
    "about 0.2 by 0.4 by 0.2 meters; a coin is about 0.02 (write 0.02, never "
    "2). NEVER write 25 for a 25-centimeter value: write 0.25.\n"
    "THIN OBJECTS: thin shafts and blades ARE realistic — 0.002 to 0.01 "
    "(2-10 millimeters) is a correct thickness for an ice pick spike, a "
    "blade or a letter opener. Write the thin axis as a small number like "
    "0.005 or 0.01, never 0, and never scale the whole object up to make "
    "it thick.\n"
    "DECLARED-DIMENSIONS RULE: the declared dimensions are the object's "
    "TRUE overall bounding box in meters. They MUST be large enough to cover "
    "every part you place (the longest part extent fits inside them) and the "
    "part layout must actually reach near those bounds — never declare a "
    "tiny 0.05m box and then place parts 0.3m apart."
)


def __dimensions_contract() -> Mapping[str, Any]:
    return {
        "dimensions": {
            "$note": "overall bounding box in METERS",
            "x": f"number in [{DIMENSION_MIN:g}, {DIMENSION_MAX:g}]",
            "y": f"number in [{DIMENSION_MIN:g}, {DIMENSION_MAX:g}]",
            "z": f"number in [{DIMENSION_MIN:g}, {DIMENSION_MAX:g}]",
        },
        "parts": {
            "$note": f"1..{MAX_PARTS} parts; unique part ids",
            "maxParts": MAX_PARTS,
            "partSchema": {
                "id": "unique id like part_00 .. part_23",
                "role": f"lowercase letters/underscores, 1..{MAX_PART_ROLE_LENGTH} chars",
                "primitive": f"one of {sorted(PRIMITIVE_ALLOWLIST)}",
                "transform": {
                    "position": f"[x,y,z] with |axis| <= {MAX_POSITION_BOUND:g}",
                    "rotation": f"[x,y,z] with |axis| <= {MAX_ROTATION_BOUND:g}",
                    "scale": f"[x,y,z] with each axis in [{MIN_PART_SCALE:g}, {MAX_PART_SCALE:g}]",
                },
                "material": f"one of {sorted(MATERIAL_VOCAB)}",
                "parentId": "null or the id of an EARLIER part (max parent depth 2)",
            },
        },
        "category": f"one of {list(CATEGORY_ALLOWLIST)}",
    }


def _stage_contract(stage: str) -> Mapping[str, Any]:
    """THE single authoritative per-stage schema skeleton.

    Both the rendered prompt contract (``schema_contract``) and the derived
    transport JSON Schema (``schema_contract_as_json_schema``) read this one
    mapping — the prompt and the structured-output ``format`` can never drift
    from each other (Phase17B §2: one source, never a duplicate).
    """
    if stage == "full_draft":
        return _full_draft_contract()
    if stage == "asset_spec":
        contract: Mapping[str, Any] = {
            "canonicalName": "non-empty string (80 chars max)",
            "category": f"one of {list(CATEGORY_ALLOWLIST)}",
            "subtype": "string or null",
            **__dimensions_contract(),
        }
    elif stage == "activity_log":
        # Phase 19J — closed structured activity-log schema. The closed enum
        # marker derives the transport JSON Schema ``enum`` for Ollama
        # structured output; the strict Phase 19J validator stays the sole
        # acceptance authority. The provider NEVER emits HTML/tables.
        # Phase19J-RI: the derived transport JSON Schema ALSO carries the hard
        # entry-count bounds (``minItems``/``maxItems`` from the authoritative
        # MIN_ACTIVITY_LOG_ENTRIES/MAX_ACTIVITY_LOG_ENTRIES constants) so the
        # Ollama grammar itself can never emit a one-row repair wrapper (the
        # observed 153/186-byte repair failures). Both ACTIVITY_LOG and
        # ACTIVITY_LOG_REPAIR share this ONE contract via STAGE_TO_CONTRACT, so
        # the bounds apply to both stages automatically.
        contract = {
            "entries": {
                "$note": (
                    f"array of {MIN_ACTIVITY_LOG_ENTRIES}.."
                    f"{MAX_ACTIVITY_LOG_ENTRIES} chronological entries"
                ),
                "minItems": MIN_ACTIVITY_LOG_ENTRIES,
                "maxItems": MAX_ACTIVITY_LOG_ENTRIES,
                "entrySchema": {
                    "timestamp": "ISO-8601 timestamp WITH timezone offset "
                        "(e.g. 2026-09-11T21:18:00+02:00 or 2026-09-11T20:18:00Z)",
                    "activityType": (
                        "enum "
                        + ",".join(sorted(ACTIVITY_LOG_ACTIVITY_TYPES))
                        + " (use EXACTLY one of those EXACT tokens, never "
                        "an invented/lowercase token)"
                    ),
                    "activity": (
                        f"plain short text, 1..{MAX_ACTIVITY_TEXT_CHARS} "
                        "characters, no HTML, no URLs, no file paths, no "
                        "control characters, no person/weapon/motive/location "
                        "names, no crime wording"
                    ),
                },
            }
        }
    elif stage == "evidence":
        contract = {
            "evidence": [
                {
                    "id": "unique id",
                    "kind": "email|physical|financial|cctv|testimonial|forensic|witness_statement",
                    "reliability": "high|medium|low",
                    "discoverable": "bool",
                    "sourceRef": {"kind": "string", "sourceId": "string"},
                    "propositions": [
                        {
                            "type": (
                                "use EXACTLY one of the proposition type "
                                "tokens: PERSON_OBSERVED_AT_LOCATION, "
                                "VICTIM_LAST_SEEN_ALIVE_AT, BODY_FIRST_FOUND_AT, "
                                "NOISE_HEARD_AT, CRIME_SCENE_OBSERVATION_AT, "
                                "WITNESS_CLAIMS, SUSPECT_CLAIMS, ALIBI_TIME_CLAIM, "
                                "OBJECT_CONTAINS_FINGERPRINT, "
                                "OBJECT_CONTAINS_BLOOD, FORENSIC_WEAPON_MATCH, "
                                "MOTIVE_LINKED_TO_PERSON, "
                                "MOTIVE_FACT_CONTRADICTED, "
                                "CAN_REACH_CRIME_SCENE_IN_TIME, "
                                "TIME_WINDOW_EXCLUSION, OTHER — never an "
                                "invented/lowercase/natural-language token, "
                                "never a truth/final verdict declaration"
                            ),
                            "personId": ("[REQUIRED] person id or null — "
                                "ALWAYS present as a key; fill with the locked "
                                "person id when the fact names a person"),
                            "locationId": ("[REQUIRED] location id or null — "
                                "ALWAYS present as a key; fill with the locked "
                                "location id when the fact names a location"),
                            "objectId": ("[REQUIRED] object id or null — "
                                "ALWAYS present as a key; fill with the locked "
                                "weapon/object id when the fact names an object"),
                            "motiveId": ("[REQUIRED] motive id or null — "
                                "ALWAYS present as a key; fill with the motive "
                                "id when the fact names a motive"),
                            "observedAt": "ISO-8601 timestamp or null",
                            "uncertaintySeconds": "non-negative int",
                            "structured": (
                                "a JSON object (nested typed fields like "
                                "personId/observedAt/locationId/objectId/match; "
                                "NEVER a JSON-encoded string)"
                            ),
                        }
                    ],
                    "presentation": {"title": "string", "description": "string"},
                }
            ]
        }
    elif stage == "world_requirements":
        contract = {
            "environmentHint": _ENVIRONMENT_ENUM_HINT,
            "locationTokens": ["matched location token strings"],
            "objects": [
                {
                    "name": "physical object noun",
                    "categoryHint": "category or null",
                    "subtypeHint": "subtype or null",
                    "tags": ["tag strings"],
                    "requiredInteraction": "inspect|read or null",
                    "evidenceId": "evidence id or null",
                    "criticality": "required|decorative",
                }
            ],
            "relations": [
                {
                    "kind": "on_desk|on_table|near_victim|inside_cabinet|floor_area|on_wall",
                    "target": "the casefolded object name this relation binds",
                }
            ],
            "unsafeUnsupported": ("short diagnostic notes, each at most 120 "
                "characters (usually an empty list; never long prose)"),
        }
    else:  # case_people
        contract = {
            "crime": {
                "type": "string (e.g. murder)",
                "victimId": "person id",
                "murdererId": "person id",
                "motiveId": "motive id",
                "weaponId": "object id",
                "locationId": "location id",
                "crimeTime": {
                    "canonical": "ISO-8601 timestamp",
                    "accusationToleranceSeconds": "non-negative int",
                },
            },
            "persons": [
                {
                    "personId": "unique id",
                    "name": "display name",
                    "role": "victim|suspect|witness|family|other",
                    "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER", "INSPECTABLE"],
                }
            ],
            "motives": [
                {"motiveId": "unique id", "label": "motive phrase", "affordances": ["MOTIVE_CANDIDATE"]}
            ],
            "locations": [{"locationId": "unique id", "name": "location name"}],
            "travelRules": [
                {
                    "fromLocationId": "location id",
                    "toLocationId": "location id",
                    "travelTimeSeconds": "non-negative int",
                }
            ],
            "scene": {"locationId": "location id", "name": "scene name"},
        }
    return contract


def _full_draft_contract() -> Mapping[str, Any]:
    """The authoritative FULL-DRAFT REPAIR contract (Phase17D C §2).

    Derived from the SAME section contracts the stage templates embed (the
    ``crime`` / ``persons`` / ``motives`` / ``locations`` / ``travelRules`` /
    ``scene`` sections of case_people, the ``evidence`` section of the
    evidence contract) plus the application-owned public-object and
    world-graph sections — one source of truth with the strict full-draft
    parser (``app.generation.parser`` ``_FULL_DRAFT_TOP``: crime, persons,
    motives, objects, locations, travelRules, scene, evidence, worldGraph).

    REPAIR has NO partial-patch semantics: the response must be the COMPLETE
    repaired draft, so every top-level key is required in the contract and
    in the transport JSON Schema.
    """
    case = _stage_contract("case_people")
    evidence = _stage_contract("evidence")
    return {
        "crime": case["crime"],
        "persons": case["persons"],
        "motives": case["motives"],
        "objects": [
            {
                "objectId": "unique object id",
                "assetId": "asset id or object id",
                "affordances": ["INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"],
                "subtype": "subtype or null",
            }
        ],
        "locations": case["locations"],
        "travelRules": case["travelRules"],
        "scene": case["scene"],
        "evidence": evidence["evidence"],
        "worldGraph": {
            "locations": [
                {
                    "locationId": "location id",
                    "template": "kit template token",
                    "rooms": ["room tokens"],
                }
            ],
            "placements": [
                {
                    "objectId": "object id",
                    "assetId": "asset id",
                    "locationId": "location id",
                    "anchor": "anchor token",
                    "interaction": "interaction string (or null)",
                    "evidenceId": "evidence id or null",
                }
            ],
        },
    }


def _activity_log_prompt_contract() -> Mapping[str, Any]:
    """The PROMPT-FACING activity-log contract: the unambiguous ARRAY shape.

    Phase19J-RI (ADV-A): the TRANSPORT JSON Schema (``schema_contract_as_json_schema``)
    intentionally carries the enriched directive object (``minItems=15`` /
    ``maxItems=20`` / ``entrySchema``) so the Ollama grammar itself bounds the
    entry count — but embedding THAT object verbatim in the prompt as the
    "EXACT schema" made a literal-copying model emit ``entries`` as an OBJECT
    instead of the ARRAY the transport schema and the worked example show.
    The prompt therefore renders the plain, unambiguous array illustration
    (a list containing one entry object, exactly like the pre-Phase19J-RI
    contract) derived from the SAME authoritative directive contract's
    ``entrySchema`` strings — one source, never a duplicate.
    """
    directive = _stage_contract("activity_log")["entries"]
    entry_schema = directive["entrySchema"]
    return {
        "entries": [
            {
                "timestamp": entry_schema["timestamp"],
                "activityType": entry_schema["activityType"],
                "activity": entry_schema["activity"],
            }
        ],
    }


def schema_contract(stage: str) -> str:
    """Deterministic JSON text of the per-stage schema skeleton (authoritative).

    ``stage`` is one of ``case_people`` / ``evidence`` / ``world_requirements`` /
    ``asset_spec`` / ``full_draft``. The returned text carries the exact numeric
    bounds from the authoritative schema constants. A unit test asserts the
    rendered values equal the constants (schema-drift guard). Rendered from the
    single ``_stage_contract`` source (Phase17B: the transport JSON Schema and
    the prompt share this mapping — no duplicate).

    Phase19J-RI (ADV-A): for ``activity_log`` (the ONLY stage whose contract
    carries a directive object) the rendered PROMPT text shows the unambiguous
    ARRAY illustration (``"entries": [{...}]``) instead of the directive keys
    (``minItems``/``maxItems``/``entrySchema``) — the directive keys remain
    authoritative ONLY in the derived transport JSON Schema, so the prompt can
    never teach a literal-copying model an object-shaped ``entries``.
    """
    if stage == "activity_log":
        rendered = _activity_log_prompt_contract()
    else:
        rendered = _stage_contract(stage)
    return json.dumps(
        rendered, sort_keys=True, ensure_ascii=False, indent=2
    )


# --------------------------------------------------------------------------- #
# Phase17B — transport-level structured output (authoritative JSON Schema)
# --------------------------------------------------------------------------- #
#
# ``schema_contract_as_json_schema`` deterministically converts the SAME
# ``_stage_contract`` mapping into a JSON Schema object for the Ollama
# ``/api/chat`` ``format`` field. The conversion is structural and permissive
# (matching keys + nesting + primitive types; no hand-maintained duplicate);
# the strict parsers remain the ONLY authority that accepts/rejects a response.
#
# Documented stage mapping (smoke alias -> GenerationStage.value ->
# prompt-template version -> schema contract):
#   case_truth      -> CASE_TRUTH      -> case_people_v1        -> case_people
#   evidence        -> EVIDENCE        -> evidence_v1           -> evidence
#   world_requirements -> WORLD_GRAPH  -> world_requirements_v1 -> world_requirements
#   asset_spec      -> ASSET_SPEC      -> asset_spec_v1         -> asset_spec
#   asset_spec_repair -> ASSET_SPEC_REPAIR -> asset_spec_repair_v1 -> asset_spec
#   activity_log    -> ACTIVITY_LOG    -> activity_log_v1       -> activity_log
#   activity_log_repair -> ACTIVITY_LOG_REPAIR -> activity_log_repair_v1 -> activity_log
#   repair          -> REPAIR          -> repair_v1             -> full_draft
STAGE_TO_PROMPT_VERSION: Mapping[str, str] = {
    "case_truth": "case_people_v1",
    "evidence": "evidence_v1",
    "world_graph": "world_requirements_v1",
    "asset_spec": "asset_spec_v1",
    "asset_spec_repair": "asset_spec_repair_v1",
    "activity_log": "activity_log_v1",
    "activity_log_repair": "activity_log_repair_v1",
    "repair": "repair_v1",
}
STAGE_TO_CONTRACT: Mapping[str, str] = {
    "case_truth": "case_people",
    "evidence": "evidence",
    "world_graph": "world_requirements",
    "asset_spec": "asset_spec",
    "asset_spec_repair": "asset_spec",
    "activity_log": "activity_log",
    "activity_log_repair": "activity_log",
    "repair": "full_draft",
}
# The authoritative schema-contract vocabulary (a closed set; the stale-version
# guard test asserts its members are exactly the ones shipped).
CONTRACT_KEYS: frozenset[str] = frozenset(
    {
        "case_people",
        "evidence",
        "world_requirements",
        "asset_spec",
        "activity_log",
        "full_draft",
    }
)


def _hint_nullable(hint: str) -> bool:
    return "null" in hint.casefold()


def _enum_token_list(hint: str) -> list[str]:
    """Deterministic closed-enum token list of a contract hint.

    A hint carrying the marker ``enum token1,token2,...`` (e.g. the Phase 19
    ``environmentHint`` vocabulary) contributes exactly those tokens to the
    derived transport JSON Schema ``{"type": "string", "enum": [...]}``. The
    token list is everything after ``enum`` up to the first ``(`` (prose
    parentheticals are cut); tokens are split on commas/whitespace and
    deduplicated while preserving order. No other hint text matches the
    marker, so no unrelated schema changes.
    """
    lowered = hint.casefold()
    marker = "enum "
    index = lowered.find(marker)
    if index < 0:
        return []
    rest = hint[index + len(marker):]
    paren = rest.find("(")
    if paren != -1:
        rest = rest[:paren]
    tokens: list[str] = []
    for token in rest.replace(",", " ").split():
        token = token.strip()
        if token:
            tokens.append(token)
    return tokens


def _key_required(value: Any) -> bool:
    """Requiredness rule for the derived JSON Schema: containers and scalars are
    always required; a string hint is optional only when it mentions ``null``
    WITHOUT the explicit ``[REQUIRED]`` key marker (a field can therefore be a
    required KEY with a nullable type — the Ollama grammar then forces the key
    to be present). Never applied to a container's repr (which may
    coincidentally mention ``null`` inside a nested hint)."""
    if isinstance(value, (Mapping, list, tuple, bool, int, float)):
        return True
    text = str(value)
    if "[REQUIRED]" in text:
        return True
    return not _hint_nullable(text)


def _hint_json_types(hint: str) -> tuple[str, ...]:
    """Deterministic primitive-type tuple derived from a contract hint string.

    Scalar types only — array/object structure comes from the contract shape.
    Used to build the permissive transport JSON Schema (the strict parsers stay
    the sole acceptance authority).
    """
    text = hint.casefold()
    if "bool" in text:
        return ("boolean",)
    # A declared nested JSON object (e.g. evidence ``structured`` — Phase17D A):
    # the transport schema must declare an OBJECT so Ollama's grammar forces a
    # real nested object and never a JSON-encoded string. The marker is the
    # explicit "JSON object" phrasing (never matches "physical object noun").
    if "json object" in text:
        return ("object",)
    if "integer" in text or " int" in text or text.startswith("int"):
        return ("integer",)
    if "number" in text:
        return ("number",)
    if _hint_nullable(hint):
        return ("string", "null")
    return ("string",)


def _contract_to_json_schema(node: Any) -> dict[str, Any]:
    """Deterministic structural JSON Schema derived from the authoritative
    ``_stage_contract`` mapping (never a hand-maintained duplicate).

    Rules (documented, deterministic):
    - ``$note`` keys carry a JSON-Schema ``description`` (never a property);
    - a dict carrying ``partSchema`` is an ARRAY whose items are the schema of
      ``partSchema`` and whose ``maxItems`` is the embedded ``maxParts`` integer;
    - a dict carrying ``entrySchema`` (Phase19J-RI) is an ARRAY whose items are
      the schema of ``entrySchema`` and whose ``minItems``/``maxItems`` are the
      embedded optional integers (the activity-log contract's hard 15..20 entry
      bound — the Ollama grammar can therefore never emit a one-row repair
      wrapper); absent markers simply leave the JSON-Schema bounds unset;
    - a list of one dict is an array whose items are that dict's schema; a list
      of strings is an array of strings;
    - a hint containing ``[x,y,z]`` declares the documented vector object with
      exactly x/y/z numeric properties;
    - a hint mentioning ``null`` marks the property optional (not required);
    - otherwise the primitive type is derived from the hint text.
    """
    if isinstance(node, Mapping):
        container = dict(node)
        note = container.pop("$note", None)
        array_schema_key = (
            "partSchema" if "partSchema" in container
            else "entrySchema" if "entrySchema" in container
            else None
        )
        if array_schema_key is not None:
            min_items = container.pop("minItems", None)
            max_items = container.pop("maxParts" if array_schema_key == "partSchema" else "maxItems", None)
            schema: dict[str, Any] = {
                "type": "array",
                "items": _contract_to_json_schema(container.pop(array_schema_key)),
            }
            if isinstance(min_items, int) and not isinstance(min_items, bool):
                schema["minItems"] = int(min_items)
            if isinstance(max_items, int) and not isinstance(max_items, bool):
                schema["maxItems"] = int(max_items)
            if note is not None:
                schema["description"] = str(note)
            return schema
        props = {
            key: _contract_to_json_schema(value)
            for key, value in container.items()
            if key != "maxParts"
        }
        required = sorted(
            key
            for key, value in container.items()
            if key != "maxParts" and _key_required(value)
        )
        schema = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        if note is not None:
            schema["description"] = str(note)
        return schema
    if isinstance(node, (list, tuple)):
        if not node:
            return {"type": "array"}
        if all(isinstance(item, str) for item in node):
            return {"type": "array", "items": {"type": "string"}}
        return {"type": "array", "items": _contract_to_json_schema(node[0])}
    if isinstance(node, bool):
        return {"type": "boolean"}
    if isinstance(node, (int, float)):
        return {"type": "number"}
    hint = str(node)
    if hint.startswith(_PROPOSITION_TYPE_HINT_PREFIX):
        # The strict evidence parser owns this vocabulary. Give Ollama's JSON
        # Schema grammar that exact enum instead of merely accepting a string.
        return {"type": "string", "enum": sorted(PROPOSITION_TYPES)}
    enum_tokens = _enum_token_list(hint)
    if enum_tokens:
        # Phase 19 Fix A: a contract hint carrying the ``enum a,b,c`` marker
        # derives a CLOSED string enum in the transport JSON Schema (the
        # ``environmentHint`` vocabulary is the canonical five ids). Sorted
        # like every other derived enum (the evidence proposition vocabulary)
        # so the grammar is deterministic. The strict parsers stay the sole
        # acceptance authority.
        return {"type": "string", "enum": sorted(set(enum_tokens))}
    if "[x,y,z]" in hint:
        return {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
            },
            "required": ["x", "y", "z"],
        }
    types = _hint_json_types(hint)
    if types == ("object",):
        # A declared nested JSON object (Phase17D A): permissive open object —
        # the grammar admits any keys/values, so the strict parser stays the
        # sole acceptance authority (a closed property set would force the
        # model to invent contradictory shapes).
        return {"type": "object", "additionalProperties": True}
    return {"type": list(types) if len(types) > 1 else types[0]}


def schema_contract_as_json_schema(stage: str) -> dict[str, Any]:
    """The authoritative transport JSON Schema (Ollama ``format`` object).

    Derived from the SAME ``_stage_contract(stage)`` mapping the prompt embeds
    — one source, never a duplicate (Phase17B §2).
    """
    return _contract_to_json_schema(_stage_contract(stage))


def json_schema_for_generation_stage(stage_value: str) -> dict[str, Any] | None:
    """The authoritative JSON Schema for a ``GenerationStage.value``, or None
    when the stage has no schema contract. Every generation stage the Ollama
    provider serves has one — including ``repair`` (the full-draft REPAIR
    contract, Phase17D C §2 — structured transport demands the COMPLETE draft,
    never a partial patch); unknown/unmapped values return None (fail-closed:
    the provider keeps ``format: "json"``)."""
    contract_stage = STAGE_TO_CONTRACT.get(stage_value)
    if contract_stage is None:
        return None
    return schema_contract_as_json_schema(contract_stage)


# --- shared template fragments ---------------------------------------------

_NO_INTERNALS = (
    "NEVER output hidden truth, solver proof, secrets, credentials, database "
    "internals, the murderer/weapon 'answer', or any clue that this is a "
    "generated puzzle. You PROPOSE structured facts only."
)


def _asset_spec_rules() -> str:
    return (
        "AssetSpec rules (MUST follow all of them):\n"
        "- Return ONLY a single JSON document (no markdown fences, no prose).\n"
        "- Top-level keys are EXACTLY: canonicalName, category, subtype, "
        "dimensions, parts. Do not invent, rename or drop any key (never "
        "snake_case variants, never extra keys).\n"
        "- Part keys are EXACTLY: id, role, primitive, transform, material, "
        "sourceColor, parentId.\n"
        "- transform keys are EXACTLY: position, rotation, scale; every vector "
        "is an object with EXACT keys x, y, z.\n"
        "- category is ONE exact token from the schema list (e.g. decor, "
        "evidence, utility, character, furniture, electronics, structural).\n"
        "- primitive is ONE exact token from the schema list (box, cylinder, "
        "plane, sphere).\n"
        "- material is ONE exact token from the schema material list (e.g. "
        "ceramic, fabric, leather, metal.brass, metal.steel, plastic, "
        "wood.dark, wood.light). Never invent a material word.\n"
        f"- At most {MAX_PARTS} parts (1 part minimum).\n"
        f"- Use ONLY the primitives: {sorted(PRIMITIVE_ALLOWLIST)}.\n"
        f"- Use ONLY the materials: {sorted(MATERIAL_VOCAB)}.\n"
        "- All part ids unique (part_00..part_23).\n"
        "- Bounded finite dimensions/position/rotation/scale (see schema).\n"
        "- Maximum parent depth 2 (a parent must be an EARLIER part).\n"
        "- NO URLs, paths, HTML, scripts, shaders, event handlers, or "
        "executable code.\n"
        "- Prefer simple, recognizable silhouettes; use as few parts as "
        "necessary (a single sphere is not a usable pick or knife).\n"
        "- Do NOT place all parts at the same position (a same-origin pile has "
        "no recognizable silhouette and is rejected).\n"
        "- Near-zero or collapsed geometry is disallowed: hand-held or "
        "evidence objects need at least TWO well-separated parts (positions "
        "differing by more than 0.05m on one axis); never declare a single "
        "tiny part or a 0.05m clump as the whole object.\n"
        "- STUDY THIS VALID MINIMAL thin-object example (copy its STRUCTURE and "
        "layout — declared dims COVER the parts, thin blade, parts "
        "separated by more than 0.05m; invent your own values):\n"
        '{"canonicalName":"Ceremonial Ice Pick","category":"decor",'
        '"subtype":"ceremonial_ice_pick",'
        '"dimensions":{"x":0.04,"y":0.32,"z":0.04},\n'
        ' "parts":[{"id":"part_00","role":"handle","primitive":"cylinder",'
        '"transform":{"position":{"x":0.0,"y":0.0,"z":0.0},'
        '"rotation":{"x":0.0,"y":0.0,"z":0.0},'
        '"scale":{"x":0.04,"y":0.18,"z":0.04}},"material":"wood.dark"},\n'
        '  {"id":"part_01","role":"blade","primitive":"box",'
        '"transform":{"position":{"x":0.0,"y":0.2,"z":0.0},'
        '"rotation":{"x":0.0,"y":0.0,"z":0.0},'
        '"scale":{"x":0.02,"y":0.1,"z":0.005}},"material":"metal.brass"}'
        "]\n}"
    )


def _phase17_repair_instruction() -> str:
    """Phase 17 — deterministic geometry-quality repair instruction.

    The LLM proposes the object; Procedural Detective verifies its geometry. The
    repair candidate is NEVER trusted incrementally: after this repair the full
    Phase 13 schema/security validation AND the Phase 17 geometry-quality
    validation run again before any compilation/publishing.
    """
    return (
        "Geometry-quality instructions (Phase 17):\n"
        "- Dimensions and positions are in METERS: 0.25 means 25 centimeters, "
        "25 means 25 meters.\n"
        "- Make the declared dimensions the TRUE overall bounding box: they "
        "must be large enough to cover every part you place (if a part pokes "
        "past the declared box, either move the part inside OR enlarge the "
        "declared dimensions so they cover it — fix both together until they "
        "agree).\n"
        "- Keep every part inside the declared object envelope (declared "
        "dimensions are the object's overall bounds in metres).\n"
        "- Use a recognizable silhouette: at least TWO well-separated parts "
        "(positions differing by more than 0.05m along one axis) for hand-held "
        "or evidence objects. A single sphere is not a usable ice pick or "
        "knife.\n"
        "- Do not place all parts at the same origin (no overlapping "
        "same-position pile).\n"
        "- Use valid identifiers and roles: part ids like part_00..part_23 and "
        "roles matching ^[a-z0-9_]+$ (no dashes, spaces or uppercase).\n"
        "- Use only allowed materials and primitives (listed in the schema "
        "above).\n"
        "- Keep the object at a visible, plausible scale; use as few parts as "
        "necessary.\n"
        "- Use the EXACT schema key names above (canonicalName, category, "
        "subtype, dimensions, parts and the part keys) — never rename, drop or "
        "invent keys.\n"
    )


# --- Phase17B field ring-fencing fragments (messages/instructions only; the
# --- schema contracts and strict parsers are untouched).

_CASE_FIELD_RULES = (
    "\n\nFIELD RULES (strict):\n"
    "- Return ONLY the JSON object for the case_people stage - no extra text, "
    "no markdown fences.\n"
    "- The document keys are EXACTLY: crime, persons, motives, locations, "
    "travelRules, scene.\n"
    "- crime uses EXACTLY: type, victimId, murdererId, motiveId, weaponId, "
    "locationId, crimeTime (camelCase - never snake_case variants like "
    "crime_time, never renamed or dropped keys).\n"
    "- crimeTime uses EXACTLY: canonical (ISO-8601 timestamp) and "
    "accusationToleranceSeconds (a plain non-negative integer).\n"
    "- person role is one exact token from: victim, suspect, witness, family, "
    "other.\n"
    "- affordances use ONLY the exact tokens SUSPECT_ELIGIBLE, MOTIVE_CANDIDATE, "
    "POTENTIAL_WEAPON, VISIBLE_CHARACTER, INSPECTABLE, POTENTIAL_SHARP_WEAPON, "
    "POTENTIAL_BLUNT_WEAPON, POTENTIAL_POISON.\n"
    "- every motive affordances array contains exactly the token MOTIVE_CANDIDATE.\n"
    "- travelTimeSeconds is a plain non-negative integer.\n"
    "- persons: include the victim, the murderer, the witness and at least "
    "TWO RED-HERRING suspects (role 'suspect' with the SUSPECT_ELIGIBLE "
    "affordance) — a case with a single suspect is not a valid mystery.\n"
    "- motives: include the LOCKED motive plus at least TWO additional "
    "MOTIVE_CANDIDATE red-herring motives with distinct plausible labels — "
    "the solver must be able to EXCLUDE every alternative motive, so a "
    "one-motive case is not a valid mystery.\n"
    "- locations: include the locked location/scene plus at least TWO OTHER "
    "distinct locations (a lab, a lobby, a parking lot, a lodge, a store...) — "
    "the other suspects need somewhere ELSE to be during the crime.\n"
    "- travelRules: MANDATORY — for EVERY non-scene location, provide the "
    "travel time FROM that location TO the scene (fromLocationId = the other "
    "location, toLocationId = the scene location, travelTimeSeconds = a "
    "realistic 900..2700, i.e. 15-45 minutes). At least two non-scene "
    "locations MUST have travelTimeSeconds >= 1200. A location without a "
    "travel rule to the scene cannot be used for an alibi/opportunity check.\n"
    "- Use EXACTLY the id tokens from the locked identity sheet for the locked "
    "people, the locked motive, the locked weapon and the locked time (never "
    "invent alternative ids for them); invent fresh ids ONLY for the "
    "red-herring people/motives/locations you create.\n"
    "- Do not invent keys, values, ids or enums; stay inside the schema.\n"
)


_EVIDENCE_FIELD_RULES = (
    "\n\nFIELD RULES (strict):\n"
    "- The document has EXACTLY one top-level key: evidence (an array of "
    "items) - never rename or wrap it.\n"
    "- Each evidence item uses EXACTLY: id, kind, reliability, discoverable, "
    "sourceRef, propositions, presentation.\n"
    "- kind is one exact token from: email, physical, financial, cctv, "
    "testimonial, forensic, witness_statement.\n"
    "- reliability is one exact token from: high, medium, low; discoverable "
    "is a real boolean (true or false).\n"
    "- sourceRef uses EXACTLY kind and sourceId; presentation uses EXACTLY "
    "title and description (plus the documented typed public fields).\n"
    "- proposition type is one of the exact proposition type tokens listed in "
    "the schema - never an invented token, never a verdict or final-truth "
    "declaration, never a lowercase/natural-language label.\n"
    "- These proposition types REQUIRE an observedAt timestamp (never null, "
    "never absent): PERSON_OBSERVED_AT_LOCATION, VICTIM_LAST_SEEN_ALIVE_AT, "
    "BODY_FIRST_FOUND_AT, NOISE_HEARD_AT, CRIME_SCENE_OBSERVATION_AT, "
    "TIME_WINDOW_EXCLUSION. Every OTHER type keeps observedAt null or absent. "
    "For a time-requiring type, observedAt MUST be the proposition's OWN "
    "observedAt field (a sibling of type) — never inside structured, never "
    "renamed.\n"
    "- WORKED EXAMPLE — a timestamped proposition is EXACTLY:\n"
    "    {\"type\": \"NOISE_HEARD_AT\", \"observedAt\": "
    "\"2026-09-19T22:00:00+02:00\", \"uncertaintySeconds\": 60, \"locationId\": "
    "\"loc001\"}\n"
    "  observedAt sits at the PROPOSITION level. The DATE and +02:00 offset in "
    "this example are placeholders — in your REAL output use the SAME date and "
    "+02:00 offset as the DEDUCTION SEED / locked crime time (never a "
    "different date, never a 'Z' UTC timestamp: the solver compares ticks, and "
    "a timestamp on another date would break the case). Do NOT copy it "
    "into structured, and DO NOT put personId/locationId/objectId timestamps "
    "inside structured either.\n"
    "into structured, and DO NOT put personId/locationId/objectId timestamps "
    "inside structured either.\n"
    "- structured holds ONLY extra typed fields that are not already "
    "proposition-level keys (e.g. FORENSIC_WEAPON_MATCH -> {\"match\": true}; "
    "ALIBI_TIME_CLAIM -> {\"claimedDeparture\": \"...\"}). Never repeat "
    "observedAt/personId/locationId/objectId inside structured.\n"
    "- propositions[].structured MUST be a nested JSON OBJECT (never a "
    "JSON-encoded string, never quoted/escaped JSON).\n"
    "- FILL THE RELATIONAL FIELDS (they are how the solver connects the "
    "facts — NEVER null when the fact names a person, location or object):\n"
    "    VICTIM_LAST_SEEN_ALIVE_AT / BODY_FIRST_FOUND_AT / "
    "CRIME_SCENE_OBSERVATION_AT -> locationId = the locked location_id, "
    "personId = the victim/murderer id where named;\n"
    "    PERSON_OBSERVED_AT_LOCATION -> personId + locationId (both filled);\n"
    "    ALIBI_TIME_CLAIM -> personId (the claimant's id) + the "
    "structured.claimedDeparture;\n"
    "    OBJECT_CONTAINS_FINGERPRINT -> objectId (the weapon) + personId "
    "(the person);\n"
    "    FORENSIC_WEAPON_MATCH -> objectId (the weapon examined);\n"
    "    MOTIVE_LINKED_TO_PERSON -> personId + motiveId.\n"
    "- ALIBI_TIME_CLAIM propositions REQUIRE structured.claimedDeparture (a "
    "non-empty ISO-8601 departure timestamp string).\n"
    "- FORENSIC_WEAPON_MATCH propositions REQUIRE structured.match (a real "
    "JSON boolean true or false, never a string).\n"
    "- observedAt is ISO-8601; uncertaintySeconds is a plain non-negative "
    "integer.\n"
    "- SPREAD the facts over SEVERAL evidence items (one coherent cluster per "
    "item, e.g. one CCTV item, one forensic item, one witness item) — NEVER "
    "merge the whole case into a single evidence item, and NEVER emit an "
    "evidence item with an empty propositions array.\n"
    "- Use the EXACT ids from the APPROVED PUBLIC MATERIAL / DEDUCTION SEED — "
    "never invent ids for locked people/objects or for persons/locations/"
    "motives listed there.\n"
    "- Do not invent keys, ids or enums; never assert the solution.\n"
)


_WORLD_FIELD_RULES = (
    "\n\nFIELD RULES (strict):\n"
    "- The document uses EXACTLY: environmentHint, locationTokens, objects, "
    "relations, unsafeUnsupported.\n"
    "- environmentHint is ONE EXACT canonical token from this closed vocabulary: "
    "apartment, office, hotel_suite, warehouse, mansion. NEVER write a "
    "location phrase ('hotel suite', 'the office'), NEVER a path ('hotel/"
    "suite'), NEVER a synonym or free text — pick the exact canonical token "
    "for the prompt's location (hotel suite -> hotel_suite, company office -> "
    "office). If the prompt names no supported location, still output one "
    "canonical token from the vocabulary (the closest match).\n"
    "- objects entries use EXACTLY: name, categoryHint, subtypeHint, tags, "
    "requiredInteraction, evidenceId, criticality.\n"
    "- MAPPING OF THE PROMPT LINES: Victim/Murderer/Witness are PERSONS, the "
    "Motive is a motive, the Time is the crime timestamp, the Location is the "
    "ENVIRONMENT (choose it via environmentHint) — none of these become an "
    "object entry. ONLY the Weapon becomes an object entry.\n"
    "- objects: list ONLY the single crime weapon. Do NOT add extra props, "
    "documents, folders, furniture, desks, chairs or other items — the scene "
    "already contains its standard props; an extra invented object can "
    "unavoidably block the world.\n"
    "- NEVER list the environment/location token or any abstract concept as "
    "an object: 'office', 'desk', 'room', 'research data', 'motive', a "
    "person's name, an activity — none of these is an object entry.\n"
    "- criticality: the weapon is exactly required; there are no other "
    "objects, so nothing else is required or decorative.\n"
    "- evidenceId and requiredInteraction ALWAYS travel together: when an "
    "evidenceId is set for an object, its requiredInteraction MUST be "
    "'inspect' in the SAME object entry — never one without the other.\n"
    "- relations: bind ONLY the weapon with EXACTLY ONE relation — the "
    "natural surface it rests on, e.g. {\"kind\": \"on_desk\", \"target\": "
    "\"<weapon name>\"} or {\"kind\": \"on_table\", \"target\": \"<weapon "
    "name>\"}. Every relation target is an OBJECT name from your objects "
    "list — never a person.\n"
    "- relations kind is one exact token from: on_desk, on_table, "
    "near_victim, inside_cabinet, floor_area, on_wall.\n"
    "- unsafeUnsupported is an ARRAY of short diagnostic notes; every entry "
    "must be at most 120 characters, and the array should normally be [] "
    "(one or two short words at most, never long sentences or prose).\n"
    "- Do not invent keys, tokens, ids or coordinates; output declarative "
    "intent only.\n"
)


def _repair_instruction() -> str:
    return (
        "Correct the AssetSpec. Do not redesign the object unless required. "
        "Fix the listed validation violations. Return the complete corrected "
        "AssetSpec as JSON only."
    )


# --- the six versioned templates -------------------------------------------

CASE_PEOPLE_PROMPT_v1 = (
    "You are generating the CASE and PEOPLE stage of a detective case for "
    "GENERATION_PROVIDER=ollama (prompt template version case_people_v1).\n"
    "Return ONLY the JSON object for this stage with this EXACT schema:\n"
    + schema_contract("case_people")
    + _CASE_FIELD_RULES
    + "\n\nSanitized user prompt:\n__PROMPT__\n\n"
    "LOCKED user constraints (MUST be respected EXACTLY - never change a "
    "locked value):\n__LOCKED__\n\n"
    + "__ID_SHEET__\n"
    + _NO_INTERNALS
    + "\nNote: the crime TIME and the crime identity come from the locked "
    "constraints plus generated filler. Never declare a final verdict."
)


# The evidence contract's DEDUCTION CONTRACT (teaching the three-state algebra
# the deterministic solver uses; NEVER a solver answer/reveal — the locked
# fields in the DEDUCTION SEED are the only truth seeds and the model emits
# FACTS, never conclusions).
_EVIDENCE_DEDUCTION_CONTRACT = (
    "\nDEDUCTION CONTRACT — your facts ARE the case a deterministic solver "
    "proves. The solver works purely from your structured facts and EXCLUDES a "
    "candidate ONLY when the right fact type is present; a missing fact "
    "leaves that candidate viable and the whole case cannot be published. You "
    "must therefore emit EVERY fact listed here — never skip one:\n"
    "1. WHEN — bound the crime window: VICTIM_LAST_SEEN_ALIVE_AT at the scene "
    "(about 2 minutes before the crime time), BODY_FIRST_FOUND_AT at the "
    "scene (about 1.5 minutes after the crime time), and "
    "CRIME_SCENE_OBSERVATION_AT at the scene around the crime time with "
    "uncertaintySeconds ~90.\n"
    "2. IMPOSSIBLE-OPPORTUNITY — one PERSON_OBSERVED_AT_LOCATION for EVERY "
    "other suspect, each at a DIFFERENT location that has an approved "
    "travelRule TO the scene of at least 1200 seconds, observed about 2 "
    "minutes before the crime time (uncertaintySeconds ~30). With that travel "
    "time the suspect physically cannot reach the scene before the crime "
    "window ends, so the solver ELIMINATES them. NEVER use type OTHER for "
    "these.\n"
    "3. PRESENCE — PERSON_OBSERVED_AT_LOCATION for the locked person AT the "
    "scene, around the crime time (uncertaintySeconds ~60) — that is the "
    "feasible scene presence that keeps the locked person the only viable "
    "suspect.\n"
    "4. ALIBI — ALIBI_TIME_CLAIM by the locked person with "
    "structured.claimedDeparture about 32 minutes before the crime time "
    "(contradicted by their scene presence — an alibi can NEVER be the only "
    "reason, it just weakens credibility).\n"
    "5. MOTIVE — MOTIVE_LINKED_TO_PERSON (locked person -> locked motive) and "
    "MOTIVE_FACT_CONTRADICTED for EVERY other motive.\n"
    "6. WEAPON — FORENSIC_WEAPON_MATCH with structured.match false for EVERY "
    "other sharp weapon (normally kitchen_knife, letter_opener, scissors) and "
    "true for the locked weapon, plus OBJECT_CONTAINS_FINGERPRINT on the "
    "locked weapon for the locked person.\n"
    "The DEDUCTION SEED below lists the EXACT ids and timestamps to use (same "
    "date and +02:00 offset as the locked crime time). Emit the facts as "
    "OBSERVATIONS/FORENSIC RESULTS — never conclusions, never the words "
    "'guilty'/'committed'/'the murderer is', never any hidden answer."
)


EVIDENCE_PROMPT_v1 = (
    "You are generating the EVIDENCE stage of a detective case for "
    "GENERATION_PROVIDER=ollama (prompt template version evidence_v1).\n"
    "Return ONLY the JSON object for this stage with this EXACT schema:\n"
    + schema_contract("evidence")
    + _EVIDENCE_FIELD_RULES
    + _EVIDENCE_DEDUCTION_CONTRACT
    + "\n\nSanitized user prompt:\n__PROMPT__\n\n"
    "LOCKED user constraints:\n__LOCKED__\n\n"
    + "__APPROVED_PEOPLE__\n"
    + "__ID_SHEET__\n"
    + "__DEDUCTION_SEED__\n"
    + "__DEDUCTION_FEEDBACK__\n"
    + _NO_INTERNALS
)


WORLD_REQUIREMENTS_PROMPT_v1 = (
    "You are generating the WORLD_REQUIREMENTS stage of a detective case for "
    "GENERATION_PROVIDER=ollama (prompt template version world_requirements_v1).\n"
    "Return ONLY the JSON object for this stage with this EXACT schema:\n"
    + schema_contract("world_requirements")
    + _WORLD_FIELD_RULES
    + "\n\nSanitized user prompt:\n__PROMPT__\n\n"
    "LOCKED user constraints:\n__LOCKED__\n\n"
    + "__ID_SHEET__\n"
    + "__WEAPON_EVIDENCE_ID__\n"
    "Produce declarative world intent ONLY: environmentHint, locationTokens, "
    "bounded objects, and bounded placement relations. NEVER output coordinates, "
    "raw transforms, JavaScript, Babylon code, shaders, event handlers, URLs or "
    "paths. The deterministic placer decides final placement.\n"
    + _NO_INTERNALS
)


ASSET_SPEC_PROMPT_v1 = (
    "You are generating a declarative procedural AssetSpec for the object "
    "'__OBJECT_CONCEPT__' for GENERATION_PROVIDER=ollama (prompt template "
    "version asset_spec_v1).\n"
    + _METERS_STATEMENT
    + "\n\nThe object this AssetSpec must represent (original concept): __OBJECT_CONCEPT__"
    + "\nCategory hint: __CATEGORY_HINT__"
    + "__CANONICAL_CONCEPT__"
    + "\n\nReturn ONLY a single JSON document with this EXACT schema:\n"
    + schema_contract("asset_spec")
    + "\n\n"
    + _asset_spec_rules()
)


ASSET_SPEC_REPAIR_PROMPT_v1 = (
    "You are REPAIRING a declarative procedural AssetSpec for the object "
    "'__OBJECT_CONCEPT__' for GENERATION_PROVIDER=ollama (prompt template "
    "version asset_spec_repair_v1).\n"
    + _METERS_STATEMENT
    + "__CANONICAL_CONCEPT__"
    + "\n\nCorrect the AssetSpec. Do not redesign the object unless required. "
    "Fix the listed validation violations. Return the complete corrected "
    "AssetSpec as JSON only.\n\n"
    "Original object concept: __OBJECT_CONCEPT__\n"
    "Previous (invalid) candidate AssetSpec:\n__PREVIOUS_CANDIDATE__\n\n"
    "SANITIZED validation issues to fix:\n__ISSUES__\n\n"
    "The exact schema and bounds:\n"
    + schema_contract("asset_spec")
    + "\n\n" + _asset_spec_rules()
    + "\n" + _phase17_repair_instruction()
    + "\nReturn the corrected AssetSpec with the EXACT schema keys above - "
    "never rename, drop or invent keys (no snake_case variants, no extra "
    "fields).\n"
    "Do not loosen or reinterpret the constraints. The repaired AssetSpec "
    "is re-validated with the FULL Phase 13 schema/security validator AND the "
    "Phase 17 geometry-quality validator before it can be compiled."
)


# --- repair/draft template (full-draft correction used by driver REPAIR) ---

REPAIR_PROMPT_v1 = (
    "You are repairing the generated draft of a detective case for "
    "GENERATION_PROVIDER=ollama (prompt template version repair_v1).\n"
    "Correct the draft to fix the SANITIZED validation issues WITHOUT changing "
    "any LOCKED user constraint.\n"
    "Return the COMPLETE repaired draft, including every required top-level "
    "key.\n"
    "The repaired draft MUST contain EVERY one of these required top-level "
    "keys — never a partial or patched draft: crime, persons, motives, "
    "objects, locations, travelRules, scene, evidence, worldGraph.\n"
    "Return ONLY a single COMPLETE JSON document matching the EXACT draft "
    "schema below. Use EXACTLY the section key names in the schema — never "
    "rename, drop or invent keys, sections or enums.\n"
    + schema_contract("full_draft")
    + "\n\n__PREVIOUS_DRAFT__\n\n"
    "SANITIZED validation issues to fix:\n__ISSUES__\n\n"
    + _NO_INTERNALS
)


# --- Phase 19J activity-log templates (server owns the canonical fact) ------
#
# Phase19J-RI (DEF-104): the prompt gives the stateless model the concrete
# ISO-8601 temporal window endpoints (computed with the EXACT same
# ``activity_log_window_bounds`` clamp the strict validator applies) and a
# machine-readable canonical-anchor example row filled with the locked value —
# so an 8B-class model never has to compute the relative ±N-minute window
# itself and never invents a second canonical instant.

# The canonical-anchor worked example (template-fill only: the actual
# ``__CANONICAL_TIME__`` value lands verbatim in this ONE example row; the
# activityType token stays a closed-enum token and the activity text is the
# existing neutral fixture phrase already used throughout the tree).
_CANONICAL_ROW_ANCHOR_SENTENCE = (
    "The locked canonical time is ONE row and must appear VERBATIM in exactly "
    'one entry, e.g. {"timestamp": "__CANONICAL_TIME__", "activityType": '
    '"LOCAL_ACTIVITY", "activity": "Local user activity detected"}.'
)

# DEF-104 follow-up #2 — COMPLETE worked example + simultaneous-constraint rule.
# The observed hermes3:8b failure class was a simultaneous-constraint
# compliance gap: the model satisfies (window) XOR (canonical-once) XOR
# (schema), never all three at once. The fix gives the stateless model a
# COMPLETE 15-row scaffold (generated at build time from the SAME
# ``activity_log_window_bounds`` math the strict validator applies) plus an
# explicit "satisfy ALL of these at once" rule and a shape-scaffold
# qualification ("this is a shape, not the answer").
#
# DEF-104 follow-up #3 — THE TIMESTAMP GRID: the model still DROPPED the
# locked canonical instant even with window endpoints + anchor row + full
# scaffold + simultaneous rule. The final semantic lever hands the model the
# app-owned DETERMINISTIC grid of EXACT timestamps (computed with the SAME
# validated-window math) and makes the canonical-once property a pure
# VERBATIM COPY task: the canonical instant IS one grid member (middle slot);
# the model only writes the surrounding neutral text. The scaffold is aligned
# to the SAME grid (its 15 rows are the grid's first 15 timestamps) so the two
# signals agree byte-for-byte. The grid is NOT truth — it is a deterministic
# sampling around the locked canonical fact (which stays separately injected).

# App-owned neutral computer-log text pool for the scaffold example rows ONLY.
# These are the phase-19J fixture vocabulary strings (a subset of the safe
# neutral phrases the deterministic driver fixtures and the FakeProvider
# golden path use): every phrase is harmless, bounded (<=120 chars) and
# mismatch-free under ``activity_text_unsafe_tokens`` / ``truth_leak_tokens``
# / ``entity_leak_tokens`` with no person/weapon/motive/location needles.
# NOTE: the pool deliberately omits the fixture phrase "System resumed from
# sleep" so the repair prompt can never echo the exact rejected-log content
# the driver-repair regression asserts stays out (test_phase19j §11).
ACTIVITY_LOG_NEUTRAL_TEXT_POOL: tuple[str, ...] = (
    "User session login recorded",
    "Mail client synchronized",
    "Browser tab opened",
    "Research document accessed",
    "File explorer opened",
    "Text editor application opened",
    "Cloud synchronization completed",
    "Background synchronization started",
    "Network activity detected",
    "Document autosaved",
    "User session unlocked",
    "Local file written",
    "Keyboard activity detected",
    "File copied to local workspace",
    "Local user activity detected",
    "Browser activity detected",
    "System entered idle state",
)

# Phase19J-RI (ADV-D) — the SMALL reserved fallback pool used ONLY when a
# canonical forbidden person/weapon/motive/location token collides with a
# primary pool phrase or the canonical-anchor text ("Local user activity
# detected"). Each phrase is harmless (bounded <= 120 chars, passes
# ``activity_text_unsafe_tokens`` / ``truth_leak_tokens`` with no needles) and
# deliberately avoids the ordinary computer-log vocabulary a hostile canonical
# name could target (user/mail/local/workspace/server/network/...). The
# substitution is deterministic (first non-colliding phrase in this fixed
# order) and bounded; a pathological hostile set covering EVERY fallback
# phrase too stays a documented LOW residual (the validator's own lexical
# limits already carry the same bounded guarantee).
ACTIVITY_LOG_NEUTRAL_FALLBACK_POOL: tuple[str, ...] = (
    "System clock synchronization heartbeat",
    "Peripheral input device reconnected",
    "Display framebuffer refresh scheduled",
    "Network daemon handshake completed",
    "Storage controller cache flushed",
    "Process scheduler tick recorded",
)

# The scaffold (DEF-104 follow-up #2/#3) is the first
# ``MIN_ACTIVITY_LOG_ENTRIES`` (15) timestamps of the app-owned TIMESTAMP GRID
# (``activity_log_timestamp_grid``) — never a hand-computed row list.
# Strictly increasing +3 minutes between the grid rows (deterministic
# generator; the validator itself only requires strict increase, but the
# grid shows the realistic cadence).
# Pre-Phase19J-RI fixed +3-minute cadence (still the PREFERRED step for the
# default/clamped-120 windows so the QA-closed path is byte-identical).
_ACTIVITY_LOG_EXAMPLE_STEP_SECONDS = 3 * 60
_ACTIVITY_LOG_EXAMPLE_CANONICAL_TYPE = "LOCAL_ACTIVITY"
_ACTIVITY_LOG_EXAMPLE_CANONICAL_TEXT = "Local user activity detected"

# Phase19J-RI (ADV-C) — the deterministic ADAPTIVE cadence chain for the
# timestamp grid. The preferred 3-minute step is tried first (default
# 60/60 and every clamped-120 case emit EXACTLY the pre-fix grid), then a
# bounded geometric fine-grain chain down to the minimal 1-second step. The
# FIRST step for which a grid of ``count`` members (largest count in
# [MIN..ACTIVITY_LOG_GRID_COUNT]) fits inside the ACTUAL clamped window with
# the canonical slot in the 15-row scaffold wins — so a sub-42-minute window
# like 30/0 or 0/30 now yields a self-validating 15-row grid instead of a
# self-contradictory count < MIN prompt. Every step is a whole number of
# seconds >= 1 (bounded; deterministic; no fractional timestamps).
_ACTIVITY_LOG_STEP_CANDIDATES: tuple[int, ...] = (3 * 60, 2 * 60, 60, 30, 15, 10, 5, 2, 1)

# DEF-104 follow-up #3 — THE TIMESTAMP GRID (app-owned, deterministic). The
# grid is NOT truth: it is a deterministic, app-owned sampling of the ACTUAL
# validated window around the locked canonical fact (that fact is already
# injected separately). The model's ONLY job with the grid is to copy these
# timestamps VERBATIM into the rows and write the surrounding neutral text —
# it never computes, reorders or redefines a time.
#
# Count: 18, derived from the authoritative entry bounds (inside
# [MIN_ACTIVITY_LOG_ENTRIES .. MAX_ACTIVITY_LOG_ENTRIES] = [15..20]) and large
# enough that a 15-row worked-example scaffold (the first
# MIN_ACTIVITY_LOG_ENTRIES grid members) plus the remaining grid members prove
# the canonical-once property on a copy, at the SMALLEST token cost that still
# saturates the row-count contract.
ACTIVITY_LOG_GRID_COUNT = 18

# The explicit simultaneous-constraint rule (DEF-104 follow-up #2 step 2).
# Shared fragment: the template prefixes "The generated" (initial) or "The
# repaired" (repair) so both prompts carry the SAME "(a)..(e) ALL at once"
# sentence with the SAME concrete window endpoints (``__WINDOW_START_ISO__ ..
# __WINDOW_END_ISO__`` are replaced by the builder with the exact rendered
# endpoints — identical to the sentence in the window bullet below).
_ACTIVITY_LOG_SIMULTANEOUS_COMMON = (
    " log MUST satisfy ALL of these at once:\n"
    f"(a) {MIN_ACTIVITY_LOG_ENTRIES} to {MAX_ACTIVITY_LOG_ENTRIES} entries;\n"
    "(b) every timestamp strictly increasing and inside "
    "[__WINDOW_START_ISO__ .. __WINDOW_END_ISO__];\n"
    "(c) the locked canonical time appears verbatim exactly once;\n"
    "(d) every activityType is one of the closed tokens;\n"
    "(e) every activity is harmless neutral text.\n"
    "Fixing one rule must NEVER break another."
)

# DEF-104 follow-up #2 step 3 — the worked example is a SHAPE SCAFFOLD, not
# the answer: the model must keep the shape and invent its own content.
_ACTIVITY_LOG_EXAMPLE_IS_SCAFFOLD = (
    "The complete worked example below shows the required shape/format only — "
    "replace the row content with your own plausible harmless rows, keep the "
    "same structure."
)

# DEF-104 follow-up #2 step 4 — repair-stage recheck line (the repair must
# re-verify EVERY simultaneous rule and never trade one for another).
_ACTIVITY_LOG_REPAIR_RECHECK_LINE = (
    "When fixing the findings, re-verify ALL the simultaneous rules above — "
    "never trade one rule for another."
)

# DEF-104 follow-up #3 — the machine-readable TIMESTAMP GRID block injected
# into BOTH templates (requirement 2). ``__TIMESTAMP_GRID__`` is filled by the
# builder with the compact comma-separated ISO list (never JSON, to minimize
# tokens) generated by ``activity_log_timestamp_grid`` — the SAME deterministic
# ``activity_log_window_bounds`` math the strict validator applies. The
# canonical-once property becomes a pure COPY task: the locked instant is ONE
# member of the grid (at the middle slot whenever the window allows), so a
# model that "copies the grid" necessarily satisfies the validator's
# canonical-once rule.
_ACTIVITY_LOG_TIMESTAMP_GRID_BLOCK = (
    "- The EXACT timestamps for the entries are GIVEN below (server-owned; "
    "deterministic):\n"
    "  TIMESTAMP_GRID: __TIMESTAMP_GRID__\n"
    "- Copy EACH timestamp VERBATIM into exactly one row's 'timestamp' "
    "field, in the exact order shown; the row whose timestamp equals the "
    "locked canonical time MUST be the ordinary neutral row. Never invent, "
    "reformat, reorder, or omit any given timestamp. Never add a timestamp "
    "outside the grid.\n"
)

# DEF-104 follow-up #3 requirement 4 — the repair stage's grid restatement:
# the canonical missing finding is resolved by including the grid's canonical
# row unchanged (the model never has to decide WHERE the canonical lands).
_ACTIVITY_LOG_REPAIR_GRID_LINE = (
    "When repairing, copy the GIVEN timestamp grid verbatim — the canonical "
    "missing finding is resolved by including the grid's canonical row "
    "unchanged."
)


def activity_log_timestamp_grid(
    canonical_time: str,
    *,
    before_minutes: int = WINDOW_DEFAULT_BEFORE_MINUTES,
    after_minutes: int = WINDOW_DEFAULT_AFTER_MINUTES,
) -> tuple[str, ...]:
    """The app-owned DETERMINISTIC timestamp grid (DEF-104 follow-up #3).

    Returns strictly-increasing ISO-8601 timestamps computed with the SAME
    ``activity_log_window_bounds`` math (and the same
    ``parse_iso8601``/``epoch_to_iso`` helpers) the strict validator
    applies. Guarantees (the tests assert every one):

    - the locked canonical time appears EXACTLY once, VERBATIM (the original
      string, never a re-render), at the slot CLOSEST to the grid middle
      (``count // 2``) that the window geometry allows, with the slot ALWAYS
      < ``MIN_ACTIVITY_LOG_ENTRIES`` so the 15-row worked-example scaffold
      contains it;
    - every member lies INSIDE the actual validated window ``[window_min ..
      window_max]`` (the validator's own inclusive acceptance bound; for the
      centered default the members are STRICTLY interior);
    - the count is ``ACTIVITY_LOG_GRID_COUNT`` whenever the window can hold 18
      members at +3 minutes; otherwise the count is the largest whole-second
      cadence fit in ``[MIN_ACTIVITY_LOG_ENTRIES .. ACTIVITY_LOG_GRID_COUNT]``
      (Phase19J-RI ADV-C ADAPTIVE GRID) — a sub-42-minute window such as 30/0
      or 0/30 deterministically yields a 15-row grid at a finer cadence (e.g.
      +2 minutes) instead of a self-contradictory count < MIN prompt.

    Phase19J-RI (ADV-C) CANONICAL DEFAULT PRESERVED: the default 60/60 and
    every clamped-120 case (90/90, 5/130, 0/120, 120/0, 15/130) emit EXACTLY
    the pre-fix grid — count 18 at +3 minutes, canonical slot 9 (or slot 1 /
    slot 0 / slot 14 for the clamped geometry) — so the QA-closed real path
    never regresses.

    Fallback (documented): only a window whose total clamped span is below
    ``MIN_ACTIVITY_LOG_ENTRIES - 1`` (14) seconds cannot geometrically hold 15
    distinct strictly-increasing integer-second ticks (the reachable 0/0
    integer-minute config). The grid then deterministically emits the 1-second
    rows the window's larger side fits. The driver NEVER builds a prompt for
    such a window: the ``activity_log_window_satisfiable`` operator guard
    fails fast BEFORE any provider call.

    Raises ``ValueError`` on an unparseable canonical time (a server contract
    error — identical to the validator's determinism: never silently rewrite a
    locked instant).
    """
    tick, offset = parse_iso8601(canonical_time)
    window_min, window_max = activity_log_window_bounds(
        tick, before_minutes=before_minutes, after_minutes=after_minutes
    )
    span_seconds = window_max - window_min
    if span_seconds < MIN_ACTIVITY_LOG_ENTRIES - 1:
        # Fail-closed degenerate span (reachable only as the integer-minute
        # 0/0 configuration): emit the densest bounded grid the window CAN
        # hold at the minimal 1-second step, canonical once on the larger
        # side. Never reached from the driver (the operator guard fails fast
        # BEFORE a prompt is built for an unsatisfiable window).
        step = 1
        rows_before = (tick - window_min) // step if (tick - window_min) >= (window_max - tick) else 0
        rows_after = (window_max - tick) // step if (window_max - tick) > (tick - window_min) else 0
        grid = [canonical_time]
        if rows_before:
            grid = [
                epoch_to_iso(tick - (rows_before - i) * step, offset)
                for i in range(rows_before)
            ] + grid
        if rows_after:
            grid = grid + [
                epoch_to_iso(tick + k * step, offset)
                for k in range(1, rows_after + 1)
            ]
        return tuple(grid)

    # Adaptive cadence search (deterministic, bounded): the FIRST step for
    # which a largest-first count in [MIN..GRID_COUNT] is feasible wins. The
    # count loop prefers the largest feasible count (closest to the desired
    # ACTIVITY_LOG_GRID_COUNT) at the CURRENT preferred step, so the default
    # and clamped-120 windows resolve at +3 minutes BEFORE any finer step is
    # ever considered.
    for step in _ACTIVITY_LOG_STEP_CANDIDATES:
        before_fit = (tick - window_min) // step
        after_fit = (window_max - tick) // step
        for count in range(ACTIVITY_LOG_GRID_COUNT, MIN_ACTIVITY_LOG_ENTRIES - 1, -1):
            mid = count // 2
            k_min = max(0, (count - 1) - after_fit)
            k_max = min(
                before_fit,
                count - 1,
                MIN_ACTIVITY_LOG_ENTRIES - 1,
            )
            if k_min > k_max:
                continue
            canon_index = min(max(mid, k_min), k_max)
            return tuple(
                canonical_time if i == canon_index else epoch_to_iso(tick + (i - canon_index) * step, offset)
                for i in range(count)
            )
    # Defensive: unreachable (a span >= MIN - 1 seconds always admits a
    # 15-row grid at the 1-second step). Deterministically fall back to the
    # helper's canonical row alone rather than raise mid-prompt.
    return (canonical_time,)


def _neutral_text_collides(
    text: str,
    *,
    person_names: Iterable[str] = (),
    weapon_names: Iterable[str] = (),
    motive_names: Iterable[str] = (),
    location_ids: Iterable[str] = (),
    location_names: Iterable[str] = (),
) -> bool:
    """Phase19J-RI (ADV-D): does ONE neutral scaffold phrase collide with the
    injected canonical forbidden-name set under the REAL entity-leak filter?

    Uses the strict validator's OWN ``entity_leak_tokens`` (never weakened):
    a scaffold row the MODEL would be told to copy must not be a row the
    VALIDATOR would reject. The canonical names themselves never appear in the
    rendered prompt — they are used ONLY for this deterministic collision
    check (ADV-256 keeps the identity sheet out of the prompt text).
    """
    if not any(
        (person_names, weapon_names, motive_names, location_ids, location_names)
    ):
        return False
    return bool(
        entity_leak_tokens(
            text,
            person_names=person_names,
            weapon_names=weapon_names,
            motive_names=motive_names,
            location_ids=location_ids,
            location_names=location_names,
        )
    )


def _first_safe_fallback_text(
    start_at: int,
    *,
    person_names: Iterable[str],
    weapon_names: Iterable[str],
    motive_names: Iterable[str],
    location_ids: Iterable[str],
    location_names: Iterable[str],
) -> str:
    """Deterministic first non-colliding fallback phrase (ADV-D). Scans the
    bounded reserved ``ACTIVITY_LOG_NEUTRAL_FALLBACK_POOL`` cyclically from
    ``start_at``; returns the pool's first phrase when every fallback phrase
    collides (a pathological hostile set beyond the bounded pool — documented
    LOW residual, same bound the validator's own lexical limits carry)."""
    pool = ACTIVITY_LOG_NEUTRAL_FALLBACK_POOL
    for index in range(len(pool)):
        phrase = pool[(start_at + index) % len(pool)]
        if not _neutral_text_collides(
            phrase,
            person_names=person_names,
            weapon_names=weapon_names,
            motive_names=motive_names,
            location_ids=location_ids,
            location_names=location_names,
        ):
            return phrase
    return pool[start_at % len(pool)]


def _activity_log_worked_example_json(
    canonical_time: str,
    *,
    before_minutes: int,
    after_minutes: int,
    person_names: Iterable[str] = (),
    weapon_names: Iterable[str] = (),
    motive_names: Iterable[str] = (),
    location_ids: Iterable[str] = (),
    location_names: Iterable[str] = (),
) -> str:
    """Deterministic COMPLETE 15-row worked example log (DEF-104 follow-up #3).

    The scaffold rows ARE the first ``MIN_ACTIVITY_LOG_ENTRIES`` timestamps of
    the SAME ``activity_log_timestamp_grid`` that the prompt's
    ``TIMESTAMP_GRID`` block injects — so the two signals agree byte-for-byte
    and the scaffold proves that a model which verbatim-copies the grid passes
    the strict validator. Guarantees (the tests assert every one of them):

    - exactly ``MIN_ACTIVITY_LOG_ENTRIES`` (15) entries;
    - every timestamp strictly increasing; every timestamp lies INSIDE the
      deterministic window ``[window_min .. window_max]`` computed with the
      validator's own ``activity_log_window_bounds`` math;
    - the locked canonical time appears EXACTLY once, VERBATIM (the original
      string, not a re-render), at an ordinary "Local user activity detected"
      row;
    - every activityType is one of the closed enum tokens and every activity
      is one of the app-owned neutral pool strings.

    Phase19J-RI (ADV-D — prompt-side mitigation, NO validator weakening): when
    an injected canonical forbidden person/weapon/motive/location token
    collides with a primary pool phrase or the canonical-anchor text, the
    colliding scaffold row only is deterministically substituted with a
    guaranteed-safe, non-colliding phrase from the small reserved
    ``ACTIVITY_LOG_NEUTRAL_FALLBACK_POOL`` — everything else stays identical,
    so the built scaffold self-validates (entity_leak_tokens == zero codes)
    even for hostile names like "Mail" / "User" / "Local" / "Workspace" or a
    distinctive location like "ServerWorkspace". The canonical anmes NEVER
    enter the rendered text (ADV-256).

    Fail-closed degenerate windows (total span below ``MIN - 1`` seconds — the
    reachable 0/0 configuration) cannot hold 15 distinct rows; the scaffold
    then renders the rows the window CAN hold (those windows never reach a
    prompt from the driver — the operator guard fails fast first).
    """
    grid = activity_log_timestamp_grid(
        canonical_time, before_minutes=before_minutes, after_minutes=after_minutes
    )
    rows = list(grid[:MIN_ACTIVITY_LOG_ENTRIES])
    # Guaranteed present (the grid's canonical slot is always < MIN, so the
    # scaffold's first 15 members contain it exactly once).
    canon_index = rows.index(canonical_time)
    tokens = tuple(sorted(ACTIVITY_LOG_ACTIVITY_TYPES))
    pool = ACTIVITY_LOG_NEUTRAL_TEXT_POOL
    forbidden = dict(
        person_names=person_names,
        weapon_names=weapon_names,
        motive_names=motive_names,
        location_ids=location_ids,
        location_names=location_names,
    )
    out: list[dict[str, str]] = []
    position = 0  # deterministic cycle index for types/texts
    for index, ts in enumerate(rows):
        if index == canon_index:
            text = _ACTIVITY_LOG_EXAMPLE_CANONICAL_TEXT
            if _neutral_text_collides(text, **forbidden):
                text = _first_safe_fallback_text(0, **forbidden)
            out.append(
                {
                    "timestamp": canonical_time,  # verbatim, exactly once
                    "activityType": _ACTIVITY_LOG_EXAMPLE_CANONICAL_TYPE,
                    "activity": text,
                }
            )
        else:
            text = pool[position % len(pool)]
            if _neutral_text_collides(text, **forbidden):
                text = _first_safe_fallback_text(position % len(ACTIVITY_LOG_NEUTRAL_FALLBACK_POOL), **forbidden)
            out.append(
                {
                    "timestamp": ts,
                    "activityType": tokens[position % len(tokens)],
                    "activity": text,
                }
            )
            position += 1
    return json.dumps(
        {"entries": out}, ensure_ascii=False, separators=(",", ":")
    )


def _activity_log_window_context(
    canonical_time: str,
    *,
    before_minutes: int,
    after_minutes: int,
) -> tuple[str, str, str, str]:
    """Deterministic app-owned activity-log window context (DEF-104).

    Computes the concrete ISO-8601 window endpoints with the SAME
    ``activity_log_window_bounds`` math the strict validator applies and
    renders both endpoints in the canonical instant's OWN timezone offset
    (``epoch_to_iso`` — the exact helper the domain uses for ISO rendering),
    so the prompt and the validator can never drift. Also returns the CLAMPED
    relative minutes the actual window uses, so the relative sentence and the
    concrete bounds always agree even when the 120-minute hard cap bites.

    Returns ``(window_start_iso, window_end_iso, before_minutes_str,
    after_minutes_str)``. Raises ``ValueError`` on an unparseable canonical
    time (a server contract error — identical to the validator's
    determinism: never silently rewrite a locked instant).
    """
    tick, offset = parse_iso8601(canonical_time)
    window_min, window_max = activity_log_window_bounds(
        tick, before_minutes=before_minutes, after_minutes=after_minutes
    )
    return (
        epoch_to_iso(window_min, offset),
        epoch_to_iso(window_max, offset),
        str(max(0, (tick - window_min) // 60)),
        str(max(0, (window_max - tick) // 60)),
    )


ACTIVITY_LOG_PROMPT_v1 = (
    "You are generating a realistic computer activity log for a detective "
    "game — GENERATION_PROVIDER=ollama (prompt template version "
    "activity_log_v1).\n"
    "The server OWNS the canonical evidence fact. The time below is LOCKED: "
    "return it VERBATIM in exactly one entry; never change it, never add a "
    "second occurrence, never call it the crime/murder/attack time.\n\n"
    "Canonical evidence time: __CANONICAL_TIME__\n\n"
    + _CANONICAL_ROW_ANCHOR_SENTENCE
    + "\n\nThe generated" + _ACTIVITY_LOG_SIMULTANEOUS_COMMON
    + "\n\nRequirements:\n"
    f"- Exactly {MIN_ACTIVITY_LOG_ENTRIES} to {MAX_ACTIVITY_LOG_ENTRIES} "
    "chronological entries (strictly increasing unique timestamps).\n"
    f"- Cover approximately the window around the canonical time: from "
    "-__WINDOW_BEFORE__ minutes to +__WINDOW_AFTER__ minutes relative to it "
    f"(the whole log spans at most {WINDOW_HARD_MAX_TOTAL_MINUTES} minutes).\n"
    f"- Every entry timestamp MUST lie inside the deterministic window "
    "[__WINDOW_START_ISO__ .. __WINDOW_END_ISO__] (the whole log spans at "
    f"most {WINDOW_HARD_MAX_TOTAL_MINUTES} minutes).\n"
    + _ACTIVITY_LOG_TIMESTAMP_GRID_BLOCK
    + "- Include the locked canonical evidence time EXACTLY ONCE, in an "
    "ordinary-looking row (e.g. \"Local user activity detected\", \"Foreground "
    "application activity recorded\"); NEVER label it as crime, murder, "
    "attack, death, weapon, evidence, clue, culprit or victim activity.\n"
    "- All other entries must be plausible harmless computer/system actions "
    "(system resume/idle, session unlock/lock, login/logout, file/document "
    "activity, browser/mail/cloud/background sync, USB, network, backup, "
    "application open/close).\n"
    "- Do NOT mention: murder, crime, death, killer, murderer, victim, "
    "weapon, motive, witness, culprit, attack, or any conclusion.\n"
    "- Do NOT introduce named people, named weapons, motives, or locations.\n"
    "\n"
    + _ACTIVITY_LOG_EXAMPLE_IS_SCAFFOLD
    + "\nComplete worked example:\n__WORKED_EXAMPLE__\n\n"
    "Return ONLY a single JSON document matching this EXACT schema:\n"
    + schema_contract("activity_log")
    + "\nNote: 'entries' is a JSON ARRAY of "
    + f"{MIN_ACTIVITY_LOG_ENTRIES}..{MAX_ACTIVITY_LOG_ENTRIES} "
    + "objects, every object exactly like the worked-example rows.\n\n"
    "No HTML, no markdown tables, no prose outside the JSON document."
)


ACTIVITY_LOG_REPAIR_PROMPT_v1 = (
    "You are REPAIRING a computer activity log for a detective game — "
    "GENERATION_PROVIDER=ollama (prompt template version activity_log_repair_v1).\n"
    "The server OWNS the canonical evidence fact. The time below is LOCKED: it "
    "must appear VERBATIM in exactly ONE of the "
    f"{MIN_ACTIVITY_LOG_ENTRIES} to {MAX_ACTIVITY_LOG_ENTRIES} entries; never "
    "change it, never add a second occurrence, never call it the "
    "crime/murder/attack time.\n\n"
    "Canonical evidence time: __CANONICAL_TIME__\n\n"
    + _CANONICAL_ROW_ANCHOR_SENTENCE
    + "\n\nThe repaired" + _ACTIVITY_LOG_SIMULTANEOUS_COMMON
    + "\n\nYour PREVIOUS activity log failed validation. The repaired response is a "
    "COMPLETE replacement ActivityLogDocument with "
    f"{MIN_ACTIVITY_LOG_ENTRIES} to {MAX_ACTIVITY_LOG_ENTRIES} entries - never "
    "a single surrounding entry, never a patched wrapper; the locked canonical "
    "time is ONE of those rows.\n\n"
    "Fix EXACTLY the machine-readable findings below (deterministic app rules):\n"
    "__FINDINGS__\n\n"
    "Findings-to-fix mapping (deterministic): if a finding says "
    "CANONICAL_TIME_MISSING, the repair MUST include the locked canonical time "
    "verbatim as exactly one of the "
    f"{MIN_ACTIVITY_LOG_ENTRIES}-{MAX_ACTIVITY_LOG_ENTRIES} rows; if "
    "TIMESTAMP_OUTSIDE_WINDOW, every row MUST be inside the deterministic "
    "window bounds above.\n"
    + _ACTIVITY_LOG_REPAIR_GRID_LINE
    + "\n"
    + _ACTIVITY_LOG_REPAIR_RECHECK_LINE
    + "\n\nRequirements (deterministic, reapplied after this repair):\n"
    f"- Exactly {MIN_ACTIVITY_LOG_ENTRIES} to {MAX_ACTIVITY_LOG_ENTRIES} "
    "chronological entries (strictly increasing unique timestamps).\n"
    f"- Cover approximately the window around the canonical time: from "
    "-__WINDOW_BEFORE__ minutes to +__WINDOW_AFTER__ minutes relative to it "
    f"(the whole log spans at most {WINDOW_HARD_MAX_TOTAL_MINUTES} minutes).\n"
    f"- Every entry timestamp MUST lie inside the deterministic window "
    "[__WINDOW_START_ISO__ .. __WINDOW_END_ISO__] (the whole log spans at "
    f"most {WINDOW_HARD_MAX_TOTAL_MINUTES} minutes).\n"
    + _ACTIVITY_LOG_TIMESTAMP_GRID_BLOCK
    + "- Include the locked canonical evidence time EXACTLY ONCE, in an "
    "ordinary-looking row (e.g. \"Local user activity detected\", \"Foreground "
    "application activity recorded\"); NEVER label it as crime, murder, "
    "attack, death, weapon, evidence, clue, culprit or victim activity.\n"
    "- All other entries must be plausible harmless computer/system actions "
    "(no named people, weapons, motives or locations; no crime wording).\n"
    "\n"
    + _ACTIVITY_LOG_EXAMPLE_IS_SCAFFOLD
    + "\nComplete worked example:\n__WORKED_EXAMPLE__\n\n"
    "Return ONLY a single JSON document matching this EXACT schema:\n"
    + schema_contract("activity_log")
    + "\nNote: 'entries' is a JSON ARRAY of "
    + f"{MIN_ACTIVITY_LOG_ENTRIES}..{MAX_ACTIVITY_LOG_ENTRIES} "
    + "objects, every object exactly like the worked-example rows.\n\n"
    "No HTML, no markdown tables, no prose outside the JSON document."
)


# ---------------------------------------------------------------------------
# internal prompt-building helpers (fill every placeholder; never leave a hole)
# ---------------------------------------------------------------------------


def _locked_contract_line(locked: Mapping[str, Any] | None) -> str:
    if not locked:
        return "(none)"
    parts = []
    for key, value in locked.items():
        if value is not None:
            parts.append(f"- {key}: {value}")
    if not parts:
        return "(none)"
    return "\n".join(parts)


def _fill(*, template: str, **tokens: str) -> str:
    out = template
    for key, value in tokens.items():
        out = out.replace(f"__{key}__", value if value is not None else "")
    return out


def build_case_people_prompt(
    prompt: str,
    locked: Mapping[str, Any] | None,
    *,
    id_sheet: str = "",
) -> str:
    return _fill(
        template=CASE_PEOPLE_PROMPT_v1,
        PROMPT=prompt,
        LOCKED=_locked_contract_line(locked),
        ID_SHEET=id_sheet,
    )


def build_evidence_prompt(
    prompt: str,
    locked: Mapping[str, Any] | None,
    *,
    id_sheet: str = "",
    approved_people: str = "",
    deduction_seed: str = "",
    deduction_feedback: str = "",
) -> str:
    return _fill(
        template=EVIDENCE_PROMPT_v1,
        PROMPT=prompt,
        LOCKED=_locked_contract_line(locked),
        ID_SHEET=id_sheet,
        APPROVED_PEOPLE=approved_people,
        DEDUCTION_SEED=deduction_seed,
        DEDUCTION_FEEDBACK=deduction_feedback,
    )


def build_world_requirements_prompt(
    prompt: str,
    locked: Mapping[str, Any] | None,
    *,
    id_sheet: str = "",
    weapon_evidence_id: str = "",
) -> str:
    sheet = id_sheet
    if not sheet:
        sheet = ""
    evidence_line = ""
    if weapon_evidence_id:
        evidence_line = (
            f"The weapon object carries sealed evidence id {weapon_evidence_id!r}. "
            f"Set the weapon object's evidenceId to that EXACT id AND its "
            f"requiredInteraction to 'inspect' — BOTH fields in the SAME "
            "object entry, never one without the other (a placement with an "
            "evidenceId but no interaction is rejected)."
        )
    return _fill(
        template=WORLD_REQUIREMENTS_PROMPT_v1,
        PROMPT=prompt,
        LOCKED=_locked_contract_line(locked),
        ID_SHEET=sheet,
        WEAPON_EVIDENCE_ID=evidence_line,
    )


def build_asset_spec_prompt(
    object_concept: str, category_hint: str | None = None
) -> str:
    return _fill(
        template=ASSET_SPEC_PROMPT_v1,
        OBJECT_CONCEPT=object_concept,
        CATEGORY_HINT=category_hint or "none",
        CANONICAL_CONCEPT=_canonical_concept_sentence(object_concept),
    )


def build_asset_spec_repair_prompt(
    object_concept: str, previous_candidate: str, issues: tuple[str, ...]
) -> str:
    return _fill(
        template=ASSET_SPEC_REPAIR_PROMPT_v1,
        OBJECT_CONCEPT=object_concept,
        PREVIOUS_CANDIDATE=previous_candidate,
        ISSUES="\n".join(f"- {i}" for i in issues),
        CANONICAL_CONCEPT=_canonical_concept_sentence(object_concept),
    )


def build_repair_prompt(previous_draft: str, issues: tuple[str, ...]) -> str:
    return _fill(
        template=REPAIR_PROMPT_v1,
        PREVIOUS_DRAFT=previous_draft,
        ISSUES="\n".join(f"- {i}" for i in issues),
    )


def build_activity_log_prompt(
    canonical_time: str,
    *,
    before_minutes: int = WINDOW_DEFAULT_BEFORE_MINUTES,
    after_minutes: int = WINDOW_DEFAULT_AFTER_MINUTES,
    person_names: Iterable[str] = (),
    weapon_names: Iterable[str] = (),
    motive_names: Iterable[str] = (),
    location_ids: Iterable[str] = (),
    location_names: Iterable[str] = (),
) -> str:
    """The Phase 19J activity-log prompt (locked canonical time + window).

    ``canonical_time`` is the app-owned evidence time injected as the locked
    constraint; the provider only ever receives this one temporal fact plus the
    neutral generation rules — no CaseTruth, no person/weapon/motive names.

    Phase19J-RI (DEF-104): the concrete deterministic window endpoints
    (``[WINDOW_START_ISO .. WINDOW_END_ISO]``) and the CLAMPED relative
    minutes are computed with the EXACT same ``activity_log_window_bounds``
    math the strict validator applies, so the prompt and the validator share
    one window and a stateless model never has to compute the relative
    ±N-minute instant range itself. DEF-104 follow-up #2 also injects a
    COMPLETE 15-row worked example (same canonical time, same window,
    strictly increasing +3-minute rows, canonical verbatim once, closed-enum
    types, neutral texts) as a shape scaffold — never the answer.
    DEF-104 follow-up #3 additionally injects the app-owned TIMESTAMP GRID
    (same deterministic math): the model must copy the GIVEN timestamps
    verbatim into the rows, so the canonical-once property is a pure copy task.

    Phase19J-RI (ADV-C): the grid and scaffold are ADAPTIVE to the actual
    clamped window span, so every satisfiable window (including 30/0 or 0/30)
    yields a self-validating scaffold; the operator guard fails fast before
    this builder for an unsatisfiable window. ADV-D: the canonical forbidden
    name/location sets are accepted ONLY for deterministic collision
    substitution inside the scaffold (reserved fallback pool); they are NEVER
    rendered into the prompt text (ADV-256 holds).
    """
    start_iso, end_iso, before_min, after_min = _activity_log_window_context(
        canonical_time, before_minutes=before_minutes, after_minutes=after_minutes
    )
    worked_example = _activity_log_worked_example_json(
        canonical_time,
        before_minutes=before_minutes,
        after_minutes=after_minutes,
        person_names=person_names,
        weapon_names=weapon_names,
        motive_names=motive_names,
        location_ids=location_ids,
        location_names=location_names,
    )
    return _fill(
        template=ACTIVITY_LOG_PROMPT_v1,
        CANONICAL_TIME=canonical_time,
        WINDOW_BEFORE=before_min,
        WINDOW_AFTER=after_min,
        WINDOW_START_ISO=start_iso,
        WINDOW_END_ISO=end_iso,
        TIMESTAMP_GRID=", ".join(
            activity_log_timestamp_grid(
                canonical_time,
                before_minutes=before_minutes,
                after_minutes=after_minutes,
            )
        ),
        WORKED_EXAMPLE=worked_example,
    )


def build_activity_log_repair_prompt(
    canonical_time: str,
    findings: tuple[str, ...],
    *,
    before_minutes: int = WINDOW_DEFAULT_BEFORE_MINUTES,
    after_minutes: int = WINDOW_DEFAULT_AFTER_MINUTES,
    person_names: Iterable[str] = (),
    weapon_names: Iterable[str] = (),
    motive_names: Iterable[str] = (),
    location_ids: Iterable[str] = (),
    location_names: Iterable[str] = (),
) -> str:
    """The Phase 19J repair prompt: machine-readable findings + locked time.

    The findings are the ONLY feedback (never the rejected log, never
    CaseTruth beyond the locked temporal constraint), so the repair can never
    be steered by content or hidden truth (Phase19J §20). The deterministic
    window is restated from the SAME app-owned configuration the initial
    prompt uses (Phase19J-RI: the repair must blind the model to the complete
    contract - count floor/ceiling, chronology, canonical-once, window).

    Phase19J-RI (DEF-104): the exponential repair also receives the concrete
    ISO-8601 window endpoints and the canonical-anchor example row, because a
    stateless 8B model cannot reliably compute the relative ±N-minute window
    itself; the mapping from the machine-readable findings tokens to the fix
    (CANONICAL_TIME_MISSING / TIMESTAMP_OUTSIDE_WINDOW) is restated
    explicitly. DEF-104 follow-up #2 additionally injects the SAME complete
    15-row worked example shape scaffold (valid under the actual configured
    window) and the repair recheck line ("never trade one rule for another").
    DEF-104 follow-up #3 additionally restates the SAME app-owned TIMESTAMP
    GRID: the canonical missing finding is resolved by copying the grid's
    canonical row unchanged. ADV-C/ADV-D: same adaptive grid + deterministic
    scaffold collision substitution as the initial prompt (operational guard
    still fails fast before an unsatisfiable window ever reaches a builder).
    """
    start_iso, end_iso, before_min, after_min = _activity_log_window_context(
        canonical_time, before_minutes=before_minutes, after_minutes=after_minutes
    )
    worked_example = _activity_log_worked_example_json(
        canonical_time,
        before_minutes=before_minutes,
        after_minutes=after_minutes,
        person_names=person_names,
        weapon_names=weapon_names,
        motive_names=motive_names,
        location_ids=location_ids,
        location_names=location_names,
    )
    return _fill(
        template=ACTIVITY_LOG_REPAIR_PROMPT_v1,
        CANONICAL_TIME=canonical_time,
        FINDINGS="\n".join(f"- {finding}" for finding in findings) or "- SCHEMA_INVALID",
        WINDOW_BEFORE=before_min,
        WINDOW_AFTER=after_min,
        WINDOW_START_ISO=start_iso,
        WINDOW_END_ISO=end_iso,
        TIMESTAMP_GRID=", ".join(
            activity_log_timestamp_grid(
                canonical_time,
                before_minutes=before_minutes,
                after_minutes=after_minutes,
            )
        ),
        WORKED_EXAMPLE=worked_example,
    )


__all__ = [
    "ACTIVITY_LOG_GRID_COUNT",
    "ACTIVITY_LOG_NEUTRAL_FALLBACK_POOL",
    "ACTIVITY_LOG_NEUTRAL_TEXT_POOL",
    "ACTIVITY_LOG_PROMPT_v1",
    "ACTIVITY_LOG_REPAIR_PROMPT_v1",
    "ASSET_SPEC_PROMPT_v1",
    "ASSET_SPEC_REPAIR_PROMPT_v1",
    "CASE_PEOPLE_PROMPT_v1",
    "CONTRACT_KEYS",
    "EVIDENCE_PROMPT_v1",
    "REPAIR_PROMPT_v1",
    "STAGE_TO_CONTRACT",
    "STAGE_TO_PROMPT_VERSION",
    "WORLD_REQUIREMENTS_PROMPT_v1",
    "activity_log_timestamp_grid",
    "build_activity_log_prompt",
    "build_activity_log_repair_prompt",
    "build_asset_spec_prompt",
    "build_asset_spec_repair_prompt",
    "build_case_people_prompt",
    "build_evidence_prompt",
    "build_repair_prompt",
    "build_world_requirements_prompt",
    "json_schema_for_generation_stage",
    "schema_contract",
    "schema_contract_as_json_schema",
]
