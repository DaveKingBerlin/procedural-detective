"""Phase 11 — environment-aware generation integration (golden stability).

- default POST /cases (NO environment hint) publishes the APARTMENT kit: the
  stored payload's world graph is placement-identical to the provider golden,
  the scene gains the additive ``environment_id == "apartment"`` and the
  investigation bootstrap reports it;
- POST /cases {environment: "office"} publishes an OFFICE kit: scene
  environmentId == "office", the bootstrap carries the golden 9-object set
  placed on OFFICE anchors (per the Phase 10 interaction contract), the world
  graph locations are the office kit zones, and the solver still derives the
  golden truth (scene location id is solver-fixed: deduction unchanged);
- CaseTruth / evidence set stay identical across kits; unknown safe values
  fall back to apartment (provenance recorded internally).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.environments.manifests import load_all_environments
from app.generation.provider import GenerationStage
from app.generation.parser import parse_stage

from phase5_helpers import create_session

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ENVIRONMENTS_DIR = REPO_ROOT / "assets" / "environments"
DEV_MODE_CASE = BACKEND_DIR / "app" / "services" / "dev_mode_case.json"

GOLDEN_OBJECT_IDS = (
    "kitchen_knife",
    "letter_opener",
    "scissors",
    "vase_01",
    "apartment_laptop",
    "apartment_table",
    "apartment_door",
    "apartment_lamp",
    "victim_body_placeholder",
)

GOLDEN_PROMPT = "Victim: sarah_miller\nMurderer: thomas_reed\n"


def _payload(phase5_app, case_id):
    return json.loads(phase5_app.app.state.store.get_published(case_id, 1).payload_json)


def _bootstrap(phase5_app, client, case_id, creator, version=1):
    playthrough = client.post(
        f"/api/v1/cases/{case_id}/versions/{version}/playthroughs",
        headers={"Authorization": f"Bearer {creator}"},
    )
    assert playthrough.status_code == 201, playthrough.text
    pt = playthrough.json()
    return client.get(
        f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
        headers={"Authorization": f"Bearer {pt['playthroughAccessToken']}"},
    )


def _publish_case(phase5_app, client, environment=None):
    session_token, _ = create_session(client)
    payload = {"prompt": GOLDEN_PROMPT}
    if environment is not None:
        payload["environment"] = environment
    res = client.post(
        "/api/v1/cases", json=payload,
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["status"] == "PUBLISHED"
    return created["caseId"], created["creatorAccessToken"]


def _golden_placements():
    with open(DEV_MODE_CASE, encoding="utf-8") as handle:
        script = json.load(handle)
    return parse_stage(GenerationStage.WORLD_GRAPH, script["world_graph"][0])


def _draft_from_payload(draft_payload):
    """Rebuild the typed GeneratedDraft from the stored (snake_case) payload."""
    from app.generation.schemas import (
        CrimeSpec,
        CrimeTimeSpec,
        EvidenceSpec,
        GeneratedDraft,
        LocationSpec,
        MotiveSpec,
        ObjectSpec,
        PersonSpec,
        PropSpec,
        SceneSpec,
        TravelRuleSpec,
        WorldGraphSpec,
    )

    d = draft_payload
    return GeneratedDraft(
        crime=CrimeSpec(
            type=d["crime"]["type"],
            victim_id=d["crime"]["victim_id"],
            murderer_id=d["crime"]["murderer_id"],
            motive_id=d["crime"]["motive_id"],
            weapon_id=d["crime"]["weapon_id"],
            location_id=d["crime"]["location_id"],
            crime_time=CrimeTimeSpec(
                canonical=d["crime"]["crime_time"]["canonical"],
                accusation_tolerance_seconds=d["crime"]["crime_time"]["accusation_tolerance_seconds"],
            ),
        ),
        persons=tuple(
            PersonSpec(
                person_id=p["person_id"], name=p["name"], role=p["role"],
                affordances=tuple(p["affordances"]),
                presented_data=p.get("presented_data") or {},
            )
            for p in d["persons"]
        ),
        motives=tuple(
            MotiveSpec(motive_id=m["motive_id"], label=m["label"],
                       affordances=tuple(m["affordances"]))
            for m in d["motives"]
        ),
        objects=tuple(
            ObjectSpec(object_id=o["object_id"], asset_id=o["asset_id"],
                       affordances=tuple(o["affordances"]), subtype=o.get("subtype"))
            for o in d["objects"]
        ),
        locations=tuple(
            LocationSpec(location_id=l["location_id"], name=l["name"])
            for l in d["locations"]
        ),
        travel_rules=tuple(
            TravelRuleSpec(from_location_id=t["from_location_id"],
                           to_location_id=t["to_location_id"],
                           travel_time_seconds=t["travel_time_seconds"])
            for t in d["travel_rules"]
        ),
        scene=SceneSpec(location_id=d["scene"]["location_id"], name=d["scene"]["name"],
                        environment_id=d["scene"].get("environment_id")),
        evidence=tuple(
            EvidenceSpec(
                id=e["id"], kind=e["kind"], reliability=e.get("reliability"),
                discoverable=e.get("discoverable"),
                source_ref=e.get("source_ref"),
                presentation=e.get("presentation") or {},
                propositions=tuple(
                    PropSpec(
                        type=p["type"], person_id=p.get("person_id"),
                        location_id=p.get("location_id"), object_id=p.get("object_id"),
                        motive_id=p.get("motive_id"), observed_at=p.get("observed_at"),
                        uncertainty_seconds=p.get("uncertainty_seconds", 0),
                        structured=p.get("structured") or {},
                    )
                    for p in e["propositions"]
                ),
            )
            for e in d["evidence"]
        ),
        world_graph=WorldGraphSpec(),
    )


def _solve_payload(phase5_app, case_id):
    payload = _payload(phase5_app, case_id)
    draft = _draft_from_payload(payload["draft"])
    from app.generation.pipeline import _draft_to_phase3
    from app.domain.solver import solve_case
    from app.validation.solution import evaluate_solution

    public, facts, truth = _draft_to_phase3(draft, case_id="CASE-X", title="T")
    proof = solve_case(public, facts)
    validation = evaluate_solution(proof, truth)
    return proof, validation


def test_default_case_publishes_apartment_kit_with_golden_set(phase5_migrated_client):
    """Golden stability: no hint -> apartment kit, provider-identical world
    graph (placement-for-placement), bootstrap carries the 9-object set."""
    client = phase5_migrated_client
    case_id, creator = _publish_case(phase5_migrated_client, client)
    payload = _payload(phase5_migrated_client, case_id)
    scene = payload["draft"]["scene"]
    assert scene["environment_id"] == "apartment"
    assert scene["location_id"] == "miller_apartment_kitchen"
    assert scene["name"] == "Miller Apartment - Kitchen"

    golden = _golden_placements()
    wg = payload["draft"]["world_graph"]

    def _loc_key(l):
        return (l["location_id"], l["template"], tuple(l["rooms"]))

    assert [_loc_key(l) for l in wg["locations"]] == [
        (l.location_id, l.template, l.rooms) for l in golden.locations
    ]

    def _pl_key(p):
        return (p["object_id"], p["asset_id"], p["location_id"], p["anchor"],
                p["interaction"], p.get("evidence_id"))

    assert [_pl_key(p) for p in wg["placements"]] == [
        (p.object_id, p.asset_id, p.location_id, p.anchor, p.interaction, p.evidence_id)
        for p in golden.placements
    ]

    boot = _bootstrap(phase5_migrated_client, client, case_id, creator)
    assert boot.status_code == 200
    body = boot.json()
    assert body["scene"]["environmentId"] == "apartment"
    object_ids = sorted(o["objectId"] for o in body["scene"]["worldObjects"])
    assert object_ids == sorted(GOLDEN_OBJECT_IDS)
    # solver unchanged
    proof, validation = _solve_payload(phase5_migrated_client, case_id)
    assert proof.who.winner == "thomas_reed"
    assert validation.all_true is True


def test_office_case_publishes_office_kit_placed_on_office_anchors(
    phase5_migrated_client,
):
    """POST /cases {environment: "office"} -> an office kit whose scene
    environmentId == "office", world graph on OFFICE anchors/zones and the
    golden 9-object set in the bootstrap."""
    client = phase5_migrated_client
    # golden baseline (no hint) published FIRST for the truth comparison
    golden_case_id, _gcreator = _publish_case(phase5_migrated_client, client)
    golden_pub = _payload(phase5_migrated_client, golden_case_id)

    case_id, creator = _publish_case(phase5_migrated_client, client, environment="office")
    payload = _payload(phase5_migrated_client, case_id)
    kits = load_all_environments(directory=ENVIRONMENTS_DIR)
    office = next(k for k in kits if k.environment_id == "office")
    office_anchors = {a.anchor_id for a in office.anchors}
    office_zones = {z.zone_id for z in office.zones}

    scene = payload["draft"]["scene"]
    assert scene["environment_id"] == "office"
    # solver-critical scene location is unchanged
    assert scene["location_id"] == "miller_apartment_kitchen"

    wg = payload["draft"]["world_graph"]
    assert len(wg["placements"]) == 9
    assert len(wg["locations"]) == len(office.zones)
    for location in wg["locations"]:
        assert location["location_id"] in office_zones
    for placement in wg["placements"]:
        assert placement["anchor"] in office_anchors
        assert placement["location_id"] in office_zones

    interface = {
        "kitchen_knife": ("inspect", "forensic_knife_match_01"),
        "letter_opener": ("inspect", "forensic_letter_opener_01"),
        "scissors": ("inspect", "forensic_scissors_01"),
        "apartment_laptop": ("read", "email_thomas_01"),
        "vase_01": ("", None),
        "apartment_table": ("", None),
        "apartment_door": ("", None),
        "apartment_lamp": ("", None),
        # ADV-222: the victim body is evidence-linked to the time-bearing
        # body_found_01 record (discoverable WHEN fact on a placed object).
        "victim_body_placeholder": ("inspect", "body_found_01"),
    }
    by_object = {p["object_id"]: p for p in wg["placements"]}
    assert set(by_object) == set(GOLDEN_OBJECT_IDS)
    for object_id, (interaction, evidence_id) in interface.items():
        assert by_object[object_id]["interaction"] == interaction
        assert by_object[object_id].get("evidence_id") == evidence_id

    boot = _bootstrap(phase5_migrated_client, client, case_id, creator)
    assert boot.status_code == 200
    body = boot.json()
    assert body["scene"]["environmentId"] == "office"
    object_ids = sorted(o["objectId"] for o in body["scene"]["worldObjects"])
    assert object_ids == sorted(GOLDEN_OBJECT_IDS)
    # solver unchanged: same evidence set + unchanged scene location
    proof, validation = _solve_payload(phase5_migrated_client, case_id)
    assert proof.who.winner == "thomas_reed"
    assert proof.why.winner == "cover_up_embezzlement"
    assert proof.weapon.winner == "kitchen_knife"
    assert validation.all_true is True
    # CaseTruth + evidence identical to the golden (published payload)
    assert payload["truth"]["crime"] == golden_pub["truth"]["crime"]
    assert payload["draft"]["evidence"] == golden_pub["draft"]["evidence"]


def test_unknown_environment_resolves_to_apartment_fallback(phase5_migrated_client):
    """Safe unknown environment values publish the fallback apartment kit with
    provenance FALLBACK (internally recorded)."""
    client = phase5_migrated_client
    case_id, creator = _publish_case(
        phase5_migrated_client, client, environment="greenhouse by the lake"
    )
    payload = _payload(phase5_migrated_client, case_id)
    assert payload["draft"]["scene"]["environment_id"] == "apartment"
    boot = _bootstrap(phase5_migrated_client, client, case_id, creator)
    assert boot.json()["scene"]["environmentId"] == "apartment"
    service = phase5_migrated_client.app.state.generation_service
    resolution = service._last_environment_resolution
    assert resolution is not None
    assert resolution["environmentId"] == "apartment"
    assert resolution["provenance"] == "FALLBACK"
    assert resolution["compositionFailed"] is False


def test_mansion_case_publishes_mansion_kit(phase5_migrated_client):
    """Live-check: {environment: "mansion"} -> mansion kit with the 9-object
    set placed on mansion anchors (bootstrap + solver unchanged)."""
    client = phase5_migrated_client
    case_id, creator = _publish_case(
        phase5_migrated_client, client, environment="mansion"
    )
    payload = _payload(phase5_migrated_client, case_id)
    kits = load_all_environments(directory=ENVIRONMENTS_DIR)
    mansion = next(k for k in kits if k.environment_id == "mansion")
    mansion_anchors = {a.anchor_id for a in mansion.anchors}
    scene = payload["draft"]["scene"]
    assert scene["environment_id"] == "mansion"
    assert scene["location_id"] == "miller_apartment_kitchen"
    wg = payload["draft"]["world_graph"]
    assert len(wg["placements"]) == 9
    for placement in wg["placements"]:
        assert placement["anchor"] in mansion_anchors
    boot = _bootstrap(phase5_migrated_client, client, case_id, creator)
    assert boot.json()["scene"]["environmentId"] == "mansion"
    object_ids = sorted(o["objectId"] for o in boot.json()["scene"]["worldObjects"])
    assert object_ids == sorted(GOLDEN_OBJECT_IDS)
    proof, validation = _solve_payload(phase5_migrated_client, case_id)
    assert proof.who.winner == "thomas_reed"
    assert validation.all_true is True