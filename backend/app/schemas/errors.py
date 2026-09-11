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


class ErrorResponse(BaseModel):
    """The exact non-2xx response body: ``{"error": {...}}``."""

    error: ErrorBody