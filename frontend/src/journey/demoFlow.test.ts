import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import {
  admissionFallbackView,
  DEMO_FAILURE_MESSAGES,
  frontierFailureMessage,
  generationFailed,
  mapAdmissionReason,
  mapDemoError,
  pollDelayMs,
  runDemo,
  sessionExpiredView,
  type AdmissionFailureView,
  type DemoFlowServices,
  type DemoProgress,
} from "./demoFlow";

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

  it("Phase 21B/25 — WITHOUT a selection the POST /cases request is BYTE-IDENTICAL; WITH one it carries the flat block", async () => {
    // Phase 21B Finding 3 baseline: whatever the mode label, a run WITHOUT a
    // generation selection calls createCase with exactly (token, prompt,
    // difficulty) — three arguments, no provider/module field ever. Phase 25
    // supersedes this ONLY for the explicit selection-carrying path: with a
    // `generation` option runDemo passes it as the 4th argument and never
    // invents one on its own.
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
    const selectionServices = makeServices();
    await runDemo("Some mystery prompt", {
      services: selectionServices,
      difficulty: "medium",
      wait: NO_WAIT,
      generation: {
        generationProvider: "ollama",
        ollamaTransport: "server",
        ollamaModel: "qwen2.5:1.5b",
      },
    });
    expect(selectionServices.createCase).toHaveBeenCalledWith(
      ANON,
      "Some mystery prompt",
      "medium",
      { generationProvider: "ollama", ollamaTransport: "server", ollamaModel: "qwen2.5:1.5b" },
    );
    const fakeSelectionServices = makeServices();
    await runDemo("Some mystery prompt", {
      services: fakeSelectionServices,
      wait: NO_WAIT,
      generation: { generationProvider: "fake" },
    });
    // A single-field selection (fake/frontier) travels WITHOUT transport/model.
    expect(fakeSelectionServices.createCase).toHaveBeenCalledWith(
      ANON,
      "Some mystery prompt",
      undefined,
      { generationProvider: "fake" },
    );
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

describe("runDemo — Phase 24 P0 reuse of a pre-existing anonymous session token", () => {
  it("reuses the provided token for createCase and SKIPS createSession", async () => {
    const services = makeServices();
    const result = await runDemo("prompt", {
      services,
      anonymousSessionToken: "paired-session-tok",
      wait: NO_WAIT,
    });
    expect(result.ok).toBe(true);
    expect(services.createSession).not.toHaveBeenCalled();
    expect(services.createCase).toHaveBeenCalledWith("paired-session-tok", "prompt", undefined);
  });

  it("the reuse path passes the difficulty label through to createCase", async () => {
    const services = makeServices();
    await runDemo("prompt", {
      services,
      difficulty: "hard",
      anonymousSessionToken: "paired-session-tok",
      wait: NO_WAIT,
    });
    expect(services.createSession).not.toHaveBeenCalled();
    expect(services.createCase).toHaveBeenCalledWith("paired-session-tok", "prompt", "hard");
  });

  it("an empty-string token is treated as absent (fresh mint — current behavior preserved)", async () => {
    const services = makeServices();
    const result = await runDemo("prompt", {
      services,
      anonymousSessionToken: "",
      wait: NO_WAIT,
    });
    expect(result.ok).toBe(true);
    expect(services.createSession).toHaveBeenCalledTimes(1);
    expect(services.createCase).toHaveBeenCalledWith(ANON, "prompt", undefined);
  });

  it("with a pre-existing token a 429 on createCase still maps to the quota failure (authorization path unchanged)", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => {
        throw new ApiError(429, "ADMISSION_DENIED", "admission denied", null);
      }),
    });
    const result = await runDemo("prompt", {
      services,
      anonymousSessionToken: "paired-session-tok",
      wait: NO_WAIT,
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    // Phase 36 — `kind` stays "quota" for backward compatibility; the
    // reason-aware recovery view travels in `admissionReason`. A legacy
    // envelope WITHOUT a reasonCode maps to the conservative
    // temporary-capacity GENERIC fallback (cache PRESERVED — never an
    // assumed session exhaustion).
    expect(result.failure.kind).toBe("quota");
    expect(result.failure.admissionReason).toBeDefined();
    expect(result.failure.admissionReason!.status).toBe("temporary-capacity-limit");
    expect(result.failure.admissionReason!.message).toBe(
      admissionFallbackView().message,
    );
    expect(result.failure.admissionReason!.clearSessionCache).toBe(false);
    expect(result.failure.admissionReason!.retryable).toBe(true);
    expect(result.failure.message).toBe(admissionFallbackView().message);
    expect(services.createSession).not.toHaveBeenCalled();
  });

  it("without a token the minting behavior is byte-identical (createSession called once, createCase with the minted token)", async () => {
    const services = makeServices();
    await runDemo("prompt", { services, wait: NO_WAIT });
    expect(services.createSession).toHaveBeenCalledTimes(1);
    expect(services.createCase).toHaveBeenCalledWith(ANON, "prompt", undefined);
  });
});

