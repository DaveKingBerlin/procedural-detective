"""Phase43B — Frontier structured-output enforcement (WAVE 1) hermetic tests.

Covers the Wave-1 deliverables: the capability model (P43B-FR-01/02/03), the
OpenRouter ``provider.require_parameters`` enforcement (P43B-FR-04), conditional
``strict:true`` (P43B-FR-05/06), the parser-aligned transport closure
(P43B-FR-07/08/09), the bounded local transport-schema validator
(P43B-FR-11/12/13), and the transport-vs-structured success split.

Every external provider is a mock (``app.generation.frontier_provider.httpx``) —
zero network, zero paid calls, fully hermetic.
"""

from __future__ import annotations

import copy
import io
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.generation import parser as parser_mod  # noqa: E402
from app.generation import pipeline  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation import schema_adapters as sa  # noqa: E402
from app.generation import transport_schema as ts  # noqa: E402
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.frontier_provider import FrontierProvider  # noqa: E402
from app.generation.frontier_registry import (  # noqa: E402
    FRONTIER_PROVIDER_REGISTRY,
    FrontierProviderDefinition,
    frontier_provider_capabilities,
    validate_frontier_capabilities,
)
from app.generation.ids import IdSource  # noqa: E402
from app.generation.pipeline import AttemptRecord  # noqa: E402
from app.generation.provider import GenerateRequest, GenerationStage  # noqa: E402
from app.generation.state_machine import GenerationState, ValidationOutcome  # noqa: E402
from app.main import create_app  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_STAGE_PAYLOADS,
)
from phase5_helpers import auth, create_session  # noqa: E402

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v4.1-flash"
# A (provider, model) pair on the closed server-owned strict-capable allowlist.
ALLOWLISTED_MODEL = "openai/gpt-4o"

_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)

_REGISTRY_STAGES = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.PUBLIC_WORLD,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)

