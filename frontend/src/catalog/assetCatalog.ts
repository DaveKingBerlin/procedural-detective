import rawCatalog from "../../../assets/catalog/catalog.json";
import { MATERIAL_VOCABULARY, STATE_VOCABULARY, TEMPLATE_VOCABULARY } from "../templates/templateRegistry";

/**
 * Phase 10 Track B — typed Asset Oracle catalog client (frontend side).
 *
 * Consumes the SAME frozen v1 manifest as the backend Asset Oracle resolver
 * (`assets/catalog/catalog.json` — the SINGLE source of truth for asset
 * identity and safe render metadata). The manifest is application-owned
 * static data BUNDLED into the frontend at build time: there is NO runtime
 * fetch, NO network, NO arbitrary URL/path resolution. A catalog update is a
 * versioned manifest change shipped with the app, exactly like the backend.
 *
 * FRONTEND/BACKEND BOUNDARY:
 *  - The BACKEND resolver turns semantic requests into exact logical
 *    `assetId`s (exact -> canonical -> alias -> semantic -> fallback; see
 *    backend/app/assets/resolver.py) and publishes those ids in the
 *    player-safe WorldGraph DTO. Aliases/canonical names are a BACKEND-only
 *    concern.
 *  - The FRONTEND accepts EXACT assetIds ONLY. {@link getAsset}, {@link
 *    hasAsset} and {@link resolveAsset} all match the raw assetId string
 *    verbatim — the frontend NEVER resolves by free text, canonical name or
 *    alias. An id that is only an alias ("apartment.laptop.basic") is NOT a
 *    catalog id here and resolves to the neutral fallback. A hostile id can
 *    therefore never reach a descriptor the backend did not choose.
 *
 * VALIDATION / SAFETY:
 *  - {@link validateCatalog} is a STRICT, deterministic validator (modeled on
 *    the backend catalog checks at backend/app/assets/catalog.py): duplicate
 *    assetIds, unknown keys, malformed dimensions/colors, unsafe strings
 *    (control characters, URL schemes, path separators/traversal, absolute
 *    paths, oversized strings), out-of-vocabulary render/category kinds and
 *    unknown composite kinds (which would ghost-render an invisible object)
 *    are all REJECTED with a sorted issue list.
 *  - The bundled manifest is validated ONCE at module load. A corrupted
 *    catalog must never silently break rendering: the module stays
 *    non-throwing, {@link isCatalogHealthy()} reports the failure and {@link
 *    getCatalogError()} carries the deterministic message so callers can
 *    surface a safe app-level error (every asset degrades to the neutral,
 *    non-interactable fallback and the scene page shows a notice).
 *
 * SECURITY: the catalog carries ONLY declarative data (ids, vocabularies,
 * hex colors, primitive geometry). No URL, no filesystem path, no
 * script/shader/handler field is ever read from it; the registry turns
 * descriptors into local Babylon primitives only.
 */

/** The render kinds the frontend can turn into local primitives/composites. */
export type CatalogRenderKind = "box" | "cylinder" | "sphere" | "flat" | "composite";

/** The four bounded variant parameter kinds (backend VARIANT_PARAM_KINDS). */
export type VariantParamKind = "color" | "material" | "scale" | "state";

/** One typed variant parameter spec (mirrors backend VariantParamSpec). */
export interface VariantParamDescriptor {
  kind: VariantParamKind;
  /** Hex colors (color) / frozen material or state tokens / empty (scale). */
  allowlist: readonly string[];
  /** The declared default (hex / token / scale float). */
  default: string | number | null;
  /** Scale bounds only (always null for color/material/state). */
  minValue: number | null;
  maxValue: number | null;
}

/** One named bounded declarative variant (mirrors backend VariantSpec). */
export interface VariantDescriptor {
  name: string;
  params: Readonly<Record<string, VariantParamDescriptor>>;
}

/** Bounding-box dimensions in world meters (all positive and finite). */
export interface CatalogDimensions {
  x: number;
  y: number;
  z: number;
}

/**
 * One typed application-owned catalog asset — mirrors the manifest's frozen
 * descriptor contract 1:1 (all arrays defensively copied by the validator).
 */
export interface CatalogAssetDescriptor {
  assetId: string;
  version: number;
  canonicalName: string;
  aliases: readonly string[];
  category: string;
  subtype: string;
  tags: readonly string[];
  renderKind: CatalogRenderKind;
  compositeKind: string | null;
  /** Phase 12: frozen logical template for composite assets (null otherwise). */
  templateId: string | null;
  dimensions: CatalogDimensions;
  colors: Readonly<Record<string, string>>;
  label: string;
  interactable: boolean;
  supportedInteractions: readonly string[];
  evidenceCapabilities: readonly string[];
  allowedAnchors: readonly string[];
  /** Phase 12: bounded declarative variant presets (empty when none). */
  variants: readonly VariantDescriptor[];
}

