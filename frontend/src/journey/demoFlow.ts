import { ApiError } from "../api/client";
import type {
  AnonymousSessionResponse,
  CreateCaseResponse,
  CreatePlaythroughResponse,
  GenerationModeId,
  GenerationStatusResponse,
} from "../api/types";

/**
 * Demo journey state machine (Phase 8 A, REQUIREMENTS 40.2/40.3/40.5).
 *
 * `runDemo(prompt, {services, ...})` drives the entire prompt-to-playthrough
 * journey of the browser demo:
 *
 *   createSession() -> createCase(sessionToken, prompt)
 *   -> poll GET /generations/{id} until PUBLISHED or FAILED
 *   -> createPlaythrough(creatorAccessToken, caseId, 1)
 *   -> { playthroughToken, playthroughId, caseId }
 *
 * The module is PURE and fully injectable: every network dependency comes
 * through {@link DemoFlowServices} and the poll backoff through {@link
 * WaitFn}, so the whole state machine is unit-testable with zero network.
 * Determinism is guaranteed by the dev/demo provider completing
 * synchronously (the first poll usually already reports PUBLISHED), but the
 * same loop also handles a genuinely async generation with bounded retries.
 *
 * Failure handling (each with a distinct, safe, player-facing message):
 *  - generation FAILED            -> { kind: "failed" }
 *  - 429 ADMISSION_DENIED quota   -> { kind: "quota" }
 *  - network/timeout/server error -> { kind: "retryable" }
 * plus a bounded-retry exhaustion -> retryable. The caller offers a Retry
 * action that simply re-runs runDemo.
 *
 * Hard guarantees:
 *  - no provider names, prompts, diagnostics or truth values are exposed;
 *  - a PUBLISHED status is ALWAYS read from the server (never faked).
 */

export interface DemoFlowServices {
  createSession(): Promise<AnonymousSessionResponse>;
  createCase(
    anonymousSessionToken: string,
    prompt: string,
    difficulty?: string,
  ): Promise<CreateCaseResponse>;
  pollGeneration(
    generationId: string,
    creatorAccessToken: string,
  ): Promise<GenerationStatusResponse>;
  createPlaythrough(
    creatorAccessToken: string,
    caseId: string,
    caseVersion: number,
  ): Promise<CreatePlaythroughResponse>;
}

/** Wait primitive used for poll backoff — injected so tests stay instant. */
export type WaitFn = (milliseconds: number) => Promise<void>;

export const defaultWait: WaitFn = (milliseconds) =>
  new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });

export const MAX_POLLS_DEFAULT = 20;
export const POLL_BASE_DELAY_MS = 200;
export const POLL_MAX_DELAY_MS = 2000;
export const PLAYTHROUGH_CASE_VERSION = 1;

/** Typed failure kinds with distinct safe messages (never raw internals). */
export type DemoFailureKind =
  | "failed"
  | "deadline"
  | "provider"
  | "quota"
  | "retryable";

export interface DemoFlowFailure {
  kind: DemoFailureKind;
  message: string;
}

export type DemoFlowResult =
  | { ok: true; playthroughToken: string; playthroughId: string; caseId: string }
  | { ok: false; failure: DemoFlowFailure };

/** Human phase names for the progress UI (pre-poll phases animate briefly). */
export type DemoPhase = "session" | "create-case" | "polling" | "playthrough";

export interface DemoProgress {
  phase: DemoPhase;
  /** Sanitized server status when a snapshot exists (PUBLISHED/FAILED/...). */
  status: string | null;
  stage: string | null;
  progress: number | null;
  /** Poll attempt number (0 outside of polling). */
  attempt: number;
  /**
   * Phase 16 Track B — the generation-mode note: the mode id the journey was
   * invoked with (demo|local|live) or null when none was selected. The backend
   * does not consume the mode over the wire yet (a followup documents the
   * header/query contract), so this is how the player's choice reaches the
   * flow logs without inventing a request body field.
   */
  mode: GenerationModeId | null;
}

/** Player-safe failure messages — the ONLY strings the journey surfaces. */
export const DEMO_FAILURE_MESSAGES = Object.freeze({
  failed: "This prompt could not be turned into a solvable case. Adjust the prompt, then try again.",
  deadline: "Generation exceeded its time budget. Please try again.",
  providerTimeout: "The AI provider took too long to respond. Please try again.",
  providerUnavailable: "The AI generation service is currently unavailable. Please try again.",
  quota: "Too many cases are being generated right now. Wait a few moments, then try again.",
  network: "The case service could not be reached. Check your connection, then try again.",
  server: "The case service reported a temporary problem. Please try again.",
  tooSlow: "Generation is taking longer than expected. Please try again.",
  generic: "The case could not be created right now. Please try again.",
  // Phase 22 — BYO-Ollama bridge typed failures (§21). The server projects
  // bridge-reported reasons onto its CLOSED code set; the public UI maps each
  // code to this frozen safe copy and NEVER surfaces the raw code text.
  bridgeNotConnected: "Connect your local Ollama bridge first.",
  bridgeDisconnected:
    "Local AI disconnected — Reconnect the local bridge or use Deterministic Demo.",
  localOllamaUnavailable: "Ollama is not reachable on this computer.",
  localModelUnavailable: "The selected local model is not available.",
  localProviderTimeout: "Local AI did not finish within the allowed time.",
});

