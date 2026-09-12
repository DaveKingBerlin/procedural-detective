"""Anonymous quota session DTOs (REQUIREMENTS 40.2, Phase5 F.1)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnonymousSessionCreatedDTO(BaseModel):
    """POST /api/v1/sessions/anonymous -> 201.

    The token appears exactly once, at creation. The opaque
    ``anonymousSessionToken`` authorizes quota identity only — it is never a
    case/playthrough credential (REQUIREMENTS 40.1).
    """

    anonymousSessionToken: str = Field(..., description="Opaque bearer token (creation-time only).")
    quotaWindowEndsAt: float = Field(..., description="UTC epoch seconds when the quota window ends.")