"""Phase 7 persistence model — accusations (Accusation).

Revision ID: 0004
Revises: 0003 (Phase 6 persistence model)
Create Date: 2026-09-13

Adds the per-playthrough authoritative accusation table (REQUIREMENTS 40.10 /
40.11, Phase7 A/B/H):

- ``accusations`` — exactly ONE row per playthrough, permanently scoped to
  the playthrough's pinned ``(case_id, case_version)``:

    playthrough_id  TEXT PK, FK -> playthroughs.playthrough_id ON DELETE
                    CASCADE (the row IS the authoritative first accusation),
    case_id         TEXT NOT NULL  (mirrored from the playthrough row),
    case_version    INTEGER NOT NULL,
    murderer_id     TEXT NOT NULL (raw submitted id),
    motive_id       TEXT NOT NULL (raw submitted id),
    weapon_id       TEXT NOT NULL (raw submitted id),
    crime_time      TEXT NOT NULL (RAW submitted string — full ISO-8601-with
                    offset OR bare "HH:MM[:SS]"; evaluated at reveal time),
    created_at      FLOAT NOT NULL (epoch seconds).

  An index on (case_id, case_version) supports version-scoped lookups /
  cross-playthrough isolation checks. ``PRAGMA foreign_keys=ON`` at runtime
  (installed by the store engine) makes the FK + ON DELETE CASCADE real.

Immutability (Phase7 H / N4): the accusation of a playthrough is frozen on
first write — the row can never be updated or deleted. The store exposes NO
update path; as defense-in-depth the migration registers SQLite BEFORE UPDATE
/ BEFORE DELETE triggers (mirroring the published_versions immutability
triggers from 0002) so even a raw SQL mutation is aborted at the database
level.

The migration is purely additive: downgrade drops the triggers, the table and
its index, restoring the 0003 schema exactly.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accusations",
        sa.Column("playthrough_id", sa.String(160), primary_key=True, nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("murderer_id", sa.String(256), nullable=False),
        sa.Column("motive_id", sa.String(256), nullable=False),
        sa.Column("weapon_id", sa.String(256), nullable=False),
        sa.Column("crime_time", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["playthrough_id"],
            ["playthroughs.playthrough_id"],
            name="fk_accusations_playthrough_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_accusations_case_case_version",
        "accusations",
        ["case_id", "case_version"],
        unique=False,
    )
    # Immutability triggers: the first accusation can never be modified or
    # deleted (Phase7 H / N4; same pattern as published_versions in 0002).
    op.execute(
        """
        CREATE TRIGGER accusations_no_update
        BEFORE UPDATE ON accusations
        BEGIN
            SELECT RAISE(ABORT, 'accusations is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER accusations_no_delete
        BEFORE DELETE ON accusations
        BEGIN
            SELECT RAISE(ABORT, 'accusations is immutable');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS accusations_no_delete")
    op.execute("DROP TRIGGER IF EXISTS accusations_no_update")
    op.drop_index("ix_accusations_case_case_version", table_name="accusations")
    op.drop_table("accusations")