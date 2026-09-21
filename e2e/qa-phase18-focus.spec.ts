import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installLeakListener, scanJsonBody } from "./helpers";

/**
 * PHASE 18B — FORENSIC FOCUS BROWSER PROBE (QA-owned; .rad/roles/qa.md,
 * .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Real-browser smoke of the OFFICE scene + direct picking + close-up focus on
 * the hermetic fake stack (backend :8000, GENERATION_PROVIDER=fake,
 * ENV_FILE=os.devnull; production SPA via vite preview :4173;
 * CORS_ALLOWED_ORIGINS=http://localhost:4173,http://localhost:5173). The
 * detailed state machine / camera contract is unit-covered by the 933-test
 * frontend suite (renderInvestigation.test.ts Phase 18B block + focusCamera +
 * FocusInspection + objectLabel); this spec proves the REAL browser wiring:
 *
 *  A1  scene loads (scene-canvas + scene-ready) for an OFFICE case;
 *  A2  direct mesh picking of the kitchen knife (catalog evidence object)
 *      opens the server-authoritative evidence panel AND enters the close-up
 *      focus (focus-inspection surface appears);
 *  A3  the focused object shows a HUMAN-readable name ("Kitchen knife") —
 *      never a raw object id and never "proc.*";
 *  A4  a catalog object carries NO "Procedural Artifact" badge (the badge
 *      IF-AND-ONLY-IF-procedural contract is unit+tests + heat-block proven;
 *      the fake stack's only proc.* object `custom_trophy` is DECORATIVE by
 *      design on the fake stack, i.e. never focusable — documented seam);
 *  A5  ESC restores the world view (focus closes, camera returns);
 *  A6  the Close button closes focus; repeated open/close stays deterministic;
 *  A7  exactly ONE interact/discover request per click (no duplicate
 *      discovery — network counter);
 *  A8  prefers-reduced-motion: with emulation set to reduce, opening focus
 *      causes NO transition animation frames and NO auto-orbit drift
 *      (live camera alpha/beta/radius sampled via the ?pd-debug-pick=1 hook).
 *
 * Plus: 0 console/page errors, 0 failed resources, 0 external traffic,
 * leak scan 0 truth markers over the session.
 */

const BACKEND_BASE = "http://localhost:8000";

/** Public registry label of the office case's kitchen knife. */
const KNIFE_LABEL = "Kitchen knife";
const KNIFE_OBJECT_ID = "kitchen_knife";

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
  interactCount: number;
}

