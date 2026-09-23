import { expect, test } from "@playwright/test";

/**
 * PHASE 21B Finding 3 — judge-facing example-case CTA truthfulness on the
 * OLLAMA SEAM STACK (QA-owned; .rad/roles/qa.md, .rad/policies/evidence.md,
 * .rad/policies/deterministic-testing.md).
 *
 * Stack contract: backend :8000 runs GENERATION_PROVIDER=ollama +
 * OLLAMA_BASE_URL=http://127.0.0.1:11498 (the QA fake-ollama seam, a working
 * provider answering /api/tags + /api/chat). The production SPA on vite
 * preview :4173 (split-stack build pointing at :8000) MUST:
 *
 *   - CTA label "Try an example case" (NEVER "Try Demo Case" — an
 *     Ollama-configured backend must not advertise the deterministic demo);
 *   - the try-demo-note names the local AI provider + carries the explicit
 *     "Not the free deterministic demo." warning and NEVER the
 *     "Deterministic demo — no API keys, no cost." promise;
 *   - the read-only mode line is "Generation mode: Local AI — llama3.2:3b —
 *     Ready";
 *   - clicking the CTA reaches /generating and PUBLISHES through the REAL
 *     ollama provider (the seam answers /api/chat) and lands on a booted
 *     /scene;
 *   - 0 console/page errors, 0 failed resources, 0 external traffic.
 *
 * This is the judge-facing fix the Phase21B-PAC §3 mandates ("Ollama
 * deployment never labels an Ollama request as deterministic demo").
 */

const LOCAL_NOTE =
  "Example case runs the local AI provider (Local AI — llama3.2:3b). Not the free deterministic demo.";

test("Phase 21B Finding 3: ollama seam stack NEVER claims deterministic demo — truthful local CTA", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const failed: Array<{ url: string; status: number }> = [];
  const external: Array<{ url: string; status: number }> = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });
  page.on("response", (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    const status = response.status();
    if (status >= 400) failed.push({ url, status });
    if (url.startsWith("http:") || url.startsWith("https:")) {
      const from = new URL(url);
      if (!(from.hostname === "localhost" && (from.port === "4173" || from.port === "8000"))) {
        external.push({ url, status });
      }
    }
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });

  // --- (1) the CTA label is the truthful rename, never "Try Demo Case" ---
  const demoButton = page.getByTestId("try-demo");
  await expect(demoButton).toBeVisible({ timeout: 30_000 });
  await expect(demoButton).toHaveText("Try an example case");

  // --- (2) the note names the local AI provider + the explicit "not the
  //          free deterministic demo" warning, NEVER the deterministic promise
  const demoNote = page.getByTestId("try-demo-note");
  await expect(demoNote).toBeVisible({ timeout: 30_000 });
  await expect(demoNote).toHaveText(LOCAL_NOTE);
  expect((await demoNote.textContent()) ?? "", "no deterministic/no-cost promise in the CTA note")
    .not.toContain("Deterministic demo — no API keys, no cost.");

  const bodyText = (await page.locator("body").innerText()).toLowerCase();
  expect(bodyText, "no 'Try Demo Case'").not.toContain("try demo case");
  expect(bodyText, "the exact demo promise string is absent")
    .not.toContain("deterministic demo — no api keys");

  // --- (3) the read-only generation-mode line is the truthful local line ---
  const line = page.getByTestId("generation-mode-line");
  await expect(line).toBeVisible({ timeout: 30_000 });
  await expect(line).toHaveText("Generation mode: Local AI — llama3.2:3b — Ready");

  // --- (4) clicking runs the OLLAMA path end-to-end (the seam answers) ---
  await demoButton.click();
  await expect(page).toHaveURL(/\/generating/, { timeout: 15_000 });
  await expect(page.getByTestId("enter-investigation")).toBeVisible({ timeout: 120_000 });
  await page.getByTestId("enter-investigation").click();
  await expect(page).toHaveURL(/\/scene/, { timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("scene-error")).toHaveCount(0);

  // --- hygiene ---
  expect(pageErrors, "no page errors").toEqual([]);
  expect(consoleErrors, "no console errors").toEqual([]);
  expect(failed, "no failed resources").toEqual([]);
  expect(external, "no external traffic").toEqual([]);

  test.info().attach("phase21b-ollama-cta.json", {
    body: JSON.stringify({
      ctaLabel: "Try an example case",
      note: LOCAL_NOTE,
      modeLine: "Generation mode: Local AI — llama3.2:3b — Ready",
      deterministicPromiseAbsent: true,
    }, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({ path: "artifacts/screenshots/qa-phase21b-ollama-cta.png", fullPage: true });
});