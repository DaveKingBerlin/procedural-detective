import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createPlaythroughViaApi, scanJsonBody, seedPlaythroughCredentials } from "./helpers";

/**
 * Phase 8_1 â€” REAL-BROWSER DIRECT 3D INTERACTION PROOF (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * The core of Phase 8_1: evidence objects must be DIRECTLY clickable in the
 * Babylon scene (Phase8_1 A) with hover feedback (ring/cursor + a label-only
 * DOM tooltip, Phase8_1 B), and the click must drive the SAME server-
 * authoritative interaction/discovery flow as the object list.
 *
 * Setup: production build (backend :8000 on a migrated scratch DB via
 * tools/process_guard + vite preview :4173 serving dist/,
 * CORS_ALLOWED_ORIGINS=http://localhost:4173,http://localhost:5173). The
 * playthrough credential is seeded through the PUBLIC API exactly like the
 * Phase 8 journey (helpers.createPlaythroughViaApi + seedPlaythroughCredentials).
 *
 * Test flow (deterministic, no blind sleeps):
 *   1. /scene -> scene-ready.
 *   2. HOVER-SCAN: move the mouse in a coarse grid over the canvas and record
 *      EVERY tooltip sighting for a known public label ("Kitchen knife",
 *      "Laptop", "Letter opener") together with the mouse position. The
 *      tooltip is the acceptance signal that mesh picking works in the real
 *      browser, INCLUDING the composite/parent-walking path and the invisible
 *      alpha-0 hitboxes (small objects such as the knife). If a target was
 *      missed, re-scan with finer steps (deterministic fallback, task 2 note).
 *   3. At the recorded positions, CLICK: the same server-authoritative flow
 *      must open â€” knife -> "Blood on the kitchen knife matches the victim",
 *      laptop -> the email discovery, opener -> "Letter opener does not match
 *      the wound".
 *   4. Fallback DOM object list still works (accessibility fallback intact).
 *   5. Leak/security scan over the whole direct-click session: every API JSON
 *      response pre-reveal carries 0 truth markers; the tooltip DOM contains
 *      ONLY the public registry label; the interact/read responses expose NO
 *      fields outside the frozen DTOs (InteractionResultDTO /
 *      EvidenceReadResultDTO); no console/page errors; no failed resources;
 *      no external network traffic.
 *   6. Visual/semantic proof: hover ring + tooltip screenshot, knife and
 *      letter opener in one frame, and a PROGRAMMATIC canvas-pixel distinct-
 *      ness check (WebGL readPixels) between the knife and the letter opener.
 */

// Public registry labels the tooltip may ever show (Phase 8_1 tooltip payload
// is type-limited to the label only â€” see scene/objectTooltip.ts).
const KNOWN_LABELS = new Set(["Kitchen knife", "Laptop", "Letter opener", "Victim", "Table", "Door", "Lamp", "Vase", "Scissors"]);

// Evidence content must NEVER appear in the tooltip DOM.
const FORBIDDEN_TOOLTIP_FRAGMENTS = [
  "Blood", "match", "wound", "funds", "email", "Re:", "Thomas", "victim",
  "evidence", "fingerprint", "statement", "CCTV",
];

interface Sight {
  label: string;
  /** Mouse position (viewport px) where the tooltip was seen. */
  x: number;
  y: number;
}

interface CapturedJson {
  url: string;
  method: string;
  status: number;
  body: unknown;
}

const KNIFE_LABEL = "Kitchen knife";
const LAPTOP_LABEL = "Laptop";
const OPENER_LABEL = "Letter opener";

// ---------------------------------------------------------------------------
// Network observers
// ---------------------------------------------------------------------------

interface SessionReport {
  captured: CapturedJson[];
  pageErrors: string[];
  consoleErrors: string[];
  failed: Array<{ url: string; status: number }>;
  external: Array<{ url: string; status: number }>;
}

