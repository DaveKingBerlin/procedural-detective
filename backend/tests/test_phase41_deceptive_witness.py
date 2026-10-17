"""Phase41 — deceptive witness model tests (§11-§14, §23, §30).

RED scope:
- Witness statements are CLAIMS; truthfulness is a hidden server-only,
  statement-level ``WitnessStatementTruth`` map (``TRUTHFUL | DECEPTIVE`` +
  optional reason) stored in the trusted truth path.
- A guilty witness (murderer with public role=witness) may lie to protect
  themselves; an innocent witness may also lie (PROTECT_OTHER) — no meta-rule
  "lying witness == murderer".
- Every materially deceptive claim has a discoverable contradiction path
  (a mechanical record), enforced by the ``DECEPTIVE_CLAIM_UNCONTRADICTED``
  generation-time gate.
- Determinism/idempotence: repeat question -> same answer + same truthfulness
  + no duplicate discovery.
- No pre-reveal leak of the truthfulness map / deception reasons.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain import witness as witness_domain  # noqa: E402
from app.domain.truth import WitnessStatementTruth  # noqa: E402
from app.generation.case_quality import (  # noqa: E402
    DECEPTIVE_CLAIM_UNCONTRADICTED,
    validate_case_quality,
)
from app.generation.evidence_facts import (  # noqa: E402
    MECHANICAL_CONTRADICTION_KINDS,
)
from app.generation.parser import parse_full_draft  # noqa: E402

# --------------------------------------------------------------------------- #
# WitnessStatementTruth trusted model
# --------------------------------------------------------------------------- #


def test_witness_statement_truth_is_a_hidden_trusted_model():
    entry = WitnessStatementTruth(
        witness_id="emily_reed",
        question_type="OBSERVATION",
        truthfulness="DECEPTIVE",
        deception_reason="SELF_PROTECTION",
    )
    assert entry.witness_id == "emily_reed"
    assert entry.question_type == "OBSERVATION"
    assert entry.truthfulness == "DECEPTIVE"
    truthful = WitnessStatementTruth(
        witness_id="emily_reed", question_type="TIME", truthfulness="TRUTHFUL"
    )
    assert truthful.deception_reason is None


def test_case_truth_carries_witness_truthfulness_map():
    from app.domain.truth import CaseTruth, Crime, CrimeTime
    from app.domain.resolution import ResolutionType

    truth = CaseTruth(
        case_id="C",
        case_version=1,
        title="T",
        crime=Crime(
            type="murder",
            victim_id="sarah_miller",
            murderer_id=None,
            motive_id="m",
            weapon_id="w",
            location_id="l",
            crime_time=CrimeTime(
                canonical="2026-09-11T22:17:00+02:00",
                accusation_tolerance_seconds=300,
            ),
            resolution_type=ResolutionType.SUICIDE,
        ),
        witness_truthfulness=(
            WitnessStatementTruth(
                witness_id="emily_reed",
                question_type="OBSERVATION",
                truthfulness="DECEPTIVE",
                deception_reason="PROTECT_OTHER",
            ),
        ),
    )
    assert truth.witness_truthfulness[0].truthfulness == "DECEPTIVE"


# --------------------------------------------------------------------------- #
# Hermetic scenario builder — a deterministic witness-murderer case with one
# deceptive statement + one discoverable mechanical contradiction record.
# --------------------------------------------------------------------------- #

_DECEPTIVE_STATEMENT = (
    "As Emily Reed, I was at the apartment building entrance the whole "
    "evening around 22:17 and I never saw anyone enter the victim's flat."
)


def _witness_murderer_draft(suicide: bool = False) -> tuple:
    """A crafted full draft where the hidden murderer is emily_reed (witness).

    The draft carries:
      - the public people (victim/suspects/witness/family);
      - a WITNESS_CLAIMS statement for emily_reed whose TEXT is deceptive;
      - a discoverable MECHANICAL contradiction record (an access-log /
        view-record fact) placing emily_reed at the scene AT the crime time;
      - a second witness (yara) with a truthful statement.
    Returns ``(draft, expected_deceptions)``.
    """
    raw = {
        "crime": {
            "type": "murder",
            "victimId": "sarah_miller",
            "murdererId": "emily_reed",
            "motiveId": "cover_up_embezzlement",
            "weaponId": "kitchen_knife",
            "locationId": "apartment",
            "crimeTime": {
                "canonical": "2026-09-11T22:17:00+02:00",
                "accusationToleranceSeconds": 300,
            },
            "resolutionType": "HOMICIDE",
        },
        "persons": [
            {
                "personId": "sarah_miller",
                "name": "Sarah Miller",
                "role": "victim",
                "affordances": ["VISIBLE_CHARACTER"],
                "presentedData": {
                    "age": 39,
                    "gender": "female",
                    "occupation": "CEO, Miller Consulting",
                },
            },
            {
                "personId": "thomas_reed",
                "name": "Thomas Reed",
                "role": "suspect",
                "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"],
            },
            {
                "personId": "emily_reed",
                "name": "Emily Reed",
                "role": "witness",
                "affordances": ["VISIBLE_CHARACTER"],
            },
            {
                "personId": "yara_salim",
                "name": "Yara Salim",
                "role": "witness",
                "affordances": ["VISIBLE_CHARACTER"],
            },
            {
                "personId": "david_kim",
                "name": "David Kim",
                "role": "family",
                "affordances": ["VISIBLE_CHARACTER"],
            },
        ],
        "motives": [
            {
                "motiveId": "cover_up_embezzlement",
                "label": "Cover up embezzlement",
                "affordances": ["MOTIVE_CANDIDATE"],
            },
            {
                "motiveId": "revenge_for_affair",
                "label": "Revenge for affair",
                "affordances": ["MOTIVE_CANDIDATE"],
            },
        ],
        "objects": [
            {
                "objectId": "kitchen_knife",
                "assetId": "PROP_KITCHEN_KNIFE_01",
                "affordances": ["POTENTIAL_WEAPON"],
            },
            {
                "objectId": "letter_opener",
                "assetId": "PROP_LETTER_OPENER_01",
                "affordances": ["POTENTIAL_WEAPON"],
            },
        ],
        "locations": [
            {"locationId": "apartment", "name": "the apartment building"},
            {"locationId": "lobby", "name": "the lobby"},
        ],
        "travelRules": [],
        "scene": {"locationId": "apartment", "name": "the apartment building"},
        "evidence": [
            {
                "id": "witness_statement_emily_01",
                "kind": "witness_statement",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": "record_x"},
                "presentation": {
                    "title": "Witness statement",
                    "description": "Emily recounted the course of the evening.",
                    "statement": _DECEPTIVE_STATEMENT,
                    "speakerName": "Emily Reed",
                },
                "propositions": [
                    {"type": "WITNESS_CLAIMS", "personId": "emily_reed"}
                ],
            },
            {
                "id": "witness_statement_yara_01",
                "kind": "witness_statement",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": "record_y"},
                "presentation": {
                    "title": "Witness statement",
                    "description": "Yara saw the victim earlier in the evening.",
                    "statement": (
                        "As Yara Salim, I saw Sarah Miller alive in the lobby "
                        "around 21:40."
                    ),
                    "speakerName": "Yara Salim",
                },
                "propositions": [
                    {
                        "type": "WITNESS_CLAIMS",
                        "personId": "yara_salim",
                        "observedAt": "2026-09-11T21:40:00+02:00",
                    }
                ],
            },
            {
                "id": "access_log_emily_01",
                "kind": "view_record",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": "record_a"},
                "presentation": {
                    "title": "Access log",
                    "description": (
                        "Building access log: Emily Reed's keycard opened the "
                        "lobby door at 22:14."
                    ),
                    "events": [
                        {
                            "time": "2026-09-11T22:14:00+02:00",
                            "personId": "emily_reed",
                            "action": "keycard_entry",
                        }
                    ],
                },
                "propositions": [
                    {
                        "type": "PERSON_OBSERVED_AT_LOCATION",
                        "personId": "emily_reed",
                        "locationId": "lobby",
                        "observedAt": "2026-09-11T22:14:00+02:00",
                        "uncertaintySeconds": 30,
                    }
                ],
            },
        ],
        "worldGraph": {
            "locations": [{"locationId": "lobby", "template": "apartment_template"}],
            "placements": [
                {
                    "objectId": "cctv_camera_emily",
                    "assetId": "CAMERA_HALL_01",
                    "locationId": "lobby",
                    "anchor": "hall_wall_01",
                    "interaction": "inspect",
                    "evidenceId": "access_log_emily_01",
                }
            ],
        },
    }
    draft = parse_full_draft(json.dumps(raw))
    assert draft is not None
    return draft


def _witness_truth_of(draft) -> tuple:
    """The canonical hidden truth of the crafted draft with the deceptive map."""
    from app.generation import pipeline

    public, evidence, truth = pipeline._draft_to_phase3(
        draft, case_id="CASE-p41-wit", title="T"
    )
    truth = replace(
        truth,
        witness_truthfulness=(
            WitnessStatementTruth(
                witness_id="emily_reed",
                question_type="OBSERVATION",
                truthfulness="DECEPTIVE",
                deception_reason="SELF_PROTECTION",
            ),
        ),
    )
    return public, evidence, truth


def _payload_view(payload_draft_persons: list[dict], evidence_records: list[dict]) -> dict:
    """The minimal allowlisted ``{draft: {persons, evidence}}`` view the
    witness projection helpers consume (serialized record shapes)."""
    return {
        "draft": {
            "persons": payload_draft_persons,
            "evidence": evidence_records,
        }
    }


# --------------------------------------------------------------------------- #
# Guilty deceptive witness (§12/§30)
# --------------------------------------------------------------------------- #


def test_guilty_deceptive_witness_contradiction_path_exists():
    """Every DECEPTIVE claim is backed by a discoverable mechanical record
    that contradicts it — the DECEPTIVE_CLAIM_UNCONTRADICTED gate passes."""
    draft = _witness_murderer_draft()
    public, evidence, truth = _witness_truth_of(draft)

    from app.domain.eligibility import derive_universes

    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert DECEPTIVE_CLAIM_UNCONTRADICTED not in codes
    assert codes == ()


def test_deceptive_claim_without_contradiction_is_rejected():
    """Remove the mechanical contradiction record -> the gate fires."""
    draft = _witness_murderer_draft()
    draft = replace(
        draft,
        evidence=tuple(
            e for e in draft.evidence if e.id != "access_log_emily_01"
        ),
    )
    public, evidence, truth = _witness_truth_of(draft)
    # Rebuild the truth map over the reduced evidence.
    from app.domain.eligibility import derive_universes

    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert DECEPTIVE_CLAIM_UNCONTRADICTED in codes


# --------------------------------------------------------------------------- #
# FIX-3 (ADV-41-05) — contradiction records are DISCOVERABLE through normal
# gameplay (bound to a world placement), and the gate REJECTS a deceptive
# claim whose contradiction exists only on paper.
# --------------------------------------------------------------------------- #


def _payload_view_of_draft(draft) -> dict:
    """A minimal serialized payload view of a GeneratedDraft (evidence +
    world graph placements) — exactly what the runtime discovery projection
    (``publication.project_discovery``) consumes."""
    return {
        "draft": {
            "evidence": [
                {
                    "id": f.id,
                    "kind": str(f.kind),
                    "discoverable": bool(f.discoverable),
                    "presentation": dict(f.presentation),
                }
                for f in draft.evidence
            ],
            "world_graph": {
                "locations": [
                    {"location_id": loc.location_id, "template": loc.template}
                    for loc in draft.world_graph.locations
                ],
                "placements": [
                    {
                        "object_id": p.object_id,
                        "asset_id": p.asset_id,
                        "location_id": p.location_id,
                        "anchor": p.anchor,
                        "interaction": p.interaction,
                        "evidence_id": p.evidence_id,
                    }
                    for p in draft.world_graph.placements
                ],
            },
        }
    }


def test_fix3a_contradiction_record_is_world_reachable():
    """(a) A generated guilty deceptive witness: the contradiction record is
    bound to a world placement and is REACHABLE through the runtime discovery
    path (``project_discovery`` returns the record with its interaction), so
    the player can challenge the lie before accusation. The quality gate
    passes."""
    draft = _witness_murderer_draft()
    public, evidence, truth = _witness_truth_of(draft)

    from app.domain.eligibility import derive_universes
    from app.services.publication import project_discovery

    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert DECEPTIVE_CLAIM_UNCONTRADICTED not in codes
    assert codes == ()

    # The contradiction record is linked to a placement in the world graph …
    placement_ids = {
        p.evidence_id for p in draft.world_graph.placements if p.evidence_id
    }
    assert "access_log_emily_01" in placement_ids
    # … and the runtime discovery projection surfaces it (interaction to use).
    payload = _payload_view_of_draft(draft)
    discovery = project_discovery(payload, "access_log_emily_01")
    assert discovery is not None
    assert discovery["kind"] == "view_record"
    assert discovery["interaction"] == "inspect"


def test_fix3b_deceptive_claim_with_undiscoverable_contradiction_rejected():
    """(b) A deceptive claim whose contradiction exists ONLY on paper (the
    record is present and discoverable-flagged but NOT linked to any world
    placement) is REJECTED by the strengthened gate — reachability, not mere
    existence, is required."""
    draft = _witness_murderer_draft()
    # Remove the placement that links the contradiction record (keep the
    # record itself in evidence — the old gate would have passed).
    draft = replace(
        draft,
        world_graph=replace(
            draft.world_graph,
            placements=tuple(
                p
                for p in draft.world_graph.placements
                if p.evidence_id != "access_log_emily_01"
            ),
        ),
    )
    public, evidence, truth = _witness_truth_of(draft)

    from app.domain.eligibility import derive_universes

    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert DECEPTIVE_CLAIM_UNCONTRADICTED in codes


def test_fix3c_driver_injects_camera_placement_for_contradiction():
    """The DRIVER's app-owned placement injection binds every witness-
    referencing mechanical observation (the ordinary ``d_ev_opp_*`` records and
    a witness murderer's ``d_ev_presence``) to an INSPECTABLE camera placement
    (the discoverability fix at the generation layer) — deterministic, with no
    leak-marked ``d_ev_contradict_*`` record, and the placement passes the
    world-graph validation."""
    from app.domain.resolution import ResolutionType
    from app.generation.schemas import (
        CrimeSpec,
        CrimeTimeSpec,
        LocationSpec,
        MotiveSpec,
        ObjectSpec,
        PersonSpec,
        PublicWorldSpec,
        SceneSpec,
    )
    from app.services import ollama_driver
    from app.services.ollama_driver import _evidence_gap_facts

    crime = CrimeSpec(
        type="murder",
        victim_id="sarah_miller",
        murderer_id="emily_reed",  # the hidden killer is a WITNESS
        motive_id="cover_up_embezzlement",
        weapon_id="kitchen_knife",
        location_id="apartment",
        crime_time=CrimeTimeSpec(canonical="2026-09-11T22:17:00+02:00", accusation_tolerance_seconds=300),
        resolution_type=ResolutionType.HOMICIDE,
    )
    public = PublicWorldSpec(
        persons=(
            PersonSpec(
                person_id="sarah_miller",
                name="Sarah Miller",
                role="victim",
                affordances=("VISIBLE_CHARACTER",),
                presented_data={
                    "age": 39,
                    "gender": "female",
                    "occupation": "CEO, Miller Consulting",
                },
            ),
            PersonSpec(person_id="thomas_reed", name="Thomas Reed", role="suspect", affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER")),
            PersonSpec(person_id="michael_carter", name="Michael Carter", role="suspect", affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER")),
            PersonSpec(person_id="emily_reed", name="Emily Reed", role="witness", affordances=("VISIBLE_CHARACTER",)),
        ),
        motives=(
            MotiveSpec(motive_id="cover_up_embezzlement", label="Cover up embezzlement", affordances=("MOTIVE_CANDIDATE",)),
            MotiveSpec(motive_id="revenge_for_affair", label="Revenge", affordances=("MOTIVE_CANDIDATE",)),
        ),
        objects=(
            ObjectSpec(object_id="kitchen_knife", asset_id="PROP_KITCHEN_KNIFE_01", affordances=("POTENTIAL_WEAPON",)),
            ObjectSpec(object_id="letter_opener", asset_id="PROP_LETTER_OPENER_01", affordances=("POTENTIAL_WEAPON",)),
        ),
        locations=(
            LocationSpec(location_id="apartment", name="the apartment"),
            LocationSpec(location_id="library", name="the library"),
        ),
        scene=SceneSpec(location_id="apartment", name="the apartment"),
    )

    from app.domain.witness_truth import WitnessStatementTruth

    class _Attempt:
        _witness_truthfulness = (
            WitnessStatementTruth(
                witness_id="emily_reed",
                question_type="OBSERVATION",
                truthfulness="DECEPTIVE",
                deception_reason="SELF_PROTECTION",
            ),
        )

    spec, _rules, _notes = _evidence_gap_facts(_Attempt(), crime, public, None)
    assert spec is not None
    ids = {f.id for f in spec.evidence}
    # FIX-1: the contradiction is the ORDINARY scene-presence observation; no
    # leak-marked ``d_ev_contradict_*`` / ``view_record`` record is generated.
    assert "d_ev_presence" in ids
    assert not any(i.startswith("d_ev_contradict_") for i in ids)

    # The injection produces exactly one camera placement bound to the record.
    from app.environments.manifests import load_environment

    kit = load_environment("apartment")
    objects, placements = ollama_driver._contradiction_world_placements(
        spec, public, kit
    )
    assert len(objects) == 1 and len(placements) == 1
    placement = placements[0]
    assert placement.evidence_id == "d_ev_presence"
    assert placement.interaction == "inspect"
    assert placement.asset_id == "CAMERA_HALL_01"
    assert objects[0].object_id == placement.object_id
    assert objects[0].affordances == ("INSPECTABLE",)

    # The injected placement + object pass the world-graph validation contract.
    from app.generation.schemas import WorldGraphLocationSpec, WorldGraphSpec
    from app.generation.safety import validate_world_graph

    wg = WorldGraphSpec(
        locations=tuple(
            WorldGraphLocationSpec(
                location_id=zone.zone_id,
                template=f"{kit.environment_id}_template",
                rooms=zone.rooms,
            )
            for zone in kit.zones
        ),
        placements=placements,
    )
    object_ids = {o.object_id for o in objects}
    evidence_ids = {f.id for f in spec.evidence}
    assert validate_world_graph(wg, object_ids, evidence_ids) == ()


def test_fix3d_driver_assemble_keeps_contradiction_discoverable():
    """Full ``_assemble`` integration: the driver's assembled draft carries the
    camera placement bound to the ordinary contradiction record and every
    world-graph validation stays clean, so the generated guilty deceptive
    witness's contradiction is genuinely discoverable through the world path."""
    from app.domain.witness_truth import WitnessStatementTruth
    from app.generation.provider import GenerationStage
    from app.generation.constraints import LockedConstraints
    from app.generation.parser import parse_stage
    from app.services.ollama_driver import (
        OllamaStageDriver,
        _evidence_gap_facts,
    )

    from tests.test_ollama_driver import (
        _j,
        _case_people,
    )

    CANONICAL = "2026-09-11T23:42:00+02:00"
    case_data = _case_people()
    case_data["crime"]["murdererId"] = "lisa_koenig"  # witness killer
    case_data["crime"]["resolutionType"] = "HOMICIDE"
    case_data["objects"] = []
    public = parse_stage(
        GenerationStage.PUBLIC_WORLD,
        _j(
            {
                k: v
                for k, v in case_data.items()
                if k in ("persons", "motives", "locations", "travelRules", "scene", "objects")
            }
        ),
        non_throwing=False,
    )
    crime = parse_stage(
        GenerationStage.CASE_TRUTH, _j({"crime": case_data["crime"]}), non_throwing=False
    )

    class _Attempt:
        locked = LockedConstraints(
            victim="Dr. Anna Weiss",
            murderer="Lisa Koenig",
            motive="stolen research data",
            weapon="bronze ceremonial ice pick",
            crime_time="23:42",
            witness="Lisa König",
        )
        _phase3_cache = None
        _witness_truthfulness = (
            WitnessStatementTruth(
                witness_id="lisa_koenig",
                question_type="OBSERVATION",
                truthfulness="DECEPTIVE",
                deception_reason="SELF_PROTECTION",
            ),
        )

    spec, _rules, _notes = _evidence_gap_facts(_Attempt(), crime, public, None)
    assert spec is not None

    from app.environments.manifests import load_environment
    from app.generation.safety import validate_world_graph
    from app.generation.pipeline import (
        _asset_reference_issues,
        _world_graph_resolution_issues,
    )
    from app.world.composer import compose_world
    from app.world.requirements import ObjectRequest, WorldRequirements

    world = WorldRequirements(
        environment_hint="office",
        objects=(ObjectRequest(requested_name="bronze ceremonial ice pick", criticality="required"),),
        relations=(),
        unsafe_unsupported=(),
    )
    kit = load_environment("office")
    composition = compose_world(world, environment_id="office", kit=kit)
    driver = OllamaStageDriver(settings=None, provider_factory=None)
    draft = driver._assemble(_Attempt(), crime, public, spec, world, composition)

    contradiction_placements = [
        p
        for p in draft.world_graph.placements
        if str(getattr(p, "evidence_id", "") or "") == "d_ev_presence"
    ]
    assert len(contradiction_placements) == 1
    placement = contradiction_placements[0]
    assert placement.interaction == "inspect"
    assert placement.asset_id == "CAMERA_HALL_01"
    # The camera object is INSPECTABLE-only — never a weapon/suspect candidate.
    camera_objects = [o for o in draft.objects if o.object_id == placement.object_id]
    assert camera_objects and camera_objects[0].affordances == ("INSPECTABLE",)

    # Every world-graph validation stays clean (the injected placement is a
    # first-class, valid part of the published world).
    assert _world_graph_resolution_issues(draft) == ()
    assert _asset_reference_issues(draft) == ()
    assert (
        validate_world_graph(
            draft.world_graph,
            {o.object_id for o in draft.objects},
            {e.id for e in draft.evidence},
        )
        == ()
    )




def test_repeat_question_is_deterministic_and_idempotent():
    """Repeat question -> same answer text and the same hidden truthfulness."""
    draft = _witness_murderer_draft()
    public, evidence, truth = _witness_truth_of(draft)

    from app.generation import pipeline

    payload_draft = {
        "persons": [
            {
                "person_id": p.person_id,
                "name": p.name,
                "role": p.role,
                "public_affordances": sorted(p.public_affordances),
            }
            for p in public.persons
        ],
        "evidence": [
            {
                "id": f.id,
                "kind": str(f.kind),
                "presentation": dict(f.presentation),
                "propositions": [
                    {
                        "person_id": getattr(p, "person_id", None),
                        "observed_at": getattr(p, "observed_at", None),
                    }
                    for p in f.propositions
                ],
                "discoverable": f.discoverable,
            }
            for f in evidence
        ],
    }
    emily = next(p for p in public.persons if p.person_id == "emily_reed")
    view = _payload_view(payload_draft["persons"], payload_draft["evidence"])
    first = witness_domain.project_witness_statement(
        view, {"person_id": "emily_reed", "name": "Emily Reed"}, witness_domain.WitnessQuestionType.OBSERVATION
    )
    second = witness_domain.project_witness_statement(
        view, {"person_id": "emily_reed", "name": "Emily Reed"}, witness_domain.WitnessQuestionType.OBSERVATION
    )
    assert first.statement.summary == second.statement.summary
    assert first.statement.observations == second.statement.observations
    assert _DECEPTIVE_STATEMENT in first.statement.summary or _DECEPTIVE_STATEMENT in (
        obs.text for obs in first.statement.observations
    )
    # The hidden truthfulness for the repeated question is stable.
    matching = [
        t
        for t in truth.witness_truthfulness
        if t.witness_id == "emily_reed" and t.question_type == "OBSERVATION"
    ]
    assert len(matching) == 1
    assert matching[0].truthfulness == "DECEPTIVE"
    assert matching[0].deception_reason == "SELF_PROTECTION"


def test_witness_statement_text_is_never_a_solver_premise():
    """The deceptive claim text stays OUT of the solver: WITNESS_CLAIMS is
    not consumed by any deduction rule (the metalogue claim/fact separation)."""
    from app.domain.evidence import WITNESS_CLAIMS

    draft = _witness_murderer_draft()
    emily_claims = [
        f
        for f in draft.evidence
        if any(
            p.type == WITNESS_CLAIMS and p.person_id == "emily_reed"
            for p in f.propositions
        )
    ]
    assert len(emily_claims) == 1
    # The proposition type is a claim, not a hard success fact.
    assert set(p.type for p in emily_claims[0].propositions) == {WITNESS_CLAIMS}


def test_no_meta_rule_lying_witness_equals_murderer():
    """A DECEPTIVE witness who is NOT the murderer is a valid, solvable case:
    an innocent witness lies to protect someone else (PROTECT_OTHER)."""
    draft = _witness_murderer_draft()
    # Make the killer a suspect; the liar (emily) stays an innocent witness.
    draft = replace(
        draft,
        crime=replace(draft.crime, murderer_id="thomas_reed"),
    )
    from app.generation import pipeline

    public, evidence, truth = pipeline._draft_to_phase3(
        draft, case_id="CASE-p41-innocent", title="T"
    )
    truth = replace(
        truth,
        witness_truthfulness=(
            WitnessStatementTruth(
                witness_id="emily_reed",
                question_type="OBSERVATION",
                truthfulness="DECEPTIVE",
                deception_reason="PROTECT_OTHER",
            ),
            WitnessStatementTruth(
                witness_id="yara_salim",
                question_type="OBSERVATION",
                truthfulness="TRUTHFUL",
            ),
        ),
    )
    from app.domain.eligibility import derive_universes
    from app.generation.case_quality import MURDERER_NOT_SUSPECT_CANDIDATE

    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert DECEPTIVE_CLAIM_UNCONTRADICTED not in codes
    # The innocent liar is NOT flagged as the killer and the killer (suspect)
    # remains a final target — the lie alone never implies guilt.
    assert MURDERER_NOT_SUSPECT_CANDIDATE not in codes


# --------------------------------------------------------------------------- #
# FIX-4 (ADV-41-06) — innocent deceptive witnesses are a documented boundary
# (never auto-generated), and a fixture-driven innocent liar keeps the true
# murderer uniquely solvable.
# --------------------------------------------------------------------------- #


def test_fix4_only_guilty_deceptive_witness_is_generated():
    """(FIX-4) The deterministic driver stamps EXACTLY ONE generated
    deceptive-witness path — the guilty witness-murderer (SELF_PROTECTION).
    An innocent witness is NEVER auto-flagged (``PROTECT_OTHER`` /
    ``HIDE_UNRELATED_SECRET`` are fixture-driven per §13/§30 — a documented
    boundary, not a silent gap)."""
    from app.generation.evidence_facts import (
        HIDE_UNRELATED_SECRET,
        PROTECT_OTHER,
        SELF_PROTECTION,
        deceptive_witness_truths,
        witness_murderer_of,
    )

    # (1) a guilty witness-murderer -> exactly one SELF_PROTECTION stamp.
    draft = _witness_murderer_draft()
    public, _evidence, truth = _witness_truth_of(draft)
    assert witness_murderer_of(truth.crime, public) == "emily_reed"
    stamps = deceptive_witness_truths(truth.crime, public)
    assert len(stamps) == 1
    assert stamps[0].witness_id == "emily_reed"
    assert stamps[0].truthfulness == "DECEPTIVE"
    assert stamps[0].deception_reason == SELF_PROTECTION
    assert stamps[0].deception_reason not in (PROTECT_OTHER, HIDE_UNRELATED_SECRET)

    # (2) a case whose killer is a suspect (all witnesses innocent) -> NO
    # innocent liar is ever auto-stamped by the deterministic driver rule.
    innocent_draft = replace(
        draft,
        crime=replace(draft.crime, murderer_id="thomas_reed"),
    )
    public2, _evidence2, truth2 = _witness_truth_of(innocent_draft)
    assert witness_murderer_of(truth2.crime, public2) is None
    assert deceptive_witness_truths(truth2.crime, public2) == ()


def test_fix4_innocent_liar_keeps_murderer_uniquely_solvable():
    """(FIX-4) A fixture-driven innocent liar (fixture-authored
    ``PROTECT_OTHER`` truthfulness, never auto-generated) does NOT disturb the
    deduction: the true murderer (a SUSPECT) stays uniquely solvable and the
    case validates end-to-end (contradiction reachable)."""
    from app.generation.evidence_facts import SELF_PROTECTION

    from tests.test_ollama_driver import _case_people, _j
    from app.generation.constraints import LockedConstraints
    from app.generation.provider import GenerationStage
    from app.generation.parser import parse_stage
    from app.services.ollama_driver import OllamaStageDriver, _evidence_gap_facts

    CANONICAL = "2026-09-11T23:42:00+02:00"
    case_data = _case_people()
    case_data["crime"]["murdererId"] = "paul_becker"  # a SUSPECT murderer
    case_data["objects"] = []
    pub = {
        k: v
        for k, v in case_data.items()
        if k in ("persons", "motives", "locations", "travelRules", "scene", "objects")
    }
    public = parse_stage(GenerationStage.PUBLIC_WORLD, _j(pub), non_throwing=False)
    crime = parse_stage(
        GenerationStage.CASE_TRUTH, _j({"crime": case_data["crime"]}), non_throwing=False
    )

    class _Attempt:
        locked = LockedConstraints(
            victim="Dr. Anna Weiss",
            murderer="Paul Becker",
            motive="stolen research data",
            weapon="bronze ceremonial ice pick",
            crime_time="23:42",
            witness="Lisa König",
        )
        _phase3_cache = None
        _witness_truthfulness = ()

    spec, _rules, _notes = _evidence_gap_facts(_Attempt(), crime, public, None)
    assert spec is not None

    # Fixture-drive the innocent liar (PROTECT_OTHER) + her discoverable
    # contradiction record + camera placement (the driver never auto-stamps
    # this — §13/§30 fixture scenario).
    from app.generation.schemas import (
        EvidenceSpec,
        ObjectSpec,
        PlacementSpec,
        PropSpec,
    )

    contradict_id = "d_ev_contradict_lisakoenig"
    cont_record = EvidenceSpec(
        id=contradict_id,
        kind="view_record",
        propositions=(
            PropSpec(
                type="PERSON_OBSERVED_AT_LOCATION",
                person_id="lisa_koenig",
                location_id="konsortium_office",
                observed_at=CANONICAL,
                uncertainty_seconds=60,
            ),
        ),
        source_ref={"kind": "record", "sourceId": f"record_{contradict_id}"},
        reliability="high",
        presentation={
            "title": "Access log",
            "description": "Access log: Lisa Koenig's keycard registered at the office.",
        },
        discoverable=True,
    )
    spec = replace(spec, evidence=tuple([*spec.evidence, cont_record]))

    from app.environments.manifests import load_environment
    from app.world.composer import compose_world
    from app.world.requirements import ObjectRequest, WorldRequirements

    world = WorldRequirements(
        environment_hint="office",
        objects=(ObjectRequest(requested_name="bronze ceremonial ice pick", criticality="required"),),
        relations=(),
        unsafe_unsupported=(),
    )
    kit = load_environment("office")
    composition = compose_world(world, environment_id="office", kit=kit)
    driver = OllamaStageDriver(settings=None, provider_factory=None)
    draft = driver._assemble(_Attempt(), crime, public, spec, world, composition)
    # Bind the innocent liar's contradiction to a camera placement (the same
    # deterministic injection the driver applies to generated contradictions).
    camera_id = "cctv_camera_innocent_liar"
    camera_object = ObjectSpec(
        object_id=camera_id,
        asset_id="CAMERA_HALL_01",
        affordances=("INSPECTABLE",),
        subtype="camera",
    )
    camera_placement = PlacementSpec(
        object_id=camera_id,
        asset_id="CAMERA_HALL_01",
        location_id="office_mainroom",
        anchor="office_cctv_01",
        interaction="inspect",
        evidence_id=contradict_id,
    )
    draft = replace(
        draft,
        objects=tuple([*draft.objects, camera_object]),
        world_graph=replace(
            draft.world_graph,
            placements=tuple([*draft.world_graph.placements, camera_placement]),
        ),
    )

    from app.domain.eligibility import derive_universes
    from app.generation.pipeline import _draft_to_phase3
    from app.domain.solver import solve_case
    from app.validation.solution import evaluate_solution

    p, e, truth = _draft_to_phase3(draft, case_id="CASE-p41-innocent-solvable", title="T")
    # The innocent liar's hidden truthfulness is authored explicitly.
    from app.domain.truth import WitnessStatementTruth

    truth = replace(
        truth,
        witness_truthfulness=(
            WitnessStatementTruth(
                witness_id="lisa_koenig",
                question_type="OBSERVATION",
                truthfulness="DECEPTIVE",
                deception_reason="PROTECT_OTHER",
            ),
        ),
    )
    # The innocent liar is a WITNESS (not the murderer) …
    assert "lisa_koenig" not in derive_universes(p).suspect_ids
    # … and the true murderer stays uniquely solvable.
    proof = solve_case(p, e)
    assert proof.who.unique is True
    assert proof.who.winner == "paul_becker"
    val = evaluate_solution(proof, truth)
    assert val.murderer_true is True
    assert val.all_true is True
    # The whole case validates (the innocent liar's contradiction is reachable).
    codes = validate_case_quality(p, truth, e, derive_universes(p), draft)
    assert codes == (), codes


def test_hidden_truthfulness_is_never_serialized_into_public_draft_sections():
    """The ``witness_truthfulness`` map lives ONLY in the truth section of a
    serialized published payload — never in ``draft`` (public DTO source)."""
    draft = _witness_murderer_draft()
    public, evidence, truth = _witness_truth_of(draft)

    from app.services.publication import _to_plain

    draft_tree = _to_plain(draft)
    truth_tree = _to_plain(truth)
    draft_text = json.dumps(draft_tree, sort_keys=True)
    truth_text = json.dumps(truth_tree, sort_keys=True)
    # The hidden map is serialized in the trusted truth tree …
    assert '"witness_truthfulness"' in truth_text
    assert "SELF_PROTECTION" in truth_text
    # … and NEVER in the draft tree (the public DTO source).
    assert "deception" not in draft_text
    assert "SELF_PROTECTION" not in draft_text
    assert "PROTECT_OTHER" not in draft_text
    assert "truthfulness" not in draft_text
