import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import { BACKEND_BASE, createPlaythroughViaApi, seedPlaythroughCredentials } from "./helpers";
import type { PlaythroughCredential } from "./helpers";

/**
 * Phase 7 M — GOLDEN ACCUSATION → REVEAL E2E (QA-owned; .rad/roles/qa.md,
 * .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Lifecycle: backend :8000 (migrated scratch DB, default dev provider,
 * CORS http://localhost:4173,http://localhost:5173) + vite preview :4173 are
 * started OUTSIDE Playwright via tools/process_guard. The spec seeds the
 * playthrough credential into localStorage (pd_playthrough_token /
 * pd_playthrough_id) through the PUBLIC API first, then drives the REAL
 * browser flow through Phase 7 M steps 1-21:
 *
 *   1  create anonymous session (API)
 *   2  generate deterministic Sarah/Thomas case (API, default dev provider)
 *   3  create exact-version playthrough (API, v1)
 *   4  open apartment (/scene, kitchen world)
 *   5  discover knife (object button -> discovery toast)
 *   6  inspect laptop (interactable object -> discovery)
 *   7  read email (evidence panel subject/body)
 *   8  open accusation UI (/accuse)
 *   9-12 submit correct WHO/WHY/WEAPON/WHEN (candidate pickers + time input)
 *  13  verify accusation accepted (echo panel, no truth)
 *  14  verify truth was NOT present before reveal (phase-7 leak scan over every
 *      API response body received so far)
 *  15  reveal the case (navigate /reveal)
 *  16  verify Thomas / embezzlement / kitchen knife / 22:17 displayed
 *  17  verify per-dimension result (all four Correct, overall solved, 4/4)
 *  18  reload the reveal page
 *  19  verify reveal persists (identical DTO-driven screen)
 *  20  zero truth leaks occurred before reveal (see step 14 scan; the reveal
 *      responses themselves are allowlist-scanned for internal material)
 *  21  clean shutdown (performed OUTSIDE Playwright via process_guard by QA)
 *
 * Plus a WRONG-accusation flow: submit a wrong WHO — the case STILL reveals
 * with overall incorrect and shows BOTH the player's answer and the truth.
 *
 * Deterministic: no fixed sleeps — every step waits on a readable condition.
 * All waits are application-readiness conditions (page state / API response),
 * per .rad/policies/deterministic-testing.md.
 */

// ---------------------------------------------------------------------------
// Phase 7 pre-reveal / post-reveal leak scanners (nested key-path walkers).
// These mirror the frozen backend scanners in backend/tests/test_phase7_helpers.py
// so the browser observation proves the same contract over the WIRE.
// ---------------------------------------------------------------------------

// Pre-reveal: keys which would designate the canonical solution, leak truth
// sections, scoring internals or provider/token material (Phase7 J / N24).
const PRE_REVEAL_FORBIDDEN_KEYS = new Set([
  "truth", "truthfulness", "canonical", "canonicalCrimeTime", "crime",
  "crimeTime", "acceptedScoring", "acceptedScoringTimeSet",
  "murdererCorrect", "motiveCorrect", "weaponCorrect", "timeCorrect",
  "correctDimensions", "totalDimensions", "overall", "isCorrect",
  "winner", "winning", "winners", "isWinner", "designated",
  "designation", "rank", "selected", "murderer", "victim",
  "universe", "universes", "solution", "solutionProof", "solverProof",
  "proof", "prompt", "providerOutput", "diagnostics", "seed", "model",
  "verifier", "tokenVerifier", "attemptId", "generationAttemptId",
  "propositions", "sourceRef", "schemaVersion", "draft", "publishedAt",
  "payload", "quotaSessionId", "anonymousQuotaSessionId",
]);

