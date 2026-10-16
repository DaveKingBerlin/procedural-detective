"""Phase 45 — World-Compose Ownership & Publication Consistency (DEF-076).

Hermetic, ZERO provider/network. Proves ADR-005 (Model B — explicit,
observable composition skip for already-authored provider worlds) and the
Phase45 acceptance criteria:

- a provider-authored canonical world (object/evidence ids disjoint from the
  golden kit namespace) is NEVER rebuilt with foreign kit ids; composition
  skips explicitly and the canonical aggregate stays authoritative;
- the discarded composed candidate can no longer leak foreign ids, mutate the
  authoritative ``generation.validation`` report, or emit a late misleading
  ``generation.validation.complete=RECOVERABLE_REPAIR``;
- composition validation is observed under its own ``world_compose.validation``
  namespace, separate from ``generation.validation``;
- the defense-in-depth namespace guard rejects any composed placement that
  references an undeclared object/location/evidence id;
- golden/demo composition still succeeds, Phase44 Gate 3 is re-evaluated on the
  final composed candidate and a regression falls back to the canonical
  aggregate;
- composition is deterministic/idempotent and never mutates CaseTruth.

Event capture is order-independent (a private handler attached to the service
logger for the duration of a block) so a leaked logger level / alembic's
in-process root reconfiguration can never make these assertions flaky.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from app.generation import pipeline, playability  # noqa: E402
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import PlacementSpec  # noqa: E402
from app.generation.state_machine import (  # noqa: E402
    GenerationState,
    ValidationOutcome,
)
from app.services.generation import GenerationService  # noqa: E402
from app.world.composer import WorldComposition  # noqa: E402
from app.world.requirements import (  # noqa: E402
    CRITICALITY_REQUIRED,
    ObjectRequest,
    WorldRequirements,
)

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

# The provider-authored fixtures rename the weapon object id, so the locked
# "Weapon: kitchen_knife" constraint would (correctly) be a TERMINAL locked
# mismatch. The DEF-076 reproduction uses a prompt without the weapon lock —
# the canonical renamed draft is still Gate 1/2/3 VALID.
PROVIDER_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

_LOGGER = "procedural-detective"


# --------------------------------------------------------------------------- #
# order-independent structured-event capture
# --------------------------------------------------------------------------- #


class _EventCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list = []

    def emit(self, record) -> None:
        if getattr(record, "pd_event", None):
            self.records.append(record)


@contextlib.contextmanager
def _capture_events():
    """Capture ``pd_event`` log records WITHOUT depending on caplog/global state.

    A private handler is attached to the service logger only for the duration
    of the block, so events emitted outside the block (e.g. during the
    controller run) never leak in, and alembic's in-process root reconfiguration
    or a leaked elevated logger level can never make the assertions flaky.
    """
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


def _events(records) -> list:
    return [getattr(r, "pd_event", None) for r in records]


def _event(records, name):
    return next(r for r in records if getattr(r, "pd_event", None) == name)


# --------------------------------------------------------------------------- #
# script builders (golden + renamed-namespace provider fixtures)
# --------------------------------------------------------------------------- #


def _dump(doc) -> str:
    return json.dumps(doc, sort_keys=True)


def _golden_script() -> dict:
    return {stage: [payload] for stage, payload in GOLDEN_STAGE_PAYLOADS.items()}


def _renamed_docs(prefix_obj: str, prefix_ev: str) -> dict:
    """Golden fixture with a DISJOINT provider namespace (prov_obj_*/prov_ev_*)."""
    docs = {
        stage: json.loads(payload)
        for stage, payload in GOLDEN_STAGE_PAYLOADS.items()
    }
    crime = docs[GenerationStage.CASE_TRUTH].get("crime") or {}
    if crime.get("weaponId"):
        crime["weaponId"] = prefix_obj + crime["weaponId"]
    for fact in docs[GenerationStage.EVIDENCE].get("evidence") or ():
        fact["id"] = prefix_ev + fact["id"]
        for prop in fact.get("propositions") or ():
            if prop.get("objectId"):
                prop["objectId"] = prefix_obj + prop["objectId"]
    for obj in docs[GenerationStage.PUBLIC_WORLD].get("objects") or ():
        obj["objectId"] = prefix_obj + obj["objectId"]
    world_graph = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    for placement in world_graph["placements"]:
        placement["objectId"] = prefix_obj + placement["objectId"]
        if placement.get("evidenceId"):
            placement["evidenceId"] = prefix_ev + placement["evidenceId"]
    return docs


def _renamed_script(
    prefix_obj: str = "prov_obj_", prefix_ev: str = "prov_ev_"
) -> dict:
    return {
        stage: [_dump(doc)]
        for stage, doc in _renamed_docs(prefix_obj, prefix_ev).items()
    }


def _empty_placements_script() -> dict:
    """Golden with a schema-valid EMPTY world graph (Gate 3 empty-world)."""
    script = _golden_script()
    world_graph = json.loads(script[GenerationStage.WORLD_GRAPH][0])
    world_graph["worldGraph"]["placements"] = []
    script[GenerationStage.WORLD_GRAPH] = [_dump(world_graph)]
    return script


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


def _held(script: dict, difficulty: str = "medium", prompt: str = GOLDEN_PROMPT):
    """Run the real controller, hold at the publish gate, then build the frozen
    canonical aggregate (the exact candidate the service composes)."""
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


def _decorative_reqs() -> WorldRequirements:
    return WorldRequirements(
        objects=(ObjectRequest(requested_name="mystery gadget"),)
    )


def _placement_signature(draft):
    return tuple(
        (
            p.object_id,
            p.asset_id,
            p.location_id,
            p.interaction,
            p.evidence_id,
        )
        for p in draft.world_graph.placements
    )


def _assert_world_preserved(canonical_draft, published):
    """The canonical world graph/objects/evidence are kept verbatim (no foreign
    IDs) — only the presentation scene may gain the kit pin."""
    assert published.draft.objects == canonical_draft.objects
    assert published.draft.evidence == canonical_draft.evidence
    assert published.draft.world_graph.locations == canonical_draft.world_graph.locations
    assert published.draft.world_graph.placements == canonical_draft.world_graph.placements


def _assert_scene_pinned(published, kit_id: str):
    """ADV-45-01 / DEF-109 — the published scene carries a non-null kit identity
    (the frontend/savegame import contract requires a non-empty environmentId
    and a positive environmentVersion)."""
    scene = published.draft.scene
    assert scene is not None
    assert scene.environment_id == kit_id
    assert isinstance(scene.environment_version, int)
    assert scene.environment_version >= 1


def _fake_composition(*, placements=(), new_objects=(), issues=(), resolved=None):
    def _factory(*_args, **_kwargs):
        return WorldComposition(
            environment_id="apartment",
            kit_version=1,
            environment_provenance="EXACT",
            placements=tuple(placements),
            new_objects=tuple(new_objects),
            provenance_by_object_id={},
            relation_satisfied={},
            relation_notes=(),
            resolution_record={"resolved": dict(resolved or {})},
            issues=tuple(issues),
            composition_notes=(),
        )

    return _factory


# --------------------------------------------------------------------------- #
# publication guard stubs (mirror the Phase44 authoritative guard)
# --------------------------------------------------------------------------- #


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


def _guard_stub():
    stub = object.__new__(GenerationService)
    stub._store = _FakeStore()
    stub._publication = _FakePublication()
    stub._last_publish_provenance = None
    return stub


def _publish_via_guard(stub, record, difficulty):
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


# =========================================================================== #
# A. validate_draft event namespace (Phase45 §14 / ADR-005 D4)
# =========================================================================== #


def test_validate_draft_default_prefix_emits_generation_namespace():
    _controller, record = _held(_golden_script(), "medium")
    with _capture_events() as records:
        pipeline.validate_draft(record)
    events = _events(records)
    assert "generation.validation.started" in events
    assert "generation.validation.complete" in events
    assert "world_compose.validation.started" not in events


def test_validate_draft_custom_prefix_separates_namespace():
    _controller, record = _held(_golden_script(), "medium")
    with _capture_events() as records:
        pipeline.validate_draft(record, event_prefix="world_compose.validation")
    events = _events(records)
    assert "world_compose.validation.started" in events
    assert "world_compose.validation.complete" in events
    # the authoritative generation namespace is NOT reused
    assert "generation.validation.started" not in events
    assert "generation.validation.complete" not in events


# =========================================================================== #
# B. namespace guard helper (ADR-005 D1) — unit
# =========================================================================== #


def test_namespace_violations_clean_for_canonical_draft():
    from app.services.generation import _world_graph_namespace_violations

    _controller, record = _held(_golden_script(), "medium")
    assert _world_graph_namespace_violations(record.published.draft) == ()


def test_namespace_violations_detects_foreign_object_location_and_evidence():
    from app.services.generation import _world_graph_namespace_violations

    _controller, record = _held(_golden_script(), "medium")
    draft = record.published.draft
    foreign = PlacementSpec(
        object_id="golden_foreign_object",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="foreign_zone_xyz",
        anchor="kitchen_counter",
        interaction="",
        evidence_id="golden_foreign_evidence",
    )
    bad = dataclasses.replace(
        draft,
        world_graph=dataclasses.replace(
            draft.world_graph,
            placements=(*draft.world_graph.placements, foreign),
        ),
    )
    violations = _world_graph_namespace_violations(bad)
    assert any("objectId" in v for v in violations)
    assert any("locationId" in v for v in violations)
    assert any("evidenceId" in v for v in violations)
    # the tokens carry NO id values (never leak the mismatching identifiers)
    joined = " ".join(violations)
    assert "golden_foreign_object" not in joined
    assert "golden_foreign_evidence" not in joined
    assert "foreign_zone_xyz" not in joined


# =========================================================================== #
# C. DEF-076 red→green (Phase45 §32) — explicit provider-authored skip
# =========================================================================== #


def test_def076_provider_authored_world_skips_explicitly():
    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    canonical_draft = record.draft
    canonical_report = record.last_validation
    canonical_proof = record.solver_proof
    canonical_cache = record._phase3_cache
    declared = {o.object_id for o in canonical_draft.objects}

    service = _service()
    with _capture_events() as records:
        ok = service._apply_kit_composition(
            record, "apartment", _decorative_reqs(), None
        )

    assert ok is True
    events = _events(records)
    assert "world_compose.started" in events
    assert "world_compose.skipped" in events
    # NO composed-candidate validation was ever attempted
    assert "world_compose.validation.started" not in events
    assert "world_compose.validation.complete" not in events
    assert "world_compose.fallback" not in events
    assert "world_compose.failed" not in events

    skip = _event(records, "world_compose.skipped")
    assert skip.pd_fields["reasonCode"] == "PROVIDER_AUTHORED_WORLD"
    assert skip.pd_fields["compositionOutcome"] == "SKIPPED_PROVIDER_AUTHORED_WORLD"
    assert skip.pd_fields["fallbackUsed"] is False

    # the canonical attempt state is fully preserved (no leak)
    assert record.draft is canonical_draft
    assert record.last_validation is canonical_report
    assert record.solver_proof is canonical_proof
    assert record._phase3_cache is canonical_cache
    # the canonical world content is preserved; only the scene gains the kit pin
    _assert_world_preserved(canonical_draft, record.published)
    _assert_scene_pinned(record.published, "apartment")
    # every persisted placement stays inside the provider's declared namespace
    for placement in record.published.draft.world_graph.placements:
        assert placement.object_id in declared


def test_def076_no_late_misleading_generation_validation_failure():
    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    service = _service()
    with _capture_events() as records:
        service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)

    # NO composition candidate is validated through the authoritative channel
    late = [
        r
        for r in records
        if getattr(r, "pd_event", None) == "generation.validation.complete"
    ]
    assert all(r.pd_fields.get("validationOutcome") == "VALID" for r in late)
    assert all(
        r.pd_fields.get("validationOutcome") != "RECOVERABLE_REPAIR" for r in late
    )
    # the authoritative report is unchanged and still VALID
    assert record.last_validation.outcome is ValidationOutcome.VALID
    assert pipeline.validate_draft(record).outcome is ValidationOutcome.VALID


def test_def076_canonical_world_preserved_and_scene_pinned_on_skip():
    """ADV-45-01 / DEF-109 — a provider-authored skip keeps the canonical world
    graph/objects/evidence byte-for-byte and pins the resolved kit scene."""
    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    before = record.published.draft
    service = _service()
    service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)
    _assert_world_preserved(before, record.published)
    # the world graph itself is byte-identical (only the scene gains the pin)
    assert record.published.draft.world_graph == before.world_graph
    assert record.published.draft.world_graph is before.world_graph
    _assert_scene_pinned(record.published, "apartment")


def test_def076_provider_ids_unchanged_after_skip():
    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    before_objects = tuple(o.object_id for o in record.published.draft.objects)
    before_evidence = tuple(e.id for e in record.published.draft.evidence)
    assert all(o.startswith("prov_obj_") for o in before_objects)
    assert all(e.startswith("prov_ev_") for e in before_evidence)

    service = _service()
    service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)

    assert tuple(o.object_id for o in record.published.draft.objects) == before_objects
    assert tuple(e.id for e in record.published.draft.evidence) == before_evidence


# =========================================================================== #
# D. golden composition still succeeds + semantic/visual id integrity
# =========================================================================== #


def test_golden_case_composition_succeeds():
    _controller, record = _held(_golden_script(), "medium")
    service = _service()
    with _capture_events() as records:
        ok = service._apply_kit_composition(
            record, "apartment", _decorative_reqs(), None
        )
    assert ok is True
    events = _events(records)
    assert "world_compose.completed" in events
    assert "world_compose.skipped" not in events
    completed = _event(records, "world_compose.completed")
    assert completed.pd_fields["compositionOutcome"] == "COMPOSED"
    assert completed.pd_fields["fallbackUsed"] is False
    # the composed draft validated under the dedicated namespace
    assert "world_compose.validation.started" in events
    assert "world_compose.validation.complete" in events
    assert record.last_validation.outcome is ValidationOutcome.VALID
    # the scene carries the resolved kit identity
    assert record.published.draft.scene.environment_id == "apartment"


def test_semantic_object_ids_remain_distinct_from_visual_asset_ids():
    _controller, record = _held(_golden_script(), "medium")
    service = _service()
    service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)
    by_id = {p.object_id: p for p in record.published.draft.world_graph.placements}
    knife = by_id["kitchen_knife"]
    # semantic identity stays semantic; the visual asset is separate
    assert knife.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert knife.asset_id != knife.object_id
    # no placement uses a visual asset id as its semantic object id
    assert all(not p.object_id.startswith("PROP_") for p in by_id.values())


def test_category_fallback_does_not_rename_semantics():
    """A prompt-specific object resolved through a fallback keeps its semantic id."""
    from app.assets.catalog import load_catalog_from_repo
    from app.environments.manifests import load_environment
    from app.world.composer import KnownObjectSpecProvider, compose_world

    _controller, record = _held(_golden_script(), "medium")
    placements = record.published.draft.world_graph.placements
    composition = compose_world(
        WorldRequirements(objects=(ObjectRequest(requested_name="Custom Trophy"),)),
        spec_provider=KnownObjectSpecProvider(),
        evidence_placements=placements,
        catalog=load_catalog_from_repo(),
        kit=load_environment("apartment"),
    )
    assert composition.issues == ()
    placement = next(
        p for p in composition.placements if p.object_id == "custom_trophy"
    )
    # semantic id is the slug of the requested name — NEVER the proc.* asset id
    assert placement.asset_id.startswith("proc.")
    assert placement.asset_id != placement.object_id


def test_composition_does_not_mutate_casetruth():
    _controller, record = _held(_golden_script(), "medium")
    before_crime = record.published.truth.crime
    service = _service()
    service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)
    assert record.published.truth.crime == before_crime


# =========================================================================== #
# E. defense-in-depth namespace guard (ADR-005 D1)
# =========================================================================== #


def test_foreign_composed_placement_is_rejected_and_skipped(monkeypatch):
    _controller, record = _held(_golden_script(), "medium")
    canonical_draft = record.draft
    foreign = PlacementSpec(
        object_id="golden_foreign_object",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="foreign_zone_xyz",
        anchor="kitchen_counter",
        interaction="",
        evidence_id=None,
    )
    monkeypatch.setattr(
        "app.world.composer.compose_world",
        _fake_composition(placements=(foreign,)),
    )
    service = _service()
    with _capture_events() as records:
        ok = service._apply_kit_composition(
            record, "apartment", _decorative_reqs(), None
        )

    assert ok is True  # explicit skip, not a failure
    events = _events(records)
    skip = _event(records, "world_compose.skipped")
    assert skip.pd_fields["reasonCode"] == "COMPOSE_NAMESPACE_MISMATCH"
    assert skip.pd_fields["compositionOutcome"] == "SKIPPED_NAMESPACE_MISMATCH"
    # no validation event for the discarded candidate
    assert "world_compose.validation.started" not in events
    # the canonical world (no foreign ID) is kept WITH the scene pin
    _assert_world_preserved(canonical_draft, record.published)
    _assert_scene_pinned(record.published, "apartment")


# =========================================================================== #
# F. fallback / failure paths preserve the canonical candidate (ADR-005 D5)
# =========================================================================== #


def test_composition_issues_degrade_to_canonical_fallback(monkeypatch):
    _controller, record = _held(_golden_script(), "medium")
    canonical_draft = record.draft
    canonical_report = record.last_validation
    canonical_proof = record.solver_proof
    monkeypatch.setattr(
        "app.world.composer.compose_world",
        _fake_composition(issues=("world.invalid-placement: synthetic",)),
    )
    service = _service()
    with _capture_events() as records:
        ok = service._apply_kit_composition(
            record, "apartment", _decorative_reqs(), None
        )
    assert ok is False
    fallback = _event(records, "world_compose.fallback")
    assert fallback.pd_fields["reasonCode"] == "COMPOSE_ISSUES"
    assert fallback.pd_fields["fallbackUsed"] is True
    # canonical state restored; only the presentation scene is pinned
    assert record.draft is canonical_draft
    assert record.last_validation is canonical_report
    assert record.solver_proof is canonical_proof
    assert record.published.draft.objects == canonical_draft.objects
    assert (
        record.published.draft.world_graph.placements
        == canonical_draft.world_graph.placements
    )


def test_terminal_unresolved_required_marks_failed(monkeypatch):
    _controller, record = _held(_golden_script(), "medium")
    monkeypatch.setattr(
        "app.world.composer.compose_world",
        _fake_composition(issues=("world.unresolved-object: synthetic",)),
    )
    service = _service()
    required = WorldRequirements(
        objects=(
            ObjectRequest(
                requested_name="quantum woggle", criticality=CRITICALITY_REQUIRED
            ),
        )
    )
    with _capture_events() as records:
        ok = service._apply_kit_composition(record, "apartment", required, None)
    assert ok is False
    assert record.state is GenerationState.FAILED
    failed = _event(records, "world_compose.failed")
    assert failed.pd_fields["reasonCode"] == "TERMINAL_REQUIRED_OBJECT_UNRESOLVED"


# =========================================================================== #
# G. Gate 3 interaction (Phase45 §21/§22, ADR-005 D3)
# =========================================================================== #


def test_gate3_regression_falls_back_to_canonical(monkeypatch):
    """A composition that would collapse the world below Gate 3 (orphaned
    evidence / too few inspectables) falls back to the canonical aggregate."""
    _controller, record = _held(_golden_script(), "medium")
    canonical_draft = record.draft
    # A "composed" candidate with NO placements (the composition orphaned every
    # evidence link) still passes the deterministic validator (solver does not
    # read placements) — only Gate 3 rejects it.
    monkeypatch.setattr(
        "app.world.composer.compose_world",
        _fake_composition(placements=()),
    )
    service = _service()
    with _capture_events() as records:
        ok = service._apply_kit_composition(
            record, "apartment", _decorative_reqs(), None
        )

    assert ok is False
    fallback = _event(records, "world_compose.fallback")
    assert fallback.pd_fields["reasonCode"] == "COMPOSE_GATE3_REGRESSION"
    # canonical state restored (never publish a Gate-3-regressed composition)
    assert record.draft is canonical_draft
    gate3 = playability.validate_published_candidate(record.published, "medium")
    assert gate3.valid, gate3.issue_codes


def test_composed_candidate_is_gate3_valid_when_composition_succeeds():
    _controller, record = _held(_golden_script(), "medium")
    service = _service()
    service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)
    gate3 = playability.validate_published_candidate(record.published, "medium")
    assert gate3.valid, gate3.issue_codes
    assert gate3.inspectable_count >= 5


# =========================================================================== #
# H. determinism / idempotence / ordering (Phase45 §23/§24)
# =========================================================================== #


def test_composition_is_deterministic_across_independent_runs():
    def _run():
        _controller, record = _held(_golden_script(), "medium")
        service = _service()
        assert service._apply_kit_composition(
            record, "apartment", _decorative_reqs(), None
        )
        return _placement_signature(record.published.draft)

    assert _run() == _run()


def test_repeated_composition_is_idempotent():
    _controller, record = _held(_golden_script(), "medium")
    service = _service()
    assert service._apply_kit_composition(
        record, "apartment", _decorative_reqs(), None
    )
    first = _placement_signature(record.published.draft)
    assert service._apply_kit_composition(
        record, "apartment", _decorative_reqs(), None
    )
    assert _placement_signature(record.published.draft) == first


# =========================================================================== #
# I. publication cases A–D (Phase45 §33)
# =========================================================================== #


def test_case_a_composition_succeeds_and_publishes():
    _controller, record = _held(_golden_script(), "medium")
    service = _service()
    assert service._apply_kit_composition(
        record, "apartment", _decorative_reqs(), None
    )
    stub = _guard_stub()
    assert _publish_via_guard(stub, record, "medium") == GenerationState.PUBLISHED.value
    assert stub._publication.calls == 1


def test_case_b_optional_composition_skipped_publishes_canonical():
    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    canonical_draft = record.draft
    service = _service()
    assert service._apply_kit_composition(
        record, "apartment", _decorative_reqs(), None
    )
    stub = _guard_stub()
    assert _publish_via_guard(stub, record, "medium") == GenerationState.PUBLISHED.value
    # the persisted candidate is the canonical world (no foreign IDs) with the
    # resolved kit scene pin
    _assert_world_preserved(canonical_draft, stub._publication.published)
    _assert_scene_pinned(stub._publication.published, "apartment")
    assert stub._publication.calls == 1


def test_case_c_optional_composition_fallback_publishes_canonical(monkeypatch):
    _controller, record = _held(_golden_script(), "medium")
    monkeypatch.setattr(
        "app.world.composer.compose_world",
        _fake_composition(issues=("world.invalid-placement: synthetic",)),
    )
    service = _service()
    assert (
        service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)
        is False
    )
    stub = _guard_stub()
    assert _publish_via_guard(stub, record, "medium") == GenerationState.PUBLISHED.value
    assert stub._publication.calls == 1
    # the persisted candidate is the canonical one (only the scene pin differs)
    assert stub._publication.published.draft.objects == record.published.draft.objects


def test_case_d_invalid_final_candidate_is_not_published():
    _controller, record = _held(_empty_placements_script(), "medium")
    stub = _guard_stub()
    status = _publish_via_guard(stub, record, "medium")
    assert status is None
    assert stub._publication.calls == 0
    assert record.state is GenerationState.FAILED


# =========================================================================== #
# J. provider neutrality (Phase45 §17/§34)
# =========================================================================== #


@pytest.mark.parametrize(
    "prefix_obj,prefix_ev",
    [
        ("prov_obj_", "prov_ev_"),
        ("fr_obj_", "fr_ev_"),
        ("ol_obj_", "ol_ev_"),
        ("demo_obj_", "demo_ev_"),
    ],
)
def test_provider_authored_worlds_skip_by_representation(prefix_obj, prefix_ev):
    script = _renamed_script(prefix_obj=prefix_obj, prefix_ev=prefix_ev)
    _controller, record = _held(script, "medium", prompt=PROVIDER_PROMPT)
    service = _service()
    with _capture_events() as records:
        ok = service._apply_kit_composition(
            record, "apartment", _decorative_reqs(), None
        )
    assert ok is True
    skip = _event(records, "world_compose.skipped")
    assert skip.pd_fields["reasonCode"] == "PROVIDER_AUTHORED_WORLD"


def test_fake_demo_composition_still_composes():
    _controller, record = _held(_golden_script(), "medium")
    service = _service()
    with _capture_events() as records:
        service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)
    assert "world_compose.completed" in _events(records)


# =========================================================================== #
# K. observability (Phase45 §15/§41)
# =========================================================================== #


def test_world_compose_safe_fields_registered():
    from app.core import observability

    for field in (
        "compositionMode",
        "environmentKit",
        "inputObjectCount",
        "inputEvidenceCount",
        "inputPlacementCount",
        "outputPlacementCount",
        "canonicalObjectNamespaceCount",
        "canonicalEvidenceNamespaceCount",
        "compositionOutcome",
        "fallbackUsed",
        "reasonCode",
        "validatorCodes",
    ):
        assert field in observability._SAFE_FIELDS


def test_world_compose_event_namespaces_are_separate():
    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    service = _service()
    with _capture_events() as records:
        service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)

    events = _events(records)
    # a provider-authored skip emits its own namespace, never a validation event
    assert "world_compose.started" in events
    assert "world_compose.skipped" in events
    assert not any(e and e.startswith("world_compose.validation") for e in events)
    assert "generation.validation.complete" not in events


def test_world_compose_started_fields_are_bounded():
    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    service = _service()
    with _capture_events() as records:
        service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)
    started = _event(records, "world_compose.started")
    assert started.pd_fields["environmentKit"] == "apartment"
    assert started.pd_fields["compositionMode"] == "PROVIDER_AUTHORED"
    assert started.pd_fields["canonicalObjectNamespaceCount"] > 0
    assert started.pd_fields["canonicalEvidenceNamespaceCount"] > 0


# =========================================================================== #
# L. scene pin + savegame import contract (ADV-45-01 / DEF-109)
# =========================================================================== #


def test_provider_authored_skip_scene_pinned_and_savegame_export_round_trips():
    """The skipped provider-authored case carries a non-null kit scene, and the
    exported SavegameV1 publicCase.scene satisfies the frontend import contract
    (non-empty environmentId + positive environmentVersion)."""
    from app.services import savegame as sg
    from app.services.publication import serialize_published_payload

    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    service = _service()
    service._apply_kit_composition(record, "apartment", _decorative_reqs(), None)
    _assert_scene_pinned(record.published, "apartment")

    payload = json.loads(
        serialize_published_payload(record.published, title="Skipped World")
    )
    document = sg.project_savegame_v1(
        payload, difficulty="medium", exported_at="2026-10-16T00:00:00Z"
    )
    public_scene = document["case"]["publicCase"]["scene"]
    assert public_scene is not None
    # mirrors the frontend requireBoundedString / requireBoundedInteger
    assert isinstance(public_scene["environmentId"], str)
    assert public_scene["environmentId"] != ""
    assert public_scene["environmentId"] == "apartment"
    assert isinstance(public_scene["environmentVersion"], int)
    assert public_scene["environmentVersion"] >= 1
    # the bootstrap scene projection carries the same identity
    assert document["case"]["scene"]["environmentId"] == "apartment"
    assert document["case"]["scene"]["environmentVersion"] >= 1
    # metadata reflects the pinned kit identity too
    assert document["case"]["metadata"]["environmentId"] == "apartment"


def test_provider_authored_skip_reflects_requested_environment_kit():
    """A requested non-default kit on a provider-authored world is reflected in
    the scene (not silently defaulted to apartment)."""
    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    service = _service()
    assert service._apply_kit_composition(
        record, "office", _decorative_reqs(), None
    )
    _assert_scene_pinned(record.published, "office")
    assert record.published.draft.scene.environment_id != "apartment"


def test_provider_authored_apartment_no_new_objects_is_provider_authored():
    """ADV-45-02 — an apartment world with no new object tokens whose kit base
    is NOT declared is classified PROVIDER_AUTHORED_WORLD (not byte-identity)."""
    _controller, record = _held(_renamed_script(), "medium", prompt=PROVIDER_PROMPT)
    service = _service()
    with _capture_events() as records:
        ok = service._apply_kit_composition(
            record, "apartment", WorldRequirements(), None
        )
    assert ok is True
    skip = _event(records, "world_compose.skipped")
    assert skip.pd_fields["reasonCode"] == "PROVIDER_AUTHORED_WORLD"
    assert skip.pd_fields["compositionMode"] == "PROVIDER_AUTHORED"
    _assert_scene_pinned(record.published, "apartment")


def test_golden_apartment_no_new_objects_stays_byte_identity():
    """A GOLDEN apartment with no new objects (kit base declared) keeps the
    legacy byte-identity classification and still pins the scene."""
    _controller, record = _held(_golden_script(), "medium")
    before = record.published.draft
    service = _service()
    with _capture_events() as records:
        ok = service._apply_kit_composition(
            record, "apartment", WorldRequirements(), None
        )
    assert ok is True
    skip = _event(records, "world_compose.skipped")
    assert skip.pd_fields["reasonCode"] == "APARTMENT_BYTE_IDENTITY"
    _assert_scene_pinned(record.published, "apartment")
    # placements unchanged (the golden world is kept verbatim)
    assert _placement_signature(record.published.draft) == _placement_signature(before)
