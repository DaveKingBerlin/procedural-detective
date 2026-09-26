import type {
  EvidenceReadResultDTO,
  WitnessObservationDTO,
  WitnessQuestionType,
  WitnessStatementDTO,
} from "../api/types";
import { compactTimeOf } from "../evidence/renderers/shared";

/**
 * Phase 23 — witness interview domain model (pure, deterministic, DOM-free).
 *
 * This module owns the CLOSED interview vocabulary and the safe coercion of
 * the interview response crossing the trust boundary:
 *   - the six frozen question types + their human labels (Phase23 §4/§19);
 *   - the asked-statement record that feeds the interview panel AND the
 *     Detective Notebook "Witness statements" group;
 *   - `parseWitnessInterviewResponse` — a NEVER-THROWS safe parser that
 *     funnels every untrusted field through text coercion + length bounds
 *     (hostile names/statements stay literal strings, are truncated at the
 *     documented caps, and can never become DOM/HTML — the renderers output
 *     them through React's default string escaping only).
 *
 * Everything here is deterministic: the same payload always yields the same
 * coerced model, so reloads and re-asks stay byte-stable.
 */

/** The six frozen interview question types, in the panel's display order. */
export const WITNESS_QUESTION_ORDER: readonly WitnessQuestionType[] = Object.freeze([
  "OBSERVATION",
  "TIME",
  "SOUND",
  "PERSON",
  "OBJECT",
  "LOCATION",
]);

/** Human question labels — the ONLY labels the UI ever shows (never the enum token). */
export const WITNESS_QUESTION_LABELS: Readonly<Record<WitnessQuestionType, string>> = Object.freeze({
  OBSERVATION: "What did you see?",
  TIME: "When were you there?",
  SOUND: "Did you hear anything?",
  PERSON: "Did you notice anyone?",
  OBJECT: "Did you notice any unusual objects?",
  LOCATION: "Where were you?",
});

/** Closed-guard: true ONLY for the six frozen question types. Hostile values
 *  (arbitrary strings, "__proto__", "") are never coerced into the enum. */
export function isWitnessQuestionType(value: unknown): value is WitnessQuestionType {
  return typeof value === "string" && WITNESS_QUESTION_ORDER.includes(value as WitnessQuestionType);
}

/** Human label for a closed question type (fallback for hostile/unknown input). */
export function witnessQuestionLabel(questionType: unknown): string {
  return isWitnessQuestionType(questionType) ? WITNESS_QUESTION_LABELS[questionType] : "Question";
}

/** Stable per-(witness, question) dedupe key (bookkeeping only, never rendered). */
export function witnessQuestionKey(witnessId: string, questionType: WitnessQuestionType): string {
  return `${witnessId}\u0000${questionType}`;
}

/* ======================================================================
 * Bounded-string policy (Phase23 §47) — every untrusted witness string is
 * capped at a documented maximum when crossing into model/DOM territory.
 * The caps are generous (real generated statements are far shorter); they
 * exist so an adversarial payload can never force unbounded DOM work.
 * ==================================================================== */

/** Max rendered witness display-name length. */
export const MAX_WITNESS_DISPLAY_NAME = 120;
/** Max rendered statement summary length. */
export const MAX_WITNESS_SUMMARY_LENGTH = 2000;
/** Max rendered single observation length. */
export const MAX_WITNESS_OBSERVATION_LENGTH = 600;

/** Coerce an arbitrary payload value to plain text; hostile values stay literal. */
function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

/** True for plain non-array objects (hostile strings/arrays are not). */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Truncate to `max` characters with a trailing ellipsis (never grows input). */
export function boundedText(value: unknown, max: number): string {
  const text = asText(value).trim();
  if (text.length <= max) return text;
  const cut = text.slice(0, Math.max(0, max - 1)).trimEnd();
  return `${cut}…`;
}

/**
 * One normalized observation for rendering: `canonical` is the VERBATIM time
 * string the server sent (kept for the semantic <time dateTime="...">) and
 * `display` is the compact player-facing clock ("23:42" — Phase23 §21). A
 * missing/empty/hostile time yields canonical null + empty display (the text
 * alone renders, exactly like Phase 19G text-only).
 */
export interface ObservationTimeView {
  canonical: string | null;
  display: string;
}

export function observationTimeView(time: unknown): ObservationTimeView {
  const text = asText(time).trim();
  if (text === "") return { canonical: null, display: "" };
  return { canonical: text, display: compactTimeOf(text) };
}

/* ======================================================================
 * Asked-statement record.
 *
 * One (witness, question) the player legitimately asked. The session keeps
 * these in memory (idempotent re-ask; no duplicate button state, no
 * duplicate notebook line) and the notebook model consumes them for the
 * "Witness statements" group. Statements that DISCOVERED evidence also flow
 * into the server-derived knowledge/records, so they survive a reload.
 * ==================================================================== */

