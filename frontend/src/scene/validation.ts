import type {
  AccusationCandidatesDTO,
  GeneratedAssetDefinition,
  GeneratedPartDTO,
  GeneratedPrimitiveKind,
  GeneratedVec3DTO,
  InvestigationBootstrapResponse,
  InvestigationSceneLocationDTO,
  MotiveCandidateDTO,
  PlaythroughLifecycleState,
  PlayerKnowledgeDTO,
  SuspectCandidateDTO,
  WeaponCandidateDTO,
  WorldObjectDTO,
} from "../api/types";

/**
 * Strict, deterministic validation for the frozen Phase 6 investigation DTOs.
 *
 * Every payload crossing the trust boundary (the /investigation bootstrap,
 * and anything inside its `scene` — the player-safe WorldGraph) is parsed
 * through these tiny validator objects: parse, never trust. A malformed
 * payload throws ValidationError and is never silently coerced; callers map
 * that error to the player-safe "malformed world graph" state so the page
 * degrades instead of white-screening.
 *
 * Deterministic by construction: no randomness, no dates, no mutation of
 * inputs; the same raw payload always produces the same result or the same
 * error.
 */

export class ValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
  }
}

/** The shape of every tiny deterministic validator in this module. */
export interface Validator<T> {
  validate(raw: unknown): T;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function requireString(owner: Record<string, unknown>, field: string, where: string): string {
  const value = owner[field];
  if (!isNonEmptyString(value)) {
    throw new ValidationError(`${where}.${field} must be a non-empty string.`);
  }
  return value;
}

function requireNullableString(owner: Record<string, unknown>, field: string, where: string): string | null {
  const value = owner[field];
  if (value === null) return null;
  if (typeof value !== "string") {
    throw new ValidationError(`${where}.${field} must be a string or null.`);
  }
  return value;
}

/**
 * A string-or-null field that may ALSO be OMITTED entirely (undefined).
 *
 * PD-SEC-01 (Phase 20): the backend strips `evidenceId` from UNDISCOVERED
 * world objects in pre-reveal DTOs — a missing key is the NEW valid pre-reveal
 * value (normalized to null, exactly like the older explicit null). A PRESENT
 * but non-string/non-null value is still malformed and rejected — the parser
 * never silently coerces a hostile value.
 */
function requireOptionalNullableString(
  owner: Record<string, unknown>,
  field: string,
  where: string,
): string | null {
  const value = owner[field];
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") {
    throw new ValidationError(`${where}.${field} must be a string, null, or absent.`);
  }
  return value;
}

/**
 * A string that MAY be empty (but must still be a string). Used for the
 * world-object `interaction` field: DEF-062 — published cases carry an empty
 * interaction string for objects with NO interaction affordance (the
 * affordance is payload-driven); an empty string is a VALID DTO value, only a
 * non-string or a missing field is malformed.
 */
function requireStringAllowEmpty(owner: Record<string, unknown>, field: string, where: string): string {
  const value = owner[field];
  if (typeof value !== "string") {
    throw new ValidationError(`${where}.${field} must be a string.`);
  }
  return value;
}

function requireBoolean(owner: Record<string, unknown>, field: string, where: string): boolean {
  const value = owner[field];
  if (typeof value !== "boolean") {
    throw new ValidationError(`${where}.${field} must be a boolean.`);
  }
  return value;
}

function requireStringArray(owner: Record<string, unknown>, field: string, where: string): string[] {
  const value = owner[field];
  if (!Array.isArray(value) || !value.every(isNonEmptyString)) {
    throw new ValidationError(`${where}.${field} must be an array of non-empty strings.`);
  }
  return [...value];
}

function requireNonNegativeInteger(owner: Record<string, unknown>, field: string, where: string): number {
  const value = owner[field];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ValidationError(`${where}.${field} must be a non-negative integer.`);
  }
  return value;
}

/** Validator for the PlayerKnowledge portion of the bootstrap. */
export const validatePlayerKnowledge: Validator<PlayerKnowledgeDTO> = {
  validate(raw: unknown): PlayerKnowledgeDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("playerKnowledge must be an object.");
    }
    return {
      discoveredEvidenceIds: requireStringArray(raw, "discoveredEvidenceIds", "playerKnowledge"),
      readEvidenceIds: requireStringArray(raw, "readEvidenceIds", "playerKnowledge"),
      visitedLocationIds: requireStringArray(raw, "visitedLocationIds", "playerKnowledge"),
    };
  },
};

