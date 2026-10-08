"""Phase31B — §16 — schema/parser drift guards (consolidated).

Deterministic guards which prove the transport schemas and the STRICT parser
can never silently drift for the closed vocabularies and required fields:

  - reliability vocabulary (schema enum == parser vocabulary == shared constant)
  - affordance vocabulary (Phase31A drift guard stays green)
  - placement interaction requiredness/type/empty behavior
  - crimeTime required fields + timestamp field representation
  - proposition type vocabulary (closed)
  - FORENSIC_WEAPON_MATCH structured.match representation
  - ALIBI_TIME_CLAIM claimedDeparture representation

Plus the §38 guard rails that Phase31B changed NOTHING else:
no validator relaxation, no repair-budget change, no deadline/timeout change,
REQUIREMENTS.md byte-identical, DeepSeek envelope extraction unchanged, and
the Frontier parser-shaped stage wrapping contract intact.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.domain.evidence import (  # noqa: E402
    ALIBI_TIME_CLAIM,
    FORENSIC_WEAPON_MATCH,
    PROPOSITION_TYPES,
    RELIABILITY_VOCABULARY,
    TIME_REQUIRED_TYPES,
)
from app.domain.time_interval import canonical_timestamp_schema_pattern  # noqa: E402
from app.generation import budgets, parser, prompts  # noqa: E402
from app.generation.frontier_provider import (  # noqa: E402
    extract_openai_chat_completions_content,
)
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    AFFORDANCE_VOCABULARY,
    MAX_SINGLE_TEXT_FIELD_CHARS,
)

REQUIREMENTS_SHA256 = "b2e568c029b02de79a74c43bb0e2ecd4d44a7fbec65d7de191a890a6dec9970d"
REQUIREMENTS_PATH = Path(__file__).resolve().parents[2] / "REQUIREMENTS.md"


def _node(schema, *path):
    current = schema
    for key in path:
        current = current[key]
    return current


# --------------------------------------------------------------------------- #
# §38.1 — Frontier parser-shaped stage schemas still wrap stages correctly
# --------------------------------------------------------------------------- #


def test_frontier_parser_shaped_stage_wrapping_contract():
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS, GOLDEN_FULL_DRAFT

    for stage in (
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
    ):
        schema = prompts.json_schema_for_stage_output(stage.value)
        assert schema is not None, stage
        doc = json.loads(GOLDEN_STAGE_PAYLOADS[stage])
        assert set(schema["properties"]) == set(doc), stage
        assert set(schema["required"]) == set(doc), stage
        assert parser.parse_stage(stage, GOLDEN_STAGE_PAYLOADS[stage], non_throwing=False)
    # case_truth -> {crime}; world_graph -> {worldGraph}.
    assert set(prompts.json_schema_for_stage_output("case_truth")["properties"]) == {
        "crime"
    }
    assert set(
        prompts.json_schema_for_stage_output("world_graph")["properties"]
    ) == {"worldGraph"}
    # repair -> the full-draft golden roundtrips.
    repair = prompts.json_schema_for_stage_output("repair")
    doc = json.loads(GOLDEN_FULL_DRAFT)
    assert set(repair["properties"]) == set(doc)
    assert parser.collect_full_draft_issues(json.dumps(doc)) == ()


# --------------------------------------------------------------------------- #
# §16 — closed-vocabulary drift guards (constant-derived, not introspective)
# --------------------------------------------------------------------------- #


def test_reliability_drift_guard():
    """schema enum == parser vocabulary == the domain shared constant."""
    schema = prompts.json_schema_for_stage_output("evidence")
    enum = _node(
        schema, "properties", "evidence", "items", "properties", "reliability", "enum"
    )
    assert enum == sorted(RELIABILITY_VOCABULARY)
    assert set(enum) == set(parser._RELIABILITY_VOCABULARY)
    assert RELIABILITY_VOCABULARY == parser._RELIABILITY_VOCABULARY


def test_affordance_drift_guard_still_green():
    """Phase31A's affordance transport enum is untouched by Phase31B."""
    for stage_value in ("public_world", "repair"):
        schema = prompts.json_schema_for_stage_output(stage_value)
        affordances = _node(
            schema, "properties", "persons", "items", "properties", "affordances"
        )
        assert affordances["items"]["enum"] == sorted(AFFORDANCE_VOCABULARY)


def test_interaction_drift_guard():
    """placement interaction: schema requiredness/type/maxLength derive from
    the parser contract constants."""
    schema = prompts.json_schema_for_stage_output("world_graph")
    item = _node(schema, "properties", "worldGraph", "properties", "placements", "items")
    assert "interaction" in item["required"]
    assert item["properties"]["interaction"]["type"] == "string"
    assert item["properties"]["interaction"]["maxLength"] == MAX_SINGLE_TEXT_FIELD_CHARS