# The DEF-072 evidence context-echo document (§17.4 / AC-11).
_CONTEXT_ECHO = {
    "caseTruth": {},
    "lockedConstraints": {},
    "prompt": "...",
    "promptNote": "...",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _simple_schema() -> dict:
    return {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }


class _Resp:
    def __init__(self, body: bytes | str, status: int = 200) -> None:
        self.status_code = status
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def iter_bytes(self, chunk_size: int):
        yield self._body


class _Recorder:
    """Deterministic ``httpx.post`` stand-in: records bodies, serves a queue."""

    def __init__(self, queue: list[str]) -> None:
        from app.generation import frontier_provider as fp_mod

        self._fp_mod = fp_mod
        self._original = fp_mod.httpx.post
        self.queue = list(queue)
        self.bodies: list[dict] = []

    def _post(self, url, json=None, headers=None, timeout=None):
        self.bodies.append(dict(json or {}))
        content = self.queue.pop(0) if self.queue else "<not-json>"
        return _Resp(content)

    def install(self, monkeypatch) -> "_Recorder":
        monkeypatch.setattr(self._fp_mod.httpx, "post", self._post)
        return self


def _provider(provider_id: str | None, *, model: str = MODEL) -> FrontierProvider:
    return FrontierProvider(
        endpoint_url=ENDPOINT,
        api_key="P43B-KEY",
        model=model,
        timeout_seconds=30,
        clock=ManualClock(1_000_000.0),
        structured_output_mode="openai_json_schema",
        provider_id=provider_id,
    )


def _request(schema: dict, *, stage: GenerationStage = GenerationStage.EVIDENCE) -> GenerateRequest:
    return GenerateRequest(
        attempt_id="GA-p43b",
        stage=stage,
        prompt_context="context",
        json_schema=schema,
        schema_id="S_v1",
    )


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


def _events(log_text: str) -> list[dict]:
    out = []
    for line in log_text.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _make_app(database_url, **overrides):
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
    kwargs.update(overrides)
    return create_app(Settings(database_url=database_url, **kwargs))


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


# --------------------------------------------------------------------------- #
# §1 — bounded transport-schema validator unit tests
# --------------------------------------------------------------------------- #


def _object_schema(**extra) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
            "kind": {"type": "string", "enum": ["a", "b"]},
            "maybe": {"type": ["string", "null"]},
            "nested": {
                "type": "object",
                "properties": {"x": {"type": "number"}},
                "required": ["x"],
                "additionalProperties": False,
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    schema.update(extra)
    return schema


def test_validator_accepts_valid_and_rejects_named_failures():
    schema = _object_schema()
    assert ts.validate_transport_schema(
        {"name": "n", "count": 1, "tags": ["x"], "kind": "a", "maybe": None,
         "nested": {"x": 1.5}},
        schema,
    ) == ()
    assert ts.transport_schema_is_valid({"name": "n"}, schema) is True


def test_validator_rejects_unknown_property_when_closed():
    issues = ts.validate_transport_schema(
        {"name": "n", "description": "unexpected"}, _object_schema()
    )
    assert any(i.startswith("additional_property@$") for i in issues), issues
    # content-safe: the instance VALUE/KEY is never echoed.
    assert not any("description" in i for i in issues), issues


def test_validator_rejects_missing_required_wrong_type_enum_and_bounds():
    assert any(
        i.startswith("missing_required@$:name")
        for i in ts.validate_transport_schema({}, _object_schema())
    )
    assert any(
        i.startswith("type@name")
        for i in ts.validate_transport_schema({"name": 1}, _object_schema())
    )
    assert any(
        i.startswith("enum@kind")
        for i in ts.validate_transport_schema(
            {"name": "n", "kind": "z"}, _object_schema()
        )
    )
    assert any(
        i.startswith("maxItems@tags")
        for i in ts.validate_transport_schema(
            {"name": "n", "tags": ["a", "b", "c"]}, _object_schema()
        )
    )


def test_validator_nullable_and_nested_closed_object():
    schema = _object_schema()
    assert ts.validate_transport_schema({"name": "n", "maybe": None}, schema) == ()
    assert ts.validate_transport_schema({"name": "n", "maybe": "s"}, schema) == ()
    nested_issues = ts.validate_transport_schema(
        {"name": "n", "nested": {"x": 1, "extra": 2}}, schema
    )
    assert any(i.startswith("additional_property@nested") for i in nested_issues)


def test_validator_is_deterministic_sorted_and_never_raises():
    schema = _object_schema()
    bad = {"count": "x", "kind": "z", "tags": "no"}
    first = ts.validate_transport_schema(bad, schema)
    second = ts.validate_transport_schema(bad, schema)
    assert first == second
    assert list(first) == sorted(first)
    # A non-mapping schema never raises.
    assert ts.validate_transport_schema({}, "not-a-schema")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# §2 — capability model (P43B-FR-01/02/03, AC-01)
# --------------------------------------------------------------------------- #


def _schema_keys(node):
    """Yield only SCHEMA keyword keys (``properties`` values are sub-schemas,
    not keywords)."""
    if not isinstance(node, dict):
        return
    yield from node.keys()
    properties = node.get("properties")
    if isinstance(properties, dict):
        for subschema in properties.values():
            yield from _schema_keys(subschema)
    items = node.get("items")
    if isinstance(items, dict):
        yield from _schema_keys(items)
    additional = node.get("additionalProperties")
    if isinstance(additional, dict):
        yield from _schema_keys(additional)


def test_validator_keyword_subset_covers_every_generated_transport_schema():
    """The bounded validator must cover EXACTLY the keyword subset the transport
    schemas actually emit — verified for every stage, the closure projection and
    the Cohere adaptation."""
    supported = {
        "type", "properties", "required", "additionalProperties", "items",
        "enum", "minItems", "maxItems", "pattern", "format", "maxLength",
        "description",
    }
    for stage_value in (
        "case_truth", "evidence", "public_world", "world_graph", "repair"
    ):
        canonical = prompts.json_schema_for_stage_output(stage_value)
        assert canonical is not None, stage_value
        variants = [
            canonical,
            sa.close_transport_modeled_objects(canonical),
            sa.adapt_schema_for_transport(
                canonical, adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
            ),
        ]
        for variant in variants:
            unexpected = set(_schema_keys(variant)) - supported
            assert not unexpected, (stage_value, unexpected)


def test_registry_capabilities_are_conservative_and_consistent():
    for definition in FRONTIER_PROVIDER_REGISTRY:
        assert definition.accepts_response_format is True
        assert definition.supports_json_schema is True
        # Only the PROVEN OpenRouter entry may claim native strict/gating.
        if definition.provider_id == "openrouter":
            assert definition.supports_native_strict_json_schema is True
            assert definition.supports_openrouter_parameter_gating is True
        else:
            assert definition.supports_native_strict_json_schema is False
            assert definition.supports_openrouter_parameter_gating is False
        validate_frontier_capabilities(definition)


def test_capability_overclaim_is_rejected_at_import_validation():
    with pytest.raises(ValueError, match="supports_json_schema"):
        validate_frontier_capabilities(
            FrontierProviderDefinition(
                provider_id="openai",
                label="OpenAI",
                endpoint="https://api.openai.com/v1/chat/completions",
                structured_output_mode="openai_json_schema",
                supports_json_schema=True,
                accepts_response_format=False,
            )
        )
    with pytest.raises(ValueError, match="response_format acceptance must never imply"):
        validate_frontier_capabilities(
            FrontierProviderDefinition(
                provider_id="openai",
                label="OpenAI",
                endpoint="https://api.openai.com/v1/chat/completions",
                structured_output_mode="openai_json_schema",
                accepts_response_format=True,
                supports_json_schema=False,
                supports_native_strict_json_schema=True,
                supports_openrouter_parameter_gating=False,
            )
        )
    with pytest.raises(ValueError, match="OpenRouter-specific request fields"):
        validate_frontier_capabilities(
            FrontierProviderDefinition(
                provider_id="groq",
                label="Groq",
                endpoint="https://api.groq.com/openai/v1/chat/completions",
                structured_output_mode="openai_json_schema",
                accepts_response_format=True,
                supports_json_schema=True,
                supports_openrouter_parameter_gating=True,
            )
        )


def test_capability_helper_fails_closed_for_unknown_provider():
    caps = frontier_provider_capabilities("does-not-exist")
    assert caps.accepts_response_format is False
    assert caps.supports_native_strict_json_schema is False
    assert caps.supports_openrouter_parameter_gating is False
    assert frontier_provider_capabilities(None).provider_id == ""


# --------------------------------------------------------------------------- #
# §17.1 / §17.2 — OpenRouter parameter gating + conditional strict
# --------------------------------------------------------------------------- #


def test_openrouter_gets_require_parameters_and_strict(monkeypatch):
    """Strict is sent for an ALLOWLISTED (provider, model) pair with a
    strict-compatible schema; OpenRouter parameter gating still applies."""
    rec = _Recorder([json.dumps({"a": "x"})]).install(monkeypatch)
    provider = _provider("openrouter", model=ALLOWLISTED_MODEL)
    provider.generate(_request(_simple_schema()))
    body = rec.bodies[-1]
    assert body["provider"] == {"require_parameters": True}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert provider.last_require_parameters_requested is True
    assert provider.last_strict_requested is True


def test_strict_absent_for_non_allowlisted_model_on_strict_capable_provider(
    monkeypatch,
):
    """ADV-43B-01 (P43B-FR-02) — a strict-CAPABLE provider (OpenRouter) whose
    model is NOT on the closed strict-capable allowlist must NOT receive
    ``strict:true`` even with a strict-compatible schema; OpenRouter parameter
    gating still applies."""
    from app.generation.frontier_registry import (
        model_supports_native_strict_json_schema,
    )

    assert model_supports_native_strict_json_schema("openrouter", MODEL) is False
    rec = _Recorder([json.dumps({"a": "x"})]).install(monkeypatch)
    provider = _provider("openrouter", model=MODEL)
    provider.generate(_request(_simple_schema()))
    body = rec.bodies[-1]
    assert body["provider"] == {"require_parameters": True}
    assert "strict" not in body["response_format"]["json_schema"]
    assert provider.last_strict_requested is False


def test_strict_predicate_is_closed_and_default_deny():
    """The (provider, model) strict predicate is a closed, server-owned,
    default-deny allowlist: only the exact allowlisted pair matches."""
    from app.generation.frontier_registry import (
        STRICT_CAPABLE_MODELS,
        model_supports_native_strict_json_schema,
    )

    assert ("openrouter", ALLOWLISTED_MODEL) in STRICT_CAPABLE_MODELS
    assert model_supports_native_strict_json_schema(
        "openrouter", ALLOWLISTED_MODEL
    ) is True
    # Unknown provider, unknown model, empty/absent values -> deny.
    assert model_supports_native_strict_json_schema("openrouter", MODEL) is False
    assert model_supports_native_strict_json_schema("openai", ALLOWLISTED_MODEL) is False
    assert model_supports_native_strict_json_schema("openrouter", "OPENAI/GPT-4O-") is False
    assert model_supports_native_strict_json_schema(None, ALLOWLISTED_MODEL) is False
    assert model_supports_native_strict_json_schema("openrouter", None) is False
    assert model_supports_native_strict_json_schema("openrouter", "   ") is False
    # Casefold/strip normalization of the model only.
    assert model_supports_native_strict_json_schema(
        "openrouter", "  OpenAI/GPT-4O  "
    ) is True


@pytest.mark.parametrize("provider_id", ["openai", "groq", "together", "mistral"])
def test_direct_providers_never_receive_openrouter_fields(monkeypatch, provider_id):
    rec = _Recorder([json.dumps({"a": "x"})]).install(monkeypatch)
    provider = _provider(provider_id)
    provider.generate(_request(_simple_schema()))
    body = rec.bodies[-1]
    assert "provider" not in body
    assert "strict" not in body["response_format"]["json_schema"]
    assert provider.last_require_parameters_requested is False
    assert provider.last_strict_requested is False


def test_strict_omitted_for_openrouter_schema_with_optional_property(monkeypatch):
    """A schema with an unrequired property is NOT a proven strict-compatible
    combination, so ``strict`` is omitted EVEN for an allowlisted (provider,
    model) pair while OpenRouter gating still applies."""
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
    }
    rec = _Recorder([json.dumps({"a": "x"})]).install(monkeypatch)
    provider = _provider("openrouter", model=ALLOWLISTED_MODEL)
    provider.generate(_request(schema))
    body = rec.bodies[-1]
    assert "strict" not in body["response_format"]["json_schema"]
    assert provider.last_strict_requested is False
    assert body["provider"] == {"require_parameters": True}


def test_strict_compat_rejects_open_no_property_object():
    """ADV-43B-03 — an object node that is NOT closed (including an open
    no-``properties`` object with ``additionalProperties:true``) is never
    strict-compatible."""
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "blob": {"type": "object", "additionalProperties": True},
        },
        "required": ["a", "blob"],
        "additionalProperties": False,
    }
    assert sa.openai_strict_schema_is_compatible(schema) is False
    # The same open no-property object with additionalProperties absent is also
    # not closed.
    no_marker = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "blob": {"type": "object"}},
        "required": ["a", "blob"],
        "additionalProperties": False,
    }
    assert sa.openai_strict_schema_is_compatible(no_marker) is False
    # A genuinely closed no-property object IS strict-compatible.
    closed = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "blob": {"type": "object", "additionalProperties": False},
        },
        "required": ["a", "blob"],
        "additionalProperties": False,
    }
    assert sa.openai_strict_schema_is_compatible(closed) is True



