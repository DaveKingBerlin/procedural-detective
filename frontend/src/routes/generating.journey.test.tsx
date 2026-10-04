// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GenerationJourney, type RunFn } from "./generating";
import type { CapabilityLoader } from "./generating";
import type { DemoFlowResult, DemoFlowServices } from "../journey/demoFlow";
import { runDemo } from "../journey/demoFlow";
import { ApiError } from "../api/client";
import {
  createOrReuseAnonymousSession,
  getCachedAnonymousSession,
  resetAnonymousSessionCache,
} from "../api/anonymousSession";
import type { JourneyParams } from "../journey/context";
import { DEMO_CASE_ID_STORAGE_KEY } from "../journey/demoCaseSelection";

// React's test utilities need the act() environment flag (same as the other
// jsdom suites in this repo).
declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/**
 * ADV-212 — the /generating journey never claims the Local-AI label sequence
 * from an unverified localStorage value. The stored `pd_generation_mode` is
 * validated against the LIVE generation-capabilities DTO before ANY label is
 * chosen: a stale/tampered `local` on a demo-only (or failing) capability
 * probe resolves to the generic/demo sequence, and the SAME validated mode
 * drives both the pre-poll animation and the run's progress snapshots.
 */

const PARAMS: JourneyParams = { prompt: "A crime", difficulty: "medium" };

const demoOnly: CapabilityLoader = async () => ({
  modes: [{ id: "demo", available: true }],
});
const localReady: CapabilityLoader = async () => ({
  modes: [
    { id: "demo", available: true },
    { id: "local", available: true, label: "Local AI", model: "llama3.2:3b" },
  ],
});
const failing: CapabilityLoader = async () => {
  throw new Error("probe unreachable");
};

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  // Phase 24 P0 §8 — the in-memory anonymous-session holder is module-global;
  // reset it so each test starts with a clean one-session-per-page state.
  resetAnonymousSessionCache();
  // Stale/tampered stored mode: every scenario below starts from this state.
  localStorage.setItem("pd_generation_mode", "local");
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  container.remove();
  localStorage.clear();
  sessionStorage.clear();
  resetAnonymousSessionCache();
});

/** Let the async capability probe + effect chain settle inside an act scope. */
async function settleEffects(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

interface Mounted {
  run: ReturnType<typeof vi.fn<(...args: readonly unknown[]) => Promise<DemoFlowResult>>>;
  /** Resolve the gated run promise (ends the journey with a safe failure). */
  releaseRun: () => Promise<void>;
}

function mountJourney(loadCapabilities: CapabilityLoader, params: JourneyParams = PARAMS): Mounted {
  let release: ((result: DemoFlowResult) => void) | null = null;
  const run = vi.fn<(...args: readonly unknown[]) => Promise<DemoFlowResult>>(
    (_prompt, _difficulty, _onProgress, _mode, _anonymousToken, _generation) => {
      const gate = new Promise<DemoFlowResult>((resolve) => {
        release = resolve;
      });
      return gate;
    },
  );
  act(() => {
    root = createRoot(container);
    root.render(
      <MemoryRouter initialEntries={["/generating"]}>
        <GenerationJourney
          params={params}
          run={run}
          onSuccess={() => {
            throw new Error("must never auto-enter in these scenarios");
          }}
          loadCapabilities={loadCapabilities}
        />
      </MemoryRouter>,
    );
  });
  const releaseRun = async () => {
    await act(async () => {
      if (!release) throw new Error("run gate not created yet");
      release({ ok: false, failure: { kind: "failed", message: "test only" } });
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });
  };
  return { run, releaseRun };
}

function stageLabel(): string | null {
  const el = container.querySelector<HTMLElement>('[data-testid="generation-stage-label"]');
  return el?.textContent ?? null;
}

function failedView(): boolean {
  return container.querySelector('[data-testid="generation-failed"]') !== null;
}

describe("ADV-212 — the /generating label sequence requires LIVE capability confirmation", () => {
  it("stale local storage + demo-only capabilities -> generic labels; run receives null", async () => {
    const { run, releaseRun } = mountJourney(demoOnly);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][3]).toBeNull(); // validated mode, never raw storage
    expect(stageLabel()).toBe("Creating case");
    expect(stageLabel()).not.toContain("Understanding the case");
    await releaseRun();
    expect(failedView()).toBe(true); // the run really happened with the validated mode
  });

  it("local storage + capabilities confirm local ready -> local labels; run receives local", async () => {
    const { run, releaseRun } = mountJourney(localReady);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][3]).toBe("local");
    expect(stageLabel()).toBe("Understanding the case…");
    await releaseRun();
  });

  it("capabilities fetch failure -> generic labels (never a local pipeline claim)", async () => {
    const { run, releaseRun } = mountJourney(failing);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][3]).toBeNull();
    expect(stageLabel()).toBe("Creating case");
    expect(stageLabel()).not.toContain("Understanding the case");
    expect(stageLabel()).not.toContain("crime scene");
    await releaseRun();
    expect(failedView()).toBe(true);
  });

  it("well-formed local flow unchanged: capabilities confirm -> the Local-AI sequence is claimed", async () => {
    // (Stand-in for the /new-driven happy path: storage says local AND the
    // live DTO confirms it — the §21 sequence is exactly as before.)
    const { run, releaseRun } = mountJourney(localReady);
    await settleEffects();
    expect(run.mock.calls[0][3]).toBe("local");
    expect(stageLabel()).toBe("Understanding the case…");
    await releaseRun();
  });
});

