"""Phase41 — deterministic final homicide target universe tests (§8).

RED scope:
- ``final_homicide_target_ids(public)`` = sorted, deduped ``person_id`` of
  public persons with public role ``suspect`` OR ``witness``, EXCLUDING the
  victim and any non-person/invalid/hidden-only records.
- Truth-independent, stable, deterministic, broader than ``suspect_universe``
  (witness inclusion).
- The victim is represented separately via the suicide option —
  ``has_public_victim``.
- ``candidate_block_of`` gains a protected ``finalTargets`` block
  (``{id, name, role}``) and a ``hasSuicideOption`` marker.
- Accusation membership uses the FINAL TARGET universe for HOMICIDE (not the
  suspect universe): witnesses are accuseable; victims and unknown persons
  are not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.eligibility import (  # noqa: E402
    final_homicide_target_ids,
    has_public_victim,
    suspect_universe,
)
from app.domain.public import PublicCase, PublicPerson  # noqa: E402
from app.generation.schemas import PersonSpec  # noqa: E402

# --------------------------------------------------------------------------- #
# domain derivation (§8 Option B)
# --------------------------------------------------------------------------- #


def test_final_homicide_target_ids_include_witnesses_and_suspects():
    from fixtures.golden import golden_public

    targets = final_homicide_target_ids(golden_public())
    assert targets == (
        "anna_karlsson",
        "emily_reed",  # the witness — a final homicide target
        "michael_carter",
        "thomas_reed",
    )


def test_final_targets_are_broader_than_suspect_universe():
    from fixtures.golden import golden_public

    public = golden_public()
    assert set(final_homicide_target_ids(public)) > set(suspect_universe(public))
    assert "emily_reed" in final_homicide_target_ids(public)
    assert "emily_reed" not in suspect_universe(public)


def test_final_targets_exclude_victim_and_non_suspect_witness_roles():
    from fixtures.golden import golden_public

    targets = set(final_homicide_target_ids(golden_public()))
    assert "sarah_miller" not in targets  # victim excluded
    assert "david_kim" not in targets  # family excluded


def test_final_targets_are_sorted_deduped_deterministic():
    from fixtures.golden import golden_public

    public = golden_public()
    first = final_homicide_target_ids(public)
    second = final_homicide_target_ids(public)
    assert first == second == tuple(sorted(first))
    assert len(set(first)) == len(first)


def test_final_targets_are_truth_independent():
    from fixtures.golden import golden_public, truth_variant_a, truth_variant_b

    public = golden_public()
    targets = final_homicide_target_ids(public)
    # Two different hidden truths, identical public -> identical final targets.
    assert truth_variant_a() != truth_variant_b()
    assert final_homicide_target_ids(public) == targets


def test_final_targets_ignore_hidden_only_and_invalid_records():
    persons = (
        PublicPerson(
            person_id="vict_1",
            name="V",
            role="victim",
            public_affordances=frozenset({"VISIBLE_CHARACTER"}),
        ),
        PublicPerson(
            person_id="sus_1",
            name="S",
            role="suspect",
            public_affordances=frozenset({"SUSPECT_ELIGIBLE"}),
        ),
        PublicPerson(
            person_id="wit_1",
            name="W",
            role="witness",
            public_affordances=frozenset({"VISIBLE_CHARACTER"}),
        ),
        PublicPerson(
            person_id="fam_1",
            name="F",
            role="family",
            public_affordances=frozenset({"VISIBLE_CHARACTER"}),
        ),
    )
    public = PublicCase(case_id="C", case_version=1, persons=persons)
    assert final_homicide_target_ids(public) == ("sus_1", "wit_1")


def test_has_public_victim_flag():
    from fixtures.golden import golden_public

    assert has_public_victim(golden_public()) is True
    no_victim = PublicCase(
        case_id="C",
        case_version=1,
        persons=(
            PublicPerson(
                person_id="sus_1",
                name="S",
                role="suspect",
                public_affordances=frozenset({"SUSPECT_ELIGIBLE"}),
            ),
        ),
    )
    assert has_public_victim(no_victim) is False


# --------------------------------------------------------------------------- #
# candidate block — protected finalTargets / hasSuicideOption (§5/§20/§23)
# --------------------------------------------------------------------------- #


def _payload(
    persons: list[dict],
    *,
    suspect_ids: list[str],
    motive_ids: list[str] | None = None,
    weapon_ids: list[str] | None = None,
) -> dict:
    return {
        "draft": {
            "persons": persons,
            "motives": [],
            "objects": [],
        },
        "universes": {
            "suspect_ids": suspect_ids,
            "motive_ids": motive_ids if motive_ids is not None else [],
            "weapon_ids": weapon_ids if weapon_ids is not None else [],
        },
    }


def test_candidate_block_of_exposes_final_targets_and_suicide_option():
    from app.services.reveal import candidate_block_of

    payload = _payload(
        [
            {"person_id": "sarah_miller", "name": "Sarah Miller", "role": "victim"},
            {"person_id": "thomas_reed", "name": "Thomas Reed", "role": "suspect"},
            {"person_id": "emily_reed", "name": "Emily Reed", "role": "witness"},
            {"person_id": "david_kim", "name": "David Kim", "role": "family"},
        ],
        suspect_ids=["thomas_reed"],
    )
    candidates = candidate_block_of(payload)
    assert set(candidates.keys()) == {
        "suspects",
        "motives",
        "weapons",
        "finalTargets",
        "hasSuicideOption",
    }
    final_targets = candidates["finalTargets"]
    assert [t["id"] for t in final_targets] == ["emily_reed", "thomas_reed"]
    assert all(set(t.keys()) == {"id", "name", "role"} for t in final_targets)
    assert all(t["role"] in {"suspect", "witness"} for t in final_targets)
    assert candidates["hasSuicideOption"] is True


def test_candidate_block_no_victim_means_no_suicide_option():
    from app.services.reveal import candidate_block_of

    payload = _payload(
        [
            {"person_id": "thomas_reed", "name": "Thomas Reed", "role": "suspect"},
            {"person_id": "emily_reed", "name": "Emily Reed", "role": "witness"},
        ],
        suspect_ids=["thomas_reed"],
    )
    candidates = candidate_block_of(payload)
    assert candidates["hasSuicideOption"] is False
    assert [t["id"] for t in candidates["finalTargets"]] == ["emily_reed", "thomas_reed"]


def test_candidate_block_final_targets_never_mark_the_winner():
    from app.services.reveal import candidate_block_of

    payload = _payload(
        [
            {"person_id": "sarah_miller", "name": "Sarah Miller", "role": "victim"},
            {"person_id": "thomas_reed", "name": "Thomas Reed", "role": "suspect"},
            {"person_id": "emily_reed", "name": "Emily Reed", "role": "witness"},
        ],
        suspect_ids=["thomas_reed"],
    )
    for entry in candidate_block_of(payload)["finalTargets"]:
        assert set(entry.keys()) == {"id", "name", "role"}


# --------------------------------------------------------------------------- #
# accusation membership — HOMICIDE uses the FINAL TARGET universe (§5/§31)
# --------------------------------------------------------------------------- #


def _accusation_payload() -> dict:
    return _payload(
        [
            {"person_id": "sarah_miller", "name": "Sarah Miller", "role": "victim"},
            {"person_id": "thomas_reed", "name": "Thomas Reed", "role": "suspect"},
            {"person_id": "emily_reed", "name": "Emily Reed", "role": "witness"},
            {"person_id": "david_kim", "name": "David Kim", "role": "family"},
        ],
        suspect_ids=["thomas_reed"],
        motive_ids=["cover_up_embezzlement"],
        weapon_ids=["kitchen_knife"],
    )


def _request(murderer_id, resolution_type="HOMICIDE"):
    from app.schemas.accusation import AccusationRequest

    return AccusationRequest(
        murdererId=murderer_id,
        motiveId="cover_up_embezzlement",
        weaponId="kitchen_knife",
        crimeTime="2026-09-11T22:17:00+02:00",
        resolutionType=resolution_type,
    )


def test_homicide_accusation_accepts_a_witness_final_target():
    """Witness murderers are accuseable even though they are NOT in the
    SUSPECT_ELIGIBLE universe (the whole Phase-41 point)."""
    from app.services.accusation import validate_universe_membership

    payload = _accusation_payload()
    # emily_reed is a witness (not a suspect candidate) but a final target.
    assert "emily_reed" not in set(payload["universes"]["suspect_ids"])
    validate_universe_membership(payload, _request("emily_reed"))  # must not raise


def test_homicide_accusation_accepts_a_suspect_final_target():
    from app.services.accusation import validate_universe_membership

    validate_universe_membership(_accusation_payload(), _request("thomas_reed"))


def test_homicide_accusation_rejects_the_victim():
    from app.services.accusation import AccusationValidationError, validate_universe_membership

    with pytest.raises(AccusationValidationError):
        validate_universe_membership(_accusation_payload(), _request("sarah_miller"))


def test_homicide_accusation_rejects_unknown_person():
    from app.services.accusation import AccusationValidationError, validate_universe_membership

    with pytest.raises(AccusationValidationError):
        validate_universe_membership(_accusation_payload(), _request("ghost_person"))


def test_homicide_accusation_rejects_family_only_person():
    from app.services.accusation import AccusationValidationError, validate_universe_membership

    with pytest.raises(AccusationValidationError):
        validate_universe_membership(_accusation_payload(), _request("david_kim"))


def test_suicide_accusation_requires_null_murderer():
    from app.schemas.accusation import AccusationRequest

    # SUICIDE + non-null murdererId is a closed contract violation (422).
    with pytest.raises(ValueError):
        AccusationRequest(
            murdererId="thomas_reed",
            motiveId="cover_up_embezzlement",
            weaponId="kitchen_knife",
            crimeTime="2026-09-11T22:17:00+02:00",
            resolutionType="SUICIDE",
        )


def test_suicide_accusation_accepts_null_murderer():
    from app.services.accusation import validate_universe_membership

    body = _request(None, resolution_type="SUICIDE")
    validate_universe_membership(_accusation_payload(), body)  # motive/weapon checked


def test_unsupported_resolution_type_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _request_wrapper()
    # explicit


def _request_wrapper():
    from app.schemas.accusation import AccusationRequest

    return AccusationRequest(
        murdererId=None,
        motiveId="m",
        weaponId="w",
        crimeTime="22:17:00",
        resolutionType="ACCIDENT",  # type: ignore[arg-type]
    )


def test_evaluate_accusation_wrong_theories():
    """§30 wrong-theory matrix through the truth-aware evaluation."""
    from app.services.accusation import evaluate_accusation

    homicide_truth = {
        "murderer_id": "emily_reed",
        "motive_id": "cover_up_embezzlement",
        "weapon_id": "kitchen_knife",
        "crime_time": {"canonical": "2026-09-11T22:17:00+02:00", "accusation_tolerance_seconds": 300},
    }
    suicide_truth = {
        "murderer_id": None,
        "motive_id": "cover_up_embezzlement",
        "weapon_id": "kitchen_knife",
        "crime_time": {"canonical": "2026-09-11T22:17:00+02:00", "accusation_tolerance_seconds": 300},
    }
    base = {
        "motive_id": "cover_up_embezzlement",
        "weapon_id": "kitchen_knife",
        "crime_time": "2026-09-11T22:17:00+02:00",
    }

    # truth HOMICIDE + correct witness -> correct
    correct = evaluate_accusation(
        homicide_truth, {**base, "murderer_id": "emily_reed", "resolution_type": "HOMICIDE"}
    )
    assert correct["murdererCorrect"] is True

    # truth HOMICIDE + wrong person -> incorrect
    wrong = evaluate_accusation(
        homicide_truth, {**base, "murderer_id": "thomas_reed", "resolution_type": "HOMICIDE"}
    )
    assert wrong["murdererCorrect"] is False

    # truth HOMICIDE + suicide theory -> incorrect
    suicide_theory = evaluate_accusation(
        homicide_truth, {**base, "murderer_id": None, "resolution_type": "SUICIDE"}
    )
    assert suicide_theory["murdererCorrect"] is False

    # truth SUICIDE + suicide theory -> correct
    suicide_ok = evaluate_accusation(
        suicide_truth, {**base, "murderer_id": None, "resolution_type": "SUICIDE"}
    )
    assert suicide_ok["murdererCorrect"] is True

    # truth SUICIDE + homicide + any person -> incorrect
    homicide_any = evaluate_accusation(
        suicide_truth, {**base, "murderer_id": "thomas_reed", "resolution_type": "HOMICIDE"}
    )
    assert homicide_any["murdererCorrect"] is False


# --------------------------------------------------------------------------- #
# FIX-5 (ADV-41-07) — MURDERER_NOT_SUSPECT_CANDIDATE NARROWING (pinned)
# --------------------------------------------------------------------------- #
#
# Phase41 retired the universal "murderer must be SUSPECT_ELIGIBLE" rule and
# replaced it with the FINAL TARGET invariant, but the narrowed
# ``MURDERER_NOT_SUSPECT_CANDIDATE`` is RETAINED for the classical investigative
# role: a public ``role=suspect`` murderer missing from ``SUSPECT_ELIGIBLE``
# still fires (frozen REQUIREMENTS §17/§31.1 tie the deduction universe to
# SUSPECT_ELIGIBLE). A witness murderer (a final target, never a deduction
# candidate) does NOT fire it — ``HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET`` is
# the primary final-target invariant.


def _case_quality_for_murderer(murderer_id: str):
    """quality codes for a golden public/evidence with the given hidden
    murderer (deterministic, zero provider)."""
    from dataclasses import replace

    from app.domain.eligibility import derive_universes
    from app.generation.case_quality import validate_case_quality

    from fixtures.golden import golden_evidence, golden_public, truth_variant_a

    public = golden_public()
    evidence = golden_evidence()
    truth = replace(truth_variant_a(), crime=replace(truth_variant_a().crime, murderer_id=murderer_id))
    return validate_case_quality(public, truth, evidence, derive_universes(public))


def test_fix5_suspect_murderer_out_of_eligibility_fires_narrowed_code():
    """A classical ``role=suspect`` murderer missing from ``SUSPECT_ELIGIBLE``
    still fires ``MURDERER_NOT_SUSPECT_CANDIDATE`` (the narrowed retention)."""
    from app.generation.case_quality import MURDERER_NOT_SUSPECT_CANDIDATE

    codes = _case_quality_for_murderer("thomas_reed")
    # thomas IS SUSPECT_ELIGIBLE in the golden — baseline: no narrowed code.
    assert MURDERER_NOT_SUSPECT_CANDIDATE not in codes
    # Strip SUSPECT_ELIGIBLE from the suspect murderer -> the narrowed code.
    from dataclasses import replace

    from app.domain.eligibility import derive_universes
    from app.generation.case_quality import validate_case_quality
    from app.domain.public import PublicPerson

    from fixtures.golden import golden_evidence, golden_public, truth_variant_a

    public = golden_public()
    stripped = []
    for person in public.persons:
        if person.person_id == "thomas_reed":
            stripped.append(
                PublicPerson(
                    person_id=person.person_id,
                    name=person.name,
                    role=person.role,
                    public_affordances=frozenset(
                        person.public_affordances - {"SUSPECT_ELIGIBLE"}
                    ),
                )
            )
        else:
            stripped.append(person)
    from app.domain.public import PublicCase

    public = PublicCase(
        case_id=public.case_id,
        case_version=public.case_version,
        persons=stripped,
        motives=public.motives,
        objects=public.objects,
        locations=public.locations,
        travel_rules=public.travel_rules,
        scene=public.scene,
    )
    truth = replace(truth_variant_a(), crime=replace(truth_variant_a().crime, murderer_id="thomas_reed"))
    codes = validate_case_quality(public, truth, golden_evidence(), derive_universes(public))
    assert MURDERER_NOT_SUSPECT_CANDIDATE in codes


def test_fix5_witness_murderer_does_not_fire_narrowed_code():
    """A WITNESS murderer (role=witness, never ``SUSPECT_ELIGIBLE``, but a
    final homicide target) does NOT fire ``MURDERER_NOT_SUSPECT_CANDIDATE`` —
    the narrowed code covers only the classical investigative-suspect role."""
    from app.generation.case_quality import MURDERER_NOT_SUSPECT_CANDIDATE

    codes = _case_quality_for_murderer("emily_reed")
    assert MURDERER_NOT_SUSPECT_CANDIDATE not in codes


def test_fix5_homicide_perpetrator_not_final_target_is_primary():
    """``HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET`` remains the PRIMARY final-
    target invariant: a family/other-role murderer (not a final target) fires
    it — and never the narrowed ``MURDERER_NOT_SUSPECT_CANDIDATE``."""
    from app.generation.case_quality import (
        HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET,
        MURDERER_NOT_SUSPECT_CANDIDATE,
    )

    codes = _case_quality_for_murderer("david_kim")  # role=family
    assert HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET in codes
    assert MURDERER_NOT_SUSPECT_CANDIDATE not in codes