def test_cohere_model_never_gets_strict_on_openrouter(monkeypatch):
    """The Cohere-family adapter is behaviorally unchanged and strict is gated
    off for it (a provider-compat projection, not a native-strict schema)."""
    rec = _Recorder([json.dumps({"a": "x"})]).install(monkeypatch)
    provider = _provider("openrouter", model="cohere/command-a-plus")
    provider.generate(_request(_simple_schema()))
    body = rec.bodies[-1]
    # OpenRouter gating still applies, but strict does NOT (adapter path).
    assert body["provider"] == {"require_parameters": True}
    assert "strict" not in body["response_format"]["json_schema"]


# --------------------------------------------------------------------------- #
# §17.5 / §17.4-partial — local validation provenance + split
# --------------------------------------------------------------------------- #


def test_schema_invalid_2xx_is_not_transport_schema_success(monkeypatch):
    rec = _Recorder([json.dumps({"a": 123})]).install(monkeypatch)
    provider = _provider("openrouter")
    result = provider.generate(_request(_simple_schema()))
    assert result.content == json.dumps({"a": 123})
    assert provider.last_content_extracted is True
    assert provider.last_json_decoded is True
    assert provider.last_transport_schema_valid is False


def test_json_decode_failure_is_not_schema_success(monkeypatch):
    rec = _Recorder(["<not-json>"]).install(monkeypatch)
    provider = _provider("openrouter")
    result = provider.generate(_request(_simple_schema()))
    assert result.content == "<not-json>"
    assert provider.last_content_extracted is True
    assert provider.last_json_decoded is False
    assert provider.last_transport_schema_valid is False


