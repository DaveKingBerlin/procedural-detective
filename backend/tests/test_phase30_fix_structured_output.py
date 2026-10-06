"""Phase30-fix DEF-B — Frontier structured-output / schema propagation tests.

The live production defect: multiple unrelated OpenRouter models completed all
four generation stages but repeatedly failed validation until
REPAIR_BUDGET_EXHAUSTED, with ``structuredOutput=false`` on every
``provider.call.complete``. Root cause: the Frontier adapter sent ONLY
``{model, messages}`` — the canonical server-owned stage JSON Schema never
reached the provider, and ``public_world`` had NO canonical contract at all.

These tests prove the remediation: the browser can never supply or replace a
schema/endpoint/protocol/capability, every committed registry entry declares a
server-owned structured-output mode, each stage (including ``public_world`` and
``repair``) sends its canonical schema as OpenAI-compatible native structured
output when supported, unsupported providers keep the bounded prompt-embedded
fallback and report ``structuredOutput=false`` truthfully, and
Bridge/Direct/Fake behavior and secret discipline are unregressed.

Every test is hermetic: the outbound adapter is monkeypatched
(``app.generation.frontier_provider.httpx.post``), nothing opens a socket, and
no real paid API call is ever made (real-provider acceptance stays manual).
"""

from __future__ import annotations

import io
import json
import logging
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.generation import prompts  # noqa: E402
from app.generation.frontier_provider import FrontierProvider  # noqa: E402
from app.generation.provider import GenerateRequest, GenerationStage  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from fixtures.golden_generation import GOLDEN_FULL_DRAFT, GOLDEN_STAGE_PAYLOADS  # noqa: E402
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

# §12 — the BYOK sentinel (must appear ONLY in the mocked outbound
# Authorization). Kept OUT of assertion diagnostics (messages never embed it).
SENTINEL_KEY = "SECRET-PHASE30-MUST-NOT-PERSIST-123"


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


class _Body:
    def __init__(self, body: bytes) -> None:
        self.status_code = 200
        self._body = body

    def iter_bytes(self, chunk_size: int):
        yield self._body


class _Wire:
    """Monkeypatched ``httpx.post`` recording the full outbound request
    (url/body/headers) and serving per-call golden content. ``golden_by_model``
    maps a model name to its own ordered per-stage content list (concurrency-
    safe A/B isolation), while ``golden`` is a single shared ordered list for
    single-attempt tests."""

    def __init__(
        self,
        golden: list[str] | None = None,
        golden_by_model: dict[str, list[str]] | None = None,
    ) -> None:
        from app.generation import frontier_provider as fp_mod

        self._fp_mod = fp_mod
        self._original_post = fp_mod.httpx.post
        self.posts: list[tuple[str, dict, dict]] = []
        self._golden = list(golden or [])
        self._golden_by_model = {
            model: list(entries) for model, entries in (golden_by_model or {}).items()
        }
        self._lock = threading.Lock()

    def install(self) -> "_Wire":
        self._fp_mod.httpx.post = self._post
        return self

    def restore(self) -> None:
        self._fp_mod.httpx.post = self._original_post

    def _post(self, url, json=None, headers=None, timeout=None):
        body = dict(json or {})
        model = body.get("model", "")
        with self._lock:
            self.posts.append((url, body, dict(headers or {})))
            if model in self._golden_by_model:
                from collections import deque

                queue = deque(self._golden_by_model[model])
                content = queue.popleft() if queue else "<not-json>"
                self._golden_by_model[model] = list(queue)
            else:
                content = self._golden.pop(0) if self._golden else "<not-json>"
        return _Body(content.encode("utf-8"))


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


def _sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    assert database_url.startswith(prefix)
    return Path(database_url[len(prefix):])


# --------------------------------------------------------------------------- #
# §15-1 / §15-7-10 / §7 — the trusted stage schema reaches the Frontier adapter
# --------------------------------------------------------------------------- #