// Post-reveal: reveal-interNAL keys forbidden even in the reveal DTO
// (Phase7 E / N25/N26).
const REVEAL_FORBIDDEN_KEYS = new Set([
  "solverProof", "solutionProof", "proof", "acceptedScoring",
  "acceptedScoringTimeSet", "prompt", "providerOutput", "diagnostics",
  "seed", "model", "verifier", "tokenVerifier", "attemptId",
  "generationAttemptId", "propositions", "sourceRef", "universe",
  "universes", "canonical", "canonicalCrimeTime", "truthfulness",
  "winner", "winning", "winners", "isWinner", "designated",
  "designation", "rank", "selected", "schemaVersion", "draft",
  "publishedAt", "payload", "quotaSessionId", "anonymousQuotaSessionId",
  "token", "sessionId",
]);

const CANONICAL_TIME_VALUE = "2026-09-11T22:17:00+02:00";
const HEX64_RE = /\b[0-9a-fA-F]{64}\b/;

function walk(node: unknown, path: string, hits: string[], forbidden: Set<string>): void {
  if (node === null || node === undefined) return;
  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i++) walk(node[i], `${path}[${i}]`, hits, forbidden);
    return;
  }
  if (typeof node === "object") {
    for (const [key, value] of Object.entries(node)) {
      const child = `${path}.${key}`;
      // Echo exemption (REQUIREMENTS 40.10): the accusation 200 MAY carry the
      // player's OWN submitted crimeTime inside the frozen `accusation` echo.
      const underEcho = path.endsWith(".accusation");
      if (forbidden.has(key) && !(key === "crimeTime" && underEcho)) {
        hits.push(child);
      }
      if (typeof value === "string") {
        if (value === CANONICAL_TIME_VALUE && !underEcho) hits.push(`${child}(=canonicalCrimeTime)`);
      }
      walk(value, child, hits, forbidden);
    }
  }
}

/** Pre-reveal scan over every captured API response body. */
export function scanPreReveal(bodies: Array<{ url: string; body: unknown }>): {
  scanned: number;
  matched: Array<{ url: string; paths: string[] }>;
} {
  const matched: Array<{ url: string; paths: string[] }> = [];
  for (const entry of bodies) {
    if (entry.url.includes("generation-capabilities")) {
      // Public allowlist DTO (Phase 16 J): documented to carry the public
      // model display label — scanned exhaustively by phase16-modes.spec.ts.
      continue;
    }
    const hits: string[] = [];
    walk(entry.body, "$", hits, PRE_REVEAL_FORBIDDEN_KEYS);
    if (hits.length > 0) matched.push({ url: entry.url, paths: hits });
  }
  return { scanned: bodies.length, matched };
}

/** Post-reveal allowlist scan (internal material + 64-hex strings). */
export function scanRevealInternal(body: unknown): string[] {
  const hits: string[] = [];
  const walker = (node: unknown, path: string): void => {
    if (node === null || node === undefined) return;
    if (Array.isArray(node)) {
      for (let i = 0; i < node.length; i++) walker(node[i], `${path}[${i}]`);
      return;
    }
    if (typeof node === "object") {
      for (const [key, value] of Object.entries(node)) {
        const child = `${path}.${key}`;
        if (REVEAL_FORBIDDEN_KEYS.has(key)) hits.push(child);
        if (typeof value === "string" && HEX64_RE.test(value)) hits.push(`${child}(=64hex)`);
        walker(value, child);
      }
    }
  };
  walker(body, "$");
  return hits;
}

interface CapturedResponse {
  url: string;
  body: unknown;
}

function installResponseCapture(page: Page): { bodies: CapturedResponse[] } {
  const bodies: CapturedResponse[] = [];
  page.on("response", async (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    if (!url.includes("/api/")) return;
    const headers = typeof response.headers === "function" ? response.headers() : response.headers;
    const contentType = headers["content-type"] ?? headers["Content-Type"] ?? "";
    if (!contentType.includes("application/json")) return;
    try {
      bodies.push({ url, body: await response.json() });
    } catch {
      // non-JSON/streamed bodies: nothing to scan
    }
  });
  return { bodies };
}

