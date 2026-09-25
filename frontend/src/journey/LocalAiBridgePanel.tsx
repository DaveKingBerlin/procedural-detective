import { useEffect, useRef, useState } from "react";
import type {
  AnonymousSessionResponse,
  BridgePairingResponse,
  BridgeStatusResponse,
  GenerationCapabilitiesResponse,
  RemoteLocalAiDTO,
} from "../api/types";
import { ApiError, createAnonymousSession, createBridgePairing, getBridgeStatus } from "../api/client";
import { isRemoteLocalAiOffered, remoteLocalAiBlock } from "./generationMode";
import {
  BRIDGE_CODE_LABEL,
  BRIDGE_CONNECT_BUTTON_LABEL,
  BRIDGE_CONNECTED_LINE,
  BRIDGE_DISCONNECTED_MESSAGE,
  BRIDGE_POLL_INTERVAL_MS,
  BRIDGE_READY_LINE,
  BRIDGE_BUSY_LINE,
  BRIDGE_REQUEST_NEW_CODE_LABEL,
  BRIDGE_SOURCE_LINE,
  BRIDGE_STATUS_NOT_CONNECTED,
  BRIDGE_WAIT_EXPIRED_MESSAGE,
  BRIDGE_WAIT_MAX_MS,
  BRIDGE_WAITING_LABEL,
  bridgeCliCommand,
  bridgeModelLine,
  initialBridgeView,
  nextBridgeView,
  pairingWaitingView,
  type BridgePanelView,
} from "./localAiBridge";

/**
 * Phase 22 — BYO-Ollama pairing/status panel (rendered ONLY on /new, inside
 * the generation-mode section).
 *
 * Renders truthful bridge lines from the PARSED capability DTO and, once the
 * user clicks [Connect local Ollama], drives the createBridgePairing ->
 * poll getBridgeStatus loop with a bounded connect-wait (every 2s, up to
 * {@link BRIDGE_WAIT_MAX_MS}); a real connection flips the panel to
 * "Local AI — Connected", a later connected:false flip shows the honest
 * disconnected message — the panel NEVER silently re-labels a run.
 *
 * Player separation (Phase 22 §36): this component is only ever mounted by
 * the /new creator route; the scene/accuse/reveal playthrough routes never
 * import it and never call the bridge client functions (a release-hygiene
 * scan enforces that at the source level), so a player browser cannot reach
 * pairing controls or bridge status beyond the safe case-level capability.
 * The panel itself only ever calls bridge endpoints with an anonymous session
 * token IT created (the backend additionally enforces the same
 * session-scoped auth).
 *
 * The browser NEVER talks to a local Ollama and never opens a bridge
 * WebSocket (Phase 22 §26): the code the server mints is displayed for the
 * user to type into the LOCAL BRIDGE CLI (`pd-ollama-bridge connect <code>`),
 * which owns the bridge<->server WS.
 *
 * When the capability DTO does NOT offer remoteLocalAi (ENABLE_BRIDGE=false
 * or an older server) this component renders NOTHING — the /new page stays
 * byte-identical to Phase 21B.
 */

/** Injectable endpoint set (unit tests substitute fakes; no network). */
export interface BridgePanelServices {
  createAnonymousSession(): Promise<AnonymousSessionResponse>;
  createBridgePairing(anonymousToken: string): Promise<BridgePairingResponse>;
  getBridgeStatus(anonymousToken: string): Promise<BridgeStatusResponse>;
}

export const DEFAULT_BRIDGE_SERVICES: BridgePanelServices = {
  createAnonymousSession,
  createBridgePairing,
  getBridgeStatus,
};

export interface LocalAiBridgePanelProps {
  /** The PARSED capability DTO (the route's own allowlist-parsed value). */
  capabilities: GenerationCapabilitiesResponse | null;
  /** Injectable endpoint set (defaults to the real api/client functions). */
  services?: BridgePanelServices;
  /** Test/QA seam: poll cadence (default 2s). */
  pollIntervalMs?: number;
  /** Test/QA seam: bounded connect-wait (default 2 minutes). */
  waitMaxMs?: number;
}

/** Safe, player-facing copy for a failed pairing request (never raw codes). */
export function bridgePairingActionError(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) {
    return "Too many pairing requests right now. Please try again later.";
  }
  return "Could not start the local bridge connection. Please try again.";
}

