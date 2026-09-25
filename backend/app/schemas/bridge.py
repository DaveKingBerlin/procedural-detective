"""Phase 22 — BYO-Ollama bridge REST DTOs (pairing + status).

SANITIZED EXACTLY like every other player-safe DTO in this codebase: never the
pairing code verifier, never the bridge session token, never the local Ollama
URL/IP, never the bridge's secret. The pairing code is returned to the browser
EXACTLY once at creation; the status DTO is a pure boolean/label view.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BridgePairingCreatedDTO(BaseModel):
    """POST /api/v1/bridge/pairing -> 201.

    ``pairingCode`` is a short-lived single-use code (PD-XXXX-XXXX) shown to
    the user; ``pairingSessionId`` is the opaque server-side record id;
    ``expiresAt`` bounds the code lifetime in epoch seconds.
    """

    pairingSessionId: str = Field(..., description="Opaque pairing record id.")
    pairingCode: str = Field(..., description="Short-lived single-use pairing code (PD-XXXX-XXXX).")
    expiresAt: float = Field(..., description="UTC epoch seconds when the code expires.")


class BridgeLocalAiStatusDTO(BaseModel):
    """The sanitized remote-local-AI status block (never token/IP/URL)."""

    available: bool = Field(..., description="Bridge feature enabled and that provider is usable here.")
    connected: bool = Field(..., description="A live bridge is bound to THIS requester's session.")
    model: str | None = Field(None, description="Sanitized model label reported by the bound bridge.")
    ready: bool = Field(..., description="Connected and currently accepting a new job.")


class BridgeStatusDTO(BaseModel):
    """GET /api/v1/bridge/status -> 200, scoped to the requester's session."""

    remoteLocalAi: BridgeLocalAiStatusDTO = Field(..., description="Sessionscoped bridge status.")


__all__ = ["BridgeLocalAiStatusDTO", "BridgePairingCreatedDTO", "BridgeStatusDTO"]