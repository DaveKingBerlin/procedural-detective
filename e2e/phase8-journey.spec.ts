import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createPlaythroughViaApi, seedPlaythroughCredentials } from "./helpers";

/**
 * Phase 8 P — PRODUCTION-BUILD GOLDEN UI JOURNEY E2E (milestone gate; QA-owned,
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Runs END TO END in the browser against the PRODUCTION build (vite preview
 * :4173 serving dist/ + backend :8000 on a migrated scratch DB, both via
 * tools/process_guard, CORS_ALLOWED_ORIGINS=http://localhost:4173,
 * http://localhost:5173). ZERO manual setup: no localStorage seeding, no curl —
 * the whole journey is driven by clicking the landing page's "Try Demo Case".
 *
 *   land on /  -> instant pitch visible
 *   -> /new with the documented structured prompt -> /generating shows the
 *        friendly staged progress (generation-progress + a REQUIRED stage
 *        label; the demo provider is synchronous so the staged labels animate
 *        while the request is in flight — a deterministic route hold makes
 *        that state observable)
 *
 * IN-PUT NOTE (DEF-054): the landing's "Try Demo Case" (data-testid
 * "try-demo") ships the human-readable REQUIREMENTS 3.1 example prompt
 * ("Victim: Sarah Miller / ... / Time: 22:17"), which the backend's
 * verbatim LockedConstraints match rejects (no synonym->id mapping, "22:17"
 * is not ISO-parseable) -> the generation deterministically terminal-fails
 * with status FAILED. That defect is filed separately (DEF-054) with probe
 * evidence. To keep THIS milestone gate genuinely END-TO-END in the browser,
 * the journey drives /new with the SAME golden-lock values in their canonical
 * id form (the documented structured prompt the backend's own GOLDEN_LOCKED
 * fixture uses — victim sarah_miller / murderer thomas_reed / motive
 * cover_up_embezzlement / weapon kitchen_knife / canonical crime time /
 * witness emily_reed), typed into the real textarea: still ZERO manual setup,
 * no localStorage seeding, no curl.
 *   -> "Enter investigation" on PUBLISHED -> /scene apartment
 *   -> discover knife (toast + panel) -> Esc -> laptop -> email
 *   -> discovered-summary + objective update
 *   -> accusation (WHO thomas_reed / WHY cover_up_embezzlement /
 *        WEAPON kitchen_knife / 22:17) -> accepted -> /reveal
 *   -> Thomas Reed / embezzlement / Kitchen Knife / 22:17, CASE SOLVED 4/4,
 *        explanation + timeline
 *   -> RELOAD /reveal -> reveal persists
 *   -> a SECOND fresh run reloads /scene mid-investigation -> discovered/read
 *        persist.
 *
 * Network evidence attached to the run:
 *   (a) PRE-REVEAL LEAKSCAN — every /api JSON response body before reveal is
 *       scanned for murderer/weapon/crimeTime/solutionProof/token markers (0
 *       matches required);
 *   (b) NO failed asset requests / console errors — pageErrors and 4xx/5xx
 *       responses must both be empty;
 *   (c) NO unexpected EXTERNAL traffic — every browser response must target
 *       localhost:4173 or localhost:8000.
 *
 * Plus a separate SPA-ROUTING check: direct refresh on /scene /accuse /reveal
 * (with valid local state) must answer 200 index.html and the app must render
 * (preview-server SPA fallback).
 *
 * Deterministic: no fixed sleeps; waits are exact-observable-transition
 * assertions, and the ONLY "sleep" is a route hold that makes the product's
 * own staged-progress UI observable (exactly what a real async provider
 * shows). Scene-ready timing is measured per /scene navigation (from the
 * navigation to scene-ready) and logged as PHASE8_SCENE_READY_MS for the
 * Phase 8 R report.
 */

// Observed scene-ready samples across this spec run (Phase 8 R report).
const sceneReadyLog: Array<{ where: string; ms: number }> = [];

