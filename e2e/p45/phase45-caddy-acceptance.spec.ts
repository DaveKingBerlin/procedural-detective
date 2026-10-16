import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Phase 45 §39 — PRODUCTION-TOPOLOGY BROWSER ACCEPTANCE (QA-owned).
 *
 * Topology: cold browser context -> SPA through the REAL Caddy HTTPS edge
 * `https://enshrouded-server` (docker-compose.prod.yml + lan + debug,
 * GENERATION_PROVIDER=fake) -> uvicorn backend rebuilt from the Phase 45
 * working tree. LOCAL-only ignoreHTTPSErrors (Caddy internal root CA).
 *
 * Exercises one MEDIUM and one HARD case:
 *   - cold context (no storage)
 *   - SPA cold load through Caddy; the /scene route lazily loads its chunk
 *   - Investigation scene non-blank (live WebGL pixels)
 *   - object inspection -> player-visible evidence/discovery content
 *   - witness interview -> usable answer
 *   - accusation + reveal (CASE SOLVED)
 *   - /health responsive through the edge
 *   - zero uncaught frontend errors
 *
 * No fixed sleeps: every wait is an application-readiness condition.
 */

interface Handshake {
  caseId: string;
  creatorAccessToken: string;
  playthroughId: string;
  playthroughAccessToken: string;
  createdStatus: string;
}

const PROMPT =
  "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\nWeapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n";

async function startCase(
  request: APIRequestContext,
  difficulty: string,
): Promise<Handshake> {
  const sessionRes = await request.post("/api/v1/sessions/anonymous");
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();

  const caseRes = await request.post("/api/v1/cases", {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt: PROMPT, difficulty },
  });
  expect(caseRes.status(), `create ${difficulty} case`).toBe(201);
  const created = await caseRes.json();
  expect(created.status, `${difficulty} fake-provider case publishes`).toBe(
    "PUBLISHED",
  );

  const ptRes = await request.post(
    `/api/v1/cases/${created.caseId}/versions/1/playthroughs`,
    { headers: { Authorization: `Bearer ${created.creatorAccessToken}` } },
  );
  expect(ptRes.status(), "create playthrough").toBe(201);
  const pt = await ptRes.json();
  return {
    caseId: created.caseId,
    creatorAccessToken: created.creatorAccessToken,
    playthroughId: pt.playthroughId,
    playthroughAccessToken: pt.playthroughAccessToken,
    createdStatus: created.status,
  };
}

async function seedCreds(page: Page, h: Handshake): Promise<void> {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([pid, token]) => {
      localStorage.setItem("pd_playthrough_id", pid);
      localStorage.setItem("pd_playthrough_token", token);
    },
    [h.playthroughId, h.playthroughAccessToken] as const,
  );
}

async function canvasPixelStats(page: Page): Promise<{
  width: number;
  height: number;
  distinctColors: number;
}> {
  return await page.evaluate(() => {
    const shell = document.querySelector('[data-testid="scene-canvas"]');
    const canvas = (shell?.querySelector("canvas") ??
      document.querySelector("canvas.scene-canvas")) as HTMLCanvasElement | null;
    if (!canvas) return { width: 0, height: 0, distinctColors: 0 };
    const off = document.createElement("canvas");
    off.width = canvas.width;
    off.height = canvas.height;
    const ctx = off.getContext("2d");
    if (!ctx) return { width: canvas.width, height: canvas.height, distinctColors: 0 };
    ctx.drawImage(canvas, 0, 0);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const seen = new Set<number>();
    for (let i = 0; i < data.length; i += 4) {
      seen.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
    }
    return { width: canvas.width, height: canvas.height, distinctColors: seen.size };
  });
}