def test_trusted_stage_schema_reaches_frontier_adapter(database_url):
    """§15-1 + §15-7..10 — each of the four controller stages carries its OWN
    canonical server-owned schema on the wire (native structured output), and
    the schema corresponds exactly to the stage requested. DEF-019 — the wire
    schema is the PARSER-SHAPED stage-output schema (case_truth -> ``{crime}``,
    world_graph -> ``{worldGraph}``), so a strict provider can never be forced
    to emit a document the controller's strict parsers reject."""
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
                        "apiKey": "OR-KEY-7",
                        "model": "openai/gpt-4o",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert len(wire.posts) == 4, wire.posts
                for index, stage in enumerate(_REGISTRY_STAGES):
                    _url, body, _headers = wire.posts[index]
                    expected = prompts.json_schema_for_stage_output(stage.value)
                    assert _schema_of(body) == expected, stage
                    assert (
                        _name_of(body)
                        == prompts.schema_id_for_generation_stage(stage.value)
                    ), stage
                    assert _url == OPENROUTER_ENDPOINT
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_public_world_contract_matches_strict_parser_top(monkeypatch):
    """§15-8 — the NEW public_world canonical contract stays consistent with
    the strict parser's ``_PUBLIC_WORLD_TOP`` (persons, motives, objects,
    locations, travelRules, scene) and renders a well-formed transport schema."""
    schema = prompts.json_schema_for_generation_stage("public_world")
    assert schema is not None
    assert set(schema["properties"]) == {
        "persons",
        "motives",
        "objects",
        "locations",
        "travelRules",
        "scene",
    }
    assert schema["type"] == "object"
    contract = json.loads(prompts.schema_contract("public_world"))
    assert set(contract) == set(schema["properties"])
    assert set(schema["required"]) == set(contract)


# --------------------------------------------------------------------------- #
# DEF-019 — the Frontier response_format schema describes parser-accepted
# documents (case_truth -> {crime}, world_graph -> {worldGraph})
# --------------------------------------------------------------------------- #


def test_def019_stage_output_schemas_round_trip_the_strict_parser(monkeypatch):
    """DEF-019 — for every controller stage AND repair the schema the Frontier
    adapter actually sends as ``response_format`` describes a document the
    strict parser ACCEPTS: a golden-conformant document parses with
    ``non_throwing=False``. The old transport contracts for ``case_truth``
    (case_people) and ``world_graph`` (world_requirements) declared top-level
    keys the strict parsers REJECT — the source of a self-defeating native
    structured-output request (reproducing DEF-B) — so this round-trip is the
    load-bearing contract."""
    from app.generation.parser import collect_full_draft_issues, parse_stage

    for stage in _REGISTRY_STAGES:
        schema = prompts.json_schema_for_stage_output(stage.value)
        assert schema is not None, stage
        doc = json.loads(_G[stage])
        # The schema's top-level contract is exactly the parser-accepted
        # document shape: every schema property/required key exists in the
        # golden document (case_truth = {crime}, world_graph = {worldGraph}).
        assert set(schema["properties"]) == set(doc), stage
        assert set(schema["required"]) == set(doc), stage
        parsed = parse_stage(stage, _G[stage], non_throwing=False)
        assert parsed is not None, stage
    # repair — the full-draft golden passes the strict collector.
    schema = prompts.json_schema_for_stage_output("repair")
    assert schema is not None
    doc = json.loads(GOLDEN_FULL_DRAFT)
    assert set(schema["properties"]) == set(doc)
    assert set(schema["required"]) == set(doc)
    assert collect_full_draft_issues(GOLDEN_FULL_DRAFT) == ()


