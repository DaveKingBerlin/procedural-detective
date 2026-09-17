import type { Vec3 } from "./apartment";
import {
  catalogAssetIds,
  fallbackAsset,
  getAsset,
  isCatalogHealthy,
  type CatalogAssetDescriptor,
} from "../catalog/assetCatalog";
import { buildTemplateComposite } from "../templates/templateRegistry";
import { resolveDefaultVariant } from "../templates/variantParams";

/**
 * Application-owned asset registry (Phase 6 E, REQUIREMENTS 25) — now derived
 * from the Phase 10 Asset Oracle catalog.
 *
 * PHASE 10 TRACK B — THE CATALOG IS THE SINGLE SOURCE OF TRUTH:
 * `assets/catalog/catalog.json` is bundled into the app (no runtime fetch, no
 * network) and every per-asset entry below is DERIVED from its typed
 * descriptor — renderKind + compositeKind -> primitive/builder kind, colors ->
 * the primary hex tone, dimensions -> the absolute scale in meters, label and
 * interactable verbatim. The hand-maintained parallel tables this file used to
 * carry are gone: the backend resolver and this renderer now read the SAME
 * manifest, so they can never drift.
 *
 * BOUNDARY (frontend/backend): the backend emits ONLY logical assetIds in the
 * player-safe WorldGraph DTO. This registry accepts EXACT catalog assetIds
 * only — the frontend NEVER resolves by free text, canonical name or alias
 * (the backend's Asset Oracle did that). A dot-style legacy id such as
 * "apartment.laptop.basic" is an ALIAS in the manifest for the backend, NOT a
 * catalog id: here it resolves to the fallback. A hostile id can never reach a
 * descriptor the backend did not select.
 *
 * UNKNOWN ids resolve to {@link FALLBACK_ASSET} — a neutral gray box that is
 * non-interactable — so an unknown asset can never cause a network load, a
 * raised exception or a broken scene (a player-visible notice is shown in the
 * scene sidebar by the route). If the bundled catalog itself fails strict
 * validation, every asset degrades to the same neutral fallback and
 * {@link getCatalogError} reports the deterministic issue message for a safe
 * app-level error (never silent corruption).
 *
 * Interactability, colors, labels and geometry are the catalog's declarative
 * values, byte-identical with the backend's view: "Kitchen knife", "Letter
 * opener", "Scissors", "Laptop", "Table", "Door", "Lamp", "Vase", "Victim",
 * "Unknown object"; only the four evidence-bearing assets are interactable.
 *
 * Nothing here is derived from server payload data: no urls, no file paths, no
 * script/shader sources, no arbitrary material strings. The scene renderer
 * turns these entries into local primitives only.
 */

export type AssetPrimitiveKind = "box" | "cylinder" | "sphere" | "flat";

/* ======================================================================
 * Phase 8_1 — direct 3D interaction geometry.
 *
 * Composite parts: a world object renders as a named ROOT mesh
 * (`pd_obj_<objectId>`) holding one or more child geometry meshes
 * (composite parts). ALL of the child geometry is derived from these pure,
 * deterministic descriptors — primitives only, catalog-owned colors only.
 * Nothing here is ever read from the server payload.
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
  /** Application-owned hex color for this part (catalog-derived, never a server string). */
  color: string;
}

/**
 * Named composite builders registered in the asset registry — one builder per
 * catalog `compositeKind` the frontend can safely render (see
 * SUPPORTED_COMPOSITE_KINDS in ../catalog/assetCatalog).
 */
export type CompositeKind = "knife" | "letter-opener" | "scissors" | "laptop" | "victim" | "table";

