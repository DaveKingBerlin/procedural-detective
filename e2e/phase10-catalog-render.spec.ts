import { expect, test } from "@playwright/test";
import { createPlaythroughViaApi, installLeakListener, seedPlaythroughCredentials } from "./helpers";

/**
 * Phase 10 — BROWSER GOLDEN THROUGH THE ASSET ORACLE (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * The Phase 10 DoD viewed from the browser: a published golden (all PROP_*
 * catalog ids) must render with ZERO fallback renders and ZERO catalog errors.
 *
 *  1. /scene with a real playthrough -> scene-ready.
 *  2. `assets-notice` (any unknownAsset object) is ABSENT — 0 fallback renders.
 *  3. `catalog-error` (bundled-catalog validation failure) is ABSENT.
 *  4. Every catalog label appears in the player-safe object list for the
 *     published placements: Kitchen knife, Letter opener, Scissors, Laptop,
 *     Table, Door, Lamp, Vase, Victim (catalog labels are the only label
 *     source — non-interactable env objects still show their catalog label).
 *  5. Interactable catalog assets are buttons (knife/laptop/opener/scissors);
 *     non-interactable env objects (table/door/lamp/vase/victim) are NOT
 *     buttons (per the manifest's interactable:false).
 *  6. Leak scan over the whole session: 0 truth markers.
 *
 * Setup: production build (backend :8000 on a migrated scratch DB to head
 * 0004 via tools/process_guard + vite preview :4173 serving dist/,
 * CORS_ALLOWED_ORIGINS=http://localhost:4173,http://localhost:5173). The
 * playthrough credential is seeded through the PUBLIC API (helpers).
 */

const CATALOG_LABELS = [
  "Kitchen knife",
  "Letter opener",
  "Scissors",
  "Laptop",
  "Table",
  "Door",
  "Lamp",
  "Vase",
  "Victim",
];

// interactable:true in the v1 manifest -> DOM buttons.
const INTERACTABLE_OBJECT_IDS = [
  "kitchen_knife",
  "letter_opener",
  "scissors",
  "apartment_laptop",
];
// interactable:false in the v1 manifest -> plain list spans, never buttons.
const NON_INTERACTABLE_OBJECT_IDS = [
  "apartment_table",
  "apartment_door",
  "apartment_lamp",
  "vase_01",
  "victim_body_placeholder",
];

test("Phase 10: published golden renders 0 fallback through the catalog (labels, notices, leaks)", async ({
  page,
  request,
}) => {
  const leak = installLeakListener(page);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });

  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);

  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });

  // -- 2/3: zero fallback renders, zero catalog errors ----------------------
  await expect(page.getByTestId("assets-notice")).toHaveCount(0, { timeout: 10_000 });
  await expect(page.getByTestId("catalog-error")).toHaveCount(0);

  // -- 4: every catalog label present in the object list --------------------
  const listText = (await page.getByTestId("scene-objects").innerText()).toLowerCase();
  for (const label of CATALOG_LABELS) {
    expect(listText, `object list must carry the catalog label "${label}"`).toContain(label.toLowerCase());
  }

  // -- 5: interactability comes from the manifest ---------------------------
  for (const objectId of INTERACTABLE_OBJECT_IDS) {
    const button = page.getByTestId(`object-${objectId}`);
    await expect(button, `${objectId} must be an interactable button (catalog)`).toBeVisible({ timeout: 15_000 });
    expect(await button.evaluate((el) => el.tagName), `${objectId} tag`).toBe("BUTTON");
  }
  for (const objectId of NON_INTERACTABLE_OBJECT_IDS) {
    const label = page.getByTestId(`object-label-${objectId}`);
    await expect(label, `${objectId} must show its catalog label`).toBeVisible({ timeout: 15_000 });
    const buttonCount = await page.getByTestId(`object-${objectId}`).count();
    expect(buttonCount, `${objectId} must NOT be a button (catalog interactable:false)`).toBe(0);
  }

  // -- 6: leak scan ----------------------------------------------------------
  console.log("P10_CATALOG_RENDER_LEAKSCAN", JSON.stringify({ scanned: leak.scanned, matched: leak.matched }));
  await test.info().attach("p10-catalog-render-leak.json", {
    body: JSON.stringify({ scanned: leak.scanned, matched: leak.matched }, null, 2),
    contentType: "application/json",
  });
  expect(leak.matched, `no forbidden truth keys in ${leak.scanned} API responses`).toEqual([]);
  expect(leak.scanned, "bootstrap responses must have been scanned").toBeGreaterThan(0);

  expect(pageErrors, "no uncaught page errors").toEqual([]);
  expect(consoleErrors, "no console errors").toEqual([]);

  await page.screenshot({ path: "artifacts/screenshots/p10-catalog-golden-render.png", fullPage: true });
  console.log("P10_CATALOG_RENDER_TRANSCRIPT", JSON.stringify({
    sceneReady: true,
    assetsNoticeCount: 0,
    catalogErrorCount: 0,
    labelsShown: CATALOG_LABELS.length,
    interactableButtons: INTERACTABLE_OBJECT_IDS,
    nonInteractableSpans: NON_INTERACTABLE_OBJECT_IDS,
    leakScanned: leak.scanned,
    leakMatched: leak.matched.length,
  }));
});