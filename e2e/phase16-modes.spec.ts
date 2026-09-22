import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installLeakListener, scanJsonBody } from "./helpers";

/**
 * PHASE 16 TRACK B — GENERATION-MODE SELECTOR (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Runs against the PRODUCTION SPA served by vite preview on :4173 and the QA
 * backend on :8000 (freshly migrated scratch DB via tools/process_guard +
 * e2e/qa-phase16-backend.py; CORS_ALLOWED_ORIGINS=http://localhost:4173,
 * http://localhost:5173). Two controlled backend states, exactly as the Phase
 * 16 task mandates:
 *
 *  TEST A ("demo-only notice") — backend with provider DEFAULTS (fake).
 *    The landing must show the honest static notice "Demo mode active"
 *    (`generation-mode-demo-notice`) plus the truthful read-only line
 *    "Generation mode: Deterministic demo" (`generation-mode-line`) and MUST
 *    NOT offer Local AI / Cloud AI (`generation-mode-selector` absent;
 *    `Local AI` / `Cloud AI` absent from the landing DOM). No URL/host/IP
 *    ever rendered.
 *
 *  TEST B ("ollama-available backend") — backend relaunched with
 *    GENERATION_PROVIDER=ollama + OLLAMA_BASE_URL pointed at the QA-owned
 *    fake Ollama server (e2e/qa-phase16-fake-ollama.py on 127.0.0.1:11499,
 *    answered /api/tags + /api/chat with scripted golden JSON), so the REAL
 *    probe + REAL endpoint work end-to-end over real HTTP. The landing must
 *    then show the READ-ONLY backend-authoritative line
 *    "Generation mode: Local AI — llama3.2:3b — Ready"
 *    (`generation-mode-selector` + `generation-mode-line`; Phase 21 F-03 —
 *    the interactive <select> was REMOVED, so `generation-mode-select` must
 *    have count 0 and NOTHING implies a switch). The legacy storage key
 *    `pd_generation_mode=local` is injected ONLY via the QA seam (no user
 *    action writes it anymore) for the honesty-note paths, a reload keeps the
 *    backend-authoritative line, AND the Demo flow still runs (the one-click
 *    demo case publishes through the REAL ollama provider over the fake
 *    server -> /scene boots).
 *
 *  EVERY test: leak listener scans every /api response body (forbidden
 *  pre-reveal key paths MUST be 0); the capability response + scene responses
 *  are scanned for host/IP/port/URL tokens (MUST be 0); the DOM text NEVER
 *  contains "11434", "127.0.0.1", "host.docker.internal" or the fake server
 *  port; 0 console/page errors; 0 failed resources; 0 external traffic.
 *
 *  The QA harness runs this file twice (per state): TEST A on the fake
 *  default backend, TEST B on the ollama relaunch (see DEFECTS.md note).
 */

const FAKE_OLLAMA_PORT = "11499"; // the QA fake server's loopback port
const FORBIDDEN_DOM_TOKENS = [
  "11434", // real Ollama default port
  FAKE_OLLAMA_PORT, // the QA fake server port — must never reach the DOM
  "127.0.0.1",
  "host.docker.internal",
  "http://localhost:8000", // the backend base must not be rendered as text
];

/**
 * The generation-capabilities DTO is a DOCUMENTED public endpoint whose
 * contract EXPLICITLY carries the operator-configured display `model` name
 * (Phase 16 J; never a URL/credential). The standing pre-reveal truth-leak
 * scanner treats `model` as a forbidden key (Phase 6 pre-reveal rule), so the
 * capabilities endpoint is exempted from the GENERIC scan and instead exhausts
 * the Phase 16 allowlist scan below (exact modes surface + zero
 * URL/port/key/host material).
 */
function withoutCapabilities(records: Array<{ url: string; paths: string[]; body: unknown }>) {
  return records.filter((m) => !m.url.includes("generation-capabilities"));
}

