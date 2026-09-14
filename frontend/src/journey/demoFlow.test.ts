import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { DEMO_FAILURE_MESSAGES, pollDelayMs, runDemo, type DemoFlowServices, type DemoProgress } from "./demoFlow";

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