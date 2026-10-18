import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installLeakListener } from "./helpers";

/**
 * PHASE 48 — "Generate random case" prompt-helper browser proof (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Runs against the PRODUCTION SPA (vite preview :4173) and the QA backend on
 * :8000 launched with the hermetic fake provider (GENERATION_PROVIDER=fake,
 * ENV_FILE=os.devnull — the deterministic dev composition; see
 * Phase17DBugfix.md / e2e/phase17dbugfix-examples.spec.ts for the same stack).
 *
 * Acceptance matrix from Phase48.md §32 (+ §17/§18/§19/§27):
 *
 *  A1 — a Random Case action renders DIRECTLY below the difficulty control,
 *       is type="button", and its accessible name is "Generate random case";
 *  A2 — Random is NOT treated as a difficulty (exactly Easy/Medium/Hard remain,
 *       the selected difficulty is preserved, selecting it never changes it);
 *  A3 — an empty textarea is filled with a STRUCTURED 7-line prompt (exact
 *       stable labels; Victim "(gender, age, occupation)"; distinct persons);
 *  A4 — pressing Random NEVER auto-starts generation: ZERO POST /api/v1/cases,
 *       stays on /new, no navigation, no submit;
 *  A5 — a populated textarea shows the INLINE confirmation and does not destroy
 *       user text; Keep leaves it untouched; Replace applies the pending prompt;
 *  A6 — the CURRENT difficulty is used (the eventual POST body carries it);
 *  A7 — keyboard-only operation (Tab focus + Enter + Space activation);
 *  A8 — narrow/mobile viewport has NO horizontal overflow (idle + confirm open);
 *  A9 — no truth leak, no page/console errors on /new.
 */

/** True when the current URL is exactly /new (never the funnel screens). */
function urlIsNew(page: Page): boolean {
  return new URL(page.url()).pathname === "/new";
}

/** Count POST-s to the case-creation endpoint (generation start) so far. */
function installGenerationCounter(page: Page): { count: () => number } {
  let started = 0;
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/api/v1/cases")) started += 1;
  });
  return { count: () => started };
}

/** The structured lines of a generated prompt. */
function promptLines(text: string): string[] {
  return text.split("\n");
}