/** The fully validated manifest document. */
export interface CatalogDocument {
  catalogVersion: number;
  fallbackAssetId: string;
  /** Manifest order is preserved (deterministic identity/rendering). */
  assets: readonly CatalogAssetDescriptor[];
}

/* ======================================================================
 * Validation
 * ==================================================================== */

/**
 * Raised by {@link validateCatalog} when the raw manifest fails the strict
 * deterministic checks. `issues` is the sorted, de-duplicated issue list.
 */
export class CatalogValidationError extends Error {
  readonly issues: readonly string[];
  constructor(issues: readonly string[]) {
    const sorted = [...new Set(issues)].sort();
    const count = sorted.length;
    super(`asset catalog failed validation (${count} issue${count === 1 ? "" : "s"}): ${sorted.join("; ")}`);
    this.name = "CatalogValidationError";
    this.issues = sorted;
  }
}

/** assetId grammar — mirrors backend/app/generation/safety._ASSET_ID_PATTERN. */
const ASSET_ID_PATTERN = /^[A-Z][A-Z0-9_]+$/;
/** RGB hex color grammar, e.g. "#c8ccd4". */
const COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;
/** Windows drive-letter absolute path prefix, e.g. "C:\" / "C:/". */
const DRIVE_ABSOLUTE_RE = /^[A-Za-z]:[\\/]/;

/** The EXACT documented key set of every asset descriptor (schema contract). */
export const DOCUMENTED_ASSET_KEYS: readonly string[] = [
  "assetId",
  "version",
  "canonicalName",
  "aliases",
  "category",
  "subtype",
  "tags",
  "renderKind",
  "compositeKind",
  "templateId",
  "dimensions",
  "colors",
  "label",
  "interactable",
  "supportedInteractions",
  "evidenceCapabilities",
  "allowedAnchors",
  "variants",
];

/**
 * Phase 12 keys that MAY be absent (the Phase 10/11 non-composite entries keep
 * their byte-stable shape and simply do not declare them). Every COMPOSITE
 * entry MUST declare `templateId` (and MAY declare `variants`).
 */
export const OPTIONAL_ASSET_KEYS: readonly string[] = ["templateId", "variants"];

/** Phase 12 §Variant — max named variant presets per asset. */
export const MAX_VARIANTS_PER_ASSET = 3;
/** Phase 12 §Variant — max allowlist entries per variant param. */
export const MAX_VARIANT_ALLOWLIST = 16;
/** Phase 12 §Variant — category-safe scale bounds. */
export const SCALE_MIN_BOUND = 0.5;
export const SCALE_MAX_BOUND = 2.0;
/** Phase 12 §Variant — legal variant parameter keys. */
export const VARIANT_PARAM_KINDS: readonly string[] = ["color", "material", "scale", "state"];
/** Phase 12 §Variant — variant-name grammar ^[a-z0-9_]{1,24}$. */
export const VARIANT_NAME_PATTERN = /^[a-z0-9_]{1,24}$/;

/**
 * The frozen Phase 12 literal vocabularies, RE-EXPORTED from the template
 * registry (the single frontend copy, pinned to backend/app/assets/catalog.py
 * TEMPLATE_VOCABULARY / MATERIAL_VOCABULARY / STATE_VOCABULARY). The Phase 12
 * lockstep test asserts every templateId / material / state token the real
 * manifest uses is a member, so the frontend and the backend can never drift.
 */
export { MATERIAL_VOCABULARY, STATE_VOCABULARY, TEMPLATE_VOCABULARY } from "../templates/templateRegistry";

/** Documented category vocabulary (shared contract with the backend). */
export const CATEGORY_VOCABULARY: readonly string[] = [
  "evidence",
  "electronics",
  "furniture",
  "structural",
  "character",
  "decor",
  "utility",
];

/** The primitive render kinds the frontend renderer understands. */
export const RENDER_KIND_VOCABULARY: readonly CatalogRenderKind[] = [
  "box",
  "cylinder",
  "sphere",
  "flat",
  "composite",
];

/**
 * The composite kinds this frontend CAN safely build. Any other compositeKind
 * would yield an EMPTY part list and a ghost (invisible) object, so the
 * validator rejects them at load instead of silently breaking rendering.
 */
export const SUPPORTED_COMPOSITE_KINDS: readonly string[] = [
  "kitchen_knife",
  "letter_opener",
  "scissors",
  "laptop",
  "victim",
  "table",
];

const FORBIDDEN_URL_TOKENS: readonly string[] = ["http://", "https://", "data:", "file:", "javascript:"];

/** Manifests are declarative data; strings longer than this are suspect. */
const MAX_CATALOG_STRING_LENGTH = 120;

/**
 * DEF-060 — array-size bounds matching the backend contract. An oversized
 * vocabulary/alias list is a manifest-red flag and is REJECTED with a
 * deterministic load issue instead of being silently accepted.
 */
