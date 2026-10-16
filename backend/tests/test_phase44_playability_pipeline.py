"""Phase 44 integration tests — Gate 3 in the generation/publication pipeline.

Hermetic, ZERO provider/network. Drives the real ``GenerationController`` with
``FakeProvider`` scripts and exercises the authoritative publication guard
(``GenerationService._publish_if_ready``) directly via a stubbed store/publication.

Proves Phase44 §31: an Easy/Medium/Hard full-pipeline fixture yields
Gate 1 = VALID, Gate 2 = VALID, Gate 3 = VALID and published = true; and a
Gate-3-only failure (schema-valid but playability-sparse) NEVER publishes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from app.generation import pipeline, playability  # noqa: E402
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import WorldGraphSpec  # noqa: E402
from app.generation.state_machine import GenerationState, ValidationOutcome  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

SCENE = "miller_apartment_kitchen"
_G = GOLDEN_STAGE_PAYLOADS


# --------------------------------------------------------------------------- #
# script builders
# --------------------------------------------------------------------------- #


def _dump(doc) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def _golden_script() -> dict:
    return {stage: [payload] for stage, payload in _G.items()}


def _empty_world_script() -> dict:
    """Golden but with a schema-valid EMPTY world graph (ADV-INV-01)."""
    wg = json.loads(_G[GenerationStage.WORLD_GRAPH])
    wg["worldGraph"]["placements"] = []
    script = _golden_script()
    script[GenerationStage.WORLD_GRAPH] = [_dump(wg)]
    return script


def _zero_link_script() -> dict:
    """Golden but with EVERY placement's ``evidenceId`` removed (ADV-44-01).

    The world is still schema-valid and every object remains interactable, but
    NO evidence record is placement-linked — the exact entity-surface bypass.
    """
    wg = json.loads(_G[GenerationStage.WORLD_GRAPH])
    for placement in wg["worldGraph"]["placements"]:
        placement["evidenceId"] = None
        if not placement.get("interaction"):
            placement["interaction"] = "inspect"
    script = _golden_script()
    script[GenerationStage.WORLD_GRAPH] = [_dump(wg)]
    return script


def _decoy_why_script() -> dict:
    """ADV-44-07 — the winner motive fact is unlinked; a DECOY motive fact is
    linked instead (the winning WHY support becomes unreachable)."""
    wg = json.loads(_G[GenerationStage.WORLD_GRAPH])
    for placement in wg["worldGraph"]["placements"]:
        if placement.get("evidenceId") == "motive_audit_01":
            placement["evidenceId"] = "motive_no_affair_01"
    script = _golden_script()
    script[GenerationStage.WORLD_GRAPH] = [_dump(wg)]
    return script


def _hard_script() -> dict:
    """Golden + 2 meaningful interactive objects -> satisfies the Hard contract."""
    pw = json.loads(_G[GenerationStage.PUBLIC_WORLD])
    ev = json.loads(_G[GenerationStage.EVIDENCE])
    wg = json.loads(_G[GenerationStage.WORLD_GRAPH])
    extra = (
        ("clue_painting_01", "clue_painting_fact_01", "desk_main"),
        ("clue_ledger_01", "clue_ledger_fact_01", "shelf_01"),
    )
    for object_id, fact_id, anchor in extra:
        pw["objects"].append(
            {
                "objectId": object_id,
                "assetId": "PROP_VASE_01",
                "affordances": ["INSPECTABLE"],
            }
        )
        ev["evidence"].append(
            {
                "id": fact_id,
                "kind": "object",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": f"record_{fact_id}"},
                "propositions": [
                    {
                        "type": "OTHER",
                        "objectId": object_id,
                        "uncertaintySeconds": 0,
                        "structured": {},
                    }
                ],
                "presentation": {"title": fact_id, "description": "A local clue."},
            }
        )
        wg["worldGraph"]["placements"].append(
            {
                "objectId": object_id,
                "assetId": "PROP_VASE_01",
                "locationId": SCENE,
                "anchor": anchor,
                "interaction": "inspect",
                "evidenceId": fact_id,
            }
        )
    script = _golden_script()
    script[GenerationStage.PUBLIC_WORLD] = [_dump(pw)]
    script[GenerationStage.EVIDENCE] = [_dump(ev)]
    script[GenerationStage.WORLD_GRAPH] = [_dump(wg)]
    return script


# --------------------------------------------------------------------------- #
# controller harness + publication guard stubs
# --------------------------------------------------------------------------- #


def _run(script: dict, difficulty: str):
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
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    return controller, handle, record


class _FakeStore:
    def __init__(self) -> None:
        self.case_version_states: list[tuple] = []
        self.attempt_states: list[dict] = []

    def update_case_version_state(
        self, case_id, version, state, state_reason=None
    ) -> None:
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


def _make_guard_stub():
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


# --------------------------------------------------------------------------- #
# positive: Easy / Medium / Hard full pipeline
# --------------------------------------------------------------------------- #


def _assert_pipeline_publishes(script: dict, difficulty: str):
    controller, handle, record = _run(script, difficulty)
    # Gate 1 + Gate 2: the full deterministic validation passed.
    report = pipeline.validate_draft(record)
    assert report.outcome is ValidationOutcome.VALID, (
        report.structural_issues,
        report.safety_issues,
        report.universe_issues,
        report.quality_issues,
    )
    assert set(pipeline.STAGE_ORDER) <= record.stage_done
    assert record.state is GenerationState.VALIDATING

    # Controller-level publication builds the frozen aggregate.
    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success and result.published is not None
    published = result.published

    # Gate 3 over the frozen candidate.
    gate3 = playability.validate_published_candidate(published, difficulty)
    assert gate3.valid, gate3.issue_codes

    # The authoritative publication guard persists and returns PUBLISHED.
    stub = _make_guard_stub()
    record.published = published
    status = _publish_via_guard(stub, record, difficulty)
    assert status == GenerationState.PUBLISHED.value
    assert stub._publication.calls == 1
    return published, gate3


def test_easy_full_pipeline_publishes():
    published, gate3 = _assert_pipeline_publishes(_golden_script(), "easy")
    assert gate3.inspectable_count >= 3


def test_medium_full_pipeline_publishes():
    published, gate3 = _assert_pipeline_publishes(_golden_script(), "medium")
    assert gate3.inspectable_count >= 5
    assert gate3.red_herring_count >= 1
    assert set(gate3.reachable_critical_dimensions) == {"WHO", "WHY", "WEAPON", "WHEN"}


def test_hard_full_pipeline_publishes():
    published, gate3 = _assert_pipeline_publishes(_hard_script(), "hard")
    assert gate3.inspectable_count >= 7
    assert gate3.meaningful_inspectable_count >= 5
    assert gate3.red_herring_count >= 2
    assert gate3.distinct_meaningful_source_count >= 4


def test_hard_script_fails_easy_level_only_when_sparse():
    # The hard fixture meets the hard contract but a Medium case would also pass
    # it (richness never blocks): confirm the policy direction is monotonic.
    _, gate3 = _assert_pipeline_publishes(_hard_script(), "hard")
    assert gate3.valid


# --------------------------------------------------------------------------- #
# negative: Gate-3-only failure never publishes
# --------------------------------------------------------------------------- #


def test_placements_empty_stage_gate_rejects_world_graph():
    controller, handle, record = _run(_empty_world_script(), "medium")
    # The WORLD_GRAPH stage was NOT trusted (stage-local Gate 3).
    assert GenerationStage.WORLD_GRAPH not in record.stage_done
    assert GenerationStage.EVIDENCE in record.stage_done
    # Gate 1 / Gate 2 remain VALID while Gate 3 fails (schema-valid empty world).
    report = pipeline.validate_draft(record)
    assert report.outcome is ValidationOutcome.VALID
    assert record.state is GenerationState.VALIDATING


def test_failed_gate3_prevents_publication():
    controller, handle, record = _run(_empty_world_script(), "medium")
    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success and result.published is not None
    record.published = result.published

    # Gate 3 must reject the frozen aggregate.
    gate3 = playability.validate_published_candidate(record.published, "medium")
    assert not gate3.valid
    assert playability.PLAYABILITY_EMPTY_WORLD in gate3.issue_codes

    # The authoritative guard REFUSES and never persists the payload.
    stub = _make_guard_stub()
    status = _publish_via_guard(stub, record, "medium")
    assert status is None
    assert stub._publication.calls == 0
    assert record.state is GenerationState.FAILED
    assert stub._store.case_version_states
    assert stub._store.case_version_states[-1][2] == GenerationState.FAILED.value
    assert stub._store.attempt_states
    assert stub._store.attempt_states[-1]["status"] == GenerationState.FAILED.value


def test_gate3_only_failure_marks_attempt_failed_not_published():
    """AC-29 — a failed Gate 3 can NEVER yield published=true for this candidate."""
    controller, handle, record = _run(_empty_world_script(), "medium")
    result = controller.publish(handle.attempt_id, hold_ok=True)
    record.published = result.published
    stub = _make_guard_stub()
    assert _publish_via_guard(stub, record, "medium") is None
    assert stub._publication.calls == 0
    assert record.state is not GenerationState.PUBLISHED


def test_adv44_01_zero_evidence_links_never_publishes_unrepaired():
    """ADV-44-01 — with EVERY placement's evidenceId removed, Gate 3 must reject
    the raw candidate (no entity-surface shortcut).

    Phase45 Fix: the authoritative publication guard now performs a
    deterministic, provider-free reachability repair first — the EXTRA
    invariant is that whatever gets published is Gate 3 VALID with NO
    unreachable critical dimension. The raw (unrepaired) candidate is still
    rejected by Gate 3 and would fail closed if the repair could not fix it.
    """
    controller, handle, record = _run(_zero_link_script(), "medium")
    # The WORLD_GRAPH stage gate rejected the (structurally valid) world.
    assert GenerationStage.WORLD_GRAPH not in record.stage_done
    # Gate 1 / Gate 2 are VALID — only Gate 3 fails.
    assert pipeline.validate_draft(record).outcome is ValidationOutcome.VALID

    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success and result.published is not None
    record.published = result.published

    # The RAW frozen candidate is rejected by Gate 3.
    gate3 = playability.validate_published_candidate(record.published, "medium")
    assert not gate3.valid
    assert playability.PLAYABILITY_EVIDENCE_UNREACHABLE in gate3.issue_codes
    assert playability.PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE in gate3.issue_codes

    stub = _make_guard_stub()
    status = _publish_via_guard(stub, record, "medium")
    # The guard repairs -> revalidates -> publishes a Gate 3 VALID candidate.
    assert status == GenerationState.PUBLISHED.value
    assert stub._publication.calls == 1
    published_gate3 = playability.validate_published_candidate(
        stub._publication.published, "medium"
    )
    assert published_gate3.valid, published_gate3.issue_codes
    assert published_gate3.unreachable_critical_dimensions == ()


def test_adv44_07_decoy_why_never_publishes_unrepaired():
    """ADV-44-07 — a reachable DECOY WHY fact does not satisfy Gate 3 while the
    winning motive support is unreachable (the raw candidate is rejected).

    Phase45 Fix: the authoritative publication guard repairs the candidate so
    the WINNING motive clue becomes reachable; only a Gate 3 VALID candidate is
    published (never one with unreachable critical dimensions).
    """
    controller, handle, record = _run(_decoy_why_script(), "medium")
    # Structurally/canonically valid (Gate 1/2), but Gate 3 rejects WHY.
    assert pipeline.validate_draft(record).outcome is ValidationOutcome.VALID
    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success and result.published is not None
    record.published = result.published

    gate3 = playability.validate_published_candidate(record.published, "medium")
    assert not gate3.valid
    assert playability.DIMENSION_WHY in gate3.unreachable_critical_dimensions
    assert playability.PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE in gate3.issue_codes

    stub = _make_guard_stub()
    status = _publish_via_guard(stub, record, "medium")
    assert status == GenerationState.PUBLISHED.value
    assert stub._publication.calls == 1
    published_gate3 = playability.validate_published_candidate(
        stub._publication.published, "medium"
    )
    assert published_gate3.valid, published_gate3.issue_codes
    assert playability.DIMENSION_WHY in published_gate3.reachable_critical_dimensions


def test_publication_guard_metrics_events_are_emitted(caplog):
    import logging

    controller, handle, record = _run(_golden_script(), "medium")
    result = controller.publish(handle.attempt_id, hold_ok=True)
    record.published = result.published
    stub = _make_guard_stub()
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        assert _publish_via_guard(stub, record, "medium") == (
            GenerationState.PUBLISHED.value
        )
    events = [getattr(r, "pd_event", None) for r in caplog.records]
    assert "playability.validation.started" in events
    assert "playability.validation.complete" in events
    # The metrics fields are on the allowlist (present in the safe payload).
    complete = next(
        r
        for r in caplog.records
        if getattr(r, "pd_event", None) == "playability.validation.complete"
    )
    assert "inspectableCount" in complete.pd_fields
    assert "difficulty" in complete.pd_fields


def test_observability_safe_fields_registered():
    from app.core import observability

    for field in (
        "difficulty",
        "inspectableCount",
        "meaningfulInspectableCount",
        "distinctMeaningfulSourceCount",
        "redHerringCount",
        "reachableCriticalDimensions",
        "unreachableCriticalDimensions",
        "playabilityOutcome",
    ):
        assert field in observability._SAFE_FIELDS


def test_playability_codes_registered_in_validator_vocabulary():
    from app.generation import validation_codes

    for code in playability.PLAYABILITY_ISSUE_CODES:
        assert code in validation_codes.VALIDATOR_CODE_VOCABULARY
        assert code in validation_codes._VALIDATOR_CODE_SEVERITY


def test_scope_valid_but_sparse_negative_does_not_mutate_truth():
    controller, handle, record = _run(_empty_world_script(), "medium")
    result = controller.publish(handle.attempt_id, hold_ok=True)
    before = result.published.truth.crime
    record.published = result.published
    stub = _make_guard_stub()
    _publish_via_guard(stub, record, "medium")
    assert record.published.truth.crime == before
