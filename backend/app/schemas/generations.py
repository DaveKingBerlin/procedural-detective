"""Generation progress DTO (REQUIREMENTS 40.3 "Generation Progress", Phase5 F.3).

Only the sanitized durable snapshot is ever returned: status / progress /
stage, read from the database. NO truth, NO proof, NO diagnostics, NO prompt,
NO provider output, NO tokens.
"""

from __future__ import annotations

from pydantic import BaseModel


class GenerationProgressDTO(BaseModel):
    """GET /api/v1/generations/{generationId} -> 200."""

    caseId: str
    generationId: str
    status: str
    progress: int
    stage: str | None = None
    failureCode: str | None = None