export interface AssetEntry {
  primitiveKind: AssetPrimitiveKind;
  /** Application-owned hex color (catalog-derived, never a server string). */
  color: string;
  /** Absolute primitive size in apartment meters (x=width, y=height, z=depth). */
  scale: Vec3;
  /** Public label; left null for the fallback so no misleading name is shown. */
  label: string | null;
  /** Whether this asset may carry an interaction affordance (catalog verbatim). */
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
  /** Phase 12: the asset's frozen logical template (null for non-composites). */
  templateId?: string | null;
  /**
   * Phase 12: the factory-built child parts of a TEMPLATE-ONLY composite
   * (compositeKind === null), built with the asset's DEFAULT variant. Null
   * for legacy composites, primitives and the fallback.
   */
  templateParts?: readonly CompositePartDescriptor[] | null;
  /** Phase 12: the template `hitbox` scaled by the default variant scale. */
  templateHitbox?: { x: number; y: number; z: number } | null;
  /**
   * Phase 12: the factory's absolute bounding box at the DEFAULT variant
   * scale — the world-object scale for spacing/hitbox/ring math.
   */
  templateFaceBounds?: { x: number; y: number; z: number } | null;
  /** Phase 12: resolved default-variant material token (null = no tint). */
  templateMaterial?: string | null;
  /** Phase 12: resolved default-variant state token (pass-through only). */
  templateState?: string | null;
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

/**
 * Deterministic error string when the bundled catalog failed validation
 * (null when healthy). Re-exported from the catalog client so the scene route
 * can surface a safe app-level error without importing the catalog module.
 */
export { getCatalogError } from "../catalog/assetCatalog";

/* ======================================================================
 * Catalog -> registry derivation (Phase 10 Track B).
 * ==================================================================== */

/** Catalog `compositeKind` -> registered builder name (one builder per kind). */
const COMPOSITE_BUILDER_BY_CATALOG_KIND: Readonly<Record<string, CompositeKind>> = {
  kitchen_knife: "knife",
  letter_opener: "letter-opener",
  scissors: "scissors",
  laptop: "laptop",
  victim: "victim",
  table: "table",
};

/** Nominal single-part primitive for each composite builder (fallback render). */
const NOMINAL_PRIMITIVE_BY_COMPOSITE: Readonly<Record<CompositeKind, AssetPrimitiveKind>> = {
  knife: "box",
  "letter-opener": "box",
  scissors: "box",
  laptop: "flat",
  victim: "flat",
  table: "box",
};

/**
 * Deterministic primary-tone preference: the most descriptive visible color
 * key of a descriptor (shade/base/blade/blades/top/body) wins; anything else
 * falls back to the first color entry (manifest order), then to
 * {@link FALLBACK_COLOR}. Every v1 asset hits one of these keys.
 */
const PRIMARY_COLOR_KEYS: readonly string[] = ["shade", "base", "blade", "blades", "top", "body"];

function primaryColor(colors: Readonly<Record<string, string>>, fallbackHex: string): string {
  for (const key of PRIMARY_COLOR_KEYS) {
    const value = colors[key];
    if (typeof value === "string" && value !== "") return value;
  }
  const first = Object.values(colors)[0];
  return typeof first === "string" && first !== "" ? first : fallbackHex;
}

function primitiveKindFor(descriptor: CatalogAssetDescriptor, compositeKind: CompositeKind | null): AssetPrimitiveKind {
  if (descriptor.renderKind === "composite") {
    // A catalog composite has a documented builder; the nominal primitive is
    // only used for the single-part fallback render (composites never ghost).
    return compositeKind !== null ? NOMINAL_PRIMITIVE_BY_COMPOSITE[compositeKind] : "box";
  }
  if (descriptor.renderKind === "flat") return "flat";
  if (descriptor.renderKind === "cylinder") return "cylinder";
  if (descriptor.renderKind === "sphere") return "sphere";
  return "box"; // "box" renderKind and any non-composite kind validated upstream
}

function freeze(entry: AssetEntry): AssetEntry {
  return Object.freeze({
    primitiveKind: entry.primitiveKind,
    color: entry.color,
    scale: Object.freeze({ x: entry.scale.x, y: entry.scale.y, z: entry.scale.z }),
    label: entry.label,
    interactable: entry.interactable,
    compositeKind: entry.compositeKind ?? null,
    hitboxScale: entry.hitboxScale ?? 1,
    templateId: entry.templateId ?? null,
    templateParts: entry.templateParts ?? null,
    templateHitbox:
      entry.templateHitbox === null || entry.templateHitbox === undefined
        ? null
        : Object.freeze({ x: entry.templateHitbox.x, y: entry.templateHitbox.y, z: entry.templateHitbox.z }),
    templateFaceBounds:
      entry.templateFaceBounds === null || entry.templateFaceBounds === undefined
        ? null
        : Object.freeze({
            x: entry.templateFaceBounds.x,
            y: entry.templateFaceBounds.y,
            z: entry.templateFaceBounds.z,
          }),
    templateMaterial: entry.templateMaterial ?? null,
    templateState: entry.templateState ?? null,
  });
}

/**
 * Phase 12: for a TEMPLATE-ONLY composite (compositeKind === null but a
 * declared templateId) build the DEFAULT-variant factory result ONCE at
 * registry load: child parts, hitbox, absolute bounds, material/state
 * tokens. The six legacy composite builders NEVER take this path (their
 * hardcoded geometry is the golden apartment's byte-identical source). An
 * unknown / absent template degrades to the neutral fallback parts (never a
 * crash, never a ghost).
 */
function templateFieldsFrom(descriptor: CatalogAssetDescriptor): Pick<
  AssetEntry,
  "templateId" | "templateParts" | "templateHitbox" | "templateFaceBounds" | "templateMaterial" | "templateState"
> {
  if (descriptor.renderKind !== "composite" || descriptor.compositeKind !== null || descriptor.templateId === null) {
    return {
      templateId: descriptor.templateId ?? null,
      templateParts: null,
      templateHitbox: null,
      templateFaceBounds: null,
      templateMaterial: null,
      templateState: null,
    };
  }
  const variant = resolveDefaultVariant(descriptor);
  const built = buildTemplateComposite(descriptor.templateId, { colors: variant.colors, scale: variant.scale });
  return {
    templateId: descriptor.templateId,
    templateParts: built.parts,
    templateHitbox: built.hitbox,
    templateFaceBounds: built.bounds,
    templateMaterial: variant.material,
    templateState: variant.state,
  };
}

/** Render entry derived from ONE catalog descriptor (deterministic). */
function entryFromDescriptor(descriptor: CatalogAssetDescriptor): AssetEntry {
  const compositeKind =
    descriptor.compositeKind === null
      ? null
      : (COMPOSITE_BUILDER_BY_CATALOG_KIND[descriptor.compositeKind] ?? null);
  const templateFields = templateFieldsFrom(descriptor);
  return freeze({
    primitiveKind: primitiveKindFor(descriptor, compositeKind),
    color: primaryColor(descriptor.colors, FALLBACK_COLOR),
    scale: { x: descriptor.dimensions.x, y: descriptor.dimensions.y, z: descriptor.dimensions.z },
    label: descriptor.label,
    interactable: descriptor.interactable,
    compositeKind,
    hitboxScale: 1,
    ...templateFields,
  });
}

function buildRegistryEntries(): Map<string, AssetEntry> {
  const map = new Map<string, AssetEntry>();
  if (!isCatalogHealthy()) return map;
  for (const assetId of catalogAssetIds()) {
    const descriptor = getAsset(assetId);
    if (descriptor !== undefined) map.set(assetId, entryFromDescriptor(descriptor));
  }
  return map;
}

/**
 * Safe neutral box used for UNKNOWN ids or when the catalog failed to load.
 * `label` stays null so a neutral placeholder never claims a name.
 */
function buildFallbackAsset(): AssetEntry {
  const descriptor = isCatalogHealthy() ? fallbackAsset() : undefined;
  if (descriptor !== undefined) {
    return freeze({
      primitiveKind: "box",
      color: primaryColor(descriptor.colors, FALLBACK_COLOR),
      scale: { x: descriptor.dimensions.x, y: descriptor.dimensions.y, z: descriptor.dimensions.z },
      label: null,
      interactable: false,
    });
  }
  // Catalog unavailable: the same boring neutral box the app has always used.
  return freeze({
    primitiveKind: "box",
    color: FALLBACK_COLOR,
    scale: { x: 0.35, y: 0.35, z: 0.35 },
    label: null,
    interactable: false,
  });
}

/** The frozen fallback entry (unknown assetIds / broken catalog). */
export const FALLBACK_ASSET: AssetEntry = buildFallbackAsset();

/**
 * Catalog-driven registry: exactly the v1 manifest assetIds (incl. the
 * PROP_FALLBACK_01 entry), or the EMPTY map when the bundled catalog failed
 * validation (every id then resolves to the fallback and the error surfaces
 * through {@link getCatalogError}).
 */
export const ASSET_REGISTRY: AssetRegistry = buildRegistryEntries();

/** Registry lookup; returns the frozen fallback for unknown ids (never throws). */
export function resolveAsset(assetId: string): AssetEntry {
  const entry = ASSET_REGISTRY.get(assetId);
  return entry ?? FALLBACK_ASSET;
}

/** True only for ids registered in the catalog (exact assetId match). */
export function isKnownAsset(assetId: string): boolean {
  return ASSET_REGISTRY.has(assetId);
}

/* ======================================================================
 * Composite geometry builders (Phase 8_1 C), catalog-color driven.
 *
 * Purely descriptive, deterministic and primitive-only: the Babylon glue
 * turns these descriptors into real meshes (see renderInvestigation.ts).
 * Every part has an explicit local offset (the object ROOT sits at the
 * anchor transform), so the same builder yields exactly the same scene in
 * every run, in every browser. Part colors come from the catalog descriptor's
 * `colors` map via {@link partColor} (deterministic keyed lookup with the
 * legacy tone as the safe fallback when a key is absent).
 *
 * Differentiation notes (Phase 9/roadmap: the knife, letter opener, scissors
 * and screwdriver family must NEVER be visually interchangeable):
 *  - kitchen knife: ONE long thin pale-steel blade (0.24m) + a big dark grip;
 *  - letter opener: SHORT WIDE flat brass blade (0.13m) + a small stub handle;
 *  - scissors: TWO thin crossing blades (0.1m) + a round pivot rivet — a
 *    three-part silhouette neither the knife nor the opener has.
 * ==================================================================== */

/** Local helper so single-part primitives share the descriptor shape too. */
const box = (size: Vec3, offset: Vec3, color: string): CompositePartDescriptor => ({
  kind: "box",
  size,
  offset,
  color,
});

/**
 * Catalog descriptor colors, indexed by builder kind. Built once from the
 * healthy catalog (each catalog compositeKind maps to exactly one builder).
 * Empty when the catalog is unavailable: builders then fall back to their
 * documented safe tones, so composites keep rendering deterministically.
 */
const COMPOSITE_COLORS: ReadonlyMap<CompositeKind, Readonly<Record<string, string>>> = (() => {
  const map = new Map<CompositeKind, Record<string, string>>();
  if (isCatalogHealthy()) {
    for (const assetId of catalogAssetIds()) {
      const descriptor = getAsset(assetId);
      if (descriptor === undefined || descriptor.compositeKind === null) continue;
      const builder = COMPOSITE_BUILDER_BY_CATALOG_KIND[descriptor.compositeKind];
      if (builder !== undefined) map.set(builder, descriptor.colors);
    }
  }
  return map;
})();

/** Keyed catalog color for a composite part, with a safe deterministic fallback. */
function partColor(kind: CompositeKind, key: string, fallback: string): string {
  const value = COMPOSITE_COLORS.get(kind)?.[key];
  return typeof value === "string" && value !== "" ? value : fallback;
}

/** Two-part kitchen knife: long thin pale-steel blade + wider dark handle. */
function knifeParts(entry: { color: string }): CompositePartDescriptor[] {
  return [
    // Long, thin elongated blade laid flat along the local z axis.
    box({ x: 0.045, y: 0.014, z: 0.24 }, { x: 0, y: 0, z: 0.065 }, entry.color),
    // Slightly wider, shorter, darker handle behind the blade.
    box(
      { x: 0.035, y: 0.024, z: 0.09 },
      { x: 0, y: 0, z: -0.155 },
      partColor("knife", "handle", "#5a3b22"),
    ),
  ];
}

/** Letter opener: SHORT WIDE flat brass blade + a small handle — clearly ≠ knife. */
function letterOpenerParts(entry: { color: string }): CompositePartDescriptor[] {
  return [
    // Short wide flat spatulate blade (0.13 long vs the knife's 0.24).
    box({ x: 0.09, y: 0.018, z: 0.13 }, { x: 0, y: 0, z: 0.075 }, entry.color),
    // Small dark stub handle.
    box(
      { x: 0.05, y: 0.028, z: 0.07 },
      { x: 0, y: 0, z: -0.135 },
      partColor("letter-opener", "handle", "#4a3620"),
    ),
  ];
}

/**
 * Scissors (Phase 10: catalog compositeKind "scissors"): TWO thin crossing
 * blades splayed around a small pivot rivet. The three-part crossed silhouette
 * plus the pivot disc keeps the Phase 9 legibility pair (knife vs scissors)
 * apart: the knife is one straight 0.24m blade; the scissors are two short
 * crossed 0.1m blades with a round rivet — primitives only, deterministic.
 */
function scissorsParts(entry: { color: string }): CompositePartDescriptor[] {
  const bladeColor = partColor("scissors", "blades", entry.color);
  const pivotColor = partColor("scissors", "pivot", entry.color);
  const BLADE_LENGTH = 0.1; // visibly shorter than the knife's 0.24 blade
  const BLADE_WIDTH = 0.022; // visibly narrower than the opener's 0.09 blade
  return [
    // Blade A splayed +0.45 rad around the pivot.
    {
      kind: "box",
      size: { x: BLADE_WIDTH, y: 0.012, z: BLADE_LENGTH },
      offset: { x: 0, y: 0, z: 0.05 },
      rotation: { x: 0, y: 0.45, z: 0 },
      color: bladeColor,
    },
    // Blade B splayed -0.45 rad (the crossing X silhouette).
    {
      kind: "box",
      size: { x: BLADE_WIDTH, y: 0.012, z: BLADE_LENGTH },
      offset: { x: 0, y: 0, z: 0.05 },
      rotation: { x: 0, y: -0.45, z: 0 },
      color: bladeColor,
    },
    // Small pivot rivet disc between the blades.
    {
      kind: "cylinder",
      size: { x: 0.03, y: 0.018, z: 0.03 },
      offset: { x: 0, y: 0, z: 0 },
      color: pivotColor,
    },
  ];
}

/** Thin two-tone closed laptop: a slim base + a lid. */
function laptopParts(entry: { scale: Vec3; color: string }): CompositePartDescriptor[] {
  return [
    // Base (the descriptor's "base" tone).
    box({ x: 0.56, y: 0.022, z: 0.4 }, { x: 0, y: -0.012, z: 0 }, entry.color),
    // Lid (darker "lid" tone; simple two-tone — no emissive trickery required).
    box(
      { x: 0.56, y: 0.016, z: 0.4 },
      { x: 0, y: 0.018, z: 0 },
      partColor("laptop", "lid", "#20242e"),
    ),
  ];
}

/** Calm, neutral recumbent victim: low flat body + a subtle blanket slab. */
function victimParts(entry: { color: string }): CompositePartDescriptor[] {
  return [
    // Low flat body in the descriptor's muted "body" tone.
    box({ x: 1.15, y: 0.14, z: 0.5 }, { x: 0, y: 0, z: 0 }, entry.color),
    // A subtle second slab (blanket) lying over the body — still primitive-only.
    box(
      { x: 1.05, y: 0.08, z: 0.44 },
      { x: 0, y: 0.1, z: 0 },
      partColor("victim", "blanket", "#6d6a80"),
    ),
  ];
}

/** Stable table: a tabletop slab + four thin legs (descriptor "top"/"legs" tones). */
function tableParts(_entry: { scale: Vec3; color: string }): CompositePartDescriptor[] {
  const legColor = partColor("table", "legs", "#5a3b22");
  return [
    // Tabletop just below the "dining_table ~0.9m" surface anchor.
    box({ x: 1.9, y: 0.1, z: 1.1 }, { x: 0, y: 0, z: 0 }, partColor("table", "top", "#8a5a2b")),
    // Four thin legs at the corners, down to near floor level.
    box({ x: 0.09, y: 0.72, z: 0.09 }, { x: -0.87, y: -0.41, z: -0.47 }, legColor),
    box({ x: 0.09, y: 0.72, z: 0.09 }, { x: 0.87, y: -0.41, z: -0.47 }, legColor),
    box({ x: 0.09, y: 0.72, z: 0.09 }, { x: -0.87, y: -0.41, z: 0.47 }, legColor),
    box({ x: 0.09, y: 0.72, z: 0.09 }, { x: 0.87, y: -0.41, z: 0.47 }, legColor),
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
    case "scissors":
      return scissorsParts(entry);
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