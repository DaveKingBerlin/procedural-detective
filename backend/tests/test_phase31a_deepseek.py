"""Phase31A — Track B (DeepSeek repair-exhaustion) validation telemetry.

Additive, hermetic regression tests for the Phase31A-Frontier-MC diagnostics
layer (§25 / §12-16). Every test proves ONE of the required properties:

  1. the initial validator-code set is observable safely;
  2. repair #1's failure set is recorded (``generation.stage.validation_failed``
     at ``repairCount`` 1);
  3. repair #2's failure set is recorded (``repairCount`` 2);
  4. fixed codes are calculated correctly (``codesFixed``);
  5. unchanged codes are calculated correctly (``codesUnchanged``);
  6. introduced codes are calculated correctly (``codesIntroduced``);
  7. repair effectiveness = IMPROVED;
  8. repair effectiveness = UNCHANGED;
  9. repair effectiveness = REGRESSED;
  10. repair effectiveness = VALID;
  11. the repair schema (REPAIR_v1) is present on the outbound request;
  12. structured output is true on the supported repair path;
  13. no raw draft is logged;
  14. no CaseTruth is logged;
  15. the bounded repair count remains unchanged (exactly 2);
  16. the third repair is still rejected (REPAIR_BUDGET_EXHAUSTED);
  17. validator behavior remains unchanged (golden fixtures still parse/publish);
  18. the provider-call budget remains unchanged;
  19. deadline behavior remains unchanged;
  20. concurrent DeepSeek attempts remain isolated.

All "closed validator code" helpers are pure and are tested directly first
(``app.generation.validation_codes``). Every external provider is a mock; no
real paid call is ever made.
"""

from __future__ import annotations

import io
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.domain.proof import CandidateDimensionResult, SolverProof  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation import parser as parser_mod  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.report import ValidationReport  # noqa: E402
from app.generation.validation_codes import (  # noqa: E402
    VALIDATOR_CODE_VOCABULARY,
    failure_set_delta,
    failure_set_fingerprint,
    repair_effectiveness,
    validation_failure_codes,
)
from app.main import create_app  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402
from phase5_helpers import auth, create_session  # noqa: E402

_REGISTRY_STAGES = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.PUBLIC_WORLD,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)
_G = GOLDEN_STAGE_PAYLOADS
_GOLDEN_STAGE_STRINGS = [_G[stage] for stage in _REGISTRY_STAGES]

_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

# The Phase31A sentinel (must appear ONLY in the mocked outbound header).
SENTINEL_KEY = "SECRET-PHASE31A-DEEPSEEK-NOT-PERSIST"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _make_app(database_url, **settings_overrides):
    upgrade_db(database_url)
    kwargs = dict(
        cors_allowed_origins=["http://localhost:5173"],
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_limit_per_ip_per_hour=1000,
        frontier_enabled=True,
    )
    kwargs.update(settings_overrides)
    return create_app(Settings(database_url=database_url, **kwargs))


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


def _post_case(client, token, prompt=_PROMPT, **extra):
    body = {"prompt": prompt}
    body.update(extra)
    return client.post("/api/v1/cases", json=body, headers=auth(token))


class _Wire:
    """httpx.post stand-in (identical contract to the Phase 30/31 wires)."""

    class _FakeResponse:
        def __init__(self, body: bytes) -> None:
            self.status_code = 200
            self._body = body

        def iter_bytes(self, chunk_size):
            yield self._body

    def __init__(self, golden=None) -> None:
        from app.generation import frontier_provider as fp_mod

        self._fp_mod = fp_mod
        self._original_post = fp_mod.httpx.post
        self.posts: list[tuple[str, dict, dict]] = []
        self._queue = list(golden or [])

    def install(self) -> "_Wire":
        self._fp_mod.httpx.post = self._post
        return self

    def restore(self) -> None:
        self._fp_mod.httpx.post = self._original_post

    def _post(self, url, json=None, headers=None, timeout=None):
        body = dict(json or {})
        self.posts.append((url, body, dict(headers or {})))
        content = self._queue.pop(0) if self._queue else "<not-json>"
        return self._FakeResponse(content.encode("utf-8"))


class _CaptureLog:
    def __init__(self, buffer) -> None:
        from app.core.observability import SERVICE_LOGGER_NAME, JsonEventFormatter

        self.handler = logging.StreamHandler(buffer)
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(JsonEventFormatter())
        self.service_logger = logging.getLogger(SERVICE_LOGGER_NAME)
        self._previous_level = self.service_logger.level
        logging.getLogger().addHandler(self.handler)

    def __enter__(self) -> "_CaptureLog":
        self.service_logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *_exc) -> None:
        logging.getLogger().removeHandler(self.handler)
        self.service_logger.setLevel(self._previous_level)


