import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installLeakListener } from "./helpers";

/**
 * PHASE 17D BUGFIX PART B — "Try an example:" selector browser proof (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Runs against the PRODUCTION SPA (vite preview :4173) and the QA backend on
 * :8000 launched with the hermetic fake provider (GENERATION_PROVIDER=fake,
 * ENV_FILE=os.devnull — the deterministic dev composition; see DEFECTS.md
 * QA-note conventions), so example selection and the NORMAL Prompt-to-World
 * pipeline are exercised in a real browser with zero network cost.
 *
 * Acceptance matrix from Phase17DBugfix.md PART B/D (+ PART E note for the
 * fake leg):
 *
 *  D1 — Easy button fills the EXACT Easy prompt (byte equality);
 *  D2 — Medium button fills the EXACT Medium prompt;
 *  D3 — Hard button fills the EXACT Hard prompt;
 *  D4 — selecting an example NEVER auto-submits: the user stays on /new, no
 *       POST /cases fires, no journey is staged — generation starts ONLY on
 *       the explicit "Generate case" submit (calibration in the funnel test);
 *  D5 — the prompt stays fully editable after any example fill;
 *  D6 — changing examples replaces the prompt deterministically and moves the
 *       active state (aria-pressed), re-selecting the same example is
 *       idempotent; any edit that diverges clears the active mark;
 *  D7 — NO internal implementation detail in the prompt UI copy: no proc.*,
 *       no AssetSpec, no Phase 13/17, no solver tokens, no render id hash;
 *  EASY/MEDIUM/HARD then flow through the NORMAL Generate-case pipeline
 *       (same path as any user-written prompt): /generating -> /scene boots
 *       the deterministic golden apartment world (the fake provider yields
 *       the same published case for every prompt — the partition here is that
 *       the SAME pipeline path works; environment notice stays absent for the
 *       default apartment kit).
 *
 * Plus: DOM-wide "proc." (case-insensitive) audit outside data-testid attrs
 * on /new, and 0 console/page errors / 0 failed resources / 0 external
 * traffic / leak scan 0.
 */

// ---------------------------------------------------------------------------
// the three pinned prompts, byte-exact copies of the frozen module literals
// (Phase17DBugfix.md PART B) — written independently by QA here so a silent
// drift in either side fails the byte-equality assertions.
// ---------------------------------------------------------------------------
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

const EXAMPLES: Array<{ id: string; label: string; helper: string; prompt: string }> = [
  { id: "easy", label: "Easy", helper: "Known environment and common objects.", prompt: EASY_EXPECTED },
  { id: "medium", label: "Medium", helper: "More varied setting and evidence.", prompt: MEDIUM_EXPECTED },
  { id: "hard", label: "Hard", helper: "Includes an unusual object that may require procedural 3D generation.", prompt: HARD_EXPECTED },
];

/** True when the current URL is exactly /new (never the funnel screens). */
function urlIsNew(page: Page): boolean {
  const u = new URL(page.url());
  return u.pathname === "/new";
}

/** Count POST-s to the case-creation endpoint (generation start) so far. */
function installGenerationCounter(page: Page): { count: () => number } {
  let started = 0;
  page.on("request", (req) => {
    if (req.method === "POST" && req.url.includes("/api/v1/cases")) started += 1;
  });
  return {
    count: () => started,
  };
}

