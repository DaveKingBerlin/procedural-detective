"""Generation progress router (REQUIREMENTS 40.3, Phase5 F.3).

Serves the SANITIZED durable progress snapshot only — status/progress/stage
read from the database. Never truth, proof, diagnostics, prompt, provider
output or tokens.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.errors import http_error, map_service_error
from app.auth import require_creator
from app.models.credentials import CreatorCredential
from app.schemas.generations import GenerationProgressDTO

router = APIRouter(prefix="/generations", tags=["generations"])


@router.get(
    "/{generation_id}",
    response_model=GenerationProgressDTO,
    summary="Get sanitized generation progress (durable)",
    description=(
        "Requires the case's creatorAccessToken (the credential determines "
        "the case; the per-case generationId label is resolved only within "
        "that case). 404 when unknown or not owned."
    ),
)
def get_generation_progress(
    generation_id: str,
    request: Request,
    credential: Annotated[CreatorCredential, Depends(require_creator)] = None,
) -> GenerationProgressDTO:
    service = request.app.state.generation_service
    try:
        progress = service.get_generation_progress(
            generation_id, case_id=credential.case_id
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None
    if progress is None:
        raise http_error(404, "NOT_FOUND", "Not found")
    return GenerationProgressDTO(**progress)