function assertStructuredPrompt(text: string): void {
  const lines = promptLines(text);
  expect(lines, "the random prompt must be exactly 7 lines").toHaveLength(7);
  for (const line of lines) {
    expect(line.trim().length, "no generated line may be blank").toBeGreaterThan(0);
  }
  expect(lines[0]).toMatch(/^Victim: .+ \((Male|Female|Other|Unknown), \d{1,3}, .+\)$/);
  expect(lines[1]).toMatch(/^Murderer: .+$/);
  expect(lines[2]).toMatch(/^Motive: .+$/);
  expect(lines[3]).toMatch(/^Weapon: .+$/);
  expect(lines[4]).toMatch(/^Time: \d{2}:\d{2}$/);
  expect(lines[5]).toMatch(/^Witness: .+$/);
  expect(lines[6]).toMatch(/^Location: .+$/);

  // Distinct persons (victim / murderer / witness share no full name).
  const victim = lines[0].replace(/^Victim: /, "").replace(/ \(.*$/, "");
  const murderer = lines[1].replace(/^Murderer: /, "");
  const witness = lines[5].replace(/^Witness: /, "");
  expect(new Set([victim, murderer, witness]).size, "victim/murderer/witness must differ").toBe(3);

  // No placeholder names (Phase48 §5).
  for (const name of [victim, murderer, witness]) {
    expect(name).not.toMatch(/^(Person [A-Z]|Victim \d|Witness [A-Z])$/);
  }
}

test("A1/A2/A3/A4/A9 — placement, a11y, empty-fill structure, ZERO auto-submit", async ({ page }) => {
  test.setTimeout(90_000);
  const leak = installLeakListener(page);
  const gen = installGenerationCounter(page);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(`[${msg.type()}] ${msg.text}`);
  });

  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("prompt-input")).toBeVisible({ timeout: 30_000 });

  const select = page.getByTestId("difficulty-select");
  const random = page.getByTestId("generate-random-case");
  const generateCase = page.getByTestId("generate-case");

  // A1 — directly below the difficulty control (immediate element sibling).
  await expect(
    page.locator('[data-testid="difficulty-select"] + [data-testid="generate-random-case"]'),
    "the Random button must be the immediate next element after the difficulty control",
  ).toHaveCount(1);
  await expect(random).toBeVisible();
  await expect(random).toHaveAttribute("type", "button");
  await expect(random).toHaveAttribute("aria-label", "Generate random case");
  await expect(
    page.getByRole("button", { name: "Generate random case", exact: true }),
    "accessible name is exactly 'Generate random case'",
  ).toBeVisible();

  // DOM geometry: below the select, above the Generate-case action.
  const selectBox = await select.boundingBox();
  const randomBox = await random.boundingBox();
  const generateBox = await generateCase.boundingBox();
  expect(selectBox && randomBox && generateBox).toBeTruthy();
  expect(randomBox!.y).toBeGreaterThan(selectBox!.y);
  expect(randomBox!.y).toBeLessThan(generateBox!.y);

  // A2 — exactly Easy/Medium/Hard; Random is not a difficulty option.
  const options = page.locator("#difficulty-select option");
  await expect(options).toHaveCount(3);
  expect(await options.evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value))).toEqual([
    "easy",
    "medium",
    "hard",
  ]);

  // A3/A4 — empty textarea -> structured fill, no generation.
  const textarea = page.getByTestId("prompt-input");
  await expect(textarea).toHaveValue("");
  const difficultyBefore = await select.inputValue();

  await random.click();

  const value = await textarea.inputValue();
  assertStructuredPrompt(value);

  // The selected difficulty is preserved (A2) — Random never changes it.
  expect(await select.inputValue()).toBe(difficultyBefore);
  expect(await select.inputValue()).toBe("medium");

  // No inline confirmation was needed for an empty textarea.
  await expect(page.getByTestId("random-confirm")).toHaveCount(0);

  // A4 — the helper never starts generation.
  await page.waitForTimeout(500);
  expect(urlIsNew(page), "random fill must stay on /new").toBe(true);
  expect(gen.count(), "random fill must issue ZERO POST /api/v1/cases").toBe(0);
  await expect(page.getByTestId("prompt-form")).toBeVisible();

  // The generated prompt is editable: a divergent keystroke is kept.
  await textarea.fill(value + "\nClue: diary");
  await expect(textarea).toHaveValue(value + "\nClue: diary");

  expect(leak.matched, "no leak listener matches on /new").toEqual([]);
  expect(pageErrors, "no page errors").toEqual([]);
  expect(consoleErrors, "no console errors").toEqual([]);
});

test("A5 — populated field: inline confirm, Keep leaves text, Replace applies", async ({ page }) => {
  test.setTimeout(90_000);
  const gen = installGenerationCounter(page);

  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("prompt-input")).toBeVisible({ timeout: 30_000 });
  const textarea = page.getByTestId("prompt-input");
  const userText = "A quiet murder in the archive room.";

  await textarea.fill(userText);
  await page.getByTestId("generate-random-case").click();

  // Inline confirmation (not a modal / window.confirm); user text untouched.
  const confirm = page.getByTestId("random-confirm");
  await expect(confirm).toBeVisible();
  await expect(confirm).toContainText("Replace it with the generated case?");
  await expect(page.getByTestId("random-replace")).toBeVisible();
  await expect(page.getByTestId("random-cancel")).toBeVisible();
  await expect(textarea).toHaveValue(userText);

  // Keep current description -> dismissed, text untouched.
  await page.getByTestId("random-cancel").click();
  await expect(confirm).toHaveCount(0);
  await expect(textarea).toHaveValue(userText);

  // Random again -> Replace description applies the pending prompt.
  await page.getByTestId("generate-random-case").click();
  await expect(confirm).toBeVisible();
  await expect(textarea).toHaveValue(userText);
  await page.getByTestId("random-replace").click();
  await expect(confirm).toHaveCount(0);
  const replaced = await textarea.inputValue();
  assertStructuredPrompt(replaced);
  expect(replaced).not.toBe(userText);

  // Still no generation started.
  await page.waitForTimeout(400);
  expect(gen.count(), "confirm/replace must never start generation").toBe(0);
  expect(urlIsNew(page)).toBe(true);
});

