"""Phase31B — §8/§9/§20 — canonical timestamp grammar alignment.

The strict parser (``app.generation.parser._check_timestamp``) validates every
canonical timestamp through ``app.domain.time_interval.parse_iso8601`` plus the
solver-time domain bound ``assert_epoch_in_domain``. This suite proves:

- the transport stage-output schemas (``json_schema_for_stage_output``) teach
  the EXACT canonical grammar for ``crimeTime.canonical`` (case_truth) and the
  evidence ``propositions[].observedAt`` (and the REPAIR full-draft copies of
  both);
- the derived JSON-Schema ``pattern`` (``canonical_timestamp_schema_pattern``)
  is the range-strengthened single-source grammar: month 01..12, day 01..31,
  hour 00..23, minute 00..59, second 00..59 plus the documented ``:60``
  leap-second tolerance (the parser clamps it to 59), offset hour 00..23 and
  offset minute 00..59 — the expressible out-of-range families are rejected by
  the pattern AND the parser (DEF-053);
- the DOCUMENTED RESIDUAL schema-weaker-than-parser: year ``0000`` and
  impossible calendar dates (2026-02-30, 2026-04-31, non-leap 02-29, ...)
  cannot be expressed in the provider-safe regex subset — the schema ACCEPTS
  those (as a generation aid) while the STRICT parser STILL REJECTS them
  (never coerced);
- the STRICT PARSER is unchanged and remains authoritative.

DEF-054 contract note on ``observedAt``: the parser REQUIRES ``observedAt``
for EXACTLY the canonical time-required proposition types
(``app.domain.evidence.TIME_REQUIRED_TYPES``); for every OTHER type the field
is optional and absent ~= null is TRUE at the parser level only. The schema
keeps the field OPTIONAL and teaches "omit, never emit null" (DEF-055); the
time-required set is documented in the schema description and drift-guarded.

The parser file must NOT be modified by Phase31B — the schema is a generation
aid, the parser the acceptance authority.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.domain.evidence import TIME_REQUIRED_TYPES  # noqa: E402
from app.domain.time_interval import canonical_timestamp_schema_pattern  # noqa: E402
from app.generation import parser, prompts  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402


def _crime_doc(canonical, tolerance=120):
    return {
        "crime": {
            "type": "murder",
            "victimId": "victim_01",
            "murdererId": "murderer_01",
            "motiveId": "motive_01",
            "weaponId": "weapon_01",
            "locationId": "loc_01",
            "crimeTime": {
                "canonical": canonical,
                "accusationToleranceSeconds": tolerance,
            },
        }
    }


def _evidence_doc(observed_at, *, reliability="high", ptype="BODY_FIRST_FOUND_AT"):
    proposition = {
        "type": ptype,
        "uncertaintySeconds": 60,
        "structured": {},
    }
    if observed_at is not None:
        proposition["observedAt"] = observed_at
    # DEF-056: reference fields are canonical OMIT (never null) — only include
    # the ones this fixture needs.
    if ptype in ("BODY_FIRST_FOUND_AT", "CRIME_SCENE_OBSERVATION_AT",
                 "VICTIM_LAST_SEEN_ALIVE_AT", "PERSON_OBSERVED_AT_LOCATION"):
        proposition["locationId"] = "loc_01"
        if ptype == "PERSON_OBSERVED_AT_LOCATION":
            proposition["personId"] = "person_01"
    return {
        "evidence": [
            {
                "id": "e001",
                "kind": "forensic",
                "reliability": reliability,
                "discoverable": True,
                "sourceRef": {"kind": "report", "sourceId": "s001"},
                "propositions": [proposition],
                "presentation": {"title": "Forensic report", "description": "Report"},
            }
        ]
    }


def _find_by_key(node, key):
    """First node carrying ``key`` in a schema mapping (deep walk)."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = _find_by_key(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_by_key(item, key)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------- #
# §9 — the stage-output schemas carry the canonical timestamp grammar node
# --------------------------------------------------------------------------- #


