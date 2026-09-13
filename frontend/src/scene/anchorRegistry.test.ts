import { describe, expect, it } from "vitest";
import {
  ANCHOR_REGISTRY,
  fallbackAnchor,
  resolveAnchor,
  stableHash,
} from "./anchorRegistry";

describe("anchor registry determinism (J)", () => {
  it("maps the same anchor to the same transform on every call", () => {
    expect(resolveAnchor("kitchen_counter", "kitchen_knife")).toEqual(
      resolveAnchor("kitchen_counter", "kitchen_knife"),
    );
  });

  it("registers the REQUIREMENTS 26 anchors", () => {
    for (const anchorName of ["desk_main", "kitchen_counter", "dining_table", "bedside_table", "floor_body_position", "shelf_01"]) {
      expect(ANCHOR_REGISTRY.has(anchorName), `missing anchor ${anchorName}`).toBe(true);
    }
  });

  it("keeps all registry positions finite and inside the apartment shell", () => {
    for (const [name, transform] of ANCHOR_REGISTRY) {
      const { x, y, z } = transform.position;
      expect(Number.isFinite(x), `${name} x`).toBe(true);
      expect(Number.isFinite(y), `${name} y`).toBe(true);
      expect(Number.isFinite(z), `${name} z`).toBe(true);
      expect(Math.abs(x)).toBeLessThanOrEqual(5);
      expect(Math.abs(z)).toBeLessThanOrEqual(4);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(3);
    }
  });
});

describe("fallbackAnchor (unknown anchors)", () => {
  it("derives a stable slot from the object id, not the array index", () => {
    expect(fallbackAnchor("obj_alpha")).toEqual(fallbackAnchor("obj_alpha"));
    expect(fallbackAnchor("obj_alpha")).toEqual(fallbackAnchor("obj_alpha"));
  });

  it("places different object ids at different stable slots", () => {
    expect(fallbackAnchor("obj_alpha")).not.toEqual(fallbackAnchor("obj_beta"));
  });

  it("returns the registry entry for known anchors and the hash slot for unknown", () => {
    expect(resolveAnchor("kitchen_counter", "any")).toEqual(ANCHOR_REGISTRY.get("kitchen_counter"));
    expect(resolveAnchor("no_such_anchor", "kitchen_knife")).toEqual(fallbackAnchor("kitchen_knife"));
  });

  it("produces positions inside the room bounds", () => {
    for (const id of ["a", "b", "c", "kitchen_knife", "apartment_laptop", "victim_body_placeholder"]) {
      const { x, y, z } = fallbackAnchor(id).position;
      expect(Math.abs(x)).toBeLessThanOrEqual(5);
      expect(Number.isFinite(x)).toBe(true);
      expect(Math.abs(z)).toBeLessThanOrEqual(4);
      expect(y).toBe(0.5);
    }
  });
});

describe("stableHash", () => {
  it("is deterministic and pure", () => {
    expect(stableHash("kitchen_knife")).toBe(stableHash("kitchen_knife"));
    expect(stableHash("a")).not.toBe(stableHash("b"));
  });

  it("returns an unsigned 32-bit integer", () => {
    const hash = stableHash("kitchen_knife");
    expect(Number.isInteger(hash)).toBe(true);
    expect(hash).toBeGreaterThanOrEqual(0);
    expect(hash).toBeLessThanOrEqual(0xffffffff);
  });
});