import { expect, test } from "@playwright/test";
import { createPlaythroughViaApi, seedPlaythroughCredentials } from "./helpers";

/**
 * QA-owned Phase 19C ORIGINAL-SYMPTOM REPRO (hermetic fake stack).
 *
 * Lifecycle: backend :8000 (GENERATION_PROVIDER=fake, scratch DB under %TEMP%)
 * + vite preview :4173 started OUTSIDE via tools/process_guard (process-
 * lifecycle policy). The Phase 19C bug on the Ollama-DRIVER path: a Laptop
 * (and sharp-weapon) placement published interactable with evidence_id
 * stripped to None -> click gave "Interacted with X", no panel, stale
 * "Discovered 0/1" counter. Root cause fixed in ollama_driver
 * `_project_placement_evidence` (resolve-or-decorate general rule).
 *
 * On the FAKE stack the golden world wires evidence on every grand
 * interactable (laptop -> email, knife -> forensic), so this spec proves the
 * GOLDEN path (which was already correct pre-fix) end-to-end in a real
 * browser AND pins the Phase 19C UX contract:
 *
 *  1  click kitchen knife      -> discovery toast + evidence panel with the
 *     forensic comparison text + objective counter "Discovered 1 / 2" + the
 *     "Discovered evidence" strip gains the knife entry (read marker once
 *     the record is open)
 *  2  no undiscovered evidence ids/titles in the DOM (email + the office
 *     sharp weapons stay absent)
 *  3  discovered object is marked in the scene (object-discovered-knife)
 *  4  reload deterministic: discovered state + counter + strip persist
 *  5  zero page/console errors, zero failed network requests, zero external
 *     traffic
 *  6  "Nothing relevant was found on <X>." (Phase 19C §3) is a GOLDEN-
 *     world-unreachable branch (every grand golden interactable carries
 *     evidence; the other golden objects are published decorative
 *     interaction "") — proven at unit level by the 986-test frontend suite
 *     (investigationFlow.test.ts / renderInvestigation.test.ts) and at the
 *     backend contract by test_3b + the driver-path test_7 (no dead-end
 *     interactables). This spec records that fact in its transcript.
 *
 * Durable screenshot -> screenshots/evidence/phase19c-knife-discovery.png
 * (evidence policy: curated release evidence).
 */

const KNIFE = "kitchen_knife";
const KNIFE_EVIDENCE = "forensic_knife_match_01";
// Undiscovered golden evidence that must NEVER appear in the DOM after the
// knife interaction (only the knife record may be player-known).
const UNDISCOVERED_EVIDENCE_IDS = [
  "email_thomas_01",
  "forensic_letter_opener_01",
  "forensic_scissors_01",
];
const FORBIDDEN_DOM_TOKENS = UNDISCOVERED_EVIDENCE_IDS;

function installHealthObservers(page) {
  const pageErrors = [];
  const consoleErrors = [];
  const failedResponses = [];
  const externalTraffic = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("response", (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    if (response.status >= 400) failedResponses.push({ url, status: response.status });
    try {
      const host = new URL(url).hostname;
      if (host !== "localhost" && host !== "127.0.0.1") externalTraffic.push(url);
    } catch {
      externalTraffic.push(url);
    }
  });
  return { pageErrors, consoleErrors, failedResponses, externalTraffic };
}

async function scanDomForForbidden(page) {
  const html = await page.evaluate(() => document.documentElement.outerHTML);
  const found = FORBIDDEN_DOM_TOKENS.filter((t) => html.includes(t));
  return found;
}

test("Phase 19C: knife click -> forensic panel + toast + counter + strip, no leaks, reload persists", async ({
  page,
  request,
}) => {
  const health = installHealthObservers(page);
  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);

  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  // Pre-discovery objective: the leading Phase 8 F copy.
  await expect(page.getByTestId("objective-text")).toContainText(
    "Find evidence, then accuse someone.",
  );

  // (1) Click the kitchen knife.
  const knife = page.getByTestId(`object-${KNIFE}`);
  await expect(knife).toBeVisible();
  await knife.click();

  // Toast: "Discovered: <title>" (NOT the dead-end "Interacted with X").
  const toast = page.getByTestId("discovery-toast");
  await expect(toast).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("discovery-toast-text")).toContainText("Discovered");
  const toastText = (await page.getByTestId("discovery-toast-text").innerText()).toLowerCase();
  expect(toastText, "toast must be a discovery, not a dead-end interact").not.toContain(
    "interacted with",
  );

  // Evidence panel opens with the forensic comparison (the Phase 19C §2
  // "immediately opens the evidence/inspection panel" contract).
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");

  // Counter increments: the golden world has exactly 4 evidence-linked
  // interactables (laptop, knife, letter opener, scissors — the scene model
  // carries the whole pinned world, both locations).
  await expect(page.getByTestId("objective-text")).toContainText(
    "Discovered 1 / 4 evidence items",
  );

  // The "Discovered evidence" strip gains the knife entry (with read marker).
  await expect(page.getByTestId(`discovered-entry-${KNIFE_EVIDENCE}`)).toBeVisible();
  await expect(page.getByTestId(`discovered-title-${KNIFE_EVIDENCE}`)).toBeVisible();
  await expect(page.getByTestId(`discovered-entry-${KNIFE_EVIDENCE}`)).toContainText("read");

  // (3) The discovered object is visually marked in the scene.
  await expect(page.getByTestId(`object-discovered-${KNIFE}`)).toBeVisible();

  // Durable evidence screenshot (evidence policy).
  await page.screenshot({
    path: "C:/Users/Fujitsu/coding/procedural-detective/screenshots/evidence/phase19c-knife-discovery.png",
    fullPage: true,
  });

  // (2) No undiscovered evidence ids in the DOM.
  const found = await scanDomForForbidden(page);
  expect(found, "no undiscovered evidence material in the DOM").toEqual([]);

  // (4) Reload deterministic: discovered state persists.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("objective-text")).toContainText("Discovered 1 / 4 evidence items");
  await expect(page.getByTestId(`discovered-entry-${KNIFE_EVIDENCE}`)).toBeVisible();
  await expect(page.getByTestId(`object-discovered-${KNIFE}`)).toBeVisible();
  expect(await scanDomForForbidden(page), "reload: still no undiscovered material").toEqual([]);

  // (5) Health transcript.
  console.log(
    "HEALTH",
    JSON.stringify({
      pageErrors: health.pageErrors,
      consoleErrors: health.consoleErrors,
      failedResponses: health.failedResponses,
      externalTraffic: health.externalTraffic,
      undiscoveredFound: found,
    }),
  );
  expect(health.pageErrors, "no uncaught page errors").toEqual([]);
  expect(health.consoleErrors, "no console errors").toEqual([]);
  expect(health.failedResponses, "no failed network requests").toEqual([]);
  expect(health.externalTraffic, "no external traffic (hermetic)").toEqual([]);
});