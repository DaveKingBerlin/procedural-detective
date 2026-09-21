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
from typing import Any, Mapping

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
from app.domain.evidence import PROPOSITION_TYPES
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
_ENVIRONMENT_ENUM_HINT = (
    "canonical environment token; enum " + ",".join(ENVIRONMENT_IDS) +
    " (use EXACTLY one of those EXACT tokens — never a path, never a location "
    "phrase like 'hotel suite', never a free text word or null)"
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


def schema_contract(stage: str) -> str:
    """Deterministic JSON text of the per-stage schema skeleton (authoritative).

    ``stage`` is one of ``case_people`` / ``evidence`` / ``world_requirements`` /
    ``asset_spec`` / ``full_draft``. The returned text carries the exact numeric
    bounds from the authoritative schema constants. A unit test asserts the
    rendered values equal the constants (schema-drift guard). Rendered from the
    single ``_stage_contract`` source (Phase17B: the transport JSON Schema and
    the prompt share this mapping — no duplicate).
    """
    return json.dumps(
        _stage_contract(stage), sort_keys=True, ensure_ascii=False, indent=2
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
#   repair          -> REPAIR          -> repair_v1             -> full_draft
STAGE_TO_PROMPT_VERSION: Mapping[str, str] = {
    "case_truth": "case_people_v1",
    "evidence": "evidence_v1",
    "world_graph": "world_requirements_v1",
    "asset_spec": "asset_spec_v1",
    "asset_spec_repair": "asset_spec_repair_v1",
    "repair": "repair_v1",
}
STAGE_TO_CONTRACT: Mapping[str, str] = {
    "case_truth": "case_people",
    "evidence": "evidence",
    "world_graph": "world_requirements",
    "asset_spec": "asset_spec",
    "asset_spec_repair": "asset_spec",
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
        if "partSchema" in container:
            max_parts = container.pop("maxParts", None)
            schema: dict[str, Any] = {
                "type": "array",
                "items": _contract_to_json_schema(container.pop("partSchema")),
            }
            if isinstance(max_parts, int) and not isinstance(max_parts, bool):
                schema["maxItems"] = int(max_parts)
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


__all__ = [
    "ASSET_SPEC_PROMPT_v1",
    "ASSET_SPEC_REPAIR_PROMPT_v1",
    "CASE_PEOPLE_PROMPT_v1",
    "CONTRACT_KEYS",
    "EVIDENCE_PROMPT_v1",
    "REPAIR_PROMPT_v1",
    "STAGE_TO_CONTRACT",
    "STAGE_TO_PROMPT_VERSION",
    "WORLD_REQUIREMENTS_PROMPT_v1",
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
