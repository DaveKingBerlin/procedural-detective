import { expect, test, type Page } from "@playwright/test";

/**
 * Phase 24 §16 — BROWSER SMOKE (repository-owned, frontend track).
 *
 * Deterministic journey against the DOCKER-SERVED origin
 * (GENERATION_PROVIDER=fake / ENABLE_BRIDGE=false — the Phase 24 §1 CI
 * profile). The prompt is the frozen "Easy" example (frontend/src/journey/
 * examplePrompts.ts, pinned verbatim in the /new button), so the generated
 * case is the deterministic golden case — no real AI, no bridge, no private
 * host. Two tests run serially (workers: 1 — generation is stack-bound):
 *
 *   1. the full §16 flow: /new → deterministic generation → scene → inspect
 *      evidence → witness (TIME) → accuse → reveal, with ALL five checks
 *      asserted live on real page traffic;
 *   2. a fast shell check: / and /new plus their static assets + public
 *      capability API stay same-origin and never dial a private endpoint.
 *
 * CHECK IMPLEMENTATION (mirrors Phase 24 §16):
 *   - no console errors: `page.on("console")` errors + `page.on("pageerror")`;
 *   - no failed API requests: every `/api/v1` response with status >= 400
 *     fails the journey (4xx are additionally attached as facts so an
 *     operator can see them without a pass/fail lie);
 *   - no horizontal overflow: documentElement.scrollWidth and the main app
 *     region bounding box are asserted on both the scene and the reveal;
 *   - no browser request to Ollama/private endpoints: EVERY request URL is
 *     checked for a non-same-origin target and for private/loopback/LAN/
 *     docker-host literals and the Ollama service port;
 *   - same-origin API use: every `/api/v1` request resolves against the
 *     serving origin only (the app's own SAME_ORIGIN_API_ROOT = "/api/v1").
 *
 * Prereq: a RUNNING stack on $E2E_BASE_URL (default http://localhost:8000),
 * e.g. `docker compose up --build`. This suite cannot run without it; see
 * docs/CI.md. It NEVER fabricates a browser run — when there is no stack it
 * fails with the real connection error.
 */

/** The serving origin — derived from the same env contract as the config. */
const E2E_BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:8000";

/** Private / loopback / LAN IPv4 literals plus the docker-host alias. */
const PRIVATE_HOST_LITERALS: readonly RegExp[] = [
  /\b127\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/,
  /\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/,
  /\b192\.168\.\d{1,3}\.\d{1,3}\b/,
  /\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b/,
  /\bhost\.docker\.internal\b/i,
];

interface JourneyEvidence {
  consoleErrors: string[];
  pageErrors: string[];
  non2xx: Array<{ status: number; url: string }>;
  requestUrls: string[];
}

/** Attach request/response/console listeners and return the evidence sink. */
function collectJourneyEvidence(page: Page): JourneyEvidence {
  const evidence: JourneyEvidence = { consoleErrors: [], pageErrors: [], non2xx: [], requestUrls: [] };
  page.on("console", (message) => {
    if (message.type === "error") evidence.consoleErrors.push(message.text);
  });
  page.on("pageerror", (error) => {
    evidence.pageErrors.push(String(error));
  });
  page.on("response", (response) => {
    if (response.status >= 400) {
      evidence.non2xx.push({ status: response.status, url: response.url });
    }
  });
  page.on("request", (request) => {
    evidence.requestUrls.push(request.url);
  });
  return evidence;
}

/** §16 — no browser request to a private/Ollama endpoint + same-origin API. */
function assertSameOriginAndNoPrivateRequests(evidence: JourneyEvidence, origin: string): void {
  const forbidden: string[] = [];
  const crossOrigin: string[] = [];
  const apiOutsideRoot: string[] = [];
  const apiRoot = `${origin}/api/v1`;
  for (const url of evidence.requestUrls) {
    if (url.includes(":11434") || PRIVATE_HOST_LITERALS.some((re) => re.test(url))) {
      forbidden.push(url);
    }
    if (!url.startsWith(origin)) {
      crossOrigin.push(url);
      continue;
    }
    if (url.includes("/api/v1") && url !== apiRoot && !url.startsWith(`${apiRoot}/`)) {
      apiOutsideRoot.push(url);
    }
  }
  expect(forbidden, `private/Ollama endpoint requested by the browser:\n- ${forbidden.join("\n- ")}`).toEqual([]);
  expect(crossOrigin, `cross-origin requests (browser left the serving origin):\n- ${crossOrigin.join("\n- ")}`).toEqual([]);
  expect(apiOutsideRoot, `API requests NOT resolved against the serving origin:\n- ${apiOutsideRoot.join("\n- ")}`).toEqual([]);
}

/** §16 — no horizontal overflow on the current viewport (page + app region). */
async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const metrics = await page.evaluate(() => {
    const app = document.querySelector("main, .app-main") as HTMLElement | null;
    const region = app !== null ? Math.round(app.getBoundingClientRect().right) : -1;
    return {
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      region,
    };
  });
  expect(
    metrics.scrollWidth,
    `horizontal page overflow: scrollWidth ${metrics.scrollWidth} > innerWidth ${metrics.innerWidth}`,
  ).toBeLessThanOrEqual(metrics.innerWidth + 1);
  if (metrics.region >= 0) {
    expect(metrics.region, "main app region overflows the viewport").toBeLessThanOrEqual(metrics.innerWidth + 1);
  }
}

/** Close the evidence panel if it is open (it appears after a discovery). */
async function closeEvidencePanelIfOpen(page: Page): Promise<void> {
  const close = page.getByTestId("evidence-close");
  if (await close.isVisible().catch(() => false)) {
    await close.click();
  }
}

