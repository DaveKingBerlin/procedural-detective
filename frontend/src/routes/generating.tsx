import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  createCase,
  createPlaythrough,
  getGenerationCapabilitiesWithSession,
  getGenerationProgress,
} from "../api/client";
import {
  createOrReuseAnonymousSession,
  resetAnonymousSessionCache,
} from "../api/anonymousSession";
import type { CreateCaseGeneration, GenerationCapabilitiesResponse, GenerationModeId } from "../api/types";
import { setPlaythroughId, setPlaythroughToken } from "../api/playthroughToken";
import { clearJourneyParams, getJourneyParams, type JourneyParams } from "../journey/context";
import { clearSessionDemoCaseId } from "../journey/demoCaseSelection";
import {
  getGenerationMode,
  validatedJourneyMode,
} from "../journey/generationMode";
import { toCreateCaseGeneration } from "../journey/generationProvider";
import { loadGenerationCapabilities } from "../hooks/useGenerationCapabilities";
import {
  admissionFallbackView,
  runDemo,
  type AdmissionFailureStatus,
  type AdmissionFailureView,
  type DemoFlowResult,
  type DemoFlowServices,
  type DemoProgress,
} from "../journey/demoFlow";
import { stageInfoFromPhase, type StageInfo } from "../journey/generationProgress";

/**
 * "/generating" — generation progress UX (Phase 8 C, REQUIREMENTS 3.2).
 *
 * Reads the in-memory journey context (see src/journey/context.ts), runs the
 * demo journey state machine and renders the player-friendly staged progress:
 * "Creating case" -> "Building world" -> "Generating evidence" -> "Checking
 * consistency" -> "Preparing investigation". The POST /cases provider usually
 * completes synchronously, so the staged labels animate briefly while the
 * request is in flight and then settle on the REAL server-reported
 * PUBLISHED/FAILED status — never a fabricated success.
 *
 * Phase 16.2 §21 — the stored generation mode (`pd_generation_mode`) travels
 * through the flow inside every progress snapshot and switches the label
 * sequence: `local` uses the seven Local-AI labels (Understanding the case…
 * → … → Preparing the investigation…); demo/unset keeps the generic labels
 * above.
 *
 * Phase 21 F-03 — no user action writes `pd_generation_mode` anymore (the
 * interactive provider selector was removed; the backend provider is
 * process-global). The key can still hold values left by OLDER app versions
 * or injected by the QA storage seam, so the read + validation below stay as
 * defense-in-depth.
 *
 * ADV-212 — the stored mode is NEVER trusted by itself: before any Local-AI
 * label can be claimed the journey validates the mode against the LIVE
 * generation-capabilities DTO (fetched here in runJourney / the journey
 * effect). The `local` sequence is used ONLY when the capability report
 * confirms the local pipeline is actually available; an unavailable mode, a
 * stale/tampered storage value or a fetch failure all fall back to the
 * generic/demo label sequence — the stored value remains the label source
 * ONLY when capabilities confirm it.
 *
 * On PUBLISHED the journey stores {pd_playthrough_token, pd_playthrough_id}
 * (reuse playthroughToken.ts) and navigates to /scene (automatic after a
 * short beat, or immediately via "Enter investigation"). On FAILED / network
 * errors the page shows a clear safe message with a Retry action and a
 * Back-to-start link. A 429 ADMISSION_DENIED run is handled REASON-AWARE
 * (Phase 36 §13-§17/§29): the backend's closed `reasonCode` selects a
 * recovery screen that either clears an exhausted-session cache
 * (SESSION_GENERATION_LIMIT / SESSION_EXPIRED_OR_INVALID -> "Back to start",
 * no auto-mint, no reload) or PRESERVES a still-valid session and offers
 * "Try again" (session/global concurrency, global window, anonymous-session
 * capacity, unknown/legacy fallback -> the retry reuses the SAME valid
 * session; no new anonymous session is minted). The REAL durable
 * expired-session answer `401 SESSION_EXPIRED` (the auth dependency, before
 * any admission layer) is mapped by runDemo to the SAME session-recovery
 * view (DEF-082) and reaches this exact "Back to start" path. The
 * reload-based recovery of Phase 24 F-2 is REMOVED: a browser reload is not
 * a quota/session reset (Phase 36 §6/§7/§22). A hard refresh (no journey
 * context) shows a friendly "start again" state. No prompts, diagnostics or
 * provider details are ever shown.
 */

