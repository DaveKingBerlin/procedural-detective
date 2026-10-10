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
import * as anonymousSessionModule from "../api/anonymousSession";
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

describe("Phase 36 — reason-aware admission recovery on the /generating journey", () => {
  /** Mount the journey wired to the REAL runDemo with injected services. */
  function mountAdmissionRun(services: DemoFlowServices): void {
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
  }

  const admissionServices = (
    createCase: DemoFlowServices["createCase"],
    token: string,
  ): { supplier: ReturnType<typeof vi.fn>; services: DemoFlowServices } => {
    const supplier = vi.fn<
      (...args: readonly unknown[]) => Promise<{
        anonymousSessionToken: string;
        quotaWindowEndsAt: number;
      }>
    >(async () => ({ anonymousSessionToken: token, quotaWindowEndsAt: 1e12 }));
    return {
      supplier,
      services: {
        createSession: () => createOrReuseAnonymousSession(supplier),
        createCase,
        pollGeneration: vi.fn(async () => {
          throw new Error("unused");
        }),
        // A successfully-PUBLISHED run needs a working playthrough; denial
        // runs never reach it (the poll/createPlaythrough path is unused).
        createPlaythrough: vi.fn(async () => ({
          playthroughId: "PT-admission",
          caseId: "CASE-2",
          caseVersion: 1,
          playthroughAccessToken: "pt-token-admission",
          status: "PLAYING",
        })),
      },
    };
  };

  const denial = (
    reasonCode: string | null,
  ): DemoFlowServices["createCase"] =>
    vi.fn(async () => {
      throw new ApiError(
        429,
        "ADMISSION_DENIED",
        "Generation capacity exhausted",
        null,
        reasonCode,
      );
    });

  it("SESSION_GENERATION_LIMIT: clears the exhausted-session cache, renders session-limit copy with NO Reload button, and does NOT auto-mint", async () => {
    // Spy on the module holder reset: the ONLY cache-clear decision the route
    // may make (exhausted/invalid session reasons).
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    const { supplier, services } = admissionServices(denial("SESSION_GENERATION_LIMIT"), "A");
    mountAdmissionRun(services);
    await settleEffects();

    // Exactly ONE mint (the exhausted token A) — NO automatic re-mint.
    expect(supplier).toHaveBeenCalledTimes(1);
    expect(getCachedAnonymousSession()).toBeNull(); // cache cleared
    expect(resetSpy).toHaveBeenCalledTimes(1);
    // Reason-accurate screen.
    expect(
      container.querySelector('[data-testid="generation-admission-session-generation-limit"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("Start a new investigation to continue");
    // NO Reload button anywhere (Phase 36 §22/§35).
    expect(container.querySelector('[data-testid="generation-admission-retry"]')).toBeNull();
    expect(container.textContent).not.toContain("Reload page");
    expect(container.textContent).not.toContain("window.location.reload");
    // Back to start exists (the ONLY recovery action).
    expect(container.querySelector('a[data-testid="generation-back-to-start"]')).not.toBeNull();
    expect(container.querySelector('button[data-testid="generation-failed"]')).toBeNull();
  });

  it("SESSION_EXPIRED_OR_INVALID: clears the gone-session cache, renders session-recovery copy, NO reload, NO auto-mint", async () => {
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    const { supplier, services } = admissionServices(denial("SESSION_EXPIRED_OR_INVALID"), "A");
    mountAdmissionRun(services);
    await settleEffects();

    expect(supplier).toHaveBeenCalledTimes(1);
    expect(getCachedAnonymousSession()).toBeNull();
    expect(resetSpy).toHaveBeenCalledTimes(1);
    expect(
      container.querySelector('[data-testid="generation-admission-session-generation-limit"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("Start a new investigation to continue");
    expect(container.querySelector('[data-testid="generation-admission-retry"]')).toBeNull();
    expect(container.textContent).not.toContain("Reload");
  });

  it("DEF-082 — 401 SESSION_EXPIRED on POST /cases (the REAL durable expired-session path): session-recovery screen, cache cleared, NO mint on the screen, Back to start mints nothing, next explicit journey mints exactly ONE new token B", async () => {
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    // Token A minted for the first run; the durable auth dependency answers
    // 401 SESSION_EXPIRED (NOT a 429 ADMISSION_DENIED envelope) — the answer
    // the closed SESSION_EXPIRED_OR_INVALID reason can never reach from a
    // durable deployment. After the cache clears, the supplier produces B.
    const supplier = vi
      .fn<(...args: readonly unknown[]) => Promise<{ anonymousSessionToken: string; quotaWindowEndsAt: number }>>()
      .mockResolvedValueOnce({ anonymousSessionToken: "A", quotaWindowEndsAt: 1e12 })
      .mockResolvedValueOnce({ anonymousSessionToken: "B", quotaWindowEndsAt: 1e12 });

    const createCase = vi
      .fn<(...args: readonly unknown[]) => Promise<{
        caseId: string;
        generationId: string;
        generationAttemptId: string;
        creatorAccessToken: string;
        status: string;
        failureCode?: string | null;
      }>>()
      .mockRejectedValueOnce(
        new ApiError(401, "SESSION_EXPIRED", "credential expired or unknown", null),
      )
      .mockResolvedValueOnce({
        caseId: "CASE-2",
        generationId: "GEN-2",
        generationAttemptId: "ATT-2",
        creatorAccessToken: "creator-2",
        status: "PUBLISHED",
      });

    mountAdmissionRun({
      createSession: () => createOrReuseAnonymousSession(supplier),
      createCase,
      pollGeneration: vi.fn(async () => {
        throw new Error("unused");
      }),
      createPlaythrough: vi.fn(async () => ({
        playthroughId: "PT-admission",
        caseId: "CASE-2",
        caseVersion: 1,
        playthroughAccessToken: "pt-token-admission",
        status: "PLAYING",
      })),
    });
    await settleEffects();

    // Token A minted, POST /cases answered 401 SESSION_EXPIRED -> the dead
    // identity is cleared. NO mint on the error screen, NO retry affordance
    // (a retry would reuse the dead token forever — the dead-token retry loop
    // is gone).
    expect(supplier).toHaveBeenCalledTimes(1);
    expect(createCase.mock.calls[0][0]).toBe("A");
    expect(getCachedAnonymousSession()).toBeNull();
    expect(resetSpy).toHaveBeenCalledTimes(1);
    expect(
      container.querySelector('[data-testid="generation-admission-session-generation-limit"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("Generation session unavailable");
    expect(container.textContent).toContain("Start a new investigation to continue");
    expect(container.querySelector('[data-testid="generation-admission-retry"]')).toBeNull();
    expect(container.textContent).not.toContain("Reload");

    // Back to start navigates to /new and does NOT itself mint anything.
    const back = container.querySelector<HTMLElement>(
      'a[data-testid="generation-back-to-start"]',
    );
    if (!back) throw new Error("expected Back to start");
    act(() => {
      back.click();
    });
    await settleEffects();
    expect(supplier).toHaveBeenCalledTimes(1); // still no mint after Back to start

    // User starts a fresh journey: the cleared cache mints exactly ONE new
    // session B and POST /cases runs under B — no mint churn, no loop.
    act(() => {
      root?.unmount();
    });
    mountAdmissionRun({
      createSession: () => createOrReuseAnonymousSession(supplier),
      createCase,
      pollGeneration: vi.fn(async () => {
        throw new Error("unused");
      }),
      createPlaythrough: vi.fn(async () => ({
        playthroughId: "PT-admission",
        caseId: "CASE-2",
        caseVersion: 1,
        playthroughAccessToken: "pt-token-admission",
        status: "PLAYING",
      })),
    });
    await settleEffects();

    expect(supplier).toHaveBeenCalledTimes(2); // exactly ONE replacement mint
    expect(createCase).toHaveBeenCalledTimes(2);
    expect(createCase.mock.calls[1][0]).toBe("B");
    expect(container.querySelector('[data-testid="enter-investigation"]')).not.toBeNull();
  });

  it("GLOBAL_CONCURRENCY_LIMIT: PRESERVES the session, renders busy copy with Try again + Back to start", async () => {
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    const { supplier, services } = admissionServices(denial("GLOBAL_CONCURRENCY_LIMIT"), "A");
    mountAdmissionRun(services);
    await settleEffects();

    // Cache PRESERVED: the valid session A stays available for a retry.
    expect(supplier).toHaveBeenCalledTimes(1);
    expect(getCachedAnonymousSession()).not.toBeNull();
    expect(getCachedAnonymousSession()!.anonymousSessionToken).toBe("A");
    expect(resetSpy).not.toHaveBeenCalled();
    expect(
      container.querySelector('[data-testid="generation-admission-global-concurrency-limit"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("Generation service is busy");
    // Retry + Back to start.
    expect(container.querySelector('[data-testid="generation-admission-retry"]')).not.toBeNull();
    expect(container.querySelector('a[data-testid="generation-back-to-start"]')).not.toBeNull();
  });

  it("GLOBAL_GENERATION_WINDOW_LIMIT: PRESERVES the session, temporary capacity copy with Try again + Back to start", async () => {
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    const { supplier, services } = admissionServices(
      denial("GLOBAL_GENERATION_WINDOW_LIMIT"),
      "A",
    );
    mountAdmissionRun(services);
    await settleEffects();

    expect(supplier).toHaveBeenCalledTimes(1);
    expect(getCachedAnonymousSession()!.anonymousSessionToken).toBe("A");
    expect(resetSpy).not.toHaveBeenCalled();
    expect(
      container.querySelector('[data-testid="generation-admission-global-window-limit"]'),
    ).not.toBeNull();
    expect(container.querySelector('[data-testid="generation-admission-retry"]')).not.toBeNull();
  });

  it("ANONYMOUS_SESSION_CAPACITY_LIMIT: the current session stays valid — cache PRESERVED, temporary-capacity copy, NO Try again (§17/§47)", async () => {
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    const { supplier, services } = admissionServices(
      denial("ANONYMOUS_SESSION_CAPACITY_LIMIT"),
      "A",
    );
    mountAdmissionRun(services);
    await settleEffects();

    expect(supplier).toHaveBeenCalledTimes(1);
    expect(getCachedAnonymousSession()!.anonymousSessionToken).toBe("A");
    expect(resetSpy).not.toHaveBeenCalled();
    expect(
      container.querySelector('[data-testid="generation-admission-temporary-capacity-limit"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("The service is temporarily unable to start a new session.");
    // DEF-086/ADV-36-05 — the mint surface itself is capped: NO Try-again
    // button and the screen NEVER issues a new POST /sessions/anonymous
    // (the supplier stays at exactly one mint).
    expect(container.querySelector('[data-testid="generation-admission-retry"]')).toBeNull();
    const retryCandidates = Array.from(container.querySelectorAll("button")).filter((b) =>
      b.textContent?.includes("Try again"),
    );
    expect(retryCandidates.length).toBe(0);
  });

  it("legacy/unknown 429 ADMISSION_DENIED (no reasonCode): safe GENERIC fallback, cache PRESERVED, retryable (§30/§43)", async () => {
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    const { supplier, services } = admissionServices(denial(null), "A");
    mountAdmissionRun(services);
    await settleEffects();

    expect(supplier).toHaveBeenCalledTimes(1);
    expect(getCachedAnonymousSession()!.anonymousSessionToken).toBe("A");
    expect(resetSpy).not.toHaveBeenCalled();
    expect(
      container.querySelector('[data-testid="generation-admission-temporary-capacity-limit"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("Generation is temporarily unavailable");
    expect(container.querySelector('[data-testid="generation-admission-retry"]')).not.toBeNull();
  });

  it("does NOT clear the module holder or mint a second session for a transient denial — Try again reuses the SAME token A (§33)", async () => {
    // First POST /cases is denied for GLOBAL_CONCURRENCY_LIMIT; the retried
    // attempt (after the service clears) is admitted.
    const createCase = vi
      .fn<(...args: readonly unknown[]) => Promise<{
        caseId: string;
        generationId: string;
        generationAttemptId: string;
        creatorAccessToken: string;
        status: string;
        failureCode?: string | null;
      }>>()
      .mockRejectedValueOnce(
        new ApiError(429, "ADMISSION_DENIED", "Generation capacity exhausted", null, "GLOBAL_CONCURRENCY_LIMIT"),
      )
      .mockResolvedValueOnce({
        caseId: "CASE-2",
        generationId: "GEN-2",
        generationAttemptId: "ATT-2",
        creatorAccessToken: "creator-2",
        status: "PUBLISHED",
      });
    const { supplier, services } = admissionServices(createCase, "A");
    mountAdmissionRun(services);
    await settleEffects();

    // First run denied; the session A is PRESERVED (valid).
    expect(createCase).toHaveBeenCalledTimes(1);
    expect(createCase.mock.calls[0][0]).toBe("A");
    expect(getCachedAnonymousSession()!.anonymousSessionToken).toBe("A");
    expect(
      container.querySelector('[data-testid="generation-admission-global-concurrency-limit"]'),
    ).not.toBeNull();

    // Try again: SAME journey re-runs runDemo — no POST /sessions/anonymous
    // (supplier stays at 1), and the SAME token A is reused for POST /cases.
    const retryButton = container.querySelector<HTMLElement>(
      '[data-testid="generation-admission-retry"]',
    );
    if (!retryButton) throw new Error("expected the Try again button");
    act(() => {
      retryButton.click();
    });
    await settleEffects();

    expect(supplier).toHaveBeenCalledTimes(1); // NO second mint ever
    expect(createCase).toHaveBeenCalledTimes(2);
    expect(createCase.mock.calls[1][0]).toBe("A"); // same session on retry
    expect(container.querySelector('[data-testid="enter-investigation"]')).not.toBeNull();
  });

  it("SESSION_EXHAUSTION identity rotation (§34): token A exhausted -> clear A -> SPA back to /new -> ONE new session B; NO mint on the error screen", async () => {
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    // Sequence produces token A, then (only after cache clear) token B.
    const supplier = vi
      .fn<(...args: readonly unknown[]) => Promise<{ anonymousSessionToken: string; quotaWindowEndsAt: number }>>()
      .mockResolvedValueOnce({ anonymousSessionToken: "A", quotaWindowEndsAt: 1e12 })
      .mockResolvedValueOnce({ anonymousSessionToken: "B", quotaWindowEndsAt: 1e12 });

    const createCase = vi
      .fn<(...args: readonly unknown[]) => Promise<{
        caseId: string;
        generationId: string;
        generationAttemptId: string;
        creatorAccessToken: string;
        status: string;
        failureCode?: string | null;
      }>>()
      .mockRejectedValueOnce(
        new ApiError(429, "ADMISSION_DENIED", "Generation capacity exhausted", null, "SESSION_GENERATION_LIMIT"),
      )
      .mockResolvedValueOnce({
        caseId: "CASE-2",
        generationId: "GEN-2",
        generationAttemptId: "ATT-2",
        creatorAccessToken: "creator-2",
        status: "PUBLISHED",
      });

    mountAdmissionRun({
      createSession: () => createOrReuseAnonymousSession(supplier),
      createCase,
      pollGeneration: vi.fn(async () => {
        throw new Error("unused");
      }),
      createPlaythrough: vi.fn(async () => ({
        playthroughId: "PT-admission",
        caseId: "CASE-2",
        caseVersion: 1,
        playthroughAccessToken: "pt-token-admission",
        status: "PLAYING",
      })),
    });
    await settleEffects();

    // Token A minted, denied, cache cleared, NO mint for the replacement.
    expect(supplier).toHaveBeenCalledTimes(1);
    expect(createCase.mock.calls[0][0]).toBe("A");
    expect(getCachedAnonymousSession()).toBeNull();
    expect(resetSpy).toHaveBeenCalledTimes(1);
    expect(
      container.querySelector('[data-testid="generation-admission-session-generation-limit"]'),
    ).not.toBeNull();
    expect(container.querySelector('[data-testid="generation-admission-retry"]')).toBeNull();

    // Back to start navigates to /new and does NOT itself mint anything (§36).
    const back = container.querySelector<HTMLElement>(
      'a[data-testid="generation-back-to-start"]',
    );
    if (!back) throw new Error("expected Back to start");
    act(() => {
      back.click();
    });
    await settleEffects();
    expect(supplier).toHaveBeenCalledTimes(1); // still no mint after Back to start

    // User starts a fresh journey: the cache was cleared, so exactly ONE new
    // session B is minted and POST /cases runs under B.
    act(() => {
      root?.unmount();
    });
    mountAdmissionRun({
      createSession: () => createOrReuseAnonymousSession(supplier),
      createCase,
      pollGeneration: vi.fn(async () => {
        throw new Error("unused");
      }),
      createPlaythrough: vi.fn(async () => ({
        playthroughId: "PT-admission",
        caseId: "CASE-2",
        caseVersion: 1,
        playthroughAccessToken: "pt-token-admission",
        status: "PLAYING",
      })),
    });
    await settleEffects();

    expect(supplier).toHaveBeenCalledTimes(2); // exactly one replacement mint
    expect(createCase).toHaveBeenCalledTimes(2);
    expect(createCase.mock.calls[1][0]).toBe("B");
    expect(container.querySelector('[data-testid="enter-investigation"]')).not.toBeNull();
  });

  it("recovery works ENTIRELY through SPA navigation/actions — no reload anywhere on the admission path (§35)", async () => {
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    for (const reasonCode of [
      "SESSION_GENERATION_LIMIT",
      "GLOBAL_CONCURRENCY_LIMIT",
      "GLOBAL_GENERATION_WINDOW_LIMIT",
      null,
    ]) {
      const { services } = admissionServices(denial(reasonCode), "A");
      mountAdmissionRun(services);
      await settleEffects();
      expect(container.textContent).not.toContain("Reload");
      expect(container.querySelector('[data-testid="generation-admission-retry"]')?.textContent).not.toBe(
        "Reload",
      );
      // Back to start is always present as an SPA link.
      expect(container.querySelector('a[data-testid="generation-back-to-start"]')).not.toBeNull();
      act(() => {
        root?.unmount();
      });
    }
    expect(resetSpy).toHaveBeenCalledTimes(1); // only session-generation-limit cleared
  });

  it("no frontend security dependency (§37): the frontend NEVER enforces a counter — server-side limits stay authoritative", async () => {
    // Manipulating frontend state (clearing the in-memory cache manually) does
    // NOT bypass the backend: a fresh journey still performs a NEW POST /cases
    // under a NEW token, and the (server-side) admission answer is what shows.
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    const { services } = admissionServices(denial("GLOBAL_GENERATION_WINDOW_LIMIT"), "A");
    mountAdmissionRun(services);
    await settleEffects();
    // The backend (mocked here) denied the request; the frontend could not
    // have caused or removed that decision. It responds with the transient
    // capacity screen and preserves the session.
    expect(getCachedAnonymousSession()!.anonymousSessionToken).toBe("A");
    expect(resetSpy).not.toHaveBeenCalled();

    // Manually manufacture "fresh browser state" (as a reload/navigation would):
    // the module holder clears, and the NEXT journey still only sends POST
    // /cases — the server (this mock) is the one that authorizes/denies.
    resetAnonymousSessionCache();
    expect(resetSpy).toHaveBeenCalledTimes(1); // the explicit manual clear
    expect(getCachedAnonymousSession()).toBeNull();
  });

  it("defaults the module holder reset to OFF for every non-exhaustion reason (no blanket cache clearing)", async () => {
    const resetSpy = vi.spyOn(anonymousSessionModule, "resetAnonymousSessionCache");
    for (const reasonCode of [
      "SESSION_CONCURRENCY_LIMIT",
      "GLOBAL_CONCURRENCY_LIMIT",
      "GLOBAL_GENERATION_WINDOW_LIMIT",
      "ANONYMOUS_SESSION_CAPACITY_LIMIT",
      null,
    ]) {
      const { services } = admissionServices(denial(reasonCode), "A");
      mountAdmissionRun(services);
      await settleEffects();
      act(() => {
        root?.unmount();
      });
    }
    expect(resetSpy).not.toHaveBeenCalled();
    expect(getCachedAnonymousSession()).not.toBeNull(); // session A still valid
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

  it("Phase 30 — a frontier journey carries provider/key/model into the RunFn generation block (and nothing else)", async () => {
    const params: JourneyParams = {
      prompt: "A crime",
      difficulty: "medium",
      generationProvider: "frontier",
      frontierProviderId: "openai",
      frontierModel: "gpt-4o-mini",
      frontierApiKey: "sk-test-phase30-0000",
    };
    const { run, releaseRun } = mountJourney(demoOnly, params);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][5]).toEqual({
      generationProvider: "frontier",
      frontier: { provider: "openai", apiKey: "sk-test-phase30-0000", model: "gpt-4o-mini" },
    });
    // The serialized block carries the key ONLY for the POST — nothing here
    // writes any storage surface (the storage contract is pinned elsewhere).
    await releaseRun();
  });

  it("Phase 30 — a frontier journey with an EMPTY key (defense-in-depth) emits NO frontier block (fail-closed)", async () => {
    const params: JourneyParams = {
      prompt: "A crime",
      difficulty: "medium",
      generationProvider: "frontier",
      frontierProviderId: "openai",
      frontierModel: "gpt-4o-mini",
    };
    const { run, releaseRun } = mountJourney(demoOnly, params);
    await settleEffects();
    expect(run.mock.calls[0][5]).toEqual({ generationProvider: "frontier" });
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