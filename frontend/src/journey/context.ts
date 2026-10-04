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

import type { GenerationProviderId, OllamaTransportId } from "../api/types";

export type JourneyDifficulty = "easy" | "medium" | "hard";

export interface JourneyParams {
  prompt: string;
  difficulty: JourneyDifficulty;
  /**
   * Phase 24 P0 — the OPTIONAL anonymous-session bearer minted by a bridge
   * pairing on /new. In-memory ONLY: it must NEVER be persisted (matching the
   * documented client rule "bearer tokens are never persisted beyond the
   * contract-mandated localStorage key"). When present, /generating runs
   * POST /cases with THIS token — the SAME anonymous session that paired the
   * bridge — instead of minting a fresh session, so the backend's
   * session-scoped bridge binding resolves for the generation. Absent for the
   * demo/non-bridge paths (they mint a fresh session exactly as before).
   */
  anonymousSessionToken?: string;
  /**
   * Phase 25 — the OPTIONAL browser-selected generation provider (closed
   * "fake" | "ollama" | "frontier"), carried in-memory from /new to
   * /generating. Absent for pre-25/NEW-server-without-providers journeys: the
   * POST /cases request then carries NO selection field (byte-identical).
   */
  generationProvider?: GenerationProviderId;
  /**
   * Phase 25 — the OPTIONAL Ollama transport ("server" | "bridge") that
   * travels ONLY when `generationProvider === "ollama"`.
   */
  ollamaTransport?: OllamaTransportId;
  /**
   * Phase 25 — the OPTIONAL user-supplied Ollama model identifier that
   * travels ONLY when `generationProvider === "ollama"`. Never a URL,
   * credential or configuration value (§1.3).
   */
  ollamaModel?: string;
  /**
   * Phase 28 — the OPTIONAL selected Demo fixture id ("demo-apartment" /
   * "demo-gallery" / "demo-laboratory"), carried in-memory ONLY on the demo
   * path (a fresh "Try Demo Case": the value always comes from
   * rollDemoCaseId — never free text). Absent for generated-case journeys:
   * POST /cases then omits demoCaseId (generated-case behavior unchanged).
   */
  demoCaseId?: string;
}

let current: JourneyParams | null = null;

/**
 * A session token the bridge panel attached BEFORE a journey was staged (the
 * /new pair-then-generate order). Consumed by the NEXT setJourneyParams call
 * so the token survives into whichever journey the user actually starts.
 */
let pendingSessionToken: string | null = null;

/** Store the journey parameters that /generating will consume. */
export function setJourneyParams(params: JourneyParams | null): void {
  if (params === null) {
    current = params;
    pendingSessionToken = null;
    return;
  }
  const token = pendingSessionToken;
  pendingSessionToken = null;
  current =
    token !== null
      ? // A bridge pairing session was attached: carry it into the journey so
        // /new -> /generating presents the SAME anonymous session.
        { ...params, anonymousSessionToken: token }
      : // Byte-identical staging when no session was paired (demo/unpaired
        // paths keep the exact params object the caller built — no extra key).
        params;
}

/**
 * Record that a bridge-pairing anonymous session is available for the journey.
 * Merges into an ALREADY-staged journey immediately AND stays pending for the
 * NEXT staging, so the token travels /new -> /generating whether the user
 * pairs before, between or after staging the prompt.
 */
export function attachJourneySessionToken(token: string): void {
  pendingSessionToken = token;
  if (current !== null) {
    current = { ...current, anonymousSessionToken: token };
  }
}

/** Read (a snapshot of) the pending journey parameters, if any. */
export function getJourneyParams(): JourneyParams | null {
  return current;
}

/** Clear the context (after a successful journey or a reset). */
export function clearJourneyParams(): void {
  current = null;
  pendingSessionToken = null;
}