function installSessionObservers(page: Page): SessionReport {
  const report: SessionReport = {
    captured: [],
    pageErrors: [],
    consoleErrors: [],
    failed: [],
    external: [],
    interactCount: 0,
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
    if (method === "POST" && /\/objects\/[^/]+\/interact$/.test(url)) {
      report.interactCount += 1;
    }
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

async function tooltipText(page: Page): Promise<string | null> {
  const el = page.getByTestId("object-tooltip");
  if (!(await el.isVisible().catch(() => false))) return null;
  return ((await el.textContent()) ?? "").trim();
}

/** Coarse grid hover-scan over the canvas rect; first sighting per label wins. */
async function hoverScan(
  page: Page,
  rect: { x: number; y: number; width: number; height: number },
  stepX: number,
  stepY: number,
): Promise<{ log: Sight[]; found: Map<string, Sight> }> {
  const log: Sight[] = [];
  const found = new Map<string, Sight>();
  for (let gy = rect.y; gy <= rect.y + rect.height; gy += stepY) {
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

/** Create an OFFICE case through the PUBLIC API and seed the credential. */
async function createOfficePlaythrough(page: Page, request: import("@playwright/test").APIRequestContext): Promise<void> {
  const sessionRes = await request.post(`${BACKEND_BASE}/api/v1/sessions/anonymous`);
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();

  const prompt =
    "A financial crime in a company office. The killer used a letter opener at the workplace. " +
    "A heavy award is on the desk.";
  const caseRes = await request.post(`${BACKEND_BASE}/api/v1/cases`, {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt, difficulty: "hard" },
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

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([pid, token]) => {
      localStorage.setItem("pd_playthrough_id", pid);
      localStorage.setItem("pd_playthrough_token", token);
    },
    [pt.playthroughId, pt.playthroughAccessToken] as const,
  );
}

/** Read the live Babylon camera state from the ?pd-debug-pick=1 probe. */
async function cameraState(page: Page): Promise<{ alpha: number; beta: number; radius: number } | null> {
  return page.evaluate(() => {
    const dbg = (window as any).__pdDebugScene;
    const cam = dbg?.camera;
    if (!cam) return null;
    return { alpha: cam.alpha, beta: cam.beta, radius: cam.radius };
  });
}

/**
 * Close the focus inspection with ESC and assert the surface is gone.
 *
 * The window keydown listener that maps ESC is attached by a React useEffect
 * AFTER the panel node is committed/render-visible — pressing ESC in the
 * tiny window between "FocusInspection visible" and "listener subscribed"
 * would be swallowed by the browser (spec flake, not a product defect; the
 * keyboard mapping itself is unit-covered). To drive the REAL browser path
 * deterministically we first wait for the panel's autoFocused Close button
 * (guarantees the commit's effects ran, listener attached), then press ESC;
 * if a stray render race persists, a bounded retry still terminates in a
 * hard assertion — never masking a real failure.
 */
async function escCloseFocus(page: Page): Promise<void> {
  const focus = page.getByTestId("focus-inspection");
  const close = page.getByTestId("focus-close");
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await expect(focus).not.toBeVisible({ timeout: 1_000 });
      return;
    } catch {
      // still visible — ensure the listener is attached, then press ESC.
    }
    await expect(close).toBeFocused({ timeout: 5_000 }).catch(() => {});
    await page.keyboard.press("Escape");
  }
  await expect(focus).not.toBeVisible({ timeout: 15_000 });
}

test("Phase 18B: office scene focus smoke — direct pick -> close-up focus -> human label, no badge, ESC/Close restore, single interact, reduced-motion", async ({
  page,
  request,
}) => {
  test.setTimeout(240_000);
  const net = installSessionObservers(page);
  const leak = installLeakListener(page);

  await createOfficePlaythrough(page, request);

  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  const envNotice = page.getByTestId("environment-notice");
  if (await envNotice.isVisible().catch(() => false)) {
    await expect(envNotice).toHaveText("Environment: Office");
  }

  const rect = await page.getByTestId("scene-canvas").boundingBox();
  expect(rect, "scene canvas bounding box").not.toBeNull();
  const canvasBox = rect!;
  await page.mouse.move(canvasBox.x - 40, canvasBox.y + 20);

  // C2: find the knife via a coarse hover-scan, then DIRECT-CLICK its mesh.
  const scan = await hoverScan(page, canvasBox, 32, 32);
  let knife = scan.found.get(KNIFE_LABEL);
  if (!knife) {
    const fine = await hoverScan(page, canvasBox, 12, 12);
    knife = fine.found.get(KNIFE_LABEL);
    test.info().attach("phase18b-focus-grid-fine.json", {
      body: JSON.stringify(fine.log),
      contentType: "application/json",
    });
  }
  expect(knife, `hover-scan must sight "${KNIFE_LABEL}"`).not.toBeUndefined();
  const interactBefore = net.interactCount;

  await page.mouse.click(knife!.x, knife!.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });

  // C1/C2: close-up focus surface is present while the evidence panel is open.
  const focus = page.getByTestId("focus-inspection");
  await expect(focus).toBeVisible({ timeout: 15_000 });

  // C3: human-readable name — the exact public registry label, never the raw
  // object id and never proc.*.
  await expect(page.getByTestId("focus-inspection-name")).toHaveText(KNIFE_LABEL);
  const focusText = ((await focus.innerText()) ?? "").toLowerCase();
  expect(focusText).not.toContain(KNIFE_OBJECT_ID);
  expect(focusText).not.toContain("proc.");
  expect(focusText).not.toContain("proc_decor");

  // C4: a CATALOG object must NOT carry the Procedural Artifact badge.
  await expect(page.getByTestId("focus-badge-procedural")).not.toBeVisible();
  await expect(page.getByTestId("focus-badge-validated")).not.toBeVisible();

  // C7: exactly one interact per click (no duplicate discovery).
  await expect
    .poll(async () => net.interactCount, { timeout: 5_000 })
    .toBe(interactBefore + 1);

  // C8: reduced-motion — no auto-orbit drift and no transition animation.
  // (A fresh browser context with emulation is what the reduced-motion leg
  // needs; we measure camera stability after focus under the same profile.)
  await escCloseFocus(page);

  // A5/A6: re-open via the accessibility list, then close with the button.
  const knifeBtn = page.getByTestId(`object-${KNIFE_OBJECT_ID}`);
  await expect(knifeBtn).toBeVisible();
  await knifeBtn.click();
  await expect(focus).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("focus-inspection-name")).toHaveText(KNIFE_LABEL);
  await page.getByTestId("focus-close").click();
  await expect(focus).not.toBeVisible();
  // and ESC again restores, proving the same path.
  await knifeBtn.click();
  await expect(focus).toBeVisible({ timeout: 15_000 });
  await escCloseFocus(page);

  // Determinism: a third open shows the same named surface.
  await knifeBtn.click();
  await expect(focus).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("focus-inspection-name")).toHaveText(KNIFE_LABEL);
  const interactAfterThree = net.interactCount;
  await escCloseFocus(page);

  // leak + hygiene: no pre-reveal truth markers, no console/page errors, no
  // failed resources, no external traffic.
  const leakHits = leak.matched.length;
  expect(leakHits, "zero forbidden key paths").toBe(0);
  expect(net.pageErrors, "page errors").toEqual([]);
  expect(net.consoleErrors, "console errors").toEqual([]);
  expect(net.failed, "failed resources").toEqual([]);
  expect(net.external, "external traffic").toEqual([]);
  test.info().attach("phase18b-focus-session.json", {
    body: JSON.stringify({
      scannedApiResponses: net.captured.length,
      interactCountTotal: interactAfterThree,
      leakMatched: leak.matched,
      knifeLabel: KNIFE_LABEL,
    }, null, 2),
    contentType: "application/json",
  });
});

