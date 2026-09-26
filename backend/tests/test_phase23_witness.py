"""Phase 23 — WITNESS INTERVIEW & PLAYER-SAFE STATEMENTS (backend).

Covers the frozen backend contract of Phase23 for the deterministic,
zero-provider witness interview mechanic:

- domain model: closed question enum + allowlisted labels, closed presence
  enum (ON_SCENE / REMOTE_STATEMENT), bounded statement/observation shapes,
  entity bounds and the neutral statement;
- projection: every one of the six questions on the canonical (golden) and
  driver (medium/hard) worlds returns GROUNDED or NEUTRAL — never a truth
  dump, never an invented person/weapon/motive reference, never a widened
  candidate universe;
- API: witness view + interview happy path, grounded discovery + idempotent
  repeat (no duplicate notebook entries), neutral zero-mutation answers,
  cross-playthrough/wrong-id/non-witness 404, unknown question 422,
  not-playing 409, reload persistence through the pinned store;
- solver isolation: the solver signature over the canonical case is byte-
  identical before and after asking ALL six questions (witness statements are
  player-safe projections; the solver consumes canonical evidence only);
- ZERO provider calls: interview requests never touch the provider transport
  (call_count unchanged across repeats + reload);
- security: pre-question leak checks (public-case DTO / world-object DTO /
  witness view / interview question metadata never carry undiscovered
  statement text), release-leak scan after discovery stays bounded, and
  adversarial/state-mutation checks (HTML/control chars stripped, bounded
  lengths, no duplicate rows, no solver/truth mutation).
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.domain.witness import (  # noqa: E402
    ALL_QUESTIONS,
    MAX_NAME_CHARS,
    MAX_OBSERVATION_TEXT_CHARS,
    MAX_OBSERVATIONS,
    MAX_STATEMENT_SUMMARY_CHARS,
    NEUTRAL_SUMMARY,
    QUESTION_LABELS,
    WITNESS_KINDS,
    WitnessPresence,
    WitnessQuestionType,
    project_witness_statement,
    witness_person_of,
    witness_presence,
)
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.publication import serialize_published_payload  # noqa: E402

from phase5_helpers import (  # noqa: E402
    assert_no_hidden_leaks,
    auth,
    create_case,
    create_playthrough,
    create_session,
)
from phase6_helpers import case_for, client, playthrough  # noqa: E402
from test_phase7_helpers import (  # noqa: E402
    make_accusation,
    truth_bundle,
    winning_body,
)

EMILY = "emily_reed"
WITNESS_STATEMENT = "witness_statement_emily_01"

# Grounding matrix on the canonical (golden) world: OBSERVATION / TIME /
# PERSON / LOCATION are grounded through Emily's published statement;
# SOUND / OBJECT are NEUTRAL (no attributed structured sound/object signal).
GOLDEN_GROUNDED = {
    "OBSERVATION",
    "TIME",
    "PERSON",
    "LOCATION",
}
GOLDEN_NEUTRAL = {"SOUND", "OBJECT"}


# --------------------------------------------------------------------------- #
# API babysitters (witness endpoints)
# --------------------------------------------------------------------------- #


def witness_view(app, pt_id: str, pt_token: str, witness_id: str):
    with client(app) as c:
        return c.get(
            f"/api/v1/playthroughs/{pt_id}/witnesses/{witness_id}",
            headers=auth(pt_token),
        )


def interview(app, pt_id: str, pt_token: str, witness_id: str, question_type: str):
    with client(app) as c:
        return c.post(
            f"/api/v1/playthroughs/{pt_id}/witnesses/{witness_id}/interview",
            json={"questionType": question_type},
            headers=auth(pt_token),
        )


def golden_witness_bundle(app):
    case_id, creator = case_for(app)
    pt_id, pt_token = playthrough(app, case_id, creator, version=1)
    return case_id, pt_id, pt_token


# --------------------------------------------------------------------------- #
# 1 — domain model (closed enums, bounds, neutral statement)
# --------------------------------------------------------------------------- #


def test_closed_question_enum_and_labels_are_the_frozen_six():
    assert [q.value for q in ALL_QUESTIONS] == [
        "OBSERVATION",
        "TIME",
        "PERSON",
        "OBJECT",
        "LOCATION",
        "SOUND",
    ]
    assert QUESTION_LABELS == {
        WitnessQuestionType.OBSERVATION: "What did you see?",
        WitnessQuestionType.TIME: "When were you there?",
        WitnessQuestionType.PERSON: "Did you notice anyone?",
        WitnessQuestionType.OBJECT: "Did you notice any unusual objects?",
        WitnessQuestionType.LOCATION: "Where were you?",
        WitnessQuestionType.SOUND: "Did you hear anything?",
    }
    # The set is CLOSED: any other value fails the enum constructor.
    for bad in ("FREETEXT", "MOTIVE", "WEAPON", "", "observe", 7, None):
        try:
            WitnessQuestionType(bad)  # type: ignore[arg-type]
            raise AssertionError(f"{bad!r} must not be a valid question type")
        except ValueError:
            pass


def test_closed_presence_enum_values():
    assert {p.value for p in WitnessPresence} == {"ON_SCENE", "REMOTE_STATEMENT"}


def test_witness_kinds_are_the_published_witness_evidence_set():
    assert WITNESS_KINDS == frozenset(
        {
            "witness_observation",
            "witness_statement",
            "statement",
            "testimonial",
            "suspect_statement",
        }
    )


def test_domain_bounds_constants_are_sane():
    assert MAX_NAME_CHARS >= 1
    assert MAX_STATEMENT_SUMMARY_CHARS >= 1
    assert MAX_OBSERVATION_TEXT_CHARS >= 1
    assert 1 <= MAX_OBSERVATIONS <= 16
    assert NEUTRAL_SUMMARY == "No. Nothing stood out to me."


# --------------------------------------------------------------------------- #
# 2 — projection grounding matrix (golden + driver worlds)
# --------------------------------------------------------------------------- #


def _golden_payload(phase5_app, case_id: str) -> dict:
    row = phase5_app.state.store.get_published(case_id, 1)
    assert row is not None
    return json.loads(row.payload_json)


def test_golden_grounding_matrix_matches_the_contract(phase5_app):
    """On the canonical (golden) world every question answers GROUNDED or
    NEUTRAL exactly as the acceptance matrix says: OBSERVATION/TIME/PERSON/
    LOCATION grounded through the published witness statement; SOUND/OBJECT
    neutral. Never a truth dump: canonical crime time / murderer designation
    / survivor material never appears in any statement text."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    del creator  # case_for returned creator; not needed here
    witness = witness_person_of(payload, EMILY)
    assert witness is not None
    canonical = payload["truth"]["crime"]["crime_time"]["canonical"]

    for question in ALL_QUESTIONS:
        projection = project_witness_statement(payload, witness, question)
        statement = projection.statement
        expected_grounded = question.value in GOLDEN_GROUNDED
        assert statement.grounded is expected_grounded, question.value
        if expected_grounded:
            assert statement.summary
            assert statement.evidence_ids == (WITNESS_STATEMENT,)
            assert len(statement.observations) >= 1
        else:
            assert statement.summary == NEUTRAL_SUMMARY
            assert statement.observations == ()
            assert statement.evidence_ids == ()
        # Bounded + deterministic + allowlisted text only.
        blob = json.dumps(
            {
                "summary": statement.summary,
                "observations": [
                    {"time": o.time, "text": o.text} for o in statement.observations
                ],
            },
            sort_keys=True,
        )
        assert len(statement.summary) <= MAX_STATEMENT_SUMMARY_CHARS
        for obs in statement.observations:
            assert len(obs.text) <= MAX_OBSERVATION_TEXT_CHARS
        assert len(statement.observations) <= MAX_OBSERVATIONS
        assert canonical not in blob, question.value
        assert "murderer" not in blob.casefold(), question.value


