import { expect, test } from "@playwright/test";

/**
 * PHASE 18A — LANDING PAGE PROVIDER TRUTHFULNESS (QA-owned; .rad/roles/qa.md,
 * .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * On the hermetic FAKE stack (production build via vite preview :4173,
 * backend :8000 with GENERATION_PROVIDER=fake + ENV_FILE=os.devnull,
 * CORS_ALLOWED_ORIGINS=http://localhost:4173,http://localhost:5173) the
 * landing page must:
 *
 *  A1  advertise the deterministic demo (the Try Demo Case path + the
 *      "Deterministic demo — no API keys, no cost." note);
 *  A2  NEVER claim a "Live AI provider" / local-live availability for this
 *      fake stack (the capability DTO drives the copy — verified unit-level
 *      too; this is the real-browser proof);
 *  A3  render the capability-driven provider qualifier near the primary CTA
 *      (data-testid=provider-qualifier) with the honest deterministic copy;
 *  A4  show the "uses the built-in deterministic generator in this demo
 *      build" path note for the Generate-a-new-mystery entry;
 *  A5  0 console/page errors; 0 failed resources; 0 external traffic.
 */

const DETERMINISTIC_NOTE = "Deterministic demo — no API keys, no cost.";
const BUILTIN_NOTE = "uses the built-in deterministic generator in this demo build";
const QUALIFIER_DEMO = "Demo build: deterministic built-in generator";

test("Phase 18A: fake stack landing page promises ONLY the deterministic demo — never a Live AI claim", async ({
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

  // A1: primary demo entry + the deterministic note.
  await expect(page.getByTestId("try-demo")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("try-demo-note")).toHaveText(DETERMINISTIC_NOTE);

  // A3: the capability-driven provider qualifier is visible AND honest.
  await expect(page.getByTestId("provider-qualifier")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("provider-qualifier")).toContainText(QUALIFIER_DEMO);

  // A4: the Generate path note says deterministic-demo built-in on this stack.
  await expect(page.getByTestId("generate-provider-note")).toContainText(BUILTIN_NOTE);

  // A2: the page NEVER claims a live/local provider on the fake stack.
  const bodyText = (await page.locator("body").innerText()).toLowerCase();
  expect(bodyText, "no Live AI provider claim").not.toContain("live ai provider");
  expect(bodyText, "no local AI claim").not.toContain("local ai is");
  expect(bodyText, "no 'live ai enabled' claim").not.toContain("live ai enabled");

  // The capability DTO the page actually received carried demo=true only.
  const capabilities = await page.evaluate(async () => {
    const res = await fetch("http://localhost:8000/api/v1/generation-capabilities");
    return res.json();
  });
  const demo = capabilities.modes.find((m: { id: string }) => m.id === "demo");
  const local = capabilities.modes.find((m: { id: string }) => m.id === "local");
  const live = capabilities.modes.find((m: { id: string }) => m.id === "live");
  expect(demo?.available, "demo available on the fake stack").toBe(true);
  expect(local?.available, "local NOT available on the fake stack").toBe(false);
  expect(live, "live mode not configured on the fake stack").toBeUndefined();

  // A5: hygiene.
  expect(pageErrors, "page errors").toEqual([]);
  expect(consoleErrors, "console errors").toEqual([]);
  expect(failed, "failed resources").toEqual([]);
  expect(external, "external traffic").toEqual([]);
  test.info().attach("phase18a-landing-truthfulness.json", {
    body: JSON.stringify({
      deterministicNote: DETERMINISTIC_NOTE,
      qualifier: QUALIFIER_DEMO,
      builtinNote: BUILTIN_NOTE,
      capabilities: capabilities.modes,
      bodyTokens: { liveProvider: bodyText.includes("live ai provider") },
    }, null, 2),
    contentType: "application/json",
  });
});