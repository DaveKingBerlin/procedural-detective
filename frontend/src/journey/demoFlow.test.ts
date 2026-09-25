import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { DEMO_FAILURE_MESSAGES, generationFailed, pollDelayMs, runDemo, type DemoFlowServices, type DemoProgress } from "./demoFlow";

/**
 * runDemo state machine coverage (Phase 8 A) with fully injected fakes —
 * zero network, zero timers (the wait function is a no-op), fully
 * deterministic: success, FAILED generation, quota (429 ADMISSION_DENIED),
 * network/timeout (status 0), 5xx and poll-retry exhaustion.
 */

const ANON = "anon-quota-0001";
const CREATOR = "creator-token-0001";
const PLAYTHROUGH_TOKEN = "playthrough-token-0001";
const PLAYTHROUGH_ID = "PT-demo-0001";

function publishedCase(): ReturnType<NonNullable<DemoFlowServices["createCase"]>> {
  return Promise.resolve({
    caseId: "CASE-demo-01",
    generationId: "GEN-demo-01",
    generationAttemptId: "ATT-demo-01",
    creatorAccessToken: CREATOR,
    status: "PUBLISHED",
  });
}

function runningCase(): ReturnType<NonNullable<DemoFlowServices["createCase"]>> {
  return Promise.resolve({
    caseId: "CASE-demo-01",
    generationId: "GEN-demo-01",
    generationAttemptId: "ATT-demo-01",
    creatorAccessToken: CREATOR,
    status: "RUNNING",
  });
}

function makeServices(overrides: Partial<DemoFlowServices> = {}): DemoFlowServices {
  return {
    createSession: vi.fn(async () => ({ anonymousSessionToken: ANON, quotaWindowEndsAt: 1e12 })),
    createCase: vi.fn(() => publishedCase()),
    pollGeneration: vi.fn(async () => ({
      caseId: "CASE-demo-01",
      generationId: "GEN-demo-01",
      status: "PUBLISHED",
      progress: 100,
      stage: null,
    })),
    createPlaythrough: vi.fn(async () => ({
      playthroughId: PLAYTHROUGH_ID,
      caseId: "CASE-demo-01",
      caseVersion: 1,
      playthroughAccessToken: PLAYTHROUGH_TOKEN,
      status: "PLAYING",
    })),
    ...overrides,
  };
}

/** No-op wait: tests never touch real timers. */
const NO_WAIT = async () => {};

describe("runDemo — success", () => {
  it("returns the playthrough credentials and calls the services in order", async () => {
    const services = makeServices();
    const phases: DemoProgress[] = [];
    const result = await runDemo("Some mystery prompt", {
      services,
      wait: NO_WAIT,
      onProgress: (progress) => phases.push(progress),
    });

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    expect(result.playthroughToken).toBe(PLAYTHROUGH_TOKEN);
    expect(result.playthroughId).toBe(PLAYTHROUGH_ID);
    expect(result.caseId).toBe("CASE-demo-01");

    expect(services.createSession).toHaveBeenCalledTimes(1);
    expect(services.createCase).toHaveBeenCalledWith(ANON, "Some mystery prompt", undefined);
    expect(services.pollGeneration).not.toHaveBeenCalled(); // synchronous provider
    expect(services.createPlaythrough).toHaveBeenCalledWith(CREATOR, "CASE-demo-01", 1);

    expect(phases.map((p) => p.phase)).toEqual(["session", "create-case", "playthrough"]);
  });

  it("passes the difficulty label through to createCase", async () => {
    const services = makeServices();
    await runDemo("prompt", { services, difficulty: "hard", wait: NO_WAIT });
    expect(services.createCase).toHaveBeenCalledWith(ANON, "prompt", "hard");
  });

  it("polls an asynchronous provider until PUBLISHED, with exponential backoff", async () => {
    const poll = vi
      .fn()
      .mockResolvedValueOnce({
        caseId: "CASE-demo-01",
        generationId: "GEN-demo-01",
        status: "RUNNING",
        progress: 45,
        stage: "generating_evidence",
      })
      .mockResolvedValueOnce({
        caseId: "CASE-demo-01",
        generationId: "GEN-demo-01",
        status: "PUBLISHED",
        progress: 100,
        stage: null,
      });
    const services = makeServices({
      createCase: vi.fn(() => runningCase()),
      pollGeneration: poll,
    });
    const waits: number[] = [];
    const result = await runDemo("prompt", {
      services,
      wait: async (ms) => {
        waits.push(ms);
      },
    });

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    expect(poll).toHaveBeenCalledTimes(2);
    expect(waits).toEqual([pollDelayMs(1, 200, 2000)]);
    expect(services.createPlaythrough).toHaveBeenCalledTimes(1);
  });
});

