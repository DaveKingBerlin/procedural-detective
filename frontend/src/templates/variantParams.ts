import type { VariantDescriptor, VariantParamDescriptor } from "../catalog/assetCatalog";
import { MATERIAL_VOCABULARY, PRIMARY_COLOR_KEYS, STATE_VOCABULARY } from "./templateRegistry";

/**
 * Phase 12 Track B — bounded declarative variant application (frontend).
 *
 * Every composite catalog asset MAY declare `variants` (≤3 named presets,
 * validated at catalog load). This module PARSEs/validates a REQUEST-side
 * `variantParams` object ({ color?, material?, scale?, state? }) against the
 * frozen literal vocabularies and the asset's declared allowlists, and
 * resolves the DEFAULT variant used by the scene renderers.
 *
 * SAFETY MODEL (no silent pass-through, no arbitrary tokens):
 *  - unknown parameter keys -> {@link VariantParamError};
 *  - unknown allowlist members (e.g. material "neon.glow") -> error;
 *  - non-hex variant colors -> error;
 *  - scale outside the category-safe bounds [0.5, 2.0] -> error; a scale
 *    INSIDE the category bounds is CLAMPED into the asset's declared
 *    [min, max] (assets without a scale declaration default to [0.5, 2.0]);
 *  - colors merge onto the PRIMARY tone key only (see {@link renderColors});
 *  - material tokens only ever adjust diffuse/emissive hues through the
 *    SMALL application-owned {@link MATERIAL_PALETTE} (no arbitrary colors
 *    derived from strings, no shaders); state tokens are validated and
 *    passed through verbatim (a pure annotation — no color effect).
 *
 * Everything here is PURE (no DOM/engine), fully unit-testable.
 */

/** The four legal variant parameter keys (backend VARIANT_PARAM_KINDS). */
export const VARIANT_PARAM_KINDS: readonly string[] = ["color", "material", "scale", "state"];

/** Category-safe scale bounds (backend SCALE_MIN_BOUND / SCALE_MAX_BOUND). */
export const SCALE_MIN_BOUND = 0.5;
export const SCALE_MAX_BOUND = 2.0;

/** RGB hex grammar for variant color literals. */
const COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;

/**
 * Typed deterministic rejection: variantParams never silently passes an
 * unsupported value through. `code` is a stable machine-readable category.
 */
export class VariantParamError extends Error {
  readonly code: string;
  constructor(message: string, code: string) {
    super(message);
    this.name = "VariantParamError";
    this.code = code;
  }
}

/** The request-side variant parameter surface (all keys optional). */
export interface VariantParamOverrides {
  color?: string;
  material?: string;
  scale?: number;
  state?: string;
}

/** The fully resolved variant application result. */
export interface ResolvedVariantParams {
  /** The variant-merged color map (color override merged on the primary key). */
  colors: Readonly<Record<string, string>>;
  /** The resolved scale multiplier (clamped into the asset's declared range). */
  scale: number;
  /** The resolved material token, or null when the asset does not declare one. */
  material: string | null;
  /** The resolved state token, or null when the asset does not declare one. */
  state: string | null;
}

/** The descriptor surface the resolution functions need (type-only catalog import). */
export interface VariantDescriptorSurface {
  colors: Readonly<Record<string, string>>;
  variants: readonly VariantDescriptor[];
}

