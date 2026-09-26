"""Phase 19C — object-interaction / evidence UX regression suite.

Covers the Phase 19C mandate end-to-end:

1. evidence-linked object interaction discovers evidence (laptop -> email,
   knife -> forensic) and the interaction DTO contains ONLY the newly
   player-known evidence (pre-reveal leak scan);
2. repeated interaction is idempotent (second returns state
   "already-discovered" and never double-marks);
3. non-evidence interact feedback (backend contract, §3):
   - EVERY player-visible SEMANTIC object is inspectable (Phase 19F): a
     DECORATIVE placement (vase; published interaction "") answers 200 with
     the neutral inspection ``{relevant:false, label}`` and no state change
     (the Phase 19F universal-inspection rule supersedes DEF-062's plain 409
     dead-end for visible semantic placements);
   - a NON-DECORATIVE interactable-but-no-evidence placement answers 200 with
     ``discovery: null`` and ``evidenceId: null`` (the frontend renders the
     "Nothing relevant was found on <X>." feedback);
4. undiscovered evidence stays ABSENT from bootstrap/PublicCase/interact
   (the existing leak scanners run over the full interact path);
5. direct evidence discovery bypass remains impossible (unknown/unreachable
   evidence id -> 404 generic, no state change);
6. WHEN (time-of-crime) derivability for Easy / Medium / Hard: a discoverable
   time-bearing evidence fact set exists whose timeline derives the canonical
   WHEN and the reveal timeline shows it;
7. the OLLAMA DRIVER never publishes a dead-end interactable: every placement
   with a non-empty interaction resolves to a real evidence association, or is
   published DECORATIVE (interaction "") — the 19C batch-root-carrier rule;
8. accusation/reveal stay deterministic and leak no CaseTruth after the
   interact flow.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.domain.solver import solve_case  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.publication import (  # noqa: E402
    placement_for_object,
    project_world_objects,
    serialize_published_payload,
)
from app.services.reveal import timeline_of  # noqa: E402
from phase5_helpers import (  # noqa: E402
    auth,
    assert_no_hidden_leaks,
    assert_sanitized_error,
)
from phase6_helpers import (  # noqa: E402
    BODY_OBJECT,
    KNIFE_OBJECT,
    LAPTOP_OBJECT,
    EMAIL_EVIDENCE,
    SCENE_LOCATION,
    case_for,
    client,
    interact,
    playthrough,
)
from test_ollama_driver import _run, _staged, _alog_posts  # noqa: E402
from test_phase7_helpers import (  # noqa: E402
    accuse_then_reveal,
    assert_no_pre_reveal_material,
    assert_no_reveal_internal_material,
    new_playthrough,
    truth_bundle,
    winning_body,
)

# --------------------------------------------------------------------------- #
# 1/2/3/4/5/8 — the golden (fake) published path through the real API
# --------------------------------------------------------------------------- #


def _bootstrap(app, pt_id, pt_token):
    with client(app) as c:
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
        )
        assert res.status_code == 200, res.json()
        return res.json()


def _published_payload(app, case_id, version=1):
    row = app.state.store.get_published(case_id, version)
    assert row is not None
    return json.loads(row.payload_json)


def test_1_evidence_linked_interaction_discovers_and_dto_is_lean(phase5_app):
    """Laptop -> email and knife -> forensic discover on interact; the DTO
    carries ONLY the newly player-known evidence (lean + pre-reveal clean)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200
    body = res.json()
    assert body["objectId"] == LAPTOP_OBJECT
    assert body["interaction"] == "read"
    assert body["evidenceId"] == EMAIL_EVIDENCE
    assert body["result"] == "interacted"
    assert body["discovery"]["state"] == "discovered"
    assert body["discovery"]["evidenceId"] == EMAIL_EVIDENCE
    assert body["discovery"]["kind"] == "email"
    assert body["discovery"]["interaction"] == "read"
    assert set(body) == {
        "objectId",
        "interaction",
        "evidenceId",
        "discovery",
        "result",
        "inspection",  # Phase 19F additive: {relevant: true, label}
    }
    assert body["inspection"] == {"relevant": True, "label": "Apartment Laptop"}
    assert set(body["discovery"]) == {"evidenceId", "kind", "title", "interaction", "state"}
    assert_no_pre_reveal_material(body, canonical_time="2026-09-11T22:17:00+02:00")

    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    knife = res.json()
    assert knife["evidenceId"] == "forensic_knife_match_01"
    assert knife["discovery"]["kind"] == "forensic"
    assert knife["discovery"]["state"] == "discovered"
    assert knife["discovery"]["evidenceId"] == "forensic_knife_match_01"
    assert_no_pre_reveal_material(knife, canonical_time="2026-09-11T22:17:00+02:00")
    # only the two discovered ids are player-known now.
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ("email_thomas_01", "forensic_knife_match_01")


