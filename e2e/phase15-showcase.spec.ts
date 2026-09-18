import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installLeakListener, scanJsonBody } from "./helpers";

/**
 * PHASE 15 — FIRST-TIME-REVIEWER BROWSER SHOWCASE (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Emulates a hackathon reviewer with ZERO manual setup against the PRODUCTION
 * SPA served by vite preview on :4173 and the QA backend on :8000 (freshly
 * migrated scratch DB via tools/process_guard; e2e/qa-phase145-backend.py wires
 * the deterministic dev-mode spec provider behind the untouched public API).
 *
 *  JOURNEY A — "Generate a New Mystery": landing shows BOTH demo paths with
 *  their honest labels; the generate path leads to /new; a NON-golden matrix
 *  prompt (warehouse: wrench + jewelry box) is submitted through the form ->
 *  /generating progress -> /scene; the environment notice says "Warehouse",
 *  prompt-specific objects are present in the live renderer, and evidence is
 *  DISCOVERED BY A DIRECT 3D CLICK; accuse the correct WHO/WHY/WEAPON/WHEN and
 *  reveal "CASE SOLVED" with the truth.
 *
 *  JOURNEY B — "Try Demo Case": the deterministic demo path runs the golden
 *  apartment case end-to-end with direct clicks and a full accuse/reveal.
 *
 *  BOTH journeys assert: 0 console/page errors, 0 failed resources, 0 external
 *  traffic, 0 pre-reveal truth-leak matches, allowlist-clean /reveal DTOs.
 */

const WAREHOUSE_PROMPT =
  "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover up the €240,000 embezzlement\n" +
  "Weapon: kitchen_knife\nTime: 20:17Z\nWitness: emily_reed\n" +
  "A smuggling dispute in a storage depot. A wrench and a jewelry box were found on the floor.";

const KNIFE_LABEL = "Kitchen knife";
const LAPTOP_LABEL = "Laptop";

// ---------------------------------------------------------------------------
// session observers (same contract as the standing phase-14 gate)
// ---------------------------------------------------------------------------
interface SessionReport {
  captured: Array<{ url: string; method: string; status: number; body: unknown }>;
  pageErrors: string[];
  consoleErrors: string[];
  failed: Array<{ url: string; status: number }>;
  external: Array<{ url: string; status: number }>;
}