/** Exhaustive Phase 16 allowlist scan of the real capability response. */
async function assertCapabilitiesAllowlist(page: Page): Promise<number> {
  const res = await page.evaluate(async () => {
    const r = await fetch("http://localhost:8000/api/v1/generation-capabilities");
    return { status: r.status, body: await r.json() };
  });
  expect(res.status).toBe(200);
  const body = res.body as {
    modes: Array<{ id: string; available: boolean; label?: string; model?: string }>;
  };
  expect(Object.keys(body).sort()).toEqual(["modes"]);
  expect(Array.isArray(body.modes)).toBe(true);
  for (const mode of body.modes) {
    const keys = Object.keys(mode).sort();
    expect(
      keys.every((k) => ["id", "available", "label", "model"].includes(k)),
      `capability mode keys allowlist: ${keys}`,
    ).toBe(true);
    expect(typeof mode.id).toBe("string");
    expect(typeof mode.available).toBe("boolean");
    if (mode.label !== undefined) expect(typeof mode.label).toBe("string");
    if (mode.model !== undefined) expect(typeof mode.model).toBe("string");
  }
  const blob = JSON.stringify(body);
  for (const token of ["11434", "127.0.0.1", "host.docker.internal", "http://", "https://", "apiKey", FAKE_OLLAMA_PORT]) {
    expect(blob, `capability DTO must not contain ${token}`).not.toContain(token);
  }
  return res.status;
}

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

test("P16A: demo-default backend — honest 'Demo mode active' notice, no Local/Cloud options", async ({ page }) => {
  const leak = installLeakListener(page);
  const session = installSessionObservers(page);

  await page.goto("/", { waitUntil: "domcontentloaded" });

  // the honest Demo notice replaces any selector on a demo-only backend
  const notice = page.getByTestId("generation-mode-demo-notice");
  await expect(notice).toBeVisible({ timeout: 30_000 });
  await expect(notice).toHaveText("Demo mode active");
  // Phase 21 F-03 — the truthful read-only line accompanies the notice.
  const line = page.getByTestId("generation-mode-line");
  await expect(line).toBeVisible({ timeout: 10_000 });
  await expect(line).toHaveText("Generation mode: Deterministic demo");
  await page.screenshot({ path: "artifacts/screenshots/phase16-demo-notice.png", fullPage: false });
  await expect(page.getByTestId("generation-mode-selector")).toHaveCount(0);
  await expect(page.getByTestId("generation-mode-select")).toHaveCount(0);

  const bodyText = await page.evaluate(() => document.body?.innerText ?? "");
  expect(bodyText, "no Local AI option offered when unavailable").not.toContain("Local AI");
  expect(bodyText, "no Cloud AI option offered when unavailable").not.toContain("Cloud AI");

  // the capability endpoint was called and carried nothing forbidden
  const capabilityStatus = await assertCapabilitiesAllowlist(page);
  expect(capabilityStatus).toBe(200);

  // no host/port/URL ever rendered
  await expectNoHostInDom(page);

  expect(session.pageErrors, "no uncaught page errors").toEqual([]);
  expect(session.consoleErrors, "no console errors").toEqual([]);
  expect(session.failed, "no failed resources").toEqual([]);
  expect(session.external, "no external traffic").toEqual([]);
  expect(
    withoutCapabilities(leak.matched),
    "no forbidden pre-reveal key paths outside the documented capability DTO",
  ).toEqual([]);
  expect(leak.scanned, "leak listener scanned API responses").toBeGreaterThan(0);
});

test("P16C: demo-default backend — a stale stored 'local' selection NEVER claims Local AI; the honest notice communicates the switch", async ({ page }) => {
  // Phase 16-N: "fallback silently switching Local AI to Demo without telling
  // the user". A player who previously selected `local` on an ollama backend
  // returns to a DEMO-ONLY backend: the selector/notice must communicate that
  // the mode switched (no silent Local AI claim, no fake "Ready" option).
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.setItem("pd_generation_mode", "local"));
  await page.reload({ waitUntil: "domcontentloaded" });

  // The honest notice replaces any selector: the user SEES the switch.
  const notice = page.getByTestId("generation-mode-demo-notice");
  await expect(notice).toBeVisible({ timeout: 30_000 });
  await expect(notice).toHaveText("Demo mode active");
  // Phase 21 F-03 — the read-only line stays truthful even with a stale stored
  // `local` value: the demo-only backend reports the deterministic story.
  const line = page.getByTestId("generation-mode-line");
  await expect(line).toBeVisible({ timeout: 10_000 });
  await expect(line).toHaveText("Generation mode: Deterministic demo");
  await expect(page.getByTestId("generation-mode-selector")).toHaveCount(0);
  await expect(page.getByTestId("generation-mode-select")).toHaveCount(0);

  // No claim of Local AI anywhere; no host/port/URL; no errors.
  const bodyText = await page.evaluate(() => document.body?.innerText ?? "");
  expect(bodyText, "Local AI must not be claimed on a demo-only backend").not.toContain("Local AI");
  await expectNoHostInDom(page);

  expect(pageErrors, "no uncaught page errors").toEqual([]);
});

