import { describe, expect, it } from "vitest";
import { getAsset } from "../catalog/assetCatalog";
import { MATERIAL_VOCABULARY, STATE_VOCABULARY } from "./templateRegistry";
import {
  MATERIAL_PALETTE,
  VariantParamError,
  applyMaterialTint,
  applyVariantParams,
  clampScale,
  materialTintFor,
  renderColors,
  resolveDefaultVariant,
  SCALE_MAX_BOUND,
  SCALE_MIN_BOUND,
  variantScaleRangeFor,
  type VariantDescriptorSurface,
  type VariantParamOverrides,
} from "./variantParams";

/**
 * Phase 12 Track B — bounded declarative variant application. Strict
 * rejection (typed errors) + category-safe clamping + the application-owned
 * material palette only — never arbitrary colors from strings.
 */

/** Fixture helper: the real bundled descriptor surface (never undefined). */
function mustGet(id: string): VariantDescriptorSurface {
  const descriptor = getAsset(id);
  if (descriptor === undefined) throw new Error(`fixture asset ${id} missing`);
  return descriptor;
}

const KNIFE = () => mustGet("PROP_KITCHEN_KNIFE_01");
const LAPTOP = () => mustGet("PROP_LAPTOP_01");
const ID_CARD = () => mustGet("PROP_ID_CARD_01"); // no scale declaration

describe("strict variantParams validation — typed VariantParamError, never silent", () => {
  it("rejects an unknown parameter key", () => {
    // Cast through `unknown` — the override surface itself only admits the 4
    // legal keys at the type level, so a hostile payload arrives as unknown.
    expect(() => applyVariantParams(KNIFE(), { frobnicate: "x" } as unknown as VariantParamOverrides)).toThrow(
      VariantParamError,
    );
  });

  it("rejects an unknown allowlist member (material 'neon.glow')", () => {
    expect(() => applyVariantParams(KNIFE(), { material: "neon.glow" })).toThrow(VariantParamError);
  });

  it("rejects a material token that is legal but not allowlisted for the asset", () => {
    // metal.steel / metal.brass are frozen, but 'wood.dark' is NOT in the
    // knife's declared material allowlist -> reject (no silent pass-through).
    expect(() => applyVariantParams(KNIFE(), { material: "wood.dark" })).toThrow(VariantParamError);
  });

  it("rejects a non-hex variant color", () => {
    expect(() => applyVariantParams(LAPTOP(), { color: "silver" })).toThrow(VariantParamError);
    expect(() => applyVariantParams(LAPTOP(), { color: "#12345" })).toThrow(VariantParamError);
  });

  it("rejects a hex color not allowlisted for the asset", () => {
    expect(() => applyVariantParams(LAPTOP(), { color: "#ff0000" })).toThrow(VariantParamError);
  });

  it("rejects a scale outside the category-safe bounds (10.0)", () => {
    expect(() => applyVariantParams(KNIFE(), { scale: 10.0 })).toThrow(VariantParamError);
    expect(() => applyVariantParams(KNIFE(), { scale: -1 })).toThrow(VariantParamError);
    expect(() => applyVariantParams(KNIFE(), { scale: Number.NaN })).toThrow(VariantParamError);
  });

  it("rejects an unknown state token and a non-allowlisted state", () => {
    expect(() => applyVariantParams(KNIFE(), { state: "exploded" })).toThrow(VariantParamError);
    // 'open' is a frozen state token but NOT in the knife's state allowlist.
    expect(() => applyVariantParams(KNIFE(), { state: "open" })).toThrow(VariantParamError);
  });
});

describe("scale clamping — category bounds + the asset's declared range", () => {
  it("clampScale is a pure clamp: within stays, huge values clamp to the max", () => {
    expect(clampScale(1.7, 0.5, 2.0)).toBe(1.7);
    expect(clampScale(200.0, 0.5, 2.0)).toBe(2.0);
    expect(clampScale(0.1, 0.5, 2.0)).toBe(0.5);
    expect(SCALE_MIN_BOUND).toBe(0.5);
    expect(SCALE_MAX_BOUND).toBe(2.0);
  });

  it("a scale INSIDE [0.5, 2.0] is clamped into the asset's DECLARED range", () => {
    // Knife scale allowlist = [0.9, 1.1]; 1.2 is category-safe -> clamps to 1.1.
    const big = applyVariantParams(KNIFE(), { scale: 1.2 });
    expect(big.scale).toBeCloseTo(1.1, 6);
    // 0.8 clamps up to the declared min 0.9.
    const small = applyVariantParams(KNIFE(), { scale: 0.8 });
    expect(small.scale).toBeCloseTo(0.9, 6);
    // Within-range requests pass through untouched.
    const mid = applyVariantParams(KNIFE(), { scale: 1.0 });
    expect(mid.scale).toBe(1.0);
  });

  it("assets WITHOUT a scale declaration default to [0.5, 2.0] (default 1.0)", () => {
    expect(variantScaleRangeFor(ID_CARD())).toEqual({ min: 0.5, max: 2.0, default: 1 });
    const applied = applyVariantParams(ID_CARD(), { scale: 1.7 });
    expect(applied.scale).toBe(1.7);
    const clamped = applyVariantParams(ID_CARD(), { scale: 1.9 });
    expect(clamped.scale).toBeCloseTo(1.9, 6);
  });
});