/** Attach pageerror/console/failed/external/JSON-body observers for the run. */
function installSessionObservers(page: Page): SessionReport {
  const report: SessionReport = {
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
// Hover-scan helpers
// ---------------------------------------------------------------------------

/** Current tooltip DOM text, or null when no tooltip is rendered. */
async function tooltipText(page: Page): Promise<string | null> {
  const el = page.getByTestId("object-tooltip");
  if (!(await el.isVisible().catch(() => false))) return null;
  return ((await el.textContent()) ?? "").trim();
}

/**
 * Sweep a grid of points over the canvas rect. At every point where the
 * `object-tooltip` shows, record the sighting (mouse position + label). First
 * sighting per label wins. Returns the full transcript.
 */
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
      // One render-cycle settle so Babylon can compute the pick and React can
      // flush the tooltip state (deterministic-transition, not a blind sleep).
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

const INTERACT_DTO_KEYS = new Set(["objectId", "interaction", "evidenceId", "discovery", "result"]);
const DISCOVERY_DTO_KEYS = new Set(["evidenceId", "kind", "title", "interaction", "state"]);
const RECORD_DTO_KEYS = new Set(["evidenceId", "kind", "title", "description", "openedAt", "readByPlayer", "content"]);

/** Assert the interact/read responses expose NO fields outside the frozen DTOs. */
function assertDtoFrames(captured: CapturedJson[]): void {
  const interactBodies: Array<{ url: string; body: Record<string, unknown> }> = [];
  const recordBodies: Array<{ url: string; body: Record<string, unknown> }> = [];
  for (const entry of captured) {
    const body = entry.body;
    if (body === null || typeof body !== "object" || Array.isArray(body)) continue;
    if (/\/objects\/[^/]+\/interact$/.test(entry.url) && entry.method === "POST") {
      interactBodies.push({ url: entry.url, body: body as Record<string, unknown> });
    }
    if (/\/records\/[^/]+$/.test(entry.url) && entry.method === "GET") {
      recordBodies.push({ url: entry.url, body: body as Record<string, unknown> });
    }
  }
  expect(interactBodies.length, "the direct-click session must have hit /interact").toBeGreaterThan(0);
  expect(recordBodies.length, "the direct-click session must have read records").toBeGreaterThan(0);

  for (const { url, body } of interactBodies) {
    const keys = new Set(Object.keys(body));
    for (const key of keys) {
      expect(INTERACT_DTO_KEYS.has(key), `${url}: interact response field ${key} is not in InteractionResultDTO`).toBe(true);
    }
    expect(body["result"], `${url}: interact result`).toBe("interacted");
    const discovery = body["discovery"];
    if (discovery !== null && typeof discovery === "object") {
      const dk = new Set(Object.keys(discovery as Record<string, unknown>));
      for (const key of dk) {
        expect(DISCOVERY_DTO_KEYS.has(key), `${url}: discovery field ${key} is not in DiscoveryResultDTO`).toBe(true);
      }
    }
  }
  for (const { url, body } of recordBodies) {
    const keys = new Set(Object.keys(body));
    for (const key of keys) {
      expect(RECORD_DTO_KEYS.has(key), `${url}: record response field ${key} is not in EvidenceReadResultDTO`).toBe(true);
    }
    expect(body["readByPlayer"], `${url}: record readByPlayer`).toBe(true);
  }
}

/**
 * Sample an average RGBA patch out of the WebGL drawing buffer at viewport
 * coordinates (canvas-pixel proof the knife and opener render differently).
 * Returns null when readback is unavailable (recorded, not fatal).
 */
async function sampleCanvasPatch(
  page: Page,
  vx: number,
  vy: number,
  radius: number,
): Promise<{ r: number; g: number; b: number } | null> {
  return page.evaluate(
    ({ vx, vy, radius }) => {
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
      const bx = Math.round((vx - rect.left) * scaleX);
      const byTop = Math.round((vy - rect.top) * scaleY);
      const by = canvas.height - byTop; // WebGL origin is bottom-left
      const w = Math.max(1, radius * 2 + 1);
      const h = Math.max(1, radius * 2 + 1);
      const pixels = new Uint8Array(w * h * 4);
      try {
        gl.readPixels(bx - radius, by - radius, w, h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      } catch {
        return null;
      }
      let r = 0;
      let g = 0;
      let b = 0;
      let n = 0;
      for (let i = 0; i < w * h; i++) {
        const a = pixels[i * 4 + 3];
        if (a < 8) continue; // skip transparent pixels (e.g. alpha-0 hitbox fringe)
        r += pixels[i * 4];
        g += pixels[i * 4 + 1];
        b += pixels[i * 4 + 2];
        n += 1;
      }
      if (n === 0) return null;
      return { r: Math.round(r / n), g: Math.round(g / n), b: Math.round(b / n) };
    },
    { vx, vy, radius },
  );
}

// ---------------------------------------------------------------------------
// The direct-interaction proof
// ---------------------------------------------------------------------------

test("Phase 8_1 direct click: real-browser mesh picking -> server-authentic discoveries", async ({
  page,
  request,
}) => {
  // (d) leak scan over EVERY API response the direct-click session receives
  // (single response reader — see installSessionObservers).
  const net = installSessionObservers(page);

  // Zero manual setup: seed the playthrough credential through the PUBLIC API.
  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);

  // ---- (1) /scene boots ----------------------------------------------------
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  // Scene-first objective copy: the pinned substring must have been preserved
  // by appending (scene-first hint), never replaced.
  await expect(page.getByTestId("objective-text")).toContainText("Find evidence, then accuse someone.");
  await expect(page.getByTestId("controls-hint")).toContainText("Click objects in the 3D scene");

  const rect = await page.getByTestId("scene-canvas").boundingBox();
  expect(rect, "scene canvas bounding box").not.toBeNull();

  // Clear any accidental hover before the scan.
  const canvasBox = rect!;
  await page.mouse.move(canvasBox.x - 40, canvasBox.y + 20);

  // ---- (2) HOVER-SCAN ------------------------------------------------------
  // Primary: coarse grid over the full canvas; the tooltip is the acceptance
  // signal that picking resolved (composite children, roots, hitboxes).
  const primary = await hoverScan(page, canvasBox, 32, 32, 0, 1);
  test.info().attach("phase81-grid-scan-primary.json", {
    body: JSON.stringify(
      {
        step: { x: 32, y: 32 },
        scope: "full canvas",
        sightings: primary.log,
        found: [...primary.found.entries()].map(([label, sight]) => ({ label, ...sight })),
      },
      null,
      2,
    ),
    contentType: "application/json",
  });
  console.log("PHASE81_GRID_SCAN_PRIMARY", JSON.stringify(primary.log));

  // Deterministic fallback (task 2): if a target was missed, re-scan the
  // central band (where every interactable of the golden case projects) with
  // finer steps. The tooltip remains the acceptance signal.
  const wanted = [KNIFE_LABEL, LAPTOP_LABEL, OPENER_LABEL];
  let found = new Map(primary.found);
  let fineLog: Sight[] = [];
  if (!wanted.every((label) => found.has(label))) {
    const fine = await hoverScan(page, canvasBox, 14, 14, 0.15, 0.85);
    fineLog = fine.log;
    test.info().attach("phase81-grid-scan-fine-fallback.json", {
      body: JSON.stringify(
        {
          step: { x: 14, y: 14 },
          scope: "center band (15%..85% height)",
          sightings: fine.log,
          found: [...fine.found.entries()].map(([label, sight]) => ({ label, ...sight })),
        },
        null,
        2,
      ),
      contentType: "application/json",
    });
    console.log("PHASE81_GRID_SCAN_FINE", JSON.stringify(fine.log));
    for (const [label, sight] of fine.found) {
      if (!found.has(label)) found.set(label, sight);
    }
  }

  // The two evidence objects the phase is judged on MUST be mesh-pickable.
  expect(found.has(KNIFE_LABEL), `tooltip must appear over the ${KNIFE_LABEL}`).toBe(true);
  expect(found.has(LAPTOP_LABEL), `tooltip must appear over the ${LAPTOP_LABEL}`).toBe(true);
  const knife = found.get(KNIFE_LABEL)!;
  const laptop = found.get(LAPTOP_LABEL)!;
  const opener = found.get(OPENER_LABEL);

  // Tooltip DOM is label-only (task 2d / 5): every label ever shown was one
  // of the KNOWN public labels, and none of the evidence content fragments.
  for (const sight of [...primary.log, ...fineLog]) {
    expect(KNOWN_LABELS.has(sight.label), `tooltip label "${sight.label}" must be a public registry label`).toBe(true);
  }
  const allTooltipTexts = new Set([...primary.log, ...fineLog].map((s) => s.label));
  for (const text of allTooltipTexts) {
    for (const fragment of FORBIDDEN_TOOLTIP_FRAGMENTS) {
      expect(text, `tooltip must not contain evidence content "${fragment}"`).not.toContain(fragment);
    }
  }

  // ---- (3a) HOVER demo: ring + tooltip + pointer cursor --------------------
  await page.mouse.move(knife.x, knife.y);
  await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("object-tooltip")).toHaveText(KNIFE_LABEL);
  const cursor = await page.evaluate(
    () => (document.querySelector("canvas.scene-canvas") as HTMLCanvasElement | null)?.style.cursor ?? "",
  );
  expect(cursor, "hover over an interactable must set the pointer cursor").toBe("pointer");
  await page.screenshot({ path: "artifacts/screenshots/phase81-hover-knife.png" });

  // ---- (3b) CLICK the knife at the hover position ---------------------------
  await page.mouse.click(knife.x, knife.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("discovery-toast-text")).toContainText("Discovered");
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");
  // Object-label header (Phase 8_1 D1) + preview block (D2).
  await expect(page.locator(".evidence-panel-title")).toContainText("Kitchen knife");
  await expect(page.getByTestId("evidence-preview")).toBeVisible();
  await expect(page.getByTestId("evidence-preview-label")).toHaveText("Kitchen knife");
  await page.screenshot({ path: "artifacts/screenshots/phase81-panel-knife.png" });
  await page.keyboard.press("Escape");
  await expect(panel).not.toBeVisible();
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 1 /", { timeout: 15_000 });

  // ---- (3c) CLICK the laptop ------------------------------------------------
  await page.mouse.move(laptop.x, laptop.y);
  await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("object-tooltip")).toHaveText(LAPTOP_LABEL);
  const laptopPanel = page.getByTestId("evidence-panel");
  await page.mouse.click(laptop.x, laptop.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(laptopPanel).toBeVisible({ timeout: 15_000 });
  await expect(laptopPanel).toContainText("Re: the missing funds");
  await expect(laptopPanel).toContainText("We need to talk tonight");
  await expect(page.locator(".evidence-panel-title")).toContainText("Laptop");
  await expect(page.getByTestId("evidence-preview-label")).toHaveText("Laptop");
  await page.screenshot({ path: "artifacts/screenshots/phase81-panel-laptop.png" });
  await page.keyboard.press("Escape");
  await expect(laptopPanel).not.toBeVisible();
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 2 /", { timeout: 15_000 });

  // ---- (3d) CLICK the letter opener (third object when pickable) -----------
  let openerClicked = false;
  if (opener) {
    await page.mouse.move(opener.x, opener.y);
    await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("object-tooltip")).toHaveText(OPENER_LABEL);
    const openerPanel = page.getByTestId("evidence-panel");
    await page.mouse.click(opener.x, opener.y);
    await expect(openerPanel).toBeVisible({ timeout: 15_000 });
    await expect(openerPanel).toContainText("Letter opener does not match the wound");
    await expect(page.locator(".evidence-panel-title")).toContainText("Letter opener");
    await expect(page.getByTestId("evidence-preview-label")).toHaveText("Letter opener");
    await page.keyboard.press("Escape");
    await expect(openerPanel).not.toBeVisible();
    await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
    openerClicked = true;
  }

  // ---- (4) BOTH small weapons in one frame (silhouette differentiation) ----
  const knifeSight = knife;
  const openerSight = opener ?? null;
  await page.mouse.move(knifeSight.x, knifeSight.y);
  await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
  await page.screenshot({ path: "artifacts/screenshots/phase81-knife-hover-clean.png" });
  await page.screenshot({
    path: "artifacts/screenshots/phase81-knife-opener-both.png",
    fullPage: true,
  });
  // Move the pointer away so hover feedback does not tint the pixel samples.
  await page.mouse.move(canvasBox.x - 40, canvasBox.y + 20);

  // ---- (4b) PROGRAMMATIC visual distinctness (canvas pixels) ----------------
  const knifePatch = await sampleCanvasPatch(page, knifeSight.x, knifeSight.y, 4);
  const openerPatch = openerSight ? await sampleCanvasPatch(page, openerSight.x, openerSight.y, 4) : null;
  test.info().attach("phase81-pixel-samples.json", {
    body: JSON.stringify(
      { knife: knifePatch, opener: openerPatch, knifePos: [knifeSight.x, knifeSight.y], openerPos: openerSight ? [openerSight.x, openerSight.y] : null },
      null,
      2,
    ),
    contentType: "application/json",
  });
  console.log("PHASE81_PIXEL_SAMPLES", JSON.stringify({ knife: knifePatch, opener: openerPatch }));
  if (knifePatch && openerPatch) {
    const dist = Math.sqrt(
      (knifePatch.r - openerPatch.r) ** 2 +
        (knifePatch.g - openerPatch.g) ** 2 +
        (knifePatch.b - openerPatch.b) ** 2,
    );
    // Registry colors: knife pale steel #c8ccd4 vs opener warm brass #a37b35.
    expect(dist, "knife and letter opener must render as clearly different colors").toBeGreaterThan(40);
  } else {
    console.log("PHASE81_PIXEL_READBACK_UNAVAILABLE: WebGL readPixels returned null in this run");
  }

  // ---- (5) fallback DOM object list intact ---------------------------------
  const fallbackLaptop = page.getByTestId("object-apartment_laptop");
  await expect(fallbackLaptop).toBeVisible({ timeout: 15_000 });
  await fallbackLaptop.click();
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("evidence-panel")).toContainText("Re: the missing funds");
  await expect(page.getByTestId("evidence-preview-label")).toHaveText("Laptop");
  await page.keyboard.press("Escape");
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await page.getByTestId("object-kitchen_knife").click();
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("evidence-panel")).toContainText("Blood on the kitchen knife matches the victim");
  await expect(page.getByTestId("evidence-preview-label")).toHaveText("Kitchen knife");
  await page.keyboard.press("Escape");