export const MAX_TAGS = 16;
export const MAX_ALIASES = 16;
export const MAX_SUPPORTED_INTERACTIONS = 8;
export const MAX_EVIDENCE_CAPABILITIES = 8;
export const MAX_ALLOWED_ANCHORS = 16;
export const MAX_COLORS = 16;

/** Deterministic array-length bound check (no-op when not an array). */
function arrayTooLong(value: unknown, max: number, where: string, issues: string[]): void {
  if (Array.isArray(value) && value.length > max) {
    issues.push(`${where}: exceeds the maximum of ${max} entries`);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/** Deterministic string-safety scans (mirrors the backend catalog checks). */
function stringSafetyIssues(value: string, where: string): string[] {
  const issues: string[] = [];
  if (value.length > MAX_CATALOG_STRING_LENGTH) {
    issues.push(`${where}: string exceeds ${MAX_CATALOG_STRING_LENGTH} characters`);
  }
  for (let i = 0; i < value.length; i++) {
    if (value.charCodeAt(i) < 0x20) {
      issues.push(`${where}: contains control characters`);
      break;
    }
  }
  const lower = value.toLowerCase();
  for (const token of FORBIDDEN_URL_TOKENS) {
    if (lower.includes(token)) {
      issues.push(`${where}: contains a forbidden URL scheme '${token}'`);
    }
  }
  if (value.includes("/") || value.includes("\\")) {
    issues.push(`${where}: contains a path separator`);
  }
  if (value.includes("..")) {
    issues.push(`${where}: contains path traversal '..'`);
  }
  if (value.startsWith("/") || value.startsWith("\\") || DRIVE_ABSOLUTE_RE.test(value)) {
    issues.push(`${where}: is an absolute path`);
  }
  return issues;
}

function stringListIssues(value: unknown, where: string): string[] {
  const issues: string[] = [];
  if (!Array.isArray(value)) {
    issues.push(`${where}: must be an array of non-empty strings`);
    return issues;
  }
  value.forEach((entry, index) => {
    if (!isNonEmptyString(entry)) {
      issues.push(`${where}[${index}]: must be a non-empty string`);
    } else {
      issues.push(...stringSafetyIssues(entry, `${where}[${index}]`));
    }
  });
  return issues;
}

function dimensionsFrom(raw: unknown, where: string, issues: string[]): CatalogDimensions | null {
  if (!isRecord(raw)) {
    issues.push(`${where}: dimensions must be an object {x, y, z}`);
    return null;
  }
  const keys = Object.keys(raw);
  if (keys.length !== 3 || !keys.includes("x") || !keys.includes("y") || !keys.includes("z")) {
    issues.push(`${where}: dimensions must have exactly the keys x, y, z`);
    return null;
  }
  const out: CatalogDimensions = { x: 0, y: 0, z: 0 };
  let ok = true;
  for (const axis of ["x", "y", "z"] as const) {
    const value = raw[axis];
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value >= 100) {
      issues.push(`${where}.${axis}: must be a finite number > 0 and < 100`);
      ok = false;
      continue;
    }
    out[axis] = value;
  }
  return ok ? out : null;
}

/**
 * Deterministic issue list for ONE non-null variant-parameter spec object
 * (mirrors backend _variant_param_spec_issues). `kind` is already known legal.
 */
function variantParamSpecIssues(kind: VariantParamKind, spec: unknown, where: string, issues: string[]): void {
  if (!isRecord(spec)) {
    issues.push(`${where}: variant parameter ${JSON.stringify(kind)} must be an object or null`);
    return;
  }
  if (kind === "scale") {
    const specKeys = Object.keys(spec);
    if (
      specKeys.length !== 3 ||
      !specKeys.includes("min") ||
      !specKeys.includes("max") ||
      !specKeys.includes("default")
    ) {
      issues.push(`${where}: scale requires exactly the keys min, max, default`);
      return;
    }
    const numbers: Record<string, number> = {};
    for (const key of ["min", "max", "default"]) {
      const value = spec[key];
      if (typeof value !== "number" || !Number.isFinite(value)) {
        issues.push(`${where}.${key}: must be a number`);
        continue;
      }
      if (value < SCALE_MIN_BOUND || value > SCALE_MAX_BOUND) {
        issues.push(
          `${where}.${key}: must be within [${SCALE_MIN_BOUND}, ${SCALE_MAX_BOUND}] (got ${value})`,
        );
        continue;
      }
      numbers[key] = value;
    }
    if (numbers.min !== undefined && numbers.max !== undefined && numbers.default !== undefined) {
      if (numbers.min > numbers.max) {
        issues.push(`${where}: min must be <= max`);
      }
      if (!(numbers.min <= numbers.default && numbers.default <= numbers.max)) {
        issues.push(`${where}: default must be within [min, max]`);
      }
    }
    return;
  }

  const specKeys = Object.keys(spec);
  if (specKeys.length !== 2 || !specKeys.includes("allowlist") || !specKeys.includes("default")) {
    issues.push(`${where}: ${JSON.stringify(kind)} requires exactly the keys allowlist, default`);
    return;
  }

  const allowlist = spec.allowlist;
  const safeAllowlist: string[] = [];
  if (!Array.isArray(allowlist) || allowlist.length === 0) {
    issues.push(`${where}.allowlist: must be a non-empty array`);
  } else if (allowlist.length > MAX_VARIANT_ALLOWLIST) {
    issues.push(`${where}.allowlist: exceeds the maximum of ${MAX_VARIANT_ALLOWLIST} entries`);
  } else {
    allowlist.forEach((entry, index) => {
      const entryWhere = `${where}.allowlist[${index}]`;
      if (!isNonEmptyString(entry)) {
        issues.push(`${entryWhere}: must be a non-empty string`);
        return;
      }
      issues.push(...stringSafetyIssues(entry, entryWhere));
      if (kind === "color") {
        if (!COLOR_PATTERN.test(entry)) {
          issues.push(`${entryWhere}: ${JSON.stringify(entry)} is not a #RRGGBB color`);
        } else {
          safeAllowlist.push(entry);
        }
      } else if (kind === "material") {
        if (!(MATERIAL_VOCABULARY as readonly string[]).includes(entry)) {
          issues.push(`${entryWhere}: ${JSON.stringify(entry)} is not in MATERIAL_VOCABULARY`);
        } else {
          safeAllowlist.push(entry);
        }
      } else {
        if (!(STATE_VOCABULARY as readonly string[]).includes(entry)) {
          issues.push(`${entryWhere}: ${JSON.stringify(entry)} is not in STATE_VOCABULARY`);
        } else {
          safeAllowlist.push(entry);
        }
      }
    });
  }

  const def = spec.default;
  if (!isNonEmptyString(def)) {
    issues.push(`${where}.default: must be a non-empty string`);
    return;
  }
  issues.push(...stringSafetyIssues(def, `${where}.default`));
  if (kind === "color") {
    if (!COLOR_PATTERN.test(def)) issues.push(`${where}.default: ${JSON.stringify(def)} is not a #RRGGBB color`);
  } else if (kind === "material") {
    if (!(MATERIAL_VOCABULARY as readonly string[]).includes(def)) {
      issues.push(`${where}.default: ${JSON.stringify(def)} is not in MATERIAL_VOCABULARY`);
    }
  } else if (!(STATE_VOCABULARY as readonly string[]).includes(def)) {
    issues.push(`${where}.default: ${JSON.stringify(def)} is not in STATE_VOCABULARY`);
  }
  if (Array.isArray(allowlist) && allowlist.length > 0 && !allowlist.includes(def)) {
    issues.push(`${where}.default: must be present in the allowlist`);
  }
}

/**
 * Canonical per-param-key consistency signature (strings; excludes defaults).
 * The typed numbers are normalized to strings so 1.0 and 1.0 stay equal.
 */
function variantParamSignature(kind: VariantParamKind, spec: unknown): string | null {
  if (!isRecord(spec)) return null;
  try {
    if (kind === "scale") {
      return `scale:${JSON.stringify(spec.min)}:${JSON.stringify(spec.max)}`;
    }
    const allowlist = spec.allowlist;
    if (!Array.isArray(allowlist)) return null;
    return `${kind}:${JSON.stringify([...allowlist].sort())}`;
  } catch {
    return null;
  }
}

/**
 * Phase 12 §Variant — validate an asset's `variants` value (DEF-060 style
 * bounded declarative presets; mirrors backend _variant_issues). Returns the
 * typed descriptors ONLY when valid (empty when absent).
 */
function variantsFrom(value: unknown, where: string, issues: string[]): VariantDescriptor[] {
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value)) {
    issues.push(`${where}.variants: must be an array`);
    return [];
  }
  const descriptors: VariantDescriptor[] = [];
  if (value.length > MAX_VARIANTS_PER_ASSET) {
    issues.push(`${where}.variants: exceeds the maximum of ${MAX_VARIANTS_PER_ASSET} variants`);
  }
  const signatures: Record<string, string> = {};
  value.forEach((variant, variantIndex) => {
    const vwhere = `${where}.variants[${variantIndex}]`;
    if (!isRecord(variant)) {
      issues.push(`${vwhere}: variant must be an object {name, params}`);
      return;
    }
    const vKeys = Object.keys(variant).sort();
    const missing = ["name", "params"].filter((key) => !vKeys.includes(key)).sort();
    const extra = vKeys.filter((key) => key !== "name" && key !== "params");
    if (missing.length > 0) issues.push(`${vwhere}: missing required keys ${JSON.stringify(missing)}`);
    if (extra.length > 0) issues.push(`${vwhere}: unknown keys ${JSON.stringify(extra)}`);

    const name = variant.name;
    if (!isNonEmptyString(name) || !VARIANT_NAME_PATTERN.test(name)) {
      issues.push(`${vwhere}: variant name ${JSON.stringify(name)} must match ^[a-z0-9_]{1,24}$`);
    }

    const paramsRaw = variant.params;
    if (!isRecord(paramsRaw)) {
      issues.push(`${vwhere}.params: must be an object`);
      return;
    }
    const params: Record<string, VariantParamDescriptor> = {};
    for (const [paramKey, spec] of Object.entries(paramsRaw)) {
      const pwhere = `${vwhere}.params.${paramKey}`;
      if (!(VARIANT_PARAM_KINDS as readonly string[]).includes(paramKey)) {
        issues.push(`${pwhere}: unknown variant parameter ${JSON.stringify(paramKey)}`);
        continue;
      }
      const kind = paramKey as VariantParamKind;
      if (spec === null) continue;
      variantParamSpecIssues(kind, spec, pwhere, issues);
      const signature = variantParamSignature(kind, spec);
      if (signature === null) continue;
      if (signatures[paramKey] !== undefined && signatures[paramKey] !== signature) {
        issues.push(
          `${pwhere}: conflicts with an earlier variant's ${JSON.stringify(paramKey)} spec ` +
            "(allowlist/scale bounds must agree)",
        );
      } else {
        signatures[paramKey] = signature;
      }
      // Typed descriptor (built from structurally-valid specs only).
      if (!isRecord(spec)) continue;
      if (kind === "scale") {
        const items: Record<string, unknown> = spec;
        params[paramKey] = {
          kind,
          allowlist: [],
          default: typeof items.default === "number" ? items.default : null,
          minValue: typeof items.min === "number" ? items.min : null,
          maxValue: typeof items.max === "number" ? items.max : null,
        };
      } else {
        const blank: Record<string, unknown> = spec;
        params[paramKey] = {
          kind,
          allowlist: Array.isArray(blank.allowlist) ? [...(blank.allowlist as string[])] : [],
          default: typeof blank.default === "string" ? blank.default : null,
          minValue: null,
          maxValue: null,
        };
      }
    }
    descriptors.push({ name: isNonEmptyString(name) ? name : "", params });
  });
  return descriptors;
}