/** Validator for one WorldObjectDTO entry. */
export const validateWorldObject: Validator<WorldObjectDTO> = {
  validate(raw: unknown): WorldObjectDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("world object must be an object.");
    }
    const objectId = requireString(raw, "objectId", "worldObject");
    const assetId = requireString(raw, "assetId", "worldObject");
    return {
      objectId,
      assetId,
      assetType: requireString(raw, "assetType", "worldObject"),
      subtype: requireNullableString(raw, "subtype", "worldObject"),
      locationId: requireString(raw, "locationId", "worldObject"),
      anchor: requireString(raw, "anchor", "worldObject"),
      // DEF-062: interaction may be "" (NO affordance). Non-string/missing is
      // still malformed — the field is REQUIRED for the player-safe DTO shape.
      interaction: requireStringAllowEmpty(raw, "interaction", "worldObject"),
      // PD-SEC-01: `evidenceId` is optional — absent for UNDISCOVERED objects
      // (backend strips it pre-reveal), null/string for known/discovered ones.
      evidenceId: requireOptionalNullableString(raw, "evidenceId", "worldObject"),
      discovered: requireBoolean(raw, "discovered", "worldObject"),
      read: requireBoolean(raw, "read", "worldObject"),
      // Phase 13: only a `proc.*` asset uses the `generated` block. Missing or
      // invalid blocks become null here (the object falls back to the neutral
      // primitive — never a crash); non-proc assets ALWAYS ignore the field.
      generated: assetId.startsWith("proc.")
        ? validateGeneratedDefinition(raw.generated)
        : null,
    };
  },
};

/**
 * The player-safe WorldGraph: bootstrap's `scene` object
 * ({environmentId, location:{locationId,name}, worldObjects:[WorldObjectDTO]}).
 */
export interface WorldGraphDTO {
  environmentId: string;
  location: InvestigationSceneLocationDTO;
  worldObjects: WorldObjectDTO[];
}

/** The environmentId published when a legacy payload omits Phase 11's field. */
const DEFAULT_ENVIRONMENT_ID = "apartment";

/** Max environmentId length (mirrors the backend kit string bound). */
const MAX_ENVIRONMENT_ID_LENGTH = 80;

/**
 * Optional-accept-required Phase 11 field: the backend NOW sends
 * `scene.environmentId`; legacy payloads without it still parse (lenient
 * fallback to "apartment" for backward compatibility). A PRESENT but
 * malformed value (non-string / empty / oversized) is still rejected — the
 * parser never silently coerces a hostile value.
 */
function requireEnvironmentId(owner: Record<string, unknown>, where: string): string {
  const value = owner.environmentId;
  if (value === undefined || value === null) return DEFAULT_ENVIRONMENT_ID;
  if (typeof value !== "string" || value === "") {
    throw new ValidationError(`${where}.environmentId must be a non-empty string.`);
  }
  if (value.length > MAX_ENVIRONMENT_ID_LENGTH) {
    throw new ValidationError(`${where}.environmentId exceeds ${MAX_ENVIRONMENT_ID_LENGTH} characters.`);
  }
  return value;
}

/**
 * Validates the player-safe WorldGraph shape. Exported separately so the
 * frontend test suite can exercise WorldGraph validation in isolation.
 */
export const validateWorldGraph: Validator<WorldGraphDTO> = {
  validate(raw: unknown): WorldGraphDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("scene must be an object.");
    }
    const locationRaw = raw.location;
    if (!isRecord(locationRaw)) {
      throw new ValidationError("scene.location must be an object.");
    }
    const worldObjectsRaw = raw.worldObjects;
    if (!Array.isArray(worldObjectsRaw)) {
      throw new ValidationError("scene.worldObjects must be an array.");
    }
    return {
      environmentId: requireEnvironmentId(raw, "scene"),
      location: {
        locationId: requireString(locationRaw, "locationId", "scene.location"),
        name: requireString(locationRaw, "name", "scene.location"),
      },
      worldObjects: worldObjectsRaw.map((entry) => validateWorldObject.validate(entry)),
    };
  },
};

/** The frozen lifecycle states the bootstrap may honestly publish. */
const LIFECYCLE_STATES = new Set<PlaythroughLifecycleState>(["PLAYING", "ACCUSED", "REVEALED"]);

