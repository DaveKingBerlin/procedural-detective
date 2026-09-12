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
    StoreError,
    VersionAllocationError,
    VersionNotFoundError,
    VersionNotPublishedError,
)
from app.services.generation import (
    AdmissionDeniedError,
    GenerationServiceError,
    IdentifierConflict,
    PromptValidationError,
    UnknownCaseError,
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
    if isinstance(exc, (StoreError, GenerationServiceError)):
        # Sanitized generic 500: never leaks the underlying message.
        return http_error(500, "INTERNAL_ERROR", "Internal server error")
    return http_error(500, "INTERNAL_ERROR", "Internal server error")


__all__ = ["http_error", "map_service_error"]