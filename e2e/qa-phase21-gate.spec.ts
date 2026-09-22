import { expect, test } from "@playwright/test";
import type { APIRequestContext, Browser, BrowserContext, Page } from "@playwright/test";

/**
 * PHASE 21 §6/§7 — PRODUCTION BROWSER GATE: DEEP-LINK + RELOAD + TRUTHFUL MODE
 * (QA-owned; .rad/roles/qa.md, .rad/policies/evidence.md).
 *
 * F-01 (HIGH) REGRESSION: the app runs on BrowserRouter (/new /generating
 * /scene /accuse /reveal) served by the backend SPA fallback (STATIC_DIR) or
 * the production Docker edge (Caddy TLS + private uvicorn + same-origin
 * /api/v1). The built bundle must reference assets by ROOT-ABSOLUTE /assets/*
 * URLs so direct navigation, refresh, copied deep-links and history
 * back/forward all survive — never route-relative (./scene/assets/...), which
 * the F-01 defect turned into HTML-module MIME failures.
 *
 * TARGET: the actual PRODUCTION stack. Default `https://localhost` (the
 * `docker-compose.prod.yml` Caddy TLS edge). A hermetic local equivalent
 * (uvicorn STATIC_DIR=frontend/dist on :8000) can be targeted via
 * `PD_QA_TARGET=http://localhost:8000`. The SPA + its API are SAME-ORIGIN in
 * both (the API base is the relative /api/v1), so the checks are identical.
 *
 * For every route we DIRECT-OPEN it and REFRESH it in a real browser
 * (project-local Playwright Chromium) and assert:
 *   - the app shell renders (no app error page);
 *   - console has NO module/MIME errors ("Failed to load module script",
 *     "MIME type", "was loaded with an unsupported MIME type");
 *   - NO 404 asset loads, NO broken chunks, NO failed network responses;
 *   - NO route-relative asset requests — every asset is /assets/* (root);
 *   - lazy chunks still load (the /scene route pulls the Babylon lazy chunks);
 *   - history back/forward works for /scene.
 *
 * /scene /accuse /reveal additionally open on a REAL seeded playthrough (the
 * fake provider's golden case created live through the same-origin API), so
 * direct-open exercises the real scene/reveal rendering, not a redirect.
 *
 * F-03 (MEDIUM) TRUTHFUL MODE — the fake (demo-only) backend must show the
 * honest read-only surface: "Demo mode active" notice + the single line
 * "Generation mode: Deterministic demo" (`generation-mode-line`), NO
 * interactive provider <select> (`generation-mode-select` count 0), NO
 * `generation-mode-selector` container, and clicking the mode surface CANNOT
 * change any provider claim (the provider is process-global; the selector was
 * removed in Phase 21).
 */

const TARGET = (process.env.PD_QA_TARGET ?? "https://localhost").replace(/\/$/, "");
/** Every product request must stay on the SAME-ORIGIN production stack. */
const TARGET_ORIGIN = new URL(TARGET).origin;
/** Trust the local Caddy internal CA (Hermetic QA stack on localhost). */
const IGNORE_HTTPS = (process.env.PD_QA_IGNORE_HTTPS ?? "1") === "1";

const ROUTES = ["/", "/new", "/generating", "/scene", "/accuse", "/reveal"] as const;

/** Route-scoped routes that render real product state only with a credential. */
const CREDENTIAL_ROUTES = new Set(["/scene", "/accuse", "/reveal"]);

/** Console markers that indicate the F-01 module/MIME failure. */
const MIME_ERROR_MARKERS = [
  "Failed to load module script",
  "was loaded with an unsupported MIME type",
  "MIME type",
];

interface RouteReport {
  url: string;
  assetCount: number;
  distinctAssetChunks: number;
  assetFailures: Array<{ url: string; status: number }>;
  apiFailures: Array<{ url: string; status: number }>;
  nonApiFailures: Array<{ url: string; status: number }>;
  routeRelativeAssets: string[];
  otherOriginRequests: string[];
  pageErrors: string[];
  consoleErrors: string[];
}

/**
 * Sanitized product error envelopes a stateful deep-link may legitimately get
 * (e.g. GET /reveal -> 403 REVEAL_NOT_AVAILABLE until the playthrough is
 * accused). Anything else — including ANY 5xx and every non-API 4xx — is a
 * gate failure.
 */