describe("runDemo — Phase 16 Track B generation-mode note", () => {
  it("records the selected mode in every progress snapshot (the flow receives the mode)", async () => {
    const services = makeServices();
    const snapshots: DemoProgress[] = [];
    const result = await runDemo("prompt", {
      services,
      mode: "local",
      wait: NO_WAIT,
      onProgress: (progress) => snapshots.push(progress),
    });

    expect(result.ok).toBe(true);
    expect(snapshots.length).toBeGreaterThan(0);
    expect(snapshots.every((snapshot) => snapshot.mode === "local")).toBe(true);
    // The frozen service contract is untouched: createCase keeps its
    // (anonymousToken, prompt, difficulty) shape — the mode never invents a
    // request body field.
    expect(services.createCase).toHaveBeenCalledWith(ANON, "prompt", undefined);
  });

  it("Phase 21B Finding 3 — the POST /cases request is BYTE-IDENTICAL for every capability story (no provider/mode field ever)", async () => {
    // The demo/example CTA copy changes with the capability DTO (the label /
    // note are truthful per state), but the ACTION never changes: whatever the
    // label claims, runDemo sends the exact same (token, prompt, difficulty)
    // POST /cases request — no provider selection, no mode field, no fake
    // mode switching. The backend's process-global GENERATION_PROVIDER decides
    // the actual provider; the frontend never claims to control it here.
    for (const mode of [null, "demo", "local", "live"] as const) {
      const services = makeServices();
      const result = await runDemo("Some mystery prompt", {
        services,
        difficulty: "medium",
        mode,
        wait: NO_WAIT,
      });
      expect(result.ok).toBe(true);
      expect(services.createCase).toHaveBeenCalledTimes(1);
      expect(services.createCase).toHaveBeenCalledWith(ANON, "Some mystery prompt", "medium");
    }
  });

  it("propagates the mode into every phase, including the polling loop", async () => {
    const poll = vi
      .fn()
      .mockResolvedValueOnce({
        caseId: "CASE-demo-01",
        generationId: "GEN-demo-01",
        status: "RUNNING",
        progress: 40,
        stage: "world",
      })
      .mockResolvedValueOnce({
        caseId: "CASE-demo-01",
        generationId: "GEN-demo-01",
        status: "PUBLISHED",
        progress: 100,
        stage: null,
      });
    const services = makeServices({
      createCase: vi.fn(() => runningCase()),
      pollGeneration: poll,
    });
    const snapshots: DemoProgress[] = [];
    await runDemo("prompt", {
      services,
      mode: "live",
      wait: NO_WAIT,
      onProgress: (progress) => snapshots.push(progress),
    });

    expect(snapshots.map((s) => s.phase)).toEqual([
      "session",
      "create-case",
      "polling",
      "polling",
      "playthrough",
    ]);
    expect(snapshots.every((snapshot) => snapshot.mode === "live")).toBe(true);
  });

  it("records a null mode note when no mode was selected (default Demo path)", async () => {
    const services = makeServices();
    const snapshots: DemoProgress[] = [];
    await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      onProgress: (progress) => snapshots.push(progress),
    });

    expect(snapshots.length).toBeGreaterThan(0);
    expect(snapshots.every((snapshot) => snapshot.mode === null)).toBe(true);
  });
});

