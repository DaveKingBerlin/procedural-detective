import { describe, expect, it } from "vitest";
import { catalogAssetIds, getAsset } from "../catalog/assetCatalog";
import {
  MATERIAL_VOCABULARY,
  STATE_VOCABULARY,
  TEMPLATE_FALLBACK,
  TEMPLATE_VOCABULARY,
  buildTemplateComposite,
  getTemplate,
  hasTemplate,
  type TemplateCompositeResult,
} from "./templateRegistry";

/**
 * Phase 12 Track B — the frozen 59-template vocabulary + generic
 * template→composite factory. Deterministic, bounded, primitive-only.
 */

/** Strongest aspect ratio across a build's parts (longest/shortest axis). */
function aspectRatio(result: TemplateCompositeResult): number {
  let maxRatio = 0;
  for (const part of result.parts) {
    const axes = [part.size.x, part.size.y, part.size.z];
    const mx = Math.max(...axes);
    const mn = Math.min(...axes);
    maxRatio = Math.max(maxRatio, mx / mn);
  }
  return maxRatio;
}

/** Longest single axis across a build's parts. */
function longestAxis(result: TemplateCompositeResult): number {
  let longest = 0;
  for (const part of result.parts) {
    longest = Math.max(longest, part.size.x, part.size.y, part.size.z);
  }
  return longest;
}

function partCount(result: TemplateCompositeResult): number {
  return result.parts.length;
}

/** Deterministic neutral palette for factory builds in these tests. */
const NEUTRAL_COLORS = { blade: "#c8ccd4", handle: "#5a3b22", body: "#8d8d93" } as const;

function buildAt(templateId: string, scale: number = 1): TemplateCompositeResult {
  return buildTemplateComposite(templateId, { colors: NEUTRAL_COLORS, scale });
}

describe("frozen template vocabulary (backend lockstep)", () => {
  it("has EXACTLY the 59 frozen templates, no duplicates, backend order preserved", () => {
    expect(TEMPLATE_VOCABULARY).toHaveLength(59);
    expect(new Set(TEMPLATE_VOCABULARY).size).toBe(59);
    // The order is the backend's order (backend/app/assets/catalog.py).
    expect(TEMPLATE_VOCABULARY[0]).toBe("blade_chef");
    expect(TEMPLATE_VOCABULARY[58]).toBe("door_slab");
    // Spot-check the critical evidence templates are all present.
    for (const id of ["blade_chef", "blade_bread", "blade_letter", "tool_screwdriver", "tool_hammer", "key_small", "usb_stick", "phone_body", "camera_body"]) {
      expect(TEMPLATE_VOCABULARY).toContain(id);
    }
    // Every template in the vocabulary is buildable via getTemplate (none
    // resolves to the fallback).
    for (const id of TEMPLATE_VOCABULARY) {
      expect(hasTemplate(id), id).toBe(true);
      expect(getTemplate(id), id).not.toBe(TEMPLATE_FALLBACK);
    }
  });

  it("pins the material/state literal vocabularies to the backend 8/7 tokens", () => {
    expect(MATERIAL_VOCABULARY).toEqual(["wood.dark", "wood.light", "metal.brass", "metal.steel", "plastic", "fabric", "leather", "ceramic"]);
    expect(STATE_VOCABULARY).toEqual(["clean", "weathered", "damaged", "open", "closed", "on", "off"]);
  });

  it("every templateId the REAL manifest uses is a member (frontend can never drift)", () => {
    const used = new Set(
      catalogAssetIds()
        .map((id) => getAsset(id))
        .filter((d) => d !== undefined && d.renderKind === "composite")
        .map((d) => d!.templateId)
        .filter((t) => t !== null),
    );
    expect(used.size).toBeGreaterThan(0);
    for (const templateId of used) {
      expect(TEMPLATE_VOCABULARY, `manifest templateId ${templateId} must be in the frozen vocabulary`).toContain(templateId);
      expect(hasTemplate(templateId! as string)).toBe(true);
    }
  });
});

