import asyncio

import httpx
import pytest

from pd_ollama_bridge.ollama_client import (
    InvalidOutput,
    LocalModelUnavailable,
    OllamaClient,
    OllamaUnavailable,
    ProviderTimeout,
)
from conftest import make_ollama
from mock_ollama import MockOllama


def test_check_available_ok():
    mock = MockOllama(tags=("hermes3:8b", "llama3:8b"))

    async def run():
        client = make_ollama(mock)
        try:
            ok, tags = await client.check_available()
            return ok, tags
        finally:
            await client.aclose()

    ok, tags = asyncio.run(run())
    assert ok is True
    assert "hermes3:8b" in tags


def test_check_available_real_ollama_shape():
    mock = MockOllama(tags=({"name": "hermes3:8b", "model": "hermes3:8b"},))

    async def run():
        client = make_ollama(mock)
        try:
            ok, tags = await client.check_available()
            return ok, tags
        finally:
            await client.aclose()

    ok, tags = asyncio.run(run())
    assert ok is True
    assert tags == ("hermes3:8b",)


def test_check_available_failure_is_graceful():
    mock = MockOllama(tags=(), tags_status=500)

    async def run():
        client = make_ollama(mock)
        try:
            return await client.check_available()
        finally:
            await client.aclose()

    ok, tags = asyncio.run(run())
    assert ok is False
    assert tags == ()


def test_run_structured_inference_success():
    mock = MockOllama()
    expected = mock.outputs["ASSET_SPEC_v1"]

    async def run():
        client = make_ollama(mock)
        try:
            out = await client.run_structured_inference(
                prompt="a prompt", temperature=0.1, timeout_ms=120_000
            )
            return out, mock.requests
        finally:
            await client.aclose()

    out, requests = asyncio.run(run())
    assert out == expected
    assert requests[0]["model"] == "hermes3:8b"
    assert requests[0]["stream"] is False
    assert requests[0]["format"] == "json"
    assert requests[0]["messages"][0]["content"] == "a prompt"
    assert requests[0]["options"]["temperature"] == 0.1


def test_ollama_unreachable_maps_to_unavailable():
    mock = MockOllama(raise_exc=httpx.ConnectError("connection refused"))

    async def run():
        client = make_ollama(mock)
        try:
            with pytest.raises(OllamaUnavailable):
                await client.run_structured_inference(
                    prompt="x", temperature=0.1, timeout_ms=120_000
                )
        finally:
            await client.aclose()

    asyncio.run(run())


def test_model_missing_maps_to_model_unavailable():
    mock = MockOllama(status=404)

    async def run():
        client = make_ollama(mock)
        try:
            with pytest.raises(LocalModelUnavailable):
                await client.run_structured_inference(
                    prompt="x", temperature=0.1, timeout_ms=120_000
                )
        finally:
            await client.aclose()

    asyncio.run(run())


def test_provider_timeout():
    mock = MockOllama(hang=True)

    async def run():
        client = make_ollama(mock)
        try:
            with pytest.raises(ProviderTimeout):
                await client.run_structured_inference(
                    prompt="x", temperature=0.1, timeout_ms=1_000
                )
            return mock.cancelled
        finally:
            await client.aclose()

    cancelled = asyncio.run(run())
    assert cancelled == 1


def test_malformed_content_maps_to_invalid_output():
    mock = MockOllama(content='{"message":{"content":"this is not json"}}')

    async def run():
        client = make_ollama(mock)
        try:
            with pytest.raises(InvalidOutput):
                await client.run_structured_inference(
                    prompt="x", temperature=0.1, timeout_ms=120_000
                )
        finally:
            await client.aclose()

    asyncio.run(run())


def test_non_object_output_maps_to_invalid_output():
    mock = MockOllama(content='{"message":{"content":"[1,2,3]"}}')

    async def run():
        client = make_ollama(mock)
        try:
            with pytest.raises(InvalidOutput):
                await client.run_structured_inference(
                    prompt="x", temperature=0.1, timeout_ms=120_000
                )
        finally:
            await client.aclose()

    asyncio.run(run())


