import { expect, test } from "@playwright/test";
import type { Page, APIRequestContext } from "@playwright/test";
import { installLeakListener, seedPlaythroughCredentials } from "./helpers";

/**
 * PHASE 15 — DURABLE EVIDENCE CAPTURE (QA-owned; .rad/policies/evidence.md).
 *
 * Captures the polished judging screenshots promoted to screenshots/evidence/
 * (phase15-*): OFFICE and MANSION generated worlds with the environment
 * notice, the discovered-evidence caption overlay, and the
 * accusation/reveal transitions. Uses the same PUBLIC-API handshake as the
 * standing showcase gates and the production SPA on :4173 + QA backend :8000.
 *
 * NOTE (DEF-072, CLOSED by QA retest): a live session NOW flips the
 * world-object `discovered`/`read` flags immediately when evidence is found
 * (applyKnowledgeToSceneModel merge wired through InvestigationSession +
 * scene.tsx applyFeedback — e2e/phase15-def072-live.spec.ts asserts the
 * markers + caption BEFORE any reload). This capture spec intentionally keeps
 * reloading before the screenshot so the durable evidence also exercises the
 * server-persisted state a reviewer revisiting the case sees.
 */
const BACKEND_BASE = "http://localhost:8000";

const OFFICE_PROMPT =
  "A financial crime in a company office. The killer used a letter opener " +
  "at the workplace. A heavy award is on the desk.";
const MANSION_PROMPT =
  "An inheritance dispute in a mansion. A valuable antique ceremonial " +
  "letter opener and a watch are in the study.";

async function createShowcasePlaythrough(
  request: APIRequestContext,
  prompt: string,
): Promise<{ playthroughId: string; playthroughToken: string; environmentId: string }> {
  const sessionRes = await request.post(`${BACKEND_BASE}/api/v1/sessions/anonymous`);
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();

  const caseRes = await request.post(`${BACKEND_BASE}/api/v1/cases`, {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt, difficulty: "medium" },
  });
  expect(caseRes.status(), "create case").toBe(201);
  const created = await caseRes.json();
  expect(created.status, "dev provider publishes the showcase prompt").toBe("PUBLISHED");

  const ptRes = await request.post(
    `${BACKEND_BASE}/api/v1/cases/${created.caseId}/versions/1/playthroughs`,
    { headers: { Authorization: `Bearer ${created.creatorAccessToken}` } },
  );
  expect(ptRes.status(), "create playthrough").toBe(201);
  const pt = await ptRes.json();

  const bootRes = await request.get(
    `${BACKEND_BASE}/api/v1/playthroughs/${pt.playthroughId}/investigation`,
    { headers: { Authorization: `Bearer ${pt.playthroughAccessToken}` } },
  );
  expect(bootRes.status(), "investigation bootstrap").toBe(200);
  const boot = await bootRes.json();
  return {
    playthroughId: pt.playthroughId,
    playthroughToken: pt.playthroughAccessToken,
    environmentId: boot.scene.environmentId,
  };
}

async function openScene(
  page: Page,
  request: APIRequestContext,
  kit: { key: string; canonical: string; prompt: string },
): Promise<void> {
  const cred = await createShowcasePlaythrough(request, kit.prompt);
  expect(cred.environmentId, `${kit.key}: bootstrap environmentId`).toBe(kit.key);
  await seedPlaythroughCredentials(page, { playthroughId: cred.playthroughId, playthroughToken: cred.playthroughToken });
  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("environment-notice")).toContainText(`Environment: ${kit.canonical}`, { timeout: 15_000 });
  await page.waitForTimeout(1200);
}

async function discoverKnife(page: Page): Promise<void> {
  const knife = page.getByTestId("object-kitchen_knife");
  await expect(knife).toBeVisible({ timeout: 20_000 });
  await knife.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");
}

async function closeEvidencePanel(page: Page): Promise<void> {
  await page.getByTestId("discovery-toast-dismiss").click({ timeout: 3_000 }).catch(() => {});
  const panel = page.getByTestId("evidence-panel");
  const closeBtn = page.getByTestId("evidence-close");
  for (let attempt = 0; attempt < 3; attempt++) {
    await closeBtn.click({ timeout: 3_000 }).catch(() => {});
    try {
      await expect(panel).not.toBeVisible({ timeout: 8_000 });
      return;
    } catch (closeError) {
      if (attempt === 2) throw closeError;
    }
  }
}

