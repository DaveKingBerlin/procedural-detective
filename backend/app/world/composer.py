"""Phase 14 — the deterministic World Composer.

``compose_world`` turns a ``WorldRequirements`` + the resolved environment kit
into a valid, playable, deterministic ``WorldComposition``:

    WorldRequirements
        + Environment Resolver -> kit
        + Asset Oracle (catalog / variant / procedural)
        + golden evidence placements (interactions + evidence links)
        -> WorldComposition { placements, new objects, provenance,
                              relation satisfaction, world issues }

Placement rules (documented, deterministic):

- The KIT BASE set (``KIT_BASE_OBJECT_IDS``) reuses the EXISTING golden
  evidence placements verbatim (objectId / assetId / interaction / evidenceId)
  so the solver-critical evidence links (knife -> forensic_knife_match_01,
  laptop -> email_thomas_01, ...) stay fixed on EVERY kit. Prompt-specific
  requests are resolved through the Oracle; a request whose resolved asset
  already sits in the kit base is deduped (never placed twice).
- NEW (prompt-specific) objects are added as DECORATIVE supporting props
  (``interaction = ""``, ``evidence_id = None``) unless they are a known
  evidence request, so the solver candidate universe is never widened by a
  prompt word.
- Placement uses ``app.environments.placer.place_objects`` — the single
  deterministic allocation authority. Relations (``on_desk`` etc.) become
  anchor-type hints ONLY when the relation's mapped anchor type can host the
  resolved asset in the kit; a placement attempt with hints that fails the
  placement contract is retried WITHOUT hints (deterministic natural
  allocation), and the outcome is recorded honestly in ``relation_satisfied``.
  ``near_victim`` is evaluated AFTER placement: the bound object's anchor must
  stay within ``NEAR_VICTIM_PROXIMITY`` of a kit BODY anchor (proximity is not
  a placer-native constraint).
- Self-validation: every placement must pass
  ``placer.validate_placement``, every evidence-linked object must carry a
  non-empty interaction and an evidence-capable anchor, and no required
  evidence may be unreachable. All deviation is COLLECTED as sanitized
  ``world.``-prefixed issue strings (the WORld validation bucket) — never a
  crash. A KNOWN-UNSAFE request is recorded in the resolution record and NOT
  composed (safe fail).
- ADV-153 — every prompt request resolves INDEPENDENTLY: an unresolvable
  DECORATIVE unseen object is LEFT OUT of the world (never a silent wrong
  substitution, never a blocking issue) and is reported through the
  player-safe bounded ``compositionNotes`` tuple (``the '<noun>' you
  described is not currently available - it was left out``); the successfully
  resolved objects STAY in the composition. An unresolvable
  CRITICALITY_REQUIRED object is NEVER dropped or substituted: it produces the
  blocking ``world.unresolved-object`` issue so the generation lifecycle
  repairs or fails publication.

Provenance: every placed object id maps to its Asset Oracle provenance value
(CATALOG_EXACT / CATALOG_ALIAS / SEMANTIC_MATCH / PARAMETRIC_VARIANT /
PROCEDURAL_GENERATED / FALLBACK). ``resolution_record`` is diagnostic-only and
never serialized into any payload or DTO.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from app.assets.catalog import Catalog, load_catalog_from_repo
from app.assets.compiler import GeneratedAssetDefinition
from app.assets.oracle import resolve_or_generate
from app.assets.resolver import (
    AssetRequest,
    Provenance,
    resolve_with_variant,
)
from app.assets.spec_provider import (
    MAX_SPEC_PROVIDER_CALLS_PER_GENERATION,
    AssetSpecProvider,
    AssetSpecRequest,
    AssetSpecResponse,
    BoundedSpecProvider,
)
from app.core.observability import emit_event
from app.environments.manifests import EnvironmentKit, load_environment
from app.environments.placer import (
    PlacementError,
    PlacementRequest,
    generated_asset_anchor_meta,
    is_procedural_asset_id,
    place_objects,
    validate_placement,
)
from app.environments.resolver import FALLBACK_ENVIRONMENT_ID
from app.generation.failure_codes import GenerationFailureCode
from app.generation.provider import StageDriverProviderFailure
from app.generation.schemas import ObjectSpec, PlacementSpec
from app.world.extract import is_base_object_request
from app.world.requirements import (
    CRITICALITY_DECORATIVE,
    CRITICALITY_REQUIRED,
    RELATION_KINDS,
    RELATION_TO_ANCHOR_TYPES,
    WorldRequirements,
    safe_string_issues,
)


class SemanticObjectResolutionError(ValueError):
    """Phase 19 Fix B.3 — a SEMANTIC object referenced by the crime/evidence
    algebra could not be represented as a public/world object.

    Raised fail-closed (sanitized message; never raw provider content) instead
    of publishing a case whose weapon/evidence id is absent. Callers treat it
    as a terminal attempt condition — the object is never silently dropped.
    """

# --------------------------------------------------------------------------- #
# documented constants
# --------------------------------------------------------------------------- #

# Proximity (world units) inside which a near_victim-bound object is satisfied.
NEAR_VICTIM_PROXIMITY = 5.0

# Phase 14_5 — the documented minimum semantic-match confidence for a
# CRITICALITY_REQUIRED object. A SEMANTIC_MATCH produced by a tag tie alone
# (confidence == SEMANTIC_TAG_WEIGHT == 3.0) would be a DIFFERENT semantic
# class than the request (e.g. "ice pick" mapped onto an unrelated sharp
# object). Below this threshold a REQUIRED request is NEVER substituted: it
# escalates to the AssetSpecProvider; if the provider yields nothing valid the
# composer records ``world.unresolved-object`` (repair/regenerate/fail
# publication — never a wrong substitution). 6.0 == tag + category + subtype
# agreement (the resolver weights 3.0/2.0/1.0), i.e. a full-vector agreement.
CRITICAL_MIN_SEMANTIC_CONFIDENCE = 6.0

# Per-composition provider-call budget cap (see BoundedSpecProvider); the
# service hoists ONE budget over the whole attempt so repair passes share it.
SPEC_PROVIDER_CALL_LIMIT = MAX_SPEC_PROVIDER_CALLS_PER_GENERATION

# ADV-153 — player-safe composition notes for DECORATIVE unseen objects that
# could not be generated/placed: they are left OUT of the world (never a
# silent substitution), the SUCCESSFULLY resolved objects stay, and the world
# carries at most MAX_COMPOSITION_NOTES short sanitized notes (the browser may
# surface them). REQUIRED/CRITICAL unresolved objects keep the blocking
# ``world.unresolved-object`` issue (repair then terminal-fail, never dropped).
MAX_COMPOSITION_NOTES = 3
MAX_COMPOSITION_NOTE_LENGTH = 120

# Relation kind -> preferred anchor TYPES (fallback order). The FIRST type that
# can host the resolved asset in the resolved kit becomes the placement hint.
RELATION_PREFERENCE_TYPES: Mapping[str, tuple[str, ...]] = {
    "on_desk": ("DESK_EVIDENCE", "TABLE_PROP", "GENERIC_PROP", "STORAGE"),
    "on_table": ("TABLE_PROP", "DESK_EVIDENCE", "GENERIC_PROP"),
    "inside_cabinet": ("STORAGE", "GENERIC_PROP"),
    "floor_area": ("FLOOR_EVIDENCE", "GENERIC_PROP", "TABLE_PROP", "DESK_EVIDENCE"),
    "on_wall": ("WALL_EVIDENCE", "GENERIC_PROP", "DESK_EVIDENCE", "TABLE_PROP"),
    "near_victim": (
        "FLOOR_EVIDENCE",
        "TABLE_PROP",
        "GENERIC_PROP",
        "DESK_EVIDENCE",
        "STORAGE",
        "DOCUMENT",
        "COMPUTER",
    ),
}

# The per-kit default (base) placed-object set. These reuse the golden
# evidence placements VERBATIM; prompts may only ADD to them. Hotel/warehouse
# intentionally omit the two spare sharp objects (letter opener / scissors) so
# the kit anchor capacity is never exceeded by the evidentiary props.
KIT_BASE_OBJECT_IDS: Mapping[str, tuple[str, ...]] = {
    "apartment": (
        "kitchen_knife",
        "letter_opener",
        "scissors",
        "vase_01",
        "apartment_table",
        "apartment_door",
        "apartment_lamp",
        "apartment_laptop",
        "victim_body_placeholder",
    ),
    "office": (
        "kitchen_knife",
        "letter_opener",
        "scissors",
        "vase_01",
        "apartment_table",
        "apartment_door",
        "apartment_lamp",
        "apartment_laptop",
        "victim_body_placeholder",
    ),
    "mansion": (
        "kitchen_knife",
        "letter_opener",
        "scissors",
        "vase_01",
        "apartment_table",
        "apartment_door",
        "apartment_lamp",
        "apartment_laptop",
        "victim_body_placeholder",
    ),
    "hotel_suite": (
        "kitchen_knife",
        "vase_01",
        "apartment_table",
        "apartment_door",
        "apartment_lamp",
        "apartment_laptop",
        "victim_body_placeholder",
    ),
    "warehouse": (
        "kitchen_knife",
        "vase_01",
        "apartment_table",
        "apartment_door",
        "apartment_lamp",
        "apartment_laptop",
        "victim_body_placeholder",
    ),
}

# The golden evidence placements (facts) the composer falls back to when the
# caller's ``evidence_placements`` does not cover a base object (defensive).
_GOLDEN_OBJECT_FACTS: Mapping[str, tuple[str, str, str | None]] = {
    "kitchen_knife": ("PROP_KITCHEN_KNIFE_01", "inspect", "forensic_knife_match_01"),
    "letter_opener": ("PROP_LETTER_OPENER_01", "inspect", "forensic_letter_opener_01"),
    "scissors": ("PROP_SCISSORS_01", "inspect", "forensic_scissors_01"),
    "vase_01": ("PROP_VASE_01", "", None),
    "apartment_table": ("PROP_TABLE_01", "", None),
    "apartment_door": ("DOOR_APARTMENT_01", "", None),
    "apartment_lamp": ("PROP_LAMP_01", "", None),
    "apartment_laptop": ("PROP_LAPTOP_01", "read", "email_thomas_01"),
    # ADV-222 (Phase 19C §5): the VICTIM BODY carries the canonical
    # BODY_FIRST_FOUND_AT record (``body_found_01``) so the golden EASY world
    # has at least one discoverable, time-bearing evidence fact a player can
    # reach by interacting with a placed object (the body) — WHEN is then
    # derivable from evidence alone, exactly like WHO/WHY/WEAPON. In driver
    # worlds the id does not survive the canonical projection and the body
    # degrades DECORATIVE (the driver's general rule, unchanged).
    "victim_body_placeholder": ("PROP_BODY_PLACEHOLDER_01", "inspect", "body_found_01"),
}


# --------------------------------------------------------------------------- #
# builtin deterministic procedural spec provider (the /dev-mode showcase path)
# --------------------------------------------------------------------------- #
#
# The four Phase 13 golden declarative specs (unusual lab rack, antique
# ceremonial letter opener, custom trophy, distinctive desk award) are
# APPLICATION-OWNED declarative data shipped with the world package. This
# provider is what makes the office/mansion showcase prompts resolve their
# unknown objects deterministically with zero network and zero configuration.
# It is OFF by default for the Phase 13 explicit unknown-asset-request gate
# (that gate stays opt-in) — here it is the documented fallback a prompt
# naturally goes through when it names a known-but-uncatalogued object.

_KNOWN_PROCEDURAL_SPECS: Mapping[str, str] = {
    "antique ceremonial letter opener": """{
  "canonicalName": "Antique Ceremonial Letter Opener",
  "category": "decor",
  "subtype": "ceremonial_letter_opener",
  "dimensions": {"x": 0.12, "y": 0.1, "z": 0.28},
  "parts": [
    {"id": "part_00", "role": "blade", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.09},
                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.06, "y": 0.09, "z": 0.2}},
     "material": "metal.brass"},
    {"id": "part_01", "role": "guard", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": 0.0, "z": -0.02},
                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.14, "y": 0.06, "z": 0.06}},
     "material": "metal.brass"},
    {"id": "part_02", "role": "handle", "primitive": "cylinder",
     "transform": {"position": {"x": 0.0, "y": 0.0, "z": -0.14},
                   "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.06, "y": 0.06, "z": 0.16}},
     "material": "wood.dark"}
  ]
}""",
    "unusual laboratory sample rack": """{
  "canonicalName": "Unusual Laboratory Sample Rack",
  "category": "utility",
  "subtype": "sample_rack",
  "dimensions": {"x": 0.5, "y": 0.4, "z": 0.4},
  "parts": [
    {"id": "part_00", "role": "frame", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": -0.1, "z": 0.0},
                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.5, "y": 0.08, "z": 0.4}},
     "material": "metal.steel"},
    {"id": "part_01", "role": "rail", "primitive": "cylinder",
     "transform": {"position": {"x": -0.18, "y": 0.0, "z": 0.0},
                   "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.07, "y": 0.07, "z": 0.44}},
     "material": "metal.steel", "parentId": "part_00"},
    {"id": "part_02", "role": "rail", "primitive": "cylinder",
     "transform": {"position": {"x": -0.06, "y": 0.0, "z": 0.0},
                   "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.07, "y": 0.07, "z": 0.44}},
     "material": "metal.steel", "parentId": "part_00"},
    {"id": "part_03", "role": "rail", "primitive": "cylinder",
     "transform": {"position": {"x": 0.06, "y": 0.0, "z": 0.0},
                   "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.07, "y": 0.07, "z": 0.44}},
     "material": "metal.steel", "parentId": "part_00"},
    {"id": "part_04", "role": "rail", "primitive": "cylinder",
     "transform": {"position": {"x": 0.18, "y": 0.0, "z": 0.0},
                   "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.07, "y": 0.07, "z": 0.44}},
     "material": "metal.steel", "parentId": "part_00"}
  ]
}""",
    "custom trophy": """{
  "canonicalName": "Custom Trophy",
  "category": "decor",
  "subtype": "trophy",
  "dimensions": {"x": 0.3, "y": 0.5, "z": 0.3},
  "parts": [
    {"id": "part_00", "role": "base", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": -0.22, "z": 0.0},
                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.3, "y": 0.08, "z": 0.22}},
     "material": "wood.dark"},
    {"id": "part_01", "role": "stem", "primitive": "cylinder",
     "transform": {"position": {"x": 0.0, "y": -0.06, "z": 0.0},
                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.08, "y": 0.3, "z": 0.08}},
     "material": "metal.brass", "parentId": "part_00"},
    {"id": "part_02", "role": "cup", "primitive": "cylinder",
     "transform": {"position": {"x": 0.0, "y": 0.17, "z": 0.0},
                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.18, "y": 0.14, "z": 0.18}},
     "material": "metal.brass", "parentId": "part_01"}
  ]
}""",
    "distinctive desk award": """{
  "canonicalName": "Distinctive Desk Award",
  "category": "decor",
  "subtype": "desk_award",
  "dimensions": {"x": 0.24, "y": 0.32, "z": 0.24},
  "parts": [
    {"id": "part_00", "role": "base", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": -0.11, "z": 0.0},
                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.24, "y": 0.08, "z": 0.18}},
     "material": "plastic"},
    {"id": "part_01", "role": "plaque", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.18, "y": 0.18, "z": 0.06}},
     "material": "metal.brass", "parentId": "part_00"},
    {"id": "part_02", "role": "figure", "primitive": "sphere",
     "transform": {"position": {"x": 0.0, "y": 0.16, "z": 0.02},
                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                   "scale": {"x": 0.16, "y": 0.16, "z": 0.16}},
     "material": "metal.steel", "parentId": "part_01"}
  ]
}""",
}


class KnownObjectSpecProvider:
    """Deterministic app-owned asset-spec provider (the /dev-mode defaults).

    Implements ``app.assets.spec_provider.AssetSpecProvider``: returns the
    shipped declarative spec for the four known uncatalogued objects and a
    content-less miss for every other requested name (safe fail).
    """

    def __init__(self) -> None:
        self.calls: list[AssetSpecRequest] = []

    def generate(self, request: AssetSpecRequest) -> AssetSpecResponse:
        if not isinstance(request, AssetSpecRequest):
            raise TypeError("generate requires an AssetSpecRequest")
        self.calls.append(request)
        content = _KNOWN_PROCEDURAL_SPECS.get(
            request.requested_name.casefold().strip()
        )
        if content is None:
            return AssetSpecResponse(content=None)
        return AssetSpecResponse(content=content)


# --------------------------------------------------------------------------- #
# typed outcomes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResolvedObject:
    """One resolved object (provenance locked)."""

    asset_id: str
    provenance: str
    definition: GeneratedAssetDefinition | None = None
    catalog_version: int | None = None
    requested_name: str = ""
    variant: str | None = None


@dataclass(frozen=True)
class WorldComposition:
    """The deterministic outcome of a world composition.

    ``placements`` are the full (kit-valid) placement specs; ``new_objects``
    are the ObjectSpecs the caller must append to the draft's public objects;
    ``provenance_by_object_id`` maps every placed object (base + new) to its
    Asset Oracle provenance value; ``relation_satisfied`` maps each relation
    kind to the bound object ids that satisfied it after placement;
    ``resolution_record`` is a diagnostic-only mapping NEVER serialized;
    ``issues`` is the sanitized WORld validation bucket (non-empty -> the
    composition is NOT publishable without repair);

    ``composition_notes`` (ADV-153) is a PLAYER-SAFE, bounded tuple of short
    sanitized notes (max ``MAX_COMPOSITION_NOTES``, max
    ``MAX_COMPOSITION_NOTE_LENGTH`` chars each): one per DECORATIVE unseen
    object that could not be generated or placed — it was left out of the
    world (the successfully resolved objects stay). A REQUIRED unresolved
    object never produces a note: it produces a blocking ``world.*`` issue
    (repair then FAILED, never silently dropped).
    """

    environment_id: str
    kit_version: int
    environment_provenance: str
    placements: tuple[PlacementSpec, ...]
    new_objects: tuple[ObjectSpec, ...]
    provenance_by_object_id: Mapping[str, str]
    relation_satisfied: Mapping[str, tuple[str, ...]]
    relation_notes: tuple[str, ...]
    resolution_record: Mapping[str, Any]
    issues: tuple[str, ...]
    composition_notes: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _placement_facts(
    placements: Sequence[Any],
) -> dict[str, tuple[str, str, str | None]]:
    """object_id -> (asset_id, interaction, evidence_id) from evidence pods."""
    out: dict[str, tuple[str, str, str | None]] = {}
    for placement in placements:
        if isinstance(placement, Mapping):
            if "objectId" in placement:
                object_id = placement.get("objectId")
                asset_id = placement.get("assetId")
                interaction = placement.get("interaction", "")
                evidence_id = placement.get("evidenceId")
            else:
                object_id = placement.get("object_id")
                asset_id = placement.get("asset_id")
                interaction = placement.get("interaction", "")
                evidence_id = placement.get("evidence_id")
        else:
            object_id = getattr(placement, "object_id", None)
            asset_id = getattr(placement, "asset_id", None)
            interaction = getattr(placement, "interaction", "")
            evidence_id = getattr(placement, "evidence_id", None)
        if object_id is None or asset_id is None:
            continue
        out[str(object_id)] = (
            str(asset_id),
            str(interaction or "") if interaction is not None else "",
            str(evidence_id) if evidence_id is not None else None,
        )
    return out


def _to_asset_request(request: Any) -> AssetRequest:
    """Map a typed ``ObjectRequest`` to the Asset Oracle's ``AssetRequest``.

    ``evidence_id`` / ``variant_params`` are composer-side concerns and do not
    enter the Oracle request; variant params are applied afterwards through
    ``resolve_with_variant`` (Phase 12).
    """
    if isinstance(request, AssetRequest):
        return request
    variant_params = dict(getattr(request, "variant_params", ()) or ())
    if variant_params:
        # Variant application is handled by the caller; the base request never
        # carries them.
        request = dataclasses.replace(request, variant_params=())
    return AssetRequest(
        requested_name=str(getattr(request, "requested_name", "")),
        category_hint=getattr(request, "category_hint", None),
        subtype_hint=getattr(request, "subtype_hint", None),
        tags=tuple(getattr(request, "tags", ()) or ()),
        required_interaction=getattr(request, "required_interaction", None),
        required_evidence_capabilities=tuple(
            getattr(request, "required_evidence_capabilities", ()) or ()
        ),
    )


def _asset_anchor_meta(
    asset_id: str, definition: GeneratedAssetDefinition | None, catalog: Catalog
) -> tuple[str, tuple[str, ...]]:
    """(category, allowed anchor types) of a catalog or procedural asset."""
    if definition is not None:
        return generated_asset_anchor_meta(definition, catalog)
    descriptor = catalog.by_id.get(asset_id)
    if descriptor is None:
        return ("", ())
    return (descriptor.category, descriptor.allowed_anchors)


def _relation_hint_for(
    kind: str,
    asset_id: str,
    definition: GeneratedAssetDefinition | None,
    kit: EnvironmentKit,
    catalog: Catalog,
) -> str | None:
    """The deterministic anchor-type hint (or None when unusable).

    ``near_victim`` never hard-hints: proximity is evaluated after placement.
    For every other relation the FIRST preference type that (a) exists in the
    kit, (b) is allowed for the asset's anchor types and (c) allows the asset
    category on some kit anchor becomes the hint; ``None`` keeps natural
    allocation.
    """
    if kind == "near_victim":
        return None
    category, allowed_anchors = _asset_anchor_meta(asset_id, definition, catalog)
    for candidate in RELATION_PREFERENCE_TYPES.get(
        kind, (RELATION_TO_ANCHOR_TYPES[kind],)
    ):
        candidate_anchors = [a for a in kit.anchors if a.type == candidate]
        if not candidate_anchors:
            continue
        if allowed_anchors and candidate not in allowed_anchors:
            continue
        if category and not any(category in a.allowed_categories for a in candidate_anchors):
            continue
        return candidate
    return None


def _object_from_placement(
    placement: PlacementSpec, subtype: str | None
) -> ObjectSpec:
    return ObjectSpec(
        object_id=placement.object_id,
        asset_id=placement.asset_id,
        affordances=("INSPECTABLE",),
        subtype=subtype,
    )


def _definition_json(definition: GeneratedAssetDefinition | None) -> Mapping[str, Any] | None:
    if definition is None:
        return None
    return definition.to_definition_json()


def _world_issue(category: str, message: str) -> str:
    return f"world.{category}: {message}"


def _decorative_unresolved_note(requested_name: str) -> str:
    """Player-safe 'left out' note for ONE DECORATIVE unseen object (ADV-153).

    Deterministic and sanitized: no prompt echo beyond the noun itself (the
    casefolded, punctuation-stripped requested name, truncated), no URLs /
    path separators / traversal / executable word tokens, bounded length
    (<= ``MAX_COMPOSITION_NOTE_LENGTH``). Used as a non-blocking world warning
    when a decorative object cannot be generated or placed.
    """
    cleaned = "".join(
        ch
        for ch in str(requested_name).casefold()
        if ch.isalnum() or ch == " "
    )
    label = " ".join(cleaned.split())[:60].strip("'\"")
    if not label or safe_string_issues(label, "compositionNote"):
        return "an item you described is not currently available - it was left out"
    return f"the '{label}' you described is not currently available - it was left out"


def _spec_adapter_flag(provider: Any, name: str) -> bool:
    """Read a flag attribute off a (possibly wrapped) asset spec provider.

    ``compose_world`` wraps the caller's provider in ``BoundedSpecProvider``,
    so a ceiling flag recorded on the underlying driver spec adapter
    (``OllamaAssetSpecProvider.failed_asset_threshold_hit``) must be read
    through the wrapper's ``inner`` reference. Absent providers/flags read
    False (no ceiling state recorded -> the bounded fallback may skip).
    """
    if provider is None:
        return False
    target = getattr(provider, "inner", provider)
    return bool(getattr(target, name, False))


# --------------------------------------------------------------------------- #
# compose_world
# --------------------------------------------------------------------------- #


def compose_world(
    world_reqs: WorldRequirements,
    env_resolver: Callable[[str], Any] | None = None,
    oracle: Any = None,
    spec_provider: AssetSpecProvider | None = None,
    cache: Any = None,
    evidence_placements: Sequence[Any] = (),
    *,
    catalog: Catalog | None = None,
    kit: EnvironmentKit | None = None,
    environment_id: str | None = None,
    environment_provenance: str | None = "EXACT",
) -> WorldComposition:
    """Deterministically compose the world for ``world_reqs`` on a kit.

    See the module docstring for the placement / provenance / safety rules.
    Returns a ``WorldComposition`` whose ``issues`` tuple is the sanitized
    WORld validation bucket (empty == composable). Never raises for
    domain-recoverable problems.
    """
    if not isinstance(world_reqs, WorldRequirements):
        raise TypeError("compose_world requires a WorldRequirements")
    if catalog is None:
        catalog = load_catalog_from_repo()
    # Phase 14_5 — provider-call budget: one bounded provider wrapper per
    # composition (a service-hoisted BoundedSpecProvider is reused as-is so
    # every repair pass of ONE attempt shares a single budget).
    if spec_provider is not None and not isinstance(spec_provider, BoundedSpecProvider):
        spec_provider = BoundedSpecProvider(
            spec_provider, call_limit=SPEC_PROVIDER_CALL_LIMIT
        )
    resolver: Callable[[str], Any] = (
        env_resolver if env_resolver is not None else _default_env_resolver
    )
    if kit is None:
        hint = world_reqs.environment_hint or FALLBACK_ENVIRONMENT_ID
        resolution = resolver(str(hint))
        if not getattr(resolution, "resolved", True):
            # Ambiguous/unresolvable environment request: NO arbitrary winner
            # (documented safe behavior); the world stays uncomposable.
            return WorldComposition(
                environment_id="",
                kit_version=0,
                environment_provenance=str(getattr(resolution, "provenance", "UNKNOWN")),
                placements=(),
                new_objects=(),
                provenance_by_object_id={},
                relation_satisfied={kind: () for kind in RELATION_KINDS},
                relation_notes=(),
                resolution_record={
                    "environmentId": "",
                    "environmentProvenance": "UNRESOLVED",
                    "unsafeUnsupported": tuple(world_reqs.unsafe_unsupported),
                    "resolved": {},
                },
                issues=(
                    _world_issue(
                        "environment-mismatch",
                        "environment request is ambiguous; no kit selected",
                    ),
                ),
            )
        kit = load_environment(str(resolution.environment_id))
        environment_id = kit.environment_id
        environment_provenance = str(getattr(resolution, "provenance", "EXACT"))
    if not isinstance(kit, EnvironmentKit):
        raise TypeError("compose_world requires an EnvironmentKit")
    environment_id = environment_id or kit.environment_id
    environment_provenance = environment_provenance or "EXACT"

    issues: list[str] = []
    resolution_record: dict[str, Any] = {
        "environmentId": environment_id,
        "environmentProvenance": environment_provenance,
        "unsafeUnsupported": tuple(world_reqs.unsafe_unsupported),
        "resolved": {},
    }
    generated_definitions: dict[str, GeneratedAssetDefinition] = {}

    def _resolve_prompt_object(request: Any) -> ResolvedObject | None:
        """One Oracle resolution (catalog/variant/procedural) or None.

        Phase 14_5 escalation order: EXACT -> ALIAS -> SEMANTIC (with
        confidence; low-confidence + variantParams -> PARAMETRIC_VARIANT) ->
        no adequate match -> AssetSpecProvider -> strict validate/compile ->
        ``proc.*`` + PROCEDURAL_GENERATED -> explicit FALLBACK only when the
        provider yields nothing valid. A CRITICALITY_REQUIRED object whose
        only catalog answer is a LOW-CONFIDENCE semantic match (a DIFFERENT
        semantic class, e.g. "ice pick" tied onto an unrelated sharp object
        by a tag) is NEVER substituted: it escalates to the provider, and
        when that fails the caller records ``world.unresolved-object`` (the
        generation lifecycle repairs or fails publication — never a drop).
        """
        asset_request = _to_asset_request(request)
        criticality = str(getattr(request, "criticality", CRITICALITY_DECORATIVE))
        semantic_name = str(getattr(request, "requested_name", ""))
        resolve_started = 0.0
        try:
            import time

            resolve_started = time.perf_counter()
        except Exception:  # noqa: BLE001 - timing never changes resolution
            pass
        # Phase 19 §8/§13 — sanitized resolve events (structure only).
        emit_event(
            "asset.resolve.started",
            semanticObjectId=semantic_name or None,
            stage="world_compose",
            elapsedMs=0,
        )
        try:
            outcome = resolve_or_generate(
                asset_request,
                spec_provider=spec_provider,
                cache=cache,
                catalog=catalog,
            )
            resolution = outcome.resolution
            low_conf_semantic = (
                resolution is not None
                and resolution.provenance is Provenance.SEMANTIC_MATCH
                and resolution.confidence is not None
                and resolution.confidence < CRITICAL_MIN_SEMANTIC_CONFIDENCE
            )
            if low_conf_semantic and criticality == CRITICALITY_REQUIRED:
                # SEMANTIC BUT LOSSY: escalate to the provider; never substitute.
                outcome = resolve_or_generate(
                    asset_request,
                    spec_provider=spec_provider,
                    cache=cache,
                    catalog=catalog,
                    force_generate=True,
                )
                if outcome.generated is None:
                    return None
                resolution = outcome.resolution
        except StageDriverProviderFailure as exc:
            # ADV-213: a TYPED provider failure (budget/deadline/timeout) is
            # NEVER absorbed by the degrade-safe fallback — it must reach the
            # driver/controller so the attempt fails with the narrow canonical
            # cause (per-asset exhaustion attributable to this semantic
            # object, global exhaustion terminal). Unknown exceptions still
            # degrade safely (never a crash).
            # ADV-220: a DECORATIVE object whose PER-ASSET call budget is
            # exhausted follows the bounded fallback policy instead of being
            # terminal on the FIRST occurrence. The provider already counted
            # the failed asset (mark_failed_asset, exactly once, monotonic),
            # so the object is SKIPPED with the player-safe composition note
            # exactly like the return-style (content-invalid) decorative
            # fallback path; NOTHING is published silently and evidence
            # integrity is never weakened. The terminal condition for the
            # decorative path is MAX_FAILED_ASSETS_PER_GENERATION: when
            # recording this NEW failure exceeds the ceiling the provider
            # raises the driver's ``failed_asset_threshold_hit`` flag, and the
            # attempt fails with the CEILING code (MAX_FAILED_ASSETS_EXCEEDED)
            # — never the per-asset budget code, never a partial publish.
            # REQUIRED/essential assets, GLOBAL exhaustion and every other
            # typed failure stay terminal exactly as before (the narrow
            # per-asset code for essential evidence, the generic code for a
            # global ceiling hit, regardless of criticality).
            if (
                criticality == CRITICALITY_DECORATIVE
                and getattr(exc, "code", None)
                == GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED.value
            ):
                if _spec_adapter_flag(spec_provider, "failed_asset_threshold_hit"):
                    raise StageDriverProviderFailure(
                        "maximum failed assets exceeded",
                        code=GenerationFailureCode.MAX_FAILED_ASSETS_EXCEEDED,
                    ) from None
                # bounded fallback: left out with a player-safe note — the
                # caller below records it exactly like an unresolvable
                # decorative object (never a blocking issue, never a silent
                # wrong substitution).
                return None
            raise
        except Exception:  # noqa: BLE001 - degraded, never a crash
            return None
        if outcome.generated is not None:
            generated = outcome.generated
            if generated.definition is not None:
                generated_definitions[generated.asset_id] = generated.definition
            emit_event(
                "asset.resolve.complete",
                semanticObjectId=semantic_name or None,
                stage="world_compose",
                assetId=generated.asset_id,
                provenance=Provenance.PROCEDURAL_GENERATED.value,
                resolved=True,
                elapsedMs=(
                    int((time.perf_counter() - resolve_started) * 1000)
                    if resolve_started else 0
                ),
            )
            return ResolvedObject(
                asset_id=generated.asset_id,
                provenance=Provenance.PROCEDURAL_GENERATED.value,
                definition=generated.definition,
                catalog_version=catalog.catalog_version,
                requested_name=semantic_name,
            )
        if (
            resolution is None
            or not resolution.resolved
            or resolution.provenance is Provenance.FALLBACK
        ):
            emit_event(
                "asset.resolve.complete",
                semanticObjectId=semantic_name or None,
                stage="world_compose",
                assetId=None,
                provenance=(
                    resolution.provenance.value
                    if resolution is not None
                    else "UNRESOLVED"
                ),
                resolved=False,
                elapsedMs=(
                    int((time.perf_counter() - resolve_started) * 1000)
                    if resolve_started else 0
                ),
            )
            return None
        # Phase 12 bounded parametric variant (optional; never fabricates).
        variant_params = dict(getattr(request, "variant_params", ()) or ())
        variant_note: str | None = None
        if variant_params:
            try:
                variant = resolve_with_variant(
                    asset_request, variant_params, catalog=catalog
                )
                resolution = dataclasses.replace(
                    resolution, provenance=Provenance.PARAMETRIC_VARIANT
                )
                variant_note = "PARAMETRIC_VARIANT"
            except Exception:  # noqa: BLE001 - variant bad -> fall back to base
                variant_note = None
        emit_event(
            "asset.resolve.complete",
            semanticObjectId=semantic_name or None,
            stage="world_compose",
            assetId=resolution.asset_id,
            provenance=resolution.provenance.value,
            resolved=True,
            elapsedMs=(
                int((time.perf_counter() - resolve_started) * 1000)
                if resolve_started else 0
            ),
        )
        if resolution.provenance is Provenance.CATALOG_ALIAS:
            emit_event(
                "asset.alias.resolved",
                semanticObjectId=semantic_name or None,
                stage="world_compose",
                assetId=resolution.asset_id,
                matchedAlias=resolution.matched_alias or None,
            )
        return ResolvedObject(
            asset_id=resolution.asset_id,
            provenance=resolution.provenance.value,
            definition=None,
            catalog_version=resolution.catalog_version,
            requested_name=semantic_name,
            variant=variant_note,
        )

    # 1. base placements (golden facts verbatim, per-kit selection).
    facts = _placement_facts(evidence_placements)
    base_ids = KIT_BASE_OBJECT_IDS.get(environment_id, tuple(KIT_BASE_OBJECT_IDS["apartment"]))
    base_plans: list[dict[str, Any]] = []
    for object_id in base_ids:
        fact = facts.get(object_id, _GOLDEN_OBJECT_FACTS.get(object_id))
        if fact is None:
            continue
        asset_id, interaction, evidence_id = fact
        base_plans.append(
            {
                "object_id": object_id,
                "asset_id": asset_id,
                "interaction": interaction,
                "evidence_id": evidence_id,
                "is_new": False,
                "hint": None,
                "requested_name": object_id,
            }
        )

    # 2. prompt object resolution + dedupe against the base set. Each request
    #    resolves INDEPENDENTLY (ADV-153): the successfully resolved objects
    #    ALWAYS stay in the composition; an unresolvable DECORATIVE object is
    #    left out with a player-safe note (never a blocking issue, never a
    #    silent wrong substitution), an unresolvable REQUIRED object keeps the
    #    blocking ``world.unresolved-object`` issue (repair -> terminal fail).
    base_assets = {plan["asset_id"] for plan in base_plans}
    new_plans: list[dict[str, Any]] = []
    seen_new_assets: set[str] = set()
    composition_notes: list[str] = []
    for request in world_reqs.objects:
        resolved = _resolve_prompt_object(request)
        requested_name = str(getattr(request, "requested_name", ""))
        if resolved is None:
            resolution_record["resolved"][requested_name] = {
                "assetId": None,
                "provenance": "UNRESOLVED",
            }
            criticality = str(
                getattr(request, "criticality", CRITICALITY_DECORATIVE)
            )
            if criticality == CRITICALITY_REQUIRED:
                issues.append(
                    _world_issue(
                        "unresolved-object",
                        f"requested object {requested_name!r} has no catalog or "
                        "procedural resolution",
                    )
                )
            else:
                note = _decorative_unresolved_note(requested_name)
                if note not in composition_notes:
                    composition_notes.append(note)
            continue
        resolution_record["resolved"][requested_name] = {
            "assetId": resolved.asset_id,
            "provenance": resolved.provenance,
        }
        if resolved.asset_id in base_assets or resolved.asset_id in seen_new_assets:
            # dedupe: the golden base (or an earlier prompt request) already
            # supplies this asset; the solver-critical links stay put.
            continue
        seen_new_assets.add(resolved.asset_id)
        hint = _relation_hint_for_plan(request, resolved, kit, catalog, world_reqs)
        new_plans.append(
            {
                "object_id": None,  # assigned below from the requested name
                "asset_id": resolved.asset_id,
                "interaction": str(getattr(request, "required_interaction", "") or ""),
                "evidence_id": (
                    str(getattr(request, "evidence_id", None))
                    if getattr(request, "evidence_id", None) is not None
                    else None
                ),
                "is_new": True,
                "hint": hint,
                "requested_name": requested_name,
                "resolved": resolved,
            }
        )

    # Deterministic object ids for the new placements.
    used_ids = {plan["object_id"] for plan in base_plans}
    for plan in new_plans:
        plan["object_id"] = _new_object_id(
            plan["requested_name"], plan["asset_id"], used_ids, catalog
        )
        used_ids.add(plan["object_id"])

    # 3. placement (single deterministic allocation authority).
    placed = _place_plans(kit, base_plans + new_plans, catalog, generated_definitions)
    if placed is None:
        issues.append(
            _world_issue(
                "invalid-placement",
                "no valid placement exists for the requested world set "
                "(anchor capacity / category constraints)",
            )
        )
        return WorldComposition(
            environment_id=environment_id,
            kit_version=kit.version,
            environment_provenance=environment_provenance,
            placements=(),
            new_objects=(),
            provenance_by_object_id={},
            relation_satisfied={kind: () for kind in RELATION_KINDS},
            relation_notes=(),
            resolution_record=resolution_record,
            issues=tuple(sorted(set(issues))),
        )

    placed_by_object = {p.object_id: p for p in placed}
    generated_ids = {
        p.object_id for p in placed if is_procedural_asset_id(p.asset_id)
    }

    # 4. provenance per placed object (base + new).
    provenance_by_object_id: dict[str, str] = {}
    for plan in base_plans + new_plans:
        if plan["object_id"] not in placed_by_object:
            continue
        if plan["is_new"]:
            provenance_by_object_id[plan["object_id"]] = plan["resolved"].provenance
        else:
            provenance_by_object_id[plan["object_id"]] = _catalog_provenance(
                plan["asset_id"], catalog
            )

    # 5. self-validation: placement contract + evidence interactions.
    placement_issues = validate_placement(
        kit, placed, catalog=catalog, generated_definitions=generated_definitions
    )
    issues.extend(
        _world_issue("invalid-placement", message) for message in placement_issues
    )
    for p in placed:
        if p.evidence_id is not None and not p.interaction:
            issues.append(
                _world_issue(
                    "evidence-interaction",
                    f"evidence-linked object {p.object_id!r} requires a "
                    "non-empty interaction",
                )
            )
        if p.evidence_id is not None and placed_by_object[p.object_id].anchor is not None:
            anchor = kit.by_id.get(p.anchor)
            from app.environments.placer import EVIDENCE_CAPABLE_TYPES

            if anchor is not None and anchor.type not in EVIDENCE_CAPABLE_TYPES:
                issues.append(
                    _world_issue(
                        "unreachable-evidence",
                        f"evidence-linked object {p.object_id!r} is not on an "
                        "evidence-capable anchor",
                    )
                )

    # 6. relation satisfaction (post-placement, honest).
    relation_satisfied: dict[str, list[str]] = {kind: [] for kind in RELATION_KINDS}
    relation_notes: list[str] = []
    body_anchors = [a for a in kit.anchors if a.type == "BODY"]
    for plan in base_plans + new_plans:
        placed_item = placed_by_object.get(plan["object_id"])
        if placed_item is None:
            continue
        anchor = kit.by_id.get(placed_item.anchor)
        if anchor is None:
            continue
        for relation in world_reqs.relations:
            name = plan.get("requested_name") or plan["object_id"]
            if relation.target and relation.target != name.casefold():
                continue
            if relation.target == "" and plan.get("requested_name"):
                continue
            satisfied = _relation_satisfied(relation.kind, anchor, body_anchors, kit)
            if satisfied:
                if plan["object_id"] not in relation_satisfied[relation.kind]:
                    relation_satisfied[relation.kind].append(plan["object_id"])
            else:
                relation_notes.append(
                    f"relation {relation.kind!r} not satisfied by "
                    f"{plan['object_id']!r} (anchor {anchor.anchor_id!r} "
                    f"type {anchor.type!r})"
                )

    # 7. output placement specs (embedded generated definitions for proc).
    placement_specs: list[PlacementSpec] = []
    for p in placed:
        definition = generated_definitions.get(p.asset_id)
        placement_specs.append(
            PlacementSpec(
                object_id=p.object_id,
                asset_id=p.asset_id,
                location_id=p.location_id,
                anchor=p.anchor,
                interaction=p.interaction,
                evidence_id=p.evidence_id,
                generated_definition=_definition_json(definition),
            )
        )

    # 8. new public objects (decorative supporting props).
    new_objects: list[ObjectSpec] = []
    for plan in new_plans:
        placed_item = placed_by_object.get(plan["object_id"])
        if placed_item is None:
            continue
        definition = plan["resolved"].definition
        subtype: str | None = None
        if definition is not None and isinstance(definition.subtype, str):
            subtype = definition.subtype
        else:
            descriptor = catalog.by_id.get(plan["asset_id"])
            subtype = descriptor.subtype if descriptor is not None else None
        new_objects.append(
            ObjectSpec(
                object_id=plan["object_id"],
                asset_id=plan["asset_id"],
                affordances=("INSPECTABLE",),
                subtype=subtype,
            )
        )
    # 8b. record whether any relation hint fell back to natural allocation.
    unfilled = {kind for kind, ids in relation_satisfied.items() if not ids}
    if unfilled:
        resolution_record["unsatisfiedRelations"] = tuple(sorted(unfilled))

    return WorldComposition(
        environment_id=environment_id,
        kit_version=kit.version,
        environment_provenance=environment_provenance,
        placements=tuple(placement_specs),
        new_objects=tuple(new_objects),
        provenance_by_object_id=provenance_by_object_id,
        relation_satisfied={kind: tuple(ids) for kind, ids in relation_satisfied.items()},
        relation_notes=tuple(sorted(set(relation_notes))),
        resolution_record=resolution_record,
        issues=tuple(sorted(set(issues))),
        composition_notes=tuple(composition_notes[:MAX_COMPOSITION_NOTES]),
    )


def _default_env_resolver(name: str) -> Any:
    from app.environments.resolver import resolve_environment

    return resolve_environment(name)


def _relation_hint_for_plan(
    request: Any,
    resolved: ResolvedObject,
    kit: EnvironmentKit,
    catalog: Catalog,
    world_reqs: WorldRequirements,
) -> str | None:
    """Deterministic relation hint for one prompt request (or None)."""
    for relation in world_reqs.relations:
        target_name = str(getattr(request, "requested_name", ""))
        if relation.target and relation.target != target_name.casefold():
            continue
        if relation.target == "":
            continue
        return _relation_hint_for(
            relation.kind, resolved.asset_id, resolved.definition, kit, catalog
        )
    return None


def _relation_satisfied(
    kind: str,
    anchor: Any,
    body_anchors: Sequence[Any],
    kit: EnvironmentKit,
) -> bool:
    """Whether the placed anchor satisfies the relation's mapped type."""
    mapped = RELATION_TO_ANCHOR_TYPES[kind]
    if kind == "near_victim":
        if not body_anchors:
            return False
        return any(
            anchor.position.distance_to(body.position) <= NEAR_VICTIM_PROXIMITY
            for body in body_anchors
        )
    return anchor.type == mapped


