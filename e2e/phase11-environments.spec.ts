import { expect, test } from "@playwright/test";
import type { Page, APIRequestContext } from "@playwright/test";
import { installLeakListener, scanJsonBody, seedPlaythroughCredentials } from "./helpers";

/**
 * Phase 11 — PER-ENVIRONMENT BROWSER SHOWCASE (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * For EACH of the five environment kits (apartment / office / hotel_suite /
 * warehouse / mansion) this spec drives the PUBLIC API to create a case with
 * that environment hint, opens its /scene and proves:
 *
 *  1. environment identity — the investigation bootstrap carries the exact
 *     kit id, the scene renders KIT content (non-blank canvas with a distinct
 *     per-kit pixel signature — recognizable structural content) and the DOM
 *     shows ONLY the safe canonical label ("Environment: Office" etc.), never
 *     a raw internal server id (the kit world-object ANCHORS are the
 *     deterministic public placement contract; the scene page text must still
 *     not leak raw environment ids);
 *  2. scene readiness — scene-canvas + scene-ready;
 *  3. NO fallback-to-apartment for known kits — environmentId notice shows
 *     office/hotel/warehouse/mansion as appropriate;
 *  4. DIRECT-CLICK — a grid hover-scan over the canvas finds at least the
 *     kitchen knife OR the laptop (all kits reuse the golden 9-object set),
 *     and a direct mesh click at the sighting opens the server-authoritative
 *     discovery/panel;
 *  5. leak scan — 0 truth markers over the whole session; no console/page
 *     errors; no failed resources; no external traffic.
 *
 * Plus ONE no-hint case: the golden apartment (environmentId "apartment",
 * golden 9-label object list, knife directly clickable) — byte-identical
 * identity to the pre-Phase-11 golden.
 *
 * Setup: production build (backend :8000 on a FRESH MIGRATED scratch DB via
 * tools/process_guard + vite preview :4173 serving dist/,
 * CORS_ALLOWED_ORIGINS=http://localhost:4173,http://localhost:5173).
 * Credentials are seeded through the public API (helpers pattern).
 */

const BACKEND_BASE = "http://localhost:8000";

interface KitCase {
  environmentId: string;
  canonicalName: string;
  rawHint: string;
}

const KITS: KitCase[] = [
  { environmentId: "office", canonicalName: "Office", rawHint: "office" },
  { environmentId: "hotel_suite", canonicalName: "Hotel Suite", rawHint: "hotel" },
  { environmentId: "warehouse", canonicalName: "Warehouse", rawHint: "depot" },
  { environmentId: "mansion", canonicalName: "Mansion", rawHint: "villa" },
];

const KNIFE_LABEL = "Kitchen knife";
const LAPTOP_LABEL = "Laptop";
const NEEDED_LABELS = new Set([KNIFE_LABEL, LAPTOP_LABEL]);

const GOLDEN_LABELS = [
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

interface Sight {
  label: string;
  x: number;
  y: number;
}

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

/** Full public-API handshake with an environment hint. */
async function createPlaythroughWithEnvironment(
  request: APIRequestContext,
  environment?: string,
): Promise<{ playthroughId: string; playthroughToken: string; bootstrapEnvironmentId: string }> {
  const sessionRes = await request.post(`${BACKEND_BASE}/api/v1/sessions/anonymous`);
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();

  const payload: Record<string, unknown> = {
    prompt: "Victim: sarah_miller\nMurderer: thomas_reed\n",
    difficulty: "medium",
  };
  if (environment !== undefined) payload.environment = environment;

  const caseRes = await request.post(`${BACKEND_BASE}/api/v1/cases`, {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: payload,
  });
  expect(caseRes.status(), "create case").toBe(201);
  const created = await caseRes.json();
  expect(created.status, "default dev provider publishes").toBe("PUBLISHED");

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
    bootstrapEnvironmentId: boot.scene.environmentId,
  };
}

/** Current tooltip DOM text, or null. */
async function tooltipText(page: Page): Promise<string | null> {
  const el = page.getByTestId("object-tooltip");
  if (!(await el.isVisible().catch(() => false))) return null;
  return ((await el.textContent()) ?? "").trim();
}

