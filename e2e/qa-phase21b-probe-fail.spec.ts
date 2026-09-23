import { expect, test } from "@playwright/test";

/**
 * QA-OWNED durable browser contract for DEF-096 / DEF-097 (Phase 21B —
 * Finding 3 "every judge-facing generation CTA truthfully matches actual
 * provider behavior"; .rad/roles/qa.md, .rad/policies/evidence.md,
 * .rad/policies/deterministic-testing.md).
 *
 * Stack selection (one hermetic stack per run; the others SKIP):
 *   QA_P21B_STACK=a  ollama-CONFIGURED backend whose /api/tags capability
 *                    probe FAILS (e.g. OLLAMA_BASE_URL at an unbound port +
 *                    bounded CAPABILITY_PROBE_TIMEOUT_SECONDS). DEF-096: the
 *                    landing must NEVER re-show "Try Demo Case" / the
 *                    deterministic no-cost promise; the CTA is the truthful
 *                    rename, the note names the local AI provider with the
 *                    explicit "not the free deterministic demo" warning and
 *                    the read-only mode line is the truthful per-mode line
 *                    with the "Unavailable" tag. The deterministic demo notice
 *                    MUST NOT render.
 *   QA_P21B_STACK=b  capability endpoint UNREACHABLE (backend serving the SPA
 *                    stops / fetch fails -> empty allowlist). DEF-097: the
 *                    provider qualifier, generate-provider-note and
 *                    generation-mode line MUST be the NEUTRAL reachability
 *                    copy — NO "Demo build: deterministic built-in generator —
 *                    no API keys, no cost." / "Deterministic demo" /
 *                    "Demo mode active" anywhere; the page has no internal
 *                    contradiction next to the neutral CTA.
 *   QA_P21B_STACK=c  CONTROL — GENERATION_PROVIDER=fake backend: the page is
 *                    byte-identical to the pre-21B gates ("Try Demo Case" +
 *                    "Deterministic demo — no API keys, no cost." +
 *                    "Demo mode active" + "Generation mode: Deterministic
 *                    demo"), proving zero regression.
 *
 * The SPA is the production build served by vite preview :4173 (playwright
 * baseURL); the backend runs on :8000 (process_guard). All servers are
 * hermetic QA-owned processes.
 */

const STACK = process.env.QA_P21B_STACK ?? "";

const localNote = (model: string) =>
  `Example case runs the local AI provider (Local AI — ${model}). Not the free deterministic demo.`;

