"""Playthrough — permanently pinned to exactly (case_id, case_version)
(REQUIREMENTS 7.1/40.5-40.7, Phase5 F/G).

The playthrough row stores BOTH coordinates of the exact published
CaseVersion it was created against. Resolution NEVER follows "latest": every
read for a playthrough resolves the payload exclusively from the
``published_versions`` row for the pinned tuple.

State vocabulary (REQUIREMENTS 40.6, Phase7 A): ``CREATED / PLAYING /
ACCUSED / REVEALED``. Phase 5 lands new playthroughs directly in PLAYING (the
Phase 5 creation contract returns ``status: "PLAYING"``). The state column is
a plain string; the transition graph is enforced by the compare-and-set write
paths in ``app.persistence.store`` (``insert_accusation_if_unaccused`` moves
only ``{CREATED, PLAYING} -> ACCUSED``; ``mark_playthrough_revealed`` moves
only ``{ACCUSED, REVEALED} -> REVEALED``). REVEALED is terminal for the
Milestone 1 gameplay loop.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.quota import VERIFIER_LEN

# The full Milestone-1 lifecycle vocabulary (REQUIREMENTS 40.6 / Phase7 A).
PLAYTHROUGH_STATES = frozenset({"CREATED", "PLAYING", "ACCUSED", "REVEALED"})

# Accusation is allowed only from these states (REQUIREMENTS 40.10 same-accept
# contract + Phase7 A "accusation allowed ONLY from {CREATED, PLAYING}").
PRE_ACCUSATION_STATES = frozenset({"CREATED", "PLAYING"})

# Reveal is allowed only from these states (REQUIREMENTS 40.12 / Phase7 E).
REVEAL_ELIGIBLE_STATES = frozenset({"ACCUSED", "REVEALED"})


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