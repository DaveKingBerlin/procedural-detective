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
  crash. An unresolvable request (unknown name with no procedural fallback)
  is a world issue, and a KNOWN-UNSAFE request is recorded in the resolution
  record and NOT composed (safe fail).

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
    AssetSpecProvider,
    AssetSpecRequest,
    AssetSpecResponse,
)
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
from app.generation.schemas import ObjectSpec, PlacementSpec
from app.world.extract import is_base_object_request
from app.world.requirements import (
    RELATION_KINDS,
    RELATION_TO_ANCHOR_TYPES,
    WorldRequirements,
)

# --------------------------------------------------------------------------- #
# documented constants
# --------------------------------------------------------------------------- #

# Proximity (world units) inside which a near_victim-bound object is satisfied.
NEAR_VICTIM_PROXIMITY = 5.0

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
    "victim_body_placeholder": ("PROP_BODY_PLACEHOLDER_01", "", None),
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
    ``issues`` is the sanitized WORld validation bucket.
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
        """One Oracle resolution (catalog/variant/procedural) or None."""
        asset_request = _to_asset_request(request)
        try:
            outcome = resolve_or_generate(
                asset_request,
                spec_provider=spec_provider,
                cache=cache,
                catalog=catalog,
            )
        except Exception:  # noqa: BLE001 - degraded, never a crash
            return None
        resolution = outcome.resolution
        if outcome.generated is not None:
            generated = outcome.generated
            if generated.definition is not None:
                generated_definitions[generated.asset_id] = generated.definition
            return ResolvedObject(
                asset_id=generated.asset_id,
                provenance=Provenance.PROCEDURAL_GENERATED.value,
                definition=generated.definition,
                catalog_version=catalog.catalog_version,
                requested_name=str(getattr(request, "requested_name", "")),
            )
        if (
            resolution is None
            or not resolution.resolved
            or resolution.provenance is Provenance.FALLBACK
        ):
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
        return ResolvedObject(
            asset_id=resolution.asset_id,
            provenance=resolution.provenance.value,
            definition=None,
            catalog_version=resolution.catalog_version,
            requested_name=str(getattr(request, "requested_name", "")),
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

    # 2. prompt object resolution + dedupe against the base set.
    base_assets = {plan["asset_id"] for plan in base_plans}
    new_plans: list[dict[str, Any]] = []
    seen_new_assets: set[str] = set()
    for request in world_reqs.objects:
        resolved = _resolve_prompt_object(request)
        requested_name = str(getattr(request, "requested_name", ""))
        if resolved is None:
            resolution_record["resolved"][requested_name] = {
                "assetId": None,
                "provenance": "UNRESOLVED",
            }
            issues.append(
                _world_issue(
                    "unresolved-object",
                    f"requested object {requested_name!r} has no catalog or "
                    "procedural resolution",
                )
            )
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

    Prefers the catalog-derived slug (like ``place_objects``) and falls back
    to a slug from the requested name; a trailing ``_2`` keeps ids unique.
    """
    candidate = _slugify(requested_name)
    descriptor = catalog.by_id.get(asset_id)
    if descriptor is not None:
        candidate = _slugify(descriptor.canonical_name)
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
    "KIT_BASE_OBJECT_IDS",
    "KnownObjectSpecProvider",
    "NEAR_VICTIM_PROXIMITY",
    "RELATION_PREFERENCE_TYPES",
    "ResolvedObject",
    "WorldComposition",
    "compose_world",
    "is_base_object_request",
]