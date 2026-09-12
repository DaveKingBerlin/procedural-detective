"""Playthrough DTOs (REQUIREMENTS 40.5/40.6, Phase5 F.5/F.6).

A playthrough is permanently pinned to exactly ``(caseId, caseVersion)``;
these DTOs expose only the pin, the sanitized lifecycle state and time
stamps. Never the playthrough token (except the single creation-time field in
``PlaythroughCreatedDTO``), never the token verifier, never CaseTruth/proof.
"""

from __future__ import annotations

from pydantic import BaseModel


class PlaythroughCreatedDTO(BaseModel):
    """POST /api/v1/cases/{caseId}/versions/{caseVersion}/playthroughs -> 201.

    ``playthroughAccessToken`` appears exactly once, at creation.
    """

    playthroughId: str
    caseId: str
    caseVersion: int
    playthroughAccessToken: str
    status: str


class PlaythroughResponseDTO(BaseModel):
    """GET /api/v1/playthroughs/{playthroughId} -> 200 (bootstrap; Phase 6).

    ``status`` is always derived from the persisted row, never from the
    client.
    """

    playthroughId: str
    caseId: str
    caseVersion: int
    status: str
    createdAt: float
    expiresAt: float