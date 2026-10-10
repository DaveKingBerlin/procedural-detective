"""Structured error envelope shared by every non-2xx JSON response."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """One structured error occurrence."""

    code: str = Field(..., description="SCREAMING_SNAKE error code.")
    message: str = Field(..., description="Short human-readable message.")
    details: Optional[dict[str, Any]] = Field(
        default=None, description="Optional structured diagnostic detail."
    )
    reasonCode: Optional[str] = Field(
        default=None,
        description=(
            "Optional CLOSED admission-denial reason token (Phase36 §10). "
            "Present ONLY on 429 ADMISSION_DENIED envelopes; omitted on every "
            "other error so non-admission envelopes stay byte-identical."
        ),
    )


class ErrorResponse(BaseModel):
    """The exact non-2xx response body: ``{"error": {...}}``."""

    error: ErrorBody