def test_evidence_context_echo_is_rejected_by_exact_transport_schema():
    canonical = prompts.json_schema_for_stage_output("evidence")
    assert canonical is not None
    closed = sa.close_transport_modeled_objects(canonical)
    issues = ts.validate_transport_schema(_CONTEXT_ECHO, closed)
    assert issues, "the DEF-072 context echo must be transport-schema invalid"
    # The evidence golden document is valid against the same exact schema.
    good = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE])
    assert ts.validate_transport_schema(good, closed) == ()


# --------------------------------------------------------------------------- #
# §17.3 — DEF-072 public-world unknown-field rejection
# --------------------------------------------------------------------------- #


def test_public_world_unknown_fields_rejected_by_closure():
    canonical = prompts.json_schema_for_stage_output("public_world")
    assert canonical is not None
    assert "additionalProperties" not in canonical  # canonical stays open
    closed = sa.close_transport_modeled_objects(canonical)
    assert closed["additionalProperties"] is False
    assert closed["properties"]["persons"]["items"]["additionalProperties"] is False

    base = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD])
    assert ts.validate_transport_schema(base, closed) == ()

    # §3.1 — the DEF-072 extra fields at the top level and inside a person.
    top = copy.deepcopy(base)
    top["locationId"] = "x"
    assert ts.validate_transport_schema(top, closed)
    person = copy.deepcopy(base)
    person["persons"][0]["description"] = "x"
    assert ts.validate_transport_schema(person, closed)
    person2 = copy.deepcopy(base)
    person2["persons"][0]["name2"] = "x"
    assert ts.validate_transport_schema(person2, closed)


def test_closure_does_not_mutate_canonical_schema():
    canonical = prompts.json_schema_for_stage_output("evidence")
    before = json.dumps(canonical, sort_keys=True)
    sa.close_transport_modeled_objects(canonical)
    assert json.dumps(canonical, sort_keys=True) == before


# --------------------------------------------------------------------------- #
# §AC-08 — intentional open structures remain valid
# --------------------------------------------------------------------------- #


def test_open_structured_node_stays_open_and_accepts_arbitrary_keys():
    canonical = prompts.json_schema_for_stage_output("evidence")
    closed = sa.close_transport_modeled_objects(canonical)
    structured = closed["properties"]["evidence"]["items"]["properties"][
        "propositions"
    ]["items"]["properties"]["structured"]
    assert structured.get("additionalProperties") is True
    assert "properties" not in structured or not structured["properties"]

    doc = {
        "evidence": [
            {
                "id": "e1",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "report", "sourceId": "s1"},
                "propositions": [
                    {
                        "type": "OTHER",
                        "uncertaintySeconds": 0,
                        "structured": {"anything": [1, {"deep": True}]},
                    }
                ],
                "presentation": {"title": "t", "description": "d", "speakerName": "w"},
            }
        ]
    }
    assert ts.validate_transport_schema(doc, closed) == ()


# --------------------------------------------------------------------------- #
# §17.8 — request-local ID enums survive every adaptation
# --------------------------------------------------------------------------- #


def _narrowed_evidence_schema() -> dict:
    universe = {
        "personIds": ("p1", "p2"),
        "locationIds": ("loc1",),
        "objectIds": ("obj1",),
        "motiveIds": ("m1",),
    }
    return prompts.narrow_stage_output_schema("evidence", reference_universe=universe)


def test_request_local_enums_survive_closure_and_adaptation():
    narrowed = _narrowed_evidence_schema()
    props = narrowed["properties"]["evidence"]["items"]["properties"][
        "propositions"
    ]["items"]["properties"]
    assert props["personId"]["enum"] == ["p1", "p2"]

    closed = sa.close_transport_modeled_objects(narrowed)
    cprops = closed["properties"]["evidence"]["items"]["properties"]["propositions"][
        "items"
    ]["properties"]
    assert cprops["personId"]["enum"] == ["p1", "p2"]
    assert cprops["motiveId"]["enum"] == ["m1"]

    adapted = sa.adapt_schema_for_transport(
        closed, adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
    )
    aprops = adapted["properties"]["evidence"]["items"]["properties"]["propositions"][
        "items"
    ]["properties"]
    assert aprops["personId"]["enum"] == ["p1", "p2"]
    assert aprops["motiveId"]["enum"] == ["m1"]


# --------------------------------------------------------------------------- #
# §17.9 — Cohere adaptation remains behaviorally unchanged
# --------------------------------------------------------------------------- #


