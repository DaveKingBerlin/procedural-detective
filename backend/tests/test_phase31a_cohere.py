"""Phase31A — Track A (Cohere evidence-schema rejection) SAFE diagnostics.

Additive, hermetic regression tests for the Phase31A-Frontier-MC diagnostics
layer (§24). Every test proves ONE of the required diagnostic properties:

  1. evidence schema fingerprint is stable & deterministic (SHA-256 over a
     canonical sorted serialization — never insertion-order dependent);
  2. evidence schema ID is EVIDENCE_v1;
  3. Cohere/OpenRouter requests use the intended native structured-output
     mode (mocked wire: ``response_format`` json_schema with the PARSER-SHAPED
     stage schema, and the Cohere-compatible TRANSPORT adaptation of the open
     ``structured`` object per the proven root cause);
  4. case_truth / public_world accepted-control fixtures parse;
  5. evidence 4xx rejection reproduces the SAFE error classification
     (public ``failureCode`` unchanged = FRONTIER_PROVIDER_ERROR, internal
     ``safeProviderErrorClass=SCHEMA_REJECTED``, ``safeUpstreamStatus=400``);
  6. raw upstream error bodies are NEVER exposed (not in logs/DTOs);
  7. provider status is recorded safely (sanitized integer only);
  8. schema byte length is recorded safely (canonical byte size, never
     contents);
  9. schema contents are NOT logged;
  10. schema-minimization fixture isolation (descriptions vs required keys
      change the fingerprint deterministically; representational-only removal
      never breaks a CONFORMANT parse);
  11. protocol adapter preserves full canonical post-validation;
  12. unsupported-schema path fails safely;
  13. no fallback to another provider/model;
  14. no browser schema override;
  15. no arbitrary endpoint;
  16. API key remains secret;
  17. concurrent requests do not cross schema/model/key state.

Every external provider is a mock (``app.generation.frontier_provider.httpx.post``)
— no real paid call is ever made and CI stays offline (the autouse network
block enforces that).
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
from app.generation import prompts, parser  # noqa: E402
from app.generation import schema_adapters as sa  # noqa: E402
from app.generation.frontier_provider import (  # noqa: E402
    SAFE_ERROR_CLASS_SCHEMA_REJECTED,
    frontier_safe_error_class_for_status,
)
from app.generation.frontier_registry import (  # noqa: E402
    STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA,
)
from app.generation.provider import GenerationStage  # noqa: E402
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
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

# The Phase31A sentinel (must appear ONLY in the mocked Authorization header).
SENTINEL_KEY = "SECRET-PHASE31A-MUST-NOT-PERSIST-77"


def _cohere_transport_schema(stage: GenerationStage) -> dict:
    """The EXACT transport representation the fixed Cohere/OpenRouter path now
    places in ``response_format`` (registry capability + Cohere model-family
    adapter selection)."""
    canonical = prompts.json_schema_for_stage_output(stage.value)
    assert canonical is not None
    adapter = sa.schema_adapter_id_for_frontier_call(
        "cohere/command-a-plus", STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA,
    )
    assert adapter == sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
    return sa.adapt_schema_for_transport(canonical, adapter=adapter)


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
    """httpx.post stand-in for the Frontier adapter (recorded wire).

    Each call pops the next ``response`` descriptor off the configured queue:
    a ``str`` answers HTTP 200 with that content; an ``int`` answers that
    status with a provider-ish raw body; a ``(status, text)`` tuple answers
    both exactly. Every call is recorded as ``(url, body, headers)`` for the
    wire asserts.
    """

    class _FakeResponse:
        def __init__(self, status: int, body: bytes) -> None:
            self.status_code = status
            self._body = body

        def iter_bytes(self, chunk_size: int):
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
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, str):
                return self._FakeResponse(200, item.encode("utf-8"))
            if isinstance(item, tuple):
                status, text = item
                return self._FakeResponse(int(status), str(text).encode("utf-8"))
            return self._FakeResponse(
                int(item), f"provider failure: HTTP {int(item)}".encode("utf-8")
            )
        return self._FakeResponse(200, b"<not-json>")


class _CaptureLog:
    """Deterministic structured-log capture (backend suite convention)."""

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
# §24.1 / §24.2 — evidence schema fingerprint + id
# --------------------------------------------------------------------------- #


def test_evidence_schema_fingerprint_is_stable_and_deterministic():
    fp1 = prompts.schema_fingerprint_for_stage_output("evidence")
    fp2 = prompts.schema_fingerprint_for_stage_output("evidence")
    fp3 = prompts.schema_fingerprint_for_generation_stage("evidence")
    assert fp1 == fp2
    assert fp1 is not None and fp3 is not None
    assert re.fullmatch(r"[0-9a-f]{64}", fp1), fp1
    assert fp1.startswith("c9c675fef576d570"), fp1

    # Insertion-order independence: a schema built with keys in a DIFFERENT
    # order must fingerprint identically.
    schema = prompts.json_schema_for_stage_output("evidence")
    assert isinstance(schema, dict)
    shuffled = {key: schema[key] for key in sorted(schema, reverse=True)}
    assert prompts.schema_fingerprint(shuffled) == fp1

    # Unmapped/unknown stages fail closed (None), never a guessed fingerprint.
    assert prompts.schema_fingerprint_for_stage_output("bogus") is None
    assert prompts.schema_fingerprint_for_generation_stage(None) is None


def test_evidence_schema_id_is_evidence_v1():
    assert prompts.schema_id_for_generation_stage("evidence") == "EVIDENCE_v1"


# --------------------------------------------------------------------------- #
# §24.3 — Cohere/OpenRouter native structured-output request shape
# --------------------------------------------------------------------------- #


def test_cohere_openrouter_request_uses_native_structured_output(database_url):
    """The OpenRouter wire carries the PARSER-SHAPED server-owned stage
    schemas for every stage on a Cohere model (structuredOutput=true before
    any failure) — the exact evidence from Phase 31 (``structuredOutput=true``
    recorded before the evidence-stage rejection)."""
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
                        "apiKey": "COHERE-OR-KEY-31",
                        "model": "cohere/command-a-plus",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert len(wire.posts) == 4, wire.posts
                for index, stage in enumerate(_REGISTRY_STAGES):
                    _url, body, _h = wire.posts[index]
                    assert _url == OPENROUTER_ENDPOINT
                    assert body["model"] == "cohere/command-a-plus"
                    # The Cohere-compatible TRANSPORT representation (Track A:
                    # open-object nodes carry >= 1 required property while the
                    # canonical schema/fingerprint stay byte-identical).
                    assert _schema_of(body) == _cohere_transport_schema(
                        stage
                    ), stage
                    assert _name_of(body) == prompts.schema_id_for_generation_stage(
                        stage.value
                    ), stage
                # Every complete event reports the intended structured-output
                # mode truthfully.
                events = _json_events(log_buffer.getvalue())
                for stage in _REGISTRY_STAGES:
                    completes = [
                        e for e in events
                        if e.get("event") == "provider.call.complete"
                        and e.get("stage") == stage.value
                    ]
                    assert completes, stage
                    for event in completes:
                        assert event["structuredOutput"] is True
                        assert event["structuredOutputMode"] == "openai_json_schema"
                        assert event["structuredOutputRequested"] is True
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §24.4 — accepted-control fixtures parse
# --------------------------------------------------------------------------- #


def test_case_truth_and_public_world_control_fixtures_parse():
    for stage in (GenerationStage.CASE_TRUTH, GenerationStage.PUBLIC_WORLD):
        parsed = parser.parse_stage(stage, _G[stage], non_throwing=False)
        assert parsed is not None, stage
        assert parser.collect_issues(stage, _G[stage]) == (), stage


# --------------------------------------------------------------------------- #
# §24.5 / §24.6 / §24.7 — evidence 4xx rejection -> safe classification
# --------------------------------------------------------------------------- #


def test_evidence_rejection_safe_error_classification(database_url):
    """The Cohere evidence-stage rejection: the provider answers HTTP 400 with
    a RAW upstream body; the pipeline FAILS with the UNCHANGED public
    ``FRONTIER_PROVIDER_ERROR`` and the internal diagnostic
    ``safeProviderErrorClass=SCHEMA_REJECTED`` + ``safeUpstreamStatus=400``.
    The raw body never reaches logs, DTOs or telemetry."""
    raw_upstream_body = (
        '{"error":{"message":"Unsupported JSON Schema keyword: union types at '
        '#/properties/evidence/items/properties/propositions","code":'
        '"invalid_request_error"}}'
    )
    wire = _Wire(
        golden=[
            _G[GenerationStage.CASE_TRUTH],
            _G[GenerationStage.PUBLIC_WORLD],
            (400, raw_upstream_body),
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
                        "apiKey": "COHERE-REJ-KEY",
                        "model": "cohere/command-a-plus",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "FAILED"
                # Public failure-code vocabulary UNCHANGED (no new public code).
                assert res.json()["failureCode"] == "FRONTIER_PROVIDER_ERROR"
                # Exactly the two accepted stages + the evidence rejection.
                assert len(wire.posts) == 3, wire.posts
                # structured output was requested on the evidence call BEFORE
                # the rejection (the Phase 31 live evidence: structuredOutput
                # true before the failure, failure ~400-550 ms sync). The
                # evidence call itself still carried the schema — now the
                # Cohere-compatible TRANSPORT representation (Track A).
                _url, body, _h = wire.posts[2]
                assert _schema_of(body) == _cohere_transport_schema(
                    GenerationStage.EVIDENCE
                )
                assert _name_of(body) == "EVIDENCE_v1"

                events = _json_events(log_buffer.getvalue())
                error_event = next(
                    e for e in events if e.get("event") == "provider.call.error"
                )
                assert error_event["stage"] == "evidence"
                assert error_event["failureCode"] == "FRONTIER_PROVIDER_ERROR"
                assert error_event["safeProviderErrorClass"] == (
                    SAFE_ERROR_CLASS_SCHEMA_REJECTED
                )
                # Provider status recorded SAFELY: sanitized integer only.
                assert error_event["safeUpstreamStatus"] == 400
                assert "error" not in error_event  # no message-with-body key
                # The schema was requested on the failed call too: the call
                # start event reports structured-output intent with the schema
                # diagnostics.
                start_event = next(
                    e for e in events
                    if e.get("event") == "provider.call.start"
                    and e.get("stage") == "evidence"
                )
                assert start_event["stage"] == "evidence"

                # §24.6 — the raw upstream error body is NEVER exposed.
                log_text = log_buffer.getvalue()
                assert "invalid_request_error" not in log_text
                assert "Unsupported JSON Schema keyword" not in log_text
                assert raw_upstream_body not in log_text
                assert "COHERE-REJ-KEY" not in log_text
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §24.8 / §24.9 — schema byte length + no schema contents in logs
# --------------------------------------------------------------------------- #


def test_schema_byte_length_recorded_safely_and_contents_not_logged(database_url):
    """``provider.call.complete`` carries the canonical schema byte length
    (computed deterministically from the trusted schema), and captured logs
    NEVER contain schema contents (property names / enum tokens / schema text)."""
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
                        "apiKey": "COHERE-SIZE-KEY",
                        "model": "cohere/command-a-plus",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                events = _json_events(log_buffer.getvalue())
                for stage in _REGISTRY_STAGES:
                    completes = [
                        e for e in events
                        if e.get("event") == "provider.call.complete"
                        and e.get("stage") == stage.value
                    ]
                    for event in completes:
                        assert event["schemaId"] == (
                            prompts.schema_id_for_generation_stage(stage.value)
                        ), stage
                        assert event["schemaFingerprint"] == (
                            prompts.schema_fingerprint_for_stage_output(stage.value)
                        ), stage
                        assert event["requestSchemaByteLength"] == (
                            prompts.schema_byte_length_for_stage_output(stage.value)
                        ), stage
                        assert event["responseFormatType"] == "openai_json_schema"
                # The EVIDENCE_v1 canonical byte length is deterministic.
                assert prompts.schema_byte_length_for_stage_output("evidence") == 1480
                # §24.9 — schema CONTENTS are never logged.
                log_text = log_buffer.getvalue()
                for marker in (
                    '"propositions"',
                    '"sourceRef"',
                    '"PERSON_OBSERVED_AT_LOCATION"',
                    '"enum"',
                    "uncertaintySeconds",
                    '"type":"object"',
                ):
                    assert marker not in log_text, marker
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §24.10 — schema-minimization fixture isolation
# --------------------------------------------------------------------------- #


def _strip_descriptions(node):
    """Minimizer step A: remove every ``description`` key (representational
    only — no parser/validator semantics)."""
    if isinstance(node, dict):
        return {
            key: _strip_descriptions(value)
            for key, value in node.items()
            if key != "description"
        }
    if isinstance(node, list):
        return [_strip_descriptions(item) for item in node]
    return node


def _drop_required(node):
    """Minimizer step E: remove every ``required`` key (semantic weakening —
    changes the CONFORMANT shape AND the parse contract)."""
    if isinstance(node, dict):
        return {
            key: _drop_required(value)
            for key, value in node.items()
            if key != "required"
        }
    if isinstance(node, list):
        return [_drop_required(item) for item in node]
    return node


def test_schema_minimization_fixtures_isolate_feature_class():
    evidence = prompts.json_schema_for_stage_output("evidence")
    assert isinstance(evidence, dict)
    base_fp = prompts.schema_fingerprint(evidence)
    base_len = len(prompts.canonical_schema_bytes(evidence))

    stripped = _strip_descriptions(evidence)
    assert stripped != evidence
    stripped_fp = prompts.schema_fingerprint(stripped)
    stripped_len = len(prompts.canonical_schema_bytes(stripped))
    # A representational-only change moves the fingerprint AND the byte size
    # deterministically (the minimizer changed the canonical input).
    assert stripped_fp != base_fp
    assert stripped_len != base_len
    assert stripped_fp == prompts.schema_fingerprint(stripped)  # deterministic

    dropped = _drop_required(evidence)
    assert dropped != evidence
    dropped_fp = prompts.schema_fingerprint(dropped)
    assert dropped_fp != base_fp
    assert dropped_fp != stripped_fp  # the two minimizers isolate DIFFERENT
    # feature classes (descriptions vs requiredness).

    # Stripping representational-only keys NEVER changes a CONFORMANT parse.
    parsed = parser.parse_stage(GenerationStage.EVIDENCE, _G[GenerationStage.EVIDENCE], non_throwing=False)
    assert parsed is not None
    assert parser.collect_issues(GenerationStage.EVIDENCE, _G[GenerationStage.EVIDENCE]) == ()


# --------------------------------------------------------------------------- #
# §24.11 — protocol adapter preserves full canonical post-validation
# --------------------------------------------------------------------------- #


def test_protocol_adapter_preserves_full_canonical_post_validation():
    """The PARSER-SHAPED stage-output schema accepted by the adapter describes
    EXACTLY the document the strict canonical parser accepts — the adapter may
    adapt representational details but never weakens acceptance."""
    for stage in _REGISTRY_STAGES:
        schema = prompts.json_schema_for_stage_output(stage.value)
        assert schema is not None, stage
        doc = json.loads(_G[stage])
        assert set(schema["properties"]) == set(doc), stage
        assert set(schema["required"]) == set(doc), stage
        parsed = parser.parse_stage(stage, _G[stage], non_throwing=False)
        assert parsed is not None, stage
        assert parser.collect_issues(stage, _G[stage]) == (), stage


# --------------------------------------------------------------------------- #
# §24.12 — unsupported-schema path fails safely (direct adapter unit)
# --------------------------------------------------------------------------- #


def test_unsupported_schema_path_fails_safely(monkeypatch):
    """A provider whose registry entry has NO native structured-output mode
    (``structured_output_mode=None``) sends NO response_format (no schema on
    the wire), reports ``last_structured_output=None`` and STILL classifies an
    upstream 5xx as the canonical FRONTIER_PROVIDER_ERROR — never a guessed
    schema sent, never a public code change."""
    from app.generation import frontier_provider as fp_mod
    from app.generation.frontier_provider import FrontierProvider
    from app.generation.provider import GenerateRequest, GenerationStage

    captured = {}

    class _Resp:
        status_code = 500

        def iter_bytes(self, chunk_size):
            yield b"provider-down"

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = dict(json or {})
        return _Resp()

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    from app.generation.clock import ManualClock
    from app.generation.constraints import LockedConstraints

    manual = ManualClock(1_000_000.0)
    provider = FrontierProvider(
        endpoint_url=OPENROUTER_ENDPOINT,
        api_key="unsupported-key",
        model="cohere/command-a-plus",
        timeout_seconds=30,
        clock=manual,
        structured_output_mode=None,
    )
    request = GenerateRequest(
        attempt_id="GA-UNSUPPORTED",
        stage=GenerationStage.EVIDENCE,
        prompt_context="context",
        locked=LockedConstraints(),
        json_schema=prompts.json_schema_for_stage_output("evidence"),
        schema_id="EVIDENCE_v1",
    )
    with pytest.raises(fp_mod.FrontierHttpError) as excinfo:
        provider.generate(request)
    assert excinfo.value.code.value == "FRONTIER_PROVIDER_ERROR"
    assert "response_format" not in captured["body"]
    assert provider.last_structured_output is None
    assert provider.last_schema_fingerprint is not None  # derived, not sent
    assert provider.last_response_format_type is None
    assert provider.last_schema_id is None
    assert excinfo.value.safe_error_class == "UPSTREAM_ERROR"
    assert excinfo.value.safe_upstream_status == 500


# --------------------------------------------------------------------------- #
# §24.13 — no fallback to another provider/model (asserted inside §24.5)
# --------------------------------------------------------------------------- #


def test_evidence_rejection_has_no_fallback(database_url):
    """On the evidence 400 the attempt FAILS immediately: every wire post
    targets the trusted OpenRouter endpoint with the SAME Cohere model and the
    SAME key — no second provider, no model swap, no silent fallback."""
    wire = _Wire(
        golden=[
            _G[GenerationStage.CASE_TRUTH],
            _G[GenerationStage.PUBLIC_WORLD],
            (400, "rejected"),
        ]
    )
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
                        "apiKey": "COHERE-NO-FB-KEY",
                        "model": "cohere/command-a-plus",
                    },
                )
                assert res.json()["status"] == "FAILED"
                assert res.json()["failureCode"] == "FRONTIER_PROVIDER_ERROR"
                assert len(wire.posts) == 3, wire.posts  # no repair retry
                for _url, body, headers in wire.posts:
                    assert _url == OPENROUTER_ENDPOINT
                    assert body["model"] == "cohere/command-a-plus"
                    assert headers.get("Authorization") == (
                        "Bearer COHERE-NO-FB-KEY"
                    )
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §24.14 / §24.15 — no browser schema override / no arbitrary endpoint
# --------------------------------------------------------------------------- #


def test_browser_cannot_inject_schema_endpoint_or_response_format(database_url, monkeypatch):
    """The ``frontier`` block accepts EXACTLY provider/apiKey/model
    (``extra="forbid"``): a browser-supplied schema / endpoint /
    response_format / key-override field is rejected with the canonical 4xx
    envelope and NO outbound call is ever made."""
    from app.generation import frontier_provider as fp_mod

    called = []

    def _never(url, json=None, headers=None, timeout=None):
        called.append(url)
        raise AssertionError("no outbound call may be made")

    monkeypatch.setattr(fp_mod.httpx, "post", _never)
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            for extra_field, extra_value in (
                ("endpoint", "https://evil.example/v1"),
                ("baseUrl", "https://evil.example"),
                ("url", "https://evil.example"),
                ("schema", {"type": "object"}),
                ("responseFormat", {"type": "json_schema"}),
                ("headers", {"Authorization": "x"}),
                ("timeout", "1"),
                ("proxy", "http://proxy"),
                ("tls", "insecure"),
            ):
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "browser-key",
                        "model": "cohere/command-a-plus",
                        extra_field: extra_value,
                    },
                )
                assert res.status_code in (400, 422), (extra_field, res.text)
            assert called == []
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# §24.16 — API key remains secret
# --------------------------------------------------------------------------- #


def test_api_key_remains_secret(database_url):
    """The BYOK key appears ONLY in the mocked outbound Authorization header —
    never in API responses, captured logs or the durable DB payload."""
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
                        "apiKey": SENTINEL_KEY,
                        "model": "cohere/command-a-plus",
                    },
                )
                assert res.status_code == 201, res.text
                assert SENTINEL_KEY not in res.text
                log_text = log_buffer.getvalue()
                assert SENTINEL_KEY not in log_text
                # Durable payload has no key.
                case_id = res.json()["caseId"]
                payload = application.state.store.get_published(case_id, 1).payload_json
                assert SENTINEL_KEY not in payload
                # The ONLY appearance is the mocked outbound Authorization.
                wire_auths = {
                    headers.get("Authorization")
                    for _u, _b, headers in wire.posts
                }
                assert wire_auths == {f"Bearer {SENTINEL_KEY}"}
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §24.17 — concurrent requests do not cross schema/model/key state
# --------------------------------------------------------------------------- #


def test_concurrent_requests_do_not_cross_schema_model_or_key_state(database_url):
    """Two genuinely-overlapping Cohere/DeepSeek attempts (different logical
    providers/keys/models) never cross schema/model/key state: every recorded
    wire post targets that attempt's OWN registry endpoint with its OWN key +
    model and carries the PARSER-SHAPED stage schema of its own attempt."""
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

    model_a = "cohere/command-a-plus"
    model_b = "deepseek/deepseek-chat"
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
            args=("a", session_a.anonymous_quota_session_id, "openai", "KEY-A-31A", model_a),
        )
        tb = threading.Thread(
            target=_run,
            args=("b", session_b.anonymous_quota_session_id, "groq", "KEY-B-31B", model_b),
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
                if h.get("Authorization") == "Bearer KEY-A-31A"
            ]
            posts_b = [
                (u, b, h) for u, b, h in wire.posts
                if h.get("Authorization") == "Bearer KEY-B-31B"
            ]
            assert len(posts_a) == 4 and len(posts_b) == 4
            assert all(u == OPENAI_ENDPOINT for u, _b, _h in posts_a)
            assert all(u == GROQ_ENDPOINT for u, _b, _h in posts_b)
            assert all(b["model"] == model_a for _u, b, _h in posts_a)
            assert all(b["model"] == model_b for _u, b, _h in posts_b)
            assert all(b["model"] != model_a for _u, b, _h in posts_b)
            # Schema state never crosses: every post carries its OWN stage's
            # PARSER-SHAPED schema (identical for both attempts since ids are
            # deterministic, but the schema-name/id must match the stage).
            for index, stage in enumerate(_REGISTRY_STAGES):
                _u, body, _h = posts_a[index]
                assert _name_of(body) == prompts.schema_id_for_generation_stage(
                    stage.value
                ), stage
                _u, body, _h = posts_b[index]
                assert _name_of(body) == prompts.schema_id_for_generation_stage(
                    stage.value
                ), stage
            # Track A provider isolation: the Cohere-family attempt carries the
            # ADAPTED evidence transport schema (open ``structured`` object
            # with >= 1 required property); the non-Cohere attempt carries the
            # CANONICAL evidence schema byte-identical. Neither attempt's
            # adapter state can cross the other's wire.
            evidence_index = _REGISTRY_STAGES.index(GenerationStage.EVIDENCE)
            _u, evidence_a, _h = posts_a[evidence_index]
            assert _schema_of(evidence_a) == _cohere_transport_schema(
                GenerationStage.EVIDENCE
            )
            assert _schema_of(evidence_a) != prompts.json_schema_for_stage_output(
                "evidence"
            )
            _u, evidence_b, _h = posts_b[evidence_index]
            assert _schema_of(evidence_b) == prompts.json_schema_for_stage_output(
                "evidence"
            )
            # No cross-talk anywhere on the wire.
            assert len(wire.posts) == 8
            wire_keys = {h.get("Authorization") for _u, _b, h in wire.posts}
            assert wire_keys == {"Bearer KEY-A-31A", "Bearer KEY-B-31B"}
            wire_urls = {u for u, _b, _h in wire.posts}
            assert wire_urls == {OPENAI_ENDPOINT, GROQ_ENDPOINT}
        finally:
            wire.release.set()
            ta.join(timeout=10)
            tb.join(timeout=10)
    finally:
        wire.restore()
        store.dispose()


# --------------------------------------------------------------------------- #
# Track A fix — server-owned TRANSPORT schema adaptation (proven root cause)
# --------------------------------------------------------------------------- #


def _walk_object_nodes(node):
    """Yield every object node of a JSON-Schema tree (dicts with a
    ``"type": "object"`` marker)."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _walk_object_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_object_nodes(item)