def test_crime_time_canonical_schema_carries_the_canonical_grammar():
    schema = prompts.json_schema_for_stage_output("case_truth")
    assert schema is not None
    node = schema["properties"]["crime"]["properties"]["crimeTime"]["properties"][
        "canonical"
    ]
    assert node["type"] == "string"
    # The derived ECMA-262 pattern IS the canonical parser grammar (single
    # source, never a duplicate).
    assert node["pattern"] == canonical_timestamp_schema_pattern()
    # The canonical grammar is taught to the model in the node description too.
    assert "canonical ISO-8601 timestamp" in node["description"]
    # The field remains required (the strict parser requires crimeTime.canonical).
    assert "canonical" in schema["properties"]["crime"]["properties"]["crimeTime"][
        "required"
    ]


def test_observed_at_schema_carries_the_canonical_grammar():
    schema = prompts.json_schema_for_stage_output("evidence")
    assert schema is not None
    node = schema["properties"]["evidence"]["items"]["properties"]["propositions"][
        "items"
    ]["properties"]["observedAt"]
    # observedAt stays OPTIONAL in the transport schema because the schema has
    # no provider-safe per-type construct here (a Cohere-safe if/then would
    # reintroduce the union/oneOf rejection class — documented in DEF-054).
    # The parser REQUIRES observedAt for the time-required types; for every
    # OTHER type absent ~= null is TRUE at the parser level only, and the
    # teaching says "omit, never emit null" (DEF-055).
    assert "observedAt" not in schema["properties"]["evidence"]["items"][
        "properties"
    ]["propositions"]["items"]["required"]
    for _type in ([node["type"]] if isinstance(node["type"], str) else node["type"]):
        assert _type in ("string", "null")
    assert node["pattern"] == canonical_timestamp_schema_pattern()
    assert "canonical ISO-8601 timestamp" in node["description"]
    # DEF-054 drift: the description names EXACTLY the canonical time-required
    # proposition types (derived from the shared constant).
    for ptype in sorted(TIME_REQUIRED_TYPES):
        assert ptype in node["description"], ptype
    assert "never emit observedAt: null" in node["description"]


# --------------------------------------------------------------------------- #
# §20 — the schema pattern accepts canonical and rejects the failure values
# --------------------------------------------------------------------------- #

_CANONICAL = [
    "2026-09-11T22:17:00+02:00",
    "2026-09-11T20:18:00Z",
    "2026-09-11T20:18:00z",
    "2026-09-11T20:18:00+0200",
    "2026-09-11T20:18:00-05:30",
    "2026-09-11T20:18:00.500Z",
    "2026-09-11T20:18:00.123456+02:00",
]
_REJECTED = [
    "20:15",
    "20:18",
    "20:15:00",
    "2026-09-11",
    "2026-09-11T22:17:00",  # naive datetimes are rejected by the parser
    "2026-09-11T22:17",
    "2026-09-11 22:17:00",
    "",
    "T22:17:00Z",
    "...",
]

# DEF-053 — EXPRESSIBLE out-of-range families: the range-strengthened pattern
# (hour 00..23, minute 00..59, second 00..59|60, month 01..12, day 01..31,
# offset hour 00..23, offset minute 00..59) rejects these AND the STRICT
# parser rejects them too — schema/parser AGREE.
_STRENGTHENED_AGREEMENT_REJECTED = [
    "2026-09-11T24:00:00Z",  # hour 24 (parser: 0..23)
    "2026-09-11T22:61:00Z",  # minute 61 (parser: 0..59)
    "2026-09-11T22:17:61Z",  # second 61 (parser: 0..59, 60 clamped)
    "2026-09-11T22:17:99Z",  # second 99
    "2026-00-11T22:17:00Z",  # month 00 (parser: 1..12)
    "2026-13-11T22:17:00Z",  # month 13
    "2026-09-00T22:17:00Z",  # day 00 (parser: 1..31)
    "2026-09-32T22:17:00Z",  # day 32
    "2026-09-11T22:17:00+24:00",  # offset hour 24 (parser: 0..23)
    "2026-09-11T22:17:00+02:60",  # offset minute 60 (parser: 0..59)
]

