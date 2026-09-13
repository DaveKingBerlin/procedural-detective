import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useOutletContext } from "react-router";
import type { BackendStatus } from "../hooks/useBackendStatus";
import {
  getPlaythroughToken,
  setPlaythroughId,
  setPlaythroughToken,
  validatePlaythroughToken,
} from "../api/playthroughToken";

/** "/" — welcome text, backend status, and the demo entry to an investigation. */
export default function Home() {
  const { state, message, readiness } = useOutletContext<BackendStatus>();

  return (
    <section className="page home">
      <h2>Welcome</h2>
      <p>
        Procedural Detective turns a short natural-language prompt into a complete, logically
        consistent 3D investigation. This app hosts the application routes and the 3D scene
        bootstrap.
      </p>

      <h3>Backend status</h3>
      <p>
        State:{" "}
        <span className={`status-text status-text--${state}`} data-testid="home-backend-status">
          {state}
        </span>
        <br />
        Message: <code data-testid="home-backend-message">{message}</code>
        {readiness && (
          <>
            <br />
            <span data-testid="home-readiness">
              Readiness: database {readiness.database} · migrations {readiness.migrations}
            </span>
          </>
        )}
      </p>

      <StartInvestigationPanel />

      <p>
        Try the <Link to="/scene">Scene</Link> route to walk through the placeholder apartment
        built from local 3D shapes.
      </p>
    </section>
  );
}

/**
 * Demo entry point for the Phase 6 investigation: the player pastes the
 * playthrough access token (issued when their playthrough was created) along
 * with its playthrough id. Both are persisted to localStorage — the token
 * under the contract-mandated key "pd_playthrough_token" — then the app
 * navigates to /scene. QA/E2E harnesses may seed localStorage directly
 * instead of using this form.
 */
function StartInvestigationPanel() {
  const navigate = useNavigate();
  const [idInput, setIdInput] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const [hint, setHint] = useState<string | null>(null);

  const savedToken = getPlaythroughToken();

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const playthroughId = idInput.trim();
    if (playthroughId === "") {
      setHint("Enter the playthrough id for this investigation.");
      return;
    }
    if (/\s/.test(playthroughId)) {
      setHint("Playthrough id cannot contain spaces.");
      return;
    }
    const validation = validatePlaythroughToken(tokenInput);
    if (!validation.ok) {
      setHint(validation.hint);
      return;
    }
    const normalized = validation.normalized;
    if (normalized === null) {
      setHint("Enter a playthrough access token.");
      return;
    }
    setPlaythroughId(playthroughId);
    setPlaythroughToken(normalized);
    setHint(null);
    navigate("/scene");
  };

  return (
    <div className="token-panel" data-testid="start-investigation-panel">
      <h3>Start an investigation</h3>
      <p className="token-panel-intro">
        Paste the playthrough id and the playthrough access token you were given when the
        playthrough was created. The token is only ever sent to the backend as a bearer
        credential and stays in your browser.
      </p>
      <form className="token-form" onSubmit={handleSubmit}>
        <label htmlFor="playthrough-id">Playthrough id</label>
        <input
          id="playthrough-id"
          data-testid="playthrough-id-input"
          value={idInput}
          onChange={(event) => setIdInput(event.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        <label htmlFor="playthrough-token">Playthrough access token</label>
        <input
          id="playthrough-token"
          data-testid="playthrough-token-input"
          value={tokenInput}
          onChange={(event) => setTokenInput(event.target.value)}
          autoComplete="off"
          spellCheck={false}
          type="password"
        />
        {hint && (
          <p className="token-hint" data-testid="token-hint" role="status">
            {hint}
          </p>
        )}
        {!hint && savedToken && savedToken !== "" && (
          <p className="token-hint token-hint--ok" data-testid="token-saved-hint" role="status">
            A playthrough access token is already saved on this device. Entering a new one
            replaces it.
          </p>
        )}
        <button type="submit" data-testid="start-investigation">
          Start investigation
        </button>
      </form>
    </div>
  );
}