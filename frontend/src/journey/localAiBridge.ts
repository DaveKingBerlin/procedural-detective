import type { RemoteLocalAiDTO } from "../api/types";

/**
 * Phase 22 — BYO-Ollama bridge pairing/status panel: PURE LOGIC + COPY.
 *
 * The React component (src/journey/LocalAiBridgePanel.tsx) renders the /new
 * generation-mode section's truthful bridge lines and owns the timers; this
 * module is the side-effect-free half so the states, copy and bounds are
 * unit-testable with zero DOM/network.
 *
 * Hard guarantees:
 *   - the ONLY strings produced are frozen app-authored copy plus the
 *     sanitized model label from a parsed {@link RemoteLocalAiDTO} (which the
 *     allowlist parser already ran through `safeDisplay`); no raw server text,
 *     no URL/IP/host token can ever be rendered;
 *   - status is NEVER fabricated locally: every `connected`/`model`/`ready`
 *     value comes from the backend status block (or the capability DTO's
 *     session-scoped block); the panel only waits/polls, it never guesses;
 *   - the browser never talks to a local Ollama or a bridge WebSocket (Phase
 *     22 §26): the pairing code is displayed for the user to type into the
 *     LOCAL BRIDGE CLI (`pd-ollama-bridge connect <code>`), which opens the
 *     bridge<->server WS itself.
 *
 * States: idle (not connected) -> waiting (code displayed, polling) ->
 * connected -> disconnected (a poll after a real connection showed
 * connected:false — the run keeps its truthful label and the panel says so,
 * it never silently re-labels the run). waiting expires after a bounded
 * wait (the pairing code itself is short-lived server-side).
 */

/** "Local AI (Use Ollama running on this computer)" — the bridge source line. */
export const BRIDGE_SOURCE_LINE = "Local AI (Use Ollama running on this computer)";

/** "Status: Not connected" — the idle status line. */
export const BRIDGE_STATUS_NOT_CONNECTED = "Status: Not connected";

/** "[Connect local Ollama]" — the pairing CTA. */
export const BRIDGE_CONNECT_BUTTON_LABEL = "Connect local Ollama";

/** "Pairing code:" — heading above the prominent code display. */
export const BRIDGE_CODE_LABEL = "Pairing code:";

/** Prefix of the CLI instruction shown with the code (the code is appended). */
export const BRIDGE_CLI_COMMAND_PREFIX = "pd-ollama-bridge connect ";

/** "Waiting for connection…" — the paired-but-not-yet-connected state. */
export const BRIDGE_WAITING_LABEL = "Waiting for connection…";

/** "Local AI — Connected" — the connected state headline. */
export const BRIDGE_CONNECTED_LINE = "Local AI — Connected";

/** "Model: <label>" — prefix of the sanitized model line. */
export const BRIDGE_MODEL_PREFIX = "Model: ";

/** "Bridge: Ready" — the ready sub-line of the connected state. */
export const BRIDGE_READY_LINE = "Bridge: Ready";

/** "Bridge: Busy" — connected but not currently accepting a job (Phase 22 §19/§38). */
export const BRIDGE_BUSY_LINE = "Bridge: Busy";

/**
 * "Local AI disconnected — Reconnect the local bridge or use Deterministic
 * Demo." — shown when a real connection dropped (the panel never re-labels a
 * run that was started as Local AI).
 */
export const BRIDGE_DISCONNECTED_MESSAGE =
  "Local AI disconnected — Reconnect the local bridge or use Deterministic Demo.";

/** "The pairing code expired while waiting. Request a new code and reconnect." */
export const BRIDGE_WAIT_EXPIRED_MESSAGE =
  "The pairing code expired while waiting. Request a new code and reconnect.";

/** "[Request a new code]" — restarts pairing from the wait-expired state. */
export const BRIDGE_REQUEST_NEW_CODE_LABEL = "Request a new code";

/** Poll cadence while waiting/connected: every 2s (Phase 22 task bound). */
export const BRIDGE_POLL_INTERVAL_MS = 2000;

/** Bounded connect-wait: up to 2 minutes before the waiting state expires. */
export const BRIDGE_WAIT_MAX_MS = 120000;

/** The panel's lifecycle states (pure enum — the component renders one view). */
export type BridgePanelState =
  | "idle"
  | "waiting"
  | "connected"
  | "disconnected"
  | "wait-expired";

/** The panel's serialized view (pure, unit-testable). */
export interface BridgePanelView {
  state: BridgePanelState;
  /** The last minted pairing code (PD-XXXX-XXXX) or null before pairing. */
  code: string | null;
  /** The latest trustworthy server status block or null before any report. */
  status: RemoteLocalAiDTO | null;
}

/**
 * The initial panel view built ONLY from the sanitized capability block.
 * A block that already reports connected (an authenticated/re-visited fetch
 * or a test fixture) starts in the connected state; anything else starts
 * idle — the panel never invents a connection.
 */
export function initialBridgeView(block: RemoteLocalAiDTO | null): BridgePanelView {
  if (block === null) return { state: "idle", code: null, status: null };
  return block.connected === true
    ? { state: "connected", code: null, status: block }
    : { state: "idle", code: null, status: block };
}

/** The view after a pairing code was minted (waiting, polling begins). */
export function pairingWaitingView(code: string): BridgePanelView {
  return { state: "waiting", code, status: null };
}

/**
 * The pure state transition applied after each status poll.
 *
 *   - waiting: a connected poll -> connected (status becomes authoritative);
 *     a bounded-elapsed wait with no connection -> wait-expired (stop);
 *     otherwise keep waiting.
 *   - connected: a poll returning connected:false AFTER a real connection ->
 *     disconnected (the panel says the run's provider dropped; it never
 *     silently switches labels). Poll failures (null) keep the connection
 *     view — one transient network blip must not manufacture a drop.
 *   - idle / disconnected / wait-expired: no internal transitions (the
 *     component starts a new pairing only on user action).
 */
export function nextBridgeView(
  prev: BridgePanelView,
  polled: RemoteLocalAiDTO | null,
  elapsedMs: number,
  waitMaxMs: number = BRIDGE_WAIT_MAX_MS,
): BridgePanelView {
  if (prev.state === "waiting") {
    if (polled !== null && polled.connected === true) {
      return { state: "connected", code: prev.code, status: polled };
    }
    if (elapsedMs >= waitMaxMs) {
      return { state: "wait-expired", code: prev.code, status: polled };
    }
    return { state: "waiting", code: prev.code, status: polled };
  }
  if (prev.state === "connected") {
    if (polled !== null && polled.connected === false) {
      return { state: "disconnected", code: prev.code, status: polled };
    }
    return { state: "connected", code: prev.code, status: polled };
  }
  return prev;
}

/**
 * The CLI instruction shown under the pairing code: the frozen prefix plus
 * the server-minted code. The code is a display-only human-readable secret
 * the user types into the local bridge — the browser NEVER sends it anywhere.
 */
export function bridgeCliCommand(code: string): string {
  return BRIDGE_CLI_COMMAND_PREFIX + code;
}

/** The connected-state model line: "Model: <label>" when a model is known. */
export function bridgeModelLine(model: string | null | undefined): string | null {
  if (typeof model !== "string" || model === "") return null;
  return `${BRIDGE_MODEL_PREFIX}${model}`;
}