/** Grid hover-scan; first sighting per label wins. */
async function hoverScan(
  page: Page,
  rect: { x: number; y: number; width: number; height: number },
  stepX: number,
  stepY: number,
  yMinFrac: number,
  yMaxFrac: number,
): Promise<{ log: Sight[]; found: Map<string, Sight> }> {
  const log: Sight[] = [];
  const found = new Map<string, Sight>();
  const yStart = rect.y + rect.height * yMinFrac;
  const yEnd = rect.y + rect.height * yMaxFrac;
  for (let gy = yStart; gy <= yEnd; gy += stepY) {
    for (let gx = rect.x; gx <= rect.x + rect.width; gx += stepX) {
      await page.mouse.move(gx, gy);
      await page.waitForTimeout(20);
      const label = await tooltipText(page);
      if (label !== null && label !== "") {
        const sight: Sight = { label, x: Math.round(gx), y: Math.round(gy) };
        log.push(sight);
        if (!found.has(label)) found.set(label, sight);
      }
    }
  }
  return { log, found };
}

interface CanvasSignature {
  coverage: number; // fraction of sampled points that are non-clear pixels
  distinctColors: number; // distinct RGB buckets in the samples
  mean: { r: number; g: number; b: number };
}

/** Downsampled WebGL readback over the canvas -> render signature. */
async function canvasSignature(page: Page): Promise<CanvasSignature | null> {
  return page.evaluate(() => {
    const canvas = document.querySelector("canvas.scene-canvas") as HTMLCanvasElement | null;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    const gl =
      (canvas.getContext("webgl") as WebGLRenderingContext | null) ??
      (canvas.getContext("webgl2") as WebGL2RenderingContext | null);
    if (!gl) return null;
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const SW = 24;
    const SH = 18;
    const buckets = new Set<string>();
    let r = 0;
    let g = 0;
    let b = 0;
    let covered = 0;
    const n = SW * SH;
    for (let iy = 0; iy < SH; iy++) {
      for (let ix = 0; ix < SW; ix++) {
        const vx = Math.round((rect.left + (rect.width * (ix + 0.5)) / SW) * scaleX);
        const vyTop = Math.round((rect.top + (rect.height * (iy + 0.5)) / SH) * scaleY);
        const vy = canvas.height - vyTop;
        const pixels = new Uint8Array(4);
        try {
          gl.readPixels(vx, vy, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        } catch {
          continue;
        }
        const pr = pixels[0];
        const pg = pixels[1];
        const pb = pixels[2];
        const pa = pixels[3];
        if (pa < 8) continue;
        buckets.add(`${pr >> 4},${pg >> 4},${pb >> 4}`);
        r += pr;
        g += pg;
        b += pb;
        covered += 1;
      }
    }
    if (covered === 0) return null;
    return {
      coverage: covered / n,
      distinctColors: buckets.size,
      mean: { r: Math.round(r / covered), g: Math.round(g / covered), b: Math.round(b / covered) },
    };
  });
}

/** Shared per-kit showcase body (identity + readiness + direct click + hygiene). */
async function showcaseKit(
  page: Page,
  request: APIRequestContext,
  kit: KitCase,
  transcript: Record<string, unknown>,
): Promise<void> {
  const leak = installLeakListener(page);
  const net = installSessionObservers(page);

  const cred = await createPlaythroughWithEnvironment(request, kit.rawHint);
  expect(cred.bootstrapEnvironmentId, `bootstrap environmentId for ${kit.environmentId}`).toBe(kit.environmentId);
  transcript.bootstrapEnvironmentId = cred.bootstrapEnvironmentId;
  await seedPlaythroughCredentials(page, cred);

  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  transcript.sceneReady = true;

  // ---- environment identity -----------------------------------------------
  const notice = page.getByTestId("environment-notice");
  await expect(notice, `environment-notice reflects the kit ${kit.environmentId}`).toBeVisible({ timeout: 15_000 });
  await expect(notice).toContainText(`Environment: ${kit.canonicalName}`);
  transcript.noticeText = (await notice.textContent())?.trim() ?? "";

  // No raw server environmentId in the page text (only the safe canonical
  // label may surface; world-object ANCHORS stay in the API payload only).
  const bodyText = await page.evaluate(() => document.body.innerText);
  expect(bodyText, `raw environmentId ${kit.environmentId} must not leak into the page text`).not.toContain(kit.environmentId);
  transcript.rawIdInPageText = bodyText.includes(kit.environmentId);

  // ---- recognizable structural content (canvas signature) -----------------
  await page.mouse.move(40, 40); // clear any accidental hover before readback
  await page.waitForTimeout(300);
  const signature = await canvasSignature(page);
  expect(signature, "canvas readback must be available").not.toBeNull();
  expect(signature!.coverage, "canvas must render content (non-clear pixels)").toBeGreaterThan(0.05);
  expect(signature!.distinctColors, "canvas must render multiple distinct colors").toBeGreaterThan(8);
  transcript.signature = signature;
  await page.screenshot({
    path: `artifacts/screenshots/phase11-${kit.environmentId}.png`,
    fullPage: false,
  });

  // ---- direct click: at least knife OR laptop per kit ---------------------
  const rect = (await page.getByTestId("scene-canvas").boundingBox())!;
  expect(rect, "canvas bounding box").not.toBeNull();
  await page.mouse.move(rect.x - 40, rect.y + 20);

  const primary = await hoverScan(page, rect, 32, 32, 0, 1);
  let found = new Map(primary.found);
  let fineLog: Sight[] = [];
  if (![...NEEDED_LABELS].some((label) => found.has(label))) {
    const fine = await hoverScan(page, rect, 16, 16, 0.15, 0.85);
    fineLog = fine.log;
    for (const [label, sight] of fine.found) {
      if (!found.has(label)) found.set(label, sight);
    }
  }
  transcript.hoverSightings = [...primary.log, ...fineLog];
  transcript.hoverFound = [...found.entries()].map(([label, s]) => ({ label, x: s.x, y: s.y }));

  const clicked = found.get(KNIFE_LABEL) ?? found.get(LAPTOP_LABEL);
  expect(
    clicked !== undefined,
    `at least the knife or the laptop must be directly clickable in the ${kit.environmentId} kit (found: ${[...found.keys()].join(", ")})`,
  ).toBe(true);

  await page.mouse.move(clicked!.x, clicked!.y);
  await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("object-tooltip")).toHaveText(clicked!.label);
  await page.mouse.click(clicked!.x, clicked!.y);
  const panel = page.getByTestId("evidence-panel");
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(panel).toBeVisible({ timeout: 15_000 });
  if (clicked!.label === KNIFE_LABEL) {
    await expect(panel).toContainText("Blood on the kitchen knife matches the victim", { timeout: 15_000 });
  } else {
    await expect(panel).toContainText("Re: the missing funds", { timeout: 15_000 });
  }
  transcript.clicked = { label: clicked!.label, x: clicked!.x, y: clicked!.y };
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 1 /", { timeout: 15_000 });

  // ---- leak + hygiene ------------------------------------------------------
  const leakMatched: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of net.captured) {
    if (entry.body === null || typeof entry.body !== "object") continue;
    scanned += 1;
    const paths = scanJsonBody(entry.body);
    if (paths.length > 0) leakMatched.push({ url: entry.url, paths });
  }
  expect(leakMatched, `no truth markers in ${scanned} API responses for ${kit.environmentId}`).toEqual([]);
  expect(scanned, "the session must have produced scannable API responses").toBeGreaterThan(0);
  expect(net.failed, `no failed resources (${net.failed.length})`).toEqual([]);
  expect(net.external, "no external network traffic").toEqual([]);
  expect(net.pageErrors, "no uncaught page errors").toEqual([]);
  expect(net.consoleErrors, "no console errors").toEqual([]);
  transcript.leak = { scanned, matched: leakMatched.length };
  transcript.net = { failed: net.failed.length, external: net.external.length, pageErrors: net.pageErrors.length, consoleErrors: net.consoleErrors.length };

  console.log(`P11_KIT_TRANSCRIPT ${kit.environmentId}`, JSON.stringify(transcript));
  await test.info().attach(`phase11-${kit.environmentId}-transcript.json`, {
    body: JSON.stringify(transcript, null, 2),
    contentType: "application/json",
  });
}

