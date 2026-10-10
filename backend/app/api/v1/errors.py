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

from typing import Any

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
    InvalidDemoCaseError,
    InvalidFrontierConfigError,
    InvalidGenerationProviderError,
    InvalidOllamaModelError,
    PromptValidationError,
    ProviderUnavailableError,
    UnknownCaseError,
)
from app.services.investigation import (
    EvidenceNotDiscoveredError,
    InteractionNotAllowedError,
    InvestigationError,
    InvestigationNotFoundError,
    InvestigationStateError,
)
from app.services.publication import PublicRoleTruthLeak


def http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    reasonCode: str | None = None,
) -> HTTPException:
    """Build the sanitized error envelope ``{"error": {code,message,details}}``.

    ``reasonCode`` is Phase36's optional closed admission-reason token; it is
    OMITTED (not null) whenever absent so every non-admission envelope stays
    byte-identical to its pre-Phase36 shape (backward compatible for every
    existing call site and every exact-body test).
    """
    detail: dict[str, Any] = {"code": code, "message": message, "details": None}
    if reasonCode is not None:
        detail["reasonCode"] = reasonCode
    return HTTPException(status_code=status_code, detail=detail)


def map_service_error(exc: Exception) -> HTTPException:
    """Translate a service/store error into the envelope (never raw text)."""
    if isinstance(exc, AdmissionDeniedError):
        # SANITIZED: the denial reason may carry quota internals — never echo.
        # The ONLY admission detail surfaced is the CLOSED safe ``reasonCode``
        # (Phase36 §10/§11); the message stays the sanitized generic.
        return http_error(
            429,
            "ADMISSION_DENIED",
            "Generation capacity exhausted",
            reasonCode=exc.reason_code,
        )
    if isinstance(exc, InvalidFrontierConfigError):
        # Phase 30 — an invalid/missing BYOK frontier provider/key/model, or a
        # frontier block on a non-frontier selection. NEVER echo the offending
        # value (it may embed URL/header/secret material).
        return http_error(
            400,
            "INVALID_FRONTIER_CONFIG",
            "The frontier provider configuration is invalid or unsupported",
        )
    if isinstance(exc, PromptValidationError):
        # Never echo the prompt or the offending value back.
        return http_error(422, "PROMPT_ERROR", "Prompt is invalid or exceeds the limit")
    if isinstance(exc, EnvironmentHintError):
        # Never echo the offending environment value back.
        return http_error(
            422, "ENVIRONMENT_ERROR", "Environment hint is invalid or exceeds the limit"
        )
    if isinstance(exc, InvalidGenerationProviderError):
        # Phase 25 — unknown browser-supplied provider/transport id. NEVER echo
        # the offending value (it may embed hostile/URL material).
        return http_error(
            400,
            "INVALID_GENERATION_PROVIDER",
            "Unknown or invalid generation provider selection",
        )
    if isinstance(exc, ProviderUnavailableError):
        # Phase 25 — an EXPLICITLY requested but not-configured provider.
        # Safe canonical PROVIDER_UNAVAILABLE (never a silent fallback, never a
        # config/URL/secret detail).
        return http_error(
            400,
            "PROVIDER_UNAVAILABLE",
            "The requested generation provider is not available",
        )
    if isinstance(exc, InvalidOllamaModelError):
        # Phase 25 — a user-supplied Ollama model string failed the central
        # validator. The offending value is never echoed.
        return http_error(
            400,
            "INVALID_OLLAMA_MODEL",
            "The Ollama model selection is invalid or unsupported",
        )
    if isinstance(exc, InvalidDemoCaseError):
        # Phase 28 — an unknown demoCaseId, or a demoCaseId on a non-fake
        # provider. The offending value is NEVER echoed (it may embed hostile
        # material) and no internal registry detail is exposed.
        return http_error(
            400,
            "INVALID_DEMO_CASE",
            "Unknown or invalid demo case selection",
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
    if isinstance(exc, PublicRoleTruthLeak):
        # Phase35 DEF-078 — a stored ``published_versions`` row carries a
        # public person role OUTSIDE the closed pre-reveal vocabulary
        # (e.g. a legacy ``role="murderer"`` or a deleted role key). The
        # public DTO must NEVER emit that role, but a pre-Phase35 row must
        # also never become a permanently silent generic 500 — answer a
        # typed, NON-500, actionable envelope instead. The message is
        # SANITIZED: the offending role token itself (the truth-bearing
        # string) and the person id are NEVER echoed.
        return http_error(
            409,
            "PUBLIC_ROLE_TRUTH_LEAK",
            "Stored case data violates the closed public-role contract",
        )
    if isinstance(exc, InvestigationError):
        return http_error(500, "INTERNAL_ERROR", "Internal server error")
    if isinstance(exc, (StoreError, GenerationServiceError)):
        # Sanitized generic 500: never leaks the underlying message.
        return http_error(500, "INTERNAL_ERROR", "Internal server error")
    return http_error(500, "INTERNAL_ERROR", "Internal server error")


__all__ = ["http_error", "map_service_error"]