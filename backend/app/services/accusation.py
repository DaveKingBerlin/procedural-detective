"""Phase 7 accusation service (REQUIREMENTS 40.10-40.12, Phase7 B/C/D/I).

Owns the accusation domain over ONE store:

- ``parse_accusation_time``  -- the FROZEN crimeTime grammar (Phase7 B);
- ``submitted_tick``         -- the DEC-003 evaluation-time anchoring of a
  bare time-of-day onto the canonical crime date + timezone offset;
- ``evaluate_accusation``    -- truth-aware per-dimension comparison
  (Phase7 D). Truth-aware is ALLOWED here (the Phase 3/4 solver is NEVER
  re-run or mutated to fit the player answer -- the evaluation only compares
  the pinned truth against the submitted values and uses the accepted
  scoring time set of REQUIREMENTS 31.7);
- ``AccusationService``      -- the endpoint orchestration: pinned-payload
  resolution (never "latest"), lifecycle guards, universe validation and the
  compare-and-set accusation write (Phase7 C).

DEC-003 (FROZEN -- recorded here per the Phase 7 contract):
    Because the canonical crime DATE and timezone offset are pre-reveal
    truth, a bare time-of-day ``"HH:MM[:SS]"`` is anchored to the canonical
    crime date + canonical offset AT EVALUATION TIME (truth-aware, allowed).
    The persisted ``accusations.crime_time`` column stores the RAW submitted
    string; the anchoring decides only whether the submitted wall-clock falls
    inside ``[T-N, T+N+1)``. This keeps the accusation payload a pure player
    submission and the evaluation derived (never stored).
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import datetime
from typing import Any, Mapping

from app.domain.time_interval import (
    accepted_scoring_time_set,
    parse_iso8601,
)
from app.persistence.store import DuplicateAccusation, Store
from app.persistence.timebase import EpochClock
from app.schemas.accusation import AccusationRequest

# Full ISO-8601-with-offset (same strict ASCII grammar as the solver time
# domain -- DATE + T + HH:MM:SS + mandatory offset). The date is taken
# literally from the canonical string for the DEC-003 anchoring.
_FULL_ISO_DATE_RE = re.compile(
    r"^(?P<y>[0-9]{4})-(?P<mo>[0-9]{2})-(?P<d>[0-9]{2})T"
)

# Bare 24h time-of-day "HH:MM[:SS]" (DEC-003): strict two-digit fields, hour
# 00-23, minute/second 00-59. "25:99:00" / "22:17:60" / "22-17" never match.
_BARE_TIME_RE = re.compile(
    r"^(?P<h>[01][0-9]|2[0-3]):(?P<mi>[0-5][0-9])"
    r"(?::(?P<s>[0-5][0-9]))?$"
)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

# Accusation is allowed only from these states (Phase7 A / REQUIREMENTS 40.10).
_PRE_ACCUSATION_STATES = frozenset({"CREATED", "PLAYING"})

_ACCUSATION_ID_MAX = 256
_CRIME_TIME_MAX = 64


class AccusationError(Exception):
    """Base class for accusation service domain errors."""


class AccusationNotFoundError(AccusationError):
    """The pinned published version is missing/unreadable (-> 404)."""


class AccusationValidationError(AccusationError):
    """The accusation violates the frozen body contract (-> 422)."""


class AccusationConflictError(AccusationError):
    """A later/concurrent accusation attempt lost (-> 409
    CASE_ALREADY_SUBMITTED, REQUIREMENTS 40.11)."""


class RevealNotAvailableError(AccusationError):
    """Reveal was requested before a reveal-eligible state (-> 403
    REVEAL_NOT_AVAILABLE, REQUIREMENTS 40.12 / Phase7 E)."""


def _require_clean_id(value: str, label: str) -> None:
    """Ids are strings, 1..256 chars, no control characters (Phase7 B)."""
    if not isinstance(value, str) or not value:
        raise AccusationValidationError(f"{label} must be a non-empty string")
    if len(value) > _ACCUSATION_ID_MAX:
        raise AccusationValidationError(f"{label} is too long")
    if _CONTROL_CHAR_RE.match(value):
        raise AccusationValidationError(f"{label} contains control characters")


def parse_accusation_time(raw: str) -> None:
    """Validate the FROZEN crimeTime grammar (Phase7 B): either a full
    ISO-8601-with-offset timestamp or a bare ``HH:MM[:SS]`` time-of-day.

    Raises ``AccusationValidationError`` (-> 422) on any malformed value. The
    raw string is what gets persisted; anchoring happens at evaluation time
    (DEC-003).
    """
    if not isinstance(raw, str) or not raw or len(raw) > _CRIME_TIME_MAX:
        raise AccusationValidationError("crimeTime is invalid")
    candidate = raw.strip()
    if _FULL_ISO_DATE_RE.match(candidate):
        try:
            parse_iso8601(candidate)
        except (ValueError, TypeError):
            raise AccusationValidationError("crimeTime is invalid") from None
        return
    if not _BARE_TIME_RE.match(candidate):
        raise AccusationValidationError("crimeTime is invalid")


def _anchor_bare_time_to_epoch(bare: str, canonical: str) -> int:
    """DEC-003: anchor a bare time-of-day to the canonical crime DATE +
    canonical timezone offset, returning the UTC epoch tick.

    The canonical string is a validated full ISO-8601-with-offset; its date
    is taken LITERALLY (the authored local date), and the offset converts the
    wall-clock bare time into a UTC tick -- exactly the arithmetic
    ``parse_iso8601`` uses for a full timestamp.
    """
    match = _BARE_TIME_RE.match(bare)
    if match is None:
        raise AccusationValidationError("crimeTime is invalid")
    hour = int(match.group("h"))
    minute = int(match.group("mi"))
    second = int(match.group("s") or "0")
    date_match = _FULL_ISO_DATE_RE.match(canonical)
    if date_match is None:
        raise AccusationValidationError("crimeTime is invalid")
    year = int(date_match.group("y"))
    month = int(date_match.group("mo"))
    day = int(date_match.group("d"))
    _epoch, offset_minutes = parse_iso8601(canonical)
    wall = datetime(year, month, day, hour, minute, second)
    return calendar.timegm(wall.utctimetuple()) - offset_minutes * 60


def submitted_tick(crime_time: str, canonical: str) -> int:
    """The evaluation-time epoch tick of a submitted crimeTime.

    Full ISO values map directly to their UTC tick; bare times are anchored
    to the canonical crime date + canonical offset (DEC-003).
    """
    raw = str(crime_time).strip()
    if _FULL_ISO_DATE_RE.match(raw):
        return parse_iso8601(raw)[0]
    return _anchor_bare_time_to_epoch(raw, canonical)


def evaluate_accusation(
    truth_crime: Mapping[str, Any],
    accusation: Mapping[str, Any],
) -> dict[str, bool]:
    """Truth-aware per-dimension evaluation (Phase7 D) -- NEVER stored.

    ``truth_crime`` is the pinned payload's ``truth.crime`` mapping with
    ``murderer_id`` / ``motive_id`` / ``weapon_id`` and
    ``crime_time.canonical`` / ``crime_time.accusation_tolerance_seconds``.
    ``accusation`` carries the submitted ``murderer_id`` / ``motive_id`` /
    ``weapon_id`` and the RAW ``crime_time`` string. Returns the frozen
    booleans: murdererCorrect / motiveCorrect / weaponCorrect / timeCorrect.
    """
    crime_time = truth_crime.get("crime_time")
    canonical = crime_time.get("canonical") if isinstance(crime_time, Mapping) else None
    tolerance = (
        crime_time.get("accusation_tolerance_seconds")
        if isinstance(crime_time, Mapping)
        else None
    )
    canonical_tick = parse_iso8601(canonical)[0]
    accepted = accepted_scoring_time_set(canonical_tick, int(tolerance))
    tick = submitted_tick(str(accusation.get("crime_time", "")), canonical)
    return {
        "murdererCorrect": str(accusation.get("murderer_id")) == str(
            truth_crime.get("murderer_id")
        ),
        "motiveCorrect": str(accusation.get("motive_id")) == str(
            truth_crime.get("motive_id")
        ),
        "weaponCorrect": str(accusation.get("weapon_id")) == str(
            truth_crime.get("weapon_id")
        ),
        "timeCorrect": accepted.contains(tick),
    }


def validate_universe_membership(
    payload: Mapping[str, Any], body: AccusationRequest
) -> None:
    """Every id MUST belong to the pinned CaseVersion's published candidate
    universes (Phase7 B): suspect in SUSPECT_ELIGIBLE, motive in
    MOTIVE_CANDIDATE, weapon in POTENTIAL_WEAPON. Unknown/foreign ids answer
    422 VALIDATION_ERROR with a GENERIC message -- zero existence leak, and
    cross-version candidates are rejected the same way (Phase7 N9/N10).
    """
    universes = payload.get("universes")
    if not isinstance(universes, Mapping):
        raise AccusationValidationError(
            "ids must belong to the pinned case candidate universes"
        )
    suspects = {str(i) for i in (universes.get("suspect_ids") or ())}
    motives = {str(i) for i in (universes.get("motive_ids") or ())}
    weapons = {str(i) for i in (universes.get("weapon_ids") or ())}
    if str(body.murdererId) not in suspects:
        raise AccusationValidationError(
            "ids must belong to the pinned case candidate universes"
        )
    if str(body.motiveId) not in motives:
        raise AccusationValidationError(
            "ids must belong to the pinned case candidate universes"
        )
    if str(body.weaponId) not in weapons:
        raise AccusationValidationError(
            "ids must belong to the pinned case candidate universes"
        )


class AccusationService:
    """Endpoint orchestration over one Store (Phase7 C)."""

    def __init__(self, store: Store, clock: Any = None) -> None:
        if not isinstance(store, Store):
            raise TypeError("AccusationService requires a Store")
        self._store = store
        self._clock = clock if clock is not None else EpochClock()

    @property
    def store(self) -> Store:
        return self._store

    def _pinned_payload(self, playthrough: Any) -> dict[str, Any]:
        """The pinned published payload (never "latest") -- 404 when the pinned
        version row is missing or unreadable."""
        row = self._store.get_published(playthrough.case_id, playthrough.case_version)
        if row is None:
            raise AccusationNotFoundError("pinned published version unavailable")
        try:
            payload = json.loads(row.payload_json)
        except (ValueError, TypeError):
            raise AccusationNotFoundError("pinned published payload unreadable") from None
        if not isinstance(payload, Mapping):
            raise AccusationNotFoundError("pinned published payload malformed") from None
        return dict(payload)

    def _now(self) -> float:
        return float(self._clock.now())

    def submit_accusation(
        self, playthrough: Any, body: AccusationRequest
    ) -> dict[str, Any]:
        """(1) authorize exact playthrough (auth dep); (2) load pinned
        case_version; (3) state in {CREATED, PLAYING} else 409; (4) validate
        body per the frozen contract; (5) ATOMIC CAS insert + state update in
        ONE transaction; a concurrent loser answers
        409 CASE_ALREADY_SUBMITTED (40.11). The response NEVER reveals truth
        or per-dimension correctness (separation contract C)."""
        payload = self._pinned_payload(playthrough)
        if playthrough.state not in _PRE_ACCUSATION_STATES:
            raise AccusationConflictError("an accusation was already submitted")
        _require_clean_id(body.murdererId, "murdererId")
        _require_clean_id(body.motiveId, "motiveId")
        _require_clean_id(body.weaponId, "weaponId")
        parse_accusation_time(body.crimeTime)
        validate_universe_membership(payload, body)
        now = self._now()
        try:
            row = self._store.insert_accusation_if_unaccused(
                playthrough_id=playthrough.playthrough_id,
                case_id=playthrough.case_id,
                case_version=playthrough.case_version,
                murderer_id=body.murdererId,
                motive_id=body.motiveId,
                weapon_id=body.weaponId,
                crime_time=body.crimeTime,
                created_at=now,
            )
        except DuplicateAccusation:
            raise AccusationConflictError("an accusation was already submitted") from None
        return {
            "playthroughId": row.playthrough_id,
            "caseId": row.case_id,
            "caseVersion": row.case_version,
            "status": "ACCUSED",
            "accusation": {
                "murdererId": row.murderer_id,
                "motiveId": row.motive_id,
                "weaponId": row.weapon_id,
                "crimeTime": row.crime_time,
            },
        }

    def get_reveal(self, playthrough: Any) -> dict[str, Any]:
        """REVEAL (Phase7 E): only from {ACCUSED, REVEALED} (else 403);
        idempotent (repeat -> structurally identical DTO); the accusation read
        + ACCUSED -> REVEALED transition happen in ONE store transaction.
        """
        payload = self._pinned_payload(playthrough)
        row = self._store.get_accusation_and_mark_revealed(playthrough.playthrough_id)
        if row is None:
            raise RevealNotAvailableError("reveal is not available for this playthrough")
        evaluation = evaluate_accusation(
            payload.get("truth", {}).get("crime", {}),
            {
                "murderer_id": row.murderer_id,
                "motive_id": row.motive_id,
                "weapon_id": row.weapon_id,
                "crime_time": row.crime_time,
            },
        )
        from app.services import reveal as reveal_projection

        return reveal_projection.reveal_dto_of(payload, playthrough, row, evaluation)


__all__ = [
    "AccusationConflictError",
    "AccusationError",
    "AccusationNotFoundError",
    "AccusationService",
    "AccusationValidationError",
    "RevealNotAvailableError",
    "evaluate_accusation",
    "parse_accusation_time",
    "submitted_tick",
    "validate_universe_membership",
]