/** Validator for one suspect candidate ({id,name}); unknown fields dropped. */
export const validateSuspectCandidate: Validator<SuspectCandidateDTO> = {
  validate(raw: unknown): SuspectCandidateDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("candidates.suspects entry must be an object.");
    }
    return {
      id: requireString(raw, "id", "candidates.suspects"),
      name: requireString(raw, "name", "candidates.suspects"),
    };
  },
};

/** Validator for one motive candidate ({id,label}); unknown fields dropped. */
export const validateMotiveCandidate: Validator<MotiveCandidateDTO> = {
  validate(raw: unknown): MotiveCandidateDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("candidates.motives entry must be an object.");
    }
    return {
      id: requireString(raw, "id", "candidates.motives"),
      label: requireString(raw, "label", "candidates.motives"),
    };
  },
};

/** Validator for one weapon candidate ({id,assetId,name}); unknown fields dropped. */
export const validateWeaponCandidate: Validator<WeaponCandidateDTO> = {
  validate(raw: unknown): WeaponCandidateDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("candidates.weapons entry must be an object.");
    }
    return {
      id: requireString(raw, "id", "candidates.weapons"),
      assetId: requireString(raw, "assetId", "candidates.weapons"),
      name: requireString(raw, "name", "candidates.weapons"),
    };
  },
};

/**
 * Validates the player-safe `candidates` block of the bootstrap.
 *
 * Player-safe guarantees:
 *  - only {id,name} / {id,label} / {id,assetId,name} survive; every other
 *    field (e.g. a malformed/hostile "correct"/"winner" marker) is DROPPED,
 *    never copied into the typed DTO;
 *  - the ARRAY ORDER is preserved exactly — the client renders candidates in
 *    the server's alphabetical order and never re-orders them.
 */
export const validateAccusationCandidates: Validator<AccusationCandidatesDTO> = {
  validate(raw: unknown): AccusationCandidatesDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("candidates must be an object.");
    }
    const suspectsRaw = raw.suspects;
    const motivesRaw = raw.motives;
    const weaponsRaw = raw.weapons;
    if (!Array.isArray(suspectsRaw)) {
      throw new ValidationError("candidates.suspects must be an array.");
    }
    if (!Array.isArray(motivesRaw)) {
      throw new ValidationError("candidates.motives must be an array.");
    }
    if (!Array.isArray(weaponsRaw)) {
      throw new ValidationError("candidates.weapons must be an array.");
    }
    return {
      suspects: suspectsRaw.map((entry) => validateSuspectCandidate.validate(entry)),
      motives: motivesRaw.map((entry) => validateMotiveCandidate.validate(entry)),
      weapons: weaponsRaw.map((entry) => validateWeaponCandidate.validate(entry)),
    };
  },
};

/** Validates the full 200 bootstrap payload of GET .../investigation. */
export const parseInvestigationBootstrap: Validator<InvestigationBootstrapResponse> = {
  validate(raw: unknown): InvestigationBootstrapResponse {
    if (!isRecord(raw)) {
      throw new ValidationError("Investigation bootstrap must be an object.");
    }
    const playthroughId = requireString(raw, "playthroughId", "bootstrap");
    const caseId = requireString(raw, "caseId", "bootstrap");
    const caseVersion = requireNonNegativeInteger(raw, "caseVersion", "bootstrap");
    if (typeof raw.state !== "string" || !LIFECYCLE_STATES.has(raw.state as PlaythroughLifecycleState)) {
      throw new ValidationError('bootstrap.state must be one of "PLAYING", "ACCUSED" or "REVEALED".');
    }
    const state = raw.state as PlaythroughLifecycleState;
    const playerKnowledge = validatePlayerKnowledge.validate(raw.playerKnowledge);
    const scene = validateWorldGraph.validate(raw.scene);
    const candidates = validateAccusationCandidates.validate(raw.candidates);
    return {
      playthroughId,
      caseId,
      caseVersion,
      state,
      playerKnowledge,
      scene,
      candidates,
    };
  },
};