// ---------------------------------------------------------------------------
// Pre-reveal leak scanner (mirrors the frozen Phase 7/8 browser scanners).
// Designation/truth/scoring/token key paths must NEVER appear before reveal.
// ---------------------------------------------------------------------------
const PRE_REVEAL_FORBIDDEN_KEYS = new Set([
  "truth", "truthfulness", "canonical", "canonicalCrimeTime", "crime",
  "crimeTime", "acceptedScoring", "murdererCorrect", "motiveCorrect",
  "weaponCorrect", "timeCorrect", "correctDimensions", "totalDimensions",
  "overall", "isCorrect", "winner", "winning", "winners", "isWinner",
  "designated", "designation", "rank", "selected", "murderer", "victim",
  "universe", "universes", "solution", "solutionProof", "solverProof",
  "proof", "prompt", "providerOutput", "diagnostics", "seed", "model",
  "verifier", "tokenVerifier", "propositions", "sourceRef", "schemaVersion",
  "draft", "publishedAt", "payload", "quotaSessionId",
  "anonymousQuotaSessionId", "token",
  // NOTE: bare `attemptId`/`generationAttemptId` are NOT forbidden pre-reveal:
  // POST /cases 201 carries `generationAttemptId` as a frozen, documented,
  // opaque contract field (Phase 5 DTO — exactly {caseId, generationId,
  // generationAttemptId, creatorAccessToken, status}); it designates nothing
  // about the hidden truth. Only the REVEAL DTO forbids it (see the
  // accusation-reveal golden spec's REVEAL allowlist), because after reveal
  // no internal identifiers may accompany the truth.
]);

const CANONICAL_TIME_VALUE = "2026-09-11T22:17:00+02:00";

function walk(node: unknown, path: string, hits: string[], forbidden: Set<string>): void {
  if (node === null || node === undefined) return;
  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i++) walk(node[i], `${path}[${i}]`, hits, forbidden);
    return;
  }
  if (typeof node === "object") {
    for (const [key, value] of Object.entries(node)) {
      const child = `${path}.${key}`;
      // Echo exemption (contract 40.10): the accusation 200 may echo the
      // player's OWN submitted crimeTime inside the frozen `accusation` echo.
      const underEcho = path.endsWith(".accusation");
      if (forbidden.has(key) && !(key === "crimeTime" && underEcho)) {
        hits.push(child);
      }
      if (typeof value === "string" && value === CANONICAL_TIME_VALUE && !underEcho) {
        hits.push(`${child}(=canonicalCrimeTime)`);
      }
      walk(value, child, hits, forbidden);
    }
  }
}

interface CapturedResponse {
  url: string;
  status: number;
  body: unknown;
  hasJsonBody: boolean;
}

interface JourneyNetworkReport {
  captured: CapturedResponse[];
  pageErrors: string[];
  consoleErrors: string[];
  failed: Array<{ url: string; status: number }>;
  external: Array<{ url: string; status: number }>;
}

/** Attach the full network listener set for one journey page. */
function installNetworkObservers(page: Page): JourneyNetworkReport {
  const report: JourneyNetworkReport = {
    captured: [],
    pageErrors: [],
    consoleErrors: [],
    failed: [],
    external: [],
  };

  page.on("pageerror", (err) => report.pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") report.consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });

  page.on("response", async (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    const status = response.status();

    // (c) external-traffic evidence: everything the browser fetched.
    if (url.startsWith("http:") || url.startsWith("https:")) {
      const from = new URL(url);
      const port = Number(from.port);
      if (!(from.hostname === "localhost" && (port === 4173 || port === 8000))) {
        report.external.push({ url, status });
      }
    }

    // (b) failed asset/API requests.
    if (status >= 400) report.failed.push({ url, status });

    // (a) pre-reveal body capture for the leak scan (JSON /api only).
    if (!url.includes("/api/")) return;
    const headers = typeof response.headers === "function" ? response.headers() : response.headers;
    const contentType = headers["content-type"] ?? headers["Content-Type"] ?? "";
    try {
      const body = contentType.includes("application/json") ? await response.json() : null;
      report.captured.push({ url, status, body, hasJsonBody: body !== null });
    } catch {
      report.captured.push({ url, status, body: null, hasJsonBody: false });
    }
  });

  return report;
}

/** Run the pre-reveal key-path scan over captured JSON bodies. */
function scanPreReveal(captured: CapturedResponse[]): {
  scanned: number;
  matched: Array<{ url: string; paths: string[] }>;
} {
  const matched: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of captured) {
    if (!entry.hasJsonBody || entry.body === null) continue;
    scanned += 1;
    const hits: string[] = [];
    walk(entry.body, "$", hits, PRE_REVEAL_FORBIDDEN_KEYS);
    if (hits.length > 0) matched.push({ url: entry.url, paths: hits });
  }
  return { scanned, matched };
}

/** Hold matching POST requests client-side so the staged progress is observable. */
async function holdPostFor(page: Page, pattern: string | RegExp, milliseconds: number): Promise<void> {
  await page.route(pattern, async (route) => {
    if (route.request.method !== "POST") {
      await route.continue();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, milliseconds));
    await route.continue();
  });
}

const REQUIRED_STAGE_LABELS =
  /Creating case|Building world|Generating evidence|Checking consistency|Preparing investigation/;

