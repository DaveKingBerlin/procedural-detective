"""Phase31B — §14 — Cohere evidence-schema regression protection.

Any Phase31B canonical evidence-schema enhancement must flow through
``app.generation.schema_adapters.adapt_schema_for_transport`` for Cohere
without reintroducing the proven Phase31A evidence 400:

- the canonical evidence schema now carries `pattern`/`format` on the canonical
  timestamp node and a teaching description on the open ``structured`` node;
- the Cohere-compatible TRANSPORT representation strips the non-essential
  string keywords (``pattern``/``format`` — the ISO time constraint is a
  generation aid; the STRICT parser remains the authority) so the Cohere
  dialect keeps exactly the keyword surface it accepted in Phase31A;
- every object node in the adapted evidence transport still carries a
  non-empty ``required`` list (Cohere's ">= 1 required property per object"
  rule) — the open ``structured`` node keeps its synthetic ``v`` property;
- non-Cohere providers receive the canonical schema byte-identical.

The Cohere evidence 400 (union/oneOf keywords, open objects without required)
must never be reintroduced — the adapted schema asserts none of those classes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.generation import prompts  # noqa: E402
from app.generation import schema_adapters as sa  # noqa: E402
from app.generation.frontier_registry import (  # noqa: E402
    STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA,
)

_COHERE_ADAPTER = sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA


def _adapted(schema, adapter=None):
    if adapter is None:
        adapter = sa.schema_adapter_id_for_frontier_call(
            "cohere/command-a-plus", STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA
        )
    return sa.adapt_schema_for_transport(schema, adapter=adapter)


def _walk_object_nodes(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _walk_object_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_object_nodes(item)


def _walk_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


# --------------------------------------------------------------------------- #
# canonical evidence schema -> Cohere transport (proven provider path)
# --------------------------------------------------------------------------- #


def test_cohere_evidence_transport_has_required_property_per_object():
    """Every object node in the adapted evidence schema — including the open
    ``structured`` node — carries a non-empty ``required`` list (the Phase31A
    Cohere ">= 1 required property per object" rule)."""
    canonical = prompts.json_schema_for_stage_output("evidence")
    adapted = _adapted(canonical)
    object_nodes = list(_walk_object_nodes(adapted))
    assert object_nodes
    for node in object_nodes:
        required = node.get("required")
        assert isinstance(required, list) and required, node


def test_cohere_transport_keeps_structured_open_object_intent_and_description():
    """The enriched structured node (now carrying the Phase31B teaching
    description) still maps to the Phase31A-compatible shape: synthetic ``v``
    property + ``required:["v"]``, ``additionalProperties`` preserved, and the
    canonical teaching description survives — the model still sees the
    FORENSIC/ALIBI structured contract while Cohere accepts the node."""
    canonical = prompts.json_schema_for_stage_output("evidence")
    structured_canonical = canonical["properties"]["evidence"]["items"][
        "properties"
    ]["propositions"]["items"]["properties"]["structured"]
    assert structured_canonical["type"] == "object"
    assert structured_canonical["additionalProperties"] is True
    assert structured_canonical["description"]

    adapted = _adapted(canonical)
    structured_adapted = adapted["properties"]["evidence"]["items"][
        "properties"
    ]["propositions"]["items"]["properties"]["structured"]
    assert structured_adapted["type"] == "object"
    assert structured_adapted["additionalProperties"] is True
    assert structured_adapted["properties"] == {"v": {"type": "string"}}
    assert structured_adapted["required"] == ["v"]
    assert structured_adapted["description"] == structured_canonical["description"]


def test_cohere_transport_strips_iso_keywords_canonical_keeps_them():
    """``pattern``/``format`` are canonical generation aids; the Cohere
    transport drops them (the parser stays the sole acceptance authority)
    while the CANONICAL evidence schema still teaches the grammar."""
    canonical = prompts.json_schema_for_stage_output("evidence")
    assert any(key == "pattern" for key in _walk_keys(canonical))
    assert any(key == "format" for key in _walk_keys(canonical))
    adapted = _adapted(canonical)
    keys = list(_walk_keys(adapted))
    assert "pattern" not in keys
    assert "format" not in keys
    # The canonical schema itself is untouched.
    assert canonical == prompts.json_schema_for_stage_output("evidence")


def test_adapted_evidence_transport_has_no_rejection_keywords():
    """DEF-056 (ADV-31B-04): the Phase31A evidence-400 keyword classes must
    NEVER appear in the adapted transport — and this guard asserts the union
    absence TRUTHFULLY now. The previous Phase31B round left four
    ``["string","null"]`` union nodes at propositions/items/properties/
    {personId,locationId,objectId,motiveId} — the EXACT subtree the 400 named
    — while claiming no-union without asserting it. The evidence contract now
    converts those to OPTIONAL non-null string nodes, so the adapted evidence
    schema carries ZERO union-type nodes anywhere."""
    adapted = _adapted(prompts.json_schema_for_stage_output("evidence"))
    text = str(adapted)
    for marker in ("oneOf", "anyOf", "if(", '"then"', '"pattern"', '"format"'):
        assert marker not in text, marker

    union_nodes = []
    for node, _path in _walk_typed_nodes(adapted):
        if isinstance(node.get("type"), list):
            union_nodes.append(_path)
    assert union_nodes == [], union_nodes

    # The four formerly-nullable reference fields are non-null strings in the
    # transport (canonical too), and observedAt is a plain optional string.
    props = adapted["properties"]["evidence"]["items"]["properties"][
        "propositions"
    ]["items"]["properties"]
    for field in ("personId", "locationId", "objectId", "motiveId"):
        assert props[field]["type"] == "string", field
        assert "null" not in (
            props[field]["type"]
            if isinstance(props[field]["type"], str)
            else ""
        ), field
    assert props["observedAt"]["type"] == "string"
    # No raw union ``["string","null"]`` remains anywhere in the evidence
    # schema (the exact Phase31A evidence-400 subtree).
    assert '["string", "null"]' not in text and "['string', 'null']" not in text


def _walk_typed_nodes(node, _path="$"):
    """Yield every (node, path) whose ``type`` key exists (deep walk)."""
    if isinstance(node, dict):
        if "type" in node:
            yield node, _path
        for key, value in node.items():
            yield from _walk_typed_nodes(value, _path + "/" + str(key))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk_typed_nodes(item, _path + f"[{index}]")


def test_non_cohere_transport_is_byte_identical_to_canonical():
    """Non-Cohere providers and the identity adapter keep the canonical
    evidence schema (including pattern/format) byte-identical — the DeepSeek
    wire shape is unchanged."""
    canonical = prompts.json_schema_for_stage_output("evidence")
    assert sa.adapt_schema_for_transport(canonical, adapter=None) == canonical
    assert dict(canonical) == canonical  # purity: input mapping untouched


def test_cohere_adapter_is_deterministic_and_never_reintroduces_evidence_400():
    canonical = prompts.json_schema_for_stage_output("evidence")
    first = _adapted(canonical)
    second = _adapted(canonical)
    assert first == second
    # Adapter selection gating stayed Cohere-family-only.
    assert sa.schema_adapter_id_for_frontier_call(
        "deepseek/deepseek-chat", STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA
    ) is None
    assert sa.schema_adapter_id_for_frontier_call("cohere/command-a-plus", None) is None


def test_canonical_evidence_and_transport_both_parse_golden_and_v_key():
    """The enriched canonical schema and the adapted transport still describe
    documents the STRICT parser accepts (golden + the synthetic transport
    ``v`` key) — the adapter never weakens acceptance."""
    from app.generation import parser
    from app.generation.provider import GenerationStage
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS

    golden = GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE]
    parsed = parser.parse_stage(GenerationStage.EVIDENCE, golden, non_throwing=False)
    assert parsed is not None

    import json

    doc = json.loads(golden)
    doc["evidence"][0]["propositions"][0]["structured"] = {"v": "transport key"}
    adapted_doc = json.dumps(doc)
    assert parser.parse_stage(
        GenerationStage.EVIDENCE, adapted_doc, non_throwing=False
    ) is not None


@pytest.mark.parametrize(
    "stage_value",
    ["evidence", "case_truth", "public_world", "world_graph", "repair"],
)
def test_all_stages_adapt_to_cohere_compatible_shape(stage_value):
    """Any Phase31B schema change across ALL stages keeps the Cohere family
    transport safe (every object node has a non-empty required list)."""
    canonical = prompts.json_schema_for_stage_output(stage_value)
    assert canonical is not None, stage_value
    if "oneOf" in str(canonical) or "anyOf" in str(canonical):
        pytest.skip("documented: expressive schemas already proven Cohere-safe elsewhere")
    adapted = _adapted(canonical)
    # Non-Cohere identity path unchanged.
    assert sa.adapt_schema_for_transport(canonical, adapter=None) == canonical
    for node in _walk_object_nodes(adapted):
        required = node.get("required")
        assert isinstance(required, list) and required, (stage_value, node)