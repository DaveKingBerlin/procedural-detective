"""Authorization dependencies for the Phase 5 API (Phase5 C/D, REQUIREMENTS 40).

Three distinct dependencies, one per token class, each loading the verifier
from ITS OWN table — a credential of the wrong class has no row in the table
the dependency is looking in, so no class is ever accepted where another is
expected (Phase5 INVARIANT 4).

- ``require_session``            -> anonymousSessionToken (quota identity)
- ``require_creator_for_case``   -> creatorAccessToken (per-case credential)
- ``require_playthrough``        -> playthroughAccessToken (per-playthrough)

Every token comparison is constant-time (sha256 verifier + ``compare_digest``).
Existence leaks are avoided: a valid creator credential for a DIFFERENT case
answers the same generic 404 as an unknown case/version.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.auth.tokens import BearerError, parse_bearer, verifier
from app.models.credentials import CreatorCredential
from app.models.playthroughs import Playthrough
from app.models.quota import AnonymousQuotaSession

# ``request.app.state.store`` / ``request.app.state.clock`` are attached by
# create_app; tests that need deterministic expiry seed already-expired rows
# directly and keep the app on the real epoch clock.


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "UNAUTHORIZED", "message": "Request failed", "details": None},
    )


def _session_expired() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "code": "SESSION_EXPIRED",
            "message": "credential expired or unknown",
            "details": None,
        },
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": "Not found", "details": None},
    )


def _clock(request: Request):
    return request.app.state.clock


def require_session(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AnonymousQuotaSession:
    """Authorize an ``anonymousSessionToken``; return its quota session row.

    Rules: absent/malformed bearer -> 401 UNAUTHORIZED; unknown verifier ->
    401 UNAUTHORIZED; session past ``quota_window_end`` -> 401 SESSION_EXPIRED.
    """
    try:
        token = parse_bearer(authorization)
    except BearerError:
        raise _unauthorized() from None
    row = request.app.state.store.get_session_by_verifier(verifier(token))
    if row is None:
        raise _unauthorized() from None
    if _clock(request).now() >= row.quota_window_end:
        raise _session_expired() from None
    return row


def require_creator_for_case(
    request: Request,
    case_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> CreatorCredential:
    """Authorize a ``creatorAccessToken``; return the owning credential.

    - absent/malformed header            -> 401 UNAUTHORIZED
    - unknown/expired verifier           -> 401 SESSION_EXPIRED
    - valid but owned by another case    -> 404 (generic, no existence leak)
    - blank case_id                       -> 404
    """
    if not isinstance(case_id, str) or not case_id.strip() or case_id.strip() != case_id:
        raise _not_found() from None
    try:
        token = parse_bearer(authorization)
    except BearerError:
        raise _unauthorized() from None
    row = request.app.state.store.get_creator_credential_by_verifier(verifier(token))
    if row is None or _clock(request).now() >= row.expires_at:
        raise _session_expired() from None
    if row.case_id != case_id:
        # Generic 404: does not reveal whether the case exists (INVARIANT 1).
        raise _not_found() from None
    return row


def require_creator(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> CreatorCredential:
    """Authorize a creator credential WHEN the case is implied, not in the path.

    Used by GET /api/v1/generations/{generationId}: the credential itself
    determines the case (one credential per case); the generation lookup is
    then scoped by ``credential.case_id`` so a per-case generationId label can
    never resolve across cases. Unknown/expired -> 401 SESSION_EXPIRED.
    """
    try:
        token = parse_bearer(authorization)
    except BearerError:
        raise _unauthorized() from None
    row = request.app.state.store.get_creator_credential_by_verifier(verifier(token))
    if row is None or _clock(request).now() >= row.expires_at:
        raise _session_expired() from None
    return row


def require_playthrough(
    request: Request,
    playthrough_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> Playthrough:
    """Authorize a ``playthroughAccessToken`` bound to ``playthrough_id``.

    - absent/malformed header            -> 401 UNAUTHORIZED
    - unknown/expired verifier           -> 401 SESSION_EXPIRED
    - valid credential for another PT    -> 404 (playthrough A on B -> 404)
    """
    try:
        token = parse_bearer(authorization)
    except BearerError:
        raise _unauthorized() from None
    row = request.app.state.store.get_playthrough_by_verifier(verifier(token))
    if row is None or _clock(request).now() >= row.expires_at:
        raise _session_expired() from None
    if row.playthrough_id != playthrough_id:
        raise _not_found() from None
    return row


__all__ = [
    "require_creator",
    "require_creator_for_case",
    "require_playthrough",
    "require_session",
]