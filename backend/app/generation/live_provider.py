"""Narrow live HTTP provider adapter (sync httpx; no SDK types).

``LiveHttpProvider`` translates a ``GenerateRequest`` into one synchronous
HTTP POST (``{model, messages:[{role, content}]}``, Bearer auth) and maps the
response into the generic ``ProviderResult`` contract:

- 2xx  -> ``ProviderResult(content=<response text>)``
- timeout -> raises ``ProviderTimeout``
- any other transport/HTTP failure -> ``ProviderResult(error=<sanitized>)``

Phase 20 hardening:

- PD-SEC-06: provider-derived error content is NEVER echoed. A non-2xx body
  is reduced to ``"provider failure: HTTP <status>"`` — no ``response.text``,
  no exception ``str()`` (``httpx.RequestError`` strings embed the request
  URL). A provider body carrying secrets/vendor error text can therefore never
  reach ``ProviderResult.error`` and never appear in a dev trace or log.
- PD-SEC-09: 2xx responses are bounded (``MAX_LIVE_RESPONSE_BYTES``, 256 KiB,
  the same cap the Ollama adapter enforces); an over-cap body degrades to the
  clean ``"provider response exceeded the size cap"`` error instead of being
  buffered unboundedly.

It is configuration-gated by CALLERS (a later lifecycle task decides when to
select it) and must be absent from all tests (network is blocked in the test
suite). No OpenAI/Anthropic/OpenRouter SDK types exist anywhere in this
adapter or leak through the provider boundary.

``sink`` is accepted for interface compatibility; this sync adapter never
produces ``pending`` results (the call blocks until completion or timeout).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.generation.provider import (
    GenerateRequest,
    GenerationStage,
    ProviderError,
    ProviderResult,
    ProviderTimeout,
)

# Bounded 2xx response cap (PD-SEC-09): mirrors the Ollama adapter's cap, so
# the live path can never buffer an unbounded provider body.
MAX_LIVE_RESPONSE_BYTES = 256 * 1024  # 256 KiB


class LiveHttpProvider:
    def __init__(
        self,
        endpoint_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        sink: Any = None,  # CompletionSink | None (interface compatibility)
    ) -> None:
        self._endpoint_url = endpoint_url
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = float(timeout_seconds)
        self._sink = sink

    def generate(self, request: GenerateRequest) -> ProviderResult:
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": self._prompt_for(request)}],
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            timeout_seconds = float(
                request.timeout_seconds
                if request.timeout_seconds is not None
                else self._timeout_seconds
            )
            response = httpx.post(
                self._endpoint_url,
                json=body,
                headers=headers,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"provider request timed out after {timeout_seconds}s"
            ) from exc
        except httpx.RequestError as exc:
            # PD-SEC-06: str(exc) may embed the request URL — never surface it.
            return ProviderResult(
                error=f"provider request failed: {type(exc).__name__}"
            )
        if not (200 <= response.status_code < 300):
            # PD-SEC-06: the response body may carry provider/vendor error
            # content — reduce it to the sanitized status only.
            return ProviderResult(error=f"provider failure: HTTP {response.status_code}")
        text = self._read_bounded(response)
        if text is None:
            return ProviderResult(error="provider response exceeded the size cap")
        return ProviderResult(content=text)

    def _read_bounded(self, response: httpx.Response) -> str | None:
        """Read at most ``MAX_LIVE_RESPONSE_BYTES`` bytes (PD-SEC-09).

        An over-cap body is NOT buffered: reading stops, and the size-cap error
        is returned (clean failure instead of unbounded memory use).
        """
        total = 0
        chunks: list[bytes] = []
        for chunk in response.iter_bytes(65536):
            total += len(chunk)
            if total > MAX_LIVE_RESPONSE_BYTES:
                return None
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    # -- private --------------------------------------------------------------

    def _prompt_for(self, request: GenerateRequest) -> str:
        """Sanitized per-stage prompt (no provider-specific types leak)."""
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
