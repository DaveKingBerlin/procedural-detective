"""Player-safe generation capability service seam (Phase16 D).

The API layer must stay free of ``app.generation`` imports (hard boundary
contract, ``test_boundaries.py`` — API/DTOs stay schema/service-only). This
service owns the availability-probe seam: it exposes the SANITIZED Ollama
probe from the adapter so ``app.api.v1.generation_capabilities`` only ever
sees capability metadata. Nothing here reveals URLs, credentials, prompts,
network details or internal errors.
"""

from __future__ import annotations

from typing import Any

from app.generation.ollama_provider import ollama_available as _probe_ollama


def ollama_available(settings: Any, transport: Any = None) -> tuple[bool, str]:
    """Sanitized Ollama availability probe (delegates to the adapter probe).

    Returns ``(True, "")`` when the endpoint is reachable AND the configured
    model appears in ``/api/tags``; ``(False, "not available")`` for every
    failure — never raises, never reveals network details or the base URL.
    """
    return _probe_ollama(settings, transport)


__all__ = ["ollama_available"]