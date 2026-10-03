"""Phase 26C4 — the GLOBAL provider-call envelope is the legal bounded graph.

Phase 26C3 fixed the Core ceiling (``MAX_CORE_LLM_CALLS_PER_GENERATION``) by
DERIVING it from the canonical legal stage graph (Option A), but the *global*
ceiling (``MAX_LLM_CALLS_PER_GENERATION``) remained a literal 128 — SMALLER
than the Core envelope it contains:

    global calls = core_calls + asset_calls   (every real call charges the
                                               global counter exactly once)
    effective Core max <= 128 - asset_calls   (the C3 contradiction at the
                                               enclosing guard)

Phase 26C4 derives the GLOBAL default the same way — the complete legal
provider-call envelope of one whole attempt:

    Core legal maximum  = derive_core_call_budget_default()
                        = 4 passes × (5 + max(9, 50) × 3)
                        = 4 × 155
                        = 620
    Asset legal maximum = MAX_PROCEDURAL_ASSETS_PER_GENERATION
                          × MAX_LLM_CALLS_PER_PROCEDURAL_ASSET
                        = 20 × 5
                        = 100
    GLOBAL legal envelope = 620 + 100 = 720

and rejects any global override BELOW that envelope at configuration time
(fail-fast, never a silent clamp). The global guard is NOT removed: envelope+1
= the 721st call still fails closed with ``PROVIDER_CALL_BUDGET_EXHAUSTED``.
The hierarchy becomes

    per-item limits -> Core/asset buckets -> global envelope -> deadline

with no contradictory smaller superset cap.

This suite walks spec §9-§14:

- §9/§11 exact boundaries (620 / 100 / 720; 720 accepted with a LEGAL bucket
  composition; 721 fails globally);
- §10 bucket composition (720 identical Core calls is NOT a legal pipeline);
- §12 raw-model-evidence fallback stays the binding Core multiplier
  (MAX_EVIDENCE_ITEMS = 50, never a regression to MAX_CHARACTERS + 1 = 9);
- §13 whole-attempt pass accounting (4 controller passes; the global envelope
  is never derived from a single pass);
- §14 asset-ordering independence (assets before/between/after Core calls).

No real model is ever dispatched (the mocked-transport harness +
BudgetTracker arithmetic only).
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.generation.budgets import (  # noqa: E402
    CORE_BUCKET,
    BudgetTracker,
    DEFAULT_MAX_FULL_REGENERATIONS,
    DEFAULT_MAX_LLM_CALLS_PER_PROCEDURAL_ASSET,
    DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION,
    DEFAULT_MAX_REPAIR_PASSES,
    derive_asset_call_budget_default,
    derive_core_call_budget_default,
    derive_global_call_budget_default,
)
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.failure_codes import (  # noqa: E402
    GenerationFailureCode,
    failure_code_for_budget_reason,
    public_failure_code,
)
from app.generation.schemas import MAX_CHARACTERS, MAX_EVIDENCE_ITEMS  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES  # noqa: E402
from test_phase26_c3_budget import (  # noqa: E402
    KNIFE_PROMPT,
    _live_posts,
    _run_live,
)


# --------------------------------------------------------------------------- #
# helpers — the canonical legal envelope at the BudgetTracker level
# --------------------------------------------------------------------------- #


def _envelope_tracker() -> BudgetTracker:
    """A tracker wired to the canonical defaults: global 720 / Core 620 /
    per-asset 5 / 20 distinct procedural assets — the C4 legal envelope."""
    return BudgetTracker(
        ManualClock(),
        deadline_seconds=300,
        max_calls=derive_global_call_budget_default(),
        max_repairs=DEFAULT_MAX_REPAIR_PASSES,
        max_regenerations=DEFAULT_MAX_FULL_REGENERATIONS,
        max_core_calls=derive_core_call_budget_default(),
        max_asset_calls=DEFAULT_MAX_LLM_CALLS_PER_PROCEDURAL_ASSET,
        max_procedural_assets=DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION,
        max_failed_assets=3,
    )


def _legal_core_calls(tracker: BudgetTracker) -> int:
    """Reserve the ENTIRE legal Core envelope (620 CORE-bucket calls)."""
    core = derive_core_call_budget_default()
    assert all(tracker.consume_core_call() for _ in range(core))
    return core


def _legal_asset_calls(tracker: BudgetTracker) -> int:
    """Reserve the ENTIRE legal asset envelope: 20 distinct procedural objects
    × 5 per-asset calls each = 100 ASSET-bucket calls, with each object
    registered through the procedural-asset count guard too."""
    calls = 0
    for index in range(DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION):
        object_id = f"proc_obj_{index:02d}"
        assert tracker.consume_procedural_asset(object_id)
        for _ in range(DEFAULT_MAX_LLM_CALLS_PER_PROCEDURAL_ASSET):
            assert tracker.consume_call(bucket=object_id)
            calls += 1
    return calls


def _run_global_ceiling(
    global_budget: int,
    *,
    posts=None,
    max_core_llm_calls: int | None = None,
    max_repair_passes: int = 2,
    max_full_regenerations: int = 1,
):
    """Driver-harness run over the C3 live queue with an explicit GLOBAL cap
    (the tests that exercise the global envelope as the operative hard stop).
    ``max_core_llm_calls=None`` keeps the derived Core envelope so ONLY the
    global (superset) cap can bind the analyzed boundary."""
    from app.generation.ids import IdSource

    from test_ollama_driver import (
        _admission,
        _controller,
        _make_driver,
        MockOllamaTransport,
    )

    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(
        posts=list(posts if posts is not None else _live_posts())
    )
    driver = _make_driver(transport)
    controller = _controller(
        driver,
        transport,
        admission,
        clock,
        ids,
        max_llm_calls_per_generation=global_budget,
        max_core_llm_calls=(
            max_core_llm_calls
            if max_core_llm_calls is not None
            else derive_core_call_budget_default()
        ),
        max_llm_calls_per_procedural_asset=DEFAULT_MAX_LLM_CALLS_PER_PROCEDURAL_ASSET,
        max_procedural_assets_per_generation=DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION,
        max_failed_assets_per_generation=3,
        max_repair_passes=max_repair_passes,
        max_full_regenerations=max_full_regenerations,
    )
    handle = controller.start_generation(
        KNIFE_PROMPT, anonymous_quota_session_id=session.session_id
    )
    return controller.attempt(handle.attempt_id), transport


# --------------------------------------------------------------------------- #
# §11 items 1..6 — the canonical derivation (no magic numbers)
# --------------------------------------------------------------------------- #


def test_c4_derived_core_default_remains_620():
    """§11.1 — the Core legal maximum is the DERIVED whole-attempt value
    (never a magic literal); per-pass math pinned from canonical constants."""
    from app.generation.budgets import (
        DEFAULT_MAX_FULL_REGENERATIONS as _DREG,
        DEFAULT_MAX_REPAIR_PASSES as _DRP,
    )

    per_item = 1 + MAX_ACTIVITY_LOG_REPAIR_PASSES
    max_log_facts = max(MAX_CHARACTERS + 1, MAX_EVIDENCE_ITEMS)
    per_pass = 5 + max_log_facts * per_item
    total_passes = 1 + _DRP + _DREG
    assert per_item == 3
    assert max_log_facts == 50
    assert per_pass == 155
    assert total_passes == 4
    assert derive_core_call_budget_default() == total_passes * per_pass == 620


def test_c4_derived_asset_envelope_is_100():
    """§11.2 — the legal asset-wide envelope is derived from the canonical
    asset bounds (20 distinct objects × 5 per-asset calls)."""
    assert (
        derive_asset_call_budget_default()
        == DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION
        * DEFAULT_MAX_LLM_CALLS_PER_PROCEDURAL_ASSET
        == 100
    )
    # raising a subordinate asset bound raises the legal envelope.
    assert (
        derive_asset_call_budget_default(max_procedural_assets=25)
        == 25 * DEFAULT_MAX_LLM_CALLS_PER_PROCEDURAL_ASSET
        == 125
    )
    assert (
        derive_asset_call_budget_default(max_llm_calls_per_procedural_asset=4)
        == DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION * 4
        == 80
    )


def test_c4_derived_global_default_is_720():
    """§11.3 — the global default is EXACTLY the canonical Core legal maximum
    plus the legal asset envelope (an output of the constants, not a literal)."""
    assert (
        derive_global_call_budget_default()
        == derive_core_call_budget_default() + derive_asset_call_budget_default()
        == 620 + 100
        == 720
    )


def test_c4_no_literal_128_remains_the_default_source_of_truth():
    """§11.4 — the legacy literal-128 global default is gone: the Settings
    default is the derived legal envelope, and the config module no longer
    carries the old ``default=128`` field default."""
    from app.core import config as config_mod

    source = Path(config_mod.__file__).read_text(encoding="utf-8")
    assert "default=128" not in source
    settings = Settings()
    assert settings.max_llm_calls_per_generation == derive_global_call_budget_default()
    assert settings.max_llm_calls_per_generation != 128


def test_c4_no_literal_720_becomes_the_canonical_source_of_truth():
    """§11.5 — ``720`` is an OUTPUT of the canonical constants, not a stored
    source of truth: changing a subordinate canonical bound moves the derived
    envelope automatically (asset count and regeneration allowances probed)."""
    from app.generation import budgets as budgets_mod

    base = derive_global_call_budget_default()
    assert base == 720
    # fewer distinct assets -> smaller legal envelope (no stale 720).
    monkeypatch_passed = False
    original = budgets_mod.DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION
    try:
        budgets_mod.DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION = 10
        monkeypatch_passed = True
        assert derive_global_call_budget_default() == 620 + 10 * 5 == 670
    finally:
        budgets_mod.DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION = original
    assert monkeypatch_passed


def test_c4_valid_default_settings_resolve_to_720_620():
    """§11.6 — the canonical defaults resolve to global=720 / Core=620."""
    settings = Settings()
    assert settings.max_llm_calls_per_generation == 720
    assert settings.max_core_llm_calls_per_generation == 620
    assert settings.max_llm_calls_per_procedural_asset == 5
    assert settings.max_procedural_assets_per_generation == 20


# --------------------------------------------------------------------------- #
# §11 items 7..13 — cross-setting fail-fast configuration validation
# --------------------------------------------------------------------------- #


def test_c4_global_128_override_rejected():
    """§11.7 — the legacy explicit ``MAX_LLM_CALLS_PER_GENERATION=128`` fails
    fast as a configuration error (it is below the legal envelope)."""
    with pytest.raises(Exception) as excinfo:
        Settings(max_llm_calls_per_generation=128)
    text = str(excinfo.value)
    assert "MAX_LLM_CALLS_PER_GENERATION" in text
    assert "minimum" in text


def test_c4_global_620_override_rejected():
    """§11.8 — global=620 (== Core alone) cannot contain the +100 asset
    envelope; rejected."""
    with pytest.raises(Exception):
        Settings(max_llm_calls_per_generation=620)


def test_c4_global_719_override_rejected():
    """§11.9 — one below the legal envelope (719 < 720) is rejected."""
    with pytest.raises(Exception):
        Settings(max_llm_calls_per_generation=719)


def test_c4_global_720_override_accepted():
    """§11.10 — exactly the legal envelope succeeds."""
    settings = Settings(max_llm_calls_per_generation=720)
    assert settings.max_llm_calls_per_generation == 720
    assert settings.max_core_llm_calls_per_generation == 620


def test_c4_global_above_720_accepted():
    """§11.11 — an override ABOVE the legal envelope succeeds (operator wants
    more headroom); the global guard still rejects envelope + 1 at runtime."""
    for value in (721, 1000):
        assert Settings(max_llm_calls_per_generation=value).max_llm_calls_per_generation == value


def test_c4_custom_core_raises_global_minimum():
    """§11.12 — raising Core above its derived minimum raises the required
    global minimum accordingly (global >= resolved_core + asset envelope)."""
    # core=700 unset global -> the default envelope follows: 700 + 100 = 800.
    settings = Settings(max_core_llm_calls_per_generation=700)
    assert settings.max_core_llm_calls_per_generation == 700
    assert settings.max_llm_calls_per_generation == 800
    # global=799 with core=700 is INVALID; global=800 is VALID.
    with pytest.raises(Exception):
        Settings(max_core_llm_calls_per_generation=700, max_llm_calls_per_generation=799)
    assert (
        Settings(
            max_core_llm_calls_per_generation=700, max_llm_calls_per_generation=800
        ).max_llm_calls_per_generation
        == 800
    )
    # custom asset bounds move the minimum too: 620 + 25*5 = 745.
    with pytest.raises(Exception):
        Settings(
            max_procedural_assets_per_generation=25, max_llm_calls_per_generation=744
        )
    assert (
        Settings(
            max_procedural_assets_per_generation=25, max_llm_calls_per_generation=745
        ).max_llm_calls_per_generation
        == 745
    )


def test_c4_custom_core_below_legal_minimum_still_rejected():
    """§11.13 — the C3 fail-fast Core validation is unchanged: an override
    below the derived Core legal maximum stays a configuration error even with
    a large valid global envelope."""
    for bad in (12, derive_core_call_budget_default() - 1):
        with pytest.raises(Exception):
            Settings(
                max_core_llm_calls_per_generation=bad,
                max_llm_calls_per_generation=derive_global_call_budget_default(),
            )


def test_c4_env_override_precedence_and_fail_fast():
    """The env override stays authoritative (init > env) and below-minimum
    MAX_LLM_CALLS_PER_GENERATION env values fail fast; construction kwargs win
    over the env var (backward compatible resolution rules, §18)."""
    try:
        os.environ["MAX_LLM_CALLS_PER_GENERATION"] = "750"
        assert Settings().max_llm_calls_per_generation == 750
        # construction keyword still wins over the env var (init > env).
        assert (
            Settings(max_llm_calls_per_generation=721).max_llm_calls_per_generation
            == 721
        )
        os.environ["MAX_LLM_CALLS_PER_GENERATION"] = "128"
        with pytest.raises(Exception):
            Settings()
        with pytest.raises(Exception):
            Settings(max_llm_calls_per_generation=128)
    finally:
        os.environ.pop("MAX_LLM_CALLS_PER_GENERATION", None)


def test_c4_operator_error_is_clear_text():
    """§18 — the fail-fast global error states the operator-friendly concept:
    the configured global budget is below the minimum required by the
    configured legal Core + asset bounds (a startup error, never a browser
    message)."""
    with pytest.raises(Exception) as excinfo:
        Settings(max_llm_calls_per_generation=128)
    text = str(excinfo.value)
    assert "minimum" in text
    assert "Core" in text
    assert "asset" in text


# --------------------------------------------------------------------------- #
# §9/§10/§11 items 14..18 — the exact boundary at the tracker level
# --------------------------------------------------------------------------- #


def test_c4_legal_core_only_worst_case_fits():
    """§11.14 — the legal Core-only worst case (620 CORE-bucket calls) fits
    inside the global envelope; the 621st fails with the narrow CORE code."""
    tracker = _envelope_tracker()
    _legal_core_calls(tracker)
    assert tracker.core_calls == 620
    assert tracker.calls == 620
    assert tracker.remaining_core_calls() == 0
    assert tracker.remaining_global_calls() == 100
    assert not tracker.consume_core_call()
    assert tracker.exhausted_reason(CORE_BUCKET) == "core model call budget exhausted"


def test_c4_legal_asset_only_worst_case_fits():
    """§11.15 — the legal asset-only worst case (100 calls across 20 distinct
    objects) fits inside the global envelope; the 101st asset call fails
    globally and the 21st distinct asset cannot register at all."""
    tracker = _envelope_tracker()
    _legal_asset_calls(tracker)
    assert tracker.asset_calls == 100
    assert tracker.core_calls == 0
    assert tracker.calls == 100
    assert tracker.procedural_asset_count == 20
    assert tracker.remaining_global_calls() == 620
    # a 21st distinct asset is blocked by the procedural-asset COUNT guard.
    assert not tracker.consume_procedural_asset("proc_obj_20")
    # a 6th call on an already-registered object is blocked by the per-asset cap.
    assert not tracker.consume_call(bucket="proc_obj_00")
    assert tracker.exhausted_reason("proc_obj_00") == (
        "asset model call budget exhausted for proc_obj_00"
    )


def test_c4_legal_mixed_envelope_fits_then_721_fails_globally():
    """§9/§10/§11.16-17 — a LEGAL 720-call composition (620 Core + 100 asset
    reservations) is accepted by the global envelope — synthetic identical-Core
    sequences are NOT a legal composition (§10, covered separately) — and the
    envelope + 1 call fails closed on the GLOBAL guard with the canonical
    provider-call-budget exhaustion code."""
    tracker = _envelope_tracker()
    _legal_asset_calls(tracker)  # assets first: ordering must not matter
    _legal_core_calls(tracker)
    assert tracker.calls == 720
    assert tracker.core_calls == 620
    assert tracker.asset_calls == 100
    assert tracker.calls == tracker.core_calls + tracker.asset_calls
    assert tracker.remaining_global_calls() == 0
    # envelope + 1 -> the GLOBAL superset guard rejects it (not removed).
    assert not tracker.consume_core_call()
    assert tracker.exhausted_reason(CORE_BUCKET) == "model call budget exhausted"
    assert (
        failure_code_for_budget_reason(tracker.exhausted_reason(CORE_BUCKET))
        is GenerationFailureCode.PROVIDER_CALL_BUDGET_EXHAUSTED
    )
    assert (
        public_failure_code(GenerationFailureCode.PROVIDER_CALL_BUDGET_EXHAUSTED)
        == "PROVIDER_CALL_BUDGET_EXHAUSTED"
    )


def test_c4_synthetic_720_identical_core_calls_is_not_legal():
    """§10 — 720 identical Core-bucket reservations are NOT a legal pipeline:
    the Core bucket (620) must bind FIRST with the narrow CORE code, and the
    asset envelope is only legal when composed through the per-asset buckets."""
    tracker = _envelope_tracker()
    ok = all(tracker.consume_core_call() for _ in range(620))
    assert ok
    assert not tracker.consume_core_call()  # Core cap binds at 620
    assert tracker.core_calls == 620
    assert tracker.asset_calls == 0
    assert tracker.calls == 620
    reason = tracker.exhausted_reason(CORE_BUCKET)
    assert reason == "core model call budget exhausted"
    assert (
        failure_code_for_budget_reason(reason)
        is GenerationFailureCode.CORE_PROVIDER_CALL_BUDGET_EXHAUSTED
    )


def test_c4_global_calls_equals_core_plus_asset_calls_invariant():
    """§11.18 — at EVERY step of a mixed sequence the global counter is exactly
    the sum of the bucket counters (the accounting model the C4 envelope
    preserves)."""
    tracker = _envelope_tracker()
    assert tracker.calls == tracker.core_calls + tracker.asset_calls == 0
    tracker.consume_core_call()
    tracker.consume_core_call()
    assert tracker.calls == tracker.core_calls + tracker.asset_calls == 2
    tracker.consume_call(bucket="obj_a")
    tracker.consume_call(bucket="obj_b")
    tracker.consume_call(bucket="obj_a")
    assert tracker.calls == tracker.core_calls + tracker.asset_calls == 5
    assert tracker.core_calls == 2
    assert tracker.asset_calls == 3
    _legal_core_calls_from = derive_core_call_budget_default() - tracker.core_calls
    for _ in range(_legal_core_calls_from):
        tracker.consume_core_call()
    assert tracker.calls == tracker.core_calls + tracker.asset_calls


# --------------------------------------------------------------------------- #
# §14 — asset ordering must not matter
# --------------------------------------------------------------------------- #


def test_c4_asset_ordering_before_core_after_interleaved_all_fit():
    """§14 — the global envelope supports legal calls regardless of whether
    assets occur before Core calls, after Core calls, or interleaved between
    them; the cap never reserves headroom based on ordering."""
    # (a) 100 asset calls first, then the legal Core envelope.
    t1 = _envelope_tracker()
    _legal_asset_calls(t1)
    _legal_core_calls(t1)
    assert (t1.calls, t1.core_calls, t1.asset_calls) == (720, 620, 100)

    # (b) the legal Core envelope first, then 100 asset calls.
    t2 = _envelope_tracker()
    _legal_core_calls(t2)
    _legal_asset_calls(t2)
    assert (t2.calls, t2.core_calls, t2.asset_calls) == (720, 620, 100)

    # (c) interleaved: each block of 31 Core calls is followed by one full
    # asset object (5 calls); 20 blocks × 31 = 620 Core + 20 × 5 = 100 assets.
    t3 = _envelope_tracker()
    block = 620 // DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION  # 31
    for index in range(DEFAULT_MAX_PROCEDURAL_ASSETS_PER_GENERATION):
        for _ in range(block):
            assert t3.consume_core_call()
        object_id = f"interleaved_obj_{index:02d}"
        assert t3.consume_procedural_asset(object_id)
        for _ in range(DEFAULT_MAX_LLM_CALLS_PER_PROCEDURAL_ASSET):
            assert t3.consume_call(bucket=object_id)
    assert (t3.calls, t3.core_calls, t3.asset_calls) == (720, 620, 100)
    # in EVERY ordering the very next call is globally rejected.
    for tracker in (t1, t2, t3):
        assert not tracker.consume_core_call()
        assert tracker.exhausted_reason(CORE_BUCKET) == "model call budget exhausted"


# --------------------------------------------------------------------------- #
# §12 — the raw-model-evidence fallback stays the binding Core multiplier
# --------------------------------------------------------------------------- #

def test_c4_raw_evidence_fallback_multiplier_not_regressed():
    """§12 — the raw-model-evidence fallback (MAX_EVIDENCE_ITEMS = 50 facts) is
    the BINDING multiplier in the Core derivation; a regression to the
    parsed-only (MAX_CHARACTERS + 1 = 9) multiplier would yield a SMALLER
    Core/global envelope and is pinned below (the prior adversarial F1a root
    cause stays locked)."""
    from app.generation.budgets import (
        DEFAULT_MAX_FULL_REGENERATIONS as _DREG,
        DEFAULT_MAX_REPAIR_PASSES as _DRP,
    )

    parsed_only_facts = MAX_CHARACTERS + 1
    raw_fallback_facts = MAX_EVIDENCE_ITEMS
    assert parsed_only_facts == 9
    assert raw_fallback_facts == 50
    assert raw_fallback_facts > parsed_only_facts  # the binding path
    per_item = 1 + MAX_ACTIVITY_LOG_REPAIR_PASSES
    total_passes = 1 + _DRP + _DREG
    derived = derive_core_call_budget_default()
    # the derivation uses the RAW path as the binding multiplier, not 9.
    assert derived == total_passes * (5 + raw_fallback_facts * per_item) == 620
    # a would-be regression to the parsed-only multiplier gives the OLD
    # smaller envelope (4 × 32 = 128) — a value that is NOT the current
    # default and that a global override at 128 now REJECTS.
    regressed = total_passes * (5 + parsed_only_facts * per_item)
    assert regressed == 128
    assert regressed < derived
    with pytest.raises(Exception):
        Settings(max_llm_calls_per_generation=regressed)


# --------------------------------------------------------------------------- #
# §13 — pass / regeneration whole-attempt accounting
# --------------------------------------------------------------------------- #

def test_c4_four_passes_whole_attempt_accounting():
    """§13 — the global envelope is the WHOLE-attempt maximum:
    (1 + MAX_REPAIR_PASSES + MAX_FULL_REGENERATIONS) = 4 passes; deriving it
    from a single pass alone (155 + 100 = 255) is NOT the legal envelope."""
    total_passes = 1 + DEFAULT_MAX_REPAIR_PASSES + DEFAULT_MAX_FULL_REGENERATIONS
    per_pass = 5 + MAX_EVIDENCE_ITEMS * (1 + MAX_ACTIVITY_LOG_REPAIR_PASSES)
    assert total_passes == 4
    assert per_pass == 155
    assert derive_global_call_budget_default() == total_passes * per_pass + 100 == 720
    single_pass_only = per_pass + 100
    assert single_pass_only == 255
    assert single_pass_only < derive_global_call_budget_default()


def test_c4_more_regenerations_raise_core_and_global_minimum():
    """§13 — raising the regeneration allowance raises BOTH derived minimums:
    global = (1 + 3 + 2) passes... with max_full_regenerations=3 the Core
    minimum is 930 and the global minimum is 1030; a stale 720 global override
    is then REJECTED (fail-fast, custom Core interaction §7/§11.12)."""
    higher_core = derive_core_call_budget_default(max_full_regenerations=3)
    assert higher_core == 930
    higher_global = derive_global_call_budget_default(max_full_regenerations=3)
    assert higher_global == 1030
    settings = Settings(max_full_regenerations=3)
    assert settings.max_core_llm_calls_per_generation == higher_core
    assert settings.max_llm_calls_per_generation == higher_global
    with pytest.raises(Exception):
        Settings(
            max_full_regenerations=3,
            max_llm_calls_per_generation=derive_global_call_budget_default(),
        )
    assert (
        Settings(
            max_full_regenerations=3, max_llm_calls_per_generation=higher_global
        ).max_llm_calls_per_generation
        == higher_global
    )


def test_c4_tracker_supports_four_full_passes_plus_asset_envelope():
    """§13 (tracker level) — one tracker can legally burn the FULL four-pass
    Core worst case (620) AND the asset envelope (100) = 720 exactly on the
    SAME monotonic counters; the Core per-pass allowance is preserved per
    pass (F1b accounting, now inside the derived GLOBAL envelope)."""
    tracker = _envelope_tracker()
    per_pass = 155
    for _ in range(3):  # first three worst-case passes
        assert all(tracker.consume_core_call() for _ in range(per_pass))
    assert tracker.remaining_core_calls() == per_pass
    assert all(tracker.consume_core_call() for _ in range(per_pass))  # 4th pass
    assert tracker.remaining_core_calls() == 0
    _legal_asset_calls(tracker)
    assert tracker.calls == 720
    assert not tracker.consume_core_call()
    assert tracker.exhausted_reason(CORE_BUCKET) == "model call budget exhausted"


# --------------------------------------------------------------------------- #
# §11 items 19..22 — attempt / concurrency / provider-path accounting
# --------------------------------------------------------------------------- #

def test_c4_global_budget_resets_per_generation_attempt():
    """§11.19 — a fresh attempt always receives a FRESH BudgetTracker: the
    global counter starts at zero on the second controller attempt too."""
    record1, t1 = _run_global_ceiling(derive_global_call_budget_default())
    assert record1.state is GenerationState.PUBLISHED
    assert record1.budget.calls == 13
    record2, _t2 = _run_global_ceiling(derive_global_call_budget_default())
    assert record2.state is GenerationState.PUBLISHED
    assert record2.budget.calls == 13
    assert record2.budget.calls == record2.budget.core_calls + record2.budget.asset_calls


def test_c4_concurrent_attempts_remain_isolated():
    """§11.20 — two full live chains run SIMULTANEOUSLY (barrier-synchronized
    threads, no sleeps); each attempt keeps its OWN global/bucket counters."""
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _run_in_thread(key: str) -> None:
        barrier.wait()
        try:
            results[key] = _run_global_ceiling(derive_global_call_budget_default())
        except Exception as exc:  # pragma: no cover - surfaced below
            results[key] = exc

    threads = [
        threading.Thread(target=_run_in_thread, args=("A",)),
        threading.Thread(target=_run_in_thread, args=("B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for key in ("A", "B"):
        result = results[key]
        assert not isinstance(result, Exception), result
        record, _transport = result
        assert record.state is GenerationState.PUBLISHED, key
        assert record.budget.calls == 13, key
        assert record.budget.calls == (
            record.budget.core_calls + record.budget.asset_calls
        ), key


def test_c4_bridge_and_direct_share_identical_budget_semantics(database_url):
    """§11.21 — Direct and Bridge resolve the SAME derived global envelope from
    the SAME Settings model (the remote_client/bridge path uses the identical
    GenerationController wiring; its BudgetTracker starts at the same caps)."""
    from bridge_harness import make_bridge_settings

    settings = make_bridge_settings(database_url)
    assert settings.max_llm_calls_per_generation == (
        derive_global_call_budget_default()
    )
    assert settings.max_core_llm_calls_per_generation == (
        derive_core_call_budget_default()
    )


def test_c4_fake_and_frontier_paths_retain_intended_accounting():
    """§11.22 — the deterministic FakeProvider path publishes with exact
    core-only accounting under the derived global envelope, and the Frontier
    operator configuration still constructs with the SAME derived defaults."""
    from fixtures.golden_generation import (
        GOLDEN_FULL_DRAFT,
        GOLDEN_STAGE_PAYLOADS,
    )
    from app.generation.admission import AdmissionController
    from app.generation.controller import GenerationController
    from app.generation.fake_provider import FakeProvider
    from app.generation.ids import IdSource

    script = {stage: [payload] for stage, payload in GOLDEN_STAGE_PAYLOADS.items()}
    from app.generation.provider import GenerationStage

    script[GenerationStage.REPAIR] = [GOLDEN_FULL_DRAFT]
    clock, ids = ManualClock(), IdSource()
    admission = AdmissionController(
        clock=clock, ids=ids,
        max_concurrent_generations=1, max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3, max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=FakeProvider(script),
        admission=admission, clock=clock, ids=ids,
        deadline_seconds=60, max_llm_calls_per_generation=derive_global_call_budget_default(),
        max_core_llm_calls=derive_core_call_budget_default(),
        max_repair_passes=2, max_full_regenerations=1, max_prompt_chars=4000,
        seed=11,
    )
    handle = controller.start_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
        "Weapon: kitchen_knife\nTime: 22:17\nWitness: emily_reed\n",
        anonymous_quota_session_id=session.session_id,
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    budget = record.budget
    assert budget.calls == budget.core_calls + budget.asset_calls
    assert budget.asset_calls == 0
    assert budget.core_calls <= derive_core_call_budget_default()
    assert budget.calls < derive_global_call_budget_default()

    # Frontier operator config constructs with the derived defaults untouched.
    frontier = Settings(
        generation_provider="frontier",
        frontier_enabled=True,
        frontier_base_url="https://api.example.com/v1/chat/completions",
        frontier_api_key="probe-key",
        frontier_model="probe-model",
    )
    assert frontier.max_llm_calls_per_generation == derive_global_call_budget_default()
    assert frontier.max_core_llm_calls_per_generation == derive_core_call_budget_default()


# --------------------------------------------------------------------------- #
# §11 items 23..25 — C3 regression, global failure code, UI mapping stability
# --------------------------------------------------------------------------- #

def test_c4_c3_live_12_to_13_world_graph_regression_stays_green():
    """§11.23 — the C3 exact live 12→13 world_graph regression remains green
    under the derived GLOBAL envelope: the 12-call sequence reaches world_graph
    as call 13 and publishes, and the global (720) never disrupts the legal
    path the CORE budget (620) already permits."""
    record, transport = _run_live(max_core_llm_calls=derive_core_call_budget_default())
    assert record.state is GenerationState.PUBLISHED
    assert record.failure_code is None
    assert transport.call_count == 13
    assert record.budget.calls == 13
    assert record.budget.core_calls == 13
    assert record.budget.asset_calls == 0
    assert record.budget.calls < record.budget.max_calls  # envelope allows it


def test_c4_global_budget_exhaustion_failure_code_reachable():
    """§11.24 — the provider-call-exhaustion failure code remains REACHABLE at
    the wire level: with a tiny explicit global ceiling (2), the run exhausts
    the GLOBAL superset cap and fails closed with
    PROVIDER_CALL_BUDGET_EXHAUSTED (the code the browser maps to the truthful
    safety-limit message)."""
    record, transport = _run_global_ceiling(2)
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert record.budget.calls == 2
    assert record.budget.core_calls == 2
    assert record.budget.asset_calls == 0
    assert transport.call_count == 2  # the 3rd call was never reserved
    # the narrow CORE code is NOT collapsed into the generic one here: the
    # GLOBAL superset cap bound, so the code surface stays distinguishable.
    assert record.budget.exhausted_reason(CORE_BUCKET) == "model call budget exhausted"


def test_c4_ui_safety_limit_mapping_unchanged():
    """§11.25 — the externally visible failure-code surface C3 mapped in the UI
    ("safety limits" messaging) is unchanged: the global exhaustion code is
    still ``PROVIDER_CALL_BUDGET_EXHAUSTED`` and the legitimate sub-bucket
    codes stay distinct. The C3 frontend mapping itself is untouched (verified
    green by the frontend suite in the gate)."""
    assert (
        public_failure_code(GenerationFailureCode.PROVIDER_CALL_BUDGET_EXHAUSTED)
        == "PROVIDER_CALL_BUDGET_EXHAUSTED"
    )
    assert (
        public_failure_code(GenerationFailureCode.CORE_PROVIDER_CALL_BUDGET_EXHAUSTED)
        == "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED"
    )
    assert (
        public_failure_code(GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED)
        == "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"
    )


# --------------------------------------------------------------------------- #
# §16 — observability: safe configured-budget fields
# --------------------------------------------------------------------------- #

def test_c4_observability_configured_budgets_on_lifecycle_events(caplog):
    """§16 — the lifecycle events carry the SAFE configured GLOBAL and CORE
    provider-call ceilings (integers only) alongside the C3 ``providerCallBudget``
    and the runtime counters; nothing sensitive is ever logged."""
    import logging

    from app.core.observability import _SAFE_FIELDS

    assert "configuredGlobalProviderCallBudget" in _SAFE_FIELDS
    assert "configuredCoreProviderCallBudget" in _SAFE_FIELDS
    assert "providerCallCount" in _SAFE_FIELDS
    assert "coreCallCount" in _SAFE_FIELDS
    assert "assetCallCount" in _SAFE_FIELDS

    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, _transport = _run_global_ceiling(derive_global_call_budget_default())
    assert record.state is GenerationState.PUBLISHED

    by_name: dict[str, dict] = {}
    for event in caplog.records:
        ev = getattr(event, "pd_event", None)
        fields = dict(getattr(event, "pd_fields", {}) or {})
        if ev in ("generation.started", "generation.published", "generation.failed"):
            by_name[ev] = fields
    assert "generation.started" in by_name
    assert "generation.published" in by_name
    for event_name in ("generation.started", "generation.published"):
        fields = by_name[event_name]
        assert fields["configuredGlobalProviderCallBudget"] == (
            derive_global_call_budget_default()
        )
        assert fields["configuredCoreProviderCallBudget"] == (
            derive_core_call_budget_default()
        )
        # C3's narrowest-cap field keeps its meaning (the CORE cap).
        assert fields["providerCallBudget"] == derive_core_call_budget_default()
        # the FAILED event carries the same safe configured budgets.
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        failed_record, _t2 = _run_global_ceiling(2)
    assert failed_record.state is GenerationState.FAILED
    for event in caplog.records:
        fields = dict(getattr(event, "pd_fields", {}) or {})
        if getattr(event, "pd_event", None) == "generation.failed":
            assert fields["configuredGlobalProviderCallBudget"] == 2
            assert fields["configuredCoreProviderCallBudget"] == (
                derive_core_call_budget_default()
            )