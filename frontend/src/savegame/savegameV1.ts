import type {
  AccusationCandidatesDTO,
  EvidenceRenderType,
  WitnessListEntryDTO,
  WorldObjectDTO,
} from "../api/types";
import {
  ValidationError,
  validateAccusationCandidates,
  validateWitnessListEntry,
  validateWorldObject,
} from "../scene/validation";
import { isWitnessQuestionType } from "../witness/witnessModel";
import { SavegameTimeError, parseIso8601 } from "./savegameTime";

/**
 * SavegameV1 — the strict import pipeline for a portable `.pdcase` file
 * (Phase 32, ADR-003).
 *
 * Every uploaded file is ATTACKER-CONTROLLED (Phase32 §18): the whole parse
 * is `parse -> validate -> normalize`, typed errors only, and NO raw message
 * ever surfaces to the UI (Phase32 §26). The exact document contract mirrors
 * `backend/app/services/savegame.py::project_savegame_v1` + the canonical
 * fixture `backend/tests/fixtures/savegame/v1_demo_apartment.pdcase.json`:
 *
 *   - size pre-check on the RAW text (reject > MAX_EXPORT_BYTES before any
 *     expensive parse);
 *   - JSON.parse (empty / primitive / invalid -> typed error);
 *   - strict schema validation: `format` / `formatVersion` required, unknown
 *     future versions FAIL CLOSED, required structure + types + enums +
 *     counts + string/array bounds + asset ids + known ID sets +
 *     `additionalProperties:false` at every object + duplicate-ID detection +
 *     graph reference validity;
 *   - dangerous keys (`__proto__`, `constructor`, `prototype`) are rejected;
 *   - normalization builds brand-new typed objects (never a deep-merge of
 *     imported JSON into global state) and freezes the result into an
 *     immutable {@link SavedCaseDefinition}.
 *
 * Imported strings are NEVER mounted as HTML — the player UI renders them as
 * escaped React text only (the same guarantee every existing trust-boundary
 * parser relies on).
 */

// --------------------------------------------------------------------------- //
// frozen format constants (mirror backend/app/services/savegame.py, ADR-003)
// --------------------------------------------------------------------------- //

/** The canonical JSON document type marker (Phase32 §8). */
export const SAVEGAME_FORMAT = "procedural-detective-case";
/** The ONLY supported format version; unknown versions fail closed on import. */
export const SAVEGAME_FORMAT_VERSION = 1;
/** Portable file extension (lowercase, no dot). */
export const SAVEGAME_EXTENSION = ".pdcase";
/** Preferred MIME type of the exported JSON document. */
export const SAVEGAME_MIME_TYPE = "application/vnd.procedural-detective.case+json";
/** Hard size bound of one document (ADR-003 §2): 5 MiB with headroom. */
export const MAX_EXPORT_BYTES = 5 * 1024 * 1024;

/** The closed `metadata.source` vocabulary (ADR-003 §1/§3). */
export const SAVEGAME_SOURCES: readonly string[] = Object.freeze(["generated", "demo"]);

/**
 * The SYNTHETIC playthrough/case identity a saved-case replay presents to the
 * shared player flows. Display-only: the imported `sourceCaseId` is NEVER used
 * as an authority or lookup (Phase32 §22), and the flows treat these as opaque
 * strings passed straight to the in-memory services, which ignore them.
 */
export const REPLAY_PLAYTHROUGH_ID = "saved-replay";
export const REPLAY_CASE_ID = "saved-replay";

/** The closed difficulty vocabulary of the export metadata. */
export const SAVEGAME_DIFFICULTIES: readonly string[] = Object.freeze([
  "easy",
  "medium",
  "hard",
]);

// --------------------------------------------------------------------------- //
// structural bounds (Phase32 §19)
// --------------------------------------------------------------------------- //

export const MAX_ID_LENGTH = 256;
export const MAX_SHORT_TEXT_LENGTH = 300;
export const MAX_LONG_TEXT_LENGTH = 8000;
export const MAX_TIME_TEXT_LENGTH = 64;
export const MAX_NESTING_DEPTH = 6;

export const MAX_PEOPLE = 32;
export const MAX_MOTIVES = 32;
export const MAX_OBJECTS = 64;
export const MAX_LOCATIONS = 32;
export const MAX_TRAVEL_RULES = 128;
export const MAX_PUBLIC_EVIDENCE = 128;
export const MAX_WORLD_OBJECTS = 64;
export const MAX_WITNESSES = 32;
export const MAX_CANDIDATES = 32;
export const MAX_EVIDENCE_RECORDS = 128;
export const MAX_WORLD_GRAPH_LOCATIONS = 32;
export const MAX_WORLD_GRAPH_PLACEMENTS = 64;
export const MAX_COMPOSITION_NOTES = 16;
export const MAX_JSON_DEPTH = 6;
export const MAX_CONTENT_ENTRIES = 128;
export const MAX_CONTENT_EVENTS = 64;
export const MAX_CONTENT_ROWS = 64;
export const MAX_PERSON_IDS = 16;

/** The closed evidence render-type universe (mirror of evidenceContent.ts). */
export const SAVEGAME_RENDER_TYPES: readonly string[] = Object.freeze([
  "GENERIC_TEXT",
  "ACTIVITY_LOG",
  "FORENSIC_COMPARISON",
  "MESSAGE",
  "DOCUMENT",
  "BODY_OBSERVATION",
  "TIMELINE",
]);

// --------------------------------------------------------------------------- //
// typed error — ONE kind for the whole import pipeline, with a bounded UI
// message (Phase32 §26). No raw parser/schema text is ever exposed.
// --------------------------------------------------------------------------- //

export type SavegameParseErrorKind =
  | "too-large"
  | "unreadable"
  | "invalid"
  | "unsupported-version";

/** The test/debug-only diagnostic shape of ONE schema violation (Phase32-Fix
 *  §5): ``path`` / ``reasonCode`` / ``expected`` / ``actualType``. It is NEVER
 *  rendered by the production UI (``savegameErrorMessage`` stays the only
 *  user-facing surface) and NEVER written to product telemetry — it exists so
 *  a drift between exporter and validator is diagnosable from a unit test
 *  without ever showing raw file contents or schema internals to a player. */
export interface SavegameParseDiagnostic {
  /** The exact dotted JSON path of the violating field (``""`` for
   *  whole-document failures such as invalid JSON). */
  path: string;
  /** A closed machine-readable reason code (stable, deterministic). */
  reasonCode: SavegameParseReasonCode;
  /** The human-readable allowed type / vocabulary / bound of the field. */
  expected: string;
  /** The ``typeof``-style tag of the value actually present. */
  actualType: string;
}

/** Closed diagnostic reason codes (Phase32-Fix §5); never user-facing. */
export type SavegameParseReasonCode =
  | "TYPE_MISMATCH"
  | "BOUND_EXCEEDED"
  | "COUNT_EXCEEDED"
  | "UNKNOWN_KEY"
  | "DUPLICATE_ID"
  | "REFERENCE_MISMATCH"
  | "ENUM_MISMATCH"
  | "DEPTH_EXCEEDED"
  | "NOT_OBJECT"
  | "INVALID_JSON"
  | "INVALID_TIME"
  | "UNSUPPORTED_VERSION"
  /** A present string that is EMPTY where the canonical contract requires a
   *  non-empty string (DEF-066 / ADV-32F-08). Distinct from TYPE_MISMATCH so
   *  ``expected:"string"`` / ``actualType:"string"`` no longer contradict the
   *  code. */
  | "EMPTY_STRING";

export class SavegameParseError extends Error {
  readonly kind: SavegameParseErrorKind;
  /** Test/debug-only structured diagnosis (absent when unknown). */
  readonly diagnostic?: SavegameParseDiagnostic;
  constructor(kind: SavegameParseErrorKind, message: string, diagnostic?: SavegameParseDiagnostic) {
    super(message);
    this.name = "SavegameParseError";
    this.kind = kind;
    if (diagnostic !== undefined) this.diagnostic = diagnostic;
  }
}

/** The safe bounded message for one error kind (Phase32 §26 copy). */
export function savegameErrorMessage(kind: SavegameParseErrorKind): string {
  switch (kind) {
    case "too-large":
      return "This savegame is too large to load.";
    case "unreadable":
      return "The selected savegame could not be read.";
    case "unsupported-version":
      return "This savegame was created by a newer incompatible version.";
    case "invalid":
    default:
      return "This file is not a valid Procedural Detective savegame.";
  }
}

// --------------------------------------------------------------------------- //
// exact SavegameV1 type surface (mirror of backend/services/savegame.py)
// --------------------------------------------------------------------------- //

export interface SavegameLocationV1 {
  locationId: string;
  name: string;
}

