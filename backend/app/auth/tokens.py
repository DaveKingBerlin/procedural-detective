"""Opaque bearer token primitives (Phase5 B, REQUIREMENTS 40.1).

- ``issue_token()`` -> ``secrets.token_urlsafe(32)`` (>= 256 bits of
  entropy); tokens are opaque and never derivable from case/playthrough ids.
- ``verifier(token)`` -> ``sha256(token).hexdigest()``. Only the verifier is
  ever stored; the raw token is returned to the client exactly once at
  issuance and never persisted, logged or echoed.
- All verification compares hex digests with ``hmac.compare_digest``
  (constant-time); there is never a plaintext ``==`` comparison on secrets.

Three distinct token classes with SEPARATE issuance APIs and SEPARATE stores
(Phase5 INVARIANT 4 / REQUIREMENTS 40.1): an ``anonymousSessionToken`` is
only ever looked up in ``anonymous_quota_sessions``, a ``creatorAccessToken``
only in ``creator_credentials``, and a ``playthroughAccessToken`` only in
``playthroughs``. Because the verifier is looked up in a different table per
class, no token class is accepted where another is expected (a creator token
has no row in the playthrough table, and vice versa).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from enum import Enum

# Bounds: accept bearer length 20..256 chars. ``token_urlsafe(32)`` yields 43
# chars. The lower bound rejects garbage; the upper bound caps hashing cost
# and rejects absurdly oversized headers (Phase5 J robustness).
MIN_BEARER_LEN = 20
MAX_BEARER_LEN = 256


class TokenClass(str, Enum):
    """The three distinct bearer credential classes (REQUIREMENTS 40.1)."""

    ANONYMOUS_SESSION = "anonymousSessionToken"
    CREATOR_ACCESS = "creatorAccessToken"
    PLAYTHROUGH_ACCESS = "playthroughAccessToken"


def issue_token() -> str:
    """A fresh opaque bearer token (>= 256 bits of entropy)."""
    return secrets.token_urlsafe(32)


# Separate issuance APIs — one entry point per token class (they all draw
# from the same CSPRNG but exist as distinct functions so call sites are
# unambiguous about WHICH class they are issuing).


def issue_anonymous_session_token() -> str:
    """Issue a token of class ``anonymousSessionToken``."""
    return issue_token()


def issue_creator_access_token() -> str:
    """Issue a token of class ``creatorAccessToken`` (per-case credential)."""
    return issue_token()


def issue_playthrough_access_token() -> str:
    """Issue a token of class ``playthroughAccessToken``."""
    return issue_token()


def verifier(token: str) -> str:
    """sha256 hex digest of a token — the ONLY representation ever stored.

    The raw token must NEVER be stored, logged or derived from ids.
    """
    if not isinstance(token, str):
        raise TypeError(f"token must be a str; got {type(token).__name__}")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(presented: str, stored_verifier: str) -> bool:
    """Constant-time check of a presented token against a stored verifier."""
    try:
        expected = verifier(presented)
    except (TypeError, ValueError):
        return False
    if not isinstance(stored_verifier, str):
        return False
    return hmac.compare_digest(expected, stored_verifier)


class BearerError(Exception):
    """Malformed/absent/oversized/ill-formed Bearer credential.

    Always answered with ``401 UNAUTHORIZED`` (envelope) by the API layer.
    """

    def __init__(self, message: str = "invalid bearer credential") -> None:
        self.message = message
        super().__init__(message)


def parse_bearer(authorization: str | None) -> str:
    """Extract and validate the raw bearer token from an Authorization header.

    Rules (Phase5 B/J robustness):

    - header absent or not ``Bearer <token>`` -> ``BearerError``;
    - token length outside 20..256 chars -> ``BearerError`` (10_000 chars is
      rejected without hashing, not crashed);
    - token containing non-ASCII or control characters -> ``BearerError``.
    """
    if authorization is None:
        raise BearerError("missing Authorization header")
    if not isinstance(authorization, str):
        raise BearerError("malformed Authorization header")
    if len(authorization) > 4096:  # generous header cap, well under DoS range
        raise BearerError("oversized Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise BearerError("Authorization header must use the Bearer scheme")
    if len(token) < MIN_BEARER_LEN or len(token) > MAX_BEARER_LEN:
        raise BearerError(
            f"bearer token length must be within "
            f"{MIN_BEARER_LEN}..{MAX_BEARER_LEN} characters"
        )
    for char in token:
        if ord(char) < 0x20 or ord(char) > 0x7E:
            raise BearerError("bearer token contains invalid characters")
    return token


__all__ = [
    "BearerError",
    "MAX_BEARER_LEN",
    "MIN_BEARER_LEN",
    "TokenClass",
    "issue_anonymous_session_token",
    "issue_creator_access_token",
    "issue_playthrough_access_token",
    "issue_token",
    "parse_bearer",
    "verifier",
    "verify_token",
]