describe("runDemo — Phase 25 provider selection", () => {
  it("changing the provider changes the subsequent POST /cases request", async () => {
    const services = makeServices();
    await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: { generationProvider: "fake" },
    });
    await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: {
        generationProvider: "ollama",
        ollamaTransport: "server",
        ollamaModel: "llama3.2:3b",
      },
    });
    expect(services.createCase).toHaveBeenNthCalledWith(1, ANON, "prompt", undefined, {
      generationProvider: "fake",
    });
    expect(services.createCase).toHaveBeenNthCalledWith(2, ANON, "prompt", undefined, {
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "llama3.2:3b",
    });
  });

  it("explicit provider failure does NOT silently switch provider (createCase called ONCE with the same selection)", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => {
        throw new ApiError(400, "PROVIDER_UNAVAILABLE", "frontier is not configured", null);
      }),
    });
    const result = await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: { generationProvider: "frontier" },
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("provider");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.providerUnavailableExplicit);
    expect(result.failure.message).not.toContain("PROVIDER_UNAVAILABLE");
    expect(result.failure.message).not.toContain("frontier");
    // Exactly one attempt under the EXPLICIT selection — no silent fallback.
    expect(services.createCase).toHaveBeenCalledTimes(1);
    expect(services.createCase).toHaveBeenCalledWith(ANON, "prompt", undefined, {
      generationProvider: "frontier",
    });
  });

  it("INVALID_GENERATION_PROVIDER maps to the frozen safe copy, never the raw upstream text", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => {
        throw new ApiError(400, "INVALID_GENERATION_PROVIDER", "provider 'weird' unknown", null);
      }),
    });
    const result = await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: { generationProvider: "weird" as never },
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("provider");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.invalidGenerationProvider);
    expect(result.failure.message).not.toContain("weird");
    expect(result.failure.message).not.toContain("INVALID_GENERATION_PROVIDER");
    expect(services.createCase).toHaveBeenCalledTimes(1);
  });

  it("a 4xx PROVIDER_UNAVAILABLE still surfaces as a retryable-safe provider failure for a 3xx-style envelope", async () => {
    // The code match is exact and status-independent: a 409 PROVIDER_UNAVAILABLE
    // (for example) maps to the frozen explicit copy too — never the raw body.
    const services = makeServices({
      createCase: vi.fn(async () => {
        throw new ApiError(409, "PROVIDER_UNAVAILABLE", "connection refused", null);
      }),
    });
    const result = await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "hermes3:8b" },
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("provider");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.providerUnavailableExplicit);
    expect(result.failure.message).not.toContain("connection refused");
  });

  it("a prefix/substring variant of the provider codes NEVER narrows into the explicit buckets", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => {
        throw new ApiError(400, "INVALID_GENERATION_PROVIDER_2", "hostile", null);
      }),
    });
    const result = await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: { generationProvider: "fake" },
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    // Falls through to the generic retryable copy, NOT the explicit provider copy.
    expect(result.failure.kind).toBe("retryable");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.generic);
  });

  it("Phase 26C1 §17/§18 — each run POSTs EXACTLY ITS OWN transport once (no stale transport, no shared state)", async () => {
    const services = makeServices();
    // First run under an explicit Server selection.
    await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: {
        generationProvider: "ollama",
        ollamaTransport: "server",
        ollamaModel: "hermes3:8b",
      },
    });
    // Second run under an explicit Bridge selection — the first run's
    // transport must not leak into it (no client-global state mutation).
    await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: {
        generationProvider: "ollama",
        ollamaTransport: "bridge",
        ollamaModel: "hermes3:8b",
      },
    });
    // EXACTLY two POSTs — one per run, each with its OWN current transport.
    expect(services.createCase).toHaveBeenCalledTimes(2);
    expect(services.createCase).toHaveBeenNthCalledWith(1, ANON, "prompt", undefined, {
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "hermes3:8b",
    });
    expect(services.createCase).toHaveBeenNthCalledWith(2, ANON, "prompt", undefined, {
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
  });

  it("Phase 28 — a demoCaseId selection reaches createCase as the flat 4th-argument block (demo path only)", async () => {
    const services = makeServices();
    await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: { demoCaseId: "demo-laboratory" },
    });
    expect(services.createCase).toHaveBeenCalledWith(ANON, "prompt", undefined, {
      demoCaseId: "demo-laboratory",
    });
    // A second run WITHOUT a selection stays three arguments (byte-identical).
    const plainServices = makeServices();
    await runDemo("prompt", { services: plainServices, wait: NO_WAIT });
    expect(plainServices.createCase).toHaveBeenCalledWith(ANON, "prompt", undefined);
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
    // Phase 26C3 §12 — call-budget exhaustion is an internal bounded-
    // generation safety limit, NEVER provider unavailability.
    expect(providerBudget.failure.kind).toBe("safetyLimit");
    expect(providerBudget.failure.message).toBe(DEMO_FAILURE_MESSAGES.safetyLimit);
    expect(providerBudget.failure.message).not.toContain("unavailable");
  });
});