/**
 * Deterministic issue list for ONE asset entry; descriptor when fully valid.
 */
function assetDescriptorFrom(item: unknown, index: number, issues: string[]): CatalogAssetDescriptor | null {
  const where = `assets[${index}]`;
  if (!isRecord(item)) {
    issues.push(`${where}: asset entry must be a JSON object`);
    return null;
  }

  const keys = Object.keys(item).sort();
  const missing = DOCUMENTED_ASSET_KEYS.filter((key) => !(key in item) && !OPTIONAL_ASSET_KEYS.includes(key)).sort();
  const extra = keys.filter((key) => !DOCUMENTED_ASSET_KEYS.includes(key));
  if (missing.length > 0) {
    issues.push(`${where}: missing required keys ${JSON.stringify(missing)}`);
  }
  if (extra.length > 0) {
    issues.push(`${where}: unknown keys ${JSON.stringify(extra)}`);
  }

  // assetId
  const assetId = item.assetId;
  if (!isNonEmptyString(assetId) || !ASSET_ID_PATTERN.test(assetId)) {
    issues.push(`${where}: assetId ${JSON.stringify(assetId)} must match ^[A-Z][A-Z0-9_]+$`);
  }

  // version
  const version = item.version;
  if (typeof version !== "number" || !Number.isInteger(version) || version < 1) {
    issues.push(`${where}: version must be a positive integer`);
  }

  // canonicalName / subtype / label (non-empty + safe + no URL/path smuggling)
  const canonicalName = item.canonicalName;
  if (!isNonEmptyString(canonicalName)) {
    issues.push(`${where}: canonicalName must be a non-empty string`);
  } else {
    issues.push(...stringSafetyIssues(canonicalName, `${where}.canonicalName`));
  }
  const subtype = item.subtype;
  if (!isNonEmptyString(subtype)) {
    issues.push(`${where}: subtype must be a non-empty string`);
  } else {
    issues.push(...stringSafetyIssues(subtype, `${where}.subtype`));
  }
  const label = item.label;
  if (!isNonEmptyString(label)) {
    issues.push(`${where}: label must be a non-empty string`);
  } else {
    issues.push(...stringSafetyIssues(label, `${where}.label`));
  }

  // aliases (DEF-060: bounded like the backend — oversized lists rejected)
  issues.push(...stringListIssues(item.aliases, `${where}.aliases`));
  arrayTooLong(item.aliases, MAX_ALIASES, `${where}.aliases`, issues);

  // category (documented vocabulary; same contract as the backend)
  const category = item.category;
  if (!isNonEmptyString(category) || !CATEGORY_VOCABULARY.includes(category)) {
    issues.push(`${where}: category must be one of ${JSON.stringify(CATEGORY_VOCABULARY)}`);
  }

  // tags (DEF-060)
  issues.push(...stringListIssues(item.tags, `${where}.tags`));
  arrayTooLong(item.tags, MAX_TAGS, `${where}.tags`, issues);

  // renderKind + compositeKind (composite kind also must be BUILDABLE here)
  const renderKind = item.renderKind;
  if (!isNonEmptyString(renderKind) || !(RENDER_KIND_VOCABULARY as readonly string[]).includes(renderKind)) {
    issues.push(`${where}: renderKind must be one of ${JSON.stringify(RENDER_KIND_VOCABULARY)}`);
  }
  const compositeKindRaw = item.compositeKind;
  let compositeKind: string | null = null;
  if (renderKind === "composite") {
    // Phase 12: the composite builder is identified by `templateId` (frozen
    // TEMPLATE_VOCABULARY). `compositeKind` is null for template-only
    // composites; the six Phase 10/11 legacy builders keep their value.
    if (compositeKindRaw !== null && compositeKindRaw !== undefined) {
      if (!isNonEmptyString(compositeKindRaw)) {
        issues.push(`${where}: compositeKind must be a string or null`);
      } else {
        issues.push(...stringSafetyIssues(compositeKindRaw, `${where}.compositeKind`));
        if (!SUPPORTED_COMPOSITE_KINDS.includes(compositeKindRaw)) {
          issues.push(
            `${where}: compositeKind ${JSON.stringify(compositeKindRaw)} has no safe frontend renderer; ` +
              `supported kinds: ${JSON.stringify(SUPPORTED_COMPOSITE_KINDS)}`,
          );
        } else {
          compositeKind = compositeKindRaw;
        }
      }
    }
  } else if (compositeKindRaw !== null && compositeKindRaw !== undefined) {
    issues.push(`${where}: compositeKind must be null unless renderKind is 'composite'`);
  }

  // Phase 12: templateId — required (frozen vocabulary) for composites, null otherwise.
  const templateIdRaw = item.templateId;
  let templateId: string | null = null;
  if (renderKind === "composite") {
    if (!isNonEmptyString(templateIdRaw)) {
      issues.push(`${where}: renderKind 'composite' requires a templateId from TEMPLATE_VOCABULARY`);
    } else {
      issues.push(...stringSafetyIssues(templateIdRaw, `${where}.templateId`));
      if (!(TEMPLATE_VOCABULARY as readonly string[]).includes(templateIdRaw)) {
        issues.push(`${where}: templateId ${JSON.stringify(templateIdRaw)} is not in the frozen TEMPLATE_VOCABULARY`);
      } else {
        templateId = templateIdRaw;
      }
    }
  } else if (templateIdRaw !== null && templateIdRaw !== undefined) {
    issues.push(`${where}: templateId must be null unless renderKind is 'composite'`);
  }

  // Phase 12 §Variant — bounded declarative variant presets.
  const variants = variantsFrom(item.variants, where, issues);

  // dimensions
  const dimensions = dimensionsFrom(item.dimensions, `${where}.dimensions`, issues);

  // colors: non-empty {name: #rrggbb} map (values are the ONLY render colors)
  const colorsRaw = item.colors;
  let colors: Record<string, string> | null = null;
  if (!isRecord(colorsRaw)) {
    issues.push(`${where}: colors must be a non-empty object`);
  } else if (Object.keys(colorsRaw).length === 0) {
    issues.push(`${where}: colors must not be empty`);
  } else {
    const map: Record<string, string> = {};
    let colorsOk = true;
    // DEF-060: bounded color map (<= MAX_COLORS entries) like the backend.
    if (Object.keys(colorsRaw).length > MAX_COLORS) {
      issues.push(`${where}.colors: exceeds the maximum of ${MAX_COLORS} entries`);
      colorsOk = false;
    }
    for (const [name, value] of Object.entries(colorsRaw)) {
      if (!isNonEmptyString(name)) {
        issues.push(`${where}: color names must be non-empty strings`);
        colorsOk = false;
        continue;
      }
      issues.push(...stringSafetyIssues(name, `${where} color ${JSON.stringify(name)}`));
      if (!isNonEmptyString(value) || !COLOR_PATTERN.test(value)) {
        issues.push(`${where} color ${JSON.stringify(name)}: value ${JSON.stringify(value)} is not #RRGGBB`);
        colorsOk = false;
        continue;
      }
      map[name] = value;
    }
    if (colorsOk) colors = map;
  }

  // interactable
  const interactable = item.interactable;
  if (typeof interactable !== "boolean") {
    issues.push(`${where}: interactable must be a boolean`);
  }

  // interaction/capability/anchor vocabularies are arrays of non-empty safe
  // strings, bounded like the backend contract (DEF-060).
  issues.push(...stringListIssues(item.supportedInteractions, `${where}.supportedInteractions`));
  arrayTooLong(item.supportedInteractions, MAX_SUPPORTED_INTERACTIONS, `${where}.supportedInteractions`, issues);
  issues.push(...stringListIssues(item.evidenceCapabilities, `${where}.evidenceCapabilities`));
  arrayTooLong(item.evidenceCapabilities, MAX_EVIDENCE_CAPABILITIES, `${where}.evidenceCapabilities`, issues);
  issues.push(...stringListIssues(item.allowedAnchors, `${where}.allowedAnchors`));
  arrayTooLong(item.allowedAnchors, MAX_ALLOWED_ANCHORS, `${where}.allowedAnchors`, issues);

  // Abort this entry unless every render-critical field is present.
  if (
    !ASSET_ID_PATTERN.test(isNonEmptyString(assetId) ? assetId : "") ||
    typeof version !== "number" ||
    !isNonEmptyString(canonicalName) ||
    !isNonEmptyString(subtype) ||
    !isNonEmptyString(label) ||
    !isNonEmptyString(category) ||
    !Array.isArray(item.aliases) ||
    !Array.isArray(item.tags) ||
    !RENDER_KIND_VOCABULARY.includes(renderKind as CatalogRenderKind) ||
    dimensions === null ||
    colors === null ||
    typeof interactable !== "boolean" ||
    (renderKind === "composite" && templateId === null)
  ) {
    return null;
  }

  return {
    assetId: assetId as string,
    version: version as number,
    canonicalName: canonicalName as string,
    aliases: [...(item.aliases as string[])],
    category: category as string,
    subtype: subtype as string,
    tags: [...(item.tags as string[])],
    renderKind: renderKind as CatalogRenderKind,
    compositeKind,
    templateId,
    dimensions,
    colors: colors as Record<string, string>,
    label: label as string,
    interactable: interactable as boolean,
    supportedInteractions: [...(item.supportedInteractions as string[])],
    evidenceCapabilities: [...(item.evidenceCapabilities as string[])],
    allowedAnchors: [...(item.allowedAnchors as string[])],
    variants,
  };
}

