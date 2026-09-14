/**
 * In-memory journey context (Phase 8 B/C).
 *
 * Carries the prompt-to-case journey parameters from the /new (or landing
 * "Try Demo Case") route to the /generating route WITHOUT placing tokens or
 * prompts in the URL (no history/referrer leakage). The context is
 * intentionally NON-persistent: a hard refresh of /generating loses it and
 * the route renders a friendly "start again" state instead — the safest
 * failure mode (no credential material survives in the address bar).
 */

export type JourneyDifficulty = "easy" | "medium" | "hard";

export interface JourneyParams {
  prompt: string;
  difficulty: JourneyDifficulty;
}

let current: JourneyParams | null = null;

/** Store the journey parameters that /generating will consume. */
export function setJourneyParams(params: JourneyParams | null): void {
  current = params;
}

/** Read (a snapshot of) the pending journey parameters, if any. */
export function getJourneyParams(): JourneyParams | null {
  return current;
}

/** Clear the context (after a successful journey or a reset). */
export function clearJourneyParams(): void {
  current = null;
}