const DEMO_SERVICES: DemoFlowServices = {
  // Phase 24 P0 §8 — the route mints at most ONE anonymous session per page
  // lifetime (the module-level in-memory holder), so a retry / re-run no
  // longer creates a second session (churn fix); combined with the journey
  // context token this keeps the bridge-pairing session identity stable.
  //
  // Phase 36 — the holder is cleared ONLY when the denial reason PROVES the
  // session identity is exhausted/invalid (SESSION_GENERATION_LIMIT /
  // SESSION_EXPIRED_OR_INVALID — the decision travels in the
  // AdmissionFailureView.clearSessionCache flag). Transient denials
  // (concurrency / global window / anonymous-session capacity / unknown
  // fallback) PRESERVE the holder so an explicit "Try again" reuses the
  // SAME valid session — never a second anonymous identity.
  createSession: createOrReuseAnonymousSession,
  createCase,
  pollGeneration: getGenerationProgress,
  createPlaythrough,
};

/** Injectable live capability probe (real route: GET /generation-capabilities). */
export type CapabilityLoader = () => Promise<GenerationCapabilitiesResponse>;

export const DEFAULT_CAPABILITY_LOADER: CapabilityLoader = loadGenerationCapabilities;

/**
 * ADV-212 — resolve the label-driving journey mode against the LIVE
 * capability DTO. A fetch failure (loader rejection / endpoint down) always
 * degrades to the mode-independent value, so the generic/demo sequence is
 * shown and no local pipeline is ever claimed without backend confirmation.
 */
export async function resolveJourneyMode(
  loader: CapabilityLoader = DEFAULT_CAPABILITY_LOADER,
  storedMode: GenerationModeId | null = getGenerationMode(),
): Promise<GenerationModeId | null> {
  try {
    const capabilities: GenerationCapabilitiesResponse = await loader();
    return validatedJourneyMode(storedMode, capabilities);
  } catch {
    return null; // generic labels — never a local claim on a probe failure
  }
}

/**
 * Run the demo journey with the ADV-212 VALIDATED mode (computed against the
 * live capability report) — never the raw storage value.
 */
function runJourney(
  prompt: string,
  difficulty: string,
  onProgress: (progress: DemoProgress) => void,
  mode: GenerationModeId | null,
  anonymousSessionToken?: string,
  generation?: CreateCaseGeneration,
): Promise<DemoFlowResult> {
  return runDemo(prompt, {
    services: DEMO_SERVICES,
    difficulty,
    mode,
    onProgress,
    anonymousSessionToken,
    generation,
  });
}

export type RunFn = (
  prompt: string,
  difficulty: string,
  onProgress: (progress: DemoProgress) => void,
  /** ADV-212 — the mode validated against live capabilities (never raw storage). */
  mode: GenerationModeId | null,
  /**
   * Phase 24 P0 — the in-memory anonymous-session bearer carried from /new
   * (the session that paired the bridge). runDemo reuses it for POST /cases
   * and skips createSession(); undefined keeps the fresh-mint behavior.
   */
  anonymousSessionToken?: string,
  /**
   * Phase 25 — the optional browser-selected generation block carried from
   * /new into POST /cases (see {@link CreateCaseGeneration}). undefined keeps
   * the byte-identical no-selection request (§13).
   */
  generation?: CreateCaseGeneration,
) => Promise<DemoFlowResult>;

/**
 * Phase 16.2 §21 — pure, mode-aware stage model for one journey progress
 * snapshot. The mode travels inside {@link DemoProgress.mode} (the ADV-212
 * validated mode from `runJourney`); demo/unset resolves to the generic
 * staged labels exactly as before.
 */
export function stageFromProgress(progress: DemoProgress): StageInfo {
  return stageInfoFromPhase(
    progress.phase,
    progress.status,
    progress.stage,
    progress.progress,
    progress.mode,
  );
}

