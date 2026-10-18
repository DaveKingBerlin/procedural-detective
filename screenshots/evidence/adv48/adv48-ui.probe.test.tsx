// @vitest-environment jsdom
/**
 * ADV-48 UI probe (transient adversary evidence; NOT product code).
 * Attacks the "Generate random case" inline-confirmation state machine with
 * careless/impatient user sequences the QA suite does not cover.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NewCasePage from "../../../frontend/src/routes/new";
import { EXAMPLE_PROMPTS } from "../../../frontend/src/journey/examplePrompts";

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

describe("ADV48-UI attacks on the random-case confirmation state machine", () => {
  let mounted: Mounted | null = null;

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("no backend"); }));
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
    if (!mounted) throw new Error("gone");
    return mounted.container;
  };

  it("ADV48-U1 double-click Random (real two-value RNG) leaves an unexpected confirm open", async () => {
    await settleEffects();
    // Simulate two DIFFERENT random seeds (a real impatient double click).
    const seq = [0.31, 0.82];
    let i = 0;
    vi.spyOn(Math, "random").mockImplementation(() => seq[Math.min(i++, seq.length - 1)]);

    click(c(), "generate-random-case"); // empty -> immediate fill
    const first = ta(c()).value;
    expect(first).toContain("Victim: ");
    expect(q(c(), '[data-testid="random-confirm"]')).toBeNull();

    click(c(), "generate-random-case"); // now prompt non-empty & differing -> confirm
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U1] after double-click confirmPresent=${q(c(), '[data-testid="random-confirm"]') !== null} textareaChanged=${ta(c()).value !== first}`,
    );
    expect(q(c(), '[data-testid="random-confirm"]')).not.toBeNull();
    expect(ta(c()).value).toBe(first);
  });

  it("ADV48-U2 stale confirm survives an EXAMPLE click and its Replace clobbers the example", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    // Open the confirm over user text.
    typeInto(c(), "my own prompt");
    click(c(), "generate-random-case");
    expect(q(c(), '[data-testid="random-confirm"]')).not.toBeNull();

    // User changes their mind and clicks the Easy example instead.
    click(c(), "example-easy");
    const afterExample = ta(c()).value;
    expect(afterExample).toBe(EXAMPLE_PROMPTS.easy.prompt);

    const confirmStillOpen = q(c(), '[data-testid="random-confirm"]') !== null;
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U2] confirmLingersAfterExample=${confirmStillOpen} activeExamplePressed=${q(c(), '[data-testid="example-easy"]')?.getAttribute("aria-pressed")}`,
    );
    // BUG: the confirmation panel was authored for the previous text and is
    // still shown even though the textarea now holds the Easy example.
    expect(confirmStillOpen).toBe(true);

    // Clicking the stale "Replace description" overwrites the example.
    click(c(), "random-replace");
    const afterReplace = ta(c()).value;
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U2] exampleClobberedByStaleReplace=${afterReplace !== EXAMPLE_PROMPTS.easy.prompt} activeExamplePressedAfter=${q(c(), '[data-testid="example-easy"]')?.getAttribute("aria-pressed")}`,
    );
    expect(afterReplace).not.toBe(EXAMPLE_PROMPTS.easy.prompt);
  });

  it("ADV48-U3 Replace applies a stale generated string, discarding edits made after the confirm opened", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    click(c(), "generate-random-case"); // empty -> fill seed 0.5
    const generated = ta(c()).value;

    // Now user types their own text (differs), triggering confirm on next click.
    typeInto(c(), "user draft");
    click(c(), "generate-random-case");
    expect(q(c(), '[data-testid="random-confirm"]')).not.toBeNull();
    // pendingRandomPrompt now holds a NEW generated string (same seed here).
    // User edits the textarea WHILE the confirm is open.
    typeInto(c(), "user draft v2 - much better");
    expect(ta(c()).value).toBe("user draft v2 - much better");
    // Then clicks Replace.
    click(c(), "random-replace");
    const finalText = ta(c()).value;
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U3] userEditsLost=${finalText !== "user draft v2 - much better"} finalIsGenerated=${finalText === generated}`,
    );
    expect(finalText).toBe(generated);
  });

  it("ADV48-U4 whitespace-only textarea is silently overwritten without confirmation", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    typeInto(c(), "    \n\t  ");
    click(c(), "generate-random-case");
    const value = ta(c()).value;
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U4] overwroteWhitespaceWithoutConfirm=${value.trim().length > 0} confirmPresent=${q(c(), '[data-testid="random-confirm"]') !== null}`,
    );
    expect(q(c(), '[data-testid="random-confirm"]')).toBeNull();
    expect(value).toContain("Victim: ");
  });

  it("ADV48-U5 confirm open -> change difficulty -> Replace applies the OLD difficulty's text", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    typeInto(c(), "some user text");
    selectDifficulty(c(), "hard"); // confirm not open yet
    click(c(), "generate-random-case"); // pending generated for hard
    const pendingHard = ta(c()).value; // unchanged user text
    expect(q(c(), '[data-testid="random-confirm"]')).not.toBeNull();
    // User now switches to easy while the confirm is open.
    selectDifficulty(c(), "easy");
    click(c(), "random-replace");
    const applied = ta(c()).value;
    const weaponLine = applied.split("\n")[3];
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U5] selectShows=easy appliedWeaponLine=${JSON.stringify(weaponLine)} (pendingWasForHard)`,
    );
    // The applied prompt is the hard-generated one even though Easy is selected.
    expect(applied).toContain("Victim: ");
    expect(pendingHard).toBe("some user text");
    // Document: difficulty no longer matches the weapon/location the text implies.
  });

  it("ADV48-U6 confirm open -> submit navigates with CURRENT textarea text, pending is discarded", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    typeInto(c(), "a valid user prompt");
    click(c(), "generate-random-case");
    expect(q(c(), '[data-testid="random-confirm"]')).not.toBeNull();
    const form = req<HTMLFormElement>(c(), '[data-testid="prompt-form"]');
    act(() => {
      form.requestSubmit();
    });
    await settleEffects();
    const navigated = q(c(), '[data-testid="generating-route"]') !== null;
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U6] navigatedToGenerating=${navigated} confirmGone=${q(c(), '[data-testid="random-confirm"]') === null}`,
    );
    expect(navigated).toBe(true);
  });

  it("ADV48-U7 a11y: confirm has role=group but NO aria-live (state change not announced)", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    typeInto(c(), "existing text");
    const randomBtn = req<HTMLButtonElement>(c(), '[data-testid="generate-random-case"]');
    randomBtn.focus();
    click(c(), "generate-random-case");
    const confirm = req<HTMLElement>(c(), '[data-testid="random-confirm"]');
    const hasLive = confirm.hasAttribute("aria-live") || !!confirm.querySelector("[aria-live]");
    const hasRoleAlert = !!confirm.querySelector('[role="alert"]');
    const activeTag = document.activeElement?.tagName ?? "none";
    const activeTestId = (document.activeElement as HTMLElement | null)?.getAttribute?.("data-testid") ?? null;
    const diceHidden = q(c(), ".new-case-random-icon")?.getAttribute("aria-hidden");
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U7] role=${confirm.getAttribute("role")} ariaLabel=${JSON.stringify(confirm.getAttribute("aria-label"))} ariaLive=${hasLive} roleAlert=${hasRoleAlert} activeElement=${activeTag}#${activeTestId} diceAriaHidden=${diceHidden}`,
    );
    expect(confirm.getAttribute("role")).toBe("group");
    // A visually-inserted confirmation panel with no aria-live and no focus
    // move is silent to a screen-reader user.
    expect(hasLive).toBe(false);
  });

  it("ADV48-U8 accessible name of the dice button equals its visible text (no duplicate)", async () => {
    await settleEffects();
    const btn = req<HTMLButtonElement>(c(), '[data-testid="generate-random-case"]');
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U8] ariaLabel=${JSON.stringify(btn.getAttribute("aria-label"))} visibleText=${JSON.stringify(btn.textContent)}`,
    );
    expect(btn.getAttribute("aria-label")).toBe("Generate random case");
    expect(btn.textContent).toContain("Generate random case");
  });

  it("ADV48-U9 Random fill clears the active example mark; a later edit keeps it cleared", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    click(c(), "example-medium");
    expect(q(c(), '[data-testid="example-medium"]')?.getAttribute("aria-pressed")).toBe("true");
    click(c(), "generate-random-case"); // text differs from example -> confirm
    click(c(), "random-replace");
    const pressed = q(c(), '[data-testid="example-medium"]')?.getAttribute("aria-pressed");
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U9] examplePressedAfterRandomReplace=${pressed}`);
    expect(pressed).toBe("false");
  });

  it("ADV48-U10 confirm open -> click Random again replaces pending text, no crash", async () => {
    await settleEffects();
    const seq = [0.2, 0.4, 0.9];
    let i = 0;
    vi.spyOn(Math, "random").mockImplementation(() => seq[Math.min(i++, seq.length - 1)]);
    typeInto(c(), "user text");
    click(c(), "generate-random-case");
    const pending1 = q(c(), '[data-testid="random-confirm"]') !== null;
    click(c(), "generate-random-case");
    const pending2 = q(c(), '[data-testid="random-confirm"]') !== null;
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U10] confirmPresentAfterFirst=${pending1} afterSecond=${pending2}`);
    expect(pending1).toBe(true);
    expect(pending2).toBe(true);
    click(c(), "random-cancel");
    expect(q(c(), '[data-testid="random-confirm"]')).toBeNull();
  });
});
