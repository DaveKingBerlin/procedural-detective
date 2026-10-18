// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Phase 48 — the journey is ONLY ever staged by an explicit form submit. Mocking
// `setJourneyParams` proves the random-crime helper never stages/runs a journey.
vi.mock("../journey/context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../journey/context")>();
  return { ...actual, setJourneyParams: vi.fn() };
});

import { setJourneyParams } from "../journey/context";
import {
  formatRandomCrimePrompt,
  generateRandomCrime,
  makeRandomCrimeSeed,
} from "../journey/randomCrimeGenerator";
import NewCasePage from "./new";

declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Phase 48 — REAL interaction tests for the "Generate random case" prompt
 * helper (jsdom), modeled on new.examples.test.tsx:
 *
 *  - an empty textarea is filled with a 7-line structured prompt;
 *  - differing user text is NEVER silently overwritten: an inline confirmation
 *    appears and Replace / Keep behave exactly as specified;
 *  - the action NEVER auto-starts generation (no submit, no setJourneyParams,
 *    no navigation, no fetch) — the explicit Generate click stays the only path
 *    that stages a journey;
 *  - the CURRENT difficulty is read and used.
 *
 * `Math.random` is pinned so the seed (and therefore the generated text) is
 * deterministic; only the UI seed-maker ever calls it.
 */

const FIXED_RANDOM = 0.5;

function expectedPromptFor(difficulty: "easy" | "medium" | "hard"): string {
  const seed = makeRandomCrimeSeed(() => FIXED_RANDOM);
  return formatRandomCrimePrompt(generateRandomCrime(seed, difficulty));
}

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
        <Routes>
          <Route path="/new" element={<NewCasePage capabilities={{ modes: [] }} />} />
          <Route
            path="/generating"
            element={<div data-testid="generating-route">Generating…</div>}
          />
        </Routes>
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

function queryOptional<T extends Element>(container: HTMLElement, selector: string): T | null {
  return container.querySelector(selector) as T | null;
}

function promptInput(container: HTMLElement): HTMLTextAreaElement {
  return queryRequired<HTMLTextAreaElement>(container, '[data-testid="prompt-input"]');
}

function clickTestId(container: HTMLElement, testid: string): void {
  const button = queryRequired<HTMLButtonElement>(container, `[data-testid="${testid}"]`);
  act(() => {
    button.click();
  });
}

/** Simulate a real user keystroke/type (RTL-style native input event). */
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

