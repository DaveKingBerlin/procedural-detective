import { describe, expect, it } from "vitest";
import type { RemoteLocalAiDTO } from "../api/types";
import {
  BRIDGE_CONNECT_BUTTON_LABEL,
  BRIDGE_DISCONNECTED_MESSAGE,
  BRIDGE_POLL_INTERVAL_MS,
  BRIDGE_WAIT_MAX_MS,
  bridgeCliCommand,
  bridgeModelLine,
  initialBridgeView,
  nextBridgeView,
  pairingWaitingView,
} from "./localAiBridge";

/**
 * Phase 22 — pure BYO-Ollama bridge panel logic: the frozen copy constants,
 * the initial view derived ONLY from the sanitized capability block, the
 * pairingWaitingView after a minted code and the pure poll/reducer state
 * machine. No DOM, no network, no timers (the component owns those).
 */

describe("Phase 22 — bridge copy constants are frozen and app-authored", () => {
  it("carries the required Phase 22 §12/§13 lines verbatim", () => {
    expect(BRIDGE_CONNECT_BUTTON_LABEL).toBe("Connect local Ollama");
    expect(BRIDGE_DISCONNECTED_MESSAGE).toBe(
      "Local AI disconnected — Reconnect the local bridge or use Deterministic Demo.",
    );
    expect(BRIDGE_POLL_INTERVAL_MS).toBe(2000);
    expect(BRIDGE_WAIT_MAX_MS).toBe(120000); // bounded ≈ 2 minutes
  });

  it("composes the CLI instruction as the frozen prefix + the server code", () => {
    expect(bridgeCliCommand("PD-X7K4-92QP")).toBe("pd-ollama-bridge connect PD-X7K4-92QP");
  });

  it("builds the Model line only from a known non-empty model label", () => {
    expect(bridgeModelLine("hermes3:8b")).toBe("Model: hermes3:8b");
    expect(bridgeModelLine("")).toBeNull();
    expect(bridgeModelLine(null)).toBeNull();
    expect(bridgeModelLine(undefined)).toBeNull();
  });
});

describe("initialBridgeView — derived ONLY from the sanitized capability block", () => {
  const BLOCK: RemoteLocalAiDTO = { available: true, connected: false, model: "hermes3:8b", ready: false };

  it("a null block (feature not offered) starts idle with no status claim", () => {
    expect(initialBridgeView(null)).toEqual({ state: "idle", code: null, status: null });
  });

  it("an unconnected block starts the not-connected idle view", () => {
    expect(initialBridgeView(BLOCK)).toEqual({ state: "idle", code: null, status: BLOCK });
  });

  it("a block already reporting connected starts the connected view (never invents a connection)", () => {
    const connected = { ...BLOCK, connected: true, ready: true };
    expect(initialBridgeView(connected)).toEqual({
      state: "connected",
      code: null,
      status: connected,
    });
  });

  it("pairingWaitingView records the minted code in the waiting state", () => {
    expect(pairingWaitingView("PD-X7K4-92QP")).toEqual({
      state: "waiting",
      code: "PD-X7K4-92QP",
      status: null,
    });
  });
});

describe("nextBridgeView — the pure waiting/connected/disconnected reducer", () => {
  const UNCONNECTED: RemoteLocalAiDTO = { available: true, connected: false, model: null, ready: false };
  const CONNECTED: RemoteLocalAiDTO = { available: true, connected: true, model: "hermes3:8b", ready: true };

  it("waiting + a connected poll -> connected (server-authoritative status)", () => {
    const next = nextBridgeView(pairingWaitingView("PD-X7K4-92QP"), CONNECTED, 2000);
    expect(next.state).toBe("connected");
    expect(next.status).toEqual(CONNECTED);
    expect(next.code).toBe("PD-X7K4-92QP");
  });

  it("waiting + an unconnected poll keeps waiting until the bounded wait", () => {
    const view = pairingWaitingView("PD-X7K4-92QP");
    const stillWaiting = nextBridgeView(view, UNCONNECTED, BRIDGE_WAIT_MAX_MS - 1000);
    expect(stillWaiting.state).toBe("waiting");
    const expired = nextBridgeView(view, UNCONNECTED, BRIDGE_WAIT_MAX_MS);
    expect(expired.state).toBe("wait-expired");
  });

  it("connected + a connected poll stays connected (status refreshes)", () => {
    const next = nextBridgeView({ state: "connected", code: "PD-X7K4-92QP", status: CONNECTED }, CONNECTED, 6000);
    expect(next.state).toBe("connected");
  });

  it("connected + a connected:false poll -> disconnected (a later drop is truthful)", () => {
    const next = nextBridgeView({ state: "connected", code: "PD-X7K4-92QP", status: CONNECTED }, UNCONNECTED, 6000);
    expect(next.state).toBe("disconnected");
    expect(next.status).toEqual(UNCONNECTED);
  });

  it("connected + a poll failure (null) stays connected — one transient blip is not a drop", () => {
    const next = nextBridgeView({ state: "connected", code: "PD-X7K4-92QP", status: CONNECTED }, null, 6000);
    expect(next.state).toBe("connected");
  });

  it("idle / disconnected / wait-expired have no internal transition without a user action", () => {
    expect(nextBridgeView({ state: "idle", code: null, status: null }, CONNECTED, 0).state).toBe("idle");
    const disconnected = { state: "disconnected" as const, code: "PD-X7K4-92QP", status: UNCONNECTED };
    expect(nextBridgeView(disconnected, CONNECTED, 0).state).toBe("disconnected");
    const expired = { state: "wait-expired" as const, code: "PD-X7K4-92QP", status: UNCONNECTED };
    expect(nextBridgeView(expired, CONNECTED, 0).state).toBe("wait-expired");
  });
});