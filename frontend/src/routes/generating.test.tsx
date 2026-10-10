import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import {
  GenerationJourneyView,
  resolveJourneyMode,
  stageFromProgress,
} from "./generating";
import { mapAdmissionReason, mapDemoError } from "../journey/demoFlow";
import { ApiError } from "../api/client";
import type { DemoProgress } from "../journey/demoFlow";
import type { GenerationCapabilitiesResponse } from "../api/types";

/**
 * Generation progress route states (Phase 8 C) rendered statically: no
 * session, running (staged label + progress bar), done (Enter investigation)
 * and the error states (Try again + Back to start). The actual state machine
 * transitions are covered by journey/demoFlow.test.ts; this suite pins the
 * rendered surfaces QA depends on.
 *
 * Phase 16.2 §21 — stageFromProgress additionally proves the MODE travels
 * inside the flow's progress snapshots (fed from `pd_generation_mode`) and
 * selects the Local-AI labels when local, the generic labels otherwise.
 *
 * Phase 36 — a 429 ADMISSION_DENIED run reaches a REASON-AWARE recovery
 * screen (session-generation-limit / session-concurrency-limit /
 * global-concurrency-limit / global-window-limit / temporary-capacity-limit)
 * instead of the old generic "session-limit + Reload page" screen.
 */

function render(view: Parameters<typeof GenerationJourneyView>[0]["view"]): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/generating"]}>
      <GenerationJourneyView
        view={view}
        onEnter={() => {}}
        onRetry={() => {}}
      />
    </MemoryRouter>,
  );
}

/** Render one reason-aware admission view as a string. */
function renderAdmission(reasonCode: string | null | undefined): string {
  const view = mapAdmissionReason(reasonCode);
  return render({ status: view.status, admission: view });
}

describe("generation route — no session (hard refresh)", () => {
  it("offers to start again without exposing any journey material", () => {
    const html = render({ status: "no-session" });
    expect(html).toContain('data-testid="generation-no-session"');
    expect(html).toContain('data-testid="generation-back-to-start"');
    expect(html).not.toContain("prompt");
  });
});

describe("generation route — running", () => {
  it("shows the staged friendly label and a progress bar", () => {
    const html = render({
      status: "running",
      stage: { outcome: "running", label: "Building world", progress: 40 },
    });
    expect(html).toContain('data-testid="generation-stage-label"');
    expect(html).toContain("Building world");
    expect(html).toContain('role="progressbar"');
    expect(html).toContain('aria-valuenow="40"');
    expect(html).toContain('data-testid="generation-progress"');
    expect(html).toContain("width:40%");
  });

  it("never renders an enter action while running", () => {
    const html = render({
      status: "running",
      stage: { outcome: "running", label: "Creating case", progress: 10 },
    });
    expect(html).not.toContain('data-testid="enter-investigation"');
  });
});

describe("generation route — published", () => {
  it("renders the Enter investigation action", () => {
    const html = render({ status: "done", result: { ok: true, playthroughToken: "t", playthroughId: "p", caseId: "c" } });
    expect(html).toContain('data-testid="enter-investigation"');
    expect(html).toContain("Enter investigation");
  });
});