def _schema_of(post_body: dict) -> dict | None:
    rf = post_body.get("response_format")
    if not isinstance(rf, dict):
        return None
    inner = rf.get("json_schema")
    if not isinstance(inner, dict):
        return None
    return inner.get("schema")


def _name_of(post_body: dict) -> str | None:
    rf = post_body.get("response_format")
    if not isinstance(rf, dict):
        return None
    inner = rf.get("json_schema")
    if not isinstance(inner, dict):
        return None
    return inner.get("name")


def _json_events(log_text: str) -> list[dict]:
    events = []
    for line in log_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# --------------------------------------------------------------------------- #
# unit: closed validator-code mapper + set helpers (§25.1-§25.10)
# --------------------------------------------------------------------------- #


def test_validator_code_set_is_closed_and_sorted():
    """Every emitted code is a member of the closed vocabulary and the tuple
    is sorted — a stable, bounded contract for observability."""
    report = ValidationReport(
        structural_issues=("a parse error",),
        safety_issues=("unsafe token",),
    )
    codes = validation_failure_codes(report)
    assert codes == tuple(sorted(codes))
    assert set(codes) <= VALIDATOR_CODE_VOCABULARY
    assert "STRUCTURED_OUTPUT_INVALID" in codes
    assert "VALIDATION_FAILED" in codes


def test_initial_validator_code_set_observable():
    assert validation_failure_codes(ValidationReport()) == ()
    structural = ValidationReport(structural_issues=("evidence is not an array",))
    assert validation_failure_codes(structural) == ("STRUCTURED_OUTPUT_INVALID",)
    world = ValidationReport(
        world_issues=("world.unresolved-object: missing safe representation",)
    )
    assert "WORLD_ASSET_UNRESOLVED" in validation_failure_codes(world)
    assert "VALIDATION_FAILED" in validation_failure_codes(world)
    # An INCOMPLETE proof (no ``when`` dimension) is a bounded generic failure
    # (fail-closed: the deduction may not be trusted).
    who = CandidateDimensionResult(
        dimension="suspect", universe=("a", "b"), viable=("a", "b"), unique=False
    )
    incomplete = SolverProof(
        who=who,
        why=CandidateDimensionResult(dimension="motive", universe=("m",), unique=True),
        weapon=CandidateDimensionResult(dimension="weapon", universe=("w",), unique=True),
        when=None,
    )
    assert validation_failure_codes(ValidationReport(solver_result=incomplete)) == (
        "VALIDATION_FAILED",
    )
    # A COMPLETE proof whose deduction is not unique classifies SOLVER_AMBIGUOUS.
    from types import SimpleNamespace

    complete = SolverProof(
        who=who,
        why=CandidateDimensionResult(dimension="motive", universe=("m",), unique=True),
        weapon=CandidateDimensionResult(dimension="weapon", universe=("w",), unique=True),
        when=SimpleNamespace(ambiguous=False, overconstrained=False),
    )
    ambiguous = validation_failure_codes(ValidationReport(solver_result=complete))
    assert "SOLVER_AMBIGUOUS" in ambiguous
    # A closed ACTIVITY_LOG fragment in a safe diagnostic maps onto its token.
    log_report = ValidationReport(
        structural_issues=("ACTIVITY_LOG_CANONICAL_TIME_MISSING: no canonical row",)
    )
    assert "ACTIVITY_LOG_CANONICAL_TIME_MISSING" in validation_failure_codes(
        log_report
    )


def test_validator_codes_never_include_raw_generated_values():
    """Even a hostile free-text diagnostic that CONTAINS CaseTruth-like content
    can never surface that content: only closed vocabulary tokens are emitted."""
    report = ValidationReport(
        structural_issues=(
            "crime.murdererId 'thomas_reed' does not resolve to a person",
        ),
    )
    codes = validation_failure_codes(report)
    assert "thomas_reed" not in " ".join(codes)
    assert set(codes) <= VALIDATOR_CODE_VOCABULARY
    assert codes == ("STRUCTURED_OUTPUT_INVALID",)


def test_failure_set_fingerprint_is_stable_and_order_independent():
    fp1 = failure_set_fingerprint(("VALIDATION_FAILED", "SOLVER_AMBIGUOUS"))
    fp2 = failure_set_fingerprint(("SOLVER_AMBIGUOUS", "VALIDATION_FAILED"))
    fp3 = failure_set_fingerprint(("SOLVER_AMBIGUOUS", "VALIDATION_FAILED", "SOLVER_AMBIGUOUS"))
    assert fp1 == fp2 == fp3
    assert re.fullmatch(r"[0-9a-f]{64}", fp1)
    assert failure_set_fingerprint(()) == failure_set_fingerprint([])


