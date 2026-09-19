import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import * as path from "node:path";
import {
  BACKEND_BASE,
  installLeakListener,
  scanJsonBody,
  FORBIDDEN_KEY_PATHS,
} from "./helpers";

/**
 * PHASE 17C/17D WAVE 3 — REAL Hermes journey REMAINDER (QA-owned).
 *
 * Companion to e2e/phase17cd-hermes.spec.ts (whose §12 render acceptance is
 * blocked today by DEF-079: the frontend generated-definition validator still
 * enforces the pre-Phase-17D 0.05 floor, so the thin bronze ceremonial ice
 * pick's published definition is dropped and the object renders as the neutral
 * placeholder — asserted to be a REAL product defect with evidence there).
 *
 * This spec proves the REST of the Phase17C §12/§14 browser contract against
 * the REAL remote Hermes3:8b (one real full-case generation):
 *
 *   - Local-AI selector with the real probe + durable selection;
 *   - generation published through the real chain (office environment, world
 *     differs from the golden fixture);
 *   - the `proc.*` bronze ceremonial ice pick exists in the published scene
 *     (data-testid object-proc.*) with the deterministic evidence link;
 *   - SERVER-authoritative direct interaction: clicking the object opens the
 *     evidence panel (d_ev_weapon_true forensic comparison) — the interaction/
 *     evidence/panel contract is unaffected by the render demotion;
 *   - accuse WHO paul_becker / WHY stolen_research_data / WEAPON
 *     bronze_ceremonial_ice_pick / WHEN 23:42 -> accepted -> reveal
 *     "CASE SOLVED" + "4 / 4";
 *   - reload -> published world persists byte-identical (same proc.* assetId +
 *     definition from the /investigation bootstrap) and the reveal persists;
 *   - ZERO pre-reveal truth leaks and ZERO Ollama host/IP/port/URL material in
 *     the DOM or any API DTO (201 case response, bootstrap, capability, reveal).
 *
 * Evidence promoted to screenshots/evidence/phase17cd-hermes-*.
 * Not part of CI.
 */

const NONGOLDEN_PROMPT =
  "Victim: Dr. Anna Weiss\n" +
  "Murderer: Paul Becker\n" +
  "Motive: stolen research data\n" +
  "Weapon: bronze ceremonial ice pick\n" +
  "Time: 23:42\n" +
  "Witness: Lisa K\u00f6nig\n" +
  "Location: office\n";

const EXPECTED_MODEL = "hermes3:8b";
const SOLVER_IDS = {
  who: "paul_becker",
  why: "stolen_research_data",
  weapon: "bronze_ceremonial_ice_pick",
  when: "23:42",
};
const HOST_TOKENS = ["11434", "127.0.0.1", "host.docker.internal", "http://", "https://"] as const;
const IPV4_RE = /\b(?:\d{1,3}\.){3}\d{1,3}\b/;
const EVIDENCE_DIR = path.join(__dirname, "..", "screenshots", "evidence");

interface CapturedJson {
  url: string;
  method: string;
  body: unknown;
}

interface SessionReport {
  captured: CapturedJson[];
  pageErrors: string[];
  consoleErrors: string[];
  failed: Array<{ url: string; status: number }>;
  external: Array<{ url: string; status: number }>;
}