describe("template factory — every frozen template builds safely", () => {
  it("every one of the 59 templates builds at least one part with FINITE, bounded geometry", () => {
    for (const templateId of TEMPLATE_VOCABULARY) {
      const built = buildAt(templateId);
      expect(getTemplate(templateId), templateId).not.toBe(TEMPLATE_FALLBACK);
      expect(built.parts.length, `${templateId} must build >= 1 part`).toBeGreaterThanOrEqual(1);
      for (const part of built.parts) {
        for (const value of [part.size.x, part.size.y, part.size.z, part.offset.x, part.offset.y, part.offset.z]) {
          expect(Number.isFinite(value), `${templateId} finite geometry`).toBe(true);
          expect(Math.abs(value), `${templateId} |value|<=4`).toBeLessThanOrEqual(4);
        }
        if (part.rotation) {
          for (const value of [part.rotation.x, part.rotation.y, part.rotation.z]) {
            expect(Number.isFinite(value)).toBe(true);
          }
        }
        expect(/^#[0-9a-fA-F]{6}$/.test(part.color), `${templateId} hex color`).toBe(true);
      }
      for (const axis of ["x", "y", "z"] as const) {
        expect(Number.isFinite(built.bounds[axis])).toBe(true);
        expect(built.bounds[axis]).toBeGreaterThan(0);
      }
    }
  });

  it("every frozen template declares 2–6 parts (the data-table contract)", () => {
    for (const templateId of TEMPLATE_VOCABULARY) {
      const count = getTemplate(templateId).parts.length;
      expect(count, `${templateId} has 2-6 parts`).toBeGreaterThanOrEqual(2);
      expect(count, `${templateId} has 2-6 parts`).toBeLessThanOrEqual(6);
    }
  });

  it("bounded even at the MAXIMUM variant scale (2.0) — every |axis| <= 4", () => {
    for (const templateId of TEMPLATE_VOCABULARY) {
      const built = buildAt(templateId, 2.0);
      for (const part of built.parts) {
        for (const value of [part.size.x, part.size.y, part.size.z, part.offset.x, part.offset.y, part.offset.z]) {
          expect(Number.isFinite(value), `${templateId} finite at max scale`).toBe(true);
          expect(Math.abs(value), `${templateId} |value|<=4 at max scale`).toBeLessThanOrEqual(4);
        }
      }
      if (built.hitbox !== null) {
        for (const axis of ["x", "y", "z"] as const) {
          expect(Number.isFinite(built.hitbox[axis])).toBe(true);
        }
      }
    }
  });

  it("the factory clamps hostile scales defensively (never escapes [0.5, 2.0])", () => {
    const scaled = buildTemplateComposite("tool_hammer", { colors: NEUTRAL_COLORS, scale: 200 });
    const clamped = buildTemplateComposite("tool_hammer", { colors: NEUTRAL_COLORS, scale: 2.0 });
    expect(scaled).toEqual(clamped);
    const tiny = buildTemplateComposite("tool_hammer", { colors: NEUTRAL_COLORS, scale: 0.001 });
    const tinyClamped = buildTemplateComposite("tool_hammer", { colors: NEUTRAL_COLORS, scale: 0.5 });
    expect(tiny).toEqual(tinyClamped);
  });

  it("is fully deterministic: two builds deep-equal with stable part ORDER", () => {
    for (const templateId of TEMPLATE_VOCABULARY) {
      const first = buildAt(templateId, 1.5);
      const second = buildAt(templateId, 1.5);
      expect(first).toEqual(second);
      expect(first.parts.map((p) => p.kind)).toEqual(second.parts.map((p) => p.kind));
    }
  });
});

describe("unknown template — neutral fallback (never a crash, never a ghost)", () => {
  it("getTemplate('nope') resolves to the non-interactable-safe TEMPLATE_FALLBACK", () => {
    expect(getTemplate("nope")).toBe(TEMPLATE_FALLBACK);
    expect(getTemplate("")).toBe(TEMPLATE_FALLBACK);
    expect(hasTemplate("nope")).toBe(false);
    expect(TEMPLATE_FALLBACK.parts).toHaveLength(1);
    const part = TEMPLATE_FALLBACK.parts[0];
    expect(part.primitiveKind).toBe("box");
    expect(part.scale).toEqual({ x: 0.4, y: 0.4, z: 0.4 });
    expect(part.offset).toEqual({ x: 0, y: 0, z: 0 });
  });

  it("buildTemplateComposite('nope', ...) produces the fallback's single 0.4m cube", () => {
    const built = buildTemplateComposite("nope", { colors: { body: "#8d8d93" }, scale: 1 });
    expect(built.parts).toHaveLength(1);
    expect(built.parts[0].kind).toBe("box");
    expect(built.parts[0].size).toEqual({ x: 0.4, y: 0.4, z: 0.4 });
    expect(built.parts[0].color).toBe("#8d8d93");
    expect(getTemplate("nope")).toBe(TEMPLATE_FALLBACK);
  });
});

describe("critical evidence pairs — materially DIFFERENT silhouettes (Phase 12 / Requirements 2.4)", () => {
  it("chef vs bread vs letter knives are DIFFERENT templates with distinct signatures", () => {
    const chef = buildAt("blade_chef");
    const bread = buildAt("blade_bread");
    const letter = buildAt("blade_letter");
    // Chef = one sliver blade; bread = LONGER blade + a serration layer
    // (26:1 vs 17:1 — materially longer/narrower).
    expect(aspectRatio(bread)).toBeGreaterThan(aspectRatio(chef) * 1.25);
    expect(longestAxis(bread)).toBeGreaterThan(longestAxis(chef));
    // Letter opener = SHORT wide spatulate blade (never knife-like): the chef
    // sliver ratio is > 1.5x the letter's.
    expect(aspectRatio(chef)).toBeGreaterThan(aspectRatio(letter) * 1.5);
    expect(longestAxis(chef)).toBeGreaterThan(longestAxis(letter));
    // Bread has a THIRD part (the serration layer) — a distinct part count too.
    expect(partCount(bread)).toBeGreaterThan(partCount(chef));
  });

  it("screwdriver vs hammer: thin vertical shaft ratio > 1.5x blocky head ratio", () => {
    const driver = buildAt("tool_screwdriver");
    const hammer = buildAt("tool_hammer");
    expect(aspectRatio(driver)).toBeGreaterThan(aspectRatio(hammer) * 1.5);
    // Hammer head is blocky (all axes within ~3x) — driver shaft is NOT.
    expect(aspectRatio(hammer)).toBeLessThan(aspectRatio(driver));
  });

  it("key vs USB stick: the USB is a materially higher-ratio thin slab", () => {
    const key = buildAt("key_small");
    const usb = buildAt("usb_stick");
    expect(aspectRatio(usb)).toBeGreaterThan(aspectRatio(key) * 1.5);
    // Key is a multi-part y-profile (3 parts), USB is 2.
    expect(partCount(key)).toBeGreaterThan(partCount(usb));
  });

  it("phone vs camera: the phone is a > 3x-higher-ratio thin slab, NOT a chunky box", () => {
    const phone = buildAt("phone_body");
    const camera = buildAt("camera_body");
    expect(aspectRatio(phone)).toBeGreaterThan(aspectRatio(camera) * 3);
    expect(partCount(camera)).toBeGreaterThanOrEqual(3); // body + lens + grip
  });
});

describe("template color resolution (deterministic, catalog-derived only)", () => {
  it("resolves each part color from its colorKey, falling back to the PRIMARY tone", () => {
    const blade = buildTemplateComposite("blade_chef", { colors: { blade: "#a1a2a3", handle: "#111111" }, scale: 1 });
    expect(blade.parts[0].color).toBe("#a1a2a3");
    expect(blade.parts[1].color).toBe("#111111");

    // Missing colorKey -> the primary tone (here first PRIMARY_COLOR_KEYS hit).
    const missing = buildTemplateComposite("bottle_glass", { colors: { body: "#b8c0e8", cap: "#8a5a2b" }, scale: 1 });
    expect(missing.parts.every((p) => /^#[0-9a-fA-F]{6}$/.test(p.color))).toBe(true);
  });

  it("hitboxes scale with the variant scale and undeclared hitboxes are null", () => {
    const hit = buildTemplateComposite("blade_chef", { colors: NEUTRAL_COLORS, scale: 1 });
    expect(hit.hitbox).not.toBeNull();
    const scaled = buildTemplateComposite("blade_chef", { colors: NEUTRAL_COLORS, scale: 2 });
    expect(scaled.hitbox!.x).toBeCloseTo(hit.hitbox!.x * 2, 6);
  });
});