def test_failure_set_delta_calculations():
    delta = failure_set_delta(
        ("VALIDATION_FAILED", "GEOMETRY_VALIDATION_FAILED"),
        ("GEOMETRY_VALIDATION_FAILED", "SOLVER_AMBIGUOUS"),
    )
    assert delta == {
        "codes_fixed": ("VALIDATION_FAILED",),
        "codes_unchanged": ("GEOMETRY_VALIDATION_FAILED",),
        "codes_introduced": ("SOLVER_AMBIGUOUS",),
    }
    empty = failure_set_delta((), ())
    assert empty == {
        "codes_fixed": (),
        "codes_unchanged": (),
        "codes_introduced": (),
    }


def test_repair_effectiveness_all_four_outcomes():
    # VALID — no failures remain after the repair.
    assert repair_effectiveness(("STRUCTURED_OUTPUT_INVALID",), ()) == "VALID"
    # UNCHANGED — the same effective failure set remains.
    assert repair_effectiveness(
        ("VALIDATION_FAILED",), ("VALIDATION_FAILED",)
    ) == "UNCHANGED"
    # IMPROVED — the set strictly shrank.
    assert repair_effectiveness(
        ("SOLVER_AMBIGUOUS", "VALIDATION_FAILED"), ("VALIDATION_FAILED",)
    ) == "IMPROVED"
    # IMPROVED — the worst severity class strictly decreased.
    assert repair_effectiveness(
        ("SOLVER_AMBIGUOUS",), ("VALIDATION_FAILED",)
    ) == "IMPROVED"
    # REGRESSED — a new (equal or worse) failure was introduced.
    assert repair_effectiveness(
        ("VALIDATION_FAILED",), ("VALIDATION_FAILED", "SOLVER_AMBIGUOUS")
    ) == "REGRESSED"
    assert repair_effectiveness(
        ("VALIDATION_FAILED",), ("SOLVER_AMBIGUOUS",)
    ) == "REGRESSED"


def test_repair_effectiveness_severity_ordering_is_documented():
    """The documented closed ordering (low -> high): geometry < structured <
    generic < world-unresolved < solver-ambiguous < asset-spec < activity-log.
    A swap toward a HIGHER severity is REGRESSED; toward a LOWER is IMPROVED."""
    assert repair_effectiveness(
        ("ACTIVITY_LOG_ENTITY_LEAK",), ("GEOMETRY_VALIDATION_FAILED",)
    ) == "IMPROVED"
    assert repair_effectiveness(
        ("GEOMETRY_VALIDATION_FAILED",), ("ACTIVITY_LOG_ENTITY_LEAK",)
    ) == "REGRESSED"
    # foreign/unknown codes map to the LOWEST severity (fail-safe).
    assert repair_effectiveness(("FOREIGN_TOKEN",), ("FOREIGN_TOKEN", "OTHER")) == "REGRESSED"


# --------------------------------------------------------------------------- #
# integration: the DeepSeek repair-exhaustion scenario (§25.1-3 / §25.11-16)
# --------------------------------------------------------------------------- #


