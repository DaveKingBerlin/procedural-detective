import type { InvestigationBootstrapResponse, WorldObjectDTO } from "../api/types";
import type { Vec3 } from "./apartment";
import type { AssetEntry, AssetPrimitiveKind, AssetRegistry } from "./assetRegistry";
import { ASSET_REGISTRY, FALLBACK_ASSET } from "./assetRegistry";
import type { AnchorRegistry, AnchorTransform } from "./anchorRegistry";
import { ANCHOR_REGISTRY, resolveAnchor } from "./anchorRegistry";
import { parseInvestigationBootstrap } from "./validation";

/**
 * Pure, deterministic scene model builder (Phase 6 track 2).
 *
 * `buildInvestigationScene` turns a validated InvestigationBootstrapResponse
 * into a plain SCENE MODEL — no Babylon, no DOM, no randomness. It is fully
 * unit-testable as a pure function, and the Babylon glue consumes exactly
 * this model.
 *
 * Contract guarantees:
 *  - The server sends NO coordinates: every position/rotation/scale below
 *    comes from the application-owned anchor/asset registries (or a stable
 *    objectId hash), never from the payload.
 *  - Object identity is ALWAYS the backend objectId — never an array index,
 *    never React render order. The output world object array is sorted by
 *    objectId so any input ordering (or shuffle) yields the same model.
 *  - Unknown asset ids become the neutral fallback primitive, flagged with
 *    `unknownAsset` so the UI can surface a player-visible notice.
 */

export interface SceneWorldObject {
  objectId: string;
  assetId: string;
  assetType: string;
  subtype: string | null;
  primitiveKind: AssetPrimitiveKind;
  color: string;
  scale: Vec3;
  label: string | null;
  position: Vec3;
  rotation: Vec3;
  interaction: string;
  /** True only when the registry asset is interactable AND an interaction was published. */
  interactionWorks: boolean;
  evidenceId: string | null;
  discovered: boolean;
  read: boolean;
  /** True when the asset id was NOT in the application-owned registry. */
  unknownAsset: boolean;
}

export interface InvestigationSceneModel {
  location: { locationId: string; name: string };
  /** Sorted by objectId — stable identity regardless of server ordering. */
  worldObjects: SceneWorldObject[];
}

export function buildInvestigationScene(
  bootstrap: InvestigationBootstrapResponse,
  assetRegistry: AssetRegistry = ASSET_REGISTRY,
  anchorRegistry: AnchorRegistry = ANCHOR_REGISTRY,
): InvestigationSceneModel {
  // Re-validate at build time: the builder is the last safe gate before
  // geometry is derived from server-provided ids. Throws ValidationError on
  // malformed payloads (callers map that to the safe error state).
  const parsed = parseInvestigationBootstrap.validate(bootstrap);
  const scene = parsed.scene;

  const worldObjects = applySharedAnchorSpacing(
    scene.worldObjects.map((dto) => buildSceneWorldObject(dto, assetRegistry, anchorRegistry)),
  ).sort(compareByObjectId);

  return {
    location: { locationId: scene.location.locationId, name: scene.location.name },
    worldObjects,
  };
}

/**
 * Deterministic de-cramping of co-located world objects (Phase 8 E).
 *
 * The backend may place several objects at the same semantic anchor (the
 * golden case puts the vase ON the dining table — both use "dining_table").
 * A shared anchor resolves to a single position, which would stack objects
 * perfectly on top of each other. This pure helper separates such groups:
 *
 *  - the TALLEST object in the group keeps the anchor position exactly (it
 *    is the "surface");
 *  - every other object is lifted to sit ON that surface (deterministic y)
 *    and fanned out horizontally around it (deterministic x offset from the
 *    sorted objectId order).
 *
 * Everything is a pure function of the anchor transform + object scales/ids,
 * never of array order or server payload fields, so determinism and the
 * "registry-only geometry" rule both hold.
 */
export function applySharedAnchorSpacing(objects: SceneWorldObject[]): SceneWorldObject[] {
  const groups = new Map<string, SceneWorldObject[]>();
  for (const obj of objects) {
    const key = `${obj.position.x}|${obj.position.y}|${obj.position.z}`;
    const group = groups.get(key);
    if (group) {
      group.push(obj);
    } else {
      groups.set(key, [obj]);
    }
  }
  if (groups.size === objects.length) {
    return objects; // Nothing shares a spot — no spacing needed.
  }
  const spaced: SceneWorldObject[] = [];
  for (const group of groups.values()) {
    if (group.length === 1) {
      spaced.push(group[0]);
      continue;
    }
    const ordered = [...group].sort(compareByObjectId);
    let tallestIndex = 0;
    for (let i = 1; i < ordered.length; i++) {
      if (ordered[i].scale.y > ordered[tallestIndex].scale.y) tallestIndex = i;
    }
    const surface = ordered[tallestIndex];
    const surfaceTop = surface.position.y + surface.scale.y / 2;
    const others = ordered.filter((obj) => obj !== surface);
    spaced.push(surface);
    const count = others.length;
    others.forEach((obj, index) => {
      const spread = (index - (count - 1) / 2) * 0.55;
      spaced.push({
        ...obj,
        position: {
          x: surface.position.x + spread,
          y: Math.max(surfaceTop + obj.scale.y / 2, surface.position.y),
          z: surface.position.z,
        },
      });
    });
  }
  return spaced;
}

function compareByObjectId(a: SceneWorldObject, b: SceneWorldObject): number {
  if (a.objectId < b.objectId) return -1;
  if (a.objectId > b.objectId) return 1;
  return 0;
}

function buildSceneWorldObject(
  dto: WorldObjectDTO,
  assetRegistry: AssetRegistry,
  anchorRegistry: AnchorRegistry,
): SceneWorldObject {
  const knownEntry = assetRegistry.get(dto.assetId);
  const entry: AssetEntry = knownEntry ?? FALLBACK_ASSET;
  const unknownAsset = knownEntry === undefined;
  const transform: AnchorTransform = anchorRegistry.get(dto.anchor) ?? resolveAnchor(dto.anchor, dto.objectId);
  const rotation = transform.rotation ?? { x: 0, y: 0, z: 0 };

  return {
    objectId: dto.objectId,
    assetId: dto.assetId,
    assetType: dto.assetType,
    subtype: dto.subtype,
    primitiveKind: entry.primitiveKind,
    color: entry.color,
    scale: { x: entry.scale.x, y: entry.scale.y, z: entry.scale.z },
    // Unknown assets get no label: a neutral placeholder must never claim a name.
    label: unknownAsset ? null : entry.label,
    position: { x: transform.position.x, y: transform.position.y, z: transform.position.z },
    rotation,
    interaction: dto.interaction,
    interactionWorks: entry.interactable && dto.interaction !== "",
    evidenceId: dto.evidenceId,
    discovered: dto.discovered,
    read: dto.read,
    unknownAsset,
  };
}