def test_cohere_wire_schema_is_adapter_only_no_closure(monkeypatch):
    rec = _Recorder([json.dumps({"a": "x"})]).install(monkeypatch)
    canonical = prompts.json_schema_for_stage_output("evidence")
    assert canonical is not None
    provider = _provider("openrouter", model="cohere/command-a-plus")
    provider.generate(_request(canonical))
    wire_schema = rec.bodies[-1]["response_format"]["json_schema"]["schema"]
    expected = sa.adapt_schema_for_transport(
        canonical, adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
    )
    # Closure is NOT forced onto Cohere: the wire is exactly the Phase31A
    # adapter output over the canonical schema (byte-identical).
    assert wire_schema == expected
    # Every object node still carries >= 1 required property (Track A).
    for node in _walk_objects(wire_schema):
        required = node.get("required")
        assert isinstance(required, list) and required, node


def test_non_cohere_wire_schema_is_closed():
    canonical = prompts.json_schema_for_stage_output("evidence")
    closed = sa.close_transport_modeled_objects(canonical)
    assert sa.adapt_schema_for_transport(closed, adapter=None) == closed
    # The canonical schema is NOT closed (transport-only projection).
    assert "additionalProperties" not in canonical


def _walk_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _walk_objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_objects(item)


# --------------------------------------------------------------------------- #
# §17.5 full-path — controller splits transport success from structured success
# --------------------------------------------------------------------------- #


def test_controller_reports_transport_success_without_structured_success(database_url):
    """A mocked HTTP 200 with extractable but schema-invalid JSON yields
    ``success=true`` (transport), ``transportSchemaValid=false``,
    ``structuredOutputRequested=true`` (a native ``response_format`` WAS
    attached to the request) and ``structuredOutput=false`` (application
    structured success — DEF-073/ADV-43B-06 request/success split)."""
    from app.generation import frontier_provider as fp_mod

    queue = [
        GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
        json.dumps(_CONTEXT_ECHO),  # evidence: 2xx, decodable, schema-invalid
        # bounded recovery (malformed) keeps the attempt bounded
        "malformed",
        "malformed",
    ]
    rec = _Recorder(queue)
    original = fp_mod.httpx.post
    fp_mod.httpx.post = rec._post
    log_buffer = io.StringIO()
    try:
        application = _make_app(database_url)
        try:
            with _CaptureLog(log_buffer), TestClient(application) as c:
                token, _ = create_session(c)
                res = c.post(
                    "/api/v1/cases",
                    json={
                        "prompt": _PROMPT,
                        "generationProvider": "frontier",
                        "frontier": {
                            "provider": "openrouter",
                            "apiKey": "K",
                            "model": MODEL,
                        },
                    },
                    headers=auth(token),
                )
                assert res.status_code == 201, res.text
                events = _events(log_buffer.getvalue())
                evidence_completes = [
                    e
                    for e in events
                    if e.get("event") == "provider.call.complete"
                    and e.get("stage") == "evidence"
                ]
                assert evidence_completes, events
                ev = evidence_completes[0]
                assert ev["success"] is True
                assert ev["transportSchemaValid"] is False
                assert ev["structuredOutput"] is False
                assert ev["structuredOutputRequested"] is True
                assert ev["structuredOutputMode"] == "openai_json_schema"
                assert ev["jsonDecoded"] is True
                rejected = [
                    e
                    for e in events
                    if e.get("event") == "provider.structured_output.rejected"
                    and e.get("stage") == "evidence"
                ]
                assert rejected, "the schema-invalid evidence must emit rejection"
                validated = [
                    e
                    for e in events
                    if e.get("event") == "provider.structured_output.validated"
                    and e.get("stage") == "evidence"
                ]
                assert not validated
        finally:
            _dispose(application)
    finally:
        fp_mod.httpx.post = original


# --------------------------------------------------------------------------- #
# WAVE 2 — stage-boundary correctness (§17.4 / §17.6 / §17.7 / §17.10)
# --------------------------------------------------------------------------- #


def _empty_public_world_doc(*, pad: str = "") -> str:
    """A structurally valid PUBLIC_WORLD with every required array EMPTY.

    The strict parser accepts it (required keys present, empty arrays legal) —
    only the stage-local SEMANTIC breadth gate rejects it. ``pad`` lets a test
    inflate the byte size without changing the (empty) meaning, proving the
    rejection is not a response-size heuristic.
    """
    return json.dumps(
        {
            "persons": [],
            "motives": [],
            "objects": [],
            "locations": [],
            "travelRules": [],
            "scene": {"locationId": "scene_only", "name": "Scene Only"},
        }
    ) + pad


def _public_world_with_unknown_field() -> str:
    """A DEF-072-style public world carrying an unsupported person field."""
    doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD])
    doc["persons"][0]["description"] = "DEF-072 drift"
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def _public_world_without_motive_candidates() -> str:
    """A structurally valid public world whose MOTIVE_CANDIDATE universe is empty."""
    doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD])
    for motive in doc["motives"]:
        motive["affordances"] = [
            a for a in motive.get("affordances", []) if a != "MOTIVE_CANDIDATE"
        ]
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def _started_stages(events: list[dict]) -> list[str]:
    return [
        e["stage"]
        for e in events
        if e.get("event") == "provider.call.start" and "stage" in e
    ]


