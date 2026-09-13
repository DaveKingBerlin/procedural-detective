import { expect, test } from "@playwright/test";
import { createPlaythroughViaApi, seedPlaythroughCredentials } from "./helpers";

/**
 * Phase 2 boot smoke — runs with BOTH servers up (backend :8000 migrated,
 * vite preview :4173). Proves, in a real browser over real HTTP(S):
 *   a) the app shell boots with title "Procedural Detective"
 *   b) the frontend -> backend cross-origin API call works (backend connected)
 *   c) the /scene Babylon scene boots (canvas visible + ready message)
 *   d) an unknown route renders the 404 fallback
 *   e) screenshots are captured as transient evidence
 *
 * Phase 6 contract change (authorization): /scene now requires an authorized
 * playthrough (REQUIREMENTS 40.7 — the page may only receive what the current
 * player is allowed to know), so the /scene segment seeds a REAL playthrough
 * credential through the public API first, exactly as the HOME "Start an
 * investigation" panel would.
 */
test("boot smoke: title, backend connected, Babylon scene, 404", async ({ page, request }) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });

  // --- (a) shell boots with the correct title -----------------------------
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveTitle("Procedural Detective");

  // --- (b) backend status: real cross-origin health round-trip -------------
  const statusIndicator = page.getByTestId("backend-status");
  await expect(statusIndicator).toHaveClass(/backend-status--ok/, { timeout: 30_000 });
  const message = page.getByTestId("backend-status-message");
  await expect(message).toHaveText("ok");
  await page.screenshot({ path: "artifacts/screenshots/boot-home.png", fullPage: true });

  // --- (c) Babylon scene boots on /scene (Phase 6: authorized only) --------
  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible();
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-error")).toHaveCount(0);
  await page.screenshot({ path: "artifacts/screenshots/scene.png" });

  // --- (d) unknown route -> 404 fallback -----------------------------------
  await page.goto("/definitely-not-a-real-route", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("not-found")).toBeVisible();
  await page.screenshot({ path: "artifacts/screenshots/404.png" });

  // --- evidence: no uncaught page errors -----------------------------------
  await test.info().attach("page-errors.json", {
    body: JSON.stringify(pageErrors, null, 2),
    contentType: "application/json",
  });
  await test.info().attach("console-errors.json", {
    body: JSON.stringify(consoleErrors, null, 2),
    contentType: "application/json",
  });
  expect(pageErrors, "no uncaught page errors may occur").toEqual([]);
});