export interface RunDemoOptions {
  /** The real or fake endpoint set (injected in tests). */
  services: DemoFlowServices;
  /** Optional difficulty label passed through to POST /cases. */
  difficulty?: string;
  /** Injectable wait used for poll backoff (defaults to setTimeout). */
  wait?: WaitFn;
  /** Bounded poll count (default {@link MAX_POLLS_DEFAULT}). */
  maxPolls?: number;
  /** Base backoff in ms (default {@link POLL_BASE_DELAY_MS}). */
  baseDelayMs?: number;
  /** Backoff cap in ms (default {@link POLL_MAX_DELAY_MS}). */
  maxDelayMs?: number;
  /** Observe progress snapshots (used by the generating route's UI). */
  onProgress?: (progress: DemoProgress) => void;
  /**
   * Phase 16 Track B — the selected generation mode (demo|local|live). Not
   * sent over the wire (the backend contract for that is a followup decision):
   * it travels as a note in every progress snapshot logged by this flow.
   */
  mode?: GenerationModeId | null;
}

/** Map any thrown value to a typed, safe failure (pure, unit-testable). */
export function mapDemoError(error: unknown): DemoFlowFailure {
  if (error instanceof ApiError) {
    if (error.status === 429 && error.code === "ADMISSION_DENIED") {
      return { kind: "quota", message: DEMO_FAILURE_MESSAGES.quota };
    }
    if (error.status === 0) {
      return { kind: "retryable", message: DEMO_FAILURE_MESSAGES.network };
    }
    if (error.status >= 500) {
      return { kind: "retryable", message: DEMO_FAILURE_MESSAGES.server };
    }
  }
  return { kind: "retryable", message: DEMO_FAILURE_MESSAGES.generic };
}

/** The bounded exponential backoff delay for a poll attempt (1-based). */
export function pollDelayMs(
  attempt: number,
  baseDelayMs: number = POLL_BASE_DELAY_MS,
  maxDelayMs: number = POLL_MAX_DELAY_MS,
): number {
  const expo = Math.pow(2, Math.max(0, attempt - 1));
  return Math.min(Math.round(baseDelayMs * expo), maxDelayMs);
}

/**
 * Run the whole demo journey. Never throws: every failure becomes a typed
 * DemoFlowFailure with a safe message. Returns the one-time credentials the
 * caller stores and uses to enter /scene.
 */
export async function runDemo(prompt: string, options: RunDemoOptions): Promise<DemoFlowResult> {
  const { services } = options;
  const wait = options.wait ?? defaultWait;
  const maxPolls = options.maxPolls ?? MAX_POLLS_DEFAULT;
  const baseDelay = options.baseDelayMs ?? POLL_BASE_DELAY_MS;
  const maxDelay = options.maxDelayMs ?? POLL_MAX_DELAY_MS;
  const mode = options.mode ?? null;
  const report = (progress: DemoProgress) => options.onProgress?.(progress);

  report({ phase: "session", status: null, stage: null, progress: null, attempt: 0, mode });

  let sessionToken: string;
  try {
    const session = await services.createSession();
    sessionToken = session.anonymousSessionToken;
  } catch (error) {
    return { ok: false, failure: mapDemoError(error) };
  }

  report({ phase: "create-case", status: null, stage: null, progress: null, attempt: 0, mode });

  let created: CreateCaseResponse;
  try {
    created = await services.createCase(sessionToken, prompt, options.difficulty);
  } catch (error) {
    return { ok: false, failure: mapDemoError(error) };
  }

  const { caseId, generationId, creatorAccessToken, status, failureCode } = created;

  if (status === "FAILED") {
    return { ok: false, failure: generationFailed(failureCode) };
  }

  if (status !== "PUBLISHED") {
    let gainedPublish = false;
    for (let attempt = 1; attempt <= maxPolls; attempt++) {
      let polled: GenerationStatusResponse;
      try {
        polled = await services.pollGeneration(generationId, creatorAccessToken);
      } catch (error) {
        return { ok: false, failure: mapDemoError(error) };
      }
      report({
        phase: "polling",
        status: polled.status,
        stage: polled.stage,
        progress: polled.progress,
        attempt,
        mode,
      });
      if (polled.status === "PUBLISHED") {
        gainedPublish = true;
        break;
      }
      if (polled.status === "FAILED") {
        return { ok: false, failure: generationFailed(polled.failureCode) };
      }
      if (attempt >= maxPolls) {
        return { ok: false, failure: { kind: "retryable", message: DEMO_FAILURE_MESSAGES.tooSlow } };
      }
      await wait(pollDelayMs(attempt, baseDelay, maxDelay));
    }
    if (!gainedPublish) {
      // Unreachable in practice (the loop returns or breaks), kept as a guard.
      return { ok: false, failure: { kind: "retryable", message: DEMO_FAILURE_MESSAGES.tooSlow } };
    }
  }

  report({
    phase: "playthrough",
    status: "PUBLISHED",
    stage: null,
    progress: 100,
    attempt: 0,
    mode,
  });

  try {
    const playthrough = await services.createPlaythrough(
      creatorAccessToken,
      caseId,
      PLAYTHROUGH_CASE_VERSION,
    );
    return {
      ok: true,
      playthroughToken: playthrough.playthroughAccessToken,
      playthroughId: playthrough.playthroughId,
      caseId,
    };
  } catch (error) {
    return { ok: false, failure: mapDemoError(error) };
  }
}

