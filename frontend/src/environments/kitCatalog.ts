import rawApartmentManifest from "../../../assets/environments/apartment.json";
import rawOfficeManifest from "../../../assets/environments/office.json";
import rawHotelSuiteManifest from "../../../assets/environments/hotel_suite.json";
import rawWarehouseManifest from "../../../assets/environments/warehouse.json";
import rawMansionManifest from "../../../assets/environments/mansion.json";
import { CATEGORY_VOCABULARY, getAsset, isCatalogHealthy } from "../catalog/assetCatalog";

/**
 * Phase 11 Track B — environment kit catalog client (frontend side).
 *
 * Consumes the SAME frozen v1 kit manifests as the backend environment
 * service (`assets/environments/*.json` — the SINGLE source of truth for
 * anchors/zones/spawn/lighting/structural asset ids). The manifests are
 * application-owned static data BUNDLED into the frontend at build time via
 * Vite (resolveJsonModule): there is NO runtime fetch, NO network, NO
 * arbitrary URL/path resolution. A kit update is a versioned manifest change
 * shipped with the app, exactly like the backend.
 *
 * FRONTEND/BACKEND BOUNDARY:
 *  - The BACKEND owns the environment RESOLVER (free-text -> environmentId).
 *    It publishes the exact `environmentId` inside the player-safe world
 *    graph (`scene.environmentId`, additive Phase 11 field).
 *  - The FRONTEND accepts EXACT environmentIds only ({@link getKit}, {@link
 *    hasKit}): a hostile/unknown id never reaches a kit descriptor the
 *    backend did not choose (it resolves to the apartment fallback).
 *
 * VALIDATION / SAFETY:
 *  - {@link validateKit} is a STRICT, deterministic validator mirroring the
 *    backend kit checks (backend/app/environments/manifests.py): the
 *    documented top-level key set, id grammars for environment/zone/anchor
 *    ids, the ANCHOR_TYPE_VOCABULARY, zone/anchor count bounds and array-size
 *    bounds, string safety (control chars, URL schemes, path separators,
 *    traversal, absolute paths, oversized strings), finite/bounded local
 *    positions (|x|,|z| <= 20, 0 <= y <= 8 per the Track B contract),
 *    rotations inside [-2*pi, 2*pi], duplicate environmentId/anchorId/zoneId,
 *    unknown zone references, exclusive anchors never sharing a position,
 *    spawn validity (references a PLAYER_SPAWN anchor and keeps >= 0.4
 *    clearance from every other anchor), BODY anchors on the floor plane,
 *    DOCUMENT anchors near a DESK_EVIDENCE anchor, lighting vocabulary/bounds,
 *    structural asset ids present in the bundled catalog, allowedCategories
 *    inside the catalog category vocabulary and the required default-anchor
 *    coverage (BODY / FLOOR_EVIDENCE / DESK_EVIDENCE / GENERIC_PROP / DOOR /
 *    WINDOW + PLAYER_SPAWN, plus >= 2 of the secondary types).
 *  - {@link validateAllKits} additionally enforces CROSS-kit identity
 *    (duplicate environmentIds are rejected). The bundled five manifests are
 *    validated ONCE at module load. A corrupted kit set must never silently
 *    break rendering: the module stays non-throwing, {@link
 *    isKitCatalogHealthy()} reports the failure and {@link
 *    getKitCatalogError()} carries the deterministic message so callers can
 *    surface a safe app-level error (the scene degrades to the apartment
 *    shell and the page shows a notice).
 *
 * SECURITY: the kit manifests carry ONLY declarative data (ids, vocabularies,
 * hex colors, bounded local transforms). No URL, no filesystem path, no
 * script/shader/handler field is ever read from them; the geometry builder
 * turns descriptors into local Babylon primitives only.
 */

/** The Phase 11 semantic anchor vocabulary (the ONLY anchor types kits may use). */
export type AnchorType =
  | "PLAYER_SPAWN"
  | "BODY"
  | "FLOOR_EVIDENCE"
  | "DESK_EVIDENCE"
  | "TABLE_PROP"
  | "COMPUTER"
  | "DOCUMENT"
  | "WALL_EVIDENCE"
  | "DOOR"
  | "WINDOW"
  | "STORAGE"
  | "CCTV"
  | "ACCESS_CONTROL"
  | "GENERIC_PROP";

/** The exact ANCHOR_TYPE vocabulary as an ordered array (mirrors the backend). */
export const ANCHOR_TYPE_VOCABULARY: readonly string[] = [
  "PLAYER_SPAWN",
  "BODY",
  "FLOOR_EVIDENCE",
  "DESK_EVIDENCE",
  "TABLE_PROP",
  "COMPUTER",
  "DOCUMENT",
  "WALL_EVIDENCE",
  "DOOR",
  "WINDOW",
  "STORAGE",
  "CCTV",
  "ACCESS_CONTROL",
  "GENERIC_PROP",
];

