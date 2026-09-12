"""Sessions router (REQUIREMENTS 40.2, Phase5 F.1)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.v1.errors import map_service_error, http_error
from app.schemas.sessions import AnonymousSessionCreatedDTO

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post(
    "/anonymous",
    response_model=AnonymousSessionCreatedDTO,
    status_code=201,
    summary="Create anonymous quota session",
    description=(
        "No authentication. Creates a durable anonymous quota session and "
        "returns the opaque anonymousSessionToken exactly once. The token "
        "authorizes quota identity only (REQUIREMENTS 40.1/40.2)."
    ),
)
def create_anonymous_session(request: Request) -> AnonymousSessionCreatedDTO:
    try:
        created = request.app.state.generation_service.create_anonymous_quota_session()
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None
    return AnonymousSessionCreatedDTO(
        anonymousSessionToken=created.anonymous_session_token,
        quotaWindowEndsAt=created.quota_window_end,
    )