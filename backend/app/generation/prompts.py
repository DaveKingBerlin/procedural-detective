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

# --- schema-contract builder (deterministic, authoritative) ----------------
#
# ``schema_contract`` renders the JSON skeleton the stage templates embed. It
# reads ONLY the frozen constants (number bounds, allowlists) from the
# authoritative schema modules so the prompt and the strict parser share one
# source of truth. Every bound in the rendered text is literally the constant
# value (interpolated, never a copy).

# Meter-units statement used by every numeric contract (Phase16_2 §15).
# Phase17B: the worked examples make the meter/centimeter mapping concrete so a
# Hermes-class model cannot return "25" for a 25-centimetre value.
_METERS_STATEMENT = (
    "ALL dimensions, positions, rotations and scales are in METERS. "
    "0.25 means 25 centimeters; 25 means 25 meters. Use 0.05..4 for "
    "dimensions and 0.05..2 for part scale.\n"
    "METER WORKED EXAMPLES: a hand-held pick is about 0.05 by 0.5 by 0.05 "
    "meters (5cm x 50cm x 5cm); a desk lamp is about 0.2 by 0.4 by 0.2 "
    "meters; a coin is about 0.02 (write 0.02, never 2). NEVER write 25 for "
    "a 25-centimeter value: write 0.25."
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
                                "one of the existing proposition types; never a "
                                "truth/final verdict declaration"
                            ),
                            "personId": "person id or null",
                            "locationId": "location id or null",
                            "objectId": "object id or null",
                            "motiveId": "motive id or null",
                            "observedAt": "ISO-8601 timestamp or null",
                            "uncertaintySeconds": "non-negative int",
                            "structured": "object",
                        }
                    ],
                    "presentation": {"title": "string", "description": "string"},
                }
            ]
        }
    elif stage == "world_requirements":
        contract = {
            "environmentHint": "a location word from: apartment/flat/condo, "
            "office/company/workplace, hotel/room/suite, warehouse/depot/storage, "
            "mansion/villa/manor",
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
            "unsafeUnsupported": ["sanitized diagnostic-only notes"],
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


def schema_contract(stage: str) -> str:
    """Deterministic JSON text of the per-stage schema skeleton (authoritative).

    ``stage`` is one of ``case_people`` / ``evidence`` / ``world_requirements`` /
    ``asset_spec``. The returned text carries the exact numeric bounds from the
    authoritative schema constants. A unit test asserts the rendered values
    equal the constants (schema-drift guard). Rendered from the single
    ``_stage_contract`` source (Phase17B: the transport JSON Schema and the
    prompt share this mapping — no duplicate).
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
#   repair          -> REPAIR          -> repair_v1             -> (full draft; no contract)
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
}
# The authoritative schema-contract vocabulary (a closed set; the stale-version
# guard test asserts its members are exactly the ones shipped).
CONTRACT_KEYS: frozenset[str] = frozenset(
    {"case_people", "evidence", "world_requirements", "asset_spec"}
)


def _hint_nullable(hint: str) -> bool:
    return "null" in hint.casefold()


def _key_required(value: Any) -> bool:
    """Requiredness rule for the derived JSON Schema: containers and scalars are
    always required; only string hints mentioning ``null`` mark a property
    optional. Never applied to a container's repr (which may coincidentally
    mention ``null`` inside a nested hint)."""
    if isinstance(value, (Mapping, list, tuple, bool, int, float)):
        return True
    return not _hint_nullable(str(value))


def _hint_json_types(hint: str) -> tuple[str, ...]:
    """Deterministic primitive-type tuple derived from a contract hint string.

    Scalar types only — array/object structure comes from the contract shape.
    Used to build the permissive transport JSON Schema (the strict parsers stay
    the sole acceptance authority).
    """
    text = hint.casefold()
    if "bool" in text:
        return ("boolean",)
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
    return {"type": list(types) if len(types) > 1 else types[0]}


def schema_contract_as_json_schema(stage: str) -> dict[str, Any]:
    """The authoritative transport JSON Schema (Ollama ``format`` object).

    Derived from the SAME ``_stage_contract(stage)`` mapping the prompt embeds
    — one source, never a duplicate (Phase17B §2).
    """
    return _contract_to_json_schema(_stage_contract(stage))


def json_schema_for_generation_stage(stage_value: str) -> dict[str, Any] | None:
    """The authoritative JSON Schema for a ``GenerationStage.value``, or None
    when the stage has no schema contract (only ``repair`` — the full-draft
    REPAIR prompt has no per-stage skeleton; its transport ``format`` stays
    ``"json"``)."""
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
    "- proposition type is one of the existing proposition types - never an "
    "invented token, never a verdict or final-truth declaration.\n"
    "- observedAt is ISO-8601 or null; uncertaintySeconds is a plain "
    "non-negative integer.\n"
    "- Do not invent keys, ids or enums; never assert the solution.\n"
)


_WORLD_FIELD_RULES = (
    "\n\nFIELD RULES (strict):\n"
    "- The document uses EXACTLY: environmentHint, locationTokens, objects, "
    "relations, unsafeUnsupported.\n"
    "- environmentHint is one exact location word from the schema list "
    "(apartment/flat/condo, office/company/workplace, hotel/room/suite, "
    "warehouse/depot/storage, mansion/villa/manor).\n"
    "- objects entries use EXACTLY: name, categoryHint, subtypeHint, tags, "
    "requiredInteraction, evidenceId, criticality.\n"
    "- requiredInteraction is inspect, read or null; criticality is exactly "
    "required or decorative.\n"
    "- relations kind is one exact token from: on_desk, on_table, "
    "near_victim, inside_cabinet, floor_area, on_wall.\n"
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
    + _NO_INTERNALS
    + "\nNote: the crime TIME and the crime identity come from the locked "
    "constraints plus generated filler. Never declare a final verdict."
)


EVIDENCE_PROMPT_v1 = (
    "You are generating the EVIDENCE stage of a detective case for "
    "GENERATION_PROVIDER=ollama (prompt template version evidence_v1).\n"
    "Return ONLY the JSON object for this stage with this EXACT schema:\n"
    + schema_contract("evidence")
    + _EVIDENCE_FIELD_RULES
    + "\n\nSanitized user prompt:\n__PROMPT__\n\n"
    "LOCKED user constraints:\n__LOCKED__\n\n"
    "Generate a bounded set of structured evidence facts (email/message, "
    "physical evidence, financial record, access/CCTV/event record, "
    "alibi/public statement, optional red herring). Use unique bounded ids, "
    "reference existing public people/objects, use valid timestamps, and stay "
    "within content-size limits.\n"
    "NEVER declare that the case is solved. Never assert who the murderer is "
    "as an answer - the deterministic solver derives it from your facts.\n"
    "No hidden truth flags, no reliability-as-a-truth-flag, no URLs, no "
    "executable content, no scripts or event handlers.\n"
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
    "any LOCKED user constraint. Return ONLY a single COMPLETE JSON document "
    "(crime, persons, motives, objects, locations, travelRules, scene, evidence, "
    "worldGraph) matching the existing draft schema. Use EXACTLY those section "
    "key names - never rename, drop or invent keys, sections or enums.\n"
    "__PREVIOUS_DRAFT__\n\n"
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


def build_case_people_prompt(prompt: str, locked: Mapping[str, Any] | None) -> str:
    return _fill(
        template=CASE_PEOPLE_PROMPT_v1,
        PROMPT=prompt,
        LOCKED=_locked_contract_line(locked),
    )


def build_evidence_prompt(prompt: str, locked: Mapping[str, Any] | None) -> str:
    return _fill(
        template=EVIDENCE_PROMPT_v1,
        PROMPT=prompt,
        LOCKED=_locked_contract_line(locked),
    )


def build_world_requirements_prompt(prompt: str, locked: Mapping[str, Any] | None) -> str:
    return _fill(
        template=WORLD_REQUIREMENTS_PROMPT_v1,
        PROMPT=prompt,
        LOCKED=_locked_contract_line(locked),
    )


def build_asset_spec_prompt(
    object_concept: str, category_hint: str | None = None
) -> str:
    return _fill(
        template=ASSET_SPEC_PROMPT_v1,
        OBJECT_CONCEPT=object_concept,
        CATEGORY_HINT=category_hint or "none",
    )


def build_asset_spec_repair_prompt(
    object_concept: str, previous_candidate: str, issues: tuple[str, ...]
) -> str:
    return _fill(
        template=ASSET_SPEC_REPAIR_PROMPT_v1,
        OBJECT_CONCEPT=object_concept,
        PREVIOUS_CANDIDATE=previous_candidate,
        ISSUES="\n".join(f"- {i}" for i in issues),
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
