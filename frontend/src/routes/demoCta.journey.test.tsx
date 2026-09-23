// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Phase 21B Finding 3 — the demo/example CTA ACTION is byte-identical in
// every capability state (the COPY changes, the action never does): the same
// sample prompt + difficulty reaches the same /generating route -> runDemo ->
// POST /cases with NO provider/mode field. This suite drives the REAL
// rendered Home and NewCasePage across demo-only / local / live / null
// capability states and asserts the staged journey params never change.
vi.mock("../journey/context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../journey/context")>();
  return { ...actual, setJourneyParams: vi.fn() };
});

import type { GenerationCapabilitiesResponse } from "../api/types";
import { setJourneyParams } from "../journey/context";
import { EXAMPLE_PROMPT } from "../journey/demoPrompt";
import { examplePromptText } from "../journey/promptValidation";
import Home from "./home";
import NewCasePage from "./new";

declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const OK_STATUS = { state: "ok" as const, message: "ok", readiness: null };

const DEMO_ONLY: GenerationCapabilitiesResponse = {
  modes: [{ id: "demo", available: true }],
};
const LOCAL_READY: GenerationCapabilitiesResponse = {
  modes: [
    { id: "demo", available: true },
    { id: "local", available: true, label: "Local AI", model: "llama3.2:3b" },
  ],
};
const LIVE_READY: GenerationCapabilitiesResponse = {
  modes: [
    { id: "demo", available: true },
    { id: "live", available: true, label: "Cloud AI" },
  ],
};

const ALL_CAPABILITY_STATES: Array<GenerationCapabilitiesResponse | null> = [
  DEMO_ONLY,
  LOCAL_READY,
  LIVE_READY,
  null,
];

interface Mounted {
  container: HTMLDivElement;
  root: ReturnType<typeof createRoot>;
  location: string | null;
}

function mount(node: React.ReactNode): Mounted {
  const container = document.createElement("div");
  const root = createRoot(container);
  const mounted: Mounted = { container, root, location: null };
  function LocationProbe() {
    const location = useLocation();
    mounted.location = location.pathname;
    return null;
  }
  act(() => {
    root.render(
      <MemoryRouter initialEntries={["/"]}>
        {node}
        <LocationProbe />
      </MemoryRouter>,
    );
  });
  return mounted;
}

function unmount(mounted: Mounted): void {
  act(() => {
    mounted.root.unmount();
  });
}

function click(element: Element): void {
  act(() => {
    (element as HTMLElement).click();
  });
}

/** Let the async capabilities probe settle inside an act scope (no dangling updates). */
async function settleEffects(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

describe("Phase 21B Finding 3 — the demo CTA action is byte-identical in every capability state", () => {
  beforeEach(() => {
    vi.mocked(setJourneyParams).mockClear();
    // Hermetic: the capabilities probe must never touch the real network
    // (the route is given explicit capability overrides below; the hook's
    // in-flight fetch — if any — must fail fast and safely).
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("no backend in test");
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("on the landing page the same EXAMPLE_PROMPT + medium difficulty is staged and the same /generating route is taken for every capability state", async () => {
    for (const capabilities of ALL_CAPABILITY_STATES) {
      const mounted = mount(<Home status={OK_STATUS} capabilities={capabilities} />);
      await settleEffects();
      const button = mounted.container.querySelector('[data-testid="try-demo"]');
      if (!button) throw new Error("try-demo button missing");
      click(button);
      await settleEffects();
      expect(vi.mocked(setJourneyParams)).toHaveBeenCalledTimes(1);
      expect(vi.mocked(setJourneyParams)).toHaveBeenCalledWith({
        prompt: EXAMPLE_PROMPT,
        difficulty: "medium",
      });
      expect(mounted.location).toBe("/generating");
      unmount(mounted);
      vi.mocked(setJourneyParams).mockClear();
    }
  });

  it("on /new the same examplePromptText + current difficulty is staged and the same /generating route is taken for every capability state", async () => {
    for (const capabilities of ALL_CAPABILITY_STATES) {
      const mounted = mount(<NewCasePage capabilities={capabilities} />);
      await settleEffects();
      const link = mounted.container.querySelector('[data-testid="try-demo-from-new"]');
      if (!link) throw new Error("try-demo-from-new link missing");
      click(link);
      await settleEffects();
      expect(vi.mocked(setJourneyParams)).toHaveBeenCalledTimes(1);
      expect(vi.mocked(setJourneyParams)).toHaveBeenCalledWith({
        prompt: examplePromptText(),
        difficulty: "medium",
      });
      expect(mounted.location).toBe("/generating");
      unmount(mounted);
      vi.mocked(setJourneyParams).mockClear();
    }
  });
});