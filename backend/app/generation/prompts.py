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
_METERS_STATEMENT = (
    "ALL dimensions, positions, rotations and scales are in METERS. "
    "0.25 means 25 centimeters; 25 means 25 meters. Use 0.05..4 for "
    "dimensions and 0.05..2 for part scale."
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


def schema_contract(stage: str) -> str:
    """Deterministic JSON text of the per-stage schema skeleton (authoritative).

    ``stage`` is one of ``case_people`` / ``evidence`` / ``world_requirements`` /
    ``asset_spec``. The returned text carries the exact numeric bounds from the
    authoritative schema constants. A unit test asserts the rendered values
    equal the constants (schema-drift guard).
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
    return json.dumps(contract, sort_keys=True, ensure_ascii=False, indent=2)


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
        f"- At most {MAX_PARTS} parts.\n"
        f"- Use ONLY the primitives: {sorted(PRIMITIVE_ALLOWLIST)}.\n"
        f"- Use ONLY the materials: {sorted(MATERIAL_VOCAB)}.\n"
        "- All part ids unique (part_00..part_23).\n"
        "- Bounded finite dimensions/position/rotation/scale (see schema).\n"
        "- Maximum parent depth 2 (a parent must be an EARLIER part).\n"
        "- NO URLs, paths, HTML, scripts, shaders, event handlers, or executable code.\n"
        "- Prefer simple, recognizable silhouettes; use as few parts as necessary.\n"
        "- Do NOT place all parts at the same position.\n"
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
    "Return ONLY a single JSON document with this EXACT schema:\n"
    + schema_contract("case_people")
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
    "Return ONLY a single JSON document with this EXACT schema:\n"
    + schema_contract("evidence")
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
    "Return ONLY a single JSON document with this EXACT schema:\n"
    + schema_contract("world_requirements")
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
    + "\nDo not loosen or reinterpret the constraints. The repaired AssetSpec "
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
    "worldGraph) matching the existing draft schema.\n"
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
    "EVIDENCE_PROMPT_v1",
    "REPAIR_PROMPT_v1",
    "WORLD_REQUIREMENTS_PROMPT_v1",
    "build_asset_spec_prompt",
    "build_asset_spec_repair_prompt",
    "build_case_people_prompt",
    "build_evidence_prompt",
    "build_repair_prompt",
    "build_world_requirements_prompt",
    "schema_contract",
]