describe("Phase 24 P0 §8 — a /generating retry reuses the cached anonymous session (churn fix)", () => {
  it("a failed run + Try again mints the anonymous session EXACTLY ONCE and the retried createCase stays authorized under the SAME token", async () => {
    // The counting mint supplier backs createOrReuseAnonymousSession: the
    // underlying createAnonymousSession call must happen exactly once even
    // across the runId retry.
    const supplier = vi.fn<(...args: readonly unknown[]) => Promise<{ anonymousSessionToken: string; quotaWindowEndsAt: number }>>(
      async () => ({ anonymousSessionToken: "anon-retry-0001", quotaWindowEndsAt: 1e12 }),
    );
    // First run: the session has not paired its bridge yet -> the backend
    // rejects POST /cases with BRIDGE_NOT_CONNECTED. Second run (after Try
    // again, with the bridge now bound to the SAME session): PUBLISHED.
    const createCase = vi
      .fn<(...args: readonly unknown[]) => Promise<{
        caseId: string;
        generationId: string;
        generationAttemptId: string;
        creatorAccessToken: string;
        status: string;
        failureCode?: string | null;
      }>>()
      .mockResolvedValueOnce({
        caseId: "CASE-1",
        generationId: "GEN-1",
        generationAttemptId: "ATT-1",
        creatorAccessToken: "creator-1",
        status: "FAILED",
        failureCode: "BRIDGE_NOT_CONNECTED",
      })
      .mockResolvedValueOnce({
        caseId: "CASE-2",
        generationId: "GEN-2",
        generationAttemptId: "ATT-2",
        creatorAccessToken: "creator-2",
        status: "PUBLISHED",
      });

    const services: DemoFlowServices = {
      createSession: () => createOrReuseAnonymousSession(supplier),
      createCase,
      pollGeneration: vi.fn(async () => ({
        caseId: "CASE-2",
        generationId: "GEN-2",
        status: "PUBLISHED",
        progress: 100,
        stage: null,
      })),
      createPlaythrough: vi.fn(async () => ({
        playthroughId: "PT-0001",
        caseId: "CASE-2",
        caseVersion: 1,
        playthroughAccessToken: "pt-token-0001",
        status: "PLAYING",
      })),
    };

    const run: RunFn = (prompt, difficulty, onProgress, mode, anonymousSessionToken) =>
      runDemo(prompt, {
        services,
        difficulty,
        mode,
        onProgress,
        wait: async () => {},
        anonymousSessionToken,
      });

    let enterCalls = 0;
    act(() => {
      root = createRoot(container);
      root.render(
        <MemoryRouter initialEntries={["/generating"]}>
          <GenerationJourney
            params={PARAMS}
            run={run}
            onSuccess={() => {
              enterCalls += 1;
            }}
            loadCapabilities={demoOnly}
          />
        </MemoryRouter>,
      );
    });
    await settleEffects();

    // First run: exactly ONE anonymous-session mint, and createCase was
    // called under that session (then failed with BRIDGE_NOT_CONNECTED).
    expect(supplier).toHaveBeenCalledTimes(1);
    expect(createCase).toHaveBeenCalledTimes(1);
    expect(createCase.mock.calls[0][0]).toBe("anon-retry-0001");
    expect(failedView()).toBe(true);

    // Try again: runId increments -> the journey re-runs runDemo. The
    // in-memory holder returns the SAME session, so the underlying minting
    // call stays at exactly once and createCase is authorized under the SAME
    // token this time.
    const retryButton = container.querySelector<HTMLElement>(
      'button[data-testid="generation-failed"]',
    );
    if (!retryButton) throw new Error("expected the Try again button");
    act(() => {
      retryButton.click();
    });
    await settleEffects();

    expect(supplier).toHaveBeenCalledTimes(1);
    expect(createCase).toHaveBeenCalledTimes(2);
    expect(createCase.mock.calls[1][0]).toBe("anon-retry-0001");
    // The retried run really reached the published/done state (its render
    // carries the Enter investigation action).
    expect(container.querySelector('[data-testid="enter-investigation"]')).not.toBeNull();
    // Auto-entry into the investigation only happens after its own beat — we
    // assert the run itself reached the done state, not a premature auto-nav.
    expect(enterCalls).toBe(0);
  });
});