/** Change the difficulty select the way a real user interaction does. */
function selectDifficulty(container: HTMLElement, value: string): void {
  const select = queryRequired<HTMLSelectElement>(container, '[data-testid="difficulty-select"]');
  const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  const setter = descriptor?.set;
  if (!descriptor || typeof setter !== "function") {
    throw new Error("HTMLSelectElement.prototype.value setter is missing");
  }
  act(() => {
    Object.defineProperty(select, "value", { configurable: true, ...descriptor });
    setter.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function lineCount(text: string): number {
  return text.split("\n").length;
}

describe("Phase 48 — Generate random case (real interaction)", () => {
  let mounted: Mounted | null = null;
  let submitSpy = vi.fn();

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("no backend in test");
      }),
    );
    vi.spyOn(Math, "random").mockReturnValue(FIXED_RANDOM);
    submitSpy = vi.fn();
    mounted = mountNewCase();
  });

  afterEach(() => {
    const root = mounted?.root;
    mounted = null;
    if (root) act(() => root.unmount());
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const container = (): HTMLElement => {
    if (!mounted) throw new Error("test mounted page is gone");
    return mounted.container;
  };

  const attachSubmitSpy = (): void => {
    const form = queryRequired<HTMLFormElement>(container(), '[data-testid="prompt-form"]');
    form.addEventListener("submit", submitSpy);
  };

  it("exposes the button as a type=button action with the words as its accessible name", async () => {
    await settleEffects();
    const button = queryRequired<HTMLButtonElement>(
      container(),
      '[data-testid="generate-random-case"]',
    );
    expect(button.getAttribute("type")).toBe("button");
    expect(button.textContent).toContain("Generate random case");
    expect(button.getAttribute("aria-label")).toBe("Generate random case");
  });

  it("13 — empty textarea: fills a 7-line structured prompt and NEVER auto-starts generation", async () => {
    await settleEffects();
    attachSubmitSpy();
    expect(promptInput(container()).value).toBe("");

    clickTestId(container(), "generate-random-case");

    const value = promptInput(container()).value;
    expect(lineCount(value)).toBe(7);
    expect(value).toBe(expectedPromptFor("medium"));
    expect(value.split("\n")[0]).toMatch(
      /^Victim: .+ \((Male|Female|Other|Unknown), \d{1,3}, .+\)$/,
    );
    // No confirmation was needed for an empty textarea.
    expect(queryOptional(container(), '[data-testid="random-confirm"]')).toBeNull();
    // The action never starts generation: no submit, no stage, no navigation.
    expect(submitSpy).not.toHaveBeenCalled();
    expect(setJourneyParams).not.toHaveBeenCalled();
    expect(queryOptional(container(), '[data-testid="generating-route"]')).toBeNull();
    expect(queryOptional(container(), '[data-testid="prompt-form"]')).not.toBeNull();
    expect(container().textContent).toContain("New Investigation");
  });

  it("15 — a random click does not invoke the generation path (no submit / stage / navigation)", async () => {
    await settleEffects();
    attachSubmitSpy();
    clickTestId(container(), "generate-random-case");
    clickTestId(container(), "generate-random-case");
    expect(submitSpy).not.toHaveBeenCalled();
    expect(setJourneyParams).not.toHaveBeenCalled();
    expect(queryOptional(container(), '[data-testid="generating-route"]')).toBeNull();
    expect(queryOptional(container(), '[data-testid="prompt-form"]')).not.toBeNull();
  });

  it("14a — populated textarea: shows the inline confirmation and leaves the text UNCHANGED", async () => {
    await settleEffects();
    attachSubmitSpy();
    const userText = "A quiet murder in the archive room.";
    typeInto(container(), userText);

    clickTestId(container(), "generate-random-case");

    // Inline confirmation (not a modal / window.confirm) with both controls.
    const confirm = queryRequired(container(), '[data-testid="random-confirm"]');
    expect(confirm).not.toBeNull();
    expect(confirm.textContent).toContain("Replace it with the generated case?");
    expect(queryOptional(container(), '[data-testid="random-replace"]')).not.toBeNull();
    expect(queryOptional(container(), '[data-testid="random-cancel"]')).not.toBeNull();
    // The user's text is untouched until they choose Replace.
    expect(promptInput(container()).value).toBe(userText);
    expect(setJourneyParams).not.toHaveBeenCalled();
  });

  it("14b — 'Replace description' applies exactly the pending generated text", async () => {
    await settleEffects();
    typeInto(container(), "Some user text that differs.");
    clickTestId(container(), "generate-random-case");
    clickTestId(container(), "random-replace");

    const value = promptInput(container()).value;
    expect(value).toBe(expectedPromptFor("medium"));
    expect(lineCount(value)).toBe(7);
    // Confirmation dismissed after applying.
    expect(queryOptional(container(), '[data-testid="random-confirm"]')).toBeNull();
    expect(setJourneyParams).not.toHaveBeenCalled();
  });

  it("14c — 'Keep current description' dismisses and leaves the text untouched", async () => {
    await settleEffects();
    const userText = "Leave my description exactly as it is.";
    typeInto(container(), userText);
    clickTestId(container(), "generate-random-case");
    expect(promptInput(container()).value).toBe(userText);

    clickTestId(container(), "random-cancel");

    expect(promptInput(container()).value).toBe(userText);
    expect(queryOptional(container(), '[data-testid="random-confirm"]')).toBeNull();
    expect(setJourneyParams).not.toHaveBeenCalled();
  });

  it("uses the CURRENT difficulty (Hard) when generating", async () => {
    await settleEffects();
    selectDifficulty(container(), "hard");
    clickTestId(container(), "generate-random-case");
    expect(promptInput(container()).value).toBe(expectedPromptFor("hard"));
  });

  it("a second click after a random fill re-fills without a confirmation (text already equals generated)", async () => {
    await settleEffects();
    // Empty -> fill.
    clickTestId(container(), "generate-random-case");
    const first = promptInput(container()).value;
    // Same fixed Math.random -> same seed -> identical generated text; the
    // "already equals the just-generated text" branch must fill, not confirm.
    clickTestId(container(), "generate-random-case");
    expect(promptInput(container()).value).toBe(first);
    expect(queryOptional(container(), '[data-testid="random-confirm"]')).toBeNull();
  });
});
