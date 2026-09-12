"""Phase 5 persistence model — cases, auth stores, attempts, playthroughs.

Revision ID: 0002
Revises: 0001 (Phase 2 baseline)
Create Date: 2026-09-12

Adds the seven normalized relational tables behind the private versioned case
model (REQUIREMENTS 7.x / 32.9-32.11 / 40.x / 42):

- anonymous_quota_sessions  — durable quota identity + session token verifier
- cases                     — stable creator-owned logical case
- creator_credentials       — case-scoped creator credential verifiers
- case_versions             — the §7.3 generation state machine rows
- generation_attempts       — durable private generation status/progress
- published_versions        — IMMUTABLE frozen published payload rows
- playthroughs              — playthroughs pinned to exactly (case, version)

Constraint/immutability notes:

- ``published_versions`` carries SQLite BEFORE UPDATE / BEFORE DELETE
  triggers that RAISE: the row can never be modified or deleted once inserted
  (Phase5 INVARIANT 2). The triggers are dropped again on downgrade.
- Every token store column is UNIQUE and stores only the sha256 verifier.
- ``generation_attempts`` UNIQUE (case_id, case_version) enforces exactly one
  authoritative attempt per version (REQUIREMENTS 32.1/32.13).
- Foreign keys are enforced at runtime by the store engine's
  ``PRAGMA foreign_keys=ON`` (and declared here for every cross-table ref).
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) anonymous quota sessions (durable quota identity) ------------------
    op.create_table(
        "anonymous_quota_sessions",
        sa.Column("session_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("token_verifier", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("quota_window_end", sa.Float(), nullable=False),
        sa.Column("generations_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_anonymous_quota_sessions_token_verifier",
        "anonymous_quota_sessions",
        ["token_verifier"],
        unique=True,
    )

    # 2) cases ------------------------------------------------------------
    op.create_table(
        "cases",
        sa.Column("case_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("quota_session_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("difficulty", sa.String(32), nullable=True),
        sa.Column("next_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["quota_session_id"],
            ["anonymous_quota_sessions.session_id"],
            name="fk_cases_quota_session_id",
        ),
    )
    op.create_index(
        "ix_cases_quota_session_id", "cases", ["quota_session_id"], unique=False
    )

    # 3) creator credentials (per-case access credential verifiers) ---------
    op.create_table(
        "creator_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("token_verifier", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_creator_credentials_case_id"
        ),
    )
    op.create_index(
        "ix_creator_credentials_case_id", "creator_credentials", ["case_id"], unique=False
    )
    op.create_index(
        "ix_creator_credentials_token_verifier",
        "creator_credentials",
        ["token_verifier"],
        unique=True,
    )

    # 4) case_versions — the §7.3 state machine rows (composite-PK backstop) -
    op.create_table(
        "case_versions",
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("state_reason", sa.String(500), nullable=True),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("case_id", "version", name="pk_case_versions"),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_case_versions_case_id"
        ),
    )

    # 5) generation_attempts — one authoritative attempt per version --------
    op.create_table(
        "generation_attempts",
        sa.Column("attempt_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(24), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_generation_attempts_case_id"
        ),
        sa.UniqueConstraint(
            "case_id", "case_version", name="uq_generation_attempts_case_version"
        ),
    )

    # 6) published_versions — IMMUTABLE frozen payload rows ------------------
    op.create_table(
        "published_versions",
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("published_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("case_id", "case_version", name="pk_published_versions"),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_published_versions_case_id"
        ),
    )
    op.execute(
        """
        CREATE TRIGGER published_versions_no_update
        BEFORE UPDATE ON published_versions
        BEGIN
            SELECT RAISE(ABORT, 'published_versions is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER published_versions_no_delete
        BEFORE DELETE ON published_versions
        BEGIN
            SELECT RAISE(ABORT, 'published_versions is immutable');
        END
        """
    )

    # 7) playthroughs — permanently pinned (case_id, case_version) -----------
    op.create_table(
        "playthroughs",
        sa.Column("playthrough_id", sa.String(160), primary_key=True, nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("token_verifier", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_playthroughs_case_id"
        ),
    )
    op.create_index("ix_playthroughs_case_id", "playthroughs", ["case_id"], unique=False)
    op.create_index(
        "ix_playthroughs_case_case_version",
        "playthroughs",
        ["case_id", "case_version"],
        unique=False,
    )
    op.create_index(
        "ix_playthroughs_token_verifier",
        "playthroughs",
        ["token_verifier"],
        unique=True,
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS published_versions_no_delete")
    op.execute("DROP TRIGGER IF EXISTS published_versions_no_update")
    op.drop_table("playthroughs")
    op.drop_table("published_versions")
    op.drop_table("generation_attempts")
    op.drop_table("case_versions")
    op.drop_table("creator_credentials")
    op.drop_table("cases")
    op.drop_table("anonymous_quota_sessions")