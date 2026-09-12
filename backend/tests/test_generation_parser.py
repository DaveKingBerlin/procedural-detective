"""Strict generation parser tests (Phase4 J)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_STAGE_PAYLOADS,
)

from app.generation.parser import (  # noqa: E402
    GenerationParseError,
    MAX_PROVIDER_OUTPUT_CHARS,
    collect_full_draft_issues,
    collect_issues,
    parse_full_draft,
    parse_stage,
)
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    MAX_ACCUSATION_TOLERANCE_SECONDS,
    MAX_OBSERVATION_UNCERTAINTY_SECONDS,
    MAX_TRAVEL_TIME_SECONDS,
    CrimeTimeSpec,
    PropSpec,
    TravelRuleSpec,
)

CASE_TRUTH = GenerationStage.CASE_TRUTH
PUBLIC_WORLD = GenerationStage.PUBLIC_WORLD
EVIDENCE = GenerationStage.EVIDENCE
WORLD_GRAPH = GenerationStage.WORLD_GRAPH


def _dumps(doc) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


# --- small valid building blocks -------------------------------------------


def _person(person_id="p1", **overrides):
    doc = {
        "personId": person_id,
        "name": "Person One",
        "role": "suspect",
        "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"],
    }
    doc.update(overrides)
    return doc


def _motive(motive_id="m1", **overrides):
    doc = {"motiveId": motive_id, "label": "Motive One", "affordances": ["MOTIVE_CANDIDATE"]}
    doc.update(overrides)
    return doc


def _object(object_id="o1", **overrides):
    doc = {
        "objectId": object_id,
        "assetId": "PROP_KITCHEN_KNIFE_01",
        "affordances": ["POTENTIAL_WEAPON", "INSPECTABLE"],
        "subtype": "sharp_weapon",
    }
    doc.update(overrides)
    return doc


def _location(location_id="l1", **overrides):
    doc = {"locationId": location_id, "name": "Location One"}
    doc.update(overrides)
    return doc


def _travel_rule(**overrides):
    doc = {"fromLocationId": "l1", "toLocationId": "l2", "travelTimeSeconds": 60}
    doc.update(overrides)
    return doc


def _scene(**overrides):
    doc = {"locationId": "l1", "name": "Scene"}
    doc.update(overrides)
    return doc


def _public_world_doc(**overrides):
    doc = {
        "persons": [_person()],
        "motives": [_motive()],
        "objects": [_object()],
        "locations": [_location("l1"), _location("l2")],
        "travelRules": [_travel_rule()],
        "scene": _scene(),
    }
    doc.update(overrides)
    return doc


def _proposition(**overrides):
    doc = {"type": "PERSON_OBSERVED_AT_LOCATION", "personId": "p1", "observedAt": "2026-09-11T22:15:00+02:00"}
    doc.update(overrides)
    return doc


def _evidence(evidence_id="ev1", **overrides):
    doc = {
        "id": evidence_id,
        "kind": "cctv_observation",
        "reliability": "high",
        "discoverable": True,
        "sourceRef": {"kind": "record", "sourceId": "record_ev1"},
        "propositions": [_proposition()],
        "presentation": {"title": "Camera 01", "description": "Structured fact."},
    }
    doc.update(overrides)
    return doc


def _evidence_doc(**overrides):
    doc = {"evidence": [_evidence()]}
    doc.update(overrides)
    return doc


def _crime_time(**overrides):
    doc = {"canonical": "2026-09-11T22:17:00+02:00", "accusationToleranceSeconds": 120}
    doc.update(overrides)
    return doc


def _crime(**overrides):
    doc = {
        "type": "murder",
        "victimId": "sarah_miller",
        "murdererId": "thomas_reed",
        "motiveId": "cover_up_embezzlement",
        "weaponId": "kitchen_knife",
        "locationId": "l1",
        "crimeTime": _crime_time(),
    }
    doc.update(overrides)
    return doc


def _case_truth_doc(**overrides):
    doc = {"crime": _crime()}
    doc.update(overrides)
    return doc


def _wg_location(**overrides):
    doc = {"locationId": "l1", "template": "kitchen_template", "rooms": ["kitchen"]}
    doc.update(overrides)
    return doc


def _placement(**overrides):
    doc = {
        "objectId": "o1",
        "assetId": "PROP_KITCHEN_KNIFE_01",
        "locationId": "l1",
        "anchor": "kitchen_counter",
        "interaction": "inspect",
        "evidenceId": "ev1",
    }
    doc.update(overrides)
    return doc


def _world_graph_doc(**overrides):
    doc = {
        "worldGraph": {
            "locations": [_wg_location(), _wg_location(location_id="l2", template="office_template")],
            "placements": [_placement()],
        }
    }
    doc.update(overrides)
    return doc


def _full_draft_doc(**overrides):
    doc = {
        "crime": _crime(),
        "persons": [_person()],
        "motives": [_motive()],
        "objects": [_object()],
        "locations": [_location("l1"), _location("l2")],
        "travelRules": [_travel_rule()],
        "scene": _scene(),
        "evidence": [_evidence()],
        "worldGraph": _world_graph_doc()["worldGraph"],
    }
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------------
# golden payloads parse cleanly
# ---------------------------------------------------------------------------


def test_golden_case_truth_parses():
    spec = parse_stage(CASE_TRUTH, GOLDEN_STAGE_PAYLOADS[CASE_TRUTH])
    assert spec is not None
    assert spec.type == "murder"
    assert spec.victim_id == "sarah_miller"
    assert spec.murderer_id == "thomas_reed"
    assert spec.motive_id == "cover_up_embezzlement"
    assert spec.weapon_id == "kitchen_knife"
    assert spec.crime_time.canonical == "2026-09-11T22:17:00+02:00"
    assert spec.crime_time.accusation_tolerance_seconds == 300


def test_golden_public_world_parses():
    spec = parse_stage(PUBLIC_WORLD, GOLDEN_STAGE_PAYLOADS[PUBLIC_WORLD])
    assert spec is not None
    assert len(spec.persons) == 6
    assert len(spec.motives) == 4
    assert len(spec.objects) == 4
    assert len(spec.locations) == 3
    assert len(spec.travel_rules) == 2
    assert spec.scene is not None
    assert spec.scene.location_id == "miller_apartment_kitchen"


def test_golden_evidence_parses():
    spec = parse_stage(EVIDENCE, GOLDEN_STAGE_PAYLOADS[EVIDENCE])
    assert spec is not None
    assert len(spec.evidence) == 14


def test_golden_world_graph_parses():
    spec = parse_stage(WORLD_GRAPH, GOLDEN_STAGE_PAYLOADS[WORLD_GRAPH])
    assert spec is not None
    assert len(spec.locations) == 3
    assert len(spec.placements) == 4


def test_golden_payload_collect_issues_is_empty():
    for stage, payload in GOLDEN_STAGE_PAYLOADS.items():
        assert collect_issues(stage, payload) == ()
    assert collect_full_draft_issues(GOLDEN_FULL_DRAFT) == ()
    assert collect_issues(GenerationStage.REPAIR, GOLDEN_FULL_DRAFT) == ()


# ---------------------------------------------------------------------------
# unknown / dangerous keys
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_rejected():
    doc = _public_world_doc(bogusKey=True)
    issues = collect_issues(PUBLIC_WORLD, _dumps(doc))
    assert issues
    assert any("unknown key 'bogusKey'" in issue for issue in issues)


def test_unknown_nested_key_rejected():
    doc = _public_world_doc(persons=[_person(evil=1)])
    issues = collect_issues(PUBLIC_WORLD, _dumps(doc))
    assert any("unknown key 'evil'" in issue for issue in issues)


def test_unknown_world_graph_key_rejected():
    doc = {"worldGraph": {"locations": [], "placements": [], "npcs": []}}
    issues = collect_issues(WORLD_GRAPH, _dumps(doc))
    assert any("unknown key 'npcs'" in issue for issue in issues)


# ---------------------------------------------------------------------------
# duplicate ids
# ---------------------------------------------------------------------------


def test_duplicate_person_id_rejected():
    doc = _public_world_doc(persons=[_person("p1"), _person("p1")])
    issues = collect_issues(PUBLIC_WORLD, _dumps(doc))
    assert any("duplicate person id" in issue for issue in issues)


def test_duplicate_evidence_id_rejected():
    doc = _evidence_doc(evidence=[_evidence("ev1"), _evidence("ev1")])
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("duplicate evidence id" in issue for issue in issues)


def test_duplicate_object_id_rejected():
    doc = _public_world_doc(objects=[_object("o1"), _object("o1")])
    issues = collect_issues(PUBLIC_WORLD, _dumps(doc))
    assert any("duplicate object id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# timestamps
# ---------------------------------------------------------------------------


def test_malformed_timestamp_rejected():
    doc = _evidence_doc(evidence=[_evidence(propositions=[_proposition(observedAt="22:17")])])
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("invalid or out-of-domain timestamp" in issue for issue in issues)


def test_out_of_range_timestamp_rejected():
    doc = _evidence_doc(
        evidence=[_evidence(propositions=[_proposition(observedAt="2026-09-11T22:15:00+99:00")])]
    )
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("invalid or out-of-domain timestamp" in issue for issue in issues)


def test_crime_time_timestamp_validated():
    doc = _case_truth_doc(crime=_crime(crimeTime=_crime_time(canonical="not-a-time")))
    issues = collect_issues(CASE_TRUTH, _dumps(doc))
    assert issues


def test_claimed_departure_timestamp_validated():
    doc = _evidence_doc(
        evidence=[
            _evidence(
                propositions=[
                    _proposition(
                        type="ALIBI_TIME_CLAIM",
                        structured={"claimedDeparture": "after dinner"},
                    )
                ]
            )
        ]
    )
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("claimedDeparture" in issue for issue in issues)


# ---------------------------------------------------------------------------
# proposition typing
# ---------------------------------------------------------------------------


def test_unknown_proposition_type_rejected():
    doc = _evidence_doc(evidence=[_evidence(propositions=[_proposition(type="SPACE_LASER")])])
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("unknown proposition type" in issue for issue in issues)


def test_non_bool_forensic_match_rejected():
    doc = _evidence_doc(
        evidence=[
            _evidence(
                propositions=[
                    _proposition(type="FORENSIC_WEAPON_MATCH", structured={"match": "yes"})
                ]
            )
        ]
    )
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("'match'" in issue for issue in issues)


def test_missing_claimed_departure_rejected():
    doc = _evidence_doc(
        evidence=[
            _evidence(propositions=[_proposition(type="ALIBI_TIME_CLAIM", structured={})])
        ]
    )
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("claimedDeparture" in issue for issue in issues)


def test_victim_last_seen_requires_observed_at():
    doc = _evidence_doc(
        evidence=[
            _evidence(
                propositions=[
                    _proposition(type="VICTIM_LAST_SEEN_ALIVE_AT", observedAt=None)
                ]
            )
        ]
    )
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert issues


# ---------------------------------------------------------------------------
# vocabularies and types
# ---------------------------------------------------------------------------


def test_bad_affordance_rejected():
    doc = _public_world_doc(persons=[_person(affordances=["SUPER_POWER"])])
    issues = collect_issues(PUBLIC_WORLD, _dumps(doc))
    assert any("not in the documented vocabulary" in issue for issue in issues)


def test_optional_weapon_subtype_affordance_accepted():
    # POTENTIAL_SHARP_WEAPON is documented in app.domain.public (31.1.3) and is
    # part of the Phase 3 golden objects, so it must parse.
    doc = _public_world_doc(
        objects=[_object(affordances=["POTENTIAL_WEAPON", "INSPECTABLE", "POTENTIAL_SHARP_WEAPON"])]
    )
    assert collect_issues(PUBLIC_WORLD, _dumps(doc)) == ()


def test_bad_reliability_rejected():
    doc = _evidence_doc(evidence=[_evidence(reliability="omniscient")])
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("reliability" in issue for issue in issues)


def test_discoverable_must_be_bool():
    doc = _evidence_doc(evidence=[_evidence(discoverable="yes")])
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert issues


def test_negative_travel_time_rejected():
    doc = _public_world_doc(travelRules=[_travel_rule(travelTimeSeconds=-5)])
    assert collect_issues(PUBLIC_WORLD, _dumps(doc))


def test_negative_uncertainty_rejected():
    doc = _evidence_doc(evidence=[_evidence(propositions=[_proposition(uncertaintySeconds=-1)])])
    assert collect_issues(EVIDENCE, _dumps(doc))


def test_negative_accuracy_tolerance_rejected():
    doc = _case_truth_doc(crime=_crime(crimeTime=_crime_time(accusationToleranceSeconds=-1)))
    assert collect_issues(CASE_TRUTH, _dumps(doc))


def test_bool_rejected_as_int():
    doc = _evidence_doc(evidence=[_evidence(propositions=[_proposition(uncertaintySeconds=True)])])
    assert collect_issues(EVIDENCE, _dumps(doc))


def test_empty_string_id_rejected():
    doc = _public_world_doc(persons=[_person(person_id="")])
    assert collect_issues(PUBLIC_WORLD, _dumps(doc))


def test_missing_required_key_rejected():
    doc = _case_truth_doc(crime=_crime(crimeTime=None))
    assert collect_issues(CASE_TRUTH, _dumps(doc))
    doc2 = _case_truth_doc(crime={key: value for key, value in _crime().items() if key != "crimeTime"})
    assert collect_issues(CASE_TRUTH, _dumps(doc2))
    doc3 = _public_world_doc(scene=None)
    assert collect_issues(PUBLIC_WORLD, _dumps(doc3))


# ---------------------------------------------------------------------------
# sizing bounds
# ---------------------------------------------------------------------------


def test_oversized_evidence_rejected():
    doc = _evidence_doc(evidence=[_evidence(f"ev{i}") for i in range(51)])
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("MAX_EVIDENCE_ITEMS" in issue for issue in issues)


def test_oversized_characters_rejected():
    doc = _public_world_doc(persons=[_person(f"p{i}") for i in range(9)])
    issues = collect_issues(PUBLIC_WORLD, _dumps(doc))
    assert any("MAX_CHARACTERS" in issue for issue in issues)


def test_oversized_locations_rejected():
    doc = _public_world_doc(locations=[_location(f"l{i}") for i in range(9)])
    issues = collect_issues(PUBLIC_WORLD, _dumps(doc))
    assert any("MAX_LOCATIONS" in issue for issue in issues)


def test_oversized_single_text_field_rejected():
    doc = _public_world_doc(persons=[_person(name="x" * 4001)])
    issues = collect_issues(PUBLIC_WORLD, _dumps(doc))
    assert any("exceeds 4000 chars" in issue for issue in issues)


def test_oversized_description_rejected():
    doc = _evidence_doc(evidence=[_evidence(presentation={"title": "t", "description": "x" * 12001})])
    issues = collect_issues(EVIDENCE, _dumps(doc))
    assert any("exceeds 12000 chars" in issue for issue in issues)


def test_provider_output_length_rejected():
    content = " " + "x" * (MAX_PROVIDER_OUTPUT_CHARS + 1)
    issues = collect_issues(CASE_TRUTH, content)
    assert any("MAX_PROVIDER_OUTPUT_CHARS" in issue for issue in issues)


# ---------------------------------------------------------------------------
# non-JSON / empty
# ---------------------------------------------------------------------------


def test_non_json_content_rejected_single_issue():
    issues = collect_issues(CASE_TRUTH, "<not-json>")
    assert issues == ("provider output is not valid JSON: Expecting value",)


def test_empty_content_rejected():
    issues = collect_issues(CASE_TRUTH, "")
    assert issues == ("provider output is empty or whitespace",)


def test_whitespace_content_rejected():
    issues = collect_issues(CASE_TRUTH, "   \n\t ")
    assert issues == ("provider output is empty or whitespace",)


def test_json_array_root_rejected():
    issues = collect_issues(CASE_TRUTH, "[]")
    assert issues == ("provider output root must be a JSON object",)


# ---------------------------------------------------------------------------
# non-throwing behavior
# ---------------------------------------------------------------------------


def test_non_throwing_returns_none_for_bad_content():
    assert parse_stage(CASE_TRUTH, "<not-json>") is None
    assert parse_full_draft(GOLDEN_FULL_DRAFT.replace("murder", "murderx")) is None


def test_non_throwing_false_raises_generation_parse_error():
    with pytest.raises(GenerationParseError) as excinfo:
        parse_stage(CASE_TRUTH, "<not-json>", non_throwing=False)
    assert excinfo.value.issues == ("provider output is not valid JSON: Expecting value",)


def test_generation_parse_error_issues_matches_collect_issues():
    doc = _public_world_doc(persons=[_person(), _person("p1")], bogus=True)
    content = _dumps(doc)
    issues = collect_issues(PUBLIC_WORLD, content)
    assert issues  # duplicate id + unknown top key
    assert issues == tuple(sorted(set(issues)))
    with pytest.raises(GenerationParseError) as excinfo:
        parse_stage(PUBLIC_WORLD, content, non_throwing=False)
    assert excinfo.value.issues == issues


# ---------------------------------------------------------------------------
# full draft
# ---------------------------------------------------------------------------


def test_full_draft_golden_parses():
    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    assert len(draft.persons) == 6
    assert len(draft.evidence) == 14
    assert len(draft.world_graph.placements) == 4


def test_full_draft_unknown_top_level_key_rejected():
    doc = _full_draft_doc(caseId="CASE-001")
    issues = collect_full_draft_issues(_dumps(doc))
    assert any("unknown key 'caseId'" in issue for issue in issues)


def test_full_draft_missing_section_rejected():
    doc = _full_draft_doc()
    del doc["worldGraph"]
    assert collect_full_draft_issues(_dumps(doc))


def test_full_draft_duplicate_person_rejected():
    doc = _full_draft_doc(persons=[_person("p1"), _person("p1")])
    issues = collect_full_draft_issues(_dumps(doc))
    assert any("duplicate person id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# DEF-041 / ADV-128 — duplicate JSON object keys
# ---------------------------------------------------------------------------

_DUP_CRIME = (
    '{"crime": {"type": "murder", "victimId": "sarah_miller", '
    '"murdererId": "thomas_reed", "murdererId": "anna_karlsson", '
    '"motiveId": "cover_up_embezzlement", "weaponId": "kitchen_knife", '
    '"locationId": "l1", "crimeTime": {"canonical": "2026-09-11T22:17:00+02:00", '
    '"accusationToleranceSeconds": 300}}}'
)

_DUP_PROPOSITION = (
    '{"evidence": [{"id": "ev1", "kind": "cctv_observation", '
    '"reliability": "high", "discoverable": true, '
    '"sourceRef": {"kind": "record", "sourceId": "record_ev1"}, '
    '"propositions": [{"type": "PERSON_OBSERVED_AT_LOCATION", '
    '"personId": "sarah_miller", "personId": "thomas_reed", '
    '"observedAt": "2026-09-11T22:15:00+02:00"}], '
    '"presentation": {"title": "t", "description": "d"}}]}'
)

_DUP_PLACEMENT = (
    '{"worldGraph": {"locations": [{"locationId": "l1", "template": "t", '
    '"rooms": []}], "placements": [{"objectId": "o1", '
    '"assetId": "PROP_KITCHEN_KNIFE_01", "locationId": "l1", '
    '"anchor": "desk_main", "anchor": "kitchen_counter", '
    '"interaction": "inspect", "evidenceId": null}]}}'
)

_DUP_FULL_DRAFT = (
    '{"crime": {"type": "murder", "victimId": "sarah_miller", '
    '"murdererId": "thomas_reed", "murdererId": "anna_karlsson", '
    '"motiveId": "cover_up_embezzlement", "weaponId": "kitchen_knife", '
    '"locationId": "l1", "crimeTime": {"canonical": "2026-09-11T22:17:00+02:00", '
    '"accusationToleranceSeconds": 300}}, "persons": [], "motives": [], '
    '"objects": [], "locations": [], "travelRules": [], '
    '"scene": {"locationId": "l1", "name": "S"}, "evidence": [], '
    '"worldGraph": {"locations": [], "placements": []}}'
)


def test_def041_duplicate_crime_key_rejected_and_reported():
    issues = collect_issues(CASE_TRUTH, _DUP_CRIME)
    assert issues == ("(duplicate key 'murdererId')",)
    with pytest.raises(GenerationParseError) as excinfo:
        parse_stage(CASE_TRUTH, _DUP_CRIME, non_throwing=False)
    assert excinfo.value.issues == ("(duplicate key 'murdererId')",)
    assert parse_stage(CASE_TRUTH, _DUP_CRIME, non_throwing=True) is None


def test_def041_duplicate_proposition_key_rejected():
    issues = collect_issues(EVIDENCE, _DUP_PROPOSITION)
    assert issues == ("(duplicate key 'personId')",)


def test_def041_duplicate_placement_key_rejected():
    issues = collect_issues(WORLD_GRAPH, _DUP_PLACEMENT)
    assert issues == ("(duplicate key 'anchor')",)


def test_def041_duplicate_key_full_draft_rejected():
    issues = collect_full_draft_issues(_DUP_FULL_DRAFT)
    assert issues == ("(duplicate key 'murdererId')",)
    assert collect_issues(GenerationStage.REPAIR, _DUP_FULL_DRAFT) == issues


def test_def041_golden_payloads_have_no_duplicate_key_issues():
    for stage, payload in GOLDEN_STAGE_PAYLOADS.items():
        issues = collect_issues(stage, payload)
        assert issues == ()
        assert parse_full_draft(GOLDEN_FULL_DRAFT) is not None


# ---------------------------------------------------------------------------
# DEF-042 / ADV-129 — bounded integer fields
# ---------------------------------------------------------------------------


def test_def042_travel_time_out_of_range_rejected():
    for value in (10**30, MAX_TRAVEL_TIME_SECONDS + 1):
        doc = _public_world_doc(travelRules=[_travel_rule(travelTimeSeconds=value)])
        issues = collect_issues(PUBLIC_WORLD, _dumps(doc))
        assert issues
        assert any("out of range" in issue for issue in issues)
        with pytest.raises(ValueError, match="out of range"):
            TravelRuleSpec(
                from_location_id="a", to_location_id="b", travel_time_seconds=value
            )


def test_def042_uncertainty_out_of_range_rejected():
    for value in (10**30, MAX_OBSERVATION_UNCERTAINTY_SECONDS + 1):
        doc = _evidence_doc(
            evidence=[_evidence(propositions=[_proposition(uncertaintySeconds=value)])]
        )
        issues = collect_issues(EVIDENCE, _dumps(doc))
        assert issues
        assert any("out of range" in issue for issue in issues)
        with pytest.raises(ValueError, match="out of range"):
            PropSpec(
                type="PERSON_OBSERVED_AT_LOCATION",
                person_id="p1",
                observed_at="2026-09-11T22:15:00+02:00",
                uncertainty_seconds=value,
            )


def test_def042_accuracy_tolerance_out_of_range_rejected():
    for value in (10**30, MAX_ACCUSATION_TOLERANCE_SECONDS + 1):
        doc = _case_truth_doc(
            crime=_crime(crimeTime=_crime_time(accusationToleranceSeconds=value))
        )
        issues = collect_issues(CASE_TRUTH, _dumps(doc))
        assert issues
        assert any("out of range" in issue for issue in issues)
        with pytest.raises(ValueError, match="out of range"):
            CrimeTimeSpec(
                canonical="2026-09-11T22:17:00+02:00",
                accusation_tolerance_seconds=value,
            )


def test_def042_bound_values_accepted_and_golden_passes():
    # Top of range is still admissible.
    TravelRuleSpec(from_location_id="a", to_location_id="b", travel_time_seconds=MAX_TRAVEL_TIME_SECONDS)
    PropSpec(
        type="PERSON_OBSERVED_AT_LOCATION",
        person_id="p1",
        observed_at="2026-09-11T22:15:00+02:00",
        uncertainty_seconds=MAX_OBSERVATION_UNCERTAINTY_SECONDS,
    )
    CrimeTimeSpec(canonical="2026-09-11T22:17:00+02:00", accusation_tolerance_seconds=MAX_ACCUSATION_TOLERANCE_SECONDS)
    # Golden values are far below the bounds and parse cleanly.
    for stage, payload in GOLDEN_STAGE_PAYLOADS.items():
        assert collect_issues(stage, payload) == ()