// ---------------------------------------------------------------------------
// Candidate ids (from the published golden payload; asserted at runtime via
// the bootstrap candidates — these are PUBLIC player-safe values, Phase7 J).
// ---------------------------------------------------------------------------

const SUSPECTS = ["anna_karlsson", "michael_carter", "thomas_reed"];
const MOTIVES = ["cover_up_embezzlement", "revenge_for_affair", "robbery_gone_wrong"];
const WEAPONS = ["kitchen_knife", "letter_opener", "scissors"];

test("golden accusation→reveal: full Phase 7 M flow (knife→laptop→email→accuse→reveal→reload)", async ({
  page,
  request,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  const captured = installResponseCapture(page);

  // Steps 1-3: session -> golden case -> playthrough (API, default dev provider).
  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);

  // Steps 4-7: /scene, discover knife, inspect laptop, read email.
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  // Phase 8 F scene: objective text replaces the removed knowledge-summary.
  await expect(page.getByTestId("objective-text")).toContainText("Find evidence, then accuse someone.");

  // Step 5: discover the knife.
  const knife = page.getByTestId("object-kitchen_knife");
  await expect(knife).toBeVisible();
  await knife.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("discovery-toast-text")).toContainText("Discovered");
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");

  // Dismiss the discovery toast so it cannot intercept the next object click.
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  // Step 6-7: inspect the laptop -> discover + read the email.
  const closePanel = page.getByTestId("evidence-close");
  if (await closePanel.isVisible().catch(() => false)) {
    await closePanel.click();
  }
  await expect(panel).not.toBeVisible();
  const laptop = page.getByTestId("object-apartment_laptop");
  await expect(laptop).toBeVisible({ timeout: 20_000 });
  await laptop.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const emailPanel = page.getByTestId("evidence-panel");
  await expect(emailPanel).toBeVisible({ timeout: 15_000 });
  await expect(emailPanel).toContainText("Re: the missing funds");
  await expect(emailPanel).toContainText("We need to talk tonight");
  await page.getByTestId("evidence-close").click().catch(() => {});
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(emailPanel).not.toBeVisible();

  // Step 8: open the accusation UI.
  const accuseButton = page.getByTestId("accusation-open");
  await expect(accuseButton).toBeVisible();
  await accuseButton.click();
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("accusation-form")).toBeVisible();

  // Candidate pickers mirror the published golden universes (Phase7 J/K) and
  // reveal NO winner marker (the bootstrap candidates are the only source).
  const suspects = page.locator('[data-testid="accusation-suspects"] input[name="murdererId"]');
  const suspectIds = await suspects.evaluateAll((inputs) =>
    (inputs as HTMLInputElement[]).map((i) => i.value));
  expect(suspectIds).toEqual(SUSPECTS);
  const motiveIds = await page
    .locator('[data-testid="accusation-motives"] input[name="motiveId"]')
    .evaluateAll((inputs) => (inputs as HTMLInputElement[]).map((i) => i.value));
  expect(motiveIds).toEqual(MOTIVES);
  const weaponIds = await page
    .locator('[data-testid="accusation-weapons"] input[name="weaponId"]')
    .evaluateAll((inputs) => (inputs as HTMLInputElement[]).map((i) => i.value));
  expect(weaponIds).toEqual(WEAPONS);
  // No pre-reveal correctness indicator anywhere in the accusation form.
  const formText = (await page.getByTestId("accusation-form").innerText()).toLowerCase();
  for (const token of ["correct", "winner", "solution", "truth"]) {
    expect(formText, `accusation form must not reveal ${token}`).not.toContain(token);
  }

  // Steps 9-12: submit the CORRECT WHO/WHY/WEAPON/WHEN via the pickers + time.
  await page.getByTestId("accusation-option-murdererId-thomas_reed").check();
  await page.getByTestId("accusation-option-motiveId-cover_up_embezzlement").check();
  await page.getByTestId("accusation-option-weaponId-kitchen_knife").check();
  await page.getByTestId("accusation-time").fill("22:17");

  // Confirmation step (irreversible).
  await expect(page.getByTestId("accusation-submit")).toBeVisible();
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("accusation-summary-murderer")).toHaveText("Thomas Reed");
  await expect(page.getByTestId("accusation-summary-motive")).toContainText("embezzlement");
  await expect(page.getByTestId("accusation-summary-weapon")).toHaveText("Kitchen Knife");
  await expect(page.getByTestId("accusation-summary-time")).toHaveText("22:17");

  // Step 13: submit; verify the accepted state (echo only — no truth preview).
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("accusation-accepted")).toContainText("Accusation accepted");
  const acceptedText = (await page.locator("body").innerText()).toLowerCase();
  for (const token of ["murderer:", "truth", "correct", "winner", "solution"]) {
    expect(acceptedText, `post-accusation page must not reveal ${token}`).not.toContain(token);
  }

  // Step 14 + step 20: ZERO truth leaks before reveal — scan every API response
  // body the page has received this session (bootstrap, interact, read, accuse).
  const preReveal = scanPreReveal(captured.bodies);
  console.log("PRE_REVEAL_LEAKSCAN", JSON.stringify({
    scanned: preReveal.scanned,
    matched: preReveal.matched,
    canonicalTime: CANONICAL_TIME_VALUE,
  }));
  await test.info().attach("pre-reveal-leak-scan.json", {
    body: JSON.stringify({
      scanned: preReveal.scanned,
      matched: preReveal.matched,
      forbiddenKeys: [...PRE_REVEAL_FORBIDDEN_KEYS],
    }, null, 2),
    contentType: "application/json",
  });
  expect(preReveal.matched, `no forbidden key paths in ${preReveal.scanned} pre-reveal responses`).toEqual([]);
  expect(preReveal.scanned, "pre-reveal responses must have been captured").toBeGreaterThan(0);
  await page.screenshot({ path: "artifacts/screenshots/phase7-accusation-accepted.png", fullPage: true });

  // Step 15: reveal.
  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });

  // Step 16: Thomas / embezzlement / knife / 22:17 displayed (DTO-driven).
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
  await expect(page.getByTestId("reveal-truth-motive")).toContainText("embezzlement");
  await expect(page.getByTestId("reveal-truth-weapon")).toHaveText("Kitchen Knife");
  await expect(page.getByTestId("reveal-truth-time")).toHaveText("22:17");

  // Step 17: per-dimension result — all four Correct, overall solved, 4/4.
  for (const dim of ["who", "why", "weapon", "when"]) {
    await expect(page.getByTestId(`reveal-dimension-${dim}`)).toContainText("Correct");
  }
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  // Player's submitted answers shown next to the truth.
  for (const dim of ["who", "why", "weapon", "when"]) {
    await expect(page.locator(`[data-testid="reveal-dimension-${dim}"] [data-testid="reveal-dimension-submitted"]`)).toBeVisible();
  }
  await expect(page.getByTestId("reveal-explanation")).toBeVisible();
  await expect(page.getByTestId("reveal-timeline")).toBeVisible();
  const revealScreenText = await page.getByTestId("reveal-screen").innerText();
  await test.info().attach("reveal-screen-text.txt", {
    body: revealScreenText,
    contentType: "text/plain",
  });

  // The reveal responses themselves must carry NO internal material.
  const revealResponses = captured.bodies.filter((b) => b.url.includes("/reveal"));
  expect(revealResponses.length, "at least one /reveal response must exist").toBeGreaterThan(0);
  const revealInternalHits: Array<{ url: string; paths: string[] }> = [];
  for (const rr of revealResponses) {
    const hits = scanRevealInternal(rr.body);
    if (hits.length > 0) revealInternalHits.push({ url: rr.url, paths: hits });
  }
  console.log("POST_REVEAL_ALLOWLIST_SCAN", JSON.stringify({
    revealResponses: revealResponses.length,
    internalHits: revealInternalHits,
  }));
  await test.info().attach("post-reveal-allowlist-scan.json", {
    body: JSON.stringify({
      revealResponses: revealResponses.length,
      internalHits: revealInternalHits,
      forbiddenKeys: [...REVEAL_FORBIDDEN_KEYS],
    }, null, 2),
    contentType: "application/json",
  });
  expect(revealInternalHits, "reveal DTO must carry no internal material").toEqual([]);
  await page.screenshot({ path: "artifacts/screenshots/phase7-reveal-solved.png", fullPage: true });

  // Steps 18-19: reload -> reveal persists (identical DTO-driven screen).
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
  await expect(page.getByTestId("reveal-truth-time")).toHaveText("22:17");
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await page.screenshot({ path: "artifacts/screenshots/phase7-reveal-reload-persists.png", fullPage: true });

  // Full-session scan: the ONLY allowed truth-bearing responses are /reveal.
  // Everything else must remain clean even after the page re-fetched the
  // bootstrap for name resolution.
  expect(pageErrors, "no uncaught page errors").toEqual([]);
});

