"""Cases router (REQUIREMENTS 40.3/40.4/40.5, Phase5 F.2/F.4/F.5 + G).

- POST /cases                              — admit + generate + durable persist
- GET  /cases/{caseId}?version=            — exact/latest PUBLISHED public case
- POST /cases/{caseId}/versions/{v}/playthroughs — exact-version pinned pin

Version bounds (DEF-045): every version intake (query or path) accepts only
``1 <= version <= 2**63 - 1`` (the signed 64-bit SQLite int domain). Anything
outside — including values with more than 19 digits such as ``2**63`` or
``10**30`` — answers the 422 VALIDATION_ERROR envelope at the route/schema
layer with ZERO sqlite interaction instead of overflowing a sqlite bound.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.api.v1.errors import map_service_error, http_error
from app.auth import require_creator_for_case, require_session
from app.auth.tokens import (
    issue_playthrough_access_token,
    verifier as token_verifier,
)
from app.models.credentials import CreatorCredential
from app.models.quota import AnonymousQuotaSession
from app.schemas.cases import (
    CaseCreateRequest,
    CaseStartedDTO,
    PublicCaseResponse,
)
from app.schemas.playthroughs import PlaythroughCreatedDTO

router = APIRouter(prefix="/cases", tags=["cases"])

# Signed-64-bit SQLite integer domain: the widest version a store bind can
# ever accept. ``le`` makes the rejection deterministic (422 envelope) with no
# database interaction whatsoever (DEF-045).
MAX_CASE_VERSION = 2**63 - 1
_VERSION_QUERY = Query(ge=1, le=MAX_CASE_VERSION)
_VERSION_PATH = Path(ge=1, le=MAX_CASE_VERSION)


@router.post(
    "",
    response_model=CaseStartedDTO,
    status_code=201,
    summary="Create / start a private case generation",
    description=(
        "Requires an anonymousSessionToken. Admission (quota + concurrency) "
        "runs BEFORE any provider call; the creatorAccessToken appears "
        "exactly once, here at creation. The case is durable in SQLite."
    ),
)
def create_case(
    body: CaseCreateRequest,
    request: Request,
    session_row: Annotated[AnonymousQuotaSession, Depends(require_session)],
) -> CaseStartedDTO:
    service = request.app.state.generation_service
    try:
        started = service.start_case_generation(
            body.prompt,
            anonymous_quota_session_id=session_row.session_id,
            difficulty=body.difficulty,
            environment=body.environment,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None
    return CaseStartedDTO(
        caseId=started.case_id,
        generationId=started.generation_id,
        generationAttemptId=started.generation_attempt_id,
        creatorAccessToken=started.creator_access_token,
        status=started.status,
    )


@router.get(
    "/{case_id}",
    response_model=PublicCaseResponse,
    summary="Get a PUBLISHED case version (public allowlist DTO)",
    description=(
        "Requires the case's creatorAccessToken. ?version selects an EXACT "
        "published version (default: latest published). Only PUBLISHED "
        "versions are ever returned; unknown versions answer 404 and "
        "exist-but-unpublished versions answer 409."
    ),
)
def get_case(
    case_id: str,
    request: Request,
    version: Annotated[int | None, _VERSION_QUERY] = None,
    credential: Annotated[CreatorCredential, Depends(require_creator_for_case)] = None,
) -> PublicCaseResponse:
    service = request.app.state.generation_service
    try:
        if version is None:
            public_case = service.get_latest_public_case(case_id)
            if public_case is None:
                raise http_error(404, "NOT_FOUND", "Not found")
            return PublicCaseResponse(**public_case)
        # Exact requested version.
        public_case = service.get_public_case(case_id, version)
        if public_case is not None:
            return PublicCaseResponse(**public_case)
        store = request.app.state.store
        version_row = store.get_case_version(case_id, version)
        if version_row is None:
            raise http_error(404, "NOT_FOUND", "Not found")
        raise http_error(
            409, "VERSION_NOT_PUBLISHED", "Case version is not published"
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None


@router.post(
    "/{case_id}/versions/{case_version}/playthroughs",
    response_model=PlaythroughCreatedDTO,
    status_code=201,
    summary="Create a playthrough pinned to an exact PUBLISHED version",
    description=(
        "Requires the case's creatorAccessToken. Resolves the EXACT "
        "(caseId, caseVersion) tuple inside one transaction; the version must "
        "exist and be PUBLISHED (404 otherwise / 409 VERSION_NOT_PUBLISHED). "
        "The playthrough is permanently pinned to that tuple. The "
        "playthroughAccessToken appears exactly once at creation."
    ),
)
def create_playthrough(
    case_id: str,
    request: Request,
    case_version: Annotated[int, _VERSION_PATH],
    credential: Annotated[CreatorCredential, Depends(require_creator_for_case)] = None,
) -> PlaythroughCreatedDTO:
    store = request.app.state.store
    settings = request.app.state.settings
    clock = request.app.state.clock
    token = issue_playthrough_access_token()
    now = float(clock.now())
    # DEF-047: the playthrough id is an independent opaque random value —
    # it shares ZERO characters with the credential and can never be used to
    # infer the playthroughAccessToken.
    playthrough_id = f"PT-{secrets.token_urlsafe(16)}"
    try:
        row = store.create_playthrough_if_published(
            playthrough_id=playthrough_id,
            case_id=case_id,
            case_version=case_version,
            token_verifier=token_verifier(token),
            state="PLAYING",
            created_at=now,
            expires_at=now + settings.playthrough_token_ttl_seconds,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - envelope everything sanitized
        raise map_service_error(exc) from None
    return PlaythroughCreatedDTO(
        playthroughId=row.playthrough_id,
        caseId=row.case_id,
        caseVersion=row.case_version,
        playthroughAccessToken=token,
        status=row.state,
    )