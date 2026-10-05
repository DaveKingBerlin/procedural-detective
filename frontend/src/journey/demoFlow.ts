import { ApiError } from "../api/client";
import type {
  AnonymousSessionResponse,
  CreateCaseGeneration,
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
    /**
     * Phase 25 — the OPTIONAL flat generation-selection block
     * ({generationProvider, ollamaTransport?, ollamaModel?}) carried into the
     * POST /cases body ONLY when the caller actually selected a provider (§13
     * backward compat: every existing no-selection call site stays
     * byte-identical — three arguments, no selection field).
     */
    generation?: CreateCaseGeneration,
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
  | "retryable"
  | "safetyLimit";

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
  // Phase 25 — explicit-selection validation rejections. The backend rejects
  // an unknown provider id (400 INVALID_GENERATION_PROVIDER) and an
  // explicitly-selected-but-unavailable provider (PROVIDER_UNAVAILABLE); the
  // client shows this frozen safe copy and NEVER silently switches provider
  // (§4.2 / §10.8). No raw upstream text or code is ever surfaced.
  invalidGenerationProvider:
    "The selected generation provider is not supported. Choose another provider and try again.",
  providerUnavailableExplicit:
    "The selected AI provider is unavailable right now. Choose another provider and try again.",
  // Phase 26C3 §12 — the internal bounded-generation safety-limit family
  // (PROVIDER_CALL_BUDGET_EXHAUSTED / CORE_PROVIDER_CALL_BUDGET_EXHAUSTED /
  // ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED). The provider was AVAILABLE and
  // successfully returning results; the failure is an internal bounded-
  // generation safety limit, NOT a provider outage — so the provider-
  // unavailable copy is never borrowed. Neutral, truthful, friendly; no
  // internal budget number or pipeline topology is ever revealed.
  safetyLimit:
    "This case could not be completed within the generation safety limits. Please try again.",
  // Phase 30 §23 — BYOK Frontier provider failures, each mapped to frozen safe
  // copy. The raw provider response/body/code is NEVER surfaced (and the
  // client never included a provider URL, so no endpoint text can leak).
  // 401/403 -> FRONTIER_AUTH_FAILED; 404 -> FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND;
  // 429 -> FRONTIER_RATE_LIMITED; timeout -> FRONTIER_TIMEOUT; 5xx ->
  // FRONTIER_PROVIDER_ERROR. The backend's SINGLE 400-level BYOK validation
  // code INVALID_FRONTIER_CONFIG (missing/invalid provider/key/model, unknown
  // provider id — backend/app/api/v1/errors.py) maps to the single "check
  // your Frontier provider/key/model" copy. Exact code only.
  frontierAuthFailed: "The selected provider rejected the supplied API credentials.",
  frontierEndpointOrModelNotFound:
    "The selected provider or model could not be found. Check the model name, then try again.",
  frontierRateLimited:
    "The selected provider is rate-limiting requests right now. Wait a moment, then try again.",
  frontierTimeout: "The selected provider took too long to respond. Please try again.",
  frontierProviderError: "The selected provider reported an error. Please try again.",
  frontierInvalidConfiguration:
    "Check your Frontier provider, API key and model, then try again.",
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
  /**
   * Phase 24 P0 — an OPTIONAL pre-existing anonymous-session bearer (the
   * in-memory journey token carried from /new after a bridge pairing). When
   * present, the flow uses it for POST /cases and SKIPS createSession();
   * when absent it mints a fresh session exactly as before (the demo /
   * non-bridge paths are byte-identical).
   */
  anonymousSessionToken?: string;
  /**
   * Phase 25 — the OPTIONAL browser-selected generation block
   * ({generationProvider, ollamaTransport?, ollamaModel?}). When present the
   * flow passes it through to createCase -> POST /cases; when absent the
   * createCase call stays THREE arguments (byte-identical, §13) and the
   * backend resolves its configured default provider. The flow NEVER
   * auto-falls-back after an explicit selection fails (§4.2).
   */
  generation?: CreateCaseGeneration;
}

