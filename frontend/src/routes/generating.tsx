import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  createAnonymousSession,
  createCase,
  createPlaythrough,
  getGenerationProgress,
} from "../api/client";
import { setPlaythroughId, setPlaythroughToken } from "../api/playthroughToken";
import { clearJourneyParams, getJourneyParams, type JourneyParams } from "../journey/context";
import { getGenerationMode } from "../journey/generationMode";
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
 * On PUBLISHED the journey stores {pd_playthrough_token, pd_playthrough_id}
 * (reuse playthroughToken.ts) and navigates to /scene (automatic after a
 * short beat, or immediately via "Enter investigation"). On FAILED / quota /
 * network errors the page shows a clear safe message with a Retry action and
 * a Back-to-start link. A hard refresh (no journey context) shows a friendly
 * "start again" state. No prompts, diagnostics or provider details are ever
 * shown.
 */

const DEMO_SERVICES: DemoFlowServices = {
  createSession: createAnonymousSession,
  createCase,
  pollGeneration: getGenerationProgress,
  createPlaythrough,
};

/** Stable across renders so the journey effect never re-fires. */
function runJourney(
  prompt: string,
  difficulty: string,
  onProgress: (progress: DemoProgress) => void,
): Promise<DemoFlowResult> {
  // Phase 16 Track B — the player-selected generation mode (stored under
  // `pd_generation_mode` by the landing//new selector) travels into the flow
  // as a note; the request-body contract is a backend-owned followup.
  return runDemo(prompt, {
    services: DEMO_SERVICES,
    difficulty,
    mode: getGenerationMode(),
    onProgress,
  });
}

type RunFn = (
  prompt: string,
  difficulty: string,
  onProgress: (progress: DemoProgress) => void,
) => Promise<DemoFlowResult>;

export default function GeneratingPage() {
  const navigate = useNavigate();
  const params = getJourneyParams();

  return (
    <GenerationJourney
      params={params}
      run={runJourney}
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
}

type JourneyView =
  | { status: "no-session" }
  | { status: "running"; stage: StageInfo }
  | { status: "done"; result: Extract<DemoFlowResult, { ok: true }> }
  | { status: "error"; kind: string; message: string };

export function GenerationJourney({ params, run, onSuccess }: GenerationJourneyProps) {
  const [view, setView] = useState<JourneyView>(() =>
    params === null
      ? { status: "no-session" }
      : { status: "running", stage: stageInfoFromPhase("session", null, null, null) },
  );
  const [runId, setRunId] = useState(0);

  useEffect(() => {
    if (params === null) return;
    let cancelled = false;
    setView({ status: "running", stage: stageInfoFromPhase("session", null, null, null) });
    void run(params.prompt, params.difficulty, (progress) => {
      if (cancelled) return;
      setView({
        status: "running",
        stage: stageInfoFromPhase(progress.phase, progress.status, progress.stage, progress.progress),
      });
    }).then((result) => {
      if (cancelled) return;
      if (result.ok) {
        setView({ status: "done", result });
      } else {
        setView({ status: "error", kind: result.failure.kind, message: result.failure.message });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [params, run, runId]);

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

  return <GenerationJourneyView view={view} onEnter={enter} onRetry={retry} />;
}

export interface GenerationJourneyViewProps {
  view: JourneyView;
  onEnter: () => void;
  onRetry: () => void;
}

/**
 * Pure renderer for the generation route states — exported separately so the
 * states are unit-testable with react-dom/server (no effects, no network).
 */
export function GenerationJourneyView({ view, onEnter, onRetry }: GenerationJourneyViewProps) {
  if (view.status === "no-session") {
    return (
      <section className="page generating">
        <h2>Generating case</h2>
        <div className="generation-state" data-testid="generation-no-session" role="status">
          <p>No generation is in progress on this page.</p>
          <p>
            <Link to="/new" data-testid="generation-back-to-start">
              Back to start
            </Link>
          </p>
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
            <Link to="/new" data-testid="generation-back-to-start">
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