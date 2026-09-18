"""Configurable local Ollama generation provider (Phase16 B/D/E/F/G/H).

``OllamaProvider`` implements the EXACT existing ``Provider`` protocol
(``Provider.generate(GenerateRequest) -> ProviderResult``) — no Ollama type
escapes this adapter, and Ollama output is treated as untrusted generated data
that passes through the identical strict parsers / checks / budgets / lifecycle
/ validation / oracle / publication as every other provider.

Design rules (locked for this module):

- NON-STREAMING single POST to ``{base_url}/api/chat`` with a ``messages``
  array carrying the stage prompt (same declarative surface the pipeline
  already expects — ``request.prompt_context`` / ``locked.locked_fields()`` /
  ``diagnostics``, exactly like the live adapter).
- Transport is isolated HERE (sync ``httpx``): the provider maps transport
  outcomes into the generic ``ProviderResult`` contract and no provider-internal
  exception propagates to the caller:
  - 2xx with a textual ``message.content`` (or top-level ``content``) ->
    ``ProviderResult(content=...)``;
  - non-2xx -> ``ProviderResult(error=<sanitized>)``;
  - Python ``TimeoutError`` -> ``ProviderResult(timed_out=True)``;
  - any other transport/network/JSON envelope failure ->
    ``ProviderResult(error=<sanitized>, timed_out=False)``.
- Bodies are bounded: the composed prompt is capped at
  ``MAX_OLLAMA_PROMPT_CHARS`` and every response at ``MAX_OLLAMA_RESPONSE_BYTES``
  (256 KiB, clean truncation error).
- The ONLY response normalization is stripping ONE outer markdown code-fence
  wrapper (```json ... ```) BEFORE the strict parser; any remaining
  malformation fails through the NORMAL parse/validation/repair path. Code is
  never extracted or executed.
- ``OLLAMA_BASE_URL`` is OPERATOR-ONLY: never derived from the request, never
  logged, never emitted to player DTOs. No logs of prompts/keys anywhere.
- Ollama is synchronous-result only (like the live provider): the ``sink`` is
  accepted for interface compatibility, pending results are never produced and
  the existing pending/on-completion machinery is untouched.
- ASSETSPEC / procedural assets (Phase13 F): the staged generation boundary
  has no AssetSpec provider stub — procedural ``proc.*`` assets stay on the
  deterministic known/spec provider path (``app.assets.spec_provider``); this
  adapter serves the five generation stages (CASE_TRUTH / PUBLIC_WORLD /
  EVIDENCE / WORLD_GRAPH / REPAIR) with the same ``_prompt_for`` surface.
  Unknown-object requests still reach the pipeline through this provider
  boundary via the composed WORLD_GRAPH stage request (which carries the
  extracted unknown noun in its sanitized context).
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Protocol

import httpx

from app.core.config import DEFAULT_OLLAMA_BASE_URL
from app.generation.provider import GenerateRequest, ProviderResult

# Bounded request/response caps (documented; see module docstring).
MAX_OLLAMA_PROMPT_CHARS = 120_000
MAX_OLLAMA_RESPONSE_BYTES = 256 * 1024  # 256 KiB

OLLAMA_CHAT_ENDPOINT = "/api/chat"
OLLAMA_TAGS_ENDPOINT = "/api/tags"

# One outer markdown code-fence header (``` or ```json/```python/...).
_FENCE_HEADER_RE = re.compile(r"^```[A-Za-z0-9_\-]*$")


class OllamaTransport(Protocol):
    """Bounded sync transport surface (isolated to this module).

    Returns ``(status_code, response_bytes)``. A read that exceeds
    ``MAX_OLLAMA_RESPONSE_BYTES`` returns a body larger than the cap so the
    provider reports a clean truncation error. May raise ``TimeoutError`` (a
    request exceeded its timeout) or another ``Exception`` (transport error).
    """

    def post_json(
        self, url: str, payload: Mapping[str, Any], timeout: float
    ) -> tuple[int, bytes]: ...
    def get(self, url: str, timeout: float) -> tuple[int, bytes]: ...


class _HttpxOllamaTransport:
    """Default sync httpx transport (transport isolated in this module)."""

    def __init__(self) -> None:
        self._client = httpx.Client()

    def post_json(
        self, url: str, payload: Mapping[str, Any], timeout: float
    ) -> tuple[int, bytes]:
        try:
            response = self._client.post(url, json=dict(payload), timeout=timeout)
        except httpx.TimeoutException as exc:
            raise TimeoutError("ollama request timed out") from exc
        except httpx.RequestError as exc:
            raise OSError(f"ollama transport error: {type(exc).__name__}") from exc
        return response.status_code, self._read_bounded(response)

    def get(self, url: str, timeout: float) -> tuple[int, bytes]:
        try:
            response = self._client.get(url, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise TimeoutError("ollama probe timed out") from exc
        except httpx.RequestError as exc:
            raise OSError(f"ollama transport error: {type(exc).__name__}") from exc
        return response.status_code, self._read_bounded(response)

    def _read_bounded(self, response: httpx.Response) -> bytes:
        """Read at most ``MAX_OLLAMA_RESPONSE_BYTES + 1`` bytes.

        A body larger than the cap is not buffered: reading stops and the
        provider's size check turns the oversize into a clean truncation error.
        """
        total = 0
        chunks: list[bytes] = []
        for chunk in response.iter_bytes(65536):
            total += len(chunk)
            if total > MAX_OLLAMA_RESPONSE_BYTES:
                return b"x" * (MAX_OLLAMA_RESPONSE_BYTES + 1)
            chunks.append(chunk)
        return b"".join(chunks)


def _model_matches(configured: str, entry: str) -> bool:
    """Exact model-name match with ``name:tag`` normalization.

    A configured tag of ``latest`` (or a missing tag) matches any tag of the
    same model name; otherwise the tags must be equal.
    """

    def split_model(name: str) -> tuple[str, str]:
        head, _, tag = name.partition(":")
        return head, tag or "latest"

    configured_name, configured_tag = split_model(configured)
    entry_name, entry_tag = split_model(entry)
    return configured_name == entry_name and (
        configured_tag == "latest" or configured_tag == entry_tag
    )


def strip_outer_code_fence(text: str) -> str:
    """Adapter-only bounded normalization: strip ONE outer code-fence wrapper.

    Ollama frequently wraps its JSON answer in a markdown code fence
    (`````json\n...\n`````). This documented normalization removes ONLY that
    outer fence pair BEFORE the strict parser. Any remaining malformation is
    handed to the strict parser unchanged and fails through the NORMAL
    failure path (validation -> repair -> regeneration -> failure). Never
    extracts or executes code.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.split("\n")
    if len(lines) < 2:
        return text  # lone fence opener with no body — let the parser fail
    if not _FENCE_HEADER_RE.match(lines[0].strip()):
        return text
    if lines[-1].strip() != "```":
        return text
    return "\n".join(lines[1:-1]).rstrip("\n")


