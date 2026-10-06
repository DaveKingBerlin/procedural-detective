"""Phase31A — Deliverable C: per-attempt SAFE compatibility artifacts (§26).

Hermetic tests for the ``tools.frontier_benchmark`` compatibility-artifact
writer:

  - artifact safe shape (closed key set, ordered passes, bounded values);
  - NO forbidden keys / fragments (prompt, raw draft, CaseTruth, API key,
    Authorization header, upstream body, session secret);
  - the ``compatibility/`` directory is ABSENT when no telemetry exists
    (never fabricated);
  - aggregation of compatibility artifacts matches the enriched results for
    the same attempt ids;
  - end-to-end: a real ``InProcessDriver`` golden run and a
    REPAIR_BUDGET_EXHAUSTED run both yield truthful artifacts from the app's
    own captured telemetry.

Every provider call is mocked; no real paid call is ever made.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import frontier_benchmark as fb  # noqa: E402

from app.generation import prompts  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402

_GOLDEN_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)

SENTINEL = fb.SENTINEL  # must appear ONLY in the mocked outbound header


def _golden_stage_strings():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.generation.provider import GenerationStage
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS

    return [
        GOLDEN_STAGE_PAYLOADS[stage]
        for stage in (
            GenerationStage.CASE_TRUTH,
            GenerationStage.PUBLIC_WORLD,
            GenerationStage.EVIDENCE,
            GenerationStage.WORLD_GRAPH,
        )
    ]


def _deepseek_scenario_events(attempt_id: str) -> list[dict]:
    """A deterministic REPAIR_BUDGET_EXHAUSTED scenario (Phase 31 DeepSeek):
    Pass 0 fails with STRUCTURED_OUTPUT_INVALID, repair #1 keeps it
    (UNCHANGED), repair #2 keeps it (UNCHANGED), then the budget exhausts."""
    return [
        {
            "event": "provider.call.complete", "stage": "evidence",
            "provider": "openrouter", "model": "deepseek/deepseek-chat",
            "structuredOutput": True, "schemaId": "EVIDENCE_v1",
            "generationAttemptId": attempt_id,
        },
        {
            "event": "generation.stage.validation_failed",
            "generationAttemptId": attempt_id, "repairCount": 0,
            "providerCallCount": 4,
            "validatorCodes": ["STRUCTURED_OUTPUT_INVALID"],
            "validationOutcome": "RECOVERABLE_REPAIR",
        },
        {
            "event": "generation.repair.outcome",
            "generationAttemptId": attempt_id, "repairCount": 1,
            "providerCallCount": 5, "stage": "validation",
            "validatorCodesBefore": ["STRUCTURED_OUTPUT_INVALID"],
            "validatorCodesAfter": ["STRUCTURED_OUTPUT_INVALID"],
            "codesFixed": [], "codesUnchanged": ["STRUCTURED_OUTPUT_INVALID"],
            "codesIntroduced": [], "repairEffectiveness": "UNCHANGED",
        },
        {
            "event": "generation.stage.validation_failed",
            "generationAttemptId": attempt_id, "repairCount": 1,
            "providerCallCount": 5,
            "validatorCodes": ["STRUCTURED_OUTPUT_INVALID"],
            "validationOutcome": "RECOVERABLE_REPAIR",
        },
        {
            "event": "generation.repair.outcome",
            "generationAttemptId": attempt_id, "repairCount": 2,
            "providerCallCount": 6, "stage": "validation",
            "validatorCodesBefore": ["STRUCTURED_OUTPUT_INVALID"],
            "validatorCodesAfter": ["STRUCTURED_OUTPUT_INVALID"],
            "codesFixed": [], "codesUnchanged": ["STRUCTURED_OUTPUT_INVALID"],
            "codesIntroduced": [], "repairEffectiveness": "UNCHANGED",
        },
        {
            "event": "generation.stage.validation_failed",
            "generationAttemptId": attempt_id, "repairCount": 2,
            "providerCallCount": 6,
            "validatorCodes": ["STRUCTURED_OUTPUT_INVALID"],
            "validationOutcome": "RECOVERABLE_REPAIR",
        },
        {
            "event": "generation.failed", "generationAttemptId": attempt_id,
            "providerCallCount": 6, "repairCount": 2,
            "failureCode": "REPAIR_BUDGET_EXHAUSTED",
        },
    ]


