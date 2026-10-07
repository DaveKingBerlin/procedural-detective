import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { getInvestigation, getReveal, getSavegame } from "../api/client";
import { clearPlaythroughCredentials, getPlaythroughId, getPlaythroughToken } from "../api/playthroughToken";
import RevealScreen from "../reveal/RevealScreen";
import { RevealFlow, type RevealPageState } from "../reveal/revealFlow";
import { activeReplay, clearReplay } from "../savegame/replaySession";
import { buildReplayServices } from "../savegame/replayRuntime";
import { REVEAL_SPOILER_NOTE, SAVE_CASE_PROMPT_BODY, SAVE_CASE_PROMPT_TITLE } from "../savegame/savegameCopy";
import { REPLAY_PLAYTHROUGH_ID } from "../savegame/savegameV1";
import {
  downloadSavegameText,
  reExportV1,
  savegameFilenameFor,
  serializeSavegameV1,
} from "../savegame/exportV1";

/**
 * "/reveal" — the end-of-case reveal page (Phase 7 E/L) + Phase 32 Save Case.
 *
 * Live playthroughs ALWAYS re-fetch GET /reveal from the stored credential
 * and render the DTO-driven RevealScreen (unchanged behavior). A saved-case
 * replay serves the same RevealFlow from the in-memory runtime (zero
 * network). Once `state.status === "revealed"`, the Save Case prompt
 * (Phase32 §25) becomes available:
 *
 *   - live playthrough -> GET .../savegame (the server-owned allowlist text)
 *     and download the `.pdcase` with the endpoint's suggested filename;
 *   - loaded replay    -> a clean FRESH export from the normalized in-memory
 *     definition (Phase32 §15) — no server round-trip.
 *
 * The required spoiler note (Phase32 §7) accompanies the prompt. Download is
 * NEVER forced: "Not now" dismisses.
 */

export default function RevealPage() {
  const navigate = useNavigate();
  /** Phase 32 — the active saved replay (null during a live playthrough). */
  const replay = activeReplay() ?? undefined;
  const [initialNoToken, setInitialNoToken] = useState(() => {
    if (replay !== undefined) return false;
    return !hasStoredCredential();
  });
  const [reloadKey, setReloadKey] = useState(0);
  const [state, setState] = useState<RevealPageState>({ status: "loading" });

  useEffect(() => {
    const isReplay = replay !== undefined;
    const token = isReplay ? "" : getPlaythroughToken();
    const playthroughId = isReplay ? REPLAY_PLAYTHROUGH_ID : getPlaythroughId();
    if (!isReplay && (token === null || token === "" || !playthroughId)) {
      setInitialNoToken(true);
      return;
    }
    setInitialNoToken(false);
    let cancelled = false;
    setState({ status: "loading" });
    const services =
      isReplay && replay !== undefined
        ? (() => {
            const replayServices = buildReplayServices(replay.state);
            return {
              getReveal: replayServices.getReveal,
              getInvestigation: replayServices.getInvestigation,
            };
          })()
        : { getReveal, getInvestigation };
    const flow = new RevealFlow(services, token ?? "", { playthroughId: playthroughId ?? "" });
    void flow.load().then((next) => {
      if (!cancelled) setState(next);
    });
    return () => {
      cancelled = true;
    };
  }, [reloadKey, replay]);

  const resetCredential = () => {
    clearPlaythroughCredentials();
    setInitialNoToken(true);
    setState({ status: "loading" });
  };

  return (
    <section className="page reveal">
      {replay !== undefined && (
        <div className="replay-source-bar" data-testid="replay-source-bar">
          <span className="replay-source-label" data-testid="replay-source-label">
            Saved Case
          </span>
          <button
            type="button"
            className="replay-source-menu"
            data-testid="replay-back-to-menu"
            onClick={() => {
              clearReplay();
              void navigate("/");
            }}
          >
            Back to Main Menu
          </button>
        </div>
      )}

      {initialNoToken && <NoTokenState />}

      {!initialNoToken && state.status === "loading" && (
        <p className="reveal-loading" data-testid="reveal-loading" role="status">
          Loading the case reveal…
        </p>
      )}

      {!initialNoToken && state.status === "revealed" && (
        <>
          <RevealScreen reveal={state.reveal} candidates={state.candidates} />
          <SaveCasePrompt
            isReplay={replay !== undefined}
            caseIdForFilename={
              replay !== undefined ? replay.definition.metadata.sourceCaseId : state.reveal.caseId
            }
            playthroughId={replay !== undefined ? REPLAY_PLAYTHROUGH_ID : (getPlaythroughId() ?? state.reveal.playthroughId)}
            token={replay !== undefined ? "" : (getPlaythroughToken() ?? "")}
            replayDefinition={replay?.definition ?? null}
          />
        </>
      )}

      {!initialNoToken && state.status === "error" && (
        <RevealErrorState state={state} onRetry={() => setReloadKey((n) => n + 1)} onResetToken={resetCredential} />
      )}
    </section>
  );
}

/* ======================================================================
 * Phase 32 — Save Case prompt (Phase32 §25) + spoiler note (§7).
 * ==================================================================== */

interface SaveCasePromptProps {
  isReplay: boolean;
  /** Base for the suggested filename (server case id / imported display id). */
  caseIdForFilename: string;
  playthroughId: string;
  token: string;
  /** The active replay's normalized definition (local re-export) or null. */
  replayDefinition: import("../savegame/savegameV1").SavedCaseDefinition | null;
}

function SaveCasePrompt({
  isReplay,
  caseIdForFilename,
  playthroughId,
  token,
  replayDefinition,
}: SaveCasePromptProps) {
  const [dismissed, setDismissed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (dismissed) return null;

  const onSave = () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    void (async () => {
      try {
        let text: string;
        let filename: string;
        if (isReplay && replayDefinition !== null) {
          // Phase 32 §15 — clean fresh export from the normalized definition.
          text = serializeSavegameV1(reExportV1(replayDefinition));
          filename = savegameFilenameFor(caseIdForFilename);
        } else {
          // Live playthrough — the SERVER-owned allowlist export.
          const response = await getSavegame(playthroughId, token);
          text = response.text;
          filename = response.suggestedFilename;
        }
        downloadSavegameText(text, filename);
        setSaved(true);
      } catch {
        setError(
          "The savegame could not be exported right now. Please try again in a moment.",
        );
      } finally {
        setBusy(false);
      }
    })();
  };

  return (
    <div className="save-case-prompt" data-testid="save-case-prompt">
      <h3>{SAVE_CASE_PROMPT_TITLE}</h3>
      <p className="save-case-prompt-body">{SAVE_CASE_PROMPT_BODY}</p>
      <p className="save-case-spoiler-note" data-testid="save-case-spoiler-note">
        {REVEAL_SPOILER_NOTE}
      </p>
      {saved ? (
        <p className="save-case-saved" data-testid="save-case-saved" role="status">
          Savegame downloaded.
        </p>
      ) : (
        <div className="save-case-actions">
          <button type="button" data-testid="save-case-confirm" onClick={onSave} disabled={busy}>
            {busy ? "Saving…" : "Save Case"}
          </button>
          <button
            type="button"
            data-testid="save-case-dismiss"
            onClick={() => setDismissed(true)}
            disabled={busy}
          >
            Not now
          </button>
        </div>
      )}
      {error !== null && (
        <p className="save-case-error" data-testid="save-case-error" role="alert">
          {error}
        </p>
      )}
    </div>
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