# DEF-053 — DOCUMENTED RESIDUAL schema-weaker-than-parser values: NOT
# expressible in the provider-safe regex subset (year 0000 needs a lookahead;
# impossible calendar dates need month/leap-year semantics). The SCHEMA pattern
# ACCEPTS these (generation aid) while the STRICT PARSER STILL REJECTS them —
# documented limitation (§9), parser authoritative.
_RESIDUAL_SCHEMA_WEAKER_PARSER_REJECTED = [
    "0000-01-01T00:00:00Z",  # year 0000 (datetime year range)
    "2026-02-30T22:17:00Z",  # impossible: February 30th
    "2026-02-31T22:17:00Z",  # impossible: February 31st
    "2026-04-31T22:17:00Z",  # impossible: April 31st
    "2026-09-31T22:17:00Z",  # impossible: September 31st
    "2026-02-29T22:17:00Z",  # 2026 is not a leap year
    "2025-02-29T22:17:00Z",  # 2025 is not a leap year
]


@pytest.mark.parametrize("value", _CANONICAL)
def test_timestamp_pattern_accepts_the_canonical_forms(value):
    pattern = canonical_timestamp_schema_pattern()
    assert re.fullmatch(pattern, value), (pattern, value)


@pytest.mark.parametrize("value", _REJECTED)
def test_timestamp_pattern_rejects_the_failure_values(value):
    pattern = canonical_timestamp_schema_pattern()
    assert re.fullmatch(pattern, value) is None, (pattern, value)


# --------------------------------------------------------------------------- #
# DEF-053 — the range-strengthened pattern AGREES with the parser where the
# range IS expressible, and stays DOCUMENTED-weaker only for the residual
# impossible families (year 0000 / impossible calendar dates).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", _STRENGTHENED_AGREEMENT_REJECTED)
def test_schema_and_parser_agree_on_expressible_out_of_range_values(value):
    """Every out-of-range family a regex CAN express (hour/minute/second/month/
    day/offset ranges) is now rejected by the SCHEMA pattern AND the STRICT
    parser — the schema is no longer weaker on these families."""
    assert re.fullmatch(canonical_timestamp_schema_pattern(), value) is None, value
    doc = json.dumps(_crime_doc(value))
    assert parser.parse_stage(
        GenerationStage.CASE_TRUTH, doc, non_throwing=True
    ) is None
    assert parser.collect_issues(GenerationStage.CASE_TRUTH, doc), value


@pytest.mark.parametrize("value", _RESIDUAL_SCHEMA_WEAKER_PARSER_REJECTED)
def test_schema_is_documented_weaker_and_parser_rejects_residual_families(value):
    """The DOCUMENTED RESIDUAL class: year 0000 and impossible calendar dates
    cannot be expressed in the provider-safe regex subset, so the schema
    pattern ACCEPTS them (a generation aid) while the STRICT PARSER STILL
    REJECTS them. The limitation is documented in the schema description and
    this module — the parser stays authoritative (§9)."""
    assert re.fullmatch(canonical_timestamp_schema_pattern(), value), value
    doc = json.dumps(_crime_doc(value))
    assert parser.parse_stage(
        GenerationStage.CASE_TRUTH, doc, non_throwing=True
    ) is None
    issues = parser.collect_issues(GenerationStage.CASE_TRUTH, doc)
    assert issues, value
    assert any("timestamp" in issue for issue in issues), issues


def test_leap_second_rejects_out_of_range_seconds_but_accepts_60():
    """DEF-053 explicit decision: ``:60`` leap-second (parser CLAMPS to 59) is
    ACCEPTED by both layers; ``:61``..``:99`` are rejected by both."""
    pattern = canonical_timestamp_schema_pattern()
    for value in ("2026-09-11T22:17:60Z", "2026-09-11T22:17:60+02:00"):
        assert re.fullmatch(pattern, value), value
        issues = parser.collect_issues(
            GenerationStage.CASE_TRUTH, json.dumps(_crime_doc(value))
        )
        assert issues == (), value
    for value in ("2026-09-11T22:17:61Z", "2026-09-11T22:17:99Z"):
        assert re.fullmatch(pattern, value) is None, value
        issues = parser.collect_issues(
            GenerationStage.CASE_TRUTH, json.dumps(_crime_doc(value))
        )
        assert issues, value


