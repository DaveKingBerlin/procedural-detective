"""Phase 22 — Bring Your Own Ollama: durable pairing + bridge-session rows.

Two SQLite-backed tables following the EXISTING Phase 5 session/token patterns
(``app.models.quota.AnonymousQuotaSession`` / ``app.models.credentials``):

- ``bridge_pairing_records`` — ONE short-lived, single-use pairing code per
  anonymous creator session. A pairing binds a bridge to the creator/session
  that requested it; it expires in seconds (``BRIDGE_PAIRING_CODE_TTL_SECONDS``),
  is consumed exactly once (``consumed_at`` CAS), and never becomes a
  long-lived bearer credential. The pairing CODE is stored ONLY as its SHA-256
  verifier (constant-time compare on use), exactly like every other bearer
  secret in this codebase; the raw code is returned to the browser once.
- ``bridge_sessions`` — ONE bridge credential bound to a creator session scope.
  The bridge session TOKEN is returned to the bridge exactly once at pairing
  and stored ONLY as its SHA-256 verifier (never in plaintext, never exposed to
  the browser). ``connection_state`` / ``connected_at`` / ``last_seen`` /
  ``expires_at`` / ``revoked_at`` track the bounded session lifetime and the
  reconnect grace (``BRIDGE_RECONNECT_GRACE_SECONDS``).

Privacy: these rows carry NO case material, NO prompts, NO model outputs, NO
client IP and NO Ollama URL — only opaque ids, verifiers and timestamps.
"""

from __future__ import annotations

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

VERIFIER_LEN = 64  # sha256(token).hexdigest()

# The closed connection-state vocabulary (bounded enum stored as text).
BRIDGE_STATE_CONNECTED = "CONNECTED"
BRIDGE_STATE_DISCONNECTED = "DISCONNECTED"
BRIDGE_STATES = frozenset({BRIDGE_STATE_CONNECTED, BRIDGE_STATE_DISCONNECTED})


class BridgePairingRecord(Base):
    """ONE single-use, expiring pairing code for a creator session."""

    __tablename__ = "bridge_pairing_records"

    pairing_session_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    # The anonymous quota session scope that requested the pairing; the bind
    # target when the bridge presents the code (Phase22 §25: the bridge is
    # authorized ONLY for the creator/session that paired it).
    session_scope: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # sha256(pairing_code).hexdigest() — the raw code is never stored.
    code_verifier: Mapped[str] = mapped_column(
        String(VERIFIER_LEN), nullable=False, unique=True
    )
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    # NULL until the code is presented; single-use is enforced by a
    # compare-and-set on this column (see Store.consume_bridge_pairing).
    consumed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The bridge session id the code bound (NULL before binding).
    bound_bridge_session_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<BridgePairingRecord pairing_session_id={self.pairing_session_id!r} "
            f"scope={self.session_scope!r} expires={self.expires_at!r} "
            f"consumed={self.consumed_at is not None}>"
        )


class BridgeSession(Base):
    """ONE bridge credential bound to a creator/session scope.

    The bridge itself is what the owner runs on their own machine; the row is
    only the server-side durable binding (identity + lifetime + model label).
    """

    __tablename__ = "bridge_sessions"

    bridge_session_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    # The creator anonymous-session scope the bridge is authorized to serve.
    session_scope: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # sha256(bridge_session_token).hexdigest() — the raw token is returned to
    # the bridge exactly once and NEVER persisted or exposed to the browser.
    token_verifier: Mapped[str] = mapped_column(
        String(VERIFIER_LEN), nullable=False, unique=True
    )
    # The pairing code that bound this session (NULL after lazy cleanup prunes
    # the consumed pairing row — purely informational).
    pairing_session_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # The model the bridge operator selected locally (sanitized safe label).
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # JSON array of capability tokens (bounded closed set; e.g.
    # ["STRUCTURED_MODEL_INFERENCE"]).
    capabilities: Mapped[str] = mapped_column(String(512), nullable=False, default="[]")
    connected_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_seen: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    connection_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BRIDGE_STATE_DISCONNECTED
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<BridgeSession bridge_session_id={self.bridge_session_id!r} "
            f"scope={self.session_scope!r} state={self.connection_state!r} "
            f"model={self.model!r}>"
        )


__all__ = [
    "BRIDGE_STATE_CONNECTED",
    "BRIDGE_STATE_DISCONNECTED",
    "BRIDGE_STATES",
    "BridgePairingRecord",
    "BridgeSession",
    "VERIFIER_LEN",
]