/** Pure clamp: `value` clamped into [min, max] (min <= max required). */
export function clampScale(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

/** The primary tone KEY of a colors map (first PRIMARY_COLOR_KEYS hit, else first key). */
function primaryToneKey(colors: Readonly<Record<string, string>>): string | null {
  for (const key of PRIMARY_COLOR_KEYS) {
    if (typeof colors[key] === "string" && colors[key] !== "") return key;
  }
  const first = Object.keys(colors)[0];
  return first !== undefined ? first : null;
}

/**
 * Merge a variant color onto the asset's PRIMARY tone key only (all other
 * color keys stay byte-identical). Deterministic and pure.
 */
export function renderColors(
  colors: Readonly<Record<string, string>>,
  colorHex: string,
): Readonly<Record<string, string>> {
  const merged: Record<string, string> = { ...colors };
  const key = primaryToneKey(colors);
  if (key !== null) merged[key] = colorHex;
  return merged;
}

/**
 * The per-asset scale bounds. The FIRST declared `scale` spec across the
 * named presets is authoritative (the validator guarantees every variant
 * agrees on min/max). Assets without a scale declaration default to the
 * category-safe [0.5, 2.0].
 */
export function variantScaleRangeFor(descriptor: VariantDescriptorSurface): { min: number; max: number; default: number } {
  for (const variant of descriptor.variants) {
    const spec = variant.params.scale;
    if (spec !== undefined && spec !== null && spec.kind === "scale" && spec.minValue !== null && spec.maxValue !== null) {
      return {
        min: Math.max(SCALE_MIN_BOUND, spec.minValue),
        max: Math.min(SCALE_MAX_BOUND, spec.maxValue),
        default: typeof spec.default === "number" ? clampScale(spec.default, SCALE_MIN_BOUND, SCALE_MAX_BOUND) : 1,
      };
    }
  }
  return { min: SCALE_MIN_BOUND, max: SCALE_MAX_BOUND, default: 1 };
}

/** The FIRST declared default for one param kind across the named variants. */
function firstDefault(descriptor: VariantDescriptorSurface, kind: string): VariantParamDescriptor | null {
  for (const variant of descriptor.variants) {
    const spec = variant.params[kind];
    if (spec !== undefined && spec !== null) return spec;
  }
  return null;
}

/** Validate ONE request-side value against the asset's declared allowlist. */
function requireAllowlisted(descriptor: VariantDescriptorSurface, kind: string, token: string, vocabulary: readonly string[]): void {
  if (!vocabulary.includes(token)) {
    throw new VariantParamError(
      `variant ${kind} ${JSON.stringify(token)} is not in the frozen ${kind} vocabulary`,
      "unknown-token",
    );
  }
  const declared = firstDefault(descriptor, kind);
  if (declared !== null && declared.allowlist.length > 0 && !declared.allowlist.includes(token)) {
    throw new VariantParamError(
      `variant ${kind} ${JSON.stringify(token)} is not allowed for this asset (allowlist: ${JSON.stringify(declared.allowlist)})`,
      "not-in-allowlist",
    );
  }
}

/**
 * The DEFAULT variant: the first declared default per param kind, merged onto
 * the catalog colors (the color default replaces the primary tone). Pure.
 */
export function resolveDefaultVariant(descriptor: VariantDescriptorSurface): ResolvedVariantParams {
  const colorSpec = firstDefault(descriptor, "color");
  const colors =
    colorSpec !== null && typeof colorSpec.default === "string" && COLOR_PATTERN.test(colorSpec.default)
      ? renderColors(descriptor.colors, colorSpec.default)
      : { ...descriptor.colors };

  const materialSpec = firstDefault(descriptor, "material");
  const stateSpec = firstDefault(descriptor, "state");
  const scaleRange = variantScaleRangeFor(descriptor);

  return {
    colors,
    scale: scaleRange.default,
    material:
      materialSpec !== null && typeof materialSpec.default === "string" && MATERIAL_VOCABULARY.includes(materialSpec.default)
        ? materialSpec.default
        : null,
    state:
      stateSpec !== null && typeof stateSpec.default === "string" && STATE_VOCABULARY.includes(stateSpec.default)
        ? stateSpec.default
        : null,
  };
}

/**
 * Apply a REQUEST-side `variantParams` object on top of the asset's declared
 * defaults. Strict: unknown keys / unknown allowlist members / non-hex colors
 * / scales outside [0.5, 2.0] throw {@link VariantParamError} (never a
 * silent pass-through); a scale inside the category bounds is CLAMPED into
 * the asset's declared [min, max].
 */
export function applyVariantParams(
  descriptor: VariantDescriptorSurface,
  override: VariantParamOverrides | null | undefined,
): ResolvedVariantParams {
  const resolved = resolveDefaultVariant(descriptor);
  if (override === null || override === undefined) return resolved;

  for (const key of Object.keys(override)) {
    if (!VARIANT_PARAM_KINDS.includes(key)) {
      throw new VariantParamError(`unknown variant parameter ${JSON.stringify(key)}`, "unknown-param");
    }
  }

  let colors = resolved.colors;
  let scale = resolved.scale;
  let material = resolved.material;
  let state = resolved.state;

  if (override.color !== undefined) {
    if (typeof override.color !== "string" || !COLOR_PATTERN.test(override.color)) {
      throw new VariantParamError(`variant color ${JSON.stringify(override.color)} is not #RRGGBB`, "bad-color");
    }
    requireAllowlisted(descriptor, "color", override.color, [override.color]);
    colors = renderColors(resolved.colors, override.color);
  }

  if (override.material !== undefined) {
    if (typeof override.material !== "string") {
      throw new VariantParamError("variant material must be a string", "bad-material");
    }
    requireAllowlisted(descriptor, "material", override.material, MATERIAL_VOCABULARY);
    material = override.material;
  }

  if (override.state !== undefined) {
    if (typeof override.state !== "string") {
      throw new VariantParamError("variant state must be a string", "bad-state");
    }
    requireAllowlisted(descriptor, "state", override.state, STATE_VOCABULARY);
    state = override.state;
  }

  if (override.scale !== undefined) {
    if (typeof override.scale !== "number" || !Number.isFinite(override.scale)) {
      throw new VariantParamError(`variant scale must be a finite number (got ${JSON.stringify(override.scale)})`, "bad-scale");
    }
    if (override.scale < SCALE_MIN_BOUND || override.scale > SCALE_MAX_BOUND) {
      throw new VariantParamError(
        `variant scale ${override.scale} must be within [${SCALE_MIN_BOUND}, ${SCALE_MAX_BOUND}]`,
        "scale-out-of-category-bounds",
      );
    }
    const range = variantScaleRangeFor(descriptor);
    scale = clampScale(override.scale, range.min, range.max);
  }

  return { colors, scale, material, state };
}

/* ======================================================================
 * Application-owned material palette.
 *
 * Material tokens NEVER produce arbitrary colors from strings: a token only
 * selects one tiny entry here. `baseTint` is per-channel diffuse multiplier
 * (clamped to [0,1] in the result via applyMaterialTint), `emissiveTint` is
 * the absolute emission (0..1 per channel, applied by the renderer). Every
 * tint is small, deterministic and bounded — no shaders.
 * ==================================================================== */

export interface MaterialTint {
  /** Per-channel diffuse multiplier (applied to the catalog hex). */
  baseTint: readonly [number, number, number];
  /** Absolute RGB emissive accent in [0,1] per channel. */
  emissiveTint: readonly [number, number, number];
}

export const MATERIAL_PALETTE: Readonly<Record<string, MaterialTint>> = {
  "wood.dark": { baseTint: [0.82, 0.68, 0.5], emissiveTint: [0.012, 0.009, 0.005] },
  "wood.light": { baseTint: [0.95, 0.82, 0.62], emissiveTint: [0.013, 0.01, 0.006] },
  "metal.brass": { baseTint: [0.98, 0.9, 0.62], emissiveTint: [0.016, 0.012, 0.004] },
  "metal.steel": { baseTint: [0.92, 0.94, 0.98], emissiveTint: [0.008, 0.01, 0.012] },
  plastic: { baseTint: [0.86, 0.88, 0.94], emissiveTint: [0.008, 0.008, 0.01] },
  fabric: { baseTint: [0.8, 0.78, 0.84], emissiveTint: [0.005, 0.005, 0.006] },
  leather: { baseTint: [0.78, 0.6, 0.42], emissiveTint: [0.006, 0.004, 0.002] },
  ceramic: { baseTint: [0.92, 0.94, 0.92], emissiveTint: [0.006, 0.006, 0.006] },
};

/** Palette lookup; null for unknown tokens — treated as "no material tint". */
export function materialTintFor(material: string | null | undefined): MaterialTint | null {
  if (typeof material !== "string") return null;
  return MATERIAL_PALETTE[material] ?? null;
}

/**
 * Apply a base tint to an #RRGGBB diffuse hex: per-channel multiply, round,
 * clamp into [0, 255], re-encode. Always yields a valid bounded #RRGGBB.
 */
export function applyMaterialTint(diffuseHex: string, tint: MaterialTint): string {
  if (!COLOR_PATTERN.test(diffuseHex)) return diffuseHex;
  const channels = [0, 1, 2].map((i) => {
    const component = parseInt(diffuseHex.slice(1 + i * 2, 3 + i * 2), 16);
    const scaled = Math.round(component * tint.baseTint[i]);
    return Math.max(0, Math.min(255, scaled));
  });
  const toHex = (value: number): string => value.toString(16).padStart(2, "0");
  return `#${channels.map(toHex).join("")}`;
}