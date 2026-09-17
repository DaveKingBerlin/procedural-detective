import type { AnchorRegistry, AnchorTransform } from "../scene/anchorRegistry";
import { ANCHOR_REGISTRY, fallbackAnchor, resolveAnchor } from "../scene/anchorRegistry";
import type { ScenePrimitive, Vec3 } from "../scene/apartment";
import { buildApartmentManifest } from "../scene/apartment";
import { getAsset } from "../catalog/assetCatalog";
import type { EnvironmentKitDocument, KitAnchor, KitVec3 } from "./kitCatalog";
import { allKitIds, getKit, hasKit, isKitCatalogHealthy } from "./kitCatalog";

/**
 * Phase 11 Track B — kit geometry: deterministic shells + anchor transforms.
 *
 * TWO responsibilities:
 *
 * 1. {@link buildKitShell} — one deterministic SHELL per kit, built ONLY from
 *    the kit manifest + the bundled catalog:
 *     - zones/rooms become ONE shared floor plane covering the manifold's
 *       anchor extents (a deterministic margin around the min/max anchor
 *       positions) — recognizability over architectural accuracy;
 *     - the perimeter walls derive their GEOMETRY from the catalog's
 *       PROP_WALL_01 descriptor (height/thickness/color via the Asset Oracle
 *       → primitives) and their PLACEMENT from the kit anchor extents;
 *     - doors / windows / lamps are placed AT their manifest anchors (DOOR,
 *       WINDOW and the first GENERIC_PROP anchor respectively), using the
 *       catalog descriptors for size/color;
 *     - a shared overhead point light mirrors the apartment's light_01.
 *     - The APARTMENT kit (and any UNKNOWN kit id) returns the EXISTING
 *       Phase 2 apartment manifest UNCHANGED (byte-identical golden shell).
 *
 *    Every shell value is a pure function of the manifest + catalog, never of
 *    array order: anchor-derived placements are SORTED BY anchorId, so a
 *    shuffled manifest produces the identical shell.
 *
 * 2. {@link transformFor} / {@link kitAnchorTransform} — the ANCHOR TRANSFORM
 *    registry backing world-object placement:
 *     - the apartment kit keeps using the Phase 6 anchor table
 *       (ANCHOR_REGISTRY in src/scene/anchorRegistry.ts) — its values stay
 *       EXACTLY as today for the golden appearance;
 *     - every OTHER kit resolves transforms STRICTLY from the manifest
 *       (position + rotation verbatim in the camera-local space), never from
 *       array order;
 *     - unknown anchors in a known kit fall back to the stable objectId-hash
 *       slot; an unknown kit id falls back to the apartment geometry.
 *
 * Sanitization: no network, no remote assets, no executable strings — the
 * catalog + kit vocabularies were strictly validated at module load and only
 * declarative numbers/hex colors reach the renderer.
 */

/** The apartment kit id — the byte-identical golden geometry source. */
export const APARTMENT_KIT_ID = "apartment";

/** Deterministic shell margin around the kit's anchor extents (meters). */
const SHELL_PAD = 1.0;

/** Shared floor slab height (mirrors the apartment floor primitive). */
const FLOOR_SLAB_Y = 0.2;

/** Overhead light height (mirrors the apartment light_01). */
const LIGHT_Y = 2.7;

/** Application-owned deterministic shell palettes per kit (primitives only). */
const FLOOR_TONES: Readonly<Record<string, string>> = {
  office: "#5a6472", // cool corporate gray-blue
  hotel_suite: "#7a5a44", // warm timber
  warehouse: "#4b4f55", // raw concrete
  mansion: "#6b563e", // dark hardwood
};

/** Application-owned deterministic wall palettes per kit (primitives only). */
const WALL_TONES: Readonly<Record<string, string>> = {
  office: "#d8dce2",
  hotel_suite: "#e8dcc8",
  warehouse: "#c8ccd2",
  mansion: "#cfb394",
};

const DEFAULT_FLOOR_TONE = "#3b4252";
const DEFAULT_WALL_TONE = "#e8e2d8";
const OVERHEAD_LIGHT_COLOR = "#fff4e0";

/** Catalog ids consumed by the shell (all strictly validated v1 assets). */
const WALL_ASSET_ID = "PROP_WALL_01";
const DOOR_ASSET_ID = "DOOR_APARTMENT_01";
const WINDOW_ASSET_ID = "PROP_WINDOW_01";
const LAMP_ASSET_ID = "PROP_LAMP_01";