export default function GeneratingPage() {
  const navigate = useNavigate();
  const params = getJourneyParams();
  const anonymousSessionToken = params?.anonymousSessionToken;
  // Phase 24 P0 §2 — when the journey carries the pairing session bearer, the
  // capability probe is made AUTHENTICATED so the backend scopes the truthful
  // remoteLocalAi block to THIS session. The unauthenticated default call
  // stays untouched for every other consumer (cross-session isolation is
  // server-side and intentional).
  const capabilityLoader: CapabilityLoader = anonymousSessionToken
    ? () =>
        loadGenerationCapabilities(() =>
          getGenerationCapabilitiesWithSession(anonymousSessionToken),
        )
    : DEFAULT_CAPABILITY_LOADER;

  return (
    <GenerationJourney
      params={params}
      run={runJourney}
      loadCapabilities={capabilityLoader}
      onSuccess={(result) => {
        setPlaythroughToken(result.playthroughToken);
        setPlaythroughId(result.playthroughId);
        clearJourneyParams();
        navigate("/scene");
      }}
    />
  );
}

export interface GenerationJourneyProps {
  /** Journey params from the in-memory context (null after a hard refresh). */
  params: JourneyParams | null;
  /** Injectable journey runner (the real route wires runDemo + api client). */
  run: RunFn;
  /** Called with the new credentials — stores them and navigates to /scene. */
  onSuccess: (result: Extract<DemoFlowResult, { ok: true }>) => void;
  /**
   * ADV-212 — injectable live capability probe used to VALIDATE the stored
   * journey mode before any label is chosen (defaults to the real endpoint).
   */
  loadCapabilities?: CapabilityLoader;
  /**
   * Phase 28 §17 — the "Back to start" action: leaves/ends the current demo
   * journey and resets the per-session demo-case holder (the NEXT "Try Demo
   * Case" may then roll a fresh fixture; a refresh of an ACTIVE demo never
   * goes through this path, so its fixture is never re-rolled).
   */
  onBackToStart?: () => void;
}

/**
 * Phase 36 §29 — the reason-aware JourneyView. A 429 ADMISSION_DENIED run is
 * rendered by the closed admission status selected in demoFlow.ts
 * (mapAdmissionReason); the route never invented a generic session-limit
 * state anymore. `no-session` / `running` / `done` / generic `error` are
 * untouched.
 */
export type JourneyView =
  | { status: "no-session" }
  | { status: "running"; stage: StageInfo }
  | { status: "done"; result: Extract<DemoFlowResult, { ok: true }> }
  | { status: "error"; kind: string; message: string }
  | { status: AdmissionFailureStatus; admission: AdmissionFailureView };