/** Lighting profile vocabulary (mirrors the backend). */
export const LIGHTING_PROFILE_VOCABULARY: readonly string[] = ["warm_flat", "cool_dim", "neutral"];

/** Anchor types that MUST have at least one covered anchor per kit. */
export const REQUIRED_COVERAGE_TYPES: readonly string[] = [
  "PLAYER_SPAWN",
  "BODY",
  "FLOOR_EVIDENCE",
  "DESK_EVIDENCE",
  "GENERIC_PROP",
  "DOOR",
  "WINDOW",
];

/** Secondary coverage types: at least MIN_SECONDARY_COVERED must be covered. */
export const SECONDARY_COVERAGE_TYPES: readonly string[] = [
  "COMPUTER",
  "DOCUMENT",
  "TABLE_PROP",
  "STORAGE",
  "CCTV",
  "ACCESS_CONTROL",
];

/** Minimum number of covered SECONDARY_COVERAGE_TYPES per kit. */
export const MIN_SECONDARY_COVERED = 2;

/** The five Phase 11 kits, in stable (import/manifest) order. */
export const KIT_IDS: readonly string[] = ["apartment", "office", "hotel_suite", "warehouse", "mansion"];

/* ======================================================================
 * Typed descriptors
 * ==================================================================== */

/** One local-space vector (absolute local transforms, never player coords). */
export interface KitVec3 {
  x: number;
  y: number;
  z: number;
}

/** One logical zone (renderable room group) of a kit. */
export interface KitZone {
  zoneId: string;
  label: string;
  rooms: readonly string[];
}

/** One semantic anchor of a kit. */
export interface KitAnchor {
  anchorId: string;
  type: AnchorType;
  zoneId: string;
  position: KitVec3;
  rotation: KitVec3;
  allowedCategories: readonly string[];
  exclusive: boolean;
  required: boolean;
}

/** The player navigation spawn (references a PLAYER_SPAWN anchor). */
export interface KitSpawn {
  anchorId: string;
  position: KitVec3;
  rotation: KitVec3;
}

/** One lighting profile (phase-independent deterministic values). */
export interface KitLighting {
  profile: string;
  keyIntensity: number;
  hemiIntensity: number;
  accentColor: string;
}

/** One fully validated environment kit document (mirrors the backend descriptor). */
export interface EnvironmentKitDocument {
  environmentId: string;
  version: number;
  canonicalName: string;
  aliases: readonly string[];
  tags: readonly string[];
  zones: readonly KitZone[];
  anchors: readonly KitAnchor[];
  spawn: KitSpawn;
  lighting: KitLighting;
  structuralAssets: readonly string[];
  styleHint: string | null;
  defaultAnchorCoverage: Readonly<Record<string, readonly string[]>>;
}

/* ======================================================================
 * Validation
 * ==================================================================== */

/**
 * Raised by {@link validateKit} / {@link validateAllKits} when the raw
 * manifest(s) fail the strict deterministic checks. `issues` is the sorted,
 * de-duplicated issue list.
 */
export class KitValidationError extends Error {
  readonly issues: readonly string[];
  constructor(issues: readonly string[]) {
    const sorted = [...new Set(issues)].sort();
    const count = sorted.length;
    super(`environment kit manifest failed validation (${count} issue${count === 1 ? "" : "s"}): ${sorted.join("; ")}`);
    this.name = "KitValidationError";
    this.issues = sorted;
  }
}

/** Lowercase snake identifier grammar for environment/zone/anchor ids. */
const ID_PATTERN = /^[a-z][a-z0-9_]*$/;
/** RGB hex color grammar, e.g. "#e8b878". */
const COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;
/** Windows drive-letter absolute path prefix, e.g. "C:\" / "C:/". */
const DRIVE_ABSOLUTE_RE = /^[A-Za-z]:[\\/]/;

const DOCUMENTED_MANIFEST_KEYS: readonly string[] = [
  "environmentId",
  "version",
  "canonicalName",
  "aliases",
  "tags",
  "zones",
  "anchors",
  "spawn",
  "lighting",
  "structuralAssets",
  "styleHint",
  "defaultAnchorCoverage",
];
const DOCUMENTED_ZONE_KEYS: readonly string[] = ["zoneId", "label", "rooms"];
const DOCUMENTED_ANCHOR_KEYS: readonly string[] = [
  "anchorId",
  "type",
  "zoneId",
  "position",
  "rotation",
  "allowedCategories",
  "exclusive",
  "required",
];
const DOCUMENTED_SPAWN_KEYS: readonly string[] = ["anchorId", "position", "rotation"];
const DOCUMENTED_LIGHTING_KEYS: readonly string[] = ["profile", "keyIntensity", "hemiIntensity", "accentColor"];
const VEC_KEYS: readonly string[] = ["x", "y", "z"];

