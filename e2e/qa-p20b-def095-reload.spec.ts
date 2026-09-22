import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createPlaythroughViaApi, seedPlaythroughCredentials } from "./helpers";

/**
 * QA-owned DEF-095 real-browser retest (Phase 20 close gate).
 *
 * Hermetic fake stack: backend :8000 (GENERATION_PROVIDER=fake, scratch
 * migrated SQLite under %TEMP%\opencode\qa_p20b), vite preview :4173 serving
 * dist/. Playthrough credential seeded through the PUBLIC API (golden world).
 *
 * Contract under test (DEF-095): a post-accusation/reveal reload of /scene must
 * NEVER dispatch GET /records/* (the backend's frozen "gameplay ends at
 * accusation" gate would answer 409 NOT_PLAYING — failed network requests in a
 * normal player flow). The scene in ACCUSED/REVEALED is restored from the
 * bootstrap as the player-visible world with server-authoritative
 * discovered/read flags. A PLAYING reload MUST still re-hydrate the read
 * records (Phase 18C behavior preserved).
 *
 *   1. PLAYING reload: discover + read knife and laptop records, reload /scene,
 *      assert GET /records/* ARE re-issued and answer WITHOUT 409 (hydration
 *      works while PLAYING) and the discovered/read flags persist.
 *   2. golden accuse -> reveal -> CASE SOLVED (4/4).
 *   3. REVEALED reload: goto /scene?pd-debug-pick=1, assert ZERO failed network
 *      requests (net.failed == [] — no GET /records/* 409), and the scene
 *      still loads the world + discovered flags from the bootstrap.
 */

const KNIFE = "kitchen_knife";
const KNIFE_EVIDENCE = "forensic_knife_match_01";
const LAPTOP = "apartment_laptop";
const EMAIL_EVIDENCE = "email_thomas_01";

function installSessionObservers(page: Page): {
  failed: Array<{ url: string; status: number }>;
  recordReads: Array<{ url: string; status: number }>;
  pageErrors: string[];
} {
  const report: {
    failed: Array<{ url: string; status: number }>;
    recordReads: Array<{ url: string; status: number }>;
    pageErrors: string[];
  } = { failed: [], recordReads: [], pageErrors: [] };
  page.on("pageerror", (err) => report.pageErrors.push(String(err)));
  page.on("response", (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    const status = response.status();
    if (status >= 400) report.failed.push({ url, status });
    if (/\/records\/[^/]+$/.test(url) && response.request().method() === "GET") {
      report.recordReads.push({ url, status });
    }
  });
  return report;
}

async function discoverAndRead(page: Page): Promise<void> {
  // Knife: discover + open the panel (read flag flips server-side).
  const knifeButton = page.getByTestId(`object-${KNIFE}`);
  await expect(knifeButton).toBeVisible({ timeout: 30_000 });
  await knifeButton.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText(/blood on the kitchen knife/i);
  await page.getByTestId("evidence-close").click().catch(() => {});
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(panel).not.toBeVisible();

  // Laptop: discover + read the email.
  const laptopButton = page.getByTestId(`object-${LAPTOP}`);
  await expect(laptopButton).toBeVisible({ timeout: 20_000 });
  await laptopButton.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const emailPanel = page.getByTestId("evidence-panel");
  await expect(emailPanel).toBeVisible({ timeout: 15_000 });
  await expect(emailPanel).toContainText(/Re: the missing funds/i);
  await page.getByTestId("evidence-close").click().catch(() => {});
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(emailPanel).not.toBeVisible();
}

test("DEF-095: PLAYING /scene reload still re-hydrates the read records (2048-era Phase 18C behavior); post-reveal reload fires ZERO failed requests", async ({
  page,
  request,
}) => {
  test.setTimeout(240_000);
  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);

  // ---- clean PLAYING session, discover + read two records -----------------
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await discoverAndRead(page);
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 2 evidence items", { timeout: 20_000 });

  // ---- PLAYING reload: record hydration MUST still run (Phase 18C) --------
  const playingObservers = installSessionObservers(page);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 2 evidence items", { timeout: 20_000 });
  // The bootstrap carries both read ids -> the PLAYING gate permits hydration:
  // GET /records/* MUST be re-issued and MUST NOT answer 409.
  await page.waitForTimeout(1200);
  expect(playingObservers.recordReads.length, "PLAYING reload re-hydrates the read records").toBeGreaterThanOrEqual(2);
  for (const read of playingObservers.recordReads) {
    expect(read.status, `PLAYING record read answered without a hard failure: ${read.url}`).toBeLessThan(400);
  }
  // The discovered/read flags persist from the bootstrap.
  await expect(page.getByTestId(`object-discovered-${KNIFE}`)).toBeVisible();
  await expect(page.locator(`[data-testid="discovered-entry-${KNIFE_EVIDENCE}"]`)).toContainText("· read");
  expect(playingObservers.pageErrors, "no uncaught page errors while PLAYING").toEqual([]);

  // ---- golden accusation -> reveal -> CASE SOLVED ---------------------------
  const accusationOpen = page.getByTestId("accusation-open");
  await expect(accusationOpen).toBeVisible();
  await accusationOpen.click();
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("accusation-option-murdererId-thomas_reed").check();
  await page.getByTestId("accusation-option-motiveId-cover_up_embezzlement").check();
  await page.getByTestId("accusation-option-weaponId-kitchen_knife").check();
  await page.getByTestId("accusation-time").fill("22:17");
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
  for (const dim of ["who", "why", "weapon", "when"]) {
    await expect(page.getByTestId(`reveal-dimension-${dim}`)).toContainText("Correct");
  }
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");

  // ---- REVEALED reload of /scene: ZERO failed network requests -------------
  const revealedObservers = installSessionObservers(page);
  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-error")).toHaveCount(0);
  await page.waitForTimeout(1500);
  // The scene STILL loads the world + discovered flags (server-authoritative
  // bootstrap), while the record-read hydration is fully suppressed.
  await expect(page.getByTestId(`object-discovered-${KNIFE}`)).toBeVisible();
  await expect(page.locator(`[data-testid="discovered-entry-${KNIFE_EVIDENCE}"]`)).toContainText("· read");
  await expect(page.locator(`[data-testid="discovered-entry-${EMAIL_EVIDENCE}"]`)).toContainText("· read");
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 2 evidence items", { timeout: 15_000 });
  expect(revealedObservers.recordReads, "REVEALED reload must NEVER dispatch GET /records/*").toEqual([]);
  expect(revealedObservers.failed, `post-reveal reload: zero failed network requests (${revealedObservers.failed.length})`).toEqual([]);
  expect(revealedObservers.pageErrors, "no uncaught page errors while REVEALED").toEqual([]);
});