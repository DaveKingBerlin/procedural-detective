/**
 * Player Hypothesis store (Phase 18C) — the notebook's MANUAL PINS.
 *
 * These are PLAYER NOTES ONLY. They live ONLY in localStorage under a stable
 * per-playthrough key namespace (`pd_hypothesis_v1:<playthroughId>`), are
 * NEVER sent to the server, NEVER persisted via any endpoint, and NEVER
 * affect the solver, CaseTruth, scoring or authoritative game state.
 *
 * Safety rules enforced here:
 *   - the ONLY keys ever touched are `pd_hypothesis_v1:*` (never
 *     `pd_playthrough_token` etc. — and never a hidden-truth key);
 *   - reads are defensive: malformed/foreign JSON, non-object payloads and
 *     unknown keys all collapse to the empty pins (nothing leaks through);
 *   - the only values written are the four player-authored pin strings
 *     (suspect/motive/weapon ids + a bare "HH:MM" time). A pin that no
 *     longer matches the current candidate universe is still player-authored
 *     local data — the accusation flow's existing membership validation is
 *     the gate that prevents such a stale pin from ever being submitted.
 *
 * localStorage access is wrapped + injectable so the module stays pure and
 * unit-testable without a DOM (same pattern as api/playthroughToken.ts).
 */

/** Stable key namespace for per-playthrough player-hypothesis pins. */
export const HYPOTHESIS_KEY_PREFIX = "pd_hypothesis_v1";

export interface HypothesisPins {
  /** Player-authored suspect candidate id, or null when unpinned. */
  suspect: string | null;
  /** Player-authored motive candidate id, or null when unpinned. */
  motive: string | null;
  /** Player-authored weapon candidate id, or null when unpinned. */
  weapon: string | null;
  /** Player-authored bare 24h "HH:MM" time-of-day, or null when unpinned. */
  time: string | null;
}

export const EMPTY_PINS: HypothesisPins = { suspect: null, motive: null, weapon: null, time: null };

/** Minimal localStorage-compatible surface (same shape as TokenStorage). */
export interface HypothesisStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function defaultStorage(): HypothesisStorage | null {
  try {
    if (typeof window !== "undefined" && typeof window.localStorage !== "undefined") {
      return window.localStorage;
    }
  } catch {
    return null;
  }
  return null;
}

function readItem(key: string, storage: HypothesisStorage | null | undefined): string | null {
  const store = storage ?? defaultStorage();
  if (!store) return null;
  try {
    return store.getItem(key);
  } catch {
    return null;
  }
}

function writeItem(key: string, value: string, storage: HypothesisStorage | null | undefined): boolean {
  const store = storage ?? defaultStorage();
  if (!store) return false;
  try {
    store.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** The per-playthrough localStorage key (stable namespace). */
export function hypothesisKey(playthroughId: string): string {
  return `${HYPOTHESIS_KEY_PREFIX}:${playthroughId}`;
}

/** True when a localStorage key belongs to this module's namespace. */
export function isHypothesisKey(key: string): boolean {
  return typeof key === "string" && key.startsWith(`${HYPOTHESIS_KEY_PREFIX}:`);
}

/** Only the four known pin fields may ever be read back / written. */
const PIN_FIELDS: readonly (keyof HypothesisPins)[] = ["suspect", "motive", "weapon", "time"];

const TIME_PATTERN = /^\d{2}:\d{2}$/;

/** Same 24h bound the accusation form enforces: 00:00..23:59. */
function isValidTime(value: string): boolean {
  if (!TIME_PATTERN.test(value)) return false;
  const [hours, minutes] = value.split(":").map((part) => Number(part));
  return hours >= 0 && hours <= 23 && minutes >= 0 && minutes <= 59;
}

/**
 * Id-shaped pin token: candidate ids (e.g. `thomas_reed`, `motive_alpha`,
 * `weapon_beta`, `proc.decor.<hash>`-style ids) are short, bounded,
 * whitespace-free tokens. Anything else (script/HTML/whitespace/oversized)
 * is a hostile or non-pin value and is DROPPED at the storage layer — a
 * player pin is only ever an id-shaped token or a clean "HH:MM" time.
 */
const ID_PATTERN = /^[A-Za-z0-9_.:-]{1,80}$/;

function sanitizePin(value: unknown, field: keyof HypothesisPins): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed === "") return null;
  if (field === "time") return isValidTime(trimmed) ? trimmed : null;
  return ID_PATTERN.test(trimmed) ? trimmed : null;
}

/**
 * Read the player's hypothesis pins for one playthrough. Defensive: any
 * malformed/foreign content (wrong shape, unknown keys, bad value types)
 * collapses to the EMPTY pins — a hostile or stale payload can never leak
 * or crash.
 */
export function loadHypothesis(playthroughId: string, storage?: HypothesisStorage | null): HypothesisPins {
  const raw = readItem(hypothesisKey(playthroughId), storage);
  if (raw === null) return { ...EMPTY_PINS };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ...EMPTY_PINS };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { ...EMPTY_PINS };
  }
  const record = parsed as Record<string, unknown>;
  const pins: HypothesisPins = { ...EMPTY_PINS };
  for (const field of PIN_FIELDS) {
    pins[field] = sanitizePin(record[field], field);
  }
  return pins;
}

/**
 * Persist the player's hypothesis pins for one playthrough. Only the four
 * allowed pin fields are ever written; a missing/absent storage degrades to
 * false (the pins simply don't survive a reload — never a crash).
 */
export function saveHypothesis(playthroughId: string, pins: HypothesisPins, storage?: HypothesisStorage | null): boolean {
  const clean: HypothesisPins = { ...EMPTY_PINS };
  for (const field of PIN_FIELDS) {
    clean[field] = typeof pins?.[field] === "string" ? sanitizePin(pins[field], field) : null;
  }
  return writeItem(hypothesisKey(playthroughId), JSON.stringify(clean), storage);
}

/** Clear one playthrough's hypothesis pins (best-effort). */
export function clearHypothesis(playthroughId: string, storage?: HypothesisStorage | null): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.removeItem(hypothesisKey(playthroughId));
  } catch {
    // Best-effort: a storage failure must never crash the page.
  }
}