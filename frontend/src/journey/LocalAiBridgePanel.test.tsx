// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { ApiError } from "../api/client";
import { parseGenerationCapabilities } from "./generationMode";
import {
  BRIDGE_WAIT_MAX_MS,
  BRIDGE_WAITING_LABEL,
  BRIDGE_CONNECTED_LINE,
  BRIDGE_CONNECT_BUTTON_LABEL,
  BRIDGE_DISCONNECTED_MESSAGE,
  BRIDGE_READY_LINE,
  BRIDGE_WAIT_EXPIRED_MESSAGE,
} from "./localAiBridge";
import LocalAiBridgePanel, {
  type BridgePanelServices,
  type LocalAiBridgePanelProps,
} from "./LocalAiBridgePanel";
import {
  clearJourneyParams,
  getJourneyParams,
  setJourneyParams,
  type JourneyParams,
} from "./context";

/**
 * Phase 22 — the /new BYO-Ollama pairing panel interaction (jsdom + fake
 * timers, mocked services): the §12/§13 flow "Not connected -> code display ->
 * polling -> Connected", the connected-after-drop message, and the default-OFF
 * gate (an absent `remoteLocalAi` block renders NOTHING).
 */

declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const OFFERED: GenerationCapabilitiesResponse = parseGenerationCapabilities({
  modes: [
    { id: "demo", available: true },
    { id: "local", available: false, label: "Local AI", model: "llama3.2:3b" },
  ],
  configuredProvider: "fake",
  remoteLocalAi: { available: true, connected: false, model: null, ready: false },
});

const NOT_OFFERED: GenerationCapabilitiesResponse = {
  modes: [{ id: "demo", available: true }],
  configuredProvider: "fake",
};

const ANON = "anon-session-for-pairing";
const CODE = "PD-X7K4-92QP";

function makeServices(overrides: Partial<BridgePanelServices> = {}): BridgePanelServices {
  return {
    createAnonymousSession: vi.fn(async () => ({
      anonymousSessionToken: ANON,
      quotaWindowEndsAt: 1e12,
    })),
    createBridgePairing: vi.fn(async () => ({
      pairingSessionId: "PAIR-0001",
      pairingCode: CODE,
      expiresAt: 1e12 + 240,
    })),
    getBridgeStatus: vi.fn(async () => ({
      remoteLocalAi: { available: true, connected: false, model: null, ready: false },
    })),
    ...overrides,
  };
}

function makeProps(overrides: Partial<LocalAiBridgePanelProps> = {}): LocalAiBridgePanelProps {
  return {
    capabilities: OFFERED,
    services: makeServices(),
    pollIntervalMs: 2000,
    waitMaxMs: BRIDGE_WAIT_MAX_MS,
    ...overrides,
  };
}

interface Mounted {
  container: HTMLDivElement;
  root: ReturnType<typeof createRoot>;
}

function mount(props: LocalAiBridgePanelProps): Mounted {
  const container = document.createElement("div");
  const root = createRoot(container);
  act(() => {
    root.render(<LocalAiBridgePanel {...props} />);
  });
  return { container, root };
}

function unmount(mounted: Mounted): void {
  act(() => {
    mounted.root.unmount();
  });
}

function click(element: Element | null): void {
  if (element === null) throw new Error("expected element for click (was not found)");
  act(() => {
    (element as HTMLElement).click();
  });
}

