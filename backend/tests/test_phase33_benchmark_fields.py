"""Phase33 RAD-3 — hermetic unit tests for the benchmark extension.

Covers the new per-attempt failure categories (`failureCategory`), the
per-attempt `publishable` flag, the per-contestant publishable rate, the
successful-case denominator (`validatedPublishedCases`) and the cost per
validated published case (undefined, never a misleading zero), plus the
future per-model scorecard template in the `--dry-run` plan.

Hermetic by construction (same pattern as ``test_phase31_benchmark.py``):
no provider is ever contacted; ``aggregate_per_contestant`` /
``enrich_result`` / ``failure_category_for`` / ``_plan_summary_text`` are pure
deterministic functions over synthetic records.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools import frontier_benchmark as fb

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "benchmarks" / "frontier" / "corpus"


# --------------------------------------------------------------------------- #
# failure_category_for — deterministic closed mapping
# --------------------------------------------------------------------------- #


def test_failure_category_mapping_is_deterministic_and_closed():
    """Every classified attempt lands in the closed Phase33 taxonomy and the
    mapping is a pure function of (failureCode, diagnostics)."""
    cases = {
        ("FRONTIER_TIMEOUT", ()): fb.FAILURE_CATEGORY_TIMEOUT,
        ("PROVIDER_TIMEOUT", ()): fb.FAILURE_CATEGORY_TIMEOUT,
        ("GENERATION_DEADLINE_EXCEEDED", ()): fb.FAILURE_CATEGORY_TIMEOUT,
        ("FRONTIER_RATE_LIMITED", ()): fb.FAILURE_CATEGORY_QUOTA,
        ("PROVIDER_CALL_BUDGET_EXHAUSTED", ()): fb.FAILURE_CATEGORY_QUOTA,
        ("ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED", ()): fb.FAILURE_CATEGORY_QUOTA,
        ("PROVIDER_UNAVAILABLE", ()): fb.FAILURE_CATEGORY_PROVIDER_TRANSPORT,
        ("FRONTIER_PROVIDER_ERROR", ()): fb.FAILURE_CATEGORY_PROVIDER_TRANSPORT,
        ("BRIDGE_DISCONNECTED", ()): fb.FAILURE_CATEGORY_PROVIDER_TRANSPORT,
        ("STRUCTURED_OUTPUT_INVALID", ()): fb.FAILURE_CATEGORY_CONTRACT_SCHEMA,
        ("SOLVER_AMBIGUOUS", ()): fb.FAILURE_CATEGORY_SOLVER,
        (None, ()): fb.FAILURE_CATEGORY_PLATFORM_UNKNOWN,
        ("INTERNAL_ERROR", ()): fb.FAILURE_CATEGORY_PLATFORM_UNKNOWN,
        ("BENCHMARK_DRIVER_ERROR", ()): fb.FAILURE_CATEGORY_PLATFORM_UNKNOWN,
    }
    for (code, diagnostics), expected in cases.items():
        assert fb.failure_category_for(code, diagnostics) == expected, (code, diagnostics)
    # Determinism: identical inputs -> identical outputs.
    a = fb.failure_category_for("VALIDATION_FAILED", ("asset id 'X' is not in the AssetRegistry",))
    b = fb.failure_category_for("VALIDATION_FAILED", ("asset id 'X' is not in the AssetRegistry",))
    assert a == b


def test_failure_category_detects_phase33_content_classes_from_diagnostics():
    """The three Phase33 content failure classes are recognized from the app's
    own sanitized validator diagnostics, even when the failureCode is generic."""
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("asset id 'PROP_SNEAKERS_01' is not in the AssetRegistry",),
    ) == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("placements[0]: anchor 'under_the_rug' is not in ANCHOR_ALLOWLIST",),
    ) == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("locked constraint: locked witness 'emily_reed' not found among draft "
         "persons with role 'witness'",),
    ) == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("world.unresolved-object: required object nowhere",),
    ) == fb.FAILURE_CATEGORY_OTHER_WORLD
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("solver failure: deduction did not complete on the generated evidence",),
    ) == fb.FAILURE_CATEGORY_SOLVER
    # Unknown diagnostics stay honest: not guessed into a content class.
    assert fb.failure_category_for(
        "VALIDATION_FAILED", ("some generic diagnostic",)
    ) == fb.FAILURE_CATEGORY_PLATFORM_UNKNOWN


# --------------------------------------------------------------------------- #
# enrich_result — per-attempt category + publishable flag (mocked telemetry)
# --------------------------------------------------------------------------- #


def _base_record(**overrides):
    record = {
        "benchmarkSchemaVersion": "1.0.0",
        "benchmarkRunId": "r",
        "contestantId": "c1",
        "finalStatus": "FAILED",
        "published": False,
        "publishable": None,
        "failureCode": "VALIDATION_FAILED",
        "failureCategory": None,
        "generationAttemptId": "GA-1",
    }
    record.update(overrides)
    return record


def _events_for(*, terminal_failure_code=None, published=False, outcome=None, issues=()):
    events: list[dict] = []
    if issues or outcome:
        events.append(
            {
                "event": "generation.stage.validation_failed",
                "validationOutcome": outcome,
                "validatorIssueCodes": list(issues),
            }
        )
    if published:
        events.append({"event": "generation.published", "providerCallCount": 4})
    elif terminal_failure_code is not None:
        events.append({"event": "generation.failed", "failureCode": terminal_failure_code})
    return events


def test_enrich_result_populates_failure_category_and_publishable():
    # Published + VALID -> publishable True, category from failureCode (None).
    rec = _base_record(published=True, finalStatus="PUBLISHED")
    fb.enrich_result(rec, _events_for(published=True, outcome="VALID"))
    assert rec["publishable"] is True
    assert rec["failureCategory"] == fb.FAILURE_CATEGORY_PLATFORM_UNKNOWN
    assert rec["validationOutcome"] == "VALID"

    # TERMINAL witness failure -> publishable False, category registry/anchor/witness.
    rec2 = _base_record()
    fb.enrich_result(
        rec2,
        _events_for(
            terminal_failure_code="VALIDATION_FAILED",
            outcome="TERMINAL_FAILURE",
            issues=[
                "locked constraint: locked witness 'emily_reed' not found among "
                "draft persons with role 'witness'"
            ],
        ),
    )
    assert rec2["publishable"] is False
    assert rec2["failureCategory"] == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS

    # VALID outcome but not yet published (hold-before-publish) -> publishable.
    rec3 = _base_record(finalStatus="FAILED")
    fb.enrich_result(rec3, _events_for(outcome="VALID"))
    assert rec3["publishable"] is True

    # No telemetry -> fields stay None (never fabricated).
    rec4 = _base_record()
    fb.enrich_result(rec4, [])
    assert rec4["failureCategory"] is None
    assert rec4["publishable"] is None


# --------------------------------------------------------------------------- #
# aggregate_per_contestant — publishable rate + cost per validated case
# --------------------------------------------------------------------------- #


def _contestant():
    return fb.BenchmarkContestant(
        id="c1", label="C1", provider="openrouter", model="m",
        credential_env="K", enabled=True,
    )


def _synthetic_phase33_records():
    """OK / REPAIR / TERMINAL synthetic attempts for the Phase33 aggregation."""
    base = {
        "benchmarkSchemaVersion": "1.0.0",
        "benchmarkRunId": "r",
        "contestantId": "c1",
        "contestantLabel": "C1",
        "provider": "openrouter",
        "model": "m",
        "difficulty": "easy",
        "repeatIndex": 0,
    }
    return [
        {
            **base,
            "benchmarkCaseId": "ok", "executionOrder": 1,
            "finalStatus": "PUBLISHED", "published": True, "publishable": True,
            "failureCode": None, "failureCategory": None,
            "validationOutcome": "VALID", "costSource": "unavailable",
        },
        {
            **base,
            "benchmarkCaseId": "repair", "executionOrder": 2,
            "finalStatus": "PUBLISHED", "published": True, "publishable": True,
            "failureCode": None, "failureCategory": None,
            "validationOutcome": "VALID", "costSource": "unavailable",
            "repairCount": 1,
        },
        {
            **base,
            "benchmarkCaseId": "terminal", "executionOrder": 3,
            "finalStatus": "FAILED", "published": False, "publishable": False,
            "failureCode": "VALIDATION_FAILED",
            "failureCategory": fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS,
            "validationOutcome": "TERMINAL_FAILURE", "costSource": "unavailable",
        },
        {
            **base,
            "benchmarkCaseId": "timeout", "executionOrder": 4,
            "finalStatus": "FAILED", "published": False, "publishable": False,
            "failureCode": "FRONTIER_TIMEOUT",
            "failureCategory": fb.FAILURE_CATEGORY_TIMEOUT,
            "validationOutcome": None, "costSource": "unavailable",
        },
    ]


def test_aggregate_publishable_rate_and_denominator():
    row = fb.aggregate_per_contestant(_contestant(), _synthetic_phase33_records())
    assert row["attempts"] == 4
    assert row["published"] == 2
    assert row["publishable"] == 2
    assert row["publishableRate"] == round(2 / 4, 4)
    assert row["validatedPublishedCases"] == 2
    assert row["failureCategories"] == {
        fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS: 1,
        fb.FAILURE_CATEGORY_TIMEOUT: 1,
    }
    # Zero-success groups: cost per case is UNDEFINED (None + note), never zero.
    assert row["costPerValidatedPublishedCase"] is None
    assert "undefined" in row["costPerValidatedPublishedCaseNote"] or \
        "unavailable" in row["costPerValidatedPublishedCaseNote"]


def test_aggregate_cost_per_validated_case_never_misleading_zero():
    """When truthful cost IS available, cost per validated case = total /
    validated published; an all-failed group reports undefined, never 0."""
    records = _synthetic_phase33_records()
    for record in records:
        record["costSource"] = "estimated"
        record["calculatedCost"] = 10.0
    row = fb.aggregate_per_contestant(_contestant(), records)
    assert row["totalEstimatedCost"] == 40.0
    assert row["meanCostPerAttempt"] == round(40.0 / 4, 4)
    assert row["costPerValidatedPublishedCase"] == round(40.0 / 2, 4)
    assert row["costPerValidatedPublishedCaseNote"] == "estimated"

    all_failed = [r for r in records if r["published"] is False]
    row_failed = fb.aggregate_per_contestant(_contestant(), all_failed)
    assert row_failed["validatedPublishedCases"] == 0
    assert row_failed["costPerValidatedPublishedCase"] is None
    assert "zero validated published cases" in row_failed["costPerValidatedPublishedCaseNote"]


def test_aggregate_empty_keeps_new_fields_absent_not_fabricated():
    row = fb.aggregate_per_contestant(_contestant(), [])
    assert row["attempts"] == 0
    assert row["publishable"] == 0
    assert row["publishableRate"] is None
    assert row["validatedPublishedCases"] == 0
    assert row["failureCategories"] == {}
    assert row["costPerValidatedPublishedCase"] is None


# --------------------------------------------------------------------------- #
# --dry-run future scorecard template
# --------------------------------------------------------------------------- #


def test_dry_run_plan_shows_future_scorecard_template():
    contestant = _contestant()
    text = fb._plan_summary_text(
        run_id="r1",
        suite_name="smoke",
        contestants=[contestant],
        cases=[],
        repeat=1,
        pending_count=1,
        skip_count=0,
        driver="inprocess",
        concurrency=1,
        base_url=None,
        credential_map={},
        max_cases=None,
        output_dir=Path("out"),
        dry_run=True,
    )
    assert "FUTURE PER-MODEL SCORECARD TEMPLATE" in text
    assert "publishable" in text
    assert "cost per validated case" in text
    assert "DRY RUN: NO provider is contacted" in text
    assert fb.SENTINEL not in text