export interface SavegameMetadataV1 {
  title: string;
  difficulty: string | null;
  /** Closed source vocabulary: "generated" | "demo" (ADR-003 §1). */
  source: string;
  /** Display-only, NEVER authority/lookup (Phase32 §22). */
  sourceCaseId: string;
  environmentId: string | null;
}

/** One world-graph placement of the FULL public dossier (ADR-003 §3). */
export interface SavegameWorldGraphPlacementV1 {
  objectId: string;
  assetId: string;
  locationId: string;
  anchor: string;
  interaction: string;
  evidenceId: string | null;
}

export interface SavegameWorldGraphLocationV1 {
  locationId: string;
  template: string;
  rooms: string[];
}

export interface SavegamePersonV1 {
  personId: string;
  name: string;
  role: string;
  affordances: string[];
}

export interface SavegameMotiveV1 {
  motiveId: string;
  label: string;
  affordances: string[];
}

export interface SavegameObjectV1 {
  objectId: string;
  assetId: string;
  affordances: string[];
  /** Optional in the CANONICAL document: the backend emits this key ONLY when
   *  non-null (publication.py `_objects`); the normalized definition always
   *  carries it (explicit null for consumers). */
  subtype?: string | null;
}

export interface SavegamePublicEvidenceV1 {
  id: string;
  kind: string;
  reliability: string;
  title: string | null;
  description: string | null;
}

export interface SavegameTravelRuleV1 {
  fromLocationId: string;
  toLocationId: string;
  travelTimeSeconds: number;
}

export interface SavegameWorldGraphV1 {
  locations: SavegameWorldGraphLocationV1[];
  placements: SavegameWorldGraphPlacementV1[];
}

export interface SavegamePublicCaseSceneV1 {
  locationId: string;
  name: string;
  environmentId: string;
  environmentVersion: number;
}

/** The exact PublicCaseResponse allowlist (publication.public_case_dict_from_payload). */
export interface SavegamePublicCaseV1 {
  caseId: string;
  caseVersion: number;
  title: string;
  scene: SavegamePublicCaseSceneV1 | null;
  persons: SavegamePersonV1[];
  motives: SavegameMotiveV1[];
  objects: SavegameObjectV1[];
  locations: SavegameLocationV1[];
  travelRules: SavegameTravelRuleV1[];
  evidence: SavegamePublicEvidenceV1[];
  worldGraph: SavegameWorldGraphV1;
  compositionNotes: string[];
}

export interface SavegameSceneV1 {
  location: SavegameLocationV1;
  environmentId: string | null;
  environmentVersion: number | null;
  /** FULL-knowledge world-object projection (the honest spoiler archive). */
  worldObjects: WorldObjectDTO[];
}

export interface SavegameCandidatesV1 {
  suspects: { id: string; name: string }[];
  motives: { id: string; label: string }[];
  weapons: { id: string; assetId: string; name: string }[];
}

/** One kind-allowlisted evidence-content payload (Phase 19G render contract). */
export interface SavegameEvidenceContentV1 {
  renderType: EvidenceRenderType | null;
  summary?: string;
  comparison?: string;
  entries?: { time?: string | null; text: string }[];
  events?: { time: string; personId?: string | null; action: string }[];
  rows?: { date?: string; from?: string; to?: string; amount?: string; currency?: string; description?: string }[];
  suspicious?: boolean;
  toPersonIds?: string[];
  fromPersonId?: string;
  subject?: string;
  body?: string;
  timestamp?: string;
  speakerName?: string;
  statement?: string;
  questionType?: string;
  witnessId?: string;
  subtype?: string;
  locationId?: string;
  cameraId?: string;
}

export interface SavegameEvidenceRecordV1 {
  evidenceId: string;
  kind: string;
  reliability: string | null;
  title: string;
  description: string | null;
  content: SavegameEvidenceContentV1;
}

/** ReplayTruthV1 — the explicit minimal solution DTO (ADR-003 §3). The
 *  internal backend/domain `CaseTruth` object is NEVER serialized. */
export interface ReplayTruthV1 {
  murdererId: string;
  motiveId: string;
  weaponId: string;
  /** The canonical full ISO-8601-with-offset crime timestamp. */
  crimeTime: string;
  accusationToleranceSeconds: number;
  murdererName: string;
  motiveLabel: string;
  weaponName: string;
}

/** The exact top-level SavegameV1 document (ADR-003 §2/§3). */
export interface SavegameV1Document {
  format: string;
  formatVersion: number;
  exportedAt: string;
  case: {
    metadata: SavegameMetadataV1;
    publicCase: SavegamePublicCaseV1;
    scene: SavegameSceneV1;
    candidates: SavegameCandidatesV1;
    witnesses: WitnessListEntryDTO[];
    evidence: SavegameEvidenceRecordV1[];
    replayTruth: ReplayTruthV1;
  };
}

/** The IMMUTABLE normalized case definition a replay consumes. Every array is
 *  a frozen copy; imported JSON is never referenced by identity. */
export interface SavedCaseDefinition {
  readonly formatVersion: 1;
  readonly exportedAt: string;
  readonly metadata: SavegameMetadataV1;
  readonly publicCase: SavegamePublicCaseV1;
  readonly scene: SavegameSceneV1;
  readonly candidates: AccusationCandidatesDTO;
  readonly witnesses: readonly WitnessListEntryDTO[];
  readonly evidence: readonly SavegameEvidenceRecordV1[];
  readonly replayTruth: ReplayTruthV1;
}

// --------------------------------------------------------------------------- //
// tiny strict helpers
// --------------------------------------------------------------------------- //

/** Stable reason-code classifier over the frozen internal detail strings.
 *  Kept deterministic so tests can assert exact codes. The optional observed
 *  value disambiguates detail strings that mix a genuine type error with a
 *  bound/empty-string violation (e.g. "must be a non-empty string" fires for
 *  BOTH a non-string and an empty string; "must be an integer within [..]"
 *  fires for BOTH a non-number and an out-of-range number) — DEF-066 /
 *  ADV-32F-08: a correctly-typed out-of-range value must never be reported as
 *  TYPE_MISMATCH, and an empty string must never contradict ``actualType``.
 *  Reference-integrity detail strings begin with the entity token
 *  (``object "…" is not published``, ``murdererId is not a candidate``, …) and
 *  classify as REFERENCE_MISMATCH — DEF-067 / ADV-32F-09. */
