"""Shared structured evidence contract (REQUIREMENTS 12, 47A).

The solver and the player-facing presentation consume the SAME structured
facts: each ``EvidenceFact`` carries typed propositions with deterministic
semantics, a source reference, a public reliability label, and a presentation
block (title/description) that must never diverge from the solver fields.

RELIABILITY INVARIANT (47A.4): ``Reliability`` describes source quality only —
it MUST NOT encode or reveal hidden truth (`"low-reliability witness claim may
still be true"`). None of the deduction rules in this phase consume reliability
as a decision input; exclusion outcomes are invariant under reliability flips
(test ``test_propositions.py`` documents and verifies this).

INTEGRITY: this module (and every solver) never imports ``app.domain.truth``.
"""

from __future__ import annotations

import types
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from app.domain.public import PublicCase
from app.domain.time_interval import (
    assert_epoch_in_domain,
    parse_iso8601,
)

# ---------------------------------------------------------------------------
# Typed proposition vocabulary (§47A.1, plus solver-specific additions).
# ---------------------------------------------------------------------------

PERSON_OBSERVED_AT_LOCATION = "PERSON_OBSERVED_AT_LOCATION"
VICTIM_LAST_SEEN_ALIVE_AT = "VICTIM_LAST_SEEN_ALIVE_AT"
BODY_FIRST_FOUND_AT = "BODY_FIRST_FOUND_AT"
NOISE_HEARD_AT = "NOISE_HEARD_AT"
CRIME_SCENE_OBSERVATION_AT = "CRIME_SCENE_OBSERVATION_AT"
WITNESS_CLAIMS = "WITNESS_CLAIMS"
SUSPECT_CLAIMS = "SUSPECT_CLAIMS"
ALIBI_TIME_CLAIM = "ALIBI_TIME_CLAIM"
OBJECT_CONTAINS_FINGERPRINT = "OBJECT_CONTAINS_FINGERPRINT"
OBJECT_CONTAINS_BLOOD = "OBJECT_CONTAINS_BLOOD"
FORENSIC_WEAPON_MATCH = "FORENSIC_WEAPON_MATCH"
MOTIVE_LINKED_TO_PERSON = "MOTIVE_LINKED_TO_PERSON"
MOTIVE_FACT_CONTRADICTED = "MOTIVE_FACT_CONTRADICTED"
CAN_REACH_CRIME_SCENE_IN_TIME = "CAN_REACH_CRIME_SCENE_IN_TIME"
# Timeline constraint that EXCLUDES a time window (objective coverage showing
# the scene was unoccupied / no relevant activity during the window). This is
# the one time-constraining type that can split the FeasibleCrimeTimeSet into
# two or more disjoint connected intervals, which is what §31.8 ambiguity is
# about (pure intersection constraints always stay convex).
TIME_WINDOW_EXCLUSION = "TIME_WINDOW_EXCLUSION"
OTHER = "OTHER"

PROPOSITION_TYPES: frozenset[str] = frozenset(
    {
        PERSON_OBSERVED_AT_LOCATION,
        VICTIM_LAST_SEEN_ALIVE_AT,
        BODY_FIRST_FOUND_AT,
        NOISE_HEARD_AT,
        CRIME_SCENE_OBSERVATION_AT,
        WITNESS_CLAIMS,
        SUSPECT_CLAIMS,
        ALIBI_TIME_CLAIM,
        OBJECT_CONTAINS_FINGERPRINT,
        OBJECT_CONTAINS_BLOOD,
        FORENSIC_WEAPON_MATCH,
        MOTIVE_LINKED_TO_PERSON,
        MOTIVE_FACT_CONTRADICTED,
        CAN_REACH_CRIME_SCENE_IN_TIME,
        TIME_WINDOW_EXCLUSION,
        OTHER,
    }
)

# Proposition types that carry a canonical timestamp the WHEN solver consumes.
_TIME_CONSTRAINING_TYPES: frozenset[str] = frozenset(
    {
        VICTIM_LAST_SEEN_ALIVE_AT,
        BODY_FIRST_FOUND_AT,
        NOISE_HEARD_AT,
        CRIME_SCENE_OBSERVATION_AT,
        TIME_WINDOW_EXCLUSION,
    }
)

# Proposition types that structurally require an ``observed_at`` timestamp.
_TIME_REQUIRED_TYPES: frozenset[str] = frozenset(
    {PERSON_OBSERVED_AT_LOCATION}.union(_TIME_CONSTRAINING_TYPES)
)


