"""Publication gate tests (Phase4 K items 19-25).

Each test drives a golden script whose GENERATED CONTENT is dangerous/invalid
and proves the gate refuses publication: either FAILED after an exhausted
recovery budget or never PUBLISHED. Classification is additionally probed
directly on the assembled draft (no provider involvement).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_STAGE_PAYLOADS,
)

from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.pipeline import AttemptRecord, apply_stage_output, normalize_prompt, validate_draft  # noqa: E402, F401
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.state_machine import GenerationState, ValidationOutcome  # noqa: E402

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

_G = GOLDEN_STAGE_PAYLOADS

SCENE = "miller_apartment_kitchen"
OFFICE = "miller_consulting_office"
BAR = "harbor_view_bar"


def _dump(doc) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# payload variant builders
# ---------------------------------------------------------------------------


def _evidence_doc() -> dict:
    return json.loads(_G[GenerationStage.EVIDENCE])


def _evidence_without(*evidence_ids) -> str:
    doc = _evidence_doc()
    doc["evidence"] = [e for e in doc["evidence"] if e["id"] not in evidence_ids]
    return _dump(doc)


def _evidence_with_additions(*additional) -> str:
    doc = _evidence_doc()
    doc["evidence"] = doc["evidence"] + list(additional)
    return _dump(doc)


def _evidence_with_script_injection() -> str:
    doc = _evidence_doc()
    first = doc["evidence"][0]
    first["presentation"]["description"] = "<script>alert(1)</script> malformed event"
    return _dump(doc)


def _world_graph_with_asset(url: str) -> str:
    doc = json.loads(_G[GenerationStage.WORLD_GRAPH])
    doc["worldGraph"]["placements"][0]["assetId"] = url
    return _dump(doc)


def _case_truth_with_extra_key() -> str:
    doc = json.loads(_G[GenerationStage.CASE_TRUTH])
    doc["weaponHint"] = "a sharp object"
    return _dump(doc)


def _case_truth_with_murderer(murderer_id: str) -> str:
    doc = json.loads(_G[GenerationStage.CASE_TRUTH])
    doc["crime"]["murdererId"] = murderer_id
    return _dump(doc)


def _thomas_excluded_evidence() -> str:
    """Evidence where thomas cannot reach the scene and michael is at it.

    The forensic deduction then uniquely survives MICHAEL while the generated
    truth still names thomas_reed -> truth mismatch (RECOVERABLE_REPAIR).
    """
    bar_sight = {
        "id": "cctv_thomas_bar_01",
        "kind": "cctv_observation",
        "reliability": "high",
        "discoverable": True,
        "sourceRef": {"kind": "record", "sourceId": "record_cctv_thomas_bar_01"},
        "propositions": [
            {
                "type": "PERSON_OBSERVED_AT_LOCATION",
                "personId": "thomas_reed",
                "locationId": BAR,
                "observedAt": "2026-09-11T22:15:00+02:00",
                "uncertaintySeconds": 60,
            }
        ],
        "presentation": {
            "title": "Bar CCTV records Thomas at 22:15",
            "description": "Thomas is at the Harbor View Bar at 22:15.",
        },
    }
    scene_sight = {
        "id": "cctv_michael_scene_01",
        "kind": "cctv_observation",
        "reliability": "high",
        "discoverable": True,
        "sourceRef": {"kind": "record", "sourceId": "record_cctv_michael_scene_01"},
        "propositions": [
            {
                "type": "PERSON_OBSERVED_AT_LOCATION",
                "personId": "michael_carter",
                "locationId": SCENE,
                "observedAt": "2026-09-11T22:16:40+02:00",
                "uncertaintySeconds": 60,
            }
        ],
        "presentation": {
            "title": "Kitchen CCTV shows Michael at 22:16",
            "description": "Michael is in the kitchen during the crime window.",
        },
    }
    # Michael's OFFICE sighting would forbid scene presence during the window;
    # drop it so the new SCENE sighting makes Michael the unique survivor.
    doc = _evidence_doc()
    doc["evidence"] = [
        e for e in doc["evidence"] if e["id"] != "cctv_michael_office_01"
    ]
    doc["evidence"] = doc["evidence"] + [bar_sight, scene_sight]
    return _dump(doc)


def _overconstrained_evidence() -> str:
    """Golden evidence plus a TIME_WINDOW_EXCLUSION covering the whole evening."""
    coverage = {
        "id": "coverage_full_evening_01",
        "kind": "cctv_observation",
        "reliability": "high",
        "discoverable": True,
        "sourceRef": {"kind": "record", "sourceId": "record_coverage_full_evening_01"},
        "propositions": [
            {
                "type": "TIME_WINDOW_EXCLUSION",
                "locationId": SCENE,
                "observedAt": "2026-09-11T22:00:00+02:00",
                "uncertaintySeconds": 1800,
            }
        ],
        "presentation": {
            "title": "Continuous kitchen coverage",
            "description": "The kitchen camera covers the scene without gaps all evening.",
        },
    }
    return _evidence_with_additions(coverage)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _admission(clock, ids):
    return AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )


def _controller(provider, admission, clock, ids, **overrides):
    kwargs = dict(
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=5,
    )
    kwargs.update(overrides)
    return GenerationController(
        provider=provider, admission=admission, clock=clock, ids=ids, **kwargs
    )


def _run(script, **controller_overrides):
    """Run one generation over a full FakeProvider script; return the record."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    fake = FakeProvider(script)
    controller = _controller(fake, admission, clock, ids, **controller_overrides)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    return controller.attempt(handle.attempt_id)