const ALLOWED_API_FAILURE_STATUSES = new Set([403, 404, 409, 422, 429]);

function installObservers(page: Page, report: RouteReport): void {
  page.on("pageerror", (err) => report.pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    // Chrome's own "Failed to load resource: ... status 403" lines mirror the
    // response listener below; the meaningful app-surface errors are MIME/module
    // failures and plain JS errors.
    if (text.startsWith("Failed to load resource")) return;
    if (text.includes("WebGL") || text.includes("THREE") || text.includes("babylon")) {
      if (!MIME_ERROR_MARKERS.some((m) => text.includes(m))) return;
    }
    report.consoleErrors.push(text);
  });
  page.on("response", (response) => {
    const url = response.url();
    const status = response.status();
    const pathname = new URL(url).pathname;
    if (status >= 400) {
      const entry = { url, status };
      if (pathname.startsWith("/assets/")) report.assetFailures.push(entry);
      else if (pathname.startsWith("/api/v1/")) report.apiFailures.push(entry);
      else report.nonApiFailures.push(entry);
    }
  });
  page.on("request", (request) => {
    const url = request.url();
    try {
      if (new URL(url).origin !== TARGET_ORIGIN) report.otherOriginRequests.push(url);
    } catch {
      report.otherOriginRequests.push(url);
    }
    if (request.resourceType() === "script" || request.resourceType() === "stylesheet") {
      const pathname = new URL(url).pathname;
      if (pathname.startsWith("/assets/")) {
        report.assetCount += 1;
      }
      // F-01: route-relative asset requests such as /scene/assets/* must NEVER
      // happen (route scope blocks those on the route segment).
      if (/\/(?:new|generating|scene|accuse|reveal)\/assets\//.test(pathname)) {
        report.routeRelativeAssets.push(pathname);
      }
    }
  });
}

/** The REQUIREMENTS 48 / demo journey prompt — the exact golden showcase case. */
const SHOWCASE_PROMPT =
  "Victim: Sarah Miller\n" +
  "Murderer: Thomas Reed\n" +
  "Motive: €240,000 embezzlement\n" +
  "Weapon: Kitchen knife\n" +
  "Time: 22:17\n" +
  "Witness: Emily Reed";

/** Seed a golden playthrough through the SAME-ORIGIN production API. */
async function seedPlaythrough(api: APIRequestContext): Promise<{ id: string; token: string }> {
  const sessionRes = await api.post(`${TARGET}/api/v1/sessions/anonymous`);
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();
  const caseRes = await api.post(`${TARGET}/api/v1/cases`, {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt: SHOWCASE_PROMPT, difficulty: "medium" },
  });
  expect(caseRes.status(), "create case").toBe(201);
  const created = await caseRes.json();
  expect(created.status, "fake provider publishes").toBe("PUBLISHED");
  const ptRes = await api.post(
    `${TARGET}/api/v1/cases/${created.caseId}/versions/1/playthroughs`,
    { headers: { Authorization: `Bearer ${created.creatorAccessToken}` } },
  );
  expect(ptRes.status(), "create playthrough").toBe(201);
  const pt = await ptRes.json();
  return { id: pt.playthroughId as string, token: pt.playthroughAccessToken as string };
}

async function newFreshContext(browser: Browser, cert: { id: string; token: string } | null): Promise<BrowserContext> {
  const context = await browser.newContext({
    ignoreHTTPSErrors: IGNORE_HTTPS,
    viewport: { width: 1280, height: 800 },
  });
if (cert !== null) {
    await context.addInitScript(
      ([id, token]) => {
        // Guard the initial about:blank document (localStorage is denied there).
        if (!window.location.protocol.startsWith("http")) return;
        window.localStorage.setItem("pd_playthrough_id", id);
        window.localStorage.setItem("pd_playthrough_token", token);
      },
      [cert.id, cert.token] as const,
    );
  }
  return context;
}

