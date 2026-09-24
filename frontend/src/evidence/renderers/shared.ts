import type { ReactElement } from "react";
import type { EvidenceReadResultDTO } from "../../api/types";

/**
 * Phase 19G — shared support for the closed evidence renderers.
 *
 * EVERY renderer:
 *   - treats payload values as TEXT (React's default string rendering
 *     escapes everything — no dangerouslySetInnerHTML anywhere);
 *   - is a PURE function component: the same DTO always renders the same
 *     markup (reload determinism, Phase 19G §10);
 *   - emits no focusable controls, so the panel's existing focus/close/ESC
 *     behavior (evidencePanel) and reduced-motion behavior are untouched.
 *
 * Nothing in this module generates HTML, component names or code.
 */

/** Common input every closed evidence renderer receives. */
export interface EvidenceRendererProps {
  record: EvidenceReadResultDTO;
}

/** Every closed renderer is a pure function component. */
export type EvidenceRenderer = (props: EvidenceRendererProps) => ReactElement;

/** Coerce an arbitrary payload value to plain text; hostile values stay literal. */
export function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

/** True for plain non-array objects (a hostile string/array/null is not). */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** First non-empty text among the candidates ("" when none). */
export function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = asText(value);
    if (text.trim() !== "") return text;
  }
  return "";
}

/** One normalized time-entry (activity-log / timeline item). */
export interface TimeEntryItem {
  time: string;
  text: string;
}

/** "HH:MM" -> minutes past midnight, or null when not a parseable clock time. */
export function minutesOfTime(time: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(time.trim());
  if (match === null) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

/** True when `value` is already compact clock text ("23:41" or "23:41:50"). */
export function isCompactClockTime(value: string): boolean {
  return /^\d{1,2}:\d{2}(?::\d{2})?$/.test(value);
}

/**
 * Phase 19H — compact PLAYER-FACING clock text from a canonical ISO-8601
 * time string, e.g. "2026-09-11T23:41:50+02:00" -> "23:41".
 *
 * DETERMINISTIC and NEVER fabricated: the visible value is extracted
 * VERBATIM from the string the server sent — no date arithmetic, no
 * timezone conversion. The offset in the DTO IS the case/evidence LOCAL
 * time, so the string's HH:MM segment is exactly that local clock time.
 *
 * - full ISO values ("T" or space separator, offset optional) collapse to
 *   "HH:MM" (Phase 19H chooses HH:mm as the primary visible value);
 * - already-compact clock text ("23:41" / "23:41:50") passes through;
 * - malformed values fall back to the raw string, safely (never fabricated).
 *
 * The CANONICAL full value is what the caller keeps in the semantic
 * <time dateTime=...> attribute, so no information is lost from the DTO.
 */
export function compactTimeOf(iso: string): string {
  const trimmed = iso.trim();
  if (trimmed === "") return trimmed;
  if (isCompactClockTime(trimmed)) return trimmed;
  // Full ISO-8601: take the HH:MM segment verbatim as the local clock time.
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{1,2}):(\d{2})/.exec(trimmed);
  return match === null ? trimmed : `${match[2]}:${match[3]}`;
}

/**
 * Deterministic chronological comparator for time entries. Parseable "HH:MM"
 * times order numerically; anything else compares as text. STABLE: entries
 * whose keys compare equal keep their original relative order, so the same
 * payload always produces the same sequence (Phase 19G §10 determinism).
 */
export function compareTimeEntries(a: TimeEntryItem, b: TimeEntryItem): number {
  const am = minutesOfTime(a.time);
  const bm = minutesOfTime(b.time);
  if (am !== null && bm !== null) {
    if (am !== bm) return am < bm ? -1 : 1;
    return 0;
  }
  return a.time < b.time ? -1 : a.time > b.time ? 1 : 0;
}

/**
 * Parse a closed time-entry payload: look for an array under `arrayKeys`
 * (e.g. "entries" / "events"), accept per-item text under `textKeys` (e.g.
 * "text" / "description"), keep ONLY concrete entries carrying BOTH a visible
 * time and readable text (no fabricated timestamps), and sort chronologically.
 */
export function timeEntryItems(
  content: unknown,
  arrayKeys: readonly string[] = ["entries"],
  textKeys: readonly string[] = ["text"],
): TimeEntryItem[] {
  if (!isRecord(content)) return [];
  let raw: unknown = content[arrayKeys[0]];
  for (const key of arrayKeys.slice(1)) {
    if (Array.isArray(raw)) break;
    raw = content[key];
  }
  if (!Array.isArray(raw)) return [];
  const items: TimeEntryItem[] = [];
  for (const entry of raw) {
    if (!isRecord(entry)) continue;
    const time = asText(entry.time).trim();
    if (time === "") continue; // no concrete time -> not a timestamped entry
    const text = firstText(...textKeys.map((key) => entry[key])).trim();
    if (text === "") continue; // no readable text -> nothing to show
    items.push({ time, text });
  }
  return items.sort(compareTimeEntries);
}

/** Join an array of plain values into a comma-separated text list. */
export function textList(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .map((item) => asText(item).trim())
    .filter((item) => item !== "")
    .join(", ");
}