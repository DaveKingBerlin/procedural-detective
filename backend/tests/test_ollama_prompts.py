"""Phase16_2 — prompt-template tests (DELIVERABLE 1 + §12/§13/§14/§15).

Verifies:
- the rendered per-stage schema contracts carry the AUTHORITATIVE bound
  constants (no schema drift between templates and the strict parsers);
- every template embeds meters/units, bounds, the material allowlist, the
  primitive allowlist and the "no truth / no code / JSON-only" rules;
- repair templates re-state meters and carry the exact repair instruction;
- no template ever contains a URL scheme, path, script/shader/handler token or
  any hidden-truth/secret keyword;
- the material/primitive allowlists are the real application vocabularies
  (a foreign token like ``bronze`` is NOT allowed; ``metal.brass`` IS).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets.materials import MATERIAL_VOCAB  # noqa: E402
from app.assets.specs import (  # noqa: E402
    DIMENSION_MAX,
    DIMENSION_MIN,
    MAX_PARTS,
    MAX_PART_SCALE,
    MAX_POSITION_BOUND,
    MIN_PART_SCALE,
    PRIMITIVE_ALLOWLIST,
)
from app.assets.catalog import CATEGORY_ALLOWLIST  # noqa: E402
from app.domain.evidence import PROPOSITION_TYPES  # noqa: E402
from app.generation import prompts  # noqa: E402


def test_asset_spec_contract_matches_authoritative_bounds():
    text = prompts.schema_contract("asset_spec")
    assert f"[{DIMENSION_MIN:g}, {DIMENSION_MAX:g}]" in text
    assert f"[{MIN_PART_SCALE:g}, {MAX_PART_SCALE:g}]" in text
    assert f"{MAX_PARTS} parts" in text
    assert f"|axis| <= {MAX_POSITION_BOUND:g}" in text
    # every authoritative material token is present verbatim.
    for token in sorted(MATERIAL_VOCAB):
        assert token in text
    # every primitive is present; nothing outside the real allowlist.
    blob = prompts.build_asset_spec_prompt("ice pick")
    for prim in sorted(PRIMITIVE_ALLOWLIST):
        assert prim in blob
    # the frozen category vocabulary is the prompt's category contract.
    for cat in CATEGORY_ALLOWLIST:
        assert cat in text


def test_asset_spec_templates_embed_meters_statement():
    # The meters statement is a GEOMETRY contract — it belongs in the AssetSpec
    # templates (dimensions/scale are the only numeric values that are lengths).
    for builder in (prompts.build_asset_spec_prompt, prompts.build_asset_spec_repair_prompt):
        blob = builder("ice pick", None) if builder is prompts.build_asset_spec_prompt else builder("ice pick", "{}", ("a",))
        assert "0.25 means 25 centimeters" in blob
    # the asset_spec schema contract carries the meters unit note.
    assert "METERS" in prompts.schema_contract("asset_spec")


def test_no_internals_and_no_code_in_every_template():
    # NO hidden-truth/secret keywords, and NO executable code tokens/schemes
    # (the word "script"/"handler" appears ONLY inside the safe 'no scripts'
    # rule — checked separately below, so we scan for actual code patterns).
    forbidden = (
        "http://", "https://", "data:", "file:", "javascript:",
        "solverProof", "caseTruth", "timeline", "relationships",
    )
    code_patterns = ("<script", "<SCRIPT", "new Function", "eval(", "require(",
                     "babylon.code", "shader) {")
    templates = (
        prompts.ASSET_SPEC_PROMPT_v1,
        prompts.ASSET_SPEC_REPAIR_PROMPT_v1,
        prompts.CASE_PEOPLE_PROMPT_v1,
        prompts.EVIDENCE_PROMPT_v1,
        prompts.WORLD_REQUIREMENTS_PROMPT_v1,
        prompts.REPAIR_PROMPT_v1,
    )
    for template in templates:
        lowered = template.lower()
        for token in forbidden:
            assert token not in lowered, f"{token!r} leaked into a template"
        for pattern in code_patterns:
            assert pattern not in template


def test_no_scripts_rules_are_bounded_instructions():
    """The templates forbid scripts/shaders/handlers — they never invite them."""
    blob = prompts.build_asset_spec_prompt("ice pick")
    assert "NO URLs, paths, HTML, scripts, shaders, event handlers" in blob
    # and they never contain a live script tag / URL scheme.
    assert "<script" not in blob and "javascript:" not in blob


def test_json_only_and_bounded_rules_are_stated():
    blob = prompts.build_asset_spec_prompt("ice pick")
    assert "JSON only" in blob or "single JSON document" in blob
    assert f"At most {MAX_PARTS} parts" in blob
    assert "no markdown fences" in blob
    assert "unique" in blob.lower()
    assert "Maximum parent depth 2" in blob
    assert "Do NOT place all parts at the same position" in blob
    assert "prefer simple, recognizable silhouettes" in blob.lower()


def test_material_allowlist_rejects_foreign_token():
    """The embedded material vocabulary is the REAL app allowlist; a token like
    ``bronze`` (not in MATERIAL_VOCAB) must never be presented as allowed."""
    blob = prompts.build_asset_spec_prompt("ceremonial ice pick")
    assert "metal.brass" in blob          # real member
    assert "metal.steel" in blob          # real member
    assert "bronze" not in blob           # NOT a member — rejected upstream
    assert "metal.bronze" not in blob     # NOT a member


def test_primitive_allowlist_rejects_foreign_primitive():
    blob = prompts.build_asset_spec_prompt("ice pick")
    assert "capsule" not in blob
    assert "extruded_polygon" not in blob
    for prim in sorted(PRIMITIVE_ALLOWLIST):
        assert prim in blob


def test_asset_spec_repair_prompt_restates_meters_and_instruction():
    concept = "bronze ceremonial ice pick"
    candidate = '{"canonicalName":"x","dimensions":{"x":25,"y":0.1,"z":0.1},"parts":[]}'
    issues = ("dimensions.x outside allowed range", "material bronze is not allowlisted")
    blob = prompts.build_asset_spec_repair_prompt(concept, candidate, issues)
    assert "0.25 means 25 centimeters" in blob
    assert "Correct the AssetSpec" in blob
    assert "Fix the listed validation violations" in blob
    assert "Return the complete corrected AssetSpec as JSON only" in blob
    assert concept in blob
    assert candidate in blob
    for issue in issues:
        assert issue in blob
    # the repair prompt must re-state the real allowlists.
    assert "metal.brass" in blob


def test_schema_contract_no_drift_against_parser_constants():
    """The rendered asset-spec contract's bound strings equal the constants the
    strict Phase 13 parser enforces (schema drift guard)."""
    text = prompts.schema_contract("asset_spec")
    # The parser bounds are these same constants; embedding them guarantees no
    # hand-maintained approximation can drift.
    assert str(DIMENSION_MIN) in text or f"{DIMENSION_MIN:g}" in text
    assert str(DIMENSION_MAX) in text or f"{DIMENSION_MAX:g}" in text
    assert str(MAX_PARTS) in text
    for token in sorted(MATERIAL_VOCAB):
        assert token in text
    for prim in sorted(PRIMITIVE_ALLOWLIST):
        assert prim in text


def test_empty_placeholders_are_filled():
    assert "PROMPT" not in prompts.build_case_people_prompt("x", {})
    assert "__OBJECT_CONCEPT__" not in prompts.build_asset_spec_prompt("ice pick")
    assert "__ISSUES__" not in prompts.build_asset_spec_repair_prompt(
        "ice pick", "{}", ("i",)
    )


def test_evidence_transport_schema_uses_the_strict_proposition_enum():
    """Ollama's grammar gets the parser's closed vocabulary, not just string."""
    schema = prompts.schema_contract_as_json_schema("evidence")
    proposition_type = (
        schema["properties"]["evidence"]["items"]["properties"]
        ["propositions"]["items"]["properties"]["type"]
    )
    assert proposition_type == {
        "type": "string",
        "enum": sorted(PROPOSITION_TYPES),
    }
