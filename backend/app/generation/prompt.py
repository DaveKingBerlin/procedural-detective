"""Structured prompt normalization (REQUIREMENTS 33) -> LockedConstraints.

Parses a ``key: value`` structured prompt into ``LockedConstraints``.

Rules (deterministic):

- Recognized keys (case-insensitive, colon-delimited): ``victim``,
  ``murderer``, ``motive``, ``weapon``, ``time`` (also spelled
  ``Crime time:`` / ``crime time`` / ``crime_time``), ``witness``,
  ``location``.
- Values are trimmed and used VERBATIM otherwise — identity strings, never
  normalized through smarts (canonicalization to case ids is the fixture /
  pipeline product decision, not the parsers job). The ONE exception (Phase 48,
  ADV-48-01) is the ``victim`` field: a single TRAILING Phase-47 profile
  parenthetical of the shape ``(<gender>, <age>, <occupation>)`` — profile
  METADATA, not identity — is stripped from the locked victim so the canonical
  victim id stays the NAME slug (e.g. ``charlotte_hayes``) and never the
  polluted ``charlotte_hayes_female_42_art_curator``. Every other field
  (murderer/witness/weapon/motive/time/location) and the RAW prompt string
  passed to providers are untouched.
- Locked values are later COMPARED through the generic, deterministic
  equivalence layer in ``app.generation.constraints`` (DEF-054): identity
  fields by normalized exact equality ("Sarah Miller" == ``sarah_miller``),
  motive by normalized contiguous-substring ("€240,000 embezzlement" matches
  "Cover up the €240,000 embezzlement"), time by the same parsed UTC epoch
  tick ("22:17" == "2026-09-11T22:17:00+02:00"). The parser itself keeps the
  raw human values.
- Missing/unknown keys are ignored; the returned ``note`` records what was
  recognized.
- A duplicate key (after normalization, e.g. ``Time:`` + ``Crime time:``) is a
  ``PromptError`` rather than a guess.
- Prompts longer than ``max_chars`` raise ``PromptError``.
- Empty/malformed input yields empty constraints + a note (never raises).
"""

from __future__ import annotations

import re

from app.domain.victim_profile import GENDER_VALUES, MAX_AGE, MIN_AGE
from app.generation.constraints import LockedConstraints
from app.generation.schemas import MAX_PROMPT_CHARS

# Normalized key -> LockedConstraints field name.
_KEY_ALIASES: dict[str, str] = {
    "victim": "victim",
    "murderer": "murderer",
    "motive": "motive",
    "weapon": "weapon",
    "time": "crime_time",
    "crime time": "crime_time",
    "crime_time": "crime_time",
    "witness": "witness",
    # Phase43 (P43-FR-02) — the compact ``Location: apartment`` lock.
    "location": "location",
}

_EMPTY_NOTE = "no structured constraints recognized"


class PromptError(ValueError):
    """A structured prompt cannot be normalized deterministically."""


def _normalize_key(raw_key: str) -> str:
    return " ".join(raw_key.strip().lower().split())


# Phase 48 (ADV-48-01) — the victim lock strips ONE trailing Phase-47 profile
# parenthetical of the shape ``(<gender>, <age>, <occupation>)``. That
# parenthetical is profile METADATA (``app.domain.victim_profile``), NOT part of
# the victim identity: keeping it would slug the canonical id to the polluted
# ``charlotte_hayes_female_42_art_curator`` and terminally FAIL a legitimately
# name-only draft victim (``charlotte_hayes``). Only the ``victim`` field is
# affected; every other locked field stays VERBATIM.
_GENDER_ALTERNATION = "|".join(GENDER_VALUES)
_VICTIM_PROFILE_PARENTHETICAL_RE = re.compile(
    r"^(?P<name>\S.*?)\s*"
    r"\(\s*(?P<gender>" + _GENDER_ALTERNATION + r")\s*,\s*"
    r"(?P<age>\d{1,3})\s*,\s*"
    r"(?P<occupation>.*?)\s*\)\s*$",
    re.IGNORECASE,
)


def _strip_victim_profile_parenthetical(value: str) -> str | None:
    """Strip ONE trailing Phase-47 victim profile parenthetical, else verbatim.

    The parenthetical must match the exact profile shape
    ``(<gender>, <age>, <occupation>)`` where (all required):

    - a non-empty name precedes the ``(`` (after trimming),
    - ``<gender>`` case-insensitively is one of ``GENDER_VALUES``
      (``male|female|other|unknown`` — the Phase 47 vocabulary),
    - ``<age>`` is an integer of 1-3 digits within ``MIN_AGE..MAX_AGE``,
    - ``<occupation>`` is non-empty.

    Returns the trimmed NAME when every condition holds; otherwise returns the
    input UNCHANGED (e.g. ``"John (the butler)"``, ``"Sarah Miller"``,
    ``"(Female, 42, x)"`` with no name, ``"Jane (female, 39)"`` missing
    occupation). If stripping would yield an empty name, returns ``None``
    (absent), consistent with the existing empty-value rule.
    """
    match = _VICTIM_PROFILE_PARENTHETICAL_RE.match(value)
    if match is None:
        return value
    name = match.group("name").strip()
    if not name:
        return None
    age = int(match.group("age"))
    if not (MIN_AGE <= age <= MAX_AGE):
        return value
    if not match.group("occupation").strip():
        return value
    return name


def parse_prompt(
    prompt: str, *, max_chars: int = MAX_PROMPT_CHARS
) -> tuple[LockedConstraints, str]:
    """Normalize a structured ``key: value`` prompt.

    Returns ``(locked, note)``. Raises ``PromptError`` for duplicate keys or
    prompts longer than ``max_chars``.
    """
    if not isinstance(prompt, str):
        return (LockedConstraints(), _EMPTY_NOTE)
    if len(prompt) > max_chars:
        raise PromptError(
            f"prompt too long: {len(prompt)} chars (max {max_chars})"
        )
    if not prompt.strip():
        return (LockedConstraints(), _EMPTY_NOTE)

    values: dict[str, str | None] = {}
    recognized: list[str] = []
    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue  # not a structured key:value line -> ignored
        raw_key, raw_value = line.split(":", 1)
        field = _KEY_ALIASES.get(_normalize_key(raw_key))
        if field is None:
            continue  # unknown key -> ignored
        if field in values:
            raise PromptError(f"duplicate prompt key {raw_key.strip()!r}")
        # DEF-067 (ADV-43-08): an empty/whitespace value is treated as ABSENT
        # (``None``) so it can never become an empty locked identity.
        value: str | None = raw_value.strip() or None
        if field == "victim" and value is not None:
            # Phase 48 (ADV-48-01): strip ONE trailing Phase-47 profile
            # parenthetical from the VICTIM lock ONLY — it is profile METADATA,
            # not identity. An emptied name becomes absent (``None``).
            value = _strip_victim_profile_parenthetical(value) or None
        values[field] = value
        recognized.append(field)

    locked = LockedConstraints(
        victim=values.get("victim"),
        murderer=values.get("murderer"),
        motive=values.get("motive"),
        weapon=values.get("weapon"),
        crime_time=values.get("crime_time"),
        witness=values.get("witness"),
        location=values.get("location"),
    )
    if not recognized:
        return (locked, _EMPTY_NOTE)
    note = "recognized structured keys: " + ", ".join(recognized)
    return (locked, note)