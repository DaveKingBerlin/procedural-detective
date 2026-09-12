"""Locked user constraints (REQUIREMENTS 7.2 / 33) and draft violation checks.

Explicit user-specified facts are locked from the beginning of generation.
``LockedConstraints`` never silently changes anything: ``violations_against``
COMPARES the locked values against a generated draft and returns message strings
for every mismatch (or an empty tuple when the draft respects the locks).

Comparison semantics:

- victim / murderer / motive ids are compared case- and whitespace-insensitive;
- weapon compares the draft crime ``weaponId`` the same way;
- crime_time is compared as the SAME TICK: both the locked value and the draft
  cannonical time are parsed to UTC epoch seconds (so ``22:17+02:00`` and
  ``20:17Z`` compare equal);
- witness: a locked witness must exist among the draft's persons with role
  ``"witness"`` (the draft model has no crime-level witness field).

This module never imports ``app.domain.truth`` (boundary contract).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.time_interval import parse_iso8601_to_epoch


def _norm_id(value: str) -> str:
    """Case- and whitespace-insensitive id comparison form."""
    return "".join(value.split()).lower()


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
        """
        issues: list[str] = []
        crime = draft.crime
        for key, field, actual in (
            ("victim", "victim_id", crime.victim_id),
            ("murderer", "murderer_id", crime.murderer_id),
            ("motive", "motive_id", crime.motive_id),
            ("weapon", "weapon_id", crime.weapon_id),
        ):
            locked = getattr(self, key)
            if locked is not None and _norm_id(locked) != _norm_id(actual):
                issues.append(
                    f"locked {key} {locked!r} does not match draft crime.{field} "
                    f"{actual!r}"
                )
        if self.crime_time is not None:
            try:
                locked_tick = parse_iso8601_to_epoch(self.crime_time)
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
                _norm_id(person.role) == "witness"
                and _norm_id(person.person_id) == _norm_id(self.witness)
                for person in draft.persons
            )
            if not found:
                issues.append(
                    f"locked witness {self.witness!r} not found among draft persons "
                    "with role 'witness'"
                )
        return tuple(sorted(set(issues)))