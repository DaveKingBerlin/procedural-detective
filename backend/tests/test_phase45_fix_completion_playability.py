"""Phase 45 Fix — Playability Reachability after Evidence Completion (ADR-006).

Hermetic, ZERO provider/network. Proves the Phase45-Fix contract:

- the observed failure shape is reproduced: deterministic evidence completion
  (``d_ev_*`` canonical facts) makes the solver UNIQUE, but a provider-authored
  world graph that links no ``evidenceId`` leaves every required dimension
  player-unreachable (Gate 3 rejects it);
- the deterministic, provider-free ``_ensure_playability_clues`` repair binds the
  WINNING fact per missing dimension + the required red herrings to DECLARED
  inspectables, meeting the Easy/Medium/Hard contracts;
- the authoritative publication guard repairs -> revalidates (Gate 1/2) ->
  re-scores (Gate 3) before publishing, and NEVER publishes a candidate with
  unreachable critical dimensions (unrepairable candidates fail closed);
- no stale derived state (the WORLD_GRAPH context consumes the completed
  ``d_ev_*`` evidence);
- meaningful-inspectable classification counts the completion evidence;
- red herrings are reachable and do not change the unique solution;
- idempotence and boundedness; no CaseTruth/evidence mutation.

Event capture is order-independent (a private handler on the service logger for
the duration of a block).
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
from app.generation.schemas import GeneratedDraft  # noqa: E402
from app.generation.state_machine import (  # noqa: E402
    GenerationState,
    ValidationOutcome,
)
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

# The renamed provider namespace breaks the kit base set (provider-authored
# world) and the "Weapon: kitchen_knife" lock would be a terminal mismatch, so
# the reproduction uses a prompt without the weapon lock.
PROVIDER_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

_G = GOLDEN_STAGE_PAYLOADS

_LOGGER = "procedural-detective"

_REQUIRED_DIMS = ("WHO", "WHY", "WEAPON", "WHEN")


# --------------------------------------------------------------------------- #
# order-independent structured-event capture
# --------------------------------------------------------------------------- #


class _EventCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list = []

    def emit(self, record) -> None:  # noqa: ANN001
        if getattr(record, "pd_event", None):
            self.records.append(record)


@contextlib.contextmanager
def _capture_events():
    service_logger = logging.getLogger(_LOGGER)
    handler = _EventCapture()
    previous_level = service_logger.level
    previous_disabled = service_logger.disabled
    service_logger.addHandler(handler)
    service_logger.setLevel(logging.DEBUG)
    service_logger.disabled = False
    try:
        yield handler.records
    finally:
        service_logger.removeHandler(handler)
        service_logger.setLevel(previous_level)
        service_logger.disabled = previous_disabled


def _events(records) -> list:  # noqa: ANN001
    return [getattr(r, "pd_event", None) for r in records]


def _event(records, name):  # noqa: ANN001
    return next(r for r in records if getattr(r, "pd_event", None) == name)


# --------------------------------------------------------------------------- #
# script builders
# --------------------------------------------------------------------------- #


def _dump(doc) -> str:  # noqa: ANN001
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def _renamed_docs(prefix_obj: str = "prov_obj_", prefix_ev: str = "prov_ev_"):
    """Golden fixture with a DISJOINT provider namespace (provider-authored)."""
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


def _completion_docs(prefix_obj: str = "prov_obj_", prefix_ev: str = "prov_ev_"):
    """The investigation reproduction shape.

    - provider-authored world namespace (no kit base objects);
    - an EMPTY provider evidence set -> deterministic completion fires;
    - a world graph that links NO ``evidenceId`` (the broken link).
    """
    docs = _renamed_docs(prefix_obj, prefix_ev)
    docs[GenerationStage.EVIDENCE] = {"evidence": []}
    for placement in docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"]:
        placement["evidenceId"] = None
        if not placement.get("interaction"):
            placement["interaction"] = "inspect"
    return docs


def _completion_script() -> dict:
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


def _empty_placements_script() -> dict:
    """Golden-namespace provider evidence, but a schema-valid EMPTY world graph."""
    docs = {stage: json.loads(payload) for stage, payload in _G.items()}
    docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"] = []
    return {stage: [_dump(docs[stage])] for stage in _G}


# --------------------------------------------------------------------------- #
# controller harness + service/guard stubs
# --------------------------------------------------------------------------- #


def _held(script: dict, difficulty: str = "medium", prompt: str = PROVIDER_PROMPT):
    """Run the real controller (completion fires pre-WORLD_GRAPH), hold, publish."""
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
        seed=11,
        hold_before_publish=True,
        difficulty=difficulty,
    )
    handle = controller.start_generation(
        prompt, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success, result.reason
    assert result.published is not None
    record.published = result.published
    return controller, record


def _service(*, world_repair_provider=None, spec_provider=None) -> GenerationService:
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


class _FakeStore:
    def __init__(self) -> None:
        self.case_version_states: list[tuple] = []
        self.attempt_states: list[dict] = []

    def update_case_version_state(self, case_id, version, state, state_reason=None):
        self.case_version_states.append((case_id, version, state, state_reason))

    def upsert_generation_attempt(self, **kwargs) -> None:
        self.attempt_states.append(kwargs)


class _FakePublication:
    def __init__(self) -> None:
        self.calls = 0
        self.published = None

    def publish_transactionally(self, published, **kwargs) -> None:
        self.calls += 1
        self.published = published


def _guard_stub() -> GenerationService:
    stub = object.__new__(GenerationService)
    stub._store = _FakeStore()
    stub._publication = _FakePublication()
    stub._last_publish_provenance = None
    stub._last_environment_resolution = None
    return stub


def _publish_via_guard(stub, record, difficulty):  # noqa: ANN001
    return GenerationService._publish_if_ready(
        stub,
        record,
        "title",
        object(),
        1000.0,
        record.case_id,
        1,
        model=None,
        difficulty=difficulty,
    )


def _repaired_draft(record, difficulty):  # noqa: ANN001
    return _ensure_playability_clues(
        record.published.draft, load_environment("apartment"), difficulty
    )


def _gate3(draft, difficulty):  # noqa: ANN001
    public, evidence, truth = pipeline._draft_to_phase3(
        draft, case_id="CASE-FIX", title="T"
    )
    return playability.validate_playability_quality(
        draft=draft,
        public=public,
        evidence=evidence,
        universes=None,
        truth=truth,
        difficulty=difficulty,
    )


# =========================================================================== #
# Test 1 — completion makes the solver unique
# =========================================================================== #


def test_1_completion_makes_solver_unique():
    _controller, record = _held(_completion_script(), "medium")
    assert record._evidence_completed is True
    report = pipeline.validate_draft(record)
    assert report.outcome is ValidationOutcome.VALID
    ids = {e.id for e in record.stage_outputs[GenerationStage.EVIDENCE].evidence}
    assert any(i.startswith("d_ev_") for i in ids)
    proof = record.solver_proof
    assert proof is not None
    assert proof.who.unique and proof.why.unique and proof.weapon.unique
    assert proof.when.ambiguous is False
    assert proof.who.winner == "thomas_reed"


def test_1_completion_is_idempotent_evidence_fixed_point():
    docs = _completion_docs()
    public, truth = _phase3_from_docs(docs)
    first = evidence_algebra.complete_evidence_if_ambiguous(
        public, truth.crime, None
    )
    assert first.changed is True
    second = evidence_algebra.complete_evidence_if_ambiguous(
        public, truth.crime, first.evidence
    )
    assert second.changed is False
    assert second.facts_added == 0
    assert tuple(f.id for f in second.evidence.evidence) == tuple(
        f.id for f in first.evidence.evidence
    )


def _phase3_from_docs(docs):  # noqa: ANN001
    crime = parser.parse_stage(GenerationStage.CASE_TRUTH, _dump(docs[GenerationStage.CASE_TRUTH]))
    pw = parser.parse_stage(
        GenerationStage.PUBLIC_WORLD, _dump(docs[GenerationStage.PUBLIC_WORLD])
    )
    ev = parser.parse_stage(GenerationStage.EVIDENCE, _dump(docs[GenerationStage.EVIDENCE]))
    draft = GeneratedDraft(
        crime=crime,
        persons=pw.persons,
        motives=pw.motives,
        objects=pw.objects,
        locations=pw.locations,
        travel_rules=pw.travel_rules,
        scene=pw.scene,
        evidence=ev.evidence,
    )
    public, _evidence, truth = pipeline._draft_to_phase3(
        draft, case_id="CASE-FIX", title="T"
    )
    return public, truth


# =========================================================================== #
# RED -> GREEN — the observed failure shape
# =========================================================================== #


def test_red_to_green_observation_shape():
    """RED (pre-fix): solver-unique but every required dimension unreachable.

    The provider-authored, link-less world is Gate-3-invalid while the canonical
    validation (Gate 1/2) is VALID and the solver is unique. GREEN (post-fix):
    the deterministic repair makes every required dimension reachable and Gate 3
    VALID without touching the evidence/solver.
    """
    _controller, record = _held(_completion_script(), "medium")
    # Gate 1/2 VALID + solver unique (the completion closed WHO/WHY/WEAPON).
    assert pipeline.validate_draft(record).outcome is ValidationOutcome.VALID
    assert record.solver_proof is not None

    raw_draft = record.published.draft
    raw = _gate3(raw_draft, "medium")
    # RED: no reachable critical dimension, no red herring.
    assert not raw.valid
    assert raw.reachable_critical_dimensions == ()
    assert set(raw.unreachable_critical_dimensions) == set(_REQUIRED_DIMS)
    assert raw.red_herring_count == 0

    # GREEN: the deterministic repair is a pure draft supplementaion.
    repaired = _ensure_playability_clues(
        raw_draft, load_environment("apartment"), "medium"
    )
    green = _gate3(repaired, "medium")
    assert green.valid, green.issue_codes
    assert green.unreachable_critical_dimensions == ()
    assert set(green.reachable_critical_dimensions) == set(_REQUIRED_DIMS)
    # The evidence is untouched (same ids) — no CaseTruth/solver mutation.
    assert tuple(e.id for e in repaired.evidence) == tuple(
        e.id for e in raw_draft.evidence
    )
    assert repaired.crime == raw_draft.crime


# =========================================================================== #
# Test 2 — completion facts are player reachable
# =========================================================================== #


def test_2_completion_facts_are_player_reachable():
    _controller, record = _held(_completion_script(), "medium")
    repaired = _repaired_draft(record, "medium")
    report = _gate3(repaired, "medium")
    assert report.valid, report.issue_codes
    assert report.unreachable_critical_dimensions == ()
    assert set(report.reachable_critical_dimensions) == set(_REQUIRED_DIMS)

    linked = {
        str(p.evidence_id)
        for p in repaired.world_graph.placements
        if p.evidence_id is not None
    }
    # At least one completion-added canonical fact is now placement-linked.
    assert any(eid.startswith("d_ev_") for eid in linked)


# =========================================================================== #
# Test 3 — forbid unique-but-unplayable
# =========================================================================== #


def test_3_unique_but_unplayable_is_repaired_then_published():
    _controller, record = _held(_completion_script(), "medium")
    raw = playability.validate_published_candidate(record.published, "medium")
    assert not raw.valid  # never silently accepted as playable

    stub = _guard_stub()
    status = _publish_via_guard(stub, record, "medium")
    assert status == GenerationState.PUBLISHED.value
    assert stub._publication.calls == 1
    published_gate3 = playability.validate_published_candidate(
        stub._publication.published, "medium"
    )
    assert published_gate3.valid, published_gate3.issue_codes
    assert published_gate3.unreachable_critical_dimensions == ()
    assert record.state is GenerationState.PUBLISHED


def test_3_unrepairable_candidate_fails_closed():
    """A placement-less world is not reachability-repairable: it must FAIL."""
    _controller, record = _held(_empty_placements_script(), "medium")
    assert playability.validate_published_candidate(record.published, "medium").valid is False
    stub = _guard_stub()
    status = _publish_via_guard(stub, record, "medium")
    assert status is None
    assert stub._publication.calls == 0
    assert record.state is GenerationState.FAILED


def test_3_repair_event_is_emitted():
    _controller, record = _held(_completion_script(), "medium")
    stub = _guard_stub()
    with _capture_events() as records:
        assert _publish_via_guard(stub, record, "medium") == (
            GenerationState.PUBLISHED.value
        )
    events = _events(records)
    assert "playability.validation.repaired" in events
    repaired_event = _event(records, "playability.validation.repaired")
    assert repaired_event.pd_fields["playabilityOutcome"] == "REPAIRED"
    assert list(repaired_event.pd_fields["unreachableCriticalDimensions"]) == []


# --------------------------------------------------------------------------- #
# ADV-45FIX-01 — the repaired candidate is scene-pinned
# --------------------------------------------------------------------------- #


def test_repair_always_pins_the_kit_scene():
    """ADV-45FIX-01 — a repaired candidate must carry a non-null kit scene."""
    _controller, record = _held(_completion_script(), "medium")
    # The frozen candidate's scene has NO kit identity (the latent shape).
    assert getattr(record.published.draft.scene, "environment_id", None) is None

    stub = _guard_stub()
    assert _publish_via_guard(stub, record, "medium") == (
        GenerationState.PUBLISHED.value
    )
    scene = stub._publication.published.draft.scene
    assert scene is not None
    assert scene.environment_id
    assert isinstance(scene.environment_version, int)
    assert scene.environment_version >= 1


def test_repair_scene_pinned_export_passes_savegame_contract():
    """ADV-45FIX-01 — the exported publicCase.scene satisfies the import
    contract (non-empty environmentId + positive environmentVersion)."""
    from app.services import savegame as sg
    from app.services.publication import serialize_published_payload

    _controller, record = _held(_completion_script(), "medium")
    stub = _guard_stub()
    assert _publish_via_guard(stub, record, "medium") == (
        GenerationState.PUBLISHED.value
    )
    payload = json.loads(
        serialize_published_payload(stub._publication.published, title="Repaired")
    )
    document = sg.project_savegame_v1(
        payload, difficulty="medium", exported_at="2026-10-16T00:00:00Z"
    )
    public_scene = document["case"]["publicCase"]["scene"]
    assert public_scene is not None
    assert isinstance(public_scene["environmentId"], str)
    assert public_scene["environmentId"] != ""
    assert isinstance(public_scene["environmentVersion"], int)
    assert public_scene["environmentVersion"] >= 1


def test_repair_unpinnable_scene_fails_closed(monkeypatch):
    """ADV-45FIX-01 — an unpinnable scene must NOT publish the repaired candidate."""
    import app.environments.compose as compose

    def _boom(*_args, **_kwargs):
        raise RuntimeError("scene cannot be resolved")

    monkeypatch.setattr(compose, "scene_for_kit", _boom)

    _controller, record = _held(_completion_script(), "medium")
    stub = _guard_stub()
    with _capture_events() as records:
        status = _publish_via_guard(stub, record, "medium")
    assert status is None
    assert stub._publication.calls == 0
    assert record.state is GenerationState.FAILED
    assert "playability.validation.failed" in _events(records)


# --------------------------------------------------------------------------- #
# ADV-45FIX-02 — non-authoritative repair revalidation prefix
# --------------------------------------------------------------------------- #


def test_repair_revalidation_uses_non_authoritative_prefix():
    """ADV-45FIX-02 — the repair-path revalidation must NOT use the authoritative
    ``generation.validation`` namespace (ADR-005 D4); the final candidate still
    emits the normal playability lifecycle."""
    _controller, record = _held(_completion_script(), "medium")
    stub = _guard_stub()
    with _capture_events() as records:
        assert _publish_via_guard(stub, record, "medium") == (
            GenerationState.PUBLISHED.value
        )
    events = _events(records)
    assert "playability.repair.validation.started" in events
    assert "playability.repair.validation.complete" in events
    # The discarded-candidate revalidation never masquerades as authoritative.
    assert "generation.validation.started" not in events
    assert "generation.validation.complete" not in events
    # The final authoritative candidate still emits the normal playability flow.
    assert "playability.validation.repaired" in events
    assert "playability.validation.complete" in events


def test_normal_authoritative_validation_events_still_emitted_during_run():
    """The controller lifecycle still emits the normal authoritative
    ``generation.validation.*`` events (the repair prefix is additive)."""
    with _capture_events() as records:
        _controller, record = _held(_completion_script(), "medium")
    events = _events(records)
    assert "generation.validation.started" in events
    assert "generation.validation.complete" in events


# =========================================================================== #
# Test 4 — no stale derived state
# =========================================================================== #


def test_4_world_graph_context_consumes_completed_evidence():
    docs = _completion_docs()
    attempt = pipeline.AttemptRecord(
        attempt_id="GA-4", case_id="CASE-4", session_id="Q", difficulty="medium"
    )
    attempt.prompt = PROVIDER_PROMPT
    for stage in (
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
    ):
        attempt.stage_outputs[stage] = parser.parse_stage(stage, _dump(docs[stage]))

    # BEFORE completion: the world-graph context carries NO canonical completion
    # fact (the provider evidence was empty).
    before = pipeline.build_request(attempt, GenerationStage.WORLD_GRAPH)
    assert "d_ev_when_last_seen" not in before.prompt_context

    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is True
    after = pipeline.build_request(attempt, GenerationStage.WORLD_GRAPH)
    assert "d_ev_when_last_seen" in after.prompt_context
    assert "d_ev_weapon_true" in after.prompt_context


# =========================================================================== #
# Test 5 — meaningful inspectable coverage (semantic classification)
# =========================================================================== #


def test_5_completion_evidence_counts_as_meaningful_inspectables():
    _controller, record = _held(_completion_script(), "medium")
    repaired = _repaired_draft(record, "medium")
    report = _gate3(repaired, "medium")

    # Semantic classification (not a raw object count): the linked RECORD ids
    # include the completion-generated canonical facts.
    discoverable_ids = frozenset(
        fact.id
        for fact in pipeline._draft_to_phase3(
            repaired, case_id="C", title="T"
        )[1]
        if getattr(fact, "discoverable", True) is not False
    )
    linked_records, _no_link = playability._meaningful_accounting(
        repaired, discoverable_ids
    )
    assert any(rid.startswith("d_ev_") for rid in linked_records)
    assert report.meaningful_inspectable_count >= (
        difficulty_policy.policy_for("medium").min_meaningful_inspectables
    )


# =========================================================================== #
# Test 6 — difficulty matrix (parameterized)
# =========================================================================== #


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_6_difficulty_contract_is_met(difficulty):
    _controller, record = _held(_completion_script(), difficulty)
    repaired = _repaired_draft(record, difficulty)
    report = _gate3(repaired, difficulty)
    policy = difficulty_policy.policy_for(difficulty)
    assert report.valid, (difficulty, report.issue_codes)
    assert report.unreachable_critical_dimensions == ()
    assert report.inspectable_count >= policy.min_inspectable_objects
    assert report.meaningful_inspectable_count >= policy.min_meaningful_inspectables
    assert report.distinct_meaningful_source_count >= (
        policy.min_distinct_meaningful_sources
    )
    assert report.red_herring_count >= policy.min_red_herrings


# =========================================================================== #
# Test 7 — red herring contract
# =========================================================================== #


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_7_red_herring_contract(difficulty):
    _controller, record = _held(_completion_script(), difficulty)
    repaired = _repaired_draft(record, difficulty)
    report = _gate3(repaired, difficulty)
    policy = difficulty_policy.policy_for(difficulty)
    assert report.red_herring_count >= policy.min_red_herrings
    if policy.min_red_herrings == 0:
        # Easy requires none: absence is fine and must not fail.
        assert report.valid

    # Every red herring is placement-linked and its subject is an EXCLUDED,
    # non-winning solver candidate.
    public, evidence, truth = pipeline._draft_to_phase3(
        repaired, case_id="C", title="T"
    )
    proof = solve_case(public, evidence)
    excluded = playability._excluded_candidate_ids(proof)
    winners = playability._winner_ids(proof)
    evidence_by_id = {f.id: f for f in evidence}
    linked = {
        str(p.evidence_id)
        for p in repaired.world_graph.placements
        if p.evidence_id is not None
    }
    herrings = [
        fid
        for fid in linked
        if any(
            subject in excluded and subject not in winners
            for subject in playability._subjects_of_facts(evidence_by_id, (fid,))
        )
    ]
    assert len(herrings) >= policy.min_red_herrings


def test_7_red_herrings_do_not_change_the_unique_solution():
    _controller, record = _held(_completion_script(), "hard")
    raw = record.published.draft
    before = solve_case(
        *pipeline._draft_to_phase3(raw, case_id="C", title="T")[0:2]
    )
    repaired = _ensure_playability_clues(
        raw, load_environment("apartment"), "hard"
    )
    public, evidence, _truth = pipeline._draft_to_phase3(
        repaired, case_id="C", title="T"
    )
    after = solve_case(public, evidence)
    assert after.who.winner == before.who.winner == "thomas_reed"
    assert after.why.winner == before.why.winner == "cover_up_embezzlement"
    assert after.weapon.winner == before.weapon.winner


# =========================================================================== #
# Test 8 — idempotence
# =========================================================================== #


def test_8_repair_is_idempotent_no_duplicates():
    _controller, record = _held(_completion_script(), "medium")
    raw = record.published.draft
    once = _ensure_playability_clues(raw, load_environment("apartment"), "medium")
    twice = _ensure_playability_clues(once, load_environment("apartment"), "medium")
    assert twice is once or twice == once
    assert len(set(o.object_id for o in twice.objects)) == len(twice.objects)
    assert len(set(p.object_id for p in twice.world_graph.placements)) == len(
        twice.world_graph.placements
    )


def test_8_already_valid_candidate_is_a_noop():
    """A Gate-3-valid candidate is returned byte-identical (golden world)."""
    _controller, record = _held(_completion_script(), "medium")
    repaired = _repaired_draft(record, "medium")
    repeat = _ensure_playability_clues(
        repaired, load_environment("apartment"), "medium"
    )
    assert repeat is repaired or repeat == repaired


# =========================================================================== #
# boundedness + namespace guard + no mutation invariants
# =========================================================================== #


@pytest.mark.parametrize(
    "difficulty,expected_added", [("easy", 4), ("medium", 5), ("hard", 6)]
)
def test_repair_is_bounded_by_missing_dims_plus_red_herring_shortfall(
    difficulty, expected_added
):
    _controller, record = _held(_completion_script(), difficulty)
    raw = record.published.draft
    repaired = _ensure_playability_clues(
        raw, load_environment("apartment"), difficulty
    )
    added = len(repaired.world_graph.placements) - len(raw.world_graph.placements)
    assert added == expected_added
    # bounded by (missing dims = 4) + (red-herring shortfall)
    policy = difficulty_policy.policy_for(difficulty)
    assert added <= 4 + policy.min_red_herrings


def test_added_placements_preserve_the_namespace_guard():
    _controller, record = _held(_completion_script(), "medium")
    repaired = _repaired_draft(record, "medium")
    assert _world_graph_namespace_violations(repaired) == ()
    declared_objects = {o.object_id for o in repaired.objects}
    declared_evidence = {e.id for e in repaired.evidence}
    declared_locations = {
        loc.location_id for loc in repaired.world_graph.locations
    }
    for placement in repaired.world_graph.placements:
        assert placement.object_id in declared_objects
        assert placement.location_id in declared_locations
        if placement.evidence_id is not None:
            assert placement.evidence_id in declared_evidence


def test_repair_does_not_mutate_casetruth_or_evidence():
    _controller, record = _held(_completion_script(), "medium")
    before_truth = record.published.truth
    before_evidence = tuple(record.published.evidence)
    _repaired_draft(record, "medium")
    assert record.published.truth is before_truth
    assert tuple(record.published.evidence) == before_evidence


# =========================================================================== #
# end-to-end acceptance — the COMPLETE real pipeline
# =========================================================================== #


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_e2e_prompt_to_playability(difficulty):
    """prompt -> generation -> completion -> solver -> world_graph ->
    validation -> playability (deterministic FakeProvider, no live LLM)."""
    controller, record = _held(_completion_script(), difficulty)

    # generation (Gate 1) + solver (Gate 2): unique.
    assert record.solver_proof is not None
    report = pipeline.validate_draft(record)
    assert report.outcome is ValidationOutcome.VALID
    assert record.solver_proof.who.unique is True

    # evidence completion happened (canonical d_ev_* facts installed).
    assert record._evidence_completed is True
    assert any(
        e.id.startswith("d_ev_")
        for e in record.stage_outputs[GenerationStage.EVIDENCE].evidence
    )

    # service composition (provider-authored repair) + publication guard.
    service = _service()
    with _capture_events() as records:
        assert service._apply_kit_composition(
            record, "apartment", WorldRequirements(), None
        )
    assert "world_compose.completed" in _events(records)
    completed = _event(records, "world_compose.completed")
    assert completed.pd_fields["compositionOutcome"] == "REPAIRED_PLAYABILITY"

    gate3 = playability.validate_published_candidate(record.published, difficulty)
    assert gate3.valid, gate3.issue_codes
    assert gate3.unreachable_critical_dimensions == ()
    assert set(gate3.reachable_critical_dimensions) == set(_REQUIRED_DIMS)

    stub = _guard_stub()
    status = _publish_via_guard(stub, record, difficulty)
    assert status == GenerationState.PUBLISHED.value
    assert stub._publication.calls == 1
    # The persisted candidate is Gate 3 valid with no unreachable dimension.
    persisted = playability.validate_published_candidate(
        stub._publication.published, difficulty
    )
    assert persisted.valid
    assert persisted.unreachable_critical_dimensions == ()


def test_e2e_solver_outcome_stays_unique_after_repair():
    _controller, record = _held(_completion_script(), "medium")
    service = _service()
    service._apply_kit_composition(record, "apartment", WorldRequirements(), None)
    public, evidence, _truth = pipeline._draft_to_phase3(
        record.published.draft, case_id="C", title="T"
    )
    proof = solve_case(public, evidence)
    assert proof.who.unique and proof.why.unique and proof.weapon.unique
    assert proof.when.ambiguous is False