def test_cohere_adapted_evidence_wire_has_required_key_per_object():
    """The Cohere-compatible TRANSPORT schema on the mocked evidence wire:
    EVERY object node carries a non-empty ``required`` list (Cohere's "every
    object must have >= 1 required property" rule), the ``structured`` node
    keeps ``additionalProperties`` (open-object intent preserved), and the
    CANONICAL evidence schema + fingerprint stay BYTE-IDENTICAL."""
    canonical = prompts.json_schema_for_stage_output("evidence")
    canonical_fp = prompts.schema_fingerprint_for_stage_output("evidence")
    canonical_bytes = prompts.canonical_schema_bytes(canonical)
    adapted = _cohere_transport_schema(GenerationStage.EVIDENCE)

    object_nodes = list(_walk_object_nodes(adapted))
    assert object_nodes
    for node in object_nodes:
        assert "properties" in node or "additionalProperties" in node
        # Every object — including the open ``structured`` node — now has >= 1
        # required property.
        required = node.get("required")
        assert isinstance(required, list) and required, node
        assert required != [], node

    structured_canonical = canonical["properties"]["evidence"]["items"][
        "properties"]["propositions"]["items"]["properties"]["structured"]
    structured_adapted = adapted["properties"]["evidence"]["items"][
        "properties"]["propositions"]["items"]["properties"]["structured"]
    assert structured_canonical == {
        "type": "object", "additionalProperties": True
    }
    assert structured_adapted["type"] == "object"
    assert structured_adapted["additionalProperties"] is True
    assert structured_adapted["properties"] == {"v": {"type": "string"}}
    assert structured_adapted["required"] == ["v"]

    # Canonical application schema/fingerprint/bytes UNCHANGED (the adapter
    # operates ONLY at the transport boundary).
    assert canonical == prompts.json_schema_for_stage_output("evidence")
    assert prompts.schema_fingerprint_for_stage_output("evidence") == canonical_fp
    assert prompts.canonical_schema_bytes(canonical) == canonical_bytes
    assert prompts.schema_byte_length_for_stage_output("evidence") == 1480

    # The adapted transport is deterministic and structurally a superset of
    # the canonical object graph (every canonical node still present).
    assert sa.adapt_schema_for_transport(
        canonical, adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
    ) == adapted


