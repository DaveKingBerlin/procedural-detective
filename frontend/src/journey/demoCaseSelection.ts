/**
 * Phase 28 — the closed Demo-case registry + deterministic random selection.
 *
 * Contract with the sibling backend (Phase 28 §3/§5/§6/§9):
 *   - the backend exposes a CLOSED Demo registry of exactly these three
 *     fixture ids (`demo-apartment` = the existing golden Demo #1,
 *     `demo-gallery` = Demo #2, `demo-laboratory` = Demo #3);
 *   - POST /cases accepts an OPTIONAL flat `demoCaseId` on the fake/demo path
 *     only (closed allowlist; unknown ids rejected). The client only ever
 *     sends ids produced by this module's allowlist — never free text.
 *
 * This module owns the FRONTEND side of the contract:
 *   - the frozen canonical list {@link DEMO_CASE_IDS} (the sibling's exact
 *     ids — never duplicated with a different set);
 *   - ONE injectable RNG wrapper {@link selectDemoCaseId} (unit-testable with
 *     injected rng — no probabilistic tests, no `Math.random()` scattered in
 *     components);
 *   - a per-session holder (`pd_demo_case_id` in sessionStorage) that pins
 *     the selected id for the whole play session. {@link rollDemoCaseId} is
 *     the ONLY "Try Demo Case" entry point: it always rolls a FRESH fixture
 *     and stores it. Route transitions, React remounts, scene rerenders,
 *     evidence updates, returning from the accusation screen and page
 *     refreshes of the ACTIVE demo NEVER re-roll (they never call this
 *     module — the id is authoritative in the journey context once selected).
 *     The holder is cleared only on a deliberate reset (`clearSessionDemoCaseId`)
 *     or replaced by a new `Try Demo Case` action.
 *
 * Determinism is guaranteed by the backend fixtures themselves (Phase 28 §5):
 * randomness exists ONLY to choose which fixture starts.
 */

/** The frozen canonical Demo-fixture registry (Phase 28 §3/§5). */
export const DEMO_CASE_IDS = ["demo-apartment", "demo-gallery", "demo-laboratory"] as const;

/** One allowlisted Demo fixture id. */
export type DemoCaseId = (typeof DEMO_CASE_IDS)[number];

/** sessionStorage key of the per-session selected demo id. */
export const DEMO_CASE_ID_STORAGE_KEY = "pd_demo_case_id";

/** Minimal storage surface used by this module (sessionStorage-compatible). */
export interface DemoCaseStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function defaultStorage(): DemoCaseStorage | null {
  try {
    if (typeof window !== "undefined" && typeof window.sessionStorage !== "undefined") {
      return window.sessionStorage;
    }
  } catch {
    // sessionStorage can throw in hardened/embedded contexts — treat as absent.
  }
  return null;
}

/** True only for the frozen allowlisted fixture ids (never free text). */
export function isDemoCaseId(value: unknown): value is DemoCaseId {
  return (
    typeof value === "string" && (DEMO_CASE_IDS as readonly string[]).includes(value)
  );
}

/**
 * The ONE random wrapper of the entire demo-selection feature (Phase 28 §7).
 * Maps a uniform [0,1) rng to a fixture index exactly like
 * `floor(rng() * N) % N`; the `% N` guard keeps even an out-of-range rng
 * (e.g. exactly 1) on a registered id. `rng` defaults to `Math.random` —
 * tests inject a fixed value so the mapping is deterministic.
 */
export function selectDemoCaseId(rng: () => number = Math.random): DemoCaseId {
  const index = Math.floor(rng() * DEMO_CASE_IDS.length) % DEMO_CASE_IDS.length;
  return DEMO_CASE_IDS[index];
}

/** Read the per-session selected demo id. ONLY allowlisted ids are returned;
 *  a stale/tampered/absent value reads null (discard-if-stale). */
export function getSessionDemoCaseId(storage?: DemoCaseStorage | null): DemoCaseId | null {
  const store = storage ?? defaultStorage();
  if (!store) return null;
  let raw: string | null = null;
  try {
    raw = store.getItem(DEMO_CASE_ID_STORAGE_KEY);
  } catch {
    return null;
  }
  return isDemoCaseId(raw) ? raw : null;
}

/** Persist the per-session selected demo id (best-effort). */
export function setSessionDemoCaseId(
  id: DemoCaseId,
  storage?: DemoCaseStorage | null,
): boolean {
  const store = storage ?? defaultStorage();
  if (!store) return false;
  try {
    store.setItem(DEMO_CASE_ID_STORAGE_KEY, id);
    return true;
  } catch {
    return false;
  }
}

/** Clear the per-session selected demo id (deliberate reset / new-demo flow;
 *  best-effort, never throws). */
export function clearSessionDemoCaseId(storage?: DemoCaseStorage | null): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.removeItem(DEMO_CASE_ID_STORAGE_KEY);
  } catch {
    // Best-effort: a storage failure must never crash the page.
  }
}

/**
 * The ONLY "fresh Try Demo Case" action: rolls a NEW fixture from `rng`
 * (default `Math.random`) and pins it in the per-session holder. Every click
 * independently selects (Phase 28 §8); a selected id is authoritative for the
 * whole session and is never re-rolled by any later navigation/remount.
 */
export function rollDemoCaseId(
  rng: () => number = Math.random,
  storage?: DemoCaseStorage | null,
): DemoCaseId {
  const id = selectDemoCaseId(rng);
  setSessionDemoCaseId(id, storage);
  return id;
}