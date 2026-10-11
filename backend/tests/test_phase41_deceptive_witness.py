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
        "worldGraph": {"locations": [], "placements": []},
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