describe("renderColors — variant color merges on the PRIMARY tone key only", () => {
  it("merges the variant color onto the asset's primary tone and leaves the rest intact", () => {
    const merged = renderColors({ blade: "#c8ccd4", handle: "#5a3b22" }, "#112233");
    expect(merged).toEqual({ blade: "#112233", handle: "#5a3b22" });
  });

  it("applies the allowlisted color override through applyVariantParams (laptop silver)", () => {
    const silver = applyVariantParams(LAPTOP(), { color: "#c8ccd4" });
    // Laptop primary key = 'base' (PRIMARY_COLOR_KEYS ['...','base','...']).
    expect(silver.colors.base).toBe("#c8ccd4");
    expect(silver.colors.lid).toBe("#20242e");
  });
});

describe("default variant resolution (registry/scene default render)", () => {
  it("resolves the knife's declared defaults (material/state/scale)", () => {
    const kn = KNIFE();
    const resolved = resolveDefaultVariant(kn);
    expect(resolved.material).toBe("metal.steel");
    expect(resolved.state).toBe("clean");
    expect(resolved.scale).toBe(1.0);
    // No color param on the knife -> colors untouched.
    expect(resolved.colors).toEqual({ blade: "#c8ccd4", handle: "#5a3b22" });
  });

  it("merges the laptop's default color on its primary tone (byte-stable default)", () => {
    const resolved = resolveDefaultVariant(LAPTOP());
    expect(resolved.colors.base).toBe("#30343e");
    expect(resolved.state).toBe("open");
    expect(resolved.material).toBeNull(); // laptop declares no material param
  });

  it("applying NO overrides equals the default variant (idempotent)", () => {
    const kn = KNIFE();
    expect(applyVariantParams(kn, null)).toEqual(resolveDefaultVariant(kn));
    expect(applyVariantParams(kn, {})).toEqual(resolveDefaultVariant(kn));
  });
});

describe("state pass-through + material spell-check (frozen vocabularies)", () => {
  it("a validated state token passes through verbatim (no color effect)", () => {
    const damaged = applyVariantParams(KNIFE(), { state: "damaged" });
    expect(damaged.state).toBe("damaged");
    expect(damaged.material).toBe("metal.steel"); // defaults untouched
  });

  it("every palette key is a frozen material token and vice versa", () => {
    for (const token of MATERIAL_VOCABULARY) {
      expect(MATERIAL_PALETTE[token], `${token} has a palette entry`).toBeDefined();
    }
    for (const key of Object.keys(MATERIAL_PALETTE)) {
      expect(MATERIAL_VOCABULARY).toContain(key);
    }
    expect(STATE_VOCABULARY).toContain("open");
  });
});

describe("material palette — bounded, deterministic, no arbitrary strings", () => {
  it("materialTintFor returns a 2x3 bounded tint for known tokens and null otherwise", () => {
    const steel = materialTintFor("metal.steel");
    expect(steel).not.toBeNull();
    expect(steel!.baseTint).toHaveLength(3);
    expect(steel!.emissiveTint).toHaveLength(3);
    for (const value of [...steel!.baseTint, ...steel!.emissiveTint]) {
      expect(Number.isFinite(value)).toBe(true);
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(1);
    }
    expect(materialTintFor(null)).toBeNull();
    expect(materialTintFor("neon.glow")).toBeNull();
    expect(materialTintFor(undefined)).toBeNull();
  });

  it("applyMaterialTint multiplies the diffuse channels, clamps and re-encodes #RRGGBB", () => {
    const tint = materialTintFor("metal.steel")!;
    const out = applyMaterialTint("#c8ccd4", tint);
    expect(out).toMatch(/^#[0-9a-fA-F]{6}$/);
// 0xc8 * 0.92 = 183.5 -> 184 (0xb8); every baseTint is < 1, so a diffuse
    // channel never exceeds its source channel.
    const channels = [parseInt(out.slice(1, 3), 16), parseInt(out.slice(3, 5), 16), parseInt(out.slice(5, 7), 16)];
    const source = [0xc8, 0xcc, 0xd4];
    for (let i = 0; i < 3; i++) {
      expect(channels[i]).toBeGreaterThanOrEqual(0);
      expect(channels[i]).toBeLessThanOrEqual(source[i]);
      expect(channels[i]).toBeLessThanOrEqual(255);
    }
    // Deterministic + bounded for every palette entry against a mid-gray.
    for (const entry of Object.values(MATERIAL_PALETTE)) {
      const encoded = applyMaterialTint("#808080", entry);
      expect(encoded).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });

  it("handles garbage input gracefully (returns the input unchanged)", () => {
    const tint = materialTintFor("plastic")!;
    expect(applyMaterialTint("not-a-color", tint)).toBe("not-a-color");
  });
});