"""Sessions router (REQUIREMENTS 40.2, Phase5 F.1).

PD-SEC-02 (§6): ``POST /sessions/anonymous`` is a bounded public admission
point. The route enforces a per-IP rolling window AND a global ceiling before
any session is minted (denial -> the sanitized 429 ``TOO_MANY_REQUESTS``
envelope, no internal detail). The identity is the resolved client IP
(``TRUST_PROXY``-aware: the direct socket peer by default; forwarded headers
are NEVER honored while ``TRUST_PROXY=false``).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.v1.errors import http_error, map_service_error
from app.core.ratelimit import resolve_client_ip
from app.schemas.sessions import AnonymousSessionCreatedDTO

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _accept_anonymous_session(request: Request) -> str:
    """Resolve the rate-limit identity and reserve a session-admission slot.

    Returns the public IP used as the per-IP key. Raises the sanitized 429
    envelope when either the per-IP window or the global ceiling is exhausted
    — always BEFORE ``GenerationService`` is touched, with no internal detail.
    """
    limiter = request.app.state.anon_session_limiter
    trust_proxy = bool(request.app.state.settings.trust_proxy)
    ip = resolve_client_ip(request, trust_proxy=trust_proxy)
    allowed, _reason = limiter.allow(ip, request.app.state.clock.now())
    if not allowed:
        raise http_error(
            429,
            "TOO_MANY_REQUESTS",
            "Too many anonymous sessions; please try again later",
        )
    return ip


@router.post(
    "/anonymous",
    response_model=AnonymousSessionCreatedDTO,
    status_code=201,
    summary="Create anonymous quota session",
    description=(
        "No authentication. Creates a durable anonymous quota session and "
        "returns the opaque anonymousSessionToken exactly once. The token "
        "authorizes quota identity only (REQUIREMENTS 40.1/40.2). Rate "
        "limited per client IP and globally (PD-SEC-02)."
    ),
)
def create_anonymous_session(request: Request) -> AnonymousSessionCreatedDTO:
    _accept_anonymous_session(request)
    try:
        created = request.app.state.generation_service.create_anonymous_quota_session()
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None
    return AnonymousSessionCreatedDTO(
        anonymousSessionToken=created.anonymous_session_token,
        quotaWindowEndsAt=created.quota_window_end,
    )