function installSessionObservers(page: Page): SessionReport {
  const report: SessionReport = { captured: [], pageErrors: [], consoleErrors: [], failed: [], external: [] };
  page.on("pageerror", (err) => report.pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") report.consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });
  page.on("response", async (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    const status = response.status();
    const request = response.request();
    const method = request.method();
    if (url.startsWith("http:") || url.startsWith("https:")) {
      const from = new URL(url);
      if (!(from.hostname === "localhost" && (from.port === "4173" || from.port === "8000"))) {
        report.external.push({ url, status });
      }
    }
    if (status >= 400) report.failed.push({ url, status });
    if (!url.includes("/api/")) return;
    const headers = typeof response.headers === "function" ? response.headers() : response.headers;
    const contentType = headers["content-type"] ?? headers["Content-Type"] ?? "";
    if (!contentType.includes("application/json")) return;
    try {
      const body = await response.json();
      report.captured.push({ url, method, status, body });
    } catch {
      report.captured.push({ url, method, status, body: null });
    }
  });
  return report;
}

// ---------------------------------------------------------------------------
// truth-leak scanners (identical contract to the frozen phase-7 / phase-14 sets)
// ---------------------------------------------------------------------------
const PRE_REVEAL_FORBIDDEN_KEYS = new Set([
  "truth", "truthfulness", "canonical", "canonicalCrimeTime", "crime",
  "crimeTime", "acceptedScoring", "acceptedScoringTimeSet",
  "murdererCorrect", "motiveCorrect", "weaponCorrect", "timeCorrect",
  "correctDimensions", "totalDimensions", "overall", "isCorrect",
  "winner", "winning", "winners", "isWinner", "designated",
  "designation", "rank", "selected", "murderer", "victim",
  "universe", "universes", "solution", "solutionProof", "solverProof",
  "proof", "prompt", "providerOutput", "diagnostics", "seed", "model",
  "verifier", "tokenVerifier", // NOTE: bare attemptId/generationAttemptId are
  // intentionally NOT pre-reveal-forbidden: POST /cases 201 carries
  // generationAttemptId as a frozen documented opaque Phase-5 DTO field
  // (documented carve-out in e2e/phase8-journey.spec.ts; designates nothing).
  "propositions", "sourceRef", "schemaVersion", "draft", "publishedAt",
  "payload", "quotaSessionId", "anonymousQuotaSessionId",
]);

const REVEAL_FORBIDDEN_KEYS = new Set([
  "solverProof", "solutionProof", "proof", "acceptedScoring",
  "acceptedScoringTimeSet", "prompt", "providerOutput", "diagnostics",
  "seed", "model", "verifier", "tokenVerifier", "attemptId",
  "generationAttemptId", "propositions", "sourceRef", "universe",
  "universes", "canonical", "canonicalCrimeTime", "truthfulness",
  "winner", "winning", "winners", "isWinner", "designated",
  "designation", "rank", "selected", "schemaVersion", "draft",
  "publishedAt", "payload", "quotaSessionId", "anonymousQuotaSessionId",
  "token", "sessionId",
]);

const CANONICAL_TIME_VALUE = "2026-09-11T22:17:00+02:00";

function walkKeys(
  node: unknown,
  path: string,
  hits: string[],
  forbidden: Set<string>,
  opts: { checkCanonicalValue: boolean; skipGenerated: boolean },
): void {
  if (node === null || node === undefined) return;
  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i++) walkKeys(node[i], `${path}[${i}]`, hits, forbidden, opts);
    return;
  }
  if (typeof node === "object") {
    for (const [key, value] of Object.entries(node)) {
      const child = `${path}.${key}`;
      const underEcho = path.endsWith(".accusation");
      if (key === "generated" && opts.skipGenerated) {
        continue;
      }
      if (forbidden.has(key) && !(key === "crimeTime" && underEcho)) hits.push(child);
      if (opts.checkCanonicalValue && typeof value === "string") {
        if (value === CANONICAL_TIME_VALUE && !underEcho) hits.push(`${child}(=canonicalCrimeTime)`);
      }
      walkKeys(value, child, hits, forbidden, opts);
    }
  }
}

interface CapturedResponse {
  url: string;
  body: unknown;
}

function installResponseCapture(page: Page): { bodies: CapturedResponse[] } {
  const bodies: CapturedResponse[] = [];
  page.on("response", async (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    if (!url.includes("/api/")) return;
    const headers = typeof response.headers === "function" ? response.headers() : response.headers;
    const contentType = headers["content-type"] ?? headers["Content-Type"] ?? "";
    if (!contentType.includes("application/json")) return;
    try {
      bodies.push({ url, body: await response.json() });
    } catch {}
  });
  return { bodies };
}

// ---------------------------------------------------------------------------
// scene tooling (same patterns as the standing phase-14 gate)
// ---------------------------------------------------------------------------
async function tooltipText(page: Page): Promise<string | null> {
  const el = page.getByTestId("object-tooltip");
  if (!(await el.isVisible().catch(() => false))) return null;
  return ((await el.textContent()) ?? "").trim();
}

interface Sight {
  label: string;
  x: number;
  y: number;
}