function firstHexColor(colors: Readonly<Record<string, string>>, fallback: string): string {
  const first = Object.values(colors)[0];
  return typeof first === "string" && first !== "" ? first : fallback;
}

function byAnchorId(a: KitAnchor, b: KitAnchor): number {
  if (a.anchorId < b.anchorId) return -1;
  if (a.anchorId > b.anchorId) return 1;
  return 0;
}

/* ======================================================================
 * Shell builder
 * ==================================================================== */

/**
 * The deterministic shell for a kit. `apartment` and any UNKNOWN kit id
 * return the EXISTING Phase 2 apartment manifest (byte-identical golden
 * appearance); the four other kits build their own room from the manifest.
 */
export function buildKitShell(kitId: string): ScenePrimitive[] {
  const kit = getKit(kitId);
  if (kitId === APARTMENT_KIT_ID || kit === undefined) {
    return buildApartmentManifest();
  }
  return buildKitShellFromManifest(kit);
}

/** Pure shell builder for one validated non-apartment kit. */
function buildKitShellFromManifest(kit: EnvironmentKitDocument): ScenePrimitive[] {
  const xs = kit.anchors.map((anchor) => anchor.position.x);
  const zs = kit.anchors.map((anchor) => anchor.position.z);
  const x0 = Math.min(...xs) - SHELL_PAD;
  const x1 = Math.max(...xs) + SHELL_PAD;
  const z0 = Math.min(...zs) - SHELL_PAD;
  const z1 = Math.max(...zs) + SHELL_PAD;
  const width = x1 - x0;
  const depth = z1 - z0;
  const centerX = (x0 + x1) / 2;
  const centerZ = (z0 + z1) / 2;

  // Perimeter walls derive their geometry from the catalog's wall descriptor.
  const wallAsset = getAsset(WALL_ASSET_ID);
  const wallHeight = wallAsset !== undefined ? wallAsset.dimensions.y : 2.5;
  const wallThickness = wallAsset !== undefined ? wallAsset.dimensions.z : 0.12;
  const wallColor =
    wallAsset !== undefined ? firstHexColor(wallAsset.colors, DEFAULT_WALL_TONE) : DEFAULT_WALL_TONE;

  const primitives: ScenePrimitive[] = [
    // One shared floor plane over the kit's local-space anchor extents.
    {
      id: "floor_01",
      kind: "floor",
      position: { x: centerX, y: 0, z: centerZ },
      scale: { x: width, y: FLOOR_SLAB_Y, z: depth },
      color: FLOOR_TONES[kit.environmentId] ?? DEFAULT_FLOOR_TONE,
    },
    // Shared overhead point light (mirrors the apartment light_01 pattern).
    {
      id: "light_01",
      kind: "light",
      position: { x: centerX, y: LIGHT_Y, z: centerZ },
      color: OVERHEAD_LIGHT_COLOR,
    },
    // Perimeter: south (+z), north (-z), east (+x), west (-x).
    {
      id: "wall_south",
      kind: "wall",
      position: { x: centerX, y: wallHeight / 2, z: z1 - wallThickness / 2 },
      scale: { x: width, y: wallHeight, z: wallThickness },
      color: WALL_TONES[kit.environmentId] ?? wallColor,
    },
    {
      id: "wall_north",
      kind: "wall",
      position: { x: centerX, y: wallHeight / 2, z: z0 + wallThickness / 2 },
      scale: { x: width, y: wallHeight, z: wallThickness },
      color: WALL_TONES[kit.environmentId] ?? wallColor,
    },
    {
      id: "wall_east",
      kind: "wall",
      position: { x: x1 - wallThickness / 2, y: wallHeight / 2, z: centerZ },
      scale: { x: wallThickness, y: wallHeight, z: depth },
      color: WALL_TONES[kit.environmentId] ?? wallColor,
    },
    {
      id: "wall_west",
      kind: "wall",
      position: { x: x0 + wallThickness / 2, y: wallHeight / 2, z: centerZ },
      scale: { x: wallThickness, y: wallHeight, z: depth },
      color: WALL_TONES[kit.environmentId] ?? wallColor,
    },
  ];

  // Doors at their DOOR anchors (sorted by anchorId — never array order).
  const doorAsset = getAsset(DOOR_ASSET_ID);
  const doorScale =
    doorAsset !== undefined
      ? { x: doorAsset.dimensions.x, y: doorAsset.dimensions.y, z: doorAsset.dimensions.z }
      : { x: 1.6, y: 2.2, z: 0.12 };
  const doorColor = doorAsset !== undefined ? firstHexColor(doorAsset.colors, "#7c4a21") : "#7c4a21";
  kit.anchors
    .filter((anchor) => anchor.type === "DOOR")
    .sort(byAnchorId)
    .forEach((anchor, index) => {
      primitives.push({
        id: `door_${index + 1}`,
        kind: "door",
        position: { x: anchor.position.x, y: anchor.position.y + doorScale.y / 2, z: anchor.position.z },
        rotation: { x: anchor.rotation.x, y: anchor.rotation.y, z: anchor.rotation.z },
        scale: { x: doorScale.x, y: doorScale.y, z: doorScale.z },
        color: doorColor,
      });
    });

  // Windows at their WINDOW anchors (sorted by anchorId).
  const windowAsset = getAsset(WINDOW_ASSET_ID);
  const windowScale =
    windowAsset !== undefined
      ? { x: windowAsset.dimensions.x, y: windowAsset.dimensions.y, z: windowAsset.dimensions.z }
      : { x: 1.4, y: 1.2, z: 0.06 };
  const windowColor =
    windowAsset !== undefined ? firstHexColor(windowAsset.colors, "#b8d0e8") : "#b8d0e8";
  kit.anchors
    .filter((anchor) => anchor.type === "WINDOW")
    .sort(byAnchorId)
    .forEach((anchor, index) => {
      primitives.push({
        id: `window_${index + 1}`,
        kind: "wall",
        position: { x: anchor.position.x, y: anchor.position.y + windowScale.y / 2, z: anchor.position.z },
        rotation: { x: anchor.rotation.x, y: anchor.rotation.y, z: anchor.rotation.z },
        scale: { x: windowScale.x, y: windowScale.y, z: windowScale.z },
        color: windowColor,
      });
    });

  // One floor lamp at the first GENERIC_PROP anchor (sorted by anchorId).
  const lampAnchor = kit.anchors
    .filter((anchor) => anchor.type === "GENERIC_PROP")
    .sort(byAnchorId)[0];
  if (lampAnchor !== undefined) {
    const lampAsset = getAsset(LAMP_ASSET_ID);
    const lampScale =
      lampAsset !== undefined
        ? { x: lampAsset.dimensions.x, y: lampAsset.dimensions.y, z: lampAsset.dimensions.z }
        : { x: 0.3, y: 1.4, z: 0.3 };
    const lampColor = lampAsset !== undefined ? firstHexColor(lampAsset.colors, "#3c4147") : "#3c4147";
    primitives.push({
      id: "lamp_01",
      kind: "table",
      position: { x: lampAnchor.position.x, y: lampAnchor.position.y + lampScale.y / 2, z: lampAnchor.position.z },
      rotation: { x: lampAnchor.rotation.x, y: lampAnchor.rotation.y, z: lampAnchor.rotation.z },
      scale: { x: lampScale.x, y: lampScale.y, z: lampScale.z },
      color: lampColor,
    });
  }

  return primitives;
}