/* ======================================================================
 * Phase 13 — declarative generated asset definition validation.
 *
 * The `generated` block of a WorldObjectDTO is the EXACT camelCase document
 * the backend compiler emits (`to_definition_json()` in
 * backend/app/assets/compiler.py). It is treated strictly: ANY violation
 * makes {@link validateGeneratedDefinition} return null, so the owning object
 * falls back to the neutral primitive — never a crash, never a gray void.
 * Unknown document keys are DROPPED (never a violation, never copied into
 * the typed definition) — this validator only ever returns the sanitized
 * allowlisted shape.
 *
 * Rule table (all client-enforced, deterministic):
 *  - whole document: a JSON object; unknown keys dropped
 *  - compilerVersion / schemaVersion: integers equal to the CURRENT
 *    compiler/schema versions (schema-version confusion falls back)
 *  - assetId: non-empty string (≤ 128 chars)
 *  - canonicalName: non-empty string (≤ 80 chars)
 *  - dimensions {x,y,z}: finite floats within 0.001..4 (Phase17D physical
 *    meters — a realistic 0.25 m ice pick has 0.002-0.01 m thick axes)
 *  - parts: array with 1..24 entries
 *  - per part: object; id matches ^[a-zA-Z0-9_]{1,24}$ (the backend emits
 *    part_00..part_23 — the pattern also accepts any other bounded id) and is
 *    unique; role matches ^[a-z0-9_]{1,24}$ AND must NOT start with "on" (an
 *    event-handler-shaped role like "onload"/"onclick" is rejected, mirroring
 *    the backend carve-out) NOR equal a documented blocklist of attribute /
 *    property names {style, href, src, class, method, proto}; primitive ∈
 *    {box, cylinder, sphere, plane} (capsule/extruded/etc. are rejected);
 *    color is #RRGGBB; transform.{position,rotation,scale} {x,y,z} finite
 *    floats, position |v|≤4, rotation |v|≤2π, scale within 0.001..2
 *  - visible extent (Phase17D shape-aware mirror): the PARTS' composite span
 *    must reach 0.02 m in its longest axis and may NOT collapse below 0.06 m
 *    on EVERY axis — thin-but-long objects are legal, a near-zero clump never;
 *  - parentId: null or the id of an EARLIER EXISTING part; parent chains ≤ 2 deep
 *  - hitbox: {scale:{x,y,z}} — finite floats within 0.15..10 (HITBOX_MIN floors
 *    the pick box so thin objects stay directly clickable; the backend's
 *    HITBOX_VISIBLE_MAX_RATIO=2.0 cap on the derived box stays in force there)
 * ==================================================================== */

/** Current generated-definition compiler version (mirror of the backend constant). */
export const GENERATED_COMPILER_VERSION = 1;
/** Current generated-definition schema version (mirror of the backend constant). */
export const GENERATED_SCHEMA_VERSION = 1;

/** Maximum number of parts per definition (backend MAX_PARTS mirror). */
export const GENERATED_MAX_PARTS = 24;
/**
 * Dimension bounds (backend DIMENSION_MIN/MAX mirror). DEF-079: the Phase17D
 * physical-meter contract lowered the floor from 0.05 to 0.001 (1 mm) — a
 * realistic 0.25 m ice pick has 0.002-0.01 m thick axes that Hermes-class
 * models naturally emit, and the old 0.05 floor forced every generated object
 * into an unrealistic >= 5 cm slab AND dropped legal thin geometry in the
 * browser. Near-zero/collapsed geometry is still rejected by the Phase17D
 * visible-extent gate below — never by a coarse per-axis floor.
 */
export const GENERATED_DIMENSION_MIN = 0.001;
export const GENERATED_DIMENSION_MAX = 4.0;
/** Part position bound (backend MAX_POSITION_BOUND mirror). */
export const GENERATED_POSITION_BOUND = 4.0;
/** Part rotation bound (backend MAX_ROTATION_BOUND mirror). */
export const GENERATED_ROTATION_BOUND = 2 * Math.PI;
/**
 * Part scale bounds (backend MIN/MAX_PART_SCALE mirror). The SAME Phase17D
 * floor as the dimension bound: part scales are physical METERS (the compiler
 * copies them verbatim into the render definition), so 0.001 m thin features
 * are legal. The near-zero gate is the visible-extent check below, not this
 * bound — 0.0 / 0.0005 / non-finite values are still rejected here.
 */
export const GENERATED_SCALE_MIN = 0.001;
export const GENERATED_SCALE_MAX = 2.0;
/**
 * Hitbox bounds (backend HITBOX_MIN/MAX mirror): the derived picking extent is
 * always min-clamped to HITBOX_MIN (0.15) so thin/small objects stay directly
 * clickable, and capped at HITBOX_MAX. The backend ALSO caps the derived box at
 * the visible span * HITBOX_VISIBLE_MAX_RATIO — that derivation-time cap stays
 * in force on the backend; this client gate keeps validating the published
 * [0.15..10] bounds.
 */
