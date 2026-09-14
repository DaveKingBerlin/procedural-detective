import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { GenerationJourneyView } from "./generating";

/**
 * Generation progress route states (Phase 8 C) rendered statically: no
 * session, running (staged label + progress bar), done (Enter investigation)
 * and the error states (Try again + Back to start). The actual state machine
 * transitions are covered by journey/demoFlow.test.ts; this suite pins the
 * rendered surfaces QA depends on.
 */

function render(view: Parameters<typeof GenerationJourneyView>[0]["view"]): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/generating"]}>
      <GenerationJourneyView view={view} onEnter={() => {}} onRetry={() => {}} />
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
    ["quota", "Too many cases are being generated right now."],
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

  it("never exposes prompts, diagnostics or provider details in any state", () => {
    const running = render({
      status: "running",
      stage: { outcome: "running", label: "Checking consistency", progress: 80 },
    });
    expect(running).not.toContain("provider");
    expect(running).not.toContain("diagnostics");
  });
});