describe("runDemo — generation FAILED", () => {
  it("fails immediately when POST /cases reports FAILED", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => ({
        caseId: "CASE-demo-01",
        generationId: "GEN-demo-01",
        generationAttemptId: "ATT-demo-01",
        creatorAccessToken: CREATOR,
        status: "FAILED",
      })),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("failed");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
    expect(services.createPlaythrough).not.toHaveBeenCalled();
  });

  it("fails when a poll reports FAILED", async () => {
    const services = makeServices({
      createCase: vi.fn(() => runningCase()),
      pollGeneration: vi.fn(async () => ({
        caseId: "CASE-demo-01",
        generationId: "GEN-demo-01",
        status: "FAILED",
        progress: 100,
        stage: null,
      })),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("failed");
  });

  it("maps deadline and provider failures to distinct safe messages", async () => {
    const deadline = await runDemo("prompt", {
      services: makeServices({
        createCase: vi.fn(async () => ({
          caseId: "CASE-demo-01",
          generationId: "GEN-demo-01",
          generationAttemptId: "ATT-demo-01",
          creatorAccessToken: CREATOR,
          status: "FAILED",
          failureCode: "GENERATION_DEADLINE_EXCEEDED",
        })),
      }),
      wait: NO_WAIT,
    });
    expect(deadline.ok).toBe(false);
    if (deadline.ok) throw new Error("expected deadline failure");
    expect(deadline.failure.kind).toBe("deadline");
    expect(deadline.failure.message).toBe(DEMO_FAILURE_MESSAGES.deadline);

    const provider = await runDemo("prompt", {
      services: makeServices({
        createCase: vi.fn(async () => ({
          caseId: "CASE-demo-01",
          generationId: "GEN-demo-01",
          generationAttemptId: "ATT-demo-01",
          creatorAccessToken: CREATOR,
          status: "FAILED",
          failureCode: "PROVIDER_TIMEOUT",
        })),
      }),
      wait: NO_WAIT,
    });
    expect(provider.ok).toBe(false);
    if (provider.ok) throw new Error("expected provider failure");
    expect(provider.failure.kind).toBe("provider");
    expect(provider.failure.message).toBe(DEMO_FAILURE_MESSAGES.providerTimeout);

    const providerBudget = await runDemo("prompt", {
      services: makeServices({
        createCase: vi.fn(async () => ({
          caseId: "CASE-demo-01",
          generationId: "GEN-demo-01",
          generationAttemptId: "ATT-demo-01",
          creatorAccessToken: CREATOR,
          status: "FAILED",
          failureCode: "PROVIDER_CALL_BUDGET_EXHAUSTED",
        })),
      }),
      wait: NO_WAIT,
    });
    expect(providerBudget.ok).toBe(false);
    if (providerBudget.ok) throw new Error("expected provider budget failure");
    expect(providerBudget.failure.kind).toBe("provider");
    expect(providerBudget.failure.message).toBe(
      DEMO_FAILURE_MESSAGES.providerUnavailable,
    );
  });
});