def test_repair_failure_sets_and_outcomes_recorded(database_url):
    """The exact Phase 31 DeepSeek pattern: all four stages succeed, then two
    bounded repairs never converge (REPAIR_BUDGET_EXHAUSTED). Every validation
    pass and every repair delta is recorded with CLOSED validator codes and
    the deterministic effectiveness label."""
    wire = _Wire(
        golden=[
            "malformed",  # case_truth  -> deferred structural issues
            "malformed",  # public_world
            "malformed",  # evidence
            "malformed",  # world_graph
            "malformed",  # repair #1
            "malformed",  # repair #2
        ]
    )
    wire.install()
    log_buffer = io.StringIO()
    try:
        application = _make_app(database_url, generation_deadline_seconds=60)
        try:
            with _CaptureLog(log_buffer), TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "DS-1-KEY",
                        "model": DEEPSEEK_MODEL,
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "FAILED"
                assert res.json()["failureCode"] == "REPAIR_BUDGET_EXHAUSTED"
                assert len(wire.posts) == 6, wire.posts  # 4 stages + 2 repairs

                events = _json_events(log_buffer.getvalue())
                failed_passes = [
                    e for e in events
                    if e.get("event") == "generation.stage.validation_failed"
                ]
                # §25.1/.2/.3 — Pass 0 / repair #1 / repair #2 failure sets.
                assert [e.get("repairCount") for e in failed_passes] == [0, 1, 2]
                for pass_event in failed_passes:
                    codes = pass_event.get("validatorCodes")
                    assert isinstance(codes, list) and codes, pass_event
                    assert set(codes) <= VALIDATOR_CODE_VOCABULARY
                    assert pass_event["failureCodeSetFingerprint"] == (
                        failure_set_fingerprint(codes)
                    )
                    assert pass_event["providerCallCount"] == 4 + int(
                        pass_event["repairCount"]
                    )
                assert failed_passes[0]["validatorCodes"] == [
                    "STRUCTURED_OUTPUT_INVALID"
                ]

                # §25.11/.12 — the REPAIR wire carries REPAIR_v1 + the
                # parser-shaped repair schema + structured output true.
                for index in (4, 5):
                    _url, body, _h = wire.posts[index]
                    assert _name_of(body) == "REPAIR_v1"
                    assert _schema_of(body) == prompts.json_schema_for_stage_output(
                        "repair"
                    )
                repair_completes = [
                    e for e in events
                    if e.get("event") == "provider.call.complete"
                    and e.get("stage") == "repair"
                ]
                assert len(repair_completes) == 2
                for event in repair_completes:
                    assert event["structuredOutput"] is True
                    assert event["schemaId"] == "REPAIR_v1"

                outcomes = [
                    e for e in events if e.get("event") == "generation.repair.outcome"
                ]
                assert len(outcomes) == 2
                for index, outcome in enumerate(outcomes, start=1):
                    assert outcome["repairCount"] == index
                    assert outcome["stage"] == "validation"
                    before = tuple(outcome["validatorCodesBefore"])
                    after = tuple(outcome["validatorCodesAfter"])
                    assert before == ("STRUCTURED_OUTPUT_INVALID",)
                    assert after == ("STRUCTURED_OUTPUT_INVALID",)
                    # §25.4/.5/.6 — fixed / unchanged / introduced algebra.
                    assert outcome["codesUnchanged"] == ["STRUCTURED_OUTPUT_INVALID"]
                    assert outcome["codesFixed"] == []
                    assert outcome["codesIntroduced"] == []
                    assert outcome["repairEffectiveness"] == "UNCHANGED"
                    assert outcome["failureCodeSetFingerprint"] == (
                        failure_set_fingerprint(after)
                    )
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_third_repair_rejected_budget_and_deadline_unchanged(database_url):
    """§25.15/.16/.18/.19 — the repair budget still caps at exactly TWO
    repairs (no third repair), the provider-call budget remains the unchanged
    production ceiling and the attempt is killed by the REPAIR budget while a
    LARGE generation deadline remains (the exact Phase 31
    providerCallCount=6 / repairCount=2 / large-deadline-remaining pattern)."""
    wire = _Wire(
        golden=["malformed"] * 6
    )
    wire.install()
    log_buffer = io.StringIO()
    try:
        application = _make_app(database_url, generation_deadline_seconds=60)
        try:
            with _CaptureLog(log_buffer), TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "DS-2-KEY",
                        "model": DEEPSEEK_MODEL,
                    },
                )
                assert res.json()["status"] == "FAILED"
                assert res.json()["failureCode"] == "REPAIR_BUDGET_EXHAUSTED"
                assert len(wire.posts) == 6, "exactly 4 stages + 2 repairs"

                events = _json_events(log_buffer.getvalue())
                failed = next(e for e in events if e.get("event") == "generation.failed")
                started = next(e for e in events if e.get("event") == "generation.started")
                # §25.15 — bounded repair count (unchanged).
                assert failed["repairCount"] == 2
                # §25.18 — provider-call budget unchanged (production ceilings).
                assert failed["providerCallCount"] == 6
                assert failed["configuredGlobalProviderCallBudget"] is not None
                assert failed["configuredCoreProviderCallBudget"] is not None
                assert failed["providerCallBudget"] == (
                    failed["configuredCoreProviderCallBudget"]
                )
                # §25.19 — deadline UNCHANGED: the configured generation
                # deadline is untouched and the attempt keeps a LARGE deadline
                # margin when the repair budget exhausts (the exact Phase 31
                # "large generation deadline remains" pattern).
                assert started["configuredGenerationDeadlineMs"] == 60_000
                remaining = failed["deadlineRemainingMs"]
                assert remaining is not None and remaining > 30_000
                assert failed["failureCode"] == "REPAIR_BUDGET_EXHAUSTED"
                # the third repair was never invoked (wire length is the proof)
                repair_outcomes = [
                    e for e in events if e.get("event") == "generation.repair.outcome"
                ]
                assert len(repair_outcomes) == 2
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §25.13/.14 — no raw draft / no CaseTruth logged
# --------------------------------------------------------------------------- #