def test_schema_adapter_is_pure_deterministic_and_identity_for_non_cohere():
    """Unit proof of the guard rails: the adapter NEVER mutates its input,
    is deterministic, never emits ``required: []``, never drops a node, and
    ``adapter=None`` leaves an open-object-only schema BYTE-IDENTICAL (the
    "unchanged for non-Cohere providers" contract)."""
    canonical = prompts.json_schema_for_stage_output("evidence")
    before = json.dumps(canonical, sort_keys=True)
    adapter = sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
    output = sa.adapt_schema_for_transport(canonical, adapter=adapter)
    output_again = sa.adapt_schema_for_transport(canonical, adapter=adapter)
    # Determinism + purity (input untouched).
    assert output == output_again
    assert json.dumps(canonical, sort_keys=True) == before
    assert canonical == prompts.json_schema_for_stage_output("evidence")

    # Open-object-only schema WITHOUT the adapter is left byte-identical.
    bare_open = {"type": "object", "additionalProperties": True}
    unchanged = sa.adapt_schema_for_transport(bare_open, adapter=None)
    assert unchanged == bare_open
    assert unchanged.get("required") is None

    # Unknown adapter ids fail closed (identity, never partial adaptation).
    assert sa.adapt_schema_for_transport(bare_open, adapter="bogus") == bare_open

    # Non-Cohere (and capability-less) selections never pick the adapter.
    assert sa.schema_adapter_id_for_frontier_call(
        "deepseek/deepseek-chat", STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA
    ) is None
    assert sa.schema_adapter_id_for_frontier_call(
        "cohere/command-a-plus", None
    ) is None