describe("runDemo — Phase 19/26C3 failure codes (call-budget safety limits / asset limits)", () => {
  // Phase 26C3 §12 — the FULL hierarchical provider-call-budget exhaustion
  // family maps as ONE internal bounded-generation safety-limit bucket (the
  // provider was available and returning results; this is NOT an outage).
  const PROVIDER_CALL_BUDGET_CODES = [
    "PROVIDER_CALL_BUDGET_EXHAUSTED",
    "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED",
    "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED",
  ];
  const PHASE_19_ASSET_LIMIT_CODES = [
    "MAX_PROCEDURAL_ASSETS_EXCEEDED",
    "MAX_FAILED_ASSETS_EXCEEDED",
  ];

  it.each(PROVIDER_CALL_BUDGET_CODES)(
    "maps %s (server-side FAILED on createCase) to the safety-limit message, never provider-unavailable and never the raw code",
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
      if (result.ok) throw new Error("expected call-budget failure");
      expect(result.failure.kind).toBe("safetyLimit");
      expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.safetyLimit);
      // The internal code must never reach a player-facing surface.
      expect(result.failure.message).not.toContain(failureCode);
      // Must never masquerade as provider unavailability (§12).
      expect(result.failure.message).not.toBe(DEMO_FAILURE_MESSAGES.providerUnavailable);
      expect(result.failure.message).not.toContain("unavailable");
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

  it.each(PROVIDER_CALL_BUDGET_CODES)(
    "maps %s from a polled FAILED status to the safety-limit message",
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
      if (result.ok) throw new Error("expected call-budget failure");
      expect(result.failure.kind).toBe("safetyLimit");
      expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.safetyLimit);
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

  it.each([
    "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED_AND_MORE",
    "X_PROVIDER_CALL_BUDGET_EXHAUSTED",
    "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED_TWICE",
  ])(
    "a hostile/legacy %s variant falls to the generic failed message, never narrowing into the safety-limit bucket",
    async (hostile) => {
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
      // Never narrowed into the new safety-limit bucket by prefix/substring.
      expect(result.failure.kind).not.toBe("safetyLimit");
    },
  );
});

describe("generationFailed — direct mapping (Phase 19/26C3 codes)", () => {
  it("maps the full call-budget exhaustion family to the safety-limit message, never provider-unavailable", () => {
    for (const failureCode of [
      "PROVIDER_CALL_BUDGET_EXHAUSTED",
      "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED",
      "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED",
    ]) {
      const failure = generationFailed(failureCode);
      expect(failure.kind).toBe("safetyLimit");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.safetyLimit);
      expect(failure.kind).not.toBe("provider");
      expect(failure.message).not.toBe(DEMO_FAILURE_MESSAGES.providerUnavailable);
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
      "PROVIDER_CALL_BUDGET_EXHAUSTED_AGAIN",
      "PREFIX_PROVIDER_CALL_BUDGET_EXHAUSTED",
      "MAX_PROCEDURAL_ASSETS_EXCEEDED_NOW",
      "TOO_MANY_MAX_FAILED_ASSETS_EXCEEDED",
    ]) {
      const failure = generationFailed(hostile);
      expect(failure.kind).toBe("failed");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
      expect(failure.message).not.toContain(hostile);
      // The hostile prefix must never narrow into the safety-limit bucket.
      expect(failure.kind).not.toBe("safetyLimit");
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
    expect(generationFailed("PROVIDER_CALL_BUDGET_EXHAUSTED").kind).toBe("safetyLimit");
  });

  it("keeps the DEFAULT fallback unchanged (null/undefined/unknown)", () => {
    for (const failureCode of [null, undefined, "SOME_FUTURE_CODE"]) {
      const failure = generationFailed(failureCode);
      expect(failure.kind).toBe("failed");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
    }
  });
});

