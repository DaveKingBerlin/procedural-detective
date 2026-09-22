"""Playthrough router (REQUIREMENTS 40.4/40.6-40.12, Phase5 F.6/F.7 + Phase7).

Everything resolves through the PINNED (caseId, caseVersion) tuple stored on
the playthrough row — never through "latest".

Phase 7 additions:

- ``POST /playthroughs/{playthrough_id}/accusation`` — the frozen accusation
  contract (Phase7 B/C/I, REQUIREMENTS 40.10): a typed body whose ids must
  belong to the pinned CaseVersion's published candidate universes; the
  compare-and-set insert + ``{CREATED,PLAYING} -> ACCUSED`` transition happen
  in ONE transaction; a later/concurrent accusation answers
  ``409 CASE_ALREADY_SUBMITTED`` (40.11); the 200 response carries NO truth
  and NO per-dimension correctness;
- ``GET /playthroughs/{playthrough_id}/reveal`` — the frozen reveal allowlist
  (Phase7 E/F, REQUIREMENTS 40.12): only from ``{ACCUSED, REVEALED}`` (else
  ``403 REVEAL_NOT_AVAILABLE``), idempotent, sets ACCUSED -> REVEALED in the
  same transaction as the first reveal read.

Phase 8 H (frozen reveal contract — DOCUMENTED, NOT CHANGED): REQUIREMENTS
40.12 pins ``GET /playthroughs/{playthrough_id}/reveal`` as the reveal
endpoint, and Phase 7 QA locked its idempotent ACCUSED->REVEALED transition
(Phase7 E/F) plus the frontend reveal flow against it. Phase 8 H explicitly
allows keeping the frozen behavior instead of expanding scope: a
state-changing POST would violate the pinned contract for zero product value,
so the documented behavior stands — a side-effect-bearing GET that performs
the ACCUSED->REVEALED transition idempotently, with the very first read
persisting the transition in the same store transaction. NO ENDPOINT CHANGE.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.errors import http_error, map_service_error
from app.auth import require_playthrough
from app.models.playthroughs import Playthrough
from app.schemas.accusation import AccusationRequest, AccusationResponseDTO, RevealResponseDTO
from app.schemas.cases import PublicCaseResponse
from app.schemas.playthroughs import PlaythroughResponseDTO
from app.services.accusation import (
    AccusationConflictError,
    AccusationNotFoundError,
    AccusationService,
    AccusationValidationError,
    RevealNotAvailableError,
)

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
    summary="Public case of the PINNED published version (player-known only)",
    description=(
        "Requires the playthrough's own playthroughAccessToken. The payload "
        "is resolved ONLY from the published_versions row for the pinned "
        "(caseId, caseVersion) — publishing v2 never changes this response. "
        "Phase 20 (PD-SEC-01): this PLAYTHROUGH-scoped DTO exposes evidence "
        "ONLY once the player has actually discovered it — a fresh playthrough "
        "carries an EMPTY evidence list and null world-graph evidenceIds, so "
        "no undiscovered evidence id/title/description is ever revealed "
        "pre-discovery. (The creator-scoped GET /cases/{id} dossier keeps the "
        "full REQUIREMENTS 41.2 evidence list.)"
    ),
)
def get_playthrough_public_case(
    playthrough_id: str,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> PublicCaseResponse:
    service = request.app.state.generation_service
    # Player-knowledge snapshot (empty for a fresh playthrough): the ONLY
    # evidence allowed on the playthrough-scoped public-case is what the
    # player has already discovered (PD-SEC-01).
    snapshot = request.app.state.store.snapshot_player_knowledge(row.playthrough_id)
    try:
        public_case = service.get_public_case(
            row.case_id,
            row.case_version,
            discovered=set(snapshot.discovered),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None
    if public_case is None:
        raise http_error(404, "NOT_FOUND", "Not found")
    return PublicCaseResponse(**public_case)


# --------------------------------------------------------------------------- #
# Phase 7 — accusation (REQUIREMENTS 40.10/40.11) and reveal (40.12)
# --------------------------------------------------------------------------- #


class _DuplicateJsonKey(ValueError):
    """Internal marker raised by the ``object_pairs_hook`` when a WIRE body
    repeats a JSON key anywhere in the tree (DEF-052)."""


def reject_duplicate_json_keys(raw: bytes) -> bool:
    """DEF-052 / Phase7 B ("Reject: duplicate/ambiguous fields"): True when
    ``raw`` is valid JSON containing NO repeated key at ANY nesting depth;
    False when a duplicate key is found anywhere in the tree. A body that is
    not parseable JSON returns True — the route's normal Pydantic parse then
    answers the standard 422 envelope (the wire-level ambiguity we pre-reject
    here is only the duplicate-key case)."""
    if not raw:
        return True

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        for key, _value in pairs:
            if key in seen:
                raise _DuplicateJsonKey()
            seen.add(key)
        return dict(pairs)

    try:
        json.loads(raw, object_pairs_hook=_pairs)
    except _DuplicateJsonKey:
        return False
    except (ValueError, TypeError):  # not JSON at all -> let Pydantic 422
        return True
    return True


def _accusation_service(request: Request) -> AccusationService:
    """One service over the app's store + clock (no per-request state)."""
    return AccusationService(
        store=request.app.state.store,
        clock=request.app.state.clock,
    )