export const GENERATED_HITBOX_MIN = 0.15;
export const GENERATED_HITBOX_MAX = 10.0;
/** Backend derivation constant (documented mirror; the client gate does NOT
 * re-derive hitboxes — the cap is enforced by the backend compiler). */
export const GENERATED_HITBOX_VISIBLE_MAX_RATIO = 2.0;
/**
 * Phase17D shape-aware visible-extent gate (backend geometry_quality.py
 * MIN_VISIBLE_EXTENT / MIN_VISIBLE_AXIS mirror): the PARTS' composite span
 * must reach >= 0.02 m in its longest axis, and it may NOT collapse below
 * 0.06 m on EVERY axis. Thin-but-long objects (ice picks, letter openers,
 * blades) are legal — one or two thin axes are physically realistic; only
 * omnidirectional near-zero collapse is rejected.
 */
export const GENERATED_VISIBLE_EXTENT_MIN = 0.02;
export const GENERATED_VISIBLE_AXIS_MIN = 0.06;
/** canonicalName cap (backend MAX_CANONICAL_NAME_LENGTH mirror). */
const GENERATED_NAME_MAX = 80;
/** assetId defensive length cap (the backend grammar is far shorter). */
const GENERATED_ASSET_ID_MAX = 128;

const GENERATED_PRIMITIVES = new Set<GeneratedPrimitiveKind>([
  "box",
  "cylinder",
  "sphere",
  "plane",
]);
const GENERATED_PART_ID_PATTERN = /^[a-zA-Z0-9_]{1,24}$/;
const GENERATED_ROLE_PATTERN = /^[a-z0-9_]{1,24}$/;
const GENERATED_COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;
const GENERATED_VEC_AXES: readonly string[] = ["x", "y", "z"];

/**
 * DEF-070 — the documented part-role blocklist (mirror depth for the backend
 * event-handler carve-out). A role that starts with "on" (event-handler shaped)
 * OR equals one of these attribute/property-looking names is rejected, so a
 * spec can never smuggle a handler or DOM-attribute-shaped role through.
 */
export const GENERATED_BLOCKLISTED_ROLES: readonly string[] = [
  "style",
  "href",
  "src",
  "class",
  "method",
  "proto",
];

/** Plain object (not null, not an array) — the only value shape this parser accepts. */
function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Deterministic collected issue strings (sorted + deduped at the end). */
class GeneratedIssueCollector {
  private readonly issues: string[] = [];

  add(issue: string): void {
    this.issues.push(issue);
  }

  /** Deterministic sorted unique issue strings (JSON-stability for tests). */
  sorted(): string[] {
    return [...new Set(this.issues)].sort();
  }
}

/** One {x,y,z} vector: exact keys not required — only x/y/z are read; any
 * other key is dropped (never a violation). Each axis must be a FINITE number
 * within [low, high]. */
function generatedVecIssues(
  value: unknown,
  where: string,
  low: number,
  high: number,
  out: GeneratedIssueCollector,
): void {
  if (!isPlainRecord(value)) {
    out.add(`${where} must be an object {x, y, z}`);
    return;
  }
  for (const axis of GENERATED_VEC_AXES) {
    const axisValue = value[axis];
    if (typeof axisValue !== "number" || !Number.isFinite(axisValue)) {
      out.add(`${where}.${axis} must be a finite number`);
    } else if (axisValue < low || axisValue > high) {
      out.add(`${where}.${axis} must be within [${low}, ${high}]`);
    }
  }
}

/** Extract a finite numeric {x,y,z} triple (null when not a usable vector). */
function generatedNumericVec(value: unknown): [number, number, number] | null {
  if (!isPlainRecord(value)) return null;
  const x = value.x;
  const y = value.y;
  const z = value.z;
  if (typeof x !== "number" || !Number.isFinite(x)) return null;
  if (typeof y !== "number" || !Number.isFinite(y)) return null;
  if (typeof z !== "number" || !Number.isFinite(z)) return null;
  return [x, y, z];
}

/** One part box usable for the composite-span computation (finite numbers). */
interface GeneratedPartBox {
  position: [number, number, number];
  scale: [number, number, number];
  parentId: unknown;
}

/**
 * Best-effort composite visible span (metres) of a definition's PART boxes in
 * the object's world frame — an EXACT mirror of the backend Phase17D inputs
 * (geometry_quality._world_positions + _composite_metrics): each part
 * contributes [position ± scale/2] per axis after parent-unwind, and the
 * per-axis span is max(hi) - min(lo) over ALL parts.
 *
 * Returns null when NO part yields a finite numeric box (such a definition is
 * already rejected by structural issues). Out-of-range-but-finite values are
 * still usable here — a definition that violates a bound is rejected anyway;
 * only the deterministic visible-extent verdict of well-formed parts matters.
 */