export function GenerationJourney({
  params,
  run,
  onSuccess,
  loadCapabilities = DEFAULT_CAPABILITY_LOADER,
  onBackToStart = clearSessionDemoCaseId,
}: GenerationJourneyProps) {
  const [view, setView] = useState<JourneyView>(() =>
    params === null
      ? { status: "no-session" }
      // ADV-212: the pre-poll animation starts with the GENERIC label — a
      // local label appears only after the live capability report confirms
      // the local pipeline is actually available (see the effect below).
      : { status: "running", stage: stageInfoFromPhase("session", null, null, null, null) },
  );
  const [runId, setRunId] = useState(0);

  useEffect(() => {
    if (params === null) return;
    let cancelled = false;
    // ADV-212: the stored mode (pd_generation_mode) is NEVER trusted raw. The
    // label-driving mode is resolved against the LIVE capability DTO first;
    // an unavailable mode, a stale/tampered value or a probe failure all fall
    // back to the generic/demo label sequence. The same validated mode drives
    // both the pre-poll animation and every progress snapshot in the run.
    void resolveJourneyMode(loadCapabilities).then((mode) => {
      if (cancelled) return;
      setView({ status: "running", stage: stageInfoFromPhase("session", null, null, null, mode) });
      // Phase 25 — build the optional generation-selection block from the
      // journey params ONLY when a provider was actually carried from /new
      // (transport/model travel only for Ollama). Phase 28 — a demo-path
      // `demoCaseId` travels inside the SAME selection object (demo-only; the
      // blank generated-case journey omits both -> undefined -> the
      // byte-identical no-selection POST /cases request, §13).
      let generation: CreateCaseGeneration | undefined;
      const providerGeneration: CreateCaseGeneration | undefined =
        params.generationProvider !== undefined
          ? toCreateCaseGeneration({
              generationProvider: params.generationProvider,
              ollamaTransport: params.ollamaTransport ?? null,
              ollamaModel: params.ollamaModel ?? "",
              // Phase 30 — the Frontier BYOK fields travel ONLY for a
              // frontier journey: the trusted provider id + model (non-secret)
              // and the MEMORY-ONLY API key carried inside JourneyParams
              // (§15/§24). `toCreateCaseGeneration` emits the `frontier`
              // block only when the selection is complete; otherwise the
              // POST carries the flat `{generationProvider:"frontier"}` and
              // the backend fail-closes with the safe INVALID_FRONTIER_CONFIG copy.
              frontierProviderId: params.frontierProviderId ?? null,
              frontierModel: params.frontierModel ?? "",
              frontierApiKey: params.frontierApiKey ?? "",
            })
          : undefined;
      if (providerGeneration !== undefined || params.demoCaseId !== undefined) {
        generation = {
          ...(providerGeneration ?? {}),
          ...(params.demoCaseId !== undefined ? { demoCaseId: params.demoCaseId } : {}),
        };
      }
      void run(params.prompt, params.difficulty, (progress) => {
        if (cancelled) return;
        setView({
          status: "running",
          stage: stageFromProgress(progress),
        });
      }, mode, params.anonymousSessionToken, generation).then((result) => {
        if (cancelled) return;
        if (result.ok) {
          setView({ status: "done", result });
        } else if (result.failure.kind === "quota") {
          // Phase 36 — a 429 ADMISSION_DENIED run is REASON-AWARE. The
          // failure carries the closed recovery view (mapAdmissionReason in
          // demoFlow.ts): clear the anonymous-session cache ONLY when the
          // denial PROVES the current session identity is exhausted/invalid
          // (clearSessionCache — set exactly for SESSION_GENERATION_LIMIT /
          // SESSION_EXPIRED_OR_INVALID and the REAL durable 401 SESSION_EXPIRED
          // path mapped by mapDemoError, DEF-082). Every transient denial
          // (session/global concurrency, global window, anonymous-session
          // capacity, unknown/legacy fallback) PRESERVES the cache so an
          // explicit "Try again" reuses the same valid session. NO auto-mint,
          // NO auto-retry loop, NO reload — the server stays authoritative.
          const admission =
            result.failure.admissionReason ?? admissionFallbackView();
          if (admission.clearSessionCache) {
            resetAnonymousSessionCache();
          }
          setView({ status: admission.status, admission });
        } else {
          setView({ status: "error", kind: result.failure.kind, message: result.failure.message });
        }
      });
    });
    return () => {
      cancelled = true;
    };
  }, [params, run, runId, loadCapabilities]);

  // Automatic entry into the investigation shortly after PUBLISHED.
  useEffect(() => {
    if (view.status !== "done") return;
    const timer = window.setTimeout(() => {
      onSuccess(view.result);
    }, 900);
    return () => window.clearTimeout(timer);
  }, [view, onSuccess]);

  const retry = () => {
    setRunId((id) => id + 1);
  };

  const enter = () => {
    if (view.status === "done") onSuccess(view.result);
  };

  return <GenerationJourneyView view={view} onEnter={enter} onRetry={retry} onBackToStart={onBackToStart} />;
}

export interface GenerationJourneyViewProps {
  view: JourneyView;
  onEnter: () => void;
  onRetry: () => void;
  /**
   * Phase 28 §17 — the "Back to start" action (also resets the per-session
   * demo-case holder when the real route leaves the current demo). The pure
   * renderer keeps a no-op default; the real route wires the reset.
   */
  onBackToStart?: () => void;
}

/**
 * Pure renderer for the generation route states — exported separately so the
 * states are unit-testable with react-dom/server (no effects, no network).
 */