def test_2_repeated_interaction_is_idempotent(phase5_app):
    """Second interact on the same object returns state already-discovered and
    never double-marks / duplicates knowledge."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    first = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert first.status_code == 200
    assert first.json()["discovery"]["state"] == "discovered"

    second = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert second.status_code == 200
    body = second.json()
    assert body["discovery"]["state"] == "already-discovered"
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ("email_thomas_01",)
    assert len(snap.discovered) == 1  # never double-marked


def test_3_decorative_placement_returns_neutral_inspection_no_state_change(phase5_app):
    """vase_01 (published interaction "") is a player-VISIBLE semantic object,
    so Phase 19F makes it inspectable: ANY requested interaction answers 200
    with the neutral inspection ``{relevant: false, label: "Vase"}``, NO
    evidence is invented and NO knowledge is discovered. (This supersedes the
    DEF-062 409 for VISIBLE semantic placements; the no-leak semantics stay —
    a neutral inspection never reveals evidence/truth.)"""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    for requested in ("inspect", "read", "open"):
        res = interact(phase5_app, pt_id, pt_token, "vase_01", requested)
        assert res.status_code == 200, (requested, res.json())
        body = res.json()
        assert body["objectId"] == "vase_01"
        assert body["interaction"] == ""  # published decorative interaction
        assert body["evidenceId"] is None
        assert body["discovery"] is None
        assert body["result"] == "interacted"
        assert body["inspection"] == {"relevant": False, "label": "Vase"}
        assert_sanitized_error(res.text)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    # the inspection is a successful interaction: the location is visited.
    assert snap.visited == (SCENE_LOCATION,)


def test_3b_non_decorative_interactable_without_evidence_200_nothing_found(phase5_app):
    """A non-decorative placement with NO evidence association answers 200 with
    discovery:null and evidenceId:null — the frontend renders the Phase 19C
    "Nothing relevant was found on <X>." feedback (backend contract §3)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    # Craft a v2 published world carrying an informational interactable
    # (interaction "inspect", no evidence) so the pinned playthrough sees it.
    # NOTE (Phase 19F fail-closed): the crafted object uses a REGISTERED
    # render asset (PROP_BOTTLE_01) so the placement IS player-visible — an
    # object with an unregistered/unprojectable asset would have NO
    # player-visible representation and would answer 404, never 200.
    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())
    payload = _published_payload(phase5_app, case_id, 1)
    payload["caseVersion"] = 2
    payload["publishedAt"] = now
    payload["draft"]["objects"].append(
        {
            "object_id": "info_globe_01",
            "asset_id": "PROP_BOTTLE_01",
            "affordances": ["INSPECTABLE"],
            "subtype": "decor",
        }
    )
    payload["draft"]["world_graph"]["placements"].append(
        {
            "object_id": "info_globe_01",
            "asset_id": "PROP_BOTTLE_01",
            "location_id": "miller_apartment_kitchen",
            "anchor": "shelf_01",
            "interaction": "inspect",
            "evidence_id": None,
        }
    )
    store.create_case_version(
        case_id=case_id, version=2, state="PUBLISHED", generation_id="GEN-2", created_at=now
    )
    store.insert_published(
        case_id=case_id,
        case_version=2,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )
    pt2_id, pt2_token = playthrough(phase5_app, case_id, creator, version=2)

    res = interact(phase5_app, pt2_id, pt2_token, "info_globe_01", "inspect")
    assert res.status_code == 200
    body = res.json()
    assert body["objectId"] == "info_globe_01"
    assert body["interaction"] == "inspect"
    assert body["evidenceId"] is None
    assert body["discovery"] is None
    assert body["result"] == "interacted"
    assert set(body) == {
        "objectId",
        "interaction",
        "evidenceId",
        "discovery",
        "result",
        "inspection",  # Phase 19F additive: {relevant: false, label}
    }
    assert body["inspection"] == {"relevant": False, "label": "Info Globe"}
    assert_no_pre_reveal_material(body, canonical_time="2026-09-11T22:17:00+02:00")
    # no knowledge mutated, no leak, no crash
    snap = phase5_app.state.store.snapshot_player_knowledge(pt2_id)
    assert snap.discovered == ()


