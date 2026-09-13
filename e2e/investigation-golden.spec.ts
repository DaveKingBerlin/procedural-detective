import { expect, test } from "@playwright/test";
import { BACKEND_BASE, createPlaythroughViaApi, installLeakListener, seedPlaythroughCredentials } from "./helpers";
import type { PlaythroughCredential } from "./helpers";

/**
 * Phase 6 Q — GOLDEN INVESTIGATION E2E (QA-owned; .rad/policies/evidence.md).
 *
 * Lifecycle: backend :8000 (migrated scratch DB, default dev provider,
 * CORS http://localhost:4173/http://localhost:5173) + vite preview :4173 are
 * started OUTSIDE Playwright via tools/process_guard. The spec seeds the
 * playthrough credential into localStorage under the contract-mandated keys
 * (pd_playthrough_token / pd_playthrough_id) through the PUBLIC API first,
 * then drives the REAL browser flow:
 *
 *   1 open /scene -> investigation-loading -> scene-canvas + scene-ready
 *   2 interact with the kitchen knife object (accessible object button)
 *   3 assert discovery toast
 *   4 evidence panel opens with the knife record
 *   5 close the panel (Escape)
 *   6 interact with the laptop -> email discovered + read (subject/body text)
 *   7 reload -> bootstrap persistence (discovered/read flags) + scene flags
 *   8 network-leak proof: every API response is scanned; none may contain
 *     murdererId/weaponId/crimeTime/solutionProof/token/verifier key paths
 *   9 no truth/reveal UI appears
 *
 * Deterministic: no fixed sleeps — every step waits on a readable condition.
 */

const KNIFE_OBJECT = "kitchen_knife";
const KNIFE_EVIDENCE = "forensic_knife_match_01";
const LAPTOP_OBJECT = "apartment_laptop";

test("golden investigation: knife discovery, panel, persistence, leak scan, no truth UI", async ({
  page,
  request,
}) => {
  // Leak listener BEFORE any navigation: every API response the page receives.
  const leak = installLeakListener(page);
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));

  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);

  // (1) application readiness: loading state resolves to a live scene.
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });

  // (2) interact with the kitchen knife via the focusable object button.
  const knifeButton = page.getByTestId(`object-${KNIFE_OBJECT}`);
  await expect(knifeButton).toBeVisible();
  await knifeButton.click();

  // (3) discovery toast.
  const toast = page.getByTestId("discovery-toast");
  await expect(toast).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("discovery-toast-text")).toContainText("Discovered");

  // (4) evidence panel opens with the knife record.
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");

  // (5) close the panel with Escape.
  await page.keyboard.press("Escape");
  await expect(panel).not.toBeVisible();

  // (7) RELOAD -> bootstrap persistence: discovered + read survive.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("knowledge-summary")).toContainText("Evidence discovered: 1");
  await expect(page.getByTestId("knowledge-summary")).toContainText("Read: 1");
  // The scene's object list flags the knife as discovered.
  await expect(
    page.locator(`[data-testid="object-${KNIFE_OBJECT}"] + .object-discovered`),
  ).toBeVisible();

  // (8) network-leak proof across the whole session.
  console.log("LEAKSCAN", JSON.stringify({ scanned: leak.scanned, matched: leak.matched }));
  await test.info().attach("leak-scan-report.json", {
    body: JSON.stringify(
      { scanned: leak.scanned, matched: leak.matched, forbiddenPaths: ["murdererId", "weaponId", "crimeTime", "solutionProof", "token", "verifier", "canonical", "prompt", "diagnostics"] },
      null,
      2,
    ),
    contentType: "application/json",
  });
  expect(leak.matched, `no forbidden key paths in ${leak.scanned} API responses`).toEqual([]);
  expect(leak.scanned, "at least the bootstrap/read responses must have been scanned").toBeGreaterThan(0);

  // (9) no truth/reveal UI appears anywhere in the rendered page.
  const bodyText = (await page.locator("body").innerText()).toLowerCase();
  for (const forbidden of ["murderer", "solution", "reveal", "answer:", "culprit", "guilty is"]) {
    expect(bodyText, `page text must not contain ${forbidden}`).not.toContain(forbidden);
  }

  await page.screenshot({ path: "artifacts/screenshots/golden-knife-flow.png", fullPage: true });
  expect(pageErrors, "no uncaught page errors").toEqual([]);
});

test("golden investigation: laptop interaction affordance (Phase 6 Q step 6)", async ({
  page,
  request,
}) => {
  const leak = installLeakListener(page);
  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);

  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });

  try {
    // The accessible laptop button must exist so the golden flow can reach the
    // email (Phase 6 M steps 5-6). If it is absent the laptop's asset id is
    // NOT in the frontend asset registry and the object is a non-interactable
    // neutral fallback — a product defect, reproduced here.
    const laptopButton = page.getByTestId(`object-${LAPTOP_OBJECT}`);
    await expect(laptopButton, "laptop must be an interactable object button").toBeVisible({ timeout: 20_000 });
    await laptopButton.click();

    const toast = page.getByTestId("discovery-toast");
    await expect(toast).toBeVisible({ timeout: 15_000 });
    const panel = page.getByTestId("evidence-panel");
    await expect(panel).toBeVisible({ timeout: 15_000 });
    await expect(panel).toContainText("Re: the missing funds");
    await expect(panel).toContainText("We need to talk tonight");
    await page.screenshot({ path: "artifacts/screenshots/golden-laptop-email.png", fullPage: true });
  } finally {
    await page.screenshot({ path: "artifacts/screenshots/golden-laptop-state.png", fullPage: true });
    await test.info().attach("laptop-leak-scan.json", {
      body: JSON.stringify({ scanned: leak.scanned, matched: leak.matched }, null, 2),
      contentType: "application/json",
    });
  }
});