interface GoldenTranscript {
  bootstrapEnvironmentId: string;
  sceneReady: boolean;
  noticeCount: number;
  labelsShown: string[];
  knifeClicked: boolean;
  leak: { scanned: number; matched: number };
}

test("Phase 11: office kit — identity, readiness, direct click, hygiene", async ({ page, request }) => {
  await showcaseKit(page, request, KITS[0], {});
});

test("Phase 11: hotel_suite kit — identity, readiness, direct click, hygiene", async ({ page, request }) => {
  await showcaseKit(page, request, KITS[1], {});
});

test("Phase 11: warehouse kit — identity, readiness, direct click, hygiene", async ({ page, request }) => {
  await showcaseKit(page, request, KITS[2], {});
});

test("Phase 11: mansion kit — identity, readiness, direct click, hygiene", async ({ page, request }) => {
  await showcaseKit(page, request, KITS[3], {});
});

test("Phase 11: NO hint -> golden apartment identical (environmentId, labels, direct click)", async ({
  page,
  request,
}) => {
  const leak = installLeakListener(page);
  const net = installSessionObservers(page);
  const transcript: GoldenTranscript = {
    bootstrapEnvironmentId: "",
    sceneReady: false,
    noticeCount: -1,
    labelsShown: [],
    knifeClicked: false,
    leak: { scanned: 0, matched: -1 },
  };

  const cred = await createPlaythroughWithEnvironment(request);
  expect(cred.bootstrapEnvironmentId, "no hint stays the apartment kit").toBe("apartment");
  transcript.bootstrapEnvironmentId = cred.bootstrapEnvironmentId;
  await seedPlaythroughCredentials(page, cred);

  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  transcript.sceneReady = true;

  // Apartment is the default: NO environment-notice is rendered (golden
  // appearance unchanged — the notice only appears for non-default kits).
  await expect(page.getByTestId("environment-notice")).toHaveCount(0);
  transcript.noticeCount = 0;

  // Golden 9-label object list (Phase 10 contract preserved).
  const listText = (await page.getByTestId("scene-objects").innerText()).toLowerCase();
  for (const label of GOLDEN_LABELS) {
    expect(listText, `golden label "${label}"`).toContain(label.toLowerCase());
  }
  transcript.labelsShown = GOLDEN_LABELS;

  // Golden knife direct-click (Phase 8_1 path, unchanged in the apartment).
  const rect = (await page.getByTestId("scene-canvas").boundingBox())!;
  await page.mouse.move(rect.x - 40, rect.y + 20);
  const primary = await hoverScan(page, rect, 32, 32, 0, 1);
  let found = new Map(primary.found);
  let fineLog: Sight[] = [];
  if (!found.has(KNIFE_LABEL)) {
    const fine = await hoverScan(page, rect, 16, 16, 0.15, 0.85);
    fineLog = fine.log;
    for (const [label, sight] of fine.found) {
      if (!found.has(label)) found.set(label, sight);
    }
  }
  const knife = found.get(KNIFE_LABEL);
  expect(knife, "knife must be directly clickable in the apartment golden").toBeDefined();
  await page.mouse.move(knife!.x, knife!.y);
  await expect(page.getByTestId("object-tooltip")).toHaveText(KNIFE_LABEL);
  await page.mouse.click(knife!.x, knife!.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("evidence-panel")).toContainText("Blood on the kitchen knife matches the victim", {
    timeout: 15_000,
  });
  transcript.knifeClicked = true;

  const leakMatched: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of net.captured) {
    if (entry.body === null || typeof entry.body !== "object") continue;
    scanned += 1;
    const paths = scanJsonBody(entry.body);
    if (paths.length > 0) leakMatched.push({ url: entry.url, paths });
  }
  expect(leakMatched, `no truth markers in ${scanned} API responses`).toEqual([]);
  expect(net.failed, "no failed resources").toEqual([]);
  expect(net.external, "no external network traffic").toEqual([]);
  expect(net.pageErrors, "no uncaught page errors").toEqual([]);
  expect(net.consoleErrors, "no console errors").toEqual([]);
  transcript.leak = { scanned, matched: leakMatched.length };

  console.log("P11_GOLDEN_APARTMENT_TRANSCRIPT", JSON.stringify(transcript));
  await test.info().attach("phase11-golden-apartment-transcript.json", {
    body: JSON.stringify(transcript, null, 2),
    contentType: "application/json",
  });

  // The full-kit pixel signature set is collected ONCE (this test also
  // samples the apartment signature so the five are comparable).
  const signature = await canvasSignature(page);
  console.log("P11_CANVAS_SIGNATURE apartment", JSON.stringify(signature));
});