// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Phase 17D Bugfix PART B — the journey is ONLY ever staged by an explicit
// form submit (the "Generate case" button). Mocking `setJourneyParams` lets
// D4 prove that clicking an example button never stages/runs a journey.
vi.mock("../journey/context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../journey/context")>();
  return { ...actual, setJourneyParams: vi.fn() };
});

import { setJourneyParams } from "../journey/context";
import NewCasePage from "./new";

// React's test utilities flag the act(...) environment; without this every
// act() call would also print "not configured to support act(...)" noise.
declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Phase 17D Bugfix PART B — REAL interaction tests for the "Try an example:"
 * selector (jsdom). These prove, by driving the actual rendered component,
 * parts D1–D7 of the task:
 *
 *  - D1/D2/D3: each button populates the exact prompt (byte-exact);
 *  - D4: selecting an example NEVER auto-submits (no form submit event, no
 *    setJourneyParams call) — the explicit Generate click remains the only
 *    path that stages the journey (calibration test);
 *  - D5: the prompt stays fully editable after an example fill;
 *  - D6: changing examples replaces the prompt deterministically (and
 *    re-selecting the same example is idempotent);
 *  - D7: no proc.* / AssetSpec / Phase 13/17 / solver tokens anywhere in the
 *    rendered prompt UI; the active state is visible per selection and is
 *    cleared deterministically as soon as the user edits the text.
 */

const EASY_EXPECTED = [
  "Victim: Laura Stein",
  "Murderer: Daniel Roth",
  "Motive: financial gain",
  "Weapon: kitchen knife",
  "Time: 20:15",
  "Witness: Nina Weber",
  "Location: apartment",
].join("\n");

const MEDIUM_EXPECTED = [
  "Victim: Michael Hartmann",
  "Murderer: Elena Fischer",
  "Motive: blackmail over a hidden affair",
  "Weapon: antique brass letter opener",
  "Time: 21:18",
  "Witness: Daniel Weber",
  "Location: hotel suite",
].join("\n");

const HARD_EXPECTED = [
  "Victim: Dr. Anna Weiss",
  "Murderer: Paul Becker",
  "Motive: stolen research data",
  "Weapon: bronze ceremonial ice pick",
  "Time: 23:42",
  "Witness: Lisa König",
  "Location: office",
].join("\n");

const EXAMPLE_IDS = ["easy", "medium", "hard"] as const;

interface Mounted {
  container: HTMLDivElement;
  root: ReturnType<typeof createRoot>;
}