def _catalog_provenance(asset_id: str, catalog: Catalog) -> str:
    descriptor = catalog.by_id.get(asset_id)
    if descriptor is None:
        return Provenance.FALLBACK.value
    return Provenance.CATALOG_EXACT.value


def _new_object_id(
    requested_name: str,
    asset_id: str,
    used_ids: set[str],
    catalog: Catalog,
) -> str:
    """Deterministic object id for a NEW (prompt-specific) placement.

    Phase 19 Fix B — SEMANTIC OBJECT IDENTITY is authoritative: the object id
    is derived from the SEMANTIC requested name (e.g. ``antique brass letter
    opener`` -> ``antique_brass_letter_opener``), NEVER from the RENDER asset
    identity (the resolver may map the request onto the catalog ``letter
    opener`` — the render/asset layer — while the semantic public object keeps
    its own id so CaseTruth / evidence / solver / accusation never lose it).
    For requests whose semantic name IS the catalog canonical name the result
    is byte-identical to the previous catalog-slug behavior; a trailing
    ``_2`` keeps ids unique.
    """
    candidate = _slugify(requested_name)
    if not candidate:
        descriptor = catalog.by_id.get(asset_id)
        if descriptor is not None:
            candidate = _slugify(descriptor.canonical_name)
        if not candidate:
            candidate = "prop"
    if candidate not in used_ids:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used_ids:
        suffix += 1
    return f"{candidate}_{suffix}"


