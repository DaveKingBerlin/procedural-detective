"""Case + CaseVersion aggregates (REQUIREMENTS 7.1/7.3, Phase5 B).

``Case`` is the stable creator-owned logical case; ``CaseVersion`` is one
attempt of the §7.3 generation state machine. A published CaseVersion is
immutable: its frozen payload lives in ``published_versions`` (insert-only)
and its state is terminal, so no transition can mutate it.
"""

from __future__ import annotations

from datetime import datetime  # noqa: F401  (kept for column type parity)

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ISO = str  # epoch-float aliases for readability of the schema


class Case(Base):
    """The stable owner-scoped logical case (one or many versions)."""

    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    quota_session_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )  # FK enforced by the migration (PRAGMA foreign_keys=ON at runtime)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    difficulty: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Monotonic version allocator: atomic UPDATE ... RETURNING next_version.
    next_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Case case_id={self.case_id!r} next_version={self.next_version}>"


class CaseVersion(Base):
    """One row of the §7.3 CaseVersion state machine."""

    __tablename__ = "case_versions"

    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    state_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Per-case monotonic public generation label (e.g. GEN-1, GEN-2).
    generation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<CaseVersion case_id={self.case_id!r} version={self.version} "
            f"state={self.state!r}>"
        )