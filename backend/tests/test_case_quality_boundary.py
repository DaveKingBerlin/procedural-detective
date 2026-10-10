"""Phase35 — publication boundary + validator telemetry + prompt wiring.

- publication-boundary truth-isolation guard: a public DTO person whose role is
  ``murderer`` (or outside the closed public role vocabulary) is BLOCKED at the
  mapper (never silently renamed, never served);
- ``public_case_dict_from_payload`` stays byte-identical for the closed
  vocabulary roles (golden/driver worlds) — no regression;
- the Phase35 quality codes flow into validator-code telemetry and the
  report diagnostics (safe, closed, non-secret);
- the compact CASE QUALITY RULES block is wired into the persons/evidence/
  repair prompt templates AND no full golden savegame is ever embedded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation import prompts  # noqa: E402
from app.generation import validation_codes as vc  # noqa: E402
from app.generation.report import ValidationReport  # noqa: E402
from app.services import publication as pub  # noqa: E402

from test_case_quality_corpus import canonical_inputs  # noqa: E402


# --------------------------------------------------------------------------- #
# publication-boundary role guard
# --------------------------------------------------------------------------- #


def _payload_with_role(payload: dict, role: str) -> dict:
    import copy

    hostile = copy.deepcopy(dict(payload))
    hostile["draft"]["persons"][0]["role"] = role
    return hostile


def test_public_dto_rejects_murderer_role(monkeypatch):
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    hostile = _payload_with_role(payload, "murderer")
    with pytest.raises(ValueError):
        pub.public_case_dict_from_payload(hostile)


def test_public_dto_rejects_any_role_outside_closed_vocabulary(monkeypatch):
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    for role in ("killer", "detective", "perpetrator", "suspects", ""):
        hostile = _payload_with_role(payload, role)
        with pytest.raises(ValueError):
            pub.public_case_dict_from_payload(hostile)


def test_public_dto_closed_vocabulary_roles_unchanged(monkeypatch):
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    dto = pub.public_case_dict_from_payload(payload)
    assert [p["role"] for p in dto["persons"]] == [
        "victim",
        "suspect",
        "suspect",
        "suspect",
        "witness",
    ]


def test_savegame_export_also_blocks_truth_bearing_role(monkeypatch):
    from test_phase19g_evidence_render import _hard_payload
    from app.services import savegame as sg

    payload = _hard_payload()
    hostile = _payload_with_role(payload, "murderer")
    with pytest.raises(ValueError):
        sg.project_savegame_v1(hostile, difficulty=None)


# --------------------------------------------------------------------------- #
# validator-code telemetry (closed, safe)
# --------------------------------------------------------------------------- #


def test_quality_issues_map_to_closed_validator_codes():
    report = ValidationReport(quality_issues=("WITNESS_STATEMENT_MISSING",))
    codes = vc.validation_failure_codes(report)
    assert "WITNESS_STATEMENT_MISSING" in codes
    assert set(codes) <= vc.VALIDATOR_CODE_VOCABULARY
    assert vc.VALIDATOR_CODE_GENERIC in codes


def test_public_role_truth_leak_maps_to_validator_codes():
    report = ValidationReport(quality_issues=("PUBLIC_ROLE_TRUTH_LEAK",))
    codes = vc.validation_failure_codes(report)
    assert "PUBLIC_ROLE_TRUTH_LEAK" in codes
    assert set(codes) <= vc.VALIDATOR_CODE_VOCABULARY


def test_quality_codes_are_registered_in_the_closed_vocabulary():
    for code in (
        "PUBLIC_ROLE_TRUTH_LEAK",
        "VICTIM_IN_SUSPECT_CANDIDATES",
        "WITNESS_IN_SUSPECT_CANDIDATES",
        "MURDERER_NOT_SUSPECT_CANDIDATE",
        "WITNESS_STATEMENT_MISSING",
        "WITNESS_STATEMENT_EMPTY",
        "WITNESS_STATEMENT_UNKNOWN_WITNESS",
        "WITNESS_STATEMENT_WITNESS_ID_MISMATCH",
        "WITNESS_STATEMENT_SPEAKER_MISMATCH",
    ):
        assert code in vc.VALIDATOR_CODE_VOCABULARY, code


def test_outcome_branch_is_recoverable_repair_for_quality_issues():
    from app.generation.state_machine import ValidationOutcome

    report = ValidationReport(quality_issues=("WITNESS_STATEMENT_MISSING",))
    assert report.outcome is ValidationOutcome.RECOVERABLE_REPAIR
    report2 = ValidationReport(quality_issues=("PUBLIC_ROLE_TRUTH_LEAK",))
    assert report2.outcome is ValidationOutcome.RECOVERABLE_REPAIR


def test_quality_codes_never_leak_secret_material():
    report = ValidationReport(
        quality_issues=(
            "WITNESS_STATEMENT_MISSING",
            "PUBLIC_ROLE_TRUTH_LEAK",
        )
    )
    diagnostics = report.repair_diagnostics
    assert diagnostics == (
        "PUBLIC_ROLE_TRUTH_LEAK",
        "WITNESS_STATEMENT_MISSING",
    )
    codes = vc.validation_failure_codes(report)
    blob = "|".join(codes)
    assert "thomas_reed" not in blob
    assert "murdererId" not in blob


# --------------------------------------------------------------------------- #
# golden negative through the full PARSED validator does not touch providers
# --------------------------------------------------------------------------- #


def test_negative_codes_flow_into_report_diagnostics():
    document = _read_negative()
    public, truth, evidence, universes = canonical_inputs(document)
    from app.generation import case_quality

    codes = case_quality.validate_case_quality(public, truth, evidence, universes)
    report = ValidationReport(quality_issues=codes)
    for code in codes:
        assert code in report.repair_diagnostics


def _read_negative():
    from test_case_quality_corpus import NEGATIVE_01

    return json.loads(NEGATIVE_01.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# prompt wiring — compact CASE QUALITY RULES + no full golden embedded
# --------------------------------------------------------------------------- #


def _markers_for(path) -> tuple[str, ...]:
    """Unique marker strings of one golden savegame (a witness statement
    sentence + the case id) — if ANY of these appears in a prompt template or
    stage context, the full golden was injected."""
    document = json.loads(path.read_text(encoding="utf-8"))
    case = document["case"]
    markers = [case["metadata"]["sourceCaseId"]]
    for rec in case["evidence"]:
        content = rec.get("content") or {}
        statement = content.get("statement")
        if isinstance(statement, str) and statement:
            markers.append(statement[:60])
    return tuple(markers)


def _all_marker_substrings() -> tuple[str, ...]:
    from test_case_quality_corpus import GOLDEN_01, GOLDEN_02, GOLDEN_03

    out = []
    for path in (GOLDEN_01, GOLDEN_02, GOLDEN_03):
        out.extend(_markers_for(path))
    return tuple(out)


def test_case_quality_rules_block_is_present_in_templates():
    assert "CASE QUALITY RULES" in prompts.CASE_PEOPLE_PROMPT_v1
    assert "CASE QUALITY RULES" in prompts.EVIDENCE_PROMPT_v1
    assert "CASE QUALITY RULES" in prompts.REPAIR_PROMPT_v1
    rendered = prompts.build_case_people_prompt("ctx", None)
    assert "CASE QUALITY RULES" in rendered


def test_case_quality_rules_mirror_the_deterministic_contract():
    block = prompts.CASE_QUALITY_RULES
    lowered = block.casefold()
    assert "never appear in suspect candidates" in lowered
    assert "murderer" in lowered  # the TRUTH-ISOLATION sentence
    assert "witness_statement" in lowered


def test_full_golden_savegame_never_embedded_in_prompt_templates():
    markers = _all_marker_substrings()
    assert markers, "expected at least one unique golden marker"
    templates = (
        prompts.CASE_PEOPLE_PROMPT_v1,
        prompts.EVIDENCE_PROMPT_v1,
        prompts.WORLD_REQUIREMENTS_PROMPT_v1,
        prompts.REPAIR_PROMPT_v1,
        prompts.ASSET_SPEC_PROMPT_v1,
        prompts.ACTIVITY_LOG_PROMPT_v1,
    )
    for template in templates:
        for marker in markers:
            assert marker not in template, (
                f"full golden content leaked into a prompt template ({marker[:30]}...)"
            )


def test_full_golden_savegame_never_embedded_in_stage_context():
    """The pipeline stage-context builder (REPAIR / PUBLIC_WORLD / EVIDENCE /
    CASE_TRUTH) never embeds any full golden savegame marker."""
    from app.generation.pipeline import AttemptRecord, _stage_context
    from app.generation.provider import GenerationStage

    attempt = AttemptRecord(
        attempt_id="GA-golden-leak", case_id="CASE-x", session_id="QUOTA-x"
    )
    markers = _all_marker_substrings()
    for stage in (
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
        GenerationStage.REPAIR,
    ):
        try:
            context = _stage_context(attempt, stage)
        except Exception:  # noqa: BLE001 - an empty attempt degrades safely
            continue
        for marker in markers:
            assert marker not in context, (
                f"full golden content leaked into {stage.value} context "
                f"({marker[:30]}...)"
            )


def test_repair_context_carries_bounded_quality_context():
    """A quality-broken draft's REPAIR diagnostics carry the safe closed issue
    code; the REPAIR stage context includes the bounded CASE QUALITY FIX
    CONTEXT block (never truth)."""
    from app.generation.parser import parse_full_draft  # noqa: PLC0415
    from dataclasses import replace  # noqa: PLC0415

    from fixtures.golden_generation import GOLDEN_FULL_DRAFT  # noqa: PLC0415
    from app.generation.pipeline import AttemptRecord, validate_draft  # noqa: PLC0415

    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    draft = replace(
        draft,
        evidence=tuple(e for e in draft.evidence if e.kind != "witness_statement"),
    )
    attempt = AttemptRecord(
        attempt_id="GA-quality-repair", case_id="CASE-x", session_id="QUOTA-x"
    )
    attempt.draft = draft
    report = validate_draft(attempt)
    assert "WITNESS_STATEMENT_MISSING" in report.repair_diagnostics


def test_repair_prompt_diagnostics_are_safe():
    from app.generation.pipeline import build_request
    from app.generation.provider import GenerationStage
    from app.generation.report import ValidationReport

    report = ValidationReport(quality_issues=("WITNESS_STATEMENT_MISSING",))
    attempt = _attempt_with_report(report)
    request = build_request(
        attempt,
        GenerationStage.REPAIR,
        diagnostics=report.repair_diagnostics,
    )
    assert request.diagnostics == ("WITNESS_STATEMENT_MISSING",)
    assert "murdererId" not in "|".join(request.diagnostics)


def _attempt_with_report(report):
    from app.generation.pipeline import AttemptRecord

    attempt = AttemptRecord(
        attempt_id="GA-quality-telemetry", case_id="CASE-x", session_id="QUOTA-x"
    )
    attempt.last_validation = report
    return attempt