class Reliability(Enum):
    """Public source-quality label (47A.4). High/medium/low only.

    Deliberately NOT a hidden truth flag: neither status nor effect of any
    deduction rule depends on it.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def _reliability(value: Reliability | str) -> Reliability:
    if isinstance(value, Reliability):
        return value
    if isinstance(value, str):
        try:
            return Reliability(value.lower().strip())
        except ValueError:
            raise ValueError(f"invalid reliability {value!r}; must be high|medium|low") from None
    raise TypeError(f"reliability must be Reliability or str, got {type(value).__name__}")


class FrozenDict(Mapping[str, Any]):
    """Truly immutable, order-insensitive, content-equal mapping.

    Backed by ``types.MappingProxyType`` over a freshly-copied plain dict, so
    the mapping view can never be mutated through ``_data`` and the canonical
    content can never diverge from what ``__eq__``/``__hash__`` report
    (DEF-030). The hash is computed once at construction from the frozen
    (order-canonicalized) content and cached, so
    ``frozen == frozen2  =>  hash(frozen) == hash(frozen2)``.

    Structural equality is content-based regardless of key insertion order, so
    two propositions built with the same ``structured`` contents (in any key
    order) are equal. Used for ``TypedProposition.structured`` and
    ``EvidenceFact.presentation`` so evidence objects stay immutable while
    dataclass equality remains exact and deterministic.
    """

    __slots__ = ("_data", "_hash")

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        raw = dict(data) if data is not None else {}
        items = tuple(sorted(raw.items(), key=lambda kv: str(kv[0])))
        # Copy again so later caller mutation cannot leak in through the proxy.
        backing = dict(items)
        object.__setattr__(self, "_data", types.MappingProxyType(backing))
        object.__setattr__(
            self,
            "_hash",
            hash(tuple((key, self._frozen_value(value)) for key, value in items)),
        )

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):  # noqa: D105
        return iter(self._data)

    def __len__(self) -> int:  # noqa: D105
        return len(self._data)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        return dict(self._data) == dict(other)

    def __hash__(self) -> int:
        return self._hash

    @staticmethod
    def _frozen_value(value: Any) -> Any:
        if isinstance(value, Mapping):
            return tuple(
                sorted((k, FrozenDict._frozen_value(v)) for k, v in value.items())
            )
        if isinstance(value, (list, tuple)):
            return tuple(FrozenDict._frozen_value(v) for v in value)
        if isinstance(value, (set, frozenset)):
            return frozenset(FrozenDict._frozen_value(v) for v in value)
        return value

    __repr__ = Mapping.__repr__

    def as_dict(self) -> dict[str, Any]:
        """Deep-plain-dict view for presentation/debugging (read-only usage)."""
        return dict(self._data)


def _frozen_mapping(value: Mapping[str, Any] | None) -> FrozenDict:
    if value is None:
        return FrozenDict()
    if isinstance(value, FrozenDict):
        return value
    return FrozenDict(value)


# ---------------------------------------------------------------------------
# Per-proposition-type structured-field validation (DEF-032 / DEF-033).
# ---------------------------------------------------------------------------

# ``claimedDeparture`` for ALIBI claims — a required ISO-8601-with-offset
# string in ``structured`` (DEF-033).
_ALIBI_DEPARTURE_KEY = "claimedDeparture"


def _require_iso_timestamp_string(value: Any, key: str, owner: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{owner}: structured {key!r} must be a non-empty ISO-8601 string"
        )
    try:
        parse_iso8601(value)
    except ValueError as exc:
        raise ValueError(
            f"{owner}: structured {key!r} is not a valid ISO-8601 timestamp ({exc})"
        ) from None


def _validate_structured_fields(ptype: str, structured: Mapping[str, Any]) -> None:
    """Per-type structured-field contract.

    Kept permissive for every other type; only the types with required typed
    fields are enforced. Raises ``ValueError`` at proposition construction.
    """
    if ptype == FORENSIC_WEAPON_MATCH:
        match = structured.get("match")
        if not isinstance(match, bool):
            raise ValueError(
                "FORENSIC_WEAPON_MATCH requires structured 'match' to be a real "
                f"bool (true/false); got {match!r}"
            )
    elif ptype == ALIBI_TIME_CLAIM:
        _require_iso_timestamp_string(
            structured.get(_ALIBI_DEPARTURE_KEY), _ALIBI_DEPARTURE_KEY, "ALIBI_TIME_CLAIM"
        )


def _time_observations_to_validate(proposition: TypedProposition) -> tuple[str, ...]:
    """Timestamp strings a proposition carries (pre-parsed at validation).

    Every time-bearing ``observed_at`` plus the ALIBI claim departure.
    """
    timestamps: list[str] = []
    if isinstance(proposition.observed_at, str) and proposition.observed_at:
        timestamps.append(proposition.observed_at)
    if proposition.type == ALIBI_TIME_CLAIM:
        claimed = proposition.structured.get(_ALIBI_DEPARTURE_KEY)
        if isinstance(claimed, str) and claimed:
            timestamps.append(claimed)
    return tuple(timestamps)


@dataclass(frozen=True)
class SourceRef:
    """Source identification for one evidence item (§47A.2)."""

    kind: str
    source_id: str

    def __post_init__(self) -> None:
        for name in ("kind", "source_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"SourceRef.{name} must be a non-empty string")


@dataclass(frozen=True)
class TypedProposition:
    """A single typed structured proposition (§47A.1).

    ``structured`` carries additional typed fields (e.g.
    ``{"motiveId": ..., "match": true/false}``) and is immutable.
    ``observed_at`` is an ISO-8601-with-offset string when the proposition is
    time-bearing.
    """

    type: str
    person_id: str | None = None
    location_id: str | None = None
    object_id: str | None = None
    motive_id: str | None = None
    observed_at: str | None = None
    uncertainty_seconds: int = 0
    structured: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if self.type not in PROPOSITION_TYPES:
            raise ValueError(f"unknown proposition type {self.type!r}")
        if self.type in _TIME_REQUIRED_TYPES and (
            not isinstance(self.observed_at, str) or not self.observed_at
        ):
            raise ValueError(f"proposition type {self.type!r} requires observed_at")
        for name in ("person_id", "location_id", "object_id", "motive_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"TypedProposition.{name} must be None or a non-empty string")
        if not isinstance(self.uncertainty_seconds, int) or isinstance(
            self.uncertainty_seconds, bool
        ):
            raise ValueError("uncertainty_seconds must be an int")
        if self.uncertainty_seconds < 0:
            raise ValueError("uncertainty_seconds must be >= 0")
        object.__setattr__(self, "structured", _frozen_mapping(self.structured))
        _validate_structured_fields(self.type, self.structured)


@dataclass(frozen=True)
class EvidenceFact:
    """One discoverable/typed evidence item (REQUIREMENTS 12, 47A).

    ``propositions`` may carry several typed propositions. ``presentation``
    (title/description) is what the UI renders and must always be consistent
    with the structured fields (47A.6).
    """

    id: str
    kind: str
    propositions: tuple[TypedProposition, ...]
    source_ref: SourceRef | None = None
    reliability: Reliability = Reliability.HIGH
    presentation: Mapping[str, Any] = field(default_factory=FrozenDict)
    discoverable: bool = True

    def __post_init__(self) -> None:
        for name in ("id", "kind"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"EvidenceFact.{name} must be a non-empty string")
        object.__setattr__(self, "propositions", tuple(self.propositions))
        if not self.propositions:
            raise ValueError(f"EvidenceFact {self.id!r} must carry at least one proposition")
        if self.source_ref is not None and not isinstance(self.source_ref, SourceRef):
            raise ValueError("EvidenceFact.source_ref must be a SourceRef or None")
        object.__setattr__(self, "reliability", _reliability(self.reliability))
        object.__setattr__(self, "presentation", _frozen_mapping(self.presentation))
        if not isinstance(self.discoverable, bool):
            raise ValueError("EvidenceFact.discoverable must be a bool")


# ---------------------------------------------------------------------------
# Evidence validation (referential integrity + duplicate ids).
# ---------------------------------------------------------------------------


def validate_evidence(public: PublicCase, evidence: Iterable[EvidenceFact]) -> tuple[str, ...]:
    """Deterministic validation of the evidence set against the public model.

    Returns a sorted tuple of issue strings (empty when valid). Every typed
    ``person_id`` / ``location_id`` / ``object_id`` / ``motive_id`` referenced
    by a proposition must resolve in the public model, evidence ids must be
    unique, and every time-bearing ``observed_at`` (plus ALIBI claim
    departures) must parse as ISO-8601-with-offset AND lie inside the solver
    time domain (DEF-026 / DEF-034 — the solvers must never see an
    out-of-domain or malformed tick).
    """
    issues: list[str] = []
    seen: set[str] = set()
    for fact in evidence:
        if fact.id in seen:
            issues.append(f"duplicate evidence id: {fact.id}")
        seen.add(fact.id)
        for prop in fact.propositions:
            if prop.person_id is not None and public.person(prop.person_id) is None:
                issues.append(f"{fact.id}: unknown person_id {prop.person_id!r}")
            if prop.location_id is not None and public.location(prop.location_id) is None:
                issues.append(f"{fact.id}: unknown location_id {prop.location_id!r}")
            if prop.object_id is not None and public.object(prop.object_id) is None:
                issues.append(f"{fact.id}: unknown object_id {prop.object_id!r}")
            if prop.motive_id is not None and public.motive(prop.motive_id) is None:
                issues.append(f"{fact.id}: unknown motive_id {prop.motive_id!r}")
            for timestamp in _time_observations_to_validate(prop):
                try:
                    tick, _offset = parse_iso8601(timestamp)
                    assert_epoch_in_domain(tick)
                except (ValueError, TypeError) as exc:
                    issues.append(
                        f"{fact.id}: invalid or out-of-domain timestamp "
                        f"{timestamp!r} for proposition {prop.type!r}: {exc}"
                    )
    return tuple(sorted(set(issues)))


def ensure_valid_evidence(public: PublicCase, evidence: Iterable[EvidenceFact]) -> None:
    """Raise ``ValueError`` on any referential-integrity or duplicate issue.

    Solver entry points call this before any deduction so invalid input is
    rejected deterministically.
    """
    issues = validate_evidence(public, evidence)
    if issues:
        raise ValueError("invalid evidence: " + "; ".join(issues))


def discoverable_facts(evidence: Iterable[EvidenceFact]) -> tuple[EvidenceFact, ...]:
    """Facts the public/player can see — the only facts solvers may use."""
    return tuple(f for f in evidence if f.discoverable)