import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import { BACKEND_BASE, createPlaythroughViaApi, seedPlaythroughCredentials } from "./helpers";

/**
 * Phase 8 Q — RESPONSIVE/BROWSER CHECK (QA-owned).
 *
 * Same production servers as the golden journey (backend :8000 migrated +
 * vite preview :4173). Asserts the landing, /new, /scene, /accuse and /reveal
 * render correctly at (a) Chromium desktop 1440x900 and (b) a laptop-ish
 * 1024x700 viewport:
 *   - NO horizontal scroll on the landing / prompt / reveal (the scene canvas
 *     shell scales via width:100%/aspect-ratio CSS),
 *   - no button/text is clipped (key controls stay fully inside the viewport).
 *
 * QA only reports; fixes are filed as defects only for obvious blockers.
 */

const VIEWPORTS: Array<{ name: string; width: number; height: number }> = [
  { name: "desktop-1440x900", width: 1440, height: 900 },
  { name: "laptop-1024x700", width: 1024, height: 700 },
];

/** Drive the playthrough into the REVEALED state through the public API so the
 *  full reveal screen (truth table) renders. */
async function createRevealedState(page: Page, request: APIRequestContext): Promise<void> {
  const cred = await createPlaythroughViaApi(request);
  const res = await request.post(
    `${BACKEND_BASE}/api/v1/playthroughs/${cred.playthroughId}/accusation`,
    {
      headers: { Authorization: `Bearer ${cred.playthroughToken}` },
      data: {
        murdererId: "thomas_reed",
        motiveId: "cover_up_embezzlement",
        weaponId: "kitchen_knife",
        crimeTime: "22:17:00",
      },
    },
  );
  expect(res.status(), "accusation via API").toBe(200);
  await seedPlaythroughCredentials(page, cred);
}

/** Assert the page has no horizontal scroll and that an element is fully inside
 *  the viewport (not clipped). */
async function expectNoHorizontalScroll(page: Page, label: string): Promise<void> {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(
    scrollWidth,
    `${label}: no horizontal scroll (scrollWidth ${scrollWidth} <= viewport ${innerWidth})`,
  ).toBeLessThanOrEqual(innerWidth + 2);
  await test.info().attach(`responsive-${label.replace(/[^a-z0-9]+/gi, "-")}.json`, {
    body: JSON.stringify({ scrollWidth, innerWidth }),
    contentType: "application/json",
  });
}

async function expectWithinViewport(page: Page, selector: string, label: string): Promise<void> {
  const box = await page.locator(selector).boundingBox();
  expect(box, `${label}: bounding box exists`).not.toBeNull();
  const vp = page.viewportSize();
  expect(vp, "viewport size").not.toBeNull();
  expect(
    box!.x >= -1 && box!.y >= -1 && box!.x + box!.width <= vp!.width + 1 && box!.y + box!.height <= vp!.height + 1,
    `${label}: element fully inside the viewport (box ${JSON.stringify(box)}, viewport ${vp!.width}x${vp!.height})`,
  ).toBe(true);
}

const ROUTES_TO_VISIT = [
  { path: "/", name: "landing" },
  { path: "/new", name: "new" },
  { path: "/accuse", name: "accuse" },
  { path: "/reveal", name: "reveal" },
];

for (const vp of VIEWPORTS) {
  test(`Phase 8 Q responsive ${vp.name}: landing/new/accuse/reveal no horizontal scroll + reveal and accusation render`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });

    // A single revealed playthrough gives valid local state for /accuse and /reveal.
    await createRevealedState(page, request);
    // If this is the rendered state, accuse shows the already-submitted view;
    // a fresh playthrough would show the panel. Both are "rendered fine".
    const pageErrors: string[] = [];
    page.on("pageerror", (err) => pageErrors.push(String(err)));

    // Landing: instant pitch + both actions visible, no horizontal scroll.
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(
      page.getByText("Describe a crime. AI builds a logically solvable 3D investigation."),
    ).toBeVisible();
    await expect(page.getByTestId("new-investigation")).toBeVisible();
    await expect(page.getByTestId("try-demo")).toBeVisible();
    await expectWithinViewport(page, '[data-testid="new-investigation"]', `${vp.name}/landing new-investigation`);
    await expectWithinViewport(page, '[data-testid="try-demo"]', `${vp.name}/landing try-demo`);
    await expectNoHorizontalScroll(page, `${vp.name}/landing`);
    await page.screenshot({ path: `artifacts/screenshots/responsive-${vp.name}-landing.png`, fullPage: true });

    // /new: prompt form + actions visible, no horizontal scroll.
    await page.goto("/new", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("prompt-form")).toBeVisible();
    await expect(page.getByTestId("generate-case")).toBeVisible();
    await expect(page.getByTestId("use-example-prompt")).toBeVisible();
    await expectWithinViewport(page, '[data-testid="prompt-input"]', `${vp.name}/new prompt-input`);
    await expectWithinViewport(page, '[data-testid="generate-case"]', `${vp.name}/new generate-case`);
    await expectNoHorizontalScroll(page, `${vp.name}/new`);
    await page.screenshot({ path: `artifacts/screenshots/responsive-${vp.name}-new.png`, fullPage: true });

    // /scene: canvas renders and scales inside the viewport.
    await page.goto("/scene", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
    const canvasBox = await page.getByTestId("scene-canvas").boundingBox();
    expect(canvasBox, "scene canvas shell bounding box").not.toBeNull();
    expect(
      canvasBox!.width <= vp.width + 1 && canvasBox!.height <= vp.height + 1,
      `${vp.name}/scene canvas scales into the viewport (${JSON.stringify(canvasBox)})`,
    ).toBe(true);
    await expectNoHorizontalScroll(page, `${vp.name}/scene`);
    await page.screenshot({ path: `artifacts/screenshots/responsive-${vp.name}-scene.png`, fullPage: true });

    // /accuse: panel + form visible, no horizontal scroll.
    await page.goto("/accuse", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("accusation-panel").or(page.getByTestId("accusation-already-submitted"))).toBeVisible({
      timeout: 30_000,
    });
    await expectNoHorizontalScroll(page, `${vp.name}/accuse`);
    await page.screenshot({ path: `artifacts/screenshots/responsive-${vp.name}-accuse.png`, fullPage: true });

    // /reveal: full reveal screen renders its truth values, no horizontal scroll.
    await page.goto("/reveal", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
    await expect(page.getByTestId("reveal-truth-weapon")).toHaveText("Kitchen Knife");
    await expectNoHorizontalScroll(page, `${vp.name}/reveal`);
    await expectWithinViewport(page, '[data-testid="reveal-truth-murderer"]', `${vp.name}/reveal truth-murderer`);
    await expectWithinViewport(page, '[data-testid="reveal-score"]', `${vp.name}/reveal score`);
    await page.screenshot({ path: `artifacts/screenshots/responsive-${vp.name}-reveal.png`, fullPage: true });

    expect(pageErrors, "no uncaught page errors in the responsive run").toEqual([]);
  });
}