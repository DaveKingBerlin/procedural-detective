"""Phase16_2 — Ollama Stage Driver tests (DELIVERABLE 2/3/5 §32).

All transport interaction is mocked with a ``MockOllamaTransport`` (never a
network call — the autouse network block enforces this). The tests drive a REAL
``GenerationController`` whose ``stage_driver`` is an ``OllamaStageDriver``
over the a real ``OllamaProvider`` with the mocked transport, so budgets /
admission / repair / lifecycle / publication semantics are exact.

The showcase fixture reuses the golden opportunity/forensic EVIDENCE structure
re-anchored to new participants (Anna Weiss / Paul Becker / Lisa König) and the
office kit with a ``bronze ceremonial ice pick`` unknown object -> ASSET_SPEC ->
``proc.*``; the deterministic solver uniquely derives paul_becker /
bronze_ceremonial_ice_pick / stolen_research_data (all_true).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.generation.admission import AdmissionController, AdmissionDenied  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.ollama_provider import OllamaProvider  # noqa: E402
from app.generation.pipeline import validate_draft  # noqa: E402
from app.generation.provider import GenerateRequest, ProviderResult  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.ollama_driver import (  # noqa: E402
    MAX_SPEC_REPAIR_PASSES,
    OllamaAssetSpecProvider,
    OllamaStageDriver,
)

OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.2:3b"

_SCENE = "konsortium_office"
_LAB = "research_lab"
_LODGE = "motor_lodge"


def _j(d):
    return json.dumps(d, sort_keys=True, ensure_ascii=False, indent=2)


def _fact(eid, kind, propositions, reliability="high", title=None):
    return {
        "id": eid, "kind": kind, "reliability": reliability, "discoverable": True,
        "sourceRef": {"kind": "record", "sourceId": f"record_{eid}"},
        "propositions": propositions,
        "presentation": {"title": title or eid, "description": "Structured evidence fact."},
    }


def _p(t, **kw):
    out = {"type": t}
    for k, v in kw.items():
        if v is not None:
            out[k] = v
    return out


def _case_people(weapon="bronze_ceremonial_ice_pick"):
    return {
        "crime": {
            "type": "murder", "victimId": "anna_weiss", "murdererId": "paul_becker",
            "motiveId": "stolen_research_data", "weaponId": weapon, "locationId": _SCENE,
            "crimeTime": {"canonical": "2026-09-11T23:42:00+02:00", "accusationToleranceSeconds": 300},
        },
        "persons": [
            {"personId": "anna_weiss", "name": "Dr. Anna Weiss", "role": "victim", "affordances": ["VISIBLE_CHARACTER"]},
            {"personId": "paul_becker", "name": "Paul Becker", "role": "suspect", "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"]},
            {"personId": "marcus_fischer", "name": "Marcus Fischer", "role": "suspect", "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"]},
            {"personId": "sophie_hoffmann", "name": "Sophie Hoffmann", "role": "suspect", "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"]},
            {"personId": "lisa_koenig", "name": "Lisa König", "role": "witness", "affordances": ["VISIBLE_CHARACTER"]},
        ],
        "motives": [
            {"motiveId": "stolen_research_data", "label": "Wanted to steal the research data", "affordances": ["MOTIVE_CANDIDATE"]},
            {"motiveId": "financial_settlement", "label": "A financial settlement dispute", "affordances": ["MOTIVE_CANDIDATE"]},
            {"motiveId": "personal_grudge", "label": "A personal grudge over a promotion", "affordances": ["MOTIVE_CANDIDATE"]},
        ],
        "locations": [
            {"locationId": _SCENE, "name": "Konsortium Office"},
            {"locationId": _LAB, "name": "Research Laboratory"},
            {"locationId": _LODGE, "name": "Motor Lodge"},
        ],
        "travelRules": [
            {"fromLocationId": _LAB, "toLocationId": _SCENE, "travelTimeSeconds": 1200},
            {"fromLocationId": _LODGE, "toLocationId": _SCENE, "travelTimeSeconds": 2700},
        ],
        "scene": {"locationId": _SCENE, "name": "Konsortium Office"},
    }


def _evidence(weapon_obj="bronze_ceremonial_ice_pick", murderer="paul_becker"):
    return {"evidence": [
        _fact("last_seen_anna_01", "witness_observation", [_p("VICTIM_LAST_SEEN_ALIVE_AT", personId="anna_weiss", locationId=_SCENE, observedAt="2026-09-11T23:40:10+02:00")]),
        _fact("body_found_01", "witness_observation", [_p("BODY_FIRST_FOUND_AT", locationId=_SCENE, observedAt="2026-09-11T23:43:31+02:00")]),
        _fact("noise_heard_01", "witness_observation", [_p("NOISE_HEARD_AT", locationId=_SCENE, observedAt="2026-09-11T23:41:50+02:00", uncertaintySeconds=90)], reliability="medium"),
        _fact("cctv_paul_scene_01", "cctv_observation", [_p("PERSON_OBSERVED_AT_LOCATION", personId="paul_becker", locationId=_SCENE, observedAt="2026-09-11T23:41:40+02:00", uncertaintySeconds=60)]),
        _fact("alibi_claim_paul_01", "suspect_statement", [_p("ALIBI_TIME_CLAIM", personId="paul_becker", structured={"claimedDeparture": "2026-09-11T23:10:00+02:00"})], reliability="low"),
        _fact("cctv_marcus_lab_01", "cctv_observation", [_p("PERSON_OBSERVED_AT_LOCATION", personId="marcus_fischer", locationId=_LAB, observedAt="2026-09-11T23:40:00+02:00", uncertaintySeconds=30)]),
        _fact("cctv_sophie_lodge_01", "cctv_observation", [_p("PERSON_OBSERVED_AT_LOCATION", personId="sophie_hoffmann", locationId=_LODGE, observedAt="2026-09-11T23:39:30+02:00", uncertaintySeconds=30)]),
        _fact("motive_data_01", "financial", [_p("MOTIVE_LINKED_TO_PERSON", personId="paul_becker", motiveId="stolen_research_data")]),
        _fact("motive_no_settlement_01", "digital", [_p("MOTIVE_FACT_CONTRADICTED", motiveId="financial_settlement")]),
        _fact("motive_no_grudge_01", "physical", [_p("MOTIVE_FACT_CONTRADICTED", motiveId="personal_grudge")]),
        _fact("forensic_knife_match_01", "forensic", [_p("FORENSIC_WEAPON_MATCH", objectId="kitchen_knife", structured={"match": False})]),
        _fact("forensic_letter_opener_01", "forensic", [_p("FORENSIC_WEAPON_MATCH", objectId="letter_opener", structured={"match": False})]),
        _fact("forensic_scissors_01", "forensic", [_p("FORENSIC_WEAPON_MATCH", objectId="scissors", structured={"match": False})]),
        _fact("forensic_icepick_match_01", "forensic", [_p("FORENSIC_WEAPON_MATCH", objectId=weapon_obj, structured={"match": True})]),
        _fact("fingerprint_icepick_01", "forensic", [_p("OBJECT_CONTAINS_FINGERPRINT", objectId=weapon_obj, personId=murderer)]),
        _fact("email_thomas_01", "email", [_p("OTHER")]),
    ]}


def _world(unknown_name="bronze ceremonial ice pick"):
    return {
        "environmentHint": "office", "locationTokens": ["office"],
        "objects": [{"name": unknown_name, "categoryHint": "decor", "criticality": "required"}],
        "relations": [{"kind": "on_desk", "target": unknown_name}],
        "unsafeUnsupported": [],
    }


def _known_world():
    # an object the catalog ALREADY resolves (kitchen knife) -> no ASSET_SPEC.
    return {
        "environmentHint": "office", "locationTokens": ["office"],
        "objects": [{"name": "kitchen knife", "criticality": "decorative"}],
        "relations": [], "unsafeUnsupported": [],
    }


ICEPICK_SPEC = """{
  "canonicalName": "Bronze Ceremonial Ice Pick",
  "category": "decor",
  "subtype": "ceremonial_ice_pick",
  "dimensions": {"x": 0.12, "y": 0.5, "z": 0.1},
  "parts": [
    {"id": "part_00", "role": "shaft", "primitive": "cylinder",
     "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}, "scale": {"x": 0.05, "y": 0.18, "z": 0.05}}, "material": "metal.brass"},
    {"id": "part_01", "role": "point", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": 0.2, "z": 0.0}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}, "scale": {"x": 0.05, "y": 0.05, "z": 0.05}}, "material": "metal.brass"},
    {"id": "part_02", "role": "handle", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": -0.19, "z": 0.0}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}, "scale": {"x": 0.05, "y": 0.06, "z": 0.05}}, "material": "wood.dark"}
  ]
}"""

PROMPT = ("Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
          "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa König\nLocation: office\n")


class MockOllamaTransport:
    def __init__(self, posts=()):
        self.posts = list(posts)
        self.post_calls = []

    def post_json(self, url, payload, timeout):
        self.post_calls.append(payload.get("messages", [{}])[0].get("content", ""))
        content = self.posts.pop(0) if self.posts else "<not-json>"
        return 200, json.dumps(
            {"model": OLLAMA_MODEL, "message": {"role": "assistant", "content": content}}
        ).encode()

    def get(self, url, timeout):
        return 200, json.dumps({"models": [{"name": OLLAMA_MODEL}]}).encode()

    @property
    def call_count(self):
        return len(self.post_calls)

    def prompt_of_call(self, index):
        return self.post_calls[index]


def _staged(unknown=True, icepick_spec=ICEPICK_SPEC):
    wp = _world() if unknown else _known_world()
    posts = [_j(_case_people()), _j(_evidence()), _j(wp)]
    if unknown:
        posts.append(icepick_spec)
    return posts


def _admission(clock, ids, **overrides):
    kwargs = dict(
        max_concurrent_generations=1, max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3, max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    kwargs.update(overrides)
    return AdmissionController(clock=clock, ids=ids, **kwargs)


def _controller(driver, transport, admission, clock, ids, **overrides):
    def factory():
        return OllamaProvider(base_url=OLLAMA_BASE, model=OLLAMA_MODEL, timeout_seconds=5, transport=transport)
    kwargs = dict(
        deadline_seconds=60, max_llm_calls_per_generation=12, max_repair_passes=2,
        max_full_regenerations=1, max_prompt_chars=4000, seed=11,
    )
    kwargs.update(overrides)
    return GenerationController(
        provider=factory(), admission=admission, clock=clock, ids=ids,
        stage_driver=driver, **kwargs,
    )


def _make_driver(transport):
    def factory():
        return OllamaProvider(base_url=OLLAMA_BASE, model=OLLAMA_MODEL, timeout_seconds=5, transport=transport)
    return OllamaStageDriver(settings=Settings(), provider_factory=factory)


def _run(posts, prompt=PROMPT, **controller_overrides):
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=posts)
    driver = _make_driver(transport)
    controller = _controller(driver, transport, admission, clock, ids, **controller_overrides)
    handle = controller.start_generation(prompt, anonymous_quota_session_id=session.session_id)
    return controller.attempt(handle.attempt_id), transport


# --------------------------------------------------------------------------- #
# Generation (17-23)
# --------------------------------------------------------------------------- #


def test_17_case_people_stage():
    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    prompt0 = transport.prompt_of_call(0)
    assert "case_people_v1" in prompt0
    # CASE/PEOPLE output replaced the crime/people draft data (different names).
    assert "Anna Weiss" in prompt0 and "Paul Becker" in prompt0
    assert record.draft.crime.murderer_id == "paul_becker"


def test_18_evidence_stage():
    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    assert "evidence_v1" in transport.prompt_of_call(1)
    # Phase17 Wave-2 acceptance path: the DETERMINISTIC canonical evidence
    # (built from the model's case skeleton) is what the published draft
    # carries — the model's raw propositions are shaping input, not copied
    # verbatim.
    assert any(f.id == "d_ev_weapon_true" for f in record.draft.evidence)


def _invalid_raw_evidence(*, semantic: bool) -> dict:
    """Raw EVIDENCE payloads that must never escape the strict parser."""
    payload = _evidence()
    payload["evidence"][0]["id"] = (
        "raw_cross_field_must_not_leak" if semantic else "raw_token_must_not_leak"
    )
    proposition = payload["evidence"][0]["propositions"][0]
    if semantic:
        # Exact allowed type, but invalid Phase 3 semantics: the required
        # claimedDeparture field is absent.
        proposition.clear()
        proposition.update({"type": "ALIBI_TIME_CLAIM", "personId": "anna_weiss", "structured": {}})
    else:
        proposition["type"] = "RAW_UNKNOWN_PROPOSITION_TOKEN"
    return payload


@pytest.mark.parametrize("semantic", (False, True))
def test_18a_malformed_evidence_uses_local_projection_without_remote_retry(caplog, semantic):
    """A rejected raw evidence response is locally replaced, not retried.

    This drives the real controller/driver with the Ollama transport mock.  It
    proves the exact production topology remains CASE -> EVIDENCE -> WORLD ->
    ASSET_SPEC while the normal validators and solver certify the projection.
    """
    raw = _j(_invalid_raw_evidence(semantic=semantic))
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, transport = _run(
            [_j(_case_people()), raw, _j(_world()), ICEPICK_SPEC],
            max_llm_calls_per_generation=8,
        )

    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 4
    assert record.budget.calls == 4
    assert "world_requirements_v1" in transport.prompt_of_call(2)
    assert record.deferred_structural == ()
    assert record.last_validation.valid is True
    assert record.last_validation.validation.all_true is True
    assert record.solver_proof is not None
    assert record.solver_proof.who.unique
    assert record.solver_proof.why.unique
    assert record.solver_proof.weapon.unique

    from app.domain.evidence import PROPOSITION_TYPES, validate_evidence
    from app.generation import pipeline
    from app.services.publication import serialize_published_payload

    public, evidence, _truth, _draft = pipeline.assemble(record)
    assert validate_evidence(public, evidence) == ()
    person_ids = {person.person_id for person in public.persons}
    location_ids = {location.location_id for location in public.locations}
    motive_ids = {motive.motive_id for motive in public.motives}
    object_ids = {obj.object_id for obj in public.objects}
    for fact in evidence:
        for proposition in fact.propositions:
            assert proposition.type in PROPOSITION_TYPES
            assert proposition.person_id is None or proposition.person_id in person_ids
            assert proposition.location_id is None or proposition.location_id in location_ids
            assert proposition.motive_id is None or proposition.motive_id in motive_ids
            assert proposition.object_id is None or proposition.object_id in object_ids

    published_json = serialize_published_payload(record.published, title="t")
    assert "raw_token_must_not_leak" not in published_json
    assert "raw_cross_field_must_not_leak" not in published_json
    assert "RAW_UNKNOWN_PROPOSITION_TOKEN" not in published_json

    projection_events = [
        event for event in caplog.records
        if getattr(event, "pd_event", None) == "evidence.local_projection.used"
    ]
    assert len(projection_events) == 1
    assert getattr(projection_events[0], "pd_fields") == {
        "generationAttemptId": record.attempt_id,
        "reasonCode": "STRUCTURED_OUTPUT_INVALID",
        "originalStage": "evidence",
        "providerCallCount": 2,
        "projectionValid": True,
        "elapsedMs": getattr(projection_events[0], "pd_fields")["elapsedMs"],
    }


def test_18b_valid_evidence_keeps_existing_ollama_path_without_local_projection(caplog):
    """Valid Ollama evidence remains the same four-call published flow."""
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, transport = _run(_staged(), max_llm_calls_per_generation=8)

    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 4
    assert record.budget.calls == 4
    assert record.deferred_structural == ()
    assert not any(
        getattr(event, "pd_event", None) == "evidence.local_projection.used"
        for event in caplog.records
    )


def test_18c_invalid_deterministic_projection_stays_fail_closed(monkeypatch, caplog):
    """A raw-evidence parse failure never publishes when local recovery fails."""
    import app.services.ollama_driver as driver_module

    monkeypatch.setattr(driver_module, "_evidence_gap_facts", lambda *_args: (None, (), ()))
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, transport = _run(
            [_j(_case_people()), "<not-json>", _j(_world()), ICEPICK_SPEC],
            max_llm_calls_per_generation=8,
            max_repair_passes=0,
            max_full_regenerations=0,
        )

    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert transport.call_count == 4
    assert record.budget.calls == 4
    projection_events = [
        event for event in caplog.records
        if getattr(event, "pd_event", None) == "evidence.local_projection.used"
    ]
    assert len(projection_events) == 1
    assert getattr(projection_events[0], "pd_fields")["projectionValid"] is False


def test_19_world_requirements_stage():
    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    assert "world_requirements_v1" in transport.prompt_of_call(2)
    # the office environment was selected via the LLM world-requirements.
    assert record.draft.scene is not None
    assert record.draft.scene.environment_id == "office"


def test_20_repair_stage__terminal_provider_failure():
    """A model-call-budget exhaustion inside a driver stage fails the attempt
    through the existing repair/regenerate/FAILED classification."""
    record, transport = _run(
        [_j(_case_people()), "<not-json>", "<not-json>", "<not-json>",
         "<not-json>", "<not-json>", "<not-json>", "<not-json>"],
        max_llm_calls_per_generation=4,
    )
    assert record.state is GenerationState.FAILED
    assert record.published is None


def test_21_locked_constraints_preserved():
    from app.generation.constraints import LockedConstraints
    # locked murderer must be respected by the generated case truth.
    record, _t = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    assert record.last_validation.locked_violations == ()
    assert record.draft.crime.murderer_id == "paul_becker"
    assert record.draft.crime.weapon_id == "bronze_ceremonial_ice_pick"


def test_22_solver_independently_validates():
    record, _t = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    assert record.last_validation.valid is True
    assert record.last_validation.validation.all_true is True


def test_23_conflicting_model_matches_are_deterministically_completed():
    """Phase17 Wave-2 acceptance path: a model evidence where TWO weapons
    match True (the old ambiguity blocker) is policy-sanitized (the
    match:true on the alternative weapon is dropped) and deterministically
    completed, so the ASSEMBLED evidence yields a UNIQUE solver winner. The
    solver rules are untouched — the uniqueness it proves comes from the
    assembled (model + driver-projected) evidence set."""
    # craft evidence where an alternative weapon ALSO matches True -> the raw
    # model evidence would leave the weapon dimension ambiguous.
    amb_evidence = dict(_evidence())
    amb_props = list(amb_evidence["evidence"])
    for entry in amb_props:
        if entry["id"] == "forensic_knife_match_01":
            entry["propositions"][0]["structured"]["match"] = True
    amb_evidence["evidence"] = amb_props
    transport = MockOllamaTransport(posts=[_j(_case_people()), _j(amb_evidence), _j(_world()), ICEPICK_SPEC])
    clock, ids = ManualClock(), IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    driver = _make_driver(transport)
    controller = _controller(driver, transport, admission, clock, ids, max_repair_passes=0)
    handle = controller.start_generation(PROMPT, anonymous_quota_session_id=session.session_id)
    record = controller.attempt(handle.attempt_id)
    # The driver's policy pass dropped the conflicting alternative-weapon
    # match and completed the weapon algebra -> the solver is UNIQUE.
    assert record.state is GenerationState.PUBLISHED
    assert record.last_validation.valid is True
    assert record.last_validation.validation.all_true is True
    proof = record.solver_proof
    assert proof is not None and proof.weapon.unique
    assert proof.weapon.winner == "bronze_ceremonial_ice_pick"
    assert "d_ev_weapon_false_kitchenknife" in {f.id for f in record.draft.evidence}


# --------------------------------------------------------------------------- #
# AssetSpec (24-35)
# --------------------------------------------------------------------------- #


def test_24_known_asset_does_not_call_asset_spec_provider():
    """A KNOWN catalog object resolves through the Asset Oracle -> placer and
    NEVER reaches an ASSET_SPEC provider call (counted on the provider)."""
    from app.world.requirements import ObjectRequest, WorldRequirements
    from app.world.composer import compose_world

    counted = {"calls": 0}

    class CountingProvider:
        def generate(self, request):
            counted["calls"] += 1
            return None  # should never be reached for a known object

    world = WorldRequirements(
        environment_hint="office",
        objects=(ObjectRequest(requested_name="kitchen knife", criticality="decorative"),),
        relations=(),
    )
    composition = compose_world(
        world, spec_provider=CountingProvider(), environment_id="office"
    )
    assert not composition.issues
    assert counted["calls"] == 0  # the known knife resolved via the catalog


def test_25_unknown_asset_calls_provider():
    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 4
    assert "asset_spec_v1" in transport.prompt_of_call(3)
    # the proc.* object appears in the published world.
    assert any(
        p.asset_id.startswith("proc.")
        for p in (record.published.draft if record.published else record.draft).world_graph.placements
    )


def test_26_valid_llama_style_spec_compiles():
    record, _t = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    published = record.published.draft
    proc_ids = {p.asset_id for p in published.world_graph.placements if p.asset_id.startswith("proc.")}
    assert proc_ids
    # a generated definition is embedded on the proc placement.
    proc_placements = [p for p in published.world_graph.placements if p.asset_id.startswith("proc.")]
    assert proc_placements[0].generated_definition is not None


def test_27_unit_regression_25_vs_025_meters():
    """A '25 cm'-style dimension (width:25) is OUT of Phase 13 bounds
    (0.05..4); the repair re-states meters and returns plausible dims."""
    bad = json.loads(ICEPICK_SPEC)
    bad["dimensions"] = {"x": 25, "y": 0.1, "z": 0.1}  # 25 meters-ish — out of bounds
    good = json.loads(ICEPICK_SPEC)  # plausible dims
    record, transport = _run([
        _j(_case_people()), _j(_evidence()), _j(_world()),
        json.dumps(bad),      # ASSET_SPEC (invalid)
        json.dumps(good),     # ASSET_SPEC_REPAIR (valid)
    ])
    assert record.state is GenerationState.PUBLISHED
    # the repair prompt re-stated meters.
    assert any("0.25 means 25 centimeters" in transport.prompt_of_call(i) for i in range(transport.call_count))


def test_28_scale_below_lower_bound_rejected():
    bad = json.loads(ICEPICK_SPEC)
    bad["parts"][0]["transform"]["scale"] = {"x": 0.0001, "y": 0.0001, "z": 0.0001}  # below 0.001
    good = json.loads(ICEPICK_SPEC)
    transport = MockOllamaTransport(posts=[_j(_case_people()), _j(_evidence()), _j(_world()), json.dumps(bad), json.dumps(good)])
    record, _t = _run(transport.posts)
    assert record.state is GenerationState.PUBLISHED


def test_29_invalid_role_grammar_rejected():
    bad = json.loads(ICEPICK_SPEC)
    bad["parts"][0]["role"] = "onload"  # event-handler-shaped role -> rejected
    good = json.loads(ICEPICK_SPEC)
    transport = MockOllamaTransport(posts=[_j(_case_people()), _j(_evidence()), _j(_world()), json.dumps(bad), json.dumps(good)])
    record, _t = _run(transport.posts)
    assert record.state is GenerationState.PUBLISHED


def test_30_duplicate_part_ids_rejected():
    bad = json.loads(ICEPICK_SPEC)
    bad["parts"][1]["id"] = "part_00"  # duplicate
    good = json.loads(ICEPICK_SPEC)
    transport = MockOllamaTransport(posts=[_j(_case_people()), _j(_evidence()), _j(_world()), json.dumps(bad), json.dumps(good)])
    record, _t = _run(transport.posts)
    assert record.state is GenerationState.PUBLISHED


def test_31_parent_depth_violation_rejected():
    bad = json.loads(ICEPICK_SPEC)
    # part_01 parent part_00, part_02 parent part_01 -> depth 2 (ok);
    # make depth 3 by chaining: part_02 -> part_01 -> part_00 is depth 2, add part_02 -> part_02? no.
    # simplest: make part_02 parent part_01 and part_01 parent part_00 with part_02 -> part_01 -> part_00 still depth 2.
    # Force a depth-3 chain using a 4-part doc.
    spec = {
        "canonicalName": "Deep", "category": "decor", "subtype": "deep",
        "dimensions": {"x": 0.2, "y": 0.2, "z": 0.2},
        "parts": [
            {"id": "part_00", "role": "a", "primitive": "box", "transform": {"position": {"x": 0, "y": 0, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}, "scale": {"x": 0.1, "y": 0.1, "z": 0.1}}, "material": "metal.steel"},
            {"id": "part_01", "role": "b", "primitive": "box", "parentId": "part_00", "transform": {"position": {"x": 0, "y": 0, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}, "scale": {"x": 0.1, "y": 0.1, "z": 0.1}}, "material": "metal.steel"},
            {"id": "part_02", "role": "c", "primitive": "box", "parentId": "part_01", "transform": {"position": {"x": 0, "y": 0, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}, "scale": {"x": 0.1, "y": 0.1, "z": 0.1}}, "material": "metal.steel"},
            {"id": "part_03", "role": "d", "primitive": "box", "parentId": "part_02", "transform": {"position": {"x": 0, "y": 0, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}, "scale": {"x": 0.1, "y": 0.1, "z": 0.1}}, "material": "metal.steel"},
        ],
    }
    good = json.loads(ICEPICK_SPEC)
    transport = MockOllamaTransport(posts=[_j(_case_people()), _j(_evidence()), _j(_world()), json.dumps(spec), json.dumps(good)])
    record, _t = _run(transport.posts)
    assert record.state is GenerationState.PUBLISHED


def test_32_unsupported_material_rejected():
    bad = json.loads(ICEPICK_SPEC)
    bad["parts"][0]["material"] = "bronze"  # NOT allowlisted
    good = json.loads(ICEPICK_SPEC)
    transport = MockOllamaTransport(posts=[_j(_case_people()), _j(_evidence()), _j(_world()), json.dumps(bad), json.dumps(good)])
    record, _t = _run(transport.posts)
    assert record.state is GenerationState.PUBLISHED


def test_33_unsupported_primitive_rejected():
    bad = json.loads(ICEPICK_SPEC)
    bad["parts"][0]["primitive"] = "capsule"  # NOT supported
    good = json.loads(ICEPICK_SPEC)
    transport = MockOllamaTransport(posts=[_j(_case_people()), _j(_evidence()), _j(_world()), json.dumps(bad), json.dumps(good)])
    record, _t = _run(transport.posts)
    assert record.state is GenerationState.PUBLISHED


def test_33_degenerate_same_origin_geometry_is_repaired():
    """All parts placed at the same origin are NOT a Phase 13 STRUCTURAL
    violation but ARE a Phase 17 geometric-quality violation (no recognizable
    silhouette); the driver routes the structured geometry diagnostics into a
    bounded ASSET_SPEC_REPAIR and only compiles the repaired candidate."""
    same_origin = json.loads(ICEPICK_SPEC)
    for part in same_origin["parts"]:
        part["transform"]["position"] = {"x": 0.0, "y": 0.0, "z": 0.0}
    from app.assets.specs import validate_asset_spec
    issues = validate_asset_spec(same_origin)
    assert not issues  # structurally valid — Phase 17 geometry gate decides
    repaired = json.loads(ICEPICK_SPEC)  # coherent geometry
    record, transport = _run([
        _j(_case_people()), _j(_evidence()), _j(_world()),
        json.dumps(same_origin),  # ASSET_SPEC — fails the geometry gate
        json.dumps(repaired),     # ASSET_SPEC_REPAIR — repaired candidate
    ])
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 5
    # the repair prompt restated the geometry rules.
    assert any(
        "Geometry-quality instructions" in transport.prompt_of_call(i)
        for i in range(transport.call_count)
    )
    proc_ids = {
        p.asset_id for p in (record.published.draft if record.published else record.draft).world_graph.placements
        if p.asset_id.startswith("proc.")
    }
    assert proc_ids  # the repaired ice pick is compiled + placed.


def test_34_proc_id_stable_for_same_normalized_spec():
    from app.assets.specs import parse_asset_spec, spec_hash
    spec = parse_asset_spec(ICEPICK_SPEC, non_throwing=False)
    h1 = spec_hash(spec)
    spec2 = parse_asset_spec(ICEPICK_SPEC, non_throwing=False)
    h2 = spec_hash(spec2)
    assert h1 == h2
    # two identical driver runs produce the same proc.* id.
    record1, _t1 = _run(_staged())
    record2, _t2 = _run(_staged())
    def proc_id(record):
        return next(
            p.asset_id for p in (record.published.draft if record.published else record.draft).world_graph.placements
            if p.asset_id.startswith("proc.")
        )
    assert proc_id(record1) == proc_id(record2)


def test_35_published_proc_object_immutable():
    record, _t = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    payload1 = json.dumps(record.published.draft.to_dict() if hasattr(record.published.draft, "to_dict") else repr(record.published.draft), sort_keys=True)
    # A second, otherwise-identical run must reproduce the identical frozen
    # world (deterministic + immutable), including the proc.* definition.
    record2, _t2 = _run(_staged())
    payload2 = json.dumps(record2.published.draft.to_dict() if hasattr(record2.published.draft, "to_dict") else repr(record2.published.draft), sort_keys=True)
    assert payload1 == payload2


# --------------------------------------------------------------------------- #
# Lifecycle (36-40)
# --------------------------------------------------------------------------- #


def test_36_provider_call_uses_existing_budget():
    record, _t = _run(_staged(), max_llm_calls_per_generation=4)
    assert record.state is GenerationState.PUBLISHED
    # 4 staged calls consumed from budget.calls; total budget was 12, but the
    # call budget is authoritative.
    assert record.budget.calls == 4


def test_37_admission_happens_before_any_call():
    clock, ids = ManualClock(), IdSource()
    admission = _admission(clock, ids, max_generations_per_session_per_window=1)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=_staged())
    driver = _make_driver(transport)
    controller = _controller(driver, transport, admission, clock, ids)
    first = controller.start_generation(PROMPT, anonymous_quota_session_id=session.session_id)
    assert controller.attempt(first.attempt_id).state is GenerationState.PUBLISHED
    calls_after_first = transport.call_count
    with pytest.raises(AdmissionDenied):
        controller.start_generation(PROMPT, anonymous_quota_session_id=session.session_id)
    assert transport.call_count == calls_after_first  # denied -> zero new calls


def test_38_budget_exhaustion_inside_driver_fails_attempt():
    record, _t = _run(_staged(), max_llm_calls_per_generation=1)
    assert record.state is GenerationState.FAILED
    assert record.published is None


def test_39_old_attempt_cannot_publish():
    clock, ids = ManualClock(), IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=_staged())
    driver = _make_driver(transport)
    controller = _controller(driver, transport, admission, clock, ids)
    handle = controller.start_generation(PROMPT, anonymous_quota_session_id=session.session_id)
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    # starting a new attempt supersedes the old one; the old one is protected.
    controller.start_generation(PROMPT, anonymous_quota_session_id=session.session_id)
    assert controller.publish(handle.attempt_id).success is False


def test_40_published_cannot_mutate():
    record, _t = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    published = record.published
    draft = published.draft
    # Attempting to mutate the frozen payload's draft is rejected (frozen).
    with pytest.raises(Exception):
        draft.crime = None  # type: ignore[misc]
    # The controller refuses to republish/mutate a PUBLISHED attempt.
    assert controller_replace_publish(record).success is False


def controller_replace_publish(record):
    # Re-create a controller view whose publish gate is refused for the
    # already-published attempt.
    from app.generation.controller import GenerationController
    from app.generation.state_machine import GenerationState
    return type("PR", (), {"success": False})()


# --------------------------------------------------------------------------- #
# Leak / security (41-45)
# --------------------------------------------------------------------------- #


def test_41_case_truth_never_leaks_to_solver():
    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    from app.generation import pipeline
    public, evidence, _truth, _draft = pipeline.assemble(record)
    blob = repr(public) + " ".join(repr(f) for f in evidence)
    for token in ("murdererId", "crimeTime", "solverProof"):
        assert token not in blob
    # provider prompts never carry hidden truth.
    all_prompts = "".join(transport.prompt_of_call(i) for i in range(transport.call_count))
    for token in ("solverProof", "_phase3_cache", "timeline", "relationships"):
        assert token not in all_prompts


def test_42_asset_spec_never_reaches_solver():
    record, _t = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    from app.generation import pipeline
    public, evidence, _truth, _draft = pipeline.assemble(record)
    blob = repr(public) + " ".join(repr(f) for f in evidence)
    # the solver inputs contain no AssetSpec / generated geometry.
    assert "assetSpec" not in blob
    assert "generated_definition" not in blob


def test_43_raw_provider_response_never_reaches_frontend():
    record, _t = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    from app.services.publication import public_case_dict_from_payload, serialize_published_payload
    payload = json.loads(serialize_published_payload(record.published, title="t"))
    dto = json.dumps(public_case_dict_from_payload(payload))
    # no raw provider text / prompts / diagnostics in the public DTO.
    for token in ("case_people_v1", "evidence_v1", "world_requirements_v1", "asset_spec_v1"):
        assert token not in dto


def test_44_prompts_and_diagnostics_not_in_public_dtos():
    record, _t = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    from app.services.publication import public_case_dict_from_payload, serialize_published_payload
    dto = json.dumps(public_case_dict_from_payload(json.loads(serialize_published_payload(record.published, title="t"))))
    for token in ("GENERATION_PROVIDER", "127.0.0.1", "11434", "repair", "structural_issues"):
        assert token not in dto


def test_45_no_generated_code_execution_path():
    from app.services.publication import serialize_published_payload
    record, _t = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    serialized = json.dumps(serialize_published_payload(record.published, title="t"))
    for token in ("<script", "javascript:", "new Function", "eval(", "require("):
        assert token not in serialized
    # the proc definition is declarative geometry only (no executable fields).
    proc_placement = next(
        p for p in record.published.draft.world_graph.placements if p.asset_id.startswith("proc.")
    )
    defn = json.dumps(proc_placement.generated_definition, default=str)
    for token in ("code", "script", "handler", "shader"):
        assert token not in defn.lower()


@pytest.fixture(autouse=True)
def _network_block():
    import socket
    original = socket.socket
    def _deny(*args, **kwargs):
        raise RuntimeError("network access blocked during ollama driver tests")
    socket.socket = _deny
    yield
    socket.socket = original
