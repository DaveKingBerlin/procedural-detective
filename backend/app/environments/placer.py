"""Phase 11 — deterministic placement validator + object placer.

Two responsibilities:

- ``validate_placement(kit, placements)`` — deterministic validation of a set
  of placements against a kit (used by the world composer AND the adversarial
  "anchor/category confusion" checks). Documented rules:

  * an EXCLUSIVE anchor may be occupied at most once;
  * an anchor may only host catalog assets whose category is inside the
    anchor's ``allowedCategories`` (incompatible category rejected) and whose
    catalog ``allowedAnchors`` include the anchor's semantic type (incompatible
    anchor type rejected);
  * small evidence must not be placed where it is inaccessible/unpickable:
    an evidence-bearing object (catalog category ``evidence`` OR carrying an
    ``evidenceId``) may only sit on an evidence-capable anchor type
    {BODY, FLOOR_EVIDENCE, DESK_EVIDENCE, TABLE_PROP, COMPUTER, DOCUMENT,
    WALL_EVIDENCE, STORAGE, GENERIC_PROP} — DOOR/WINDOW/CCTV/ACCESS_CONTROL/
    PLAYER_SPAWN anchors are never evidence-capable;
  * evidence-bearing objects keep at least 0.3 units of separation (Euclidean,
    anchor positions);
  * the BODY anchor stays navigable: the player spawn is never within 0.8 of
    a BODY anchor;
  * the player spawn never intersects ANY anchor position (>= 0.4 clearance).

- ``place_objects(kit, requests)`` — deterministic assignment of catalog asset
  requests to free anchors: requests are sorted internally by ``assetId``
  (then generated/declared objectId), each request is assigned first-fit over
  anchors sorted by ``anchorId``; exclusive anchors are used at most once,
  non-exclusive anchors prefer an unused anchor and fall back to the first
  compatible anchor (the golden itself stacks props on a shared dining table).
  Equal inputs always produce equal outputs — including under a SHUFFLED
  request order (the internal sort makes the result order-independent).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.assets.catalog import Catalog, load_catalog_from_repo
from app.environments.manifests import (
    ANCHOR_TYPES,
    AnchorSpec,
    EnvironmentKit,
)

# Phase 13 — procedural (proc.*) asset id grammar (mirror of
# ``app.assets.compiler.PROCEDURAL_ASSET_PATTERN``; kept local like the other
# id grammars in this package). A lockstep test pins the grammars together.
_PROCEDURAL_ASSET_RE = re.compile(r"^proc\.[a-z0-9_]+\.[a-f0-9]{16}$")

# The documented evidence-capable anchor types (a small evidence item must be
# reachable/pickable on one of these; DOOR/WINDOW/CCTV/ACCESS_CONTROL/
# PLAYER_SPAWN are never evidence-bearing slots).
EVIDENCE_CAPABLE_TYPES: frozenset[str] = frozenset(
    {
        "BODY",
        "FLOOR_EVIDENCE",
        "DESK_EVIDENCE",
        "TABLE_PROP",
        "COMPUTER",
        "DOCUMENT",
        "WALL_EVIDENCE",
        "STORAGE",
        "GENERIC_PROP",
    }
)

MIN_EVIDENCE_SPACING = 0.3
BODY_SPAWN_CLEARANCE = 0.8
SPAWN_ANCHOR_CLEARANCE = 0.4


class PlacementError(ValueError):
    """An object could not be placed (no compatible/free anchor) or a
    placement set violates the placement contract."""


def is_procedural_asset_id(asset_id: object) -> bool:
    """True when ``asset_id`` is a procedural (proc.*) generated asset id."""
    return isinstance(asset_id, str) and bool(_PROCEDURAL_ASSET_RE.match(asset_id))


def generated_asset_anchor_meta(
    definition: Any, catalog: Catalog
) -> tuple[str, tuple[str, ...]]:
    """Deterministic (category, allowed_anchors) of a generated definition.

    The anchor compatibility of a generated asset is derived from the FIRST
    catalog descriptor (manifest order) of the SAME category — the exact
    semantic of "a catalog asset of this category would sit here". The same
    category ALWAYS yields the same anchor set, so ``place_objects`` stays as
    deterministic for generated assets as for catalog assets (an unknown
    category yields an empty anchor set, which the placer surfaces as a
    PlacementError — never a silent placement).
    """
    category = _definition_category(definition)
    for asset in catalog.assets:
        if asset.category == category:
            return category, asset.allowed_anchors
    return category, ()


def _definition_category(definition: Any) -> str:
    if isinstance(definition, Mapping):
        return str(definition.get("category") or "")
    return str(getattr(definition, "category", "") or "")


@dataclass(frozen=True)
class PlacementRequest:
    """One trusted placement request (catalog asset + optional hints).

    ``object_id`` / ``interaction`` / ``evidence_id`` are optional: the world
    composer supplies them to preserve the golden contract (evidence-linked
    objects keep their published interactions and evidence links); standalone
    callers may omit them and receive deterministic generated ids / the
    decorative ``""`` interaction.
    """

    asset_id: str
    anchor_type_hint: str | None = None
    category_hint: str | None = None
    object_id: str | None = None
    interaction: str = ""
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id:
            raise ValueError("PlacementRequest.asset_id must be a non-empty string")
        if self.anchor_type_hint is not None and self.anchor_type_hint not in ANCHOR_TYPES:
            raise ValueError(
                f"PlacementRequest.anchor_type_hint {self.anchor_type_hint!r} is not "
                "in the ANCHOR_TYPES vocabulary"
            )
        if not isinstance(self.interaction, str):
            raise ValueError("PlacementRequest.interaction must be a string")


@dataclass(frozen=True)
class PlacedObject:
    """One deterministic placement result (anchor-bound world object)."""

    object_id: str
    asset_id: str
    location_id: str
    anchor: str
    interaction: str
    evidence_id: str | None = None


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_SUFFIX_RE = re.compile(r"[^a-z0-9_]")


def default_object_id(asset_id: str, catalog: Catalog, occurrence: int = 1) -> str:
    """Deterministic objectId from a catalog asset (slug + occurrence suffix).

    ``occurrence`` starts at 1; ids like ``kitchen_knife`` /
    ``kitchen_knife_2`` keep the output unique within one placement set.
    """
    asset = catalog.by_id.get(asset_id)
    if asset is None:
        return _ID_SUFFIX_RE.sub("_", asset_id.lower()).strip("_")[:40]
    slug = _SLUG_RE.sub("_", asset.canonical_name.casefold()).strip("_")[:40] or "prop"
    return slug if occurrence <= 1 else f"{slug}_{occurrence}"


def _as_request(item: Any) -> PlacementRequest:
    if isinstance(item, PlacementRequest):
        return item
    if isinstance(item, Mapping):
        def _pick(camel: str, snake: str, default: Any = None) -> Any:
            if camel in item:
                return item[camel]
            if snake in item:
                return item[snake]
            return default

        return PlacementRequest(
            asset_id=str(_pick("assetId", "asset_id", "")),
            anchor_type_hint=_pick("anchorTypeHint", "anchor_type_hint"),
            category_hint=_pick("categoryHint", "category_hint"),
            object_id=_pick("objectId", "object_id"),
            interaction=str(_pick("interaction", "interaction", "") or ""),
            evidence_id=_pick("evidenceId", "evidence_id"),
        )
    raise TypeError(
        f"placement request must be a PlacementRequest or mapping; got {type(item).__name__}"
    )


def _placed_value(placement: Any, attr: str, key: str) -> Any:
    if isinstance(placement, Mapping):
        return placement.get(key, placement.get(attr))
    return getattr(placement, attr, None)


def _catalog() -> Catalog:
    return load_catalog_from_repo()


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def _evidence_bearing(
    placement: Any, catalog: Catalog, generated_definitions: Mapping[str, Any] | None = None
) -> bool:
    """Evidence-bearing = catalog category 'evidence' OR carries an evidenceId.
    A procedural (proc.*) asset is evidence-bearing when its definition's
    category is 'evidence'."""
    asset_id = _placed_value(placement, "asset_id", "assetId")
    if asset_id is not None:
        str_id = str(asset_id)
        if catalog.by_id.get(str_id, None) is not None:
            if catalog.by_id[str_id].category == "evidence":
                return True
        if generated_definitions and str_id in generated_definitions:
            if _definition_category(generated_definitions[str_id]) == "evidence":
                return True
    evidence_id = _placed_value(placement, "evidence_id", "evidenceId")
    return evidence_id is not None and str(evidence_id) != ""


def validate_placement(
    kit: EnvironmentKit,
    placements: Iterable[Any],
    *,
    catalog: Catalog | None = None,
    generated_definitions: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Validate a placement set against ``kit``; return sorted issue strings.

    Never raises and performs no I/O. See the module docstring for the exact
    documented rules. An empty tuple means the placements are valid.

    Phase 13: ``generated_definitions`` (``assetId -> GeneratedAssetDefinition``
    object or its serialized dict) lets procedural (proc.*) placements be
    validated with the same category/anchor/evidence rules as catalog assets —
    their category and allowed anchor types are derived deterministically from
    the catalog (``generated_asset_anchor_meta``). A proc.* placement WITHOUT a
    known definition is an issue (never silently treated as a catalog asset).
    """
    if catalog is None:
        catalog = _catalog()
    issues: list[str] = []
    placed: list[dict[str, Any]] = []
    for index, placement in enumerate(placements):
        where = f"placements[{index}]"
        asset_id = _placed_value(placement, "asset_id", "assetId")
        anchor_id = _placed_value(placement, "anchor", "anchor")
        if not isinstance(asset_id, str) or not asset_id:
            issues.append(f"{where}: missing assetId")
        if not isinstance(anchor_id, str) or not anchor_id:
            issues.append(f"{where}: missing anchor")
            continue
        anchor = kit.by_id.get(anchor_id)
        if anchor is None:
            issues.append(f"{where}: unknown anchor {anchor_id!r} in kit {kit.environment_id!r}")
            continue

        asset = None
        category: str | None = None
        allowed_anchors: tuple[str, ...] = ()
        if isinstance(asset_id, str) and asset_id in catalog.by_id:
            asset = catalog.by_id[asset_id]
            category = asset.category
            allowed_anchors = asset.allowed_anchors
        elif (
            isinstance(asset_id, str)
            and is_procedural_asset_id(asset_id)
            and generated_definitions is not None
            and asset_id in generated_definitions
        ):
            category, allowed_anchors = generated_asset_anchor_meta(
                generated_definitions[asset_id], catalog
            )
        else:
            issues.append(
                f"{where}: asset {asset_id!r} is not in the asset catalog"
            )
            continue
        if category is None:
            issues.append(f"{where}: asset {asset_id!r} has no resolved category")
            continue
        if category not in anchor.allowed_categories:
            issues.append(
                f"{where}: asset category {category!r} is not allowed on anchor "
                f"{anchor_id!r} (allowed {sorted(anchor.allowed_categories)!r})"
            )
        if anchor.type not in allowed_anchors:
            issues.append(
                f"{where}: anchor type {anchor.type!r} cannot host asset {asset_id!r} "
                f"(asset allows {sorted(allowed_anchors)!r})"
            )
        if _evidence_bearing(placement, catalog, generated_definitions) and anchor.type not in EVIDENCE_CAPABLE_TYPES:
            issues.append(
                f"{where}: evidence on anchor {anchor_id!r} (type {anchor.type!r}) is "
                "inaccessible/unpickable"
            )
        placed.append(
            {
                "index": index,
                "object_id": _placed_value(placement, "object_id", "objectId"),
                "asset_id": asset_id,
                "anchor_id": anchor_id,
                "evidence": _evidence_bearing(placement, catalog, generated_definitions),
            }
        )

    # Exclusive occupancy (once only).
    exclusive_counts: dict[str, list[int]] = {}
    for entry in placed:
        anchor = kit.by_id[entry["anchor_id"]]
        if anchor.exclusive:
            exclusive_counts.setdefault(entry["anchor_id"], []).append(entry["index"])
    for anchor_id in sorted(exclusive_counts):
        indices = exclusive_counts[anchor_id]
        if len(indices) > 1:
            issues.append(
                f"duplicate occupancy of exclusive anchor {anchor_id!r} at "
                + ", ".join(f"placements[{i}]" for i in indices)
            )

    # Evidence minimum spacing (>= 0.3 Euclidean between anchor positions).
    evidence_entries = [
        entry
        for entry in placed
        if entry["evidence"] and not any(
            issue.startswith(f"placements[{entry['index']}]: unknown anchor")
            or issue.startswith(f"placements[{entry['index']}]: missing anchor")
            for issue in issues
        )
    ]
    evidence_anchors = []
    for entry in evidence_entries:
        anchor = kit.by_id.get(entry["anchor_id"])
        if anchor is not None:
            evidence_anchors.append((entry["index"], anchor))
    for i in range(len(evidence_anchors)):
        i_index, i_anchor = evidence_anchors[i]
        for j in range(i + 1, len(evidence_anchors)):
            j_index, j_anchor = evidence_anchors[j]
            distance = i_anchor.position.distance_to(j_anchor.position)
            if distance < MIN_EVIDENCE_SPACING:
                issues.append(
                    f"evidence-bearing objects at placements[{i_index}] and "
                    f"placements[{j_index}] are only {distance:g} apart "
                    f"(minimum {MIN_EVIDENCE_SPACING:g})"
                )

    # BODY navigability: spawn stays >= 0.8 from every BODY anchor.
    for anchor in kit.anchors:
        if anchor.type == "BODY":
            if kit.spawn.position.distance_to(anchor.position) < BODY_SPAWN_CLEARANCE:
                issues.append(
                    f"BODY anchor {anchor.anchor_id!r} is within "
                    f"{BODY_SPAWN_CLEARANCE:g} of the player spawn (must stay navigable)"
                )

    # Spawn never intersects ANY anchor position (>= 0.4).
    for anchor in kit.anchors:
        if anchor.anchor_id == kit.spawn.anchor_id:
            continue
        if kit.spawn.position.distance_to(anchor.position) < SPAWN_ANCHOR_CLEARANCE:
            issues.append(
                f"player spawn intersects anchor {anchor.anchor_id!r} "
                f"(< {SPAWN_ANCHOR_CLEARANCE:g} clearance)"
            )

    return tuple(sorted(set(issues)))