test("P16B: ollama-available backend — read-only 'Generation mode: Local AI — llama3.2:3b — Ready' line, no selector, Demo flow still runs", async ({ page, request }) => {
  const leak = installLeakListener(page);
  const session = installSessionObservers(page);

  // --- 1. the live capability probe (REAL /api/tags against the fake server)
  await page.goto("/", { waitUntil: "domcontentloaded" });

  // Phase 21 F-03 — the interactive <select> was REMOVED: the backend runs ONE
  // process-global provider, so the UI shows the single READ-ONLY line and
  // NOTHING implies that a click can switch the provider.
  const selector = page.getByTestId("generation-mode-selector");
  await expect(selector).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("generation-mode-select")).toHaveCount(0);
  const line = page.getByTestId("generation-mode-line");
  await expect(line).toBeVisible({ timeout: 30_000 });
  await expect(line).toHaveText("Generation mode: Local AI — llama3.2:3b — Ready");
  await page.screenshot({ path: "artifacts/screenshots/phase16-ollama-mode-line.png", fullPage: false });

  // the honest demo notice must NOT be shown when local is available
  await expect(page.getByTestId("generation-mode-demo-notice")).toHaveCount(0);

  // --- 2. legacy storage seam only (Phase 21 F-03: no user action writes
  //        pd_generation_mode anymore) — the read-only line is unchanged.
  await page.evaluate(() => localStorage.setItem("pd_generation_mode", "local"));
  await expect(page.getByTestId("generation-mode-select")).toHaveCount(0);

  // --- 3. a reload keeps the backend-authoritative read-only line (there is
  //        no persisted client selection to survive).
  await page.reload({ waitUntil: "domcontentloaded" });
  const lineAfter = page.getByTestId("generation-mode-line");
  await expect(lineAfter).toBeVisible({ timeout: 30_000 });
  await expect(lineAfter).toHaveText("Generation mode: Local AI — llama3.2:3b — Ready");

  // --- 4. the Demo flow still runs: one-click demo publishes through the REAL
  //        ollama provider (fake server -> real endpoint -> PUBLISHED) and the
  //        app lands on a booted /scene.
  await page.getByTestId("try-demo").click();
  await expect(page).toHaveURL(/\/generating/, { timeout: 15_000 });
  // the deterministic journey completes and enters the investigation
  await expect(page.getByTestId("enter-investigation")).toBeVisible({ timeout: 90_000 });
  await page.getByTestId("enter-investigation").click();
  await expect(page).toHaveURL(/\/scene/, { timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("scene-error")).toHaveCount(0);

  // the capability endpoint was called and allowed-list-exhausts clean
  const capabilityStatus = await assertCapabilitiesAllowlist(page);
  expect(capabilityStatus).toBe(200);

  // --- 5. no host/port/URL ever rendered; leak scans 0 everywhere
  await expectNoHostInDom(page);

  const leakWithoutCapabilities = withoutCapabilities(leak.matched);
  const records = leakWithoutCapabilities.filter(
    (m) => m.url.includes("generation-capabilities") || m.url.includes("/scene") || m.url.includes("/investigation"),
  );
  expect(records, "capabilities + scene responses free of forbidden key paths").toEqual([]);
  for (const m of leakWithoutCapabilities) {
    const bodyBlob = JSON.stringify(m.body);
    for (const token of ["11434", FAKE_OLLAMA_PORT, "127.0.0.1", "host.docker.internal"]) {
      expect(bodyBlob, `no ${token} in any scanned API response`).not.toContain(token);
    }
  }

  expect(session.pageErrors, "no uncaught page errors").toEqual([]);
  expect(session.consoleErrors, "no console errors").toEqual([]);
  expect(session.failed, "no failed resources").toEqual([]);
  expect(session.external, "no external traffic").toEqual([]);
  expect(
    withoutCapabilities(leak.matched),
    "no forbidden pre-reveal key paths outside the documented capability DTO",
  ).toEqual([]);
  expect(leak.scanned, "leak listener scanned API responses").toBeGreaterThan(0);
});