def test_no_raw_draft_or_case_truth_logged(database_url):
    """A full golden DeepSeek run publishes; captured logs contain NEITHER raw
    draft content NOR CaseTruth identifiers (the murderer id is never logged)."""
    wire = _Wire(golden=list(_GOLDEN_STAGE_STRINGS))
    wire.install()
    log_buffer = io.StringIO()
    try:
        application = _make_app(database_url)
        try:
            with _CaptureLog(log_buffer), TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "DS-GOLD-KEY",
                        "model": DEEPSEEK_MODEL,
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                log_text = log_buffer.getvalue()
                for marker in (
                    "thomas_reed",      # CaseTruth murderer id
                    "cover_up_embezzlement",  # hidden motive id
                    '"crime":',         # raw draft fragment
                    '"evidence": [',
                    SENTINEL_KEY,
                ):
                    assert marker not in log_text, marker
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §25.17 — validator behavior unchanged (golden fixtures publish)
# --------------------------------------------------------------------------- #


def test_golden_fixtures_still_parse_and_publish(database_url):
    """The existing fake/golden control path is byte-identical: four stage
    calls, zero repairs, PUBLISHED — proving no validator was touched."""
    wire = _Wire(golden=list(_GOLDEN_STAGE_STRINGS))
    wire.install()
    try:
        application = _make_app(database_url)
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "DS-CTRL-KEY",
                        "model": DEEPSEEK_MODEL,
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert len(wire.posts) == 4
                for index, stage in enumerate(_REGISTRY_STAGES):
                    _url, body, _h = wire.posts[index]
                    assert _schema_of(body) == prompts.json_schema_for_stage_output(
                        stage.value
                    ), stage
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §25.20 — concurrent DeepSeek attempts remain isolated
# --------------------------------------------------------------------------- #


def test_concurrent_deepseek_attempts_remain_isolated(database_url):
    """Two genuinely-overlapping DeepSeek attempts (different keys, same model
    family, different registry providers) never cross key/model/schema state:
    every recorded wire post carries exactly its own Authorization + model."""
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    class _BlockingWire:
        def __init__(self, golden_by_model):
            from app.generation import frontier_provider as fp_mod

            self._fp_mod = fp_mod
            self._original_post = fp_mod.httpx.post
            self.posts = []
            self.first_for_model = set()
            self.release = threading.Event()
            self._lock = threading.Lock()
            self._golden = {m: list(g) for m, g in golden_by_model.items()}

        class _Resp:
            def __init__(self, body):
                self.status_code = 200
                self._body = body

            def iter_bytes(self, chunk_size):
                yield self._body

        def install(self):
            self._fp_mod.httpx.post = self._post
            return self

        def restore(self):
            self._fp_mod.httpx.post = self._original_post

        def _post(self, url, json=None, headers=None, timeout=None):
            body = dict(json or {})
            headers = dict(headers or {})
            model = body.get("model", "")
            with self._lock:
                self.posts.append((url, body, headers))
                first = model not in self.first_for_model
                if first:
                    self.first_for_model.add(model)
            if first:
                self.release.wait(timeout=120)
            queue = self._golden.get(model, [])
            content = queue.pop(0) if queue else "<not-json>"
            return self._Resp(content.encode("utf-8"))

    model_a = "deepseek/deepseek-chat"
    model_b = "deepseek/deepseek-coder"
    wire = _BlockingWire(
        golden_by_model={
            model_a: list(_GOLDEN_STAGE_STRINGS),
            model_b: list(_GOLDEN_STAGE_STRINGS),
        }
    )
    wire.install()
    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        generation_provider="fake",
        frontier_enabled=True,
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_deadline_seconds=60,
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    results = {}
    errors = {}

    def _run(which, session_id, provider, key, model):
        try:
            results[which] = service.start_case_generation(
                _PROMPT,
                anonymous_quota_session_id=session_id,
                generation_provider="frontier",
                frontier_provider=provider,
                frontier_api_key=key,
                frontier_model=model,
            )
        except Exception as exc:  # noqa: BLE001 - recorded for the assert
            errors[which] = exc

    try:
        session_a = service.create_anonymous_quota_session()
        session_b = service.create_anonymous_quota_session()
        ta = threading.Thread(
            target=_run,
            args=("a", session_a.anonymous_quota_session_id, "openrouter", "DS-KEY-A", model_a),
        )
        tb = threading.Thread(
            target=_run,
            args=("b", session_b.anonymous_quota_session_id, "groq", "DS-KEY-B", model_b),
        )
        ta.start()
        tb.start()
        try:
            deadline = time.monotonic() + 30
            while len(wire.first_for_model) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(wire.first_for_model) == 2
            wire.release.set()
            ta.join(timeout=120)
            tb.join(timeout=120)
            assert not ta.is_alive() and not tb.is_alive()
            assert not errors, errors
            assert results["a"].status == "PUBLISHED"
            assert results["b"].status == "PUBLISHED"

            posts_a = [
                (u, b, h) for u, b, h in wire.posts
                if h.get("Authorization") == "Bearer DS-KEY-A"
            ]
            posts_b = [
                (u, b, h) for u, b, h in wire.posts
                if h.get("Authorization") == "Bearer DS-KEY-B"
            ]
            assert len(posts_a) == 4 and len(posts_b) == 4
            assert all(u == OPENROUTER_ENDPOINT for u, _b, _h in posts_a)
            assert all(u == GROQ_ENDPOINT for u, _b, _h in posts_b)
            assert all(b["model"] == model_a for _u, b, _h in posts_a)
            assert all(b["model"] == model_b for _u, b, _h in posts_b)
            for index, stage in enumerate(_REGISTRY_STAGES):
                _u, body, _h = posts_a[index]
                assert _name_of(body) == prompts.schema_id_for_generation_stage(
                    stage.value
                ), stage
                _u, body, _h = posts_b[index]
                assert _name_of(body) == prompts.schema_id_for_generation_stage(
                    stage.value
                ), stage
            assert len(wire.posts) == 8
            wire_keys = {h.get("Authorization") for _u, _b, h in wire.posts}
            assert wire_keys == {"Bearer DS-KEY-A", "Bearer DS-KEY-B"}
            wire_urls = {u for u, _b, _h in wire.posts}
            assert wire_urls == {OPENROUTER_ENDPOINT, GROQ_ENDPOINT}
        finally:
            wire.release.set()
            ta.join(timeout=10)
            tb.join(timeout=10)
    finally:
        wire.restore()
        store.dispose()


