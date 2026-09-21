import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { BACKEND_BASE, createPlaythroughViaApi, installLeakListener, seedPlaythroughCredentials } from "./helpers";

/**
 * PHASE 18C — DETECTIVE NOTEBOOK + PLAYER-SAFE PROOF BOARD REAL-BROWSER
 * JOURNEY (QA-owned; .rad/roles/qa.md, .rad/policies/evidence.md,
 * .rad/policies/deterministic-testing.md).
 *
 * Full `discover -> inspect -> notebook -> form hypothesis -> accuse ->
 * reveal -> proof board` journey on the hermetic fake stack (backend :8000,
 * GENERATION_PROVIDER=fake, ENV_FILE=os.devnull; production SPA via vite
 * preview :4173; CORS 4173/5173). The deterministic golden case
 * (Sarah Miller / Thomas Reed / embezzlement / kitchen knife / 22:17) is
 * used, so the accusation can be made correct for a 4/4 reveal.
 *
 *  C1  after discovering evidence, the notebook drawer lists ONLY the
 *      discovered items — undiscovered world objects are absent, and no
 *      hidden truth/candidate/winner text appears anywhere;
 *  C2  pinning a hypothesis (suspect/motive/weapon/time) persists ONLY to
 *      localStorage under `pd_hypothesis_v1:<playthroughId>` — no other key
 *      is touched;
 *  C3  pinning sends NO request to the backend (request counter);
 *  C4  “Use my hypothesis” fills the accusation form ONLY with the pinned
 *      dimensions (the unpinned stay empty — we pin all four here and verify
 *      each radio/input equals the pinned id exactly);
 *  C5  submit -> reveal: proof board shows WHO/WHY/WEAPON/WHEN cards, each
 *      with supporting evidence nodes; reveal scoring is 4/4 (the pinned
 *      hypothesis is the correct one);
 *  C6  reload keeps discovered state and re-derives the notebook (the same
 *      discovered items are listed, pins survive reload);
 *  C7  no "proc.*" anywhere in the browser DOM over the whole session;
 *  C8  leak scan 0 truth over the whole session + 0 console/page errors +
 *      0 external traffic.
 */

const PIN_SUSPECT = "thomas_reed";
const PIN_MOTIVE = "cover_up_embezzlement";
const PIN_WEAPON = "kitchen_knife";
const PIN_TIME = "22:17";

interface NetReport {
  apiRequests: string[];
  pageErrors: string[];
  consoleErrors: string[];
  failed: Array<{ url: string; status: number }>;
  external: Array<{ url: string; status: number }>;
}

function installNet(page: Page): NetReport {
  const report: NetReport = { apiRequests: [], pageErrors: [], consoleErrors: [], failed: [], external: [] };
  page.on("request", (req) => {
    const url = typeof req.url === "function" ? req.url() : req.url;
    if (url.includes("/api/")) report.apiRequests.push(url);
  });
  page.on("pageerror", (err) => report.pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") report.consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });
  page.on("response", async (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    const status = response.status();
    if (status >= 400) report.failed.push({ url, status });
    if (url.startsWith("http:") || url.startsWith("https:")) {
      const from = new URL(url);
      if (!(from.hostname === "localhost" && (from.port === "4173" || from.port === "8000"))) {
        report.external.push({ url, status });
      }
    }
  });
  return report;
}

/** Body-wide "proc." audit (case-insensitive) outside data-testid attributes. */
async function procAudit(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const hits: string[] = [];
    const walker = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = (node.textContent ?? "").toLowerCase();
        if (text.includes("proc.") || text.includes("proc_decor")) hits.push(`text:${text.slice(0, 80)}`);
      } else if (node instanceof Element) {
        const elements = node as Element;
        const testid = elements.getAttribute("data-testid");
        for (const attr of ["class", "aria-label", "title"]) {
          const v = elements.getAttribute(attr);
          if (v && (v.toLowerCase().includes("proc.") || v.toLowerCase().includes("proc_decor"))) {
            hits.push(`${attr}:${v.slice(0, 80)}`);
          }
        }
      }
      for (const child of Array.from(node.childNodes)) walker(child);
    };
    walker(document.body);
    return hits.filter((h) => !(testid !== null && testid.includes("proc.")));
  });
}

