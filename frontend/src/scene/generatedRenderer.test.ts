import { describe, expect, it } from "vitest";
import { buildGeneratedComposite } from "./generatedRenderer";
import { FALLBACK_COLOR } from "./assetRegistry";
import { makeGeneratedPart, makeTrophyDefinition } from "./testFixtures";
import type { GeneratedAssetDefinition } from "../api/types";

/**
 * Phase 13 — pure deterministic generated-asset renderer tests.
 *
 * buildGeneratedComposite is a pure, engine-free function: no Babylon, no DOM,
 * no randomness. Identical input ALWAYS yields deep-equal output; unknown/null
 * definitions compile to the neutral fallback and never throw.
 */

describe("buildGeneratedComposite — declarative definition -> composite descriptors", () => {
  it("compiles every definition part in order with exact colors and kinds", () => {
    const built = buildGeneratedComposite(makeTrophyDefinition());
    expect(built.parts).toHaveLength(3);
    // Order is the definition order (backend compiler order part_00..part_0N).
    expect(built.parts[0]).toMatchObject({
      kind: "box",
      color: "#5b3a29", // wood.dark resolved — never derived
    });
    expect(built.parts[1]).toMatchObject({ kind: "cylinder", color: "#c9a227" });
    expect(built.parts[2]).toMatchObject({ kind: "cylinder", color: "#c9a227" });
  });

  it("is deterministic: two builds of the same definition deep-equal", () => {
    const definition = makeTrophyDefinition();
    expect(buildGeneratedComposite(definition)).toEqual(buildGeneratedComposite(definition));
  });

  it("applies parent-relative translation correctly (parent offset accumulates)", () => {
    const built = buildGeneratedComposite(makeTrophyDefinition());
    // root part sits at its declared position.
    expectVec(built.parts[0].offset, 0, -0.22, 0);
    // parented child: parent offset + own position (translation-only rule).
    expectVec(built.parts[1].offset, 0, -0.28, 0);
    // depth-2 chain: grandchild accumulates BOTH ancestors.
    expectVec(built.parts[2].offset, 0, -0.11, 0);
  });

  it("passes each part's Euler rotation through exactly (never composed)", () => {
    const definition = makeTrophyDefinition();
    definition.parts[1].transform.rotation = { x: 1.5707963267948966, y: 0.25, z: -0.1 };
    const built = buildGeneratedComposite(definition);
    expect(built.parts[1].rotation).toEqual({ x: 1.5707963267948966, y: 0.25, z: -0.1 });
  });

  it("maps plane primitives to the flat thin-box primitive (thickness min(scale.z,0.02))", () => {
    const definition: GeneratedAssetDefinition = {
      ...makeTrophyDefinition(),
      parts: [
        makeGeneratedPart("part_00", {
          role: "surface",
          primitive: "plane",
          transform: {
            position: { x: 0, y: 0, z: 0 },
            rotation: { x: 0, y: 0, z: 0 },
            scale: { x: 2.0, y: 1.5, z: 1.0 }, // a spec might declare a thick z — never rendered as a slab
          },
          color: "#c8a87c",
        }),
      ],
      hitbox: { scale: { x: 2.0, y: 1.5, z: 0.3 } },
    };
    const built = buildGeneratedComposite(definition);
    expect(built.parts).toHaveLength(1);
    expect(built.parts[0]).toMatchObject({ kind: "box" });
    expect(built.parts[0].size).toEqual({ x: 2.0, y: 1.5, z: 0.02 });
  });

  it("keeps colors EXACTLY as declared (hex applied verbatim, never derived)", () => {
    const definition = makeTrophyDefinition();
    definition.parts[2].color = "#a1b2c3";
    const built = buildGeneratedComposite(definition);
    expect(built.parts[2].color).toBe("#a1b2c3");
  });

  it("compiles up to the 24-part ceiling without loss", () => {
    const parts = Array.from({ length: 24 }, (_, index) =>
      makeGeneratedPart(`part_${String(index).padStart(2, "0")}`, {
        role: "body",
        color: "#d7d7dc",
      }),
    );
    const definition: GeneratedAssetDefinition = {
      ...makeTrophyDefinition(),
      parts,
    };
    expect(buildGeneratedComposite(definition).parts).toHaveLength(24);
  });
});

describe("buildGeneratedComposite — hitbox and fallback", () => {
  it("exposes the declared picking extent (def.hitbox.scale) verbatim and bounded", () => {
    const built = buildGeneratedComposite(makeTrophyDefinition());
    expect(built.hitbox).toEqual({ x: 0.3, y: 0.5, z: 0.3 });
    for (const axis of ["x", "y", "z"] as const) {
      const value = built.hitbox?.[axis];
      expect(value).not.toBeNull();
      expect(Number.isFinite(value)).toBe(true);
      expect(value!).toBeGreaterThanOrEqual(0.15);
      expect(value!).toBeLessThanOrEqual(10);
    }
  });

  it("never throws and returns the neutral fallback parts for a null/garbage definition", () => {
    expect(buildGeneratedComposite(null).parts[0]).toMatchObject({
      kind: "box",
      size: { x: 0.4, y: 0.4, z: 0.4 },
      color: FALLBACK_COLOR,
    });
    expect(buildGeneratedComposite(undefined).parts[0]).toMatchObject({
      kind: "box",
      color: FALLBACK_COLOR,
    });
    // Garbage that is not a structurally valid definition degrades the same way.
    expect(buildGeneratedComposite({ parts: "nope" } as never).parts[0]).toMatchObject({
      kind: "box",
      color: FALLBACK_COLOR,
    });
    expect(buildGeneratedComposite({ hitbox: { scale: { x: 1, y: 1 } } } as never).parts[0]).toMatchObject({
      kind: "box",
      color: FALLBACK_COLOR,
    });
  });
});

/** Bounded per-axis float assertion (float accumulation never compares exact). */
function expectVec(actual: { x: number; y: number; z: number }, x: number, y: number, z: number): void {
  expect(actual.x).toBeCloseTo(x, 9);
  expect(actual.y).toBeCloseTo(y, 9);
  expect(actual.z).toBeCloseTo(z, 9);
}