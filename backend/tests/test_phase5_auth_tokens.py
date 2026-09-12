"""Phase 5 — token primitives, entropy, verifier storage, class separation
(M2, M3, M4, M7 + requirements 40.1).

- M2  creator token issuance/verification
- M3  token entropy / basic uniqueness (>= 256 bits)
- M4  token stored only as a verifier (never recoverably in plaintext)
- M7  malformed/foreign credentials fail cleanly
- three distinct token classes with SEPARATE issuance APIs and SEPARATE
  stores — a creator token has no row in the playthrough/session stores and
  vice versa (Phase5 INVARIANT 4)
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.auth.tokens import (
    BearerError,
    MAX_BEARER_LEN,
    MIN_BEARER_LEN,
    issue_anonymous_session_token,
    issue_creator_access_token,
    issue_playthrough_access_token,
    issue_token,
    parse_bearer,
    verifier,
    verify_token,
)


def test_03_token_entropy_and_uniqueness():
    """M3: 500 tokens are all distinct; each carries >= 256 bits of entropy."""
    seen: set[str] = set()
    tokens = [issue_token() for _ in range(500)]
    assert len(set(tokens)) == 500
    for token in tokens:
        assert isinstance(token, str) and token
        # token_urlsafe(32) => 32 bytes => 256 bits of entropy.
        assert len(token.encode("utf-8")) >= 32
        entropy_bits = math.log2(64) * len(token) if all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for c in token
        ) else 8 * len(token)
        assert entropy_bits >= 256


def test_02_issuance_and_verification_roundtrip():
    """M2: a token verifies ONLY against its own verifier."""
    token = issue_token()
    digest = verifier(token)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    assert verify_token(token, digest) is True
    other = issue_token()
    assert verify_token(other, digest) is False
    assert verify_token(token, verifier(other)) is False


def test_04_token_never_stored_recoverably():
    """M4: the verifier is a one-way sha256 digest, never the token itself."""
    import hashlib

    token = issue_token()
    digest = verifier(token)
    assert digest != token
    assert digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
    # The stored representation is a non-invertible digest: the raw token is
    # not a substring of it and cannot be recovered from it.
    assert token not in digest
    assert len(digest) == 64


def test_01_three_classes_three_separate_issuance_apis():
    session = issue_anonymous_session_token()
    creator = issue_creator_access_token()
    playthrough = issue_playthrough_access_token()
    for token in (session, creator, playthrough):
        assert MIN_BEARER_LEN <= len(token) <= MAX_BEARER_LEN
    assert len({session, creator, playthrough}) == 3


def test_bounds_parse_bearer_rejections():
    """B/J: malformed/absent/oversized/unicode/control-char bearers -> 401."""
    with pytest.raises(BearerError):
        parse_bearer(None)
    with pytest.raises(BearerError):
        parse_bearer("")
    with pytest.raises(BearerError):
        parse_bearer("Basic abcdefgh")
    with pytest.raises(BearerError):
        parse_bearer("Bearer")
    with pytest.raises(BearerError):
        parse_bearer("Bearer " + "x" * (MIN_BEARER_LEN - 1))
    with pytest.raises(BearerError):
        parse_bearer("Bearer " + "x" * (MAX_BEARER_LEN + 1))
    with pytest.raises(BearerError):
        parse_bearer("Bearer " + "x" * 10_000)  # oversized must not crash
    with pytest.raises(BearerError):
        parse_bearer("Bearer " + "töken" + "a" * 20)  # non-ASCII
    with pytest.raises(BearerError):
        parse_bearer("Bearer " + "a\x00b" + "x" * 20)  # control char
    token = issue_token()
    assert parse_bearer(f"Bearer {token}") == token
    assert parse_bearer(f"bearer {token}") == token  # scheme is case-insensitive


def test_classes_not_interchangeable_across_stores(store):
    """INVARIANT 4: no class is accepted where another is expected — each
    credential only ever has a row in ITS OWN store table."""
    from app.auth.tokens import verifier as token_verifier

    session_token = issue_anonymous_session_token()
    creator_token = issue_creator_access_token()
    pt_token = issue_playthrough_access_token()
    now = 1_000_000.0
    case_id = "CASE-XYZ"
    store.create_session(
        session_id="QUOTA-XYZ",
        token_verifier=token_verifier(session_token),
        quota_window_end=now + 3600,
        created_at=now,
    )
    store.create_case(
        case_id=case_id,
        quota_session_id="QUOTA-XYZ",
        title="T",
        difficulty=None,
        created_at=now,
    )
    store.create_creator_credential(
        case_id=case_id,
        token_verifier=token_verifier(creator_token),
        created_at=now,
        expires_at=now + 3600,
    )
    store.create_playthrough(
        playthrough_id="PT-XYZ",
        case_id=case_id,
        case_version=1,
        token_verifier=token_verifier(pt_token),
        state="PLAYING",
        created_at=now,
        expires_at=now + 3600,
    )
    # A creator token does not exist in the session or playthrough stores...
    assert store.get_session_by_verifier(token_verifier(creator_token)) is None
    assert store.get_playthrough_by_verifier(token_verifier(creator_token)) is None
    # ... a session token neither in creator nor playthrough stores ...
    assert store.get_creator_credential_by_verifier(token_verifier(session_token)) is None
    assert store.get_playthrough_by_verifier(token_verifier(session_token)) is None
    # ... and a playthrough token neither in creator nor session stores.
    assert store.get_creator_credential_by_verifier(token_verifier(pt_token)) is None
    assert store.get_session_by_verifier(token_verifier(pt_token)) is None