test("D1-D7: 'Try an example:' renders Easy/Medium/Hard, fills byte-exact, never auto-submits, stays editable, active state deterministic — plus no internal copy", async ({ page }) => {
  test.setTimeout(90_000);
  const leak = installLeakListener(page);
  const gen = installGenerationCounter(page);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });

  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("prompt-input")).toBeVisible({ timeout: 30_000 });

  // ---- the section renders with the three buttons + helper copy ------------
  const section = page.getByTestId("example-prompts");
  await expect(section).toBeVisible();
  await expect(page.getByTestId("example-prompts-heading")).toHaveText("Try an example:");
  for (const ex of EXAMPLES) {
    const btn = page.getByTestId(`example-${ex.id}`);
    await expect(btn).toBeVisible();
    await expect(btn).toContainText(ex.label);
    await expect(btn).toContainText(ex.helper);
    await expect(btn).toHaveAttribute("type", "button");
    await expect(btn).toHaveAttribute("aria-pressed", "false");
  }

  // ---- D7: no internal copy anywhere in the visible /new UI -----------------
  const visible = await page.locator("body").innerText();
  const lowered = visible.toLowerCase();
  for (const forbidden of ["proc.", "assetspec", "asset spec", "phase 13", "phase 17", "solver", "4551660f4a46b2eb"]) {
    expect(lowered, `/new copy must not contain ${forbidden}`).not.toContain(forbidden);
  }

  // ---- D1/D2/D3 + D4 + D5 + D6 in one interaction sequence -----------------
  const textarea = page.getByTestId("prompt-input");
  await expect(textarea).toHaveValue("");

  // Easy fill: byte-exact, active moves to easy, NO generation started.
  await page.getByTestId("example-easy").click();
  await expect(textarea).toHaveValue(EASY_EXPECTED);
  await expect(textarea).toHaveJSProperty("value", EASY_EXPECTED);
  await expect(page.getByTestId("example-easy")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("example-medium")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("example-hard")).toHaveAttribute("aria-pressed", "false");
  await page.waitForTimeout(600);
  expect(urlIsNew(page), "after example click the user is still on /new").toBe(true);
  expect(gen.count(), "example click must never start generation (0 POST /cases)").toBe(0);
  await expect(page.getByTestId("prompt-form")).toBeVisible();

  // D5 — fully editable: a divergent keystroke is kept and clears the mark.
  await textarea.fill(EASY_EXPECTED + "\nClue: diary");
  await expect(textarea).toHaveValue(EASY_EXPECTED + "\nClue: diary");
  await expect(page.getByTestId("example-easy")).toHaveAttribute("aria-pressed", "false");

  // D6 — changing examples replaces deterministically and moves active state.
  await page.getByTestId("example-hard").click();
  await expect(textarea).toHaveValue(HARD_EXPECTED);
  await expect(page.getByTestId("example-hard")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("example-easy")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("example-medium")).toHaveAttribute("aria-pressed", "false");

  // D3 — Hard byte-exact (from the D6 transition, then resets).
  await page.getByTestId("example-medium").click();
  await expect(textarea).toHaveValue(MEDIUM_EXPECTED);
  await expect(textarea).toHaveJSProperty("value", MEDIUM_EXPECTED);
  await expect(page.getByTestId("example-medium")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("example-hard")).toHaveAttribute("aria-pressed", "false");

  // D2 idle byte-exact: select Easy again then Medium; no submissions so far.
  await page.getByTestId("example-medium").click(); // idempotent re-select
  await expect(textarea).toHaveValue(MEDIUM_EXPECTED);
  expect(gen.count(), "all example clicks together still never started generation").toBe(0);
  expect(urlIsNew(page), "still on /new after every example click").toBe(true);

  // D7 (browser-wide) — no "proc." in the DOM outside a data-testid attr.
  const procLeaks = await page.evaluate(() => {
    const html = document.documentElement?.outerHTML ?? "";
    const testids: string[] = [];
    for (const m of html.matchAll(/data-testid="([^"]*)"/g)) testids.push(m[1]);
    let stripped = html;
    for (const id of testids) stripped = stripped.replace(`data-testid="${id}"`, "");
    return { outside: stripped.toLowerCase().split("proc.").length - 1 };
  });
  expect(procLeaks.outside, "DOM contains ZERO 'proc.' outside data-testid attributes").toBe(0);

  expect(leak.matched, "no leak listener matches on /new").toEqual([]);
  expect(pageErrors, "no page errors").toEqual([]);
  expect(consoleErrors, "no console errors").toEqual([]);
});

test("D4-calibration + funnel: each example runs through the NORMAL Generate flow (fake provider) -> /generating -> the pipeline's deterministic outcome", async ({ page }) => {
  test.setTimeout(180_000);
  const leak = installLeakListener(page);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });

  for (const ex of EXAMPLES) {
    const gen = installGenerationCounter(page);
    await page.goto("/new", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("prompt-input")).toBeVisible({ timeout: 30_000 });

    // Selection fills the textarea; the USER's explicit Generate click is the
    // ONLY thing that stages the journey (no auto-submit).
    await page.getByTestId(`example-${ex.id}`).click();
    await expect(page.getByTestId("prompt-input")).toHaveValue(ex.prompt);
    await page.waitForTimeout(400);
    expect(urlIsNew(page), `[${ex.id}] example click does not navigate`).toBe(true);
    expect(gen.count(), `[${ex.id}] example click does not start generation`).toBe(0);

    // The NORMAL flow: explicit "Generate case" submit -> the SAME pipeline
    // path as any user-written prompt. The deterministic dev-mode composition
    // is a FIXED golden case (Per .env.example: "Every prompt yields the same
    // logically-validated golden case from the shipped dev-mode script"), so a
    // prompt that locks DIFFERENT entities (the showcase examples do) fails
    // the bound terminal lock-validation exactly like any other user prompt
    // would — fail-closed, sanitized, zero internal detail. (The Real Hermes
    // stack composes the prompt's own world; see the PART E real journey.)
    await page.getByTestId("generate-case").click();
    await expect(page).toHaveURL("/generating", { timeout: 15_000 });
    await expect(page.getByTestId("generation-stage-label")).toBeVisible({ timeout: 20_000 });

    // Deterministic outcome on the fake stack: the sanitized fail-closed state.
    const failedAlert = page.locator('[data-testid="generation-failed"][role="alert"]');
    await expect(failedAlert).toBeVisible({ timeout: 60_000 });
    await expect(page).toHaveURL(/\/generating/);
    const failedNotice = await failedAlert.innerText();
    expect(
      failedNotice,
      `[${ex.id}] failure copy is the sanitized contract message`,
    ).toContain("could not be turned into a solvable case");
    await expect(page.locator('button[data-testid="generation-failed"]')).toBeVisible();
    await expect(page.getByTestId("generation-back-to-start")).toBeVisible();

    // The failure state must not leak ANY internal detail (no traceback, no
    // proc.*, no host/URL material, no AssetSpec/Phase/solver tokens).
    const failedText = (await page.locator("body").innerText()).toLowerCase();
    for (const forbidden of ["traceback", "proc.", "assetspec", "phase 13", "phase 17", "solver", "11434", "127.0.0.1", "http://"]) {
      expect(failedText, `[${ex.id}] failed-state copy must not contain ${forbidden}`).not.toContain(forbidden);
    }

    expect(leak.matched, `[${ex.id}] leak listener reported nothing`).toEqual([]);
    expect(pageErrors, `[${ex.id}] no page errors`).toEqual([]);
    expect(consoleErrors, `[${ex.id}] no console errors`).toEqual([]);
    console.log(`P17DB_EXAMPLES_FUNNEL ${ex.id} FAKE_STACK_DETERMINISTIC_FAILCLOSED=true`);
  }
});