import { describe, expect, it } from "vitest";
import { catalogAssetIds, getAsset } from "../catalog/assetCatalog";
import { TEMPLATE_FALLBACK, buildTemplateComposite, getTemplate } from "../templates/templateRegistry";
import {
  buildObjectComposite,
  FALLBACK_ASSET,
  FALLBACK_COLOR,
  isKnownAsset,
  MIN_PICKABLE_EXTENT,
  needsPickHitbox,
  pickHitboxExtent,
  resolveAsset,
  type AssetEntry,
} from "./assetRegistry";
import { EMITTED_ASSET_IDS } from "./testFixtures";

describe("asset registry derives from the catalog manifest (Phase 10 Track B)", () => {
  // The SAME frozen v1 ids the backend Asset Oracle emits/resolves — the
  // registry carries EXACTLY the catalog's assetIds (incl. the fallback
  // asset). Dot-style aliases ("apartment.laptop.basic", ...) are NO LONGER
  // registry ids: the frontend never resolves aliases (backend-only).
  const documentedIds = catalogAssetIds();

  it("registers every catalog assetId and marks them known", () => {
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
    // Interactability is the catalog's value: only evidence-bearing assets
    // (knife/opener/scissors/laptop) are interactable in the manifest.
    expect(resolveAsset("PROP_VASE_01").interactable).toBe(false);
    expect(resolveAsset("PROP_LAPTOP_01").label).toBe("Laptop");
    expect(resolveAsset("PROP_LAPTOP_01").interactable).toBe(true); // -> email discovery
    expect(resolveAsset("PROP_TABLE_01").label).toBe("Table");
    expect(resolveAsset("PROP_TABLE_01").interactable).toBe(false);
    expect(resolveAsset("DOOR_APARTMENT_01").label).toBe("Door");
    expect(resolveAsset("DOOR_APARTMENT_01").interactable).toBe(false);
    expect(resolveAsset("PROP_LAMP_01").label).toBe("Lamp");
    expect(resolveAsset("PROP_LAMP_01").interactable).toBe(false);
    expect(resolveAsset("PROP_BODY_PLACEHOLDER_01").label).toBe("Victim");
    expect(resolveAsset("PROP_BODY_PLACEHOLDER_01").interactable).toBe(false); // no evidence placement
  });

  it("derives EVERY entry from its catalog descriptor (label/color/composite)", () => {
    for (const id of catalogAssetIds()) {
      const descriptor = getAsset(id);
      expect(descriptor, `catalog id ${id} exists`).toBeDefined();
      const entry = resolveAsset(id);
      expect(entry.label, `${id} label comes from the catalog`).toBe(descriptor!.label);
      expect(entry.interactable, `${id} interactable comes from the catalog`).toBe(descriptor!.interactable);
      expect(entry.scale, `${id} scale comes from the catalog dimensions`).toEqual(descriptor!.dimensions);
      expect(entry.compositeKind === null, `${id} composite presence matches the catalog`).toBe(
        descriptor!.compositeKind === null,
      );
    }
  });

  it("spot-checks the golden catalog derivation (knife/opener/laptop/victim/table/door/lamp/vase)", () => {
    expect(resolveAsset("PROP_KITCHEN_KNIFE_01")).toMatchObject({
      primitiveKind: "box",
      color: "#c8ccd4",
      label: "Kitchen knife",
      compositeKind: "knife",
    });
    expect(resolveAsset("PROP_LETTER_OPENER_01")).toMatchObject({
      color: "#a37b35",
      label: "Letter opener",
      compositeKind: "letter-opener",
    });
    expect(resolveAsset("PROP_SCISSORS_01")).toMatchObject({
      primitiveKind: "box",
      color: "#b8bcc4",
      label: "Scissors",
      compositeKind: "scissors",
    });
    expect(resolveAsset("PROP_LAPTOP_01")).toMatchObject({
      primitiveKind: "flat",
      color: "#30343e",
      label: "Laptop",
      compositeKind: "laptop",
    });
    expect(resolveAsset("PROP_BODY_PLACEHOLDER_01")).toMatchObject({
      primitiveKind: "flat",
      color: "#8f8579",
      label: "Victim",
      compositeKind: "victim",
    });
    expect(resolveAsset("PROP_TABLE_01")).toMatchObject({
      primitiveKind: "box",
      color: "#8a5a2b",
      label: "Table",
      compositeKind: "table",
    });
    expect(resolveAsset("DOOR_APARTMENT_01")).toEqual(
      expect.objectContaining({ primitiveKind: "box", color: "#7c4a21", label: "Door", compositeKind: null }),
    );
    expect(resolveAsset("PROP_LAMP_01")).toEqual(
      expect.objectContaining({ primitiveKind: "cylinder", color: "#e8d9a8", label: "Lamp", compositeKind: null }),
    );
    expect(resolveAsset("PROP_VASE_01")).toEqual(
      expect.objectContaining({ primitiveKind: "cylinder", color: "#7d5a4c", label: "Vase", compositeKind: null }),
    );
  });

  it("maps the expected primitives for the golden objects", () => {
    expect(resolveAsset("PROP_KITCHEN_KNIFE_01").primitiveKind).toBe("box");
    expect(resolveAsset("PROP_LAPTOP_01").primitiveKind).toBe("flat");
    expect(resolveAsset("PROP_LAMP_01").primitiveKind).toBe("cylinder");
    expect(resolveAsset("PROP_VASE_01").primitiveKind).toBe("cylinder");
    expect(resolveAsset("PROP_BODY_PLACEHOLDER_01").primitiveKind).toBe("flat");
  });
});

