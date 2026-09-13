import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getInvestigation, getReveal } from "../api/client";
import { clearPlaythroughCredentials, getPlaythroughId, getPlaythroughToken } from "../api/playthroughToken";
import RevealScreen from "../reveal/RevealScreen";
import { RevealFlow, type RevealPageState } from "../reveal/revealFlow";

/**
 * "/reveal" — the end-of-case reveal page (Phase 7 E/L).
 *
 * Always re-fetches GET /reveal from the stored credential and renders the
 * DTO-driven RevealScreen. Because the reveal endpoint is available from
 * ACCUSED and REVEALED states and is idempotent, a reload right after
 * submitting or after a previous reveal restores the SAME screen from the
 * DTO. Direct navigation is gated client-side by the server's 403
 * REVEAL_NOT_AVAILABLE (never accused) and by the standard error envelope for
 * expired credentials. The reveal is never fetched anywhere reachable before
 * the player has submitted their own accusation.
 */
export default function RevealPage() {
  const [initialNoToken, setInitialNoToken] = useState(() => !hasStoredCredential());
  const [reloadKey, setReloadKey] = useState(0);
  const [state, setState] = useState<RevealPageState>({ status: "loading" });

  useEffect(() => {
    const token = getPlaythroughToken();
    const playthroughId = getPlaythroughId();
    if (!token || !playthroughId) {
      setInitialNoToken(true);
      return;
    }
    setInitialNoToken(false);
    let cancelled = false;
    setState({ status: "loading" });
    const flow = new RevealFlow(
      { getReveal, getInvestigation },
      token,
      { playthroughId },
    );
    void flow.load().then((next) => {
      if (!cancelled) setState(next);
    });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const resetCredential = () => {
    clearPlaythroughCredentials();
    setInitialNoToken(true);
    setState({ status: "loading" });
  };

  return (
    <section className="page reveal">
      {initialNoToken && <NoTokenState />}

      {!initialNoToken && state.status === "loading" && (
        <p className="reveal-loading" data-testid="reveal-loading" role="status">
          Loading the case reveal…
        </p>
      )}

      {!initialNoToken && state.status === "revealed" && (
        <RevealScreen reveal={state.reveal} candidates={state.candidates} />
      )}

      {!initialNoToken && state.status === "error" && (
        <RevealErrorState state={state} onRetry={() => setReloadKey((n) => n + 1)} onResetToken={resetCredential} />
      )}
    </section>
  );
}

function RevealErrorState({
  state,
  onRetry,
  onResetToken,
}: {
  state: Extract<RevealPageState, { status: "error" }>;
  onRetry: () => void;
  onResetToken: () => void;
}) {
  return (
    <div className={`reveal-error reveal-error--${state.kind}`} data-testid="reveal-error" role="alert">
      <h3>{state.kind === "not-accused" ? "The truth is still sealed" : "The reveal is not available"}</h3>
      <p>{state.message}</p>
      {state.kind === "not-accused" && (
        <p>
          <Link to="/scene" data-testid="reveal-back-to-scene">
            Continue investigating
          </Link>{" "}
          ·{" "}
          <Link to="/accuse" data-testid="reveal-back-to-accuse">
            Make an accusation
          </Link>
        </p>
      )}
      {state.tokenInvalid && (
        <button type="button" data-testid="reveal-reset-token" onClick={onResetToken}>
          Reset playthrough access
        </button>
      )}
      {state.retryable && !state.tokenInvalid && (
        <button type="button" data-testid="reveal-retry" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

function NoTokenState() {
  return (
    <div className="reveal-no-token" data-testid="reveal-no-token">
      <p>No playthrough access is configured on this device.</p>
      <p>
        Add your playthrough id and access token on the <Link to="/">Home</Link> page, then return
        here to reveal the case.
      </p>
    </div>
  );
}

function hasStoredCredential(): boolean {
  const token = getPlaythroughToken();
  const playthroughId = getPlaythroughId();
  return token !== null && token !== "" && playthroughId !== null && playthroughId !== "";
}