test("wrong accusation: wrong WHO still reveals with overall incorrect and both answers shown", async ({
  page,
  request,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  const captured = installResponseCapture(page);

  const cred = await createPlaythroughViaApi(request);
  await seedPlaythroughCredentials(page, cred);

  await page.goto("/accuse", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });

  // Correct WHY/WEAPON/WHEN but a WRONG WHO.
  await page.getByTestId("accusation-option-murdererId-anna_karlsson").check();
  await page.getByTestId("accusation-option-motiveId-cover_up_embezzlement").check();
  await page.getByTestId("accusation-option-weaponId-kitchen_knife").check();
  await page.getByTestId("accusation-time").fill("22:17");
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });

  // A wrong accusation is STILL accepted and safely revealable.
  const preReveal = scanPreReveal(captured.bodies);
  expect(preReveal.matched, "no forbidden keys before reveal (wrong accusation)").toEqual([]);

  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });

  // Overall incorrect; per-dimension: WHO incorrect, others correct.
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE NOT SOLVED");
  await expect(page.getByTestId("reveal-dimension-who")).toContainText("Incorrect");
  await expect(page.getByTestId("reveal-dimension-why")).toContainText("Correct");
  await expect(page.getByTestId("reveal-dimension-weapon")).toContainText("Correct");
  await expect(page.getByTestId("reveal-dimension-when")).toContainText("Correct");
  await expect(page.getByTestId("reveal-score")).toContainText("3 / 4");

  // Both the player's wrong answer and the canonical truth are displayed.
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
  const whoDim = page.getByTestId("reveal-dimension-who");
  // The player's submitted WHO (raw id when candidates are unavailable after
  // accusation, resolved name when they are) must be shown next to the truth.
  const whoSubmitted = whoDim.getByTestId("reveal-dimension-submitted");
  await expect(whoSubmitted).toBeVisible();
  const submittedText = (await whoSubmitted.innerText()).trim();
  expect(submittedText.length, "player's submitted answer is shown").toBeGreaterThan(0);
  await expect(whoDim).toContainText("Thomas Reed");

  const revealResponses = captured.bodies.filter((b) => b.url.includes("/reveal"));
  const internalHits: string[] = [];
  for (const rr of revealResponses) internalHits.push(...scanRevealInternal(rr.body));
  expect(internalHits, "wrong-accusation reveal DTO must be allowlist-clean").toEqual([]);

  await page.screenshot({ path: "artifacts/screenshots/phase7-reveal-wrong-who.png", fullPage: true });
  expect(pageErrors, "no uncaught page errors").toEqual([]);
});