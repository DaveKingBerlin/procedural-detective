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
 * Phase 19H — TIME PRESENTATION (this module owns the compact clock rules):
 *   - the PLAYER-FACING time is the COMPACT local clock, extracted VERBATIM
 *     from the server string (no Date/Intl, no timezone conversion — the
 *     offset in the DTO IS the local time, so the string's HH:MM segment is
 *     exactly that local clock time);
 *   - the CANONICAL full value always stays in the DTO and in the semantic
 *     <time dateTime=...> — nothing is converted or discarded.
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

/**
 * One normalized time-entry (activity-log / timeline item).
 *
 * `time` is the CANONICAL value the server sent — kept verbatim for the
 * semantic <time dateTime="..."> attribute (never reshaped).
 * `displayTime` is the NORMALIZED player-facing clock text at one payload-wide
 * granularity (Phase 19H / ADV-247): "HH:mm" normally, "HH:mm:ss" when any
 * entry of the same payload needs seconds.
 */
export interface TimeEntryItem {
  time: string;
  displayTime: string;
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

/** "HH:MM" / "HH:MM:SS" -> seconds past midnight, or null. Numeric ordering
 *  at BOTH granules (a "20:00:30" entry can be ordered against "20:00"
 *  logically, never by lexicographic luck — ADV-247). */
export function secondsOfTime(time: string): number | null {
  const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(time.trim());
  if (match === null) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  const seconds = Number(match[3] ?? 0);
  if (hours > 23 || minutes > 59 || seconds > 59) return null;
  return hours * 3600 + minutes * 60 + seconds;
}

/** True when `value` is already compact clock text ("23:41" or "23:41:50"). */
export function isCompactClockTime(value: string): boolean {
  return /^\d{1,2}:\d{2}(?::\d{2})?$/.test(value);
}

/** A compact clock extraction + the "needs seconds" signal (ADV-245/246/247). */
export interface CompactTimeInfo {
  /**
   * Compact PLAYER-FACING clock text at the entry's OWN shape:
   *  - a full ISO collapses to "HH:mm" (case-insensitive T / space separator),
   *  - already-compact "HH:mm" / "HH:mm:ss" passes through verbatim,
   *  - malformed values fall back to the RAW string (never fabricated),
   *  - empty/non-string values coerce like `asText` ("").
   */
  compact: string;
  /**
   * True ONLY when the COMPACT form itself carries a seconds field — the one
   * shape that FORCES "HH:mm:ss" display granularity (ADV-247). A full ISO's
   * compact form is "HH:mm" by the Phase 19H contract, so a uniform full-ISO
   * payload never acquires ":00" padding.
   */
  hasSeconds: boolean;
}

/** Recognized compact clock shapes: "H:MM", "HH:MM", "H:MM:SS", "HH:MM:SS". */
const COMPACT_CLOCK_PATTERN = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/;

/** Full ISO-8601 with a wall-clock segment — the separator letter is
 *  case-INSENSITIVE ("T" and "t" are both valid ISO separators; a lowercase
 *  "t" must NOT fall through to the raw fallback — ADV-245). */
const FULL_ISO_WITH_TIME_PATTERN = /^(\d{4}-\d{2}-\d{2})[Tt ](\d{1,2}):(\d{2})(?::(\d{2}))?/;

/**
 * Phase 19H + ADV-245/246 — compact PLAYER-FACING clock text from a canonical
 * ISO-8601 time string with a SAFE fallback:
 *  - "2026-09-11T23:41:50+02:00" / "2026-09-11t23:41:50+02:00" /
 *    "2026-09-11 23:41:50+02:00" -> "23:41";
 *  - already-compact "23:41" / "23:41:50" passes through;
 *  - null / undefined / numbers coerce safely like `asText` ("", String(n));
 *  - malformed values fall back to the raw string, safely (never fabricated).
 *
 * DETERMINISTIC and NEVER fabricated: the visible value is extracted VERBATIM
 * from the string the server sent — no date arithmetic, no timezone
 * conversion. The offset in the DTO IS the case/evidence LOCAL time, so the
 * string's HH:MM segment is exactly that local clock time.
 *
 * The CANONICAL full value is what the caller keeps in the semantic
 * <time dateTime=...> attribute, so no information is lost from the DTO.
 */
export function compactTimeInfo(value: unknown): CompactTimeInfo {
  const text = asText(value).trim();
  if (text === "") return { compact: "", hasSeconds: false };
  // Already-compact clock text passes through verbatim at its own granularity.
  const compactInput = COMPACT_CLOCK_PATTERN.exec(text);
  if (compactInput !== null) {
    const seconds = compactInput[3];
    return {
      compact: `${compactInput[1]}:${compactInput[2]}${seconds !== undefined ? `:${seconds}` : ""}`,
      hasSeconds: seconds !== undefined,
    };
  }
  // Full ISO-8601: take the HH:MM segment verbatim as the local clock time.
  // Per the Phase 19H contract the primary visible value is ALWAYS "HH:mm" —
  // collapsing the seconds — so this shape never forces ":ss" on a payload.
  const fullIso = FULL_ISO_WITH_TIME_PATTERN.exec(text);
  if (fullIso !== null) {
    return { compact: `${fullIso[2]}:${fullIso[3]}`, hasSeconds: false };
  }
  // Malformed/hostile values fall back to the raw string — safely literal.
  return { compact: text, hasSeconds: false };
}

/**
 * Phase 19H — compact player-facing clock text for ONE entry at its own
 * shape (see {@link compactTimeInfo}). List rendering additionally normalizes
 * granularity across a payload (see {@link normalizeTimeEntryDisplays}).
 */
export function compactTimeOf(value: unknown): string {
  return compactTimeInfo(value).compact;
}

/**
 * Deterministic chronological comparator for time entries. Entries order
 * numerically on the NORMALIZED display time at BOTH granules ("HH:mm" and
 * "HH:mm:ss" parse to seconds — ADV-247); anything else compares as text.
 * STABLE: entries whose keys compare equal keep their original relative
 * order, so the same payload always produces the same sequence (Phase 19G
 * §10 determinism).
 */
export function compareTimeEntries(a: TimeEntryItem, b: TimeEntryItem): number {
  const av = secondsOfTime(a.displayTime);
  const bv = secondsOfTime(b.displayTime);
  if (av !== null && bv !== null) {
    if (av !== bv) return av < bv ? -1 : 1;
    return 0;
  }
  return a.displayTime < b.displayTime ? -1 : a.displayTime > b.displayTime ? 1 : 0;
}

/**
 * ADV-247 — deterministic DISPLAY granularity for a set of time entries.
 *
 * One payload never mixes clock shapes: if ANY entry's compact form carries
 * seconds ("HH:mm:ss" passthrough), EVERY entry renders "HH:mm:ss" (an
 * "HH:mm" entry gains ":00"); otherwise every entry renders "HH:mm". This is
 * the finest granularity PRESENT in the displayed forms — a uniform full-ISO
 * payload (even with seconds in the source ISO) stays "HH:mm" per the Phase
 * 19H contract, while a bare "20:00:30" next to a "20:00" normalizes BOTH.
 *
 * Ordering runs on the NORMALIZED display value, so "20:00:00" < "20:00:30"
 * holds textually AND logically — never by lexicographic luck. The canonical
 * `time` value is untouched (still the semantic dateTime).
 */
export function normalizeTimeEntryDisplays(items: readonly TimeEntryItem[]): TimeEntryItem[] {
  const anySeconds = items.some((item) => compactTimeInfo(item.time).hasSeconds);
  const normalized = items.map((item) => {
    const info = compactTimeInfo(item.time);
    let display = info.compact;
    if (anySeconds && /^\d{1,2}:\d{2}$/.test(display)) display = `${display}:00`;
    return { time: item.time, displayTime: display, text: item.text };
  });
  return normalized.sort(compareTimeEntries);
}

/**
 * Parse a closed time-entry payload: look for an array under `arrayKeys`
 * (e.g. "entries" / "events"), accept per-item text under `textKeys` (e.g.
 * "text" / "description"), keep ONLY concrete entries carrying BOTH a visible
 * time and readable text (no fabricated timestamps), normalize the display
 * granularity (ADV-247) and sort chronologically. The canonical `time` value
 * is preserved on every item (semantic dateTime) — only the player-facing
 * display text is normalized.
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
    items.push({ time, displayTime: time, text });
  }
  return normalizeTimeEntryDisplays(items);
}

/** Join an array of plain values into a comma-separated text list. */
export function textList(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .map((item) => asText(item).trim())
    .filter((item) => item !== "")
    .join(", ");
}