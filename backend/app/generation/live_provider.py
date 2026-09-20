"""Narrow live HTTP provider adapter (sync httpx; no SDK types).

``LiveHttpProvider`` translates a ``GenerateRequest`` into one synchronous
HTTP POST (``{model, messages:[{role, content}]}``, Bearer auth) and maps the
response into the generic ``ProviderResult`` contract:

- 2xx  -> ``ProviderResult(content=<response text>)``
- timeout -> raises ``ProviderTimeout``
- any other transport/HTTP failure -> ``ProviderResult(error=<sanitized>)``

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
            return ProviderResult(
                error=f"provider request failed: {type(exc).__name__}: {str(exc)[:300]}"
            )
        if 200 <= response.status_code < 300:
            return ProviderResult(content=response.text)
        return ProviderResult(
            error=(
                f"provider returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        )

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