/** Bounds mirroring the backend contract (backends may be looser; we never are). */
const MAX_STRING_LENGTH = 80;
const MIN_ZONES = 5;
const MAX_ZONES = 8;
const MIN_ANCHORS = 10;
const MAX_ANCHORS = 40;
const MAX_ALIASES = 8;
const MAX_TAGS = 16;
const MAX_ROOMS_PER_KIT = 8;
const MAX_CATEGORIES_PER_ANCHOR = 8;
const MAX_POSITION_BOUND = 20.0;
const POSITION_Y_MIN = 0.0;
const POSITION_Y_MAX = 8.0;
const ROTATION_BOUND = 2.0 * Math.PI;
const POSITION_EPS = 1e-3;
const SPAWN_CLEARANCE = 0.4;
const DOCUMENT_DESK_DISTANCE = 3.0;
const MIN_STRUCTURAL_ASSETS = 6;

const FORBIDDEN_URL_TOKENS: readonly string[] = ["http://", "https://", "data:", "file:", "javascript:"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/** Deterministic string-safety scans (mirrors the backend kit checks). */
function stringSafetyIssues(value: string, where: string): string[] {
  const issues: string[] = [];
  if (value.length > MAX_STRING_LENGTH) {
    issues.push(`${where}: string exceeds ${MAX_STRING_LENGTH} characters`);
  }
  for (let i = 0; i < value.length; i++) {
    if (value.charCodeAt(i) < 0x20) {
      issues.push(`${where}: contains a control character`);
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

function stringIssues(value: unknown, where: string, allowNull: boolean = false): string[] {
  if (value === null) return allowNull ? [] : [`${where}: must be a non-empty string`];
  if (!isNonEmptyString(value)) return [`${where}: must be a non-empty string`];
  return stringSafetyIssues(value, where);
}

function stringListIssues(value: unknown, where: string, maxEntries: number | null): string[] {
  const issues: string[] = [];
  if (!Array.isArray(value)) {
    issues.push(`${where}: must be an array of non-empty strings`);
    return issues;
  }
  if (maxEntries !== null && value.length > maxEntries) {
    issues.push(`${where}: exceeds the maximum of ${maxEntries} entries`);
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

/** Raw vector {x,y,z} checks: exact keys, finite numeric components. */
function vecDataIssues(value: unknown, where: string): string[] {
  const issues: string[] = [];
  if (!isRecord(value)) {
    return [`${where}: must be an object {x, y, z}`];
  }
  const keys = Object.keys(value);
  if (keys.length !== 3 || !keys.includes("x") || !keys.includes("y") || !keys.includes("z")) {
    return [`${where}: must have exactly the keys x, y, z`];
  }
  for (const axis of VEC_KEYS) {
    const num = value[axis];
    if (typeof num !== "number" || !Number.isFinite(num)) {
      issues.push(`${where}.${axis}: must be a finite number`);
    }
  }
  return issues;
}

/** Position: finite and bounded to the local-space contract (|x|,|z|<=20, 0<=y<=8). */
function positionIssues(value: unknown, where: string): string[] {
  const issues = vecDataIssues(value, where);
  if (!isRecord(value)) return issues;
  for (const axis of ["x", "z"]) {
    const num = value[axis];
    if (typeof num === "number" && Number.isFinite(num) && Math.abs(num) > MAX_POSITION_BOUND) {
      issues.push(`${where}.${axis}: must satisfy |v| <= ${MAX_POSITION_BOUND}`);
    }
  }
  const y = value.y;
  if (typeof y === "number" && Number.isFinite(y) && (y < POSITION_Y_MIN || y > POSITION_Y_MAX)) {
    issues.push(`${where}.y: must be within [${POSITION_Y_MIN}, ${POSITION_Y_MAX}]`);
  }
  return issues;
}

/** Rotation: finite and inside [-2*pi, 2*pi] per component. */
function rotationIssues(value: unknown, where: string): string[] {
  const issues = vecDataIssues(value, where);
  if (!isRecord(value)) return issues;
  for (const axis of VEC_KEYS) {
    const num = value[axis];
    if (
      typeof num === "number" &&
      Number.isFinite(num) &&
      !(-ROTATION_BOUND - 1e-9 <= num && num <= ROTATION_BOUND + 1e-9)
    ) {
      issues.push(`${where}.${axis}: must be within [-2*pi, 2*pi]`);
    }
  }
  return issues;
}

function vec3From(value: unknown): KitVec3 | null {
  if (!isRecord(value) || Object.keys(value).length !== 3) return null;
  const x = value.x;
  const y = value.y;
  const z = value.z;
  if (typeof x !== "number" || typeof y !== "number" || typeof z !== "number") return null;
  return { x, y, z };
}

/** Check `key` is a finite number inside [low, high]. */
function numberInRange(value: unknown, key: string, where: string, low: number, high: number, issues: string[]): void {
  const raw = isRecord(value) ? value[key] : undefined;
  if (typeof raw !== "number" || !Number.isFinite(raw)) {
    issues.push(`${where}.${key} must be a finite number`);
    return;
  }
  if (raw < low || raw > high) {
    issues.push(`${where}.${key} must be within [${low}, ${high}]`);
  }
}

function validateZone(item: unknown, where: string, issues: string[]): void {
  if (!isRecord(item)) {
    issues.push(`${where} must be a JSON object`);
    return;
  }
  const extra = Object.keys(item).filter((key) => !DOCUMENTED_ZONE_KEYS.includes(key));
  if (extra.length > 0) {
    issues.push(`${where}: unknown key ${JSON.stringify(extra.sort())}`);
  }
  issues.push(...stringIssues(item.zoneId, `${where}.zoneId`));
  issues.push(...stringIssues(item.label, `${where}.label`));
  issues.push(...stringListIssues(item.rooms, `${where}.rooms`, null));
}

function validateAnchor(item: unknown, where: string, issues: string[]): void {
  if (!isRecord(item)) {
    issues.push(`${where} must be a JSON object`);
    return;
  }
  const extra = Object.keys(item).filter((key) => !DOCUMENTED_ANCHOR_KEYS.includes(key));
  if (extra.length > 0) {
    issues.push(`${where}: unknown key ${JSON.stringify(extra.sort())}`);
  }
  const anchorId = item.anchorId;
  issues.push(...stringIssues(anchorId, `${where}.anchorId`));
  if (isNonEmptyString(anchorId) && !ID_PATTERN.test(anchorId)) {
    issues.push(`${where}.anchorId ${JSON.stringify(anchorId)} must match ^[a-z][a-z0-9_]*$`);
  }
  const type = item.type;
  if (!isNonEmptyString(type)) {
    issues.push(`${where}.type must be a non-empty string`);
  } else if (!ANCHOR_TYPE_VOCABULARY.includes(type)) {
    issues.push(`${where}.type ${JSON.stringify(type)} is not in the ANCHOR_TYPE_VOCABULARY ${JSON.stringify(ANCHOR_TYPE_VOCABULARY)}`);
  }
  issues.push(...stringIssues(item.zoneId, `${where}.zoneId`));
  issues.push(...positionIssues(item.position, `${where}.position`));
  issues.push(...rotationIssues(item.rotation, `${where}.rotation`));
  issues.push(...stringListIssues(item.allowedCategories, `${where}.allowedCategories`, MAX_CATEGORIES_PER_ANCHOR));
  if (typeof item.exclusive !== "boolean") {
    issues.push(`${where}.exclusive must be a boolean`);
  }
  if (typeof item.required !== "boolean") {
    issues.push(`${where}.required must be a boolean`);
  }
}

function validateSpawn(item: unknown, where: string, issues: string[]): void {
  if (!isRecord(item)) {
    issues.push(`${where} must be a JSON object`);
    return;
  }
  const extra = Object.keys(item).filter((key) => !DOCUMENTED_SPAWN_KEYS.includes(key));
  if (extra.length > 0) {
    issues.push(`${where}: unknown key ${JSON.stringify(extra.sort())}`);
  }
  issues.push(...stringIssues(item.anchorId, `${where}.anchorId`));
  issues.push(...positionIssues(item.position, `${where}.position`));
  issues.push(...rotationIssues(item.rotation, `${where}.rotation`));
}

function validateLighting(item: unknown, where: string, issues: string[]): void {
  if (!isRecord(item)) {
    issues.push(`${where} must be a JSON object`);
    return;
  }
  const extra = Object.keys(item).filter((key) => !DOCUMENTED_LIGHTING_KEYS.includes(key));
  if (extra.length > 0) {
    issues.push(`${where}: unknown key ${JSON.stringify(extra.sort())}`);
  }
  const profile = item.profile;
  if (!isNonEmptyString(profile)) {
    issues.push(`${where}.profile must be a non-empty string`);
  } else if (!LIGHTING_PROFILE_VOCABULARY.includes(profile)) {
    issues.push(`${where}.profile ${JSON.stringify(profile)} is not in the lighting vocabulary ${JSON.stringify(LIGHTING_PROFILE_VOCABULARY)}`);
  }
  numberInRange(item, "keyIntensity", where, 0.0, 1.0, issues);
  numberInRange(item, "hemiIntensity", where, 0.0, 1.0, issues);
  const accent = item.accentColor;
  if (!isNonEmptyString(accent) || !COLOR_PATTERN.test(accent)) {
    issues.push(`${where}.accentColor must be a #RRGGBB hex color`);
  }
}

/**
 * STRICT deterministic validator for ONE raw kit manifest. Collects every
 * issue, sorts/dedupes them and throws {@link KitValidationError} when the
 * manifest is not loadable. A corrupted manifest can never silently reach the
 * geometry builder — callers gate on {@link isKitCatalogHealthy()}/{@link
 * getKitCatalogError()} and the scene degrades to the apartment shell.
 */
export function validateKit(raw: unknown): EnvironmentKitDocument {
  const issues: string[] = [];

  if (!isRecord(raw)) {
    throw new KitValidationError(["environment document must be a JSON object"]);
  }

  const extra = Object.keys(raw).filter((key) => !DOCUMENTED_MANIFEST_KEYS.includes(key));
  if (extra.length > 0) {
    issues.push(`environment document: unknown key ${JSON.stringify(extra.sort())}`);
  }

  const environmentId = raw.environmentId;
  issues.push(...stringIssues(environmentId, "environmentId"));
  if (isNonEmptyString(environmentId) && !ID_PATTERN.test(environmentId)) {
    issues.push(`environmentId ${JSON.stringify(environmentId)} must match ^[a-z][a-z0-9_]*$`);
  }

  const version = raw.version;
  if (typeof version !== "number" || !Number.isInteger(version) || version < 1) {
    issues.push("version must be a positive integer");
  }

  issues.push(...stringIssues(raw.canonicalName, "canonicalName"));
  issues.push(...stringListIssues(raw.aliases, "aliases", MAX_ALIASES));
  issues.push(...stringListIssues(raw.tags, "tags", MAX_TAGS));

  /* --- zones --- */
  const zoneIdsSeen: string[] = [];
  const zonesRaw = raw.zones;
  if (!Array.isArray(zonesRaw)) {
    issues.push("zones must be an array");
  } else {
    if (zonesRaw.length < MIN_ZONES) issues.push(`zones must contain at least ${MIN_ZONES} entries`);
    if (zonesRaw.length > MAX_ZONES) issues.push(`zones exceeds the maximum of ${MAX_ZONES} entries`);
    let totalRooms = 0;
    zonesRaw.forEach((item, index) => {
      const where = `zones[${index}]`;
      validateZone(item, where, issues);
      if (isRecord(item)) {
        const zoneId = item.zoneId;
        if (isNonEmptyString(zoneId) && ID_PATTERN.test(zoneId)) {
          zoneIdsSeen.push(zoneId);
          if (Array.isArray(item.rooms)) {
            totalRooms += item.rooms.filter((room) => isNonEmptyString(room)).length;
          }
        }
      }
    });
    for (let index = 0; index < zoneIdsSeen.length; index++) {
      const zoneId = zoneIdsSeen[index];
      if (zoneIdsSeen.indexOf(zoneId) !== index) {
        const first = zoneIdsSeen.indexOf(zoneId);
        issues.push(
          `duplicate zoneId ${JSON.stringify(zoneId)}: declared at zones[${first}] and again at zones[${index}]`,
        );
      }
    }
    if (totalRooms > MAX_ROOMS_PER_KIT) {
      issues.push(`kit declares more than ${MAX_ROOMS_PER_KIT} rooms in total`);
    }
  }
  const zoneIdSet = new Set(zoneIdsSeen);

  /* --- anchors --- */
  const anchorIdsSeen: string[] = [];
  const anchorTypes: string[] = [];
  const anchorZoneRefs: string[] = [];
  const anchorsRaw = raw.anchors;
  if (!Array.isArray(anchorsRaw)) {
    issues.push("anchors must be an array");
  } else {
    if (anchorsRaw.length < MIN_ANCHORS) issues.push(`anchors must contain at least ${MIN_ANCHORS} entries`);
    if (anchorsRaw.length > MAX_ANCHORS) issues.push(`anchors exceeds the maximum of ${MAX_ANCHORS} entries`);
    anchorsRaw.forEach((item, index) => {
      const where = `anchors[${index}]`;
      validateAnchor(item, where, issues);
      if (isRecord(item)) {
        const anchorId = item.anchorId;
        const type = item.type;
        const zoneId = item.zoneId;
        if (isNonEmptyString(anchorId) && ID_PATTERN.test(anchorId)) {
          anchorIdsSeen.push(anchorId);
          anchorTypes.push(isNonEmptyString(type) ? type : "");
          anchorZoneRefs.push(isNonEmptyString(zoneId) ? zoneId : "");
        }
      }
    });
    for (let index = 0; index < anchorIdsSeen.length; index++) {
      const anchorId = anchorIdsSeen[index];
      if (anchorIdsSeen.indexOf(anchorId) !== index) {
        const first = anchorIdsSeen.indexOf(anchorId);
        issues.push(
          `duplicate anchorId ${JSON.stringify(anchorId)}: declared at anchors[${first}] and again at anchors[${index}]`,
        );
      }
    }
    // Unknown zone references (deterministic in manifest order).
    anchorsRaw.forEach((item, index) => {
      if (!isRecord(item)) return;
      const anchorId = item.anchorId;
      const zoneId = item.zoneId;
      if (isNonEmptyString(anchorId) && ID_PATTERN.test(anchorId) && isNonEmptyString(zoneId) && !zoneIdSet.has(zoneId)) {
        issues.push(`anchors[${index}]: unknown zoneId ${JSON.stringify(zoneId)}`);
      }
    });
    // Exclusive anchors must never share a position.
    const exclusivePositions: Array<{ anchorId: string; position: KitVec3 }> = [];
    anchorsRaw.forEach((item) => {
      if (!isRecord(item) || item.exclusive !== true) return;
      const anchorId = item.anchorId;
      const position = vec3From(item.position ?? {});
      if (isNonEmptyString(anchorId) && position !== null) {
        exclusivePositions.push({ anchorId: anchorId as string, position });
      }
    });
    for (let index = 0; index < exclusivePositions.length; index++) {
      for (let other = 0; other < index; other++) {
        const a = exclusivePositions[index];
        const b = exclusivePositions[other];
        if (distance3D(a.position, b.position) <= POSITION_EPS) {
          issues.push(
            `exclusive anchors ${JSON.stringify(b.anchorId)} and ${JSON.stringify(a.anchorId)} share position ` +
              `(${a.position.x}, ${a.position.y}, ${a.position.z})`,
          );
        }
      }
    }
    // BODY anchors lie on the floor plane; allowedCategories vocabulary.
    anchorsRaw.forEach((item, index) => {
      if (!isRecord(item)) return;
      if (item.type === "BODY" && isRecord(item.position)) {
        const y = item.position.y;
        if (typeof y === "number" && Number.isFinite(y) && Math.abs(y) > POSITION_EPS) {
          issues.push(`anchors[${index}]: BODY anchor must lie on the floor plane (y == 0)`);
        }
      }
      const categories = item.allowedCategories;
      if (Array.isArray(categories)) {
        for (const category of categories) {
          if (isNonEmptyString(category) && !CATEGORY_VOCABULARY.includes(category)) {
            issues.push(
              `anchors[${index}]: category ${JSON.stringify(category)} is not in the catalog category vocabulary ${JSON.stringify(CATEGORY_VOCABULARY)}`,
            );
          }
        }
      }
    });
    // DOCUMENT anchors must sit near a DESK_EVIDENCE anchor (<= 3.0m).
    const deskPositions: KitVec3[] = [];
    anchorsRaw.forEach((item) => {
      if (!isRecord(item) || item.type !== "DESK_EVIDENCE") return;
      const position = vec3From(item.position ?? {});
      if (position !== null) deskPositions.push(position);
    });
    anchorsRaw.forEach((item, index) => {
      if (!isRecord(item) || item.type !== "DOCUMENT") return;
      const position = vec3From(item.position ?? {});
      if (position === null) return;
      if (deskPositions.length === 0) {
        issues.push(`anchors[${index}]: DOCUMENT anchor declared but the kit has no DESK_EVIDENCE anchor to host it near`);
        return;
      }
      const near = deskPositions.some((desk) => distance3D(position, desk) <= DOCUMENT_DESK_DISTANCE);
      if (!near) {
        issues.push(
          `anchors[${index}]: DOCUMENT anchor must be within ${DOCUMENT_DESK_DISTANCE} of a DESK_EVIDENCE anchor`,
        );
      }
    });
  }

  /* --- spawn --- */
  const spawnRaw = raw.spawn;
  validateSpawn(spawnRaw, "spawn", issues);
  if (isRecord(spawnRaw) && Array.isArray(anchorsRaw)) {
    const spawnAnchorId = spawnRaw.anchorId;
    const spawnAnchor = anchorsRaw.find(
      (item) => isRecord(item) && item.anchorId === spawnAnchorId,
    );
    if (isNonEmptyString(spawnAnchorId)) {
      if (spawnAnchor === undefined) {
        issues.push(`spawn.anchorId ${JSON.stringify(spawnAnchorId)} does not reference a declared anchor`);
      } else if (!isRecord(spawnAnchor) || spawnAnchor.type !== "PLAYER_SPAWN") {
        issues.push(`spawn.anchorId ${JSON.stringify(spawnAnchorId)} must reference an anchor of type PLAYER_SPAWN`);
      }
    }
    const spawnPosition = vec3From(spawnRaw.position ?? {});
    if (spawnPosition !== null && isNonEmptyString(spawnAnchorId)) {
      for (const item of anchorsRaw) {
        if (!isRecord(item) || item.anchorId === spawnAnchorId) continue;
        const position = vec3From(item.position ?? {});
        if (position === null) continue;
        if (distance3D(spawnPosition, position) < SPAWN_CLEARANCE) {
          issues.push(
            `spawn is within ${SPAWN_CLEARANCE} of anchor ${JSON.stringify(item.anchorId)} (spawn must not intersect geometry)`,
          );
        }
      }
    }
  }

  /* --- lighting --- */
  validateLighting(raw.lighting, "lighting", issues);

  /* --- structural assets (catalog cross-check when the catalog is healthy) --- */
  const structuralAssetsRaw = raw.structuralAssets;
  const structuralAssets: string[] = [];
  if (!Array.isArray(structuralAssetsRaw)) {
    issues.push("structuralAssets must be an array");
  } else {
    if (structuralAssetsRaw.length < MIN_STRUCTURAL_ASSETS) {
      issues.push(`structuralAssets must contain at least ${MIN_STRUCTURAL_ASSETS} entries`);
    }
    structuralAssetsRaw.forEach((assetId, index) => {
      if (!isNonEmptyString(assetId)) {
        issues.push(`structuralAssets[${index}] must be a non-empty string`);
        return;
      }
      issues.push(...stringSafetyIssues(assetId, `structuralAssets[${index}]`));
      if (isCatalogHealthy() && getAsset(assetId) === undefined) {
        issues.push(`structuralAssets[${index}]: asset ${JSON.stringify(assetId)} does not exist in the asset catalog`);
      }
      structuralAssets.push(assetId);
    });
  }

  issues.push(...stringIssues(raw.styleHint, "styleHint", true));

  /* --- default anchor coverage --- */
  const anchorIdSet = new Set(anchorIdsSeen);
  const coveredTypes = new Set<string>();
  const coverageRaw = raw.defaultAnchorCoverage;
  if (!isRecord(coverageRaw)) {
    issues.push("defaultAnchorCoverage must be an object");
  } else {
    for (const [anchorType, listRaw] of Object.entries(coverageRaw)) {
      if (!ANCHOR_TYPE_VOCABULARY.includes(anchorType)) {
        issues.push(`defaultAnchorCoverage: unknown anchor type ${JSON.stringify(anchorType)}`);
        continue;
      }
      if (!Array.isArray(listRaw)) {
        issues.push(`defaultAnchorCoverage[${anchorType}] must be an array of anchorIds`);
        continue;
      }
      const seenInList = new Set<string>();
      listRaw.forEach((anchorId, index) => {
        if (!isNonEmptyString(anchorId)) {
          issues.push(`defaultAnchorCoverage[${anchorType}][${index}] must be a non-empty string`);
          return;
        }
        if (seenInList.has(anchorId)) {
          issues.push(`defaultAnchorCoverage[${anchorType}]: duplicate anchorId ${JSON.stringify(anchorId)}`);
        } else if (!anchorIdSet.has(anchorId)) {
          issues.push(`defaultAnchorCoverage[${anchorType}]: unknown anchorId ${JSON.stringify(anchorId)}`);
        } else {
          seenInList.add(anchorId);
        }
      });
      if (listRaw.length > 0) coveredTypes.add(anchorType);
    }
    for (const required of REQUIRED_COVERAGE_TYPES) {
      if (!coveredTypes.has(required)) {
        issues.push(`defaultAnchorCoverage: required anchor type ${JSON.stringify(required)} has no default coverage`);
      }
    }
    const secondaryCovered = SECONDARY_COVERAGE_TYPES.filter((type) => coveredTypes.has(type)).length;
    if (secondaryCovered < MIN_SECONDARY_COVERED) {
      issues.push(
        `defaultAnchorCoverage: at least ${MIN_SECONDARY_COVERED} of the secondary types ${JSON.stringify(SECONDARY_COVERAGE_TYPES)} must have default coverage`,
      );
    }
  }

  /* --- build the typed descriptor (only when render-critical fields exist) --- */
  const anchors: KitAnchor[] = [];
  if (Array.isArray(anchorsRaw)) {
    anchorsRaw.forEach((item) => {
      if (!isRecord(item)) return;
      const anchorId = item.anchorId;
      const type = item.type;
      const zoneId = item.zoneId;
      const position = vec3From(item.position ?? {});
      const rotation = vec3From(item.rotation ?? {});
      if (
        !isNonEmptyString(anchorId) ||
        !ID_PATTERN.test(anchorId) ||
        !isNonEmptyString(type) ||
        !ANCHOR_TYPE_VOCABULARY.includes(type) ||
        !isNonEmptyString(zoneId) ||
        position === null ||
        rotation === null ||
        !Array.isArray(item.allowedCategories) ||
        typeof item.exclusive !== "boolean" ||
        typeof item.required !== "boolean"
      ) {
        return;
      }
      anchors.push({
        anchorId,
        type: type as AnchorType,
        zoneId,
        position,
        rotation,
        allowedCategories: [...(item.allowedCategories as string[])],
        exclusive: item.exclusive as boolean,
        required: item.required as boolean,
      });
    });
  }

  if (
    issues.length > 0 ||
    !isNonEmptyString(environmentId) ||
    !ID_PATTERN.test(environmentId) ||
    typeof version !== "number" ||
    !Number.isInteger(version) ||
    version < 1 ||
    !isNonEmptyString(raw.canonicalName) ||
    !Array.isArray(raw.aliases) ||
    !Array.isArray(raw.tags) ||
    !Array.isArray(raw.zones) ||
    !Array.isArray(raw.anchors) ||
    !isRecord(raw.spawn) ||
    !isRecord(raw.lighting) ||
    !Array.isArray(structuralAssetsRaw) ||
    !isRecord(raw.defaultAnchorCoverage) ||
    anchors.length !== (raw.anchors as unknown[]).length
  ) {
    if (issues.length === 0) {
      issues.push("environment document: invalid kit structure");
    }
    throw new KitValidationError(issues);
  }

  const zones: KitZone[] = (raw.zones as unknown[]).map((item) => {
    const record = item as Record<string, unknown>;
    return {
      zoneId: record.zoneId as string,
      label: record.label as string,
      rooms: [...((record.rooms as string[]) ?? [])],
    };
  });

  const spawnRecord = raw.spawn as Record<string, unknown>;
  const lightingRecord = raw.lighting as Record<string, unknown>;

  return {
    environmentId: environmentId as string,
    version: version as number,
    canonicalName: raw.canonicalName as string,
    aliases: [...(raw.aliases as string[])],
    tags: [...(raw.tags as string[])],
    zones,
    anchors,
    spawn: {
      anchorId: spawnRecord.anchorId as string,
      position: { ...(vec3From(spawnRecord.position ?? {}) as KitVec3) },
      rotation: { ...(vec3From(spawnRecord.rotation ?? {}) as KitVec3) },
    },
    lighting: {
      profile: lightingRecord.profile as string,
      keyIntensity: lightingRecord.keyIntensity as number,
      hemiIntensity: lightingRecord.hemiIntensity as number,
      accentColor: lightingRecord.accentColor as string,
    },
    structuralAssets,
    styleHint: typeof raw.styleHint === "string" ? (raw.styleHint as string) : null,
    defaultAnchorCoverage: Object.fromEntries(
      Object.entries(coverageRaw as Record<string, unknown>).map(([key, value]) => [key, [...(value as string[])]]),
    ),
  };
}

function distance3D(a: KitVec3, b: KitVec3): number {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}

/**
 * STRICT deterministic validator for a SET of raw kit manifests. Validates
 * every manifest (collecting all issues) and enforces CROSS-kit identity:
 * duplicate environmentIds are rejected. Throws {@link KitValidationError}.
 */
export function validateAllKits(rawKits: unknown[]): EnvironmentKitDocument[] {
  const issueList: string[] = [];
  const kits: EnvironmentKitDocument[] = [];
  rawKits.forEach((raw, index) => {
    try {
      kits.push(validateKit(raw));
    } catch (error) {
      if (error instanceof KitValidationError) {
        issueList.push(...error.issues.map((issue) => `kits[${index}]: ${issue}`));
      } else {
        throw error;
      }
    }
  });
  const seen = new Map<string, number>();
  for (let index = 0; index < kits.length; index++) {
    const kit = kits[index];
    const first = seen.get(kit.environmentId);
    if (first !== undefined) {
      issueList.push(
        `duplicate environmentId ${JSON.stringify(kit.environmentId)}: declared at kits[${first}] and again at kits[${index}]`,
      );
    } else {
      seen.set(kit.environmentId, index);
    }
  }
  if (issueList.length > 0) {
    throw new KitValidationError(issueList);
  }
  return kits;
}

/* ======================================================================
 * Module-level load + non-throwing health API
 * ==================================================================== */

interface KitLoadState {
  ok: boolean;
  error: string | null;
  kits: EnvironmentKitDocument[];
}

/** Validate the five bundled manifests exactly once. Never throws. */
const LOAD: KitLoadState = (() => {
  try {
    const kits = validateAllKits([
      rawApartmentManifest,
      rawOfficeManifest,
      rawHotelSuiteManifest,
      rawWarehouseManifest,
      rawMansionManifest,
    ] as unknown[]);
    return { ok: true, error: null, kits };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, error: message, kits: [] };
  }
})();

const KITS_BY_ID: ReadonlyMap<string, EnvironmentKitDocument> = new Map(
  LOAD.kits.map((kit) => [kit.environmentId, kit] as const),
);

/** True when all five bundled kit manifests passed strict validation. */
export function isKitCatalogHealthy(): boolean {
  return LOAD.ok;
}

/** Deterministic validation message; null when the kit catalog is healthy. */
export function getKitCatalogError(): string | null {
  return LOAD.error;
}

/**
 * EXACT-environmentId lookup — the only kit access the frontend geometry
 * performs. Returns undefined for unknown ids (or when the kit catalog failed
 * to load); callers fall back to the apartment shell/geometry.
 */
export function getKit(environmentId: string): EnvironmentKitDocument | undefined {
  return KITS_BY_ID.get(environmentId);
}

/** True exactly for the environmentIds declared by the bundled manifests. */
export function hasKit(environmentId: string): boolean {
  return KITS_BY_ID.has(environmentId);
}

/** The declared kit ids in stable order (empty when the kit catalog failed). */
export function allKitIds(): readonly string[] {
  return KIT_IDS.filter((id) => KITS_BY_ID.has(id));
}

/** Deterministic default-anchor coverage lookup (anchorId list per type). */
export function coverageFor(kit: EnvironmentKitDocument, anchorType: string): readonly string[] {
  const list = kit.defaultAnchorCoverage[anchorType];
  return typeof list === "undefined" ? [] : list;
}