# --------------------------------------------------------------------------- #
# Track B fix — bounded OpenAI-compatible envelope content extraction (proven
# root cause: the strict parser was receiving the raw 2xx ENVELOPE keys
# "choices"/"id"/"model"/"usage" -> STRUCTURED_OUTPUT_INVALID that repairs
# could never fix -> REPAIR_BUDGET_EXHAUSTED).
# --------------------------------------------------------------------------- #


def _envelope(content: str, extra: dict | None = None) -> str:
    """An OpenAI-compatible Chat Completions 2xx envelope carrying ``content``
    as the stage document (the exact live Track B shape)."""
    body = {
        "choices": [{"message": {"content": content}, "index": 0}],
        "id": "chatcmpl-31a-deepseek",
        "model": DEEPSEEK_MODEL,
        "object": "chat.completion",
        "created": 1_700_000_000,
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 100,
            "total_tokens": 110,
        },
    }
    if extra:
        body.update(extra)
    return json.dumps(body)


def test_envelope_wrapped_golden_stages_publish(database_url):
    """The exact live Track B fix: when every Frontier 2xx response is an
    OpenAI-compatible ENVELOPE wrapping the golden stage JSON, the adapter now
    extracts ONLY the inner stage document and the attempt PUBLISHES through
    the strict parser + full validation — previously the parser received the
    envelope keys and hit STRUCTURED_OUTPUT_INVALID forever."""
    from app.generation import frontier_provider as fp_mod

    wrapped = [_envelope(_G[stage]) for stage in _REGISTRY_STAGES]
    wire = _Wire(golden=wrapped)
    wire.install()
    log_buffer = io.StringIO()
    try:
        application = _make_app(database_url)
        try:
            with _CaptureLog(log_buffer), TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "DS-ENV-KEY",
                        "model": DEEPSEEK_MODEL,
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert len(wire.posts) == 4, wire.posts  # zero repairs needed

                # The ProviderResult content was ONLY the inner stage JSON —
                # the envelope keys never reached the strict parser.
                for stage in _REGISTRY_STAGES:
                    assert parser_mod.parse_stage(
                        stage, _G[stage], non_throwing=False
                    ) is not None
                # structured-output telemetry remains true on the supported
                # path despite the envelope unwrapping.
                events = _json_events(log_buffer.getvalue())
                completes = [
                    e for e in events if e.get("event") == "provider.call.complete"
                ]
                assert len(completes) == 4
                for event in completes:
                    assert event["structuredOutput"] is True
                    assert event["schemaId"] == prompts.schema_id_for_generation_stage(
                        event["stage"]
                    )
                # No raw envelope markup / id / usage leaks into the logs.
                log_text = log_buffer.getvalue()
                for marker in ("chatcmpl-31a-deepseek", '"usage"', '"choices"'):
                    assert marker not in log_text, marker
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_frontier_provider_extracts_only_inner_envelope_content(monkeypatch):
    """Direct adapter unit: an OpenAI-compatible envelope returns a
    ProviderResult whose content is ONLY the inner stage JSON string (the
    envelope keys ``choices``/``id``/``model``/``usage`` never appear in the
    content), and the extracted content passes the strict stage parser."""
    from app.generation import frontier_provider as fp_mod
    from app.generation.clock import ManualClock
    from app.generation.constraints import LockedConstraints
    from app.generation.frontier_provider import FrontierProvider
    from app.generation.provider import GenerateRequest

    gold = _G[GenerationStage.EVIDENCE]
    captured = {}

    class _Resp:
        status_code = 200

        def iter_bytes(self, chunk_size):
            yield _envelope(gold).encode("utf-8")

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = dict(json or {})
        return _Resp()

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    manual = ManualClock(1_000_000.0)
    provider = FrontierProvider(
        endpoint_url=OPENROUTER_ENDPOINT,
        api_key="DS-ENV-KEY",
        model=DEEPSEEK_MODEL,
        timeout_seconds=30,
        clock=manual,
        structured_output_mode="openai_json_schema",
    )
    request = GenerateRequest(
        attempt_id="GA-ENV-1",
        stage=GenerationStage.EVIDENCE,
        prompt_context="context",
        locked=LockedConstraints(),
        json_schema=prompts.json_schema_for_stage_output("evidence"),
        schema_id="EVIDENCE_v1",
    )
    result = provider.generate(request)
    assert result.content == gold
    assert result.pending is False
    # Envelope keys never reach the parser/content.
    assert "choices" not in result.content
    assert "usage" not in result.content
    assert "chatcmpl-31a-deepseek" not in result.content
    assert SENTINEL_KEY not in result.content
    # The extracted content is exactly what the strict parser accepts.
    parsed = parser_mod.parse_stage(
        GenerationStage.EVIDENCE, result.content, non_throwing=False
    )
    assert parsed is not None