test("Phase 24 §16 — deterministic journey /new → generation → scene → evidence → witness → accuse → reveal", async ({
  page,
}) => {
  const evidence = collectJourneyEvidence(page);

  await page.goto("/new");
  await expect(page.getByTestId("prompt-input")).toBeVisible();
  const origin = new URL(page.url()).origin;
  expect(origin).toBe(new URL(E2E_BASE_URL).origin);

  // Deterministic input: the frozen "Easy" example prompt.
  await page.getByTestId("example-easy").click();
  await expect(page.getByTestId("prompt-input")).not.toHaveValue("");

  // Generate: /generating runs the whole session→case→playthrough journey.
  await page.getByTestId("generate-case").click();
  await expect(page).toHaveURL(/\/generating/);
  const enter = page.getByTestId("enter-investigation");
  await expect(enter).toBeVisible({ timeout: 300_000 });
  await enter.click();

  // Scene rendered (player-safe bootstrap + object list).
  await expect(page.getByTestId("objective-text")).toBeVisible();
  await expect(page.getByTestId("scene-objects")).toBeVisible();

  // Inspect evidence: activate the first world object (universal inspection —
  // decorative objects answer 200 too; witnesses open the interview panel).
  await page.getByTestId("scene-objects").locator("li button").first().click();
  const toast = page.getByTestId("discovery-toast");
  const panel = page.getByTestId("evidence-panel");
  const witnessDialog = page.getByTestId("witness-panel");
  await Promise.race([
    toast.waitFor({ state: "visible", timeout: 30_000 }),
    panel.waitFor({ state: "visible", timeout: 30_000 }),
    witnessDialog.waitFor({ state: "visible", timeout: 30_000 }),
  ]);
  await closeEvidencePanelIfOpen(page);

  // Witness: ask the closed TIME question and get the deterministic statement.
  const witnesses = page.getByTestId("witnesses");
  if (await witnesses.isVisible().catch(() => false)) {
    await witnesses.locator("li button").first().click();
    await expect(page.getByTestId("witness-panel")).toBeVisible();
    await page.getByTestId("witness-question-TIME").click();
    await expect(page.getByTestId("witness-answer")).toBeVisible();
    const summary = await page.getByTestId("witness-statement-summary").textContent();
    expect(summary ?? "").not.toBe("");
    await page.getByTestId("witness-close").click();
  }

  // No horizontal overflow on the rendered scene.
  await expectNoHorizontalOverflow(page);

  // Accuse: /accuse loads the player-safe candidates; pick the FIRST option in
  // each dimension (the fake case accepts any valid accusation) and confirm.
  await page.getByTestId("accusation-open").click();
  await expect(page.getByTestId("accusation-panel")).toBeVisible();
  await page.getByTestId("accusation-suspects").locator("input[type=radio]").first().check();
  await page.getByTestId("accusation-motives").locator("input[type=radio]").first().check();
  await page.getByTestId("accusation-weapons").locator("input[type=radio]").first().check();
  await page.getByTestId("accusation-time").fill("20:15");
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirm")).toBeVisible();
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible();

  // Reveal: the truth is served from the app origin only.
  await page.getByTestId("accusation-panel").getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible();
  await expect(page.getByTestId("reveal-truth")).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // ---- The five §16 checks -------------------------------------------------
  if (evidence.non2xx.length > 0) {
    await test.info().attach("http-non-2xx-facts", {
      body: JSON.stringify(evidence.non2xx, null, 2),
      contentType: "application/json",
    });
  }
  expect(evidence.pageErrors, `uncaught page exceptions:\n- ${evidence.pageErrors.join("\n- ")}`).toEqual([]);
  expect(evidence.consoleErrors, `browser console errors:\n- ${evidence.consoleErrors.join("\n- ")}`).toEqual([]);
  const failedApi = evidence.non2xx.filter((entry) => entry.url.includes("/api/v1"));
  expect(failedApi, `failed API requests (status >= 400):\n${JSON.stringify(failedApi, null, 2)}`).toEqual([]);
  expect(evidence.requestUrls.length, "the flow must have made requests").toBeGreaterThan(0);
  assertSameOriginAndNoPrivateRequests(evidence, origin);
});

test("Phase 24 §16 — shell (/ and /new): same-origin API + assets, no private endpoint requested", async ({
  page,
}) => {
  const evidence = collectJourneyEvidence(page);

  await page.goto("/");
  await expect(page.getByTestId("backend-status")).toBeVisible();
  const origin = new URL(page.url()).origin;

  await page.getByTestId("new-investigation").click();
  await expect(page.getByTestId("generate-intro")).toBeVisible();

  // Static assets and the public generation-capabilities probe are live.
  await expect(page.getByTestId("generate-provider-note")).toBeVisible();

  await expectNoHorizontalOverflow(page);

  expect(evidence.pageErrors, `uncaught page exceptions:\n- ${evidence.pageErrors.join("\n- ")}`).toEqual([]);
  expect(evidence.consoleErrors, `browser console errors:\n- ${evidence.consoleErrors.join("\n- ")}`).toEqual([]);
  const failedApi = evidence.non2xx.filter((entry) => entry.url.includes("/api/v1"));
  expect(failedApi, `failed API requests (status >= 400):\n${JSON.stringify(failedApi, null, 2)}`).toEqual([]);
  expect(evidence.requestUrls.length, "the shell must have made requests").toBeGreaterThan(0);
  assertSameOriginAndNoPrivateRequests(evidence, origin);
});