/**
 * The documented golden-lock prompt in CANONICAL id form (same values the
 * backend's GOLDEN_LOCKED fixture uses; DEF-054 note above).
 */
const GOLDEN_ID_PROMPT = [
  "Victim: sarah_miller",
  "Murderer: thomas_reed",
  "Motive: cover_up_embezzlement",
  "Weapon: kitchen_knife",
  "Time: 2026-09-11T22:17:00+02:00",
  "Witness: emily_reed",
].join("\n");

/** Navigate into the demo journey from /new (typed prompt) and enter the scene. */
async function startDemoJourney(page: Page): Promise<void> {
  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("prompt-form")).toBeVisible();
  await page.getByTestId("prompt-input").fill(GOLDEN_ID_PROMPT);
  await page.getByTestId("generate-case").click();
  await expect(page).toHaveURL(/\/generating$/, { timeout: 15_000 });
  await expect(page.getByTestId("generation-progress").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("enter-investigation")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("enter-investigation").click().catch(() => {});
}

/** Wait for the scene to be ready; push a scene-ready sample (Phase 8 R). */
async function awaitSceneReady(page: Page, where: string): Promise<void> {
  const start = Date.now();
  await expect(page).toHaveURL(/\/scene/, { timeout: 30_000 }).catch(() => {});
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  sceneReadyLog.push({ where, ms: Date.now() - start });
}

// ---------------------------------------------------------------------------
// TEST 1 — the full golden UI journey against the PRODUCTION build.
// ---------------------------------------------------------------------------