function reasonCodeOf(detail: string, actualValue?: unknown): SavegameParseReasonCode {
  if (/^must be an array with at most|^exceeds the maximum of|^array exceeds the maximum element count/.test(detail)) {
    return "COUNT_EXCEEDED";
  }
  if (/^exceeds /.test(detail)) return "BOUND_EXCEEDED";
  if (/^must be an integer within \[/.test(detail)) {
    return typeof actualValue === "number" ? "BOUND_EXCEEDED" : "TYPE_MISMATCH";
  }
  if (/^must be a non-negative integer/.test(detail) || /^must be a positive integer or null/.test(detail)) {
    return typeof actualValue === "number" ? "BOUND_EXCEEDED" : "TYPE_MISMATCH";
  }
  if (/^must be a non-empty string$/.test(detail)) {
    return actualValue === "" ? "EMPTY_STRING" : "TYPE_MISMATCH";
  }
  if (/^must be a bounded string$/.test(detail)) {
    return typeof actualValue === "string" ? "BOUND_EXCEEDED" : "TYPE_MISMATCH";
  }
  if (/^must be a bounded non-empty string$/.test(detail)) {
    if (actualValue === "") return "EMPTY_STRING";
    return typeof actualValue === "string" ? "BOUND_EXCEEDED" : "TYPE_MISMATCH";
  }
  if (/^unknown key "/.test(detail) || /^unknown content key "/.test(detail)) return "UNKNOWN_KEY";
  if (/^duplicate id "/.test(detail)) return "DUPLICATE_ID";
  if (
    /^must be one of /.test(detail) ||
    /^must be high, medium or low/.test(detail) ||
    /^must be a closed witness question type/.test(detail)
  ) {
    return "ENUM_MISMATCH";
  }
  if (
    /^(object|location|evidence|record|suspect|motive|weapon) ".*" (is not |has no )/.test(detail) ||
    /^(murdererId|motiveId|weaponId) is not a candidate/.test(detail) ||
    /^references /.test(detail)
  ) {
    return "REFERENCE_MISMATCH";
  }
  if (/^exceeds the maximum nesting depth/.test(detail)) return "DEPTH_EXCEEDED";
  if (/^must be an object$/.test(detail)) return "NOT_OBJECT";
  if (/^must be a canonical ISO-8601-with-offset timestamp|^must be an ISO-8601 timestamp|^must be a bounded ISO-8601/.test(detail)) {
    return "INVALID_TIME";
  }
  if (/^forbidden key /.test(detail)) return "UNKNOWN_KEY";
  return "TYPE_MISMATCH";
}

/** The documented allowed-type phrase for a detail string (used to build the
 *  §5 ``expected`` field when the throwing helper did not capture one). */
function expectedOf(detail: string): string {
  if (/^must be a non-empty string$/.test(detail)) return "string";
  if (/^must be a string or null/.test(detail)) return "string|null";
  if (/^must be a string$/.test(detail)) return "string";
  if (/^must be a bounded string$/.test(detail)) return "bounded string";
  if (/^must be a non-negative integer/.test(detail)) return "integer >= 0";
  if (/^must be a positive integer or null/.test(detail)) return "integer|null";
  if (/^must be an integer within \[/.test(detail)) return detail.replace(/^must be an integer within /, "");
  if (/^must be an integer$/.test(detail)) return "integer";
  if (/^must be a boolean/.test(detail)) return "boolean";
  if (/^must be an array/.test(detail)) return "array";
  if (/^must be an object$/.test(detail)) return "object";
  if (/^must be one of /.test(detail)) return detail.replace(/^must be one of /, "");
  if (/^must be high, medium or low/.test(detail)) return "high, medium or low";
  if (/^must be a closed witness question type/.test(detail)) return "a closed witness question type";
  if (/^must be a string, null, or absent/.test(detail)) return "string|null";
  if (/^must be a bounded non-empty string or null\/absent/.test(detail)) return "string|null";
  if (/^must be a bounded non-empty string/.test(detail)) return "non-empty string";
  if (/^unknown content key /.test(detail)) return "a known content key";
  if (/^exceeds /.test(detail)) return "bounded value";
  return "document";
}

/** The ``typeof``-style tag of an observed value (§5 ``actualType``). */
export function observedType(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (typeof value === "object") return "object";
  return typeof value;
}

/** Extract the shared-parser field path from a wrapped {@link ValidationError}
 *  message (shape ``"<root>.<field> must ..."``) so a savegame diagnostic can
 *  name the exact field (e.g. ``savegame.case.witnesses[0].sceneObjectId``)
 *  instead of stopping at the entry. Returns the entry path when the message
 *  does not carry a field suffix. */
function sharedParserFieldPath(where: string, message: string): string {
  const match = /^[A-Za-z0-9_]+\.([A-Za-z0-9_.]+) must /.exec(message);
  return match !== null ? `${where}.${match[1]}` : where;
}

/** The raw value of the shared-parser field named by a wrapped message, so the
 *  diagnostic can report a real ``actualType``. */
function sharedParserFieldValue(raw: Record<string, unknown>, message: string): unknown | undefined {
  const match = /^[A-Za-z0-9_]+\.([A-Za-z0-9_]+) must /.exec(message);
  if (match === null) return undefined;
  return raw[match[1]];
}

function invalid(where: string, detail: string, actualValue?: unknown, expected?: string): never {
  // Shared-parser messages carry a "<root>.<field> must ..." prefix; strip it
  // for stable reason/expected classification (the stored message keeps the
  // original bounded text — never user-facing).
  const classified = detail.replace(/^[A-Za-z0-9_]+\.([A-Za-z0-9_.]+) must /, "must ");
  const diagnostic: SavegameParseDiagnostic = {
    path: where,
    reasonCode: reasonCodeOf(classified, actualValue),
    expected: expected ?? expectedOf(classified),
    actualType: actualValue !== undefined ? observedType(actualValue) : "unknown",
  };
  throw new SavegameParseError("invalid", `${where}: ${detail}`, diagnostic);
}

/** The §5 test/debug-only diagnostic of a thrown {@link SavegameParseError},
 *  or ``null`` for errors that carry no structured path (e.g. too-large /
 *  unreadable). NEVER render this in the product UI and never send it to
 *  product telemetry — it is a unit-test diagnostic only. */
export function savegameParseDiagnostic(error: SavegameParseError): SavegameParseDiagnostic | null {
  if (error.diagnostic !== undefined) return error.diagnostic;
  if (error.kind !== "invalid") return null;
  const separator = error.message.indexOf(": ");
  if (separator <= 0) {
    // Whole-document failures (invalid JSON / not an object) have no path.
    return {
      path: "",
      reasonCode: /^the document is not valid JSON$/.test(error.message)
        ? "INVALID_JSON"
        : /^the savegame must be a JSON object$/.test(error.message)
          ? "NOT_OBJECT"
          : /^unsupported formatVersion$/.test(error.message)
            ? "UNSUPPORTED_VERSION"
            : "TYPE_MISMATCH",
      expected: expectedOf(error.message),
      actualType: "unknown",
    };
  }
  const path = error.message.slice(0, separator);
  const detail = error.message.slice(separator + 2);
  return { path, reasonCode: reasonCodeOf(detail), expected: expectedOf(detail), actualType: "unknown" };
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const DANGEROUS_KEYS: readonly string[] = ["__proto__", "constructor", "prototype"];

function assertNoDangerousKeys(record: Record<string, unknown>, where: string): void {
  for (const key of DANGEROUS_KEYS) {
    if (Object.prototype.hasOwnProperty.call(record, key)) {
      invalid(where, `forbidden key "${key}"`);
    }
  }
}

/** Recursively scan a sub-document for the dangerous key set at ANY depth
 *  (DEF-048 / ADV-32F-02): a hostile file may hide ``__proto__`` /
 *  ``constructor`` / ``prototype`` inside a nested block (e.g. a generated
 *  definition's ``parts``). Every other schema layer rejects these keys; the
 *  ``generated`` sub-document is allowlist-parsed into a fresh typed object
 *  (which drops unknown keys), so without this scan the dangerous key would be
 *  silently discarded instead of fail-closed on import. */
function assertNoDangerousKeysDeep(value: unknown, where: string): void {
  if (Array.isArray(value)) {
    for (const entry of value) {
      assertNoDangerousKeysDeep(entry, where);
    }
    return;
  }
  if (isPlainRecord(value)) {
    assertNoDangerousKeys(value, where);
    for (const key of Object.keys(value)) {
      assertNoDangerousKeysDeep(value[key], where);
    }
  }
}

/** `additionalProperties:false` per object (ADR-003 §3): unknown keys are a
 *  schema violation, never silently dropped. */
function assertOnlyKeys(record: Record<string, unknown>, allowed: readonly string[], where: string): void {
  assertNoDangerousKeys(record, where);
  for (const key of Object.keys(record)) {
    if (!allowed.includes(key)) {
      invalid(where, `unknown key "${key}"`);
    }
  }
}

function requireRecord(value: unknown, where: string): Record<string, unknown> {
  if (!isPlainRecord(value)) {
    invalid(where, "must be an object");
  }
  assertNoDangerousKeys(value, where);
  return value;
}

function requireString(record: Record<string, unknown>, field: string, where: string): string {
  const value = record[field];
  if (typeof value !== "string" || value === "") {
    invalid(`${where}.${field}`, "must be a non-empty string", value, "string");
  }
  return value;
}

function requireNullableString(record: Record<string, unknown>, field: string, where: string): string | null {
  const value = record[field];
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    invalid(`${where}.${field}`, "must be a string or null", value, "string|null");
  }
  return value;
}

function requireBoundedString(record: Record<string, unknown>, field: string, where: string, max: number): string {
  const value = requireString(record, field, where);
  if (value.length > max) {
    invalid(`${where}.${field}`, `exceeds ${max} characters`, value, `string (<= ${max})`);
  }
  return value;
}

function requireBoundedNullableString(
  record: Record<string, unknown>,
  field: string,
  where: string,
  max: number,
): string | null {
  const value = requireNullableString(record, field, where);
  if (value !== null && value.length > max) {
    invalid(`${where}.${field}`, `exceeds ${max} characters`, value, `string (<= ${max})`);
  }
  return value;
}

/** The canonical world-object ``subtype`` vocabulary (backend
 *  ``ObjectSpec.subtype`` — "None or a non-empty string", DEF-068 /
 *  ADV-32F-10): absent/null OR a bounded NON-EMPTY string. The shared
 *  live-game parser accepts any string here; the SAVEGAME-IMPORT path
 *  tightens the value to the backend-authorsable contract so a non-canonical
 *  ``""`` / >300-char subtype never enters a normalized archive. */
function requireBoundedNullableNonEmptyString(
  record: Record<string, unknown>,
  field: string,
  where: string,
  max: number,
): string | null {
  const value = record[field];
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") {
    invalid(`${where}.${field}`, "must be a string or null", value, "string|null");
  }
  if (value === "") {
    invalid(`${where}.${field}`, "must be a non-empty string", value, "string");
  }
  if (value.length > max) {
    invalid(`${where}.${field}`, `exceeds ${max} characters`, value, `string (<= ${max})`);
  }
  return value;
}

function requireOptionalBoundedString(
  record: Record<string, unknown>,
  field: string,
  where: string,
  max: number,
): string | undefined {
  const value = record[field];
  if (value === undefined) return undefined;
  if (typeof value !== "string" || value === "") {
    invalid(`${where}.${field}`, "must be a non-empty string", value, "string");
  }
  if (value.length > max) {
    invalid(`${where}.${field}`, `exceeds ${max} characters`, value, `string (<= ${max})`);
  }
  return value;
}

function requireNonNegativeInteger(record: Record<string, unknown>, field: string, where: string): number {
  const value = record[field];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    invalid(`${where}.${field}`, "must be a non-negative integer", value, "integer >= 0");
  }
  return value;
}

function requireBoundedInteger(
  record: Record<string, unknown>,
  field: string,
  where: string,
  min: number,
  max: number,
): number {
  const value = record[field];
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    invalid(`${where}.${field}`, `must be an integer within [${min}, ${max}]`, value, `integer in [${min}, ${max}]`);
  }
  return value;
}