function mountNewCase(): Mounted {
  const container = document.createElement("div");
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={["/new"]}>
        {/* Demo-only capabilities: deterministic, no network claims. */}
        <NewCasePage capabilities={{ modes: [] }} />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

/** Let the async capabilities probe settle inside an act scope. */
async function settleEffects(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

function queryRequired<T extends Element>(container: HTMLElement, selector: string): T {
  const el = container.querySelector(selector);
  if (!el) throw new Error(`expected element not found: ${selector}`);
  return el as T;
}

function promptInput(container: HTMLElement): HTMLTextAreaElement {
  return queryRequired<HTMLTextAreaElement>(container, '[data-testid="prompt-input"]');
}

function exampleButton(container: HTMLElement, id: (typeof EXAMPLE_IDS)[number]): HTMLButtonElement {
  return queryRequired<HTMLButtonElement>(container, `[data-testid="example-${id}"]`);
}

function isPressed(container: HTMLElement, id: (typeof EXAMPLE_IDS)[number]): boolean {
  return exampleButton(container, id).getAttribute("aria-pressed") === "true";
}

function clickExample(container: HTMLElement, id: (typeof EXAMPLE_IDS)[number]): void {
  const button = exampleButton(container, id);
  act(() => {
    button.click();
  });
}

/**
 * Simulate a real user keystroke/type: bypass React's controlled-value tracker
 * (RTL-style) and fire the native `input` event so the onChange handler runs.
 */
function typeInto(container: HTMLElement, value: string): void {
  const ta = promptInput(container);
  const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
  const setter = descriptor?.set;
  if (!descriptor || typeof setter !== "function") {
    throw new Error("HTMLTextAreaElement.prototype.value setter is missing");
  }
  act(() => {
    Object.defineProperty(ta, "value", { configurable: true, ...descriptor });
    setter.call(ta, value);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("Phase 17D Bugfix PART B — example prompts (real interaction, D1–D7)", () => {
  let mounted: Mounted | null = null;
  let submitSpy = vi.fn();

  beforeEach(() => {
    // Hermetic: the capabilities probe must never touch the real network.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("no backend in test");
      }),
    );
    submitSpy = vi.fn();
    mounted = mountNewCase();
  });

  afterEach(() => {
    const root = mounted?.root;
    mounted = null;
    if (root) act(() => root.unmount());
    vi.unstubAllGlobals();
  });

  const container = (): HTMLElement => {
    if (!mounted) throw new Error("test mounted page is gone");
    return mounted.container;
  };

  /** Attach a submit spy to the prompt form (fires only on real submits). */
  const attachSubmitSpy = (): void => {
    const form = queryRequired<HTMLFormElement>(container(), '[data-testid="prompt-form"]');
    form.addEventListener("submit", submitSpy);
  };

  it("K1 — the Easy card populates the EXACT Easy prompt and marks it active", async () => {
    await settleEffects();
    attachSubmitSpy();
    clickExample(container(), "easy");
    expect(promptInput(container()).value).toBe(EASY_EXPECTED);
    expect(promptInput(container()).value).toHaveLength(EASY_EXPECTED.length);
    expect(isPressed(container(), "easy")).toBe(true);
    expect(isPressed(container(), "medium")).toBe(false);
    expect(isPressed(container(), "hard")).toBe(false);
    // Clicking only ever fills the textarea — nothing is submitted or staged.
    expect(submitSpy).not.toHaveBeenCalled();
    expect(setJourneyParams).not.toHaveBeenCalled();
  });

  it("K2 — the Medium card populates the EXACT Medium prompt and marks it active", async () => {
    await settleEffects();
    clickExample(container(), "medium");
    expect(promptInput(container()).value).toBe(MEDIUM_EXPECTED);
    expect(promptInput(container()).value).toHaveLength(MEDIUM_EXPECTED.length);
    expect(isPressed(container(), "easy")).toBe(false);
    expect(isPressed(container(), "medium")).toBe(true);
    expect(isPressed(container(), "hard")).toBe(false);
    expect(setJourneyParams).not.toHaveBeenCalled();
  });

  it("K3 — the Hard card populates the EXACT Hard prompt and marks it active", async () => {
    await settleEffects();
    clickExample(container(), "hard");
    expect(promptInput(container()).value).toBe(HARD_EXPECTED);
    expect(promptInput(container()).value).toHaveLength(HARD_EXPECTED.length);
    expect(isPressed(container(), "easy")).toBe(false);
    expect(isPressed(container(), "medium")).toBe(false);
    expect(isPressed(container(), "hard")).toBe(true);
    expect(setJourneyParams).not.toHaveBeenCalled();
  });

  it("K4 — selecting a card NEVER auto-submits (no submit event, no journey stage, no navigation)", async () => {
    await settleEffects();
    attachSubmitSpy();
    for (const id of EXAMPLE_IDS) clickExample(container(), id);
    // No form submit event fired for any example click…
    expect(submitSpy).not.toHaveBeenCalled();
    // …no journey was staged…
    expect(setJourneyParams).not.toHaveBeenCalled();
    // …and the user is still on /new (the form is still rendered).
    expect(queryRequired(container(), '[data-testid="prompt-form"]')).not.toBeNull();
    expect(container().textContent).toContain("New Investigation");
  });

  it("D4-calibration — a real form submit (what the explicit Generate click produces) DOES stage the journey", async () => {
    await settleEffects();
    attachSubmitSpy();
    clickExample(container(), "easy");
    // jsdom does not implement the browser's implicit submit-on-generate-click
    // default action, so fire the form's submit event directly — in a real
    // browser this is exactly what clicking the type="submit" Generate button
    // produces (and React's onSubmit handles it identically).
    const form = queryRequired<HTMLFormElement>(container(), '[data-testid="prompt-form"]');
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    // Explicit user submit -> the journey is staged with the exact prompt.
    expect(submitSpy).toHaveBeenCalledTimes(1);
    expect(setJourneyParams).toHaveBeenCalledTimes(1);
    expect(setJourneyParams).toHaveBeenCalledWith({
      prompt: EASY_EXPECTED,
      difficulty: "medium",
    });
  });

  it("K5 — the prompt remains fully editable after a card fill", async () => {
    await settleEffects();
    clickExample(container(), "easy");
    const ta = promptInput(container());
    expect(ta.disabled).toBe(false);
    expect(ta.readOnly).toBe(false);
    // A normal user keystroke after the fill must be accepted and kept.
    const edited = EASY_EXPECTED + "\nClue: diary under the couch";
    typeInto(container(), edited);
    expect(promptInput(container()).value).toBe(edited);
    expect(promptInput(container()).value).not.toBe(EASY_EXPECTED);
  });

  it("D6 — changing examples replaces the prompt deterministically and moves the active state", async () => {
    await settleEffects();
    clickExample(container(), "easy");
    expect(promptInput(container()).value).toBe(EASY_EXPECTED);

    clickExample(container(), "hard");
    expect(promptInput(container()).value).toBe(HARD_EXPECTED);
    expect(isPressed(container(), "easy")).toBe(false);
    expect(isPressed(container(), "hard")).toBe(true);

    clickExample(container(), "medium");
    expect(promptInput(container()).value).toBe(MEDIUM_EXPECTED);
    expect(isPressed(container(), "hard")).toBe(false);
    expect(isPressed(container(), "medium")).toBe(true);
  });

  it("D6-idempotent — clicking the SAME example again keeps it (no-op, still byte-exact)", async () => {
    await settleEffects();
    clickExample(container(), "hard");
    clickExample(container(), "hard");
    expect(promptInput(container()).value).toBe(HARD_EXPECTED);
    expect(isPressed(container(), "hard")).toBe(true);
    expect(promptInput(container()).value).toHaveLength(HARD_EXPECTED.length);
  });

  it("D7 — no proc.* / AssetSpec / Phase 13/17 / solver tokens in the rendered prompt UI", async () => {
    await settleEffects();
    for (const id of EXAMPLE_IDS) clickExample(container(), id);
    const visible = container().textContent;
    expect(visible).not.toContain("proc.");
    const lowered = visible.toLowerCase();
    expect(lowered).not.toContain("assetspec");
    expect(lowered).not.toContain("asset spec");
    expect(lowered).not.toContain("phase 13");
    expect(lowered).not.toContain("phase 17");
    expect(lowered).not.toContain("solver");
    expect(lowered).not.toContain("4551660f4a46b2eb");
  });

  it("D7-active — the active state is per-selection and CLEARED when the user edits", async () => {
    await settleEffects();
    // Nothing active up front.
    expect(isPressed(container(), "easy")).toBe(false);
    expect(isPressed(container(), "medium")).toBe(false);
    expect(isPressed(container(), "hard")).toBe(false);

    // Selecting marks exactly one button active.
    clickExample(container(), "easy");
    expect(isPressed(container(), "easy")).toBe(true);
    expect(isPressed(container(), "medium")).toBe(false);
    expect(isPressed(container(), "hard")).toBe(false);

    // A single user edit diverges from the example -> active clears (and the
    // example buttons are back to the un-pressed state).
    typeInto(container(), EASY_EXPECTED + "x");
    expect(isPressed(container(), "easy")).toBe(false);
    expect(isPressed(container(), "medium")).toBe(false);
    expect(isPressed(container(), "hard")).toBe(false);
    expect(promptInput(container()).value).toBe(EASY_EXPECTED + "x");
  });
});