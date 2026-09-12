"""Structured prompt normalization (REQUIREMENTS 33) -> LockedConstraints.

Parses a ``key: value`` structured prompt into ``LockedConstraints``.

Rules (deterministic):

- Recognized keys (case-insensitive, colon-delimited): ``victim``,
  ``murderer``, ``motive``, ``weapon``, ``time`` (also spelled
  ``Crime time:`` / ``crime time`` / ``crime_time``), ``witness``.
- Values are trimmed and used VERBATIM otherwise — identity strings, never
  normalized through smarts (canonicalization to case ids is the fixture /
  pipeline product decision, not the parsers job).
- Missing/unknown keys are ignored; the returned ``note`` records what was
  recognized.
- A duplicate key (after normalization, e.g. ``Time:`` + ``Crime time:``) is a
  ``PromptError`` rather than a guess.
- Prompts longer than ``max_chars`` raise ``PromptError``.
- Empty/malformed input yields empty constraints + a note (never raises).
"""

from __future__ import annotations

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
}

_EMPTY_NOTE = "no structured constraints recognized"


class PromptError(ValueError):
    """A structured prompt cannot be normalized deterministically."""


def _normalize_key(raw_key: str) -> str:
    return " ".join(raw_key.strip().lower().split())


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

    values: dict[str, str] = {}
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
        values[field] = raw_value.strip()
        recognized.append(field)

    locked = LockedConstraints(
        victim=values.get("victim"),
        murderer=values.get("murderer"),
        motive=values.get("motive"),
        weapon=values.get("weapon"),
        crime_time=values.get("crime_time"),
        witness=values.get("witness"),
    )
    if not recognized:
        return (locked, _EMPTY_NOTE)
    note = "recognized structured keys: " + ", ".join(recognized)
    return (locked, note)