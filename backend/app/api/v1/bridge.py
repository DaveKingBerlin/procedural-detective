"""Phase 22 — BYO-Ollama bridge REST surface (pairing + status).

- ``POST /api/v1/bridge/pairing`` (auth: the anonymous SESSION bearer) creates
  ONE short-lived, single-use pairing code scoped to the authenticating
  session. The code is returned to the browser exactly once.
- ``GET /api/v1/bridge/status`` (auth: the same bearer) returns the sanitized,
  session-scoped bridge status — NEVER the token / secret / IP / URL.

Both routes exist ONLY when ENABLE_BRIDGE=true (the router is attached
conditionally in ``app.main.create_app``); with the feature OFF every bridge
path answers the existing 404 envelope.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.v1.errors import http_error
from app.core.ratelimit import resolve_client_ip
from app.models.quota import AnonymousQuotaSession
from app.auth import require_session
from app.schemas.bridge import (
    BridgeLocalAiStatusDTO,
    BridgePairingCreatedDTO,
    BridgeStatusDTO,
)
from app.services.bridge import PairingAdmissionDenied

router = APIRouter(prefix="/api/v1/bridge", tags=["bridge"])


def _pairing_service(request: Request):
    service = getattr(request.app.state, "bridge_pairing_service", None)
    if service is None:  # ENABLE_BRIDGE=false: the router is not even mounted
        raise http_error(404, "NOT_FOUND", "Not found")
    return service


def _registry(request: Request):
    registry = getattr(request.app.state, "bridge_registry", None)
    if registry is None:
        raise http_error(404, "NOT_FOUND", "Not found")
    return registry


def _enforce_pairing_rate_limit(request: Request) -> None:
    """Small per-IP hourly window on pairing-code minting (bounded admission;
    a minted code is itself high-entropy + single-use, this bounds volume)."""
    limiter = getattr(request.app.state, "bridge_pairing_ip_limiter", None)
    if limiter is None:
        return
    trust_proxy = bool(request.app.state.settings.trust_proxy)
    ip = resolve_client_ip(request, trust_proxy=trust_proxy)
    if not limiter.allow(ip, request.app.state.clock.now()):
        raise http_error(
            429,
            "TOO_MANY_REQUESTS",
            "Too many pairing codes; please try again later",
        )


@router.post(
    "/pairing",
    response_model=BridgePairingCreatedDTO,
    response_model_exclude_none=True,
    status_code=201,
    summary="Create a BYO-Ollama pairing code",
    description=(
        "Requires an anonymousSessionToken. Creates ONE short-lived, "
        "single-use pairing code (PD-XXXX-XXXX) scoped to the authenticating "
        "session. The code is displayed to the user and entered into the "
        "local bridge; it is NEVER the bridge's long-lived credential. "
        "Bounded per session and per IP."
    ),
)
def create_pairing(
    request: Request,
    session_row: Annotated[AnonymousQuotaSession, Depends(require_session)],
) -> BridgePairingCreatedDTO:
    _enforce_pairing_rate_limit(request)
    service = _pairing_service(request)
    try:
        pairing = service.create_pairing(session_row.session_id)
    except PairingAdmissionDenied:
        raise http_error(
            429, "TOO_MANY_REQUESTS", "Too many pairing codes; please try again later"
        ) from None
    return BridgePairingCreatedDTO(
        pairingSessionId=pairing.pairing_session_id,
        pairingCode=pairing.pairing_code,
        expiresAt=pairing.expires_at,
    )


@router.get(
    "/status",
    response_model=BridgeStatusDTO,
    summary="Session-scoped BYO-Ollama bridge status",
    description=(
        "Requires an anonymousSessionToken. Returns the sanitized bridge "
        "status for the REQUESTER'S session: available / connected (a live "
        "bridge is bound to this session) / model (sanitized label) / ready. "
        "NEVER returns the bridge token, IP or Ollama URL."
    ),
)
def bridge_status(
    request: Request,
    session_row: Annotated[AnonymousQuotaSession, Depends(require_session)],
) -> BridgeStatusDTO:
    registry = _registry(request)
    state = registry.status_for_scope(session_row.session_id)
    return BridgeStatusDTO(
        remoteLocalAi=BridgeLocalAiStatusDTO(
            available=True,
            connected=bool(state["connected"]),
            model=state.get("model"),
            ready=bool(state.get("ready", False)),
        )
    )


__all__ = ["router"]