def test_timestamp_pattern_is_valid_ecma_safe_regex():
    # JSON Schema ``pattern`` follows ECMA-262: Python-only named capture
    # groups (``(?P<name>...)``) must never leak into the derived pattern.
    pattern = canonical_timestamp_schema_pattern()
    assert "(?P" not in pattern
    assert pattern.startswith("^") and pattern.endswith("$")
    # Compiles under Python re for the schema-level guard tests.
    re.compile(pattern)


# --------------------------------------------------------------------------- #
# §20 — the STRICT PARSER agrees (parser.py is untouched and authoritative)
# --------------------------------------------------------------------------- #


def test_parser_rejects_time_only_crime_time():
    """The live failure values ``20:15``/``20:18`` for crimeTime.canonical are
    still rejected by the STRICT parser."""
    for value in ("20:15", "20:18"):
        doc = json.dumps(_crime_doc(value))
        assert parser.parse_stage(
            GenerationStage.CASE_TRUTH, doc, non_throwing=True
        ) is None
        issues = parser.collect_issues(GenerationStage.CASE_TRUTH, doc)
        assert issues, value
        assert any(
            "crimeTime.canonical" in issue and "timestamp" in issue
            for issue in issues
        ), (value, issues)


def test_parser_rejects_time_only_observed_at():
    for value in ("20:15", "20:18"):
        doc = json.dumps(_evidence_doc(value))
        assert parser.parse_stage(
            GenerationStage.EVIDENCE, doc, non_throwing=True
        ) is None
        issues = parser.collect_issues(GenerationStage.EVIDENCE, doc)
        assert any(
            "observedAt" in issue and "timestamp" in issue for issue in issues
        ), (value, issues)


@pytest.mark.parametrize("value", _CANONICAL)
def test_parser_accepts_canonical_timestamps(value):
    """Every canonical form the schema accepts also parses (offset/Z/z/fraction),
    incl. the leap-second clamp for ``:60`` seconds handled by parse_iso8601."""
    crime = json.dumps(_crime_doc(value))
    assert parser.collect_issues(GenerationStage.CASE_TRUTH, crime) == ()
    assert parser.parse_stage(
        GenerationStage.CASE_TRUTH, crime, non_throwing=False
    ) is not None
    observed = json.dumps(_evidence_doc(value))
    assert parser.collect_issues(GenerationStage.EVIDENCE, observed) == ()


def test_parser_rejects_malformed_empty_naive_and_wrong_primitive():
    """Malformed, empty, naive-datetime and wrong-primitive timestamps are all
    rejected with the STRICT parser unchanged (never coerced)."""
    bad_values = [
        "2026-09-11",  # date only (time+zone missing)
        "2026-09-11T22:17:00",  # naive (no zone)
        "not-a-time",
        "",
        "2026-02-30T22:17:00Z",  # impossible calendar day (datetime rejects)
        "2026-09-11T24:17:00Z",  # hour out of range
        "2026-09-11T22:61:00Z",  # minute out of range
    ]
    for value in bad_values:
        doc = json.dumps(_crime_doc(value))
        assert parser.parse_stage(
            GenerationStage.CASE_TRUTH, doc, non_throwing=True
        ) is None, value
        assert parser.collect_issues(GenerationStage.CASE_TRUTH, doc), value
    # wrong primitive: canonical is an int -> "must be a non-empty string" +
    # the string field never reaches the timestamp check.
    wrong_primitive = json.dumps({"crime": _crime_doc("2026-09-11T22:17:00Z")["crime"]})
    wrong_primitive = wrong_primitive.replace(
        '"canonical": "2026-09-11T22:17:00Z"', '"canonical": 20'
    )
    assert parser.parse_stage(
        GenerationStage.CASE_TRUTH, wrong_primitive, non_throwing=True
    ) is None
    assert any(
        "canonical" in issue and "string" in issue
        for issue in parser.collect_issues(GenerationStage.CASE_TRUTH, wrong_primitive)
    )


