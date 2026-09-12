"""Playthrough — permanently pinned to exactly (case_id, case_version)
(REQUIREMENTS 7.1/40.5-40.7, Phase5 F/G).

The playthrough row stores BOTH coordinates of the exact published
CaseVersion it was created against. Resolution NEVER follows "latest": every
read for a playthrough resolves the payload exclusively from the
``published_versions`` row for the pinned tuple.

State vocabulary: CREATED / PLAYING (ACCUSED / REVEALED are reserved for
later phases per REQUIREMENTS 40.6). Phase 5 lands new playthroughs directly
in PLAYING (the Phase 5 creation contract returns ``status: "PLAYING"``).
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.quota import VERIFIER_LEN


class Playthrough(Base):
    """One player playthrough bound to one immutable published version."""

    __tablename__ = "playthroughs"

    playthrough_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("cases.case_id"), nullable=False, index=True
    )
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    token_verifier: Mapped[str] = mapped_column(
        String(VERIFIER_LEN), nullable=False, unique=True
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<Playthrough playthrough_id={self.playthrough_id!r} "
            f"pinned=({self.case_id!r}, v{self.case_version}) "
            f"state={self.state!r}>"
        )