class _MockOutbound:
    """httpx.post seam for the inprocess driver (recorded wire)."""

    def __init__(self, golden) -> None:
        self.posts: list[tuple[str, dict, dict]] = []
        self._queue = list(golden)

    def install(self, monkeypatch) -> None:
        from app.generation import frontier_provider as fp_mod

        monkeypatch.setattr(fp_mod.httpx, "post", self._post)

    class _Resp:
        status_code = 200

        def __init__(self, body: bytes) -> None:
            self._body = body

        def iter_bytes(self, chunk_size):
            yield self._body

    def _post(self, url, json=None, headers=None, timeout=None):
        body = dict(json or {})
        self.posts.append((url, dict(body), dict(headers or {})))
        content = self._queue.pop(0) if self._queue else "<not-json>"
        return self._Resp(content.encode("utf-8"))


# --------------------------------------------------------------------------- #
# artifact safe shape + forbidden-key proof
# --------------------------------------------------------------------------- #


def test_artifact_safe_shape_and_no_forbidden_keys():
    attempt_id = "GA-ARTIFACT-1"
    artifact = fb.build_compatibility_artifact(
        attempt_id, _deepseek_scenario_events(attempt_id)
    )
    assert artifact is not None
    # Closed key set only (§26 shape).
    assert set(artifact) <= fb.COMPAT_ARTIFACT_KEYS
    assert artifact["generationAttemptId"] == attempt_id
    assert artifact["provider"] == "openrouter"
    assert artifact["model"] == "deepseek/deepseek-chat"
    assert artifact["stage"] == "validation"
    assert artifact["structuredOutput"] is True
    assert artifact["finalFailureCode"] == "REPAIR_BUDGET_EXHAUSTED"
    assert artifact["schemaId"] == "EVIDENCE_v1"
    # ordered passes: initial + after each repair.
    assert [p["repairCount"] for p in artifact["passes"]] == [0, 1, 2]
    for pass_index, expected_effectiveness in (
        (0, None),
        (1, "UNCHANGED"),
        (2, "UNCHANGED"),
    ):
        entry = artifact["passes"][pass_index]
        assert set(entry) <= fb.COMPAT_PASS_KEYS
        assert entry["validatorCodes"] == ["STRUCTURED_OUTPUT_INVALID"]
        if expected_effectiveness is None:
            assert "repairEffectiveness" not in entry
        else:
            assert entry["repairEffectiveness"] == expected_effectiveness

    # Forbidden-key / forbidden-fragment proof over the serialized bytes.
    text = json.dumps(artifact).casefold()
    for fragment in fb.COMPAT_FORBIDDEN_FRAGMENTS:
        assert fragment.casefold() not in text, fragment
    assert SENTINEL not in text


def test_artifact_without_any_validation_reports_is_truthful():
    """A PUBLISHED attempt (no failures) has an empty passes list and no
    finalFailureCode — the artifact never fabricates failures."""
    events = [
        {
            "event": "provider.call.complete", "stage": "world_graph",
            "provider": "openrouter", "model": "deepseek/deepseek-chat",
            "structuredOutput": True, "generationAttemptId": "GA-OK-1",
        },
        {
            "event": "generation.validation.complete",
            "generationAttemptId": "GA-OK-1",
            "validationOutcome": "VALID",
        },
        {
            "event": "generation.published", "generationAttemptId": "GA-OK-1",
            "published": True, "failureCode": None,
        },
    ]
    artifact = fb.build_compatibility_artifact("GA-OK-1", events)
    assert artifact is not None
    assert artifact["passes"] == []
    assert artifact["finalFailureCode"] is None
    assert artifact["structuredOutput"] is True
    assert artifact["stage"] == "validation"
    assert artifact["provider"] == "openrouter"