describe("runDemo — Phase 19 failure codes (budget / asset limits)", () => {
  const PHASE_19_PROVIDER_BUDGET_CODES = [
    "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED",
    "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED",
  ];
  const PHASE_19_ASSET_LIMIT_CODES = [
    "MAX_PROCEDURAL_ASSETS_EXCEEDED",
    "MAX_FAILED_ASSETS_EXCEEDED",
  ];

  it.each(PHASE_19_PROVIDER_BUDGET_CODES)(
    "maps %s (server-side FAILED on createCase) to the safe provider message, never the raw code",
    async (failureCode) => {
      const result = await runDemo("prompt", {
        services: makeServices({
          createCase: vi.fn(async () => ({
            caseId: "CASE-demo-01",
            generationId: "GEN-demo-01",
            generationAttemptId: "ATT-demo-01",
            creatorAccessToken: CREATOR,
            status: "FAILED",
            failureCode,
          })),
        }),
        wait: NO_WAIT,
      });
      expect(result.ok).toBe(false);
      if (result.ok) throw new Error("expected provider-budget failure");
      expect(result.failure.kind).toBe("provider");
      expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.providerUnavailable);
      // The raw code must never reach a player-facing surface.
      expect(result.failure.message).not.toContain(failureCode);
    },
  );

  it.each(PHASE_19_ASSET_LIMIT_CODES)(
    "maps %s (server-side FAILED on createCase) to the safe failed message, never the raw code",
    async (failureCode) => {
      const result = await runDemo("prompt", {
        services: makeServices({
          createCase: vi.fn(async () => ({
            caseId: "CASE-demo-01",
            generationId: "GEN-demo-01",
            generationAttemptId: "ATT-demo-01",
            creatorAccessToken: CREATOR,
            status: "FAILED",
            failureCode,
          })),
        }),
        wait: NO_WAIT,
      });
      expect(result.ok).toBe(false);
      if (result.ok) throw new Error("expected asset-limit failure");
      expect(result.failure.kind).toBe("failed");
      expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
      expect(result.failure.message).not.toContain(failureCode);
    },
  );

  it.each(PHASE_19_PROVIDER_BUDGET_CODES)(
    "maps %s from a polled FAILED status to the safe provider message",
    async (failureCode) => {
      const services = makeServices({
        createCase: vi.fn(() => runningCase()),
        pollGeneration: vi.fn(async () => ({
          caseId: "CASE-demo-01",
          generationId: "GEN-demo-01",
          status: "FAILED",
          progress: 100,
          stage: null,
          failureCode,
        })),
      });
      const result = await runDemo("prompt", { services, wait: NO_WAIT });
      expect(result.ok).toBe(false);
      if (result.ok) throw new Error("expected provider-budget failure");
      expect(result.failure.kind).toBe("provider");
      expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.providerUnavailable);
    },
  );

  it.each([
    "MAX_PROCEDURAL_ASSETS_EXCEEDED",
    "MAX_FAILED_ASSETS_EXCEEDED",
  ])("maps %s from a polled FAILED status to the safe failed message", async (failureCode) => {
    const services = makeServices({
      createCase: vi.fn(() => runningCase()),
      pollGeneration: vi.fn(async () => ({
        caseId: "CASE-demo-01",
        generationId: "GEN-demo-01",
        status: "FAILED",
        progress: 100,
        stage: null,
        failureCode,
      })),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected asset-limit failure");
    expect(result.failure.kind).toBe("failed");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
  });

  it("falls back to the generic failed message for an unknown/hostile code, never the raw code", async () => {
    const hostile = "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED_AND_MORE";
    const result = await runDemo("prompt", {
      services: makeServices({
        createCase: vi.fn(async () => ({
          caseId: "CASE-demo-01",
          generationId: "GEN-demo-01",
          generationAttemptId: "ATT-demo-01",
          creatorAccessToken: CREATOR,
          status: "FAILED",
          failureCode: hostile,
        })),
      }),
      wait: NO_WAIT,
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected unknown-code failure");
    expect(result.failure.kind).toBe("failed");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
    expect(result.failure.message).not.toContain(hostile);
  });
});

describe("generationFailed — direct mapping (Phase 19 codes)", () => {
  it("maps each new provider-budget code to the safe provider message class", () => {
    for (const failureCode of [
      "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED",
      "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED",
    ]) {
      const failure = generationFailed(failureCode);
      expect(failure.kind).toBe("provider");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.providerUnavailable);
    }
  });

  it("maps each new asset-limit code to the safe failed message class", () => {
    for (const failureCode of [
      "MAX_PROCEDURAL_ASSETS_EXCEEDED",
      "MAX_FAILED_ASSETS_EXCEEDED",
    ]) {
      const failure = generationFailed(failureCode);
      expect(failure.kind).toBe("failed");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
    }
  });

  it("matches exact strings only — a prefix/substring variant never narrows into the new buckets", () => {
    for (const hostile of [
      "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED_EXTRA",
      "X_ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED",
      "MAX_PROCEDURAL_ASSETS_EXCEEDED_NOW",
      "TOO_MANY_MAX_FAILED_ASSETS_EXCEEDED",
    ]) {
      const failure = generationFailed(hostile);
      expect(failure.kind).toBe("failed");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
      expect(failure.message).not.toContain(hostile);
    }
    // Same for the pre-existing buckets: no prefix narrowing regressions.
    expect(generationFailed("PROVIDER_TIMEOUT_x").kind).toBe("failed");
    expect(generationFailed("PROVIDER_UNAVAILABLE_2").kind).toBe("failed");
  });

  it("keeps the existing code mappings intact", () => {
    expect(generationFailed("GENERATION_DEADLINE_EXCEEDED").kind).toBe("deadline");
    expect(generationFailed("PROVIDER_TIMEOUT").kind).toBe("provider");
    expect(generationFailed("PROVIDER_UNAVAILABLE").kind).toBe("provider");
    expect(generationFailed("PROVIDER_INVALID_RESPONSE").kind).toBe("provider");
    expect(generationFailed("PROVIDER_CALL_BUDGET_EXHAUSTED").kind).toBe("provider");
  });

  it("keeps the DEFAULT fallback unchanged (null/undefined/unknown)", () => {
    for (const failureCode of [null, undefined, "SOME_FUTURE_CODE"]) {
      const failure = generationFailed(failureCode);
      expect(failure.kind).toBe("failed");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
    }
  });
});

describe("generationFailed — Phase 22 BYO-Ollama bridge typed codes (§21)", () => {
  it("maps the 5 new bridge/local codes to their distinct safe copy, never the raw code", () => {
    const cases: Array<[string, string]> = [
      ["BRIDGE_NOT_CONNECTED", DEMO_FAILURE_MESSAGES.bridgeNotConnected],
      ["BRIDGE_DISCONNECTED", DEMO_FAILURE_MESSAGES.bridgeDisconnected],
      ["LOCAL_OLLAMA_UNAVAILABLE", DEMO_FAILURE_MESSAGES.localOllamaUnavailable],
      ["LOCAL_MODEL_UNAVAILABLE", DEMO_FAILURE_MESSAGES.localModelUnavailable],
      ["LOCAL_PROVIDER_TIMEOUT", DEMO_FAILURE_MESSAGES.localProviderTimeout],
    ];
    for (const [failureCode, expected] of cases) {
      const failure = generationFailed(failureCode);
      expect(failure.kind).toBe("provider");
      expect(failure.message).toBe(expected);
      expect(failure.message).not.toContain(failureCode);
      expect(failure.message).not.toContain("_");
    }
  });

  it("exact copy for each of the 5 codes (§21 wording)", () => {
    expect(generationFailed("BRIDGE_NOT_CONNECTED").message).toBe(
      "Connect your local Ollama bridge first.",
    );
    expect(generationFailed("LOCAL_OLLAMA_UNAVAILABLE").message).toBe(
      "Ollama is not reachable on this computer.",
    );
    expect(generationFailed("LOCAL_MODEL_UNAVAILABLE").message).toBe(
      "The selected local model is not available.",
    );
    expect(generationFailed("LOCAL_PROVIDER_TIMEOUT").message).toBe(
      "Local AI did not finish within the allowed time.",
    );
    expect(generationFailed("BRIDGE_DISCONNECTED").message).toBe(
      "Local AI disconnected — Reconnect the local bridge or use Deterministic Demo.",
    );
  });

  it("exact strings only — a prefix/substring variant never narrows into the Phase 22 buckets", () => {
    for (const hostile of [
      "BRIDGE_NOT_CONNECTED_PLEASE",
      "X_LOCAL_OLLAMA_UNAVAILABLE",
      "LOCAL_MODEL_UNAVAILABLE_2",
      "LOCAL_PROVIDER_TIMEOUT_NOW",
      "BRIDGE_DISCONNECTED_AGAIN",
      "NOT_BRIDGE_NOT_CONNECTED",
    ]) {
      const failure = generationFailed(hostile);
      expect(failure.kind).toBe("failed");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
      expect(failure.message).not.toContain(hostile);
    }
  });

  it("the remaining Phase 22 codes (busy/expired/protocol/invalid) fall to the generic safe failed message", () => {
    for (const failureCode of [
      "BRIDGE_PAIRING_EXPIRED",
      "BRIDGE_BUSY",
      "BRIDGE_PROTOCOL_ERROR",
      "LOCAL_PROVIDER_INVALID_OUTPUT",
    ]) {
      const failure = generationFailed(failureCode);
      expect(failure.kind).toBe("failed");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
      expect(failure.message).not.toContain(failureCode);
    }
  });

  it("the Phase 22 codes also map from a run (createCase FAILED with failureCode)", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => ({
        caseId: "CASE-demo-01",
        generationId: "GEN-demo-01",
        generationAttemptId: "ATT-demo-01",
        creatorAccessToken: CREATOR,
        status: "FAILED",
        failureCode: "BRIDGE_NOT_CONNECTED",
      })),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("provider");
    expect(result.failure.message).toBe("Connect your local Ollama bridge first.");
    expect(result.failure.message).not.toContain("BRIDGE_NOT_CONNECTED");
  });
});

