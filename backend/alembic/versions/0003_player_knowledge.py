"""Phase 6 persistence model — player_knowledge (PlayerKnowledge).

Revision ID: 0003
Revises: 0002 (Phase 5 persistence model)
Create Date: 2026-09-13

Adds the single per-playthrough player-observable state table
(REQUIREMENTS 36 / 41.3, Phase6 A):

- ``player_knowledge`` — exactly ONE row per playthrough, permanently scoped
  to the playthrough's pinned ``(case_id, case_version)``:

    playthrough_id  TEXT PK, FK -> playthroughs.playthrough_id ON DELETE
                    CASCADE (the row is the ONLY state of that playthrough;
                    deleting the playthrough removes its knowledge),
    case_id         TEXT NOT NULL  (mirrored from the playthrough row),
    case_version    INTEGER NOT NULL,
    discovered_json TEXT NOT NULL DEFAULT '[]' (set of evidence ids),
    read_json       TEXT NOT NULL DEFAULT '[]' (set of evidence ids),
    visited_json    TEXT NOT NULL DEFAULT '[]' (set of location ids),
    notes_json      TEXT NOT NULL DEFAULT '{}' (server-owned public notes map),
    updated_at      FLOAT NOT NULL (epoch seconds).

  An index on (case_id, case_version) supports version-scoped lookups /
  cross-playthrough isolation checks. ``PRAGMA foreign_keys=ON`` at runtime
  (installed by the store engine) makes the FK + ON DELETE CASCADE real.

The migration is purely additive: downgrade drops the table and restores the
0002 schema exactly.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_knowledge",
        sa.Column("playthrough_id", sa.String(160), primary_key=True, nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("discovered_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("read_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("visited_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("notes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["playthrough_id"],
            ["playthroughs.playthrough_id"],
            name="fk_player_knowledge_playthrough_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_player_knowledge_case_case_version",
        "player_knowledge",
        ["case_id", "case_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_player_knowledge_case_case_version", table_name="player_knowledge"
    )
    op.drop_table("player_knowledge")