def test_4_undiscovered_evidence_absent_from_bootstrap_and_interact(phase5_app):
    """Deep leak scan over bootstrap + the interact DTO: no hidden material,
    no undiscovered evidence CONTENT (propositions/timestamps/sourceRef) and
    no raw evidence structural markers anywhere in the investigation flight.
    The PublicCase dossier intentionally carries the frozen Phase 5 evidence
    presentations (REQUIREMENTS 41.2 / test_phase5_cases_api pins len==16) —
    here we run the SAME existing leak scanners over it (no truth/keys)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    payload = _published_payload(phase5_app, case_id, 1)
    # Public candidate-universe ids (Phase 7 J/K) may legitimately coincide
    # with evidence presentation VALUES (e.g. fromPersonId == suspect id) —
    # those are PUBLIC universe material, not undiscovered evidence content.
    universes = payload.get("universes") or {}
    public_ids = {
        str(i)
        for list_key in ("suspect_ids", "motive_ids", "weapon_ids")
        for i in (universes.get(list_key) or ())
    }
    # Phase 23: the bootstrap ``witnesses`` block legitimately echoes the
    # PUBLIC witness person identity (id + display name) — the same class as
    # the candidate-universe ids: player-safe world material, never
    # undiscovered evidence content. Only role=="witness" persons are
    # exempted; the forbidden-set semantics are unchanged for everyone else.
    public_ids |= {
        str(person.get("person_id"))
        for person in payload["draft"].get("persons") or ()
        if str(person.get("role")) == "witness" and person.get("person_id") is not None
    }
    public_names = {
        str(person.get("name"))
        for person in payload["draft"].get("persons") or ()
        if str(person.get("role")) == "witness"
        and isinstance(person.get("name"), str)
        and person.get("name")
    }
    forbidden_strings = []
    for fact in payload["draft"]["evidence"]:
        presentation = fact.get("presentation") or {}
        for value in presentation.values():
            if isinstance(value, str) and value:
                if value in public_ids or value in public_names:
                    continue  # public witness identity / candidate id
                forbidden_strings.append(value)

    # bootstrap (player-facing investigation flight): NEVER any evidence
    # presentation content (same deep scan as Phase 6 O13).
    boot = _bootstrap(phase5_app, pt_id, pt_token)
    assert_no_hidden_leaks(boot)
    boot_text = json.dumps(boot, sort_keys=True)
    for needle in forbidden_strings:
        assert needle not in boot_text, f"undiscovered evidence content leaked in bootstrap: {needle!r}"
    assert "propositions" not in boot_text
    assert "sourceRef" not in boot_text
    assert "observedAt" not in boot_text

    # PublicCase: the existing leak scanner (Phase 5 INVARIANT 5) — no hidden
    # truth keys, no canonical designation, no propositions anywhere.
    with client(phase5_app) as c:
        res = c.get(f"/api/v1/cases/{case_id}", headers=auth(creator))
        assert res.status_code == 200
        pub = res.json()
    assert set(pub) >= {"caseId", "caseVersion", "worldGraph", "evidence"}
    assert_no_hidden_leaks(pub)
    assert "propositions" not in json.dumps(pub, sort_keys=True)
    assert "sourceRef" not in json.dumps(pub, sort_keys=True)

    # interact DTO (lean on discovery content; title is now player-known)
    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200
    dto = res.json()
    assert_no_pre_reveal_material(dto, canonical_time="2026-09-11T22:17:00+02:00")
    # only the discovered evidence's own title may appear — every OTHER
    # presentation string must stay away from the interact response.
    dto_text = json.dumps(dto, sort_keys=True)
    for needle in forbidden_strings:
        if needle == dto["discovery"]["title"]:
            continue  # the newly player-known evidence title is legitimately there
        assert needle not in dto_text, f"other undiscovered evidence leaked in interact DTO: {needle!r}"

    # after discovery the read-knowledge shows exactly one id
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (EMAIL_EVIDENCE,)


def test_5_direct_discovery_route_removed(phase5_app):
    """Phase 20 (PD-SEC-01): the DIRECT discovery route
    ``POST /evidence/{id}/discover`` has been REMOVED for normal player
    discovery — it answers 404 for ANY id (even a placement-reachable one),
    sanitized, with NO state change. Discovery happens ONLY through a
    validated ``POST /objects/{object_id}/interact`` (phase19c tests 1-3)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    with client(phase5_app) as c:
        for evil in ("ghost_evidence_99", "truth", "murdererId", "cctv_michael_office_01"):
            res = c.post(
                f"/api/v1/playthroughs/{pt_id}/evidence/{evil}/discover",
                headers=auth(pt_token),
            )
            assert res.status_code == 404, (evil, res.json())
            body = res.json()
            assert body["error"]["code"] == "NOT_FOUND"
            assert_sanitized_error(res.text)
        # The route is gone even for a REAL placement-reachable evidence id:
        # without a preceding world interaction nothing can ever be discovered
        # by id.
        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/evidence/forensic_knife_match_01/discover",
            headers=auth(pt_token),
        )
        assert res.status_code == 404, res.json()
        assert res.json()["error"]["code"] == "NOT_FOUND"
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.visited == ()