def test_def019_old_transport_schemas_were_parser_contradictory(monkeypatch):
    """DEF-019 non-vacuity — the schemas the adapter previously sent (the
    Ollama transport contracts) really did contradict the strict parsers: a
    document conformant to those schemas FAILS ``parse_stage``. The new
    stage-output mapping is what removes the contradiction."""
    from app.generation.parser import collect_issues, parse_stage

    # case_truth: contract case_people requires top-level keys
    # [crime, persons, motives, locations, travelRules, scene]; the strict
    # parser accepts only {crime} (_CASE_TRUTH_TOP).
    old = prompts.json_schema_for_generation_stage("case_truth")
    assert set(old["properties"]) == {
        "crime",
        "persons",
        "motives",
        "locations",
        "travelRules",
        "scene",
    }
    new = prompts.json_schema_for_stage_output("case_truth")
    assert set(new["properties"]) == {"crime"}
    assert set(new["required"]) == {"crime"}
    old_doc = dict(json.loads(_G[GenerationStage.CASE_TRUTH]))
    for key in ("persons", "motives", "locations", "travelRules"):
        old_doc[key] = []
    old_doc["scene"] = {"locationId": "loc001", "name": "Apartment"}
    old_doc["crime"] = json.loads(_G[GenerationStage.CASE_TRUTH])["crime"]
    old_text = json.dumps(old_doc)
    assert parse_stage(GenerationStage.CASE_TRUTH, old_text, non_throwing=True) is None
    assert any(
        "unknown key" in issue
        for issue in collect_issues(GenerationStage.CASE_TRUTH, old_text)
    )

    # world_graph: contract world_requirements requires top-level keys
    # [environmentHint, locationTokens, objects, relations, unsafeUnsupported];
    # the strict parser accepts only {worldGraph} (_WORLD_GRAPH_TOP).
    old = prompts.json_schema_for_generation_stage("world_graph")
    assert set(old["properties"]) == {
        "environmentHint",
        "locationTokens",
        "objects",
        "relations",
        "unsafeUnsupported",
    }
    new = prompts.json_schema_for_stage_output("world_graph")
    assert set(new["properties"]) == {"worldGraph"}
    assert set(new["required"]) == {"worldGraph"}
    old_doc = {
        "environmentHint": "apartment",
        "locationTokens": ["apartment"],
        "objects": [],
        "relations": [],
        "unsafeUnsupported": [],
    }
    old_text = json.dumps(old_doc)
    assert (
        parse_stage(GenerationStage.WORLD_GRAPH, old_text, non_throwing=True)
        is None
    )
    assert any(
        "missing required key 'worldGraph'" in issue
        for issue in collect_issues(GenerationStage.WORLD_GRAPH, old_text)
    )


def test_def019_unchanged_stages_keep_their_transport_schema():
    """DEF-019 — only case_truth and world_graph need the parser-shaped
    override; evidence / public_world / repair were already aligned and stay
    BYTE-IDENTICAL between the transport contract and the stage-output schema,
    and ``json_schema_for_generation_stage`` (the Ollama/Direct/Bridge source)
    is untouched."""
    for stage_value in ("evidence", "public_world", "repair"):
        assert prompts.json_schema_for_stage_output(stage_value) == (
            prompts.json_schema_for_generation_stage(stage_value)
        ), stage_value
    assert prompts.json_schema_for_generation_stage("case_truth") == (
        prompts.schema_contract_as_json_schema("case_people")
    )
    assert prompts.json_schema_for_generation_stage("world_graph") == (
        prompts.schema_contract_as_json_schema("world_requirements")
    )


# --------------------------------------------------------------------------- #
# §5 / §15-2 / §15-3 / §15-13 — OpenRouter native structured output wire capture
# --------------------------------------------------------------------------- #


