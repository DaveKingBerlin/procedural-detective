"""Playthrough router (REQUIREMENTS 40.4/40.6, Phase5 F.6/F.7).

Everything resolves through the PINNED (caseId, caseVersion) tuple stored on
the playthrough row — never through "latest".
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.errors import http_error, map_service_error
from app.auth import require_playthrough
from app.models.playthroughs import Playthrough
from app.schemas.cases import PublicCaseResponse
from app.schemas.playthroughs import PlaythroughResponseDTO

router = APIRouter(prefix="/playthroughs", tags=["playthroughs"])


@router.get(
    "/{playthrough_id}",
    response_model=PlaythroughResponseDTO,
    summary="Playthrough bootstrap (pinned version + state only)",
    description=(
        "Requires the playthrough's own playthroughAccessToken. A credential "
        "for playthrough A used on B answers 404. status always comes from "
        "the persisted row."
    ),
)
def get_playthrough(
    playthrough_id: str,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> PlaythroughResponseDTO:
    return PlaythroughResponseDTO(
        playthroughId=row.playthrough_id,
        caseId=row.case_id,
        caseVersion=row.case_version,
        status=row.state,
        createdAt=row.created_at,
        expiresAt=row.expires_at,
    )


@router.get(
    "/{playthrough_id}/public-case",
    response_model=PublicCaseResponse,
    summary="Public case of the PINNED published version",
    description=(
        "Requires the playthrough's own playthroughAccessToken. The payload "
        "is resolved ONLY from the published_versions row for the pinned "
        "(caseId, caseVersion) — publishing v2 never changes this response."
    ),
)
def get_playthrough_public_case(
    playthrough_id: str,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> PublicCaseResponse:
    service = request.app.state.generation_service
    try:
        public_case = service.get_public_case(row.case_id, row.case_version)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None
    if public_case is None:
        raise http_error(404, "NOT_FOUND", "Not found")
    return PublicCaseResponse(**public_case)