function generatedCompositeSpan(parts: readonly unknown[]): [number, number, number] | null {
  const byId = new Map<string, GeneratedPartBox>();
  for (const part of parts) {
    if (!isPlainRecord(part) || typeof part.id !== "string") continue;
    if (!isPlainRecord(part.transform)) continue;
    const position = generatedNumericVec(part.transform.position);
    const scale = generatedNumericVec(part.transform.scale);
    if (position === null || scale === null) continue;
    byId.set(part.id, { position, scale, parentId: part.parentId });
  }
  if (byId.size === 0) return null;

  const mins: [number, number, number] = [Infinity, Infinity, Infinity];
  const maxs: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (const part of parts) {
    if (!isPlainRecord(part) || typeof part.id !== "string") continue;
    if (!isPlainRecord(part.transform)) continue;
    const position = generatedNumericVec(part.transform.position);
    const scale = generatedNumericVec(part.transform.scale);
    if (position === null || scale === null) continue;
    // Parent-unwind: world position = own position + every ancestor position
    // (translation-only inheritance; the <= 2 hop chain is validated elsewhere).
    let wx = position[0];
    let wy = position[1];
    let wz = position[2];
    const seen = new Set<string>([part.id]);
    let parentId: unknown = part.parentId;
    while (typeof parentId === "string" && !seen.has(parentId)) {
      const parent = byId.get(parentId);
      if (parent === undefined) break;
      seen.add(parentId);
      wx += parent.position[0];
      wy += parent.position[1];
      wz += parent.position[2];
      parentId = parent.parentId;
    }
    mins[0] = Math.min(mins[0], wx - scale[0] / 2);
    maxs[0] = Math.max(maxs[0], wx + scale[0] / 2);
    mins[1] = Math.min(mins[1], wy - scale[1] / 2);
    maxs[1] = Math.max(maxs[1], wy + scale[1] / 2);
    mins[2] = Math.min(mins[2], wz - scale[2] / 2);
    maxs[2] = Math.max(maxs[2], wz + scale[2] / 2);
  }
  if (!Number.isFinite(mins[0] + mins[1] + mins[2] + maxs[0] + maxs[1] + maxs[2])) {
    return null;
  }
  return [maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]];
}

/**
 * Deterministic, sorted issue strings of ONE raw generated definition
 * document (empty array == valid). Unknown document keys are silently ignored
 * (dropped); every enumerated rule above is enforced strictly. Never throws.
 */
