"""Phase 19 — deterministic canonical environment-hint handling.

The LLM ``world_requirements`` stage used to return ``environmentHint`` values
like ``"hotel/suite"`` (path-like) or ``"Hotel Suite"`` (non-canonical), which
the strict validators rejected and then routed into REMOTE repair — burning
provider calls on a deterministic contract issue. This module owns the pure,
determinetic local repair surface:

- the canonical environment vocabulary (``ENVIRONMENT_IDS``) — the ONLY five
  environment ids the resolver accepts;
- ``canonicalize_environment_hint(raw)`` — trims, lowercases, converts
  spaces/hyphens to underscores and maps aliases onto the canonical ids.
  Path-like values (``/``, ``\\``, ``..``, absolute-path prefixes, URL schemes,
  control characters, executable word tokens) are REJECTED deterministically
  (``(None, issues)``): a rejected value is never used as a file path, never
  triggers a remote LLM repair, and never consumes provider budget.

Equal inputs ALWAYS produce equal outputs. No I/O, no network, no randomness.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from app.world.requirements import safe_string_issues

# The closed canonical environment vocabulary (the ONLY ids that resolve).
ENVIRONMENT_IDS: tuple[str, ...] = (
    "apartment",
    "office",
    "hotel_suite",
    "warehouse",
    "mansion",
)

# Canonical id -> alias token family (mirrors ``app.world.extract``). The alias
# set includes the canonical id itself so ``canonicalize_environment_hint`` is
# idempotent (an already-canonical value passes cleanly).
ENVIRONMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "apartment": ("apartment", "flat", "condo"),
    "office": ("office", "company", "workplace"),
    "hotel_suite": ("hotel", "room", "suite"),
    "warehouse": ("warehouse", "depot", "storage"),
    "mansion": ("mansion", "villa", "manor"),
}

# The normalized-alias lookup (alias token -> canonical id), built once.
_ALIAS_TO_ID: dict[str, str] = {}
for _environment_id, _aliases in ENVIRONMENT_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_ID.setdefault(
            unicodedata.normalize("NFKC", _alias).casefold().replace(" ", "_"),
            _environment_id,
        )
# The canonical underscore token of every id is always accepted (idempotent).
_ALIAS_TO_ID.update({environment_id: environment_id for environment_id in ENVIRONMENT_IDS})


def _underscore_token(value: str) -> str:
    """Trims, lowercases and maps spaces/hyphens to underscores.

    ``"  Hotel Suite "`` / ``"hotel-suite"`` -> ``"hotel_suite"``.
    """
    return (
        unicodedata.normalize("NFKC", value)
        .strip()
        .casefold()
        .replace(" ", "_")
        .replace("-", "_")
    )


def _strip_leading_trailing_whitespace(value: str) -> str:
    """Deterministic canonical-whitespace pre-normalization (Phase 19B
    ADV-217).

    Harmless leading/trailing whitespace — spaces, tabs, newlines, carriage
    returns and OTHER Unicode whitespace (``str.strip`` semantics) — is
    removed BEFORE the safety gate so a model-emitted trailing newline/tab
    (e.g. ``"hotel_suite\\n"``) is canonicalized instead of rejected as a
    spurious "contains a control character". Only LEADING/TRAILING whitespace
    is stripped: INTERIOR control characters (e.g. ``"hotel_\\nsuite"``,
    ``"hotel\\x00suite"``) are untouched and still rejected by the safety gate.
    """
    return unicodedata.normalize("NFKC", value).strip()


def canonicalize_environment_hint(raw: Any) -> tuple[str | None, tuple[str, ...]]:
    """Deterministically canonicalize one raw ``environmentHint`` value.

    Returns ``(canonical_id | None, issues)``:

    - ``None`` raw value -> ``(None, ())`` — a MISSING hint is valid (the
      caller resolver falls back to the documented default kit);
    - a safe value that normalizes (after trim/lowercase/space->underscore/
      hyphen->underscore) onto one of the five canonical ids -> that id with
      no issues;
    - an UNSAFE value (path separators, ``..`` traversal, absolute-path
      prefix, URL scheme, control characters, executable word tokens) ->
      ``(None, issues)`` — REJECTED deterministically; the issuer must never
      pass it into file lookup and must never trigger a remote repair for the
      formatting problem alone;
    - a safe but UNSUPPORTED token (e.g. ``"beach"``) -> ``(None, issues)`` —
      also rejected so the caller falls back to the authoritative
      user-supplied location / default kit (never a silent wrong kit).
    """
    if raw is None:
        return (None, ())
    if not isinstance(raw, str):
        return (None, ("environmentHint: must be a string",))
    # ADV-217: harmless leading/trailing whitespace (incl. newline/tab and
    # other Unicode whitespace) is canonical whitespace — strip it BEFORE the
    # safety gate so a trailing model newline never becomes a spurious
    # "control character" rejection. Interior control characters are NOT
    # touched here and keep being rejected below.
    value = _strip_leading_trailing_whitespace(raw)
    if not value:
        return (None, ("environmentHint: must be a non-empty string",))
    safety = safe_string_issues(value, "environmentHint")
    if safety:
        return (None, tuple(sorted(set(safety))))
    token = _underscore_token(value)
    canonical = _ALIAS_TO_ID.get(token)
    if canonical is not None:
        return (canonical, ())
    return (
        None,
        (
            f"environmentHint: unsupported environment token {token!r} "
            f"(supported: {', '.join(ENVIRONMENT_IDS)})",
        ),
    )


__all__ = [
    "ENVIRONMENT_ALIASES",
    "ENVIRONMENT_IDS",
    "canonicalize_environment_hint",
]