def _run_frontier(
    monkeypatch,
    queue,
    *,
    prompt=_PROMPT,
    max_repair_passes=2,
    max_full_regenerations=1,
):
    """Run one synchronous Frontier generation over a mocked HTTP queue.

    Returns ``(controller, recorder, handle, events)``. Zero network, zero
    paid calls — ``httpx.post`` is a deterministic recorder.
    """
    rec = _Recorder(list(queue)).install(monkeypatch)
    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=_provider("openrouter"),
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=max_repair_passes,
        max_full_regenerations=max_full_regenerations,
        max_prompt_chars=4000,
        seed=11,
        provider_name="frontier",
        provider_model=MODEL,
        provider_sub_id="openrouter",
    )
    log_buffer = io.StringIO()
    with _CaptureLog(log_buffer):
        handle = controller.start_generation(
            prompt, anonymous_quota_session_id=session.session_id
        )
    return controller, rec, handle, _events(log_buffer.getvalue())


def test_17_4_evidence_context_echo_not_trusted_and_no_downstream(monkeypatch):
    """§17.4 — the DEF-072 evidence context echo: local transport validation
    fails, the malformed provider stage is REJECTED (never structured-output
    success), and downstream generation never proceeds from that INVALID
    provider output.

    Phase43B (P43A-FR-07) — the malformed/unusable EVIDENCE stage is now offered
    to the Phase43A deterministic evidence completion, which supplies the
    canonical evidence and marks EVIDENCE trusted (ZERO provider budget). So
    WORLD_GRAPH DOES proceed — but from the canonical completed evidence, NOT
    from the rejected context echo. The rejected provider output still never
    becomes structured-output success and the downstream world_graph here is a
    deliberately malformed payload that routes through the existing bounded
    recovery (the attempt ends FAILED)."""
    queue = [
        GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
        json.dumps(_CONTEXT_ECHO),
        "malformed",
        "malformed",
    ]
    controller, _rec, handle, events = _run_frontier(monkeypatch, queue)
    record = controller.attempt(handle.attempt_id)

    evidence_completes = [
        e
        for e in events
        if e.get("event") == "provider.call.complete" and e.get("stage") == "evidence"
    ]
    assert evidence_completes, events
    assert evidence_completes[0]["transportSchemaValid"] is False
    assert evidence_completes[0]["structuredOutput"] is False

    # No application-level structured-output validation success for EVIDENCE.
    assert not [
        e
        for e in events
        if e.get("event") == "provider.structured_output.validated"
        and e.get("stage") == "evidence"
    ]
    # The malformed PROVIDER output was rejected by the canonical parser; it was
    # superseded (not accepted) by the deterministic completion.
    rejected = [
        e
        for e in events
        if e.get("event") == "generation.stage.rejected" and e.get("stage") == "evidence"
    ]
    assert rejected and rejected[0]["canonicalParseValid"] is False
    assert not [
        e
        for e in events
        if e.get("event") == "generation.stage.accepted" and e.get("stage") == "evidence"
    ]
    completion = [
        e for e in events if e.get("event") == "evidence.completion.completed"
    ]
    assert completion, "the deterministic completion must supply the evidence"
    # EVIDENCE is trusted via the deterministic completion (not the provider).
    assert GenerationStage.EVIDENCE in record.stage_done

    # Downstream generation proceeds from the COMPLETED canonical evidence (not
    # from the rejected context echo); the supplied malformed world_graph then
    # routes through the existing bounded recovery.
    seq = _started_stages(events)
    assert seq[:3] == ["case_truth", "public_world", "evidence"], seq
    assert "world_graph" in seq
    assert record.state is GenerationState.FAILED  # bounded recovery exhausted


def test_17_6_invalid_public_world_stops_downstream_then_recovers(monkeypatch):
    """§17.6 — an invalid public_world must produce the sequence
    ``case_truth -> public_world -> validation/recovery`` (NO evidence/
    world_graph) until a valid public world exists, then recover and proceed."""
    queue = [
        GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        _public_world_with_unknown_field(),
        # bounded recovery: a full-draft repair from the valid trusted state
        GOLDEN_FULL_DRAFT,
    ]
    controller, _rec, handle, events = _run_frontier(monkeypatch, queue)
    record = controller.attempt(handle.attempt_id)
    seq = _started_stages(events)

    # case_truth -> public_world -> validation/recovery; the invalid public
    # world NEVER triggers evidence/world_graph generation.
    assert seq == ["case_truth", "public_world", "repair"], seq
    assert "evidence" not in seq
    assert "world_graph" not in seq
    rejected = [
        e
        for e in events
        if e.get("event") == "generation.stage.rejected"
        and e.get("stage") == "public_world"
    ]
    assert rejected and rejected[0]["canonicalParseValid"] is False
    # The bounded recovery installs a valid full draft: the attempt recovers
    # and publishes (never having generated a downstream stage from the invalid
    # upstream state).
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None