def test_8_accusation_reveal_deterministic_no_case_truth_leak_after_interact(phase5_app):
    """After the laptop + knife interact flow, accusation/reveal stay
    deterministic and the reveal DTO carries no solver/provider/token material
    (N25/N26-style scan)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200

    truth = truth_bundle(phase5_app, case_id, 1)
    body = winning_body(truth)
    reveal = accuse_then_reveal(phase5_app, pt_id, pt_token, body)
    assert reveal["status"] == "REVEALED"
    assert reveal["score"]["correctDimensions"] == 4
    assert reveal["result"]["murdererCorrect"] is True
    assert reveal["result"]["timeCorrect"] is True
    assert_no_reveal_internal_material(reveal)
    # deterministic: a fresh playthrough on the SAME case yields the SAME truth.
    pt2_id, pt2_token = new_playthrough(phase5_app, case_id, creator)
    reveal2 = accuse_then_reveal(phase5_app, pt2_id, pt2_token, winning_body(truth))
    assert reveal2["status"] == "REVEALED"
    assert reveal2["truth"]["murdererId"] == reveal["truth"]["murdererId"]


# --------------------------------------------------------------------------- #
# 6 — WHEN (time-of-crime) derivability for Easy / Medium / Hard
# --------------------------------------------------------------------------- #


def _when_support_ok_typed(
    draft: Any, canonical: str
) -> bool:
    """True when a TYPED GeneratedDraft carries a discoverable, time-bearing
    evidence fact set whose propositions deterministically derive ``canonical``.
    ``draft`` is the same artifact the publication serializer freezes (a
    ``GeneratedDraft``); the golden dev-mode case is byte-identical to the
    fake-provider published payload (proven by test_generation_roundtrip)."""
    from app.domain.time_interval import parse_iso8601_to_epoch
    from app.generation.pipeline import _draft_to_phase3

    p, e, _truth = _draft_to_phase3(draft, case_id="when", title="t")
    proof = solve_case(p, e)
    tick = parse_iso8601_to_epoch(canonical)
    return (
        not proof.when.ambiguous
        and not proof.when.overconstrained
        and proof.when.feasible.contains(tick)
    )


def _timeline_entries(payload: dict) -> list[dict]:
    return timeline_of(payload)


def test_6_when_derivability_easy_golden(phase5_app):
    """EASY (golden apartment/22:17): last_seen/body_found/noise_heard are
    discoverable, derive the canonical WHEN and the reveal timeline shows a
    time-bearing entry."""
    from test_generation_roundtrip import _draft_from_stages

    case_id, creator = case_for(phase5_app)
    payload = _published_payload(phase5_app, case_id, 1)
    facts = {f["id"]: f for f in payload["draft"]["evidence"]}
    for when_id in ("last_seen_01", "body_found_01", "noise_heard_01"):
        assert when_id in facts, f"missing golden when-evidence {when_id}"
        assert facts[when_id].get("discoverable") is not False
    # the PUBLISHED draft is byte-identical to the typed golden draft; solve
    # the canonical WHEN from it and assert it contains 22:17.
    assert _when_support_ok_typed(_draft_from_stages(), "2026-09-11T22:17:00+02:00")
    entries = _timeline_entries(payload)
    assert entries, "golden reveal timeline is empty"
    strings = {e["description"] for e in entries}
    assert any("Neighbour saw Sarah alive" in s for s in strings)
    assert any("Body found" in s for s in strings)
    assert any("struggle" in s for s in strings)


def test_6_when_derivability_medium_driver_hotel_knife():
    """MEDIUM (driver hotel_suite kitchen-knife / 21:18): the canonical
    time-bearing algebra (d_ev_when_last_seen/body/obs) is discoverable,
    derives 21:18 and appears in the reveal timeline."""
    from test_ollama_driver import _case_people, _evidence, _j, _world, _alog_posts

    cp = _case_people(weapon="kitchen_knife")
    cp["crime"]["crimeTime"] = {
        "canonical": "2026-09-11T21:18:00+02:00",
        "accusationToleranceSeconds": 300,
    }
    posts = [
        _j(cp),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T21:18:00+02:00'),
        _j(_world("kitchen knife")),
    ]
    prompt = (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
        "Weapon: kitchen knife\nTime: 21:18\nWitness: Lisa K\u00f6nig\n"
        "Location: hotel suite\n"
    )
    record, _transport = _run(posts, prompt=prompt)
    assert record.state is GenerationState.PUBLISHED, record.deferred_structural
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="medium", model="mock", title="Medium"
        )
    )
    ids = {f["id"] for f in payload["draft"]["evidence"]}
    assert {"d_ev_when_last_seen", "d_ev_when_body", "d_ev_when_obs"} <= ids
    # the serialized draft is exactly the record's typed draft; solve WHEN.
    assert _when_support_ok_typed(record.published.draft, "2026-09-11T21:18:00+02:00")
    entries = _timeline_entries(payload)
    assert entries
    assert any("Body discovered" in e["description"] for e in entries)


def test_6_when_derivability_hard_driver_office_icepick():
    """HARD (driver office bronze-ice-pick / 23:42): canonical time-bearing
    algebra derives 23:42 and appears in the reveal timeline."""
    record, _transport = _run(_staged())  # the established MockOllamaTransport run
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="hard", model="mock", title="Hard"
        )
    )
    ids = {f["id"] for f in payload["draft"]["evidence"]}
    assert {"d_ev_when_last_seen", "d_ev_when_body", "d_ev_when_obs"} <= ids
    assert _when_support_ok_typed(record.published.draft, "2026-09-11T23:42:00+02:00")
    entries = _timeline_entries(payload)
    assert entries
    assert any("Body discovered" in e["description"] for e in entries)


# --------------------------------------------------------------------------- #
# 7 — the Ollama DRIVER never publishes a dead-end interactable
# --------------------------------------------------------------------------- #


def _driver_dead_end_report(payload: dict) -> list[tuple[str, str]]:
    """(objectId, interaction) placements published interactable but with NO
    evidence association — the Phase 19C dead-end class."""
    placements = payload["draft"]["world_graph"]["placements"]
    return [
        (str(p.get("object_id")), str(p.get("interaction") or ""))
        for p in placements
        if (p.get("interaction") or "") and p.get("evidence_id") is None
    ]


def test_7_driver_world_never_publishes_dead_end_interactables():
    """The general root-cause fix: EVERY driver-generated kit publishes every
    interactable placement with a real evidence association; the LAPTOP is
    the world's activity-log device (ADV-222) and every unresolved evidence
    placement publishes with a real record or DECORATIVE (interaction "")."""
    from app.services.ollama_driver import _first_evidence_referencing_object

    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="p19c", model="mock", title="P19C"
        )
    )
    report = _driver_dead_end_report(payload)
    assert report == [], f"driver published dead-end interactables: {report}"

    placements = {p["object_id"]: p for p in payload["draft"]["world_graph"]["placements"]}
    # the sharp weapons resolve to their REAL canonical forensic records.
    assert placements["kitchen_knife"]["evidence_id"] == "d_ev_weapon_false_kitchenknife"
    assert placements["letter_opener"]["evidence_id"] == "d_ev_weapon_false_letteropener"
    assert placements["scissors"]["evidence_id"] == "d_ev_weapon_false_scissors"
    # the LOCKED weapon keeps its sealed match record.
    assert placements["bronze_ceremonial_ice_pick"]["evidence_id"] == "d_ev_weapon_true"
    assert placements["kitchen_knife"]["interaction"] == "inspect"
    # ADV-222: the laptop is the driver world's DEVICE / activity-log anchor.
    # Its golden email association (email_thomas_01) does not survive into any
    # driver world, so the general rule re-binds it to the canonical scene
    # activity-log record (d_ev_when_obs, CRIME_SCENE_OBSERVATION_AT) — a
    # TIME-BEARING fact a player can discover by reading the laptop. WHEN is
    # thus derivable from discoverable evidence in every driver world (the
    # defensive fallback INTERACTS with the never-a-dead-end rule: the
    # laptop never publishes as an interactable-but-evidence-less placement).
    assert placements["apartment_laptop"]["interaction"] == "read"
    assert placements["apartment_laptop"]["evidence_id"] == "d_ev_when_obs"
    assert "apartment_laptop" in {o["object_id"] for o in payload["draft"]["objects"]}


def test_7b_projection_never_decorates_a_known_informational_object():
    """The projection keeps an explicitly-requested informational object
    interactable (interaction kept, evidence id dropped) — the existing
    phase17d trophy contract stays intact (no blanket decoration)."""
    from app.generation.parser import parse_stage
    from app.generation.provider import GenerationStage
    from app.generation.schemas import PlacementSpec
    from app.services.ollama_driver import _project_placement_evidence

    evidence_doc = {
        "evidence": [
            {
                "id": "ev-weapon-01",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "forensic", "sourceId": "s"},
                "propositions": [
                    {
                        "type": "OBJECT_CONTAINS_FINGERPRINT",
                        "objectId": "bronze_ceremonial_ice_pick",
                        "personId": "paul_becker",
                        "uncertaintySeconds": 0,
                        "structured": {},
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            }
        ]
    }
    evidence_spec = parse_stage(GenerationStage.EVIDENCE, json.dumps(evidence_doc), non_throwing=False)
    placements = [
        PlacementSpec(
            object_id="bronze_ceremonial_ice_pick",
            asset_id="proc.decor.abcdef0123456789",
            location_id="office", anchor="office_desk_a",
            interaction="", evidence_id="weapon_id: bronze_ceremonial_ice_pick",
        ),
        PlacementSpec(
            object_id="trophy",
            asset_id="PROP_TROPHY",
            location_id="office", anchor="office_meeting_table",
            interaction="inspect", evidence_id=None,
        ),
    ]
    weapon, trophy = _project_placement_evidence(placements, evidence_spec, "ev-weapon-01")
    assert weapon.evidence_id == "ev-weapon-01"
    assert weapon.interaction == "inspect"
    assert trophy.evidence_id is None
    assert trophy.interaction == "inspect"  # informational object is preserved


def test_7c_first_evidence_referencing_object_is_deterministic():
    """The rebinding helper resolves existing canonical evidence by object
    reference deterministically and returns None when nothing references."""
    from app.services.ollama_driver import _first_evidence_referencing_object
    from test_ollama_driver import _j
    from app.generation.parser import parse_stage
    from app.generation.provider import GenerationStage

    doc = {
        "evidence": [
            {
                "id": "d_ev_weapon_false_kitchenknife",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "r", "sourceId": "s"},
                "propositions": [
                    {
                        "type": "FORENSIC_WEAPON_MATCH",
                        "objectId": "kitchen_knife",
                        "structured": {"match": False},
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            }
        ]
    }
    spec = parse_stage(GenerationStage.EVIDENCE, _j(doc), non_throwing=False)
    assert _first_evidence_referencing_object(spec, "kitchen_knife") == "d_ev_weapon_false_kitchenknife"
    assert _first_evidence_referencing_object(spec, "Kitchen Knife") == "d_ev_weapon_false_kitchenknife"
    assert _first_evidence_referencing_object(spec, "apartment_laptop") is None
    assert _first_evidence_referencing_object(spec, "") is None
    assert _first_evidence_referencing_object(spec, None) is None


__all__ = []  # pytest module: no accidental public names