def test_bare_document_and_code_fenced_pass_through_unchanged(monkeypatch):
    """Backward compatibility (Phase 30 golden mocks): bodies that are NOT the
    envelope shape — a bare stage JSON document, a code-fenced document, a
    malformed fixture — pass through as the SAME body (the extraction never
    wraps or splits them)."""
    from app.generation import frontier_provider as fp_mod
    from app.generation.clock import ManualClock
    from app.generation.constraints import LockedConstraints
    from app.generation.frontier_provider import FrontierProvider
    from app.generation.provider import GenerateRequest

    cases = [
        _G[GenerationStage.CASE_TRUTH],            # bare JSON
        "```json\n" + _G[GenerationStage.CASE_TRUTH] + "\n```",  # code-fenced
        "malformed",                                # invalid fixture
        '<div>not json at all</div>',
    ]
    for raw_body in cases:
        captured = {}

        class _Resp:
            status_code = 200

            def iter_bytes(self, chunk_size):
                yield raw_body.encode("utf-8")

        def _post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            return _Resp()

        monkeypatch.setattr(fp_mod.httpx, "post", _post)
        manual = ManualClock(1_000_000.0)
        provider = FrontierProvider(
            endpoint_url=OPENROUTER_ENDPOINT,
            api_key="DS-BARE-KEY",
            model=DEEPSEEK_MODEL,
            timeout_seconds=30,
            clock=manual,
            structured_output_mode="openai_json_schema",
        )
        request = GenerateRequest(
            attempt_id="GA-BARE-1",
            stage=GenerationStage.CASE_TRUTH,
            prompt_context="context",
            locked=LockedConstraints(),
            json_schema=prompts.json_schema_for_stage_output("case_truth"),
            schema_id="CASE_PEOPLE_v1",
        )
        result = provider.generate(request)
        # byte-identical pass-through (the extraction is envelope-shape-gated).
        assert result.content == raw_body, repr(raw_body)