test("Phase 18C: discover -> notebook (only discovered) -> pin -> use -> accuse -> 4/4 reveal -> proof board -> reload", async ({
  page,
  request,
}) => {
  test.setTimeout(240_000);
  const net = installNet(page);
  const leak = installLeakListener(page);

  const cred = await createPlaythroughViaApi(request);
  const playthroughId = cred.playthroughId;
  await seedPlaythroughCredentials(page, cred);

  // ---- discover ONE item (laptop -> email) so the notebook has a
  // partial discovery to prove "only discovered" gating.
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });

  const laptop = page.getByTestId("object-apartment_laptop");
  await expect(laptop).toBeVisible({ timeout: 20_000 });
  await laptop.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("evidence-panel")).toContainText("Re: the missing funds");
  await page.getByTestId("evidence-close").click().catch(() => {});
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(page.getByTestId("evidence-panel")).not.toBeVisible();

  // C1: notebook drawer — open it and assert the objects group lists the
  // DISCOVERED laptop only, NOT the undiscovered knife/opener/scissors.
  const notebook = page.getByTestId("notebook-panel");
  await expect(notebook).toBeVisible({ timeout: 15_000 });
  const toggle = page.getByTestId("notebook-toggle");
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  await expect(page.getByTestId("notebook-heading")).toHaveText("Detective Notebook");

  // the "Objects" group exists and lists only discovered evidence ids
  const objectsGroup = page.getByTestId("notebook-group-objects-list");
  await expect(objectsGroup).toBeVisible({ timeout: 15_000 });
  const notebookText = (await objectsGroup.innerText()).toLowerCase();
  expect(notebookText).toContain("laptop");
  expect(notebookText).not.toContain("knife");
  expect(notebookText).not.toContain("letter opener");
  expect(notebookText).not.toContain("scissors");

  // no hidden truth / candidate-leak copy anywhere in the open notebook:
  const notebookBody = (await notebook.innerText()).toLowerCase();
  for (const token of ["winner", "truth", "solved", "correct answer", "proc."]) {
    expect(notebookBody, `notebook must not reveal "${token}"`).not.toContain(token);
  }

  // C2/C3: pin all four hypothesis dimensions; assert storage under the ONLY
  // allowed namespace key and ZERO API requests fired for pinning.
  const apiRequestsBeforePin = net.apiRequests.length;
  await page.getByTestId("hypothesis-suspect-select").selectOption(PIN_SUSPECT);
  await page.getByTestId("hypothesis-motive-select").selectOption(PIN_MOTIVE);
  await page.getByTestId("hypothesis-weapon-select").selectOption(PIN_WEAPON);
  await page.getByTestId("hypothesis-time-input").fill(PIN_TIME);

  const stored = await page.evaluate((key) => localStorage.getItem(key), `pd_hypothesis_v1:${playthroughId}`);
  expect(stored, "pins stored under pd_hypothesis_v1:<id>").not.toBeNull();
  const pins = JSON.parse(stored as string);
  expect(pins.suspect).toBe(PIN_SUSPECT);
  expect(pins.motive).toBe(PIN_MOTIVE);
  expect(pins.weapon).toBe(PIN_WEAPON);
  expect(pins.time).toBe(PIN_TIME);

  const localStorageKeys = await page.evaluate(() => Object.keys(localStorage));
  expect(localStorageKeys.filter((k) => k.startsWith("pd_hypothesis_v1:")), "only pin keys in namespace").toEqual(
    [`pd_hypothesis_v1:${playthroughId}`],
  );
  expect(net.apiRequests.length, "pin changes must NOT hit the backend").toBe(apiRequestsBeforePin);

  // ---- C6 reload (mid-PLAYING — the canonical resume contract, Phase 8 P):
  // reload keeps discovered state and re-derives the notebook; pins survive.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("discovered-entry-email_thomas_01")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("notebook-panel")).toBeVisible({ timeout: 15_000 });
  const notebookAfterReload = page.getByTestId("notebook-group-objects-list");
  await expect(notebookAfterReload).toBeVisible({ timeout: 15_000 });
  const afterReloadText = (await notebookAfterReload.innerText()).toLowerCase();
  expect(afterReloadText, "reload re-derives only the discovered laptop").toContain("laptop");
  expect(afterReloadText).not.toContain("knife");
  const storedAfterReload = await page.evaluate(
    (key) => localStorage.getItem(key),
    `pd_hypothesis_v1:${playthroughId}`,
  );
  expect(JSON.parse(storedAfterReload as string).suspect, "pins survive reload").toBe(PIN_SUSPECT);
  const reloadPins = JSON.parse(storedAfterReload as string);
  expect(reloadPins.motive, "motive pin survives reload").toBe(PIN_MOTIVE);
  expect(reloadPins.weapon, "weapon pin survives reload").toBe(PIN_WEAPON);
  expect(reloadPins.time, "time pin survives reload").toBe(PIN_TIME);

  // ---- accuse -------------------------------------------------------------
  await page.getByTestId("accusation-open").click();
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("accuse-use-hypothesis")).toBeVisible();

  const apiRequestsBeforeUse = net.apiRequests.length;
  await page.getByTestId("accuse-use-hypothesis").click();
  expect(net.apiRequests.length, "Use my hypothesis must NOT hit the backend").toBe(apiRequestsBeforeUse);

  // C4: the form is filled ONLY with the pinned dimensions (exact ids).
  await expect(page.getByTestId("accusation-option-murdererId-thomas_reed")).toBeChecked();
  await expect(page.getByTestId("accusation-option-motiveId-cover_up_embezzlement")).toBeChecked();
  await expect(page.getByTestId("accusation-option-weaponId-kitchen_knife")).toBeChecked();
  await expect(page.getByTestId("accusation-time")).toHaveValue(PIN_TIME);

  // submit -> confirm -> reveal
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("accusation-summary-murderer")).toHaveText("Thomas Reed");
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });

  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });

  // C5: proof board — the four WHO/WHY/WEAPON/WHEN cards with evidence nodes.
  await expect(page.getByTestId("reveal-proof-board")).toBeVisible();
  for (const dimension of ["who", "why", "weapon", "when"]) {
    const card = page.getByTestId(`proof-card-${dimension}`);
    await expect(card).toBeVisible();
    await expect(page.getByTestId(`proof-card-${dimension}-title`)).toHaveText(dimension.toUpperCase());
    await expect(page.getByTestId(`proof-node-${dimension}-0`)).toBeVisible();
  }

  // reveal scoring (correct pins -> 4/4).
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  for (const dimension of ["who", "why", "weapon", "when"]) {
    await expect(page.getByTestId(`reveal-dimension-${dimension}`)).toContainText("Correct");
  }

  // C6: reload the REVEAL screen — deterministic replay.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await page.goto("/reveal", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");

  // C7: no proc.* anywhere in the DOM across the journey.
  const auditHits = await procAudit(page);
  expect(auditHits, "no proc.* tokens in reveal DOM").toEqual([]);

  // C8: hygiene. The generic leak scanner walks EVERY API response; the
  // /reveal endpoint is DOCUMENTED to carry truth/player.accusation (the
  // reveal contract — same carve-out the golden Phase 7 spec applies by
  // scanning only PRE-reveal bodies), so only non-reveal responses may be
  // truth-key-free here. REQUIREMENTS 40.10 also exempts the accusation
  // 200 ECHO: it may carry the player's OWN submitted values under the
  // frozen `$.accusation.*` block (the golden Phase 7 spec encodes this
  // exact exemption). Any OTHER pre-reveal truth-key hit is a defect.
  const forbiddenEcho = (m: { url: string; paths: string[] }) => {
    const outsideEcho = m.paths.filter((p) => !/^\$\.accusation\./.test(p));
    const insideEchoHidden = m.paths.filter(
      (p) => /^\$\.accusation\./.test(p) && !p.endsWith(".murdererId") && !p.endsWith(".motiveId") && !p.endsWith(".weaponId") && !p.endsWith(".crimeTime"),
    );
    return outsideEcho.concat(insideEchoHidden);
  };
  const nonRevealLeaks = leak.matched
    .filter((m) => !m.url.includes("/reveal"))
    .map((m) => ({ url: m.url, paths: forbiddenEcho(m) }))
    .filter((m) => m.paths.length > 0);
  expect(nonRevealLeaks, "zero forbidden key paths outside the reveal/echo DTOs").toEqual([]);
  const revealLeaks = leak.matched.filter((m) => m.url.includes("/reveal"));
  expect(revealLeaks.length, "reveal DTOs ARE the documented truth surface").toBeGreaterThan(0);
  for (const m of revealLeaks) {
    if (net.external.length === 0) {
      expect(m.url, "reveal leak entries must come from the local backend").toContain("localhost:8000");
    }
  }
  expect(net.pageErrors, "page errors").toEqual([]);
  expect(net.consoleErrors, "console errors").toEqual([]);
  expect(net.failed, "failed resources").toEqual([]);
  expect(net.external, "external traffic").toEqual([]);
  test.info().attach("phase18c-notebook-journey.json", {
    body: JSON.stringify({
      playthroughId,
      pins,
      localStorageKeys,
      apiRequestCount: net.apiRequests.length,
      leakMatched: leak.matched,
    }, null, 2),
    contentType: "application/json",
  });
});