def test_crime_time_drift_guard():
    schema = prompts.json_schema_for_stage_output("case_truth")
    crime_time = _node(schema, "properties", "crime", "properties", "crimeTime")
    assert set(crime_time["required"]) == set(parser._CRIME_TIME_KEYS)
    assert crime_time["properties"]["canonical"]["type"] == "string"
    assert crime_time["properties"]["canonical"]["pattern"] == (
        canonical_timestamp_schema_pattern()
    )


def test_proposition_type_vocabulary_closed_in_schema():
    schema = prompts.json_schema_for_stage_output("evidence")
    enum = _node(
        schema,
        "properties",
        "evidence",
        "items",
        "properties",
        "propositions",
        "items",
        "properties",
        "type",
        "enum",
    )
    assert enum == sorted(PROPOSITION_TYPES)
    assert set(enum) == set(PROPOSITION_TYPES)


def test_observed_at_time_required_drift_guard():
    """DEF-054: the schema's observedAt teaching names EXACTLY the canonical
    time-required proposition types (derived from the shared constant, never a
    second copy) and the STRICT parser rejects each of those types without
    observedAt — the teaching and the parser contract can never drift."""
    hint = prompts.observed_at_contract_hint()
    for ptype in sorted(TIME_REQUIRED_TYPES):
        assert ptype in hint, ptype
        doc = json.dumps(
            {
                "evidence": [
                    {
                        "id": "e001",
                        "kind": "forensic",
                        "reliability": "high",
                        "discoverable": True,
                        "sourceRef": {"kind": "report", "sourceId": "s001"},
                        "propositions": [
                            {
                                "type": ptype,
                                "uncertaintySeconds": 60,
                                "structured": {},
                            }
                        ],
                        "presentation": {"title": "T", "description": "D"},
                    }
                ]
            }
        )
        issues = parser.collect_issues(GenerationStage.EVIDENCE, doc)
        assert any("requires observed_at" in i for i in issues), (ptype, issues)
    # The EVIDENCE_PROMPT field rules also derive the set from the constant.
    from app.generation.prompts import EVIDENCE_PROMPT_v1  # noqa: PLC0415

    for ptype in sorted(TIME_REQUIRED_TYPES):
        assert ptype in EVIDENCE_PROMPT_v1, ptype
    assert "never emitted as null" in EVIDENCE_PROMPT_v1


def test_evidence_reference_fields_are_optional_non_null_strings():
    """DEF-056: the four evidence reference fields are OPTIONAL non-null
    strings in the schema (parser treats missing ~= null as absent) — no union
    node survives in the canonical evidence schema, and the parser accepts a
    proposition that omits them."""
    schema = prompts.json_schema_for_stage_output("evidence")
    items = schema["properties"]["evidence"]["items"]["properties"]["propositions"][
        "items"
    ]
    for field in ("personId", "locationId", "objectId", "motiveId"):
        assert field not in items["required"], field
        assert items["properties"][field]["type"] == "string", field
    # Parser proof: a non-relational proposition without any reference key is
    # structurally valid.
    doc = json.dumps(
        {
            "evidence": [
                {
                    "id": "e001",
                    "kind": "forensic",
                    "reliability": "high",
                    "discoverable": True,
                    "sourceRef": {"kind": "report", "sourceId": "s001"},
                    "propositions": [
                        {
                            "type": "WITNESS_CLAIMS",
                            "uncertaintySeconds": 60,
                            "structured": {},
                        }
                    ],
                    "presentation": {"title": "T", "description": "D"},
                }
            ]
        }
    )
    assert parser.collect_issues(GenerationStage.EVIDENCE, doc) == ()


def test_forensic_weapon_match_schema_parser_drift():
    """FORENSIC_WEAPON_MATCH structured.match: the schema teaches a real JSON
    boolean (shared contract sentence) and the STRICT parser enforces it."""
    from app.generation.prompts import structured_contract_sentence

    structured = _node(
        prompts.json_schema_for_stage_output("evidence"),
        "properties",
        "evidence",
        "items",
        "properties",
        "propositions",
        "items",
        "properties",
        "structured",
    )
    assert structured_contract_sentence(FORENSIC_WEAPON_MATCH) in structured[
        "description"
    ]
    # Parser-level proof (behavioral, not introspective): match must be real.
    doc = json.dumps(
        {
            "evidence": [
                {
                    "id": "e001",
                    "kind": "forensic",
                    "reliability": "high",
                    "discoverable": True,
                    "sourceRef": {"kind": "report", "sourceId": "s001"},
                    "propositions": [
                        {
                            "type": "FORENSIC_WEAPON_MATCH",
                            "objectId": "weapon_01",
                            "uncertaintySeconds": 60,
                            "structured": {"match": "true"},
                        }
                    ],
                    "presentation": {"title": "T", "description": "D"},
                }
            ]
        }
    )
    issues = parser.collect_issues(GenerationStage.EVIDENCE, doc)
    assert any("FORENSIC_WEAPON_MATCH requires structured 'match'" in i for i in issues)