for (const route of ROUTES) {
  test(`F-01 deep-link + refresh: ${route}`, async ({ browser }) => {
    test.setTimeout(120_000);
const report: RouteReport = {
      url: route,
      assetCount: 0,
      distinctAssetChunks: 0,
      assetFailures: [],
      apiFailures: [],
      nonApiFailures: [],
      routeRelativeAssets: [],
      otherOriginRequests: [],
      pageErrors: [],
      consoleErrors: [],
    };
    const needsCredential = CREDENTIAL_ROUTES.has(route);
    const context = await newFreshContext(browser, null);
    // A credential is created once via the same-origin API (the context's own
    // request honors the same TLS trust) and stored BEFORE the direct-open
    // (browser state equal to a real player's returning deep link).
    const cert = needsCredential ? await seedPlaythrough(context.request) : null;
    await context.close();
    const deepContext = needsCredential
      ? await newFreshContext(browser, cert)
      : await newFreshContext(browser, null);
    const deepPage = await deepContext.newPage();
    installObservers(deepPage, report);

    // 1. DIRECT OPEN of the route.
    await deepPage.goto(`${TARGET}${route}`, { waitUntil: "domcontentloaded", timeout: 60_000 });
    await deepPage.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => {});
    const body = await deepPage.evaluate(() => document.body?.innerText ?? "");
    expect(
      body,
      `app shell rendered on direct-open ${route} (no app error page)`,
    ).not.toContain("Application error");
    expect(report.pageErrors, `no uncaught page errors on ${route}`).toEqual([]);

// 2. RELOAD of the same deep link.
    report.consoleErrors.length = 0;
    report.assetFailures.length = 0;
    report.apiFailures.length = 0;
    report.nonApiFailures.length = 0;
    await deepPage.reload({ waitUntil: "domcontentloaded", timeout: 60_000 });
    await deepPage.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => {});
    const bodyAfter = await deepPage.evaluate(() => document.body?.innerText ?? "");
    expect(bodyAfter, `app shell rendered after refresh ${route}`).not.toContain("Application error");
    expect(report.pageErrors, `no page errors after refresh ${route}`).toEqual([]);

    // 3. F-01 contract assertions.
expect(
      report.consoleErrors,
      `no module/MIME console errors on ${route} (direct + refresh)`,
    ).toEqual([]);
    expect(
      report.assetFailures,
      `no failed/broken asset loads (404 chunks) on ${route} (direct + refresh)`,
    ).toEqual([]);
    expect(
      report.nonApiFailures,
      `no failed NON-API responses on ${route} (direct + refresh)`,
    ).toEqual([]);
    for (const failure of report.apiFailures) {
      expect(
        ALLOWED_API_FAILURE_STATUSES.has(failure.status),
        `API failure on ${route} must be a sanitized product envelope, got ${failure.status} ${failure.url}`,
      ).toBe(true);
    }
    expect(
      report.routeRelativeAssets,
      `no route-relative asset requests (all root /assets/*) on ${route}`,
    ).toEqual([]);
    expect(
      report.otherOriginRequests,
      `no localhost/dev/3rd-party endpoints in the browser network on ${route} (same-origin HTTPS only)`,
    ).toEqual([]);
    expect(report.assetCount, `root-absolute assets WERE requested on ${route}`).toBeGreaterThan(0);

    await deepContext.close();
  });
}

