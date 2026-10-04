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
  runDemo,
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
 * Back-to-start link. A quota-denied run (backend 429 ADMISSION_DENIED) shows
 * the Phase 24 F-2 session-limit recovery state instead (clear holder + reload
 * guidance — never an auto-mint). A hard refresh (no journey context) shows a
 * friendly "start again" state. No prompts, diagnostics or provider details
 * are ever shown.
 */

const DEMO_SERVICES: DemoFlowServices = {
  // Phase 24 P0 §8 — the route mints at most ONE anonymous session per page
  // lifetime (the module-level in-memory holder), so a retry / re-run no
  // longer creates a second session (churn fix); combined with the journey
  // context token this keeps the bridge-pairing session identity stable.
  createSession: createOrReuseAnonymousSession,
  createCase,
  pollGeneration: getGenerationProgress,
  createPlaythrough,
};

/**
 * Phase 24 F-2 — frozen recovery copy for a session-window-DENIED run.
 *
 * The backend sanitizes EVERY admission denial to the same safe 429
 * ADMISSION_DENIED envelope (per-session window exhausted, global window
 * exhausted, unknown/expired session) — no internal gate is ever revealed.
 * Under the P0 one-session-per-page holder a per-session window denial would
 * pin the page into "generation window exhausted" forever (every retry reuses
 * the same exhausted session). Recovery is therefore EXPLICIT: clear the
 * module holder and tell the user to reload the page, which starts a fresh
 * session — while the server-side per-IP generation budget stays untouched
 * (a fresh session still consumes it). NO auto-mint happens on the denial: the
 * server rate limit stays authoritative.
 */
export const SESSION_LIMIT_HEADING = "Generation limit reached";
export const SESSION_LIMIT_MESSAGE =
  "This page's generation session has reached its limit. Reload the page to start a fresh session.";
export const SESSION_LIMIT_RELOAD_LABEL = "Reload page";

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
   * Phase 24 F-2 — recovery action for a session-window-DENIED run (defaults
   * to a full page reload, which gives the next page a clean session without
   * ever auto-minting).
   */
  onReload?: () => void;
  /**
   * Phase 28 §17 — the "Back to start" action: leaves/ends the current demo
   * journey and resets the per-session demo-case holder (the NEXT "Try Demo
   * Case" may then roll a fresh fixture; a refresh of an ACTIVE demo never
   * goes through this path, so its fixture is never re-rolled).
   */
  onBackToStart?: () => void;
}

type JourneyView =
  | { status: "no-session" }
  | { status: "running"; stage: StageInfo }
  | { status: "done"; result: Extract<DemoFlowResult, { ok: true }> }
  | { status: "error"; kind: string; message: string }
  | { status: "session-limit" };

/** Phase 24 F-2 — recovery action for a session-denied run (a page reload). */
const reloadPage = (): void => {
  window.location.reload();
};

export function GenerationJourney({
  params,
  run,
  onSuccess,
  loadCapabilities = DEFAULT_CAPABILITY_LOADER,
  onReload = reloadPage,
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
          // Phase 24 F-2 — a session-window-DENIED run (the backend's sanitized
          // 429 ADMISSION_DENIED on POST /cases). Under the P0 one-session-per-
          // page holder a retry would reuse the SAME exhausted session and fail
          // forever; recover EXPLICITLY instead: clear the in-memory holder (a
          // RELOAD / next page then mints a clean session under the unchanged
          // server-side per-IP budget) and present the recover-by-reload state.
          // No auto-mint happens here — the server rate limit stays
          // authoritative.
          resetAnonymousSessionCache();
          setView({ status: "session-limit" });
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

  return <GenerationJourneyView view={view} onEnter={enter} onRetry={retry} onReload={onReload} onBackToStart={onBackToStart} />;
}

export interface GenerationJourneyViewProps {
  view: JourneyView;
  onEnter: () => void;
  onRetry: () => void;
  /**
   * Phase 24 F-2 — recovery action for the session-limit state (a page
   * reload by default; the parent injects it so the renderer stays pure).
   */
  onReload: () => void;
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
  onReload,
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

  if (view.status === "session-limit") {
    // Phase 24 F-2 — session-window-denied recovery: a page reload is the
    // recovery (a fresh page starts a clean session under the unchanged
    // server-side rate limit). This is the ONLY action besides Back to start —
    // deliberately NO "Try again" that would re-run the same exhausted session.
    return (
      <section className="page generating">
        <h2>{SESSION_LIMIT_HEADING}</h2>
        <div
          className="generation-state generation-state--session-limit"
          data-testid="generation-session-limit"
          role="status"
        >
          <p className="generation-error-message">{SESSION_LIMIT_MESSAGE}</p>
          <div className="generation-actions">
            <button
              type="button"
              data-testid="generation-session-limit-reload"
              onClick={onReload}
            >
              {SESSION_LIMIT_RELOAD_LABEL}
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