"""Phase41 — suicide case semantics + API behavior tests (§16-§22, §30, §31).

RED scope:
- SUICIDE truth shape: murderer_id null, victim present, never
  ``murderer_id == victim_id``; legacy homicide payloads stay HOMICIDE.
- The victim-suicide option is a real final choice: the candidate block
  exposes ``finalTargets`` + ``hasSuicideOption`` when a public victim exists.
- Evaluation matrix: SUICIDE theory wins on suicide truth; HOMICIDE + any
  person on suicide truth is incorrect; SUICIDE theory on homicide truth is
  incorrect; the classic suspect path still works on a homicide truth.
- Closed 422 VALIDATION_ERROR envelopes for contract violations.
- Reveal: no murderer label for SUICIDE, resolutionType present, no
  pre-reveal resolution/murderer leak anywhere.
- The deterministic suicide evidence projection produces a valid evidence set
  WITHOUT a murderer (world/activity support, §18-§19).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_helpers import assert_sanitized_error, auth  # noqa: E402
from phase6_helpers import client, playthrough  # noqa: E402
from test_phase7_helpers import (  # noqa: E402
    assert_no_pre_reveal_material,
    create_published_case_and_playthrough,
    make_accusation,
)
from phase41_helpers import (  # noqa: E402
    homicide_body,
    insert_variant,
    read_payload,
    suicide_body,
    to_suicide,
    to_witness_murderer,
)

_CANONICAL = "2026-09-11T22:17:00+02:00"


# --------------------------------------------------------------------------- #
# suicide payload fixture — published demo-apartment mutated into SUICIDE
# --------------------------------------------------------------------------- #


def _suicide_case(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    payload = to_suicide(read_payload(phase5_app, bundle["caseId"], 1))
    insert_variant(phase5_app, bundle["caseId"], 2, payload)
    pt_id, pt_token = playthrough(
        phase5_app, bundle["caseId"], bundle["creator"], version=2
    )
    return {
        "app": phase5_app,
        "caseId": bundle["caseId"],
        "creator": bundle["creator"],
        "pt_id": pt_id,
        "pt_token": pt_token,
        "payload": payload,
    }


def _golden(phase5_app):
    """The unmutated v1 homicide case bundle."""
    return create_published_case_and_playthrough(phase5_app)


# --------------------------------------------------------------------------- #
# candidate block — victim-suicide option (§6/§20/§23)
# --------------------------------------------------------------------------- #


def test_suicide_option_visible_in_investigation_bootstrap(phase5_app):
    case = _suicide_case(phase5_app)
    with client(phase5_app) as c:
        res = c.get(
            f"/api/v1/playthroughs/{case['pt_id']}/investigation",
            headers=auth(case["pt_token"]),
        )
        assert res.status_code == 200, res.text
        candidates = res.json()["candidates"]
        assert candidates["hasSuicideOption"] is True
        final_ids = [t["id"] for t in candidates["finalTargets"]]
        assert "sarah_miller" not in final_ids  # victim is NOT a homicide target
        assert "emily_reed" in final_ids  # witness IS a homicide target
        assert set(candidates["finalTargets"][0].keys()) == {"id", "name", "role"}
    # No-leak sweep: the pre-reveal bootstrap carries no resolution truth.
    assert_no_pre_reveal_material(
        res.json(), known_tokens={case["pt_token"]}, canonical_time=_CANONICAL
    )


# --------------------------------------------------------------------------- #
# winning / losing theories (§21/§30)
# --------------------------------------------------------------------------- #


def test_suicide_theory_wins_on_suicide_truth(phase5_app):
    case = _suicide_case(phase5_app)
    with client(phase5_app) as c:
        res = make_accusation(c, case["pt_id"], case["pt_token"], suicide_body(case["payload"]))
        assert res.status_code == 200, res.text
        reveal = c.get(
            f"/api/v1/playthroughs/{case['pt_id']}/reveal", headers=auth(case["pt_token"])
        )
        assert reveal.status_code == 200, reveal.text
    body = reveal.json()
    assert body["truth"]["resolutionType"] == "SUICIDE"
    assert body["truth"]["murdererId"] is None
    assert body["truth"]["murdererName"] is None
    assert body["result"]["murdererCorrect"] is True
    assert body["result"]["overall"] == "solved"
    assert body["score"]["correctDimensions"] == 4
    # No "murderer" label for a suicide body — the reveal explains the
    # self-inflicted outcome instead.
    assert "murderer" not in {k.casefold() for k in body["truth"].keys()}


def test_homicide_theory_is_incorrect_on_suicide_truth(phase5_app):
    case = _suicide_case(phase5_app)
    body = homicide_body(case["payload"], murderer_id="thomas_reed")
    with client(phase5_app) as c:
        res = make_accusation(c, case["pt_id"], case["pt_token"], body)
        assert res.status_code == 200, res.text
        reveal = c.get(
            f"/api/v1/playthroughs/{case['pt_id']}/reveal", headers=auth(case["pt_token"])
        )
        assert reveal.status_code == 200, reveal.text
        reveal_body = reveal.json()
        assert reveal_body["truth"]["resolutionType"] == "SUICIDE"
        assert reveal_body["result"]["murdererCorrect"] is False
        assert reveal_body["result"]["overall"] == "incorrect"


def test_suicide_theory_is_incorrect_on_homicide_truth(phase5_app):
    bundle = _golden(phase5_app)
    truth = bundle["truth"]
    suicide = {
        "resolutionType": "SUICIDE",
        "murdererId": None,
        "motiveId": truth["motiveId"],
        "weaponId": truth["weaponId"],
        "crimeTime": truth["canonical"],
    }
    with client(phase5_app) as c:
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], suicide)
        assert res.status_code == 200, res.text
        reveal = c.get(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/reveal",
            headers=auth(bundle["playthroughToken"]),
        )
        assert reveal.status_code == 200, reveal.text
        body = reveal.json()
        assert body["truth"]["resolutionType"] == "HOMICIDE"
        assert body["truth"]["murdererId"] == "thomas_reed"
        assert body["result"]["murdererCorrect"] is False
        assert body["result"]["overall"] == "incorrect"


def test_classic_suspect_path_still_wins_on_homicide_truth(phase5_app):
    bundle = _golden(phase5_app)
    truth = bundle["truth"]
    body = {
        "murdererId": truth["murdererId"],
        "motiveId": truth["motiveId"],
        "weaponId": truth["weaponId"],
        "crimeTime": truth["canonical"],
    }
    with client(phase5_app) as c:
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        assert res.status_code == 200, res.text
        reveal = c.get(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/reveal",
            headers=auth(bundle["playthroughToken"]),
        )
        body = reveal.json()
        assert body["result"]["overall"] == "solved"
        assert body["truth"]["resolutionType"] == "HOMICIDE"


def test_witness_murderer_homicide_accusation_wins(phase5_app):
    """§30 witness-murderer: a HOMICIDE case whose killer is a public witness
    is accuseable (final target) — the correct accusation wins and the witness
    remains interviewable while PLAYING."""
    bundle = create_published_case_and_playthrough(phase5_app)
    payload = to_witness_murderer(
        read_payload(phase5_app, bundle["caseId"], 1), witness_id="emily_reed"
    )
    insert_variant(phase5_app, bundle["caseId"], 2, payload)
    pt_id, pt_token = playthrough(phase5_app, bundle["caseId"], bundle["creator"], 2)
    with client(phase5_app) as c:
        # emily_reed is interviewable even though she is the hidden killer
        # (PLAYING-only per Phase 23 §14; no guilt leak in the answer).
        iv = c.post(
            f"/api/v1/playthroughs/{pt_id}/witnesses/emily_reed/interview",
            json={"questionType": "OBSERVATION"},
            headers=auth(pt_token),
        )
        assert iv.status_code == 200, iv.text
        iv_body = iv.json()
        assert "Emily" in str(iv_body["statement"])
        assert_no_pre_reveal_material(iv_body, known_tokens={pt_token})

        res = make_accusation(c, pt_id, pt_token, homicide_body(payload, murderer_id="emily_reed"))
        assert res.status_code == 200, res.text
        # No-leak sweep on the accusation echo (witness killer undetectable).
        assert_no_pre_reveal_material(res.json(), echo=True, known_tokens={pt_token})
        reveal = c.get(
            f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(pt_token)
        )
        assert reveal.status_code == 200, reveal.text
        body = reveal.json()
        assert body["truth"]["murdererId"] == "emily_reed"
        assert body["result"]["murdererCorrect"] is True
        assert body["result"]["overall"] == "solved"


# --------------------------------------------------------------------------- #
# closed API error handling (§31)
# --------------------------------------------------------------------------- #


def test_homicide_plus_null_person_answers_422(phase5_app):
    bundle = _golden(phase5_app)
    truth = bundle["truth"]
    body = {
        "resolutionType": "HOMICIDE",
        "murdererId": None,
        "motiveId": truth["motiveId"],
        "weaponId": truth["weaponId"],
        "crimeTime": truth["canonical"],
    }
    with client(phase5_app) as c:
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert_sanitized_error(res.text)


def test_homicide_plus_victim_answers_422(phase5_app):
    bundle = _golden(phase5_app)
    truth = bundle["truth"]
    body = {
        "resolutionType": "HOMICIDE",
        "murdererId": "sarah_miller",
        "motiveId": truth["motiveId"],
        "weaponId": truth["weaponId"],
        "crimeTime": truth["canonical"],
    }
    with client(phase5_app) as c:
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert_sanitized_error(res.text)


def test_homicide_plus_unknown_person_answers_422(phase5_app):
    bundle = _golden(phase5_app)
    truth = bundle["truth"]
    body = {
        "resolutionType": "HOMICIDE",
        "murdererId": "ghost_who_never_existed",
        "motiveId": truth["motiveId"],
        "weaponId": truth["weaponId"],
        "crimeTime": truth["canonical"],
    }
    with client(phase5_app) as c:
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert_sanitized_error(res.text)


def test_suicide_plus_nonnull_person_answers_422(phase5_app):
    case = _suicide_case(phase5_app)
    body = homicide_body(case["payload"], murderer_id="thomas_reed")
    body["resolutionType"] = "SUICIDE"
    body["murdererId"] = "thomas_reed"
    with client(phase5_app) as c:
        res = make_accusation(c, case["pt_id"], case["pt_token"], body)
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert_sanitized_error(res.text)


def test_unsupported_resolution_type_answers_422(phase5_app):
    bundle = _golden(phase5_app)
    truth = bundle["truth"]
    body = {
        "resolutionType": "ACCIDENT",
        "murdererId": truth["murdererId"],
        "motiveId": truth["motiveId"],
        "weaponId": truth["weaponId"],
        "crimeTime": truth["canonical"],
    }
    with client(phase5_app) as c:
        res = make_accusation(c, bundle["playthroughId"], bundle["playthroughToken"], body)
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert_sanitized_error(res.text)


# --------------------------------------------------------------------------- #
# reveal for suicide (§22) — no murderer label, coherent body
# --------------------------------------------------------------------------- #


def test_suicide_reveal_explains_self_inflicted_and_contradictions(phase5_app):
    case = _suicide_case(phase5_app)
    with client(phase5_app) as c:
        make_accusation(c, case["pt_id"], case["pt_token"], suicide_body(case["payload"]))
        reveal = c.get(
            f"/api/v1/playthroughs/{case['pt_id']}/reveal", headers=auth(case["pt_token"])
        )
        body = reveal.json()
    truth = body["truth"]
    assert truth["resolutionType"] == "SUICIDE"
    assert truth["murdererId"] is None
    assert truth["murdererName"] is None
    assert truth["motiveId"] == case["payload"]["truth"]["crime"]["motive_id"]
    assert truth["weaponId"] == case["payload"]["truth"]["crime"]["weapon_id"]
    # Repeating the reveal is idempotent.
    with client(phase5_app) as c:
        again = c.get(
            f"/api/v1/playthroughs/{case['pt_id']}/reveal", headers=auth(case["pt_token"])
        )
    assert again.status_code == 200
    assert again.json()["truth"] == truth


# --------------------------------------------------------------------------- #
# deterministic suicide evidence projection (§16-§19) — zero murderer
# --------------------------------------------------------------------------- #


def test_evidence_gap_facts_produce_suicide_evidence_without_murderer():
    """``_evidence_gap_facts`` must NOT bail on a SUICIDE truth: it produces a
    full deterministic evidence set (WHEN + red-herring opportunity + weapon +
    witness statements) WITHOUT any murderer-linked facts."""
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

    crime = CrimeSpec(
        type="suicide",
        victim_id="sarah_miller",
        murderer_id=None,
        motive_id="cover_up_embezzlement",
        weapon_id="kitchen_knife",
        location_id="apartment",
        crime_time=CrimeTimeSpec(canonical=_CANONICAL, accusation_tolerance_seconds=300),
        resolution_type=ResolutionType.SUICIDE,
    )
    public = PublicWorldSpec(
        persons=(
            PersonSpec(
                person_id="sarah_miller",
                name="Sarah Miller",
                role="victim",
                affordances=("VISIBLE_CHARACTER",),
            ),
            PersonSpec(
                person_id="thomas_reed",
                name="Thomas Reed",
                role="suspect",
                affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"),
            ),
            PersonSpec(
                person_id="michael_carter",
                name="Michael Carter",
                role="suspect",
                affordances=("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"),
            ),
            PersonSpec(
                person_id="emily_reed",
                name="Emily Reed",
                role="witness",
                affordances=("VISIBLE_CHARACTER",),
            ),
        ),
        motives=(
            MotiveSpec(
                motive_id="cover_up_embezzlement",
                label="Cover up embezzlement",
                affordances=("MOTIVE_CANDIDATE",),
            ),
            MotiveSpec(
                motive_id="revenge_for_affair",
                label="Revenge",
                affordances=("MOTIVE_CANDIDATE",),
            ),
        ),
        objects=(
            ObjectSpec(
                object_id="kitchen_knife",
                asset_id="PROP_KITCHEN_KNIFE_01",
                affordances=("POTENTIAL_WEAPON",),
            ),
            ObjectSpec(
                object_id="letter_opener",
                asset_id="PROP_LETTER_OPENER_01",
                affordances=("POTENTIAL_WEAPON",),
            ),
        ),
        locations=(
            LocationSpec(location_id="apartment", name="the apartment"),
            LocationSpec(location_id="library", name="the library"),
        ),
        scene=SceneSpec(location_id="apartment", name="the apartment"),
    )
    spec, extra_rules, notes = ollama_driver._evidence_gap_facts(
        object(), crime, public, None  # type: ignore[arg-type]
    )
    assert spec is not None
    ids = {f.id for f in spec.evidence}
    # When/weapon/witness/red-herring facts exist …
    assert "d_ev_when_last_seen" in ids
    assert "d_ev_weapon_true" in ids
    # The driver witness id normalizes the person id (identity normalization
    # strips separators), exactly like the homicide driver facts.
    assert "d_ev_witness_emilyreed" in ids
    # … and NO murderer-linked facts (presence/alibi/fingerprint) exist.
    assert "d_ev_presence" not in ids
    assert "d_ev_alibi" not in ids
    assert "d_ev_fp" not in ids
    assert "d_ev_motive_link" not in ids
    # Every red-herring suspect receives an opportunity exclusion (driver id
    # normalization strips separators).
    assert "d_ev_opp_thomasreed" in ids
    assert "d_ev_opp_michaelcarter" in ids