def test_oversized_response_rejected():
    huge = "x" * 300_000
    raw = '{"message":{"content":"' + huge + '"}}'
    mock = MockOllama(body=raw.encode("utf-8"))

    async def run():
        client = make_ollama(mock)
        try:
            with pytest.raises(InvalidOutput):
                await client.run_structured_inference(
                    prompt="x", temperature=0.1, timeout_ms=120_000
                )
        finally:
            await client.aclose()

    asyncio.run(run())


def test_deep_response_rejected():
    content = '{"a":' + "{" * 60 + "}" * 60 + "}"
    raw = '{"message":{"content":"' + content + '"}}'
    mock = MockOllama(body=raw.encode("utf-8"))

    async def run():
        client = make_ollama(mock)
        try:
            with pytest.raises(InvalidOutput):
                await client.run_structured_inference(
                    prompt="x", temperature=0.1, timeout_ms=120_000
                )
        finally:
            await client.aclose()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Phase 26C2 (R1) — trusted structured-schema pass-through
# --------------------------------------------------------------------------- #


def _activity_log_schema() -> dict:
    from pd_ollama_bridge import trusted_schemas

    schema = trusted_schemas.schema_for("ACTIVITY_LOG_v1")
    assert schema is not None
    return schema


def test_run_structured_inference_forwards_trusted_schema_as_format():
    """R1 — when a TRUSTED local schema is supplied (mapped from the job's
    authoritative schemaId by the bridge client) AND the local Ollama supports
    structured output, the /api/chat payload carries that schema as ``format``
    (minItems/maxItems/enum grammar enforcement — the observed live gap)."""
    mock = MockOllama(version="0.35.0")

    async def run():
        client = make_ollama(mock)
        try:
            out = await client.run_structured_inference(
                prompt="a prompt",
                temperature=0.1,
                timeout_ms=120_000,
                format_schema=_activity_log_schema(),
            )
            return out, mock.requests
        finally:
            await client.aclose()

    out, requests = asyncio.run(run())
    assert requests[0]["format"] == _activity_log_schema()
    assert requests[0]["format"]["type"] == "object"
    assert requests[0]["format"]["properties"]["entries"]["minItems"] == 15
    assert requests[0]["format"]["properties"]["entries"]["maxItems"] == 20
    assert requests[0]["model"] == "hermes3:8b"


def test_run_structured_inference_old_ollama_keeps_json_fallback():
    """R1 safety — an Ollama below the structured-output capability floor keeps
    free-form ``format: "json"`` (never a regression for older installs)."""
    mock = MockOllama(version="0.7.9")

    async def run():
        client = make_ollama(mock)
        try:
            out = await client.run_structured_inference(
                prompt="a prompt",
                temperature=0.1,
                timeout_ms=120_000,
                format_schema=_activity_log_schema(),
            )
            return out, mock.requests
        finally:
            await client.aclose()

    out, requests = asyncio.run(run())
    assert requests[0]["format"] == "json"


def test_run_structured_inference_unknown_version_fails_closed_to_json():
    """R1 safety — an Ollama whose version cannot be probed (404/unknown) fails
    CLOSED to ``format: "json"``; the trusted schema never crosses to a server
    that cannot be confirmed to accept it."""
    mock = MockOllama(version_status=404)

    async def run():
        client = make_ollama(mock)
        try:
            out = await client.run_structured_inference(
                prompt="a prompt",
                temperature=0.1,
                timeout_ms=120_000,
                format_schema=_activity_log_schema(),
            )
            return out, mock.requests
        finally:
            await client.aclose()

    out, requests = asyncio.run(run())
    assert requests[0]["format"] == "json"


def test_run_structured_inference_no_schema_keeps_json():
    """R1 — when the job's schemaId maps to NO trusted schema, the existing
    free-form behavior is byte-for-byte unchanged (format: \"json\")."""
    mock = MockOllama(version="0.35.0")

    async def run():
        client = make_ollama(mock)
        try:
            out = await client.run_structured_inference(
                prompt="a prompt", temperature=0.1, timeout_ms=120_000
            )
            return out, mock.requests
        finally:
            await client.aclose()

    out, requests = asyncio.run(run())
    assert requests[0]["format"] == "json"