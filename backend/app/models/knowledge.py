"""PlayerKnowledge — durable per-playthrough player-observable state
(REQUIREMENTS 36/41.3, Phase6 A).

One row per playthrough, permanently scoped to that playthrough's EXACT
``(case_id, case_version)`` pinned tuple (REQUIREMENTS 7.1/40.5): the values
are written from the playthrough row ONLY — never from the request and never
from "latest".

State is stored as bounded JSON text columns (set semantics):

- ``discovered_json`` — evidence IDs the player has discovered;
- ``read_json``       — evidence IDs the player has inspected/read;
- ``visited_json``    — location IDs the player has visited through valid
  interactions;
- ``notes_json``      — small server-owned public notes map (currently the
  stable ``opened_at`` epoch per evidence ID so repeat reads return the exact
  same DTO; REQUIREMENTS 36 "public notes/facts unlocked through evidence").

PlayerKnowledge MUST NOT contain CaseTruth, SolutionProof, hidden canonical
answers, undiscovered evidence content or internal validation material
(REQUIREMENTS 41.3).
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Compatible with ``playthroughs.playthrough_id`` (String(160)).
_KNOWLEDGE_ID_LEN = 160


class PlayerKnowledge(Base):
    """The ONLY persistent player state of exactly one playthrough."""

    __tablename__ = "player_knowledge"

    playthrough_id: Mapped[str] = mapped_column(
        String(_KNOWLEDGE_ID_LEN),
        ForeignKey("playthroughs.playthrough_id", ondelete="CASCADE"),
        primary_key=True,
    )
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    discovered_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    read_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    visited_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    notes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("ix_player_knowledge_case_case_version", "case_id", "case_version"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<PlayerKnowledge playthrough={self.playthrough_id!r} "
            f"pinned=({self.case_id!r}, v{self.case_version})>"
        )