/* ======================================================================
 * Anchor transform registry (manifest source)
 * ==================================================================== */

/** Per-kit anchorId -> anchor-descriptor index built once at load. */
const ANCHOR_INDEX: ReadonlyMap<string, ReadonlyMap<string, KitAnchor>> = (() => {
  const outer = new Map<string, ReadonlyMap<string, KitAnchor>>();
  if (isKitCatalogHealthy()) {
    for (const kitId of allKitIds()) {
      const kit = getKit(kitId);
      if (kit === undefined) continue;
      const inner = new Map<string, KitAnchor>();
      for (const anchor of kit.anchors) {
        inner.set(anchor.anchorId, anchor);
      }
      outer.set(kitId, inner);
    }
  }
  return outer;
})();

/**
 * Manifest transform lookup for ANY validated kit document (independent of
 * the loaded module state) — exported so tests can prove the registry is
 * array-order independent on shuffled manifests.
 */
export function anchorTransformFrom(kit: EnvironmentKitDocument, anchorId: string): AnchorTransform | null {
  const index = new Map<string, KitAnchor>(kit.anchors.map((anchor) => [anchor.anchorId, anchor] as const));
  const anchor = index.get(anchorId);
  if (anchor === undefined) {
    return null;
  }
  return {
    position: { x: anchor.position.x, y: anchor.position.y, z: anchor.position.z },
    rotation: { x: anchor.rotation.x, y: anchor.rotation.y, z: anchor.rotation.z },
  };
}

