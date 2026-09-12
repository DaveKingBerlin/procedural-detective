"""Evidence referential-integrity + duplicate-id validation tests (47A).

Every typed person/location/object/motive id referenced by a proposition must
resolve in the public model; duplicate evidence ids are rejected. The solver
must reject invalid evidence deterministically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    SCENE_LOCATION,
    golden_evidence,
    golden_public,
)

from app.domain.evidence import (  # noqa: E402
    NOISE_HEARD_AT,
    PERSON_OBSERVED_AT_LOCATION,
    VICTIM_LAST_SEEN_ALIVE_AT,
    EvidenceFact,
    Reliability,
    SourceRef,
    TypedProposition,
    validate_evidence,
)
from app.domain.solver import solve_case  # noqa: E402


def _clone_with_new_id(fact: EvidenceFact, new_id: str) -> EvidenceFact:
    import dataclasses

    return dataclasses.replace(fact, id=new_id)


def test_valid_golden_evidence_has_no_issues():
    public = golden_public()
    assert validate_evidence(public, golden_evidence()) == ()


def test_unknown_person_id_is_rejected():
    public = golden_public()
    evidence = golden_evidence()
    bad = EvidenceFact(
        id="bad_person",
        kind="cctv_observation",
        propositions=(
            TypedProposition(
                type=PERSON_OBSERVED_AT_LOCATION,
                person_id="ghost_person",
                location_id=SCENE_LOCATION,
                observed_at="2026-09-11T22:16:00+02:00",
            ),
        ),
        source_ref=SourceRef(kind="camera", source_id="camera_1"),
        reliability=Reliability.HIGH,
        presentation={"title": "bad", "description": "bad"},
    )
    issues = validate_evidence(public, [bad])
    assert any("unknown person_id" in issue and "ghost_person" in issue for issue in issues)
    with pytest.raises(ValueError, match="invalid evidence"):
        solve_case(public, [bad])


def test_unknown_location_id_is_rejected():
    public = golden_public()
    evidence = [
        EvidenceFact(
            id="bad_location",
            kind="witness_observation",
            propositions=(
                TypedProposition(
                    type=NOISE_HEARD_AT,
                    location_id="disneyland",
                    observed_at="2026-09-11T22:16:00+02:00",
                ),
            ),
            source_ref=SourceRef(kind="witness", source_id="witness_1"),
            reliability=Reliability.MEDIUM,
            presentation={"title": "bad", "description": "bad"},
        )
    ]
    assert any(
        "unknown location_id" in issue and "disneyland" in issue
        for issue in validate_evidence(public, evidence)
    )


def test_unknown_object_id_is_rejected():
    public = golden_public()
    evidence = golden_evidence()
    bad = EvidenceFact(
        id="bad_object",
        kind="forensic",
        propositions=(
            TypedProposition(
                type="FORENSIC_WEAPON_MATCH",
                object_id="lightsaber",
                structured={"match": True},
            ),
        ),
        source_ref=SourceRef(kind="lab", source_id="lab_1"),
        reliability=Reliability.HIGH,
        presentation={"title": "bad", "description": "bad"},
    )
    assert any(
        "unknown object_id" in issue and "lightsaber" in issue
        for issue in validate_evidence(public, [bad])
    )


def test_unknown_motive_id_is_rejected():
    public = golden_public()
    evidence = [
        EvidenceFact(
            id="bad_motive",
            kind="digital",
            propositions=(TypedProposition(type="MOTIVE_LINKED_TO_PERSON", motive_id="no_such_motive"),),
            source_ref=SourceRef(kind="record", source_id="record_1"),
            reliability=Reliability.HIGH,
            presentation={"title": "bad", "description": "bad"},
        )
    ]
    assert any(
        "unknown motive_id" in issue and "no_such_motive" in issue
        for issue in validate_evidence(public, evidence)
    )


def test_duplicate_evidence_ids_are_rejected():
    public = golden_public()
    evidence = golden_evidence()
    # Duplicate last_seen_01 by direct replacement.
    duplicated = list(evidence)
    duplicated.append(_clone_with_new_id(evidence[0], evidence[0].id))
    issues = validate_evidence(public, duplicated)
    assert any("duplicate evidence id: last_seen_01" in issue for issue in issues)
    with pytest.raises(ValueError, match="duplicate evidence id"):
        solve_case(public, duplicated)


def test_solver_rejects_invalid_input_combinations():
    public = golden_public()
    with pytest.raises(TypeError):
        solve_case(public, tuple("not-evidence"))  # not EvidenceFact items
    with pytest.raises(TypeError):
        solve_case("not-a-public-case", golden_evidence())
    # Empty evidence is VALID input (no references to validate); the solver
    # answers with a non-unique proof rather than an error.
    empty_proof = solve_case(public, [])
    assert empty_proof.who.unique is False
    assert empty_proof.why.unique is False
    assert empty_proof.weapon.unique is False


def test_non_discoverable_evidence_is_ignored_by_solver():
    """Non-discoverable facts must not drive deduction (reachability gate)."""
    public = golden_public()
    evidence = golden_evidence()
    hidden = [
        EvidenceFact(
            id="secret_sight_01",
            kind="cctv_observation",
            propositions=(
                TypedProposition(
                    type=VICTIM_LAST_SEEN_ALIVE_AT,
                    person_id="sarah_miller",
                    location_id=SCENE_LOCATION,
                    observed_at="2026-09-11T23:59:00+02:00",
                ),
            ),
            source_ref=SourceRef(kind="camera", source_id="camera_9"),
            reliability=Reliability.HIGH,
            presentation={"title": "hidden", "description": "hidden"},
            discoverable=False,
        )
    ]
    with_hidden = evidence + hidden
    proof_visible = solve_case(public, evidence)
    proof_with_hidden = solve_case(public, with_hidden)
    assert proof_with_hidden.when == proof_visible.when
    assert "secret_sight_01" not in proof_with_hidden.when.critical_evidence_ids
    assert proof_with_hidden.when.feasible == proof_visible.when.feasible


def test_invalid_reliability_value_raises():
    from app.domain.evidence import EvidenceFact

    with pytest.raises(ValueError):
        EvidenceFact(
            id="x",
            kind="y",
            propositions=(
                TypedProposition(
                    type=NOISE_HEARD_AT,
                    location_id=SCENE_LOCATION,
                    observed_at="2026-09-11T22:16:00+02:00",
                ),
            ),
            reliability="mega-high",
            presentation={},
        )


def test_time_bearing_proposition_requires_observed_at():
    with pytest.raises(ValueError):
        TypedProposition(type=NOISE_HEARD_AT, location_id=SCENE_LOCATION)