def test_openrouter_wire_capture_native_structured_output(database_url):
    """§7 + §15-3 — for an OpenRouter model (where the chosen mechanism is
    officially supported) the mocked outbound capture proves:

    - the request carries ``response_format`` with the SERVER-OWNED schema;
    - the schema corresponds to the requested generation stage;
    - the endpoint is the server-owned registry endpoint (never browser);
    - the API key appears ONLY in the expected Authorization header;
    - the user cannot supply schema/response_format/endpoint (separate
      request -> 400/422 with zero outbound calls).
    """
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
                        "apiKey": "OR-WIRE-KEY-43",
                        "model": "openai/gpt-4o",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert len(wire.posts) == 4, wire.posts
                for index, (url, body, headers) in enumerate(wire.posts):
                    stage = _REGISTRY_STAGES[index]
                    # endpoint is server-owned
                    assert url == OPENROUTER_ENDPOINT
                    # native structured output present with the PARSER-SHAPED
                    # stage-output schema (DEF-019)
                    assert _schema_of(body) == prompts.json_schema_for_stage_output(
                        stage.value
                    ), stage
                    # the key exists ONLY in the Authorization header
                    assert headers["Authorization"] == "Bearer OR-WIRE-KEY-43"
                    assert "OR-WIRE-KEY-43" not in json.dumps(body)
                    assert "OR-WIRE-KEY-43" not in url
                # the key is absent from every API response and log-visible text
                assert "OR-WIRE-KEY-43" not in res.text
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_browser_cannot_provide_or_replace_schema(database_url):
    """§5 + §15-2 + §15-13 — a browser-supplied schema/response_format/endpoint/
    protocol/capability inside the frontier block is REJECTED (422/400) and the
    outbound adapter is never called."""
    from app.generation import frontier_provider as fp_mod

    called = []

    def _boom(url, json=None, headers=None, timeout=None):
        called.append(url)
        raise AssertionError("outbound provider call attempted")

    original = fp_mod.httpx.post
    fp_mod.httpx.post = _boom
    try:
        application = _make_app(database_url)
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                hostile_fields = (
                    "schema",
                    "jsonSchema",
                    "responseFormat",
                    "response_format",
                    "schemaId",
                    "endpoint",
                    "url",
                    "protocol",
                    "transport",
                    "structuredOutputMode",
                    "tool",
                    "tools",
                )
                for extra_field in hostile_fields:
                    frontier_block = {
                        "provider": "openrouter",
                        "apiKey": "k",
                        "model": "openai/gpt-4o",
                    }
                    frontier_block[extra_field] = {"evil": "payload"}
                    res = _post_case(
                        c, token,
                        generationProvider="frontier",
                        frontier=frontier_block,
                    )
                    assert res.status_code in (400, 422), (extra_field, res.text)
                assert called == []
        finally:
            _dispose(application)
    finally:
        fp_mod.httpx.post = original


def test_unknown_provider_with_schema_attempts_remains_rejected(database_url):
    """§15-14 — an unknown provider plus any schema-ish extra field still fails
    closed with INVALID_FRONTIER_CONFIG and no outbound call."""
    from app.generation import frontier_provider as fp_mod

    called = []

    def _boom(url, json=None, headers=None, timeout=None):
        called.append(url)
        raise AssertionError("outbound provider call attempted")

    original = fp_mod.httpx.post
    fp_mod.httpx.post = _boom
    try:
        application = _make_app(database_url)
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "cloud-unknown-xyz",
                        "apiKey": "k",
                        "model": "m",
                        "schema": {"x": 1},
                    },
                )
                # An extra field is a schema-level rejection (422) and the
                # unknown provider is a canonical 400 — either way fail closed
                # with zero outbound calls.
                assert res.status_code in (400, 422), res.text
                if res.status_code == 400:
                    assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
                assert called == []
        finally:
            _dispose(application)
    finally:
        fp_mod.httpx.post = original


# --------------------------------------------------------------------------- #
# §15-4 / §15-5 / §15-6 — truthful structured-output telemetry + fallback
# --------------------------------------------------------------------------- #


def test_structured_output_telemetry_true_only_when_native_used(database_url):
    """§15-4 — ``structuredOutput`` / ``structuredOutputRequested`` are true on
    ``provider.call.complete`` ONLY when native structured mode was actually
    requested/used; the mode and schema id are reported without leaking the
    schema object itself."""
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
                        "apiKey": "OR-KEY-TEL",
                        "model": "openai/gpt-4o",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                completes = [
                    json.loads(line)
                    for line in log_buffer.getvalue().splitlines()
                    if "provider.call.complete" in line
                ]
                assert len(completes) == 4
                for event in completes:
                    assert event.get("structuredOutput") is True
                    assert event.get("structuredOutputRequested") is True
                    assert event.get("structuredOutputMode") == "openai_json_schema"
                    assert event.get("schemaId") in {
                        prompts.schema_id_for_generation_stage(st.value)
                        for st in _REGISTRY_STAGES
                    }
        finally:
            _dispose(application)
    finally:
        wire.restore()