@router.post(
    "/{playthrough_id}/accusation",
    response_model=AccusationResponseDTO,
    summary="Submit the authoritative accusation (frozen typed contract)",
    description=(
        "Requires the playthrough's own playthroughAccessToken. The ids must "
        "belong to the PINNED CaseVersion's published candidate universes; "
        "crimeTime is a full ISO-8601-with-offset timestamp or a bare 24h "
        "time-of-day (anchored to the canonical crime date + offset at "
        "evaluation time, DEC-003). The first accepted accusation is frozen; a "
        "later/concurrent one answers 409 CASE_ALREADY_SUBMITTED. The response "
        "contains NO truth and NO per-dimension correctness. A wire body with a "
        "repeated JSON key (duplicate/ambiguous field) answers 422."
    ),
)
async def submit_accusation(
    playthrough_id: str,
    body: AccusationRequest,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> AccusationResponseDTO:
    # DEF-052: reject a duplicate/ambiguous WIRE-level JSON key BEFORE any
    # parsing side effect can reach the service. The body parameter has
    # already been parsed by FastAPI from the CACHED request bytes; reading
    # the same cached bytes and scanning the whole tree for repeated keys is
    # cheap and rejects both key orders (json.loads keeps only the LAST value,
    # so the winner would otherwise silently depend on the client's ordering).
    raw = await request.body()
    if not reject_duplicate_json_keys(raw):
        # Generic envelope: no internals, no duplicate-key details.
        raise http_error(422, "VALIDATION_ERROR", "Request validation failed")
    service = _accusation_service(request)
    try:
        result = service.submit_accusation(row, body)
    except HTTPException:
        raise
    except AccusationValidationError:
        # Generic 422 envelope: no existence leak, no canonical material.
        raise http_error(422, "VALIDATION_ERROR", "Accusation is invalid") from None
    except AccusationConflictError:
        raise http_error(
            409, "CASE_ALREADY_SUBMITTED", "An accusation was already submitted"
        ) from None
    except AccusationNotFoundError:
        raise http_error(404, "NOT_FOUND", "Not found") from None
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None
    return AccusationResponseDTO(**result)


@router.get(
    "/{playthrough_id}/reveal",
    response_model=RevealResponseDTO,
    summary="Reveal the canonical truth (explicit allowlist DTO)",
    description=(
        "Requires the playthrough's own playthroughAccessToken. Allowed only "
        "from {ACCUSED, REVEALED} (else 403 REVEAL_NOT_AVAILABLE). Idempotent: "
        "repeat reveals return the structurally identical DTO; the first read "
        "persists ACCUSED -> REVEALED in the same store transaction. The DTO "
        "carries NO solver-proof/prompt/token/verifier internals."
    ),
)
def get_reveal(
    playthrough_id: str,
    request: Request,
    row: Annotated[Playthrough, Depends(require_playthrough)] = None,
) -> RevealResponseDTO:
    service = _accusation_service(request)
    try:
        result = service.get_reveal(row)
    except HTTPException:
        raise
    except RevealNotAvailableError:
        raise http_error(403, "REVEAL_NOT_AVAILABLE", "Reveal is not available yet") from None
    except AccusationNotFoundError:
        raise http_error(404, "NOT_FOUND", "Not found") from None
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None
    return RevealResponseDTO(**result)