def test_17_7_empty_public_world_rejected_by_semantic_breadth_not_size():
    """§17.7 — a tiny but structurally valid empty-array public world is
    rejected by the SEMANTIC breadth gate, not a byte-size heuristic."""
    attempt = AttemptRecord(attempt_id="GA-e", case_id="CASE-e", session_id="Q-e")
    assert (
        pipeline.apply_stage_output(
            attempt,
            GenerationStage.CASE_TRUTH,
            GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        )
        is True
    )
    tiny = _empty_public_world_doc()
    # A LARGE but still all-empty document: if rejection were byte-size based
    # this would be accepted (it is bigger than the valid golden public world).
    huge = _empty_public_world_doc(pad=" " * 50_000)
    assert len(huge.encode("utf-8")) > len(
        GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD].encode("utf-8")
    )

    spec = parser_mod.parse_stage(GenerationStage.PUBLIC_WORLD, tiny)
    assert spec is not None, "the empty-array public world is structurally valid"
    issues = pipeline.public_world_semantic_breadth_issues(attempt, spec)
    assert set(issues) == {
        "final homicide target universe is empty",
        "motive universe is empty",
        "weapon universe is empty",
    }

    assert (
        pipeline.apply_stage_output(attempt, GenerationStage.PUBLIC_WORLD, tiny)
        is False
    )
    assert GenerationStage.PUBLIC_WORLD not in attempt.stage_done
    assert (
        pipeline.apply_stage_output(attempt, GenerationStage.PUBLIC_WORLD, huge)
        is False
    )
    assert GenerationStage.PUBLIC_WORLD not in attempt.stage_done
    # A structurally valid, semantically BROAD world is accepted.
    assert (
        pipeline.apply_stage_output(
            attempt,
            GenerationStage.PUBLIC_WORLD,
            GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
        )
        is True
    )
    assert GenerationStage.PUBLIC_WORLD in attempt.stage_done


def test_17_7_empty_public_world_controller_stops_downstream(monkeypatch):
    """§17.7 (controller) — a large but semantically empty public world is
    rejected at the public_world boundary; evidence/world_graph never run."""
    queue = [
        GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        _empty_public_world_doc(pad=" " * 50_000),
        "malformed",
        "malformed",
    ]
    controller, _rec, handle, events = _run_frontier(monkeypatch, queue)
    record = controller.attempt(handle.attempt_id)
    seq = _started_stages(events)
    assert "evidence" not in seq
    assert "world_graph" not in seq
    rejected = [
        e
        for e in events
        if e.get("event") == "generation.stage.rejected"
        and e.get("stage") == "public_world"
    ]
    assert rejected
    assert rejected[0]["canonicalParseValid"] is True
    assert rejected[0]["semanticBreadthValid"] is False
    assert GenerationStage.PUBLIC_WORLD not in record.stage_done


def test_17_10_bounded_regeneration_preserves_locked_ids(monkeypatch):
    """§17.10 — after a semantically invalid public_world and a bounded
    regeneration: locked canonical IDs remain preserved, downstream stages are
    never replayed from the invalid upstream state, and the case publishes."""
    queue = [
        GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        _public_world_without_motive_candidates(),
        GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
        GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE],
        GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH],
    ]
    controller, _rec, handle, events = _run_frontier(monkeypatch, queue)
    record = controller.attempt(handle.attempt_id)
    seq = _started_stages(events)

    # Bounded regeneration: the first public world never generated evidence /
    # world_graph; the stages replayed from a valid upstream state afterwards.
    assert seq == [
        "case_truth",
        "public_world",
        "case_truth",
        "public_world",
        "evidence",
        "world_graph",
    ], seq
    assert record.budget.regenerations == 1
    assert record.state is GenerationState.PUBLISHED

    published = record.published
    assert published is not None
    # Locked canonical IDs preserved across the bounded regeneration.
    assert published.truth.crime.murderer_id == "thomas_reed"
    assert published.truth.crime.motive_id == "cover_up_embezzlement"
    assert published.truth.crime.weapon_id == "kitchen_knife"
    assert published.truth.crime.victim_id == "sarah_miller"
    assert published.solver_proof.validation.all_true is True
    assert published.locked == record.locked


def test_carryover_transport_invalid_but_canonically_valid_stage_is_trusted(
    monkeypatch,
):
    """Wave-1 carry-over: ``transportSchemaValid`` is TELEMETRY. A full-draft
    repair that ADDS declarations excluded from the sent narrowed reference
    enums is transport-schema-invalid but canonically valid, and MUST still
    become trusted — the canonical parser is the sole stage authority."""
    narrowed = prompts.narrow_stage_output_schema(
        "repair",
        reference_universe={
            "personIds": ("only_person",),
            "motiveIds": ("only_motive",),
            "objectIds": ("only_object",),
            "locationIds": ("only_location",),
        },
    )
    rec = _Recorder([GOLDEN_FULL_DRAFT]).install(monkeypatch)
    provider = _provider("openrouter")
    result = provider.generate(_request(narrowed, stage=GenerationStage.REPAIR))
    assert result.content == GOLDEN_FULL_DRAFT
    # Transport validity is telemetry: the golden full draft carries ids outside
    # the narrowed reference enums.
    assert provider.last_transport_schema_valid is False
    assert provider.last_content_extracted is True
    assert provider.last_json_decoded is True

    # The canonical parser is the stage authority: the content is canonically
    # valid and the stage becomes trusted despite the transport telemetry.
    assert parser_mod.collect_full_draft_issues(GOLDEN_FULL_DRAFT) == ()
    attempt = AttemptRecord(attempt_id="GA-c", case_id="CASE-c", session_id="Q-c")
    assert (
        pipeline.apply_stage_output(attempt, GenerationStage.REPAIR, GOLDEN_FULL_DRAFT)
        is True
    )
    assert attempt.draft is not None


# --------------------------------------------------------------------------- #
# P43A-FR-07 completion seam — an UNTRUSTED EVIDENCE stage is offered to the
# deterministic algebra BEFORE routing to recovery (Phase43B integration fix).
# --------------------------------------------------------------------------- #