/** Flush pending microtasks (promise chains) inside act. */
async function settle(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

/** Advance the fake clock and fire the resulting poll ticks inside act. */
async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function query(mounted: Mounted, testid: string): Element | null {
  return mounted.container.querySelector(`[data-testid="${testid}"]`);
}

const textOf = (element: Element | null): string | null =>
  element === null ? null : element.textContent ?? null;

describe("Phase 22 — /new pairing panel (Not connected -> code -> connected)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Phase 24 P0/F-1 — the journey context is module-global; each test starts
    // with a clean context so token-attachment assertions are self-contained.
    clearJourneyParams();
  });

  afterEach(() => {
    clearJourneyParams();
    vi.useRealTimers();
  });

  it("renders NOTHING when the capability DTO does not offer remoteLocalAi (default OFF — byte-identical page)", () => {
    const mounted = mount(makeProps({ capabilities: NOT_OFFERED }));
    expect(query(mounted, "local-ai-bridge-panel")).toBeNull();
    unmount(mounted);
  });

  it("starts with the truthful not-connected line + [Connect local Ollama] button", () => {
    const mounted = mount(makeProps());
    expect(query(mounted, "local-ai-bridge-panel")).not.toBeNull();
    expect(textOf(query(mounted, "bridge-status-not-connected"))).toBe("Status: Not connected");
    expect(textOf(query(mounted, "bridge-connect"))).toBe(BRIDGE_CONNECT_BUTTON_LABEL);
    expect(query(mounted, "bridge-pairing-code")).toBeNull();
    unmount(mounted);
  });

  it("Not connected -> click -> PAIRING CODE + CLI command + waiting, then polls to Connected (fake timers + mocked status)", async () => {
    const services = makeServices();
    const getBridgeStatus = vi.mocked(services.getBridgeStatus);
    // First poll still unconnected (user has not run the bridge yet), second
    // poll connected (the user ran `pd-ollama-bridge connect <code>`).
    getBridgeStatus
      .mockResolvedValueOnce({
        remoteLocalAi: { available: true, connected: false, model: null, ready: false },
      })
      .mockResolvedValueOnce({
        remoteLocalAi: { available: true, connected: true, model: "hermes3:8b", ready: true },
      });
    const mounted = mount(makeProps({ services }));

    click(query(mounted, "bridge-connect"));
    await settle();

    // Code + CLI instruction + waiting state appear prominently.
    expect(textOf(query(mounted, "bridge-pairing-code"))).toBe(CODE);
    expect(textOf(query(mounted, "bridge-cli-command"))).toBe(
      "pd-ollama-bridge connect PD-X7K4-92QP",
    );
    expect(textOf(query(mounted, "bridge-waiting"))).toBe(BRIDGE_WAITING_LABEL);
    // The pairing endpoints were called with the ANONYMOUS session token the
    // panel created (the browser never sends anything to a local Ollama).
    expect(services.createAnonymousSession).toHaveBeenCalledTimes(1);
    expect(services.createBridgePairing).toHaveBeenCalledWith(ANON);

    // First poll (2s): still waiting.
    await advance(2000);
    await settle();
    expect(query(mounted, "bridge-waiting")).not.toBeNull();

    // Second poll (4s): the bridge bound -> Connected.
    await advance(2000);
    await settle();
    expect(textOf(query(mounted, "bridge-connected"))).toBe(BRIDGE_CONNECTED_LINE);
    expect(textOf(query(mounted, "bridge-model"))).toBe("Model: hermes3:8b");
    expect(textOf(query(mounted, "bridge-ready"))).toBe(BRIDGE_READY_LINE);
    expect(query(mounted, "bridge-waiting")).toBeNull();
    unmount(mounted);
  });

  it("connected -> a later poll returning connected:false flips to the disconnected message (never a silent re-label)", async () => {
    const services = makeServices();
    const getBridgeStatus = vi.mocked(services.getBridgeStatus);
    getBridgeStatus
      .mockResolvedValueOnce({
        remoteLocalAi: { available: true, connected: true, model: "hermes3:8b", ready: true },
      })
      .mockResolvedValueOnce({
        remoteLocalAi: { available: true, connected: false, model: null, ready: false },
      });
    const mounted = mount(makeProps({ services }));

    click(query(mounted, "bridge-connect"));
    await settle();
    await advance(2000);
    await settle();
    expect(textOf(query(mounted, "bridge-connected"))).toBe(BRIDGE_CONNECTED_LINE);

    // The bridge drops: the next poll reports connected:false -> truthful line.
    await advance(2000);
    await settle();
    expect(textOf(query(mounted, "bridge-disconnected"))).toBe(BRIDGE_DISCONNECTED_MESSAGE);
    expect(query(mounted, "bridge-connected")).toBeNull();
    expect(query(mounted, "bridge-connect")).not.toBeNull(); // reconnect CTA
    unmount(mounted);
  });

  it("a connected capability block (fixture) renders the connected state without any pairing click", async () => {
    const connectedCaps: GenerationCapabilitiesResponse = parseGenerationCapabilities({
      modes: [{ id: "demo", available: true }],
      configuredProvider: "fake",
      remoteLocalAi: {
        available: true,
        connected: true,
        model: "hermes3:8b",
        ready: true,
      },
    });
    const mounted = mount(makeProps({ capabilities: connectedCaps }));
    expect(textOf(query(mounted, "bridge-connected"))).toBe(BRIDGE_CONNECTED_LINE);
    expect(textOf(query(mounted, "bridge-model"))).toBe("Model: hermes3:8b");
    expect(textOf(query(mounted, "bridge-ready"))).toBe(BRIDGE_READY_LINE);
    unmount(mounted);
  });

  it("waiting expires after the bounded 2-minute wait and offers [Request a new code]", async () => {
    const services = makeServices();
    // The status endpoint keeps reporting not-connected; the panel must stop
    // after the bounded wait (no infinite poll) and show the expiry copy.
    const mounted = mount(makeProps({ services }));
    click(query(mounted, "bridge-connect"));
    await settle();
    expect(query(mounted, "bridge-waiting")).not.toBeNull();

    await advance(BRIDGE_WAIT_MAX_MS);
    await settle();
    expect(textOf(query(mounted, "bridge-wait-expired"))).toBe(BRIDGE_WAIT_EXPIRED_MESSAGE);
    expect(textOf(query(mounted, "bridge-request-new-code"))).toBe("Request a new code");
    unmount(mounted);
  });

  it("a 429 pairing response surfaces the safe quota copy, never a raw code", async () => {
    const services = makeServices({
      createBridgePairing: vi.fn(async () => {
        throw new ApiError(429, "TOO_MANY_REQUESTS", "too many pairing codes", null);
      }),
    });
    const mounted = mount(makeProps({ services }));
    click(query(mounted, "bridge-connect"));
    await settle();
    expect(textOf(query(mounted, "bridge-action-error"))).toBe(
      "Too many pairing requests right now. Please try again later.",
    );
    expect(textOf(query(mounted, "bridge-action-error"))).not.toContain("429");
    unmount(mounted);
  });

  // Phase 24 F-1 — the pairing token is fused into the journey ONLY when the
  // pairing request SUCCEEDED. A failed/429 pairing must leave the journey
  // WITHOUT the token (a generation under a never-paired session would burn a
  // per-session attempt and fail BRIDGE_NOT_CONNECTED); a successful pairing
  // keeps the P0 attach flow intact so /new -> /generating runs POST /cases
  // under the SAME session that paired the bridge.
  it("a SUCCESSFUL pairing attaches the session token to the journey (P0 flow kept intact)", async () => {
    const services = makeServices();
    const mounted = mount(makeProps({ services }));
    click(query(mounted, "bridge-connect"));
    await settle();
    expect(services.createBridgePairing).toHaveBeenCalledWith(ANON);

    // Staging a journey AFTER the successful pairing carries the paired
    // session bearer into JourneyParams (attach -> pending -> staged).
    setJourneyParams({ prompt: "a crime", difficulty: "easy" });
    expect(getJourneyParams()).toEqual({
      prompt: "a crime",
      difficulty: "easy",
      anonymousSessionToken: ANON,
    });
    unmount(mounted);
  });

  it("a FAILED pairing leaves the journey WITHOUT the attached token (F-1)", async () => {
    const services = makeServices({
      createBridgePairing: vi.fn(async () => {
        throw new ApiError(429, "TOO_MANY_REQUESTS", "too many pairing codes", null);
      }),
    });
    const mounted = mount(makeProps({ services }));
    click(query(mounted, "bridge-connect"));
    await settle();
    expect(textOf(query(mounted, "bridge-action-error"))).toBe(
      "Too many pairing requests right now. Please try again later.",
    );

    // No successful pairing -> a journey staged afterwards is byte-identical:
    // no anonymousSessionToken key is ever fused into it (and nothing stays
    // pending for the NEXT staging either).
    setJourneyParams({ prompt: "a crime", difficulty: "easy" });
    const staged = getJourneyParams() as JourneyParams;
    expect(staged).toEqual({ prompt: "a crime", difficulty: "easy" });
    expect("anonymousSessionToken" in staged).toBe(false);
    unmount(mounted);
  });

  // Phase 26C1 §3 — a pairing the user actually completed (waiting -> connected
  // poll transition) is strong Bridge intent: the panel notifies the /new page
  // (onBridgePaired) so it selects + persists the Bridge transport. A
  // capabilities-refresh connected block is NOT a pairing action and must not
  // fire the callback.
  it("a COMPLETED pairing (waiting -> connected) notifies onBridgePaired (§3)", async () => {
    const onBridgePaired = vi.fn();
    const services = makeServices();
    const getBridgeStatus = vi.mocked(services.getBridgeStatus);
    getBridgeStatus
      .mockResolvedValueOnce({
        remoteLocalAi: { available: true, connected: false, model: null, ready: false },
      })
      .mockResolvedValueOnce({
        remoteLocalAi: { available: true, connected: true, model: "hermes3:8b", ready: true },
      });
    const mounted = mount(makeProps({ services, onBridgePaired }));

    click(query(mounted, "bridge-connect"));
    await settle();
    // Still waiting for the bridge CLI: no Bridge-intent notification yet.
    expect(onBridgePaired).not.toHaveBeenCalled();

    await advance(2000);
    await settle();
    // First poll still reports not-connected -> still waiting, no notification.
    expect(onBridgePaired).not.toHaveBeenCalled();

    await advance(2000);
    await settle();
    expect(textOf(query(mounted, "bridge-connected"))).toBe(BRIDGE_CONNECTED_LINE);
    // The completed pairing fired EXACTLY once.
    expect(onBridgePaired).toHaveBeenCalledTimes(1);

    // A later connected poll (connection stays up) must NOT fire it again.
    await advance(2000);
    await settle();
    expect(onBridgePaired).toHaveBeenCalledTimes(1);
    unmount(mounted);
  });

  it("a connected capability block (no pairing action) does NOT call onBridgePaired", () => {
    const onBridgePaired = vi.fn();
    const connectedCaps: GenerationCapabilitiesResponse = parseGenerationCapabilities({
      modes: [{ id: "demo", available: true }],
      configuredProvider: "fake",
      remoteLocalAi: {
        available: true,
        connected: true,
        model: "hermes3:8b",
        ready: true,
      },
    });
    const mounted = mount(makeProps({ capabilities: connectedCaps, onBridgePaired }));
    expect(textOf(query(mounted, "bridge-connected"))).toBe(BRIDGE_CONNECTED_LINE);
    expect(onBridgePaired).not.toHaveBeenCalled();
    unmount(mounted);
  });

  // Phase 26C1 (LOW fix) — a fresh pairing window opens when the user clicks
  // [Connect local Ollama]; the /new page re-arms its explicit-choice ordering
  // guard at that exact point. The START signal must fire synchronously with
  // the user action (never only after the completion poll) and NOT for a
  // capability-refresh connected block (which is not a pairing action).
  it("starting a pairing notifies onBridgePairingStarted exactly once — and only for a FRESH window", async () => {
    const onBridgePairingStarted = vi.fn();
    const onBridgePaired = vi.fn();
    const services = makeServices();
    const mounted = mount(makeProps({ services, onBridgePaired, onBridgePairingStarted }));
    // No pairing in progress yet: no start notification.
    expect(onBridgePairingStarted).not.toHaveBeenCalled();

    click(query(mounted, "bridge-connect"));
    await settle();
    expect(onBridgePairingStarted).toHaveBeenCalledTimes(1);

    // A re-click while the window is still waiting must NOT open a second
    // window (the panel's waiting guard is authoritative).
    expect(query(mounted, "bridge-connect")).toBeNull(); // waiting view has no CTA
    await advance(2000);
    await settle();
    expect(onBridgePairingStarted).toHaveBeenCalledTimes(1);
    unmount(mounted);
  });

  it("[Request a new code] opens a SECOND fresh pairing window and re-fires onBridgePairingStarted", async () => {
    const onBridgePairingStarted = vi.fn();
    const services = makeServices(); // status always reports not-connected
    const mounted = mount(makeProps({ services, onBridgePairingStarted }));

    click(query(mounted, "bridge-connect"));
    await settle();
    expect(onBridgePairingStarted).toHaveBeenCalledTimes(1);

    // The bounded wait expires -> [Request a new code] restarts pairing.
    await advance(BRIDGE_WAIT_MAX_MS);
    await settle();
    expect(query(mounted, "bridge-wait-expired")).not.toBeNull();

    click(query(mounted, "bridge-request-new-code"));
    await settle();
    expect(onBridgePairingStarted).toHaveBeenCalledTimes(2);
    unmount(mounted);
  });

  it("a FAILED pairing request (429) still opened a window once — but the route's guard stays harmless (no completion can ever fire)", async () => {
    const onBridgePairingStarted = vi.fn();
    const onBridgePaired = vi.fn();
    const services = makeServices({
      createBridgePairing: vi.fn(async () => {
        throw new ApiError(429, "TOO_MANY_REQUESTS", "too many pairing codes", null);
      }),
    });
    const mounted = mount(makeProps({ services, onBridgePaired, onBridgePairingStarted }));
    click(query(mounted, "bridge-connect"));
    await settle();
    expect(textOf(query(mounted, "bridge-action-error"))).toBe(
      "Too many pairing requests right now. Please try again later.",
    );
    // The start signal re-armed the route guard once; with no successful
    // pairing there is no completion, so onBridgePaired must stay silent.
    expect(onBridgePairingStarted).toHaveBeenCalledTimes(1);
    expect(onBridgePaired).not.toHaveBeenCalled();
    unmount(mounted);
  });
});