export function generatedDefinitionIssues(raw: unknown): readonly string[] {
  const out = new GeneratedIssueCollector();
  if (!isPlainRecord(raw)) {
    out.add("generated must be an object");
    return out.sorted();
  }

  for (const key of ["compilerVersion", "schemaVersion"]) {
    const value = raw[key];
    if (typeof value !== "number" || !Number.isInteger(value)) {
      out.add(`generated.${key} must be an integer`);
    }
  }
  if (raw.compilerVersion !== GENERATED_COMPILER_VERSION) {
    out.add(
      `generated.compilerVersion must be ${GENERATED_COMPILER_VERSION} (schema-version confusion is rejected)`,
    );
  }
  if (raw.schemaVersion !== GENERATED_SCHEMA_VERSION) {
    out.add(
      `generated.schemaVersion must be ${GENERATED_SCHEMA_VERSION} (schema-version confusion is rejected)`,
    );
  }

  const assetId = raw.assetId;
  if (typeof assetId !== "string" || assetId.length === 0) {
    out.add("generated.assetId must be a non-empty string");
  } else if (assetId.length > GENERATED_ASSET_ID_MAX) {
    out.add(`generated.assetId exceeds ${GENERATED_ASSET_ID_MAX} characters`);
  }

  const canonicalName = raw.canonicalName;
  if (typeof canonicalName !== "string" || canonicalName.length === 0) {
    out.add("generated.canonicalName must be a non-empty string");
  } else if (canonicalName.length > GENERATED_NAME_MAX) {
    out.add(`generated.canonicalName exceeds ${GENERATED_NAME_MAX} characters`);
  }

  generatedVecIssues(
    raw.dimensions,
    "generated.dimensions",
    GENERATED_DIMENSION_MIN,
    GENERATED_DIMENSION_MAX,
    out,
  );

  const parts = raw.parts;
  if (!Array.isArray(parts)) {
    out.add("generated.parts must be an array");
  } else {
    if (parts.length === 0) {
      out.add("generated.parts must contain at least 1 part");
    }
    if (parts.length > GENERATED_MAX_PARTS) {
      out.add(`generated.parts exceeds the maximum of ${GENERATED_MAX_PARTS} parts`);
    }
    const idToIndex = new Map<string, number>();
    parts.forEach((part, index) => {
      const where = `generated.parts[${index}]`;
      if (!isPlainRecord(part)) {
        out.add(`${where} must be an object`);
        return;
      }
      const partId = part.id;
      if (typeof partId !== "string" || !GENERATED_PART_ID_PATTERN.test(partId)) {
        out.add(`${where}.id must match ^[a-zA-Z0-9_]{1,24}$`);
      } else if (idToIndex.has(partId)) {
        out.add(`${where}.id ${partId} is a duplicate part id`);
      } else {
        idToIndex.set(partId, index);
      }
      const role = part.role;
      if (typeof role !== "string" || !GENERATED_ROLE_PATTERN.test(role)) {
        out.add(`${where}.role must match ^[a-z0-9_]{1,24}$`);
      } else if (role.startsWith("on")) {
        // DEF-070: mirror the backend carve-out (^on[a-z_]+$ plus the bare
        // "on" itself) — an event-handler-shaped role is rejected, never
        // accepted as inert metadata.
        out.add(`${where}.role ${role} starts with "on" (event-handler shaped) and is rejected`);
      } else if (GENERATED_BLOCKLISTED_ROLES.includes(role)) {
        out.add(
          `${where}.role ${role} is a blocklisted attribute/property name and is rejected`,
        );
      }
      const primitive = part.primitive;
      if (typeof primitive !== "string" || !GENERATED_PRIMITIVES.has(primitive as GeneratedPrimitiveKind)) {
        out.add(`${where}.primitive must be one of box, cylinder, sphere, plane`);
      }
      const color = part.color;
      if (typeof color !== "string" || !GENERATED_COLOR_PATTERN.test(color)) {
        out.add(`${where}.color must be a #RRGGBB hex color`);
      }
      const parentId = part.parentId;
      if (parentId !== null && (typeof parentId !== "string" || parentId.length === 0)) {
        out.add(`${where}.parentId must be a string or null`);
      }
      const transform = part.transform;
      if (!isPlainRecord(transform)) {
        out.add(`${where}.transform must be an object {position, rotation, scale}`);
      } else {
        generatedVecIssues(
          transform.position,
          `${where}.transform.position`,
          -GENERATED_POSITION_BOUND,
          GENERATED_POSITION_BOUND,
          out,
        );
        generatedVecIssues(
          transform.rotation,
          `${where}.transform.rotation`,
          -GENERATED_ROTATION_BOUND,
          GENERATED_ROTATION_BOUND,
          out,
        );
        generatedVecIssues(
          transform.scale,
          `${where}.transform.scale`,
          GENERATED_SCALE_MIN,
          GENERATED_SCALE_MAX,
          out,
        );
      }
    });
    // parentId must reference an EARLIER EXISTING part (deterministic, checked
    // after every id is collected so unknown parents are flagged once here).
    // A forward, self or dangling reference is a violation.
    parts.forEach((part, index) => {
      if (!isPlainRecord(part) || part.parentId === null || typeof part.parentId !== "string") return;
      const parentIndex = idToIndex.get(part.parentId);
      if (parentIndex === undefined) {
        out.add(`generated.parts[${index}].parentId ${part.parentId} references an unknown part id`);
      } else if (parentIndex >= index) {
        out.add(`generated.parts[${index}].parentId must reference an EARLIER part`);
      }
    });
    // Max nesting depth: every part's ancestor chain is ≤ 2 hops (a depth-3
    // chain such as base->stem->cup->handle is a violation).
    parts.forEach((part, index) => {
      if (!isPlainRecord(part) || typeof part.id !== "string") return;
      let depth = 0;
      let probe: Record<string, unknown> = part;
      while (
        isPlainRecord(probe) &&
        probe.parentId !== null &&
        typeof probe.parentId === "string"
      ) {
        depth += 1;
        if (depth > GENERATED_MAX_PARENT_DEPTH) {
          out.add(
            `generated.parts[${index}].parent chain of ${part.id} exceeds the maximum nesting depth of ${GENERATED_MAX_PARENT_DEPTH}`,
          );
          break;
        }
        const parentIndex = idToIndex.get(probe.parentId);
        if (parentIndex === undefined || parentIndex >= index) break;
        const parent = parts[parentIndex];
        if (!isPlainRecord(parent)) break;
        probe = parent;
      }
    });

    // Phase17D shape-aware visible-extent gate (DEF-079): the physical 0.001 m
    // floor must NEVER let a near-zero / all-axes-collapsed clump through — the
    // PARTS' composite span has to reach MIN_VISIBLE_EXTENT (0.02 m) in its
    // longest axis and may not collapse below MIN_VISIBLE_AXIS (0.06 m) on
    // EVERY axis. Exact mirror of the backend geometry_quality.py 4.8 rule:
    // thin-but-long objects (ice picks, letter openers, blades) pass; a 5 mm
    // speck or a same-origin 5 cm clump never does.
    const visibleSpan = generatedCompositeSpan(parts);
    if (visibleSpan !== null) {
      const largest = Math.max(visibleSpan[0], visibleSpan[1], visibleSpan[2]);
      if (
        largest < GENERATED_VISIBLE_EXTENT_MIN ||
        (visibleSpan[0] < GENERATED_VISIBLE_AXIS_MIN &&
          visibleSpan[1] < GENERATED_VISIBLE_AXIS_MIN &&
          visibleSpan[2] < GENERATED_VISIBLE_AXIS_MIN)
      ) {
        out.add(
          `generated.visibleExtent: the object's visible geometry is too small or collapsed ` +
            `(largest span ${largest.toFixed(4)} m; spans ${visibleSpan[0].toFixed(4)}x${visibleSpan[1].toFixed(4)}x${visibleSpan[2].toFixed(4)} m) ` +
            `— the longest span must be >= ${GENERATED_VISIBLE_EXTENT_MIN} m or at least one axis must ` +
            `reach ${GENERATED_VISIBLE_AXIS_MIN} m (Phase17D shape-aware visible-extent gate)`,
        );
      }
    }
  }

  const hitbox = raw.hitbox;
  if (!isPlainRecord(hitbox)) {
    out.add("generated.hitbox must be an object {scale}");
  } else {
    generatedVecIssues(
      hitbox.scale,
      "generated.hitbox.scale",
      GENERATED_HITBOX_MIN,
      GENERATED_HITBOX_MAX,
      out,
    );
  }

  return out.sorted();
}

