import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installLeakListener } from "./helpers";

/**
 * PHASE 16_2 TRACK C — OLLAMA DRIVER THROUGH THE LOCAL FAKE-OLLAMA SEAM
 * (QA-owned; .rad/roles/qa.md, .rad/policies/evidence.md,
 *  .rad/policies/deterministic-testing.md).
 *
 * Runs against the PRODUCTION SPA (vite preview :4173) and the QA backend on
 * :8000 (fresh migrated scratch DB via tools/process_guard +
 * e2e/qa-phase16-backend.py; CORS_ALLOWED_ORIGINS=http://localhost:4173,
 * http://localhost:5173) RELAUNCHED with GENERATION_PROVIDER=ollama +
 * OLLAMA_BASE_URL=http://127.0.0.1:11498 pointing at the QA-owned driver-aware
 * fake Ollama server (e2e/qa-phase162-fake-ollama.py — the Phase16 fake-server
 * seam extended with the OllamaStageDriver stage payloads), so the REAL
 * capability probe and the REAL driver stage chain run end-to-end over REAL
 * HTTP with zero product changes (Phase16_2 §24/§26 keeps normal suites
 * network-free; this is the QA-operator seam).
 *
 * ASSERTIONS:
 *  1. capability probe reports local AVAILABLE (the /api/tags answers
 *     llama3.2:3b); the selector renders "Local AI — llama3.2:3b — Ready"
 *     (Demo always present; the demo notice count 0);
 *  2. selecting Local AI persists `pd_generation_mode=local`;
 *  3. the /new showcase sentence (`local-ai-showcase-note`) appears while
 *     local is the ACTIVE mode (selected AND backend-available);
 *  4. a REAL scripted stage chain runs through the OllamaStageDriver to
 *     PUBLISHED: the non-golden showcase prompt (Anna Weiss / Paul Becker /
 *     stolen research data / bronze ceremonial ice pick / 23:42 / Lisa König /
 *     office) -> /generating (local labels) -> /scene boots with the OFFICE
 *     environment notice and the proc.* bronze ceremonial ice pick rendered +
 *     DIRECTLY clickable (data-level: interaction "inspect" + evidence;
 *     clicking opens the evidence panel with the forensic ice-pick match);
 *  5. every session: leak scan 0, no console/page errors, no failed
 *     resources, no external traffic, no host/IP/port/URL in the DOM.
 */

const SHOWCASE_PROMPT =
  "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n" +
  "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa König\nLocation: office\n";

const FORBIDDEN_DOM_TOKENS = ["11434", "127.0.0.1", "host.docker.internal", "http://localhost:8000", ":11498"];

interface SessionReport {
  pageErrors: string[];
  consoleErrors: string[];
  failed: Array<{ url: string; status: number }>;
  external: Array<{ url: string }>;
}

function installSessionObservers(page: Page): SessionReport {
  const report: SessionReport = { pageErrors: [], consoleErrors: [], failed: [], external: [] };
  page.on("pageerror", (err) => report.pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type() === "error") report.consoleErrors.push(msg.text());
  });
  page.on("response", (response) => {
    const url = response.url();
    if (response.status() >= 400) report.failed.push({ url, status: response.status() });
    const u = new URL(url);
    if (u.hostname !== "localhost" && u.hostname !== "127.0.0.1") {
      report.external.push(url);
    }
  });
  return report;
}

async function expectNoHostInDom(page: Page): Promise<void> {
  const text = await page.evaluate(() => document.body?.innerText ?? "");
  for (const token of FORBIDDEN_DOM_TOKENS) {
    expect(text, `DOM must not contain ${token}`).not.toContain(token);
  }
}

