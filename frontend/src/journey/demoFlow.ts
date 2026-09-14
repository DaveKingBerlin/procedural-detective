import { ApiError } from "../api/client";
import type {
  AnonymousSessionResponse,
  CreateCaseResponse,
  CreatePlaythroughResponse,
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
export type DemoFailureKind = "failed" | "quota" | "retryable";

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
}

/** Player-safe failure messages — the ONLY strings the journey surfaces. */
export const DEMO_FAILURE_MESSAGES = Object.freeze({
  failed: "This prompt could not be turned into a solvable case. Adjust the prompt, then try again.",
  quota: "Too many cases are being generated right now. Wait a few moments, then try again.",
  network: "The case service could not be reached. Check your connection, then try again.",
  server: "The case service reported a temporary problem. Please try again.",
  tooSlow: "Generation is taking longer than expected. Please try again.",
  generic: "The case could not be created right now. Please try again.",
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
  const report = (progress: DemoProgress) => options.onProgress?.(progress);

  report({ phase: "session", status: null, stage: null, progress: null, attempt: 0 });

  let sessionToken: string;
  try {
    const session = await services.createSession();
    sessionToken = session.anonymousSessionToken;
  } catch (error) {
    return { ok: false, failure: mapDemoError(error) };
  }

  report({ phase: "create-case", status: null, stage: null, progress: null, attempt: 0 });

  let created: CreateCaseResponse;
  try {
    created = await services.createCase(sessionToken, prompt, options.difficulty);
  } catch (error) {
    return { ok: false, failure: mapDemoError(error) };
  }

  const { caseId, generationId, creatorAccessToken, status } = created;

  if (status === "FAILED") {
    return { ok: false, failure: generationFailed() };
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
      });
      if (polled.status === "PUBLISHED") {
        gainedPublish = true;
        break;
      }
      if (polled.status === "FAILED") {
        return { ok: false, failure: generationFailed() };
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

function generationFailed(): DemoFlowFailure {
  return { kind: "failed", message: DEMO_FAILURE_MESSAGES.failed };
}