/**
 * Map a canonical generation failure code to a safe, player-facing failure.
 *
 * Every comparison is EXACT string equality — a longer, legacy or hostile
 * variant containing one of these codes as a prefix/substring never matches
 * (the same narrowing the pre-Phase-19 mapping already used) and falls through
 * to the generic failed message. Raw failureCode text is never surfaced.
 *
 * Phase 19 notes (backend failure_codes.py, READ ONLY from the frontend):
 *  - CORE_PROVIDER_CALL_BUDGET_EXHAUSTED / ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED
 *    map to the SAME safe provider message class as PROVIDER_CALL_BUDGET_EXHAUSTED:
 *    provider-neutral, no core/asset/internal budget wording.
 *  - MAX_PROCEDURAL_ASSETS_EXCEEDED / MAX_FAILED_ASSETS_EXCEEDED map to the
 *    SAME safe "could not be turned into a playable case" message class as the
 *    generic failed fallback. No internal limits are ever shown.
 *
 * Phase 22 notes (bridge typed failures, §21): BRIDGE_NOT_CONNECTED /
 * BRIDGE_DISCONNECTED / LOCAL_OLLAMA_UNAVAILABLE / LOCAL_MODEL_UNAVAILABLE /
 * LOCAL_PROVIDER_TIMEOUT map to distinct frozen safe copy (the local-Ollama
 * user action each requires). The remaining Phase 22 codes
 * (BRIDGE_PAIRING_EXPIRED / BRIDGE_BUSY / BRIDGE_PROTOCOL_ERROR /
 * LOCAL_PROVIDER_INVALID_OUTPUT) and every unknown/hostile variant fall
 * through to the generic failed message — no raw code is ever surfaced.
 */
export function generationFailed(failureCode?: string | null): DemoFlowFailure {
  if (failureCode === "GENERATION_DEADLINE_EXCEEDED") {
    return { kind: "deadline", message: DEMO_FAILURE_MESSAGES.deadline };
  }
  if (failureCode === "PROVIDER_TIMEOUT") {
    return { kind: "provider", message: DEMO_FAILURE_MESSAGES.providerTimeout };
  }
  if (
    failureCode === "PROVIDER_UNAVAILABLE" ||
    failureCode === "PROVIDER_INVALID_RESPONSE" ||
    // Phase 19 — hierarchical provider-budget exhaustion: same safe provider
    // message class as PROVIDER_CALL_BUDGET_EXHAUSTED (exact match only).
    failureCode === "PROVIDER_CALL_BUDGET_EXHAUSTED" ||
    failureCode === "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED" ||
    failureCode === "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"
  ) {
    return { kind: "provider", message: DEMO_FAILURE_MESSAGES.providerUnavailable };
  }
  if (
    // Phase 19 — asset-count limits: same safe "not playable" message class as
    // the generic failed fallback (exact match only; internals never revealed).
    failureCode === "MAX_PROCEDURAL_ASSETS_EXCEEDED" ||
    failureCode === "MAX_FAILED_ASSETS_EXCEEDED"
  ) {
    return { kind: "failed", message: DEMO_FAILURE_MESSAGES.failed };
  }
  // Phase 22 — BYO-Ollama bridge typed failures (§21): exact-string matches
  // only, each mapped to frozen safe copy — the raw code (or a hostile
  // prefix/substring variant) can never surface.
  if (failureCode === "BRIDGE_NOT_CONNECTED") {
    return { kind: "provider", message: DEMO_FAILURE_MESSAGES.bridgeNotConnected };
  }
  if (failureCode === "BRIDGE_DISCONNECTED") {
    return { kind: "provider", message: DEMO_FAILURE_MESSAGES.bridgeDisconnected };
  }
  if (failureCode === "LOCAL_OLLAMA_UNAVAILABLE") {
    return { kind: "provider", message: DEMO_FAILURE_MESSAGES.localOllamaUnavailable };
  }
  if (failureCode === "LOCAL_MODEL_UNAVAILABLE") {
    return { kind: "provider", message: DEMO_FAILURE_MESSAGES.localModelUnavailable };
  }
  if (failureCode === "LOCAL_PROVIDER_TIMEOUT") {
    return { kind: "provider", message: DEMO_FAILURE_MESSAGES.localProviderTimeout };
  }
  return { kind: "failed", message: DEMO_FAILURE_MESSAGES.failed };
}
