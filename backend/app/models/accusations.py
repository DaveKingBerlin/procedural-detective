"""Accusation — the FIRST authoritative accusation of one playthrough
(REQUIREMENTS 40.10/40.11, Phase7 A/B/H, migration 0004).

Exactly ONE row per playthrough (``playthrough_id`` is the primary key): the
first accepted accusation is frozen and authoritative forever. The row stores
the RAW submitted strings (including the raw ``crime_time`` — a full
ISO-8601-with-offset string OR a bare ``HH:MM[:SS]`` time-of-day; the bare form
is anchored to the canonical crime date + canonical timezone offset at
EVALUATION time per DEC-003, never at write time).

Immutability (Phase7 H / test N4):

- the store exposes NO update path for the row (there is no
  ``update_accusation`` anywhere);
- a SECOND insert for the same playthrough is refused by the
  compare-and-set write (``insert_accusation_if_unaccused``) AND by the
  primary key itself;
- as defense-in-depth, migration 0004 installs SQLite BEFORE UPDATE /
  BEFORE DELETE triggers identical in spirit to the ``published_versions``
  immutability triggers: even a raw SQL UPDATE/DELETE is aborted at the
  database level. The row can only be created once and only ever read.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Bounded accusation-field lengths (REQUIREMENTS 40.10 / Phase7 B contract).
_ACCUSATION_ID_LEN = 256
_CRIME_TIME_LEN = 64


class Accusation(Base):
    """The immutable first accusation of exactly one playthrough."""

    __tablename__ = "accusations"

    playthrough_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("playthroughs.playthrough_id", ondelete="CASCADE"),
        primary_key=True,
    )
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    murderer_id: Mapped[str] = mapped_column(String(_ACCUSATION_ID_LEN), nullable=False)
    motive_id: Mapped[str] = mapped_column(String(_ACCUSATION_ID_LEN), nullable=False)
    weapon_id: Mapped[str] = mapped_column(String(_ACCUSATION_ID_LEN), nullable=False)
    # The RAW submitted string (ISO-8601-with-offset or "HH:MM[:SS]"). Never
    # the evaluated/normalized tick: evaluation is derived at reveal time.
    crime_time: Mapped[str] = mapped_column(String(_CRIME_TIME_LEN), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("ix_accusations_case_case_version", "case_id", "case_version"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<Accusation playthrough={self.playthrough_id!r} "
            f"pinned=({self.case_id!r}, v{self.case_version})>"
        )