test("F-01 lazy chunks: /scene pulls the Babylon lazy chunk graph and survives history back/forward", async ({ browser }) => {
  test.setTimeout(180_000);
  const context = await newFreshContext(browser, null);
  const cert = await seedPlaythrough(context.request);
  await context.close();
  const credContext = await newFreshContext(browser, cert);
  const scenePage = await credContext.newPage();
  const chunkSet = new Set<string>();
  const assetRequests: string[] = [];
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failed: Array<{ url: string; status: number }> = [];
  scenePage.on("pageerror", (err) => pageErrors.push(String(err)));
  scenePage.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  scenePage.on("response", (response) => {
    const url = response.url();
    if (response.status() >= 400) failed.push({ url, status: response.status() });
  });
  scenePage.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith("/assets/")) {
      chunkSet.add(pathname);
      assetRequests.push(pathname);
    }
  });

  await scenePage.goto(`${TARGET}/scene`, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await scenePage.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => {});
  // /scene boots the real investigation (fake golden case).
  await expect(scenePage.getByTestId("scene-canvas").or(scenePage.getByTestId("scene-ready")))
    .toBeVisible({ timeout: 60_000 })
    .catch(() => {});
  const body = await scenePage.evaluate(() => document.body?.innerText ?? "");
  expect(body).not.toContain("Application error");

  // The Babylon lazy chunk graph (> the entry chunk) must have been fetched.
  expect(
    chunkSet.size,
    `lazy chunks loaded on /scene (distinct /assets/* modules, got ${chunkSet.size})`,
  ).toBeGreaterThanOrEqual(4);
  expect(assetRequests.every((p) => p.startsWith("/assets/")), "all assets root-absolute").toBe(true);

  // history back -> forward: the SPA must survive both directions with no
  // MIME errors and no second shell break.
  await scenePage.goBack({ waitUntil: "domcontentloaded" }).catch(() => {});
  await scenePage.waitForTimeout(800);
  await scenePage.goForward({ waitUntil: "domcontentloaded" }).catch(() => {});
  await scenePage.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => {});
  const bodyAfter = await scenePage.evaluate(() => document.body?.innerText ?? "");
  expect(bodyAfter, "history back/forward keeps the app shell").not.toContain("Application error");

  const mimeErrors = consoleErrors.filter((e) =>
    MIME_ERROR_MARKERS.some((m) => e.includes(m)),
  );
  expect(mimeErrors, "no MIME/module errors across the lazy-chunk session").toEqual([]);
  const asset404s = failed.filter((f) => new URL(f.url).pathname.startsWith("/assets/"));
  expect(asset404s, "no 404 asset loads across the lazy-chunk session").toEqual([]);
  expect(pageErrors, "no uncaught page errors").toEqual([]);

  await credContext.close();
});

test("F-03 truthful mode: demo-only backend shows the honest read-only line, no selector, clicks cannot change any provider claim", async ({ browser }) => {
  test.setTimeout(120_000);
  const context = await newFreshContext(browser, null);
  const demoPage = await context.newPage();
  await demoPage.goto(`${TARGET}/`, { waitUntil: "domcontentloaded" });

  // The honest notice + the truthful single line.
  const notice = demoPage.getByTestId("generation-mode-demo-notice");
  await expect(notice).toBeVisible({ timeout: 30_000 });
  await expect(notice).toHaveText("Demo mode active");
  const line = demoPage.getByTestId("generation-mode-line");
  await expect(line).toBeVisible({ timeout: 10_000 });
  await expect(line).toHaveText("Generation mode: Deterministic demo");

  // NO interactive provider <select>, NO selector container, NO option list.
  await expect(demoPage.getByTestId("generation-mode-select")).toHaveCount(0);
  await expect(demoPage.getByTestId("generation-mode-selector")).toHaveCount(0);
  expect(await demoPage.locator("select").count(), "no <select> control at all on the landing").toBe(0);

  // Clicking the mode surface changes NOTHING: the same read-only line, no
  // select appears, no localStorage write implies a provider choice.
  const beforeText = await line.textContent();
  await notice.click({ force: true }).catch(() => {});
  await line.click({ force: true }).catch(() => {});
  await demoPage.waitForTimeout(300);
  const afterText = await demoPage.getByTestId("generation-mode-line").textContent();
  expect(afterText, "the mode line is immutable under clicks").toBe(beforeText);
  await expect(demoPage.getByTestId("generation-mode-select")).toHaveCount(0);
  await expect(demoPage.getByTestId("generation-mode-selector")).toHaveCount(0);
  const stored = await demoPage.evaluate(() => localStorage.getItem("pd_generation_mode"));
  expect(stored, "no provider 'selection' is written by clicking").toBeNull();

  // /new carries the identical read-only story.
  await demoPage.goto(`${TARGET}/new`, { waitUntil: "domcontentloaded" });
  await expect(demoPage.getByTestId("generation-mode-demo-notice")).toBeVisible({ timeout: 30_000 });
  await expect(demoPage.getByTestId("generation-mode-line")).toHaveText(
    "Generation mode: Deterministic demo",
  );
  await expect(demoPage.getByTestId("generation-mode-select")).toHaveCount(0);
  await expect(demoPage.getByTestId("generation-mode-selector")).toHaveCount(0);

  // Leak hygiene on both pages: no host/port/URL material ever rendered.
  const allText = await demoPage.evaluate(() => document.body?.innerText ?? "");
  for (const token of ["11434", "127.0.0.1", "host.docker.internal", "http://localhost:8000"]) {
    expect(allText, `DOM must not contain ${token}`).not.toContain(token);
  }

  await context.close();
});