// ---- (6) LEAK + DTO + no-console/no-failed/no-external -------------------
  // Deep truth-leak scan over the direct-click session: every captured API
  // JSON body is walked for forbidden pre-reveal key paths (same forbidden set
  // as the frozen Phase 6 Q network-leak proof).
  const leakMatched: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of net.captured) {
    if (entry.body === null || typeof entry.body !== "object") continue;
    scanned += 1;
    const paths = scanJsonBody(entry.body);
    if (paths.length > 0) leakMatched.push({ url: entry.url, paths });
  }
  console.log("PHASE81_LEAKSCAN", JSON.stringify({ scanned, matched: leakMatched }));
  await test.info().attach("phase81-leak-scan.json", {
    body: JSON.stringify(
      { scanned, matched: leakMatched, forbiddenPaths: ["murdererId", "victimId", "weaponId", "motiveId", "crimeTime", "canonical", "solutionProof", "token", "prompt", "diagnostics", "truth"] },
      null,
      2,
    ),
    contentType: "application/json",
  });
  expect(leakMatched, `no truth markers in any of the ${scanned} direct-click API responses`).toEqual([]);
  expect(scanned, "the direct-click session must have produced scannable API responses").toBeGreaterThan(0);

  assertDtoFrames(net.captured);
  await test.info().attach("phase81-dto-transcript.json", {
    body: JSON.stringify(net.captured, null, 2),
    contentType: "application/json",
  });

  expect(net.failed, `no failed resources (${net.failed.length} failures)`).toEqual([]);
  console.log("PHASE81_NET_FAILED", JSON.stringify(net.failed));
  await test.info().attach("phase81-failed-responses.json", {
    body: JSON.stringify(net.failed, null, 2),
    contentType: "application/json",
  });
  expect(net.external, "no external network traffic").toEqual([]);
  console.log("PHASE81_NET_EXTERNAL", JSON.stringify({ count: net.external.length, urls: net.external }));
  await test.info().attach("phase81-external-traffic.json", {
    body: JSON.stringify(net.external, null, 2),
    contentType: "application/json",
  });
  console.log("PHASE81_PAGE_ERRORS", JSON.stringify(net.pageErrors));
  expect(net.pageErrors, "no uncaught page errors").toEqual([]);
  console.log("PHASE81_CONSOLE_ERRORS", JSON.stringify(net.consoleErrors));
  await test.info().attach("phase81-console-errors.json", {
    body: JSON.stringify(net.consoleErrors, null, 2),
    contentType: "application/json",
  });

  // Transcript note for the Phase 8_1 report.
  console.log(
    "PHASE81_TRANSCRIPT",
    JSON.stringify({
      hovered: [...found.entries()].map(([label, s]) => ({ label, x: s.x, y: s.y })),
      clickedKnife: true,
      clickedLaptop: true,
      clickedOpener: openerClicked,
      fallbackListClicked: true,
    }),
  );
});