function requireStringArray(record: Record<string, unknown>, field: string, where: string, maxCount: number): string[] {
  const value = record[field];
  if (!Array.isArray(value) || value.length > maxCount) {
    invalid(`${where}.${field}`, `must be an array with at most ${maxCount} entries`, value, `array (<= ${maxCount})`);
  }
  return value.map((entry, index) => {
    if (typeof entry !== "string" || entry === "") {
      invalid(`${where}.${field}[${index}]`, "must be a non-empty string", entry, "string");
    }
    return entry;
  });
}

/** Assert an array is present and bounded, RETURNING it narrowed so callers
 *  can map/forEach without re-narrowing (the `invalid` helper never returns). */
function requireArray(value: unknown, where: string, max: number): unknown[] {
  if (!Array.isArray(value)) {
    invalid(where, "must be an array", value, "array");
  }
  if (value.length > max) {
    invalid(where, `exceeds the maximum of ${max} entries`, value, `array (<= ${max})`);
  }
  return value;
}

function requireEnum(record: Record<string, unknown>, field: string, where: string, closed: readonly string[]): string {
  const value = requireString(record, field, where);
  if (!closed.includes(value)) {
    invalid(`${where}.${field}`, `must be one of ${closed.join(", ")}`, value, `one of ${closed.join(", ")}`);
  }
  return value;
}

// --------------------------------------------------------------------------- //
// per-section strict validators
// --------------------------------------------------------------------------- //

function validateMetadata(raw: unknown): SavegameMetadataV1 {
  const record = requireRecord(raw, "case.metadata");
  assertOnlyKeys(record, ["title", "difficulty", "source", "sourceCaseId", "environmentId"], "case.metadata");
  return {
    title: requireBoundedString(record, "title", "case.metadata", MAX_SHORT_TEXT_LENGTH),
    difficulty: (() => {
      const value = requireBoundedNullableString(record, "difficulty", "case.metadata", MAX_SHORT_TEXT_LENGTH);
      if (value !== null && !SAVEGAME_DIFFICULTIES.includes(value)) {
        invalid("case.metadata.difficulty", `must be one of ${SAVEGAME_DIFFICULTIES.join(", ")} or null`);
      }
      return value;
    })(),
    source: requireEnum(record, "source", "case.metadata", SAVEGAME_SOURCES),
    sourceCaseId: requireBoundedString(record, "sourceCaseId", "case.metadata", MAX_ID_LENGTH),
    environmentId: requireBoundedNullableString(record, "environmentId", "case.metadata", MAX_ID_LENGTH),
  };
}

function validatePublicCaseLocation(raw: unknown, where: string): SavegameLocationV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["locationId", "name"], where);
  return {
    locationId: requireBoundedString(record, "locationId", where, MAX_ID_LENGTH),
    name: requireBoundedString(record, "name", where, MAX_SHORT_TEXT_LENGTH),
  };
}

function validatePublicPerson(raw: unknown, where: string): SavegamePersonV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["personId", "name", "role", "affordances"], where);
  return {
    personId: requireBoundedString(record, "personId", where, MAX_ID_LENGTH),
    name: requireBoundedString(record, "name", where, MAX_SHORT_TEXT_LENGTH),
    role: requireBoundedString(record, "role", where, MAX_SHORT_TEXT_LENGTH),
    affordances: requireStringArray(record, "affordances", where, 16),
  };
}

function validatePublicMotive(raw: unknown, where: string): SavegameMotiveV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["motiveId", "label", "affordances"], where);
  return {
    motiveId: requireBoundedString(record, "motiveId", where, MAX_ID_LENGTH),
    label: requireBoundedString(record, "label", where, MAX_LONG_TEXT_LENGTH),
    affordances: requireStringArray(record, "affordances", where, 16),
  };
}

function validatePublicObject(raw: unknown, where: string): SavegameObjectV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["objectId", "assetId", "affordances", "subtype"], where);
  return {
    objectId: requireBoundedString(record, "objectId", where, MAX_ID_LENGTH),
    assetId: requireBoundedString(record, "assetId", where, MAX_ID_LENGTH),
    affordances: requireStringArray(record, "affordances", where, 16),
    subtype: requireBoundedNullableString(record, "subtype", where, MAX_SHORT_TEXT_LENGTH),
  };
}

function validatePublicEvidence(raw: unknown, where: string): SavegamePublicEvidenceV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["id", "kind", "reliability", "title", "description"], where);
  const reliability = requireString(record, "reliability", where);
  if (!["high", "medium", "low"].includes(reliability)) {
    invalid(`${where}.reliability`, "must be high, medium or low");
  }
  return {
    id: requireBoundedString(record, "id", where, MAX_ID_LENGTH),
    kind: requireBoundedString(record, "kind", where, MAX_SHORT_TEXT_LENGTH),
    reliability,
    title: requireBoundedNullableString(record, "title", where, MAX_LONG_TEXT_LENGTH),
    description: requireBoundedNullableString(record, "description", where, MAX_LONG_TEXT_LENGTH),
  };
}

function validateTravelRule(raw: unknown, where: string): SavegameTravelRuleV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["fromLocationId", "toLocationId", "travelTimeSeconds"], where);
  return {
    fromLocationId: requireBoundedString(record, "fromLocationId", where, MAX_ID_LENGTH),
    toLocationId: requireBoundedString(record, "toLocationId", where, MAX_ID_LENGTH),
    travelTimeSeconds: requireBoundedInteger(record, "travelTimeSeconds", where, 0, 3_600_000),
  };
}

function validateWorldGraphLocation(raw: unknown, where: string): SavegameWorldGraphLocationV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["locationId", "template", "rooms"], where);
  return {
    locationId: requireBoundedString(record, "locationId", where, MAX_ID_LENGTH),
    template: requireBoundedString(record, "template", where, MAX_SHORT_TEXT_LENGTH),
    rooms: requireStringArray(record, "rooms", where, 16),
  };
}

/** A string that MAY be empty (but must still be a string and bounded):
 *  DEF-062 — decorative placements carry an empty published interaction. */
function requireBoundedStringAllowEmpty(
  record: Record<string, unknown>,
  field: string,
  where: string,
  max: number,
): string {
  const value = record[field];
  if (typeof value !== "string") {
    invalid(`${where}.${field}`, "must be a string", value, "string");
  }
  if (value.length > max) {
    invalid(`${where}.${field}`, `exceeds ${max} characters`, value, `string (<= ${max})`);
  }
  return value;
}

function validatePlacement(raw: unknown, where: string): SavegameWorldGraphPlacementV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["objectId", "assetId", "locationId", "anchor", "interaction", "evidenceId"], where);
  return {
    objectId: requireBoundedString(record, "objectId", where, MAX_ID_LENGTH),
    assetId: requireBoundedString(record, "assetId", where, MAX_ID_LENGTH),
    locationId: requireBoundedString(record, "locationId", where, MAX_ID_LENGTH),
    anchor: requireBoundedString(record, "anchor", where, MAX_ID_LENGTH),
    interaction: requireBoundedStringAllowEmpty(record, "interaction", where, MAX_SHORT_TEXT_LENGTH),
    evidenceId: requireBoundedNullableString(record, "evidenceId", where, MAX_ID_LENGTH),
  };
}