/**
 * STRICT deterministic validator for a raw catalog manifest. Collects every
 * issue, sorts/dedupes them and throws {@link CatalogValidationError} when the
 * manifest is not loadable. A corrupted manifest can never silently reach the
 * renderer — callers gate on {@link isCatalogHealthy()}/{@link
 * getCatalogError()} and the registry degrades to neutral fallbacks.
 */
export function validateCatalog(raw: unknown): CatalogDocument {
  const issues: string[] = [];

  if (!isRecord(raw)) {
    throw new CatalogValidationError(["catalog document must be a JSON object"]);
  }

  let catalogVersion: number | null = null;
  if (typeof raw.catalogVersion !== "number" || !Number.isInteger(raw.catalogVersion) || raw.catalogVersion < 1) {
    issues.push("catalog document: catalogVersion must be a positive integer");
  } else {
    catalogVersion = raw.catalogVersion;
  }

  let fallbackAssetId: string | null = null;
  if (!isNonEmptyString(raw.fallbackAsset)) {
    issues.push("catalog document: fallbackAsset must be a non-empty string");
  } else {
    issues.push(...stringSafetyIssues(raw.fallbackAsset, "catalog document.fallbackAsset"));
    fallbackAssetId = raw.fallbackAsset;
  }

  if (!Array.isArray(raw.assets)) {
    issues.push("catalog document: 'assets' must be an array");
    throw new CatalogValidationError(issues);
  }

  const descriptors: CatalogAssetDescriptor[] = [];
  const seenIds = new Map<string, number>();
  const idClaims: Array<[number, string]> = [];
  // DEF-066 (DEF-058 mirror): the raw category/interactable of every entry
  // WITH a valid assetId pattern, keyed by assetId — the neutral-fallback
  // invariant runs on these RAW values exactly like the backend, so a
  // manifest whose fallback is an interactable, non-utility asset is rejected
  // deterministically (the frontend must never resolve an unknown id to an
  // interactable, render-visible object instead of the neutral placeholder).
  const declaredMeta = new Map<string, { category: unknown; interactable: unknown }>();

  raw.assets.forEach((entry, index) => {
    const descriptor = assetDescriptorFrom(entry, index, issues);
    if (descriptor !== null) {
      descriptors.push(descriptor);
      idClaims.push([index, descriptor.assetId]);
      declaredMeta.set(descriptor.assetId, {
        category: descriptor.category,
        interactable: descriptor.interactable,
      });
    } else if (isRecord(entry) && isNonEmptyString(entry.assetId) && ASSET_ID_PATTERN.test(entry.assetId)) {
      // Even an otherwise-invalid entry still counts for the duplicate check
      // (and for the fallback meta when it declares a usable assetId).
      idClaims.push([index, entry.assetId]);
      declaredMeta.set(entry.assetId, { category: entry.category, interactable: entry.interactable });
    }
  });

  // Cross-asset identity checks (deterministic in manifest order).
  for (const [index, assetId] of idClaims) {
    const first = seenIds.get(assetId);
    if (first !== undefined) {
      issues.push(`duplicate assetId ${JSON.stringify(assetId)}: declared at assets[${first}] and again at assets[${index}]`);
    } else {
      seenIds.set(assetId, index);
    }
  }

  if (fallbackAssetId !== null && !seenIds.has(fallbackAssetId)) {
    issues.push(`catalog document: fallbackAsset ${JSON.stringify(fallbackAssetId)} is not a declared assetId`);
  } else if (fallbackAssetId !== null) {
    // DEF-066 — mirror of the backend DEF-058 neutral-fallback invariant: an
    // unresolved request must end in the EXPLICIT neutral placeholder, never
    // in an interactable, render-visible object. The declared fallback asset
    // must be `category === 'utility'` AND `interactable === false`.
    const meta = declaredMeta.get(fallbackAssetId);
    if (meta !== undefined) {
      if (meta.category !== "utility") {
        issues.push(
          `catalog document: fallbackAsset ${JSON.stringify(fallbackAssetId)} has category ${JSON.stringify(meta.category)}; ` +
            "a neutral fallback must have category 'utility'",
        );
      }
      if (meta.interactable !== false) {
        issues.push(
          `catalog document: fallbackAsset ${JSON.stringify(fallbackAssetId)} is interactable; ` +
            "a neutral fallback must be non-interactable",
        );
      }
    }
  }

  if (issues.length > 0) {
    throw new CatalogValidationError(issues);
  }

  return {
    catalogVersion: catalogVersion as number,
    fallbackAssetId: fallbackAssetId as string,
    assets: descriptors,
  };
}