/**
 * Strictly manifest-sourced transform for one kit anchor. Returns null for
 * the apartment kit (its geometry comes from the Phase 6 anchor table), for
 * unknown kits and for unknown anchor ids.
 */
export function kitAnchorTransform(kitId: string, anchorId: string): AnchorTransform | null {
  if (kitId === APARTMENT_KIT_ID) return null;
  const inner = ANCHOR_INDEX.get(kitId);
  if (inner === undefined) return null;
  const anchor = inner.get(anchorId);
  if (anchor === undefined) return null;
  return {
    position: { x: anchor.position.x, y: anchor.position.y, z: anchor.position.z },
    rotation: { x: anchor.rotation.x, y: anchor.rotation.y, z: anchor.rotation.z },
  };
}

/**
 * The world-object transform resolver: known non-apartment kits resolve
 * strictly from the manifest; the apartment kit (and any unknown kit id)
 * resolve through the Phase 6 apartment anchor table; unknown anchors always
 * land on the stable objectId-hash slot.
 */
export function transformFor(
  kitId: string,
  anchorId: string,
  objectId: string,
  apartmentRegistry: AnchorRegistry = ANCHOR_REGISTRY,
): AnchorTransform {
  if (kitId !== APARTMENT_KIT_ID && hasKit(kitId)) {
    const fromManifest = kitAnchorTransform(kitId, anchorId);
    if (fromManifest !== null) return fromManifest;
    return fallbackAnchor(objectId);
  }
  return apartmentRegistry.get(anchorId) ?? resolveAnchor(anchorId, objectId);
}

/* ======================================================================
 * Lighting + camera profiles (renderer options source)
 * ==================================================================== */

/** One resolved kit lighting profile (renderer-intensity values). */
export interface KitLightingProfile {
  profile: string;
  keyIntensity: number;
  hemiIntensity: number;
  accentColor: string;
}

/**
 * The manifest lighting profile for a kit; null for the apartment kit (which
 * keeps its golden lighting exactly) and for unknown kits (apartment lights).
 */
export function lightingFor(kitId: string): KitLightingProfile | null {
  if (kitId === APARTMENT_KIT_ID || !hasKit(kitId)) return null;
  const kit = getKit(kitId);
  if (kit === undefined) return null;
  return {
    profile: kit.lighting.profile,
    keyIntensity: kit.lighting.keyIntensity,
    hemiIntensity: kit.lighting.hemiIntensity,
    accentColor: kit.lighting.accentColor,
  };
}

/** The spawn transform of a kit; null for the apartment kit (golden camera). */
export function spawnTransformFor(kitId: string): AnchorTransform | null {
  if (kitId === APARTMENT_KIT_ID || !hasKit(kitId)) return null;
  const kit = getKit(kitId);
  if (kit === undefined) return null;
  return {
    position: { x: kit.spawn.position.x, y: kit.spawn.position.y, z: kit.spawn.position.z },
    rotation: { x: kit.spawn.rotation.x, y: kit.spawn.rotation.y, z: kit.spawn.rotation.z },
  };
}

/** Default camera framing profile (target + orbit distance) for a kit. */
export interface KitCameraProfile {
  target: Vec3;
  distance: number;
}

/**
 * Deterministic camera framing for a non-apartment kit: the target is the
 * manifest SPAWN (lifted slightly above floor level); the orbit distance is
 * derived from the kit's anchor extents so the whole room fits in view.
 * Returns null for the apartment kit (which keeps its golden camera) and for
 * unknown kits.
 */
export function cameraProfileFor(kitId: string): KitCameraProfile | null {
  if (kitId === APARTMENT_KIT_ID || !hasKit(kitId)) return null;
  const kit = getKit(kitId);
  if (kit === undefined) return null;
  const xs = kit.anchors.map((anchor) => anchor.position.x);
  const zs = kit.anchors.map((anchor) => anchor.position.z);
  const halfWidth = (Math.max(...xs) - Math.min(...xs)) / 2;
  const halfDepth = (Math.max(...zs) - Math.min(...zs)) / 2;
  const radius = Math.hypot(halfWidth, halfDepth);
  const distance = Math.max(12, radius * 2.4 + 2);
  return {
    target: { x: kit.spawn.position.x, y: kit.spawn.position.y + 1.0, z: kit.spawn.position.z },
    distance,
  };
}

/** Convenience: kit spawn position (used by the camera target math/tests). */
export function kitSpawnPosition(kitId: string): KitVec3 | null {
  const transform = spawnTransformFor(kitId);
  return transform === null ? null : { x: transform.position.x, y: transform.position.y, z: transform.position.z };
}