describe("Phase 24 F-2 — a session-window-DENIED run recovers by reload, never by auto-minting", () => {
  it("a 429 ADMISSION_DENIED run clears the module holder, shows the reload guidance and mints NO fresh session", async () => {
    // Counting mint supplier backs createOrReuseAnonymousSession: exactly ONE
    // underlying mint may happen (the initial session). The 429 denial must
    // NOT trigger a second mint — the server rate limit stays authoritative.
    const supplier = vi.fn<
      (...args: readonly unknown[]) => Promise<{
        anonymousSessionToken: string;
        quotaWindowEndsAt: number;
      }>
    >(async () => ({ anonymousSessionToken: "anon-window-0001", quotaWindowEndsAt: 1e12 }));
    // The backend admits the first attempt under the freshly-minted session,
    // then denies the per-session generation window on POST /cases with the
    // sanitized 429 ADMISSION_DENIED envelope (the F-2 lock scenario: the
    // page previously consumed the whole per-session window).
    const createCase = vi.fn<(...args: readonly unknown[]) => Promise<never>>(async () => {
      throw new ApiError(429, "ADMISSION_DENIED", "admission denied", null);
    });

    const services: DemoFlowServices = {
      createSession: () => createOrReuseAnonymousSession(supplier),
      createCase,
      pollGeneration: vi.fn(async () => {
        throw new Error("unused");
      }),
      createPlaythrough: vi.fn(async () => {
        throw new Error("unused");
      }),
    };

    const run: RunFn = (prompt, difficulty, onProgress, mode, anonymousSessionToken) =>
      runDemo(prompt, {
        services,
        difficulty,
        mode,
        onProgress,
        wait: async () => {},
        anonymousSessionToken,
      });

    act(() => {
      root = createRoot(container);
      root.render(
        <MemoryRouter initialEntries={["/generating"]}>
          <GenerationJourney
            params={PARAMS}
            run={run}
            onSuccess={() => {
              throw new Error("must never auto-enter on a denied run");
            }}
            loadCapabilities={demoOnly}
          />
        </MemoryRouter>,
      );
    });
    await settleEffects();

    // The run really happened under exactly one freshly-minted session.
    expect(supplier).toHaveBeenCalledTimes(1);
    expect(createCase).toHaveBeenCalledTimes(1);
    expect(createCase.mock.calls[0][0]).toBe("anon-window-0001");

    // The window-denied run CLEARED the module holder (recovery = a reload /
    // next page gets a clean session; no auto-mint on the 429).
    expect(getCachedAnonymousSession()).toBeNull();
    expect(supplier).toHaveBeenCalledTimes(1);

    // Explicit recovery guidance is shown instead of a Try-again error.
    expect(
      container.querySelector('[data-testid="generation-session-limit"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="generation-session-limit-reload"]'),
    ).not.toBeNull();
    // Deliberately NO Try-again button: re-running would reuse the exhausted
    // holder session and fail forever (or auto-mint, which would defeat the
    // limit) — reload is the only recovery beyond Back to start.
    expect(container.querySelector('button[data-testid="generation-failed"]')).toBeNull();
  });
});