/* ======================================================================
 * Module-level load + non-throwing health API
 * ==================================================================== */

interface CatalogLoadState {
  ok: boolean;
  error: string | null;
  document: CatalogDocument | null;
}

/** Validate the bundled manifest exactly once. Never throws. */
const LOAD: CatalogLoadState = (() => {
  try {
    return { ok: true, error: null, document: validateCatalog(rawCatalog as unknown) };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, error: message, document: null };
  }
})();

const INDEX: ReadonlyMap<string, CatalogAssetDescriptor> = new Map(
  (LOAD.document?.assets ?? []).map((descriptor) => [descriptor.assetId, descriptor] as const),
);

/** True when the bundled manifest passed strict validation. */
export function isCatalogHealthy(): boolean {
  return LOAD.ok;
}

/** Deterministic validation message; null when the catalog is healthy. */
export function getCatalogError(): string | null {
  return LOAD.error;
}

/** The manifest's catalogVersion (0 when the catalog failed to load). */
export function getCatalogVersion(): number {
  return LOAD.document?.catalogVersion ?? 0;
}

/**
 * EXACT-assetId lookup — the only catalog access the frontend ever performs.
 * A free-text name, canonical name or alias is NEVER resolved here (the
 * backend already resolved semantic requests). Returns undefined when the id
 * is not a catalog id (or the catalog failed to load).
 */
