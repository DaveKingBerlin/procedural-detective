"""Phase41A — procedural witness-murderer & deceptive-witness generation.

RED scope (§49-§67):

- ``OllamaStageDriver`` generation CAN produce a HOMICIDE whose hidden murderer
  is a public witness (role=witness, no SUSPECT_ELIGIBLE, final target), and
  the draft validates + publishes (this is NOT a hand-built case).
- The WHO solver iterates the FINAL HOMICIDE TARGET universe
  (``CandidateUniverses.homicide_target_ids``) so the witness murderer is the
  determined winner; ``evaluate_solution`` certifies all_true.
- Accusation accepts the witness final target (no role rewrite); interview
  stays fully available and deterministic; replay is provider-free.
- Guilty and innocent deceptive witnesses are BOTH reachable procedurally
  (seed-branched, deterministic), every material lie has a discoverable
  contradiction path, and no truth/deception marker leaks pre-reveal.
- Classic suspect homicide + suicide stay fully green (regressions).
- No brute-force retries: bounded, single-pass generation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_ollama_driver import (  # noqa: E402
    ICEPICK_SPEC,
    MockOllamaTransport,
    PROMPT,
    _admission,
    _alog,
    _case_people,
    _controller,
    _evidence,
    _j,
    _make_driver,
    _run,
    _world,
)
from phase5_helpers import auth, create_session  # noqa: E402
from phase6_helpers import client, playthrough  # noqa: E402
from test_phase7_helpers import (  # noqa: E402
    create_published_case_and_playthrough,
    make_accusation,
)
from phase41_helpers import (  # noqa: E402
    homicide_body,
    insert_variant,
    read_payload,
)

from app.domain.resolution import ResolutionType  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402

# flake8: noqa

_CRIME_CANONICAL = "2026-09-11T23:42:00+02:00"
_CRIME_MURDERER_SUSPECT = "paul_becker"
_WITNESS = "lisa_koenig"

# The locked prompt for a HOMICIDE whose murderer is the public WITNESS
# (``Lisa König`` = the person name of ``lisa_koenig``).
_WM_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Lisa König\nMotive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa König\nLocation: office\n"
)

# The SUICIDE prompt (no murderer lock — a suicide truth has no murderer).
_SUICIDE_PROMPT = (
    "Victim: Dr. Anna Weiss\nMotive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa König\nLocation: office\n"
)


# --------------------------------------------------------------------------- #
# deterministic case variants + activity-log posts for the driver
# --------------------------------------------------------------------------- #


def _witness_murderer_people():
    """The hidden murderer is the public WITNESS ``lisa_koenig`` (HOMICIDE)."""
    cp = _case_people()
    cp["crime"]["murdererId"] = _WITNESS
    cp["crime"]["resolutionType"] = "HOMICIDE"
    return cp


def _two_witness_people():
    """A SUSPECT murderer (paul_becker) + TWO public witnesses; the seed-1
    innocent-deception branch selects ``lisa_koenig`` as the innocent liar."""
    cp = _case_people()
    cp["crime"]["murdererId"] = _CRIME_MURDERER_SUSPECT
    cp["crime"]["resolutionType"] = "HOMICIDE"
    cp["persons"] = [
        *cp["persons"],
        {
            "personId": "erika_vogt",
            "name": "Erika Vogt",
            "role": "witness",
            "affordances": ["VISIBLE_CHARACTER"],
        },
    ]
    return cp


def _suicide_people():
    cp = _case_people()
    cp["crime"]["murdererId"] = None
    cp["crime"]["resolutionType"] = "SUICIDE"
    return cp


def _log_posts(*anchors):
    from app.domain.time_interval import epoch_to_iso, parse_iso8601

    tick, offset = parse_iso8601(_CRIME_CANONICAL)

    def iso(delta_seconds):
        return epoch_to_iso(tick + delta_seconds, offset)

    return [_j(_alog(iso(a))) for a in anchors]


def _wm_posts(deceptive: bool):
    # witness murderer (seed 11 -> SELF_PROTECTION liar; seed 7 -> truthful):
    # other targets = marcus/paul/sophie (3 opp) + when_obs + presence = 5
    # time-bearing cctv facts. FIX-1 (ADV-41A-02): the deception contradiction
    # IS the ordinary scene-presence record (``d_ev_presence``, already the
    # murderer's presence log), so the deceptive case carries EXACTLY the same
    # activity-log layout as the truthful one (no special/marked record).
    del deceptive  # the neutral evidence algebra is identical either way
    anchors = (-10, -120, -120, -120, -20)
    return [
        _j(_witness_murderer_people()),
        _j(_evidence()),
        *_log_posts(*anchors),
        _j(_world()),
        ICEPICK_SPEC,
    ]


def _innocent_liar_posts():
    # paul murderer + two witnesses; seed-1 liar = lisa -> her ORDINARY
    # distant opportunity record (neutral ``d_ev_opp_lisakoenig``, kind cctv,
    # canonical t-120) is the discoverable contradiction: when, opp_marcus,
    # opp_sophie, opp_erika, opp_lisa, presence (6 calls).
    return [
        _j(_two_witness_people()),
        _j(_evidence()),
        # evidence order: when(-10), opp_erika/opp_lisa/opp_marcus/opp_sophie
        # (four -120), presence(-20).
        *_log_posts(-10, -120, -120, -120, -120, -20),
        _j(_world()),
        ICEPICK_SPEC,
    ]


def _suicide_posts():
    # suicide: other targets = paul/marcus/sophie/lisa (4 opp), no presence.
    return [
        _j(_suicide_people()),
        _j(_evidence()),
        *_log_posts(-10, -120, -120, -120, -120),
        _j(_world()),
        ICEPICK_SPEC,
    ]


def _run_wm(deceptive: bool, seed: int, **kwargs):
    """Run the full driver generation for the witness-murderer case with the
    witness-murderer locked prompt (``Murderer: Lisa König``)."""
    return _run(_wm_posts(deceptive), prompt=_WM_PROMPT, seed=seed, **kwargs)


def _driver_payload(record) -> dict:
    """The deterministic serialized published payload of a driver record."""
    from app.services.publication import serialize_published_payload

    return json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="p41a", model="mock", title="P41A"
        )
    )


def _assembled(record):
    from app.generation import pipeline

    return pipeline.assemble(record)


# --------------------------------------------------------------------------- #
# §49 — GENERATED witness murderer is valid and publishable (real driver path)
# --------------------------------------------------------------------------- #


def test_generated_witness_murderer_is_valid_and_publishable():
    record, transport = _run_wm(True, 11, max_llm_calls_per_generation=12)
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 0
    # FIX-1 (ADV-41A-02): the deception contradiction is an ordinary observation
    # (no marked ``view_record`` record), so a deceptive witness murderer has
    # the SAME bounded single-pass layout as a truthful one — 9 calls.
    assert transport.call_count == 9, "single bounded pass, no extra calls"

    public, evidence, truth, draft = _assembled(record)
    # the hidden murderer is the public WITNESS (not a crafted fixture case).
    assert truth.crime.resolution_type is ResolutionType.HOMICIDE
    assert truth.crime.murderer_id == _WITNESS
    lisa = next(p for p in public.persons if p.person_id == _WITNESS)
    assert lisa.role == "witness"
    assert "SUSPECT_ELIGIBLE" not in lisa.public_affordances
    assert _WITNESS in set(derive_universes__(public))

    # quality gate fully clean; solver certifies the witness murderer.
    from app.domain.eligibility import derive_universes
    from app.domain.solver import solve_case
    from app.generation.case_quality import validate_case_quality
    from app.validation.solution import evaluate_solution

    proof = solve_case(public, evidence)
    assert proof.who.unique is True
    assert proof.who.winner == _WITNESS
    val = evaluate_solution(proof, truth)
    assert val.all_true is True
    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert codes == (), codes


def derive_universes__(public):
    from app.domain.eligibility import final_homicide_target_ids

    return tuple(final_homicide_target_ids(public))


# --------------------------------------------------------------------------- #
# §50 — witness-murderer accusation (API, from the generated payload)
# --------------------------------------------------------------------------- #


def _publish_generated_as_v2(phase5_app, payload, version=2):
    bundle = create_published_case_and_playthrough(phase5_app)
    payload["caseVersion"] = version
    insert_variant(phase5_app, bundle["caseId"], version, payload)
    pt_id, pt_token = playthrough(
        phase5_app, bundle["caseId"], bundle["creator"], version=version
    )
    return {
        "app": phase5_app,
        "caseId": bundle["caseId"],
        "creator": bundle["creator"],
        "pt_id": pt_id,
        "pt_token": pt_token,
        "payload": payload,
    }


def test_witness_murderer_accusation_wins_without_role_rewrite(phase5_app):
    record, _transport = _run_wm(True, 11, max_llm_calls_per_generation=12)
    assert record.state is GenerationState.PUBLISHED
    payload = _driver_payload(record)
    case = _publish_generated_as_v2(phase5_app, payload)

    with client(phase5_app) as c:
        res = make_accusation(
            c,
            case["pt_id"],
            case["pt_token"],
            homicide_body(payload, _WITNESS),
        )
        assert res.status_code == 200, res.text
        reveal = c.get(
            f"/api/v1/playthroughs/{case['pt_id']}/reveal",
            headers=auth(case["pt_token"]),
        )
        assert reveal.status_code == 200, reveal.text
    body = reveal.json()
    assert body["truth"]["resolutionType"] == "HOMICIDE"
    assert body["truth"]["murdererId"] == _WITNESS
    assert body["result"]["murdererCorrect"] is True
    # no role rewrite: the witness stays a witness in the public draft.
    roles = {
        p["person_id"]: p["role"] for p in payload["draft"]["persons"]
    }
    assert roles[_WITNESS] == "witness"


# --------------------------------------------------------------------------- #
# §51 — witness-murderer interview: all questions, deterministic, works
# --------------------------------------------------------------------------- #


def _witness_questions(app, case, witness_id):
    with client(app) as c:
        return c.get(
            f"/api/v1/playthroughs/{case['pt_id']}/witnesses/{witness_id}",
            headers=auth(case["pt_token"]),
        )


def _witness_answer(app, case, witness_id, question_type):
    with client(app) as c:
        return c.post(
            f"/api/v1/playthroughs/{case['pt_id']}/witnesses/{witness_id}/interview",
            json={"questionType": question_type},
            headers=auth(case["pt_token"]),
        )


def test_witness_murderer_interview_all_questions_and_final_target(phase5_app):
    record, _transport = _run_wm(True, 11, max_llm_calls_per_generation=12)
    assert record.state is GenerationState.PUBLISHED
    payload = _driver_payload(record)
    case = _publish_generated_as_v2(phase5_app, payload)

    view = _witness_questions(phase5_app, case, _WITNESS)
    assert view.status_code == 200, view.text
    questions = [q["questionType"] for q in view.json()["questions"]]
    assert questions == ["OBSERVATION", "TIME", "PERSON", "OBJECT", "LOCATION", "SOUND"]

    answers = []
    for q in questions:
        res = _witness_answer(phase5_app, case, _WITNESS, q)
        assert res.status_code == 200, (q, res.text)
        answers.append(res.json())
    # deterministic: asking again returns the same answer text + repeated
    # questions stay idempotent (no new discovery on repeat).
    again = _witness_answer(phase5_app, case, _WITNESS, "OBSERVATION")
    assert again.status_code == 200
    assert again.json()["statement"] == answers[0]["statement"]
    assert again.json()["discovery"]["newlyDiscovered"] is False

    # discovery works: at least one grounded question discovers the linked
    # witness-kind evidence (the discovery record is the read-record DTO).
    assert any(a.get("discovery") for a in answers)

    # final target still selectable (accusable): the witness remains in the
    # public final-target universe and the accusation endpoint accepts her.
    from app.domain.eligibility import final_homicide_target_ids_of_records

    persons = payload["draft"]["persons"]
    assert _WITNESS in final_homicide_target_ids_of_records(persons)
    with client(phase5_app) as c:
        res = make_accusation(
            c,
            case["pt_id"],
            case["pt_token"],
            homicide_body(payload, _WITNESS),
        )
        assert res.status_code == 200, res.text


# --------------------------------------------------------------------------- #
# §52 — guilty deceptive witness (SELF_PROTECTION) with discoverable lie
# --------------------------------------------------------------------------- #


def test_generated_guilty_deceptive_witness_has_discoverable_contradiction():
    # seed=11 -> witness-murderer deception branch: SELF_PROTECTION lie.
    record, _transport = _run_wm(True, 11, max_llm_calls_per_generation=12)
    assert record.state is GenerationState.PUBLISHED
    public, evidence, truth, draft = _assembled(record)

    deceptive = [t for t in truth.witness_truthfulness if t.truthfulness == "DECEPTIVE"]
    assert len(deceptive) == 1
    assert deceptive[0].witness_id == _WITNESS
    assert deceptive[0].deception_reason == "SELF_PROTECTION"

    # the lie is discoverably contradicted (an ordinary observation + camera
    # placement — no marked contradiction record).
    from app.generation.case_quality import (
        DECEPTIVE_CLAIM_UNCONTRADICTED,
        validate_case_quality,
    )
    from app.generation.evidence_facts import mechanical_contradiction_fact_ids

    contradiction_ids = mechanical_contradiction_fact_ids(evidence, _WITNESS)
    assert contradiction_ids, "a mechanical contradiction path must exist"
    placement_ids = {
        str(getattr(p, "evidence_id", ""))
        for p in draft.world_graph.placements
        if getattr(p, "evidence_id", None)
    }
    assert any(cid in placement_ids for cid in contradiction_ids), (
        "contradiction record must be world-reachable"
    )
    from app.domain.eligibility import derive_universes

    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert DECEPTIVE_CLAIM_UNCONTRADICTED not in codes
    assert codes == (), codes


# --------------------------------------------------------------------------- #
# §53 — innocent deceptive witness (PROTECT_OTHER / HIDE_UNRELATED_SECRET)
# --------------------------------------------------------------------------- #


def test_generated_innocent_deceptive_witness_keeps_true_murderer_solvable():
    # seed=1 -> innocent-deception branch; liar == lisa_koenig (≠ murderer paul).
    record, _transport = _run(
        _innocent_liar_posts(), seed=1, max_llm_calls_per_generation=12
    )
    assert record.state is GenerationState.PUBLISHED
    public, evidence, truth, draft = _assembled(record)

    deceptive = [t for t in truth.witness_truthfulness if t.truthfulness == "DECEPTIVE"]
    assert len(deceptive) == 1
    liar = deceptive[0].witness_id
    assert liar == _WITNESS
    assert liar != truth.crime.murderer_id
    assert deceptive[0].deception_reason in ("PROTECT_OTHER", "HIDE_UNRELATED_SECRET")

    from app.domain.eligibility import derive_universes
    from app.domain.solver import solve_case
    from app.generation.case_quality import (
        DECEPTIVE_CLAIM_UNCONTRADICTED,
        validate_case_quality,
    )
    from app.generation.evidence_facts import mechanical_contradiction_fact_ids

    # the true murderer (a suspect) stays uniquely solvable.
    proof = solve_case(public, evidence)
    assert proof.who.unique is True
    assert proof.who.winner == _CRIME_MURDERER_SUSPECT
    # the liar is NOT a viable WHO candidate (excluded by the distant record).
    assert liar not in proof.who.viable
    # the lie is discoverably contradicted + placement-reachable.
    contradiction_ids = mechanical_contradiction_fact_ids(evidence, liar)
    assert contradiction_ids
    placement_ids = {
        str(getattr(p, "evidence_id", ""))
        for p in draft.world_graph.placements
        if getattr(p, "evidence_id", None)
    }
    assert any(cid in placement_ids for cid in contradiction_ids)
    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert DECEPTIVE_CLAIM_UNCONTRADICTED not in codes
    assert codes == (), codes


# --------------------------------------------------------------------------- #
# §54 — truthful witness murderer (no deception)
# --------------------------------------------------------------------------- #


def test_generated_truthful_witness_murderer_still_valid_and_solvable():
    # seed=7 -> witness-murderer branch is TRUTHFUL (no SELF_PROTECTION stamp).
    record, _transport = _run_wm(False, 7, max_llm_calls_per_generation=12)
    assert record.state is GenerationState.PUBLISHED
    public, evidence, truth, draft = _assembled(record)

    assert not any(
        t.truthfulness == "DECEPTIVE" and t.witness_id == _WITNESS
        for t in truth.witness_truthfulness
    )

    from app.domain.eligibility import derive_universes
    from app.domain.solver import solve_case
    from app.generation.case_quality import validate_case_quality

    proof = solve_case(public, evidence)
    assert proof.who.unique is True
    assert proof.who.winner == _WITNESS
    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert codes == (), codes


# --------------------------------------------------------------------------- #
# §55 — meta-game guard: lie != guilt (two accepted cases)
# --------------------------------------------------------------------------- #


def test_deceptive_guilt_and_deceptive_innocence_are_both_accepted():
    # Case A: deceptive witness murderer (seed=11).
    record_a, _ = _run_wm(True, 11, max_llm_calls_per_generation=12)
    assert record_a.state is GenerationState.PUBLISHED
    public_a, evidence_a, truth_a, draft_a = _assembled(record_a)
    # Case B: deceptive innocent witness (seed=1).
    record_b, _ = _run(_innocent_liar_posts(), seed=1, max_llm_calls_per_generation=12)
    assert record_b.state is GenerationState.PUBLISHED
    public_b, evidence_b, truth_b, draft_b = _assembled(record_b)

    from app.domain.eligibility import derive_universes
    from app.generation.case_quality import validate_case_quality

    for public, evidence, truth, draft in (
        (public_a, evidence_a, truth_a, draft_a),
        (public_b, evidence_b, truth_b, draft_b),
    ):
        codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
        assert codes == (), codes
    # A deceptive witness is guilty in case A and innocent in case B.
    assert any(t.truthfulness == "DECEPTIVE" and t.witness_id == _WITNESS for t in truth_a.witness_truthfulness)
    assert any(t.truthfulness == "DECEPTIVE" and t.witness_id == _WITNESS for t in truth_b.witness_truthfulness)
    assert truth_a.crime.murderer_id == _WITNESS
    assert truth_b.crime.murderer_id == _CRIME_MURDERER_SUSPECT


# --------------------------------------------------------------------------- #
# §56 — ordinary SUsPECT murderer regression
# --------------------------------------------------------------------------- #


def test_classic_suspect_homicide_generation_regression():
    record, _transport = _run(_run_posts_for_suspect(), seed=11, max_llm_calls_per_generation=12)
    assert record.state is GenerationState.PUBLISHED
    public, evidence, truth, _draft = _assembled(record)
    from app.domain.solver import solve_case

    assert truth.crime.murderer_id == _CRIME_MURDERER_SUSPECT
    proof = solve_case(public, evidence)
    assert proof.who.unique is True
    assert proof.who.winner == _CRIME_MURDERER_SUSPECT


def _run_posts_for_suspect():
    from test_ollama_driver import _driver_posts

    return _driver_posts(
        _j(_case_people()), _j(_evidence()), _j(_world()), _CRIME_CANONICAL
    ) + [ICEPICK_SPEC]


def test_suicide_generation_regression_no_murderer_branch():
    """§57 — SUICIDE still generates+publishes and NEVER runs the murderer
    branch (no presence/alibi/deception)."""
    record, _transport = _run(
        _suicide_posts(),
        prompt=_SUICIDE_PROMPT,
        seed=11,
        max_llm_calls_per_generation=12,
    )
    assert record.state is GenerationState.PUBLISHED
    public, evidence, truth, draft = _assembled(record)
    assert truth.crime.resolution_type is ResolutionType.SUICIDE
    assert truth.crime.murderer_id is None
    # no murderer branch ran: no presence record, no alibi, no deception stamps.
    ids = {str(getattr(f, "id", "")) for f in evidence}
    assert "d_ev_presence" not in ids
    assert "d_ev_alibi" not in ids
    assert not truth.witness_truthfulness
    from app.domain.eligibility import derive_universes
    from app.generation.case_quality import validate_case_quality

    codes = validate_case_quality(public, truth, evidence, derive_universes(public), draft)
    assert codes == (), codes


# --------------------------------------------------------------------------- #
# §58 / §59 — final-target & SUsPECT_ELIGIBLE truth independence
# --------------------------------------------------------------------------- #


def test_final_targets_are_truth_independent():
    """§58 — mutating the hidden murderer while keeping the public people
    unchanged leaves ``final_homicide_target_ids(public)`` identical."""
    from app.domain.eligibility import final_homicide_target_ids

    public, _evidence, truth, _draft = _assembled(
        _run_wm(True, 11, max_llm_calls_per_generation=12)[0]
    )
    before = final_homicide_target_ids(public)
    assert _WITNESS in before
    assert _CRIME_MURDERER_SUSPECT in before
    # The SAME public derives the SAME targets under a different hidden truth
    # (the public is truth-independent by construction — re-derivation is
    # identical no matter what CaseTruth says).
    assert final_homicide_target_ids(public) == before
    assert truth.crime.murderer_id == _WITNESS


def test_changing_murderer_does_not_rewrite_witness_eligibility():
    """§59 — moving the hidden murderer between a suspect and the witness does
    NOT add/remove SUSPECT_ELIGIBLE on either."""
    from app.domain.eligibility import suspect_universe

    public_a, _e, _t, _draft = _assembled(
        _run_wm(True, 11, max_llm_calls_per_generation=12)[0]
    )  # murderer = lisa (witness)
    public_b, _e2, _t2, _d2 = _assembled(
        _run(_run_posts_for_suspect(), seed=11, max_llm_calls_per_generation=12)[0]
    )  # murderer = paul (suspect)
    assert _WITNESS not in suspect_universe(public_a)
    assert _WITNESS not in suspect_universe(public_b)
    lisa_a = next(p for p in public_a.persons if p.person_id == _WITNESS)
    lisa_b = next(p for p in public_b.persons if p.person_id == _WITNESS)
    assert "SUSPECT_ELIGIBLE" not in lisa_a.public_affordances
    assert "SUSPECT_ELIGIBLE" not in lisa_b.public_affordances


# --------------------------------------------------------------------------- #
# §60 — solver candidate universe (the central regression)
# --------------------------------------------------------------------------- #


def test_solver_accepts_witness_winner_in_final_target_universe():
    public, evidence, _truth, _draft = _assembled(
        _run_wm(True, 11, max_llm_calls_per_generation=12)[0]
    )
    from app.domain.eligibility import derive_universes
    from app.domain.solver import solve_case

    universes = derive_universes(public)
    assert _WITNESS in universes.homicide_target_ids
    assert _WITNESS not in universes.suspect_ids
    proof = solve_case(public, evidence)
    assert _WITNESS in proof.who.universe
    assert proof.who.unique is True
    assert proof.who.winner == _WITNESS


# --------------------------------------------------------------------------- #
# §61 — evidence solvability: the winner is derivable from DISCOVERABLE facts
# --------------------------------------------------------------------------- #


def test_witness_murderer_is_deducible_from_discoverable_evidence():
    from app.domain.solver import solve_case
    from app.domain.evidence import discoverable_facts

    public, evidence, _truth, draft = _assembled(
        _run_wm(True, 11, max_llm_calls_per_generation=12)[0]
    )
    proof = solve_case(public, evidence)
    # the winner's support comes from DISCOVERABLE evidence: the presence
    # record (d_ev_presence) is a real discoverable fact referenced by the
    # winner's supported outcomes.
    for outcome in proof.who.rule_outcomes:
        if outcome.target_candidate_id == _WITNESS and outcome.is_support():
            assert outcome.evidence_ids
    discoverable_ids = {f.id for f in discoverable_facts(evidence)}
    used = {
        eid
        for outcome in proof.who.rule_outcomes
        for eid in outcome.evidence_ids
    }
    assert used <= discoverable_ids
    assert "d_ev_presence" in discoverable_ids
    # all other final targets are evidence-backed excluded.
    assert len(proof.who.excluded) == len(proof.who.universe) - 1
    for exclusion in proof.who.excluded:
        assert exclusion.rule_outcomes


# --------------------------------------------------------------------------- #
# §62 — contradiction discoverability (publication-visible path)
# --------------------------------------------------------------------------- #


def test_every_generated_deception_has_a_discoverable_contradiction():
    for posts, seed, prompt in (
        (_wm_posts(True), 11, _WM_PROMPT),
        (_innocent_liar_posts(), 1, PROMPT),
    ):
        record, _ = _run(posts, prompt=prompt, seed=seed, max_llm_calls_per_generation=12)
        assert record.state is GenerationState.PUBLISHED
        public, evidence, truth, draft = _assembled(record)
        placements = {
            str(getattr(p, "evidence_id", ""))
            for p in draft.world_graph.placements
            if getattr(p, "evidence_id", None)
        }
        from app.generation.evidence_facts import mechanical_contradiction_fact_ids

        for entry in truth.witness_truthfulness:
            if entry.truthfulness != "DECEPTIVE":
                continue
            contradiction_ids = mechanical_contradiction_fact_ids(
                evidence, entry.witness_id
            )
            assert contradiction_ids, entry
            assert any(cid in placements for cid in contradiction_ids), entry
            # the contradiction is publication-visible through project_discovery.
            from app.services.publication import project_discovery

            payload = _driver_payload(record)
            for cid in contradiction_ids:
                if cid in placements:
                    discovery = project_discovery(payload, cid)
                    assert discovery is not None, cid
                    break


# --------------------------------------------------------------------------- #
# §63 — no pre-reveal truth / deception leak
# --------------------------------------------------------------------------- #


def test_no_truth_or_deception_leak_in_public_sections():
    public, evidence, truth, draft = _assembled(
        _run_wm(True, 11, max_llm_calls_per_generation=12)[0]
    )
    from app.services.publication import _to_plain

    draft_text = json.dumps(_to_plain(draft), sort_keys=True)
    truth_text = json.dumps(_to_plain(truth), sort_keys=True)
    # The hidden map lives ONLY in the trusted truth tree.
    assert "SELF_PROTECTION" in truth_text
    assert "deception_reason" in truth_text
    for marker in ("SELF_PROTECTION", "PROTECT_OTHER", "HIDE_UNRELATED_SECRET", "truthfulness", "deception_reason"):
        assert marker not in draft_text, marker
    # Public roles carry no murderer marker.
    from app.generation.case_quality import PUBLIC_ROLE_VOCABULARY

    for p in public.persons:
        assert p.role in PUBLIC_ROLE_VOCABULARY
        assert p.role != "murderer"
    # The investigation/public DTO source keeps candidates guilt-agnostic.
    assert _WITNESS in {p.person_id for p in public.persons}


# --------------------------------------------------------------------------- #
# FIX-1 (ADV-41A-02) — no pre-reveal deception meta-leak in published evidence
# --------------------------------------------------------------------------- #


def test_deceptive_witness_public_evidence_is_indistinguishable_from_truthful():
    """FIX-1 (ADV-41A-02): the deceptive witness's contradiction is an ORDINARY
    observation, so the published evidence of a deceptive witness-murderer is
    structurally IDENTICAL to a truthful witness-murderer's (same ids, kinds and
    titles) and nothing in the public DTO marks deception/truthfulness."""
    deceptive = _assembled(_run_wm(True, 11, max_llm_calls_per_generation=12)[0])
    truthful = _assembled(_run_wm(False, 7, max_llm_calls_per_generation=12)[0])
    _dpub, d_ev, d_truth, _ddraft = deceptive
    _tpub, t_ev, t_truth, _tdraft = truthful

    from app.services.publication import _to_plain

    # The deceptive stamp only exists in the hidden truth, never in the evidence.
    assert any(t.truthfulness == "DECEPTIVE" for t in d_truth.witness_truthfulness)
    assert not any(t.truthfulness == "DECEPTIVE" for t in t_truth.witness_truthfulness)

    # Identical public evidence shape: no special/renamed/marked record.
    assert sorted(str(e.id) for e in d_ev) == sorted(str(e.id) for e in t_ev)
    assert {str(e.id): str(e.kind) for e in d_ev} == {
        str(e.id): str(e.kind) for e in t_ev
    }
    assert {str(e.id): str((e.presentation or {}).get("title")) for e in d_ev} == {
        str(e.id): str((e.presentation or {}).get("title")) for e in t_ev
    }
    # No deception marker namespace / wording anywhere in the evidence.
    evidence_text = json.dumps(
        [
            {
                "id": str(e.id),
                "kind": str(e.kind),
                "presentation": _to_plain(dict(e.presentation or {})),
            }
            for e in d_ev
        ],
        sort_keys=True,
    )
    for marker in (
        "d_ev_contradict",
        "view_record",
        "contradicting",
        "SELF_PROTECTION",
        "PROTECT_OTHER",
        "HIDE_UNRELATED_SECRET",
        "truthfulness",
        "deception_reason",
    ):
        assert marker not in evidence_text, marker


def test_deception_marker_never_leaks_into_public_case_dto():
    """FIX-1 (ADV-41A-02): the public-case DTO mapper
    (``public_case_dict_from_payload``) exposes no field/string that could tell
    a deceptive witness from a truthful one."""
    from app.services.publication import (
        _to_plain,
        public_case_dict_from_payload,
    )

    record, _ = _run_wm(True, 11, max_llm_calls_per_generation=12)
    payload = _driver_payload(record)
    text = json.dumps(_to_plain(public_case_dict_from_payload(payload)), sort_keys=True)
    for marker in (
        "d_ev_contradict",
        "view_record",
        "contradicting",
        "SELF_PROTECTION",
        "PROTECT_OTHER",
        "HIDE_UNRELATED_SECRET",
        "truthfulness",
        "deception_reason",
    ):
        assert marker not in text, marker


# --------------------------------------------------------------------------- #
# FIX-2 (ADV-41A-01) — a truthful witness never contradicts its own evidence
# --------------------------------------------------------------------------- #


def test_truthful_witness_statement_matches_its_opportunity_evidence():
    """FIX-2 (ADV-41A-01): a generated case with a TRUTHFUL non-murderer witness
    answers consistently with its published opportunity/observation evidence (no
    unintentional contradiction); the case still validates and a DECEPTIVE
    witness still intentionally contradicts."""
    from app.domain.eligibility import derive_universes
    from app.domain.solver import solve_case
    from app.generation.case_quality import validate_case_quality

    # seed=11 suspect-murderer case: lisa_koenig is a TRUTHFUL non-murderer
    # witness (no innocent-deception branch at this seed).
    record, _ = _run(
        _run_posts_for_suspect(), seed=11, max_llm_calls_per_generation=12
    )
    assert record.state is GenerationState.PUBLISHED
    public, evidence, truth, draft = _assembled(record)
    assert not any(t.truthfulness == "DECEPTIVE" for t in truth.witness_truthfulness)

    statement_fact = next(
        e for e in evidence if str(e.id) == "d_ev_witness_lisakoenig"
    )
    opportunity = next(e for e in evidence if str(e.id) == "d_ev_opp_lisakoenig")
    distant_id = next(
        getattr(p, "location_id", None)
        for p in opportunity.propositions
        if getattr(p, "location_id", None)
    )
    distant_name = next(
        loc.name for loc in public.locations if loc.location_id == distant_id
    )
    statement_text = str(statement_fact.presentation["statement"])
    # The witness truthfully states the SAME location their observation places
    # them at, and never asserts the scene (the contradiction).
    assert distant_name in statement_text
    assert public.scene.name not in statement_text

    proof = solve_case(public, evidence)
    assert proof.who.unique is True
    assert proof.who.winner == _CRIME_MURDERER_SUSPECT
    codes = validate_case_quality(
        public, truth, evidence, derive_universes(public), draft
    )
    assert codes == (), codes

    # A DECEPTIVE witness still intentionally contradicts its (ordinary) record.
    d_record, _ = _run_wm(True, 11, max_llm_calls_per_generation=12)
    d_public, d_evidence, d_truth, d_draft = _assembled(d_record)
    assert any(t.truthfulness == "DECEPTIVE" for t in d_truth.witness_truthfulness)
    d_statement = next(
        e for e in d_evidence if str(e.id) == "d_ev_witness_lisakoenig"
    )
    assert "left the" in str(d_statement.presentation["statement"])
    d_codes = validate_case_quality(
        d_public, d_truth, d_evidence, derive_universes(d_public), d_draft
    )
    assert d_codes == (), d_codes


# --------------------------------------------------------------------------- #
# §64 — ordering stability (final targets never murderer-first)
# --------------------------------------------------------------------------- #


def test_final_target_ordering_is_deterministic_and_murderer_agnostic():
    from app.domain.eligibility import final_homicide_target_ids

    a = final_homicide_target_ids(_assembled(
        _run_wm(True, 11, max_llm_calls_per_generation=12)[0]
    )[0])  # murderer = witness
    b = final_homicide_target_ids(_assembled(
        _run(_run_posts_for_suspect(), seed=11, max_llm_calls_per_generation=12)[0]
    )[0])  # murderer = suspect
    assert a == b
    assert a == tuple(sorted(a))
    # no murderer-first / special ordering: the murderer's position is derived
    # ONLY from the deterministic public-id sort and is IDENTICAL regardless of
    # who the hidden murderer is.
    assert a.index(_WITNESS) == b.index(_WITNESS)
    assert a.index(_CRIME_MURDERER_SUSPECT) == b.index(_CRIME_MURDERER_SUSPECT)


# --------------------------------------------------------------------------- #
# §65 — save -> load -> fresh replay with ZERO provider calls
# --------------------------------------------------------------------------- #


def test_witness_murderer_replay_round_trip_zero_provider_calls(phase5_app):
    from app.services import savegame as sg

    record, _transport = _run_wm(True, 11, max_llm_calls_per_generation=12)
    assert record.state is GenerationState.PUBLISHED
    payload = _driver_payload(record)
    assert any(
        t["truthfulness"] == "DECEPTIVE"
        and t["witness_id"] == _WITNESS
        for t in payload["truth"].get("witness_truthfulness", [])
    ), "deception metadata saved (SELF_PROTECTION seed)"

    case = _publish_generated_as_v2(phase5_app, payload)

    def _new_playthrough():
        return playthrough(phase5_app, case["caseId"], case["creator"], version=2)

    # 1. export (reveal-gated -> accuse first).
    pt1_id, pt1_token = _new_playthrough()
    with client(phase5_app) as c:
        res = make_accusation(
            c, pt1_id, pt1_token, homicide_body(payload, _WITNESS)
        )
        assert res.status_code == 200, res.text
        export = c.get(
            f"/api/v1/playthroughs/{pt1_id}/savegame", headers=auth(pt1_token)
        )
        assert export.status_code == 200, export.text
        doc1 = json.loads(export.text)
    assert doc1["formatVersion"] == 1
    replay = doc1["case"]["replayTruth"]
    # Legacy homicide replayTruth carries the murderer id EXACTLY (the
    # additive ``resolutionType`` key is only for SUICIDE saves).
    assert replay["murdererId"] == _WITNESS
    assert replay["murdererName"] == "Lisa König"

    # 2. "import" (serializer round-trip).
    doc2 = json.loads(sg.serialize_savegame_v1(doc1))
    assert doc2["case"]["replayTruth"] == replay

    # 3. fresh replay (second playthrough) — zero provider calls, fresh
    # question progress, same hidden truth/deception.
    pt2_id, pt2_token = _new_playthrough()
    with client(phase5_app) as c:
        res = make_accusation(
            c, pt2_id, pt2_token, homicide_body(payload, _WITNESS)
        )
        assert res.status_code == 200, res.text
        reveal = c.get(f"/api/v1/playthroughs/{pt2_id}/reveal", headers=auth(pt2_token))
        assert reveal.status_code == 200, reveal.text
    assert reveal.json()["truth"]["murdererId"] == _WITNESS

    # 4. re-export -> re-import identical (no new truth).
    assert doc1["case"]["replayTruth"] == doc2["case"]["replayTruth"]


# --------------------------------------------------------------------------- #
# §66 — weighted branch mechanics (deterministic; both branches reachable)
# --------------------------------------------------------------------------- #


def test_deception_branch_mechanics_are_deterministic_and_reachable():
    from app.generation.evidence_facts import deceptive_witness_truths

    # Same fixed seed -> same stamps (pure function).
    crime_a, public_a = _wm_case_objects()
    first = deceptive_witness_truths(crime_a, public_a, seed=11)
    second = deceptive_witness_truths(crime_a, public_a, seed=11)
    assert first == second

    # Both witness-murderer branches reachable across seeds (deceptive 11 vs
    # truthful 7).
    crime11, public11 = _wm_case_objects()
    stamps11 = deceptive_witness_truths(crime11, public11, seed=11)
    crime7, public7 = _wm_case_objects()
    stamps7 = deceptive_witness_truths(crime7, public7, seed=7)
    assert any(t.truthfulness == "DECEPTIVE" and t.witness_id == _WITNESS for t in stamps11)
    assert not any(t.truthfulness == "DECEPTIVE" and t.witness_id == _WITNESS for t in stamps7)

    # The innocent deception branch is reachable too (seed=1) and never picks
    # the murderer (a witness ≠ murderer).
    crime_b, public_b = _two_witness_case_objects()
    stamps_b = deceptive_witness_truths(crime_b, public_b, seed=1)
    assert any(t.truthfulness == "DECEPTIVE" for t in stamps_b)
    for t in stamps_b:
        if t.truthfulness == "DECEPTIVE":
            assert t.witness_id != crime_b.murderer_id


def _wm_case_objects():
    from app.generation.parser import parse_stage
    from app.generation.provider import GenerationStage

    cp = _witness_murderer_people()
    public_payload = {
        k: v
        for k, v in cp.items()
        if k in ("persons", "motives", "locations", "travelRules", "scene")
    }
    public_payload["objects"] = []
    public = parse_stage(
        GenerationStage.PUBLIC_WORLD, _j(public_payload), non_throwing=False
    )
    crime = parse_stage(
        GenerationStage.CASE_TRUTH, _j({"crime": cp["crime"]}), non_throwing=False
    )
    return crime, public


def _two_witness_case_objects():
    from app.generation.parser import parse_stage
    from app.generation.provider import GenerationStage

    cp = _two_witness_people()
    public_payload = {
        k: v
        for k, v in cp.items()
        if k in ("persons", "motives", "locations", "travelRules", "scene")
    }
    public_payload["objects"] = []
    public = parse_stage(
        GenerationStage.PUBLIC_WORLD, _j(public_payload), non_throwing=False
    )
    crime = parse_stage(
        GenerationStage.CASE_TRUTH, _j({"crime": cp["crime"]}), non_throwing=False
    )
    return crime, public


# --------------------------------------------------------------------------- #
# §67 — no brute-force retries / repair-loop amplification
# --------------------------------------------------------------------------- #


def test_witness_murderer_generation_is_bounded_single_pass():
    """The witness murderer is chosen by explicit bounded logic — a single
    bounded pass (9 provider calls for both the deceptive and the truthful
    branch — FIX-1 neutralised the extra contradiction record), zero repairs,
    zero regenerations, no role-roll retries."""
    records = []
    for posts, seed, prompt in ((_wm_posts(True), 11, _WM_PROMPT), (_wm_posts(False), 7, _WM_PROMPT)):
        record, transport = _run(posts, prompt=prompt, seed=seed, max_llm_calls_per_generation=12)
        assert record.state is GenerationState.PUBLISHED
        assert record.budget.repair_passes == 0
        # bounded single pass: 9 provider calls either way — the deceptive
        # contradiction is an ordinary observation, never a marked extra fact.
        expected = 9
        assert transport.call_count == expected, "no repair-loop amplification"
        records.append(record)
    assert records[0].solver_proof.who.winner == _WITNESS
    assert records[1].solver_proof.who.winner == _WITNESS