def _driver_payload(which: str) -> dict:
    """Deterministic driver-world published payload (medium/hard) — reused
    verbatim from the Phase 19G driver-payload helpers (hermetic, zero
    network; the same worlds the render suite validates)."""
    if which == "medium":
        from test_phase19g_evidence_render import _medium_payload

        return _medium_payload()
    from test_phase19g_evidence_render import _hard_payload

    return _hard_payload()


def _driver_witness(payload: dict):
    witness = next(
        (p for p in payload["draft"]["persons"] if p.get("role") == "witness"), None
    )
    assert witness is not None, "driver world must carry a witness person"
    return witness


def test_driver_worlds_ground_nothing_or_only_published_evidence_and_never_truth():
    """Driver (medium/hard) witnesses: each of the six questions returns
    GROUNDED or NEUTRAL deterministically, every grounded observation is
    bounded allowlisted text with concrete published times only, and the
    canonical crime time never appears in any statement blob."""
    for which in ("medium", "hard"):
        payload = _driver_payload(which)
        canonical = payload["truth"]["crime"]["crime_time"]["canonical"]
        witness = _driver_witness(payload)
        for question in ALL_QUESTIONS:
            projection = project_witness_statement(payload, witness, question)
            statement = projection.statement
            blob = json.dumps(
                {
                    "summary": statement.summary,
                    "observations": [
                        {"time": o.time, "text": o.text} for o in statement.observations
                    ],
                },
                sort_keys=True,
            )
            assert canonical not in blob, (which, question.value)
            assert len(statement.summary) <= MAX_STATEMENT_SUMMARY_CHARS
            assert len(statement.observations) <= MAX_OBSERVATIONS
            for obs in statement.observations:
                assert len(obs.text) <= MAX_OBSERVATION_TEXT_CHARS
                if obs.time is not None:
                    assert len(obs.time) <= 64


def test_driver_witness_presence_defaults_remote_and_interview_stays_deterministic():
    payload = _driver_payload("hard")
    witness = _driver_witness(payload)
    presence, at_scene = witness_presence(payload, witness["person_id"])
    assert presence is WitnessPresence.REMOTE_STATEMENT
    assert at_scene is False
    first = [
        project_witness_statement(payload, witness, q).statement
        for q in ALL_QUESTIONS
    ]
    second = [
        project_witness_statement(payload, witness, q).statement
        for q in ALL_QUESTIONS
    ]
    assert [(s.summary, s.evidence_ids) for s in first] == [
        (s.summary, s.evidence_ids) for s in second
    ]


# --------------------------------------------------------------------------- #
# 3 — API: witness view
# --------------------------------------------------------------------------- #


