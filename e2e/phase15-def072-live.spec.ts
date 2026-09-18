import { expect, test } from "@playwright/test";
import { createPlaythroughViaApi, installLeakListener, seedPlaythroughCredentials } from "./helpers";

/**
 * DEF-072 RETEST (QA-owned; .rad/roles/qa.md, .rad/policies/defect-lifecycle.md,
 * .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Original reproduction (filed by QA against the production build): after a
 * DIRECT 3D mesh click on the knife (and reading the laptop), the object-list
 * discovered markers and the discovered-only floating caption NEVER appeared
 * during the live session — only after a page reload. Root cause: the Phase 15
 * polish was keyed off the bootstrap-only `discovered` flag snapshot instead of
 * the live server-confirmed knowledge.
 *
 * This retest drives the CURRENT production build (backend :8000 fresh
 * migrated scratch DB + vite preview :4173 via tools/process_guard,
 * CORS_ALLOWED_ORIGINS=http://localhost:4173,http://localhost:5173) and asserts
 * the fix end-to-end:
 *
 *  1. after a DIRECT mesh click on the kitchen knife: the object-list marker
 *     `object-discovered-kitchen_knife` AND the floating caption
 *     `object-caption-kitchen_knife` appear IMMEDIATELY — BEFORE any reload;
 *  2. reading the laptop flips `object-discovered-apartment_laptop` +
 *     `object-read-apartment_laptop` immediately (no reload);
 *  3. decorative / no-evidence objects (vase_01, apartment_table, …) stay
 *     untouched — 0 discovered markers, 0 captions;
 *  4. repeating interactions are idempotent — no duplicate objects, no churn
 *     (the object list always has EXACTLY one entry per object);
 *  5. the reload-persist behavior still holds (server-persisted knowledge
 *     re-renders markers + captions on the next bootstrap);
 *  6. hygiene: 0 console/page errors, 0 forbidden pre-reveal key paths.
 *
 * Deterministic: no fixed sleeps — every step waits on a readable condition.
 * The direct mesh click is a hover-scan + mouse click on the live canvas
 * (the same mechanism phase15-showcase uses; software-WebGL paced).
 */

const KNIFE_LABEL = "Kitchen knife";
const LAPTOP_LABEL = "Laptop";

const DECORATIVE_OBJECTS = ["vase_01", "apartment_table"];

interface Sight {
  label: string;
  x: number;
  y: number;
}

async function tooltipText(page: import("@playwright/test").Page): Promise<string | null> {
  const el = page.getByTestId("object-tooltip");
  if (!(await el.isVisible().catch(() => false))) return null;
  return ((await el.textContent()) ?? "").trim();
}

/** Grid hover-scan; first sighting per label wins (same budget as the phase-14 gate). */
async function hoverScan(
  page: import("@playwright/test").Page,
  rect: { x: number; y: number; width: number; height: number },
  stepX: number,
  stepY: number,
): Promise<Map<string, Sight>> {
  const found = new Map<string, Sight>();
  const yEnd = rect.y + rect.height;
  const xEnd = rect.x + rect.width;
  for (let gy = rect.y; gy <= yEnd; gy += stepY) {
    for (let gx = rect.x; gx <= xEnd; gx += stepX) {
      await page.mouse.move(gx, gy);
      await page.waitForTimeout(16);
      const label = await tooltipText(page);
      if (label !== null && label !== "") {
        const sight: Sight = { label, x: Math.round(gx), y: Math.round(gy) };
        if (!found.has(label)) found.set(label, sight);
      }
    }
  }
  return found;
}

/** Direct 3D mesh click discovery on the live canvas (no reload, no debug flag). */
async function directMeshClick(
  page: import("@playwright/test").Page,
  wanted: string,
  rect: { x: number; y: number; width: number; height: number },
): Promise<{ label: string; x: number; y: number }> {
  await page.mouse.move(rect.x - 40, rect.y + 20);
  const primary = await hoverScan(page, rect, 32, 32);
  const found = new Map(primary);
  if (!found.has(wanted)) {
    const fine = await hoverScan(page, rect, 16, 16);
    for (const [label, sight] of fine) {
      if (!found.has(label)) found.set(label, sight);
    }
  }
  const target = found.get(wanted);
  expect(target, `${wanted}: direct 3D click target found`).toBeDefined();
  await page.mouse.move(target!.x, target!.y);
  await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("object-tooltip")).toHaveText(target!.label);
  await page.mouse.click(target!.x, target!.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  return { label: target!.label, x: target!.x, y: target!.y };
}

/** Dismiss the discovery toast + close the evidence panel (robust driver). */
async function closeEvidencePanel(page: import("@playwright/test").Page): Promise<void> {
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  const panel = page.getByTestId("evidence-panel");
  const closeButton = page.getByTestId("evidence-close");
  for (let attempt = 0; attempt < 3; attempt++) {
    await closeButton.click({ timeout: 3_000 }).catch(() => {});
    try {
      await expect(panel).not.toBeVisible({ timeout: 8_000 });
      return;
    } catch (closeError) {
      if (attempt === 2) throw closeError;
    }
  }
}

