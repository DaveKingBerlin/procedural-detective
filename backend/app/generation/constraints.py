"""Locked user constraints (REQUIREMENTS 7.2 / 33) and draft violation checks.

Explicit user-specified facts are locked from the beginning of generation.
``LockedConstraints`` never silently changes anything: ``violations_against``
COMPARES the locked values against a generated draft and returns message strings
for every mismatch (or an empty tuple when the draft respects the locks).

Comparison semantics — the GENERIC, deterministic equivalence layer (DEF-054):

1. Identity fields (victim / murderer / weapon / witness person): name<->id
   equivalence. Every value is normalized with ``normalize_identity`` (Unicode
   ``casefold`` + keep ONLY ASCII letters and digits) and compared for EXACT
   equality. So "Thomas Reed", "thomas_reed" and "THOMAS REED!" all normalize
   to ``thomasreed``. A witness lock additionally matches a draft witness
   person's NAME (same normalization), and a victim/murderer lock additionally
   matches the linked draft person's NAME — so a human display name matches the
   generated person even when the draft gives the person a non-name id. A
   locked value that matches NOTHING after normalization remains a violation
   (immutability guarantee: locking "Dave Smith" while the draft murderer is
   ``thomas_reed`` stays TERMINAL).

2. Motive: the locked motive matches the draft's WINNING motive
   (``crime.motive_id`` and its public label) when the normalized locked text
   is a CONTIGUOUS substring of the normalized id/label — or the reverse (the
   id/label is a contiguous substring of the locked text), i.e. either
   direction. Motive text is normalized with ``normalize_motive_text``:
   casefold + keep ASCII letters/digits AND currency symbols (``$ £ ¥ €``),
   drop every other character. Consequently "€240,000 embezzlement" ->
   ``€240000embezzlement``, which IS a contiguous substring of "Cover up the
   €240,000 embezzlement" -> ``coverupthe€240000embezzlement``.

   Minimum-relevance floor (DEF-055): a normalized locked motive shorter than
   ``MIN_MOTIVE_NORM_LEN`` (``4``) normalized characters is a VIOLATION
   (verified deterministically, never implicitly matched). A 1-3 character
   lock ("e", "€", "o", "hi", "XYZ", "!!!") cannot carry enough meaning to
   verify a motive, and the substring rule would otherwise accept it against
   nearly any id/label (``"e"`` is a substring of almost every motive). The
   floor is 4 because it rejects every degenerate 1-3 char token while still
   accepting the golden material ("embezzlement" = 12) AND every real motive
   id the game can produce (the shortest plausible single-token motive ids —
   e.g. ``spite``, ``envy``, ``greed``, ``hate`` — are all >= 4 normalized
   characters; ``crime.motive_id`` values must be non-empty strings and the
   public vocabulary is phrase ids like ``cover_up_embezzlement``). A lock
   that is exactly the full normalized label/id (any length >= 4) still
   matches, so a future short-but-meaningful golden motive id remains
   verifiable.

2b. weapon (ADV-237): the locked weapon is reduced through the pipeline's
   SINGLE semantic-id slug source (``app.world.requirements.semantic_object_id``
   — the same 40-char-bounded slug the composer materializes for the resolved
   weapon object) BEFORE the DEF-054 normalized equality of rule 1. The
   pipeline materializes a locked weapon under its SEMANTIC id (display text
   -> slug, truncated identically EVERYWHERE), so a >40-char locked weapon
   MUST match its truncated composed id — never a divergence, never a
   collision (one weapon per case).

3. crime_time: the locked value and the draft canonical are compared as the
   SAME UTC epoch tick. A locked value may be a full ISO-8601-with-offset
   timestamp (existing rule) OR a bare 24h wall-clock ``H:MM[:SS]`` /
   ``HH:MM[:SS]`` optionally with a date/offset, deterministically anchored to
   the draft's canonical crime DATE + timezone offset (``parse_locked_time_to_epoch``
   in ``app.domain.time_interval`` — the same DEC-003 arithmetic the accusation
   path uses). "22:17" == "22:17:00" == "2026-09-11T22:17:00+02:00" == "20:17Z".

4. Unknown values stay exact (see rule 1).

This module never imports ``app.domain.truth`` (boundary contract).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.time_interval import (
    parse_iso8601_to_epoch,
    parse_locked_time_to_epoch,
)

# ASCII lowercase letters a-z + digits 0-9: the ONLY characters kept by
# ``normalize_identity`` (casefolded input is already lowercase).
_ASCII_LOWER_LETTERS = frozenset(chr(code) for code in range(ord("a"), ord("z") + 1))
_ASCII_DIGITS = frozenset(chr(code) for code in range(ord("0"), ord("9") + 1))
_CURRENCY_SYMBOLS = frozenset("$£¥€")

# Minimum-relevance floor for NORMALIZED motive locks (DEF-055). A normalized
# locked motive shorter than this is a violation: it cannot carry enough
# meaning to verify ("e"/"€"/"o"/"hi"/"XYZ" <= 3 chars) and the contiguous-
# substring rule would otherwise accept it against nearly any motive id/label.
# 4 is the shortest length at which a lock can still identify a distinct
# motive token (``spite``/``envy``/``greed``/``hate`` are all >= 4) while the
# golden material ("embezzlement" = 12) and every real motive id remain far
# above the floor.
MIN_MOTIVE_NORM_LEN = 4


def normalize_identity(value: str) -> str:
    """DEF-054 rule 1: casefold + keep only ASCII letters/digits.

    "Thomas Reed", "thomas_reed" and "THOMAS REED!" all normalize to
    ``thomasreed``.
    """
    if not isinstance(value, str):
        return ""
    return "".join(
        ch
        for ch in value.casefold()
        if ch in _ASCII_LOWER_LETTERS or ch in _ASCII_DIGITS
    )


def normalize_motive_text(value: str) -> str:
    """DEF-054 rule 2: casefold + keep ASCII letters/digits AND currency
    symbols (``$ £ ¥ €``); every other character is dropped.

    "€240,000 embezzlement" -> ``€240000embezzlement``.
    """
    if not isinstance(value, str):
        return ""
    return "".join(
        ch
        for ch in value.casefold()
        if ch in _ASCII_LOWER_LETTERS or ch in _ASCII_DIGITS or ch in _CURRENCY_SYMBOLS
    )


def _identity_equivalent(locked: str, *candidates: str) -> bool:
    """DEF-054 rule 1: EXACT normalized equality against ANY candidate."""
    needle = normalize_identity(locked)
    return any(
        normalize_identity(candidate) == needle
        for candidate in candidates
        if candidate
    )


def _motive_matches(locked: str, motive_id: str, label: str | None) -> bool:
    """DEF-054 rule 2 (with DEF-055 floor): contiguous-substring equivalence,
    either direction.

    A normalized locked motive shorter than ``MIN_MOTIVE_NORM_LEN`` is a
    VIOLATION (returns False): it cannot verify a motive deterministically and
    must never be implicitly matched ("e" is a substring of nearly everything).
    Locks at/above the floor keep the full contiguous-substring semantics —
    including an exact full-label/id lock — unchanged.
    """
    needle = normalize_motive_text(locked)
    if len(needle) < MIN_MOTIVE_NORM_LEN:
        return False
    for candidate in (motive_id, label):
        if not candidate:
            continue
        haystack = normalize_motive_text(candidate)
        if haystack and (needle in haystack or haystack in needle):
            return True
    return False


@dataclass(frozen=True)
class LockedConstraints:
    victim: str | None = None
    murderer: str | None = None
    motive: str | None = None
    weapon: str | None = None
    crime_time: str | None = None
    witness: str | None = None

    def locked_fields(self) -> tuple[tuple[str, str | None], ...]:
        """Deterministic (field, value-or-None) listing."""
        return (
            ("victim", self.victim),
            ("murderer", self.murderer),
            ("motive", self.motive),
            ("weapon", self.weapon),
            ("crime_time", self.crime_time),
            ("witness", self.witness),
        )

    def violations_against(self, draft: "GeneratedDraft") -> tuple[str, ...]:
        """Return sorted issue strings when the draft violates a locked value.

        ``draft`` is any object exposing ``.crime`` (a ``CrimeSpec``) and
        ``.persons`` (an iterable of ``PersonSpec``) — i.e. a
        ``GeneratedDraft``. Returns an empty tuple when every locked field is
        respected.

        Comparison operators are the DEF-054 equivalence layer (rules 1-3
        above): identity fields by normalized exact equality, motive by
        normalized contiguous-substring (either direction), crime_time by the
        same parsed UTC epoch tick.
        """
        issues: list[str] = []
        crime = draft.crime
        persons_by_id = {person.person_id: person for person in draft.persons}

        for key, field, locked in (
            ("victim", "victim_id", self.victim),
            ("murderer", "murderer_id", self.murderer),
        ):
            if locked is None:
                continue
            actual = getattr(crime, field)
            linked = persons_by_id.get(actual)
            names: tuple[str, ...] = (linked.name,) if linked is not None else ()
            if not _identity_equivalent(locked, actual, *names):
                issues.append(
                    f"locked {key} {locked!r} does not match draft crime.{field} "
                    f"{actual!r}"
                )
        if self.weapon is not None:
            actual = crime.weapon_id
            # ADV-237 — the locked weapon is reduced via the pipeline's SINGLE
            # semantic-id slug source FIRST (same 40-char bound the composer
            # materializes): a >40-char locked display text is NEVER compared
            # against its truncated composed id as a raw string — that would be
            # the ADV-237 divergence (a genuinely long but valid weapon would
            # FAIL a case that otherwise publishes).
            from app.world.requirements import semantic_object_id

            locked_weapon_id = semantic_object_id(self.weapon)
            if not _identity_equivalent(locked_weapon_id, actual):
                issues.append(
                    f"locked weapon {self.weapon!r} does not match draft "
                    f"crime.weapon_id {actual!r}"
                )
        if self.motive is not None:
            motive_id = crime.motive_id
            label = next(
                (m.label for m in draft.motives if m.motive_id == motive_id), None
            )
            if not _motive_matches(self.motive, motive_id, label):
                issues.append(
                    f"locked motive {self.motive!r} does not match draft "
                    f"crime.motive_id {motive_id!r}"
                )
        if self.crime_time is not None:
            try:
                locked_tick = parse_locked_time_to_epoch(
                    self.crime_time, crime.crime_time.canonical
                )
                draft_tick = parse_iso8601_to_epoch(crime.crime_time.canonical)
            except (TypeError, ValueError) as exc:
                issues.append(f"locked crime_time cannot be compared: {exc}")
            else:
                if locked_tick != draft_tick:
                    issues.append(
                        f"locked crime_time {self.crime_time!r} does not match draft "
                        f"crime time {crime.crime_time.canonical!r}"
                    )
        if self.witness is not None:
            found = any(
                normalize_identity(person.role) == "witness"
                and _identity_equivalent(
                    self.witness, person.person_id, person.name
                )
                for person in draft.persons
            )
            if not found:
                issues.append(
                    f"locked witness {self.witness!r} not found among draft persons "
                    "with role 'witness'"
                )
        return tuple(sorted(set(issues)))