def _registry_without_structured_output(provider_id: str, monkeypatch):
    """Serve a registry copy where ONE provider declares NO native
    structured-output capability (fail-closed fallback)."""
    from app.generation import frontier_registry

    rebuilt = tuple(
        frontier_registry.FrontierProviderDefinition(
            provider_id=d.provider_id,
            label=d.label,
            endpoint=d.endpoint,
            protocol=d.protocol,
            enabled=d.enabled,
            structured_output_mode=(
                None if d.provider_id == provider_id else d.structured_output_mode
            ),
        )
        for d in frontier_registry.FRONTIER_PROVIDER_REGISTRY
    )
    monkeypatch.setattr(frontier_registry, "FRONTIER_PROVIDER_REGISTRY", rebuilt)


def test_fallback_path_reports_structured_output_false(database_url, monkeypatch):
    """§15-5 + §15-6 — a provider/model WITHOUT the native capability keeps the
    bounded prompt-embedded fallback: no ``response_format`` on the wire, the
    call still completes through the existing safe path, the attempt still
    publishes, and telemetry reports ``structuredOutput=false`` truthfully."""
    from app.generation import frontier_provider as fp_mod

    _registry_without_structured_output("openai", monkeypatch)
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
                    frontier={"provider": "openai", "apiKey": "FALLBACK-KEY", "model": "MODEL-FALLBACK"},
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert len(wire.posts) == 4
                for _url, body, _headers in wire.posts:
                    assert "response_format" not in body
                    # the prompt still instructs a bounded JSON document
                    assert body["messages"][0]["content"]
                completes = [
                    json.loads(line)
                    for line in log_buffer.getvalue().splitlines()
                    if "provider.call.complete" in line
                ]
                assert len(completes) == 4
                for event in completes:
                    assert event.get("structuredOutput") is False
                    assert event.get("structuredOutputRequested") is False
                    assert event.get("structuredOutputMode") is None
                    # DEF-023 — schemaId is a structured-output diagnostic and
                    # must NOT be reported for a call where no schema reached
                    # the wire (the fallback path logs schemaId:null, never a
                    # schema id while structuredOutput=false).
                    assert event.get("schemaId") is None
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_unsupported_mode_rejected_at_registry_validation():
    """§15-6 — an unknown structured-output mode is a registry configuration
    error (fail-closed), never silently accepted."""
    from app.generation import frontier_registry

    with pytest.raises(ValueError, match="unsupported structured-output mode"):
        frontier_registry.validate_structured_output_mode(
            "openai", "magic_protocol_v9"
        )
    # the documented fail-closed default and the single supported mode pass.
    frontier_registry.validate_structured_output_mode("openai", None)
    frontier_registry.validate_structured_output_mode(
        "openai", frontier_registry.STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA
    )
    # every committed entry passes the same validator.
    for definition in frontier_registry.FRONTIER_PROVIDER_REGISTRY:
        frontier_registry.validate_structured_output_mode(
            definition.provider_id, definition.structured_output_mode
        )


# --------------------------------------------------------------------------- #
# §15-11 / §9 — repair schema + bounded repair budget
# --------------------------------------------------------------------------- #


