import type { Vec3 } from "./apartment";

/**
 * Application-owned asset registry (Phase 6 E, REQUIREMENTS 25).
 *
 * The backend emits ONLY logical asset ids. This map resolves each id to a
 * SAFE, local, application-owned primitive specification: a mesh kind, a hex
 * color, an absolute scale in meters, an optional public label and an
 * interactability flag.
 *
 * SOURCE OF TRUTH: the asset ids actually emitted by the backend dev-mode
 * case (backend/app/services/dev_mode_case.json, vetted by the backend's own
 * AssetRegistry allowlist in backend/app/generation/safety.py). Every id the
 * golden case can emit MUST resolve to a NON-fallback entry — otherwise the
 * scene silently degrades objects into neutral (non-interactable) placeholders
 * and evidence becomes undiscoverable (DEF-049).
 *
 * Emitted ids (9): PROP_KITCHEN_KNIFE_01, PROP_LETTER_OPENER_01,
 * PROP_SCISSORS_01, PROP_VASE_01, PROP_LAPTOP_01, PROP_TABLE_01,
 * DOOR_APARTMENT_01, PROP_LAMP_01, PROP_BODY_PLACEHOLDER_01.
 *
 * Interactability follows the frozen contract placement semantics (DEF-049):
 * an object whose placement links it to evidence MUST be interactable
 * (knife/letter opener/scissors/laptop); objects placed without evidence may
 * still be interactable (they return "interacted" with no discovery); the
 * victim/body placeholder is registered NON-interactable because no placement
 * links it to evidence.
 *
 * The dot-style ids (apartment.laptop.basic, ...) remain registered purely as
 * harmless aliases for any fixture/tooling that still emits them.
 *
 * Nothing here is derived from server data: no urls, no file paths, no
 * script/shader sources, no arbitrary material strings. The scene renderer
 * turns these entries into local primitives only.
 *
 * UNKNOWN ids resolve to {@link FALLBACK_ASSET} — a neutral gray box that is
 * non-interactable — so an unknown asset can never cause a network load, a
 * raised exception or a broken scene (a player-visible notice is shown in the
 * scene sidebar by the route).
 */

export type AssetPrimitiveKind = "box" | "cylinder" | "sphere" | "flat";

/* ======================================================================
 * Phase 8_1 — direct 3D interaction geometry.
 *
 * Composite parts: a world object renders as a named ROOT mesh
 * (`pd_obj_<objectId>`) holding one or more child geometry meshes
 * (composite parts). ALL of the child geometry is derived from these pure,
 * deterministic descriptors — primitives only, application-owned colors
 * only. Nothing here is ever read from the server payload.
 * ==================================================================== */

/** The primitive kinds a composite part may be built from. */
export type CompositePartKind = "box" | "cylinder" | "sphere" | "torus";

export interface CompositePartDescriptor {
  kind: CompositePartKind;
  /** Part size in world meters (x=width, y=height, z=depth; sphere uses the max axis as diameter). */
  size: Vec3;
  /** Local offset from the object ROOT, in meters (children inherit the root transform). */
  offset: Vec3;
  /** Optional local Euler rotation in radians. */
  rotation?: Vec3;
  /** Application-owned hex color for this part (never a server-provided string). */
  color: string;
}

/** Named composite builders registered in the asset registry. */
export type CompositeKind = "knife" | "letter-opener" | "laptop" | "victim" | "table";

export interface AssetEntry {
  primitiveKind: AssetPrimitiveKind;
  /** Application-owned hex color (never a server-provided string). */
  color: string;
  /** Absolute primitive size in apartment meters (x=width, y=height, z=depth). */
  scale: Vec3;
  /** Public label; left null for the fallback so no misleading name is shown. */
  label: string | null;
  /** Whether this asset may carry an interaction affordance. */
  interactable: boolean;
  /** Optional named composite builder; supersedes the simple primitive render. */
  compositeKind?: CompositeKind | null;
  /**
   * Optional multiplier for the invisible pick hitbox. Default 1 =
   * auto-compensate every axis up to {@link MIN_PICKABLE_EXTENT}, so small
   * evidence (knife, letter opener) is reliably clickable even though its
   * visible geometry is far below the threshold.
   */
  hitboxScale?: number;
}