# --------------------------------------------------------------------------- #
# placement
# --------------------------------------------------------------------------- #


def place_objects(
    kit: EnvironmentKit,
    requests: Iterable[Any],
    *,
    catalog: Catalog | None = None,
    generated_definitions: Mapping[str, Any] | None = None,
) -> tuple[PlacedObject, ...]:
    """Deterministically assign catalog-asset requests to free kit anchors.

    - requests are sorted by ``(assetId, objectId or '')`` so the outcome is
      IDENTICAL when the caller shuffles the request order;
    - each request is first-fit over anchors sorted by ``anchorId``;
    - an EXCLUSIVE anchor is used at most once;
    - a non-exclusive anchor PREFERS an unused anchor and otherwise falls back
      to the first compatible anchor (multiple props may share it, matching
      the golden's dining-table stacking);
    - an explicit ``anchor_type_hint`` RESTRICTS the pool to exactly that type
      (no silent fallback: unsatisfiable hints raise ``PlacementError``).

    Phase 13: ``generated_definitions`` (``assetId -> GeneratedAssetDefinition``
    object or serialized dict) lets procedural (proc.*) asset requests be placed
    with the SAME anchor contract as catalog assets (category + allowed anchor
    types derived deterministically from the catalog) — a generated asset can
    therefore be placed into any kit that hosts its category.

    Returns frozen ``PlacedObject`` tuples whose ``location_id`` is the anchor's
    zone id and whose ``object_id`` is the request's id (or a deterministic
    catalog-derived slug). The result is validated and any placement-contract
    violation raises ``PlacementError`` (the kits are designed to always pass).
    """
    if catalog is None:
        catalog = _catalog()
    if not isinstance(kit, EnvironmentKit):
        raise TypeError("place_objects requires an EnvironmentKit")

    normalized: list[PlacementRequest] = [_as_request(r) for r in requests]
    # Deterministic request order (assetId, then declared/generated id).
    normalized.sort(key=lambda r: (r.asset_id, r.object_id or ""))

    used_exclusive: set[str] = set()
    anchor_occupancy: dict[str, list[str]] = {}
    placed: list[PlacedObject] = []
    generated_used: set[str] = set()
    slug_occurrence: dict[str, int] = {}

    for request in normalized:
        asset = catalog.by_id.get(request.asset_id)
        if asset is not None:
            category = asset.category
            allowed_anchors = asset.allowed_anchors
        elif (
            is_procedural_asset_id(request.asset_id)
            and generated_definitions is not None
            and request.asset_id in generated_definitions
        ):
            category, allowed_anchors = generated_asset_anchor_meta(
                generated_definitions[request.asset_id], catalog
            )
            if not category:
                raise PlacementError(
                    f"generated asset {request.asset_id!r} has no resolvable category"
                )
        else:
            raise PlacementError(
                f"asset {request.asset_id!r} is not in the asset catalog"
            )
        pool = [
            anchor
            for anchor in kit.anchors
            if anchor.type in allowed_anchors
        ]
        if request.anchor_type_hint is not None:
            hinted = [a for a in pool if a.type == request.anchor_type_hint]
            if not hinted:
                raise PlacementError(
                    f"no anchor of type {request.anchor_type_hint!r} can host "
                    f"{request.asset_id!r} in kit {kit.environment_id!r}"
                )
            pool = hinted
        if not pool:
            raise PlacementError(
                f"no anchor in kit {kit.environment_id!r} can host {request.asset_id!r} "
                f"(asset allows {sorted(allowed_anchors)!r})"
            )

        # Category compatibility restrict: an anchor must allow the asset's
        # category (category-capable anchors first, then any compatible).
        compatible = [
            a for a in pool if category in a.allowed_categories
        ]
        if not compatible:
            raise PlacementError(
                f"no anchor allows category {category!r} for "
                f"{request.asset_id!r} in kit {kit.environment_id!r}"
            )

        def _free_exclusive(a: AnchorSpec) -> bool:
            return not a.exclusive or a.anchor_id not in used_exclusive

        available = [a for a in compatible if _free_exclusive(a)]
        if not available:
            raise PlacementError(
                f"no free anchor for {request.asset_id!r} in kit "
                f"{kit.environment_id!r} (exclusive anchors are all occupied)"
            )
        # Prefer an unused compatible anchor; fall back to the first
        # compatible non-exclusive anchor (multi-occupancy, golden pattern).
        anchor = next(
            (a for a in sorted(available, key=lambda a: a.anchor_id) if a.anchor_id not in anchor_occupancy),
            sorted(available, key=lambda a: a.anchor_id)[0],
        )

        object_id = request.object_id
        if not object_id:
            slug = default_object_id(request.asset_id, catalog)
            occurrence = slug_occurrence.get(slug, 1)
            candidate = slug if occurrence == 1 else f"{slug}_{occurrence}"
            while candidate in generated_used:
                occurrence += 1
                candidate = f"{slug}_{occurrence}"
            slug_occurrence[slug] = occurrence + 1
            generated_used.add(candidate)
            object_id = candidate

        if anchor.exclusive:
            used_exclusive.add(anchor.anchor_id)
        anchor_occupancy.setdefault(anchor.anchor_id, []).append(object_id)
        placed.append(
            PlacedObject(
                object_id=object_id,
                asset_id=request.asset_id,
                location_id=anchor.zone_id,
                anchor=anchor.anchor_id,
                interaction=request.interaction,
                evidence_id=request.evidence_id,
            )
        )

    result = tuple(placed)
    issues = validate_placement(
        kit, result, catalog=catalog, generated_definitions=generated_definitions
    )
    if issues:
        raise PlacementError("; ".join(issues))
    return result


__all__ = [
    "BODY_SPAWN_CLEARANCE",
    "EVIDENCE_CAPABLE_TYPES",
    "MIN_EVIDENCE_SPACING",
    "SPAWN_ANCHOR_CLEARANCE",
    "PlacedObject",
    "PlacementError",
    "PlacementRequest",
    "default_object_id",
    "generated_asset_anchor_meta",
    "is_procedural_asset_id",
    "place_objects",
    "validate_placement",
]