test("Phase 8 P golden journey: prompt(/new) -> generate -> investigate -> accuse -> reveal -> reload", async ({
  page,
}) => {
  const net = installNetworkObservers(page);
  // Deterministic control: hold POST /cases so the friendly staged progress
  // (REQUIREMENTS 3.2) is observable while the request is in flight.
  await holdPostFor(page, "**/api/v1/cases", 5000);

  // (1) land on "/" — instant pitch + title.
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveTitle("Procedural Detective");
  await expect(
    page.getByText("Describe a crime. AI builds a logically solvable 3D investigation."),
  ).toBeVisible();
  await expect(page.getByTestId("try-demo")).toBeVisible();
  await page.screenshot({ path: "artifacts/screenshots/phase8-landing.png", fullPage: true });

  // (2) the FULL journey runs in the browser — /new with the documented prompt
  // typed into the real textarea (DEF-054 note in the file header: the
  // "Try Demo Case" button itself currently ships the failing human-readable
  // example; the milestone-gate journey uses the canonical id form so the
  // whole pipeline is still exercised END-TO-END through the real UI).
  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("prompt-form")).toBeVisible();
  await page.getByTestId("prompt-input").fill(GOLDEN_ID_PROMPT);
  await page.getByTestId("generate-case").click();
  await expect(page).toHaveURL(/\/generating$/, { timeout: 15_000 });

  // (3) generation route: friendly staged progress with a REQUIRED label.
  await expect(page.getByTestId("generation-progress").first()).toBeVisible({ timeout: 15_000 });
  const stageLabel = page.getByTestId("generation-stage-label");
  await expect(stageLabel).toHaveText(REQUIRED_STAGE_LABELS, { timeout: 15_000 });
  test.info().attach("generation-stage-label.txt", {
    body: `observed stage label: ${await stageLabel.innerText()}`,
    contentType: "text/plain",
  });
  await page.screenshot({ path: "artifacts/screenshots/phase8-generation-progress.png", fullPage: true });

  // (4) PUBLISHED -> "Enter investigation" appears; seize it immediately (the
  // route auto-enters ~900ms after PUBLISHED).
  await expect(page.getByTestId("enter-investigation")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("enter-investigation").click().catch(() => {});
  await awaitSceneReady(page, "journey-test1-initial");
  // Objective + empty discovered summary before any interaction.
  await expect(page.getByTestId("objective-text")).toContainText("Find evidence, then accuse someone.");
  await expect(page.getByTestId("discovered-summary")).toContainText("Nothing discovered yet");

  // (5) discover the KNIFE (object button -> toast -> panel).
  await page.getByTestId("object-kitchen_knife").click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("discovery-toast-text")).toContainText("Discovered");
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");
  await page.screenshot({ path: "artifacts/screenshots/phase8-scene-knife.png", fullPage: true });
  // Esc closes the panel.
  await page.keyboard.press("Escape");
  await expect(panel).not.toBeVisible();

  // (6) objective + discovered summary update after the knife.
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 1 /", { timeout: 15_000 });

  // (7) discover the LAPTOP -> read the EMAIL (subject/body).
  const laptop = page.getByTestId("object-apartment_laptop");
  await expect(laptop).toBeVisible({ timeout: 20_000 });
  await laptop.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const emailPanel = page.getByTestId("evidence-panel");
  await expect(emailPanel).toBeVisible({ timeout: 15_000 });
  await expect(emailPanel).toContainText("Re: the missing funds");
  await expect(emailPanel).toContainText("We need to talk tonight");
  await page.screenshot({ path: "artifacts/screenshots/phase8-scene-email.png", fullPage: true });
  await page.getByTestId("evidence-close").click().catch(() => {});
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(emailPanel).not.toBeVisible();
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 2 /", { timeout: 15_000 });

  // (8) open the accusation route from the scene.
  const accuseOpen = page.getByTestId("accusation-open");
  await expect(accuseOpen).toBeVisible();
  await accuseOpen.click();
  await expect(page).toHaveURL(/\/accuse$/, { timeout: 15_000 });
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("accusation-form")).toBeVisible();
  await page.screenshot({ path: "artifacts/screenshots/phase8-accusation.png", fullPage: true });

  // (9) pick the golden WHO/WHY/WEAPON + time, confirm, and accept.
  await page.getByTestId("accusation-option-murdererId-thomas_reed").check();
  await page.getByTestId("accusation-option-motiveId-cover_up_embezzlement").check();
  await page.getByTestId("accusation-option-weaponId-kitchen_knife").check();
  await page.getByTestId("accusation-time").fill("22:17");
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("accusation-summary-murderer")).toHaveText("Thomas Reed");
  await expect(page.getByTestId("accusation-summary-motive")).toContainText("embezzlement");
  await expect(page.getByTestId("accusation-summary-weapon")).toHaveText("Kitchen Knife");
  await expect(page.getByTestId("accusation-summary-time")).toHaveText("22:17");
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });

  // (10) PRE-REVEAL leak scan: snapshot every API response received so far.
  const preReveal = scanPreReveal(net.captured);
  console.log("PHASE8_PRE_REVEAL_LEAKSCAN", JSON.stringify(preReveal));
  await test.info().attach("phase8-pre-reveal-leak-scan.json", {
    body: JSON.stringify({
      scanned: preReveal.scanned,
      matched: preReveal.matched,
      forbiddenKeys: [...PRE_REVEAL_FORBIDDEN_KEYS],
    }, null, 2),
    contentType: "application/json",
  });
  expect(preReveal.matched, `no forbidden key paths in ${preReveal.scanned} pre-reveal responses`).toEqual([]);
  expect(preReveal.scanned, "pre-reveal API responses must have been captured").toBeGreaterThan(0);

  // (11) reveal the case.
  await page.getByTestId("reveal-case").click();
  await expect(page).toHaveURL(/\/reveal$/, { timeout: 15_000 });
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });

  // (12) truth displayed: Thomas Reed / embezzlement / Kitchen Knife / 22:17.
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
  await expect(page.getByTestId("reveal-timeline")).toBeVisible();
  await page.screenshot({ path: "artifacts/screenshots/phase8-reveal.png", fullPage: true });
  const revealText = await page.getByTestId("reveal-screen").innerText();
  await test.info().attach("phase8-reveal-screen-text.txt", { body: revealText, contentType: "text/plain" });

  // (13) RELOAD /reveal -> reveal persists.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
  await expect(page.getByTestId("reveal-truth-time")).toHaveText("22:17");
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await page.screenshot({ path: "artifacts/screenshots/phase8-reveal-reload.png", fullPage: true });

  // (b) no failed assets, (c) no external traffic, no page/console errors.
  expect(net.failed, `no failed asset/API requests (${net.failed.length} failures)`).toEqual([]);
  console.log("PHASE8_NET_FAILED", JSON.stringify(net.failed));
  await test.info().attach("phase8-failed-responses.json", {
    body: JSON.stringify(net.failed, null, 2),
    contentType: "application/json",
  });
  expect(net.external, "no unexpected external network traffic").toEqual([]);
  console.log("PHASE8_NET_EXTERNAL", JSON.stringify({ count: net.external.length, urls: net.external }));
  await test.info().attach("phase8-external-traffic.json", {
    body: JSON.stringify(net.external, null, 2),
    contentType: "application/json",
  });
  console.log("PHASE8_PAGE_ERRORS", JSON.stringify(net.pageErrors));
  expect(net.pageErrors, "no uncaught page errors").toEqual([]);
  console.log("PHASE8_CONSOLE_ERRORS", JSON.stringify(net.consoleErrors));
  await test.info().attach("phase8-console-errors.json", {
    body: JSON.stringify(net.consoleErrors, null, 2),
    contentType: "application/json",
  });
});

