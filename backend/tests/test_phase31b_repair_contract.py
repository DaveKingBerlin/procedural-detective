"""Phase31B — §15/§25 — REPAIR_v1 contract alignment.

The repair schema (``json_schema_for_stage_output("repair")`` = the full-draft
contract) must expose the SAME aligned constraints as the stage schemas: the
canonical timestamp grammar, the closed reliability vocabulary, the required
interaction string and the proposition structured requirements — so repair #1
can actually REPAIR the structural issue instead of preserving it.

This suite also proves the §25 end-to-end shape on a deterministic full draft
carrying the four proven live mismatch classes:

  before  -> STRUCTURED_OUTPUT_INVALID (structural issue set non-empty)
  repair  -> parser-valid full draft (issue set empty -> effectiveness VALID)

No repair-count/deadline/timeout budget changes are part of Phase31B — the
canonical defaults are asserted unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.generation import budgets, parser, prompts  # noqa: E402
from app.generation.report import ValidationOutcome, ValidationReport  # noqa: E402
from app.generation.validation_codes import (  # noqa: E402
    repair_effectiveness,
    validation_failure_codes,
)


def _full_draft(*, crime_canonical="2026-09-11T22:17:00+02:00",
                reliability="high",
                match=True,
                interaction="inspect",
                placement_missing_interaction=False):
    propositions = [
        {
            "type": "FORENSIC_WEAPON_MATCH",
            "objectId": "weapon_01",
            "uncertaintySeconds": 60,
            "structured": {"match": match},
        }
    ]
    world_graph = {
        "locations": [
            {"locationId": "loc_01", "template": "kitchen_template", "rooms": []}
        ],
        "placements": [
            {
                "objectId": "weapon_01",
                "assetId": "PROP_WEAPON_01",
                "locationId": "loc_01",
                "anchor": "desk_main",
                "interaction": interaction,
                "evidenceId": "e001",
            }
        ],
    }
    if placement_missing_interaction:
        world_graph["placements"][0].pop("interaction", None)
    return {
        "crime": {
            "type": "murder",
            "victimId": "victim_01",
            "murdererId": "murderer_01",
            "motiveId": "motive_01",
            "weaponId": "weapon_01",
            "locationId": "loc_01",
            "crimeTime": {
                "canonical": crime_canonical,
                "accusationToleranceSeconds": 120,
            },
        },
        "persons": [
            {
                "personId": "murderer_01",
                "name": "Thomas",
                "role": "suspect",
                "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER", "INSPECTABLE"],
            }
        ],
        "motives": [
            {"motiveId": "motive_01", "label": "Embezzlement", "affordances": ["MOTIVE_CANDIDATE"]}
        ],
        "objects": [
            {"objectId": "weapon_01", "assetId": "PROP_WEAPON_01", "affordances": ["INSPECTABLE", "POTENTIAL_WEAPON"]}
        ],
        "locations": [{"locationId": "loc_01", "name": "Office"}],
        "travelRules": [],
        "scene": {"locationId": "loc_01", "name": "Office"},
        "evidence": [
            {
                "id": "e001",
                "kind": "forensic",
                "reliability": reliability,
                "discoverable": True,
                "sourceRef": {"kind": "report", "sourceId": "s001"},
                "propositions": propositions,
                "presentation": {"title": "T", "description": "D"},
            }
        ],
        "worldGraph": world_graph,
    }


# --------------------------------------------------------------------------- #
# §15 — REPAIR_v1 schema carries the aligned constraints
# --------------------------------------------------------------------------- #


def test_repair_schema_carries_canonical_timestamp_constraint():
    schema = prompts.json_schema_for_stage_output("repair")
    assert schema is not None
    node = schema["properties"]["crime"]["properties"]["crimeTime"]["properties"][
        "canonical"
    ]
    assert node["type"] == "string"
    assert node["pattern"] == prompts.canonical_timestamp_schema_pattern()
    from app.domain.time_interval import canonical_timestamp_schema_pattern  # noqa: PLC0415

    assert node["pattern"] == canonical_timestamp_schema_pattern()


def test_repair_schema_carries_reliability_enum():
    schema = prompts.json_schema_for_stage_output("repair")
    from app.domain.evidence import RELIABILITY_VOCABULARY  # noqa: PLC0415

    node = schema["properties"]["evidence"]["items"]["properties"]["reliability"]
    assert node["enum"] == sorted(RELIABILITY_VOCABULARY)


def test_repair_schema_carries_interaction_contract():
    schema = prompts.json_schema_for_stage_output("repair")
    from app.generation.schemas import MAX_SINGLE_TEXT_FIELD_CHARS  # noqa: PLC0415

    placement = schema["properties"]["worldGraph"]["properties"]["placements"]["items"]
    assert "interaction" in placement["required"]
    assert placement["properties"]["interaction"]["type"] == "string"
    assert placement["properties"]["interaction"]["maxLength"] == MAX_SINGLE_TEXT_FIELD_CHARS


def test_repair_schema_teaches_structured_requirements():
    schema = prompts.json_schema_for_stage_output("repair")
    structured = schema["properties"]["evidence"]["items"]["properties"][
        "propositions"
    ]["items"]["properties"]["structured"]
    assert structured["type"] == "object"  # open-object teaching node preserved
    assert "forensic" in structured["description"].lower() or "match" in structured[
        "description"
    ]


# --------------------------------------------------------------------------- #
# §25 — deterministic draft with the four proven mismatch classes
# --------------------------------------------------------------------------- #


def test_draft_with_proven_mismatch_classes_fails_structural():
    """A full draft carrying the four live failure classes (time-only
    timestamp, non-canonical reliability, string match, missing interaction)
    is structurally INVALID through the strict full-draft parser."""
    draft = _full_draft(
        crime_canonical="20:15",
        reliability="HIGH",
        match="true",
        placement_missing_interaction=True,
    )
    issues = parser.collect_full_draft_issues(json.dumps(draft))
    assert issues
    codes = validation_failure_codes(ValidationReport(structural_issues=issues))
    assert codes == ("STRUCTURED_OUTPUT_INVALID",)
    report = ValidationReport(structural_issues=issues)
    assert report.outcome is ValidationOutcome.RECOVERABLE_REPAIR


def test_repair_output_correcting_mismatch_classes_parses_valid():
    """The doctored draft in exactly the canonical shapes becomes a fully
    parser-valid draft — repair #1 CAN actually fix the structural issue."""
    repaired = _full_draft(
        crime_canonical="2026-09-11T22:17:00+02:00",
        reliability="high",
        match=True,
        interaction="inspect",
    )
    issues = parser.collect_full_draft_issues(json.dumps(repaired))
    assert issues == ()
    draft = parser.parse_full_draft(json.dumps(repaired), non_throwing=False)
    assert draft is not None
    assert draft.crime.crime_time.canonical == "2026-09-11T22:17:00+02:00"


def test_repair_effectiveness_semantics_unchanged():
    """The repair-effectiveness algebra is untouched by Phase31B: a structural
    fail -> clean repair yields VALID, and the closed failure-code vocabulary
    (STRUCTURED_OUTPUT_INVALID) is unchanged for parser/structural failures."""
    before = ("STRUCTURED_OUTPUT_INVALID", "VALIDATION_FAILED")
    after = ()
    assert repair_effectiveness(before, after) == "VALID"
    assert validation_failure_codes(
        ValidationReport(structural_issues=("crime is missing",))
    ) == ("STRUCTURED_OUTPUT_INVALID",)


# --------------------------------------------------------------------------- #
# §5/§38 — no repair-budget / deadline / timeout changes
# --------------------------------------------------------------------------- #


def test_repair_budget_deadline_timeout_defaults_unchanged(tmp_path):
    """Phase31B must not raise any budget: the canonical repair-pass allowance,
    the generation deadline and the provider timeout default stay at their
    documented values."""
    assert budgets.DEFAULT_MAX_REPAIR_PASSES == 2
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    assert settings.generation_deadline_seconds == 60
    assert settings.ollama_timeout_seconds == 60.0