/* ======================================================================
 * Invisible pick hitbox policy (Phase 8_1 A2).
 *
 * Objects whose smallest visible dimension is below
 * {@link MIN_PICKABLE_EXTENT} (0.4 world meters) get an invisible parent
 * hitbox sized to a safe minimum pickable extent, so tiny evidence is
 * clickable without visible decoy geometry in screenshots.
 * ==================================================================== */

/** Documented minimum pickable extent in world meters (Phase 8_1 A2). */
export const MIN_PICKABLE_EXTENT = 0.4;

/** True when an asset's smallest visible dimension is below the threshold. */
export function needsPickHitbox(entry: { scale: Vec3; hitboxScale?: number }): boolean {
  return Math.min(entry.scale.x, entry.scale.y, entry.scale.z) < MIN_PICKABLE_EXTENT;
}

/**
 * Deterministic hitbox extent (meters): every axis is clamped up to
 * {@link MIN_PICKABLE_EXTENT} and then multiplied by the per-asset
 * hitboxScale (default 1). For a small object this yields a safe, invisible,
 * still-pickable interaction target around the composite root.
 */
export function pickHitboxExtent(entry: { scale: Vec3; hitboxScale?: number }): Vec3 {
  const k = entry.hitboxScale ?? 1;
  return {
    x: Math.max(entry.scale.x, MIN_PICKABLE_EXTENT) * k,
    y: Math.max(entry.scale.y, MIN_PICKABLE_EXTENT) * k,
    z: Math.max(entry.scale.z, MIN_PICKABLE_EXTENT) * k,
  };
}

export type AssetRegistry = ReadonlyMap<string, AssetEntry>;

/** Neutral gray used ONLY for unknown ids — the safe, boring fallback. */
export const FALLBACK_COLOR = "#8d8d93";

export const FALLBACK_ASSET: AssetEntry = Object.freeze({
  primitiveKind: "box",
  color: FALLBACK_COLOR,
  scale: Object.freeze({ x: 0.35, y: 0.35, z: 0.35 }),
  label: null,
  interactable: false,
});

function freeze(entry: AssetEntry): AssetEntry {
  return Object.freeze({
    primitiveKind: entry.primitiveKind,
    color: entry.color,
    scale: Object.freeze({ x: entry.scale.x, y: entry.scale.y, z: entry.scale.z }),
    label: entry.label,
    interactable: entry.interactable,
    compositeKind: entry.compositeKind ?? null,
    hitboxScale: entry.hitboxScale ?? 1,
  });
}

function knife(): AssetEntry {
  return freeze({
    primitiveKind: "box",
    // Pale metallic blade; the composite draws a two-part blade+handle shape
    // (see buildObjectComposite below) — the registry color is the blade tone.
    color: "#c8ccd4",
    scale: { x: 0.08, y: 0.04, z: 0.4 },
    label: "Kitchen knife",
    interactable: true,
    compositeKind: "knife",
    hitboxScale: 1,
  });
}

function laptop(): AssetEntry {
  return freeze({
    primitiveKind: "flat",
    color: "#30343e",
    scale: { x: 0.62, y: 0.055, z: 0.44 },
    label: "Laptop",
    interactable: true,
    compositeKind: "laptop",
  });
}

function table(): AssetEntry {
  return freeze({
    primitiveKind: "box",
    color: "#8a5a2b",
    scale: { x: 2.0, y: 0.85, z: 1.2 },
    label: "Table",
    interactable: true,
    compositeKind: "table",
  });
}

function door(): AssetEntry {
  return freeze({
    primitiveKind: "box",
    color: "#7c4a21",
    scale: { x: 1.6, y: 2.2, z: 0.12 },
    label: "Door",
    interactable: true,
  });
}

function lamp(): AssetEntry {
  return freeze({
    primitiveKind: "cylinder",
    color: "#e9d9a8",
    scale: { x: 0.16, y: 0.55, z: 0.16 },
    label: "Lamp",
    interactable: true,
  });
}

