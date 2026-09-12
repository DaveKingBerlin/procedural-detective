"""Anonymous quota session — the durable, stable quota identity (REQUIREMENTS
32.9/40.1, Phase5 A).

Authorization credentials and quota identity are separate concepts: this row
carries ONLY the quota identity (``session_id``) plus the verifier of the
``anonymousSessionToken`` that authorizes it. Case-specific creator
credentials never reset or replace this identity.
"""

from __future__ import annotations

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# sha256(token).hexdigest() -> exactly 64 lowercase hex chars.
VERIFIER_LEN = 64


class AnonymousQuotaSession(Base):
    """One anonymous quota session (quota window + token verifier)."""

    __tablename__ = "anonymous_quota_sessions"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    token_verifier: Mapped[str] = mapped_column(
        String(VERIFIER_LEN), nullable=False, unique=True
    )
    # UTC epoch seconds (float) — consistent time base across the schema.
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    quota_window_end: Mapped[float] = mapped_column(Float, nullable=False)
    generations_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<AnonymousQuotaSession session_id={self.session_id!r} "
            f"window_end={self.quota_window_end!r} gens={self.generations_count}>"
        )