describe("generation route — failure states", () => {
  for (const [kind, message] of [
    ["failed", "This prompt could not be turned into a solvable case."],
    ["retryable", "Generation is taking longer than expected."],
    // Phase 26C3 §12 — the internal bounded-generation safety-limit bucket
    // renders through the generic error state with a Retry action (the budget
    // resets per generation attempt, so "Try again" is a truthful affordance).
    ["safetyLimit", "This case could not be completed within the generation safety limits."],
  ] as const) {
    it(`renders a clear ${kind} message with Try again and Back to start`, () => {
      const html = render({ status: "error", kind, message });
      expect(html).toContain('data-testid="generation-failed"');
      expect(html).toContain(message);
      expect(html).toContain("Try again");
      expect(html).toContain('data-testid="generation-back-to-start"');
      expect(html).not.toContain('data-testid="enter-investigation"');
    });
  }

  it("session-generation-limit (SESSION_GENERATION_LIMIT) renders session-specific copy, NO Reload button, NO Try again, and Back to start", () => {
    const html = renderAdmission("SESSION_GENERATION_LIMIT");
    expect(html).toContain('data-testid="generation-admission-session-generation-limit"');
    expect(html).toContain("Generation limit reached");
    expect(html).toContain("investigation session has used its available generation attempts");
    expect(html).toContain("Start a new investigation to continue");
    // Phase 36 §22/§35 — NO reload affordance anywhere on the admission path.
    expect(html).not.toContain("Reload page");
    expect(html).not.toContain("window.location.reload");
    expect(html).not.toContain('data-testid="generation-admission-retry"');
    expect(html).toContain('data-testid="generation-back-to-start"');
    expect(html).not.toContain('data-testid="enter-investigation"');
  });

  it("SESSION_EXPIRED_OR_INVALID renders the same session-recovery status (fresh journey) with NO reload and NO auto-mint affordance", () => {
    const html = renderAdmission("SESSION_EXPIRED_OR_INVALID");
    expect(html).toContain('data-testid="generation-admission-session-generation-limit"');
    expect(html).toContain("Start a new investigation to continue");
    expect(html).not.toContain("Reload");
    expect(html).not.toContain('data-testid="generation-admission-retry"');
    expect(html).toContain('data-testid="generation-back-to-start"');
  });

  it("session-concurrency-limit (SESSION_CONCURRENCY_LIMIT) renders the already-running copy with a safe Try again and Back to start", () => {
    const html = renderAdmission("SESSION_CONCURRENCY_LIMIT");
    expect(html).toContain('data-testid="generation-admission-session-concurrency-limit"');
    expect(html).toContain("Generation already in progress");
    expect(html).toContain("Another case generation is already running for this session");
    // DEF-083/ADV-36-02 — no attempt handle exists to navigate back to, so the
    // rendered copy must not promise a return to the active generation.
    expect(html).not.toContain("Return to the in-progress generation");
    expect(html).toContain("Please wait for it to finish before starting another.");
    expect(html).toContain("Try again");
    expect(html).toContain('data-testid="generation-admission-retry"');
    expect(html).toContain('data-testid="generation-back-to-start"');
    expect(html).not.toContain("Reload");
  });

  it("global-concurrency-limit (GLOBAL_CONCURRENCY_LIMIT) renders the busy copy with Try again + Back to start", () => {
    const html = renderAdmission("GLOBAL_CONCURRENCY_LIMIT");
    expect(html).toContain('data-testid="generation-admission-global-concurrency-limit"');
    expect(html).toContain("Generation service is busy");
    expect(html).toContain("try again shortly");
    expect(html).toContain('data-testid="generation-admission-retry"');
    expect(html).toContain('data-testid="generation-back-to-start"');
    expect(html).not.toContain("Reload");
  });

  it("global-window-limit (GLOBAL_GENERATION_WINDOW_LIMIT) renders the temporary capacity copy with Try again + Back to start", () => {
    const html = renderAdmission("GLOBAL_GENERATION_WINDOW_LIMIT");
    expect(html).toContain('data-testid="generation-admission-global-window-limit"');
    expect(html).toContain("temporarily exhausted");
    expect(html).toContain("try again");
    expect(html).toContain('data-testid="generation-admission-retry"');
    expect(html).toContain('data-testid="generation-back-to-start"');
    expect(html).not.toContain("Reload");
  });

  it("temporary-capacity-limit (ANONYMOUS_SESSION_CAPACITY_LIMIT) renders the new-session capacity copy with Back to start and NO Try again (§17/§47)", () => {
    const html = renderAdmission("ANONYMOUS_SESSION_CAPACITY_LIMIT");
    expect(html).toContain('data-testid="generation-admission-temporary-capacity-limit"');
    expect(html).toContain("The service is temporarily unable to start a new session.");
    expect(html).toContain("Please try again later");
    // DEF-086/ADV-36-05 — the mint surface itself is capped: NO Try-again
    // button (a retry would re-invoke the exact mint the server just capped),
    // no reload encouragement — Back to start only.
    expect(html).not.toContain('data-testid="generation-admission-retry"');
    expect(html).not.toContain(">Try again</button>");
    expect(html).toContain('data-testid="generation-back-to-start"');
    expect(html).not.toContain("Reload");
  });

  it("DEF-082 — a 401 SESSION_EXPIRED (the REAL durable expired-session answer) renders the session-recovery screen: NO Try again, NO reload, Back to start only", () => {
    // The auth dependency rejects a genuinely expired/unknown anonymous session
    // with 401 SESSION_EXPIRED BEFORE any admission layer — mapDemoError maps
    // it to the SAME session-recovery view as SESSION_EXPIRED_OR_INVALID.
    const failure = mapDemoError(
      new ApiError(401, "SESSION_EXPIRED", "credential expired or unknown", null),
    );
    expect(failure.kind).toBe("quota");
    const view = failure.admissionReason!;
    const html = render({ status: view.status, admission: view });
    expect(html).toContain('data-testid="generation-admission-session-generation-limit"');
    expect(html).toContain("Generation session unavailable");
    expect(html).toContain("Start a new investigation to continue");
    // No dead-token retry loop: the dead token gets NO Try-again affordance.
    expect(html).not.toContain('data-testid="generation-admission-retry"');
    expect(html).not.toContain(">Try again</button>");
    expect(html).not.toContain("Reload");
    expect(html).toContain('data-testid="generation-back-to-start"');
  });

  it("unknown/absent reasonCode renders the safe GENERIC temporary-capacity fallback (§30) — never a session-limit claim", () => {
    const html = renderAdmission(null);
    expect(html).toContain('data-testid="generation-admission-temporary-capacity-limit"');
    expect(html).toContain("Generation is temporarily unavailable");
    expect(html).toContain("Please try again");
    expect(html).toContain('data-testid="generation-admission-retry"');
    expect(html).toContain('data-testid="generation-back-to-start"');
    // Never describes the session as exhausted.
    expect(html).not.toContain("has reached its limit");
    expect(html).not.toContain("Reload the page");
    expect(html).not.toContain("Reload");
  });

  it("every admission recovery screen is accessible: heading, role=status, descriptive labels, keyboard-visible actions", () => {
    const html = renderAdmission("GLOBAL_CONCURRENCY_LIMIT");
    expect(html).toContain("<h2>"); // heading hierarchy
    expect(html).toContain('role="status"'); // status announced via role
    expect(html).toContain(">Try again</button>"); // real button (focusable)
    expect(html).toContain("Back to start"); // descriptive link
    expect(html).not.toContain('<div role="alert"'); // not an over-eager alert
  });

  it("never exposes prompts, diagnostics or provider details in any state", () => {
    const running = render({
      status: "running",
      stage: { outcome: "running", label: "Checking consistency", progress: 80 },
    });
    expect(running).not.toContain("provider");
    expect(running).not.toContain("diagnostics");
  });
});