def test_alibi_claimed_departure_drift_guard():
    """ALIBI_TIME_CLAIM structured.claimedDeparture: the schema teaches the
    canonical ISO grammar and the STRICT parser rejects the non-canonical
    representations."""
    from app.generation.prompts import structured_contract_sentence

    structured = _node(
        prompts.json_schema_for_stage_output("evidence"),
        "properties",
        "evidence",
        "items",
        "properties",
        "propositions",
        "items",
        "properties",
        "structured",
    )
    sentence = structured_contract_sentence(ALIBI_TIME_CLAIM)
    assert sentence in structured["description"]
    assert "canonical ISO-8601 timestamp" in structured["description"]
    doc = json.dumps(
        {
            "evidence": [
                {
                    "id": "e001",
                    "kind": "forensic",
                    "reliability": "high",
                    "discoverable": True,
                    "sourceRef": {"kind": "report", "sourceId": "s001"},
                    "propositions": [
                        {
                            "type": "ALIBI_TIME_CLAIM",
                            "personId": "person_01",
                            "uncertaintySeconds": 60,
                            "structured": {"claimedDeparture": "20:15"},
                        }
                    ],
                    "presentation": {"title": "T", "description": "D"},
                }
            ]
        }
    )
    issues = parser.collect_issues(GenerationStage.EVIDENCE, doc)
    assert any("claimedDeparture" in i and "timestamp" in i for i in issues)


# --------------------------------------------------------------------------- #
# §38.13-15 — DeepSeek envelope extraction / no validator relaxation
# --------------------------------------------------------------------------- #


def test_deepseek_envelope_extraction_unchanged():
    """Phase31A Track B envelope extraction is byte-unchanged: an OpenAI-compatible
    envelope yields the inner stage content; a bare document passes through."""
    inner = '{"evidence": []}'
    envelope = json.dumps(
        {"choices": [{"message": {"content": inner}}], "id": "x", "model": "m"}
    )
    assert extract_openai_chat_completions_content(envelope) == inner
    assert extract_openai_chat_completions_content(inner) is None
    assert extract_openai_chat_completions_content("<not-json>") is None


def test_strict_parser_still_rejects_the_four_live_mismatch_classes():
    """No validator relaxation: every proven live failure value is STILL
    rejected by the STRICT parser with the unchanged issue categories."""
    from fixtures.golden_generation import GOLDEN_FULL_DRAFT, GOLDEN_STAGE_PAYLOADS

    # 1) time-only timestamp (case_truth)
    doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH])
    doc["crime"]["crimeTime"]["canonical"] = "20:15"
    issues = parser.collect_issues(
        GenerationStage.CASE_TRUTH, json.dumps(doc)
    )
    assert any("crimeTime.canonical" in i and "timestamp" in i for i in issues)
    # 2) non-canonical reliability (evidence)
    doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE])
    doc["evidence"][0]["reliability"] = "DEFINITIVE"
    issues = parser.collect_issues(
        GenerationStage.EVIDENCE, json.dumps(doc)
    )
    assert any("reliability must be one of high|medium|low" in i for i in issues)
    # 3) string match (evidence FORENSIC_WEAPON_MATCH)
    doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE])
    touched = False
    for item in doc["evidence"]:
        for prop in item["propositions"]:
            if prop["type"] == "FORENSIC_WEAPON_MATCH" and prop.get("structured"):
                prop["structured"]["match"] = "true"
                touched = True
    assert touched, "the golden fixture must carry a FORENSIC_WEAPON_MATCH prop"
    issues = parser.collect_issues(
        GenerationStage.EVIDENCE, json.dumps(doc)
    )
    assert any("FORENSIC_WEAPON_MATCH requires structured 'match'" in i for i in issues)
    # 4) missing interaction (world_graph)
    doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH])
    doc["worldGraph"]["placements"][0].pop("interaction", None)
    issues = parser.collect_issues(
        GenerationStage.WORLD_GRAPH, json.dumps(doc)
    )
    assert any("missing required key 'interaction'" in i for i in issues)
    # Golden fixtures still pass (no relaxation changed acceptance).
    assert parser.collect_issues(
        GenerationStage.CASE_TRUTH, GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH]
    ) == ()
    assert parser.collect_full_draft_issues(GOLDEN_FULL_DRAFT) == ()


# --------------------------------------------------------------------------- #
# §38.16-17 — budgets / deadlines unchanged
# --------------------------------------------------------------------------- #


def test_no_repair_budget_or_deadline_change(tmp_path):
    assert budgets.DEFAULT_MAX_REPAIR_PASSES == 2
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    assert settings.generation_deadline_seconds == 60
    assert settings.ollama_timeout_seconds == 60.0
    assert budgets.DEFAULT_MAX_FULL_REGENERATIONS == 1


# --------------------------------------------------------------------------- #
# §38.18 — REQUIREMENTS.md byte-identical
# --------------------------------------------------------------------------- #


def test_requirements_md_hash_unchanged():
    digest = hashlib.sha256(REQUIREMENTS_PATH.read_bytes()).hexdigest()
    assert digest == REQUIREMENTS_SHA256, (
        "REQUIREMENTS.md drifted from the freeze hash"
    )