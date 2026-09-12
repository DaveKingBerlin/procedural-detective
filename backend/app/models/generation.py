"""Generation attempt — durable status of one generation (Phase5 A/E).

The attempt row carries the sanitized ``{status, stage, progress}`` snapshot
the API reports (REQUIREMENTS 40.3 "Generation Progress"), so progress
survives application restart. Internal generation material (drafts, provider
output, diagnostics, truth) is NEVER persisted here.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GenerationAttempt(Base):
    """Exactly one generation attempt per (case_id, case_version)."""

    __tablename__ = "generation_attempts"

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("cases.case_id"), nullable=False
    )
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(24), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<GenerationAttempt attempt_id={self.attempt_id!r} "
            f"case=({self.case_id!r}, v{self.case_version}) "
            f"status={self.status!r} progress={self.progress}>"
        )