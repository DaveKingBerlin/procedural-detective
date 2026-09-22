"""Shared API error translation for the Phase 5 routers.

Every service/store domain error is translated into the structured envelope
``{"error": {"code", "message", "details"}}`` with the status code Phase5
mandates. Messages are SANITIZED — no stack traces, no SQL, no table/column
names, no file paths, no secrets (Phase5 J/K).

BOUNDARY: this module imports ONLY ``app.services`` / ``app.persistence``
exceptions — never ``app.domain`` / ``app.validation`` / ``app.generation``
(test_boundaries.py contract). The service layer already translated Phase 4
errors (AdmissionDenied, PromptError) into service-level exceptions.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.persistence.store import (
    DuplicateAttempt,
    DuplicateCase,
    DuplicateCaseVersion,
    DuplicateCredential,
    DuplicatePlaythrough,
    DuplicatePublication,
    DuplicateSession,
    PlaythroughCapacityError,
    StoreError,
    VersionAllocationError,
    VersionNotFoundError,
    VersionNotPublishedError,
)
from app.services.generation import (
    AdmissionDeniedError,
    EnvironmentHintError,
    GenerationServiceError,
    IdentifierConflict,
    PromptValidationError,
    UnknownCaseError,
)
from app.services.investigation import (
    EvidenceNotDiscoveredError,
    InteractionNotAllowedError,
    InvestigationError,
    InvestigationNotFoundError,
    InvestigationStateError,
)


def http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "details": None},
    )


def map_service_error(exc: Exception) -> HTTPException:
    """Translate a service/store error into the envelope (never raw text)."""
    if isinstance(exc, AdmissionDeniedError):
        # SANITIZED: the denial reason may carry quota internals — never echo.
        return http_error(429, "ADMISSION_DENIED", "Generation capacity exhausted")
    if isinstance(exc, PromptValidationError):
        # Never echo the prompt or the offending value back.
        return http_error(422, "PROMPT_ERROR", "Prompt is invalid or exceeds the limit")
    if isinstance(exc, EnvironmentHintError):
        # Never echo the offending environment value back.
        return http_error(
            422, "ENVIRONMENT_ERROR", "Environment hint is invalid or exceeds the limit"
        )
    if isinstance(exc, (DuplicatePublication, DuplicateCaseVersion, DuplicateAttempt)):
        return http_error(409, "CASE_VERSION_CONFLICT", "Case version conflict")
    if isinstance(
        exc, (DuplicateCase, DuplicateCredential, DuplicatePlaythrough, DuplicateSession)
    ):
        return http_error(409, "CONFLICT", "Identifier already exists")
    if isinstance(exc, IdentifierConflict):
        return http_error(409, "CONFLICT", "Identifier conflict")
    if isinstance(exc, VersionNotPublishedError):
        return http_error(409, "VERSION_NOT_PUBLISHED", "Case version is not published")
    if isinstance(exc, VersionNotFoundError):
        return http_error(404, "NOT_FOUND", "Not found")
    if isinstance(exc, (VersionAllocationError, UnknownCaseError)):
        return http_error(404, "NOT_FOUND", "Not found")
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
    if isinstance(exc, PlaythroughCapacityError):
        # Phase 21 F-02 — per-case playthrough caps reached. SANITIZED: the
        # ``kind`` ("active"/"retained") and every internal counter are never
        # echoed; both deny with the same safe 429 envelope. The caller
        # created NO row and issued NO token (rejection happens inside the
        # create transaction).
        return http_error(
            429,
            "PLAYTHROUGH_LIMIT_EXCEEDED",
            "Playthrough limit reached for this case",
        )
    if isinstance(exc, InvestigationError):
        return http_error(500, "INTERNAL_ERROR", "Internal server error")
    if isinstance(exc, (StoreError, GenerationServiceError)):
        # Sanitized generic 500: never leaks the underlying message.
        return http_error(500, "INTERNAL_ERROR", "Internal server error")
    return http_error(500, "INTERNAL_ERROR", "Internal server error")


__all__ = ["http_error", "map_service_error"]