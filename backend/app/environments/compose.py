"""Phase 11 — environment-aware world-graph composition (dev provider path).

``compose_world_graph_for_kit`` and ``scene_for_kit`` turn a resolved kit into
the player-safe world material of a published draft:

- the world graph's LOCATIONS are the kit's zones (``locationId`` = zone id),
- the world graph's PLACEMENTS re-anchor the SAME evidence-relevant objects
  the golden case uses (kitchen_knife / letter_opener / scissors / laptop +
  the five decorative env objects) through ``app.environments.placer`` on the
  kit's semantic anchors; interactions and evidence links follow the Phase 10
  contract (evidence-linked placements keep their non-empty interaction and
  evidenceId; decorative placements keep ``""`` / ``None``),
- the SCENE keeps its solver-critical ``location_id`` unchanged (the Phase 11
  "solver unaffected" invariant: deduction depends on the crime scene's
  location identity, which must NOT move when the kit changes — proven by the
  golden's opportunity-exclusion rules), while its NAME is derived from the
  kit and the ``environment_id`` carries the kit identity.

The composed world graph passes the existing validation suite by construction
(same object ids, same evidence ids, catalog-registered assets, anchors inside
the extended ``ANCHOR_ALLOWLIST``, kit zone location ids declared by the
graph's own locations list).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable

from app.assets.catalog import Catalog, load_catalog_from_repo
from app.environments.manifests import EnvironmentKit
from app.environments.placer import PlacementRequest, place_objects
from app.generation.schemas import (
    PlacementSpec,
    SceneSpec,
    WorldGraphLocationSpec,
    WorldGraphSpec,
)


def compose_world_graph_for_kit(
    kit: EnvironmentKit,
    golden_placements: Iterable[Any],
    *,
    catalog: Catalog | None = None,
) -> WorldGraphSpec:
    """Build the kit world graph that re-anchors ``golden_placements``.

    Placements keep their objectId / assetId / interaction / evidenceId (the
    golden contract) and receive a kit anchor + the anchor's zone as
    locationId. Raising any placement-contract violation surfaces loudly (a
    kit must always accommodate the golden object set).
    """
    if catalog is None:
        catalog = load_catalog_from_repo()
    if not isinstance(kit, EnvironmentKit):
        raise TypeError("compose_world_graph_for_kit requires an EnvironmentKit")

    requests: list[PlacementRequest] = []
    for placement in golden_placements:
        asset_id = getattr(placement, "asset_id", None)
        if asset_id is None:
            asset_id = placement.get("assetId") if isinstance(placement, dict) else None
        if asset_id is None:
            raise ValueError("golden placement carries no asset_id")
        asset = catalog.by_id.get(str(asset_id))
        requests.append(
            PlacementRequest(
                asset_id=str(asset_id),
                category_hint=asset.category if asset is not None else None,
                object_id=getattr(placement, "object_id", None)
                or (placement.get("objectId") if isinstance(placement, dict) else None),
                interaction=getattr(placement, "interaction", "")
                or (placement.get("interaction", "") if isinstance(placement, dict) else ""),
                evidence_id=getattr(placement, "evidence_id", None)
                or (placement.get("evidenceId") if isinstance(placement, dict) else None),
            )
        )

    placed = place_objects(kit, requests, catalog=catalog)
    locations = tuple(
        WorldGraphLocationSpec(
            location_id=zone.zone_id,
            template=f"{kit.environment_id}_template",
            rooms=zone.rooms,
        )
        for zone in kit.zones
    )
    placements = tuple(
        PlacementSpec(
            object_id=item.object_id,
            asset_id=item.asset_id,
            location_id=item.location_id,
            anchor=item.anchor,
            interaction=item.interaction,
            evidence_id=item.evidence_id,
        )
        for item in placed
    )
    return WorldGraphSpec(locations=locations, placements=placements)


def scene_for_kit(
    kit: EnvironmentKit,
    current_scene: SceneSpec | None,
) -> SceneSpec:
    """The published scene spec of a resolved kit.

    ``location_id`` is ALWAYS the current (solver-fixed) scene location —
    Phase 11 changes only the world-graph PLACEMENT, the presentational name
    and the kit identity, never the deduction-relevant scene location.
    ``name`` keeps the golden name for the golden apartment kit and otherwise
    reads ``"{canonicalName} - {spawn zone label}"`` from the kit.
    """
    current_location = (
        current_scene.location_id
        if isinstance(current_scene, SceneSpec)
        else "miller_apartment_kitchen"
    )
    if kit.environment_id == "apartment":
        name = current_scene.name if isinstance(current_scene, SceneSpec) else "Miller Apartment - Kitchen"
    else:
        spawn_anchor = kit.by_id.get(kit.spawn.anchor_id)
        zone_label = (
            kit.zones_by_id[spawn_anchor.zone_id].label
            if spawn_anchor is not None and spawn_anchor.zone_id in kit.zones_by_id
            else "Main Area"
        )
        name = f"{kit.canonical_name} - {zone_label}"
    return SceneSpec(
        location_id=current_location,
        name=name,
        environment_id=kit.environment_id,
    )


def replace_draft_scene_and_world_graph(
    draft: Any,
    world_graph: WorldGraphSpec,
    scene: SceneSpec,
) -> Any:
    """Frozen ``GeneratedDraft`` with the composed world graph + scene.

    Every other draft section (crime / persons / motives / objects /
    locations / travel rules / evidence) is byte-identical — CaseTruth and the
    evidence set never change on this path.
    """
    return dataclasses.replace(
        draft,
        world_graph=world_graph,
        scene=scene,
    )


__all__ = [
    "compose_world_graph_for_kit",
    "replace_draft_scene_and_world_graph",
    "scene_for_kit",
]