describe("frontend/backend boundary — exact assetIds only (aliases are backend-resolved)", () => {
  it("a legacy dot-alias id is NOT a catalog id and resolves to the fallback", () => {
    // "apartment.laptop.basic" is a declared ALIAS of PROP_LAPTOP_01 in the
    // manifest — but alias resolution belongs to the backend Asset Oracle.
    // The frontend only accepts exact catalog assetIds; asserting the fallback
    // documents that boundary (a backend-published WorldGraph DTO always
    // carries the exact assetId, so no reachable placement hits this path).
    expect(getAsset("apartment.laptop.basic")).toBeUndefined();
    expect(isKnownAsset("apartment.laptop.basic")).toBe(false);
    expect(resolveAsset("apartment.laptop.basic")).toBe(FALLBACK_ASSET);

    expect(isKnownAsset("evidence.knife.basic")).toBe(false);
    expect(resolveAsset("evidence.knife.basic")).toBe(FALLBACK_ASSET);
    expect(isKnownAsset("apartment.chair.basic")).toBe(false);
    expect(resolveAsset("apartment.chair.basic")).toBe(FALLBACK_ASSET);
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

describe("buildObjectComposite — scissors (Phase 10 catalog compositeKind; Phase 9 legibility)", () => {
  const scissorsParts = buildObjectComposite("scissors", resolveAsset("PROP_SCISSORS_01"));
  const knifeParts = buildObjectComposite("knife", resolveAsset("PROP_KITCHEN_KNIFE_01"));
  const openerParts = buildObjectComposite("letter-opener", resolveAsset("PROP_LETTER_OPENER_01"));

  it("scissors is a THREE-part composite: two thin blades + a pivot rivet", () => {
    expect(scissorsParts).toHaveLength(3);
    expect(scissorsParts[0].kind).toBe("box");
    expect(scissorsParts[1].kind).toBe("box");
    expect(scissorsParts[2].kind).toBe("cylinder");
    for (const part of scissorsParts) {
      expect(/^#[0-9a-fA-F]{6}$/.test(part.color)).toBe(true);
    }
  });

  it("uses the catalog descriptor colors (blades + pivot)", () => {
    expect(scissorsParts[0].color).toBe("#b8bcc4");
    expect(scissorsParts[1].color).toBe("#b8bcc4");
    expect(scissorsParts[2].color).toBe("#5b6068");
    // Blade tone is neither the knife's steel nor the opener's brass.
    expect(scissorsParts[0].color).not.toBe(knifeParts[0].color);
    expect(scissorsParts[0].color).not.toBe(openerParts[0].color);
  });

  it("produces a DISTINCT silhouette signature vs knife and opener", () => {
    // Knife: ONE straight 0.24m blade, no rotation. Opener: short WIDE 0.09m
    // blade, no rotation. Scissors: TWO short 0.1m blades CROSSED on a pivot.
    expect(knifeParts).toHaveLength(2);
    expect(openerParts).toHaveLength(2);
    expect(knifeParts.some((p) => p.rotation !== undefined)).toBe(false);
    expect(openerParts.some((p) => p.rotation !== undefined)).toBe(false);
    expect(scissorsParts.some((p) => p.rotation !== undefined)).toBe(true);

    const longestBlade = (parts: typeof knifeParts) =>
      Math.max(...parts.map((p) => (p.kind === "box" ? Math.max(p.size.x, p.size.z) : 0)));
    const bladeWidth = (parts: typeof knifeParts) =>
      Math.max(...parts.map((p) => (p.kind === "box" ? Math.min(p.size.x, p.size.z) : 0)));
    // Distinct length ordering: knife 0.24 > opener 0.13 > scissors 0.1.
    const lengths = [longestBlade(knifeParts), longestBlade(openerParts), longestBlade(scissorsParts)];
    expect(lengths[0]).toBeGreaterThan(lengths[1]);
    expect(lengths[1]).toBeGreaterThan(lengths[2]);
    // Distinct width ordering: opener (spatulate, 0.09) > knife (0.045) > scissors (0.022).
    expect(bladeWidth(openerParts)).toBeGreaterThan(bladeWidth(knifeParts));
    expect(bladeWidth(knifeParts)).toBeGreaterThan(bladeWidth(scissorsParts));
  });

  it("is fully deterministic across calls", () => {
    expect(buildObjectComposite("scissors", resolveAsset("PROP_SCISSORS_01"))).toEqual(scissorsParts);
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

/* ======================================================================
 * Phase 12 Track B — template-backed composite integration.
 *
 * Every catalog composite now renders through the generic template factory;
 * the six LEGACY composite builders keep their byte-identical geometry for
 * the golden apartment. These tests pin BOTH contracts.
 * ==================================================================== */

describe("Phase 12 — template-backed composites in the asset registry", () => {
  it("every TEMPLATE-ONLY composite resolves to factory parts with valid geometry", () => {
    const templateOnly = catalogAssetIds()
      .map((id) => getAsset(id))
      .filter((d) => d !== undefined && d.renderKind === "composite" && d.compositeKind === null);
    expect(templateOnly.length).toBeGreaterThan(0);
    for (const descriptor of templateOnly) {
      const id = descriptor!.assetId;
      const entry: AssetEntry = resolveAsset(id);
      expect(entry, `${id} resolves`).not.toBe(FALLBACK_ASSET);
      expect(entry.templateId, `${id} carries its frozen templateId`).toBe(descriptor!.templateId);
      expect(entry.templateParts, `${id} has factory-built parts`).not.toBeNull();
      expect(entry.templateParts!.length, `${id} builds >= 1 part`).toBeGreaterThanOrEqual(1);
      expect(entry.templateFaceBounds, `${id} has factory bounds`).not.toBeNull();
      for (const part of entry.templateParts!) {
        expect(Number.isFinite(part.size.x) && Number.isFinite(part.size.y) && Number.isFinite(part.size.z)).toBe(true);
        expect(part.color).toMatch(/^#[0-9a-fA-F]{6}$/);
      }
      // Rebuilding from the descriptor's own template+colors is deep-equal
      // (registry prebuild == factory on demand — deterministic single source).
      const rebuilt = buildTemplateComposite(descriptor!.templateId as string, {
        colors: descriptor!.colors,
        scale: 1,
      });
      expect(entry.templateHitbox).toEqual(rebuilt.hitbox);
      expect(entry.templateFaceBounds).toEqual(rebuilt.bounds);
    }
  });

  it("every composite templateId in the manifest resolves to a REAL template (no fallback)", () => {
    for (const id of catalogAssetIds()) {
      const descriptor = getAsset(id);
      if (descriptor === undefined || descriptor.renderKind !== "composite" || descriptor.templateId === null) continue;
      expect(getTemplate(descriptor.templateId), `${descriptor.assetId} uses ${descriptor.templateId}`).not.toBe(
        TEMPLATE_FALLBACK,
      );
    }
  });

  it("the six LEGACY composites keep their builders (templateParts stay null)", () => {
    const legacy = ["PROP_KITCHEN_KNIFE_01", "PROP_LETTER_OPENER_01", "PROP_SCISSORS_01", "PROP_LAPTOP_01", "PROP_TABLE_01", "PROP_BODY_PLACEHOLDER_01"];
    for (const id of legacy) {
      const entry = resolveAsset(id);
      expect(entry.compositeKind, `${id} keeps the legacy builder`).not.toBeNull();
      expect(entry.templateParts, `${id} stays on the legacy builder`).toBeNull();
    }
    // Golden geometry is byte-identical (the same builder outputs as Phase 8_1).
    const knife = resolveAsset("PROP_KITCHEN_KNIFE_01");
    expect(buildObjectComposite("knife", { scale: knife.scale, color: knife.color })[0].size).toEqual({
      x: 0.045,
      y: 0.014,
      z: 0.24,
    });
  });

  it("template-only composite part colors derive from the catalog (never fallback gray)", () => {
    for (const id of catalogAssetIds()) {
      const parts = resolveAsset(id).templateParts;
      if (parts === null || parts === undefined) continue;
      expect(parts.every((p) => p.color !== FALLBACK_COLOR), `${id} uses catalog tones`).toBe(true);
    }
  });
});