/** Map any thrown value to a typed, safe failure (pure, unit-testable). */
export function mapDemoError(error: unknown): DemoFlowFailure {
  if (error instanceof ApiError) {
    if (error.status === 429 && error.code === "ADMISSION_DENIED") {
      return { kind: "quota", message: DEMO_FAILURE_MESSAGES.quota };
    }
    // Phase 25 — explicit-selection rejections. The backend rejects an
    // unknown provider id with INVALID_GENERATION_PROVIDER and an explicitly
    // selected-but-unavailable provider with PROVIDER_UNAVAILABLE; both map
    // to frozen safe copy (never the raw code/message) and the journey NEVER
    // silently switches provider — the caller must re-run with a different
    // explicit choice (§4.2 / §10.8). Exact code equality only: a hostile
    // prefix/substring variant cannot narrow into these buckets.
    if (error.code === "INVALID_GENERATION_PROVIDER") {
      return { kind: "provider", message: DEMO_FAILURE_MESSAGES.invalidGenerationProvider };
    }
    if (error.code === "PROVIDER_UNAVAILABLE") {
      return { kind: "provider", message: DEMO_FAILURE_MESSAGES.providerUnavailableExplicit };
    }
    // Phase 30 §23 — a Frontier failure surfaced as an HTTP error envelope
    // (the backend rejects the BYOK attempt with FRONTIER_* / the single 400
    // INVALID_FRONTIER_CONFIG code). Exact-string match only, mapped to the
    // frozen safe copy — never the raw code/body.
    const frontierMessage = frontierFailureMessage(error.code);
    if (frontierMessage !== null) {
      return { kind: "provider", message: frontierMessage };
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

/**
 * Phase 30 §23 — EXACT-STRING map from a Frontier failure code to its frozen
 * safe player-facing copy, or null when the code is not a known Frontier
 * code. Used by BOTH mapping paths (`generationFailed` for a FAILED
 * generation, `mapDemoError` for a rejected POST /cases). The project's
 * exact-string rule guarantees a hostile/legacy prefix or substring variant
 * can never narrow into these buckets — unknown codes fall through to the
 * generic safe message. No raw code/provider text is ever surfaced.
 */
export function frontierFailureMessage(code: string | null | undefined): string | null {
  if (code === "FRONTIER_AUTH_FAILED") return DEMO_FAILURE_MESSAGES.frontierAuthFailed;
  if (code === "FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND") {
    return DEMO_FAILURE_MESSAGES.frontierEndpointOrModelNotFound;
  }
  if (code === "FRONTIER_RATE_LIMITED") return DEMO_FAILURE_MESSAGES.frontierRateLimited;
  if (code === "FRONTIER_TIMEOUT") return DEMO_FAILURE_MESSAGES.frontierTimeout;
  if (code === "FRONTIER_PROVIDER_ERROR") return DEMO_FAILURE_MESSAGES.frontierProviderError;
  // The backend's SINGLE 400-level BYOK request-validation code
  // INVALID_FRONTIER_CONFIG (missing/invalid provider / api key / model,
  // unknown provider id — backend/app/api/v1/errors.py) maps to ONE safe
  // "check your Frontier provider/key/model" copy. Exact code only — a
  // hostile/legacy prefix or substring variant can never narrow in.
  if (code === "INVALID_FRONTIER_CONFIG") {
    return DEMO_FAILURE_MESSAGES.frontierInvalidConfiguration;
  }
  return null;
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
    const preexistingToken = options.anonymousSessionToken;
    if (typeof preexistingToken === "string" && preexistingToken !== "") {
      // Phase 24 P0 — reuse the anonymous session that paired the bridge:
      // POST /cases is authorized under that session and createSession() is
      // SKIPPED (no second anonymous session, no bridge lookup miss).
      sessionToken = preexistingToken;
    } else {
      const session = await services.createSession();
      sessionToken = session.anonymousSessionToken;
    }
  } catch (error) {
    return { ok: false, failure: mapDemoError(error) };
  }

  report({ phase: "create-case", status: null, stage: null, progress: null, attempt: 0, mode });

  let created: CreateCaseResponse;
  try {
    // Phase 25 — the selection is carried ONLY when present: a no-selection
    // call stays THREE arguments (byte-identical, §13) and the backend
    // resolves its configured default provider.
    created =
      options.generation === undefined
        ? await services.createCase(sessionToken, prompt, options.difficulty)
        : await services.createCase(sessionToken, prompt, options.difficulty, options.generation);
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
 *  - MAX_PROCEDURAL_ASSETS_EXCEEDED / MAX_FAILED_ASSETS_EXCEEDED map to the
 *    SAME safe "could not be turned into a playable case" message class as the
 *    generic failed fallback. No internal limits are ever shown.
 *
 * Phase 26C3 §12 — the hierarchical PROVIDER-CALL-budget exhaustion family
 * (PROVIDER_CALL_BUDGET_EXHAUSTED / CORE_PROVIDER_CALL_BUDGET_EXHAUSTED /
 * ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED) maps to a DISTINCT truthful
 * "generation safety-limit" bucket with a retry affordance. The provider was
 * available and successfully returning results — this is NOT a provider
 * outage/connectivity failure — so the provider-unavailable copy is never
 * borrowed. No budget number or pipeline topology is ever revealed.
 *
 * Phase 22 notes (bridge typed failures, §21): BRIDGE_NOT_CONNECTED /
 * BRIDGE_DISCONNECTED / LOCAL_OLLAMA_UNAVAILABLE / LOCAL_MODEL_UNAVAILABLE /
 * LOCAL_PROVIDER_TIMEOUT map to distinct frozen safe copy (the local-Ollama
 * user action each requires). The remaining Phase 22 codes
 * (BRIDGE_PAIRING_EXPIRED / BRIDGE_BUSY / BRIDGE_PROTOCOL_ERROR /
 * LOCAL_PROVIDER_INVALID_OUTPUT) and every unknown/hostile variant fall
 * through to the generic failed message — no raw code is ever surfaced.
 *
 * Phase 30 notes (§23, BYOK Frontier): FRONTIER_AUTH_FAILED /
 * FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND / FRONTIER_RATE_LIMITED /
 * FRONTIER_TIMEOUT / FRONTIER_PROVIDER_ERROR and the 400-level
 * INVALID_FRONTIER_CONFIG validation code map to frozen safe copy through
 * {@link frontierFailureMessage} (exact strings only). The provider's raw
 * response body/endpoint/credentials are never echoed back to the browser.
 */
export function generationFailed(failureCode?: string | null): DemoFlowFailure {
  if (failureCode === "GENERATION_DEADLINE_EXCEEDED") {
    return { kind: "deadline", message: DEMO_FAILURE_MESSAGES.deadline };
  }
  if (failureCode === "PROVIDER_TIMEOUT") {
    return { kind: "provider", message: DEMO_FAILURE_MESSAGES.providerTimeout };
  }
  if (
    // Phase 26C3 §12 — ONLY actual provider availability/connectivity failures
    // (and a repeatedly misbehaving provider) may use the provider-unavailable
    // copy. Exact-string equality only: a hostile/legacy prefix or substring
    // variant can never narrow into this bucket.
    failureCode === "PROVIDER_UNAVAILABLE" ||
    failureCode === "PROVIDER_INVALID_RESPONSE"
  ) {
    return { kind: "provider", message: DEMO_FAILURE_MESSAGES.providerUnavailable };
  }
  if (
    // Phase 26C3 §12 — the internal bounded-generation safety-limit family
    // (hierarchical provider-CALL-budget exhaustion). The provider was
    // available and returning results; the generation could not be completed
    // within its internal safety limits. NEVER shown as provider unavailability.
    // Exact-string equality only (the project's established rule).
    failureCode === "PROVIDER_CALL_BUDGET_EXHAUSTED" ||
    failureCode === "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED" ||
    failureCode === "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"
  ) {
    return { kind: "safetyLimit", message: DEMO_FAILURE_MESSAGES.safetyLimit };
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
  // Phase 30 §23 — a generation that FAILED with a Frontier provider code
  // (401/403 auth, 404 endpoint-or-model, 429 rate limit, timeout, 5xx, and
  // the 400-level INVALID_FRONTIER_CONFIG validation code) maps to frozen
  // safe copy via the exact-string helper. Every variant/unknown code falls
  // through to the generic failed message — no raw code ever surfaces.
  const frontierMessage = frontierFailureMessage(failureCode);
  if (frontierMessage !== null) {
    return { kind: "provider", message: frontierMessage };
  }
  return { kind: "failed", message: DEMO_FAILURE_MESSAGES.failed };
}