/** Victim/body placeholder: NO placement links it to evidence -> non-interactable. */
function victimBody(): AssetEntry {
  return freeze({
    primitiveKind: "flat",
    color: "#8f8579",
    scale: { x: 1.3, y: 0.22, z: 0.62 },
    label: "Victim",
    interactable: false,
    compositeKind: "victim",
  });
}

/**
 * Golden-scene asset ids. Keyed on the EXACT strings the backend emits —
 * the 9 ids from backend/app/services/dev_mode_case.json placements (see the
 * module docstring). The dot-style ids (apartment.laptop.basic, ...) remain
 * registered only as harmless aliases for older fixtures/tooling. Any other
 * id resolves to the fallback.
 */
export const ASSET_REGISTRY: AssetRegistry = new Map<string, AssetEntry>([
  // ---- emitted by the backend dev-mode case (SOURCE OF TRUTH) ----------
  ["PROP_KITCHEN_KNIFE_01", knife()],
  ["PROP_LETTER_OPENER_01", freeze({
    primitiveKind: "box", color: "#a37b35", scale: { x: 0.12, y: 0.04, z: 0.34 }, label: "Letter opener", interactable: true,
    compositeKind: "letter-opener", hitboxScale: 1,
  })],
  ["PROP_SCISSORS_01", freeze({
    primitiveKind: "box", color: "#7d8bb8", scale: { x: 0.09, y: 0.03, z: 0.22 }, label: "Scissors", interactable: true,
  })],
  ["PROP_VASE_01", freeze({
    primitiveKind: "cylinder", color: "#7a57c9", scale: { x: 0.2, y: 0.42, z: 0.2 }, label: "Vase", interactable: true,
  })],
  ["PROP_LAPTOP_01", laptop()],
  ["PROP_TABLE_01", table()],
  ["DOOR_APARTMENT_01", door()],
  ["PROP_LAMP_01", lamp()],
  ["PROP_BODY_PLACEHOLDER_01", victimBody()],
  // ---- dot-style aliases (kept for older fixtures/tooling) -------------
  ["apartment.table.basic", table()],
  ["apartment.laptop.basic", laptop()],
  ["evidence.knife.basic", knife()],
  ["prop.body.victim.basic", victimBody()],
  ["apartment.door.basic", door()],
  ["apartment.lamp.basic", lamp()],
  ["apartment.chair.basic", freeze({
    primitiveKind: "box", color: "#5f4b36", scale: { x: 0.62, y: 0.95, z: 0.62 }, label: "Chair", interactable: true,
  })],
]);

/** Registry lookup; returns the frozen fallback for unknown ids (never throws). */
export function resolveAsset(assetId: string): AssetEntry {
  const entry = ASSET_REGISTRY.get(assetId);
  return entry ?? FALLBACK_ASSET;
}

/** True only for ids registered in the application-owned registry. */
export function isKnownAsset(assetId: string): boolean {
  return ASSET_REGISTRY.has(assetId);
}

/* ======================================================================
 * Composite geometry builders (Phase 8_1 C).
 *
 * Purely descriptive, deterministic and primitive-only: the Babylon glue
 * turns these descriptors into real meshes (see renderInvestigation.ts).
 * Every part has an explicit local offset (the object ROOT sits at the
 * anchor transform), so the same builder yields exactly the same scene in
 * every run, in every browser.
 *
 * Differentiation notes (kitchen knife vs letter opener — both meaty
 * evidence items, so they must be told apart at a glance):
 *  - blade length: knife 0.24m vs opener 0.13m (≈1.85x — over the 1.5x bar);
 *  - blade width: knife 0.045m vs opener 0.09m (the opener's blade is the
 *    wider, spatulate kind of a real letter opener);
 *  - material: pale metallic steel #c8ccd4 vs warm brass #a37b35;
 *  - handle: knife gets a large dark grip, the opener a short small handle.
 * ==================================================================== */

/** Local helper so single-part primitives share the descriptor shape too. */
const box = (size: Vec3, offset: Vec3, color: string): CompositePartDescriptor => ({ kind: "box", size, offset, color });