function validatePublicCase(raw: unknown, where: string): SavegamePublicCaseV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(
    record,
    [
      "caseId",
      "caseVersion",
      "title",
      "scene",
      "persons",
      "motives",
      "objects",
      "locations",
      "travelRules",
      "evidence",
      "worldGraph",
      "compositionNotes",
    ],
    where,
  );

  const personsRaw = requireArray(record.persons, `${where}.persons`, MAX_PEOPLE);
  const persons = personsRaw.map((entry, index) => validatePublicPerson(entry, `${where}.persons[${index}]`));

  const motivesRaw = requireArray(record.motives, `${where}.motives`, MAX_MOTIVES);
  const motives = motivesRaw.map((entry, index) => validatePublicMotive(entry, `${where}.motives[${index}]`));

  const objectsRaw = requireArray(record.objects, `${where}.objects`, MAX_OBJECTS);
  const objects = objectsRaw.map((entry, index) => validatePublicObject(entry, `${where}.objects[${index}]`));

  const locationsRaw = requireArray(record.locations, `${where}.locations`, MAX_LOCATIONS);
  const locations = locationsRaw.map((entry, index) => validatePublicCaseLocation(entry, `${where}.locations[${index}]`));

  const travelRaw = requireArray(record.travelRules, `${where}.travelRules`, MAX_TRAVEL_RULES);
  const travelRules = travelRaw.map((entry, index) => validateTravelRule(entry, `${where}.travelRules[${index}]`));

  const evidenceRaw = requireArray(record.evidence, `${where}.evidence`, MAX_PUBLIC_EVIDENCE);
  const evidence = evidenceRaw.map((entry, index) => validatePublicEvidence(entry, `${where}.evidence[${index}]`));

  const notesRaw = requireArray(record.compositionNotes, `${where}.compositionNotes`, MAX_COMPOSITION_NOTES);
  const compositionNotes = notesRaw.map((entry, index) => {
    if (typeof entry !== "string") {
      invalid(`${where}.compositionNotes[${index}]`, "must be a string");
    }
    if (entry.length > MAX_LONG_TEXT_LENGTH) {
      invalid(`${where}.compositionNotes[${index}]`, `exceeds ${MAX_LONG_TEXT_LENGTH} characters`);
    }
    return entry;
  });

  const sceneRaw = record.scene;
  let scene: SavegamePublicCaseSceneV1 | null = null;
  if (sceneRaw !== null) {
    const sceneRecord = requireRecord(sceneRaw, `${where}.scene`);
    assertOnlyKeys(sceneRecord, ["locationId", "name", "environmentId", "environmentVersion"], `${where}.scene`);
    scene = {
      locationId: requireBoundedString(sceneRecord, "locationId", `${where}.scene`, MAX_ID_LENGTH),
      name: requireBoundedString(sceneRecord, "name", `${where}.scene`, MAX_SHORT_TEXT_LENGTH),
      environmentId: requireBoundedString(sceneRecord, "environmentId", `${where}.scene`, MAX_ID_LENGTH),
      environmentVersion: requireBoundedInteger(sceneRecord, "environmentVersion", `${where}.scene`, 1, 100_000),
    };
  }

  const wgRaw = record.worldGraph;
  const wgRecord = requireRecord(wgRaw, `${where}.worldGraph`);
  assertOnlyKeys(wgRecord, ["locations", "placements"], `${where}.worldGraph`);
  const wgLocationsRaw = requireArray(wgRecord.locations, `${where}.worldGraph.locations`, MAX_WORLD_GRAPH_LOCATIONS);
  const wgLocations = wgLocationsRaw.map((entry, index) =>
    validateWorldGraphLocation(entry, `${where}.worldGraph.locations[${index}]`),
  );
  const placementsRaw = requireArray(wgRecord.placements, `${where}.worldGraph.placements`, MAX_WORLD_GRAPH_PLACEMENTS);
  const placements = placementsRaw.map((entry, index) =>
    validatePlacement(entry, `${where}.worldGraph.placements[${index}]`),
  );

  return {
    caseId: requireBoundedString(record, "caseId", where, MAX_ID_LENGTH),
    caseVersion: requireNonNegativeInteger(record, "caseVersion", where),
    title: requireBoundedString(record, "title", where, MAX_SHORT_TEXT_LENGTH),
    scene,
    persons,
    motives,
    objects,
    locations,
    travelRules,
    evidence,
    worldGraph: { locations: wgLocations, placements },
    compositionNotes,
  };
}

/** Strict-key gate + reuse of the existing typed WorldObjectDTO parser. The
 *  saved world objects ARE the exact WorldObjectDTO wire shape. The nested
 *  ``generated`` sub-document is DEEP-scanned for dangerous keys
 *  (``__proto__`` / ``constructor`` / ``prototype``) BEFORE the shared parser
 *  runs: the parser builds an allowlisted typed object and would silently drop
 *  such a key, perfect defence-in-depth, but acceptance-with-drop is
 *  inconsistent with the rest of the import (which REJECTS them). DEF-048 /
 *  ADV-32F-02 — fail closed instead. */
function validateWorldObjectStrict(raw: unknown, where: string): WorldObjectDTO {
  const record = requireRecord(raw, where);
  assertOnlyKeys(
    record,
    [
      "objectId",
      "assetId",
      "assetType",
      "subtype",
      "locationId",
      "anchor",
      "interaction",
      "evidenceId",
      "discovered",
      "read",
      "generated",
      "displayLabel",
    ],
    where,
  );
  for (const field of ["objectId", "assetId", "assetType", "locationId", "anchor"]) {
    // Validate bounds up front (the shared parser drops nothing but does not
    // bound these ids).
    void requireBoundedString(record, field, where, MAX_ID_LENGTH);
  }
  // Phase32-Fix2 §9/§11: `interaction` is the SAME canonical field as
  // `publicCase.worldGraph.placements[].interaction` — REQUIRED string, empty
  // string allowed (decorative, DEF-062), bounded at MAX_SHORT_TEXT_LENGTH.
  // The shared live-game parser only checks it is a string (no length bound);
  // the savegame-import path tightens the bound (import-scoped only) so an
  // unbounded interaction can never enter the normalized archive (§22).
  void requireBoundedStringAllowEmpty(record, "interaction", where, MAX_SHORT_TEXT_LENGTH);
  // DEF-068 / ADV-32F-10: the canonical world-object subtype is
  // `null | bounded non-empty string (max MAX_SHORT_TEXT_LENGTH)` — the exact
  // vocabulary the backend authors. The shared live-game parser accepts any
  // string; the savegame-import path tightens it (import-scoped only).
  void requireBoundedNullableNonEmptyString(record, "subtype", where, MAX_SHORT_TEXT_LENGTH);
  if (record.generated !== undefined && record.generated !== null) {
    assertNoDangerousKeysDeep(record.generated, `${where}.generated`);
  }
  try {
    return validateWorldObject.validate(raw);
  } catch (error) {
    if (error instanceof ValidationError) {
      invalid(sharedParserFieldPath(where, error.message), error.message, sharedParserFieldValue(record, error.message));
    }
    throw error;
  }
}

function validateScene(raw: unknown, where: string): SavegameSceneV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["location", "environmentId", "environmentVersion", "worldObjects"], where);
  const locationRaw = record.location;
  const locationRecord = requireRecord(locationRaw, `${where}.location`);
  assertOnlyKeys(locationRecord, ["locationId", "name"], `${where}.location`);
  const location = {
    locationId: requireBoundedString(locationRecord, "locationId", `${where}.location`, MAX_ID_LENGTH),
    name: requireBoundedString(locationRecord, "name", `${where}.location`, MAX_SHORT_TEXT_LENGTH),
  };
  const worldObjectsRaw = requireArray(record.worldObjects, `${where}.worldObjects`, MAX_WORLD_OBJECTS);
  const worldObjects = worldObjectsRaw.map((entry, index) =>
    validateWorldObjectStrict(entry, `${where}.worldObjects[${index}]`),
  );
  return {
    location,
    environmentId: requireBoundedNullableString(record, "environmentId", where, MAX_ID_LENGTH),
    environmentVersion: (() => {
      const value = record.environmentVersion;
      if (value === null || value === undefined) return null;
      if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 100_000) {
        invalid(`${where}.environmentVersion`, "must be a positive integer or null");
      }
      return value;
    })(),
    worldObjects,
  };
}

/** Strict candidates block: reuse the shared accusation-candidate parser
 *  after bounding EVERY text field. The shared parser bounds only the ``id``
 *  and requires non-empty strings for ``name``/``label``/``assetId``; those
 *  values are rendered as React text by the accuse/reveal flows, so they are
 *  capped here with the documented candidate-field bound
 *  ``MAX_SHORT_TEXT_LENGTH`` (300) for every rendered candidate string —
 *  suspects[].name, motives[].label and weapons[].name/.assetId — with the
 *  frozen ``invalid`` rejection (DEF-047 / ADV-32F-01). Asset ids KEEP going
 *  through the sibling trusted-asset bounds everywhere else in this file
 *  (public objects/placements/world objects at ``MAX_ID_LENGTH``, generated
 *  definitions at the shared parser's 128-char cap) — the candidate weapon
 *  assetId is display/resolution data and shares the same 300-char cap as the
 *  other rendered candidate strings per the regression contract. Strict
 *  rejection per Phase32 §19 — an over-long candidate field is never accepted
 *  into the normalized definition. */