export function getAsset(assetId: string): CatalogAssetDescriptor | undefined {
  return INDEX.get(assetId);
}

/** Exactly the {@link getAsset} predicate for readability. */
export function hasAsset(assetId: string): boolean {
  return INDEX.has(assetId);
}

/** All assetIds in manifest order (empty when the catalog failed to load). */
export function catalogAssetIds(): readonly string[] {
  return LOAD.document ? LOAD.document.assets.map((descriptor) => descriptor.assetId) : [];
}

/**
 * The manifest's fallback assetId ("PROP_FALLBACK_01" in v1). Empty when the
 * catalog failed to load.
 */
export function fallbackAssetId(): string {
  return LOAD.document?.fallbackAssetId ?? "";
}

/** The fallback descriptor; undefined when the catalog failed to load. */
export function fallbackAsset(): CatalogAssetDescriptor | undefined {
  const id = fallbackAssetId();
  return id === "" || !LOAD.ok ? undefined : getAsset(id);
}

/**
 * EXACT-assetId lookup that never returns "unknown": an id absent from the
 * catalog resolves to the declared fallback descriptor. When the whole catalog
 * failed to load this returns undefined (callers must gate on
 * {@link isCatalogHealthy()} first).
 */
export function resolveAsset(assetId: string): CatalogAssetDescriptor | undefined {
  return getAsset(assetId) ?? fallbackAsset();
}