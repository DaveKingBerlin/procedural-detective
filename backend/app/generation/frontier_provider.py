"""Phase 25 — Frontier hosted OpenAI-compatible provider adapter.

``FrontierProvider`` implements the EXACT existing ``Provider`` protocol
(``Provider.generate(GenerateRequest) -> ProviderResult``) and reuses the
``LiveHttpProvider`` transport pattern (one synchronous HTTP POST of an
OpenAI-compatible ``{model, messages:[{role, content}]}`` body + Bearer auth),
with the SAME Phase 20 hardening:

- PD-SEC-06: provider-derived error content is NEVER echoed. A non-2xx body
  is reduced to ``"provider failure: HTTP <status>"`` — no ``response.text``,
  no exception ``str()`` (``httpx.RequestError`` strings embed the request
  URL). A provider body carrying secrets/vendor error text can therefore never
  reach ``ProviderResult.error`` and never appear in a dev trace or log.
- PD-SEC-09: 2xx responses are bounded (``MAX_FRONTIER_RESPONSE_BYTES``,
  256 KiB — the same cap LiveHttpProvider and OllamaProvider enforce); an
  over-cap body degrades to the clean ``"provider response exceeded the size
  cap"`` error instead of being buffered unboundedly.
- The API key is SERVER-ONLY: it lives in this adapter's constructor (from
  operator Settings), is never logged, never embedded in an exception and
  never serialized into any DTO / capability response / case material.

Why a separate adapter instead of overloading ``LiveHttpProvider``:
``LiveHttpProvider`` is the legacy ``live`` provider pair (``LIVE_PROVIDER_URL``
/ ``LLM_API_KEY`` / ``LLM_MODEL``) with its own configured model bound at
construction. Phase 25 needs a SECOND hosted provider (``frontier``) with its
own ``FRONTIER_*`` settings so both can be available simultaneously and the
deployment can switch defaults without breaking the legacy live trio. The
transport pattern (bounded, sanitized) is deliberately mirrored here.

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

# Bounded 2xx response cap (PD-SEC-09 parity with the live/ollama adapters).
MAX_FRONTIER_RESPONSE_BYTES = 256 * 1024  # 256 KiB

# Documented default timeout when the caller does not supply one (the service
# always passes the operator-configured FRONTIER_TIMEOUT_SECONDS).
DEFAULT_FRONTIER_TIMEOUT_SECONDS = 60.0


class FrontierProvider:
    """Synchronous, bounded OpenAI-compatible hosted-language adapter.

    All constructor arguments are OPERATOR configuration (already validated by
    ``Settings``); nothing is ever derivable from a ``GenerateRequest``.
    """

    def __init__(
        self,
        endpoint_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_FRONTIER_TIMEOUT_SECONDS,
        sink: Any = None,  # CompletionSink | None (interface compatibility)
    ) -> None:
        self._endpoint_url = str(endpoint_url)
        self._api_key = str(api_key)
        self._model = str(model)
        self._timeout_seconds = float(timeout_seconds)
        self._sink = sink

    # -- Provider protocol ---------------------------------------------------

    def generate(self, request: GenerateRequest) -> ProviderResult:
        body: dict[str, Any] = {
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
        except httpx.TimeoutException:
            raise ProviderTimeout(
                f"provider request timed out after {timeout_seconds}s"
            ) from None
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
        """Read at most ``MAX_FRONTIER_RESPONSE_BYTES`` bytes (PD-SEC-09).

        An over-cap body is NOT buffered: reading stops, and the size-cap error
        is returned (clean failure instead of unbounded memory use).
        """
        total = 0
        chunks: list[bytes] = []
        for chunk in response.iter_bytes(65536):
            total += len(chunk)
            if total > MAX_FRONTIER_RESPONSE_BYTES:
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


__all__ = [
    "DEFAULT_FRONTIER_TIMEOUT_SECONDS",
    "FrontierProvider",
    "MAX_FRONTIER_RESPONSE_BYTES",
]