describe("runDemo — quota / admission rejection", () => {
  it("maps a 429 ADMISSION_DENIED on session creation to the quota failure", async () => {
    const services = makeServices({
      createSession: vi.fn(async () => {
        throw new ApiError(429, "ADMISSION_DENIED", "quota window exhausted", null);
      }),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("quota");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.quota);
  });

  it("maps a 429 ADMISSION_DENIED on createCase to the quota failure", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => {
        throw new ApiError(429, "ADMISSION_DENIED", "admission denied", null);
      }),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("quota");
  });
});

describe("runDemo — network / server errors are retryable", () => {
  it("maps a transport failure (status 0) to a retryable network failure", async () => {
    const services = makeServices({
      createSession: vi.fn(async () => {
        throw new ApiError(0, "NETWORK_ERROR", "connection refused", null);
      }),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("retryable");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.network);
  });

  it("maps a 5xx from createCase to a retryable server failure", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => {
        throw new ApiError(503, "HTTP_503", "temporary", null);
      }),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("retryable");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.server);
  });

  it("maps a playthrough failure to a typed failure and never reports ok", async () => {
    const services = makeServices({
      createPlaythrough: vi.fn(async () => {
        throw new ApiError(0, "TIMEOUT", "timed out", null);
      }),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("retryable");
  });

  it("gives up after the bounded retries with a tooSlow retryable failure", async () => {
    const poll = vi.fn(async () => ({
      caseId: "CASE-demo-01",
      generationId: "GEN-demo-01",
      status: "RUNNING",
      progress: 30,
      stage: null,
    }));
    const services = makeServices({
      createCase: vi.fn(() => runningCase()),
      pollGeneration: poll,
    });
    const waits: number[] = [];
    const result = await runDemo("prompt", {
      services,
      wait: async (ms) => {
        waits.push(ms);
      },
      maxPolls: 3,
    });

    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("retryable");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.tooSlow);
    expect(poll).toHaveBeenCalledTimes(3);
    expect(waits).toEqual([200, 400]); // backoff between bounded polls, capped
    expect(services.createPlaythrough).not.toHaveBeenCalled();
  });
});

describe("pollDelayMs — deterministic bounded backoff", () => {
  it("grows exponentially and caps at the max delay", () => {
    expect(pollDelayMs(1)).toBe(200);
    expect(pollDelayMs(2)).toBe(400);
    expect(pollDelayMs(3)).toBe(800);
    expect(pollDelayMs(4)).toBe(1600);
    expect(pollDelayMs(5)).toBe(2000);
    expect(pollDelayMs(20)).toBe(2000);
  });

  it("respects custom base/cap values", () => {
    expect(pollDelayMs(1, 50, 1000)).toBe(50);
    expect(pollDelayMs(5, 50, 1000)).toBe(800);
    expect(pollDelayMs(10, 50, 1000)).toBe(1000);
  });
});
