import { useState } from "react";
import { Link, useNavigate, useOutletContext } from "react-router";
import type { BackendStatus } from "../hooks/useBackendStatus";
import { useGenerationCapabilities } from "../hooks/useGenerationCapabilities";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { setJourneyParams, type JourneyParams } from "../journey/context";
import { rollDemoCaseId } from "../journey/demoCaseSelection";
import { EXAMPLE_PROMPT } from "../journey/demoPrompt";
import { demoCtaLabel, demoCtaState } from "../journey/generationMode";
import { loadSavegameFile } from "../savegame/loadCase";
import type { LoadCaseFile } from "../savegame/loadCase";
import { startReplay } from "../savegame/replaySession";

/**
 * "/" — the public landing page (Phase 8 D, REQUIREMENTS 3.1).
 *
 * A first-time visitor needs ZERO developer knowledge. The landing clearly
 * presents the three primary actions: start a new investigation, try the
 * example case, or load a saved case.
 *
 * The example/demo CTA label and request remain capability-aware. A confirmed
 * demo backend pins a demoCaseId; local, live, and unknown backends keep the
 * existing example-case request without one. No provider selection or backend
 * behavior is changed here.
 *
 * The legacy "New Investigation" entry stays as-is (it reaches the same
 * /new screen). The backend status indicator stays visible (data-testid
 * "backend-status" lives in the shell header; a compact copy is shown here
 * for demo troubleshooting).
 */
export interface HomeProps {
  /** Backend status override (unit tests inject a value; the route reads the shell context). */
  status?: BackendStatus;
  /**
   * Phase 16 Track B — generation-capabilities override (unit tests inject a
   * fixture; the route fetches + parses the public DTO at runtime).
   */
  capabilities?: GenerationCapabilitiesResponse | null;
}

export default function Home(overrides: HomeProps = {}) {
  const navigate = useNavigate();
  const outletStatus = overrides.status ?? useOutletContext<BackendStatus>();
  const fetchedCapabilities = useGenerationCapabilities();
  const capabilities =
    overrides.capabilities !== undefined ? overrides.capabilities : fetchedCapabilities;
  const { state, message, readiness } = outletStatus;
  // Phase 32 — `Load Case` intake state (bounded error copy only; the file
  // picker itself is the hidden native input below).
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadBusy, setLoadBusy] = useState(false);
  const [fileInputKey, setFileInputKey] = useState(0);

  const startDemo = () => {
    // Phase 28 — the random three-fixture Demo pool applies ONLY when the
    // backend reports the demo-only allowlist (`demoCtaState === "demo"` —
    // the exact state that earns the "Try Demo Case" label; the demo-only
    // backend is server-enforced). rollDemoCaseId also pins the selection
    // for the whole session. On a local/live/unknown backend the renamed
    // example-case action keeps its byte-identical request: the backend
    // rejects demoCaseId for any non-fake provider (400 INVALID_DEMO_CASE),
    // so the frontend never sends it unless the fake path is confirmed.
    const demoCaseId = demoCtaState(capabilities) === "demo" ? rollDemoCaseId() : undefined;
    const params: JourneyParams = { prompt: EXAMPLE_PROMPT, difficulty: "medium" };
    if (demoCaseId !== undefined) params.demoCaseId = demoCaseId;
    setJourneyParams(params);
    navigate("/generating");
  };

  // Phase 32 — load an uploaded `.pdcase`: size-precheck -> strict parse ->
  // fresh replay -> straight into the existing case player. Picker cancel is
  // not an error (no onChange file). The file value is reset afterwards so
  // the SAME file can be loaded repeatedly.
  const handleLoadFile = (file: LoadCaseFile | null) => {
    setLoadError(null);
    if (file === null) return;
    setLoadBusy(true);
    void loadSavegameFile(file).then((outcome) => {
      setLoadBusy(false);
      if (outcome.ok) {
        startReplay(outcome.definition);
        setFileInputKey((key) => key + 1);
        navigate("/scene");
      } else {
        setLoadError(outcome.message);
        setFileInputKey((key) => key + 1);
      }
    });
  };

  return (
    <section className="page home landing">
      <h2 className="landing-title">Procedural Detective</h2>
      <p className="landing-tagline">
        Describe a crime. AI builds a logically solvable 3D investigation.
      </p>

      <div className="landing-actions">
        <Link className="landing-button landing-button--primary" to="/new" data-testid="new-investigation">
          New Investigation
        </Link>
        <button
          type="button"
          className="landing-button"
          data-testid="try-demo"
          onClick={startDemo}
        >
          {demoCtaLabel(capabilities)}
        </button>
        {/* Phase 32 — Load Case: native file picker (accept .pdcase / the
            canonical MIME / plain JSON), strict import, fresh replay. */}
        <button
          type="button"
          className="landing-button"
          data-testid="load-case"
          onClick={() => {
            const input = document.getElementById("pd-load-case-input");
            if (input !== null && typeof (input as HTMLInputElement).click === "function") {
              (input as HTMLInputElement).click();
            }
          }}
        >
          Load Case
        </button>
        <input
          key={fileInputKey}
          id="pd-load-case-input"
          type="file"
          accept=".pdcase,application/vnd.procedural-detective.case+json,application/json"
          className="visually-hidden"
          data-testid="load-case-input"
          aria-label="Load a Procedural Detective savegame (.pdcase) file"
          onChange={(event) => {
            const file = event.target.files !== null ? event.target.files[0] : undefined;
            handleLoadFile(file ?? null);
          }}
        />
      </div>
      {/* Phase 32 — bounded load-error copy (Phase32 §26): cancel never
          renders here, busy never blocks the picker. */}
      {loadError !== null && (
        <p className="landing-load-error" data-testid="load-case-error" role="alert">
          {loadError}
        </p>
      )}
      {loadBusy && (
        <p className="landing-load-busy" data-testid="load-case-busy" role="status">
          Loading the saved case…
        </p>
      )}
      <div className="landing-panel">
        <h3>How it works</h3>
        <ul className="landing-bullets">
          <li>
            <strong>Describe a crime</strong> — your plain-language prompt
            becomes a full case with one hidden truth.
          </li>
          <li>
            <strong>Deterministic validation</strong> — the truth is checked by
            rules, so every case is solvable from the evidence, not a coin flip.
          </li>
          <li>
            <strong>Explore in 3D</strong> — investigate the scene, collect
            evidence, accuse a suspect, and reveal the truth.
          </li>
        </ul>
      </div>

      <div className="landing-panel">
        <h3>Controls</h3>
        <p>
          Drag to look around · scroll to zoom · click an object to interact ·
          use the object list as an alternative · Esc closes panels.
        </p>
      </div>

      <p className="landing-github">
        <a data-testid="github-link" href="https://github.com/DaveKingBerlin/procedural-detective">
          View on GitHub
        </a>
      </p>

      <BackendStatusPanel state={state} message={message} readiness={readiness} />
    </section>
  );
}

function BackendStatusPanel(status: BackendStatus) {
  return (
    <div className="landing-status" data-testid="home-backend-status">
      <h3>Backend status</h3>
      <p>
        State:{" "}
        <span className={`status-text status-text--${status.state}`}>{status.state}</span>
        <br />
        Message: <code data-testid="home-backend-message">{status.message}</code>
        {status.readiness && (
          <>
            <br />
            <span data-testid="home-readiness">
              Readiness: database {status.readiness.database} · migrations{" "}
              {status.readiness.migrations}
            </span>
          </>
        )}
      </p>
    </div>
  );
}
