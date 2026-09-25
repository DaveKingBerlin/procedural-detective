"""Phase 22 persistence model — BYO-Ollama bridge pairing + sessions.

Revision ID: 0005
Revises: 0004 (Phase 7 persistence model)
Create Date: 2026-09-25

Adds the durable tables for the "Bring Your Own Ollama" remote local-provider
bridge (Phase22 §4/§24/§25):

- ``bridge_pairing_records`` — ONE short-lived, single-use pairing code per
  anonymous creator session. The raw code is stored ONLY as its SHA-256
  verifier (64 hex chars, constant-time compare on use), bound to a
  ``session_scope`` (the creator's anonymous quota session id), with
  created_at / expires_at and a compare-and-set ``consumed_at`` column that
  enforces single-use atomically (the store's UPDATE ... WHERE consumed_at IS
  NULL is the race guard).
- ``bridge_sessions`` — ONE bridge credential bound to a creator session
  scope. The bridge session TOKEN is stored ONLY as its SHA-256 verifier
  (returned to the bridge exactly once, hashed at rest, never exposed to the
  browser). Carries the sanitized model label, capabilities JSON, connected /
  last_seen / expires_at / revoked_at and the bounded connection state.

Privacy: neither table stores case material, prompts, model outputs, client
IPs or the local Ollama URL. ``PRAGMA foreign_keys=ON`` is installed by the
store engine at runtime; the (optional) FK from ``bridge_sessions`` back to
``bridge_pairing_records`` is NOT declared as a hard FK because a pairing row
may be pruned by lazy bounded cleanup while the longer-lived bridge session
stays valid (the pairing_session_id column is informational). Session rows are
never deleted by the runtime (revoked/expired rows are retained like other
audited rows); the in-memory registry is what is bounded and lazily cleaned.

The migration is purely additive: downgrade drops both tables, restoring the
0004 schema exactly.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bridge_pairing_records",
        sa.Column("pairing_session_id", sa.String(160), primary_key=True, nullable=False),
        sa.Column("session_scope", sa.String(128), nullable=False),
        sa.Column("code_verifier", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("consumed_at", sa.Float(), nullable=True),
        sa.Column("bound_bridge_session_id", sa.String(160), nullable=True),
    )
    op.create_index(
        "ix_bridge_pairing_records_session_scope",
        "bridge_pairing_records",
        ["session_scope"],
        unique=False,
    )
    op.create_index(
        "ix_bridge_pairing_records_code_verifier",
        "bridge_pairing_records",
        ["code_verifier"],
        unique=True,
    )
    op.create_table(
        "bridge_sessions",
        sa.Column("bridge_session_id", sa.String(160), primary_key=True, nullable=False),
        sa.Column("session_scope", sa.String(128), nullable=False),
        sa.Column("token_verifier", sa.String(64), nullable=False, unique=True),
        sa.Column("pairing_session_id", sa.String(160), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("capabilities", sa.String(512), nullable=False),
        sa.Column("connected_at", sa.Float(), nullable=True),
        sa.Column("last_seen", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("connection_state", sa.String(32), nullable=False),
    )
    op.create_index(
        "ix_bridge_sessions_session_scope",
        "bridge_sessions",
        ["session_scope"],
        unique=False,
    )
    op.create_index(
        "ix_bridge_sessions_token_verifier",
        "bridge_sessions",
        ["token_verifier"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_bridge_sessions_token_verifier", table_name="bridge_sessions")
    op.drop_index("ix_bridge_sessions_session_scope", table_name="bridge_sessions")
    op.drop_table("bridge_sessions")
    op.drop_index(
        "ix_bridge_pairing_records_code_verifier",
        table_name="bridge_pairing_records",
    )
    op.drop_index(
        "ix_bridge_pairing_records_session_scope",
        table_name="bridge_pairing_records",
    )
    op.drop_table("bridge_pairing_records")