test("A6 — the current difficulty is used by the normal generation path", async ({ page }) => {
  test.setTimeout(90_000);

  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("prompt-input")).toBeVisible({ timeout: 30_000 });

  await page.getByTestId("difficulty-select").selectOption("hard");
  await page.getByTestId("generate-random-case").click();
  const generated = await page.getByTestId("prompt-input").inputValue();
  assertStructuredPrompt(generated);

  // The difficulty is preserved across the random fill.
  await expect(page.getByTestId("difficulty-select")).toHaveValue("hard");

  // The explicit user submit is the ONLY path that stages a journey.
  const requestPromise = page.waitForRequest(
    (req) => req.method() === "POST" && req.url().includes("/api/v1/cases"),
    { timeout: 20_000 },
  );
  await page.getByTestId("generate-case").click();
  const request = await requestPromise;
  const body = request.postDataJSON() as { prompt?: string; difficulty?: string };
  expect(body.difficulty, "the POST /cases body carries the selected difficulty").toBe("hard");
  expect(body.prompt, "the POST /cases body carries the generated prompt verbatim").toBe(generated);
  await expect(page).toHaveURL(/\/generating/, { timeout: 20_000 });
});

test("A7 — keyboard-only operation (Tab focus, Enter, Space)", async ({ page }) => {
  test.setTimeout(90_000);

  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("prompt-input")).toBeVisible({ timeout: 30_000 });
  const random = page.getByTestId("generate-random-case");
  const textarea = page.getByTestId("prompt-input");

  // Keyboard navigation: focus the difficulty control, Tab to the Random button.
  await page.getByTestId("difficulty-select").focus();
  await page.keyboard.press("Tab");
  await expect(random).toBeFocused();

  // Enter activates it.
  await page.keyboard.press("Enter");
  await expect(textarea).not.toHaveValue("");
  const first = await textarea.inputValue();
  assertStructuredPrompt(first);

  // Space activates it too (a NEW random prompt now differs -> inline confirm).
  await random.focus();
  await page.keyboard.press("Space");
  await expect(page.getByTestId("random-confirm")).toBeVisible();
  await expect(textarea).toHaveValue(first);

  // The confirmation itself is keyboard-operable: Tab to Keep and activate.
  await page.getByTestId("random-cancel").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("random-confirm")).toHaveCount(0);
  await expect(textarea).toHaveValue(first);
});

test("A8 — narrow/mobile viewport has no horizontal overflow (idle + confirm)", async ({ page }) => {
  test.setTimeout(90_000);

  for (const width of [360, 320]) {
    await page.setViewportSize({ width, height: 740 });
    await page.goto("/new", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("prompt-input")).toBeVisible({ timeout: 30_000 });

    const random = page.getByTestId("generate-random-case");
    await expect(random).toBeVisible();

    const measure = async () => {
      const doc = document.documentElement;
      const randomEl = document.querySelector('[data-testid="generate-random-case"]')!;
      const rect = randomEl.getBoundingClientRect();
      return {
        scrollWidth: doc.scrollWidth,
        clientWidth: doc.clientWidth,
        innerWidth: window.innerWidth,
        right: rect.right,
        left: rect.left,
      };
    };

    const idle = await page.evaluate(measure);
    expect(
      idle.scrollWidth,
      `[${width}px] no horizontal overflow on /new`,
    ).toBeLessThanOrEqual(idle.clientWidth + 1);
    expect(idle.left, `[${width}px] Random button left edge inside viewport`).toBeGreaterThanOrEqual(0);
    expect(idle.right, `[${width}px] Random button right edge inside viewport`).toBeLessThanOrEqual(
      idle.innerWidth + 1,
    );

    // Confirm panel open must not introduce overflow either.
    await page.getByTestId("prompt-input").fill("Some pre-existing user text.");
    await random.click();
    await expect(page.getByTestId("random-confirm")).toBeVisible();
    const open = await page.evaluate(measure);
    expect(
      open.scrollWidth,
      `[${width}px] no horizontal overflow with the inline confirm open`,
    ).toBeLessThanOrEqual(open.clientWidth + 1);
  }
});
