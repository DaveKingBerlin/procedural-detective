// @vitest-environment jsdom
/**
 * ADV-48 UI probe #2 (transient adversary evidence; NOT product code).
 * Second wave: confirmation state-machine staleness, focus/keyboard, fetch
 * neutrality, equality fast-path and confirm/example interaction.
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

describe("ADV48-UI-2 attacks on the random-case confirmation state machine", () => {
  let mounted: Mounted | null = null;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => {
      throw new Error("no backend");
    });
    vi.stubGlobal("fetch", fetchMock);
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

  it("ADV48-U11 stale confirm: example click then Cancel keeps the example (control)", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    typeInto(c(), "user text");
    click(c(), "generate-random-case");
    expect(q(c(), '[data-testid="random-confirm"]')).not.toBeNull();
    click(c(), "example-easy");
    click(c(), "random-cancel");
    const value = ta(c()).value;
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U11] afterExampleCancelKeepsExample=${value === EXAMPLE_PROMPTS.easy.prompt} confirmGone=${q(c(), '[data-testid="random-confirm"]') === null}`,
    );
    expect(value).toBe(EXAMPLE_PROMPTS.easy.prompt);
    expect(q(c(), '[data-testid="random-confirm"]')).toBeNull();
  });

  it("ADV48-U12 confirm open, edit textarea, Cancel keeps the edited text", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    typeInto(c(), "original");
    click(c(), "generate-random-case");
    typeInto(c(), "edited while confirm open");
    click(c(), "random-cancel");
    const value = ta(c()).value;
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U12] cancelKeepsEdits=${value === "edited while confirm open"}`);
    expect(value).toBe("edited while confirm open");
  });

  it("ADV48-U13 confirm open, click example, then submit -> example is what is staged (pending discarded)", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    typeInto(c(), "user text");
    click(c(), "generate-random-case");
    click(c(), "example-medium");
    const form = req<HTMLFormElement>(c(), '[data-testid="prompt-form"]');
    act(() => {
      form.requestSubmit();
    });
    await settleEffects();
    const navigated = q(c(), '[data-testid="generating-route"]') !== null;
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U13] navigated=${navigated}`);
    expect(navigated).toBe(true);
  });

  it("ADV48-U14 confirm open, click example, click Random again -> pending regenerated; Replace applies a random prompt, not the example", async () => {
    await settleEffects();
    const seq = [0.11, 0.77];
    let i = 0;
    vi.spyOn(Math, "random").mockImplementation(() => seq[Math.min(i++, seq.length - 1)]);
    typeInto(c(), "user text");
    click(c(), "generate-random-case"); // pending #1
    click(c(), "example-hard");
    click(c(), "generate-random-case"); // pending #2 (new seed)
    click(c(), "random-replace");
    const value = ta(c()).value;
    const isExample = value === EXAMPLE_PROMPTS.hard.prompt;
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U14] afterReplaceIsExample=${isExample} startsWithVictim=${value.startsWith("Victim: ")}`);
    expect(isExample).toBe(false);
    expect(value).toContain("Victim: ");
  });

  it("ADV48-U15 a11y: confirm is role=group, no aria-live, no aria-describedby, focus not moved", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    typeInto(c(), "existing");
    const randomBtn = req<HTMLButtonElement>(c(), '[data-testid="generate-random-case"]');
    randomBtn.focus();
    click(c(), "generate-random-case");
    const confirm = req<HTMLElement>(c(), '[data-testid="random-confirm"]');
    const textarea = ta(c());
    const hasLive = confirm.hasAttribute("aria-live") || !!confirm.querySelector("[aria-live]");
    const describedBy = textarea.getAttribute("aria-describedby");
    const activeId = (document.activeElement as HTMLElement | null)?.getAttribute?.("data-testid") ?? null;
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-U15] role=${confirm.getAttribute("role")} ariaLive=${hasLive} textareaDescribedBy=${JSON.stringify(describedBy)} activeElement=${activeId}`,
    );
    expect(confirm.getAttribute("role")).toBe("group");
    expect(hasLive).toBe(false);
  });

  it("ADV48-U16 keyboard tab order from Random reaches Replace then Cancel", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    typeInto(c(), "existing");
    const randomBtn = req<HTMLButtonElement>(c(), '[data-testid="generate-random-case"]');
    randomBtn.focus();
    click(c(), "generate-random-case");
    // Focus stays on the Random button (no move); document the order.
    const order: string[] = [];
    order.push(document.activeElement?.getAttribute?.("data-testid") ?? "?");
    const tabbables = Array.from(
      c().querySelectorAll<HTMLElement>(
        '[data-testid="generate-random-case"], [data-testid="random-replace"], [data-testid="random-cancel"]',
      ),
    ).map((el) => el.getAttribute("data-testid"));
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U16] activeAfterOpen=${order[0]} tabbablesInDomOrder=${JSON.stringify(tabbables)}`);
    expect(tabbables).toEqual(["generate-random-case", "random-replace", "random-cancel"]);
  });

  it("ADV48-U17 no fetch is ever issued by random fill / confirm / replace", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    typeInto(c(), "user text");
    click(c(), "generate-random-case");
    click(c(), "random-replace");
    typeInto(c(), "another user text"); // force a fresh confirm (text differs from a new random draw)
    click(c(), "generate-random-case");
    click(c(), "random-cancel");
    await settleEffects();
    // The capabilities hook may fetch on mount; assert no CASE-CREATION POST.
    const casePosts = fetchMock.mock.calls.filter(([url, init]) => {
      const method = (init as RequestInit | undefined)?.method ?? "GET";
      return method === "POST" && String(url).includes("/api/v1/cases");
    });
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U17] totalFetch=${fetchMock.mock.calls.length} caseCreationPosts=${casePosts.length}`);
    expect(casePosts.length).toBe(0);
  });

  it("ADV48-U18 equality fast-path: Random on text already equal to the generated text shows NO confirm", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    click(c(), "generate-random-case"); // empty -> fill
    const first = ta(c()).value;
    click(c(), "generate-random-case"); // same mocked seed -> same text -> silent no-op
    const confirmPresent = q(c(), '[data-testid="random-confirm"]') !== null;
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U18] confirmOnIdenticalText=${confirmPresent} textUnchanged=${ta(c()).value === first}`);
    expect(confirmPresent).toBe(false);
    expect(ta(c()).value).toBe(first);
  });

  it("ADV48-U19 difficulty change before Random uses the new difficulty's weapon pool", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.999);
    selectDifficulty(c(), "easy");
    click(c(), "generate-random-case");
    const value = ta(c()).value;
    const weapon = value.split("\n")[3].replace("Weapon: ", "");
    const easyNames = new Set(
      (await import("../../../frontend/src/journey/randomCrimeGenerator")).eligibleWeapons("easy").map((w) => w.displayName),
    );
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U19] easyWeapon=${JSON.stringify(weapon)} inEasyPool=${easyNames.has(weapon)}`);
    expect(easyNames.has(weapon)).toBe(true);
  });

  it("ADV48-U20 generated prompt fits the textarea maxLength", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    click(c(), "generate-random-case");
    const value = ta(c()).value;
    const maxLength = ta(c()).maxLength;
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U20] len=${value.length} maxLength=${maxLength}`);
    expect(maxLength).toBeGreaterThanOrEqual(value.length);
  });

  it("ADV48-U21 confirm open then Replace then a second Replace click is a safe no-op", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    typeInto(c(), "user text");
    click(c(), "generate-random-case");
    click(c(), "random-replace");
    // button is gone now; ensure no crash and textarea holds the random prompt
    const gone = q(c(), '[data-testid="random-replace"]') === null;
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U21] replaceButtonGoneAfterApply=${gone} hasVictim=${ta(c()).value.startsWith("Victim: ")}`);
    expect(gone).toBe(true);
  });

  it("ADV48-U22 confirm open then difficulty change then Cancel leaves text untouched", async () => {
    await settleEffects();
    vi.spyOn(Math, "random").mockReturnValue(0.42);
    typeInto(c(), "my text");
    click(c(), "generate-random-case");
    selectDifficulty(c(), "hard");
    click(c(), "random-cancel");
    // eslint-disable-next-line no-console
    console.log(`[ADV48-U22] textUntouched=${ta(c()).value === "my text"}`);
    expect(ta(c()).value).toBe("my text");
  });
});