export interface AskedWitnessStatement {
  /** Semantic witness/person id (player-safe, from the bootstrap list). */
  witnessId: string;
  /** Player-safe display name (untrusted text — render as text). */
  displayName: string;
  /** The closed question the player asked. */
  questionType: WitnessQuestionType;
  /** The deterministic player-safe statement the server returned. */
  statement: WitnessStatementDTO;
  /** Evidence id when this question discovered evidence; null otherwise. */
  evidenceId: string | null;
}

/**
 * Never-throws safe parser for the interview response. Every untrusted field
 * is coerced to a bounded literal string; a hostile shape degrades to an
 * empty (but safe) statement — the UI never crashes and never injects HTML.
 */
export interface CoercedWitnessInterview {
  witnessId: string;
  displayName: string;
  /** The server's echoed question type, or null when not a closed value. */
  questionType: WitnessQuestionType | null;
  statement: WitnessStatementDTO;
  observationCount: number;
  discovery: WitnessInterviewDiscoveryView | null;
}

export interface WitnessInterviewDiscoveryView {
  newlyDiscovered: boolean;
  record: EvidenceReadResultDTO | null;
}

/** True when the raw payload LOOKS like an interview response we can read. */
function isInterviewPayload(value: unknown): value is Record<string, unknown> {
  return isRecord(value);
}

export function parseWitnessInterviewResponse(raw: unknown): CoercedWitnessInterview {
  if (!isInterviewPayload(raw)) {
    return {
      witnessId: "",
      displayName: "",
      questionType: null,
      statement: { summary: "", observations: [] },
      observationCount: 0,
      discovery: null,
    };
  }
  const statementRaw = isRecord(raw.statement) ? raw.statement : {};
  const rawObservations = Array.isArray(statementRaw.observations) ? statementRaw.observations : [];
  const observations: WitnessObservationDTO[] = [];
  for (const entry of rawObservations) {
    if (isRecord(entry)) {
      const time = asText(entry.time).trim();
      const text = boundedText(entry.text, MAX_WITNESS_OBSERVATION_LENGTH);
      if (text === "") continue;
      observations.push({ time: time === "" ? null : time, text });
    } else if (typeof entry === "string") {
      const text = boundedText(entry, MAX_WITNESS_OBSERVATION_LENGTH);
      if (text === "") continue;
      observations.push({ time: null, text });
    }
  }
  const summary = boundedText(statementRaw.summary, MAX_WITNESS_SUMMARY_LENGTH);
  const discoveryRaw = isRecord(raw.discovery) ? raw.discovery : null;
  let discovery: WitnessInterviewDiscoveryView | null = null;
  if (discoveryRaw !== null) {
    const recordRaw = isRecord(discoveryRaw.record) ? discoveryRaw.record : null;
    const record = recordRaw !== null ? coerceEvidenceRecord(recordRaw) : null;
    discovery = {
      newlyDiscovered: discoveryRaw.newlyDiscovered === true,
      record,
    };
  }
  return {
    witnessId: boundedText(raw.witnessId, MAX_WITNESS_DISPLAY_NAME),
    displayName: boundedText(raw.displayName, MAX_WITNESS_DISPLAY_NAME),
    questionType: isWitnessQuestionType(raw.questionType) ? raw.questionType : null,
    statement: { summary, observations },
    observationCount: observations.length,
    discovery,
  };
}

/** Coerce a raw evidence record into the safe typed shape. Every field the
 *  downstream renderers read funnels through asText there; here only the
 *  top-level allowlisted strings are normalized and the content object is
 *  kept pass-through (unknown keys simply never match a renderer field). */
function coerceEvidenceRecord(raw: Record<string, unknown>): EvidenceReadResultDTO {
  const content = isRecord(raw.content) ? raw.content : {};
  return {
    evidenceId: asText(raw.evidenceId).trim(),
    kind: asText(raw.kind).trim(),
    title: asText(raw.title).trim(),
    description: (() => {
      const value = raw.description;
      return typeof value === "string" ? value : null;
    })(),
    openedAt: asText(raw.openedAt).trim(),
    readByPlayer: true,
    content,
  };
}

/** Escape handling shared by the interview panel and the scene route. */
export function handleWitnessPanelKey(key: string): "close" | null {
  return key === "Escape" ? "close" : null;
}

/** Safe sentence-case for the human presence hint ("On scene" — never the
 *  raw enum token leaked onto the page; unknown values fall back to a fixed
 *  neutral string). */
export function witnessPresenceHint(presence: unknown): string {
  if (presence === "ON_SCENE") return "On scene";
  if (presence === "REMOTE_STATEMENT") return "Remote statement";
  return "Witness";
}