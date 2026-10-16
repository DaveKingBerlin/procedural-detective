"""Shared hermetic harness for the Phase45-Fix adversarial probes.

ZERO network / ZERO paid calls. Mirrors the new test module's helpers but adds
hostile variants and deep introspection.
"""
from __future__ import annotations

import contextlib
import copy
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = str(Path(r"C:\Users\Fujitsu\coding\procedural-detective\backend"))
TESTS = str(Path(BACKEND) / "tests")
for p in (BACKEND, TESTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import dataclasses  # noqa: E402

from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from app.domain.solver import solve_case  # noqa: E402
from app.environments.manifests import load_environment  # noqa: E402
from app.generation import (  # noqa: E402
    difficulty_policy,
    evidence_algebra,
    parser,
    pipeline,
    playability,
)
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import GeneratedDraft, PlacementSpec  # noqa: E402
from app.generation.state_machine import GenerationState, ValidationOutcome  # noqa: E402
from app.services.generation import (  # noqa: E402
    GenerationService,
    _ensure_playability_clues,
    _world_graph_namespace_violations,
)
from app.world.requirements import WorldRequirements  # noqa: E402

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)
PROVIDER_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)
_G = GOLDEN_STAGE_PAYLOADS
_LOGGER = "procedural-detective"


def _dump(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def _renamed_docs(prefix_obj="prov_obj_", prefix_ev="prov_ev_"):
    docs = {stage: json.loads(payload) for stage, payload in _G.items()}
    crime = docs[GenerationStage.CASE_TRUTH]["crime"]
    crime["weaponId"] = prefix_obj + crime["weaponId"]
    for fact in docs[GenerationStage.EVIDENCE]["evidence"]:
        fact["id"] = prefix_ev + fact["id"]
        for prop in fact["propositions"]:
            if prop.get("objectId"):
                prop["objectId"] = prefix_obj + prop["objectId"]
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        obj["objectId"] = prefix_obj + obj["objectId"]
    for placement in docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"]:
        placement["objectId"] = prefix_obj + placement["objectId"]
        if placement.get("evidenceId"):
            placement["evidenceId"] = prefix_ev + placement["evidenceId"]
    return docs


def _completion_docs(prefix_obj="prov_obj_", prefix_ev="prov_ev_"):
    docs = _renamed_docs(prefix_obj, prefix_ev)
    docs[GenerationStage.EVIDENCE] = {"evidence": []}
    for placement in docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"]:
        placement["evidenceId"] = None
        if not placement.get("interaction"):
            placement["interaction"] = "inspect"
    return docs


def completion_script():
    docs = _completion_docs()
    return {
        stage: [_dump(docs[stage])]
        for stage in (
            GenerationStage.CASE_TRUTH,
            GenerationStage.PUBLIC_WORLD,
            GenerationStage.EVIDENCE,
            GenerationStage.WORLD_GRAPH,
        )
    }


def golden_script():
    docs = {stage: json.loads(payload) for stage, payload in _G.items()}
    return {stage: [_dump(docs[stage])] for stage in docs}


def empty_placements_script():
    docs = {stage: json.loads(payload) for stage, payload in _G.items()}
    docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"] = []
    return {stage: [_dump(docs[stage])] for stage in _G}


class EventCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        if getattr(record, "pd_event", None):
            self.records.append(record)


@contextlib.contextmanager
def capture_events():
    lg = logging.getLogger(_LOGGER)
    h = EventCapture()
    prev_level = lg.level
    prev_disabled = lg.disabled
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    lg.disabled = False
    try:
        yield h.records
    finally:
        lg.removeHandler(h)
        lg.setLevel(prev_level)
        lg.disabled = prev_disabled


def events(records):
    return [getattr(r, "pd_event", None) for r in records]


def held(script, difficulty="medium", prompt=PROVIDER_PROMPT, hold=True,
         seed=11):
    clock = ManualClock(start_time=1000.0)
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=4,
        max_concurrent_generations_global=8,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_end=1000.0 + 86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=FakeProvider(script=script),
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=2,
        max_prompt_chars=4000,
        seed=seed,
        hold_before_publish=hold,
        difficulty=difficulty,
    )
    handle = controller.start_generation(
        prompt, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    if hold:
        result = controller.publish(handle.attempt_id, hold_ok=True)
        assert result.success, result.reason
        assert result.published is not None
        record.published = result.published
    return controller, record


def service(*, spec_provider=None, world_repair_provider=None):
    stub = object.__new__(GenerationService)
    stub._settings = SimpleNamespace(max_world_objects_per_kit=32)
    stub._spec_provider = spec_provider
    stub._generate_unknown_assets = False
    stub._generated_cache = None
    stub._world_repair_provider = world_repair_provider
    stub._max_world_repair_passes = 2
    stub._last_generated_composition_failed = None
    stub._last_environment_resolution = None
    return stub


class FakeStore:
    def __init__(self):
        self.case_version_states = []
        self.attempt_states = []

    def update_case_version_state(self, case_id, version, state, state_reason=None):
        self.case_version_states.append((case_id, version, state, state_reason))

    def upsert_generation_attempt(self, **kwargs):
        self.attempt_states.append(kwargs)


class FakePublication:
    def __init__(self):
        self.calls = 0
        self.published = None
        self.kwargs = None

    def publish_transactionally(self, published, **kwargs):
        self.calls += 1
        self.published = published
        self.kwargs = kwargs


def guard_stub():
    stub = object.__new__(GenerationService)
    stub._store = FakeStore()
    stub._publication = FakePublication()
    stub._last_publish_provenance = None
    stub._last_environment_resolution = None
    return stub


def publish_via_guard(stub, record, difficulty):
    return GenerationService._publish_if_ready(
        stub, record, "title", object(), 1000.0, record.case_id, 1,
        model=None, difficulty=difficulty,
    )


def repaired_draft(record, difficulty, kit="apartment"):
    return _ensure_playability_clues(
        record.published.draft, load_environment(kit), difficulty
    )


def gate3(draft, difficulty):
    public, evidence, truth = pipeline._draft_to_phase3(
        draft, case_id="CASE-FIX", title="T"
    )
    return playability.validate_playability_quality(
        draft=draft, public=public, evidence=evidence, universes=None,
        truth=truth, difficulty=difficulty,
    )


def solve(draft):
    public, evidence, _truth = pipeline._draft_to_phase3(
        draft, case_id="CASE-FIX", title="T"
    )
    return solve_case(public, evidence)


def phase3(draft):
    return pipeline._draft_to_phase3(draft, case_id="CASE-FIX", title="T")


def placement_tuple(draft):
    return tuple(
        (p.object_id, p.location_id, p.anchor, p.interaction, p.evidence_id)
        for p in draft.world_graph.placements
    )


def object_tuple(draft):
    return tuple(
        (o.object_id, o.asset_id, tuple(o.affordances), o.subtype)
        for o in draft.objects
    )


def header(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
