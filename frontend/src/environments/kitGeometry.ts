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

/**
 * Application-owned deterministic shell palettes per kit (primitives only).
 *
 * Phase 18D — showcase art direction: the two flagship kits get a stronger
 * floor/wall luminance contrast while keeping their identity. OFFICE is a
 * dark clinical slate floor under a pale cool wall (late-night, research
 * focused); HOTEL_SUITE is a deep warm walnut floor under a creamy wall
 * (private, upscale, quiet). The other kits are untouched.
 */
const FLOOR_TONES: Readonly<Record<string, string>> = {
  office: "#39454f", // Phase 18D: dark clinical slate (was #5a6472)
  hotel_suite: "#6e4f38", // Phase 18D: deep warm walnut (was #7a5a44)
  warehouse: "#4b4f55", // raw concrete
  mansion: "#6b563e", // dark hardwood
};

/** Application-owned deterministic wall palettes per kit (primitives only). */
const WALL_TONES: Readonly<Record<string, string>> = {
  office: "#e2e6ea", // Phase 18D: pale cool wall (was #d8dce2) — stronger contrast
  hotel_suite: "#f0e6d2", // Phase 18D: creamy wall (was #e8dcc8) — warmer
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
 * Phase 18D — showcase art direction (deterministic shell decoration).
 *
 * Two bounded, purely-declarative extensions of the kit shell:
 *  1. A warm DESK POOL point light above the DESK_EVIDENCE anchor centroid
 *     (a warm pendant pool over the investigation workspace). This uses the
 *     EXISTING "light" primitive kind — zero renderer changes — and gives the
 *     cool office a warm local accent WITHOUT enabling any global shadows.
 *  2. A bounded set of decorative non-interactable props, placed strictly at
 *     existing manifest anchors (plus deterministic offsets), using existing
 *     catalog ASSET descriptors for geometry/color (same pattern as the
 *     existing lamp pedestal). Every prop passes a deterministic EVIDENCE
 *     GUARD so a decor box can never overlap an evidence anchor; the guard is
 *     exported so tests can prove it independently.
 * ==================================================================== */

/** Warm pendant pool color per kit (kit-id -> hex). Absent = no pool. */
const WARM_POOL_COLORS: Readonly<Record<string, string>> = {
  office: "#f2c48a", // warm amber pool over the evidence desks (cool room)
  hotel_suite: "#f7d4a2", // soft honey pool over the study nook desks
};

/**
 * Phase 18D camera tuning: how tightly the default orbit frames the room.
 * The distance formula stays "extents-derived" (radius * scale + 2), but the
 * flagship kits use a closer multiplier so evidence reads immediately and
 * direct picking stays practical. Untuned kits keep the Phase 11 formula.
 */
interface KitCameraTuning {
  distanceScale: number;
  minDistance: number;
}
const CAMERA_TUNING: Readonly<Record<string, KitCameraTuning>> = {
  office: { distanceScale: 1.55, minDistance: 13 },
  hotel_suite: { distanceScale: 1.8, minDistance: 12 },
};

/** Hard bound on decor props per kit (bounded scene cost, task contract). */
export const MAX_DECOR_PROPS_PER_KIT = 6;

/** The anchor classes that may host evidence (decor must NEVER touch these). */
const EVIDENCE_ANCHOR_TYPES: readonly string[] = [
  "DESK_EVIDENCE",
  "FLOOR_EVIDENCE",
  "BODY",
  "DOCUMENT",
  "COMPUTER",
  "WALL_EVIDENCE",
];

/** Evidence visual footprint radius at its anchor (world meters). */
const EVIDENCE_ANCHOR_RADIUS = 0.45;
/** Decor prop padding around its catalog footprint (world meters). */
const DECOR_PADDING = 0.15;
/** Spawn point clearance radius (decor must keep the player spawn clear). */
const SPAWN_CLEARANCE = 0.4;
/** Vertical gap below which two boxes are considered "same level". */
const DECOR_SAME_LEVEL_EPS = 0.05;

/** One deterministic decor prop plan (Phase 18D). */
export interface KitDecorPlan {
  /** Deterministic shell primitive id (prefix "decor_"). */
  id: string;
  /** An EXISTING catalog asset id (geometry source only — never a world object). */
  assetId: string;
  /** An EXISTING manifest anchor the prop is placed at (must be a kit anchor). */
  hostAnchorId: string;
  /**
   * World-space offset from the host anchor's manifest position. `offset.y`
   * is the DESIRED ABSOLUTE center height (world meters) — host anchors have
   * differing y (tables vs floors), so absolute Y is the deterministic norm.
   */
  offset: KitVec3;
  /** Optional explicit diffuse hex; defaults to the asset's first color. */
  color?: string;
  /**
   * When set, horizontal overlap with this prop id is INTENTIONAL (a stack —
   * e.g. a monitor on a desk) and is exempt from the decor-vs-decor guard.
   */
  stackOn?: string;
}

/**
 * The authored Phase 18D decor plans, keyed by kit id. Every plan host anchor
 * must exist in the kit manifest (plans referencing a missing anchor are
 * dropped deterministically — the shell can never break on a plan edit).
 */
const DECOR_PLANS: Readonly<Record<string, readonly KitDecorPlan[]>> = {
  // OFFICE — the manager's office becomes a desk cluster (desk + chair +
  // monitor + warm task lamp) and the storage room gets a bookshelf with a
  // folder. All placements stay clear of the office evidence anchors.
  office: [
    { id: "decor_desk_01", assetId: "PROP_DESK_01", hostAnchorId: "office_generic_01", offset: { x: 0.0, y: 0.375, z: 0.0 } },
    { id: "decor_chair_01", assetId: "PROP_OFFICE_CHAIR_01", hostAnchorId: "office_generic_01", offset: { x: 1.6, y: 0.55, z: 0.3 } },
    { id: "decor_monitor_01", assetId: "PROP_DESKTOP_MONITOR_01", hostAnchorId: "office_generic_01", offset: { x: 0.55, y: 0.95, z: 0.0 }, stackOn: "decor_desk_01" },
    { id: "decor_desklamp_01", assetId: "PROP_DESK_LAMP_01", hostAnchorId: "office_generic_01", offset: { x: -0.45, y: 0.95, z: 0.3 }, color: "#e8d9a8", stackOn: "decor_desk_01" },
    { id: "decor_bookshelf_01", assetId: "PROP_BOOKSHELF_01", hostAnchorId: "office_shelf_01", offset: { x: 0.0, y: 0.95, z: 0.0 } },
    { id: "decor_folder_01", assetId: "PROP_FOLDER_01", hostAnchorId: "office_shelf_01", offset: { x: 0.35, y: 1.05, z: 0.0 }, stackOn: "decor_bookshelf_01" },
  ],
  // HOTEL SUITE — a bed against the bedroom's north wall (clear of the body
  // and the bedside anchor), and a lounge composition of sofa + coffee table
  // + table lamp + armchair + handbag around the lounge generic anchor.
  hotel_suite: [
    { id: "decor_bed_01", assetId: "PROP_HOTEL_BED_01", hostAnchorId: "hotel_bedside_01", offset: { x: -1.2, y: 0.3, z: -0.85 } },
    { id: "decor_sofa_01", assetId: "PROP_SOFA_01", hostAnchorId: "hotel_generic_01", offset: { x: 0.0, y: 0.425, z: 0.7 } },
    { id: "decor_coffeetable_01", assetId: "PROP_COFFEE_TABLE_01", hostAnchorId: "hotel_generic_01", offset: { x: 0.0, y: 0.225, z: -0.8 } },
    { id: "decor_tablelamp_01", assetId: "PROP_TABLE_LAMP_01", hostAnchorId: "hotel_generic_01", offset: { x: 0.35, y: 0.625, z: -0.7 }, color: "#e8d9a8", stackOn: "decor_coffeetable_01" },
    { id: "decor_armchair_01", assetId: "PROP_ARMCHAIR_01", hostAnchorId: "hotel_generic_01", offset: { x: 1.35, y: 0.475, z: -0.9 } },
    { id: "decor_handbag_01", assetId: "PROP_HANDBAG_01", hostAnchorId: "hotel_generic_01", offset: { x: 0.25, y: 0.96, z: 0.7 }, stackOn: "decor_sofa_01" },
  ],
};

/** Horizontal AABB overlap test (world XZ plane). */
function rectsOverlap(
  ax: number,
  az: number,
  ahx: number,
  ahz: number,
  bx: number,
  bz: number,
  bhx: number,
  bhz: number,
): boolean {
  return Math.abs(ax - bx) < ahx + bhx && Math.abs(az - bz) < ahz + bhz;
}

/**
 * The deterministic Phase 18D evidence guard: true when a box of half-extents
 * `halfX`/`halfZ` at `position` does NOT overlap ANY evidence-capable anchor
 * of the kit (DESK_EVIDENCE / FLOOR_EVIDENCE / BODY / DOCUMENT / COMPUTER /
 * WALL_EVIDENCE) nor the kit's spawn point. Exported so tests can prove every
 * accepted decor prop passes it (and that a deliberately-colliding box fails).
 */
export function evidenceClearanceOk(
  kit: EnvironmentKitDocument,
  position: KitVec3,
  halfX: number,
  halfZ: number,
): boolean {
  for (const anchor of kit.anchors) {
    if (!EVIDENCE_ANCHOR_TYPES.includes(anchor.type)) continue;
    if (
      rectsOverlap(
        position.x,
        position.z,
        halfX,
        halfZ,
        anchor.position.x,
        anchor.position.z,
        EVIDENCE_ANCHOR_RADIUS,
        EVIDENCE_ANCHOR_RADIUS,
      )
    ) {
      return false;
    }
  }
  return !rectsOverlap(
    position.x,
    position.z,
    halfX,
    halfZ,
    kit.spawn.position.x,
    kit.spawn.position.z,
    SPAWN_CLEARANCE,
    SPAWN_CLEARANCE,
  );
}

/**
 * Build the deterministic Phase 18D decor props for a non-apartment kit.
 * Pure function of the manifest + catalog: every plan is snapped to its host
 * anchor (missing anchors/assets are dropped), sorted by plan id, bounded by
 * {@link MAX_DECOR_PROPS_PER_KIT}, and each accepted prop must pass:
 *   - {@link evidenceClearanceOk} (never occlude evidence or the spawn);
 *   - the decor-vs-decor guard (no overlapping boxes at the same level,
 *     intentional `stackOn` bases exempt);
 *   - the interior-bounds check (the prop center stays inside the room).
 */
export function buildKitDecorProps(
  kit: EnvironmentKitDocument,
  interior: { x0: number; x1: number; z0: number; z1: number } | null = null,
): ScenePrimitive[] {
  const plans = (DECOR_PLANS[kit.environmentId] ?? [])
    .filter((plan) => getAsset(plan.assetId) !== undefined)
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .slice(0, MAX_DECOR_PROPS_PER_KIT);
  const anchorsById = new Map<string, KitAnchor>(kit.anchors.map((anchor) => [anchor.anchorId, anchor] as const));
  const accepted: Array<{ plan: KitDecorPlan; position: KitVec3; halfX: number; halfZ: number; height: number }> = [];
  const props: ScenePrimitive[] = [];

  for (const plan of plans) {
    const asset = getAsset(plan.assetId);
    if (asset === undefined) continue;
    const host = anchorsById.get(plan.hostAnchorId);
    if (host === undefined) continue;
    const position: KitVec3 = {
      x: host.position.x + plan.offset.x,
      y: plan.offset.y, // absolute desired center height (documented norm)
      z: host.position.z + plan.offset.z,
    };
    const halfX = asset.dimensions.x / 2 + DECOR_PADDING;
    const halfZ = asset.dimensions.z / 2 + DECOR_PADDING;
    if (!evidenceClearanceOk(kit, position, halfX, halfZ)) continue;
    if (
      interior !== null &&
      (position.x < interior.x0 || position.x > interior.x1 || position.z < interior.z0 || position.z > interior.z1)
    ) {
      continue;
    }
    const collidesWith = accepted.find((entry) => {
      // Intentional stacks are exempt BOTH ways: this prop declared `entry` as
      // its base (monitor on desk) OR `entry` declared THIS prop as its base
      // (the sofa must not be dropped just because a handbag rests on it).
      if (entry.plan.id === plan.stackOn) return false;
      if (entry.plan.stackOn === plan.id) return false;
      const gapY = Math.abs(entry.position.y - position.y) - (entry.height / 2 + asset.dimensions.y / 2);
      if (gapY >= DECOR_SAME_LEVEL_EPS) return false; // vertically separated = no collision
      return rectsOverlap(
        position.x,
        position.z,
        halfX,
        halfZ,
        entry.position.x,
        entry.position.z,
        entry.halfX,
        entry.halfZ,
      );
    });
    if (collidesWith !== undefined) continue;
    accepted.push({ plan, position, halfX, halfZ, height: asset.dimensions.y });
    props.push({
      id: plan.id,
      kind: "table",
      position,
      rotation: { x: 0, y: 0, z: 0 },
      scale: { x: asset.dimensions.x, y: asset.dimensions.y, z: asset.dimensions.z },
      color: plan.color ?? firstHexColor(asset.colors, DEFAULT_FLOOR_TONE),
    });
  }
  return props;
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

  // Phase 18D — warm pendant pool above the DESK_EVIDENCE anchor centroid
  // (a warm local accent pool over the investigation workspace). Uses the
  // existing "light" primitive kind: the renderer already turns it into a
  // PointLight + small emissive marker. Deterministic (sorted + averaged).
  const poolHex = WARM_POOL_COLORS[kit.environmentId];
  if (poolHex !== undefined) {
    const desks = kit.anchors.filter((anchor) => anchor.type === "DESK_EVIDENCE").sort(byAnchorId);
    if (desks.length > 0) {
      const poolX = desks.reduce((sum, anchor) => sum + anchor.position.x, 0.0) / desks.length;
      const poolZ = desks.reduce((sum, anchor) => sum + anchor.position.z, 0.0) / desks.length;
      primitives.push({
        id: "light_desk_pool",
        kind: "light",
        position: { x: poolX, y: LIGHT_Y, z: poolZ },
        color: poolHex,
      });
    }
  }

  // Phase 18D — deterministic decorative (non-interactable) props: bounded,
  // evidence-guarded, catalog-sourced, snapped to existing manifest anchors.
  const interior = {
    x0: x0 + wallThickness / 2,
    x1: x1 - wallThickness / 2,
    z0: z0 + wallThickness / 2,
    z1: z1 - wallThickness / 2,
  };
  primitives.push(...buildKitDecorProps(kit, interior));

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
 * Phase 18D: the flagship kits (office / hotel_suite) use a tighter camera
 * tuning (closer orbit) so evidence reads immediately and direct picking is
 * practical — the distance stays extents-derived (larger room -> larger
 * orbit). Returns null for the apartment kit (which keeps its golden camera)
 * and for unknown kits.
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
  const tuning = CAMERA_TUNING[kit.environmentId];
  const distance =
    tuning !== undefined
      ? Math.max(tuning.minDistance, radius * tuning.distanceScale + 2)
      : Math.max(12, radius * 2.4 + 2);
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