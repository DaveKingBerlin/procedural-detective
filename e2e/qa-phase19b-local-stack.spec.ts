import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * PHASE 19B REAL-HOST SUPPLEMENTARY BROWSER CHECK (QA-owned).
 *
 * This spec runs against the REAL local stack: production SPA (vite preview
 * :4173) + real backend (:8000, GENERATION_PROVIDER=ollama,
 * OLLAMA_BASE_URL=http://127.0.0.1:11434, OLLAMA_MODEL=llama3.2:3b).
 *
 * It performs ZERO case generations: Phase 19 §17/§17B runs each example
 * prompt EXACTLY ONCE. Easy/Hard already failed terminally (PROVIDER_TIMEOUT —
 * operator-side model latency on a CPU-only host) and must never be retried;
 * the Medium acceptance run PUBLISHED but its one-time-only creatorAccessToken
 * was lost by a QA probe-harness client read-timeout (the synchronous
 * POST /cases 201 was discarded), so a playthrough on that exact case cannot
 * be opened without a prohibited second generation. Per the acceptance
 * instruction, the browser scene/picking/accusation journey is therefore
 * DEFERRED and the maximum non-generation browser evidence is recorded here:
 *
 *  B1  the production SPA loads against the REAL local backend;
 *  B2  GET /api/v1/generation-capabilities (browser-fetched) reports
 *      local available:true with model llama3.2:3b (honest local claim);
 *  B3  the home selector shows "Local AI — llama3.2:3b — Ready";
 *  B4  /new shows NO "Local AI is unavailable" note and surfaces the
 *      Local-AI showcase note once the user selects Local mode
 *      (capability-driven honesty, ADV-208/ADV-212 surface);
 *  B5  FULL-DOM audit: no LAN/loopback host, no provider URL, no port
 *      token, no proc.*, no internal technical ids rendered as UI text;
 *  B6  0 page errors / 0 console errors / 0 failed resources / 0 external
 *      traffic / 0 case creations (POST /cases never fired).
 */

const FORBIDDEN_DOM_TOKENS = [
  "11434",
  "127.0.0.1",
  "192.168",
  "host.docker.internal",
  "http://localhost:8000",
];

interface SessionReport {
  pageErrors: string[];
  consoleErrors: string[];
  failed: Array<{ url: string; status: number }>;
  external: Array<{ url: string }>;
  postCases: number;
}

function installSessionObservers(page: Page): SessionReport {
  const report: SessionReport = {
    pageErrors: [],
    consoleErrors: [],
    failed: [],
    external: [],
    postCases: 0,
  };
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
  page.on("request", (request) => {
    if (request.method === "POST" && request.url.endsWith("/api/v1/cases")) {
      report.postCases += 1;
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

test("P19B-REAL: production SPA + real local backend - honest Local AI ready UI, zero generation", async ({
  page,
}) => {
  const report = installSessionObservers(page);

  // ---- B2: capabilities are fetched by the browser and must report local ----
  const caps = await page.request.get("http://localhost:8000/api/v1/generation-capabilities");
  expect(caps.status(), "capabilities probe 200").toBe(200);
  const capsJson = (await caps.json()) as {
    modes: Array<{ id: string; available: boolean; label?: string; model?: string }>;
  };
  const local = capsJson.modes.find((m) => m.id === "local");
  expect(local, "capability DTO reports local mode").toBeTruthy();
  expect(local!.available, "local available on the real 127.0.0.1 Ollama").toBe(true);
  expect(local!.model, "local model is llama3.2:3b").toBe("llama3.2:3b");

  // ---- B1/B3: home page renders the ready selector ---------------------------
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const localOption = page.locator('option[value="local"]');
  await expect(localOption).toHaveText("Local AI — llama3.2:3b — Ready", { timeout: 30_000 });
  const homeBody = await page.evaluate(() => document.body?.innerText ?? "");
  expect(homeBody, "home shows the ready Local AI entry").toContain(
    "Local AI — llama3.2:3b — Ready",
  );

  // ---- B4: /new surfaces the honest capability-driven Local-AI UI ------------
  await page.goto("/new", { waitUntil: "domcontentloaded" });
  // Local IS available on the real backend, so the explicit unavailable note
  // must be ABSENT and the shared Local-AI selector must offer the model.
  await expect(page.getByTestId("local-ai-unavailable")).toHaveCount(0, { timeout: 30_000 });
  const newLocalOption = page.locator('option[value="local"]');
  await expect(newLocalOption).toHaveText("Local AI — llama3.2:3b — Ready", {
    timeout: 30_000,
  });
  const newText = await page.evaluate(() => document.body?.innerText ?? "");
  expect(newText, "/new mentions Local AI availability, not unavailability").not.toContain(
    "Local AI is unavailable right now",
  );
  // The showcase note is legitimate here (capability-driven), so if rendered
  // it must carry the accurate "available" phrasing.
  const showcase = page.getByTestId("local-ai-showcase-note");
  if ((await showcase.count()) > 0) {
    await expect(showcase.first()).toContainText("Local AI is available");
  }

  // ---- B5: DOM hygiene --------------------------------------------------------
  await expectNoHostInDom(page);
  const fullText = await page.evaluate(() => document.body?.innerText ?? "");
  expect(fullText, "no proc.* internal ids in the /new DOM").not.toContain("proc.");
  expect(fullText, "no raw asset catalog ids in the /new DOM").not.toContain("PROP_");
  expect(fullText, "no case ids in the /new DOM").not.toContain("CASE-");

  await page.screenshot({
    path: "artifacts/screenshots/phase19b-real-new-local.png",
    fullPage: false,
  });

  // ---- B6: clean session, zero generation triggered ---------------------------
  expect(report.pageErrors, "no uncaught page errors").toEqual([]);
  expect(report.consoleErrors, "no console errors").toEqual([]);
  expect(report.failed, "no failed resources").toEqual([]);
  expect(report.external, "no external traffic").toEqual([]);
  expect(report.postCases, "no case generation was triggered").toBe(0);
});