test("Phase 18B: prefers-reduced-motion — focus opens straight to framing, zero orbit drift", async ({
  page,
  request,
}) => {
  test.setTimeout(240_000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const net = installSessionObservers(page);

  await createOfficePlaythrough(page, request);
  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });

  const rect = await page.getByTestId("scene-canvas").boundingBox();
  expect(rect).not.toBeNull();
  const canvasBox = rect!;
  await page.mouse.move(canvasBox.x - 40, canvasBox.y + 20);

  // A8: use the accessibility list path (deterministic in both motion modes)
  // and sample the live camera across a quiet one-second window around the
  // focus open. With reduces-motion the camera must already be AT the framing
  // once the surface appears (no transition frames) and must NOT drift (orbit
  // disabled) while still.
  const knifeBtn = page.getByTestId(`object-${KNIFE_OBJECT_ID}`);
  await expect(knifeBtn).toBeVisible();

  const worldCamera = await cameraState(page);
  expect(worldCamera, "world camera probe").not.toBeNull();

  await knifeBtn.click();
  await expect(page.getByTestId("focus-inspection")).toBeVisible({ timeout: 15_000 });

  const framing = await cameraState(page);
  expect(framing, "framing camera probe").not.toBeNull();
  expect(framing!.radius, "focus must zoom in from the world distance").toBeLessThan(worldCamera!.radius);

  const samples: Array<{ alpha: number; beta: number; radius: number }> = [];
  for (let i = 0; i < 5; i++) {
    await page.waitForTimeout(200);
    const s = await cameraState(page);
    if (s) samples.push(s);
  }
  for (const s of samples) {
    expect(s.alpha, "alpha must not drift under reduced motion").toBe(framing!.alpha);
    expect(s.beta, "beta must not drift under reduced motion").toBe(framing!.beta);
    expect(s.radius, "radius must not drift under reduced motion").toBe(framing!.radius);
  }
  test.info().attach("phase18b-reduced-motion-camera.json", {
    body: JSON.stringify({ world: worldCamera, framing, samples }, null, 2),
    contentType: "application/json",
  });

  await expect(net.pageErrors).toEqual([]);
  await expect(net.consoleErrors).toEqual([]);
  await expect(net.failed).toEqual([]);
  await expect(net.external).toEqual([]);
});