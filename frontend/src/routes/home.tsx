import { Link, useNavigate, useOutletContext } from "react-router";
import type { BackendStatus } from "../hooks/useBackendStatus";
import { setJourneyParams } from "../journey/context";
import { EXAMPLE_PROMPT } from "../journey/demoPrompt";

/**
 * "/" — the public landing page (Phase 8 D, REQUIREMENTS 3.1).
 *
 * A first-time visitor needs ZERO developer knowledge: two primary actions
 * start the whole journey from the browser UI. "New Investigation" opens the
 * prompt screen; "Try Demo Case" runs the deterministic demo journey
 * immediately and lands (through /generating) inside a real 3D
 * investigation. The backend status indicator stays visible (data-testid
 * "backend-status" lives in the shell header; a compact copy is shown here
 * for demo troubleshooting).
 */
export interface HomeProps {
  /** Backend status override (unit tests inject a value; the route reads the shell context). */
  status?: BackendStatus;
}

export default function Home(overrides: HomeProps = {}) {
  const navigate = useNavigate();
  const outletStatus = overrides.status ?? useOutletContext<BackendStatus>();
  const { state, message, readiness } = outletStatus;

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