describe("Phase 25 — the generation-selection carried from /new reaches the journey", () => {
  it("passes the selection as the RunFn 6th argument (transport/model only for ollama)", async () => {
    const params: JourneyParams = {
      prompt: "A crime",
      difficulty: "medium",
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "qwen2.5:1.5b",
    };
    const { run, releaseRun } = mountJourney(demoOnly, params);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    // Existing arg positions are stable: index 3 = validated mode, index
    // 4 = anonymous token (none here), index 5 = the Phase 25 selection.
    expect(run.mock.calls[0][3]).toBeNull();
    expect(run.mock.calls[0][5]).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "qwen2.5:1.5b",
    });
    await releaseRun();
  });

  it("a first/second case without a selection keeps the RunFn generation argument undefined", async () => {
    const { run, releaseRun } = mountJourney(demoOnly, PARAMS);
    await settleEffects();
    expect(run.mock.calls[0][5]).toBeUndefined();
    await releaseRun();
  });

  it("an ollama+bridge selection carried from /new reaches the RunFn unchanged (byte-exact transport)", async () => {
    const params: JourneyParams = {
      prompt: "A crime",
      difficulty: "medium",
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    };
    const { run, releaseRun } = mountJourney(demoOnly, params);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][5]).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
    await releaseRun();
  });

  it("a fake-only selection travels WITHOUT transport/model fields", async () => {
    const params: JourneyParams = {
      prompt: "A crime",
      difficulty: "medium",
      generationProvider: "fake",
    };
    const { run, releaseRun } = mountJourney(demoOnly, params);
    await settleEffects();
    expect(run.mock.calls[0][5]).toEqual({ generationProvider: "fake" });
    await releaseRun();
  });

  it("Phase 28 — a demo-case id carried from the demo path reaches the RunFn generation block", async () => {
    const params: JourneyParams = {
      prompt: "A crime",
      difficulty: "medium",
      demoCaseId: "demo-gallery",
    };
    const { run, releaseRun } = mountJourney(demoOnly, params);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][3]).toBeNull(); // validated mode unchanged
    expect(run.mock.calls[0][5]).toEqual({ demoCaseId: "demo-gallery" });
    await releaseRun();
  });

  it("Phase 28 — a journey WITHOUT a demo id carries NO demoCaseId (generated cases unchanged)", async () => {
    const { run, releaseRun } = mountJourney(demoOnly, PARAMS);
    await settleEffects();
    expect(run.mock.calls[0][5]).toBeUndefined();
    await releaseRun();
  });
});

describe("Phase 28 §17 — Back to start resets the per-session demo holder", () => {
  it("clicking Back to start from a failed demo clears pd_demo_case_id so the NEXT Try Demo Case may roll fresh", async () => {
    sessionStorage.setItem(DEMO_CASE_ID_STORAGE_KEY, "demo-apartment");
    const { run, releaseRun } = mountJourney(demoOnly);
    await settleEffects();
    await releaseRun(); // the run fails -> the error view (with Back to start)
    expect(run).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(DEMO_CASE_ID_STORAGE_KEY)).toBe("demo-apartment");

    const link = container.querySelector<HTMLAnchorElement>(
      'a[data-testid="generation-back-to-start"]',
    );
    if (!link) throw new Error("expected the Back to start link");
    act(() => {
      link.click();
    });

    // A deliberate reset: the holder is cleared (a refresh of an ACTIVE demo
    // never goes through this path, so its fixture is never re-rolled).
    expect(sessionStorage.getItem(DEMO_CASE_ID_STORAGE_KEY)).toBeNull();
  });
});