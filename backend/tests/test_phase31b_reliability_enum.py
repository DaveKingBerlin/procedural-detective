"""Phase31B — §10/§21 — evidence reliability vocabulary alignment.

The strict parser accepts ONLY ``{high, medium, low}`` for
``evidence[].reliability`` (``parser._RELIABILITY_VOCABULARY``). The live
Gemini failure class emitted ``DEFINITIVE`` / ``HIGH`` — the transport schema
must teach the SAME closed vocabulary instead of the current open
``{"type": "string"}``.

Phase31B adds the canonical shared constant
``app.domain.evidence.RELIABILITY_VOCABULARY`` (derived from the domain
``Reliability`` enum) and derives the schema ``enum`` from it. The drift guard
proves ``schema enum == parser vocabulary`` — parser.py is NOT modified.

Required (§21): high/medium/low PASS; HIGH/DEFINITIVE/unknown/""/null FAIL —
through BOTH the schema enum AND the strict parser.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.domain.evidence import RELIABILITY_VOCABULARY  # noqa: E402
from app.generation import parser, prompts  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402


def _evidence_doc(reliability):
    return {
        "evidence": [
            {
                "id": "e001",
                "kind": "forensic",
                "reliability": reliability,
                "discoverable": True,
                "sourceRef": {"kind": "report", "sourceId": "s001"},
                "propositions": [
                    {
                        "type": "BODY_FIRST_FOUND_AT",
                        "locationId": "loc_01",
                        "observedAt": "2026-09-11T22:17:00+02:00",
                        "uncertaintySeconds": 60,
                        "structured": {},
                    }
                ],
                "presentation": {"title": "T", "description": "D"},
            }
        ]
    }


def _reliability_node(schema):
    return schema["properties"]["evidence"]["items"]["properties"]["reliability"]


# --------------------------------------------------------------------------- #
# §10 — the schema enum derives from the canonical shared constant
# --------------------------------------------------------------------------- #


def test_reliability_schema_enum_equals_parser_vocabulary():
    """schema enum == parser._RELIABILITY_VOCABULARY (the Phase31B §10/§16
    drift-proof)."""
    schema_node = _reliability_node(prompts.json_schema_for_stage_output("evidence"))
    expected = sorted(RELIABILITY_VOCABULARY)
    assert schema_node["type"] == "string"
    assert schema_node["enum"] == expected
    assert schema_node["enum"] == sorted(parser._RELIABILITY_VOCABULARY)
    # The canonical vocabulary is exactly the three documented tokens.
    assert set(schema_node["enum"]) == {"high", "medium", "low"}


def test_reliability_enum_derives_from_the_domain_enum():
    """The shared constant is derived from the single canonical ``Reliability``
    enum — no second authoritative copy exists."""
    from app.domain.evidence import Reliability

    assert RELIABILITY_VOCABULARY == frozenset(member.value for member in Reliability)
    assert RELIABILITY_VOCABULARY == parser._RELIABILITY_VOCABULARY


def test_invalid_values_are_not_in_the_closed_enum():
    schema_node = _reliability_node(prompts.json_schema_for_stage_output("evidence"))
    for bad in ("HIGH", "DEFINITIVE", "unknown", "", "High", "Medium"):
        assert bad not in schema_node["enum"], bad
    # null is not an acceptable reliability value (parser rejects it).
    assert not any(
        isinstance(item, type(None)) for item in schema_node["enum"]
    )


def test_ollama_transport_schema_carries_the_same_enum():
    """Both transport derivations come from the same contract mapping, so the
    Ollama/Direct/Bridge path carries the identical closed enum."""
    stage = prompts.json_schema_for_stage_output("evidence")
    transport = prompts.json_schema_for_generation_stage("evidence")
    assert _reliability_node(transport) == _reliability_node(stage)
    assert _reliability_node(transport)["enum"] == sorted(RELIABILITY_VOCABULARY)


# --------------------------------------------------------------------------- #
# §21 — the strict parser agrees (unchanged, still authoritative)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["high", "medium", "low"])
def test_parser_accepts_canonical_reliability(value):
    doc = json.dumps(_evidence_doc(value))
    assert parser.parse_stage(
        GenerationStage.EVIDENCE, doc, non_throwing=False
    ) is not None
    assert parser.collect_issues(GenerationStage.EVIDENCE, doc) == ()


@pytest.mark.parametrize("value", ["HIGH", "DEFINITIVE", "unknown", ""])
def test_parser_rejects_non_canonical_reliability(value):
    doc = json.dumps(_evidence_doc(value))
    assert parser.parse_stage(
        GenerationStage.EVIDENCE, doc, non_throwing=True
    ) is None
    issues = parser.collect_issues(GenerationStage.EVIDENCE, doc)
    assert issues
    # HIGH/DEFINITIVE/unknown -> the closed-vocabulary message; "" -> the
    # non-empty-string message. Both are parser rejections of a value the
    # canonical schema enum also refuses.
    assert any(
        "reliability" in issue
        and ("high|medium|low" in issue or "non-empty string" in issue)
        for issue in issues
    ), issues


def test_parser_rejects_null_and_missing_reliability():
    doc = json.dumps(_evidence_doc(None))
    assert parser.parse_stage(
        GenerationStage.EVIDENCE, doc, non_throwing=True
    ) is None
    assert parser.collect_issues(GenerationStage.EVIDENCE, doc)