def test_non_cohere_provider_wire_keeps_canonical_evidence_schema(database_url):
    """A non-Cohere model (DeepSeek via OpenRouter) still receives the
    BYTE-IDENTICAL canonical evidence schema on the wire — the transport
    adapter is Cohere-family-gated and can never leak into another provider's
    request shape."""
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
                        "apiKey": "DS-NON-COHERE-KEY",
                        "model": "deepseek/deepseek-chat",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                for index, stage in enumerate(_REGISTRY_STAGES):
                    _url, body, _h = wire.posts[index]
                    assert _schema_of(body) == prompts.json_schema_for_stage_output(
                        stage.value
                    ), stage
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_schema_conformant_evidence_still_passes_strict_parser(database_url):
    """A schema-conformant response (the evidence golden, plus a synthetic
    document carrying the synthetic ``structured.v`` transport key that a
    Cohere model would emit under the adapted schema) still passes the STRICT
    parser and the full pipeline validation (PUBLISHED) — the transport
    rewrite never weakens acceptance."""
    # 1) the evidence GOLDEN still passes the strict stage parser (canonical).
    parsed = parser.parse_stage(
        GenerationStage.EVIDENCE, _G[GenerationStage.EVIDENCE], non_throwing=False
    )
    assert parsed is not None
    assert parser.collect_issues(
        GenerationStage.EVIDENCE, _G[GenerationStage.EVIDENCE]
    ) == ()

    # 2) a model response SHAPED by the adapted transport schema (the open
    #    ``structured`` node emits the synthetic ``v`` key) parses identically:
    #    the strict parser treats ``structured`` as an open object.
    evidence_doc = json.loads(_G[GenerationStage.EVIDENCE])
    proposition = evidence_doc["evidence"][0]["propositions"][0]
    proposition["structured"] = {"v": "observed with the victim"}
    doc_text = json.dumps(evidence_doc)
    parsed_with_v = parser.parse_stage(
        GenerationStage.EVIDENCE, doc_text, non_throwing=False
    )
    assert parsed_with_v is not None

    # 3) end-to-end: the adapted Cohere wire + golden payloads still PUBLISH
    #    through the FULL canonical validation suite.
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
                        "apiKey": "COHERE-ADAPTED-KEY",
                        "model": "cohere/command-a-plus",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                # the wire carried the adapted evidence schema.
                evidence_wire = wire.posts[
                    _REGISTRY_STAGES.index(GenerationStage.EVIDENCE)
                ][1]
                assert _schema_of(evidence_wire) == _cohere_transport_schema(
                    GenerationStage.EVIDENCE
                )
        finally:
            _dispose(application)
    finally:
        wire.restore()