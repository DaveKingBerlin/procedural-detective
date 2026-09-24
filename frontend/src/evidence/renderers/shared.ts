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