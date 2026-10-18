// @vitest-environment jsdom
/**
 * PHASE 48 QA RETEST — independent UI probe (QA-owned; NOT the developer's
 * test). Mounts the REAL NewCasePage and independently re-verifies the
 * ADV-48-02/04/05/07 frontend fixes:
 *
 *  - the inline replace-confirmation is DISMISSED by a later edit / example
 *    click / difficulty change, and the user's newer text is intact;
 *  - `random-confirm` exposes aria-live="polite" + its accessible name;
 *  - core confirm semantics (Replace applies pending, Keep keeps text);
 *  - submitting while the confirmation is open stages the CURRENT text, never
 *    the stale pending generated text.
 *
 * `setJourneyParams` is mocked so the staged journey can be inspected without a
 * backend/route.
 *
 * Run from frontend/ with the QA vitest config:
 *   npx vitest run --config ../e2e/probes/qa-p48-retest.vitest.config.mjs
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const journeySpy = vi.hoisted(() => vi.fn());

vi.mock("../../frontend/src/journey/context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../frontend/src/journey/context")>();
  return { ...actual, setJourneyParams: journeySpy };
});

import NewCasePage from "../../frontend/src/routes/new";
import { EXAMPLE_PROMPTS } from "../../frontend/src/journey/examplePrompts";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

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
          <Route path="/generating" element={<div data-testid="generating-route">Generating…</div>} />
        </Routes>
      </MemoryRouter>,
    );
  });
  return { container, root };
}

async function settleEffects(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

function q<T extends Element>(container: HTMLElement, selector: string): T | null {
  return container.querySelector(selector) as T | null;
}
function req<T extends Element>(container: HTMLElement, selector: string): T {
  const el = q<T>(container, selector);
  if (!el) throw new Error(`missing ${selector}`);
  return el;
}
function ta(container: HTMLElement): HTMLTextAreaElement {
  return req<HTMLTextAreaElement>(container, '[data-testid="prompt-input"]');
}
function click(container: HTMLElement, testid: string): void {
  act(() => req<HTMLButtonElement>(container, `[data-testid="${testid}"]`).click());
}
function typeInto(container: HTMLElement, value: string): void {
  const el = ta(container);
  const d = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!;
  act(() => {
    d.set!.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function selectDifficulty(container: HTMLElement, value: string): void {
  const sel = req<HTMLSelectElement>(container, '[data-testid="difficulty-select"]');
  const d = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!;
  act(() => {
    d.set!.call(sel, value);
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
function confirmPresent(container: HTMLElement): boolean {
  return q(container, '[data-testid="random-confirm"]') !== null;
}
function log(tag: string, payload: Record<string, unknown>): void {
  // eslint-disable-next-line no-console
  console.log(`[QA-P48-RETEST] ${tag} ${JSON.stringify(payload)}`);
}

describe("PHASE 48 QA retest — inline confirmation dismissal + aria", () => {
  let mounted: Mounted | null = null;

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("no backend in probe");
      }),
    );
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    journeySpy.mockClear();
    mounted = mountNewCase();
  });
  afterEach(() => {
    const root = mounted?.root;
    mounted = null;
    if (root) act(() => root.unmount());
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });
  const c = (): HTMLElement => {
    if (!mounted) throw new Error("probe page gone");
    return mounted.container;
  };

  it("R1 — the confirmation exposes aria-live=polite and its accessible name", async () => {
    await settleEffects();
    typeInto(c(), "Some differing user text.");
    click(c(), "generate-random-case");
    const confirm = req<HTMLElement>(c(), '[data-testid="random-confirm"]');
    const state = {
      ariaLive: confirm.getAttribute("aria-live"),
      role: confirm.getAttribute("role"),
      ariaLabel: confirm.getAttribute("aria-label"),
      text: confirm.textContent,
    };
    log("R1", state);
    expect(state.ariaLive).toBe("polite");
    expect(state.role).toBe("group");
    expect(state.ariaLabel).toBe("Replace the current description?");
    expect(state.text).toContain("Replace it with the generated case?");
  });

  it("R2 — editing the description dismisses the confirmation and keeps the new text", async () => {
    await settleEffects();
    typeInto(c(), "Original user description.");
    click(c(), "generate-random-case");
    expect(confirmPresent(c())).toBe(true);

    typeInto(c(), "A newer edited description.");
    const state = { confirmPresent: confirmPresent(c()), value: ta(c()).value };
    log("R2", state);
    expect(state.confirmPresent).toBe(false);
    expect(state.value).toBe("A newer edited description.");
  });

  it("R3 — clicking an example dismisses the confirmation and loads the example", async () => {
    await settleEffects();
    typeInto(c(), "Original user description.");
    click(c(), "generate-random-case");
    expect(confirmPresent(c())).toBe(true);

    click(c(), "example-easy");
    const state = {
      confirmPresent: confirmPresent(c()),
      matchesExample: ta(c()).value === EXAMPLE_PROMPTS.easy.prompt,
    };
    log("R3", state);
    expect(state.confirmPresent).toBe(false);
    expect(state.matchesExample).toBe(true);
  });

  it("R4 — changing the difficulty dismisses the confirmation and keeps the user text", async () => {
    await settleEffects();
    const userText = "Original user description.";
    typeInto(c(), userText);
    click(c(), "generate-random-case");
    expect(confirmPresent(c())).toBe(true);

    selectDifficulty(c(), "hard");
    const state = {
      confirmPresent: confirmPresent(c()),
      value: ta(c()).value,
      difficulty: req<HTMLSelectElement>(c(), '[data-testid="difficulty-select"]').value,
    };
    log("R4", state);
    expect(state.confirmPresent).toBe(false);
    expect(state.value).toBe(userText);
    expect(state.difficulty).toBe("hard");
  });

  it("R5 — control: Replace still applies the pending generated text (7 lines)", async () => {
    await settleEffects();
    typeInto(c(), "Some user text that differs.");
    click(c(), "generate-random-case");
    expect(confirmPresent(c())).toBe(true);
    click(c(), "random-replace");
    const value = ta(c()).value;
    const state = { confirmPresent: confirmPresent(c()), lines: value.split("\n").length, journeyCalls: journeySpy.mock.calls.length };
    log("R5", state);
    expect(state.confirmPresent).toBe(false);
    expect(state.lines).toBe(7);
    expect(journeySpy).not.toHaveBeenCalled();
  });

  it("R6 — control: Keep leaves the user's text untouched", async () => {
    await settleEffects();
    const userText = "Keep me exactly.";
    typeInto(c(), userText);
    click(c(), "generate-random-case");
    click(c(), "random-cancel");
    const state = { confirmPresent: confirmPresent(c()), value: ta(c()).value };
    log("R6", state);
    expect(state.confirmPresent).toBe(false);
    expect(state.value).toBe(userText);
  });

  it("R7 — adversarial: submitting while the confirmation is open stages the CURRENT text, not the pending generated text", async () => {
    await settleEffects();
    const userText = "Text staged on submit.";
    typeInto(c(), userText);
    click(c(), "generate-random-case");
    expect(confirmPresent(c())).toBe(true);

    const form = req<HTMLFormElement>(c(), '[data-testid="prompt-form"]');
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    const staged = journeySpy.mock.calls.length > 0 ? journeySpy.mock.calls[0][0] : null;
    const state = {
      journeyCalls: journeySpy.mock.calls.length,
      stagedPrompt: staged?.prompt ?? null,
      stagedIsUserText: staged?.prompt === userText,
    };
    log("R7", state);
    expect(state.journeyCalls).toBe(1);
    expect(state.stagedIsUserText).toBe(true);
  });

  it("R8 — observation (ADV-48-06): whitespace-only text is overwritten without confirmation", async () => {
    await settleEffects();
    typeInto(c(), "    \n\t  ");
    click(c(), "generate-random-case");
    const value = ta(c()).value;
    const state = {
      confirmPresent: confirmPresent(c()),
      overwrittenWithoutConfirm: confirmPresent(c()) === false && value.trim().length > 0,
      lines: value.split("\n").length,
    };
    log("R8", state);
    expect(value.trim().length).toBeGreaterThan(0);
  });
});
