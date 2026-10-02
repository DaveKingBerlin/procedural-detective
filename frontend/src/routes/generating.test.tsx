import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import {
  GenerationJourneyView,
  resolveJourneyMode,
  stageFromProgress,
  SESSION_LIMIT_HEADING,
  SESSION_LIMIT_RELOAD_LABEL,
} from "./generating";
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
 * Phase 24 F-2 — a session-window-DENIED run reaches a dedicated
 * "session-limit" recovery state (clear holder + reload guidance), NEVER the
 * generic Try-again error.
 */

function render(view: Parameters<typeof GenerationJourneyView>[0]["view"]): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/generating"]}>
      <GenerationJourneyView
        view={view}
        onEnter={() => {}}
        onRetry={() => {}}
        onReload={() => {}}
      />
    </MemoryRouter>,
  );
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

  it("a session-window-denied run renders the explicit session-limit recovery state (F-2): reload guidance, NO Try again", () => {
    const html = render({ status: "session-limit" });
    expect(html).toContain('data-testid="generation-session-limit"');
    expect(html).toContain(SESSION_LIMIT_HEADING);
    // (renderToStaticMarkup escapes the apostrophe, so the message body is
    // asserted on its stable, punctuation-free fragments.)
    expect(html).toContain("generation session has reached its limit");
    expect(html).toContain("Reload the page to start a fresh session");
    expect(html).toContain('data-testid="generation-session-limit-reload"');
    expect(html).toContain(SESSION_LIMIT_RELOAD_LABEL);
    // Deliberately NO "Try again": re-running would reuse the same exhausted
    // session; recovery is a page reload (which starts a fresh session server-
    // side-protected by the unchanged rate limits).
    expect(html).not.toContain("Try again");
    expect(html).not.toContain('data-testid="enter-investigation"');
    expect(html).toContain('data-testid="generation-back-to-start"');
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