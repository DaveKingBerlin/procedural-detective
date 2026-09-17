"""Typed stage-output / full-draft generation models (Phase 4, section 4).

Frozen dataclasses mirroring the documented stage JSON shapes, with
constructors that validate on build. This is the boundary between the strict
parser and the lifecycle/pipeline: once a ``GeneratedDraft`` (or a stage spec)
exists, it is structurally valid by construction.

Admissible affordance vocabulary (REQUIREMENTS 31.1 + 31.1.3): the canonical
five predicates from ``app.domain.public.AFFORDANCES`` PLUS the documented
optional weapon-subtype labels the Phase 3 model accepts
(``POTENTIAL_SHARP_WEAPON`` / ``POTENTIAL_BLUNT_WEAPON`` / ``POTENTIAL_POISON``).
The Phase 3 golden objects carry ``POTENTIAL_SHARP_WEAPON``, so strict goldens
must accept the same documented vocabulary or the golden round-trip would be
impossible.

Sizing bounds are the frozen spec defaults (REQUIREMENTS 32.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Union

from app.domain.evidence import FrozenDict, PROPOSITION_TYPES, TypedProposition
from app.domain.public import AFFORDANCES
from app.domain.time_interval import assert_epoch_in_domain, parse_iso8601

MAX_CHARACTERS = 8
MAX_LOCATIONS = 8
MAX_EVIDENCE_ITEMS = 50
MAX_DOCUMENT_CHARS = 12000
MAX_SINGLE_TEXT_FIELD_CHARS = 4000
MAX_PROMPT_CHARS = 4000

# Bounded numeric fields (DEF-042 / ADV-129): solver-relevant durations are
# bounded so absurd provider values can never reach the deduction engine.
# Direct construction and strict parsing both reject out-of-range values with
# a clean "out of range" error/issue (never coerced).
MAX_TRAVEL_TIME_SECONDS = 86400 * 7  # one week
MAX_OBSERVATION_UNCERTAINTY_SECONDS = 86400 * 2
MAX_ACCUSATION_TOLERANCE_SECONDS = 86400 * 30

_OPTIONAL_AFFORDANCES: frozenset[str] = frozenset(
    {"POTENTIAL_SHARP_WEAPON", "POTENTIAL_BLUNT_WEAPON", "POTENTIAL_POISON"}
)
AFFORDANCE_VOCABULARY: frozenset[str] = frozenset(AFFORDANCES) | _OPTIONAL_AFFORDANCES


def _require_nonempty_str(value: Any, owner: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{owner} must be a non-empty string")


def _require_int_nonneg(value: Any, owner: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{owner} must be an int")
    if value < 0:
        raise ValueError(f"{owner} must be >= 0")


def _require_int_bounded(value: Any, owner: str, maximum: int) -> None:
    """Non-negative int that must not exceed a documented upper bound."""
    _require_int_nonneg(value, owner)
    if value > maximum:
        raise ValueError(f"{owner} out of range: {value} > {maximum}")


def _check_affordances(value: Any, owner: str) -> None:
    unknown = sorted(set(value) - AFFORDANCE_VOCABULARY)
    if unknown:
        raise ValueError(f"{owner}: unknown affordances {tuple(unknown)!r}")


def _frozen(value: Any) -> FrozenDict:
    if isinstance(value, FrozenDict):
        return value
    return FrozenDict(dict(value) if value is not None else {})


def _check_iso_timestamp(value: str, owner: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{owner} must be a non-empty ISO-8601 string")
    try:
        tick, _offset = parse_iso8601(value)
        assert_epoch_in_domain(tick)
    except ValueError as exc:
        raise ValueError(
            f"{owner} is not a valid in-domain ISO-8601 timestamp ({exc})"
        ) from None


@dataclass(frozen=True)
class PersonSpec:
    person_id: str
    name: str
    role: str
    affordances: tuple[str, ...] = ()
    # Optional player-facing presentation data (JSON key "presentedData").
    # Documented extension so ``assemble_phase3`` can reproduce the Phase 3
    # golden ``PublicPerson.presented_data`` exactly.
    presented_data: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_nonempty_str(self.person_id, "PersonSpec.person_id")
        _require_nonempty_str(self.name, "PersonSpec.name")
        _require_nonempty_str(self.role, "PersonSpec.role")
        object.__setattr__(self, "affordances", tuple(self.affordances))
        _check_affordances(self.affordances, f"PersonSpec {self.person_id!r}")
        object.__setattr__(self, "presented_data", _frozen(self.presented_data))


@dataclass(frozen=True)
class MotiveSpec:
    motive_id: str
    label: str
    affordances: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty_str(self.motive_id, "MotiveSpec.motive_id")
        _require_nonempty_str(self.label, "MotiveSpec.label")
        object.__setattr__(self, "affordances", tuple(self.affordances))
        _check_affordances(self.affordances, f"MotiveSpec {self.motive_id!r}")


@dataclass(frozen=True)
class ObjectSpec:
    object_id: str
    asset_id: str
    affordances: tuple[str, ...] = ()
    subtype: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.object_id, "ObjectSpec.object_id")
        _require_nonempty_str(self.asset_id, "ObjectSpec.asset_id")
        object.__setattr__(self, "affordances", tuple(self.affordances))
        _check_affordances(self.affordances, f"ObjectSpec {self.object_id!r}")
        if self.subtype is not None and (
            not isinstance(self.subtype, str) or not self.subtype
        ):
            raise ValueError("ObjectSpec.subtype must be None or a non-empty string")


@dataclass(frozen=True)
class LocationSpec:
    location_id: str
    name: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.location_id, "LocationSpec.location_id")
        _require_nonempty_str(self.name, "LocationSpec.name")


@dataclass(frozen=True)
class TravelRuleSpec:
    from_location_id: str
    to_location_id: str
    travel_time_seconds: int

    def __post_init__(self) -> None:
        _require_nonempty_str(self.from_location_id, "TravelRuleSpec.from_location_id")
        _require_nonempty_str(self.to_location_id, "TravelRuleSpec.to_location_id")
        _require_int_bounded(
            self.travel_time_seconds,
            "TravelRuleSpec.travel_time_seconds",
            MAX_TRAVEL_TIME_SECONDS,
        )


@dataclass(frozen=True)
class SceneSpec:
    location_id: str
    name: str
    # Phase 11 (Five Environment Kits & Semantic Anchors): additive player-safe
    # kit identity (``apartment`` / ``office`` / ``hotel_suite`` /
    # ``warehouse`` / ``mansion``). Optional so the golden dev-mode scene (and
    # pre-Phase-11 provider output) stays parseable unchanged.
    environment_id: str | None = None
    # Phase 14 (Prompt-to-World): the pinned environment kit VERSION of the
    # published scene (``kit.version``). Optional so provider output without
    # the pin stays parseable unchanged; the world composer ALWAYS pins it.
    environment_version: int | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.location_id, "SceneSpec.location_id")
        _require_nonempty_str(self.name, "SceneSpec.name")
        if self.environment_id is not None and (
            not isinstance(self.environment_id, str) or not self.environment_id
        ):
            raise ValueError(
                "SceneSpec.environment_id must be None or a non-empty string"
            )
        if self.environment_version is not None and (
            not isinstance(self.environment_version, int)
            or isinstance(self.environment_version, bool)
            or self.environment_version < 1
        ):
            raise ValueError(
                "SceneSpec.environment_version must be None or a positive integer"
            )


@dataclass(frozen=True)
class CrimeTimeSpec:
    canonical: str
    accusation_tolerance_seconds: int

    def __post_init__(self) -> None:
        _check_iso_timestamp(self.canonical, "CrimeTimeSpec.canonical")
        _require_int_bounded(
            self.accusation_tolerance_seconds,
            "CrimeTimeSpec.accusation_tolerance_seconds",
            MAX_ACCUSATION_TOLERANCE_SECONDS,
        )


@dataclass(frozen=True)
class CrimeSpec:
    type: str
    victim_id: str
    murderer_id: str
    motive_id: str
    weapon_id: str
    location_id: str
    crime_time: CrimeTimeSpec

    def __post_init__(self) -> None:
        for name in (
            "type",
            "victim_id",
            "murderer_id",
            "motive_id",
            "weapon_id",
            "location_id",
        ):
            _require_nonempty_str(getattr(self, name), f"CrimeSpec.{name}")
        if not isinstance(self.crime_time, CrimeTimeSpec):
            raise ValueError("CrimeSpec.crime_time must be a CrimeTimeSpec")


@dataclass(frozen=True)
class PropSpec:
    """Phase 3 ``TypedProposition`` equivalent in GENERATION shape."""

    type: str
    person_id: str | None = None
    location_id: str | None = None
    object_id: str | None = None
    motive_id: str | None = None
    observed_at: str | None = None
    uncertainty_seconds: int = 0
    structured: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or not self.type:
            raise ValueError("PropSpec.type must be a non-empty string")
        if self.type not in PROPOSITION_TYPES:
            raise ValueError(f"unknown proposition type {self.type!r}")
        for name in ("person_id", "location_id", "object_id", "motive_id", "observed_at"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(
                    f"PropSpec.{name} must be None or a non-empty string"
                )
        _require_int_bounded(
            self.uncertainty_seconds,
            "PropSpec.uncertainty_seconds",
            MAX_OBSERVATION_UNCERTAINTY_SECONDS,
        )
        object.__setattr__(self, "structured", _frozen(self.structured))
        if self.observed_at is not None:
            _check_iso_timestamp(self.observed_at, "PropSpec.observed_at")
        # Reuse the Phase 3 TypedProposition validation wholesale (type
        # vocabulary, time-required observed_at, structured-field contracts).
        _ = self._as_typed_proposition()

    def _as_typed_proposition(self) -> TypedProposition:
        """Phase 3 deduction shape of this proposition (immutable)."""
        return TypedProposition(
            type=self.type,
            person_id=self.person_id,
            location_id=self.location_id,
            object_id=self.object_id,
            motive_id=self.motive_id,
            observed_at=self.observed_at,
            uncertainty_seconds=self.uncertainty_seconds,
            structured=self.structured,
        )


@dataclass(frozen=True)
class EvidenceSpec:
    id: str
    kind: str
    propositions: tuple[PropSpec, ...]
    source_ref: Mapping[str, Any] | None = None
    reliability: str = "high"
    presentation: Mapping[str, Any] = field(default_factory=FrozenDict)
    discoverable: bool = True

    def __post_init__(self) -> None:
        _require_nonempty_str(self.id, "EvidenceSpec.id")
        _require_nonempty_str(self.kind, "EvidenceSpec.kind")
        object.__setattr__(self, "propositions", tuple(self.propositions))
        if not self.propositions:
            raise ValueError(f"EvidenceSpec {self.id!r} must carry at least one proposition")
        for prop in self.propositions:
            if not isinstance(prop, PropSpec):
                raise ValueError(
                    "EvidenceSpec.propositions must contain PropSpec instances"
                )
        if self.source_ref is not None:
            if not isinstance(self.source_ref, Mapping):
                raise ValueError("EvidenceSpec.source_ref must be a Mapping or None")
            _require_nonempty_str(
                self.source_ref.get("kind"), f"EvidenceSpec {self.id!r} source_ref.kind"
            )
            _require_nonempty_str(
                self.source_ref.get("sourceId"),
                f"EvidenceSpec {self.id!r} source_ref.sourceId",
            )
            object.__setattr__(self, "source_ref", _frozen(self.source_ref))
        if self.reliability not in ("high", "medium", "low"):
            raise ValueError(
                f"EvidenceSpec.reliability must be high|medium|low; got "
                f"{self.reliability!r}"
            )
        if not isinstance(self.discoverable, bool):
            raise ValueError("EvidenceSpec.discoverable must be a bool")
        object.__setattr__(self, "presentation", _frozen(self.presentation))


@dataclass(frozen=True)
class WorldGraphLocationSpec:
    location_id: str
    template: str
    rooms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty_str(self.location_id, "WorldGraphLocationSpec.location_id")
        _require_nonempty_str(self.template, "WorldGraphLocationSpec.template")
        object.__setattr__(self, "rooms", tuple(self.rooms))
        for room in self.rooms:
            if not isinstance(room, str) or not room:
                raise ValueError(
                    "WorldGraphLocationSpec.rooms must contain non-empty strings"
                )


@dataclass(frozen=True)
class PlacementSpec:
    object_id: str
    asset_id: str
    location_id: str
    anchor: str
    interaction: str
    evidence_id: str | None = None
    # Phase 13 — the EMBEDDED declarative definition of a procedural
    # (proc.*) asset. Carried ONLY on the trusted service path (the strict
    # parser REJECTS a raw provider placement that smuggles this key); the
    # projection gate re-validates it per the current compiler/schema versions.
    generated_definition: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("object_id", "asset_id", "location_id", "anchor"):
            _require_nonempty_str(getattr(self, name), f"PlacementSpec.{name}")
        # interaction may be the EMPTY string to mean "decorative / not
        # interactable" (DEF-062: the published payload is the single source of
        # interaction affordances). It must still be a str; non-empty values
        # are allowlisted by app.generation.safety.validate_world_graph.
        if not isinstance(self.interaction, str):
            raise ValueError("PlacementSpec.interaction must be a string")
        if self.evidence_id is not None and (
            not isinstance(self.evidence_id, str) or not self.evidence_id
        ):
            raise ValueError(
                "PlacementSpec.evidence_id must be None or a non-empty string"
            )
        if self.generated_definition is not None:
            if not isinstance(self.generated_definition, Mapping):
                raise ValueError(
                    "PlacementSpec.generated_definition must be a Mapping or None"
                )
            object.__setattr__(
                self, "generated_definition", _frozen(self.generated_definition)
            )


@dataclass(frozen=True)
class WorldGraphSpec:
    locations: tuple[WorldGraphLocationSpec, ...] = ()
    placements: tuple[PlacementSpec, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "locations", tuple(self.locations))
        object.__setattr__(self, "placements", tuple(self.placements))
        for item in self.locations:
            if not isinstance(item, WorldGraphLocationSpec):
                raise ValueError(
                    "WorldGraphSpec.locations must contain WorldGraphLocationSpec instances"
                )
        for item in self.placements:
            if not isinstance(item, PlacementSpec):
                raise ValueError(
                    "WorldGraphSpec.placements must contain PlacementSpec instances"
                )


@dataclass(frozen=True)
class PublicWorldSpec:
    persons: tuple[PersonSpec, ...] = ()
    motives: tuple[MotiveSpec, ...] = ()
    objects: tuple[ObjectSpec, ...] = ()
    locations: tuple[LocationSpec, ...] = ()
    travel_rules: tuple[TravelRuleSpec, ...] = ()
    scene: SceneSpec | None = None

    def __post_init__(self) -> None:
        for name, expected in (
            ("persons", PersonSpec),
            ("motives", MotiveSpec),
            ("objects", ObjectSpec),
            ("locations", LocationSpec),
            ("travel_rules", TravelRuleSpec),
        ):
            value = getattr(self, name)
            object.__setattr__(self, name, tuple(value))
            for item in getattr(self, name):
                if not isinstance(item, expected):
                    raise ValueError(
                        f"PublicWorldSpec.{name} must contain {expected.__name__} "
                        "instances"
                    )
        if self.scene is not None and not isinstance(self.scene, SceneSpec):
            raise ValueError("PublicWorldSpec.scene must be a SceneSpec or None")


@dataclass(frozen=True)
class EvidenceSetSpec:
    evidence: tuple[EvidenceSpec, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        for item in self.evidence:
            if not isinstance(item, EvidenceSpec):
                raise ValueError(
                    "EvidenceSetSpec.evidence must contain EvidenceSpec instances"
                )


@dataclass(frozen=True)
class GeneratedDraft:
    """The fully assembled generated draft (REPAIR stage output)."""

    crime: CrimeSpec
    persons: tuple[PersonSpec, ...] = ()
    motives: tuple[MotiveSpec, ...] = ()
    objects: tuple[ObjectSpec, ...] = ()
    locations: tuple[LocationSpec, ...] = ()
    travel_rules: tuple[TravelRuleSpec, ...] = ()
    scene: SceneSpec | None = None
    evidence: tuple[EvidenceSpec, ...] = ()
    world_graph: WorldGraphSpec = field(default_factory=WorldGraphSpec)

    def __post_init__(self) -> None:
        if not isinstance(self.crime, CrimeSpec):
            raise ValueError("GeneratedDraft.crime must be a CrimeSpec")
        for name, expected in (
            ("persons", PersonSpec),
            ("motives", MotiveSpec),
            ("objects", ObjectSpec),
            ("locations", LocationSpec),
            ("travel_rules", TravelRuleSpec),
            ("evidence", EvidenceSpec),
        ):
            value = getattr(self, name)
            object.__setattr__(self, name, tuple(value))
            for item in getattr(self, name):
                if not isinstance(item, expected):
                    raise ValueError(
                        f"GeneratedDraft.{name} must contain {expected.__name__} "
                        "instances"
                    )
        if self.scene is not None and not isinstance(self.scene, SceneSpec):
            raise ValueError("GeneratedDraft.scene must be a SceneSpec or None")
        if not isinstance(self.world_graph, WorldGraphSpec):
            raise ValueError("GeneratedDraft.world_graph must be a WorldGraphSpec")


# The per-stage return value of ``parse_stage`` (REPAIR stage returns the full
# ``GeneratedDraft``).
GenerationStageOutput = Union[
    CrimeSpec,
    PublicWorldSpec,
    EvidenceSetSpec,
    WorldGraphSpec,
    GeneratedDraft,
]