describe("Phase 26C3 §12/§13 — provider QOS vs internal safety-limit mapping", () => {
  it("PROVIDER_TIMEOUT keeps its distinct provider-timeout copy (never the provider-outage copy)", () => {
    const failure = generationFailed("PROVIDER_TIMEOUT");
    expect(failure.kind).toBe("provider");
    expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.providerTimeout);
    expect(failure.message).not.toBe(DEMO_FAILURE_MESSAGES.providerUnavailable);
  });

  it("BRIDGE_NOT_CONNECTED keeps its distinct bridge-pairing copy", () => {
    const failure = generationFailed("BRIDGE_NOT_CONNECTED");
    expect(failure.kind).toBe("provider");
    expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.bridgeNotConnected);
  });

  it("a genuine provider outage (PROVIDER_UNAVAILABLE) keeps the provider-unavailable copy", () => {
    const failure = generationFailed("PROVIDER_UNAVAILABLE");
    expect(failure.kind).toBe("provider");
    expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.providerUnavailable);
  });

  it("CORE_PROVIDER_CALL_BUDGET_EXHAUSTED maps to the generation safety-limit message, NOT provider unavailability", () => {
    const failure = generationFailed("CORE_PROVIDER_CALL_BUDGET_EXHAUSTED");
    expect(failure.kind).toBe("safetyLimit");
    expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.safetyLimit);
    expect(failure.kind).not.toBe("provider");
    expect(failure.message).not.toBe(DEMO_FAILURE_MESSAGES.providerUnavailable);
    // No internal budget number or pipeline topology is ever exposed.
    expect(failure.message).not.toMatch(/\d{2,}/);
    expect(failure.message).not.toContain("core");
  });

  it("a validation failure (VALIDATION_FAILED) keeps the generic failed copy", () => {
    const failure = generationFailed("VALIDATION_FAILED");
    expect(failure.kind).toBe("failed");
    expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
  });

  it("generation deadline exhausted keeps its distinct deadline copy", () => {
    const failure = generationFailed("GENERATION_DEADLINE_EXCEEDED");
    expect(failure.kind).toBe("deadline");
    expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.deadline);
  });

  it("the full budget family also maps through a real run (createCase FAILED)", async () => {
    const result = await runDemo("prompt", {
      services: makeServices({
        createCase: vi.fn(async () => ({
          caseId: "CASE-demo-01",
          generationId: "GEN-demo-01",
          generationAttemptId: "ATT-demo-01",
          creatorAccessToken: CREATOR,
          status: "FAILED",
          failureCode: "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED",
        })),
      }),
      wait: NO_WAIT,
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected call-budget failure");
    expect(result.failure.kind).toBe("safetyLimit");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.safetyLimit);
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
  it("maps a 429 ADMISSION_DENIED on session creation to the quota failure with the generic fallback view", async () => {
    const services = makeServices({
      createSession: vi.fn(async () => {
        throw new ApiError(429, "ADMISSION_DENIED", "quota window exhausted", null);
      }),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    // Legacy envelope (no reasonCode) -> conservative temporary-capacity
    // fallback: cache PRESERVED, retryable. kind stays "quota" (backward
    // compatible with the pre-Phase36 union).
    expect(result.failure.kind).toBe("quota");
    expect(result.failure.admissionReason).toEqual(admissionFallbackView());
    expect(result.failure.message).toBe(admissionFallbackView().message);
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
    expect(result.failure.admissionReason).toEqual(admissionFallbackView());
  });
});

describe("Phase 36 §13-§17/§29/§32 — reason-aware admission mapping", () => {
  const EXPECTED_VIEWS: Record<
    string,
    Pick<AdmissionFailureView, "status" | "retryable" | "clearSessionCache">
  > = {
    SESSION_GENERATION_LIMIT: {
      status: "session-generation-limit",
      retryable: false,
      clearSessionCache: true,
    },
    SESSION_EXPIRED_OR_INVALID: {
      status: "session-generation-limit",
      retryable: false,
      clearSessionCache: true,
    },
    SESSION_CONCURRENCY_LIMIT: {
      status: "session-concurrency-limit",
      retryable: true,
      clearSessionCache: false,
    },
    GLOBAL_CONCURRENCY_LIMIT: {
      status: "global-concurrency-limit",
      retryable: true,
      clearSessionCache: false,
    },
    GLOBAL_GENERATION_WINDOW_LIMIT: {
      status: "global-window-limit",
      retryable: true,
      clearSessionCache: false,
    },
    ANONYMOUS_SESSION_CAPACITY_LIMIT: {
      status: "temporary-capacity-limit",
      // DEF-086/ADV-36-05 — the mint/creation surface itself was capped, so a
      // Try-again would only re-mint against the limit just enforced. NO
      // retry affordance (Back to start only), cache PRESERVED.
      retryable: false,
      clearSessionCache: false,
    },
  };

  it.each(Object.keys(EXPECTED_VIEWS))(
    "maps reasonCode %s to the exact reason-aware view (status/copy/retryable/cache-reset)",
    (reasonCode) => {
      const view = mapAdmissionReason(reasonCode);
      const expected = EXPECTED_VIEWS[reasonCode];
      expect(view.status).toBe(expected.status);
      expect(view.retryable).toBe(expected.retryable);
      // clearSessionCache is the ONLY input the route uses to decide
      // resetAnonymousSessionCache() — the exhausted/expired reasons clear,
      // every transient reason preserves.
      expect(view.clearSessionCache).toBe(expected.clearSessionCache);
      // Copy is never empty and never contains the raw wire token.
      expect(view.heading.length).toBeGreaterThan(0);
      expect(view.message.length).toBeGreaterThan(0);
      expect(view.heading).not.toContain("ADMISSION_DENIED");
      expect(view.message).not.toContain(reasonCode);
    },
  );

  it("SESSION_GENERATION_LIMIT uses session-specific copy with Back-to-start-only recovery", () => {
    const view = mapAdmissionReason("SESSION_GENERATION_LIMIT");
    expect(view.heading).toBe("Generation limit reached");
    expect(view.message).toContain("investigation session");
    expect(view.message).toContain("Start a new investigation to continue.");
    expect(view.retryable).toBe(false);
    expect(view.clearSessionCache).toBe(true);
  });

  it("SESSION_EXPIRED_OR_INVALID is recoverable via a fresh journey AND clears the cache", () => {
    const view = mapAdmissionReason("SESSION_EXPIRED_OR_INVALID");
    expect(view.status).toBe("session-generation-limit");
    expect(view.clearSessionCache).toBe(true);
    expect(view.retryable).toBe(false);
    expect(view.heading).toBe("Generation session unavailable");
    expect(view.message).toContain("no longer available");
    expect(view.message).toContain("Start a new investigation to continue.");
  });

  it("SESSION_CONCURRENCY_LIMIT explains the active generation and preserves the session", () => {
    const view = mapAdmissionReason("SESSION_CONCURRENCY_LIMIT");
    expect(view.status).toBe("session-concurrency-limit");
    expect(view.heading).toBe("Generation already in progress");
    expect(view.message).toContain("already running");
    expect(view.retryable).toBe(true);
    expect(view.clearSessionCache).toBe(false);
    // DEF-083/ADV-36-02 — the copy must describe ONLY actions that exist. The
    // denial surfaces at POST /cases with NO attempt handle, so the message
    // must NOT promise a navigation back to the active attempt.
    expect(view.message).not.toContain("Return to the in-progress generation");
    expect(view.message).not.toContain("Return to");
    expect(view.message).toContain("Please wait for it to finish before starting another.");
  });

  it("GLOBAL_CONCURRENCY_LIMIT is transient busy copy with a safe Try-again retry", () => {
    const view = mapAdmissionReason("GLOBAL_CONCURRENCY_LIMIT");
    expect(view.heading).toBe("Generation service is busy");
    expect(view.message).toContain("try again");
    expect(view.retryable).toBe(true);
    expect(view.clearSessionCache).toBe(false);
  });

  it("GLOBAL_GENERATION_WINDOW_LIMIT is transient capacity copy with a safe Try-again retry", () => {
    const view = mapAdmissionReason("GLOBAL_GENERATION_WINDOW_LIMIT");
    expect(view.status).toBe("global-window-limit");
    expect(view.message).toContain("try again");
    expect(view.retryable).toBe(true);
    expect(view.clearSessionCache).toBe(false);
  });

  it("ANONYMOUS_SESSION_CAPACITY_LIMIT is temporary capacity copy, cache PRESERVED, NON-retryable (§17/§47)", () => {
    const view = mapAdmissionReason("ANONYMOUS_SESSION_CAPACITY_LIMIT");
    expect(view.status).toBe("temporary-capacity-limit");
    expect(view.heading).toContain("temporarily unable to start a new session");
    expect(view.message).toContain("Please try again later");
    // DEF-086/ADV-36-05 — the capped surface is the mint/creation itself: a
    // Try-again would re-mint against the very limit just enforced, so the
    // screen must NOT invite one. Back to start only.
    expect(view.retryable).toBe(false);
    expect(view.clearSessionCache).toBe(false);
  });

  it("null / unknown / legacy reasonCode -> safe GENERIC temporary-capacity fallback, cache PRESERVED", () => {
    for (const reasonCode of [null, undefined, "", "SOME_FUTURE_REASON", "ADMISSION_DENIED"]) {
      const view = mapAdmissionReason(reasonCode);
      expect(view).toEqual(admissionFallbackView());
      expect(view.status).toBe("temporary-capacity-limit");
      expect(view.message).toBe("Generation is temporarily unavailable. Please try again.");
      expect(view.retryable).toBe(true);
      expect(view.clearSessionCache).toBe(false);
    }
  });

  it("a hostile prefix/substring variant never narrows into a reason bucket (§30 safe fallback)", () => {
    for (const hostile of [
      "SESSION_GENERATION_LIMIT_2",
      "X_GLOBAL_CONCURRENCY_LIMIT",
      "GLOBAL_GENERATION_WINDOW_LIMIT_NOW",
      "SESSION_EXPIRED_OR_INVALID_PLEASE",
      "ANONYMOUS_SESSION_CAPACITY_LIMIT_EXTRA",
      "NOT_SESSION_CONCURRENCY_LIMIT",
    ]) {
      expect(mapAdmissionReason(hostile)).toEqual(admissionFallbackView());
    }
  });

  it("admission mapping is PROVIDER-AGNOSTIC (§25/§38): the same reason maps identically regardless of journey shape/provider", async () => {
    // The mapper itself takes only `reasonCode` — no provider input at all.
    // The journey shapes (fake / ollama bridge / remote_client / frontier)
    // all flow through the same runDemo -> mapDemoError -> mapAdmissionReason
    // path; none of them can bias the admission view.
    const reason = "GLOBAL_CONCURRENCY_LIMIT";
    const expected = mapAdmissionReason(reason);

    const shapes: Array<{ generation?: unknown; label: string }> = [
      { label: "no selection" },
      { label: "fake", generation: { generationProvider: "fake" } },
      {
        label: "ollama server",
        generation: {
          generationProvider: "ollama",
          ollamaTransport: "server",
          ollamaModel: "llama3.2:3b",
        },
      },
      {
        label: "bridge/remote_client",
        generation: {
          generationProvider: "ollama",
          ollamaTransport: "bridge",
          ollamaModel: "hermes3:8b",
        },
      },
      {
        label: "frontier BYOK",
        generation: {
          generationProvider: "frontier",
          frontier: { provider: "openai", apiKey: "sk-test", model: "gpt-4o-mini" },
        },
      },
    ];

    for (const shape of shapes) {
      const services = makeServices({
        createCase: vi.fn(async () => {
          throw new ApiError(
            429,
            "ADMISSION_DENIED",
            "Generation capacity exhausted",
            null,
            reason,
          );
        }),
      });
      const result = await runDemo("prompt", {
        services,
        wait: NO_WAIT,
        generation: shape.generation as never,
      });
      expect(result.ok, shape.label).toBe(false);
      if (result.ok) throw new Error("expected failure");
      expect(result.failure.kind, shape.label).toBe("quota");
      expect(result.failure.admissionReason, shape.label).toEqual(expected);
    }
  });

  it("TOO_MANY_REQUESTS (per-IP POST /cases budget) is a DISTINCT retryable — never an admission state (§18)", () => {
    const failure = mapDemoError(new ApiError(429, "TOO_MANY_REQUESTS", "per-ip budget", null));
    expect(failure.kind).toBe("retryable");
    expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.tooManyRequests);
    // No admission view: the route can never render a session/capacity screen
    // for it, and the session cache is untouched by it.
    expect(failure.admissionReason).toBeUndefined();
    // Also proves it does not route into ADMISSION_DENIED handling even at
    // the same HTTP status.
    expect(mapAdmissionReason(null)).toEqual(admissionFallbackView());
  });

  it("DEF-082 — a 401 SESSION_EXPIRED (the REAL durable expired-session answer) maps to the SAME session-recovery view as SESSION_EXPIRED_OR_INVALID", () => {
    const failure = mapDemoError(
      new ApiError(401, "SESSION_EXPIRED", "credential expired or unknown", null),
    );
    // `kind` stays "quota" so the route renders the reason-aware recovery view.
    expect(failure.kind).toBe("quota");
    expect(failure.admissionReason).toEqual(mapAdmissionReason("SESSION_EXPIRED_OR_INVALID"));
    expect(failure.admissionReason).toEqual(sessionExpiredView());
    expect(failure.admissionReason!.status).toBe("session-generation-limit");
    expect(failure.admissionReason!.heading).toBe("Generation session unavailable");
    expect(failure.admissionReason!.clearSessionCache).toBe(true);
    // DEF-082 core: the dead token gets NO "Try again" affordance — a retry
    // would only re-use the same dead token forever (the old dead-token retry
    // loop). NO auto-mint: identity is minted only on the next explicit journey.
    expect(failure.admissionReason!.retryable).toBe(false);
    expect(failure.message).toBe(failure.admissionReason!.message);
  });

  it("DEF-082 — a REAL run with a 401 SESSION_EXPIRED on POST /cases surfaces the session-recovery quota view (no dead-token retry)", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => {
        throw new ApiError(401, "SESSION_EXPIRED", "credential expired or unknown", null);
      }),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("quota");
    expect(result.failure.admissionReason).toEqual(sessionExpiredView());
    expect(result.failure.admissionReason!.clearSessionCache).toBe(true);
    expect(result.failure.admissionReason!.retryable).toBe(false);
    // The identity is gone — a retry would reuse the dead token forever.
    expect(result.failure.admissionReason!.heading).toBe("Generation session unavailable");
  });

  it("DEF-082 — 401 UNAUTHORIZED is a DISTINCT surface (absent/malformed bearer for every token class) and is NOT routed to the session-recovery view", () => {
    // The auth dependency answers UNAUTHORIZED for absent/malformed headers on
    // session/creator/playthrough credentials alike — mapping it to a
    // dead-session recovery could break non-session auth errors. ONLY the
    // exact-code SESSION_EXPIRED branch maps (DEF-082).
    const failure = mapDemoError(new ApiError(401, "UNAUTHORIZED", "Request failed", null));
    expect(failure.kind).toBe("retryable");
    expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.generic);
    expect(failure.admissionReason).toBeUndefined();
    // A hostile/legacy prefix of the real code never narrows into the branch.
    const hostile = mapDemoError(new ApiError(401, "SESSION_EXPIRED_AND_MORE", "x", null));
    expect(hostile.kind).toBe("retryable");
    expect(hostile.message).toBe(DEMO_FAILURE_MESSAGES.generic);
    const wrongStatus = mapDemoError(new ApiError(400, "SESSION_EXPIRED", "x", null));
    expect(wrongStatus.kind).toBe("retryable");
    expect(wrongStatus.message).toBe(DEMO_FAILURE_MESSAGES.generic);
  });

  it("mapDemoError forwards the backend reasonCode into the admission view for ADMISSION_DENIED", () => {
    const failure = mapDemoError(
      new ApiError(
        429,
        "ADMISSION_DENIED",
        "Generation capacity exhausted",
        null,
        "GLOBAL_CONCURRENCY_LIMIT",
      ),
    );
    expect(failure.kind).toBe("quota");
    expect(failure.admissionReason).toEqual(mapAdmissionReason("GLOBAL_CONCURRENCY_LIMIT"));
    expect(failure.message).toBe(mapAdmissionReason("GLOBAL_CONCURRENCY_LIMIT").message);
  });

  it("runDemo surfaces the full reason-aware view for EVERY backend reason code", async () => {
    for (const reasonCode of Object.keys(EXPECTED_VIEWS)) {
      const services = makeServices({
        createCase: vi.fn(async () => {
          throw new ApiError(
            429,
            "ADMISSION_DENIED",
            "Generation capacity exhausted",
            null,
            reasonCode,
          );
        }),
      });
      const result = await runDemo("prompt", { services, wait: NO_WAIT });
      expect(result.ok, reasonCode).toBe(false);
      if (result.ok) throw new Error("expected failure");
      expect(result.failure.kind, reasonCode).toBe("quota");
      expect(result.failure.admissionReason, reasonCode).toEqual(
        mapAdmissionReason(reasonCode),
      );
    }
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

describe("Phase 30 §23 — BYOK Frontier failure-code mapping (generationFailed)", () => {
  it("maps the five FRONTIER_* provider codes to their frozen safe copy, never the raw code", () => {
    const cases: Array<[string, string]> = [
      ["FRONTIER_AUTH_FAILED", DEMO_FAILURE_MESSAGES.frontierAuthFailed],
      [
        "FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND",
        DEMO_FAILURE_MESSAGES.frontierEndpointOrModelNotFound,
      ],
      ["FRONTIER_RATE_LIMITED", DEMO_FAILURE_MESSAGES.frontierRateLimited],
      ["FRONTIER_TIMEOUT", DEMO_FAILURE_MESSAGES.frontierTimeout],
      ["FRONTIER_PROVIDER_ERROR", DEMO_FAILURE_MESSAGES.frontierProviderError],
    ];
    for (const [failureCode, expected] of cases) {
      const failure = generationFailed(failureCode);
      expect(failure.kind).toBe("provider");
      expect(failure.message).toBe(expected);
      expect(failure.message).not.toContain(failureCode);
      expect(failure.message).not.toContain("_");
    }
  });

  it("exact copy for each of the five codes (§23 wording)", () => {
    expect(generationFailed("FRONTIER_AUTH_FAILED").message).toBe(
      "The selected provider rejected the supplied API credentials.",
    );
    expect(generationFailed("FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND").message).toBe(
      "The selected provider or model could not be found. Check the model name, then try again.",
    );
    expect(generationFailed("FRONTIER_RATE_LIMITED").message).toBe(
      "The selected provider is rate-limiting requests right now. Wait a moment, then try again.",
    );
    expect(generationFailed("FRONTIER_TIMEOUT").message).toBe(
      "The selected provider took too long to respond. Please try again.",
    );
    expect(generationFailed("FRONTIER_PROVIDER_ERROR").message).toBe(
      "The selected provider reported an error. Please try again.",
    );
  });

  it("maps the backend's 400-level INVALID_FRONTIER_CONFIG code to the safe 'check your provider/key/model' copy", () => {
    const failure = generationFailed("INVALID_FRONTIER_CONFIG");
    expect(failure.kind).toBe("provider");
    expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.frontierInvalidConfiguration);
    expect(failure.message).not.toContain("INVALID_FRONTIER_CONFIG");
  });

  it("exact strings only — a hostile/legacy prefix or substring variant NEVER narrows into the Frontier buckets", () => {
    for (const hostile of [
      "FRONTIER_AUTH_FAILED_2",
      "X_FRONTIER_RATE_LIMITED",
      "FRONTIER_TIMEOUT_NOW",
      "INVALID_FRONTIER_CONFIG_EXTRA",
      "X_INVALID_FRONTIER_CONFIG",
      "PROVIDER_FRONTIER_ERROR",
      "NOT_FRONTIER_AUTH_FAILED",
    ]) {
      const failure = generationFailed(hostile);
      expect(failure.kind).toBe("failed");
      expect(failure.message).toBe(DEMO_FAILURE_MESSAGES.failed);
      expect(failure.message).not.toContain(hostile);
    }
  });

  it("keeps every pre-Phase-30 mapping unchanged (no narrowing regressions)", () => {
    expect(generationFailed("PROVIDER_TIMEOUT").kind).toBe("provider");
    expect(generationFailed("PROVIDER_UNAVAILABLE").kind).toBe("provider");
    expect(generationFailed("GENERATION_DEADLINE_EXCEEDED").kind).toBe("deadline");
    expect(generationFailed("BRIDGE_NOT_CONNECTED").message).toBe(
      DEMO_FAILURE_MESSAGES.bridgeNotConnected,
    );
    expect(generationFailed("CORE_PROVIDER_CALL_BUDGET_EXHAUSTED").kind).toBe("safetyLimit");
  });
});

describe("Phase 30 §23 — Frontier codes also map from a REAL run and from mapDemoError", () => {
  it("a FAILED frontier generation (createCase returns FAILED with FRONTIER_AUTH_FAILED) surfaces the frozen copy", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => ({
        caseId: "CASE-demo-01",
        generationId: "GEN-demo-01",
        generationAttemptId: "ATT-demo-01",
        creatorAccessToken: CREATOR,
        status: "FAILED",
        failureCode: "FRONTIER_AUTH_FAILED",
      })),
    });
    const result = await runDemo("prompt", { services, wait: NO_WAIT });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("provider");
    expect(result.failure.message).toBe(
      "The selected provider rejected the supplied API credentials.",
    );
    expect(result.failure.message).not.toContain("FRONTIER_AUTH_FAILED");
  });

  it("an HTTP-rejected frontier attempt (400 INVALID_FRONTIER_CONFIG via mapDemoError) maps to the safe copy", async () => {
    const services = makeServices({
      createCase: vi.fn(async () => {
        throw new ApiError(400, "INVALID_FRONTIER_CONFIG", "provider or model invalid", null);
      }),
    });
    const result = await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: {
        generationProvider: "frontier",
        frontier: { provider: "openai", apiKey: "sk-test", model: "x" },
      },
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.failure.kind).toBe("provider");
    expect(result.failure.message).toBe(DEMO_FAILURE_MESSAGES.frontierInvalidConfiguration);
    expect(result.failure.message).not.toContain("INVALID_FRONTIER_CONFIG");
    expect(result.failure.message).not.toContain("x");
    // Exactly one attempt — no silent fallback after the explicit selection.
    expect(services.createCase).toHaveBeenCalledTimes(1);
  });

  it("mapDemoError maps the FRONTIER_* codes exactly and keeps non-frontier codes untouched", () => {
    const auth = mapDemoError(new ApiError(502, "FRONTIER_AUTH_FAILED", "upstream", null));
    expect(auth.kind).toBe("provider");
    expect(auth.message).toBe(DEMO_FAILURE_MESSAGES.frontierAuthFailed);
    const rate = mapDemoError(new ApiError(429, "FRONTIER_RATE_LIMITED", "slow down", null));
    expect(rate.message).toBe(DEMO_FAILURE_MESSAGES.frontierRateLimited);
    // ADMISSION_DENIED still wins for the quota path (checked BEFORE frontier).
    const quota = mapDemoError(new ApiError(429, "ADMISSION_DENIED", "quota", null));
    expect(quota.kind).toBe("quota");
    // Unknown codes still fall to the generic retryable copy.
    const unknown = mapDemoError(new ApiError(400, "FRONTIER_WHATEVER_FUTURE", "x", null));
    expect(unknown.kind).toBe("retryable");
    expect(unknown.message).toBe(DEMO_FAILURE_MESSAGES.generic);
    // A hostile/legacy prefix variant of the real 400 code NEVER narrows in.
    const hostileConfig = mapDemoError(
      new ApiError(400, "INVALID_FRONTIER_CONFIG_EXTRA", "x", null),
    );
    expect(hostileConfig.kind).toBe("retryable");
    expect(hostileConfig.message).toBe(DEMO_FAILURE_MESSAGES.generic);
  });

  it("frontierFailureMessage is the exact-string single helper used by both paths", () => {
    expect(frontierFailureMessage("FRONTIER_AUTH_FAILED")).toBe(
      "The selected provider rejected the supplied API credentials.",
    );
    expect(frontierFailureMessage("INVALID_FRONTIER_CONFIG")).toBe(
      DEMO_FAILURE_MESSAGES.frontierInvalidConfiguration,
    );
    expect(frontierFailureMessage("INVALID_FRONTIER_CONFIG_EXTRA")).toBeNull();
    expect(frontierFailureMessage("X_INVALID_FRONTIER_CONFIG")).toBeNull();
    expect(frontierFailureMessage("FRONTIER_AUTH_FAILED_X")).toBeNull();
    expect(frontierFailureMessage(null)).toBeNull();
    expect(frontierFailureMessage(undefined)).toBeNull();
  });

  it("runDemo carries a COMPLETE frontier generation block through to createCase unchanged", async () => {
    const services = makeServices();
    const result = await runDemo("prompt", {
      services,
      wait: NO_WAIT,
      generation: {
        generationProvider: "frontier",
        frontier: { provider: "openai", apiKey: "sk-test", model: "gpt-4o-mini" },
      },
    });
    expect(result.ok).toBe(true);
    expect(services.createCase).toHaveBeenCalledWith(ANON, "prompt", undefined, {
      generationProvider: "frontier",
      frontier: { provider: "openai", apiKey: "sk-test", model: "gpt-4o-mini" },
    });
  });
});