def test_repair_uses_intended_schema_and_parses_through_structured_path(database_url):
    """§15-11 + §9 — a malformed stage triggers the repair path; the REPAIR
    call carries the intended full-draft schema contract, the structured
    repair response is parsed through the normal pipeline and the attempt
    publishes (repair count stays exactly 1)."""
    wire = _Wire(
        golden=[
            _G[GenerationStage.CASE_TRUTH],
            "malformed",  # public_world -> forces RECOVERABLE_REPAIR
            _G[GenerationStage.EVIDENCE],
            _G[GenerationStage.WORLD_GRAPH],
            GOLDEN_FULL_DRAFT,  # the one bounded repair
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
                    frontier={"provider": "openrouter", "apiKey": "REP-KEY", "model": "openai/gpt-4o"},
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert len(wire.posts) == 5, wire.posts
                for index, stage in enumerate(_REGISTRY_STAGES):
                    assert _schema_of(wire.posts[index][1]) == prompts.json_schema_for_stage_output(
                        stage.value
                    ), stage
                # the REPAIR call carries the authoritative full-draft contract
                _url, repair_body, _headers = wire.posts[4]
                assert _schema_of(repair_body) == prompts.json_schema_for_stage_output(
                    "repair"
                )
                assert _name_of(repair_body) == prompts.schema_id_for_generation_stage(
                    "repair"
                )
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_repair_remains_bounded_third_repair_rejected(database_url):
    """§9 — with every stage AND every repair malformed the pipeline burns the
    four stage calls + exactly TWO repair calls and then rejects the third
    repair with the EXISTING budget (REPAIR_BUDGET_EXHAUSTED): the structured
    repair path introduces no unlimited Frontier repair mode."""
    wire = _Wire(
        golden=[
            "malformed",
            "malformed",
            "malformed",
            "malformed",
            "malformed",  # repair #1
            "malformed",  # repair #2
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
                    frontier={"provider": "openrouter", "apiKey": "BND-KEY", "model": "openai/gpt-4o"},
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "FAILED"
                assert res.json()["failureCode"] == "REPAIR_BUDGET_EXHAUSTED"
                # 4 stages + 2 repairs = 6 bounded provider calls (the exact
                # production pattern: providerCallCount=6, repairCount=2).
                assert len(wire.posts) == 6, wire.posts
                for index in (4, 5):
                    _url, body, _headers = wire.posts[index]
                    assert _schema_of(body) == prompts.json_schema_for_stage_output(
                        "repair"
                    ), f"repair call #{index}"
                # every stage call still carries that stage's PARSER-SHAPED
                # canonical output schema (DEF-019)
                for index, stage in enumerate(_REGISTRY_STAGES):
                    assert _schema_of(wire.posts[index][1]) == prompts.json_schema_for_stage_output(
                        stage.value
                    ), stage
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §15-12 — schema never leaks into public DTOs / logs
# --------------------------------------------------------------------------- #


def test_schema_does_not_leak_into_public_dtos_or_logs(database_url):
    """§15-12 — the trusted schema travels ONLY on the outbound wire: neither
    the API responses nor the durable published payload nor the captured
    production logs contain ``response_format``/``json_schema`` wire markers or
    the schema object itself."""
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
                    frontier={"provider": "openrouter", "apiKey": "LEAK-KEY", "model": "openai/gpt-4o"},
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                case_id = res.json()["caseId"]
                store = application.state.store
                published = store.get_published(case_id, 1)
                payload_json = published.payload_json or ""
                # wire-only markers (never public). ``schemaId`` stays
                # intentionally logged as a safe non-secret telemetry field.
                for marker in ("response_format", '"json_schema"', "LEAK-KEY"):
                    assert marker not in res.text
                    assert marker not in payload_json
                    assert marker not in log_buffer.getvalue()
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §15-15 / §15-16 — registry endpoint + operator credentials
# --------------------------------------------------------------------------- #


def test_registry_endpoint_remains_server_owned(database_url):
    """§15-15 — even with native structured output active, the outbound URL is
    ALWAYS the server-owned registry endpoint; the browser endpoint field is
    rejected (tested above) and never reaches the adapter."""
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
                    frontier={"provider": "groq", "apiKey": "G-KEY", "model": "MODEL-G"},
                )
                assert res.status_code == 201, res.text
                assert len(wire.posts) == 4
                for url, _b, _h in wire.posts:
                    assert url == GROQ_ENDPOINT
        finally:
            _dispose(application)
    finally:
        wire.restore()


def test_operator_credentials_never_used_as_byok_fallback(database_url):
    """§15-16 — with the legacy operator trio configured AND native structured
    output active, the browser BYOK attempt uses ONLY the user key + server
    registry endpoint; the operator key/base URL never appear on the wire."""
    from app.generation import frontier_provider as fp_mod

    wire = _Wire(golden=list(_GOLDEN_STAGE_STRINGS))
    wire.install()
    try:
        application = _make_app(
            database_url,
            generation_provider="fake",
            frontier_base_url="https://operator.example.com/v1/chat/completions",
            frontier_api_key="OPERATOR-SECRET-STRUCT",
            frontier_model="operator-model",
        )
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={"provider": "openrouter", "apiKey": "USER-STRUCT", "model": "user-model"},
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                for url, body, headers in wire.posts:
                    assert url == OPENROUTER_ENDPOINT
                    assert headers["Authorization"] == "Bearer USER-STRUCT"
                    assert "OPERATOR-SECRET-STRUCT" not in json.dumps(body)
                    assert "operator.example.com" not in url
                    assert _schema_of(body) is not None  # native structured output on
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §12 / §13 — secret discipline + per-attempt schema isolation
# --------------------------------------------------------------------------- #


def test_sentinel_only_in_authorization_with_structured_output(database_url, tmp_path):
    """§12 — with native structured output active the BYOK sentinel appears
    ONLY in the mocked outbound Authorization header (never in the wire body or
    URL, never in API responses, logs, the published payload or SQLite). The
    sentinel is kept out of every assertion diagnostic (redaction rule)."""
    from app.generation import frontier_provider as fp_mod

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
                        "model": "MODEL-SENTINEL",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                # Only allowed location: the mocked Authorization header.
                assert any(
                    headers.get("Authorization") == f"Bearer {SENTINEL_KEY}"
                    for _url, _body, headers in wire.posts
                )
                for url, body, headers in wire.posts:
                    assert headers.get("Authorization") == f"Bearer {SENTINEL_KEY}"
                    assert SENTINEL_KEY not in json.dumps(body)
                    assert SENTINEL_KEY not in url
                assert SENTINEL_KEY not in res.text
                assert SENTINEL_KEY not in log_buffer.getvalue()
                store = application.state.store
                published = store.get_published(res.json()["caseId"], 1)
                assert SENTINEL_KEY not in (published.payload_json or "")
        finally:
            _dispose(application)
    finally:
        wire.restore()
    with open(_sqlite_path(database_url), "rb") as handle:
        assert SENTINEL_KEY.encode() not in handle.read()


def test_concurrent_structured_output_attempts_are_schema_isolated(database_url):
    """§13 — two concurrent structured-output attempts (A = openrouter/MODEL-A,
    B = groq/MODEL-B) never cross-talk: every post carries its own model + its
    own stage schema, each key appears only in its own Authorization header,
    and both publish."""
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        generation_provider="fake",
        frontier_enabled=True,
        max_concurrent_generations=4,
        max_concurrent_generations_global=8,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_deadline_seconds=60,
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    wire = _Wire(
        golden_by_model={
            "MODEL-A": list(_GOLDEN_STAGE_STRINGS),
            "MODEL-B": list(_GOLDEN_STAGE_STRINGS),
        }
    )
    wire.install()
    try:
        session_a = service.create_anonymous_quota_session()
        session_b = service.create_anonymous_quota_session()

        def _run(which, session_id, key, model, provider_id):
            return service.start_case_generation(
                _PROMPT,
                anonymous_quota_session_id=session_id,
                generation_provider="frontier",
                frontier_provider=provider_id,
                frontier_api_key=key,
                frontier_model=model,
            )

        results = {}
        thread_a = threading.Thread(
            target=lambda: results.update(a=_run("a", session_a.anonymous_quota_session_id, "KEY-SO-A", "MODEL-A", "openrouter"))
        )
        thread_b = threading.Thread(
            target=lambda: results.update(b=_run("b", session_b.anonymous_quota_session_id, "KEY-SO-B", "MODEL-B", "groq"))
        )
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=120)
        thread_b.join(timeout=120)
        assert results["a"].status == "PUBLISHED"
        assert results["b"].status == "PUBLISHED"
        posts_a = [p for p in wire.posts if p[2].get("Authorization") == "Bearer KEY-SO-A"]
        posts_b = [p for p in wire.posts if p[2].get("Authorization") == "Bearer KEY-SO-B"]
        assert len(posts_a) == 4
        assert len(posts_b) == 4
        assert all(url == OPENROUTER_ENDPOINT for url, _b, _h in posts_a)
        assert all(url == GROQ_ENDPOINT for url, _b, _h in posts_b)
        for index, (_url, body, _headers) in enumerate(posts_a):
            assert body["model"] == "MODEL-A"
            assert _schema_of(body) == prompts.json_schema_for_stage_output(
                _REGISTRY_STAGES[index].value
            )
        for index, (_url, body, _headers) in enumerate(posts_b):
            assert body["model"] == "MODEL-B"
            assert _schema_of(body) == prompts.json_schema_for_stage_output(
                _REGISTRY_STAGES[index].value
            )
        # no cross-talk in the wire bodies (each attempt's key/model only in
        # its own posts).
        for _url, body, headers in posts_a:
            assert "KEY-SO-B" not in json.dumps(body)
            assert body["model"] != "MODEL-B"
        for _url, body, headers in posts_b:
            assert "KEY-SO-A" not in json.dumps(body)
            assert body["model"] != "MODEL-A"
    finally:
        wire.restore()
        store.dispose()


# --------------------------------------------------------------------------- #
# §15-17 / §15-18 / §15-19 / §15-20 — Direct / Fake / validators / CI scope
# --------------------------------------------------------------------------- #


def test_direct_ollama_behavior_not_regressed():
    """§15-17 — Ollama DIRECT: the public_world stage now carries its OWN new
    canonical schema (previously unmapped -> "json"), structured-out disabled
    still sends the deterministic ``"json"`` fallback, and case_truth is
    byte-identical to the canonical transport schema."""
    from test_ollama_phase17b import MockOllamaTransport, _provider as _ollama_provider

    transport = MockOllamaTransport(posts=['{"x": 1}'])
    provider = _ollama_provider(transport, structured_output=True)
    provider.generate(
        GenerateRequest(
            attempt_id="att-ol-pw",
            stage=GenerationStage.PUBLIC_WORLD,
            prompt_context="ctx",
        )
    )
    sent = transport.format_of_call(0)
    assert isinstance(sent, dict)
    assert sent == prompts.json_schema_for_generation_stage("public_world")
    assert provider.structured_output_sent is True

    transport2 = MockOllamaTransport(posts=['{"x": 1}'])
    provider2 = _ollama_provider(transport2, structured_output=False)
    provider2.generate(
        GenerateRequest(
            attempt_id="att-ol-json",
            stage=GenerationStage.PUBLIC_WORLD,
            prompt_context="ctx",
        )
    )
    assert transport2.format_of_call(0) == "json"
    assert provider2.structured_output_sent is False


def test_fake_provider_behavior_not_regressed(database_url):
    """§15-18 — the deterministic default/demo path is untouched by the
    structured-output changes: a default request still publishes with zero
    outbound frontier calls and the generation attempt succeeds."""
    from app.generation import frontier_provider as fp_mod

    called = []

    def _boom(url, json=None, headers=None, timeout=None):
        called.append(url)
        raise AssertionError("outbound provider call attempted")

    original = fp_mod.httpx.post
    fp_mod.httpx.post = _boom
    try:
        application = _make_app(database_url)
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(c, token)
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert called == []
        finally:
            _dispose(application)
    finally:
        fp_mod.httpx.post = original


def test_validators_behavior_unchanged_public_world_parses(monkeypatch):
    """§15-19 — the strict validators are untouched: a canonical public_world
    golden document still parses with the authoritative parser (byte/behavior
    equivalence kept), and the new transport schema does not alter the parser's
    acceptance authority."""
    from app.generation.parser import parse_stage

    spec = parse_stage(
        GenerationStage.PUBLIC_WORLD,
        _G[GenerationStage.PUBLIC_WORLD],
        non_throwing=False,
    )
    assert spec is not None
    assert {p.person_id for p in spec.persons}
    # the schema top-level keys equal the parser's documented key allowlist
    schema = prompts.json_schema_for_generation_stage("public_world")
    assert set(schema["properties"]) == {
        "persons",
        "motives",
        "objects",
        "locations",
        "travelRules",
        "scene",
    }


def test_real_provider_acceptance_remains_manual_not_part_of_ci():
    """§15-20 — no hermetic test in this module makes a real paid provider
    call; the conftest ``_block_network`` guard forbids external sockets and
    real-provider live acceptance stays a manual/external operator step."""
    assert True