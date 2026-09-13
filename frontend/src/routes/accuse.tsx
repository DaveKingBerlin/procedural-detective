import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { ApiError, getInvestigation, getReveal, submitAccusation } from "../api/client";
import { clearPlaythroughCredentials, getPlaythroughId, getPlaythroughToken } from "../api/playthroughToken";
import type { InvestigationBootstrapResponse } from "../api/types";
import AccusationPanel from "../accusation/AccusationPanel";
import { AccusationFlow } from "../accusation/accusationFlow";
import { isAuthorisationFailure } from "../scene/investigationFlow";
import { parseInvestigationBootstrap } from "../scene/validation";

type AccusePageStatus =
  | { status: "loading" }
  | { status: "no-token" }
  | {
      status: "error";
      kind: "auth" | "network" | "gameplay" | "malformed";
      message: string;
      tokenInvalid: boolean;
      retryable: boolean;
    };

/**
 * "/accuse" — the accusation page (Phase 7 K).
 *
 * Loads the player-safe investigation bootstrap (the ONLY authorised source
 * of WHO/WHY/WEAPON/WHEN candidates) and, while the playthrough is PLAYING,
 * renders the accusation panel. The bootstrap's frozen lifecycle state also
 * drives graceful reload/restart behaviour: an already-ACCUSED playthrough
 * shows the already-submitted view (reveal available), and a REVEALED one
 * directs straight to the reveal screen. No reveal/truth endpoint is ever
 * called before the player submits their own accusation.
 */
export default function AccusationPage() {
  const navigate = useNavigate();
  const [runId, setRunId] = useState(0);
  const [status, setStatus] = useState<AccusePageStatus>(() =>
    hasStoredCredential() ? { status: "loading" } : { status: "no-token" },
  );
  const [bootstrap, setBootstrap] = useState<InvestigationBootstrapResponse | null>(null);

  useEffect(() => {
    const token = getPlaythroughToken();
    const playthroughId = getPlaythroughId();
    if (!token || !playthroughId) {
      setStatus({ status: "no-token" });
      return;
    }
    let cancelled = false;
    setBootstrap(null);
    setStatus({ status: "loading" });
    void getInvestigation(playthroughId, token).then(
      (raw) => {
        if (cancelled) return;
        try {
          const parsed = parseInvestigationBootstrap.validate(raw);
          setBootstrap(parsed);
        } catch {
          setStatus({
            status: "error",
            kind: "malformed",
            message: "The case data is malformed and the accusation view cannot be shown.",
            tokenInvalid: false,
            retryable: false,
          });
        }
      },
      (error) => {
        if (!cancelled) setStatus(mapBootstrapError(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const resetCredential = () => {
    clearPlaythroughCredentials();
    setBootstrap(null);
    setStatus({ status: "no-token" });
  };

  const flow = useMemo(() => {
    if (bootstrap === null) return null;
    const token = getPlaythroughToken() ?? "";
    const playthroughId = getPlaythroughId() ?? bootstrap.playthroughId;
    if (bootstrap.state !== "PLAYING") return null;
    return new AccusationFlow(
      { submitAccusation, getReveal },
      token,
      { playthroughId },
      bootstrap.candidates,
      { onRevealAvailable: () => void navigate("/reveal") },
    );
  }, [bootstrap, navigate]);

  // Re-render whenever the flow's state machine advances (subscribe pattern —
  // the flow itself is a pure object, safe to use with react-dom/server tests).
  const [, setTick] = useState(0);
  useEffect(() => {
    if (flow === null) return;
    return flow.subscribe(() => setTick((tick) => tick + 1));
  }, [flow]);

  return (
    <section className="page accuse">
      <h2>Accusation</h2>
      <p className="accuse-intro">
        <Link to="/scene" data-testid="accuse-back-to-scene">
          Back to the investigation
        </Link>
      </p>

      {status.status === "no-token" && <NoTokenState />}

      {status.status === "loading" && (
        <p className="accuse-loading" data-testid="accuse-loading" role="status">
          Loading the accusation view…
        </p>
      )}

      {status.status === "error" && (
        <div className="accuse-error" data-testid="accuse-error" role="alert">
          <p>{status.message}</p>
          {status.tokenInvalid && (
            <button type="button" data-testid="accuse-reset-token" onClick={resetCredential}>
              Reset playthrough access
            </button>
          )}
          {status.retryable && !status.tokenInvalid && (
            <button type="button" data-testid="accuse-retry" onClick={() => setRunId((n) => n + 1)}>
              Try again
            </button>
          )}
        </div>
      )}

      {bootstrap !== null && bootstrap.state === "PLAYING" && flow !== null && (
        <AccusationPanel flow={flow} onReveal={() => void navigate("/reveal")} onResetToken={resetCredential} />
      )}

      {bootstrap !== null && bootstrap.state === "ACCUSED" && <AlreadySubmittedView onReveal={() => void navigate("/reveal")} />}

      {bootstrap !== null && bootstrap.state === "REVEALED" && <RevealedView onReveal={() => void navigate("/reveal")} />}
    </section>
  );
}

function AlreadySubmittedView({ onReveal }: { onReveal: () => void }) {
  return (
    <div className="accusation-already-submitted" data-testid="accusation-already-submitted" role="status">
      <h3>This case has already been submitted</h3>
      <p>
        An accusation is already on file for this playthrough, so the accusation page is
        closed. The case can now be revealed.
      </p>
      <button type="button" data-testid="reveal-case" onClick={onReveal}>
        Reveal the case
      </button>
    </div>
  );
}

function RevealedView({ onReveal }: { onReveal: () => void }) {
  return (
    <div className="accusation-revealed" data-testid="accusation-revealed" role="status">
      <h3>This case has already been revealed</h3>
      <p>The truth for this case is already available on the reveal screen.</p>
      <button type="button" data-testid="reveal-case" onClick={onReveal}>
        View the reveal
      </button>
    </div>
  );
}

function NoTokenState() {
  return (
    <div className="accuse-no-token" data-testid="accuse-no-token">
      <p>No playthrough access is configured on this device.</p>
      <p>
        Add your playthrough id and access token on the <Link to="/">Home</Link> page to make an
        accusation.
      </p>
    </div>
  );
}

function mapBootstrapError(error: unknown): AccusePageStatus {
  if (isAuthorisationFailure(error)) {
    return {
      status: "error",
      kind: "auth",
      message: "Playthrough access is not valid for this accusation view.",
      tokenInvalid: true,
      retryable: false,
    };
  }
  if (error instanceof ApiError) {
    if (error.status === 0) {
      return {
        status: "error",
        kind: "network",
        message: "The case data could not be loaded. Check that the backend is running, then try again.",
        tokenInvalid: false,
        retryable: true,
      };
    }
    if (error.status >= 500) {
      return {
        status: "error",
        kind: "gameplay",
        message: "The case service reported a temporary problem.",
        tokenInvalid: false,
        retryable: true,
      };
    }
  }
  return {
    status: "error",
    kind: "gameplay",
    message: "This playthrough cannot be accused right now.",
    tokenInvalid: false,
    retryable: false,
  };
}

function hasStoredCredential(): boolean {
  const token = getPlaythroughToken();
  const playthroughId = getPlaythroughId();
  return token !== null && token !== "" && playthroughId !== null && playthroughId !== "";
}