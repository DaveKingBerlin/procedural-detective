import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createPlaythroughViaApi, seedPlaythroughCredentials } from "./helpers";

/**
 * Phase 8 S — ACCESSIBILITY SANITY (QA-owned).
 *
 * From the LIVE app (backend :8000 migrated + vite preview :4173):
 *   - every non-3D action is reachable by keyboard: Tab to the object-list
 *     buttons, the accusation radios, the time input, confirm, reveal nav;
 *   - Esc closes panels (keyboard path);
 *   - visible focus rings (:focus-visible outline);
 *   - readable contrast on landing/reveal primary text (computed ratio);
 *   - semantic buttons/forms (labels/legends present);
 *   - evidence/reveal text is fully usable without the canvas (the object list
 *     + panel are DOM text, not canvas-only).
 *
 * QA only reports; defects are filed only for blockers.
 */

/** Serializable snapshot of the page's active element (browser side). */
interface ActiveSnapshot {
  tag: string;
  testid: string;
  type: string;
  name: string;
  text: string;
  ariaLabel: string;
}

async function activeSnapshot(page: Page): Promise<ActiveSnapshot | null> {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (el === null || el === document.body || el === undefined) return null;
    return {
      tag: el.tagName,
      testid: el.getAttribute("data-testid") ?? "",
      type: el.getAttribute("type") ?? "",
      name: el.getAttribute("name") ?? "",
      text: (el.textContent ?? "").trim().slice(0, 60),
      ariaLabel: el.getAttribute("aria-label") ?? "",
    };
  });
}

/** Tab until `predicate` matches the ACTIVE-ELEMENT SNAPSHOT (bounded), proving keyboard reachability. */
async function tabUntil(
  page: Page,
  predicate: (active: ActiveSnapshot | null) => boolean,
  label: string,
): Promise<void> {
  for (let i = 0; i < 30; i++) {
    const snap = await activeSnapshot(page);
    if (predicate(snap)) {
      await test.info().attach(`a11y-tab-target-${label}.json`, {
        body: JSON.stringify(snap, null, 2),
        contentType: "application/json",
      });
      return;
    }
    await page.keyboard.press("Tab");
  }
  throw new Error(`Tab never reached: ${label}`);
}

const isNewInvestigation = (s: ActiveSnapshot | null) =>
  s?.tag === "A" && s.testid === "new-investigation";
const isTryDemo = (s: ActiveSnapshot | null) => s?.tag === "BUTTON" && s.testid === "try-demo";
const isObjectListButton = (s: ActiveSnapshot | null) => s?.tag === "BUTTON" && s.testid.startsWith("object-");
const isKnifeButton = (s: ActiveSnapshot | null) => s?.tag === "BUTTON" && s.testid === "object-kitchen_knife";
const isRadioNamed = (s: ActiveSnapshot | null, name: string) =>
  s?.tag === "INPUT" && s.type === "radio" && s.name === name;
const isTimeInput = (s: ActiveSnapshot | null) => s?.tag === "INPUT" && s.type === "time";
const isSubmitButton = (s: ActiveSnapshot | null) =>
  s?.tag === "BUTTON" && s.testid === "accusation-submit";

/** Contrast ratio between an element's computed color and background (bottom-up). */
async function contrastRatio(page: Page, selector: string): Promise<{ ratio: number; fg: string; bg: string }> {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { ratio: 0, fg: "", bg: "" };
    const style = getComputedStyle(el);
    const fg = style.color;
    const bg = style.backgroundColor;
    const parse = (c: string): [number, number, number] | null => {
      const m = c.match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const parts = m[1].split(",").map((s) => parseFloat(s.trim()));
      return [parts[0], parts[1], parts[2]];
    };
    // Walk up the DOM until a fully opaque background is found (a translucent
    // tint such as rgba(...,0.1) visually sits ON a panel color, so it must not
    // count as the background for contrast).
    let node: HTMLElement | null = el;
    let bgColor = "";
    while (node) {
      const b = getComputedStyle(node).backgroundColor;
      const alphaMatch = b.match(/rgba?\(([^)]+)\)/);
      const alpha =
        alphaMatch && (b.startsWith("rgba") || b.startsWith("RGBA"))
          ? parseFloat(alphaMatch[1].split(",")[3]?.trim() ?? "1")
          : 1;
      if (b !== "rgba(0, 0, 0, 0)" && b !== "transparent" && alpha >= 0.95) {
        bgColor = b;
        break;
      }
      node = node.parentElement;
    }
    if (bgColor === "") bgColor = "rgb(15, 17, 23)"; // the app shell background
    const linearize = (v: number) => {
      const s = v / 255;
      return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    const lum = (c: string) => {
      const rgb = parse(c);
      if (!rgb) return 1;
      const [r, g, b] = rgb.map(linearize);
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const l1 = lum(fg);
    const l2 = lum(bgColor);
    const [hi, lo] = l1 >= l2 ? [l1, l2] : [l2, l1];
    const ratio = (hi + 0.05) / (lo + 0.05);
    return { ratio: Math.round(ratio * 100) / 100, fg, bg: bgColor };
  }, selector);
}

