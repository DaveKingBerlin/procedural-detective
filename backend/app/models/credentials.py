"""Creator credential — case-scoped access credential (REQUIREMENTS 40.1).

The ``creatorAccessToken`` is a case access credential, NEVER a quota
identity (REQUIREMENTS 32.9/40.3). Only its sha256 verifier is stored; the
raw token is returned to the client exactly once at case creation.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.quota import VERIFIER_LEN


class CreatorCredential(Base):
    """One creator credential granting access to exactly one ``case_id``."""

    __tablename__ = "creator_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("cases.case_id"), nullable=False, index=True
    )
    token_verifier: Mapped[str] = mapped_column(
        String(VERIFIER_LEN), nullable=False, unique=True
    )
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<CreatorCredential id={self.id!r} case_id={self.case_id!r}>"