# --------------------------------------------------------------------------- #
# writer behavior: absent without telemetry, files with telemetry
# --------------------------------------------------------------------------- #


def test_compatibility_dir_absent_without_telemetry(tmp_path):
    run_dir = tmp_path / "run-no-telemetry"
    run_dir.mkdir()
    records = [
        {
            "generationAttemptId": "GA-1",
            "contestantId": "c", "benchmarkCaseId": "k", "repeatIndex": 0,
        }
    ]
    written = fb.write_compatibility_artifacts(run_dir, records, {})
    assert written == 0
    assert not (run_dir / fb.COMPATIBILITY_DIR_NAME).exists()


def test_compatibility_artifacts_written_and_aggregate_with_results(tmp_path):
    """Aggregation: the per-attempt artifact files match the enriched results
    for the same attempt ids (finalFailureCode + provider/model + the
    deterministic passes)."""
    run_dir = tmp_path / "run-with-telemetry"
    run_dir.mkdir()
    record_a = {
        "generationAttemptId": "GA-ARTIFACT-1",
        "contestantId": "ds", "benchmarkCaseId": "easy-office-001",
        "repeatIndex": 0, "provider": "openrouter",
        "model": "deepseek/deepseek-chat", "published": False,
        "failureCode": "REPAIR_BUDGET_EXHAUSTED",
    }
    record_b = {
        "generationAttemptId": "GA-OK-1",
        "contestantId": "ds2", "benchmarkCaseId": "easy-office-002",
        "repeatIndex": 0, "provider": "openrouter",
        "model": "deepseek/deepseek-chat", "published": True,
        "failureCode": None,
    }
    telemetry = {
        "GA-ARTIFACT-1": _deepseek_scenario_events("GA-ARTIFACT-1"),
        "GA-OK-1": [
            {
                "event": "generation.published",
                "generationAttemptId": "GA-OK-1",
                "published": True, "failureCode": None,
            }
        ],
    }
    written = fb.write_compatibility_artifacts(
        run_dir, [record_a, record_b], telemetry
    )
    assert written == 2
    compat_dir = run_dir / fb.COMPATIBILITY_DIR_NAME
    assert compat_dir.is_dir()
    artifact_a = json.loads(
        (compat_dir / "GA-ARTIFACT-1.json").read_text(encoding="utf-8")
    )
    artifact_b = json.loads(
        (compat_dir / "GA-OK-1.json").read_text(encoding="utf-8")
    )
    # Aggregation matches the same attempt ids' enriched results.
    assert artifact_a["finalFailureCode"] == record_a["failureCode"]
    assert artifact_b["finalFailureCode"] is None
    assert artifact_a["provider"] == record_a["provider"]
    assert artifact_a["model"] == record_a["model"]
    assert [p["repairCount"] for p in artifact_a["passes"]] == [0, 1, 2]
    assert artifact_b["passes"] == []
    # no attempt-id-less file is ever written.
    assert set(p.name for p in compat_dir.glob("*.json")) == {
        "GA-ARTIFACT-1.json", "GA-OK-1.json"
    }


# --------------------------------------------------------------------------- #
# end-to-end: real InProcessDriver captured telemetry
# --------------------------------------------------------------------------- #


