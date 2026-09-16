import { describe, expect, it } from "vitest";
import {
  buildObjectComposite,
  FALLBACK_ASSET,
  FALLBACK_COLOR,
  isKnownAsset,
  MIN_PICKABLE_EXTENT,
  needsPickHitbox,
  pickHitboxExtent,
  resolveAsset,
} from "./assetRegistry";
import { EMITTED_ASSET_IDS } from "./testFixtures";

describe("asset registry contents (emitted backend dev-fixture ids + dot-style aliases)", () => {
  const documentedIds = [
    // ---- the 9 ids the backend dev-mode case actually emits (source of truth)
    "PROP_KITCHEN_KNIFE_01",
    "PROP_LETTER_OPENER_01",
    "PROP_SCISSORS_01",
    "PROP_VASE_01",
    "PROP_LAPTOP_01",
    "PROP_TABLE_01",
    "DOOR_APARTMENT_01",
    "PROP_LAMP_01",
    "PROP_BODY_PLACEHOLDER_01",
    // ---- dot-style aliases kept for older fixtures/tooling
    "apartment.table.basic",
    "apartment.laptop.basic",
    "evidence.knife.basic",
    "prop.body.victim.basic",
    "apartment.door.basic",
    "apartment.lamp.basic",
    "apartment.chair.basic",
  ];

  it("registers every documented id and marks them known", () => {
    for (const id of documentedIds) {
      expect(isKnownAsset(id), `expected ${id} to be known`).toBe(true);
      const entry = resolveAsset(id);
      expect(entry).not.toBe(FALLBACK_ASSET);
      expect(entry.color).toMatch(/^#[0-9a-fA-F]{6}$/);
      expect(Number.isFinite(entry.scale.x)).toBe(true);
      expect(Number.isFinite(entry.scale.y)).toBe(true);
      expect(Number.isFinite(entry.scale.z)).toBe(true);
    }
  });

  it("registers every assetId the backend dev-mode case emits (DEF-049)", () => {
    for (const id of EMITTED_ASSET_IDS) {
      expect(isKnownAsset(id), `emitted id ${id} must be registered`).toBe(true);
      expect(resolveAsset(id)).not.toBe(FALLBACK_ASSET);
    }
  });

  it("maps the expected labels and interactability for the emitted golden objects", () => {
    expect(resolveAsset("PROP_KITCHEN_KNIFE_01").label).toBe("Kitchen knife");
    expect(resolveAsset("PROP_KITCHEN_KNIFE_01").interactable).toBe(true);
    expect(resolveAsset("PROP_LETTER_OPENER_01").label).toBe("Letter opener");
    expect(resolveAsset("PROP_LETTER_OPENER_01").interactable).toBe(true);
    expect(resolveAsset("PROP_SCISSORS_01").label).toBe("Scissors");
    expect(resolveAsset("PROP_SCISSORS_01").interactable).toBe(true);
    expect(resolveAsset("PROP_VASE_01").label).toBe("Vase");
    expect(resolveAsset("PROP_LAPTOP_01").label).toBe("Laptop");
    expect(resolveAsset("PROP_LAPTOP_01").interactable).toBe(true); // -> email discovery
    expect(resolveAsset("PROP_TABLE_01").label).toBe("Table");
    expect(resolveAsset("DOOR_APARTMENT_01").label).toBe("Door");
    expect(resolveAsset("PROP_LAMP_01").label).toBe("Lamp");
    expect(resolveAsset("PROP_BODY_PLACEHOLDER_01").label).toBe("Victim");
    expect(resolveAsset("PROP_BODY_PLACEHOLDER_01").interactable).toBe(false); // no evidence placement
  });

  it("keeps both id styles for the kitchen knife identical in shape", () => {
    const styleA = resolveAsset("PROP_KITCHEN_KNIFE_01");
    const styleB = resolveAsset("evidence.knife.basic");
    expect(styleA.primitiveKind).toBe(styleB.primitiveKind);
    expect(styleA.scale).toEqual(styleB.scale);
    expect(styleA.label).toBe("Kitchen knife");
    expect(styleA.interactable).toBe(true);
  });

  it("maps the expected primitives for the golden objects", () => {
    expect(resolveAsset("PROP_KITCHEN_KNIFE_01").primitiveKind).toBe("box");
    expect(resolveAsset("PROP_LAPTOP_01").primitiveKind).toBe("flat");
    expect(resolveAsset("PROP_LAMP_01").primitiveKind).toBe("cylinder");
    expect(resolveAsset("PROP_VASE_01").primitiveKind).toBe("cylinder");
    expect(resolveAsset("PROP_BODY_PLACEHOLDER_01").primitiveKind).toBe("flat");
  });
});

describe("unknown asset id behavior", () => {
  it("never throws and resolves to the neutral gray fallback", () => {
    const entry = resolveAsset("ASSET.THAT.DOES.NOT.EXIST");
    expect(entry).toBe(FALLBACK_ASSET);
    expect(entry.primitiveKind).toBe("box");
    expect(entry.color).toBe(FALLBACK_COLOR);
    expect(entry.label).toBeNull();
    expect(entry.interactable).toBe(false);
  });

  it("marks unknown ids as not known", () => {
    expect(isKnownAsset("ASSET.THAT.DOES.NOT.EXIST")).toBe(false);
    expect(isKnownAsset("")).toBe(false);
    expect(isKnownAsset("javascript:alert(1)")).toBe(false);
    expect(isKnownAsset("https://evil.example/x.glb")).toBe(false);
    expect(isKnownAsset("file:///etc/passwd")).toBe(false);
  });

  it("is deterministic across repeated lookups", () => {
    expect(resolveAsset("mystery.asset.01")).toBe(resolveAsset("mystery.asset.01"));
    expect(resolveAsset("PROP_KITCHEN_KNIFE_01")).toBe(resolveAsset("PROP_KITCHEN_KNIFE_01"));
  });

  it("never exposes server-provided strings as colors or labels for unknown ids", () => {
    const entry = resolveAsset("#ff0000;url(javascript:alert(1))");
    expect(entry.color).toBe(FALLBACK_COLOR);
    expect(entry.label).toBeNull();
  });
});

describe("registry immutability", () => {
  it("is a ReadonlyMap and the fallback is frozen", () => {
    expect(Object.isFrozen(FALLBACK_ASSET)).toBe(true);
    expect(Object.isFrozen(FALLBACK_ASSET.scale)).toBe(true);
  });
});

/* ======================================================================
 * Phase 8_1 — composite visuals + invisible pick hitbox policy.
 * ==================================================================== */

describe("buildObjectComposite — knife vs letter opener silhouette differentiation (Phase 8_1 C)", () => {
  const knifeParts = buildObjectComposite("knife", resolveAsset("PROP_KITCHEN_KNIFE_01"));
  const openerParts = buildObjectComposite("letter-opener", resolveAsset("PROP_LETTER_OPENER_01"));

  const longestBlade = (parts: typeof knifeParts) =>
    Math.max(...parts.map((p) => (p.kind === "box" ? Math.max(p.size.x, p.size.z) : 0)));

  it("knife and opener are both composites with the expected part counts", () => {
    expect(knifeParts).toHaveLength(2); // blade + handle
    expect(openerParts).toHaveLength(2); // blade + handle
    expect(knifeParts.every((p) => p.kind === "box")).toBe(true);
    expect(knifeParts.every((p) => /^#[0-9a-fA-F]{6}$/.test(p.color))).toBe(true);
  });

  it("the knife blade is > 1.5x LONGER than the letter-opener blade", () => {
    const knifeBlade = longestBlade(knifeParts);
    const openerBlade = longestBlade(openerParts);
    expect(knifeBlade).toBeGreaterThan(openerBlade * 1.5);
  });

  it("knife vs opener use DISTINCT material colors (steel vs brass)", () => {
    expect(knifeParts[0].color).toBe("#c8ccd4");
    expect(openerParts[0].color).toBe("#a37b35");
    expect(knifeParts[0].color).not.toBe(openerParts[0].color);
    // The knife handle is a darker wood/grip tone, the opener handle a stub.
    expect(knifeParts[1].color).toBe("#5a3b22");
  });

  it("the opener blade is the WIDER, spatulate kind (clearly not a knife)", () => {
    const knifeBladeW = knifeParts[0].size.x;
    const openerBladeW = openerParts[0].size.x;
    expect(openerBladeW).toBeGreaterThan(knifeBladeW);
  });

  it("is fully deterministic across calls", () => {
    const again = buildObjectComposite("knife", resolveAsset("PROP_KITCHEN_KNIFE_01"));
    expect(again).toEqual(knifeParts);
  });
});

describe("buildObjectComposite — laptop / victim / table / fallback (Phase 8_1 C)", () => {
  it("laptop is a THIN two-part composite (base + lid)", () => {
    const parts = buildObjectComposite("laptop", resolveAsset("PROP_LAPTOP_01"));
    expect(parts).toHaveLength(2);
    for (const part of parts) {
      expect(part.kind).toBe("box");
      expect(part.size.y, "laptop parts are thin (flat two-part shape)").toBeLessThan(0.05);
    }
    expect(parts.map((p) => p.size.x)).toEqual([0.56, 0.56]); // same footprint -> stacked lid
  });

  it("victim is a calm two-part recumbent representation with a blanket", () => {
    const parts = buildObjectComposite("victim", resolveAsset("PROP_BODY_PLACEHOLDER_01"));
    expect(parts).toHaveLength(2); // body + blanket
    expect(parts[0].size).toEqual({ x: 1.15, y: 0.14, z: 0.5 }); // low flat body
    expect(parts[1].size.z).toBeLessThan(parts[0].size.z); // blanket is subtly smaller
    expect(parts[1].offset.y).toBeGreaterThan(parts[0].offset.y); // blanket above the body
  });

  it("table is a tabletop slab + four thin legs", () => {
    const parts = buildObjectComposite("table", resolveAsset("PROP_TABLE_01"));
    expect(parts).toHaveLength(5);
    expect(parts[0].size.y).toBe(0.1); // tabletop
    for (const leg of parts.slice(1)) {
      expect(leg.kind).toBe("box");
      expect(leg.size.x).toBeLessThan(0.1); // thin leg
    }
  });

  it("unknown composite kinds fall back to an EMPTY part list (never crash)", () => {
    expect(buildObjectComposite("bogus" as never, resolveAsset("PROP_VASE_01"))).toEqual([]);
  });
});

describe("invisible pick hitbox policy (Phase 8_1 A2)", () => {
  it("small evidence (knife, letter opener) triggers a hitbox of the safe minimum extent", () => {
    for (const id of ["PROP_KITCHEN_KNIFE_01", "PROP_LETTER_OPENER_01", "PROP_LAPTOP_01"]) {
      const entry = resolveAsset(id);
      expect(needsPickHitbox(entry), `${id} must need a pick hitbox`).toBe(true);
      const extent = pickHitboxExtent(entry);
      expect(extent.x).toBeGreaterThanOrEqual(MIN_PICKABLE_EXTENT);
      expect(extent.y).toBeGreaterThanOrEqual(MIN_PICKABLE_EXTENT);
      expect(extent.z).toBeGreaterThanOrEqual(MIN_PICKABLE_EXTENT);
      expect(extent.x).toBeLessThanOrEqual(Math.max(entry.scale.x, MIN_PICKABLE_EXTENT) * (entry.hitboxScale ?? 1));
    }
    // The knife's full extent clamps to the documented threshold.
    expect(pickHitboxExtent(resolveAsset("PROP_KITCHEN_KNIFE_01"))).toEqual({
      x: MIN_PICKABLE_EXTENT,
      y: MIN_PICKABLE_EXTENT,
      z: MIN_PICKABLE_EXTENT,
    });
  });

  it("large blocky objects (table) do not need a hitbox; THIN slabs (door) still do", () => {
    // The table's smallest footprint dimension is 0.85m — already pickable.
    expect(needsPickHitbox(resolveAsset("PROP_TABLE_01"))).toBe(false);
    // The door is a 0.12m thin slab: below the threshold, so it benefits
    // from the safe enlarged invisible hitbox to catch clicks on its face.
    expect(needsPickHitbox(resolveAsset("DOOR_APARTMENT_01"))).toBe(true);
    expect(pickHitboxExtent(resolveAsset("DOOR_APARTMENT_01"))).toEqual({
      x: 1.6,
      y: 2.2,
      z: MIN_PICKABLE_EXTENT,
    });
  });

  it("rejects nonsense hitbox input deterministically (no NaN/Infinity)", () => {
    const extent = pickHitboxExtent({ scale: { x: 0, y: -2, z: 0.1 } });
    expect(Number.isFinite(extent.x)).toBe(true);
    expect(Number.isFinite(extent.y)).toBe(true);
    expect(Number.isFinite(extent.z)).toBe(true);
    expect(extent.x).toBe(MIN_PICKABLE_EXTENT);
    expect(extent.z).toBe(MIN_PICKABLE_EXTENT);
  });
});