function installSessionObservers(page: Page): SessionReport {
  const report: SessionReport = { captured: [], pageErrors: [], consoleErrors: [], failed: [], external: [] };
  page.on("pageerror", (err) => report.pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") report.consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });
  page.on("response", async (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    const status = response.status();
    const method = response.request().method();
    if (url.startsWith("http:") || url.startsWith("https:")) {
      const from = new URL(url);
      if (from.hostname !== "localhost" && from.hostname !== "127.0.0.1") {
        report.external.push({ url, status });
      }
    }
    if (status >= 400) report.failed.push({ url, status });
    if (!url.includes("/api/")) return;
    const headers = typeof response.headers === "function" ? response.headers() : response.headers;
    const contentType = headers["content-type"] ?? headers["Content-Type"] ?? "";
    if (!contentType.includes("application/json")) return;
    try {
      report.captured.push({ url, method, body: await response.json() });
    } catch {
      report.captured.push({ url, method, body: null });
    }
  });
  return report;
}

function hostHitsInBlob(blob: string): string[] {
  const hits: string[] = [];
  if (IPV4_RE.test(blob)) hits.push("IPv4");
  for (const token of HOST_TOKENS) {
    if (blob.toLowerCase().includes(token.toLowerCase())) hits.push(token);
  }
  return hits;
}

function procSnapshot(body: unknown): string | null {
  if (body === null || typeof body !== "object") return null;
  const scene = (body as { scene?: { worldObjects?: unknown[] } }).scene;
  const world = (scene?.worldObjects ?? []).find(
    (o: { assetId?: string }) => typeof o?.assetId === "string" && o.assetId.startsWith("proc."),
  );
  if (world === undefined) return null;
  const w = world as Record<string, unknown>;
  // The IMMUTABLE published world part: objectId + assetId + the compiled
  // definition. Player-knowledge flags (discovered/read) legitimately change
  // as progress persists — they are not part of the byte-identical requirement.
  return JSON.stringify({ objectId: w.objectId, assetId: w.assetId, generated: w.generated });
}

test("Phase17C/17D Wave 3 — real Hermes journey remainder: proc.* interaction, accuse 4/4, byte-identical reload, zero leak, zero host", async ({
  page,
  request,
}) => {
  test.setTimeout(1_500_000);
  const net = installSessionObservers(page);
  const leak = installLeakListener(page);
  const evidence = (name: string) => path.join(EVIDENCE_DIR, name);

  // (1) Real capability probe -> selector "Local AI — hermes3:8b — Ready".
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const select = page.getByTestId("generation-mode-select");
  await expect(select).toBeVisible({ timeout: 60_000 });
  const options = await select.locator("option").allTextContents();
  expect(options.join(" | ")).toContain(`Local AI — ${EXPECTED_MODEL} — Ready`);
  await select.selectOption("local");
  await page.waitForTimeout(150);
  expect(await page.evaluate(() => localStorage.getItem("pd_generation_mode"))).toBe("local");

  // (2) One REAL full-case generation through the PUBLIC API.
  const sessionRes = await request.post(`${BACKEND_BASE}/api/v1/sessions/anonymous`);
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();
  const caseRes = await request.post(`${BACKEND_BASE}/api/v1/cases`, {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt: NONGOLDEN_PROMPT, difficulty: "medium" },
    timeout: 1_200_000,
  });
  expect(caseRes.status(), "create case against the REAL provider").toBe(201);
  const created = await caseRes.json();
  expect(created.status, "generation published through the real chain").toBe("PUBLISHED");
  expect(hostHitsInBlob(JSON.stringify(created)), "201 case DTO has zero host material").toEqual([]);
  const caseId: string = created.caseId as string;

  const ptRes = await request.post(
    `${BACKEND_BASE}/api/v1/cases/${caseId}/versions/1/playthroughs`,
    { headers: { Authorization: `Bearer ${created.creatorAccessToken}` } },
  );
  expect(ptRes.status(), "create playthrough").toBe(201);
  const pt = await ptRes.json();

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([pid, token]) => {
      localStorage.setItem("pd_playthrough_id", pid);
      localStorage.setItem("pd_playthrough_token", token);
    },
    [pt.playthroughId, pt.playthroughAccessToken] as const,
  );

  // (3) /scene: office environment + the generated proc.* object present.
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("scene-error")).toHaveCount(0);
  await expect(page.getByTestId("environment-notice")).toHaveText("Environment: Office");

  const procButtons = page.locator('[data-testid="object-bronze_ceremonial_ice_pick"]');
  expect(await procButtons.count(), "the bronze ceremonial ice pick world object is present").toBe(1);
  const firstProc = procButtons.first();
  const procButtonTestId = (await firstProc.getAttribute("data-testid"))!;
  const worldObjectId = procButtonTestId.replace(/^object-/, "");
  // The published world object is a GENERATED asset: its assetId is `proc.*`
  // and its /investigation bootstrap embeds the compiled `generated` block.
  const bootstrapsNow = net.captured.filter((c) => c.url.includes("/investigation")).map((c) => c.body);
  const lastBootstrap = bootstrapsNow[bootstrapsNow.length - 1];
  const procWorld = (lastBootstrap as { scene?: { worldObjects?: Array<Record<string, unknown>> } }).scene?.worldObjects?.find(
    (o) => o.objectId === worldObjectId,
  );
  expect(procWorld, "bootstrap carries the ice pick world object").toBeDefined();
  expect(String(procWorld!.assetId), "the ice pick is a generated proc.* asset").toMatch(/^proc\..+/);
  expect(procWorld!.generated, "the bootstrap embeds the compiled generated definition").not.toBeNull();
  const sceneObjectsText = await page.getByTestId("scene-objects").innerText();
  expect(sceneObjectsText, "the ice pick object is listed in the room").toContain("bronze_ceremonial_ice_pick");
  await page.screenshot({ path: evidence("phase17cd-hermes-office-scene.png"), fullPage: false });

  // (4) SERVER-authoritative direct interaction: click the object -> evidence
  //     panel for the deterministic locked-weapon evidence.
  await procButtons.first().click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("discovered-entry-d_ev_weapon_true")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("discovered-summary")).toContainText("Forensic comparison");
  await page.screenshot({ path: evidence("phase17cd-hermes-icepick-panel.png") });
  await page.keyboard.press("Escape");
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(page.getByTestId("discovered-entry-d_ev_weapon_true")).toBeVisible({ timeout: 20_000 });

  // (5) RELOAD -> the published world persists byte-identical.
  const bootstraps = () => net.captured.filter((c) => c.url.includes("/investigation")).map((c) => c.body);
  const snapshots = bootstraps();
  const snapshotBefore = procSnapshot(snapshots[snapshots.length - 1]);
  expect(snapshotBefore, "bootstrap before reload carries the proc.* definition").not.toBeNull();

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 60_000 });
  const snapshotsAfter = bootstraps();
  const snapshotAfter = procSnapshot(snapshotsAfter[snapshotsAfter.length - 1]);
  expect(snapshotAfter, "bootstrap after reload carries the proc.* definition").not.toBeNull();
  expect(snapshotAfter, "reload returns a byte-identical proc.* world object").toEqual(snapshotBefore);
  await expect(page.getByTestId("discovered-entry-d_ev_weapon_true")).toBeVisible({ timeout: 20_000 });

  // (6) Accuse -> accepted -> reveal 4/4 (the solver's unique winners).
  await page.getByTestId("accusation-open").click();
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
  for (const id of [SOLVER_IDS.who, SOLVER_IDS.why, SOLVER_IDS.weapon]) {
    const name = id === SOLVER_IDS.who ? "murdererId" : id === SOLVER_IDS.why ? "motiveId" : "weaponId";
    const ids = await page.locator(`input[name="${name}"]`).evaluateAll((inputs) =>
      (inputs as HTMLInputElement[]).map((i) => i.value));
    expect(ids, `candidate universe contains the locked ${id}`).toContain(id);
  }
  await page.getByTestId(`accusation-option-murdererId-${SOLVER_IDS.who}`).check();
  await page.getByTestId(`accusation-option-motiveId-${SOLVER_IDS.why}`).check();
  await page.getByTestId(`accusation-option-weaponId-${SOLVER_IDS.weapon}`).check();
  await page.getByTestId("accusation-time").fill(SOLVER_IDS.when);
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("accusation-summary-murderer")).toContainText("Paul");
  await expect(page.getByTestId("accusation-summary-motive")).toContainText(/research data/i);
  // Generated assets get the deterministic player-safe label from the public
  // assetId (reveal.weapon_label_of) — here the proc.* id itself.
  await expect(page.getByTestId("accusation-summary-weapon")).toContainText(/proc\.decor\./i);
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: evidence("phase17cd-hermes-accusation.png") });

  // (7) Pre-reveal leak scan (0 forbidden key paths over every captured body).
  const preRevealLeaks: Array<{ url: string; paths: string[] }> = [];
  let preRevealScanned = 0;
  for (const entry of net.captured) {
    if (entry.url.includes("generation-capabilities")) continue;
    if (entry.body === null || typeof entry.body !== "object") continue;
    preRevealScanned += 1;
    const paths = scanJsonBody(entry.body).filter((p) => !p.startsWith("$.accusation."));
    if (paths.length > 0) preRevealLeaks.push({ url: entry.url, paths });
  }
  const preRevealDom = (await page.locator("body").innerText()).toLowerCase();
  for (const token of ["murderer:", "truth", "correct", "winner", "solution"]) {
    expect(preRevealDom, `pre-reveal DOM must not reveal ${token}`).not.toContain(token);
  }
  expect(preRevealLeaks, "0 forbidden key paths in all pre-reveal API responses").toEqual([]);
  expect(preRevealScanned, "pre-reveal responses must have been scanned").toBeGreaterThan(0);

  // (8) REVEAL: CASE SOLVED 4/4.
  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-truth-murderer")).toContainText("Paul");
  await expect(page.getByTestId("reveal-truth-motive")).toContainText(/research data/i);
  await expect(page.getByTestId("reveal-truth-weapon")).toContainText(/proc\.decor\./i);
  await expect(page.getByTestId("reveal-truth-time")).toContainText(/23:\d\d/);
  for (const dim of ["who", "why", "weapon", "when"]) {
    await expect(page.getByTestId(`reveal-dimension-${dim}`)).toContainText("Correct");
  }
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await page.screenshot({ path: evidence("phase17cd-hermes-reveal-4of4.png"), fullPage: true });

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await page.screenshot({ path: evidence("phase17cd-hermes-reveal-reload.png"), fullPage: true });

  // (9) FULL no-host scan: DOM + every API body (incl. the reveal DTO).
  const domText = await page.locator("body").innerText();
  expect(hostHitsInBlob(domText), "DOM must be free of host/URL material").toEqual([]);
  const hostLeaks: Array<{ url: string; tokens: string[] }> = [];
  for (const entry of net.captured) {
    const hits = hostHitsInBlob(JSON.stringify(entry.body ?? {}));
    if (hits.length > 0) hostLeaks.push({ url: entry.url, tokens: hits });
  }
  const caseHits = hostHitsInBlob(JSON.stringify(created));
  if (caseHits.length > 0) hostLeaks.push({ url: "POST /api/v1/cases (201)", tokens: caseHits });
  expect(net.captured.some((c) => c.url.includes("/reveal")), "a reveal response was captured").toBe(true);
  expect(hostLeaks, "NO Ollama host/IP/port/URL in ANY DOM text or API DTO").toEqual([]);

  // (10) Session health.
  test.info().attach("phase17cd-remainder-transcript.json", {
    body: JSON.stringify(
      { worldObjectId, solverIds: SOLVER_IDS, captured: net.captured.map((c) => c.url) },
      null,
      2,
    ),
    contentType: "application/json",
  });
  expect(net.pageErrors, "no uncaught page errors").toEqual([]);
  expect(net.consoleErrors, "no console errors").toEqual([]);
  expect(net.failed, "no failed resources").toEqual([]);
  expect(net.external, "no external network traffic").toEqual([]);
  const leakOthers = leak.matched.filter((m) => !m.url.includes("generation-capabilities") && !m.url.includes("/reveal"));
  const leakNonEcho = leakOthers
    .map((m) => ({ url: m.url, paths: m.paths.filter((p) => !p.startsWith("$.accusation.") && !p.startsWith("$.player.accusation.")) }))
    .filter((m) => m.paths.length > 0);
  expect(leakNonEcho, "every pre-reveal API response (non-reveal, non-echo) carries zero forbidden key paths").toEqual([]);
  expect(leak.scanned, "leak listener scanned API responses").toBeGreaterThan(0);
  console.log(
    "PHASE17CD_REMAINDER_TRANSCRIPT",
JSON.stringify({
      model: EXPECTED_MODEL,
      environment: "office",
      worldObjectId,
      interactionEvidence: "d_ev_weapon_true",
      accuse: SOLVER_IDS,
      reveal: "4/4 CASE SOLVED",
      reloadByteIdentical: true,
      preRevealLeakScan: `${preRevealScanned} scanned / 0 matched`,
      hostScan: `0 leaks in ${net.captured.length} bodies + DOM`,
    }),
  );
});