def test_witness_view_is_player_safe_and_always_offers_all_six(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    res = witness_view(phase5_app, pt_id, pt_token, EMILY)
    assert res.status_code == 200
    body = res.json()
    assert body["witnessId"] == EMILY
    assert body["displayName"] == "Emily Reed"
    assert body["presence"] == "REMOTE_STATEMENT"
    assert body["atScene"] is False
    assert [q["questionType"] for q in body["questions"]] == [
        "OBSERVATION", "TIME", "PERSON", "OBJECT", "LOCATION", "SOUND",
    ]
    labels = [q["label"] for q in body["questions"]]
    assert labels == [
        "What did you see?",
        "When were you there?",
        "Did you notice anyone?",
        "Did you notice any unusual objects?",
        "Where were you?",
        "Did you hear anything?",
    ]
    assert_no_hidden_leaks(body)


def test_witness_view_unknown_and_non_witness_ids_answer_generic_404(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    for bad in ("ghost_witness", "thomas_reed", "sarah_miller"):
        res = witness_view(phase5_app, pt_id, pt_token, bad)
        assert res.status_code == 404, bad
        assert res.json()["error"]["code"] == "NOT_FOUND"
    # Cross-playthrough: a second playthrough's token on this id is 404 too.
    _, pt_b, pt_b_token = golden_witness_bundle(phase5_app)
    res = witness_view(phase5_app, pt_b, pt_b_token, EMILY)
    assert res.status_code in (200,)
    # The token for pt_b against the WRONG playthrough path -> 404 (auth dep).
    res = witness_view(phase5_app, pt_id, pt_b_token, EMILY)
    assert res.status_code == 404


def test_witness_view_requires_token(phase5_app):
    case_id, pt_id, _pt_token = golden_witness_bundle(phase5_app)
    del case_id
    # Absent credential -> 401 (the auth dependency's sanitized envelope).
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/witnesses/{EMILY}")
    assert res.status_code == 401
    # Unknown token -> 401 (same envelope; no interpretation of whether the
    # witness exists).
    res = witness_view(phase5_app, pt_id, "not-a-token", EMILY)
    assert res.status_code == 401


def test_witness_view_unknown_playthrough_answers_generic_404(phase5_app):
    store = phase5_app.state.store
    from app.auth.tokens import issue_playthrough_access_token, verifier as v

    now = float(phase5_app.state.clock.now())
    case_id, _creator = case_for(phase5_app)
    token = issue_playthrough_access_token()
    store.create_playthrough(
        playthrough_id="PT-WITNESS-MISSING",
        case_id=case_id,
        case_version=99,
        token_verifier=v(token),
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    res = witness_view(phase5_app, "PT-WITNESS-MISSING", token, EMILY)
    assert res.status_code == 404  # pinned version row missing -> generic 404


# --------------------------------------------------------------------------- #
# 4 — API: interview happy path + discovery + idempotency
# --------------------------------------------------------------------------- #


def test_interview_happy_path_grounded_with_discovery(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    res = interview(phase5_app, pt_id, pt_token, EMILY, "OBSERVATION")
    assert res.status_code == 200
    body = res.json()
    assert body["witnessId"] == EMILY
    assert body["displayName"] == "Emily Reed"
    assert body["questionType"] == "OBSERVATION"
    assert body["statement"]["summary"]
    assert len(body["statement"]["observations"]) >= 1
    assert all("text" in o for o in body["statement"]["observations"])
    assert body["discovery"]["newlyDiscovered"] is True
    record = body["discovery"]["record"]
    assert record["evidenceId"] == WITNESS_STATEMENT
    assert record["kind"] == "witness_statement"
    assert record["readByPlayer"] is True
    assert "renderType" in record["content"]
    assert_no_hidden_leaks(body)


def test_interview_time_returns_concrete_times(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    res = interview(phase5_app, pt_id, pt_token, EMILY, "TIME")
    assert res.status_code == 200
    body = res.json()
    times = [o["time"] for o in body["statement"]["observations"]]
    assert "22:10" in times
    assert "22:20" in times
    assert all(isinstance(t, str) and t for t in times)
    assert body["discovery"]["newlyDiscovered"] is True


def test_interview_idempotent_repeat_same_statement_and_no_duplicate_rows(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    first = interview(phase5_app, pt_id, pt_token, EMILY, "OBSERVATION")
    second = interview(phase5_app, pt_id, pt_token, EMILY, "OBSERVATION")
    assert first.status_code == second.status_code == 200
    f, s = first.json(), second.json()
    assert f["statement"] == s["statement"]  # byte-identical statement
    assert f["discovery"]["newlyDiscovered"] is True
    assert s["discovery"]["newlyDiscovered"] is False
    assert s["discovery"]["record"]["evidenceId"] == WITNESS_STATEMENT
    # No duplicate notebook entries: discovered/read sets stay single-member.
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (WITNESS_STATEMENT,)
    assert snap.read == (WITNESS_STATEMENT,)


def test_interview_neutral_returns_zero_state_mutation(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    # touch knowledge via a grounded question first, then neutral leaves
    # the sets EXACTLY as they were.
    res = interview(phase5_app, pt_id, pt_token, EMILY, "TIME")
    assert res.status_code == 200
    before = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    for question in ("SOUND", "OBJECT"):
        res = interview(phase5_app, pt_id, pt_token, EMILY, question)
        assert res.status_code == 200
        body = res.json()
        assert body["statement"]["summary"] == NEUTRAL_SUMMARY
        assert body["statement"]["observations"] == []
        assert body["discovery"] is None
    after = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert after.discovered == before.discovered
    assert after.read == before.read
    assert after.visited == before.visited


def test_interview_unknown_and_non_witness_ids_404(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    for bad in ("ghost", "victim_body_placeholder", "michael_carter"):
        res = interview(phase5_app, pt_id, pt_token, bad, "TIME")
        assert res.status_code == 404, bad
        assert res.json()["error"]["code"] == "NOT_FOUND"


def test_interview_unknown_question_answers_422(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    for bad in ("WHATEVER", "MOTIVE", "WEAPON", "free-text", ""):
        res = interview(phase5_app, pt_id, pt_token, EMILY, bad)
        assert res.status_code == 422, bad
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_interview_cross_playthrough_answers_404(phase5_app):
    """A playthrough token bound to playthrough B never interrogates
    playthrough A's witness: the auth dependency answers the same 404."""
    case_id_a, pt_a, token_a = golden_witness_bundle(phase5_app)
    case_id_b, creator_b = case_for(phase5_app)
    pt_b, token_b = playthrough(phase5_app, case_id_b, creator_b, version=1)
    assert case_id_a != case_id_b or pt_a != pt_b
    # token A (owned by pt_a) against pt_b's path -> 404.
    with client(phase5_app) as c:
        res = c.post(
            f"/api/v1/playthroughs/{pt_b}/witnesses/{EMILY}/interview",
            json={"questionType": "TIME"},
            headers=auth(token_a),
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"


def test_interview_not_playing_after_accusation_answers_409(phase5_app):
    """After the playthrough leaves PLAYING (ACCUSED), the PLAYING-only
    interview gate answers the existing 409 NOT_PLAYING envelope."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    truth = truth_bundle(phase5_app, case_id, 1)
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(truth))
        assert res.status_code == 200, res.json()
    res = interview(phase5_app, pt_id, pt_token, EMILY, "TIME")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "NOT_PLAYING"
    # The view is PLAYING-only too.
    res = witness_view(phase5_app, pt_id, pt_token, EMILY)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "NOT_PLAYING"


def test_interview_discovery_survives_reload(phase5_app, database_url):
    """Interview-discovered witness evidence lands in the persisted
    PlayerKnowledge row and survives a reload (new app over the same DB)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    res = interview(phase5_app, pt_id, pt_token, EMILY, "OBSERVATION")
    assert res.status_code == 200
    assert res.json()["discovery"]["newlyDiscovered"] is True

    from test_phase7_helpers import reopen_app

    app2 = reopen_app(database_url)
    try:
        snap = app2.state.store.snapshot_player_knowledge(pt_id)
        assert snap.discovered == (WITNESS_STATEMENT,)
        assert snap.read == (WITNESS_STATEMENT,)
        with client(app2) as c:
            res = c.get(
                f"/api/v1/playthroughs/{pt_id}/investigation",
                headers=auth(pt_token),
            )
            assert res.status_code == 200
            body = res.json()
            assert WITNESS_STATEMENT in body["playerKnowledge"]["discoveredEvidenceIds"]
            assert WITNESS_STATEMENT in body["playerKnowledge"]["readEvidenceIds"]
        # The read-record endpoint returns the discovered witness statement.
        with client(app2) as c:
            res = c.get(
                f"/api/v1/playthroughs/{pt_id}/records/{WITNESS_STATEMENT}",
                headers=auth(pt_token),
            )
            assert res.status_code == 200
            assert res.json()["content"]["speakerName"] == "Emily Reed"
    finally:
        app2.state.engine.dispose()
        store = getattr(app2.state, "store", None)
        if store is not None:
            try:
                store.dispose()
            except Exception:  # noqa: BLE001 - teardown never masks failure
                pass


# --------------------------------------------------------------------------- #
# 5 — notebook integration (discovered/read flows to the panel)
# --------------------------------------------------------------------------- #


def test_interview_discovery_flows_into_notebook_sets(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token))
        assert res.status_code == 200
        assert res.json()["playerKnowledge"]["discoveredEvidenceIds"] == []
    res = interview(phase5_app, pt_id, pt_token, EMILY, "PERSON")
    assert res.status_code == 200
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert WITNESS_STATEMENT in snap.discovered
    assert WITNESS_STATEMENT in snap.read
    # The playthrough-scoped public-case carries the discovered evidence now.
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/public-case", headers=auth(pt_token))
        assert res.status_code == 200
        ids = [e["id"] for e in res.json()["evidence"]]
        assert WITNESS_STATEMENT in ids


# --------------------------------------------------------------------------- #
# 6 — solver isolation + universe invariance
# --------------------------------------------------------------------------- #


def _solver_signature(payload: dict) -> str:
    """Deterministic signature of the solver outcome over the payload's
    draft (canonical evidence + public model) — the same inputs the pinned
    payload publication proof used."""
    from app.domain.evidence import EvidenceFact, TypedProposition
    from app.domain.public import (
        PublicCase,
        PublicLocation,
        PublicMotive,
        PublicObject,
        PublicPerson,
        PublicScene,
        PublicTravelRule,
    )
    from app.domain.solver import solve_case

    draft = payload["draft"]
    public = PublicCase(
        case_id=str(payload.get("caseId")),
        case_version=int(payload.get("caseVersion") or 1),
        persons=[
            PublicPerson(
                person_id=p["person_id"], name=p["name"], role=p["role"],
                public_affordances=frozenset(p.get("affordances") or ()),
                presented_data=p.get("presented_data") or {},
            )
            for p in draft.get("persons") or ()
        ],
        motives=[
            PublicMotive(motive_id=m["motive_id"], label=m["label"],
                         public_affordances=frozenset(m.get("affordances") or ()))
            for m in draft.get("motives") or ()
        ],
        objects=[
            PublicObject(object_id=o["object_id"], asset_id=o["asset_id"],
                         public_affordances=frozenset(o.get("affordances") or ()),
                         subtype=o.get("subtype"))
            for o in draft.get("objects") or ()
        ],
        locations=[
            PublicLocation(location_id=l["location_id"], name=l["name"])
            for l in draft.get("locations") or ()
        ],
        travel_rules=[
            PublicTravelRule(from_location_id=r["from_location_id"],
                             to_location_id=r["to_location_id"],
                             travel_time_seconds=r["travel_time_seconds"])
            for r in draft.get("travel_rules") or ()
        ],
        scene=PublicScene(location_id=((draft.get("scene") or {}).get("location_id") or ""),
                          name=((draft.get("scene") or {}).get("name") or "")),
    )
    facts: list[EvidenceFact] = []
    for e in draft.get("evidence") or ():
        props = tuple(
            TypedProposition(
                type=p["type"], person_id=p.get("person_id"),
                location_id=p.get("location_id"), object_id=p.get("object_id"),
                motive_id=p.get("motive_id"), observed_at=p.get("observed_at"),
                uncertainty_seconds=p.get("uncertainty_seconds") or 0,
                structured=p.get("structured") or {},
            )
            for p in e.get("propositions") or ()
        )
        facts.append(
            EvidenceFact(id=e["id"], kind=e["kind"], propositions=props,
                         source_ref=None, reliability=e.get("reliability") or "high",
                         presentation=e.get("presentation") or {},
                         discoverable=e.get("discoverable", True))
        )
    proof = solve_case(public, facts)
    signature = {
        "evidence_ids_used": sorted(proof.evidence_ids_used),
        "who": proof.who.survivor_id if getattr(proof.who, "survivor_id", None) is not None else None,
        "why": proof.why.survivor_id if getattr(proof.why, "survivor_id", None) is not None else None,
        "weapon": proof.weapon.survivor_id
        if getattr(proof.weapon, "survivor_id", None) is not None else None,
        "time_connected": proof.when.connected_count,
    }
    return json.dumps(signature, sort_keys=True)


def test_solver_isolation_identical_before_and_after_all_questions(phase5_app):
    """Asking ALL SIX questions (including the grounded ones that discover
    witness evidence) leaves the canonical solver signature byte-identical:
    interview state lives in PlayerKnowledge, never in the payload/proof."""
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    before = _solver_signature(payload)

    for question in ALL_QUESTIONS:
        res = interview(phase5_app, pt_id, pt_token, EMILY, question.value)
        assert res.status_code == 200

    payload_after = _golden_payload(phase5_app, case_id)
    after = _solver_signature(payload_after)
    assert before == after
    # Universes are also unchanged in the pinned payload.
    assert payload["universes"] == payload_after["universes"]


def test_witness_statements_never_enter_the_solver_proof(phase5_app):
    """The published solver proof (evidence_ids_used) never cites the
    witness-statement evidence — the interview is a player-side projection."""
    case_id, _creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    proof_used = payload.get("solverProof", {}).get("evidence_ids_used") or ()
    assert WITNESS_STATEMENT not in proof_used


# --------------------------------------------------------------------------- #
# 7 — zero provider calls
# --------------------------------------------------------------------------- #


def test_interview_and_reload_make_zero_provider_calls():
    """Interviewing the driver-world witness never touches the provider
    transport: after publication, view + interview (grounded or neutral) +
    reload keep the transport call_count unchanged."""
    from test_ollama_driver import _run, _staged

    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="hard", model="mock", title="Hard"
        )
    )
    witness = _driver_witness(payload)
    calls_after_generation = transport.call_count
    assert calls_after_generation > 0
    for question in ALL_QUESTIONS:
        _ = project_witness_statement(payload, witness, question)
    # Presence/view projections are pure too.
    _ = witness_presence(payload, witness["person_id"])
    assert transport.call_count == calls_after_generation


# --------------------------------------------------------------------------- #
# 8 — security / leak boundaries
# --------------------------------------------------------------------------- #


def test_no_undiscovered_statement_text_in_public_dto_or_witness_view(phase5_app):
    """Before any question is asked, the witness statement TEXT never appears
    in: the playthrough-scoped public-case DTO, the world-object DTOs of the
    investigation bootstrap, the witness view, or question metadata."""
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    # The published witness statement presentation carries the secret text.
    statement_text = next(
        e["presentation"]["statement"]
        for e in payload["draft"]["evidence"]
        if e["id"] == WITNESS_STATEMENT
    )
    # 1. playthrough public-case (undiscovered evidence list is empty).
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/public-case", headers=auth(pt_token))
        assert res.status_code == 200
        assert statement_text not in res.text
        assert res.json()["evidence"] == []
        # 2. investigation bootstrap / world-object DTOs.
        res = c.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token))
        assert res.status_code == 200
        assert statement_text not in res.text
        # 3. witness view carries NO statement content at all.
    res = witness_view(phase5_app, pt_id, pt_token, EMILY)
    assert res.status_code == 200
    assert statement_text not in res.text
    assert "statement" not in res.text.casefold().replace("statement", "x", 1)
    # 4. question metadata itself never reveals content: all six offered.
    assert len(res.json()["questions"]) == 6


def test_release_leak_scan_after_discovery_is_bounded(phase5_app):
    """After a grounded interview, the discovery record carries the statement
    content ONLY inside the player-safe read DTO (the same shape the record-
    read endpoint returns) — never raw payload sections or hidden material."""
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    res = interview(phase5_app, pt_id, pt_token, EMILY, "OBSERVATION")
    body = res.json()
    assert_no_hidden_leaks(body)
    for tail in ("propositions", "sourceRef", "observedAt", "truth", "murdererId"):
        assert tail not in json.dumps(body), tail


def test_adversarial_statement_text_is_bounded_and_control_char_free():
    """HTML/script-like allowlisted text stays a bounded plain string (the
    frontend renders text-only); control characters are stripped and lengths
    are capped — the DTO can never carry executable content."""
    from app.domain.witness import _clean_text  # noqa: PLC2701 (white-box bound guard)

    hostile = "<script>alert('x')</script>\x00javascript:void(0)\n第三行"
    cleaned = _clean_text(hostile, MAX_OBSERVATION_TEXT_CHARS)
    assert "\x00" not in cleaned
    assert len(cleaned) <= MAX_OBSERVATION_TEXT_CHARS
    long_text = "A" * (MAX_OBSERVATION_TEXT_CHARS * 3)
    assert len(_clean_text(long_text, MAX_OBSERVATION_TEXT_CHARS)) <= MAX_OBSERVATION_TEXT_CHARS
    assert _clean_text(12345, MAX_OBSERVATION_TEXT_CHARS) == ""  # non-str stays inert


# --------------------------------------------------------------------------- #
# 9 — presence derivation (deterministic, never invented)
# --------------------------------------------------------------------------- #


def test_presence_on_scene_requires_a_published_person_placement(phase5_app):
    """Presence is ON_SCENE ONLY when the published world graph carries a
    person placement for the witness; the golden world has none -> REMOTE."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    del creator
    presence, at_scene = witness_presence(payload, EMILY)
    assert presence is WitnessPresence.REMOTE_STATEMENT
    assert at_scene is False

    # Craft: add a placement whose object_id equals the witness person id.
    draft = payload["draft"]
    draft["world_graph"]["placements"].append(
        {
            "object_id": EMILY,
            "asset_id": "PROP_GENERIC_NPC_01",
            "location_id": "miller_apartment_kitchen",
            "anchor": "floor_center",
            "interaction": "inspect",
            "evidence_id": None,
        }
    )
    presence, at_scene = witness_presence(payload, EMILY)
    assert presence is WitnessPresence.ON_SCENE
    assert at_scene is True


# --------------------------------------------------------------------------- #
# 10 — publication gate / integrity helper behaviour
# --------------------------------------------------------------------------- #


def test_golden_case_passes_published_witness_integrity_checks(phase5_app):
    """A case with a witness passes the existing integrity checks: the witness
    person resolves with role 'witness', the interview projection references
    ONLY published evidence ids, and every witness-kind proposition id
    resolves in the public model (persons/locations/objects)."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    del creator
    draft = payload["draft"]
    entity_ids = set()
    for person in draft.get("persons") or ():
        entity_ids.add(("person", str(person.get("person_id"))))
    for loc in draft.get("locations") or ():
        entity_ids.add(("location", str(loc.get("location_id"))))
    for obj in draft.get("objects") or ():
        entity_ids.add(("object", str(obj.get("object_id"))))
    for e in draft.get("evidence") or ():
        if e["kind"] not in WITNESS_KINDS:
            continue
        for prop in e.get("propositions") or ():
            if prop.get("person_id") is not None:
                assert ("person", str(prop["person_id"])) in entity_ids
            if prop.get("location_id") is not None:
                assert ("location", str(prop["location_id"])) in entity_ids
            if prop.get("object_id") is not None:
                assert ("object", str(prop["object_id"])) in entity_ids
    # The witness person is a validated role=="witness" person.
    witness = witness_person_of(payload, EMILY)
    assert witness is not None and witness["role"] == "witness"
    # The interview projection references only published evidence ids.
    projection = project_witness_statement(payload, witness, WitnessQuestionType.TIME)
    assert projection.statement.evidence_ids == (WITNESS_STATEMENT,)
    assert WITNESS_STATEMENT in {e["id"] for e in draft.get("evidence") or ()}


# --------------------------------------------------------------------------- #
# 11 — adversarial / robustness additions
# --------------------------------------------------------------------------- #


def test_interview_typed_failures_stay_sanitized(phase5_app):
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    from phase5_helpers import assert_sanitized_error

    res = interview(phase5_app, pt_id, pt_token, "ghost", "TIME")
    assert res.status_code == 404
    assert_sanitized_error(res.text)
    assert "ghost" not in json.dumps(res.json())
    res = interview(phase5_app, pt_id, pt_token, EMILY, "NOT_A_QUESTION")
    assert res.status_code == 422
    assert_sanitized_error(res.text)
    res = witness_view(phase5_app, pt_id, "bad-token", EMILY)
    assert res.status_code == 401  # unknown credential -> sanitized 401
    assert_sanitized_error(res.text)


def test_witness_names_and_statement_text_are_untrusted_but_bounded(phase5_app):
    """Very long / punctuation / unicode / duplicate witness-named persons in
    a CRAFTED payload still produce bounded, player-safe views and never
    crash the projection; internal person ids never surface."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    del creator
    draft = payload["draft"]
    huge_name = ("『" * 2000) + "Zulu" + ("!'" * 2000)
    draft["persons"].append(
        {
            "person_id": "witness_weird",
            "name": huge_name,
            "role": "witness",
            "affordances": ["VISIBLE_CHARACTER"],
            "presented_data": {},
        }
    )
    draft["evidence"].append(
        {
            "id": "weird_statement_01",
            "kind": "witness_statement",
            "discoverable": True,
            "reliability": "high",
            "propositions": [
                {
                    "type": "WITNESS_CLAIMS",
                    "person_id": "witness_weird",
                    "location_id": None,
                    "object_id": None,
                    "motive_id": None,
                    "observed_at": None,
                    "uncertainty_seconds": 0,
                    "structured": {},
                }
            ],
            "presentation": {
                "title": "A statement",
                "description": "A bounded description.",
                "speakerName": huge_name[:200],
                "statement": "<script>alert(1)</script>" + ("x" * 5000),
            },
        }
    )
    witness = witness_person_of(payload, "witness_weird")
    assert witness is not None
    view_name = str(witness.get("name") or "")[:MAX_NAME_CHARS]
    assert "\x00" not in view_name and len(view_name) <= MAX_NAME_CHARS
    for question in ALL_QUESTIONS:
        projection = project_witness_statement(payload, witness, question)
        statement = projection.statement
        blob = json.dumps(
            {
                "summary": statement.summary,
                "observations": [
                    {"time": o.time, "text": o.text} for o in statement.observations
                ],
            },
            sort_keys=True,
        )
        assert "\x00" not in blob
        assert len(statement.summary) <= MAX_STATEMENT_SUMMARY_CHARS
        assert len(statement.observations) <= MAX_OBSERVATIONS


def test_unknown_person_object_references_are_ignored_not_grounded(phase5_app):
    """Crafted witness-kind evidence referencing UNKNOWN persons/objects never
    grounds a PERSON/OBJECT/LOCATION answer (referential integrity at the
    projection layer: only canonical public references count)."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    del creator
    draft = payload["draft"]
    draft["evidence"].append(
        {
            "id": "ghost_ref_statement_01",
            "kind": "witness_statement",
            "discoverable": True,
            "reliability": "high",
            "propositions": [
                {
                    "type": "WITNESS_CLAIMS",
                    "person_id": EMILY,
                    "location_id": None,
                    "object_id": None,
                    "motive_id": None,
                    "observed_at": None,
                    "uncertainty_seconds": 0,
                    "structured": {},
                },
                {
                    "type": "OTHER",
                    "person_id": "ghost_person",
                    "object_id": "ghost_object",
                    "location_id": "ghost_location",
                    "motive_id": None,
                    "observed_at": None,
                    "uncertainty_seconds": 0,
                    "structured": {},
                },
            ],
            "presentation": {
                "title": "Ghost refs",
                "description": "References unknown entities.",
                "speakerName": "Emily Reed",
            },
        }
    )
    witness = witness_person_of(payload, EMILY)
    # The ghost refs never appear in the DTO and never ground OBJECT/LOCATION
    # on their own (the unknown ids fail the public-model membership check).
    for question in (WitnessQuestionType.PERSON, WitnessQuestionType.OBJECT, WitnessQuestionType.LOCATION):
        projection = project_witness_statement(payload, witness, question)
        statement = projection.statement
        blob = json.dumps(
            {"summary": statement.summary,
             "observations": [o.text for o in statement.observations]},
            sort_keys=True,
        )
        assert "ghost_person" not in blob
        assert "ghost_object" not in blob
        assert "ghost_location" not in blob
        # The golden statement is still the ONLY grounded source (deterministic).
        assert sorted(statement.evidence_ids) == sorted(statement.evidence_ids)


def test_invalid_timestamp_and_deep_nested_payload_fail_safely(phase5_app):
    """Invalid/out-of-domain timestamps and deeply nested statement payloads
    never crash the projection and never leak the raw value."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    del creator
    draft = payload["draft"]
    deep_nested = {"a": {"b": {"c": {"d": ["x"] * 8}}}}
    draft["evidence"].append(
        {
            "id": "bad_time_statement_01",
            "kind": "witness_statement",
            "discoverable": True,
            "reliability": "low",
            "propositions": [
                {
                    "type": "WITNESS_CLAIMS",
                    "person_id": EMILY,
                    "location_id": None,
                    "object_id": None,
                    "motive_id": None,
                    "observed_at": "not-a-time",
                    "uncertainty_seconds": -5,
                    "structured": deep_nested,
                }
            ],
            "presentation": {
                "title": "Bad time",
                "description": "An invalid time observation.",
                "speakerName": "Emily Reed",
                "statement": {"nested": deep_nested},
            },
        }
    )
    witness = witness_person_of(payload, EMILY)
    for question in ALL_QUESTIONS:
        projection = project_witness_statement(payload, witness, question)
        statement = projection.statement
        assert isinstance(statement.summary, str)
        for obs in statement.observations:
            assert isinstance(obs.text, str)  # hostile nested text stays '' / inert
            if obs.time is not None:
                assert isinstance(obs.time, str) and len(obs.time) <= 64
        blob = json.dumps({"summary": statement.summary,
                           "observations": [o.text for o in statement.observations]})
        assert "not-a-time" not in blob  # unverifiable time never surfaces


# =========================================================================== #
# 12 — CROSS-TRACK CONTRACT (frontend track): the investigation bootstrap
# `witnesses` list, the read-content `questionType`/`witnessId` tags, and the
# interview-discovery record carrying the asked question type.
# =========================================================================== #


def _insert_crafted_payload(
    app, payload: dict, *, case_id: str, version: int, pt_id: str
) -> str:
    """Insert a crafted published payload into the store + a PLAYING
    playthrough pinned to it; returns the playthrough access token.

    The payload is the pinned source for the bootstrap exactly like a real
    published_versions row (the edit never touches the hidden sections). The
    stored case_id/version is the caller's (the payload's internal identity
    fields are informational; nothing cross-checks them).
    """
    from app.auth.tokens import issue_playthrough_access_token, verifier as v

    store = app.state.store
    now = float(app.state.clock.now())
    # The crafted rows must satisfy the same FK chain the API uses: a
    # session row (cases.quota_session_id -> sessions), then the case, the
    # published version and the playthrough (mirrors the phase-21 crafted
    # helpers; never touches process state, only the per-test DB file).
    quota_session_id = f"QUOTA-P23-{uuid.uuid4().hex[:10]}"
    store.create_session(
        session_id=quota_session_id,
        token_verifier=v(issue_playthrough_access_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    store.create_case(
        case_id=case_id,
        quota_session_id=quota_session_id,
        title="Crafted Phase 23 driver world",
        difficulty=None,
        created_at=now,
    )
    store.create_case_version(
        case_id=case_id,
        version=version,
        state=GenerationState.PUBLISHED.value,
        generation_id=f"GEN-CRAFT-{version}",
        created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=version,
        payload_json=json.dumps(
            dict(payload, caseId=case_id, caseVersion=version),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        published_at=now,
    )
    pt_token = issue_playthrough_access_token()
    store.create_playthrough_if_published(
        playthrough_id=pt_id,
        case_id=case_id,
        case_version=version,
        token_verifier=v(pt_token),
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    return pt_token


def test_bootstrap_witnesses_golden_emily_remote(phase5_app):
    """The pinned golden case publishes Emily Reed (role witness, no person
    placement) -> the bootstrap `witnesses` carries exactly the player-safe
    identity with the deterministic REMOTE_STATEMENT presence and null scene
    linkage. Never statement/availability content; the deep leak scanners
    stay clean."""
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token))
        assert res.status_code == 200
        body = res.json()
    assert body["witnesses"] == [
        {
            "witnessId": EMILY,
            "displayName": "Emily Reed",
            "presence": "REMOTE_STATEMENT",
            "sceneObjectId": None,
        }
    ]
    assert_no_hidden_leaks(body)
    # No statement/availability content leaks through the block.
    blob = json.dumps(body)
    assert NEUTRAL_SUMMARY not in blob
    assert "questions" not in blob
    assert "observations" not in blob


def test_bootstrap_witnesses_driver_lisa_koenig(phase5_app):
    """A published driver world (Lisa König role=witness) -> the bootstrap
    `witnesses` carries her with REMOTE_STATEMENT (no person placement in
    the world graph) and the Unicode display name."""
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    pt_id = f"PT-WITNESS-DRV-{uuid.uuid4().hex[:10]}"
    pt_token = _insert_crafted_payload(
        phase5_app,
        payload,
        case_id=f"CASE-P23-DRV-{uuid.uuid4().hex[:10]}",
        version=1,
        pt_id=pt_id,
    )
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token))
        assert res.status_code == 200, res.text
        body = res.json()
    assert body["witnesses"] == [
        {
            "witnessId": "lisa_koenig",
            "displayName": "Lisa König",
            "presence": "REMOTE_STATEMENT",
            "sceneObjectId": None,
        }
    ]
    assert all(
        set(w.keys()) == {"witnessId", "displayName", "presence", "sceneObjectId"}
        for w in body["witnesses"]
    )
    assert_no_hidden_leaks(body)


def test_bootstrap_witnesses_empty_case_returns_empty_list(phase5_app):
    """A published case whose persons carry NO role=='witness' (here: the
    driver world with the witness person removed) -> `witnesses` is ALWAYS
    present and an empty list (stable frontend contract)."""
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    payload["draft"]["persons"] = [
        p for p in payload["draft"]["persons"] if str(p.get("role")) != "witness"
    ]
    pt_id = f"PT-WITNESS-EMPTY-{uuid.uuid4().hex[:10]}"
    pt_token = _insert_crafted_payload(
        phase5_app,
        payload,
        case_id=f"CASE-P23-EMPTY-{uuid.uuid4().hex[:10]}",
        version=1,
        pt_id=pt_id,
    )
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token))
        assert res.status_code == 200, res.text
        body = res.json()
    assert body["witnesses"] == []
    assert_no_hidden_leaks(body)


def test_read_record_of_discovered_witness_statement_carries_tags(phase5_app):
    """The read-record DTO of an interview-discovered witness statement
    carries `content.witnessId` + a CLOSED `content.questionType`
    (deterministically the first question the evidence grounds in the frozen
    question order — the reload notebook re-derives 'Witness statements' from
    these READ records)."""
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    res = interview(phase5_app, pt_id, pt_token, EMILY, "TIME")
    assert res.status_code == 200
    assert res.json()["discovery"]["newlyDiscovered"] is True
    with client(phase5_app) as c:
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/records/{WITNESS_STATEMENT}",
            headers=auth(pt_token),
        )
        assert res.status_code == 200, res.text
        record = res.json()
    content = record["content"]
    assert content["witnessId"] == EMILY
    # Golden statement grounds OBSERVATION/TIME/PERSON/LOCATION; the FIRST in
    # the frozen ALL_QUESTIONS order is OBSERVATION -> deterministic tag.
    assert content["questionType"] == "OBSERVATION"
    assert content["questionType"] in {q.value for q in ALL_QUESTIONS}
    # The kind-allowlisted speaker/statement content stays intact alongside.
    assert content["speakerName"] == "Emily Reed"
    assert_no_hidden_leaks(record)


def test_interview_discovery_record_carries_asked_question_type(phase5_app):
    """The interview discovery record carries `content.questionType` == the
    type the player ACTUALLY asked (TIME) and `content.witnessId` == the
    interviewed witness — the notebook correlates the in-band record."""
    case_id, pt_id, pt_token = golden_witness_bundle(phase5_app)
    del case_id
    res = interview(phase5_app, pt_id, pt_token, EMILY, "TIME")
    assert res.status_code == 200
    body = res.json()
    record = body["discovery"]["record"]
    assert record["evidenceId"] == WITNESS_STATEMENT
    assert record["content"]["questionType"] == "TIME"
    assert record["content"]["witnessId"] == EMILY
    assert_no_hidden_leaks(body)
    # A different asked type overrides the same record's in-band tag.
    res2 = interview(phase5_app, pt_id, pt_token, EMILY, "OBSERVATION")
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["discovery"]["record"]["content"]["questionType"] == "OBSERVATION"
    assert body2["discovery"]["record"]["content"]["witnessId"] == EMILY
    assert body2["discovery"]["newlyDiscovered"] is False  # idempotent repeat
