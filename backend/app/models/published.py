"""Published immutable CaseVersion payload row (REQUIREMENTS 7.4/7.5, Phase5 A).

One immutable row per published version: the PK (case_id, case_version) is
the collision backstop for duplicate concurrent publication, and SQLite
BEFORE UPDATE / BEFORE DELETE triggers (created by migration 0002) make the
immutability a database-level guarantee — the row can never be modified or
removed once inserted.
"""

from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PublishedVersion(Base):
    """The frozen public payload of exactly one published CaseVersion."""

    __tablename__ = "published_versions"

    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Bounded JSON (schemaVersion 1): the frozen PublishedCaseVersion payload.
    # Bounded by the generation input/output bounds (REQUIREMENTS 32.8).
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<PublishedVersion case=({self.case_id!r}, v{self.case_version}) "
            f"published_at={self.published_at!r}>"
        )