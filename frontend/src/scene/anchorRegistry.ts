import type { Vec3 } from "./apartment";

/**
 * Semantic anchor registry (Phase 6, REQUIREMENTS 26 — "prefer semantic
 * anchors over raw coordinates").
 *
 * The API sends NO coordinates: an object's placement is derived client-side
 * from its `anchor` through this deterministic map. The same anchor ALWAYS
 * yields the same transform within the 10x8x3 apartment template from
 * src/scene/apartment.ts (determinism J).
 *
 * Anchors not present in the table fall back to {@link fallbackAnchor}, a
 * stable slot derived from a hash of the OBJECT ID — never from array
 * position or render order, so object identity stays stable no matter how
 * the world object array is ordered or shuffled.
 */

export interface AnchorTransform {
  position: Vec3;
  rotation?: Vec3;
  scale?: Vec3;
}

export type AnchorRegistry = ReadonlyMap<string, AnchorTransform>;

function anchor(x: number, y: number, z: number, rotation?: Vec3): AnchorTransform {
  return rotation ? { position: { x, y, z }, rotation } : { position: { x, y, z } };
}

/**
 * Anchor placements inside the apartment shell (floor y=0, walls at
 * x=±5, z=±4, ceiling at y=3; door gap centered at x=0 on the front wall).
 * Surfaces are chosen so objects sit on furniture tops:
 *  - kitchen_counter / counter_03 are counters along the front-left wall;
 *  - desk_main / office_desk_01 are desk surfaces at ~0.7m;
 *  - dining_table sits on the top of the manifest's table_01 (center
 *    {1.25, 0.425, -1}, height 0.85) => ~0.9m;
 *  - floor_body_position lays a body flat on the floor near the kitchen.
 */
export const ANCHOR_REGISTRY: AnchorRegistry = new Map<string, AnchorTransform>([
  ["kitchen_counter", anchor(-2.9, 0.95, 2.2)],
  ["counter_03", anchor(-3.4, 0.95, 3.0)],
  ["desk_main", anchor(2.6, 0.72, -2.2)],
  ["office_desk_01", anchor(3.4, 0.72, -2.6)],
  ["dining_table", anchor(1.25, 0.9, -1.0)],
  ["bedside_table", anchor(-4.0, 0.55, -2.6)],
  ["floor_body_position", anchor(-1.5, 0.09, 2.4)],
  ["shelf_01", anchor(3.9, 1.45, 3.2)],
  // Phase 8: the door is a 1.6-wide slab — sitting flush against the right
  // wall (interior face at x = 4.9) reads as a real doorway instead of a
  // floating board half a meter away from the wall.
  ["hall_wall_01", anchor(4.85, 1.35, 0.0)],
]);

/**
 * FNV-1a 32-bit — a tiny deterministic string hash. Bitwise ops in JS are
 * signed 32-bit, so the accumulator is masked every iteration and finally
 * reinterpreted as UNSIGNED with `>>> 0`. Identical across processes and
 * platforms; used ONLY to derive stable fallback geometry (never security).
 */
export function stableHash(text: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = (hash * 0x01000193) & 0xffffffff;
  }
  return hash >>> 0;
}

/**
 * Deterministic fallback placement for anchors without a registry entry.
 * The slot depends only on the object id string, so the same object always
 * lands at the same place regardless of payload ordering, shuffle or render
 * order.
 */
export function fallbackAnchor(objectId: string): AnchorTransform {
  const seed = stableHash(objectId);
  const x = -4.0 + (seed % 801) / 100.0; //  -4.00 ..  4.00
  // `>>> 8`: seed is unsigned 32-bit; a signed shift would flip sign-bit ids.
  const z = -3.0 + ((seed >>> 8) % 601) / 100.0; //  -3.00 ..  3.00
  return { position: { x, y: 0.5, z } };
}

/** Registry lookup with stable objectId-hash fallback for unknown anchors. */
export function resolveAnchor(anchor: string, objectId: string): AnchorTransform {
  const known = ANCHOR_REGISTRY.get(anchor);
  return known ?? fallbackAnchor(objectId);
}