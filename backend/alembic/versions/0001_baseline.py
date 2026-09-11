"""Phase 2 baseline migration — intentionally empty.

Revision ID: 0001
Revises:
Create Date: 2026-09-11

The Phase 2 contract contains no domain tables (no CaseTruth, cases or
playthroughs yet). Future phases append numbered revisions on top of this
baseline; the explicit Alembic revision chain is the immutability and
versioning mechanism — released revisions are never edited after release.
"""

import sqlalchemy as sa  # noqa: F401  (kept for template parity)

from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Baseline: no tables to create yet."""


def downgrade() -> None:
    """Reverse of upgrade: nothing to drop at the baseline."""