async function hoverScan(
  page: Page,
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

async function modelProbe(
  page: Page,
  objectIds: string[],
): Promise<Record<string, { inModel: boolean; label: string | null; root: boolean; partCount: number; generated: boolean }>> {
  return page.evaluate(
    ({ ids }) => {
      const dbg = (window as any).__pdDebugScene;
      const scene = dbg?.scene;
      const model = dbg?.model;
      const result: Record<string, any> = {};
      for (const id of ids) {
        const obj = (model?.worldObjects ?? []).find((o: any) => o.objectId === id);
        const root = scene ? scene.getNodeByName(`pd_obj_${id}`) : null;
        result[id] = {
          inModel: obj !== undefined,
          label: obj?.label ?? null,
          root: !!root,
          partCount: root ? root.getChildMeshes(false).length : 0,
          generated: obj?.generated !== undefined,
        };
      }
      return result;
    },
    { ids: objectIds },
  );
}

/** Dismiss the discovery toast + close the evidence panel (robust driver). */
async function closeEvidencePanel(page: Page): Promise<void> {
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

// ---------------------------------------------------------------------------
// shared accuse -> reveal loop with leak scans + hygiene
// ---------------------------------------------------------------------------
async function accuseAndReveal(
  page: Page,
  net: SessionReport,
  captured: { bodies: CapturedResponse[] },
  transcript: Record<string, unknown>,
  label: string,
): Promise<void> {
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
  transcript.accused = { who: "thomas_reed", why: "cover_up_embezzlement", weapon: "kitchen_knife", when: "22:17" };

  // Pre-reveal truth-leak scan over every captured API body up to this point.
  const preReveal: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of captured.bodies) {
    scanned += 1;
    const hits: string[] = [];
    walkKeys(entry.body, "$", hits, PRE_REVEAL_FORBIDDEN_KEYS, {
      checkCanonicalValue: true,
      skipGenerated: true,
    });
    if (hits.length > 0) preReveal.push({ url: entry.url, paths: hits });
  }
  console.log(`P15_PRE_REVEAL ${label}`, JSON.stringify({ scanned, matched: preReveal }));
  expect(preReveal, `${label}: no forbidden key paths in ${scanned} pre-reveal responses`).toEqual([]);
  expect(scanned, `${label}: pre-reveal responses captured`).toBeGreaterThan(0);
  transcript.preRevealLeak = { scanned, matched: preReveal.length };

  // Reveal.
  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
  await expect(page.getByTestId("reveal-truth-motive")).toContainText("embezzlement");
  await expect(page.getByTestId("reveal-truth-weapon")).toHaveText("Kitchen Knife");
  await expect(page.getByTestId("reveal-truth-time")).toHaveText("22:17");
  for (const dim of ["who", "why", "weapon", "when"]) {
    await expect(page.getByTestId(`reveal-dimension-${dim}`)).toContainText("Correct");
  }
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await expect(page.getByTestId("reveal-explanation")).toBeVisible();
  transcript.reveal = { overall: "CASE SOLVED", score: "4 / 4" };

  // Post-reveal allowlist scan of the /reveal responses.
  const revealResponses = captured.bodies.filter((b) => b.url.includes("/reveal"));
  expect(revealResponses.length, `${label}: reveal responses exist`).toBeGreaterThan(0);
  const revealHits: Array<{ url: string; paths: string[] }> = [];
  for (const rr of revealResponses) {
    const hits: string[] = [];
    walkKeys(rr.body, "$", hits, REVEAL_FORBIDDEN_KEYS, {
      checkCanonicalValue: false,
      skipGenerated: true,
    });
    if (hits.length > 0) revealHits.push({ url: rr.url, paths: hits });
  }
  expect(revealHits, `${label}: reveal DTO carry no internal material`).toEqual([]);
  transcript.revealDtoAllowlist = { revealResponses: revealResponses.length, hits: revealHits.length };

  // Session hygiene.
  expect(net.failed, `${label}: no failed resources`).toEqual([]);
  expect(net.external, `${label}: no external traffic`).toEqual([]);
  expect(net.pageErrors, `${label}: no uncaught page errors`).toEqual([]);
  expect(net.consoleErrors, `${label}: no console errors`).toEqual([]);
  transcript.net = {
    failed: net.failed.length,
    external: net.external.length,
    pageErrors: net.pageErrors.length,
    consoleErrors: net.consoleErrors.length,
  };
  console.log(`P15_FULL_LOOP ${label}`, JSON.stringify(transcript));
}

/** Direct 3D click discovery on the canvas (hover-scan, first sighting wins). */
async function directClickDiscovery(
  page: Page,
  wanted: string | null,
  rect: { x: number; y: number; width: number; height: number },
): Promise<{ label: string; x: number; y: number }> {
  await page.mouse.move(rect.x - 40, rect.y + 20);
  const primary = await hoverScan(page, rect, 32, 32);
  let found = new Map(primary);
  if (wanted !== null && !found.has(wanted)) {
    const fine = await hoverScan(page, rect, 16, 16);
    for (const [label, sight] of fine) {
      if (!found.has(label)) found.set(label, sight);
    }
  } else if (!found.has(KNIFE_LABEL) && !found.has(LAPTOP_LABEL)) {
    const fine = await hoverScan(page, rect, 16, 16);
    for (const [label, sight] of fine) {
      if (!found.has(label)) found.set(label, sight);
    }
  }
  const target = found.get(wanted ?? "") ?? found.get(KNIFE_LABEL) ?? found.get(LAPTOP_LABEL);
  expect(target, "direct 3D click target found").toBeDefined();
  await page.mouse.move(target!.x, target!.y);
  await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("object-tooltip")).toHaveText(target!.label);
  await page.mouse.click(target!.x, target!.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 15_000 });
  return { label: target!.label, x: target!.x, y: target!.y };
}

