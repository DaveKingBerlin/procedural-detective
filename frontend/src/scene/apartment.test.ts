import { describe, expect, it } from "vitest";
import { buildApartmentManifest, type ScenePrimitive } from "./apartment";

function countByKind(manifest: ScenePrimitive[], kind: ScenePrimitive["kind"]): number {
  return manifest.filter((p) => p.kind === kind).length;
}

describe("buildApartmentManifest", () => {
  const manifest = buildApartmentManifest();

  it("is deterministic: two calls are deep-equal", () => {
    expect(buildApartmentManifest()).toEqual(buildApartmentManifest());
  });

  it("contains exactly one floor", () => {
    expect(countByKind(manifest, "floor")).toBe(1);
  });

  it("contains at least four walls", () => {
    expect(countByKind(manifest, "wall")).toBeGreaterThanOrEqual(4);
  });

  it("contains at least one door (a gap in one wall)", () => {
    expect(countByKind(manifest, "door")).toBeGreaterThanOrEqual(1);
  });

  it("contains at least one table", () => {
    expect(countByKind(manifest, "table")).toBeGreaterThanOrEqual(1);
  });

  it("contains at least one light", () => {
    expect(countByKind(manifest, "light")).toBeGreaterThanOrEqual(1);
  });

  it("uses unique ids", () => {
    const ids = manifest.map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("uses finite coordinates everywhere", () => {
    const values: number[] = [];
    for (const p of manifest) {
      values.push(p.position.x, p.position.y, p.position.z);
      if (p.rotation) values.push(p.rotation.x, p.rotation.y, p.rotation.z);
      if (p.scale) values.push(p.scale.x, p.scale.y, p.scale.z);
    }
    expect(values.length).toBeGreaterThan(0);
    for (const value of values) {
      expect(Number.isFinite(value)).toBe(true);
    }
  });
});