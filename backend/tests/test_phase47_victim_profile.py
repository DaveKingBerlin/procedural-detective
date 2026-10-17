"""Phase 47 — victim profile & investigation record (backend scope).

Focused coverage for the frozen Phase 47 contract:

1. AI victim profile contract  — a generated playable case carries the required
   victim identity and ``victim.personId`` resolves to a canonical person;
2. bootstrap victim identity + discovery (Demo Case 1);
3. no solution leak — hidden CaseTruth never appears in the victim block / read
   content;
4. forensic progressive disclosure — a victim-linked record carries the additive
   ``victimRelated`` marker and its content stays undiscovered until read;
5. Demo Case 1/2/3 victim profiles (identity + discovery resolve);
6. Demo solutions unchanged;
7. legacy / missing optional fields never crash;
8. save/restore preserves the victim profile;
9. difficulty matrix (Easy/Medium/Hard) yields the required identity;
10. provider-independent structured ``presentedData`` acceptance;

plus the victim-profile quality codes (blocking + repairable) and the evidence
completion canonical-victim guard.

The tests reuse the hermetic fake provider (ZERO external network) and the
shared API babysitters; every case is generated through the REAL pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.domain.public import (  # noqa: E402
    PublicCase,
    PublicLocation,
    PublicScene,
)
from app.domain.truth import CaseTruth, Crime, CrimeTime  # noqa: E402
from app.domain.victim_profile import (  # noqa: E402
    DEFAULT_VICTIM_AGE,
    GENDER_VALUES,
    identity_of,
    normalize_age,
    normalize_gender,
)
from app.generation import case_quality  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation import repair_routing  # noqa: E402
from app.generation.parser import parse_full_draft  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.cases import PersonDTO  # noqa: E402
from app.services import demo_cases  # noqa: E402
from app.services import savegame as sg  # noqa: E402
from app.services import publication as pub  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from phase5_helpers import (  # noqa: E402
    auth,
    create_case,
    create_playthrough,
    create_session,
)

_DEMO_IDS = ("demo-apartment", "demo-gallery", "demo-laboratory")
_HIDDEN_TRUTH_KEYS = (
    "murderer_id",
    "murdererId",
    "motive_id",
    "weapon_id",
    "crime_time",
    "crimeTime",
    "solver_proof",
    "solverProof",
    "truth",
)


# --------------------------------------------------------------------------- #
# app / case / playthrough helpers
# --------------------------------------------------------------------------- #


def _make_app(database_url, **overrides):
    upgrade_db(database_url)
    return create_app(
        Settings(
            database_url=database_url,
            cors_allowed_origins=["http://localhost:5173"],
            **overrides,
        )
    )


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


def _start_case(client, session_token: str, prompt: str, **extra) -> dict:
    body = {"prompt": prompt, **extra}
    res = client.post("/api/v1/cases", json=body, headers=auth(session_token))
    assert res.status_code == 201, res.text
    return res.json()


def _publish_and_play(client, session_token: str, prompt: str, **extra):
    started = _start_case(client, session_token, prompt, **extra)
    assert started["status"] == "PUBLISHED", started
    status, pt = create_playthrough(
        client, started["creatorAccessToken"], started["caseId"], 1
    )
    assert status == 201, pt
    return started, pt


def _published_payload(app, case_id: str, version: int = 1) -> dict:
    row = app.state.store.get_published(case_id, version)
    assert row is not None
    return json.loads(row.payload_json)


def _bootstrap(client, pt: dict) -> dict:
    res = client.get(
        f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
        headers=auth(pt["playthroughAccessToken"]),
    )
    assert res.status_code == 200, res.text
    return res.json()


def _demo_truth(demo_case_id: str) -> dict:
    record = demo_cases.get_demo_case(demo_case_id)
    assert record is not None
    raw = record.script[GenerationStage.CASE_TRUTH][0]
    return json.loads(raw)["crime"]


# --------------------------------------------------------------------------- #
# pure normalization / projection unit tests
# --------------------------------------------------------------------------- #


def test_normalize_gender_closed_vocabulary_and_documented_variants():
    for value in ("male", "M", "man", "Man", "masculine", "MALE"):
        assert normalize_gender(value) == "male"
    for value in ("female", "F", "woman", "feminine", "WOMAN"):
        assert normalize_gender(value) == "female"
    assert normalize_gender("other") == "other"
    for value in ("banana", "  ", "", None, 7, "unknown", "x"):
        assert normalize_gender(value) == "unknown"
    assert normalize_gender("unknown") in GENDER_VALUES


def test_normalize_age_bounds_and_types():
    assert normalize_age(1) == 1
    assert normalize_age(120) == 120
    assert normalize_age("39") == 39
    for value in (0, 121, -3, True, False, "abc", None, 1.5, "999"):
        assert normalize_age(value) is None


def test_identity_of_omits_absent_and_normalizes_present():
    assert identity_of(None) == {}
    assert identity_of({}) == {}
    # A present but unrecognized gender normalizes to the safe closed value.
    assert identity_of({"gender": "banana"}) == {"gender": "unknown"}
    out = identity_of(
        {
            "age": 39,
            "gender": "F",
            "occupation": "  CEO, Miller Consulting  ",
            "background": "Founder of the firm.",
        }
    )
    assert out == {
        "age": 39,
        "gender": "female",
        "occupation": "CEO, Miller Consulting",
        "background": "Founder of the firm.",
    }
    # Invalid age is OMITTED, never rendered as null/NaN.
    assert "age" not in identity_of({"age": 999})


def test_person_dto_omits_absent_identity_keys():
    assert PersonDTO(personId="p", name="N", role="victim").model_dump() == {
        "personId": "p",
        "name": "N",
        "role": "victim",
        "affordances": [],
    }
    dumped = PersonDTO(
        personId="victim", name="V", role="victim", age=44, gender="female",
        occupation="Chemist",
    ).model_dump()
    assert dumped["age"] == 44 and dumped["gender"] == "female"
    assert dumped["occupation"] == "Chemist"
    assert "background" not in dumped


def _payload(crime_victim: str | None, persons, evidence=(), placements=(), locations=()):
    crime = {} if crime_victim is None else {"victim_id": crime_victim}
    return {
        "draft": {
            "crime": crime,
            "persons": list(persons),
            "evidence": list(evidence),
            "world_graph": {"placements": list(placements)},
            "locations": list(locations),
        }
    }


def test_victim_profile_of_projects_identity_and_discovery():
    payload = _payload(
        "sarah",
        [
            {
                "person_id": "sarah",
                "name": "Sarah Miller",
                "role": "victim",
                "presented_data": {"age": 39, "gender": "F", "occupation": "CEO"},
            }
        ],
        evidence=[
            {
                "id": "body_found_01",
                "presentation": {"title": "Body found at 22:18"},
                "propositions": [
                    {
                        "type": "BODY_FIRST_FOUND_AT",
                        "location_id": "kitchen",
                        "observed_at": "2026-09-11T22:18:31+02:00",
                    }
                ],
            }
        ],
        placements=[{"object_id": "victim_body", "evidence_id": "body_found_01"}],
        locations=[{"location_id": "kitchen", "name": "Kitchen"}],
    )
    profile = pub.victim_profile_of(payload)
    assert profile is not None
    assert profile["personId"] == "sarah"
    assert profile["name"] == "Sarah Miller"
    assert profile["age"] == 39 and profile["gender"] == "female"
    assert profile["occupation"] == "CEO"
    assert profile["sceneObjectId"] == "victim_body"
    assert profile["discovery"] == {
        "evidenceId": "body_found_01",
        "locationId": "kitchen",
        "locationName": "Kitchen",
        "observedAt": "2026-09-11T22:18:31+02:00",
        "title": "Body found at 22:18",
    }


def test_victim_profile_of_returns_none_without_resolvable_victim():
    no_crime = _payload(None, [{"person_id": "x", "name": "X", "role": "suspect"}])
    assert pub.victim_profile_of(no_crime) is None
    dangling = _payload("ghost", [{"person_id": "v", "name": "V", "role": "victim"}])
    assert pub.victim_profile_of(dangling) is None
    # A case with no BODY_FIRST_FOUND_AT still yields the identity block. DEF-115:
    # a resolved victim with no presented_data renders a COMPLETE identity with
    # the documented deterministic fallbacks.
    no_discovery = _payload(
        "v",
        [{"person_id": "v", "name": "V", "role": "victim", "presented_data": {}}],
    )
    profile = pub.victim_profile_of(no_discovery)
    assert profile == {
        "personId": "v",
        "name": "V",
        "gender": "unknown",
        "occupation": "Unknown",
        "age": DEFAULT_VICTIM_AGE,
    }


# --------------------------------------------------------------------------- #
# 3 / 4 — no leak + progressive disclosure
# --------------------------------------------------------------------------- #


def _golden_or_demo_payload(application, demo_case_id="demo-apartment"):
    record = demo_cases.get_demo_case(demo_case_id)
    prompt = record.prompt
    with TestClient(application) as c:
        session_token, _ = create_session(c)
        started = _start_case(c, session_token, prompt, demoCaseId=demo_case_id)
        assert started["status"] == "PUBLISHED"
    return _published_payload(application, started["caseId"])


def test_victim_block_and_read_content_never_leak_hidden_truth(database_url):
    application = _make_app(database_url)
    try:
        payload = _golden_or_demo_payload(application)
        profile = pub.victim_profile_of(payload)
        assert profile is not None
        blob = json.dumps(profile)
        for key in _HIDDEN_TRUTH_KEYS:
            assert key not in blob, key
        # The published truth (murderer/motive/weapon/time) is NOT in the profile.
        truth = payload["truth"]["crime"]
        assert truth["murderer_id"] not in blob
        assert truth["crime_time"]["canonical"] not in blob
        # Read content of a victim-linked record also carries no hidden truth.
        content = pub.project_read_content(payload, "last_seen_01")
        content_blob = json.dumps(content)
        for key in _HIDDEN_TRUTH_KEYS:
            assert key not in content_blob, key
        assert truth["murderer_id"] not in content_blob
    finally:
        _dispose(application)


def test_victim_related_marker_only_on_victim_linked_records(database_url):
    application = _make_app(database_url)
    try:
        payload = _golden_or_demo_payload(application)
        victim_id = payload["draft"]["crime"]["victim_id"]
        linked = pub.project_read_content(payload, "last_seen_01")
        assert linked.get("victimRelated") is True
        victim_prop = payload["draft"]["evidence"]
        # Sanity: last_seen_01 really references the victim.
        last_seen = next(f for f in victim_prop if f["id"] == "last_seen_01")
        assert any(
            p.get("person_id") == victim_id for p in last_seen["propositions"]
        )
        # A non-victim record omits the key entirely (never false).
        knife = pub.project_read_content(payload, "forensic_knife_match_01")
        assert "victimRelated" not in knife
    finally:
        _dispose(application)


def test_victim_linked_record_revealed_only_after_discovery(database_url):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            session_token, _ = create_session(c)
            started, pt = _publish_and_play(
                c, session_token, demo_cases.get_demo_case("demo-apartment").prompt,
                demoCaseId="demo-apartment",
            )
            pt_id = pt["playthroughId"]
            pt_token = pt["playthroughAccessToken"]
            # Undiscovered victim-linked record -> 403 (content stays gated).
            res = c.get(
                f"/api/v1/playthroughs/{pt_id}/records/last_seen_01",
                headers=auth(pt_token),
            )
            assert res.status_code == 403, res.text
            assert res.json()["error"]["code"] == "EVIDENCE_NOT_DISCOVERED"
            # Discover a placement-linked record (the victim body) and read it.
            res = c.post(
                f"/api/v1/playthroughs/{pt_id}/objects/victim_body_placeholder/interact",
                json={"interaction": "inspect"},
                headers=auth(pt_token),
            )
            assert res.status_code == 200, res.text
            res = c.get(
                f"/api/v1/playthroughs/{pt_id}/records/body_found_01",
                headers=auth(pt_token),
            )
            assert res.status_code == 200, res.text
            # body_found_01 has no victim person_id -> no marker (never false).
            assert "victimRelated" not in res.json()["content"]
        payload = _published_payload(application, started["caseId"])
        assert pub.project_read_content(payload, "last_seen_01")["victimRelated"] is True
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# 2 / 5 — bootstrap victim identity + discovery for every Demo Case
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("demo_case_id", _DEMO_IDS)
def test_bootstrap_exposes_victim_identity_and_discovery(database_url, demo_case_id):
    application = _make_app(database_url)
    try:
        record = demo_cases.get_demo_case(demo_case_id)
        with TestClient(application) as c:
            session_token, _ = create_session(c)
            _, pt = _publish_and_play(
                c, session_token, record.prompt, demoCaseId=demo_case_id
            )
            boot = _bootstrap(c, pt)
        assert "victim" in boot, "victim block must be present"
        victim = boot["victim"]
        assert victim["personId"]
        assert victim["name"].strip()
        assert 1 <= int(victim["age"]) <= 120
        assert victim["gender"] in GENDER_VALUES
        assert victim["occupation"].strip()
        discovery = victim["discovery"]
        assert discovery["evidenceId"]
        assert discovery["locationId"]
        assert discovery["locationName"].strip()
        assert discovery["title"].strip()
        # The discovery time parses as ISO-8601.
        from app.domain.time_interval import parse_iso8601

        parse_iso8601(discovery["observedAt"])
        # The victim resolves to a canonical public person of the pinned case.
        payload = _published_payload(application, pt["caseId"])
        ids = {p["person_id"] for p in payload["draft"]["persons"]}
        assert victim["personId"] in ids
    finally:
        _dispose(application)


def test_bootstrap_victim_scene_object_id_resolves_to_body_placement(database_url):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            session_token, _ = create_session(c)
            _, pt = _publish_and_play(
                c, session_token, demo_cases.get_demo_case("demo-apartment").prompt,
                demoCaseId="demo-apartment",
            )
            boot = _bootstrap(c, pt)
        victim = boot["victim"]
        assert victim["sceneObjectId"] == "victim_body_placeholder"
        object_ids = {o["objectId"] for o in boot["scene"]["worldObjects"]}
        assert victim["sceneObjectId"] in object_ids
    finally:
        _dispose(application)


def test_bootstrap_omits_victim_key_for_victim_less_case(database_url):
    """Additive contract: a crafted pinned payload with no victim yields a
    bootstrap with NO ``victim`` key (byte-identical to the pre-47 shape)."""
    payload = _payload(None, [{"person_id": "x", "name": "X", "role": "suspect"}])
    from app.schemas.investigation import InvestigationBootstrapResponse

    body = InvestigationBootstrapResponse(
        playthroughId="PT",
        caseId="C",
        caseVersion=1,
        state="PLAYING",
        playerKnowledge={},
        scene={"location": {"locationId": "l", "name": "L"}, "worldObjects": []},
    )
    dumped = body.model_dump()
    assert "victim" not in dumped
    assert pub.victim_profile_of(payload) is None


# --------------------------------------------------------------------------- #
# 6 — Demo solutions unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("demo_case_id", _DEMO_IDS)
def test_demo_solution_unchanged(database_url, demo_case_id):
    application = _make_app(database_url)
    try:
        record = demo_cases.get_demo_case(demo_case_id)
        with TestClient(application) as c:
            session_token, _ = create_session(c)
            started, _ = _publish_and_play(
                c, session_token, record.prompt, demoCaseId=demo_case_id
            )
        payload = _published_payload(application, started["caseId"])
        crime = payload["draft"]["crime"]
        expected = _demo_truth(demo_case_id)
        assert crime["victim_id"] == expected["victimId"]
        assert crime["murderer_id"] == expected["murdererId"]
        assert crime["motive_id"] == expected["motiveId"]
        assert crime["weapon_id"] == expected["weaponId"]
        assert (
            crime["crime_time"]["canonical"]
            == expected["crimeTime"]["canonical"]
        )
        # The victim identity is the ONLY Phase 47 addition to the persons.
        victim = next(
            p for p in payload["draft"]["persons"] if p["person_id"] == crime["victim_id"]
        )
        assert set(victim["presented_data"]) >= {"age", "gender", "occupation"}
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# 1 / 9 — AI generation contract + difficulty matrix
# --------------------------------------------------------------------------- #


def _assert_ai_victim_contract(payload: dict) -> None:
    crime = payload["draft"]["crime"]
    victim_id = crime["victim_id"]
    victim = next(
        (p for p in payload["draft"]["persons"] if p["person_id"] == victim_id), None
    )
    assert victim is not None and victim["role"] == "victim"
    presented = victim["presented_data"]
    assert 1 <= int(presented["age"]) <= 120
    assert normalize_gender(presented["gender"]) in GENDER_VALUES
    assert isinstance(presented["occupation"], str) and presented["occupation"].strip()


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_ai_generation_contract_identity_across_difficulties(database_url, difficulty):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            session_token, _ = create_session(c)
            started = create_case(c, session_token, difficulty=difficulty)
            assert started["status"] == "PUBLISHED", started
            status, pt = create_playthrough(
                c, started["creatorAccessToken"], started["caseId"], 1
            )
            assert status == 201
            boot = _bootstrap(c, pt)
        payload = _published_payload(application, started["caseId"])
        _assert_ai_victim_contract(payload)
        assert "victim" in boot
        assert normalize_gender(boot["victim"]["gender"]) in GENDER_VALUES
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# 8 — savegame / restore preserves the victim profile
# --------------------------------------------------------------------------- #


def test_savegame_export_and_restore_preserve_victim_identity(database_url):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            session_token, _ = create_session(c)
            started, _ = _publish_and_play(
                c, session_token, demo_cases.get_demo_case("demo-gallery").prompt,
                demoCaseId="demo-gallery",
            )
        payload = _published_payload(application, started["caseId"])
        doc = sg.project_savegame_v1(payload, difficulty="medium")
        exported_persons = {
            p["personId"]: p for p in doc["case"]["publicCase"]["persons"]
        }
        victim_id = payload["draft"]["crime"]["victim_id"]
        assert {"age", "gender", "occupation"} <= set(exported_persons[victim_id])
        # The export is a deterministic projection of the SAME pinned payload, so
        # re-deriving it (the backend half of save -> reload -> restore) yields
        # the identical identity.
        again = sg.project_savegame_v1(payload, difficulty="medium")
        assert again["case"]["publicCase"] == doc["case"]["publicCase"]
        restored = {
            p["personId"]: p for p in again["case"]["publicCase"]["persons"]
        }
        assert restored[victim_id] == exported_persons[victim_id]
        # The identity survives through the bootstrap projection too.
        assert pub.victim_profile_of(payload)["gender"] == "female"
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# 7 — legacy / missing optional fields never crash
# --------------------------------------------------------------------------- #


def test_legacy_payload_without_identity_fields_projects_safely():
    payload = _payload(
        "v",
        [{"person_id": "v", "name": "V", "role": "victim"}],  # no presented_data
    )
    # DEF-115: the victim profile ALWAYS renders a complete identity (name +
    # age + gender + occupation) via the documented deterministic fallbacks.
    profile = pub.victim_profile_of(payload)
    assert profile == {
        "personId": "v",
        "name": "V",
        "gender": "unknown",
        "occupation": "Unknown",
        "age": DEFAULT_VICTIM_AGE,
    }
    # The additive PersonDTO still OMITS absent identity (no fabricated fields
    # on the generic public-case persons surface).
    dto = pub.public_case_dict_from_payload(
        {
            "caseId": "C",
            "caseVersion": 1,
            "title": "T",
            "draft": {
                "crime": {"victim_id": "v"},
                "persons": [{"person_id": "v", "name": "V", "role": "victim"}],
                "motives": [],
                "objects": [],
                "locations": [],
                "travel_rules": [],
                "evidence": [],
                "world_graph": {"locations": [], "placements": []},
            },
        }
    )
    assert dto["persons"][0] == {
        "personId": "v",
        "name": "V",
        "role": "victim",
        "affordances": [],
    }


# --------------------------------------------------------------------------- #
# 10 — provider-independent structured presentedData acceptance
# --------------------------------------------------------------------------- #


def test_presented_data_identity_keys_accepted_by_strict_parser():
    from fixtures.golden_generation import GOLDEN_FULL_DRAFT  # noqa: PLC0415

    for variant in ("female", "F", "woman", "male", "other", "unknown"):
        raw = json.loads(GOLDEN_FULL_DRAFT)
        for person in raw["persons"]:
            if person["role"] == "victim":
                person["presentedData"] = {
                    "age": "39",
                    "gender": variant,
                    "occupation": "CEO, Miller Consulting",
                    "background": "A short public note.",
                }
        draft = parse_full_draft(json.dumps(raw))
        assert draft is not None, variant
        victim = next(p for p in draft.persons if p.role == "victim")
        assert victim.presented_data["gender"] == variant


def test_transport_schema_keeps_presented_data_an_open_object():
    """Provider independence: every provider path (frontier/ollama/fake) derives
    its transport schema from the SAME contract — ``presentedData`` must stay an
    OPEN object so the strict parser remains the acceptance authority."""
    for stage in ("public_world", "repair"):
        schema = prompts.json_schema_for_stage_output(stage)
        assert schema is not None
        node = schema["properties"]["persons"]["items"]["properties"]["presentedData"]
        assert node["type"] == "object"
        assert node.get("additionalProperties") is True


# --------------------------------------------------------------------------- #
# victim-profile quality codes (blocking + repairable) + routing
# --------------------------------------------------------------------------- #


def _viol_public(presented, victim_id="v1", name="V One"):
    from app.domain.public import PublicPerson

    return PublicCase(
        case_id="C1",
        case_version=1,
        persons=(
            PublicPerson(
                person_id=victim_id,
                name=name,
                role="victim",
                public_affordances=frozenset({"VISIBLE_CHARACTER"}),
                presented_data=presented,
            ),
            PublicPerson(
                person_id="s1",
                name="S One",
                role="suspect",
                public_affordances=frozenset(
                    {"SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"}
                ),
            ),
        ),
        locations=(PublicLocation(location_id="loc1", name="Loc One"),),
        scene=PublicScene(location_id="loc1", name="Loc One"),
    )


def _viol_truth(victim_id="v1"):
    return CaseTruth(
        case_id="C1",
        case_version=1,
        title="t",
        crime=Crime(
            type="murder",
            victim_id=victim_id,
            murderer_id="s1",
            motive_id="m1",
            weapon_id="o1",
            location_id="loc1",
            crime_time=CrimeTime(
                canonical="2026-09-11T22:17:00+02:00",
                accusation_tolerance_seconds=300,
            ),
        ),
    )


def _body_found_fact(location_id="loc1", observed_at="2026-09-11T22:18:31+02:00"):
    return SimpleNamespace(
        id="body_found_01",
        kind="witness_observation",
        presentation={"title": "Body found"},
        propositions=(
            SimpleNamespace(
                type="BODY_FIRST_FOUND_AT",
                location_id=location_id,
                observed_at=observed_at,
                person_id=None,
            ),
        ),
        discoverable=True,
    )


def _viol_public_no_victim():
    from app.domain.public import PublicPerson

    return PublicCase(
        case_id="C1",
        case_version=1,
        persons=(
            PublicPerson(
                person_id="s1",
                name="S One",
                role="suspect",
                public_affordances=frozenset(
                    {"SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"}
                ),
            ),
        ),
        locations=(PublicLocation(location_id="loc1", name="Loc One"),),
        scene=PublicScene(location_id="loc1", name="Loc One"),
    )


def test_victim_profile_blocking_codes():
    # No victim person at all + a dangling truth victim id.
    codes = case_quality.validate_case_quality(
        _viol_public_no_victim(), _viol_truth("ghost"), (), ()
    )
    assert case_quality.VICTIM_MISSING in codes
    assert case_quality.VICTIM_REFERENCE_DANGLING in codes
    # Empty victim name.
    codes_empty = case_quality.validate_case_quality(
        _viol_public({}, name="   "), _viol_truth(), (), ()
    )
    assert case_quality.VICTIM_NAME_EMPTY in codes_empty
    # Invalid discovery location + time.
    bad_fact = _body_found_fact(location_id="ghost", observed_at="not-a-time")
    codes_discovery = case_quality.validate_case_quality(
        _viol_public({}), _viol_truth(), (bad_fact,), ()
    )
    assert case_quality.VICTIM_DISCOVERY_LOCATION_INVALID in codes_discovery
    assert case_quality.VICTIM_DISCOVERY_TIME_INVALID in codes_discovery


def test_victim_profile_repairable_identity_codes():
    # Partial identity: age + occupation present, gender missing.
    codes_missing_gender = case_quality.validate_case_quality(
        _viol_public({"age": 44, "occupation": "Chemist"}), _viol_truth(), (), ()
    )
    assert case_quality.VICTIM_GENDER_INVALID in codes_missing_gender
    # Missing age + occupation.
    codes_missing = case_quality.validate_case_quality(
        _viol_public({"gender": "female"}), _viol_truth(), (), ()
    )
    assert case_quality.VICTIM_AGE_INVALID in codes_missing
    assert case_quality.VICTIM_OCCUPATION_MISSING in codes_missing
    # Invalid age.
    codes_bad_age = case_quality.validate_case_quality(
        _viol_public({"age": 999, "gender": "female", "occupation": "Chemist"}),
        _viol_truth(),
        (),
        (),
    )
    assert case_quality.VICTIM_AGE_INVALID in codes_bad_age
    # A valid complete profile with a victim-linked body record passes.
    codes_ok = case_quality.validate_case_quality(
        _viol_public({"age": 44, "gender": "unknown", "occupation": "Chemist"}),
        _viol_truth(),
        (_body_found_fact(),),
        (),
    )
    assert case_quality.VICTIM_GENDER_INVALID not in codes_ok
    assert case_quality.VICTIM_AGE_INVALID not in codes_ok
    assert case_quality.VICTIM_OCCUPATION_MISSING not in codes_ok
    # DEF-115: a victim with NO presented_data (absent/empty) is an INCOMPLETE
    # profile and is now flagged (no longer a legacy escape hatch).
    codes_legacy = case_quality.validate_case_quality(
        _viol_public({}), _viol_truth(), (_body_found_fact(),), ()
    )
    for code in (
        case_quality.VICTIM_AGE_INVALID,
        case_quality.VICTIM_GENDER_INVALID,
        case_quality.VICTIM_OCCUPATION_MISSING,
    ):
        assert code in codes_legacy, code
    # A completely absent presented_data is equivalent to {}.
    codes_absent = case_quality.validate_case_quality(
        _viol_public(None), _viol_truth(), (_body_found_fact(),), ()
    )
    assert case_quality.VICTIM_AGE_INVALID in codes_absent
    assert case_quality.VICTIM_GENDER_INVALID in codes_absent
    assert case_quality.VICTIM_OCCUPATION_MISSING in codes_absent


def test_victim_profile_blocking_multiple_victim_persons():
    """DEF-118: more than one public role=='victim' person is a BLOCKING code."""
    from app.domain.public import PublicPerson

    public = PublicCase(
        case_id="C1",
        case_version=1,
        persons=(
            PublicPerson(
                person_id="v1",
                name="V One",
                role="victim",
                public_affordances=frozenset({"VISIBLE_CHARACTER"}),
                presented_data={"age": 40, "gender": "female", "occupation": "X"},
            ),
            PublicPerson(
                person_id="v2",
                name="V Two",
                role="victim",
                public_affordances=frozenset({"VISIBLE_CHARACTER"}),
                presented_data={"age": 41, "gender": "male", "occupation": "Y"},
            ),
        ),
        locations=(PublicLocation(location_id="loc1", name="Loc One"),),
        scene=PublicScene(location_id="loc1", name="Loc One"),
    )
    codes = case_quality.validate_case_quality(public, _viol_truth("v1"), (), ())
    assert case_quality.MULTIPLE_VICTIM_PERSONS in codes
    # A single victim never triggers it.
    single = _viol_public({"age": 44, "gender": "female", "occupation": "Chemist"})
    assert (
        case_quality.MULTIPLE_VICTIM_PERSONS
        not in case_quality.validate_case_quality(single, _viol_truth(), (), ())
    )


def test_non_discoverable_body_record_yields_no_discovery_block():
    """DEF-117: a non-discoverable body-found record must not back the public
    discovery block, and is a repairable quality defect."""
    payload = _payload(
        "v",
        [
            {
                "person_id": "v",
                "name": "V",
                "role": "victim",
                "presented_data": {"age": 44, "gender": "female", "occupation": "X"},
            }
        ],
        evidence=[
            {
                "id": "body_found_01",
                "discoverable": False,
                "presentation": {"title": "Body found"},
                "propositions": [
                    {
                        "type": "BODY_FIRST_FOUND_AT",
                        "location_id": "kitchen",
                        "observed_at": "2026-09-11T22:18:31+02:00",
                    }
                ],
            }
        ],
        placements=[{"object_id": "victim_body", "evidence_id": "body_found_01"}],
        locations=[{"location_id": "kitchen", "name": "Kitchen"}],
    )
    profile = pub.victim_profile_of(payload)
    assert profile is not None
    assert "discovery" not in profile
    assert "sceneObjectId" not in profile
    nondiscoverable_fact = SimpleNamespace(
        id="body_found_01",
        kind="witness_observation",
        presentation={"title": "Body found"},
        propositions=(
            SimpleNamespace(
                type="BODY_FIRST_FOUND_AT",
                location_id="kitchen",
                observed_at="2026-09-11T22:18:31+02:00",
                person_id=None,
            ),
        ),
        discoverable=False,
    )
    codes = case_quality.validate_case_quality(
        _viol_public({"age": 44, "gender": "female", "occupation": "X"}),
        _viol_truth(),
        (nondiscoverable_fact,),
        (),
    )
    assert case_quality.VICTIM_DISCOVERY_NOT_DISCOVERABLE in codes


def test_victim_profile_codes_are_routed_and_registered():
    from app.generation import validation_codes as vc

    all_codes = (
        case_quality.VICTIM_MISSING,
        case_quality.VICTIM_REFERENCE_DANGLING,
        case_quality.VICTIM_NAME_EMPTY,
        case_quality.VICTIM_DISCOVERY_LOCATION_INVALID,
        case_quality.VICTIM_DISCOVERY_TIME_INVALID,
        case_quality.VICTIM_DISCOVERY_NOT_DISCOVERABLE,
        case_quality.MULTIPLE_VICTIM_PERSONS,
        case_quality.VICTIM_OCCUPATION_MISSING,
        case_quality.VICTIM_AGE_INVALID,
        case_quality.VICTIM_GENDER_INVALID,
    )
    for code in all_codes:
        assert code in vc.VALIDATOR_CODE_VOCABULARY, code
        assert repair_routing.repair_owner_for_code(code) in (
            repair_routing.RepairOwner.CASE_PEOPLE,
            repair_routing.RepairOwner.CASE_TRUTH,
        )
    assert (
        repair_routing.repair_owner_for_code(case_quality.VICTIM_MISSING)
        is repair_routing.RepairOwner.CASE_TRUTH
    )
    assert (
        repair_routing.repair_owner_for_code(case_quality.VICTIM_NAME_EMPTY)
        is repair_routing.RepairOwner.CASE_PEOPLE
    )
    assert (
        repair_routing.repair_owner_for_code(case_quality.MULTIPLE_VICTIM_PERSONS)
        is repair_routing.RepairOwner.CASE_PEOPLE
    )


# --------------------------------------------------------------------------- #
# evidence completion guard — never invents a second victim identity
# --------------------------------------------------------------------------- #


def test_evidence_completion_uses_canonical_victim_identity():
    from app.domain.eligibility import derive_universes
    from app.generation.evidence_algebra import complete_evidence_if_ambiguous
    from fixtures.golden_generation import assemble_phase3, GOLDEN_FULL_DRAFT

    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    public, _evidence, truth = assemble_phase3(draft)
    # The completion algebra consumes the GENERATION evidence-spec shape.
    result = complete_evidence_if_ambiguous(public, truth.crime, draft.evidence)
    victim_id = truth.crime.victim_id
    victim_name = public.person(victim_id).name
    victim_person_ids = set()
    for fact in result.evidence.evidence:
        for prop in fact.propositions:
            if prop.type == "VICTIM_LAST_SEEN_ALIVE_AT":
                victim_person_ids.add(prop.person_id)
    # Every last-seen fact references the canonical victim id (never a new one).
    assert victim_person_ids <= {victim_id}, victim_person_ids
    assert victim_name  # the canonical public victim name resolves
    assert derive_universes(public)  # sanity: public model intact


# --------------------------------------------------------------------------- #
# DEF-115 — an AI-generated case with absent victim presentedData still publishes
# with a complete victim identity (CASE_PEOPLE repair + deterministic fallback)
# --------------------------------------------------------------------------- #


def _run_fake_generation(script: dict, prompt: str):
    from app.generation.admission import AdmissionController
    from app.generation.clock import ManualClock
    from app.generation.controller import GenerationController
    from app.generation.fake_provider import FakeProvider
    from app.generation.ids import IdSource

    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=FakeProvider(script=script),
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=1,
        max_full_regenerations=0,
        max_prompt_chars=4000,
        seed=11,
    )
    handle = controller.start_generation(
        prompt, anonymous_quota_session_id=session.session_id
    )
    return controller.attempt(handle.attempt_id)


def test_absent_victim_presented_data_publishes_with_complete_identity():
    """ADV-47-01 / DEF-115: a generated case whose PUBLIC_WORLD victim carries
    NO presentedData is repaired by the REPAIR stage and still publishes with a
    complete victim identity (name + age + gender + occupation)."""
    from app.services.publication import serialize_published_payload
    from fixtures.golden_generation import (
        GOLDEN_FULL_DRAFT,
        GOLDEN_STAGE_PAYLOADS,
    )

    public_world = json.loads(
        GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD]
    )
    for person in public_world["persons"]:
        if person.get("role") == "victim":
            person.pop("presentedData", None)
    script = {
        GenerationStage.CASE_TRUTH: [
            GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH]
        ],
        GenerationStage.PUBLIC_WORLD: [json.dumps(public_world)],
        GenerationStage.EVIDENCE: [
            GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE]
        ],
        GenerationStage.WORLD_GRAPH: [
            GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH]
        ],
        GenerationStage.REPAIR: [GOLDEN_FULL_DRAFT],
    }
    prompt = (
        "Victim: sarah_miller\nMurderer: thomas_reed\n"
        "Motive: cover_up_embezzlement\nWeapon: kitchen_knife\n"
        "Time: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
    )
    record = _run_fake_generation(script, prompt)
    from app.generation.state_machine import GenerationState

    assert record.state is GenerationState.PUBLISHED, record.reason
    assert record.published is not None
    # The absent identity triggered the CASE_PEOPLE repair pass (bounded).
    assert record.budget is not None and record.budget.repair_passes == 1
    payload = json.loads(
        serialize_published_payload(record.published, title="t")
    )
    victim_id = payload["draft"]["crime"]["victim_id"]
    victim = next(
        p for p in payload["draft"]["persons"] if p["person_id"] == victim_id
    )
    presented = victim["presented_data"]
    assert 1 <= int(presented["age"]) <= 120
    assert normalize_gender(presented["gender"]) in GENDER_VALUES
    assert isinstance(presented["occupation"], str) and presented["occupation"].strip()
    # The read projection renders the same complete identity.
    profile = pub.victim_profile_of(payload)
    assert profile is not None
    assert profile["age"] and profile["gender"] in GENDER_VALUES
    assert profile["occupation"]


def test_absent_victim_presented_data_flagged_repairable():
    """DEF-115 root cause guard: an absent/empty presented_data is flagged as a
    repairable victim identity defect (it no longer escapes validation)."""
    from app.generation.report import ValidationReport
    from app.generation.state_machine import ValidationOutcome

    codes = case_quality.validate_case_quality(
        _viol_public({}), _viol_truth(), (_body_found_fact(),), ()
    )
    assert set(codes) >= {
        case_quality.VICTIM_AGE_INVALID,
        case_quality.VICTIM_GENDER_INVALID,
        case_quality.VICTIM_OCCUPATION_MISSING,
    }
    report = ValidationReport(quality_issues=codes)
    assert report.outcome is ValidationOutcome.RECOVERABLE_REPAIR


# --------------------------------------------------------------------------- #
# DEF-116 — savegame record content keeps the victimRelated marker
# --------------------------------------------------------------------------- #


def test_savegame_exported_record_carries_victim_related(database_url):
    application = _make_app(database_url)
    try:
        payload = _golden_or_demo_payload(application)
        doc = sg.project_savegame_v1(payload, difficulty="medium")
        records = {r["evidenceId"]: r for r in doc["case"]["evidence"]}
        # last_seen_01 references the victim -> marker present in the archive.
        assert records["last_seen_01"]["content"]["victimRelated"] is True
        # A non-victim record omits the key (never false).
        assert "victimRelated" not in records["forensic_knife_match_01"]["content"]
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# DEF-119 — narrow top-level `victim` exemption in the pre-reveal leak scan
# --------------------------------------------------------------------------- #


def test_pre_reveal_scan_allows_only_top_level_victim_block():
    from test_phase7_helpers import assert_no_pre_reveal_material

    # The exact top-level bootstrap victim block is allowed.
    assert assert_no_pre_reveal_material({"victim": {"personId": "v"}}) == []
    # A NESTED ``victim`` key is still forbidden (truth-designating shape).
    for hostile in (
        {"wrapper": {"victim": {"personId": "v"}}},
        {"truth": {"victim": "someone"}},
        {"scene": {"victim": "x"}},
    ):
        try:
            assert_no_pre_reveal_material(hostile)
        except AssertionError:
            continue
        raise AssertionError(f"nested victim key was not flagged: {hostile!r}")