function validateCandidates(raw: unknown, where: string): SavegameCandidatesV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["suspects", "motives", "weapons"], where);
  const textBounds: Record<string, { field: string; max: number }[]> = {
    suspects: [{ field: "name", max: MAX_SHORT_TEXT_LENGTH }],
    motives: [{ field: "label", max: MAX_SHORT_TEXT_LENGTH }],
    weapons: [
      { field: "name", max: MAX_SHORT_TEXT_LENGTH },
      { field: "assetId", max: MAX_SHORT_TEXT_LENGTH },
    ],
  };
  for (const listName of ["suspects", "motives", "weapons"]) {
    const list = requireArray(record[listName], `${where}.${listName}`, MAX_CANDIDATES);
    if (!Array.isArray(list)) continue;
    list.forEach((entry, index) => {
      const entryRecord = requireRecord(entry, `${where}.${listName}[${index}]`);
      const at = `${where}.${listName}[${index}]`;
      requireBoundedString(entryRecord, "id", at, MAX_ID_LENGTH);
      for (const { field, max } of textBounds[listName]) {
        requireBoundedString(entryRecord, field, at, max);
      }
    });
  }
  try {
    const parsed = validateAccusationCandidates.validate(raw);
    return {
      suspects: parsed.suspects,
      motives: parsed.motives,
      weapons: parsed.weapons,
    };
  } catch (error) {
    if (error instanceof ValidationError) {
      invalid(sharedParserFieldPath(where, error.message), error.message, sharedParserFieldValue(record, error.message));
    }
    throw error;
  }
}

function validateWitnesses(raw: unknown, where: string): WitnessListEntryDTO[] {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["witnesses"], where);
  const list = requireArray(record.witnesses, `${where}.witnesses`, MAX_WITNESSES);
  return list.map((entry, index) => {
    if (!isPlainRecord(entry)) {
      invalid(`${where}.witnesses[${index}]`, "must be an object");
    }
    assertOnlyKeys(entry, ["witnessId", "displayName", "presence", "sceneObjectId"], `${where}.witnesses[${index}]`);
    try {
      return validateWitnessListEntry.validate(entry);
    } catch (error) {
      if (error instanceof ValidationError) {
        invalid(
          sharedParserFieldPath(`${where}.witnesses[${index}]`, error.message),
          error.message,
          sharedParserFieldValue(entry, error.message),
        );
      }
      throw error;
    }
  });
}

/** The max nesting depth of any content subtree (imported JSON is bounded:
 *  Phase19G content is closed-shape, so this tripwire never fires on a valid
 *  export — it stops a hostile deeply-nested blob before any processing). */
function assertMaxDepth(value: unknown, depth: number, where: string): void {
  if (depth > MAX_JSON_DEPTH) {
    invalid(where, "exceeds the maximum nesting depth");
  }
  if (Array.isArray(value)) {
    if (value.length > 512) {
      invalid(where, "array exceeds the maximum element count");
    }
    for (const entry of value) {
      assertMaxDepth(entry, depth + 1, where);
    }
  } else if (isPlainRecord(value)) {
    for (const key of Object.keys(value)) {
      assertMaxDepth(value[key], depth + 1, where);
    }
  }
}

function validateEvidenceContent(raw: unknown, where: string): SavegameEvidenceContentV1 {
  const record = requireRecord(raw, where);
  assertNoDangerousKeys(record, where);
  assertMaxDepth(record, 0, where);
  const allowed = new Set<string>([
    "renderType",
    "summary",
    "comparison",
    "entries",
    "events",
    "rows",
    "suspicious",
    "toPersonIds",
    "fromPersonId",
    "subject",
    "body",
    "timestamp",
    "speakerName",
    "statement",
    "questionType",
    "witnessId",
    "subtype",
    "locationId",
    "cameraId",
  ]);
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) {
      invalid(`${where}`, `unknown content key "${key}"`);
    }
  }

  const renderTypeValue = record.renderType;
  if (renderTypeValue !== undefined && renderTypeValue !== null) {
    if (typeof renderTypeValue !== "string" || !SAVEGAME_RENDER_TYPES.includes(renderTypeValue)) {
      invalid(`${where}.renderType`, `must be one of ${SAVEGAME_RENDER_TYPES.join(", ")} or null`);
    }
  }

  const content: SavegameEvidenceContentV1 = {
    renderType: renderTypeValue === null ? null : (renderTypeValue as EvidenceRenderType),
  };

  const summary = requireOptionalBoundedString(record, "summary", where, MAX_LONG_TEXT_LENGTH);
  if (summary !== undefined) content.summary = summary;
  const comparison = requireOptionalBoundedString(record, "comparison", where, MAX_LONG_TEXT_LENGTH);
  if (comparison !== undefined) content.comparison = comparison;
  const speakerName = requireOptionalBoundedString(record, "speakerName", where, MAX_SHORT_TEXT_LENGTH);
  if (speakerName !== undefined) content.speakerName = speakerName;
  const statement = requireOptionalBoundedString(record, "statement", where, MAX_LONG_TEXT_LENGTH);
  if (statement !== undefined) content.statement = statement;
  const fromPersonId = requireOptionalBoundedString(record, "fromPersonId", where, MAX_ID_LENGTH);
  if (fromPersonId !== undefined) content.fromPersonId = fromPersonId;
  const subject = requireOptionalBoundedString(record, "subject", where, MAX_SHORT_TEXT_LENGTH);
  if (subject !== undefined) content.subject = subject;
  const body = requireOptionalBoundedString(record, "body", where, MAX_LONG_TEXT_LENGTH);
  if (body !== undefined) content.body = body;
  const timestamp = requireOptionalBoundedString(record, "timestamp", where, MAX_TIME_TEXT_LENGTH);
  if (timestamp !== undefined) content.timestamp = timestamp;
  const cameraId = requireOptionalBoundedString(record, "cameraId", where, MAX_ID_LENGTH);
  if (cameraId !== undefined) content.cameraId = cameraId;
  const subtype = requireOptionalBoundedString(record, "subtype", where, MAX_SHORT_TEXT_LENGTH);
  if (subtype !== undefined) content.subtype = subtype;
  const contentLocationId = requireOptionalBoundedString(record, "locationId", where, MAX_ID_LENGTH);
  if (contentLocationId !== undefined) content.locationId = contentLocationId;
  const witnessId = requireOptionalBoundedString(record, "witnessId", where, MAX_ID_LENGTH);
  if (witnessId !== undefined) content.witnessId = witnessId;

  const questionType = record.questionType;
  if (questionType !== undefined && !isWitnessQuestionType(questionType)) {
    invalid(`${where}.questionType`, "must be a closed witness question type");
  }
  if (questionType !== undefined) content.questionType = questionType;

  if (record.suspicious !== undefined) {
    if (typeof record.suspicious !== "boolean") {
      invalid(`${where}.suspicious`, "must be a boolean");
    }
    content.suspicious = record.suspicious;
  }

  if (record.entries !== undefined) {
    const entries = record.entries;
    if (!Array.isArray(entries) || entries.length > MAX_CONTENT_ENTRIES) {
      invalid(`${where}.entries`, `must be an array with at most ${MAX_CONTENT_ENTRIES} entries`);
    }
    content.entries = entries.map((entry, index) => {
      const entryRecord = requireRecord(entry, `${where}.entries[${index}]`);
      assertOnlyKeys(entryRecord, ["time", "text"], `${where}.entries[${index}]`);
      const time = requireBoundedNullableString(entryRecord, "time", `${where}.entries[${index}]`, MAX_TIME_TEXT_LENGTH);
      const text = requireBoundedString(entryRecord, "text", `${where}.entries[${index}]`, MAX_LONG_TEXT_LENGTH);
      return { time, text };
    });
  }

  if (record.events !== undefined) {
    const events = record.events;
    if (!Array.isArray(events) || events.length > MAX_CONTENT_EVENTS) {
      invalid(`${where}.events`, `must be an array with at most ${MAX_CONTENT_EVENTS} entries`);
    }
    content.events = events.map((entry, index) => {
      const entryRecord = requireRecord(entry, `${where}.events[${index}]`);
      assertOnlyKeys(entryRecord, ["time", "personId", "action"], `${where}.events[${index}]`);
      const time = requireBoundedString(entryRecord, "time", `${where}.events[${index}]`, MAX_TIME_TEXT_LENGTH);
      const personId = requireBoundedNullableString(entryRecord, "personId", `${where}.events[${index}]`, MAX_ID_LENGTH);
      const action = requireBoundedString(entryRecord, "action", `${where}.events[${index}]`, MAX_LONG_TEXT_LENGTH);
      // DEF-049 / ADV-32F-03: mirror the backend's canonical event shape. The
      // server projection OMITS a null ``personId`` key entirely (its
      // ``_filter_list_of_mappings`` keeps only present+non-null keys), so
      // CCTV-style events without a person carry NO ``personId`` key — never
      // an explicit ``null``. Only a non-null person id is emitted, keeping a
      // re-exported server save byte-identical (when ``exportedAt`` is pinned).
      const normalizedEvent: { time: string; personId?: string; action: string } = { time, action };
      if (personId !== null) normalizedEvent.personId = personId;
      return normalizedEvent;
    });
  }

  if (record.rows !== undefined) {
    const rows = record.rows;
    if (!Array.isArray(rows) || rows.length > MAX_CONTENT_ROWS) {
      invalid(`${where}.rows`, `must be an array with at most ${MAX_CONTENT_ROWS} entries`);
    }
    const ROW_KEYS = ["date", "from", "to", "amount", "currency", "description"];
    content.rows = rows.map((entry, index) => {
      const entryRecord = requireRecord(entry, `${where}.rows[${index}]`);
      assertOnlyKeys(entryRecord, ROW_KEYS, `${where}.rows[${index}]`);
      const out: { date?: string; from?: string; to?: string; amount?: string; currency?: string; description?: string } = {};
      for (const key of ROW_KEYS) {
        const value = entryRecord[key];
        if (value === undefined) continue;
        if (typeof value !== "string" || value.length > MAX_LONG_TEXT_LENGTH) {
          invalid(`${where}.rows[${index}].${key}`, "must be a bounded string");
        }
        (out as Record<string, string>)[key] = value;
      }
      return out;
    });
  }

  if (record.toPersonIds !== undefined) {
    const rawIds = record.toPersonIds;
    if (!Array.isArray(rawIds) || rawIds.length > MAX_PERSON_IDS) {
      invalid(`${where}.toPersonIds`, `must be an array with at most ${MAX_PERSON_IDS} entries`);
    }
    content.toPersonIds = rawIds.map((entry, index) => {
      if (typeof entry !== "string" || entry === "" || entry.length > MAX_ID_LENGTH) {
        invalid(`${where}.toPersonIds[${index}]`, "must be a bounded non-empty string");
      }
      return entry;
    });
  }

  return content;
}