describe("stageFromProgress — Phase 16.2 §21 mode-aware label wiring", () => {
  const progress = (overrides: Partial<DemoProgress>): DemoProgress => ({
    phase: "polling",
    status: "RUNNING",
    stage: "world_generation",
    progress: 30,
    attempt: 1,
    mode: null,
    ...overrides,
  });

  it("uses the Local-AI label sequence when the snapshot travels with mode local", () => {
    expect(stageFromProgress(progress({ mode: "local" })).label).toBe(
      "Building the crime scene…",
    );
    expect(stageFromProgress(progress({ mode: "local", progress: 10 })).outcome).toBe("running");
    expect(stageFromProgress(progress({ phase: "session", mode: "local" })).label).toBe(
      "Understanding the case…",
    );
  });

  it("keeps the demo labels when the snapshot travels with mode demo or unset", () => {
    expect(stageFromProgress(progress({ mode: "demo" })).label).toBe("Building world");
    expect(stageFromProgress(progress({ mode: null })).label).toBe("Building world");
  });

  it("settles local mode on the final Local-AI label only from a server PUBLISHED", () => {
    const info = stageFromProgress(progress({ mode: "local", status: "PUBLISHED", progress: 100 }));
    expect(info.outcome).toBe("published");
    expect(info.label).toBe("Preparing the investigation…");
    expect(info.progress).toBe(100);
  });

  it("never renders a local label from demo/unset mode (local labels stay local-only)", () => {
    const html = render({
      status: "running",
      stage: { outcome: "running", label: "Building world", progress: 40 },
    });
    expect(html).not.toContain("crime scene");
    expect(html).not.toContain("Understanding the case");
  });
});

describe("resolveJourneyMode — ADV-212 the journey mode is validated against LIVE capabilities", () => {
  const demoOnly: () => Promise<GenerationCapabilitiesResponse> = async () => ({
    modes: [{ id: "demo", available: true }],
  });
  const localReady: () => Promise<GenerationCapabilitiesResponse> = async () => ({
    modes: [
      { id: "demo", available: true },
      { id: "local", available: true, label: "Local AI", model: "llama3.2:3b" },
    ],
  });
  const failing: () => Promise<GenerationCapabilitiesResponse> = async () => {
    throw new Error("probe unreachable");
  };

  it("stale localStorage local + demo-only capabilities -> generic (null)", async () => {
    expect(await resolveJourneyMode(demoOnly, "local")).toBeNull();
  });

  it("local + capabilities confirm local ready -> local labels", async () => {
    expect(await resolveJourneyMode(localReady, "local")).toBe("local");
  });

  it("capabilities fetch failure -> generic (never a local pipeline claim)", async () => {
    expect(await resolveJourneyMode(failing, "local")).toBeNull();
  });

  it("well-formed flow unchanged: demo stays demo, live stays live, unset stays null", async () => {
    expect(await resolveJourneyMode(demoOnly, "demo")).toBe("demo");
    expect(await resolveJourneyMode(localReady, "live")).toBe("live");
    expect(await resolveJourneyMode(localReady, null)).toBeNull();
  });
});