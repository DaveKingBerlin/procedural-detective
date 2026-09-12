"""SQLAlchemy 2.0 declarative base shared by every persisted aggregate.

Phase 5 persistence is relational-first (normalized columns); the ONLY
bounded JSON column is ``published_versions.payload_json`` (the frozen
published CaseVersion payload). The declared metadata is intentionally NOT
connected to Alembic ``env.py`` (the Phase 2 contract keeps ``target_metadata
=None``; revisions declare their own tables explicitly) — ORM metadata is a
typing/runtime convenience, the migration chain is the schema authority.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """One declarative base for all Phase 5 ORM entities."""

    pass