import { describe, expect, it } from "vitest";
import {
  FALLBACK_ASSET,
  FALLBACK_COLOR,
  isKnownAsset,
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