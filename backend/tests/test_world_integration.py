"""Phase 14 — prompt-to-world API integration tests.

End-to-end: POST /cases with the five showcase prompts (no explicit
environment) publishes a kit world whose environment + prompt-specific objects
are visible in the investigation bootstrap, whose knife/laptop evidence is
clickable and whose solver proof stays TERMINALLY true. Also regression: the
DEFAULT GOLDEN APARTMENT stays byte-identical, explicit environment hints keep
working, catalog mutation does not change a published world, and re-publication
(v2) leaves v1 byte-identical. No network, in-process only.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.solver import solve_case  # noqa: E402
from app.environments.manifests import load_all_environments  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.parser import parse_stage  # noqa: E402
from app.validation.solution import evaluate_solution  # noqa: E402

from fixtures.golden import truth_variant_a, truth_variant_b  # noqa: E402
from fixtures.world_showcase import (  # noqa: E402
    GOLDEN_DEFAULT_PROMPT,
    MANSION_PROMPT,
    SHOWCASE_EXPECTED,
    SHOWCASE_PROMPTS,
)
from phase5_helpers import create_session, create_playthrough  # noqa: E402

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


def _payload(phase5_app, case_id, version=1):
    return json.loads(
        phase5_app.app.state.store.get_published(case_id, version).payload_json
    )


def _bootstrap(phase5_app, client, case_id, creator, version=1):
    status, created = create_playthrough(client, creator, case_id, version)
    assert status == 201, created
    return client.get(
        f"/api/v1/playthroughs/{created['playthroughId']}/investigation",
        headers={"Authorization": f"Bearer {created['playthroughAccessToken']}"},
    )


def _publish(phase5_app, client, prompt, environment=None):
    session_token, _ = create_session(client)
    payload = {"prompt": prompt}
    if environment is not None:
        payload["environment"] = environment
    res = client.post(
        "/api/v1/cases", json=payload,
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert res.status_code == 201, res.text
    created = res.json()
    return created["caseId"], created["creatorAccessToken"], created["status"]


def _draft_from_payload(draft_payload):
    from app.generation.schemas import (
        CrimeSpec, CrimeTimeSpec, EvidenceSpec, GeneratedDraft,
        LocationSpec, MotiveSpec, ObjectSpec, PersonSpec, PropSpec,
        SceneSpec, TravelRuleSpec, WorldGraphSpec,
    )

    d = draft_payload
    return GeneratedDraft(
        crime=CrimeSpec(
            type=d["crime"]["type"], victim_id=d["crime"]["victim_id"],
            murderer_id=d["crime"]["murderer_id"], motive_id=d["crime"]["motive_id"],
            weapon_id=d["crime"]["weapon_id"], location_id=d["crime"]["location_id"],
            crime_time=CrimeTimeSpec(
                canonical=d["crime"]["crime_time"]["canonical"],
                accusation_tolerance_seconds=d["crime"]["crime_time"]["accusation_tolerance_seconds"],
            ),
        ),
        persons=tuple(
            PersonSpec(person_id=p["person_id"], name=p["name"], role=p["role"],
                       affordances=tuple(p["affordances"]), presented_data=p.get("presented_data") or {})
            for p in d["persons"]
        ),
        motives=tuple(
            MotiveSpec(motive_id=m["motive_id"], label=m["label"], affordances=tuple(m["affordances"]))
            for m in d["motives"]
        ),
        objects=tuple(
            ObjectSpec(object_id=o["object_id"], asset_id=o["asset_id"],
                       affordances=tuple(o["affordances"]), subtype=o.get("subtype"))
            for o in d["objects"]
        ),
        locations=tuple(LocationSpec(location_id=l["location_id"], name=l["name"]) for l in d["locations"]),
        travel_rules=tuple(
            TravelRuleSpec(from_location_id=t["from_location_id"], to_location_id=t["to_location_id"],
                           travel_time_seconds=t["travel_time_seconds"])
            for t in d["travel_rules"]
        ),
        scene=SceneSpec(location_id=d["scene"]["location_id"], name=d["scene"]["name"],
                        environment_id=d["scene"].get("environment_id"),
                        environment_version=d["scene"].get("environment_version")),
        evidence=tuple(
            EvidenceSpec(id=e["id"], kind=e["kind"], reliability=e.get("reliability"),
                         discoverable=e.get("discoverable"), source_ref=e.get("source_ref"),
                         presentation=e.get("presentation") or {},
                         propositions=tuple(
                             PropSpec(type=p["type"], person_id=p.get("person_id"),
                                      location_id=p.get("location_id"), object_id=p.get("object_id"),
                                      motive_id=p.get("motive_id"), observed_at=p.get("observed_at"),
                                      uncertainty_seconds=p.get("uncertainty_seconds", 0),
                                      structured=p.get("structured") or {})
                             for p in e["propositions"]
                         ))
            for e in d["evidence"]
        ),
        world_graph=WorldGraphSpec(),
    )


def _solve_payload(phase5_app, case_id, version=1):
    payload = _payload(phase5_app, case_id, version)
    draft = _draft_from_payload(payload["draft"])
    from app.generation.pipeline import _draft_to_phase3
    public, facts, truth = _draft_to_phase3(draft, case_id="CASE-X", title="T")
    return public, facts, truth, solve_case(public, facts)


def _golden_placements():
    script = json.loads(DEV_MODE_CASE.read_text(encoding="utf-8"))
    return parse_stage(GenerationStage.WORLD_GRAPH, script["world_graph"][0])


def _bootstrap_asset_ids(body):
    return {
        item["objectId"]: (
            item["assetId"],
            item["interaction"],
            item.get("evidenceId"),
            item.get("generated") is not None,
        )
        for item in body["scene"]["worldObjects"]
    }


# --------------------------------------------------------------------------- #
# the five showcase prompts drive the API end-to-end
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("env_id", sorted(SHOWCASE_PROMPTS))
def test_five_showcase_prompts_publish_selected_environment(phase5_migrated_client, env_id):
    client = phase5_migrated_client
    prompt = SHOWCASE_PROMPTS[env_id]
    expectation = SHOWCASE_EXPECTED[env_id]
    case_id, creator, status = _publish(phase5_migrated_client, client, prompt)
    assert status == "PUBLISHED"
    payload = _payload(phase5_migrated_client, case_id)
    assert payload["draft"]["scene"]["environment_id"] == expectation.environment_id
    assert payload["draft"]["scene"]["environment_version"] == 1
    # CaseTruth + evidence NEVER change across kits (golden set fixed)
    assert payload["truth"]["crime"]["murderer_id"] == "thomas_reed"
    assert payload["truth"]["crime"]["weapon_id"] == "kitchen_knife"

    boot = _bootstrap(phase5_migrated_client, client, case_id, creator)
    assert boot.status_code == 200, boot.text
    body = boot.json()
    assert body["scene"]["environmentId"] == expectation.environment_id
    assert body["scene"]["environmentVersion"] == 1
    # the public case DTO pins the same environment + kit version
    dto = client.get(
        f"/api/v1/cases/{case_id}?version=1",
        headers={"Authorization": f"Bearer {creator}"},
    )
    assert dto.status_code == 200, dto.text
    assert dto.json()["scene"]["environmentId"] == expectation.environment_id
    assert dto.json()["scene"]["environmentVersion"] == 1
    asset_ids = _bootstrap_asset_ids(body)
    # the prompt-specific supporting object IS in the bootstrap
    for prompt_id in expectation.bootstrap_assert_ids:
        assert prompt_id in asset_ids, (env_id, prompt_id)
    # the required evidence is clickable: non-empty interaction + evidence link
    knife_asset_id, knife_interaction, knife_evidence, knife_generated = asset_ids["kitchen_knife"]
    assert knife_interaction in ("inspect", "read")
    assert knife_evidence is not None
    laptop_id, laptop_interaction, laptop_evidence, _ = asset_ids["apartment_laptop"]
    assert laptop_interaction == "read"
    assert laptop_evidence == "email_thomas_01"
    # proc assets project their embedded definitions
    if expectation.proc_assets_expected:
        assert any(v[3] for v in asset_ids.values()), env_id

    # solver all_true for the published composition
    public, facts, truth, proof = _solve_payload(phase5_migrated_client, case_id)
    assert proof.who.winner == "thomas_reed"
    assert evaluate_solution(proof, truth).all_true is True


# --------------------------------------------------------------------------- #
# default golden apartment stays byte-identical
# --------------------------------------------------------------------------- #


def test_default_golden_apartment_is_byte_identical_to_the_golden_world(
    phase5_migrated_client,
):
    client = phase5_migrated_client
    case_a, creator_a, status_a = _publish(
        phase5_migrated_client, client, GOLDEN_DEFAULT_PROMPT
    )
    assert status_a == "PUBLISHED"
    case_b, creator_b, status_b = _publish(
        phase5_migrated_client, client, GOLDEN_DEFAULT_PROMPT
    )
    assert status_b == "PUBLISHED"
    payload_a = _payload(phase5_migrated_client, case_a)
    payload_b = _payload(phase5_migrated_client, case_b)
    # deterministic: two identical runs share the same world material
    # (case ids are per-attempt opaque; everything else is byte-identical)
    assert payload_a["draft"]["world_graph"] == payload_b["draft"]["world_graph"]
    assert payload_a["draft"]["objects"] == payload_b["draft"]["objects"]
    assert payload_a["draft"]["evidence"] == payload_b["draft"]["evidence"]
    assert payload_a["truth"]["crime"] == payload_b["truth"]["crime"]

    scene = payload_a["draft"]["scene"]
    assert scene["environment_id"] == "apartment"
    assert scene["environment_version"] == 1
    assert scene["location_id"] == "miller_apartment_kitchen"

    # world graph is placement-for-placement the provider golden
    golden = _golden_placements()
    wg = payload_a["draft"]["world_graph"]
    def _pl_key(p):
        return (p["object_id"], p["asset_id"], p["location_id"], p["anchor"],
                p["interaction"], p.get("evidence_id"))
    assert [_pl_key(p) for p in wg["placements"]] == [
        (p.object_id, p.asset_id, p.location_id, p.anchor, p.interaction, p.evidence_id)
        for p in golden.placements
    ]
    boot = _bootstrap(phase5_migrated_client, client, case_a, creator_a)
    assert sorted(o["objectId"] for o in boot.json()["scene"]["worldObjects"]) == sorted(
        GOLDEN_OBJECT_IDS
    )


def test_explicit_environment_hint_still_works(phase5_migrated_client):
    client = phase5_migrated_client
    case_id, creator, status = _publish(
        phase5_migrated_client, client, GOLDEN_DEFAULT_PROMPT, environment="office"
    )
    assert status == "PUBLISHED"
    payload = _payload(phase5_migrated_client, case_id)
    assert payload["draft"]["scene"]["environment_id"] == "office"
    office = next(
        k for k in load_all_environments(directory=ENVIRONMENTS_DIR)
        if k.environment_id == "office"
    )
    for placement in payload["draft"]["world_graph"]["placements"]:
        assert placement["anchor"] in {a.anchor_id for a in office.anchors}
    public, facts, truth, proof = _solve_payload(phase5_migrated_client, case_id)
    assert evaluate_solution(proof, truth).all_true is True


# --------------------------------------------------------------------------- #
# catalog mutation does not change a published world
# --------------------------------------------------------------------------- #


def test_catalog_descriptor_mutation_does_not_change_published_world(
    phase5_migrated_client,
):
    client = phase5_migrated_client
    case_id, _creator, status = _publish(
        phase5_migrated_client, client, GOLDEN_DEFAULT_PROMPT
    )
    assert status == "PUBLISHED"
    original = _payload(phase5_migrated_client, case_id)

    # Build a MUTATED catalog copy (cosmetic descriptor change: recolour + bump
    # the knife descriptor version; identity/resolution unaffected).
    catalog_path = REPO_ROOT / "assets" / "catalog" / "catalog.json"
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    for asset in data["assets"]:
        if asset["assetId"] == "PROP_KITCHEN_KNIFE_01":
            asset["version"] += 1
            asset["colors"]["shade"] = "#ff0000"
    with tempfile.TemporaryDirectory() as tmpdir:
        mutated = Path(tmpdir) / "catalog.json"
        mutated.write_text(
            json.dumps(data, sort_keys=True), encoding="utf-8"
        )

        from app.assets.catalog import load_catalog_from_repo
        from app.environments.manifests import load_environment
        from app.world.composer import compose_world
        from app.world.extract import extract_world_requirements
        from app.environments.placer import validate_placement

        mutated_catalog = load_catalog_from_repo(path=mutated)
        kit = load_environment("apartment")
        world_reqs = extract_world_requirements(GOLDEN_DEFAULT_PROMPT)
        original_composition = compose_world(
            world_reqs,
            evidence_placements=_golden_placements().placements,
            catalog=None,
            kit=kit,
        )
        mutated_composition = compose_world(
            world_reqs,
            evidence_placements=_golden_placements().placements,
            catalog=mutated_catalog,
            kit=kit,
        )
        assert original_composition.placements == mutated_composition.placements

    # the published (pinned) payload is unchanged by the catalog mutation
    assert _payload(phase5_migrated_client, case_id) == original


# --------------------------------------------------------------------------- #
# re-publication of v2 leaves v1 byte-identical
# --------------------------------------------------------------------------- #


def test_republish_v2_keeps_v1_unchanged(phase5_migrated_client):
    client = phase5_migrated_client
    case_id, creator, status = _publish(
        phase5_migrated_client, client, GOLDEN_DEFAULT_PROMPT
    )
    assert status == "PUBLISHED"
    v1_before = json.dumps(_payload(phase5_migrated_client, case_id), sort_keys=True)

    service = phase5_migrated_client.app.state.generation_service
    session_token, _ = create_session(client)
    started = service.start_case_version(
        case_id,
        MANSION_PROMPT,
        anonymous_quota_session_id=_session_id_of(
            phase5_migrated_client, session_token
        ),
    )
    assert started.status == "PUBLISHED", started
    v2_payload = _payload(phase5_migrated_client, case_id, version=2)
    assert v2_payload["draft"]["scene"]["environment_id"] == "mansion"
    # v1 world is byte-identical after the v2 re-publication
    assert json.dumps(_payload(phase5_migrated_client, case_id, version=1), sort_keys=True) == v1_before
    public, facts, truth, proof = _solve_payload(phase5_migrated_client, case_id, version=2)
    assert evaluate_solution(proof, truth).all_true is True


def _session_id_of(app, session_token):
    store = app.app.state.store
    from app.auth.tokens import verifier
    row = store.get_session_by_verifier(verifier(session_token))
    assert row is not None
    return row.session_id


# --------------------------------------------------------------------------- #
# same seed -> identical worlds via the API
# --------------------------------------------------------------------------- #


def test_same_prompt_publishes_identical_worlds(phase5_migrated_client):
    client = phase5_migrated_client

    def _once():
        case_id, _creator, status = _publish(
            phase5_migrated_client, client, SHOWCASE_PROMPTS["warehouse"]
        )
        assert status == "PUBLISHED"
        payload = _payload(phase5_migrated_client, case_id)
        return [
            (p["object_id"], p["asset_id"], p["anchor"], p["interaction"], p.get("evidence_id"))
            for p in payload["draft"]["world_graph"]["placements"]
        ]

    assert _once() == _once()


def test_unsafe_prompt_fails_safely_through_the_api(phase5_migrated_client):
    client = phase5_migrated_client
    case_id, creator, status = _publish(
        phase5_migrated_client, client,
        "The killer planted a bomb and a gun at the castle.",
    )
    assert status == "PUBLISHED"
    payload = _payload(phase5_migrated_client, case_id)
    # falls back to apartment and composes NO bomb/gun asset
    assert payload["draft"]["scene"]["environment_id"] == "apartment"
    joined = json.dumps(payload["draft"]["world_graph"])
    assert "bomb" not in joined.lower()
    boot = _bootstrap(phase5_migrated_client, client, case_id, creator)
    assert boot.status_code == 200


# --------------------------------------------------------------------------- #
# solver truth-independence over all five compositions
# --------------------------------------------------------------------------- #


def test_solver_truth_independence_across_five_compositions(phase5_migrated_client):
    client = phase5_migrated_client
    proofs = {}
    for env_id, prompt in SHOWCASE_PROMPTS.items():
        case_id, _creator, status = _publish(client, client, prompt)
        assert status == "PUBLISHED"
        public, facts, truth, proof = _solve_payload(phase5_migrated_client, case_id)
        assert proof.who.winner == "thomas_reed"
        assert proof.why.winner == "cover_up_embezzlement"
        assert proof.weapon.winner == "kitchen_knife"
        assert evaluate_solution(proof, truth).all_true is True
        proofs[env_id] = proof
    # identical solver inputs (public + evidence) across kits -> identical proofs
    a = proofs["apartment"]
    for env_id, proof in proofs.items():
        assert proof == a, env_id


def test_two_different_truths_over_five_compositions_produce_identical_proofs(
    phase5_migrated_client,
):
    client = phase5_migrated_client
    for env_id, prompt in SHOWCASE_PROMPTS.items():
        case_id, _creator, status = _publish(phase5_migrated_client, client, prompt)
        assert status == "PUBLISHED"
        public, facts, truth, proof = _solve_payload(phase5_migrated_client, case_id)
        # two hidden truths over IDENTICAL public + evidence -> one proof
        proof_again = solve_case(public, facts)
        assert proof_again == proof
        assert evaluate_solution(proof, truth_variant_a()).all_true is True
        # variant B over the same inputs: identical deduction, different verdict
        assert evaluate_solution(proof, truth_variant_b()).all_true is False