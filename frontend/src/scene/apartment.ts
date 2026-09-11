/**
 * Pure, deterministic placeholder apartment manifest (Phase 2).
 *
 * No Math.random, no Date, no network access: the same scene is produced on
 * every call, which keeps it unit-testable and safe to render from primitives.
 */

export type PrimitiveKind = "floor" | "wall" | "door" | "table" | "light";

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export interface ScenePrimitive {
  id: string;
  kind: PrimitiveKind;
  position: Vec3;
  rotation?: Vec3;
  scale?: Vec3;
  color?: string;
}

export const APARTMENT_DIMENSIONS = {
  width: 10, // along x
  depth: 8, // along z
  height: 3, // along y
  wallThickness: 0.2,
  doorWidth: 1.6,
} as const;

const WALL_Y = APARTMENT_DIMENSIONS.height / 2; // walls rise half-height above/below their center

// Front wall (z = +depth/2): split into two segments leaving the door gap
// centered at x = 0. Segment width = (10 - 1.6 door gap) / 2 = 4.2;
// segment centers sit at ±2.9 so the segments span x ∈ [-5, -0.8] and [0.8, 5].
const FRONT_SEGMENT_W = (APARTMENT_DIMENSIONS.width - APARTMENT_DIMENSIONS.doorWidth) / 2;
const FRONT_SEGMENT_CENTER_X = APARTMENT_DIMENSIONS.width / 2 - FRONT_SEGMENT_W / 2;

/**
 * Build the fixed placeholder apartment scene.
 *
 * Layout: a 10x8x3 room. The front wall (z = +4) is split into two segments
 * leaving a door gap centered at x = 0; the "door" primitive is the slab that
 * fills that gap. A table sits near the back-left, with a warm light overhead.
 */
export function buildApartmentManifest(): ScenePrimitive[] {
  return [
    // --- floor ---
    {
      id: "floor_01",
      kind: "floor",
      position: { x: 0, y: 0, z: 0 },
      scale: { x: APARTMENT_DIMENSIONS.width, y: 0.2, z: APARTMENT_DIMENSIONS.depth },
      color: "#3b4252",
    },
    // --- walls (front wall split around the door gap) ---
    {
      id: "wall_front_left",
      kind: "wall",
      position: { x: -FRONT_SEGMENT_CENTER_X, y: WALL_Y, z: APARTMENT_DIMENSIONS.depth / 2 },
      scale: { x: FRONT_SEGMENT_W, y: APARTMENT_DIMENSIONS.height, z: APARTMENT_DIMENSIONS.wallThickness },
      color: "#6b7280",
    },
    {
      id: "wall_front_right",
      kind: "wall",
      position: { x: FRONT_SEGMENT_CENTER_X, y: WALL_Y, z: APARTMENT_DIMENSIONS.depth / 2 },
      scale: { x: FRONT_SEGMENT_W, y: APARTMENT_DIMENSIONS.height, z: APARTMENT_DIMENSIONS.wallThickness },
      color: "#6b7280",
    },
    {
      id: "wall_back",
      kind: "wall",
      position: { x: 0, y: WALL_Y, z: -APARTMENT_DIMENSIONS.depth / 2 },
      scale: {
        x: APARTMENT_DIMENSIONS.width,
        y: APARTMENT_DIMENSIONS.height,
        z: APARTMENT_DIMENSIONS.wallThickness,
      },
      color: "#6b7280",
    },
    {
      id: "wall_left",
      kind: "wall",
      position: { x: -APARTMENT_DIMENSIONS.width / 2, y: WALL_Y, z: 0 },
      scale: {
        x: APARTMENT_DIMENSIONS.wallThickness,
        y: APARTMENT_DIMENSIONS.height,
        z: APARTMENT_DIMENSIONS.depth,
      },
      color: "#6b7280",
    },
    {
      id: "wall_right",
      kind: "wall",
      position: { x: APARTMENT_DIMENSIONS.width / 2, y: WALL_Y, z: 0 },
      scale: {
        x: APARTMENT_DIMENSIONS.wallThickness,
        y: APARTMENT_DIMENSIONS.height,
        z: APARTMENT_DIMENSIONS.depth,
      },
      color: "#6b7280",
    },
    // --- door (slab filling the gap in the front wall) ---
    {
      id: "door_01",
      kind: "door",
      position: { x: 0, y: 1.1, z: APARTMENT_DIMENSIONS.depth / 2 },
      scale: { x: APARTMENT_DIMENSIONS.doorWidth, y: 2.2, z: 0.12 },
      color: "#7c4a21",
    },
    // --- table (placeholder low block) ---
    {
      id: "table_01",
      kind: "table",
      position: { x: 1.25, y: 0.425, z: -1 },
      scale: { x: 1.8, y: 0.85, z: 1.0 },
      color: "#8a5a2b",
    },
    // --- light ---
    {
      id: "light_01",
      kind: "light",
      position: { x: 0, y: 2.7, z: 0 },
      color: "#fff4e0",
    },
  ];
}