// ---- OFFICE ---------------------------------------------------------------

test("Phase 15 evidence: office scene + discovered-evidence caption overlay", async ({ page, request }) => {
  test.setTimeout(180_000);
  const leak = installLeakListener(page);
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));

  await openScene(page, request, { key: "office", canonical: "Office", prompt: OFFICE_PROMPT });
  await page.screenshot({ path: "../screenshots/evidence/phase15-office-scene.png", fullPage: false });
  await page.screenshot({ path: "artifacts/screenshots/phase15-office-scene.png", fullPage: false });

  await discoverKnife(page);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("object-caption-kitchen_knife")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("object-discovered-kitchen_knife")).toBeVisible({ timeout: 10_000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: "../screenshots/evidence/phase15-office-evidence.png", fullPage: false });
  await page.screenshot({ path: "artifacts/screenshots/phase15-office-evidence.png", fullPage: false });

  expect(pageErrors, "no uncaught page errors").toEqual([]);
  expect(leak.matched, "leak listener clean").toEqual([]);
});

test("Phase 15 evidence: office accusation + reveal transitions", async ({ page, request }) => {
  test.setTimeout(180_000);
  const leak = installLeakListener(page);
  await openScene(page, request, { key: "office", canonical: "Office", prompt: OFFICE_PROMPT });
  await discoverKnife(page);
  await closeEvidencePanel(page);
  // The raw leak listener scans the pre-accusation session (the deliberate
  // accusation echo would trip the naive key scanner; the frozen bypass is
  // exercised in the phase-15 showcase journeys).
  expect(leak.matched, "leak listener clean before accusation").toEqual([]);

  await page.getByTestId("accusation-open").click();
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: "../screenshots/evidence/phase15-office-accusation.png", fullPage: false });
  await page.screenshot({ path: "artifacts/screenshots/phase15-office-accusation.png", fullPage: false });

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
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await page.waitForTimeout(800);
  await page.screenshot({ path: "../screenshots/evidence/phase15-office-reveal.png", fullPage: false });
  await page.screenshot({ path: "artifacts/screenshots/phase15-office-reveal.png", fullPage: false });
});

// ---- MANSION --------------------------------------------------------------

test("Phase 15 evidence: mansion scene + discovered-evidence caption overlay", async ({ page, request }) => {
  test.setTimeout(180_000);
  const leak = installLeakListener(page);
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));

  await openScene(page, request, { key: "mansion", canonical: "Mansion", prompt: MANSION_PROMPT });
  await page.screenshot({ path: "../screenshots/evidence/phase15-mansion-scene.png", fullPage: false });
  await page.screenshot({ path: "artifacts/screenshots/phase15-mansion-scene.png", fullPage: false });

  await discoverKnife(page);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("object-caption-kitchen_knife")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: "../screenshots/evidence/phase15-mansion-evidence.png", fullPage: false });
  await page.screenshot({ path: "artifacts/screenshots/phase15-mansion-evidence.png", fullPage: false });

  expect(pageErrors, "no uncaught page errors").toEqual([]);
  expect(leak.matched, "leak listener clean").toEqual([]);
});

test("Phase 15 evidence: mansion accusation + reveal transitions", async ({ page, request }) => {
  test.setTimeout(180_000);
  const leak = installLeakListener(page);
  await openScene(page, request, { key: "mansion", canonical: "Mansion", prompt: MANSION_PROMPT });
  await discoverKnife(page);
  await closeEvidencePanel(page);
  expect(leak.matched, "leak listener clean before accusation").toEqual([]);

  await page.getByTestId("accusation-open").click();
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
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await page.waitForTimeout(800);
  await page.screenshot({ path: "../screenshots/evidence/phase15-mansion-reveal.png", fullPage: false });
  await page.screenshot({ path: "artifacts/screenshots/phase15-mansion-reveal.png", fullPage: false });
});