/**
 * The read-only __pdDebugScene renderer probe is gated behind the
 * DEF-056 diagnostic query (?pd-debug-pick=1). The authentic reviewer journey
 * navigates from /generating WITHOUT it, so after scene-ready we enable the
 * hook with a reload (localStorage credentials persist; same playthrough).
 */
async function enableSceneDebugOnce(page: Page): Promise<void> {
  const has = await page.evaluate(() => Boolean((window as any).__pdDebugScene));
  if (has) return;
  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  const ready = await page.evaluate(() => Boolean((window as any).__pdDebugScene));
  expect(ready, "debug scene handle installed after reload").toBe(true);
  await page.waitForTimeout(800);
}

// ---------------------------------------------------------------------------
// JOURNEY A — Generate a New Mystery (warehouse, non-golden) -> reveal
// ---------------------------------------------------------------------------
test("Phase 15 first-time reviewer: landing honesty + Generate a New Mystery warehouse journey", async ({ page }) => {
  test.setTimeout(180_000);
  const leak = installLeakListener(page);
  const net = installSessionObservers(page);
  const captured = installResponseCapture(page);
  const transcript: Record<string, unknown> = { journey: "A-generate" };

  // ---- landing: BOTH demo paths with honest labels --------------------------
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const demoButton = page.getByTestId("try-demo");
  await expect(demoButton).toBeVisible({ timeout: 15_000 });
  await expect(demoButton).toHaveText("Try Demo Case");
  const demoNote = page.getByTestId("try-demo-note");
  await expect(demoNote).toContainText("Deterministic demo — no API keys, no cost.");

  const generatePath = page.getByTestId("generate-new-mystery");
  await expect(generatePath).toBeVisible();
  await expect(generatePath).toHaveText("Generate a New Mystery");
  const genNote = page.getByTestId("generate-provider-note");
  await expect(genNote).toBeVisible();
  const genNoteText = (await genNote.textContent()) ?? "";
  // The default fake build MUST NOT claim live-AI behaviour.
  expect(genNoteText).toContain("uses the built-in deterministic generator in this demo build");
  expect(genNoteText).not.toContain("Live AI provider");
  const pageText = (await page.locator("body").innerText()) ?? "";
  expect(pageText).not.toContain("Live AI provider");
  transcript.landing = {
    demo: "Try Demo Case",
    demoNote: "Deterministic demo — no API keys, no cost.",
    generate: "Generate a New Mystery",
    providerNote: genNoteText,
  };

  // ---- generate path -> /new ------------------------------------------------
  await generatePath.click();
  await expect(page).toHaveURL("/new", { timeout: 10_000 });
  await expect(page.getByTestId("prompt-input")).toBeVisible();
  const intro = (await page.getByTestId("generate-intro").textContent()) ?? "";
  expect(intro).toContain("Write your own detective scenario");
  const newNote = (await page.getByTestId("generate-provider-note").textContent()) ?? "";
  expect(newNote).toContain("uses the built-in deterministic generator in this demo build");
  await expect(page.getByTestId("try-demo-note")).toContainText("Deterministic demo — no API keys, no cost.");
  transcript.newPage = { intro: intro.slice(0, 60), providerNote: newNote };

  // ---- submit a NON-golden matrix prompt -------------------------------------
  await page.getByTestId("prompt-input").fill(WAREHOUSE_PROMPT);
  await page.getByTestId("generate-case").click();
  await expect(page).toHaveURL("/generating", { timeout: 15_000 });
  await expect(page.getByTestId("generation-stage-label")).toBeVisible({ timeout: 20_000 });
  transcript.progressSeen = true;
  await expect(page).toHaveURL(/\/scene/, { timeout: 60_000 });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);
  transcript.sceneReady = true;

  // ---- environment identity: the right kit -----------------------------------
  const notice = page.getByTestId("environment-notice");
  await expect(notice).toBeVisible({ timeout: 15_000 });
  await expect(notice).toContainText("Environment: Warehouse");
  transcript.notice = (await notice.textContent())?.trim() ?? "";

  // ---- prompt-specific objects present + directly clickable evidence ----------
  await enableSceneDebugOnce(page);
  const probe = await modelProbe(page, ["adjustable_wrench", "jewelry_box", "kitchen_knife", "apartment_laptop"]);
  for (const id of ["adjustable_wrench", "jewelry_box"]) {
    const o = (probe as Record<string, any>)[id];
    expect(o, `${id} in the live model`).not.toBeUndefined();
    expect(o.inModel, `${id} present in the scene model`).toBe(true);
    expect(o.root, `${id} rendered as pd_obj_ root mesh`).toBe(true);
    expect(o.partCount, `${id} has rendered part meshes`).toBeGreaterThan(0);
  }
  transcript.promptObjectsInModel = ["adjustable_wrench", "jewelry_box"];

  const rect = (await page.getByTestId("scene-canvas").boundingBox())!;
  const clicked = await directClickDiscovery(page, KNIFE_LABEL, rect);
  transcript.directClick = clicked;
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");
  await closeEvidencePanel(page);

  // Discover the second evidence (laptop email) via the object toolbox.
  const laptop = page.getByTestId("object-apartment_laptop");
  await expect(laptop).toBeVisible({ timeout: 20_000 });
  await laptop.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Re: the missing funds");
  await closeEvidencePanel(page);
  transcript.investigated = ["kitchen_knife", "apartment_laptop"];

  expect(leak.matched, "leak listener reported nothing").toEqual([]);
  await accuseAndReveal(page, net, captured, transcript, "warehouse");

  await test.info().attach("phase15-warehouse-first-time-reviewer.json", {
    body: JSON.stringify(transcript, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({ path: "artifacts/screenshots/phase15-warehouse-reveal.png", fullPage: true });
});

// ---------------------------------------------------------------------------
// JOURNEY B — Try Demo Case (deterministic, apartment) -> full accuse/reveal
// ---------------------------------------------------------------------------
test("Phase 15 first-time reviewer: Try Demo Case journey with direct clicks and full accuse/reveal", async ({ page }) => {
  test.setTimeout(180_000);
  const leak = installLeakListener(page);
  const net = installSessionObservers(page);
  const captured = installResponseCapture(page);
  const transcript: Record<string, unknown> = { journey: "B-demo" };

  await page.goto("/", { waitUntil: "domcontentloaded" });
  const demoButton = page.getByTestId("try-demo");
  await expect(demoButton).toBeVisible({ timeout: 15_000 });
  await expect(demoButton).toHaveText("Try Demo Case");

  // One click: deterministic demo, zero manual setup.
  await demoButton.click();
  await expect(page).toHaveURL("/generating", { timeout: 15_000 });
  await expect(page.getByTestId("generation-stage-label")).toBeVisible({ timeout: 20_000 });
  await expect(page).toHaveURL(/\/scene/, { timeout: 60_000 });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);
  transcript.sceneReady = true;

  // Apartment default golden case: NO environment notice by design.
  await expect(page.getByTestId("environment-notice")).toHaveCount(0);
  transcript.notice = "(none — apartment default)";

  // Direct 3D click discovery (knife), then laptop via the object toolbox.
  const rect = (await page.getByTestId("scene-canvas").boundingBox())!;
  const clicked = await directClickDiscovery(page, KNIFE_LABEL, rect);
  transcript.directClick = clicked;
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");
  await closeEvidencePanel(page);

  const laptop = page.getByTestId("object-apartment_laptop");
  await expect(laptop).toBeVisible({ timeout: 20_000 });
  await laptop.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Re: the missing funds");
  await closeEvidencePanel(page);
  transcript.investigated = ["kitchen_knife", "apartment_laptop"];

  expect(leak.matched, "leak listener reported nothing").toEqual([]);
  await accuseAndReveal(page, net, captured, transcript, "demo-apartment");

  await test.info().attach("phase15-demo-journey.json", {
    body: JSON.stringify(transcript, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({ path: "artifacts/screenshots/phase15-demo-reveal.png", fullPage: true });
});