def test_leap_second_and_fraction_flooring_match_parser_semantics():
    """parse_iso8601 clamps a ``:60`` leap second to 59 and FLOORS fractional
    seconds — the schema pattern accepts both while the parser maps them into
    the integer-second domain."""
    for value in ("2026-09-11T22:17:60+02:00", "2026-09-11T20:18:00.999Z"):
        assert re.fullmatch(canonical_timestamp_schema_pattern(), value), value
        issues = parser.collect_issues(
            GenerationStage.CASE_TRUTH, json.dumps(_crime_doc(value))
        )
        assert issues == (), value


def test_parser_accepts_and_rejects_same_grammar_with_lowercase_z_and_colonless_offset():
    """The actual parser grammar (``time_interval._ISO_RE``) accepts ``z`` and
    ``+0200``-style offsets; the schema derivation mirrors it exactly."""
    for value in ("2026-09-11T20:18:00z", "2026-09-11T20:18:00+0200"):
        assert re.fullmatch(canonical_timestamp_schema_pattern(), value), value
        assert parser.collect_issues(
            GenerationStage.CASE_TRUTH, json.dumps(_crime_doc(value))
        ) == (), value


# --------------------------------------------------------------------------- #
# DEF-054 — the parser REQUIRES observedAt for the canonical time-required set
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ptype", sorted(TIME_REQUIRED_TYPES))
def test_parser_rejects_each_time_required_type_without_observed_at(ptype):
    """Every canonical time-required proposition type is STILL rejected by the
    STRICT parser when observedAt is missing (the schema keeps the field
    optional and teaches the set — the parser stays authoritative)."""
    doc = json.dumps(_evidence_doc(None, ptype=ptype))
    assert parser.parse_stage(
        GenerationStage.EVIDENCE, doc, non_throwing=True
    ) is None
    issues = parser.collect_issues(GenerationStage.EVIDENCE, doc)
    assert any(
        "requires observed_at" in issue and ptype in issue for issue in issues
    ), (ptype, issues)


def test_parser_accepts_observed_at_omission_for_non_time_required_type():
    """A NON time-required type (WITNESS_CLAIMS) without observedAt is VALID —
    absent observedAt is legal for these types at the parser level."""
    doc = json.dumps(_evidence_doc(None, ptype="WITNESS_CLAIMS"))
    assert parser.collect_issues(GenerationStage.EVIDENCE, doc) == ()


def test_observed_at_teaching_names_exactly_the_canonical_time_required_set():
    """DEF-054 drift: the schema description's time-required list is DERIVED
    from the canonical ``TIME_REQUIRED_TYPES`` constant — never a second
    copy."""
    hint = prompts.observed_at_contract_hint()
    for ptype in sorted(TIME_REQUIRED_TYPES):
        assert ptype in hint, ptype
    node = prompts.json_schema_for_stage_output("evidence")["properties"][
        "evidence"
    ]["items"]["properties"]["propositions"]["items"]["properties"]["observedAt"]
    assert node["description"] == hint


# --------------------------------------------------------------------------- #
# §16 — crimeTime schema/parser drift guard
# --------------------------------------------------------------------------- #


def test_crime_time_schema_required_keys_match_parser():
    """The transport schema requires exactly the parser's ``_CRIME_TIME_KEYS``
    and types the timestamp field as the canonical string."""
    schema = prompts.json_schema_for_stage_output("case_truth")
    node = schema["properties"]["crime"]["properties"]["crimeTime"]
    assert set(node["required"]) == set(parser._CRIME_TIME_KEYS) == {
        "canonical",
        "accusationToleranceSeconds",
    }
    assert node["properties"]["accusationToleranceSeconds"]["type"] == "integer"
    assert node["properties"]["canonical"]["type"] == "string"