def _world_graph_without_evidence_refs() -> str:
    """A canonically valid WORLD_GRAPH whose placements carry no evidenceId (so
    it validates against the deterministic algebra's canonical evidence ids,
    which differ from the golden evidence ids)."""
    doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH])
    for placement in doc["worldGraph"]["placements"]:
        placement["evidenceId"] = None
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def _case_truth_with_unknown_murderer() -> str:
    """A structurally valid CASE_TRUTH whose ``crime.murdererId`` does not
    resolve to any public person — a NON-evidence structural defect."""
    doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH])
    doc["crime"]["murdererId"] = "ghost_person"
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def test_malformed_evidence_completed_deterministically_then_publishes(monkeypatch):
    """P43A-FR-07 — a malformed/unusable EVIDENCE stage alongside a valid
    case/public skeleton is superseded by the deterministic algebra: EVIDENCE
    becomes trusted (ZERO provider/repair/regeneration budget), WORLD_GRAPH
    proceeds from the canonical evidence, and the case publishes. No LLM/network:
    the provider is the deterministic ``_Recorder``."""
    queue = [
        GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
        json.dumps(_CONTEXT_ECHO),  # malformed/unusable provider EVIDENCE
        _world_graph_without_evidence_refs(),
    ]
    controller, rec, handle, events = _run_frontier(monkeypatch, queue)
    record = controller.attempt(handle.attempt_id)

    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    assert record.last_validation is not None
    assert record.last_validation.outcome is ValidationOutcome.VALID
    # The deterministic completion supplied the evidence and marked it trusted.
    assert GenerationStage.EVIDENCE in record.stage_done
    assert record._evidence_completed is True
    completed = [e for e in events if e.get("event") == "evidence.completion.completed"]
    assert len(completed) == 1
    installed = record.stage_outputs[GenerationStage.EVIDENCE]
    installed_ids = {str(item.id) for item in installed.evidence}
    assert "d_ev_presence" in installed_ids and "d_ev_weapon_true" in installed_ids
    # Zero recovery budget and exactly the four GENERATING provider calls.
    assert record.budget.repair_passes == 0
    assert record.budget.regenerations == 0
    assert record.budget.calls == 4
    assert len(rec.bodies) == 4
    assert _started_stages(events) == [
        "case_truth",
        "public_world",
        "evidence",
        "world_graph",
    ]
    assert record.published.solver_proof.validation.all_true is True


def test_malformed_evidence_with_non_evidence_defect_refuses_and_recovers(monkeypatch):
    """P43A-FR-08 — a malformed EVIDENCE stage alongside a NON-evidence defect
    (an unresolved ``crime.murdererId``) makes the deterministic completion
    REFUSE. The defect is NOT masked: EVIDENCE stays untrusted, WORLD_GRAPH is
    never generated, and the failure routes through the existing bounded
    recovery."""
    queue = [
        _case_truth_with_unknown_murderer(),
        GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
        "malformed",  # provider EVIDENCE parse failure
        "malformed",  # would-be WORLD_GRAPH (must NOT be reached)
        "malformed",  # bounded repair
        "malformed",  # bounded repair
    ]
    controller, _rec, handle, events = _run_frontier(monkeypatch, queue)
    record = controller.attempt(handle.attempt_id)

    # The completion refused: no evidence installed, no completion success.
    completion = [e for e in events if e.get("event") == "evidence.completion.completed"]
    assert not completion
    skipped = [e for e in events if e.get("event") == "evidence.completion.skipped"]
    assert skipped and skipped[0]["reasonCode"] == "PRECONDITIONS_NOT_MET"
    assert GenerationStage.EVIDENCE not in record.stage_done
    # The non-evidence structural defect (unresolved murdererId) survives into
    # validation rather than being masked by a completion.
    assert any(
        "ghost_person" in issue for issue in record.last_validation.structural_issues
    )
    # Downstream generation never proceeded from the untrusted EVIDENCE state.
    seq = _started_stages(events)
    assert "world_graph" not in seq, seq
    assert "repair" in seq
    assert record.state is GenerationState.FAILED  # bounded recovery exhausted


def test_evidence_completion_is_idempotent_and_budgetless():
    """P43A-FR-10 — the deterministic completion runs AT MOST ONCE per attempt
    and consumes ZERO provider calls / repair / regeneration budget."""
    from app.generation.budgets import BudgetTracker

    attempt = AttemptRecord(
        attempt_id="GA-idem", case_id="CASE-idem", session_id="Q-idem"
    )
    attempt.budget = BudgetTracker(
        ManualClock(),
        deadline_seconds=60,
        max_calls=8,
        max_repairs=2,
        max_regenerations=1,
    )
    assert (
        pipeline.apply_stage_output(
            attempt,
            GenerationStage.CASE_TRUTH,
            GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        )
        is True
    )
    assert (
        pipeline.apply_stage_output(
            attempt,
            GenerationStage.PUBLIC_WORLD,
            GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
        )
        is True
    )
    assert (
        pipeline.apply_stage_output(attempt, GenerationStage.EVIDENCE, "not-json")
        is False
    )

    calls_before = attempt.budget.calls
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is True
    installed = attempt.stage_outputs[GenerationStage.EVIDENCE]
    assert GenerationStage.EVIDENCE in attempt.stage_done

    # A second offer is a no-op (once-per-attempt guard): no re-completion, no
    # mutation, no budget.
    assert pipeline.complete_generic_evidence_if_underconstrained(attempt) is False
    assert attempt.stage_outputs[GenerationStage.EVIDENCE] is installed
    assert attempt._evidence_completed is True

    assert attempt.budget.calls == calls_before
    assert attempt.budget.repair_passes == 0
    assert attempt.budget.regenerations == 0