function validateEvidenceRecord(raw: unknown, where: string): SavegameEvidenceRecordV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(record, ["evidenceId", "kind", "reliability", "title", "description", "content"], where);
  const reliability = requireBoundedNullableString(record, "reliability", where, MAX_SHORT_TEXT_LENGTH);
  if (reliability !== null && !["high", "medium", "low"].includes(reliability)) {
    invalid(`${where}.reliability`, "must be high, medium or low");
  }
  return {
    evidenceId: requireBoundedString(record, "evidenceId", where, MAX_ID_LENGTH),
    kind: requireBoundedString(record, "kind", where, MAX_SHORT_TEXT_LENGTH),
    reliability,
    title: requireBoundedString(record, "title", where, MAX_LONG_TEXT_LENGTH),
    description: requireBoundedNullableString(record, "description", where, MAX_LONG_TEXT_LENGTH),
    content: validateEvidenceContent(record.content, `${where}.content`),
  };
}

function validateReplayTruth(raw: unknown, where: string): ReplayTruthV1 {
  const record = requireRecord(raw, where);
  assertOnlyKeys(
    record,
    [
      "murdererId",
      "motiveId",
      "weaponId",
      "crimeTime",
      "accusationToleranceSeconds",
      "murdererName",
      "motiveLabel",
      "weaponName",
    ],
    where,
  );
  const crimeTime = requireBoundedString(record, "crimeTime", where, MAX_TIME_TEXT_LENGTH);
  try {
    parseIso8601(crimeTime);
  } catch (error) {
    if (error instanceof SavegameTimeError) {
      invalid(`${where}.crimeTime`, "must be a canonical ISO-8601-with-offset timestamp");
    }
    throw error;
  }
  return {
    murdererId: requireBoundedString(record, "murdererId", where, MAX_ID_LENGTH),
    motiveId: requireBoundedString(record, "motiveId", where, MAX_ID_LENGTH),
    weaponId: requireBoundedString(record, "weaponId", where, MAX_ID_LENGTH),
    crimeTime,
    accusationToleranceSeconds: requireBoundedInteger(
      record,
      "accusationToleranceSeconds",
      where,
      0,
      86_400,
    ),
    murdererName: requireBoundedString(record, "murdererName", where, MAX_SHORT_TEXT_LENGTH),
    motiveLabel: requireBoundedString(record, "motiveLabel", where, MAX_LONG_TEXT_LENGTH),
    weaponName: requireBoundedString(record, "weaponName", where, MAX_SHORT_TEXT_LENGTH),
  };
}

// --------------------------------------------------------------------------- //
// duplicate detection + graph reference validity
// --------------------------------------------------------------------------- //

function assertUniqueIds(ids: readonly string[], where: string): void {
  const seen = new Set<string>();
  ids.forEach((id, index) => {
    if (seen.has(id)) {
      // DEF-067 / ADV-32F-09: name the EXACT duplicate entry (``[index]``) and
      // carry the offending id so the §5 diagnostic reports a real actualType.
      invalid(`${where}[${index}]`, `duplicate id "${id}"`, id, "a unique id");
    }
    seen.add(id);
  });
}

function validateReferences(pub: SavegamePublicCaseV1, scene: SavegameSceneV1, evidence: readonly SavegameEvidenceRecordV1[]): void {
  const locationIds = new Set(pub.locations.map((location) => location.locationId));
  const objectIds = new Set(pub.objects.map((obj) => obj.objectId));
  const publicEvidenceIds = new Set(pub.evidence.map((entry) => entry.id));
  const recordIds = new Set(evidence.map((record) => record.evidenceId));

  assertUniqueIds(pub.persons.map((p) => p.personId), "savegame.case.publicCase.persons");
  assertUniqueIds(pub.motives.map((m) => m.motiveId), "savegame.case.publicCase.motives");
  assertUniqueIds(pub.objects.map((o) => o.objectId), "savegame.case.publicCase.objects");
  assertUniqueIds(pub.locations.map((l) => l.locationId), "savegame.case.publicCase.locations");
  assertUniqueIds([...publicEvidenceIds], "savegame.case.publicCase.evidence");
  assertUniqueIds(pub.worldGraph.placements.map((p) => p.objectId), "savegame.case.publicCase.worldGraph.placements");

  pub.travelRules.forEach((rule, index) => {
    const at = `savegame.case.publicCase.travelRules[${index}]`;
    if (!locationIds.has(rule.fromLocationId)) {
      invalid(`${at}.fromLocationId`, "references a location that does not exist", rule.fromLocationId, "a published location id");
    }
    if (!locationIds.has(rule.toLocationId)) {
      invalid(`${at}.toLocationId`, "references a location that does not exist", rule.toLocationId, "a published location id");
    }
  });
  pub.worldGraph.placements.forEach((placement, index) => {
    const at = `savegame.case.publicCase.worldGraph.placements[${index}]`;
    if (!objectIds.has(placement.objectId)) {
      invalid(`${at}.objectId`, `object "${placement.objectId}" is not published`, placement.objectId, "a published object id");
    }
    if (!locationIds.has(placement.locationId)) {
      invalid(`${at}.locationId`, `location "${placement.locationId}" is not published`, placement.locationId, "a published location id");
    }
    if (placement.evidenceId !== null && !publicEvidenceIds.has(placement.evidenceId)) {
      invalid(`${at}.evidenceId`, `evidence "${placement.evidenceId}" is not published`, placement.evidenceId, "a published evidence id");
    }
  });
  scene.worldObjects.forEach((worldObject, index) => {
    const at = `savegame.case.scene.worldObjects[${index}]`;
    if (worldObject.evidenceId !== null && !recordIds.has(worldObject.evidenceId)) {
      invalid(`${at}.evidenceId`, `evidence "${worldObject.evidenceId}" has no read record`, worldObject.evidenceId, "an evidence id with a read record");
    }
    // The scene's world objects are one projection of the placements — every
    // object must have a published placement too (identity integrity).
    if (!pub.worldGraph.placements.some((placement) => placement.objectId === worldObject.objectId)) {
      invalid(`${at}.objectId`, `object "${worldObject.objectId}" has no world-graph placement`, worldObject.objectId, "an object id with a world-graph placement");
    }
  });
  evidence.forEach((record, index) => {
    if (!publicEvidenceIds.has(record.evidenceId)) {
      invalid(`savegame.case.evidence[${index}].evidenceId`, `record "${record.evidenceId}" is not in the public case`, record.evidenceId, "a public evidence id");
    }
  });
}

