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
  });
}

function knife(): AssetEntry {
  return freeze({
    primitiveKind: "box",
    color: "#cfd3d9",
    scale: { x: 0.06, y: 0.02, z: 0.34 },
    label: "Kitchen knife",
    interactable: true,
  });
}

function laptop(): AssetEntry {
  return freeze({
    primitiveKind: "flat",
    color: "#30343e",
    scale: { x: 0.62, y: 0.035, z: 0.44 },
    label: "Laptop",
    interactable: true,
  });
}

function table(): AssetEntry {
  return freeze({
    primitiveKind: "box",
    color: "#8a5a2b",
    scale: { x: 2.0, y: 0.85, z: 1.2 },
    label: "Table",
    interactable: true,
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
    scale: { x: 1.3, y: 0.16, z: 0.62 },
    label: "Victim",
    interactable: false,
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
    primitiveKind: "box", color: "#b8912f", scale: { x: 0.05, y: 0.02, z: 0.3 }, label: "Letter opener", interactable: true,
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