import type { WitnessListEntryDTO } from "../api/types";
import {
  isWitnessQuestionType,
  parseWitnessInterviewResponse,
  witnessQuestionKey,
  type AskedWitnessStatement,
} from "./witnessModel";

/**
 * Bounded cache of witness answers the player has already seen.
 *
 * Neutral answers intentionally create no evidence/knowledge row on the
 * server. This cosmetic per-playthrough cache lets them survive a reload
 * without replaying interview requests. It never carries discovery data and
 * cannot affect evidence, the solver, accusation, or reveal.
 */
export const WITNESS_STATEMENT_KEY_PREFIX = "pd_witness_statements_v1";
const MAX_STORED_BYTES = 64 * 1024;
const MAX_OBSERVATIONS_PER_STATEMENT = 32;

export interface WitnessStatementStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function defaultStorage(): WitnessStatementStorage | null {
  try {
    if (typeof window !== "undefined" && typeof window.localStorage !== "undefined") {
      return window.localStorage;
    }
  } catch {
    // Hardened/embedded browsers may deny storage access.
  }
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function witnessStatementKey(playthroughId: string): string {
  return `${WITNESS_STATEMENT_KEY_PREFIX}:${playthroughId}`;
}

function normalizeStoredStatement(
  raw: unknown,
  witnesses: ReadonlyMap<string, WitnessListEntryDTO>,
): AskedWitnessStatement | null {
  if (!isRecord(raw)) return null;
  const witnessId = typeof raw.witnessId === "string" ? raw.witnessId : "";
  const witness = witnesses.get(witnessId);
  if (!witness || !isWitnessQuestionType(raw.questionType)) return null;

  // Apply the same literal-text coercion and bounds as a live response.
  // Current bootstrap identity wins over stale/tampered stored display names.
  const parsed = parseWitnessInterviewResponse({
    witnessId,
    displayName: witness.displayName,
    questionType: raw.questionType,
    statement: raw.statement,
    discovery: null,
  });
  return {
    witnessId,
    displayName: witness.displayName,
    questionType: raw.questionType,
    statement: {
      summary: parsed.statement.summary,
      observations: parsed.statement.observations.slice(0, MAX_OBSERVATIONS_PER_STATEMENT),
    },
    evidenceId: null,
  };
}

/** Load answers only for witnesses present in the current safe bootstrap. */
export function loadWitnessStatements(
  playthroughId: string,
  witnesses: readonly WitnessListEntryDTO[],
  storage?: WitnessStatementStorage | null,
): AskedWitnessStatement[] {
  const store = storage ?? defaultStorage();
  if (!store) return [];
  let raw: string | null;
  try {
    raw = store.getItem(witnessStatementKey(playthroughId));
  } catch {
    return [];
  }
  if (raw === null || raw.length > MAX_STORED_BYTES) return [];

  let document: unknown;
  try {
    document = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!isRecord(document) || document.version !== 1 || !Array.isArray(document.entries)) {
    return [];
  }

  const witnessMap = new Map(witnesses.map((witness) => [witness.witnessId, witness]));
  const maximumEntries = witnesses.length * 6;
  const restored = new Map<string, AskedWitnessStatement>();
  for (const entry of document.entries.slice(0, maximumEntries)) {
    const normalized = normalizeStoredStatement(entry, witnessMap);
    if (!normalized) continue;
    restored.set(witnessQuestionKey(normalized.witnessId, normalized.questionType), normalized);
  }
  return [...restored.values()];
}

/** Persist only allowlisted statement fields already shown to the player. */
export function saveWitnessStatements(
  playthroughId: string,
  witnesses: readonly WitnessListEntryDTO[],
  statements: readonly AskedWitnessStatement[],
  storage?: WitnessStatementStorage | null,
): boolean {
  const store = storage ?? defaultStorage();
  if (!store) return false;
  const witnessMap = new Map(witnesses.map((witness) => [witness.witnessId, witness]));
  const entries: Array<{ witnessId: string; questionType: string; statement: unknown }> = [];
  const seen = new Set<string>();
  for (const raw of statements) {
    const normalized = normalizeStoredStatement(raw, witnessMap);
    if (!normalized) continue;
    const key = witnessQuestionKey(normalized.witnessId, normalized.questionType);
    if (seen.has(key)) continue;
    seen.add(key);
    entries.push({
      witnessId: normalized.witnessId,
      questionType: normalized.questionType,
      statement: normalized.statement,
    });
  }
  try {
    const serialized = JSON.stringify({ version: 1, entries });
    if (serialized.length > MAX_STORED_BYTES) return false;
    store.setItem(witnessStatementKey(playthroughId), serialized);
    return true;
  } catch {
    return false;
  }
}