/** Two-part kitchen knife: long thin pale-steel blade + wider dark handle. */
function knifeParts(entry: { color: string }): CompositePartDescriptor[] {
  return [
    // Long, thin elongated blade laid flat along the local z axis.
    box({ x: 0.045, y: 0.014, z: 0.24 }, { x: 0, y: 0, z: 0.065 }, entry.color),
    // Slightly wider, shorter, darker handle behind the blade.
    box({ x: 0.035, y: 0.024, z: 0.09 }, { x: 0, y: 0, z: -0.155 }, "#5a3b22"),
  ];
}

/** Letter opener: SHORT WIDE flat brass blade + a small handle — clearly ≠ knife. */
function letterOpenerParts(entry: { color: string }): CompositePartDescriptor[] {
  return [
    // Short wide flat spatulate blade (0.13 long vs the knife's 0.24).
    box({ x: 0.09, y: 0.018, z: 0.13 }, { x: 0, y: 0, z: 0.075 }, entry.color),
    // Small dark stub handle.
    box({ x: 0.05, y: 0.028, z: 0.07 }, { x: 0, y: 0, z: -0.135 }, "#4a3620"),
  ];
}

/** Thin two-tone closed laptop: a slim base + a lid. */
function laptopParts(entry: { scale: Vec3; color: string }): CompositePartDescriptor[] {
  return [
    // Base (the registry's dark tone).
    box({ x: 0.56, y: 0.022, z: 0.4 }, { x: 0, y: -0.012, z: 0 }, entry.color),
    // Lid (darker tone; simple two-tone — no emissive trickery required).
    box({ x: 0.56, y: 0.016, z: 0.4 }, { x: 0, y: 0.018, z: 0 }, "#20242e"),
  ];
}

/** Calm, neutral recumbent victim: low flat body + a subtle blanket slab. */
function victimParts(entry: { color: string }): CompositePartDescriptor[] {
  return [
    // Low flat body in the registry's muted tone.
    box({ x: 1.15, y: 0.14, z: 0.5 }, { x: 0, y: 0, z: 0 }, entry.color),
    // A subtle second slab (blanket) lying over the body — still primitive-only.
    box({ x: 1.05, y: 0.08, z: 0.44 }, { x: 0, y: 0.1, z: 0 }, "#6d6a80"),
  ];
}

/** Stable table: a tabletop slab + four thin legs. */
function tableParts(_entry: { scale: Vec3; color: string }): CompositePartDescriptor[] {
  return [
    // Tabletop just below the "dining_table ~0.9m" surface anchor.
    box({ x: 1.9, y: 0.1, z: 1.1 }, { x: 0, y: 0, z: 0 }, "#8a5a2b"),
    // Four thin legs at the corners, down to near floor level.
    box({ x: 0.09, y: 0.72, z: 0.09 }, { x: -0.87, y: -0.41, z: -0.47 }, "#5a3b22"),
    box({ x: 0.09, y: 0.72, z: 0.09 }, { x: 0.87, y: -0.41, z: -0.47 }, "#5a3b22"),
    box({ x: 0.09, y: 0.72, z: 0.09 }, { x: -0.87, y: -0.41, z: 0.47 }, "#5a3b22"),
    box({ x: 0.09, y: 0.72, z: 0.09 }, { x: 0.87, y: -0.41, z: 0.47 }, "#5a3b22"),
  ];
}

/**
 * Pure composite descriptor builder: maps a registered composite kind to its
 * child primitive descriptors. Unknown/unlisted kinds return an EMPTY list,
 * so the renderer can fall back safely (single-part primitive rendering).
 */
export function buildObjectComposite(
  kind: CompositeKind,
  entry: { scale: Vec3; color: string },
): CompositePartDescriptor[] {
  switch (kind) {
    case "knife":
      return knifeParts(entry);
    case "letter-opener":
      return letterOpenerParts(entry);
    case "laptop":
      return laptopParts(entry);
    case "victim":
      return victimParts(entry);
    case "table":
      return tableParts(entry);
    default:
      return [];
  }
}