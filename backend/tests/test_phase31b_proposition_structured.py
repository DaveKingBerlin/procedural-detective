"""Phase31B — §12/§13/§23/§24 — proposition structured-data contract.

Canonical proposition-type matrix (the STRICT parser
``app.generation.parser._validate_proposition`` plus the Phase 3
``TypedProposition`` constructor):

  proposition type             required base fields          structured requirements
  ---------------------------  ---------------------------  ------------------------------
  PERSON_OBSERVED_AT_LOCATION  observedAt                    (no required structured key)
  VICTIM_LAST_SEEN_ALIVE_AT    observedAt                    (no required structured key)
  BODY_FIRST_FOUND_AT          observedAt                    (no required structured key)
  NOISE_HEARD_AT               observedAt                    (no required structured key)
  CRIME_SCENE_OBSERVATION_AT   observedAt                    (no required structured key)
  TIME_WINDOW_EXCLUSION        observedAt                    (no required structured key)
  WITNESS_CLAIMS/SUSPECT_CLAIMS/CAN_REACH_CRIME_SCENE_IN_TIME/OTHER
                               (none)                        (no required structured key)
  OBJECT_CONTAINS_FINGERPRINT  (none; objectId filled)       (no required structured key)
  OBJECT_CONTAINS_BLOOD        (none; objectId filled)       (no required structured key)
  MOTIVE_LINKED_TO_PERSON      (none; personId+motiveId)     (no required structured key)
  MOTIVE_FACT_CONTRADICTED     (none; motiveId filled)       (no required structured key)
  FORENSIC_WEAPON_MATCH        (none; objectId filled)       match: real JSON boolean
  ALIBI_TIME_CLAIM             (none; personId filled)       claimedDeparture: canonical
                                                             ISO-8601-with-offset string

Phase31B scope: FORENSIC_WEAPON_MATCH ``structured.match`` and ALIBI_TIME_CLAIM
``structured.claimedDeparture`` are the two live-proven mismatch classes. The
schema teaches them (closed type enum + a canonical structured-field teaching
description derived from the shared constant); the proposition ``structured``
node stays an OPEN OBJECT in the transport schema — a DOCUMENTED
provider-limitation (Cohere's "every object needs >= 1 required property" and
its union-type rejection forbid an if-then/oneOf branch on this node). The
STRICT parser remains the acceptance authority.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.domain.evidence import (  # noqa: E402
    ALIBI_TIME_CLAIM,
    FORENSIC_WEAPON_MATCH,
    PROPOSITION_TYPES,
)
from app.generation import parser, prompts  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402


def _proposition_doc(ptype, **overrides):
    proposition = {
        "type": ptype,
        "uncertaintySeconds": 60,
        "structured": {},
    }
    # DEF-056: reference fields are canonical OMIT (never null) — the fixture
    # only carries the keys the callers pass via overrides.
    proposition.update(overrides)
    return {
        "evidence": [
            {
                "id": "e001",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "report", "sourceId": "s001"},
                "propositions": [proposition],
                "presentation": {"title": "T", "description": "D"},
            }
        ]
    }


def _issues(ptype, **overrides):
    doc = json.dumps(_proposition_doc(ptype, **overrides))
    return parser.collect_issues(GenerationStage.EVIDENCE, doc)


# --------------------------------------------------------------------------- #
# §23 — FORENSIC_WEAPON_MATCH structured.match (strict parser)
# --------------------------------------------------------------------------- #


def test_forensic_weapon_match_real_boolean_passes():
    for value in (True, False):
        assert _issues(FORENSIC_WEAPON_MATCH, structured={"match": value}) == ()


def test_forensic_weapon_match_requires_structured_match():
    # missing structured entirely
    issues = _issues(FORENSIC_WEAPON_MATCH)
    assert any(
        "FORENSIC_WEAPON_MATCH requires structured 'match'" in i for i in issues
    ), issues
    # structured present but match missing
    issues = _issues(FORENSIC_WEAPON_MATCH, structured={"anything": 1})
    assert any(
        "FORENSIC_WEAPON_MATCH requires structured 'match'" in i for i in issues
    ), issues


@pytest.mark.parametrize(
    "bad",
    [
        "true",  # JSON string, not a bool
        1,  # JSON int
        {},  # JSON object
        [],  # JSON array
        None,  # null
    ],
)
def test_forensic_weapon_match_rejects_non_boolean(bad):
    issues = _issues(FORENSIC_WEAPON_MATCH, structured={"match": bad})
    assert any(
        "FORENSIC_WEAPON_MATCH requires structured 'match'" in i for i in issues
    ), (bad, issues)


# --------------------------------------------------------------------------- #
# §24 — ALIBI_TIME_CLAIM structured.claimedDeparture (strict parser)
# --------------------------------------------------------------------------- #


def test_alibi_claimed_departure_valid_timestamp_passes():
    issues = _issues(
        ALIBI_TIME_CLAIM,
        personId="person_01",
        structured={"claimedDeparture": "2026-09-11T20:18:00Z"},
    )
    assert issues == ()


@pytest.mark.parametrize(
    "bad",
    [
        "20:15",  # the proven live time-only failure value
        "20:18",
        "",  # empty (rejected with the non-empty-ISO message)
        "2026-09-11T22:17:00",  # naive (no offset)
    ],
)
def test_alibi_claimed_departure_rejects_invalid_timestamp(bad):
    issues = _issues(
        ALIBI_TIME_CLAIM,
        personId="person_01",
        structured={"claimedDeparture": bad},
    )
    assert any(
        "claimedDeparture" in issue for issue in issues
    ), (bad, issues)
    if bad:  # non-empty but malformed -> the timestamp-grammar message
        assert any(
            "claimedDeparture" in issue and "timestamp" in issue
            for issue in issues
        ), (bad, issues)


def test_alibi_claimed_departure_rejects_non_string():
    for bad in (20, True, {}, []):
        issues = _issues(
            ALIBI_TIME_CLAIM,
            personId="person_01",
            structured={"claimedDeparture": bad},
        )
        assert any(
            "claimedDeparture" in issue for issue in issues
        ), (bad, issues)


# --------------------------------------------------------------------------- #
# §12/§16 — the proposition-type vocabulary stays closed in the schema
# --------------------------------------------------------------------------- #


def test_proposition_type_enum_is_closed_in_schema():
    schema = prompts.json_schema_for_stage_output("evidence")
    enum = schema["properties"]["evidence"]["items"]["properties"]["propositions"][
        "items"
    ]["properties"]["type"]["enum"]
    assert enum == sorted(PROPOSITION_TYPES)
    from app.generation.parser import _PROPOSITION_KEYS  # noqa: PLC0415

    # proposition base keys in the schema == the parser key allowlist.
    node = schema["properties"]["evidence"]["items"]["properties"]["propositions"]
    assert set(node["items"]["properties"]) == set(_PROPOSITION_KEYS)
    # DEF-056: the four reference fields are OPTIONAL non-null strings (the
    # parser treats a missing key and a JSON null identically as absent); the
    # schema requires only type/uncertaintySeconds/structured.
    assert set(node["items"]["required"]) == {
        "type",
        "uncertaintySeconds",
        "structured",
    }
    for field in ("personId", "locationId", "objectId", "motiveId"):
        assert node["items"]["properties"][field] == {
            "type": "string"
        } or node["items"]["properties"][field].get("type") == "string"
        assert "null" not in (
            node["items"]["properties"][field].get("type")
            if isinstance(node["items"]["properties"][field].get("type"), str)
            else ""
        )


def test_schema_teaches_forensic_match_boolean_contract():
    """The structured node description carries the canonical FORENSIC_WEAPON_MATCH
    contract (real JSON boolean) derived from the shared teaching constant —
    the schema steers the model toward a real boolean while the parser enforces
    it."""
    schema = prompts.json_schema_for_stage_output("evidence")
    structured = schema["properties"]["evidence"]["items"]["properties"][
        "propositions"
    ]["items"]["properties"]["structured"]
    from app.generation.prompts import structured_contract_sentence  # noqa: PLC0415

    expected = structured_contract_sentence(FORENSIC_WEAPON_MATCH)
    assert expected in structured["description"]
    assert "real JSON boolean true|false" in structured["description"]
    assert "never a string" in structured["description"]


def test_schema_teaches_alibi_claimed_departure_grammar():
    schema = prompts.json_schema_for_stage_output("evidence")
    structured = schema["properties"]["evidence"]["items"]["properties"][
        "propositions"
    ]["items"]["properties"]["structured"]
    from app.generation.prompts import structured_contract_sentence  # noqa: PLC0415

    expected = structured_contract_sentence(ALIBI_TIME_CLAIM)
    assert expected in structured["description"]
    assert "canonical ISO-8601 timestamp" in structured["description"]


def test_structured_node_stays_open_object_documented_limitation():
    """The proposition ``structured`` node remains an OPEN OBJECT so no union
    or ``oneOf`` reaches the Cohere transport (Phase31A proof: Cohere rejects
    union keywords and requires >= 1 required property per object). The node
    carries the canonical teaching description; the STRICT parser stays the
    acceptance authority (§13 fallback path)."""
    schema = prompts.json_schema_for_stage_output("evidence")
    structured = schema["properties"]["evidence"]["items"]["properties"][
        "propositions"
    ]["items"]["properties"]["structured"]
    assert structured["type"] == "object"
    assert structured["additionalProperties"] is True
    assert "properties" not in structured
    assert "oneOf" not in structured
    assert "anyOf" not in structured
    assert "if" not in structured
    assert structured["description"]