export default function LocalAiBridgePanel({
  capabilities,
  services = DEFAULT_BRIDGE_SERVICES,
  pollIntervalMs = BRIDGE_POLL_INTERVAL_MS,
  waitMaxMs = BRIDGE_WAIT_MAX_MS,
}: LocalAiBridgePanelProps) {
  // §36 gate: NOTHING renders unless the PARSED capability DTO offers the
  // bridge (ENABLE_BRIDGE=true and available:) — an absent block keeps every
  // page byte-identical to Phase 21B.
  if (!isRemoteLocalAiOffered(capabilities)) return null;

  const [view, setView] = useState<BridgePanelView>(() =>
    initialBridgeView(remoteLocalAiBlock(capabilities)),
  );
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const viewRef = useRef(view);
  viewRef.current = view;
  const tokenRef = useRef<string | null>(null);
  const timerRef = useRef<number | null>(null);
  /** Poll ticks since the current pairing started (elapsed = ticks * interval). */
  const ticksRef = useRef(0);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => clearTimer, []);

  /** One status poll — a transient failure yields null (keep current view). */
  const pollOnce = (token: string): Promise<RemoteLocalAiDTO | null> =>
    services
      .getBridgeStatus(token)
      .then((response) => response.remoteLocalAi)
      .catch(() => null);

  /** Schedule the next poll after `pollIntervalMs`. */
  const scheduleTick = (token: string) => {
    clearTimer();
    timerRef.current = window.setTimeout(() => tick(token), pollIntervalMs);
  };

  const tick = (token: string) => {
    void pollOnce(token).then((polled) => {
      const prev = viewRef.current;
      ticksRef.current += 1;
      const next = nextBridgeView(
        prev,
        polled,
        ticksRef.current * pollIntervalMs,
        waitMaxMs,
      );
      viewRef.current = next;
      setView(next);
      // Keep polling while waiting (bounded by waitMaxMs inside the reducer)
      // and while connected (to detect a later drop truthfully).
      if (next.state === "waiting" || next.state === "connected") {
        scheduleTick(token);
      }
    });
  };

  /** Start (or restart) the pairing flow: session -> code -> bounded polling. */
  const connect = () => {
    if (busy) return;
    if (viewRef.current.state === "waiting") return;
    setBusy(true);
    setActionError(null);
    ticksRef.current = 0;
    const haveSession =
      tokenRef.current !== null
        ? Promise.resolve({ anonymousSessionToken: tokenRef.current as string })
        : services.createAnonymousSession();
    void haveSession
      .then((session) => {
        tokenRef.current = session.anonymousSessionToken;
        return services.createBridgePairing(session.anonymousSessionToken);
      })
      .then((pairing) => {
        viewRef.current = pairingWaitingView(pairing.pairingCode);
        setView(viewRef.current);
        // Poll every `pollIntervalMs` up to the bounded wait.
        ticksRef.current = 0;
        scheduleTick(tokenRef.current as string);
      })
      .catch((error: unknown) => {
        setActionError(bridgePairingActionError(error));
      })
      .then(() => {
        setBusy(false);
      });
  };

  const code = view.code ?? "";
  const statusBlock = view.status;
  const modelLabel = bridgeModelLine(statusBlock?.model);

  return (
    <div className="local-ai-bridge" data-testid="local-ai-bridge-panel">
      {view.state === "idle" && (
        <>
          <p className="bridge-source-line">{BRIDGE_SOURCE_LINE}</p>
          <p className="bridge-status-line" data-testid="bridge-status-not-connected">
            {BRIDGE_STATUS_NOT_CONNECTED}
          </p>
          <button
            type="button"
            className="bridge-connect"
            data-testid="bridge-connect"
            disabled={busy}
            onClick={connect}
          >
            {BRIDGE_CONNECT_BUTTON_LABEL}
          </button>
        </>
      )}

      {view.state === "waiting" && (
        <>
          <p className="bridge-code-label">{BRIDGE_CODE_LABEL}</p>
          <p className="bridge-pairing-code" data-testid="bridge-pairing-code">
            {code}
          </p>
          <p className="bridge-cli-command" data-testid="bridge-cli-command">
            {bridgeCliCommand(code)}
          </p>
          <p className="bridge-waiting" data-testid="bridge-waiting">
            {BRIDGE_WAITING_LABEL}
          </p>
        </>
      )}

      {view.state === "wait-expired" && (
        <>
          <p className="bridge-code-label">{BRIDGE_CODE_LABEL}</p>
          <p className="bridge-pairing-code" data-testid="bridge-pairing-code">
            {code}
          </p>
          <p className="bridge-cli-command" data-testid="bridge-cli-command">
            {bridgeCliCommand(code)}
          </p>
          <p className="bridge-wait-expired" data-testid="bridge-wait-expired" role="status">
            {BRIDGE_WAIT_EXPIRED_MESSAGE}
          </p>
          <button
            type="button"
            className="bridge-connect"
            data-testid="bridge-request-new-code"
            disabled={busy}
            onClick={connect}
          >
            {BRIDGE_REQUEST_NEW_CODE_LABEL}
          </button>
        </>
      )}

      {view.state === "connected" && (
        <>
          <p className="bridge-connected" data-testid="bridge-connected">
            {BRIDGE_CONNECTED_LINE}
          </p>
          {modelLabel && (
            <p className="bridge-model" data-testid="bridge-model">
              {modelLabel}
            </p>
          )}
          <p className="bridge-ready" data-testid={statusBlock?.ready === true ? "bridge-ready" : "bridge-busy"}>
            {statusBlock?.ready === true ? BRIDGE_READY_LINE : BRIDGE_BUSY_LINE}
          </p>
        </>
      )}

      {view.state === "disconnected" && (
        <>
          <p className="bridge-disconnected" data-testid="bridge-disconnected" role="alert">
            {BRIDGE_DISCONNECTED_MESSAGE}
          </p>
          <button
            type="button"
            className="bridge-connect"
            data-testid="bridge-connect"
            disabled={busy}
            onClick={connect}
          >
            {BRIDGE_CONNECT_BUTTON_LABEL}
          </button>
        </>
      )}

      {actionError && (
        <p className="bridge-action-error" data-testid="bridge-action-error" role="alert">
          {actionError}
        </p>
      )}
    </div>
  );
}