import { useState } from "react";
import { Link, useNavigate, useOutletContext } from "react-router";
import type { BackendStatus } from "../hooks/useBackendStatus";
import { useGenerationCapabilities } from "../hooks/useGenerationCapabilities";
import type { GenerationCapabilitiesResponse, GenerationModeId } from "../api/types";
import { setJourneyParams } from "../journey/context";
import { EXAMPLE_PROMPT } from "../journey/demoPrompt";
import { getGenerationMode, setGenerationMode } from "../journey/generationMode";
import { GenerationModeSelector } from "../journey/generationModeSelector";
import { APP_PROVIDER_MODE, providerPathNote, providerQualifier } from "../journey/providerMode";

/**
 * "/" — the public landing page (Phase 8 D, REQUIREMENTS 3.1).
 *
 * A first-time visitor needs ZERO developer knowledge. The landing clearly
 * presents the TWO Phase 15 paths (Phase15.md "Demo mode"):
 *
 *   1. "Try Demo Case" — the deterministic, zero-cost demo journey: it runs
 *      immediately (through /generating) inside a real 3D investigation and
 *      is labelled as such ("Deterministic demo — no API keys, no cost").
 *   2. "Generate a New Mystery" — the SAME generation journey started from
 *      the /new prompt screen, emphasising a custom prompt. Mechanics are
 *      identical to the demo path: in the default build the backend answers
 *      with the deterministic generator (the note says so); when a live
 *      provider is configured at build time (VITE_APP_PROVIDER=live) the note
 *      says "Live AI provider" instead — the button label itself never claims
 *      live-AI behavior in fake mode.
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
  const [mode, setMode] = useState<GenerationModeId>(() => getGenerationMode() ?? "demo");

  const selectMode = (next: GenerationModeId) => {
    setMode(next);
    setGenerationMode(next);
  };

  const startDemo = () => {
    setJourneyParams({ prompt: EXAMPLE_PROMPT, difficulty: "medium" });
    navigate("/generating");
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
          Try Demo Case
        </button>
      </div>
      {/* ADV-152 — honest app-level provider qualifier right below the primary
          CTA: the REQUIREMENTS §62 tagline stays verbatim, and this note makes
          the deterministic default build's "AI" claim unambiguous (mode-aware,
          config-driven; never server text). */}
      <p className="provider-qualifier" data-testid="provider-qualifier">
        {providerQualifier(APP_PROVIDER_MODE)}
      </p>
      <p className="landing-path-note landing-path-note--demo" data-testid="try-demo-note">
        Deterministic demo — no API keys, no cost.
      </p>

      <div className="landing-actions landing-actions--generate">
        <Link
          className="landing-button landing-button--primary generate-path"
          to="/new"
          data-testid="generate-new-mystery"
        >
          Generate a New Mystery
        </Link>
      </div>
      <p className="landing-path-note landing-path-note--generate" data-testid="generate-provider-note">
        {providerPathNote(APP_PROVIDER_MODE)}
      </p>

      {/* Phase 16 Track B — generation-mode selector: always offers Demo and
          additionally Local AI / Cloud AI only when the backend reports them
          available; a demo-only backend is shown as the static
          "Demo mode active" notice instead. No host/IP, credentials, prompts
          or diagnostics are ever rendered — only frozen public labels. */}
      <GenerationModeSelector capabilities={capabilities} value={mode} onSelect={selectMode} />

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
        <a data-testid="github-link" href="https://github.com/">
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