class OllamaProvider:
    """Synchronous, non-streaming local Ollama adapter (Provider protocol).

    All constructor arguments are OPERATOR configuration (already validated by
    ``Settings``); nothing is ever derivable from a ``GenerateRequest``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        temperature: float = 0.2,
        num_ctx: int = 4096,
        transport: Any | None = None,  # OllamaTransport | factory | None
        sink: Any = None,  # CompletionSink | None (interface compatibility)
    ) -> None:
        self._base_url = str(base_url).rstrip("/")
        self._model = str(model)
        self._timeout_seconds = float(timeout_seconds)
        self._temperature = float(temperature)
        self._num_ctx = int(num_ctx)
        self._transport = transport
        self._sink = sink

    # -- Provider protocol ---------------------------------------------------

    def generate(self, request: GenerateRequest) -> ProviderResult:
        try:
            prompt = self._prompt_for(request)
        except Exception:  # noqa: BLE001 - defensive; never propagate
            return ProviderResult(error="provider could not build the request prompt")
        if len(prompt) > MAX_OLLAMA_PROMPT_CHARS:
            return ProviderResult(
                error="provider request prompt exceeds the maximum size"
            )
        url = f"{self._base_url}{OLLAMA_CHAT_ENDPOINT}"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": self._temperature,
                "num_ctx": self._num_ctx,
            },
        }
        try:
            status, raw = self._get_transport().post_json(
                url, payload, self._timeout_seconds
            )
        except TimeoutError:
            return ProviderResult(timed_out=True)
        except Exception as exc:  # noqa: BLE001 - transport error, sanitized
            return ProviderResult(
                error=f"provider transport failure: {type(exc).__name__}"
            )
        if not (200 <= status < 300):
            return ProviderResult(error=f"provider returned HTTP {status}")
        if raw is None or len(raw) > MAX_OLLAMA_RESPONSE_BYTES:
            return ProviderResult(error="provider response exceeded the size cap")
        text = self._extract_content(raw)
        if text is None:
            return ProviderResult(error="provider response carried no textual content")
        return ProviderResult(content=strip_outer_code_fence(text))

    # -- private -------------------------------------------------------------

    def _get_transport(self) -> OllamaTransport:
        transport = self._transport
        if transport is None:
            return _HttpxOllamaTransport()
        if callable(transport):
            return transport()
        return transport

    def _extract_content(self, raw: bytes) -> str | None:
        """The textual provider payload of one 2xx /api/chat envelope."""
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        message = data.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
        content = data.get("content")
        if isinstance(content, str):
            return content
        return None

    def _prompt_for(self, request: GenerateRequest) -> str:
        """Sanitized per-stage prompt (SAME declarative surface the pipeline
        already expects: stage + sanitized prompt_context + locked projection +
        sanitized repair diagnostics). No provider-specific types leak."""
        lines = [
            f"You are generating structured JSON for the "
            f"'{request.stage.value}' stage of a detective case.",
            "Return ONLY a single JSON document matching the documented schema.",
            "",
        ]
        lines.append(request.prompt_context or "(no context)")
        if request.locked is not None:
            lines.append("")
            lines.append("Locked user constraints (must be respected exactly):")
            for field, value in request.locked.locked_fields():
                if value is not None:
                    lines.append(f"- {field}: {value}")
        if request.diagnostics:
            lines.append("")
            lines.append("Repair diagnostics (sanitized):")
            for diagnostic in request.diagnostics:
                lines.append(f"- {diagnostic}")
        return "\n".join(lines)


def ollama_available(settings: Any, transport: Any = None) -> tuple[bool, str]:
    """Lightweight availability probe (Phase16 D): GET ``{base}/api/tags``.

    Returns ``(True, "")`` when the endpoint is reachable AND the configured
    model appears in ``data.models[].name`` (exact name or ``name:tag``
    normalization); ``(False, "not available")`` otherwise. NEVER raises and
    never reveals network details or the base URL: every failure (timeout,
    transport error, HTTP error, capped/oversize body, unparsable body, model
    absent) degrades to the same sanitized ``"not available"`` detail.

    The probe has its own timeout (the configured ``OLLAMA_TIMEOUT_SECONDS``,
    bounded 5..300 by Settings) and a capped response
    (``MAX_OLLAMA_RESPONSE_BYTES``).
    """
    transport = transport if transport is not None else _HttpxOllamaTransport()
    base = str(
        getattr(settings, "ollama_base_url", None) or DEFAULT_OLLAMA_BASE_URL
    ).rstrip("/")
    model = str(getattr(settings, "ollama_model", "") or "")
    timeout = float(getattr(settings, "ollama_timeout_seconds", 60.0) or 60.0)
    try:
        status, raw = transport.get(f"{base}{OLLAMA_TAGS_ENDPOINT}", timeout)
    except Exception:  # noqa: BLE001 - availability never raises
        return False, "not available"
    if not (200 <= status < 300):
        return False, "not available"
    if raw is None or len(raw) > MAX_OLLAMA_RESPONSE_BYTES:
        return False, "not available"
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, TypeError):
        return False, "not available"
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return False, "not available"
    for entry in data["models"]:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            if _model_matches(model, entry["name"]):
                return True, ""
    return False, "not available"


__all__ = [
    "DEFAULT_OLLAMA_BASE_URL",
    "MAX_OLLAMA_PROMPT_CHARS",
    "MAX_OLLAMA_RESPONSE_BYTES",
    "OLLAMA_CHAT_ENDPOINT",
    "OLLAMA_TAGS_ENDPOINT",
    "OllamaProvider",
    "ollama_available",
    "strip_outer_code_fence",
]