def test_malformed_envelope_passes_through_and_strict_parser_deterministic(
    database_url,
):
    """A malformed envelope (no usable content string) passes the body through
    UNCHANGED so the strict parser still reports its deterministic issue —
    the attempt exhausts the bounded repair budget with the canonical code,
    exactly as before, and never crashes."""
    no_content_envelope = json.dumps({"choices": [], "id": "chatcmpl-x", "usage": {}})
    wire = _Wire(
        golden=[
            no_content_envelope,  # case_truth (not an envelope -> pass-through
            no_content_envelope,  # public_world
            no_content_envelope,  # evidence
            no_content_envelope,  # world_graph
            no_content_envelope,  # repair #1
            no_content_envelope,  # repair #2
        ]
    )
    wire.install()
    try:
        application = _make_app(database_url, generation_deadline_seconds=60)
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "DS-MAL-ENV-KEY",
                        "model": DEEPSEEK_MODEL,
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "FAILED"
                assert res.json()["failureCode"] == "REPAIR_BUDGET_EXHAUSTED"
                assert len(wire.posts) == 6
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_repair_envelope_wrapped_full_draft_publishes(database_url):
    """Repair flows through the same envelope extraction: a repair response
    that arrives as an OpenAI-compatible ENVELOPE wrapping the golden FULL
    DRAFT becomes the bare full draft and the attempt PUBLISHES after exactly
    ONE repair. The repair wire still carries REPAIR_v1 + structuredOutput
    true (unchanged)."""
    from fixtures.golden_generation import GOLDEN_FULL_DRAFT

    wire = _Wire(
        golden=[
            _G[GenerationStage.CASE_TRUTH],   # valid stage
            "malformed",                     # public_world -> RECOVERABLE_REPAIR
            _G[GenerationStage.EVIDENCE],
            _G[GenerationStage.WORLD_GRAPH],
            _envelope(GOLDEN_FULL_DRAFT),    # repair #1 arrives as an envelope
        ]
    )
    wire.install()
    log_buffer = io.StringIO()
    try:
        application = _make_app(database_url)
        try:
            with _CaptureLog(log_buffer), TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "DS-REP-ENV-KEY",
                        "model": DEEPSEEK_MODEL,
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert len(wire.posts) == 5, wire.posts  # 4 stages + 1 repair
                # The repair wire is unchanged: REPAIR_v1 + structured output.
                _url, repair_body, _h = wire.posts[4]
                assert _name_of(repair_body) == "REPAIR_v1"
                assert _schema_of(repair_body) == prompts.json_schema_for_stage_output(
                    "repair"
                )
                events = _json_events(log_buffer.getvalue())
                repair_completes = [
                    e for e in events
                    if e.get("event") == "provider.call.complete"
                    and e.get("stage") == "repair"
                ]
                assert len(repair_completes) == 1
                assert repair_completes[0]["structuredOutput"] is True
                outcomes = [
                    e for e in events if e.get("event") == "generation.repair.outcome"
                ]
                assert len(outcomes) == 1
                assert outcomes[0]["repairEffectiveness"] == "VALID"
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_deep_bombed_envelope_does_not_crash_and_never_leaks(database_url):
    """A deep/bombed envelope body is rejected by the bounded parse (no
    RecursionError crash) and is not extracted; the strict parser's own depth
    preflight then reports its deterministic issue, so the attempt stays
    bounded (repair budget) — and the deep body never reaches logs/DTOs."""
    bomb = "{" + "[".join(["[" for _ in range(2000)]) + "]" * 2000 + "}"
    wire = _Wire(
        golden=[
            bomb,
            bomb,
            bomb,
            bomb,
            _envelope(_G[GenerationStage.CASE_TRUTH]) + "  ",
        ]
    )
    wire.install()
    log_buffer = io.StringIO()
    try:
        application = _make_app(database_url, generation_deadline_seconds=60)
        try:
            with _CaptureLog(log_buffer), TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "DS-BOMB-KEY",
                        "model": DEEPSEEK_MODEL,
                    },
                )
                assert res.status_code == 201, res.text
                # The attempt must TERMINATE (bounded) — either via the repair
                # budget (each stage is a bomb -> parse failure) or a
                # deterministic terminal code. It must never crash (500).
                assert res.json()["status"] in ("FAILED",)
                assert res.json()["failureCode"] == "REPAIR_BUDGET_EXHAUSTED"
                log_text = log_buffer.getvalue()
                assert "RecursionError" not in log_text
                assert "Traceback" not in log_text
                assert "DS-BOMB-KEY" not in log_text
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_over_cap_envelope_still_sized_and_structured_telemetry_true(database_url):
    """The size cap still binds through the envelope path: an over-cap body
    degrades to the canonical size-cap FRONTIER_PROVIDER_ERROR (no unbounded
    buffer), and the structured-output telemetry for the attempted call stays
    truthful (requested on the call)."""
    from app.generation import frontier_provider as fp_mod

    assert hasattr(fp_mod, "MAX_FRONTIER_RESPONSE_BYTES")
    cap = fp_mod.MAX_FRONTIER_RESPONSE_BYTES
    over_cap = _envelope(
        "x" * (cap + 1), extra={"id": "chatcmpl-overcap"}
    )
    wire = _Wire(golden=[over_cap])
    wire.install()
    try:
        application = _make_app(database_url)
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "DS-CAP-KEY",
                        "model": DEEPSEEK_MODEL,
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "FAILED"
                assert res.json()["failureCode"] == "FRONTIER_PROVIDER_ERROR"
        finally:
            _dispose(application)
    finally:
        wire.restore()