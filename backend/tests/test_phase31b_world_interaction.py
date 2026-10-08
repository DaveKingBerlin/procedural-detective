"""Phase31B — §11/§22 — world placement interaction contract alignment.

The strict parser treats ``worldGraph.placements[].interaction`` as a REQUIRED
string that MAY be the empty string ("decorative / not interactable", DEF-062)
and is bounded to ``MAX_SINGLE_TEXT_FIELD_CHARS`` (parser.py
``_str_field(..., allow_empty=True)``).

Phase31B root cause: the transport schema's interaction hint
``"interaction string (or null)"`` was mis-typed by ``_hint_json_types`` as
``{"type": "integer"}`` (the ``startswith("int")`` branch matches the word
"interaction") and was NOT required — the live Gemini diagnostic
``world_graph.worldGraph.placements[0].interaction must be a string`` /
``missing required key 'interaction'``. The alignments:
- derivation fixed so interaction is typed ``string``;
- the key becomes REQUIRED (parser-required);
- ``maxLength`` = parser's ``MAX_SINGLE_TEXT_FIELD_CHARS``;
- the empty string stays valid (type string accepts "" — the parser
  allow_empty rule).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.generation import parser, prompts  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import MAX_SINGLE_TEXT_FIELD_CHARS  # noqa: E402


_NULL = object()  # sentinel: explicitly write interaction: null in the fixture


def _world_graph_doc(interaction="inspect", *, missing=False, present_as=_NULL):
    placement = {
        "objectId": "weapon_01",
        "assetId": "PROP_WEAPON_01",
        "locationId": "loc_01",
        "anchor": "desk_main",
        "interaction": interaction,
        "evidenceId": "e001",
    }
    if missing:
        placement.pop("interaction", None)
    if present_as is not _NULL:
        placement["interaction"] = present_as
    return {
        "worldGraph": {
            "locations": [
                {"locationId": "loc_01", "template": "kitchen_template", "rooms": []}
            ],
            "placements": [placement],
        }
    }


def _placement_item(schema):
    return schema["properties"]["worldGraph"]["properties"]["placements"]["items"]


def _interaction_node(schema):
    return _placement_item(schema)["properties"]["interaction"]


# --------------------------------------------------------------------------- #
# §11 — schema requiredness / type / max length
# --------------------------------------------------------------------------- #


def test_placement_interaction_is_required_in_the_schema():
    schema = prompts.json_schema_for_stage_output("world_graph")
    assert "interaction" in _placement_item(schema)["required"]


def test_placement_interaction_is_typed_string_not_integer():
    """Regression: the derivation bug typed interaction as integer because the
    old hint started with the word "interaction" (the ``startswith("int")``
    integer branch). The aligned node is a string, never integer."""
    schema = prompts.json_schema_for_stage_output("world_graph")
    node = _interaction_node(schema)
    assert node["type"] == "string"
    assert node["type"] != "integer"


def test_placement_interaction_max_length_matches_parser():
    schema = prompts.json_schema_for_stage_output("world_graph")
    node = _interaction_node(schema)
    assert node["maxLength"] == MAX_SINGLE_TEXT_FIELD_CHARS


def test_placement_interaction_empty_string_is_valid():
    """The parser allows ``interaction == ""`` (decorative); type string
    accepts the empty string in the schema too — same fixture passes both."""
    doc = json.dumps(_world_graph_doc(""))
    assert parser.collect_issues(GenerationStage.WORLD_GRAPH, doc) == ()
    node = _interaction_node(prompts.json_schema_for_stage_output("world_graph"))
    assert node["type"] == "string"  # an empty string is a string value
    # The contract description teaches the empty-string rule.
    assert "empty string" in node["description"].casefold()


def test_repair_schema_carries_the_interaction_constraint():
    """The full-draft REPAIR schema shares the same placement contract."""
    schema = prompts.json_schema_for_stage_output("repair")
    node = _interaction_node(schema)
    assert node["type"] == "string"
    assert "interaction" in _placement_item(schema)["required"]
    assert node["maxLength"] == MAX_SINGLE_TEXT_FIELD_CHARS


# --------------------------------------------------------------------------- #
# §22 — the strict parser agrees (unchanged, still authoritative)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "interaction",
    ["inspect", "read", "collect", "open", "activate", "talk", "view_record",
     "add_to_evidence_board"],
)
def test_parser_accepts_allowlisted_interactions(interaction):
    doc = json.dumps(_world_graph_doc(interaction))
    assert parser.collect_issues(GenerationStage.WORLD_GRAPH, doc) == ()


def test_parser_rejects_missing_interaction():
    doc = json.dumps(_world_graph_doc(missing=True))
    issues = parser.collect_issues(GenerationStage.WORLD_GRAPH, doc)
    assert any("missing required key 'interaction'" in i for i in issues), issues


@pytest.mark.parametrize(
    "bad",
    [
        None,  # null (the parser treats null as absent -> missing required key)
        {"x": 1},  # object
        ["inspect"],  # array
        7,  # integer
    ],
)
def test_parser_rejects_non_string_interaction(bad):
    doc = json.dumps(_world_graph_doc(present_as=bad))
    issues = parser.collect_issues(GenerationStage.WORLD_GRAPH, doc)
    assert any(
        "interaction" in i and ("must be a string" in i or "missing required key" in i)
        for i in issues
    ), (bad, issues)


def test_parser_rejects_overlong_interaction_at_the_parser_bound():
    """The parser caps interaction at MAX_SINGLE_TEXT_FIELD_CHARS — the schema
    carries the SAME constant as maxLength, so a fixture over the bound is
    rejected by the parser and exceeds the schema maxLength."""
    too_long = "i" * (MAX_SINGLE_TEXT_FIELD_CHARS + 1)
    doc = json.dumps(_world_graph_doc(too_long))
    issues = parser.collect_issues(GenerationStage.WORLD_GRAPH, doc)
    assert any("exceeds" in i and "4000" in i for i in issues), issues
    node = _interaction_node(prompts.json_schema_for_stage_output("world_graph"))
    assert node["maxLength"] < len(too_long)


def test_schema_and_parser_agree_on_the_same_fixture():
    """Meta-assertion for §22: the interaction contract is the SAME object
    graph property (required, type string, max bound) in both layers, never a
    fixture accepted by one layer and rejected by the other."""
    schema = prompts.json_schema_for_stage_output("world_graph")
    assert "interaction" in schema["properties"]["worldGraph"]["properties"][
        "placements"
    ]["items"]["required"]
    node = _interaction_node(schema)
    assert node["type"] == "string"
    assert node["maxLength"] == MAX_SINGLE_TEXT_FIELD_CHARS
    # empty string: schema-valid (type string) and parser-valid.
    assert parser.collect_issues(
        GenerationStage.WORLD_GRAPH, json.dumps(_world_graph_doc(""))
    ) == ()
    # non-string: invalid in BOTH layers.
    issues = parser.collect_issues(
        GenerationStage.WORLD_GRAPH,
        json.dumps(_world_graph_doc(present_as=123)),
    )
    assert any("interaction must be a string" in i for i in issues)