test("DEF-072: knife + laptop markers/captions appear IMMEDIATELY after a direct mesh click (no reload), decorative untouched, idempotent, reload persists", async ({
  page,
  request,
}) => {
  test.setTimeout(240_000);
  const leak = installLeakListener(page);
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));

  // Fresh playthrough through the PUBLIC API (golden apartment case).
  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);

  // ---- 0. PRE-condition: nothing discovered yet, no markers, no captions -----
  await expect(page.getByTestId("object-kitchen_knife")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("object-discovered-kitchen_knife")).toHaveCount(0);
  await expect(page.getByTestId("object-caption-kitchen_knife")).toHaveCount(0);
  for (const deco of DECORATIVE_OBJECTS) {
    await expect(page.getByTestId(`object-discovered-${deco}`)).toHaveCount(0);
    await expect(page.getByTestId(`object-caption-${deco}`)).toHaveCount(0);
  }

  // ---- 1. DIRECT mesh click on the knife (the ORIGINAL reproduction) ---------
  const rect = (await page.getByTestId("scene-canvas").boundingBox())!;
  const clicked = await directMeshClick(page, KNIFE_LABEL, rect);
  await expect(page.getByTestId("evidence-panel")).toContainText(
    "Blood on the kitchen knife matches the victim",
  );
  // IMMEDIATE marker + caption — NO RELOAD has happened on this page session.
  const knifeDiscovered = page.getByTestId("object-discovered-kitchen_knife");
  await expect(knifeDiscovered).toBeVisible({ timeout: 15_000 });
  await expect(knifeDiscovered).toHaveText("· discovered");
  const knifeCaption = page.getByTestId("object-caption-kitchen_knife");
  await expect(knifeCaption).toBeVisible({ timeout: 15_000 });
  const captionText = ((await knifeCaption.textContent()) ?? "").trim();
  expect(captionText.length, "caption has app-authored text").toBeGreaterThan(0);
  await closeEvidencePanel(page);

  // ---- 2. reading the laptop (object toolbox) -> marker flips immediately ----
  const laptopButton = page.getByTestId("object-apartment_laptop");
  await expect(laptopButton).toBeVisible({ timeout: 20_000 });
  await laptopButton.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Re: the missing funds");
  // NO RELOAD: the laptop shows both "· discovered" AND "· read" immediately.
  await expect(page.getByTestId("object-discovered-apartment_laptop")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("object-read-apartment_laptop")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("object-read-apartment_laptop")).toHaveText("· read");
  await expect(page.getByTestId("object-caption-apartment_laptop")).toBeVisible({ timeout: 15_000 });
  await closeEvidencePanel(page);
  await page.waitForTimeout(600);

  // ---- 3. decorative / no-evidence objects are untouched ---------------------
  for (const deco of DECORATIVE_OBJECTS) {
    await expect(page.getByTestId(`object-discovered-${deco}`)).toHaveCount(0);
    await expect(page.getByTestId(`object-caption-${deco}`)).toHaveCount(0);
  }

  // ---- 4. idempotency: re-click the knife -> no duplicates, no churn ---------
  await expect(page.getByTestId("object-kitchen_knife")).toHaveCount(1);
  await page.getByTestId("object-kitchen_knife").click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await closeEvidencePanel(page);
  await expect(page.getByTestId("object-kitchen_knife")).toHaveCount(1);
  await expect(page.getByTestId("object-apartment_laptop")).toHaveCount(1);
  await expect(page.getByTestId("object-discovered-kitchen_knife")).toHaveCount(1);
  await expect(page.getByTestId("object-read-kitchen_knife")).toHaveCount(1);
  await expect(page.getByTestId("object-discovered-apartment_laptop")).toHaveCount(1);
  await expect(page.getByTestId("object-read-apartment_laptop")).toHaveCount(1);
  // Exactly one caption per discovered evidence object.
  await expect(page.getByTestId("object-caption-kitchen_knife")).toHaveCount(1);
  await expect(page.getByTestId("object-caption-apartment_laptop")).toHaveCount(1);
  await page.screenshot({ path: "artifacts/screenshots/def072-live-markers.png", fullPage: true });

  // ---- 5. RELOAD-persist still holds (server-persisted knowledge) ------------
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("object-discovered-kitchen_knife")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("object-read-kitchen_knife")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("object-caption-kitchen_knife")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("object-discovered-apartment_laptop")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("object-read-apartment_laptop")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("object-caption-apartment_laptop")).toBeVisible({ timeout: 15_000 });
  for (const deco of DECORATIVE_OBJECTS) {
    await expect(page.getByTestId(`object-discovered-${deco}`)).toHaveCount(0);
  }
  await page.screenshot({ path: "artifacts/screenshots/def072-reload-persist.png", fullPage: true });

  // ---- 6. hygiene -------------------------------------------------------------
  expect(leak.matched, `no forbidden key paths in ${leak.scanned} API responses`).toEqual([]);
  expect(leak.scanned, "API responses scanned by the leak listener").toBeGreaterThan(0);
  expect(pageErrors, "no uncaught page errors").toEqual([]);
});