def test_inprocess_driver_golden_run_produces_truthful_artifact(tmp_path, monkeypatch):
    """A real golden DeepSeek-style inprocess run: the artifact assembled from
    the driver's OWN captured telemetry reports structuredOutput=true, stage=
    validation, an empty passes list and no fabricated failure code."""
    wire = _MockOutbound(golden=_golden_stage_strings())
    wire.install(monkeypatch)
    db_url = f"sqlite:///{(tmp_path / 'ds-golden.db').as_posix()}"
    driver = fb.InProcessDriver(concurrency=1, database_url=db_url)
    try:
        contestant = fb.BenchmarkContestant(
            id="ds", label="DeepSeek", provider="openrouter",
            model="deepseek/deepseek-chat", credential_env="K",
            enabled=True, credential="DS-ART-KEY-GOLD",
        )
        case = fb.BenchmarkCase(id="easy-office-001", difficulty="easy",
                                prompt=_GOLDEN_PROMPT)
        outcome = driver.run_case(contestant, case)
        assert outcome["created"] == "PUBLISHED"
        attempt_id = outcome["generationAttemptId"]
        telemetry = fb.index_events_by_attempt(driver.captured_events)
        assert attempt_id in telemetry
        artifact = fb.build_compatibility_artifact(attempt_id, telemetry[attempt_id])
        assert artifact is not None
        assert artifact["provider"] == "openrouter"
        assert artifact["model"] == "deepseek/deepseek-chat"
        assert artifact["structuredOutput"] is True
        assert artifact["stage"] == "validation"
        assert artifact["passes"] == []
        assert artifact["finalFailureCode"] is None
        assert set(artifact) <= fb.COMPAT_ARTIFACT_KEYS
        text = json.dumps(artifact).casefold()
        for fragment in fb.COMPAT_FORBIDDEN_FRAGMENTS:
            assert fragment.casefold() not in text, fragment
        # The model+key stayed out of every artifact file.
        assert SENTINEL not in json.dumps(artifact)
    finally:
        driver.close()


def test_inprocess_driver_repair_exhaustion_artifact(tmp_path, monkeypatch):
    """The exact Phase 31 DeepSeek pattern through the REAL inprocess driver:
    the compatibility artifact records Pass 0/repair#1/repair#2 validator-code
    sets and the final REPAIR_BUDGET_EXHAUSTED code — all from the driver's
    own captured telemetry (aggregates with the results file)."""
    malformed = ["malformed"] * 6  # 4 stages + repair #1 + repair #2
    wire = _MockOutbound(golden=malformed)
    wire.install(monkeypatch)
    db_url = f"sqlite:///{(tmp_path / 'ds-repair.db').as_posix()}"
    driver = fb.InProcessDriver(concurrency=1, database_url=db_url)
    try:
        contestant = fb.BenchmarkContestant(
            id="ds", label="DeepSeek", provider="openrouter",
            model="deepseek/deepseek-chat", credential_env="K",
            enabled=True, credential="DS-ART-KEY-REP",
        )
        case = fb.BenchmarkCase(id="easy-office-001", difficulty="easy",
                                prompt=_GOLDEN_PROMPT)
        outcome = driver.run_case(contestant, case)
        assert outcome["created"] == "FAILED"
        attempt_id = outcome["generationAttemptId"]
        telemetry = fb.index_events_by_attempt(driver.captured_events)
        assert attempt_id in telemetry
        artifact = fb.build_compatibility_artifact(attempt_id, telemetry[attempt_id])
        assert artifact is not None
        assert artifact["finalFailureCode"] == "REPAIR_BUDGET_EXHAUSTED"
        assert [p["repairCount"] for p in artifact["passes"]] == [0, 1, 2]
        for pass_index, entry in enumerate(artifact["passes"]):
            assert entry["validatorCodes"] == ["STRUCTURED_OUTPUT_INVALID"]
            if pass_index == 0:
                # the INITIAL pass has no repair delta (no effectiveness yet).
                assert "repairEffectiveness" not in entry
            else:
                assert entry["repairEffectiveness"] == "UNCHANGED"
        # aggregating the artifact against the RICHER results record: the
        # driver failure code already surfaced the same terminal code.
        assert artifact["finalFailureCode"] == outcome.get("failureCode")
        # REPAIR_v1 reached the wire (repair calls #4/#5).
        assert any(
            (body.get("response_format") or {}).get("json_schema", {}).get("name")
            == "REPAIR_v1"
            for _u, body, _h in wire.posts
        )
        assert (
            prompts.json_schema_for_stage_output("repair")
            in [
                (body.get("response_format") or {}).get("json_schema", {}).get("schema")
                for _u, body, _h in wire.posts
            ]
        )
    finally:
        driver.close()