def _simple_script(stage_docs, repair_entries=None):
    script = {stage: [content] for stage, content in stage_docs.items()}
    if repair_entries is not None:
        script[GenerationStage.REPAIR] = list(repair_entries)
    return script


def _probe(stage_docs) -> ValidationOutcome:
    """Direct classification probe over a draft (no provider calls)."""
    attempt = AttemptRecord(
        attempt_id="GA-x", case_id="CASE-x", session_id="QUOTA-x"
    )
    attempt.locked, _ = normalize_prompt(GOLDEN_PROMPT, max_chars=4000)
    for stage in (
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
    ):
        apply_stage_output(attempt, stage, stage_docs[stage])
    report = validate_draft(attempt)
    attempt.last_validation = report
    return attempt


def _golden_docs():
    return {
        GenerationStage.CASE_TRUTH: _G[GenerationStage.CASE_TRUTH],
        GenerationStage.PUBLIC_WORLD: _G[GenerationStage.PUBLIC_WORLD],
        GenerationStage.EVIDENCE: _G[GenerationStage.EVIDENCE],
        GenerationStage.WORLD_GRAPH: _G[GenerationStage.WORLD_GRAPH],
    }


# ---------------------------------------------------------------------------
# the eight gate tests (19-25)
# ---------------------------------------------------------------------------


def test_19_generated_arbitrary_asset_url_rejected():
    """A placement assetId pointing at an arbitrary URL -> never published."""
    docs = _golden_docs()
    docs[GenerationStage.WORLD_GRAPH] = _world_graph_with_asset(
        "https://attacker.example/evil.glb"
    )
    probe = _probe(docs)
    assert probe.last_validation.safety_issues != ()
    assert probe.last_validation.outcome is ValidationOutcome.RECOVERABLE_REPAIR

    record = _run(_simple_script(docs, repair_entries=["malformed", "malformed"]))
    assert record.state is GenerationState.FAILED
    assert record.published is None


def test_20_executable_generated_content_rejected():
    """<script> content injected into a presentation -> never published."""
    docs = _golden_docs()
    docs[GenerationStage.EVIDENCE] = _evidence_with_script_injection()
    probe = _probe(docs)
    assert any("unsafe generated content" in i for i in probe.last_validation.safety_issues)
    assert probe.last_validation.outcome is ValidationOutcome.RECOVERABLE_REPAIR

    record = _run(_simple_script(docs, repair_entries=["malformed", "malformed"]))
    assert record.state is GenerationState.FAILED
    assert record.published is None


