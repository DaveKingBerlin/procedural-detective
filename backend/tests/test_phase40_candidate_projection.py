"""Phase40 — trusted deterministic candidate-integrity projection tests.

Covers the closed three-rule integrity semantics (§7/§10/§11), outcome
vocabulary (§13/§44), idempotence (§24), determinism (§25), ordering (§26/§28),
the exact Easy regression shape (§29/§30), murderer-missing-candidate (§31),
combined corruption (§32), deep-equality preservation (§33), role-independence
(§34), escalation (§35/§36) and the adversarial catalog (§53).

All tests are PURE: they construct ``PersonSpec`` records in memory and call
``project_candidate_integrity`` directly (zero providers, zero network, zero
clock).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.candidate_projection import (  # noqa: E402
    CandidateProjectionOutcome,
    project_candidate_integrity,
)
from app.generation.schemas import PersonSpec  # noqa: E402

_SE = "SUSPECT_ELIGIBLE"
_VIS = "VISIBLE_CHARACTER"


def _p(
    person_id: str,
    role: str = "suspect",
    affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"),
    name: str | None = None,
    note: str | None = None,
) -> PersonSpec:
    return PersonSpec(
        person_id=person_id,
        name=name or person_id.replace("_", " ").title(),
        role=role,
        affordances=tuple(affordances),
        presented_data={"note": note or person_id},
    )


def _ids(persons) -> tuple[str, ...]:
    return tuple(p.person_id for p in persons)


def _affordances_of(person) -> frozenset[str]:
    return frozenset(person.affordances)


def _easy_shape():
    """The EXACT §29 Easy regression shape (victim eligible, witness present,
    murderer eligible, two valid red herrings)."""
    return (
        _p("anna_weiss", role="victim", affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"), name="Dr. Anna Weiss"),
        _p("lisa_koenig", role="witness", affordances=("VISIBLE_CHARACTER",), name="Lisa König"),
        _p("paul_becker", role="suspect", affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"), name="Paul Becker"),
        _p("marcus_fischer", role="suspect", affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"), name="Marcus Fischer"),
        _p("sophie_hoffmann", role="suspect", affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"), name="Sophie Hoffmann"),
    )


# --------------------------------------------------------------------------- #
# §29 — the exact Easy regression shape
# --------------------------------------------------------------------------- #


def test_exact_easy_regression_shape():
    persons = _easy_shape()
    result = project_candidate_integrity(persons, murderer_id="paul_becker")

    assert result.outcome is CandidateProjectionOutcome.CORRECTED
    assert result.victim_eligibility_removed is True
    assert result.witness_eligibility_removed_count == 0
    assert result.murderer_eligibility_added is False
    assert result.candidate_count_before == 4  # victim + murderer + 2 herrings
    assert result.candidate_count_after == 3  # murderer + 2 herrings

    by_id = {p.person_id: p for p in result.corrected_persons}
    # victim candidate token removed; every other field unchanged
    victim = by_id["anna_weiss"]
    assert _SE not in victim.affordances
    assert victim.role == "victim"
    assert victim.name == "Dr. Anna Weiss"
    assert victim.presented_data == {"note": "anna_weiss"}
    # witness not candidate (unchanged)
    witness = by_id["lisa_koenig"]
    assert _SE not in witness.affordances
    # murderer retained as candidate
    assert _SE in by_id["paul_becker"].affordances
    # red herrings retained as candidates
    assert _SE in by_id["marcus_fischer"].affordances
    assert _SE in by_id["sophie_hoffmann"].affordances
    # person ordering preserved
    assert _ids(result.corrected_persons) == _ids(persons)


# --------------------------------------------------------------------------- #
# §30 — direct regression against the observed repair regression
# --------------------------------------------------------------------------- #


def test_victim_only_defect_does_not_introduce_witness_issue():
    persons = (
        _p("anna_weiss", role="victim", affordances=(_SE, _VIS), name="Dr. Anna Weiss"),
        _p("lisa_koenig", role="witness", affordances=(_VIS,), name="Lisa König"),
        _p("paul_becker", role="suspect", affordances=(_SE, _VIS), name="Paul Becker"),
        _p("red_herring", role="suspect", affordances=(_SE, _VIS), name="Red Herring"),
    )
    # the input is a VICTIM_IN_SUSPECT_CANDIDATES-only shape
    assert _SE in _affordances_of(persons[0])
    assert _SE not in _affordances_of(persons[1])

    result = project_candidate_integrity(persons, murderer_id="paul_becker")

    assert result.outcome is CandidateProjectionOutcome.CORRECTED
    by_id = {p.person_id: p for p in result.corrected_persons}
    # pure removal: WITNESS_IN_SUSPECT_CANDIDATES is NEVER introduced
    assert _SE not in by_id["anna_weiss"].affordances
    assert _SE not in by_id["lisa_koenig"].affordances
    # murderer remains a candidate
    assert _SE in by_id["paul_becker"].affordances
    # red herring untouched
    assert _SE in by_id["red_herring"].affordances


# --------------------------------------------------------------------------- #
# §31 — murderer-missing-candidate
# --------------------------------------------------------------------------- #


def test_murderer_missing_candidate_is_added_without_truth_marker():
    persons = (
        _p("anna_weiss", role="victim", affordances=(_VIS,), name="Dr. Anna Weiss"),
        _p("paul_becker", role="suspect", affordances=(_VIS,), name="Paul Becker"),
    )
    result = project_candidate_integrity(persons, murderer_id="paul_becker")

    assert result.outcome is CandidateProjectionOutcome.CORRECTED
    assert result.murderer_eligibility_added is True
    murderer = {p.person_id: p for p in result.corrected_persons}["paul_becker"]
    assert _SE in murderer.affordances
    assert murderer.role == "suspect"  # public role unchanged
    assert murderer.name == "Paul Becker"
    # no truth marker: the public role vocabulary stays closed, no "murderer"
    assert murderer.role not in ("murderer",)
    # candidate count went from 0 -> 1
    assert result.candidate_count_before == 0
    assert result.candidate_count_after == 1


# --------------------------------------------------------------------------- #
# §32 — combined corruption
# --------------------------------------------------------------------------- #


def test_combined_corruption_victim_witness_and_murderer():
    persons = (
        _p("anna_weiss", role="victim", affordances=(_SE, _VIS), name="Dr. Anna Weiss"),
        _p("lisa_koenig", role="witness", affordances=(_SE, _VIS), name="Lisa König"),
        _p("paul_becker", role="suspect", affordances=(_VIS,), name="Paul Becker"),
        _p("red_herring", role="suspect", affordances=(_SE, _VIS), name="Red Herring"),
    )
    result = project_candidate_integrity(persons, murderer_id="paul_becker")

    assert result.outcome is CandidateProjectionOutcome.CORRECTED
    assert result.victim_eligibility_removed is True
    assert result.witness_eligibility_removed_count == 1
    assert result.murderer_eligibility_added is True
    assert result.candidate_count_before == 3
    assert result.candidate_count_after == 2

    by_id = {p.person_id: p for p in result.corrected_persons}
    assert _SE not in by_id["anna_weiss"].affordances
    assert _SE not in by_id["lisa_koenig"].affordances
    assert _SE in by_id["paul_becker"].affordances
    assert _SE in by_id["red_herring"].affordances


# --------------------------------------------------------------------------- #
# §33 — deep-equality preservation of unaffected records
# --------------------------------------------------------------------------- #


def test_unrelated_persons_and_affordances_preserved_deep_equal():
    persons = (
        _p("anna_weiss", role="victim", affordances=(_SE, _VIS), name="Dr. Anna Weiss"),
        _p("marcus_fischer", role="suspect", affordances=(_SE, _VIS, "MOTIVE_CANDIDATE"), name="Marcus Fischer"),
        _p("family_member", role="family", affordances=(_VIS,), name="Family Member", note="fn"),
        _p("other_person", role="other", affordances=(_VIS, "INSPECTABLE"), name="Other Person", note="op"),
    )
    result = project_candidate_integrity(persons, murderer_id="marcus_fischer")

    assert result.outcome is CandidateProjectionOutcome.CORRECTED
    corrected = {p.person_id: p for p in result.corrected_persons}
    # affected record: victim only
    assert _SE not in corrected["anna_weiss"].affordances
    # every unrelated record is DEEP-EQUAL to its input (same object values)
    for idx, person_id in enumerate(("marcus_fischer", "family_member", "other_person")):
        original = {p.person_id: p for p in persons}[person_id]
        rebuilt = corrected[person_id]
        assert rebuilt == original, person_id
        assert rebuilt.affordances == original.affordances, person_id
        assert rebuilt.presented_data == original.presented_data, person_id
        assert rebuilt.role == original.role, person_id
        assert rebuilt.name == original.name, person_id


# --------------------------------------------------------------------------- #
# §34 — role-independence (ordinary role mutation does not rewrite the universe)
# --------------------------------------------------------------------------- #


def test_ordinary_role_mutation_alone_does_not_rewrite_eligibility():
    # A person with ordinary role "family"/"other" that the model chose as a
    # candidate keeps SUSPECT_ELIGIBLE — role mutation alone must NOT silently
    # redefine the candidate universe (Phase-3 eligibility semantics).
    persons = (
        _p("victim_1", role="victim", affordances=(_VIS,), name="Victim"),
        _p("candidate_1", role="suspect", affordances=(_SE, _VIS), name="C One"),
        _p("candidate_2", role="other", affordances=(_SE, _VIS), name="C Two"),
        _p("candidate_3", role="family", affordances=(_SE, _VIS), name="C Three"),
        _p("murderer", role="suspect", affordances=(_SE, _VIS), name="Murderer"),
    )
    result = project_candidate_integrity(persons, murderer_id="murderer")

    # non-victim/witness roles keep their SUSPECT_ELIGIBLE membership exactly
    by_id = {p.person_id: p for p in result.corrected_persons}
    assert _SE in by_id["candidate_1"].affordances
    assert _SE in by_id["candidate_2"].affordances
    assert _SE in by_id["candidate_3"].affordances
    assert _SE in by_id["murderer"].affordances
    assert result.candidate_count_after == 4


# --------------------------------------------------------------------------- #
# §35/§53 — escalation: leave genuine semantic defects to the canonical layer
# --------------------------------------------------------------------------- #


def test_escalation_invalid_public_role_is_returned_untouched():
    persons = (
        _p("anna_weiss", role="victim", affordances=(_SE, _VIS), name="Dr. Anna Weiss"),
        _p("paul_becker", role="murderer", affordances=(_SE, _VIS), name="Paul Becker"),
    )
    result = project_candidate_integrity(persons, murderer_id="paul_becker")

    assert result.outcome is CandidateProjectionOutcome.FAILED_VALIDATION
    # nothing is rewritten — the canonical PUBLIC_ROLE_TRUTH_LEAK validator
    # must still see the original data
    assert result.corrected_persons is persons
    assert {p.role for p in result.corrected_persons} == {"victim", "murderer"}


def test_escalation_missing_murderer_person_is_declined():
    persons = tuple(
        p for p in _easy_shape() if p.person_id != "paul_becker"
    )  # drop the murderer person entirely
    result = project_candidate_integrity(persons, murderer_id="paul_becker")
    assert result.outcome is CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT
    assert result.corrected_persons is persons


def test_escalation_unknown_murderer_id_is_declined():
    persons = _easy_shape()
    result = project_candidate_integrity(persons, murderer_id="ghost_who_never_existed")
    assert result.outcome is CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT
    assert result.corrected_persons is persons


def test_escalation_empty_murderer_id_is_declined():
    result = project_candidate_integrity(_easy_shape(), murderer_id=None)
    assert result.outcome is CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT
    result = project_candidate_integrity(_easy_shape(), murderer_id="")
    assert result.outcome is CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT


def test_escalation_invalid_person_structure_is_failed():
    # A record with no exposeable person id/role/affordance surface.
    class Broken:
        pass

    persons = (
        _p("anna_weiss", role="victim", affordances=(_SE, _VIS), name="Dr. Anna Weiss"),
        Broken(),
    )
    result = project_candidate_integrity(persons, murderer_id="paul_becker")
    assert result.outcome is CandidateProjectionOutcome.FAILED_VALIDATION


def test_escalation_empty_person_tuple_is_declined():
    result = project_candidate_integrity((), murderer_id="paul_becker")
    assert result.outcome is CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT
    assert result.corrected_persons == ()


def test_escalation_murderer_in_victim_role_is_declined():
    # The truth declares the victim as murderer — beyond candidate membership;
    # the projection cannot satisfy the three rules and escalates.
    persons = (
        _p("anna_weiss", role="victim", affordances=(_SE, _VIS), name="Dr. Anna Weiss"),
        _p("marcus_fischer", role="suspect", affordances=(_SE, _VIS), name="Marcus Fischer"),
    )
    result = project_candidate_integrity(persons, murderer_id="anna_weiss")
    assert result.outcome is CandidateProjectionOutcome.DECLINED_INSUFFICIENT_CONTEXT


def test_public_role_murderer_still_rejected():
    """§53 — a truth-bearing public role "murderer" is NEVER accepted/rewritten
    by the projection and never silently fixed by adding eligibility."""
    persons = (
        _p("thomas_reed", role="suspect", affordances=(_SE, _VIS), name="Thomas Reed"),
        _p("paul_becker", role="murderer", affordances=(_SE, _VIS), name="Paul Becker"),
    )
    result = project_candidate_integrity(persons, murderer_id="paul_becker")
    assert result.outcome is CandidateProjectionOutcome.FAILED_VALIDATION
    assert {p.role for p in result.corrected_persons} == {"suspect", "murderer"}


# --------------------------------------------------------------------------- #
# §36/§53 — candidate-count behavior (Decision B: no minimum beyond the closed
# rules; never auto-promote unrelated people)
# --------------------------------------------------------------------------- #


def test_candidate_count_after_correction_is_kept_and_no_auto_promotion():
    # Removing a victim leaves a valid non-empty universe -> continue; the
    # model's `other`/`family` people are NOT auto-promoted to candidates.
    persons = (
        _p("anna_weiss", role="victim", affordances=(_SE, _VIS), name="Dr. Anna Weiss"),
        _p("paul_becker", role="suspect", affordances=(_SE, _VIS), name="Paul Becker"),
        _p("marcus_fischer", role="suspect", affordances=(_SE, _VIS), name="Marcus Fischer"),
        _p("family_member", role="family", affordances=(_VIS,), name="Family"),
        _p("other_person", role="other", affordances=(_VIS,), name="Other"),
    )
    result = project_candidate_integrity(persons, murderer_id="paul_becker")
    assert result.outcome is CandidateProjectionOutcome.CORRECTED
    assert result.candidate_count_after == 2  # murderer + red herring only
    by_id = {p.person_id: p for p in result.corrected_persons}
    assert _SE not in by_id["family_member"].affordances
    assert _SE not in by_id["other_person"].affordances


def test_multiple_witnesses_all_have_eligibility_removed():
    persons = (
        _p("anna_weiss", role="victim", affordances=(_VIS,), name="Dr. Anna Weiss"),
        _p("w1", role="witness", affordances=(_SE, _VIS), name="W One"),
        _p("w2", role="witness", affordances=(_SE, _VIS), name="W Two"),
        _p("w3", role="witness", affordances=(_SE, _VIS), name="W Three"),
        _p("paul_becker", role="suspect", affordances=(_SE, _VIS), name="Paul Becker"),
    )
    result = project_candidate_integrity(persons, murderer_id="paul_becker")
    assert result.outcome is CandidateProjectionOutcome.CORRECTED
    assert result.witness_eligibility_removed_count == 3
    for p in result.corrected_persons:
        if p.role == "witness":
            assert _SE not in p.affordances


# --------------------------------------------------------------------------- #
# §24/§25/§27/§26/§28 — idempotence, determinism, ordering, stable universe
# --------------------------------------------------------------------------- #


def test_idempotence():
    persons = _easy_shape()
    once = project_candidate_integrity(persons, murderer_id="paul_becker")
    twice = project_candidate_integrity(
        once.corrected_persons, murderer_id="paul_becker"
    )
    assert twice.corrected_persons == once.corrected_persons
    assert twice.outcome is CandidateProjectionOutcome.NO_CHANGE


def test_determinism_identical_input_identical_output():
    persons = _easy_shape()
    first = project_candidate_integrity(persons, murderer_id="paul_becker")
    second = project_candidate_integrity(persons, murderer_id="paul_becker")
    assert first == second
    assert first.corrected_persons == second.corrected_persons
    assert first.outcome is second.outcome


def test_person_ordering_preserved():
    import random

    persons = list(_easy_shape())
    random.Random(1234).shuffle(persons)
    result = project_candidate_integrity(persons, murderer_id="paul_becker")
    assert _ids(result.corrected_persons) == _ids(persons)


def test_affordance_normalization_is_provider_order_independent():
    """§27 — rebuilding an affected person uses a canonical sorted affordance
    tuple; two provider orderings of the SAME affordance set give identical
    corrected records."""
    a = _p("anna_weiss", role="victim", affordances=(_SE, _VIS), name="Dr. Anna Weiss")
    b = _p("anna_weiss", role="victim", affordances=(_VIS, _SE), name="Dr. Anna Weiss")
    family = _p("family", role="family", affordances=(_VIS,), name="Family")
    murderer = _p("paul_becker", role="suspect", affordances=(_SE,), name="Paul Becker")
    result_a = project_candidate_integrity((a, family, murderer), "paul_becker")
    result_b = project_candidate_integrity((b, family, murderer), "paul_becker")
    assert result_a.outcome is result_b.outcome is CandidateProjectionOutcome.CORRECTED
    assert result_a.corrected_persons == result_b.corrected_persons


def test_suspect_ids_remain_sorted_and_stable():
    """§28 — the rebuilt records re-derive a sorted ``CandidateUniverses``
    suspect id tuple (canonical eligibility derivation; never role-filtered)."""
    from app.domain.eligibility import derive_universes
    from app.domain.public import PublicCase, PublicPerson

    result = project_candidate_integrity(_easy_shape(), murderer_id="paul_becker")
    persons = tuple(
        PublicPerson(
            person_id=p.person_id,
            name=p.name,
            role=p.role,
            public_affordances=frozenset(p.affordances),
        )
        for p in result.corrected_persons
    )
    public = PublicCase(
        case_id="CASE-p40-sorted",
        case_version=1,
        persons=persons,
    )
    universes = derive_universes(public)
    assert list(universes.suspect_ids) == sorted(universes.suspect_ids)
    assert universes.suspect_ids == ("marcus_fischer", "paul_becker", "sophie_hoffmann")
    # stable across repeated derivations
    assert derive_universes(public).suspect_ids == universes.suspect_ids