// ---------------------------------------------------------------------------
// TEST 2 — SECOND FRESH RUN: /scene reload mid-investigation persists.
// ---------------------------------------------------------------------------

test("Phase 8 P second fresh run: reload /scene mid-investigation keeps discovered/read", async ({ page }) => {
  const net = installNetworkObservers(page);
  await holdPostFor(page, "**/api/v1/cases", 2500);

  // Fresh run entirely in the browser (a new playthrough through the landing).
  await startDemoJourney(page);
  await awaitSceneReady(page, "journey-test2-initial");

  // Discover knife + read email (same golden objects).
  await page.getByTestId("object-kitchen_knife").click();
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  const laptop = page.getByTestId("object-apartment_laptop");
  await expect(laptop).toBeVisible({ timeout: 20_000 });
  await laptop.click();
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("evidence-panel")).toContainText("Re: the missing funds");
  await page.getByTestId("evidence-close").click().catch(() => {});
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 2 /");

  // RELOAD mid-investigation -> discovered/read persist from the server.
  await page.reload({ waitUntil: "domcontentloaded" });
  await awaitSceneReady(page, "journey-test2-reload");
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 2 /");
  const entries = page.locator('[data-testid^="discovered-entry-"]');
  expect(await entries.count(), "both discovered evidence entries survive the reload").toBe(2);
  for (const text of await entries.allTextContents()) {
    expect(text, "each surviving entry keeps its read marker").toContain("· read");
  }
  await page.screenshot({ path: "artifacts/screenshots/phase8-reload-persists.png", fullPage: true });

  const reloadScan = scanPreReveal(net.captured);
  console.log("PHASE8_RELOAD_LEAKSCAN", JSON.stringify(reloadScan));
  expect(reloadScan.matched, "second-run session must stay leak-free before reveal").toEqual([]);
  expect(net.failed, "no failed asset/API requests on the second run").toEqual([]);
  expect(net.external, "no unexpected external traffic on the second run").toEqual([]);
  expect(net.pageErrors, "no uncaught page errors on the second run").toEqual([]);
  console.log("PHASE8_CONSOLE_ERRORS_TEST2", JSON.stringify(net.consoleErrors));
});

// ---------------------------------------------------------------------------
// SPA-ROUTING check — direct refresh on deep links must serve index.html (200)
// via the preview server's SPA fallback and the app must render.
// ---------------------------------------------------------------------------

test("Phase 8 P SPA routing: direct refresh on /scene /accuse /reveal serves index.html + app renders", async ({
  page,
  request,
}) => {
  // Valid local state: a real playthrough credential (public API, like the page flow).
  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);

  const documentResponses: Array<{ url: string; status: number; contentType: string }> = [];
  page.on("response", async (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    const headers = typeof response.headers === "function" ? response.headers() : response.headers;
    const contentType = headers["content-type"] ?? headers["Content-Type"] ?? "";
    if (contentType.includes("text/html")) {
      documentResponses.push({ url, status: response.status(), contentType });
    }
  });

  const assertSpaRefresh = async (path: string, renderAssert: () => Promise<void>) => {
    const before = documentResponses.length;
    await page.goto(path, { waitUntil: "domcontentloaded" });
    await renderAssert();
    const docs = documentResponses.slice(before);
    expect(docs.length, `document response for ${path}`).toBeGreaterThan(0);
    for (const doc of docs) {
      expect(doc.status, `${path} document must be 200`).toBe(200);
      expect(doc.contentType, `${path} document must be served as HTML`).toContain("text/html");
    }
  };

  // /scene with a PLAYING playthrough -> the investigation renders.
  await assertSpaRefresh("/scene", async () => {
    await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  });

  // /accuse -> the accusation panel renders (still PLAYING).
  await assertSpaRefresh("/accuse", async () => {
    await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("accusation-form")).toBeVisible();
  });

  // /reveal -> the app renders the sealed-truth state (server-gated, no leak).
  await assertSpaRefresh("/reveal", async () => {
    await expect(page.getByTestId("reveal-error")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("reveal-error")).toContainText("The truth is still sealed");
  });

  await test.info().attach("spa-routing-documents.json", {
    body: JSON.stringify(documentResponses, null, 2),
    contentType: "application/json",
  });
  console.log("PHASE8_SPA_DOCUMENTS", JSON.stringify(documentResponses));

  // Final per-spec scene-ready log (Phase 8 R report input).
  console.log("PHASE8_SCENE_READY_MS", JSON.stringify(sceneReadyLog));
});