test("DEF-096 (a) ollama probe-FAIL landing — truthful rename, no deterministic promise, 'Unavailable' mode line", async ({
  page,
}) => {
  test.skip(STACK !== "a", `run with QA_P21B_STACK=a (current=${STACK})`);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const failed: Array<{ url: string; status: number }> = [];
  const external: Array<{ url: string; status: number }> = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 400) failed.push({ url: response.url, status: response.status() });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });

  // (1) CTA = the truthful rename, NEVER "Try Demo Case".
  const demoButton = page.getByTestId("try-demo");
  await expect(demoButton).toBeVisible({ timeout: 30_000 });
  await expect(demoButton).toHaveText("Try an example case");

  // (2) note names the local AI provider + the explicit not-demo warning.
  //     toHaveText auto-retries until the capability DTO resolves (the hook
  //     starts null -> neutral copy, then swaps to the DTO-driven copy).
  const demoNote = page.getByTestId("try-demo-note");
  await expect(demoNote).toHaveText(
    `Example case runs the local AI provider (Local AI — llama3.2:3b). Not the free deterministic demo.`,
    { timeout: 30_000 },
  );
  const noteText = (await demoNote.textContent()) ?? "";
  expect(noteText, "no deterministic/no-cost promise in the CTA note").not.toContain(
    "Deterministic demo — no API keys, no cost.",
  );

  // (3) read-only mode line = truthful per-mode line with the Unavailable tag.
  const line = page.getByTestId("generation-mode-line");
  await expect(line).toBeVisible({ timeout: 30_000 });
  await expect(line).toHaveText("Generation mode: Local AI — llama3.2:3b — Unavailable", {
    timeout: 30_000,
  });
  const lineText = (await line.textContent()) ?? "";

  // (3b) the truthful local qualifier / per-path note reflect the configured
  //      Ollama deploy (not the demo-build claim).
  await expect(page.getByTestId("provider-qualifier")).toHaveText(
    "Local AI is available: the model proposes structured data; "
      + "deterministic validators verify and construct the investigation — "
      + "no API keys, no cost. Live AI is opt-in and not enabled in this build.",
    { timeout: 30_000 },
  );
  await expect(page.getByTestId("generate-provider-note")).toHaveText(
    "uses the local AI pipeline — the model proposes structured data; "
      + "deterministic validators build the investigation",
    { timeout: 30_000 },
  );

  // (4) NO demo notice, NO deterministic story anywhere on the page.
  await expect(page.getByTestId("generation-mode-demo-notice")).toHaveCount(0);
  const body = (await page.locator("body").innerText()).toLowerCase();
  expect(body, "no 'try demo case'").not.toContain("try demo case");
  expect(body, "no deterministic demo no-api-keys promise").not.toContain(
    "deterministic demo — no api keys, no cost",
  );

  // hygiene: backend is UP, so zero console/page errors and zero failed resources.
  expect(pageErrors, "no page errors").toEqual([]);
  expect(consoleErrors, "no console errors").toEqual([]);
  expect(failed, "no failed resources").toEqual([]);

  test.info().attach("qa-phase21b-probe-fail-a.json", {
    body: JSON.stringify({
      ctaLabel: await demoButton.textContent(),
      note: noteText,
      modeLine: lineText,
      deterministicPromiseAbsent: true,
      demoNoticeAbsent: true,
    }, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({
    path: "artifacts/screenshots/qa-phase21b-probe-fail-a.png",
    fullPage: true,
  });
});

test("DEF-097 (b) capability endpoint UNREACHABLE — neutral CTA + neutral qualifier/note/mode line, no deterministic claim", async ({
  page,
}) => {
  test.skip(STACK !== "b", `run with QA_P21B_STACK=b (current=${STACK})`);
  await page.goto("/", { waitUntil: "domcontentloaded" });

  // (1) CTA = neutral truthful label.
  const demoButton = page.getByTestId("try-demo");
  await expect(demoButton).toBeVisible({ timeout: 30_000 });
  await expect(demoButton).toHaveText("Try an example case");

  // (2) CTA note = provider-neutral pipeline copy.
  const demoNote = page.getByTestId("try-demo-note");
  await expect(demoNote).toBeVisible({ timeout: 30_000 });
  await expect(demoNote).toHaveText("Runs the same generation pipeline as a custom prompt.");

  // (3) qualifier + per-path note + mode line = NEUTRAL reachability copy.
  await expect(page.getByTestId("provider-qualifier")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("provider-qualifier")).toHaveText(
    "Generation is available once the service is reachable.",
  );
  await expect(page.getByTestId("generate-provider-note")).toHaveText(
    "runs through the backend-configured generation pipeline once the service is reachable",
  );
  const line = page.getByTestId("generation-mode-line");
  await expect(line).toBeVisible({ timeout: 15_000 });
  await expect(line).toHaveText("Generation mode: Available once the service is reachable.");

  // (4) NO demo notice, NO "Demo build"/no-API-keys claim ANYWHERE.
  await expect(page.getByTestId("generation-mode-demo-notice")).toHaveCount(0);
  const body = (await page.locator("body").innerText()).toLowerCase();
  expect(body, "no 'demo build' claim").not.toContain("demo build");
  expect(body, "no 'no api keys' claim").not.toContain("no api keys");
  expect(body, "no 'deterministic demo' claim").not.toContain("deterministic demo");
  expect(body, "no 'demo mode active'").not.toContain("demo mode active");

  test.info().attach("qa-phase21b-probe-fail-b.json", {
    body: JSON.stringify({
      ctaLabel: await demoButton.textContent(),
      note: (await demoNote.textContent()) ?? "",
      qualifier: (await page.getByTestId("provider-qualifier").textContent()) ?? "",
      modeLine: (await line.textContent()) ?? "",
      deterministicClaimAbsent: true,
    }, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({
    path: "artifacts/screenshots/qa-phase21b-probe-fail-b.png",
    fullPage: true,
  });
});

test("DEF-096/097 (c) CONTROL — fake backend keeps the truthful demo copy byte-identical (zero regression)", async ({
  page,
}) => {
  test.skip(STACK !== "c", `run with QA_P21B_STACK=c (current=${STACK})`);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const failed: Array<{ url: string; status: number }> = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 400) failed.push({ url: response.url, status: response.status() });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });

  // (1) the historical demo CTA + deterministic promise are truthful here.
  const demoButton = page.getByTestId("try-demo");
  await expect(demoButton).toBeVisible({ timeout: 30_000 });
  await expect(demoButton).toHaveText("Try Demo Case");
  const demoNote = page.getByTestId("try-demo-note");
  await expect(demoNote).toBeVisible({ timeout: 30_000 });
  await expect(demoNote).toHaveText("Deterministic demo — no API keys, no cost.");

  // (2) qualifier + mode line + demo notice keep the exact pre-21B copy.
  await expect(page.getByTestId("provider-qualifier")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("provider-qualifier")).toHaveText(
    "Demo build: deterministic built-in generator — no API keys, no cost. "
      + "Live AI is opt-in and not enabled in this build.",
  );
  await expect(page.getByTestId("generation-mode-line")).toHaveText(
    "Generation mode: Deterministic demo",
  );
  const demoNotice = page.getByTestId("generation-mode-demo-notice");
  await expect(demoNotice).toBeVisible({ timeout: 15_000 });
  await expect(demoNotice).toHaveText("Demo mode active");

  // hygiene: backend is UP, so zero console/page errors and zero failed resources.
  expect(pageErrors, "no page errors").toEqual([]);
  expect(consoleErrors, "no console errors").toEqual([]);
  expect(failed, "no failed resources").toEqual([]);

  test.info().attach("qa-phase21b-probe-fail-c.json", {
    body: JSON.stringify({
      ctaLabel: "Try Demo Case",
      note: "Deterministic demo — no API keys, no cost.",
      qualifier: "Demo build: deterministic built-in generator — no API keys, no cost. "
        + "Live AI is opt-in and not enabled in this build.",
      modeLine: "Generation mode: Deterministic demo",
      demoNotice: "Demo mode active",
    }, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({
    path: "artifacts/screenshots/qa-phase21b-probe-fail-c.png",
    fullPage: true,
  });
});