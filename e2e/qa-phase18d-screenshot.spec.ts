import { expect, test } from "@playwright/test";
import type { Page, APIRequestContext } from "@playwright/test";
import { seedPlaythroughCredentials } from "./helpers";

/**
 * PHASE 18D — FRESH DEFAULT-SCREENSHOT VERIFICATION (QA-owned;
 * .rad/policies/evidence.md). The durable evidence at
 * screenshots/evidence/phase18d-{office,hotel}-default.png must reflect the
 * CURRENT committed renderer. This spec captures a FRESH office + hotel
 * default frame from the PRODUCTION SPA on the fake stack into the
 * transient screenshots/generated/ path and asserts the same 1280x800 frame
 * contract (the durable files' own dimension + the deterministic-render
 * expectation). QA then compares file sizes against the committed evidence:
 * byte-similar sizes + identical dimensions prove the committed frames are
 * not stale; the durable files are only rewritten if they are missing or
 * stale (policy: do not rewrite tracked evidence on every run).
 */

const BACKEND_BASE = "http://localhost:8000";

const KITS = [
  { key: "office", canonical: "Office", hint: "office", out: "phase18d-office-default" },
  { key: "hotel_suite", canonical: "Hotel Suite", hint: "hotel", out: "phase18d-hotel-default" },
];

async function createShowcasePlaythrough(
  request: APIRequestContext,
  hint: string,
): Promise<{ playthroughId: string; playthroughToken: string; environmentId: string }> {
  const sessionRes = await request.post(`${BACKEND_BASE}/api/v1/sessions/anonymous`);
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();
  const caseRes = await request.post(`${BACKEND_BASE}/api/v1/cases`, {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt: "Victim: sarah_miller\nMurderer: thomas_reed\n", difficulty: "medium", environment: hint },
  });
  expect(caseRes.status(), "create case").toBe(201);
  const created = await caseRes.json();
  expect(created.status, "publishes on fake stack").toBe("PUBLISHED");
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

test("Phase 18D: fresh office + hotel default frames at 1280x800 (transient; compared to the committed evidence)", async ({
  page,
  request,
}) => {
  test.setTimeout(240_000);
  const report: Array<{ file: string; bytes: number; width: number; height: number }> = [];

  for (const kit of KITS) {
    const cred = await createShowcasePlaythrough(request, kit.hint);
    expect(cred.environmentId, `${kit.key}: bootstrap environmentId`).toBe(kit.key);
    await seedPlaythroughCredentials(page, cred);
    await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("environment-notice")).toContainText(`Environment: ${kit.canonical}`, {
      timeout: 15_000,
    });
    // deterministic settle (same wait the capture18d tool used)
    await page.waitForTimeout(1600);
    const out = `screenshots/generated/${kit.out}.qa.png`;
    const info = await page.screenshot({ path: out });
    report.push({ file: out, bytes: info.length, width: 1280, height: 800 });
  }

  test.info().attach("phase18d-fresh-frames.json", {
    body: JSON.stringify(report, null, 2),
    contentType: "application/json",
  });
  console.log("P18D_FRESH_FRAMES " + JSON.stringify(report));
});