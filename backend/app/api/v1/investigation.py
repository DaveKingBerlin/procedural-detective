"""Investigation router (REQUIREMENTS 40.7-40.9, Phase6 B, frozen Phase 6
contract consumed by the parallel frontend track).

Every endpoint:

- requires the playthrough's OWN ``playthroughAccessToken`` (reused
  ``require_playthrough`` dependency): a credential for playthrough A used on
  B answers 404;
- resolves the pinned published payload EXCLUSIVELY from the (case_id,
  case_version) stored on the playthrough row — never "latest"; a missing /
  unpublished pinned version answers the 404 envelope;
- answers every non-2xx with the shared error envelope
  ``{"error": {code, message, details}}``.

Endpoints:

- GET    /playthroughs/{playthrough_id}/investigation
- POST   /playthroughs/{playthrough_id}/evidence/{evidence_id}/discover
- POST   /playthroughs/{playthrough_id}/objects/{object_id}/interact
- GET    /playthroughs/{playthrough_id}/records/{record_id}
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.errors import http_error, map_service_error
from app.auth import require_playthrough
from app.models.playthroughs import Playthrough
from app.schemas.investigation import (
    DiscoveryResultDTO,
    EvidenceReadResultDTO,
    InteractionResultDTO,
    InvestigationBootstrapResponse,
    ObjectInteractionRequest,
)
from app.services.investigation import (
    EvidenceNotDiscoveredError,
    InteractionNotAllowedError,
    InvestigationError,
    InvestigationNotFoundError,
    InvestigationService,
    InvestigationStateError,
)

router = APIRouter(prefix="/playthroughs", tags=["playthroughs", "investigation"])


def _service(request: Request) -> InvestigationService:
    return InvestigationService(
        store=request.app.state.store,
        clock=request.app.state.clock,
    )


def _translate(exc: Exception) -> HTTPException:
    """Translate investigation service errors into the shared envelope."""
    if isinstance(exc, InvestigationNotFoundError):
        return http_error(404, "NOT_FOUND", "Not found")
    if isinstance(exc, EvidenceNotDiscoveredError):
        return http_error(
            403,
            "EVIDENCE_NOT_DISCOVERED",
            "This record has not been discovered yet",
        )
    if isinstance(exc, InteractionNotAllowedError):
        return http_error(
            409,
            "INTERACTION_NOT_ALLOWED",
            "That interaction is not allowed for this object",
        )
    if isinstance(exc, InvestigationStateError):
        return http_error(409, "NOT_PLAYING", "Playthrough is not currently playable")
    if isinstance(exc, InvestigationError):
        return http_error(500, "INTERNAL_ERROR", "Internal server error")
    return http_error(500, "INTERNAL_ERROR", "Internal server error")


@router.get(
    "/{playthrough_id}/investigation",
    response_model=InvestigationBootstrapResponse,
    summary="Investigation bootstrap (pinned scene + PlayerKnowledge)",
    description=(
        "Requires the playthrough's own playthroughAccessToken. Returns the "
        "starting location, every player-safe world object of the PINNED "
        "(caseId, caseVersion), the current PlayerKnowledge and the player-"
        "safe accusation candidates. DEF-051: READABLE from every lifecycle "
        "state (PLAYING / ACCUSED / REVEALED) — after an accusation this is "
        "the only player-safe source of the candidates block the reveal view "
        "needs for name resolution and reload state. Contains no coordinates, "
        "no evidence content, no propositions, no hidden truth/proof material."
    ),
)
def get_investigation(
    playthrough_id: str,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> InvestigationBootstrapResponse:
    service = _service(request)
    try:
        result = service.get_investigation_bootstrap(row)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise _translate(exc) from None
    return InvestigationBootstrapResponse(**result)


@router.post(
    "/{playthrough_id}/evidence/{evidence_id}/discover",
    response_model=DiscoveryResultDTO,
    summary="Discover one evidence record (server-authoritative)",
    description=(
        "Requires the playthrough's own playthroughAccessToken. The evidence "
        "must belong to the PINNED CaseVersion and be reachable via a valid "
        "world-graph placement (404 otherwise — unknown/fabricated and "
        "non-reachable ids are indistinguishable). Idempotent: repeating "
        "returns state 'already-discovered'. Marks the linked placement's "
        "location visited (server-side)."
    ),
)
def discover_evidence(
    playthrough_id: str,
    evidence_id: str,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> DiscoveryResultDTO:
    service = _service(request)
    try:
        result = service.discover_evidence(row, evidence_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise _translate(exc) from None
    return DiscoveryResultDTO(**result)


@router.post(
    "/{playthrough_id}/objects/{object_id}/interact",
    response_model=InteractionResultDTO,
    summary="Interact with one world object (validated by the placement)",
    description=(
        "Requires the playthrough's own playthroughAccessToken. The object "
        "must exist in the PINNED version's placements (404 otherwise); the "
        "requested interaction must equal the placement's published "
        "interaction (mismatch -> 409 INTERACTION_NOT_ALLOWED, no state "
        "change). An evidence-linked placement runs the same discovery logic "
        "as the discover endpoint and returns its DTO."
    ),
)
def interact_with_object(
    playthrough_id: str,
    object_id: str,
    body: ObjectInteractionRequest,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> InteractionResultDTO:
    service = _service(request)
    try:
        result = service.interact_with_object(row, object_id, body.interaction)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise _translate(exc) from None
    return InteractionResultDTO(**result)


@router.get(
    "/{playthrough_id}/records/{record_id}",
    response_model=EvidenceReadResultDTO,
    summary="Read one DISCOVERED record (kind-allowlisted content)",
    description=(
        "Requires the playthrough's own playthroughAccessToken. The record "
        "must exist in the PINNED CaseVersion (404 otherwise) and be in the "
        "playthrough's discovered set (403 EVIDENCE_NOT_DISCOVERED, generic "
        "message, no content). Idempotent: repeat reads return the same DTO "
        "(openedAt = ISO-8601 UTC of the first read). Content is the "
        "kind-specific allowlisted public mapping."
    ),
)
def read_record(
    playthrough_id: str,
    record_id: str,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> EvidenceReadResultDTO:
    service = _service(request)
    try:
        result = service.read_record(row, record_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise _translate(exc) from None
    return EvidenceReadResultDTO(**result)