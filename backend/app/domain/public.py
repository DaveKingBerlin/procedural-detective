"""Public (solver-visible) case model — everything the deduction engine may see.

REQUIREMENTS 31.1 / 41.2: ``PublicCase`` contains ONLY information that may
exist in the world independently of whether the player has discovered it, and
NEVER contains hidden solution fields (no murderer id, no true motive id, no
true weapon id, no canonical crime time). The WHEN/WHO/WHY/WEAPON solvers
derive their results exclusively from this module + ``evidence`` + ``rules``.

``public_affordances`` are the structured public eligibility predicates that
define the candidate universes (``SUSPECT_ELIGIBLE``, ``MOTIVE_CANDIDATE``,
``POTENTIAL_WEAPON``) plus player-visible labels (``VISIBLE_CHARACTER``,
``INSPECTABLE``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

AFFORDANCES: tuple[str, ...] = (
    "SUSPECT_ELIGIBLE",
    "MOTIVE_CANDIDATE",
    "POTENTIAL_WEAPON",
    "VISIBLE_CHARACTER",
    "INSPECTABLE",
)

# Optional weapon-subtype affordances (REQUIREMENTS §31.1.3): admissible
# public labels but NOT part of the contract's canonical AFFORDANCES tuple.
_OPTIONAL_AFFORDANCES = frozenset(
    {"POTENTIAL_SHARP_WEAPON", "POTENTIAL_BLUNT_WEAPON", "POTENTIAL_POISON"}
)

_KNOWN_AFFORDANCES = frozenset(AFFORDANCES) | _OPTIONAL_AFFORDANCES


def _affordance_set(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, frozenset):
        return value
    return frozenset(str(item) for item in value)


def _affordance_check(value: frozenset[str], owner: str) -> None:
    unknown = sorted(value - _KNOWN_AFFORDANCES)
    if unknown:
        raise ValueError(f"{owner}: unknown affordances {unknown!r}")


def _require_unique_ids(
    items: tuple[Any, ...], id_field: str, collection: str, expected_type: type
) -> None:
    """Reject duplicate identity fields inside one public collection (DEF-031)."""
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, expected_type):
            raise ValueError(
                f"PublicCase.{collection} must only contain {expected_type.__name__} "
                f"instances; got {type(item).__name__}"
            )
        value = getattr(item, id_field)
        counts[value] = counts.get(value, 0) + 1
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(
            f"PublicCase.{collection} contains duplicate {id_field} values: "
            + ", ".join(repr(value) for value in duplicates)
        )


@dataclass(frozen=True)
class PublicPerson:
    person_id: str
    name: str
    role: str
    public_affordances: frozenset[str] = field(default_factory=frozenset)
    presented_data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("person_id", "name", "role"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"PublicPerson.{name} must be a non-empty string")
        object.__setattr__(self, "public_affordances", _affordance_set(self.public_affordances))
        _affordance_check(self.public_affordances, f"PublicPerson {self.person_id!r}")

    def has_affordance(self, affordance: str) -> bool:
        return affordance in self.public_affordances


@dataclass(frozen=True)
class PublicMotive:
    motive_id: str
    label: str
    public_affordances: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name in ("motive_id", "label"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"PublicMotive.{name} must be a non-empty string")
        object.__setattr__(self, "public_affordances", _affordance_set(self.public_affordances))
        _affordance_check(self.public_affordances, f"PublicMotive {self.motive_id!r}")

    def has_affordance(self, affordance: str) -> bool:
        return affordance in self.public_affordances


@dataclass(frozen=True)
class PublicObject:
    object_id: str
    asset_id: str
    public_affordances: frozenset[str] = field(default_factory=frozenset)
    subtype: str | None = None

    def __post_init__(self) -> None:
        for name in ("object_id", "asset_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"PublicObject.{name} must be a non-empty string")
        if self.subtype is not None and (not isinstance(self.subtype, str) or not self.subtype):
            raise ValueError("PublicObject.subtype must be None or a non-empty string")
        object.__setattr__(self, "public_affordances", _affordance_set(self.public_affordances))
        _affordance_check(self.public_affordances, f"PublicObject {self.object_id!r}")

    def has_affordance(self, affordance: str) -> bool:
        return affordance in self.public_affordances


@dataclass(frozen=True)
class PublicLocation:
    location_id: str
    name: str

    def __post_init__(self) -> None:
        for name in ("location_id", "name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"PublicLocation.{name} must be a non-empty string")


@dataclass(frozen=True)
class PublicTravelRule:
    """Public spatial/travel metadata (REQUIREMENTS 10.4 / 31.6).

    ``travel_time_seconds`` is the minimum time required to move from
    ``from_location_id`` to ``to_location_id``. It is explicitly public
    deduction metadata.
    """

    from_location_id: str
    to_location_id: str
    travel_time_seconds: int

    def __post_init__(self) -> None:
        for name in ("from_location_id", "to_location_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"PublicTravelRule.{name} must be a non-empty string")
        if not isinstance(self.travel_time_seconds, int) or isinstance(
            self.travel_time_seconds, bool
        ):
            raise ValueError("PublicTravelRule.travel_time_seconds must be an int")
        if self.travel_time_seconds < 0:
            raise ValueError("PublicTravelRule.travel_time_seconds must be >= 0")


@dataclass(frozen=True)
class PublicScene:
    location_id: str
    name: str

    def __post_init__(self) -> None:
        for name in ("location_id", "name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"PublicScene.{name} must be a non-empty string")


@dataclass(frozen=True)
class PublicCase:
    """The fully public case model. No hidden solution fields anywhere."""

    case_id: str
    case_version: int
    persons: tuple[PublicPerson, ...] = field(default_factory=tuple)
    motives: tuple[PublicMotive, ...] = field(default_factory=tuple)
    objects: tuple[PublicObject, ...] = field(default_factory=tuple)
    locations: tuple[PublicLocation, ...] = field(default_factory=tuple)
    travel_rules: tuple[PublicTravelRule, ...] = field(default_factory=tuple)
    scene: PublicScene | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("PublicCase.case_id must be a non-empty string")
        if not isinstance(self.case_version, int) or isinstance(self.case_version, bool):
            raise ValueError("PublicCase.case_version must be an int")
        for name in ("persons", "motives", "objects", "locations", "travel_rules"):
            value = getattr(self, name)
            object.__setattr__(self, name, tuple(value))
        if self.scene is not None and not isinstance(self.scene, PublicScene):
            raise ValueError("PublicCase.scene must be a PublicScene")
        # Duplicate-id rejection (DEF-031): the raw public collections must be
        # unique so snapshot/vs-raw derivations can never diverge (e.g.
        # suspect_universe returning duplicates while CandidateUniverses
        # deduplicates).
        _require_unique_ids(self.persons, "person_id", "persons", PublicPerson)
        _require_unique_ids(self.motives, "motive_id", "motives", PublicMotive)
        _require_unique_ids(self.objects, "object_id", "objects", PublicObject)
        _require_unique_ids(self.locations, "location_id", "locations", PublicLocation)
        self._require_unique_travel_rules()
        # Lookup maps (order-insensitive; makes solvers deterministic under
        # shuffled collection order).
        object.__setattr__(self, "_persons", {p.person_id: p for p in self.persons})
        object.__setattr__(self, "_motives", {m.motive_id: m for m in self.motives})
        object.__setattr__(self, "_objects", {o.object_id: o for o in self.objects})
        object.__setattr__(self, "_locations", {l.location_id: l for l in self.locations})
        object.__setattr__(
            self,
            "_travel",
            {
                (r.from_location_id, r.to_location_id): r.travel_time_seconds
                for r in self.travel_rules
            },
        )

    def _require_unique_travel_rules(self) -> None:
        seen: dict[tuple[str, str], int] = {}
        for rule in self.travel_rules:
            pair = (rule.from_location_id, rule.to_location_id)
            seen[pair] = seen.get(pair, 0) + 1
        duplicates = sorted(k for k, count in seen.items() if count > 1)
        if duplicates:
            raise ValueError(
                "PublicCase.travel_rules contains duplicate directed pairs: "
                + ", ".join(f"{a}->{b}" for a, b in duplicates)
            )

    # -- deterministic lookups -------------------------------------------------

    def person(self, person_id: str) -> PublicPerson | None:
        return self._persons.get(person_id)

    def motive(self, motive_id: str) -> PublicMotive | None:
        return self._motives.get(motive_id)

    def object(self, object_id: str) -> PublicObject | None:
        return self._objects.get(object_id)

    def location(self, location_id: str) -> PublicLocation | None:
        return self._locations.get(location_id)

    def travel_time(self, from_location_id: str, to_location_id: str) -> int | None:
        """Return minimum travel time, or None when no rule is declared.

        A missing rule means the travel time is unknown — the solvers must then
        treat the movement as unconstrained (never use it to exclude).
        """
        return self._travel.get((from_location_id, to_location_id))

    @property
    def scene_location_id(self) -> str | None:
        return self.scene.location_id if self.scene is not None else None