function validateCandidatesAgainstPublic(
  candidates: SavegameCandidatesV1,
  pub: SavegamePublicCaseV1,
): void {
  const personIds = new Set(pub.persons.map((p) => p.personId));
  const motiveIds = new Set(pub.motives.map((m) => m.motiveId));
  const objectIds = new Set(pub.objects.map((o) => o.objectId));
  assertUniqueIds(candidates.suspects.map((s) => s.id), "savegame.case.candidates.suspects");
  assertUniqueIds(candidates.motives.map((m) => m.id), "savegame.case.candidates.motives");
  assertUniqueIds(candidates.weapons.map((w) => w.id), "savegame.case.candidates.weapons");
  candidates.suspects.forEach((suspect, index) => {
    if (!personIds.has(suspect.id)) {
      invalid(`savegame.case.candidates.suspects[${index}].id`, `suspect "${suspect.id}" is not a published person`, suspect.id, "a published person id");
    }
  });
  candidates.motives.forEach((motive, index) => {
    if (!motiveIds.has(motive.id)) {
      invalid(`savegame.case.candidates.motives[${index}].id`, `motive "${motive.id}" is not published`, motive.id, "a published motive id");
    }
  });
  candidates.weapons.forEach((weapon, index) => {
    if (!objectIds.has(weapon.id)) {
      invalid(`savegame.case.candidates.weapons[${index}].id`, `weapon "${weapon.id}" is not a published object`, weapon.id, "a published object id");
    }
  });
}

function validateReplayTruthAgainstCandidates(
  truth: ReplayTruthV1,
  candidates: SavegameCandidatesV1,
): void {
  if (!candidates.suspects.some((s) => s.id === truth.murdererId)) {
    invalid("savegame.case.replayTruth.murdererId", "murdererId is not a candidate", truth.murdererId, "a candidate suspect id");
  }
  if (!candidates.motives.some((m) => m.id === truth.motiveId)) {
    invalid("savegame.case.replayTruth.motiveId", "motiveId is not a candidate", truth.motiveId, "a candidate motive id");
  }
  if (!candidates.weapons.some((w) => w.id === truth.weaponId)) {
    invalid("savegame.case.replayTruth.weaponId", "weaponId is not a candidate", truth.weaponId, "a candidate weapon id");
  }
}

function assertUniqueWitnesses(witnesses: readonly WitnessListEntryDTO[]): void {
  assertUniqueIds(witnesses.map((w) => w.witnessId), "savegame.case.witnesses");
}

// --------------------------------------------------------------------------- //
// the import pipeline
// --------------------------------------------------------------------------- //

/** UTF-8 byte length of a string (the backend serializes UTF-8 and sizes the
 *  export in bytes; the size precheck is performed BEFORE any JSON parse). */
export function utf8ByteLength(text: string): number {
  if (typeof TextEncoder !== "undefined") {
    return new TextEncoder().encode(text).length;
  }
  // Fallback for exotic hosts: count by code points (upper-bound-ish; never
  // under-counts multi-byte characters in a way that could admit an oversized
  // file — a real browser/Node always has TextEncoder).
  let bytes = 0;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.codePointAt(index) ?? 0;
    if (code < 0x80) bytes += 1;
    else if (code < 0x800) bytes += 2;
    else if (code < 0x10000) bytes += 3;
    else bytes += 4;
  }
  return bytes;
}

/**
 * Parse the RAW text of a `.pdcase` file into an immutable
 * {@link SavedCaseDefinition}. NEVER throws a raw exception: every failure is
 * a typed {@link SavegameParseError} with a bounded UI message.
 *
 * @param text      the raw UTF-8 decoded file text
 * @param byteLength the ACTUAL byte count of the uploaded file (the file
 *                   picker's `File.size`); defaults to the text's own UTF-8
 *                   length when omitted.
 */
export function parseSavegameV1(text: string, byteLength?: number): SavedCaseDefinition {
  const bytes = byteLength !== undefined ? byteLength : utf8ByteLength(text);
  if (bytes > MAX_EXPORT_BYTES) {
    throw new SavegameParseError("too-large", "savegame exceeds the size bound");
  }

  let document: unknown;
  try {
    document = JSON.parse(text);
  } catch {
    throw new SavegameParseError("invalid", "the document is not valid JSON");
  }

  return normalizeSavegameV1(document);
}

/** A small pure extractor used by the load-capable modules: parses an already
 *  trusted `unknown` (e.g. from a test or a JSON import) — same schema. */
export function normalizeSavegameV1(document: unknown): SavedCaseDefinition {
  if (!isPlainRecord(document)) {
    throw new SavegameParseError("invalid", "the savegame must be a JSON object");
  }
  assertNoDangerousKeys(document, "savegame");
  assertOnlyKeys(document, ["format", "formatVersion", "exportedAt", "case"], "savegame");

  const format = requireString(document, "format", "savegame");
  if (format !== SAVEGAME_FORMAT) {
    throw new SavegameParseError("invalid", "the document is not a Procedural Detective case save");
  }
  const formatVersion = document.formatVersion;
  if (typeof formatVersion !== "number" || !Number.isInteger(formatVersion)) {
    throw new SavegameParseError("invalid", "formatVersion must be an integer");
  }
  if (formatVersion !== SAVEGAME_FORMAT_VERSION) {
    // Unknown future versions FAIL CLOSED (Phase32 §27).
    throw new SavegameParseError("unsupported-version", "unsupported formatVersion");
  }

  const exportedAt = requireBoundedString(document, "exportedAt", "savegame", MAX_TIME_TEXT_LENGTH);
  try {
    parseIso8601(exportedAt);
  } catch {
    invalid("savegame.exportedAt", "must be an ISO-8601 timestamp");
  }

  const caseRaw = requireRecord(document.case, "savegame.case");
  assertOnlyKeys(caseRaw, ["metadata", "publicCase", "scene", "candidates", "witnesses", "evidence", "replayTruth"], "savegame.case");

  const metadata = validateMetadata(caseRaw.metadata);
  const publicCase = validatePublicCase(caseRaw.publicCase, "savegame.case.publicCase");
  const scene = validateScene(caseRaw.scene, "savegame.case.scene");
  const candidates = validateCandidates(caseRaw.candidates, "savegame.case.candidates");
  const witnesses = validateWitnesses({ witnesses: caseRaw.witnesses }, "savegame.case");
  const evidenceRaw = requireArray(caseRaw.evidence, "savegame.case.evidence", MAX_EVIDENCE_RECORDS);
  const evidence = evidenceRaw.map((entry, index) => validateEvidenceRecord(entry, `savegame.case.evidence[${index}]`));
  const replayTruth = validateReplayTruth(caseRaw.replayTruth, "savegame.case.replayTruth");

  // Cross-section integrity: duplicates + graph reference validity + truth
  // membership, all fail-closed.
  assertUniqueIds(scene.worldObjects.map((o) => o.objectId), "savegame.case.scene.worldObjects");
  assertUniqueIds(evidence.map((r) => r.evidenceId), "savegame.case.evidence");
  assertUniqueWitnesses(witnesses);
  validateReferences(publicCase, scene, evidence);
  validateCandidatesAgainstPublic(candidates, publicCase);
  validateReplayTruthAgainstCandidates(replayTruth, candidates);

  // The normalized definition is IMMUTABLE by construction AND at runtime:
  // every array is a fresh frozen copy and no code path ever mutates it. The
  // `Object.freeze` reads are typed `readonly` on the array fields while the
  // DTO interfaces keep the player-friendly mutable shapes — the assertion
  // documents that freeze as the canonical identity of this object.
  const definition = Object.freeze({
    formatVersion: SAVEGAME_FORMAT_VERSION,
    exportedAt,
    metadata: Object.freeze({ ...metadata }),
    publicCase: Object.freeze({
      ...publicCase,
      persons: Object.freeze(publicCase.persons),
      motives: Object.freeze(publicCase.motives),
      objects: Object.freeze(publicCase.objects),
      locations: Object.freeze(publicCase.locations),
      travelRules: Object.freeze(publicCase.travelRules),
      evidence: Object.freeze(publicCase.evidence),
      compositionNotes: Object.freeze(publicCase.compositionNotes),
      worldGraph: Object.freeze({
        locations: Object.freeze(publicCase.worldGraph.locations),
        placements: Object.freeze(publicCase.worldGraph.placements),
      }),
    }),
    scene: Object.freeze({
      location: Object.freeze({ ...scene.location }),
      environmentId: scene.environmentId,
      environmentVersion: scene.environmentVersion,
      worldObjects: Object.freeze(scene.worldObjects),
    }),
    candidates: Object.freeze({
      suspects: Object.freeze(candidates.suspects),
      motives: Object.freeze(candidates.motives),
      weapons: Object.freeze(candidates.weapons),
    }),
    witnesses: Object.freeze(witnesses),
    evidence: Object.freeze(evidence),
    replayTruth: Object.freeze({ ...replayTruth }),
  }) as unknown as SavedCaseDefinition;
  return definition;
}