export function GenerationJourneyView({
  view,
  onEnter,
  onRetry,
  onBackToStart = () => {},
}: GenerationJourneyViewProps) {
  if (view.status === "no-session") {
    return (
      <section className="page generating">
        <h2>Generating case</h2>
        <div className="generation-state" data-testid="generation-no-session" role="status">
          <p>No generation is in progress on this page.</p>
          <p>
            {/* Phase 28 — wrap so the synthetic event never leaks into
                clearSessionDemoCaseId (it would be read as its storage arg). */}
            <Link to="/new" data-testid="generation-back-to-start" onClick={() => onBackToStart()}>
              Back to start
            </Link>
          </p>
        </div>
      </section>
    );
  }

  const admissionView =
    view.status === "session-generation-limit" ||
    view.status === "session-concurrency-limit" ||
    view.status === "global-concurrency-limit" ||
    view.status === "global-window-limit" ||
    view.status === "temporary-capacity-limit"
      ? view.admission
      : null;

  if (admissionView !== null) {
    // Phase 36 §29-§30 — reason-aware admission recovery. Every denial is a
    // normal SPA state: "Back to start" navigates to /new (no mint), and —
    // when retryable — "Try again" re-runs the journey with the SAME
    // (preserved) anonymous session token. There is deliberately NO "Reload
    // page" / window.location.reload() anymore — a reload is not a
    // quota/session reset (Phase 36 §6/§7/§22).
    return (
      <section className="page generating">
        <h2>{admissionView.heading}</h2>
        <div
          className={`generation-state generation-state--admission generation-state--${admissionView.status}`}
          data-testid={`generation-admission-${admissionView.status}`}
          role="status"
        >
          <p className="generation-error-message">{admissionView.message}</p>
          <div className="generation-actions">
            {admissionView.retryable ? (
              <button
                type="button"
                data-testid="generation-admission-retry"
                onClick={onRetry}
              >
                Try again
              </button>
            ) : null}
            {/* Phase 28 — wrap so the synthetic event never reaches the
                demo-holder reset (its storage parameter). */}
            <Link to="/new" data-testid="generation-back-to-start" onClick={() => onBackToStart()}>
              Back to start
            </Link>
          </div>
        </div>
      </section>
    );
  }

  if (view.status === "error") {
    return (
      <section className="page generating">
        <h2>Generating case</h2>
        <div className="generation-state generation-state--error" data-testid="generation-failed" role="alert">
          <p className="generation-error-message">{view.message}</p>
          <div className="generation-actions">
            <button type="button" data-testid="generation-failed" onClick={onRetry}>
              Try again
            </button>
            {/* Phase 28 — wrap so the synthetic event never reaches the
                demo-holder reset (its storage parameter). */}
            <Link to="/new" data-testid="generation-back-to-start" onClick={() => onBackToStart()}>
              Back to start
            </Link>
          </div>
        </div>
      </section>
    );
  }

  if (view.status === "done") {
    return (
      <section className="page generating">
        <h2>Generating case</h2>
        <div className="generation-state generation-state--done" role="status">
          <p className="generation-done">
            <strong>Your investigation is ready.</strong>
          </p>
          <button
            type="button"
            className="generation-enter"
            data-testid="enter-investigation"
            onClick={onEnter}
          >
            Enter investigation
          </button>
        </div>
      </section>
    );
  }

  if (view.status === "running") {
    return (
      <section className="page generating">
        <h2>Generating case</h2>
        <div className="generation-state" data-testid="generation-progress" role="status">
          <p className="generation-stage-label" data-testid="generation-stage-label">
            {view.stage.label}
          </p>
          <div
            className="generation-progress-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={view.stage.progress}
            aria-label={`Generation progress: ${view.stage.label}`}
          >
            <div
              className="generation-progress-fill"
              data-testid="generation-progress"
              style={{ width: `${view.stage.progress}%` }}
            />
          </div>
          <p className="generation-progress-note" data-testid="generation-progress-note">
            Building the case from your prompt…
          </p>
        </div>
      </section>
    );
  }

  // Exhaustive guard: every JourneyView status returns above.
  return (
    <section className="page generating">
      <h2>Generating case</h2>
      <div className="generation-state" data-testid="generation-no-session" role="status">
        <p>No generation is in progress on this page.</p>
      </div>
    </section>
  );
}