async function runAcceptance(
  page: Page,
  request: APIRequestContext,
  difficulty: "medium" | "hard",
  minInspectables: number,
  minMeaningfulSources: number,
) {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  const lazyAssets: string[] = [];
  page.on("response", (res) => {
    const url = res.url();
    if (/\/assets\/.+\.js(\?|$)/.test(url)) lazyAssets.push(url);
  });
  const evidence: Record<string, unknown> = {};

  const h = await startCase(request, difficulty);
  evidence.handshake = h;
  evidence.difficulty = difficulty;

  const invRes = await request.get(
    `/api/v1/playthroughs/${h.playthroughId}/investigation`,
    { headers: { Authorization: `Bearer ${h.playthroughAccessToken}` } },
  );
  expect(invRes.status(), "investigation bootstrap").toBe(200);
  const inv = await invRes.json();
  const worldObjects: Array<{ objectId: string; interaction?: string }> =
    inv?.scene?.worldObjects ?? [];
  const interactive = worldObjects.filter(
    (o) => typeof o.interaction === "string" && o.interaction.length > 0,
  );
  evidence.investigation = {
    caseVersion: inv.caseVersion,
    environmentId: inv?.scene?.environmentId ?? null,
    sceneName: inv?.scene?.name ?? null,
    worldObjectCount: worldObjects.length,
    interactiveCount: interactive.length,
    interactiveIds: interactive.map((o) => o.objectId),
    witnessIds: (inv.witnesses ?? []).map((w: { witnessId: string }) => w.witnessId),
  };
  expect(
    interactive.length,
    `${difficulty} contract: >= ${minInspectables} interactive inspectables`,
  ).toBeGreaterThanOrEqual(minInspectables);

  await seedCreds(page, h);
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  evidence.lazyAssets = lazyAssets;

  const pixels = await expect
    .poll(async () => (await canvasPixelStats(page)).distinctColors, {
      timeout: 30_000,
      intervals: [200, 400, 800, 1200],
    })
    .toBeGreaterThan(5)
    .then(() => canvasPixelStats(page));
  evidence.canvas = pixels;
  expect(pixels.width, "canvas backing width").toBeGreaterThan(0);
  expect(pixels.height, "canvas backing height").toBeGreaterThan(0);
  await page.screenshot({
    path: `artifacts/screenshots/p45-${difficulty}-scene.png`,
    fullPage: true,
  });

  for (const o of interactive) {
    await expect(
      page.getByTestId(`object-${o.objectId}`),
      `interactive object button ${o.objectId}`,
    ).toBeVisible();
  }

  const objectResults: Record<string, { discovery: boolean; panel: string }> = {};
  for (const o of interactive) {
    const btn = page.getByTestId(`object-${o.objectId}`);
    await expect(btn, `object button ${o.objectId}`).toBeVisible();
    await btn.click();
    let discovery = false;
    let panelText = "";
    const panel = page.getByTestId("evidence-panel");
    const panelShown = await panel
      .waitFor({ state: "visible", timeout: 6_000 })
      .then(() => true)
      .catch(() => false);
    if (panelShown) {
      panelText = ((await panel.innerText()) ?? "").trim();
      await page.getByTestId("evidence-close").click().catch(() => {});
      await panel.waitFor({ state: "hidden", timeout: 5_000 }).catch(() => {});
    }
    const toast = page.getByTestId("discovery-toast");
    if (await toast.isVisible().catch(() => false)) {
      const text = (await page.getByTestId("discovery-toast-text").innerText()) ?? "";
      discovery = /Discovered/i.test(text);
      await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
    }
    objectResults[o.objectId] = { discovery, panel: panelText.slice(0, 200) };
  }
  evidence.objectInteractions = objectResults;
  const meaningfulObjects = Object.entries(objectResults)
    .filter(([, r]) => r.discovery || r.panel.length > 0)
    .map(([id]) => id);
  evidence.meaningfulObjectSources = meaningfulObjects;
  expect(
    meaningfulObjects.length,
    "several objects expose player-visible investigation content",
  ).toBeGreaterThanOrEqual(2);

  const witnessBtn = page.getByTestId("witness-emily_reed");
  await expect(witnessBtn, "witness emily_reed button").toBeVisible({
    timeout: 15_000,
  });
  await witnessBtn.click();
  await expect(page.getByTestId("witness-panel")).toBeVisible({ timeout: 15_000 });
  let answered = false;
  for (const q of ["OBSERVATION", "TIME", "LOCATION", "OBJECT", "SOUND"]) {
    const qBtn = page.getByTestId(`witness-question-${q}`);
    if (await qBtn.isVisible().catch(() => false)) {
      await qBtn.click();
      const answer = page.getByTestId("witness-answer");
      const ok = await answer
        .waitFor({ state: "visible", timeout: 12_000 })
        .then(() => true)
        .catch(() => false);
      if (ok) {
        const text = (await answer.innerText()).trim();
        evidence.witnessAnswer = {
          question: q,
          length: text.length,
          sample: text.slice(0, 200),
        };
        answered = text.length > 0;
        if (answered) break;
      }
    }
  }
  expect(answered, "witness returned a usable player-facing answer").toBe(true);
  await page.getByTestId("witness-close").click().catch(() => {});

  const totalSources = meaningfulObjects.length + (answered ? 1 : 0);
  evidence.meaningfulSourceTotal = totalSources;
  expect(
    totalSources,
    `${difficulty} distinct meaningful interaction sources >= ${minMeaningfulSources}`,
  ).toBeGreaterThanOrEqual(minMeaningfulSources);

  const health = await request.get("/api/v1/health");
  expect(health.status(), "/health responsive").toBe(200);
  evidence.healthStatus = health.status();

  await page.goto("/accuse", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("accusation-option-murdererId-thomas_reed").check();
  await page
    .getByTestId("accusation-option-motiveId-cover_up_embezzlement")
    .check();
  await page.getByTestId("accusation-option-weaponId-kitchen_knife").check();
  await page.getByTestId("accusation-time").fill("22:17");
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({
    timeout: 10_000,
  });
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({
    timeout: 30_000,
  });
  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await page.screenshot({
    path: `artifacts/screenshots/p45-${difficulty}-reveal.png`,
    fullPage: true,
  });

  evidence.pageErrors = pageErrors;
  try {
    const dir = join(process.cwd(), "artifacts", "screenshots");
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, `p45-${difficulty}-acceptance-evidence.json`),
      JSON.stringify(evidence, null, 2),
    );
  } catch {
    /* evidence write is best-effort */
  }
  await test.info().attach(`p45-${difficulty}-acceptance-evidence.json`, {
    body: JSON.stringify(evidence, null, 2),
    contentType: "application/json",
  });
  expect(pageErrors, "no uncaught frontend errors").toEqual([]);
}

test("Phase45 §39 — Caddy production MEDIUM acceptance", async ({ page, request }) => {
  await runAcceptance(page, request, "medium", 5, 3);
});

test("Phase45 §39 — Caddy production HARD acceptance", async ({ page, request }) => {
  await runAcceptance(page, request, "hard", 7, 4);
});