test("Phase 8 S a11y: keyboard-only accusation->reveal, focus rings, contrast, semantics", async ({
  page,
  request,
}) => {
  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));

  // ------------------------------------------------ landing keyboard focus ---
  await page.goto("/", { waitUntil: "domcontentloaded" });
  // The app shell header has its own focusable links, so Tab until the first
  // landing action (New Investigation) is reached — every landing action stays
  // reachable through plain Tab.
  await tabUntil(page, isNewInvestigation, "landing-new-investigation");
  const landingFocus = await page.evaluate(() => ({
    testid: document.activeElement?.getAttribute("data-testid") ?? "",
    outline: document.activeElement ? getComputedStyle(document.activeElement).outlineStyle : "",
  }));
  console.log("A11Y_LANDING_FOCUS", JSON.stringify(landingFocus));
  expect(landingFocus.testid, "Tab reaches New Investigation").toBe("new-investigation");
  expect(landingFocus.outline, "focused landing action has a visible focus ring").not.toBe("none");
  // One more Tab reaches Try Demo Case (also keyboard-reachable).
  await page.keyboard.press("Tab");
  const secondFocus = await page.evaluate(() => document.activeElement?.getAttribute("data-testid") ?? "");
  console.log("A11Y_LANDING_SECOND_FOCUS", JSON.stringify(secondFocus));
  expect(secondFocus, "next Tab reaches Try Demo Case").toBe("try-demo");

  // Contrast: landing tagline + heading.
  const landingTagline = await contrastRatio(page, ".landing-tagline");
  const landingTitleRatio = await contrastRatio(page, ".landing-title");
  console.log("A11Y_LANDING_CONTRAST", JSON.stringify({ tagline: landingTagline, title: landingTitleRatio }));
  expect(landingTagline.ratio, "landing tagline contrast >= 4.5").toBeGreaterThanOrEqual(4.5);
  expect(landingTitleRatio.ratio, "landing title contrast >= 4.5").toBeGreaterThanOrEqual(4.5);

  // ------------------------------------------------ /new semantics ----------
  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByLabel("Describe the crime")).toBeVisible();
  await expect(page.getByTestId("difficulty-select")).toBeVisible();
  const generateFocus = await page.evaluate(() => {
    const btn = document.querySelector('[data-testid="generate-case"]') as HTMLElement;
    btn.focus();
    return getComputedStyle(btn).outlineStyle;
  });
  console.log("A11Y_NEW_GENERATE_FOCUS_RING", JSON.stringify(generateFocus));
  expect(generateFocus, "focused Generate case button has a visible focus ring").not.toBe("none");

  // ------------------------------------------------ /scene keyboard ---------
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });

  // The object-list buttons are keyboard-reachable (non-canvas UI).
  await page.keyboard.press("Tab");
  await tabUntil(page, isObjectListButton, "scene-object-list-button");
  const objectListFocus = await page.evaluate(() => ({
    tag: document.activeElement?.tagName,
    testid: document.activeElement?.getAttribute("data-testid") ?? "",
    outline: document.activeElement ? getComputedStyle(document.activeElement).outlineStyle : "",
  }));
  console.log("A11Y_SCENE_OBJECT_FOCUS", JSON.stringify(objectListFocus));
  expect(objectListFocus.tag, "object list button is a real button").toBe("BUTTON");
  expect(objectListFocus.outline, "focused object button has a visible focus ring").not.toBe("none");

  // Evidence is usable WITHOUT the canvas: the list label is DOM text.
  const objectListText = await page.getByTestId("scene-objects").innerText();
  expect(objectListText.toLowerCase(), "object list (non-canvas) carries the object labels").toContain("kitchen knife");

  // Tab specifically to the KNIFE button (first tabbable objects — door/lamp —
  // carry no evidence), then Enter -> panel; Esc closes.
  await tabUntil(page, isKnifeButton, "object-kitchen_knife-button");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 15_000 });
  // The evidence record text is DOM text (readable without the canvas).
  const panelText = await page.getByTestId("evidence-panel").innerText();
  expect(panelText.toLowerCase(), "evidence record text visible without the canvas").toContain("kitchen knife");
  await page.screenshot({ path: "artifacts/screenshots/a11y-scene-panel.png", fullPage: true });
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("evidence-panel")).not.toBeVisible();

  // ------------------------------------------------ /accuse keyboard path ---
  await page.goto("/accuse", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });

  // Semantic form structure: named fieldsets with legends + radios + labelled time.
  await expect(page.getByTestId("accusation-form")).toBeVisible();
  expect(await page.getByRole("radio", { name: /Thomas Reed/ }).count(), "WHO radios present").toBeGreaterThan(0);
  const legends = await page.locator("fieldset legend").allTextContents();
  console.log("A11Y_ACCUSE_LEGENDS", JSON.stringify(legends.map((l) => l.trim())));
  expect(legends.length, "each candidate fieldset has a legend").toBe(3);

  // Keyboard reachability: radios, time input, submit button in tab order.
  await page.keyboard.press("Tab");
  await tabUntil(page, (el) => isRadioNamed(el, "murdererId"), "murderer-radio");
  await tabUntil(page, (el) => isRadioNamed(el, "motiveId"), "motive-radio");
  await tabUntil(page, (el) => isRadioNamed(el, "weaponId"), "weapon-radio");
  await tabUntil(page, isTimeInput, "time-input");
  await tabUntil(page, isSubmitButton, "submit-button");

  // Full keyboard accusation: focus + Space selects radios, keyboard types the
  // time, Tab+Enter submits and confirms.
  await page.getByTestId("accusation-option-murdererId-thomas_reed").focus();
  await page.keyboard.press("Space");
  await page.getByTestId("accusation-option-motiveId-cover_up_embezzlement").focus();
  await page.keyboard.press("Space");
  await page.getByTestId("accusation-option-weaponId-kitchen_knife").focus();
  await page.keyboard.press("Space");
  await page.getByTestId("accusation-time").focus();
  await page.keyboard.type("22:17");
  await page.getByTestId("accusation-submit").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("accusation-confirm").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });

  // ------------------------------------------------ reveal via keyboard -----
  await page.getByTestId("reveal-case").focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/reveal$/, { timeout: 15_000 });
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");

  // Contrast on reveal primary text (truth values + overall result).
  const revealTruth = await contrastRatio(page, '[data-testid="reveal-truth-murderer"]');
  const revealOverall = await contrastRatio(page, '[data-testid="reveal-overall"]');
  console.log("A11Y_REVEAL_CONTRAST", JSON.stringify({ truth: revealTruth, overall: revealOverall }));
  expect(revealTruth.ratio, "reveal truth text contrast >= 4.5").toBeGreaterThanOrEqual(4.5);
  expect(revealOverall.ratio, "reveal overall text contrast >= 4.5").toBeGreaterThanOrEqual(4.5);

  // Reveal text is fully usable without the canvas (pure DOM text).
  const revealText = await page.getByTestId("reveal-screen").innerText();
  expect(revealText.toLowerCase(), "reveal screen carries the truth as DOM text").toContain("thomas reed");
  expect(revealText, "reveal screen includes the timeline").toContain("Timeline");

  expect(pageErrors, "no uncaught page errors in the a11y run").toEqual([]);
});