def _slugify(text: str) -> str:
    lowered = "".join(ch for ch in str(text).casefold() if ch.isalnum() or ch in " _-")
    words = [w for w in lowered.replace("-", " ").split() if w]
    slug = " ".join(words)[:40].replace(" ", "_").strip("_")
    return slug if slug else "prop"


def _place_plans(
    kit: EnvironmentKit,
    plans: Sequence[Mapping[str, Any]],
    catalog: Catalog,
    generated_definitions: Mapping[str, GeneratedAssetDefinition],
) -> tuple[Any, ...] | None:
    """Deterministic placement (try hints; natural fallback). None on failure."""
    base_requests = [
        PlacementRequest(
            asset_id=plan["asset_id"],
            object_id=plan["object_id"],
            interaction=plan["interaction"],
            evidence_id=plan["evidence_id"],
        )
        for plan in plans
    ]
    hinted_requests = [
        PlacementRequest(
            asset_id=plan["asset_id"],
            object_id=plan["object_id"],
            interaction=plan["interaction"],
            evidence_id=plan["evidence_id"],
            anchor_type_hint=plan.get("hint"),
            category_hint=(
                plan["resolved"].definition.category
                if plan.get("resolved") is not None
                and plan["resolved"].definition is not None
                else None
            ),
        )
        for plan in plans
    ]
    attempts = [hinted_requests] if any(r.anchor_type_hint for r in hinted_requests) else []
    attempts.append(base_requests)
    for requests in attempts:
        try:
            placed = place_objects(
                kit, requests, catalog=catalog, generated_definitions=generated_definitions
            )
            if validate_placement(
                kit, placed, catalog=catalog, generated_definitions=generated_definitions
            ) == ():
                return placed
        except PlacementError:
            continue
    return None


__all__ = [
    "CRITICAL_MIN_SEMANTIC_CONFIDENCE",
    "KIT_BASE_OBJECT_IDS",
    "KnownObjectSpecProvider",
    "MAX_COMPOSITION_NOTES",
    "MAX_COMPOSITION_NOTE_LENGTH",
    "NEAR_VICTIM_PROXIMITY",
    "RELATION_PREFERENCE_TYPES",
    "ResolvedObject",
    "SPEC_PROVIDER_CALL_LIMIT",
    "SemanticObjectResolutionError",
    "WorldComposition",
    "compose_world",
    "is_base_object_request",
]