"""Phase 23 — witness interview router (deterministic, player-safe).

Endpoints (mounted under /api/v1 by the v1 namespace):

- GET  /playthroughs/{playthrough_id}/witnesses/{witness_id}
       -> the player-safe witness view (identity + closed presence + the six
       always-offered questions). PLAYING-only for consistency (Phase 23 §14).
- POST /playthroughs/{playthrough_id}/witnesses/{witness_id}/interview
       body {"questionType": "<closed enum>"}
       -> {witnessId, displayName, questionType, statement, discovery}.

Authorization / IDOR:

- ``require_playthrough`` (reused) binds the credential to the playthrough;
  a credential for playthrough A used on B answers the generic 404;
- the pinned published payload is resolved EXCLUSIVELY from the (case_id,
  case_version) stored on the playthrough row — never "latest";
- an unknown witness id, a NON-WITNESS public person, or a witness outside
  the pinned case answers the same generic 404 (no enumeration oracle);
- question availability NEVER leaks clue semantics: all six questions are
  always offered and each answers grounded or neutral;
- every non-2xx uses the shared error envelope
  ``{"error": {code, message, details}}``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.errors import http_error
from app.auth import require_playthrough
from app.models.playthroughs import Playthrough
from app.schemas.witnesses import (
    WitnessInterviewRequest,
    WitnessInterviewResponse,
    WitnessViewResponse,
)
from app.services.witnesses import (
    WitnessError,
    WitnessNotFoundError,
    WitnessService,
    WitnessStateError,
)

router = APIRouter(prefix="/playthroughs", tags=["playthroughs", "witnesses"])


def _service(request: Request) -> WitnessService:
    return WitnessService(
        store=request.app.state.store,
        clock=request.app.state.clock,
    )


def _translate(exc: Exception) -> HTTPException:
    """Translate witness-service errors into the shared envelope."""
    if isinstance(exc, WitnessNotFoundError):
        return http_error(404, "NOT_FOUND", "Not found")
    if isinstance(exc, WitnessStateError):
        return http_error(409, "NOT_PLAYING", "Playthrough is not currently playable")
    if isinstance(exc, WitnessError):
        return http_error(500, "INTERNAL_ERROR", "Internal server error")
    return http_error(500, "INTERNAL_ERROR", "Internal server error")


@router.get(
    "/{playthrough_id}/witnesses/{witness_id}",
    response_model=WitnessViewResponse,
    summary="Public witness view (identity + presence + available questions)",
    description=(
        "Requires the playthrough's own playthroughAccessToken. The witness "
        "must be a published person with role 'witness' in the PINNED "
        "(caseId, caseVersion) — unknown/non-witness ids answer the generic "
        "404. All six questions are ALWAYS offered (no clue-semantics leak); "
        "each answers a grounded or a neutral statement at interview time. "
        "PLAYING-only for consistency."
    ),
)
def get_witness(
    playthrough_id: str,
    witness_id: str,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> WitnessViewResponse:
    service = _service(request)
    try:
        result = service.get_witness(row, witness_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise _translate(exc) from None
    return WitnessViewResponse(**result)


@router.post(
    "/{playthrough_id}/witnesses/{witness_id}/interview",
    response_model=WitnessInterviewResponse,
    summary="Ask one structured witness question (deterministic, zero LLM calls)",
    description=(
        "Requires the playthrough's own playthroughAccessToken and a PLAYING "
        "playthrough. Body questionType must be one of the CLOSED values "
        "OBSERVATION/TIME/PERSON/OBJECT/LOCATION/SOUND (else 422). The "
        "answer is a deterministic projection from the PINNED published "
        "evidence — ZERO provider calls, no open chat. A GROUNDED question "
        "whose linked witness-kind evidence is discoverable discovers it "
        "(idempotent; repeat -> same statement + newlyDiscovered=False); a "
        "NEUTRAL answer performs zero state mutation. Unknown/non-witness id "
        "-> generic 404; not PLAYING -> 409 NOT_PLAYING."
    ),
)
def interview_witness(
    playthrough_id: str,
    witness_id: str,
    body: WitnessInterviewRequest,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> WitnessInterviewResponse:
    service = _service(request)
    try:
        result = service.interview_witness(row, witness_id, body.questionType)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise _translate(exc) from None
    return WitnessInterviewResponse(**result)