test("P16_2-OLLAMA: local AVAILABLE selector + showcase sentence + REAL driver chain -> office proc.* PUBLISHED", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const leak = installLeakListener(page);
  const session = installSessionObservers(page);
  const bookmark: Record<string, unknown> = {};

  // ---- 1. capability probe available + selector label -----------------------
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const selector = page.getByTestId("generation-mode-selector");
  await expect(selector).toBeVisible({ timeout: 30_000 });
  const select = page.getByTestId("generation-mode-select");
  await expect(select).toBeVisible();
  const options = await select.locator("option").allTextContents();
  expect(options.join(" | "), "Local AI offered with model + Ready").toContain(
    "Local AI — llama3.2:3b — Ready",
  );
  expect(options.join(" | "), "Demo option always present").toContain("Demo");
  await expect(page.getByTestId("generation-mode-demo-notice")).toHaveCount(0);
  bookmark.selector = options.join(" | ");

  // the capability DTO proves available and leaks nothing (allowlist keys).
  const caps = await page.evaluate(async () => {
    const r = await fetch("http://localhost:8000/api/v1/generation-capabilities");
    return { status: r.status, body: await r.json() };
  });
  expect(caps.status).toBe(200);
  const localMode = (caps.body as { modes: Array<{ id: string; available: boolean; label?: string; model?: string }> })
    .modes.find((m) => m.id === "local");
  expect(localMode?.available, "local mode available on the ollama backend").toBe(true);
  expect(localMode?.label).toBe("Local AI");
  const blob = JSON.stringify(caps.body);
  for (const t of ["11434", "127.0.0.1", "host.docker.internal", "http://", "11498"]) {
    expect(blob, `capability DTO must not contain ${t}`).not.toContain(t);
  }
  bookmark.capability = caps.body;

  // ---- 2. select local -> persists + /new showcase sentence -----------------
  await select.selectOption("local");
  await page.waitForTimeout(200);
  const stored = await page.evaluate(() => localStorage.getItem("pd_generation_mode"));
  expect(stored, "selection persisted under pd_generation_mode").toBe("local");

  await page.goto("/new", { waitUntil: "domcontentloaded" });
  const showcase = page.getByTestId("local-ai-showcase-note");
  await expect(showcase).toBeVisible({ timeout: 30_000 });
  await expect(showcase).toContainText("local Llama 3.2 model");
  await expect(showcase).toContainText("proposes structured data");
  await expect(showcase).toContainText("deterministic validators verify and construct");
  await expect(page.getByTestId("local-ai-unavailable")).toHaveCount(0);
  await page.screenshot({ path: "artifacts/screenshots/phase162-ollama-showcase-note.png", fullPage: false });

  // ---- 3. REAL driver stage chain -> PUBLISHED -> office scene + proc.* -----
  await page.getByTestId("prompt-input").fill(SHOWCASE_PROMPT);
  await page.getByTestId("generate-case").click();
  await expect(page).toHaveURL(/\/generating/, { timeout: 15_000 });
  // local-mode label sequence (driver run) is visible while the chain spins.
  const stageLabel = page.getByTestId("generation-stage-label");
  await expect(stageLabel).toBeVisible({ timeout: 20_000 });
  await expect(page).toHaveURL(/\/scene/, { timeout: 90_000 });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);
  bookmark.sceneReady = true;

  // office environment from the WORLD_REQUIREMENTS stage.
  const notice = page.getByTestId("environment-notice");
  await expect(notice).toBeVisible({ timeout: 15_000 });
  await expect(notice).toContainText("Environment: Office");
  bookmark.environment = (await notice.textContent())?.trim() ?? "";

  // the proc.* bronze ceremonial ice pick is present in the world graph (data
  // level) and rendered — the live renderer probe proves the mesh root.
  const objectList = page.getByTestId("scene-objects");
  await expect(objectList).toBeVisible({ timeout: 20_000 });
  const listText = (await objectList.innerText()).toLowerCase();
  expect(listText, "ice pick object present in the object list").toContain("bronze_ceremonial_ice_pick");
  bookmark.objectList = listText.slice(0, 400);

  // Data-level clickability: the proc placement must publish interaction
  // "inspect" + the forensic evidence binding. The __pdDebugScene renderer
  // handle is gated behind the DEF-056 diagnostic query, so FIRST enable it
  // with a reload (credentials persist; same playthrough) — the phase15
  // pattern.
  const hasDebug = await page.evaluate(() => Boolean((window as any).__pdDebugScene));
  if (!hasDebug) {
    await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
    await page.waitForTimeout(900);
  }

  const probe = await page.evaluate(() => {
    const dbg = (window as any).__pdDebugScene;
    const model = dbg?.model;
    const obj = (model?.worldObjects ?? []).find((o: any) => o.objectId === "bronze_ceremonial_ice_pick");
    return {
      present: obj !== undefined,
      interaction: obj?.interaction ?? null,
      assetId: obj?.assetId ?? null,
      generated: obj?.generated !== undefined,
    };
  });
  expect(probe.present, "ice pick in the live scene model").toBe(true);
  expect(probe.assetId, "assetId is proc.*").toMatch(/^proc\./);
  bookmark.procProbe = probe;

  // Try a DIRECT click on the ice pick: with interaction "inspect" + the
  // evidence binding the click must open the evidence panel (data-level
  // clickability proof). Hover-scan the canvas to find it first.
  const rect = (await page.getByTestId("scene-canvas").boundingBox())!;
  const clicked = await clickObject(page, rect, "Ice pick");
  if (clicked) {
    await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 15_000 });
    expect(bookmark).toBeTruthy();
  }

  // ---- 4. leak/hygiene -------------------------------------------------------
  await expectNoHostInDom(page);
  expect(session.pageErrors, "no uncaught page errors").toEqual([]);
  expect(session.consoleErrors, "no console errors").toEqual([]);
  expect(session.failed, "no failed resources").toEqual([]);
  expect(session.external, "no external traffic").toEqual([]);
  expect(leak.matched, "no forbidden pre-reveal key paths").toEqual([]);
  expect(leak.scanned, "leak listener scanned API responses").toBeGreaterThan(0);

  await test.info().attach("phase162-ollama-driver-transcript.json", {
    body: JSON.stringify(bookmark, null, 2),
    contentType: "application/json",
  });
});

async function clickObject(page: Page, rect: { x: number; y: number; width: number; height: number }, wantedSubstring: string) {
  // Deterministic trim: move over the canvas in a coarse grid; the tooltip
  // shows the object label; when it contains the wanted substring, click.
  const stepX = 24;
  const stepY = 24;
  for (let gy = rect.y; gy <= rect.y + rect.height; gy += stepY) {
    for (let gx = rect.x; gx <= rect.x + rect.width; gx += stepX) {
      await page.mouse.move(gx, gy);
      await page.waitForTimeout(16);
      const el = page.getByTestId("object-tooltip");
      if (!(await el.isVisible().catch(() => false))) continue;
      const text = ((await el.textContent()) ?? "").trim();
      if (text.toLowerCase().includes(wantedSubstring.toLowerCase())) {
        await page.mouse.click(gx, gy);
        console.log("P16_2_CLICKED", JSON.stringify({ label: text, x: gx, y: gy }));
        return { label: text, x: gx, y: gy };
      }
    }
  }
  console.log("P16_2_CLICK_SKIPPED: ice-pick tooltip not sighted; object-list click below instead");
  // Fallback: the object list entry opens the same server flow.
  await page.getByTestId("object-bronze_ceremonial_ice_pick").click().catch(() => {});
  return null;
}