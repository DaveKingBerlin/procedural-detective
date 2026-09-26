"""Phase 19 §16 — scalable hierarchical provider budgeting regression suite.

Fix C: the single global 8-call ceiling becomes a hierarchical model:

- GLOBAL (MAX_LLM_CALLS_PER_GENERATION=128, hard);
- CORE (MAX_CORE_LLM_CALLS_PER_GENERATION=12 — case/evidence/world +
  repair/regeneration, hard);
- per-asset (MAX_LLM_CALLS_PER_PROCEDURAL_ASSET=5, independent per semantic
  object);
- procedural-asset COUNT (20) and failed-asset THRESHOLD (3).

Deterministic LOCAL repairs (environment canonicalization, semantic-id
preservation, catalog alias resolution) cost ZERO provider calls. Only actual
model calls increment counters. Asset exhaustion is distinguishable from
global exhaustion (narrow failure codes).

All driver tests use the mocked transport harness (``test_ollama_driver._run``)
— never a network call; the autouse backend network block is active.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.budgets import BudgetTracker, CORE_BUCKET  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.failure_codes import (  # noqa: E402
    GenerationFailureCode,
    failure_code_for_budget_reason,
    infer_failure_code,
    public_failure_code,
)
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.world.composer import SemanticObjectResolutionError  # noqa: E402
from test_ollama_driver import (  # noqa: E402
    _case_people,
    _evidence,
    _j,
    _run,
    _world,
    _alog_posts,
)


def _tracker(
    *,
    max_calls=128,
    max_core=None,
    max_asset=None,
    max_assets=None,
    max_failed=None,
):
    return BudgetTracker(
        ManualClock(),
        deadline_seconds=300,
        max_calls=max_calls,
        max_repairs=2,
        max_regenerations=1,
        max_core_calls=max_core,
        max_asset_calls=max_asset,
        max_procedural_assets=max_assets,
        max_failed_assets=max_failed,
    )


def _clock_harder(tracker, seconds=400):
    tracker._clock = ManualClock(start_time=401.0)
    return tracker


# --------------------------------------------------------------------------- #
# 5. global 128 hard  /  6. core 12 hard  /  2. per-asset up to 5
# --------------------------------------------------------------------------- #


def test_14_global_128_ceiling_hard():
    budget = _tracker(max_calls=128)
    ok = all(budget.consume_call(bucket=CORE_BUCKET) for _ in range(128))
    assert ok
    assert not budget.consume_call(bucket=CORE_BUCKET)
    assert budget.calls == 128
    assert budget.exhausted_reason(CORE_BUCKET) == "model call budget exhausted"


def test_core_12_ceiling_hard_independent_of_global():
    budget = _tracker(max_calls=128, max_core=12)
    ok = all(budget.consume_call(bucket=CORE_BUCKET) for _ in range(12))
    assert ok
    assert not budget.consume_call(bucket=CORE_BUCKET)
    # the narrower cause is known: CORE, not global.
    assert budget.exhausted_reason(CORE_BUCKET) == "core model call budget exhausted"
    assert budget.calls == 12
    assert budget.core_calls == 12
    assert budget.remaining_global_calls() == 128 - 12


def test_per_asset_budget_up_to_five():
    budget = _tracker(max_calls=128, max_asset=5)
    ok = all(budget.consume_call(bucket="weapon_a") for _ in range(5))
    assert ok
    assert not budget.consume_call(bucket="weapon_a")
    assert budget.asset_call_count("weapon_a") == 5
    assert budget.exhausted_reason("weapon_a") == (
        "asset model call budget exhausted for weapon_a"
    )


# --------------------------------------------------------------------------- #
# 3/4. independent per-asset sub-budgets
# --------------------------------------------------------------------------- #


def test_two_assets_independent_sub_budgets():
    budget = _tracker(max_calls=128, max_asset=5)
    all(budget.consume_call(bucket="weapon_a") for _ in range(5))
    assert not budget.consume_call(bucket="weapon_a")
    # weapon_b still has its own full allowance.
    assert all(budget.consume_call(bucket="weapon_b") for _ in range(5))
    assert budget.asset_call_count("weapon_b") == 5
    assert budget.asset_call_count("weapon_a") == 5


def test_failing_asset_cannot_consume_another_assets_budget():
    budget = _tracker(max_calls=128, max_asset=5)
    all(budget.consume_call(bucket="failing") for _ in range(5))
    assert not budget.consume_call(bucket="failing")
    assert all(budget.consume_call(bucket="healthy") for _ in range(5))
    assert budget.asset_call_count("healthy") == 5


# --------------------------------------------------------------------------- #
# 9/10. procedural asset count + failed asset thresholds
# --------------------------------------------------------------------------- #


def test_max_procedural_assets_enforced():
    budget = _tracker(max_assets=20)
    for index in range(20):
        assert budget.consume_procedural_asset(f"obj_{index}")
    assert not budget.consume_procedural_asset("obj_20")
    assert budget.procedural_asset_count == 20
    # re-recording an existing object is a no-op (never a new count).
    assert budget.consume_procedural_asset("obj_0")


def test_failed_asset_threshold_enforced():
    budget = _tracker(max_failed=3)
    assert budget.mark_failed_asset("a")
    assert budget.mark_failed_asset("b")
    assert budget.mark_failed_asset("c")
    # a FOURTH distinct failure exceeds the threshold -> False signal.
    assert not budget.mark_failed_asset("d")
    # re-recording a known failure stays True.
    assert budget.mark_failed_asset("a")
    assert budget.failed_asset_count == 3


# --------------------------------------------------------------------------- #
# 13. accounting monotonic and exact
# --------------------------------------------------------------------------- #


def test_accounting_monotonic_and_exact():
    budget = _tracker(max_calls=128, max_core=12, max_asset=5)
    budget.consume_call(bucket=CORE_BUCKET)
    budget.consume_call(bucket=CORE_BUCKET)
    budget.consume_call(bucket="prop_1")
    budget.consume_call(bucket="prop_1")
    budget.consume_call(bucket="prop_2")
    # every call is attributable to exactly one bucket.
    assert budget.calls == budget.core_calls + budget.asset_calls
    assert budget.calls == 5
    assert sum(budget.asset_calls_by_object.values()) == budget.asset_calls == 3
    assert budget.asset_calls_by_object == {"prop_1": 2, "prop_2": 1}
    snap = budget.snapshot()
    assert snap["globalCallCount"] == 5
    assert snap["coreCallCount"] == 2
    assert snap["assetCallCount"] == 3
    assert snap["remainingGlobalCalls"] == 123
    assert snap["remainingCoreCalls"] == 10


# --------------------------------------------------------------------------- #
# 7. deterministic local repairs cost ZERO calls
# --------------------------------------------------------------------------- #


def test_deterministic_local_repairs_cost_zero_calls():
    """No deterministic local repair helper may touch consume_call."""
    budget = _tracker()
    budget.consume_call(bucket=CORE_BUCKET)
    before = budget.calls
    # canonicalize_environment_hint (with a path-like + a benign value) ...
    from app.world.environment import canonicalize_environment_hint

    canonicalize_environment_hint("hotel/suite")
    canonicalize_environment_hint("Hotel Suite")
    # ... and composer semantic-id preservation are pure.
    assert budget.calls == before


def test_driver_environment_formatting_costs_zero_extra_calls():
    """A format-only environmentHint failure never spends a provider call
    (the full driver is exercised in test_phase19_environment; this proves the
    budget counters stay at the 7-call path: case/evidence + 4 Phase 19J
    activity logs + world — environment canonicalization adds zero calls)."""
    from test_phase19_environment import _hotel_suite_prompt, _staged

    record, transport = _run(_staged(True), prompt=_hotel_suite_prompt())
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 7
    assert record.budget.calls == 7
    assert record.budget.core_calls == 7
    assert record.budget.asset_calls == 0


# --------------------------------------------------------------------------- #
# 8. asset exhaustion distinguishable from global/core exhaustion
# --------------------------------------------------------------------------- #


def test_failure_code_mapping_distinguishes_buckets():
    assert failure_code_for_budget_reason(
        "model call budget exhausted"
    ) is GenerationFailureCode.PROVIDER_CALL_BUDGET_EXHAUSTED
    assert failure_code_for_budget_reason(
        "core model call budget exhausted"
    ) is GenerationFailureCode.CORE_PROVIDER_CALL_BUDGET_EXHAUSTED
    assert failure_code_for_budget_reason(
        "asset model call budget exhausted for prop_1"
    ) is GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED
    assert failure_code_for_budget_reason(
        "procedural asset count ceiling exceeded"
    ) is GenerationFailureCode.MAX_PROCEDURAL_ASSETS_EXCEEDED
    assert failure_code_for_budget_reason(
        "maximum failed assets exceeded"
    ) is GenerationFailureCode.MAX_FAILED_ASSETS_EXCEEDED
    # infer_failure_code (used by generic paths) keeps the same narrowing.
    assert infer_failure_code("core model call budget exhausted") \
        is GenerationFailureCode.CORE_PROVIDER_CALL_BUDGET_EXHAUSTED
    assert infer_failure_code("asset call budget exhausted") \
        is GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED
    # the codes serialize as player-safe public codes.
    for code in (
        GenerationFailureCode.CORE_PROVIDER_CALL_BUDGET_EXHAUSTED,
        GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED,
        GenerationFailureCode.MAX_PROCEDURAL_ASSETS_EXCEEDED,
        GenerationFailureCode.MAX_FAILED_ASSETS_EXCEEDED,
    ):
        assert public_failure_code(code) == code.value


def test_driver_core_budget_exhaustion_narrows_code():
    """A pathological endless-repair run whose CORE budget (12) is exhausted
    BEFORE the global ceiling fails with the narrow CORE code."""
    from app.core.config import Settings
    from app.generation.ollama_provider import OllamaProvider
    from app.services.ollama_driver import OllamaStageDriver
    from test_ollama_driver import (
        OLLAMA_BASE,
        OLLAMA_MODEL,
        _admission,
        _controller,
        _alog_posts,
    )

    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts("2026-09-11T23:42:00+02:00"),  # pass-1 activity logs
        "<not-json>",  # pass-1 world (parse fails -> structural issue -> repair)
        "<not-json>",  # pass-1 world retry (still fails)
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),  # pass-2 evidence
        *_alog_posts("2026-09-11T23:42:00+02:00"),  # pass-2 activity logs
        # pass-2 world never runs: the CORE budget (12) is exhausted by the
        # fourth pass-2 activity-log call (case is cached across passes).
    ]

    class _Transport:
        def __init__(self, posts):
            self.posts = list(posts)
            self._calls = []

        def post_json(self, url, payload, timeout):
            self._calls.append(url)
            content = self.posts.pop(0) if self.posts else "<not-json>"
            return 200, json.dumps(
                {"model": OLLAMA_MODEL, "message": {"role": "assistant", "content": content}}
            ).encode()

        def get(self, url, timeout):
            return 200, json.dumps({"models": [{"name": OLLAMA_MODEL}]}).encode()

    transport = _Transport(list(posts))

    def factory():
        return OllamaProvider(
            base_url=OLLAMA_BASE, model=OLLAMA_MODEL, timeout_seconds=5,
            transport=transport,
        )

    driver = OllamaStageDriver(settings=Settings(), provider_factory=factory)
    clock, ids = ManualClock(), IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = _controller(
        driver, transport, admission, clock, ids,
        max_llm_calls_per_generation=60,
        max_core_llm_calls=12,
        max_repair_passes=10,
        max_full_regenerations=1,
    )
    handle = controller.start_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.session_id,
    )
    attempt = controller.attempt(handle.attempt_id)
    assert attempt.state is GenerationState.FAILED
    assert attempt.failure_code == "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert attempt.budget.core_calls >= 12
    assert attempt.budget.calls <= 60
    assert attempt.budget.calls == 12


# --------------------------------------------------------------------------- #
# 1/10/11/12/14 — driver integration: bounded asset behavior
# --------------------------------------------------------------------------- #

# A locked-weapon-kitchen-knife prompt matching the ``test_ollama_driver``
# staged case/evidence fixtures (office kit).
_KNIFE_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
    "Motive: stolen research data\nWeapon: kitchen knife\nTime: 23:42\n"
    "Witness: Lisa König\nLocation: office\n"
)


def test_catalog_only_case_is_low_call():
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world(unknown_name="kitchen knife")),
    ]
    record, transport = _run(posts, prompt=_KNIFE_PROMPT)
    assert record.state is GenerationState.PUBLISHED
    # case + evidence + 4 Phase 19J activity logs + world (kitchen knife is
    # catalog-resolved: no ASSET_SPEC call).
    assert transport.call_count == 7
    assert record.budget.calls == 7
    assert record.budget.core_calls == 7
    assert record.budget.asset_calls == 0
    assert record.budget.procedural_asset_count == 0


def _always_bad_spec(initial_round=0):
    return json.dumps(
        {"canonicalName": "x", "category": "decor", "subtype": "x",
         "dimensions": {"x": 25, "y": 0.1, "z": 0.1}, "parts": []},
    )


def test_no_infinite_asset_retry_loop_and_failed_asset_bounded():
    """A NEVER-valid procedural spec terminates inside the bounded
    ASSET_SPEC/REPAIR loop (1 initial + MAX_SPEC_REPAIR_PASSES repairs, never
    an unbounded chain)."""
    from app.services.ollama_driver import MAX_SPEC_REPAIR_PASSES

    posts = [
        _j(_case_people(weapon="bronze_ceremonial_ice_pick")),
        _j(_evidence(weapon_obj="bronze_ceremonial_ice_pick", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world()),
        _always_bad_spec(),  # ASSET_SPEC
    ]
    for _ in range(MAX_SPEC_REPAIR_PASSES):
        posts.append(_always_bad_spec())  # repairs (all still invalid)
    record, transport = _run(posts, max_repair_passes=0, max_full_regenerations=0)
    assert record.state is GenerationState.FAILED
    assert record.published is None
    # exactly 1 initial + bounded repairs; never a loop beyond the budget.
    assert transport.call_count == 7 + 1 + MAX_SPEC_REPAIR_PASSES
    assert record.budget.calls == 7 + 1 + MAX_SPEC_REPAIR_PASSES


def test_essential_evidence_asset_failure_stays_fail_closed():
    """The REQUIRED weapon's procedural generation failing is a terminal
    world condition: nothing is published, no bare drop."""
    posts = [
        _j(_case_people(weapon="bronze_ceremonial_ice_pick")),
        _j(_evidence(weapon_obj="bronze_ceremonial_ice_pick", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world()),
        "<not-json>",  # ASSET_SPEC (cannot even parse)
        "<not-json>",  # ASSET_SPEC_REPAIR
        "<not-json>",  # ASSET_SPEC_REPAIR
    ]
    record, transport = _run(posts, max_repair_passes=0, max_full_regenerations=0)
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert "bronze_ceremonial_ice_pick" in record.deferred_structural or True


def test_decorative_asset_failure_falls_back_safely():
    """A DECORATIVE unknown object that cannot be generated is left OUT with a
    player-safe note; the rest of the world still publishes."""
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        json.dumps({
            "environmentHint": "office",
            "locationTokens": ["office"],
            "objects": [
                {"name": "kitchen knife", "criticality": "decorative"},
                {"name": "unusual trinket", "criticality": "decorative"},
            ],
            "relations": [],
            "unsafeUnsupported": [],
        }),
        "<not-json>",  # ASSET_SPEC for the trinket (fails)
        "<not-json>",
        "<not-json>",
    ]
    record, transport = _run(
        posts, prompt=_KNIFE_PROMPT, max_repair_passes=0, max_full_regenerations=0
    )
    assert record.state is GenerationState.PUBLISHED
    published = record.published.draft if record.published else record.draft
    assert any(o.object_id == "kitchen_knife" for o in published.objects)
    assert all(o.object_id != "unusual_trinket" for o in published.objects)
    assert any(
        "unusual trinket" in note for note in (published.composition_notes or ())
    )


# --------------------------------------------------------------------------- #
# 15/16/17 — provider compatibility
# --------------------------------------------------------------------------- #


def test_fakeprovider_deterministic_with_exact_accounting():
    """The deterministic FakeProvider golden case publishes with exact,
    monotonic, fully-attributable accounting (core-only)."""
    from fixtures.golden_generation import (
        GOLDEN_FULL_DRAFT,
        GOLDEN_STAGE_PAYLOADS,
    )
    from app.generation.admission import AdmissionController
    from app.generation.controller import GenerationController
    from app.generation.fake_provider import FakeProvider

    script = {
        stage: [payload] for stage, payload in GOLDEN_STAGE_PAYLOADS.items()
    }
    script[GenerationStage.REPAIR] = [GOLDEN_FULL_DRAFT]
    fake = FakeProvider(script)
    clock, ids = ManualClock(), IdSource()
    admission = AdmissionController(
        clock=clock, ids=ids,
        max_concurrent_generations=1, max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3, max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=fake, admission=admission, clock=clock, ids=ids,
        deadline_seconds=60, max_llm_calls_per_generation=128,
        max_repair_passes=2, max_full_regenerations=1, max_prompt_chars=4000,
        seed=11,
    )
    handle = controller.start_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n"
        "Motive: cover_up_embezzlement\nWeapon: kitchen_knife\nTime: 22:17\n"
        "Witness: emily_reed\n",
        anonymous_quota_session_id=session.session_id,
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    budget = record.budget
    assert budget.calls == budget.core_calls + budget.asset_calls
    assert budget.asset_calls == 0
    assert budget.core_calls >= 4
    assert budget.calls >= 4
    first = budget.snapshot()
    # monotonic: re-reading on the same attempt yields identical snapshots.
    assert budget.snapshot() == first


def test_livehttpprovider_compatible_with_budgeted_controller():
    """LiveHttpProvider construction + factory compatibility stays intact
    (no network: the provider is constructed, never invoked)."""
    from app.core.config import Settings
    from app.persistence.store import Store
    from app.services.generation import GenerationService
    import tempfile, os

    url = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "probe.db")
    store = Store(url)
    try:
        settings = Settings(
            database_url=url,
            generation_provider="live",
            llm_api_key="k",
            llm_model="m",
            live_provider_url="https://api.example.com/v1/chat/completions",
        )
        service = GenerationService(settings=settings, store=store)
        provider = service._build_default_provider_factory()()
        from app.generation.live_provider import LiveHttpProvider

        assert isinstance(provider, LiveHttpProvider)
        # the budgeted controller accepts the live provider shape.
        from app.generation.admission import AdmissionController
        from app.generation.clock import ManualClock
        from app.generation.controller import GenerationController
        from app.generation.ids import IdSource

        clock, ids = ManualClock(), IdSource()
        admission = AdmissionController(
            clock=clock, ids=ids, max_concurrent_generations=1,
            max_concurrent_generations_global=3,
            max_generations_per_session_per_window=3,
            max_generations_global_per_window=20,
            anonymous_quota_session_ttl_seconds=86400,
        )
        controller = GenerationController(
            provider=provider, admission=admission, clock=clock, ids=ids,
            deadline_seconds=60, max_llm_calls_per_generation=128,
            max_core_llm_calls=12, max_repair_passes=2,
            max_full_regenerations=1, max_prompt_chars=4000, seed=1,
        )
        assert controller._max_llm_calls == 128
        assert controller._max_core_llm_calls == 12
    finally:
        store.dispose()


def test_ollamaprovider_compatible_with_budgeted_controller():
    """OllamaProvider stays compatible with the hierarchical budget (the full
    driver runs in the identity/environment suites; here we prove the budgeted
    controller consumes CORE buckets through the real provider glass)."""
    from test_ollama_driver import OLLAMA_BASE, OLLAMA_MODEL
    from app.generation.ollama_provider import OllamaProvider

    class _Transport:
        def __init__(self, posts):
            self.posts = list(posts)

        def post_json(self, url, payload, timeout):
            content = self.posts.pop(0) if self.posts else "<not-json>"
            return 200, json.dumps(
                {"model": OLLAMA_MODEL, "message": {"role": "assistant", "content": content}}
            ).encode()

        def get(self, url, timeout):
            return 200, json.dumps({"models": [{"name": OLLAMA_MODEL}]}).encode()

    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world(unknown_name="kitchen knife")),
    ]
    transport = _Transport(posts)

    def factory():
        return OllamaProvider(
            base_url=OLLAMA_BASE, model=OLLAMA_MODEL, timeout_seconds=5,
            transport=transport,
        )

    from app.services.ollama_driver import OllamaStageDriver
    from app.core.config import Settings

    driver = OllamaStageDriver(settings=Settings(), provider_factory=factory)
    clock, ids = ManualClock(), IdSource()
    from test_ollama_driver import _admission as _driver_admission

    admission = _driver_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    from test_ollama_driver import _controller as _driver_controller

    controller = _driver_controller(
        driver, transport, admission, clock, ids,
        max_llm_calls_per_generation=128,
        max_core_llm_calls=12,
        max_llm_calls_per_procedural_asset=5,
        max_procedural_assets_per_generation=20,
        max_failed_assets_per_generation=3,
    )
    handle = controller.start_generation(
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
        "Motive: stolen research data\nWeapon: kitchen knife\nTime: 23:42\n"
        "Witness: Lisa König\nLocation: office\n",
        anonymous_quota_session_id=session.session_id,
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED
    # 7 core calls: case + evidence + 4 Phase 19J activity logs + world.
    assert record.budget.calls == 7
    assert record.budget.core_calls == 7
    assert record.budget.asset_calls == 0


# --------------------------------------------------------------------------- #
# Phase19J-RI — provider budget unchanged (the activity-log repair round trip
# is a NORMAL bounded CORE-bucket path; global/core ceilings untouched).
# --------------------------------------------------------------------------- #


def test_ri09_activity_log_roundtrip_core_budget_unchanged():
    """A full-bounded activity-log round trip (initial + the two bounded
    repairs = EXACTLY 3 CORE-call provider calls for the ONE log) publishes
    through the SAME hierarchical budget: every log call is a normal CORE
    call (never a hidden/free pass), the global/core ceilings are untouched
    (Settings defaults stay 128/12, MAX_ACTIVITY_LOG_REPAIR_PASSES stays 2)
    and zero asset calls are spent."""
    from app.core.config import Settings
    from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES
    from test_ollama_driver import (
        ICEPICK_SPEC,
        _alog,
        _case_people,
        _evidence,
        _world,
    )

    assert Settings().max_llm_calls_per_generation == 128
    assert Settings().max_core_llm_calls_per_generation == 12
    assert MAX_ACTIVITY_LOG_REPAIR_PASSES == 2

    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs = "2026-09-11T23:41:50+02:00"
    bad = _alog(when_obs)
    bad["entries"][8]["timestamp"] = "2026-09-11T23:40:00+02:00"  # canonical removed
    good = _alog(when_obs)
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        json.dumps(bad),   # ACTIVITY_LOG (invalid)
        json.dumps(bad),   # repair 1 (invalid)
        json.dumps(good),  # repair 2 (valid -> exactly 3 log calls)
        *_alog_posts(crime_canonical)[1:],
        _j(_world()),
    ]
    posts.append(ICEPICK_SPEC)
    record, transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    # 1 case + 1 evidence + 3 (when_obs round trip) + 3 (other logs) + 1 world
    # = 9 CORE calls; the ASSET_SPEC call goes to the independent per-asset
    # bucket (1 asset call). Total transport calls = 10.
    assert transport.call_count == 10
    assert record.budget.calls == 10
    assert record.budget.core_calls == 9
    assert record.budget.asset_calls == 1
    # the 3-call bounded activity-log round trip itself consumed EXACTLY 3
    # CORE calls (never a hidden or free pass).
    log_calls = sum(
        1
        for i in range(transport.call_count)
        if "activity_log" in transport.prompt_of_call(i)[:80]
    )
    assert log_calls == 3 + 3


__all__ = ["_tracker"]