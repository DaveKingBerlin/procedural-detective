"""Local Ollama access — the bridge speaks ONLY to the operator-owned Ollama.

The bridge calls the LOCAL Ollama HTTP API: ``GET /api/tags`` for reachability/
model discovery and ``POST /api/chat`` (a non-stream JSON-mode call) for
STRUCTURED_INFERENCE jobs. Every response is byte-bounded, depth-bounded and
collection-length-bounded before it can be returned as ``structuredOutput``.

Typed failures (the closed BRIDGE_FAILURE_CODES vocabulary):

- OllamaUnavailable      -> LOCAL_OLLAMA_UNAVAILABLE
- LocalModelUnavailable  -> LOCAL_MODEL_UNAVAILABLE
- ProviderTimeout        -> LOCAL_PROVIDER_TIMEOUT
- InvalidOutput          -> LOCAL_PROVIDER_INVALID_OUTPUT
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional, Tuple

import httpx

from . import protocol
from .bounded_json import BoundedJsonError, bounded_json_loads
from .urls import validate_ollama_url

FAILURE_CODE_ATTR = "failure_code"


class OllamaClientError(Exception):
    failure_code = "BRIDGE_PROTOCOL_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = str(message)


class OllamaUnavailable(OllamaClientError):
    failure_code = "LOCAL_OLLAMA_UNAVAILABLE"


class LocalModelUnavailable(OllamaClientError):
    failure_code = "LOCAL_MODEL_UNAVAILABLE"


class ProviderTimeout(OllamaClientError):
    failure_code = "LOCAL_PROVIDER_TIMEOUT"


class InvalidOutput(OllamaClientError):
    failure_code = "LOCAL_PROVIDER_INVALID_OUTPUT"


def _effective_timeout_ms(timeout_ms: int) -> int:
    margin = min(5_000, int(timeout_ms) // 2)
    return max(300, int(timeout_ms) - margin)


class OllamaClient:
    """Thin, bounded client for a single local Ollama endpoint + model."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        connect_timeout_seconds: float = protocol.DEFAULT_CONNECT_TIMEOUT_SECONDS,
        max_response_bytes: int = protocol.MAX_RESPONSE_BYTES,
        max_json_depth: int = protocol.MAX_JSON_DEPTH,
        max_collection_length: int = protocol.MAX_COLLECTION_LENGTH,
        http_client: Optional[httpx.AsyncClient] = None,
        allow_lan: bool = False,
    ) -> None:
        self.base_url = validate_ollama_url(base_url, allow_lan=allow_lan)
        self.model = model
        self.connect_timeout_seconds = connect_timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_json_depth = max_json_depth
        self.max_collection_length = max_collection_length
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout_seconds, read=None, write=None, pool=None
            ),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def check_available(self) -> Tuple[bool, Tuple[str, ...]]:
        try:
            response = await asyncio.wait_for(
                self._http.get(f"{self.base_url}/api/tags"),
                timeout=self.connect_timeout_seconds,
            )
            if response.status_code != 200:
                return False, ()
            payload = bounded_json_loads(response.content)
            if not isinstance(payload, Mapping) or not isinstance(payload.get("models"), list):
                return False, ()
            tags: list[str] = []
            for tag in payload["models"]:
                if isinstance(tag, str) and tag:
                    tags.append(tag)
                elif (
                    isinstance(tag, Mapping)
                    and isinstance(tag.get("name"), str)
                    and tag["name"]
                ):
                    tags.append(tag["name"])
            return True, tuple(tags)
        except (asyncio.TimeoutError, httpx.HTTPError, BoundedJsonError, ValueError):
            return False, ()

    async def list_local_models(self) -> Tuple[str, ...]:
        _, tags = await self.check_available()
        return tags

    async def run_structured_inference(
        self, *, prompt: str, temperature: float, timeout_ms: int
    ) -> Mapping[str, Any]:
        effective = _effective_timeout_ms(timeout_ms)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"temperature": float(temperature)},
        }
        try:
            coro = self._http.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                headers={"Accept": "application/json"},
            )
            async with asyncio.timeout(effective / 1000.0):
                async with coro as response:
                    if response.status_code == 404:
                        raise LocalModelUnavailable("local model unavailable")
                    if response.status_code != 200:
                        raise await self._map_http_error(response)
                    body = await self._read_bounded(response)
        except asyncio.TimeoutError as exc:
            raise ProviderTimeout("local provider timeout") from exc
        except OllamaClientError:
            raise
        except httpx.HTTPError as exc:
            raise OllamaUnavailable("local ollama unavailable") from exc
        except BoundedJsonError as exc:
            raise InvalidOutput(str(exc)) from exc
        try:
            return self._extract_structured_output(body)
        except (ValueError, BoundedJsonError) as exc:
            raise InvalidOutput("invalid structured output") from exc

    async def _map_http_error(self, response: httpx.Response) -> OllamaClientError:
        message = ""
        try:
            body = await self._read_bounded(response)
            parsed = bounded_json_loads(body)
            if isinstance(parsed, Mapping):
                message = str(parsed.get("error") or "").lower()
        except (BoundedJsonError, ValueError):
            message = ""
        if "model" in message:
            return LocalModelUnavailable("local model unavailable")
        return InvalidOutput("local provider invalid output")

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self.max_response_bytes:
                raise InvalidOutput("response exceeds maximum size")
            chunks.append(chunk)
        return b"".join(chunks)

    def _extract_structured_output(self, body: bytes) -> Mapping[str, Any]:
        parsed = bounded_json_loads(
            body,
            max_depth=self.max_json_depth,
            max_collection_length=self.max_collection_length,
        )
        if not isinstance(parsed, Mapping):
            raise InvalidOutput("ollama response is not a JSON object")
        raw_text: str | None = None
        message = parsed.get("message")
        if isinstance(message, Mapping) and isinstance(message.get("content"), str):
            raw_text = message["content"]
        elif isinstance(parsed.get("response"), str):
            raw_text = parsed["response"]
        if raw_text is None:
            raise InvalidOutput("ollama response has no model content")
        output = bounded_json_loads(
            raw_text,
            max_depth=self.max_json_depth,
            max_collection_length=self.max_collection_length,
        )
        if not isinstance(output, Mapping):
            raise InvalidOutput("model output is not a JSON object")
        return output


__all__ = [
    "InvalidOutput",
    "LocalModelUnavailable",
    "OllamaClient",
    "OllamaClientError",
    "OllamaUnavailable",
    "ProviderTimeout",
    "_effective_timeout_ms",
]