/** Max parent chain depth (backend nesting depth rule mirror). */
const GENERATED_MAX_PARENT_DEPTH = 2;

/** Copy a validated {x,y,z} object into the typed Vec3 shape (unknown keys dropped). */
function typedVec(value: unknown): GeneratedVec3DTO {
  const record = value as Record<string, number>;
  return { x: record.x, y: record.y, z: record.z };
}

/** Copy one validated part into the typed shape (unknown keys dropped). */
function typedPart(value: unknown): GeneratedPartDTO {
  const record = value as Record<string, unknown>;
  const transform = record.transform as Record<string, unknown>;
  return {
    id: record.id as string,
    role: record.role as string,
    primitive: record.primitive as GeneratedPrimitiveKind,
    transform: {
      position: typedVec(transform.position),
      rotation: typedVec(transform.rotation),
      scale: typedVec(transform.scale),
    },
    color: record.color as string,
    parentId: (record.parentId as string | null) ?? null,
  };
}

/**
 * Strict Phase 13 gate: validates one raw `generated` block and returns the
 * sanitized typed definition (unknown document keys dropped) or NULL on ANY
 * violation. Null callers treat the object as an unknown asset and render the
 * neutral fallback primitive — never a crash, never a gray void, never a
 * partially-trusted definition.
 */
export function validateGeneratedDefinition(raw: unknown): GeneratedAssetDefinition | null {
  if (generatedDefinitionIssues(raw).length > 0) return null;
  const record = raw as Record<string, unknown>;
  const parts = record.parts as unknown[];
  const hitbox = record.hitbox as Record<string, unknown>;
  return {
    compilerVersion: record.compilerVersion as number,
    schemaVersion: record.schemaVersion as number,
    assetId: record.assetId as string,
    canonicalName: record.canonicalName as string,
    dimensions: typedVec(record.dimensions),
    parts: parts.map((part) => typedPart(part)),
    hitbox: { scale: typedVec(hitbox.scale) },
  };
}