def test_21_unknown_extra_unsafe_fields_rejected():
    """A bogus top-level CASE_TRUTH key -> parser structural issue -> not published."""
    docs = _golden_docs()
    docs[GenerationStage.CASE_TRUTH] = _case_truth_with_extra_key()
    probe = _probe(docs)
    assert any("unknown key" in i for i in probe.last_validation.structural_issues)
    assert probe.last_validation.outcome is ValidationOutcome.RECOVERABLE_REPAIR

    record = _run(_simple_script(docs, repair_entries=["malformed", "malformed"]))
    assert record.state is GenerationState.FAILED
    assert record.published is None


def test_22_locked_constraint_violation_blocks_publication():
    """Murderer changed to anna_karlsson -> TERMINAL_FAILURE -> never published."""
    docs = _golden_docs()
    docs[GenerationStage.CASE_TRUTH] = _case_truth_with_murderer("anna_karlsson")
    probe = _probe(docs)
    assert probe.last_validation.locked_violations != ()
    assert probe.last_validation.outcome is ValidationOutcome.TERMINAL_FAILURE

    record = _run(_simple_script(docs))
    assert record.state is GenerationState.FAILED
    assert "locked" in record.reason
    assert record.published is None
    assert record.last_validation.locked_violations != ()


def test_23_ambiguous_solver_result_blocks_publication():
    """A suspect left unknown-viable -> RECOVERABLE_REGENERATE -> never published."""
    docs = _golden_docs()
    docs[GenerationStage.EVIDENCE] = _evidence_without("cctv_michael_office_01")
    probe = _probe(docs)
    assert probe.last_validation.outcome is ValidationOutcome.RECOVERABLE_REGENERATE
    assert probe.last_validation.solver_result is not None
    assert probe.last_validation.solver_result.who.unique is False

    ambiguous = _evidence_without("cctv_michael_office_01")
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]] * 2,
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]] * 2,
        GenerationStage.EVIDENCE: [ambiguous, ambiguous],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]] * 2,
    }
    record = _run(script, max_full_regenerations=1)
    assert record.state is GenerationState.FAILED
    assert "regeneration budget" in record.reason
    assert record.published is None


def test_24_solver_case_truth_mismatch_blocks_publication():
    """Deducted murderer != generated truth murderer -> never published."""
    docs = _golden_docs()
    docs[GenerationStage.EVIDENCE] = _thomas_excluded_evidence()
    probe = _probe(docs)
    assert probe.last_validation.validation is not None
    assert probe.last_validation.validation.all_true is False
    assert probe.last_validation.validation.murderer_true is False
    assert probe.last_validation.solver_result.who.winner == "michael_carter"
    assert probe.last_validation.outcome is ValidationOutcome.RECOVERABLE_REPAIR

    record = _run(_simple_script(docs, repair_entries=["malformed", "malformed"]))
    assert record.state is GenerationState.FAILED
    assert record.published is None


def test_25_overconstrained_time_blocks_publication():
    """A fully-covering TIME_WINDOW_EXCLUSION -> overconstrained -> never published."""
    docs = _golden_docs()
    docs[GenerationStage.EVIDENCE] = _overconstrained_evidence()
    probe = _probe(docs)
    assert probe.last_validation.solver_result is not None
    assert probe.last_validation.solver_result.when.overconstrained is True
    assert probe.last_validation.outcome is ValidationOutcome.RECOVERABLE_REGENERATE

    overconstrained = _overconstrained_evidence()
    script = {
        GenerationStage.CASE_TRUTH: [_G[GenerationStage.CASE_TRUTH]] * 2,
        GenerationStage.PUBLIC_WORLD: [_G[GenerationStage.PUBLIC_WORLD]] * 2,
        GenerationStage.EVIDENCE: [overconstrained, overconstrained],
        GenerationStage.WORLD_GRAPH: [_G[GenerationStage.WORLD_GRAPH]] * 2,
    }
    record = _run(script, max_full_regenerations=1)
    assert record.state is GenerationState.FAILED
    assert "regeneration budget" in record.reason
    assert record.published is None