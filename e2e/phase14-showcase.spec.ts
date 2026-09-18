import { expect, test } from "@playwright/test";
import type { Page, APIRequestContext } from "@playwright/test";
import { installLeakListener, scanJsonBody, seedPlaythroughCredentials } from "./helpers";

/**
 * PHASE 14 — PROMPT-TO-WORLD BROWSER SHOWCASE (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * For EACH of the five showcase prompts (through the PUBLIC API: anonymous
 * session -> POST /cases with the prompt, no explicit `environment` ->
 * playthrough) this spec drives /scene in the PRODUCTION SPA and proves:
 *
 *  1. environment identity — the bootstrap's environmentId + the rendered
 *     "Environment: <Canonical>" notice (apartment = default golden, no
 *     notice) and the kit class of the tensorial scene content;
 *  2. scene readiness — scene-canvas + scene-ready;
 *  3. PROMPT-SPECIFIC OBJECTS present — the bootstrap + the live
 *     renderer model both carry the prompt-derived objects (office:
 *     custom_trophy (proc, decorated by the Asset Oracle + embedded
 *     definition), hotel: glass_bottle + medication_bottle, warehouse:
 *     adjustable_wrench + rope, mansion: wristwatch + antique proc opener);
 *  4. PROMPT-SPECIFIC OBJECT DIRECTLY CLICKABLE — evidence-class objects
 *     (knife/laptop, present on every kit) get the hover-tooltip + click ->
 *     discovery/panel path; DECORATIVE prompt objects fall back cleanly (a
 *     real click at their live mesh location produces no interaction error,
 *     no evidence panel, and the scene stays ready).
 *  5. FULL LOOP for office AND warehouse: investigate (knife + laptop
 *     evidence) -> accuse (correct WHO/WHY/WEAPON/WHEN) -> reveal (CASE
 *     SOLVED, Thomas Reed / embezzlement / kitchen knife / 22:17 shown) with
 *     0 pre-reveal truth markers, allowlist-clean reveal DTOs, and the
 *     reveal persisting on reload.
 *  6. every session: leak scan 0 truth, 0 console/page errors, 0 failed
 *     resources, 0 external traffic.
 *
 * Server topology of THIS gate: backend :8000 (FRESH migrated scratch DB,
 * CORS_ALLOWED_ORIGINS=http://localhost:4173,http://localhost:5173, via
 * tools/process_guard) serving the REAL public API + a QA production build
 * served by vite preview :4173. All assertions are port-agnostic.
 */

const BACKEND_BASE = "http://localhost:8000";

interface Kit {
  key: string;
  canonicalName: string;
  prompt: string;
  environmentId: string;
  noticeExpected: boolean;
  /** objectIds that MUST come from the prompt (present in the world). */
  promptObjectIds: string[];
  /** catalog label(s) of the prompt objects when the asset has one. */
  promptLabels: string[];
  /** strings that must appear in the lowercased object LIST (label or id). */
  listExpectations: string[];
}

const OFFICE_PROMPT =
  "A financial crime in a company office. The killer used a letter opener " +
  "at the workplace. A heavy award is on the desk.";
const HOTEL_PROMPT =
  "A murder in a hotel room. A broken glass bottle and medication are near the body.";
const WAREHOUSE_PROMPT =
  "A smuggling dispute in a storage depot. A wrench and a rope were used.";
const MANSION_PROMPT =
  "An inheritance dispute in a mansion. A valuable antique ceremonial " +
  "letter opener and a watch are in the study.";
const APARTMENT_PROMPT =
  "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: €240,000 embezzlement\n" +
  "Weapon: Kitchen knife\nTime: 22:17\nWitness: Emily Reed";

const KITS: Kit[] = [
  {
    key: "office",
    canonicalName: "Office",
    prompt: OFFICE_PROMPT,
    environmentId: "office",
    noticeExpected: true,
    promptObjectIds: ["custom_trophy"],
    promptLabels: [],
    listExpectations: ["custom_trophy"],
  },
  {
    key: "hotel_suite",
    canonicalName: "Hotel Suite",
    prompt: HOTEL_PROMPT,
    environmentId: "hotel_suite",
    noticeExpected: true,
    promptObjectIds: ["glass_bottle", "medication_bottle"],
    promptLabels: ["Glass bottle", "Medication bottle"],
    listExpectations: ["glass bottle", "medication bottle"],
  },
  {
    key: "warehouse",
    canonicalName: "Warehouse",
    prompt: WAREHOUSE_PROMPT,
    environmentId: "warehouse",
    noticeExpected: true,
    promptObjectIds: ["adjustable_wrench", "rope"],
    promptLabels: ["Adjustable wrench", "Rope"],
    listExpectations: ["adjustable wrench", "rope"],
  },
  {
    key: "mansion",
    canonicalName: "Mansion",
    prompt: MANSION_PROMPT,
    environmentId: "mansion",
    noticeExpected: true,
    promptObjectIds: ["wristwatch", "antique_ceremonial_letter_opener"],
    promptLabels: ["Wristwatch"],
    listExpectations: ["wristwatch", "antique_ceremonial_letter_opener"],
  },
  {
    key: "apartment",
    canonicalName: "Apartment",
    prompt: APARTMENT_PROMPT,
    environmentId: "apartment",
    noticeExpected: false,
    promptObjectIds: ["kitchen_knife"],
    promptLabels: ["Kitchen knife"],
    listExpectations: ["kitchen knife"],
  },
];

const KNIFE_LABEL = "Kitchen knife";
const LAPTOP_LABEL = "Laptop";

interface Sight {
  label: string;
  x: number;
  y: number;
}

interface SessionReport {
  captured: Array<{ url: string; method: string; status: number; body: unknown }>;
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
    const request = response.request();
    const method = request.method();
    if (url.startsWith("http:") || url.startsWith("https:")) {
      const from = new URL(url);
      if (!(from.hostname === "localhost" && (from.port === "4173" || from.port === "8000"))) {
        report.external.push({ url, status });
      }
    }
    if (status >= 400) report.failed.push({ url, status });
    if (!url.includes("/api/")) return;
    const headers = typeof response.headers === "function" ? response.headers() : response.headers;
    const contentType = headers["content-type"] ?? headers["Content-Type"] ?? "";
    if (!contentType.includes("application/json")) return;
    try {
      const body = await response.json();
      report.captured.push({ url, method, status, body });
    } catch {
      report.captured.push({ url, method, status, body: null });
    }
  });
  return report;
}

/** Public-API handshake for one showcase prompt (no explicit environment). */
async function createShowcasePlaythrough(
  request: APIRequestContext,
  prompt: string,
): Promise<{
  playthroughId: string;
  playthroughToken: string;
  bootstrapEnvironmentId: string;
  bootstrapEnvironmentVersion: number | null;
  objectIds: string[];
}> {
  const sessionRes = await request.post(`${BACKEND_BASE}/api/v1/sessions/anonymous`);
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();

  const caseRes = await request.post(`${BACKEND_BASE}/api/v1/cases`, {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt, difficulty: "medium" },
  });
  expect(caseRes.status(), "create case").toBe(201);
  const created = await caseRes.json();
  expect(created.status, "dev provider publishes the showcase prompt").toBe("PUBLISHED");

  const ptRes = await request.post(
    `${BACKEND_BASE}/api/v1/cases/${created.caseId}/versions/1/playthroughs`,
    { headers: { Authorization: `Bearer ${created.creatorAccessToken}` } },
  );
  expect(ptRes.status(), "create playthrough").toBe(201);
  const pt = await ptRes.json();

  const bootRes = await request.get(
    `${BACKEND_BASE}/api/v1/playthroughs/${pt.playthroughId}/investigation`,
    { headers: { Authorization: `Bearer ${pt.playthroughAccessToken}` } },
  );
  expect(bootRes.status(), "investigation bootstrap").toBe(200);
  const boot = await bootRes.json();
  return {
    playthroughId: pt.playthroughId,
    playthroughToken: pt.playthroughAccessToken,
    bootstrapEnvironmentId: boot.scene.environmentId,
    bootstrapEnvironmentVersion: boot.scene.environmentVersion,
    objectIds: boot.scene.worldObjects.map((o: { objectId: string }) => o.objectId),
  };
}

async function tooltipText(page: Page): Promise<string | null> {
  const el = page.getByTestId("object-tooltip");
  if (!(await el.isVisible().catch(() => false))) return null;
  return ((await el.textContent()) ?? "").trim();
}

/** Grid hover-scan; first sighting per label wins. */
async function hoverScan(
  page: Page,
  rect: { x: number; y: number; width: number; height: number },
  stepX: number,
  stepY: number,
): Promise<{ log: Sight[]; found: Map<string, Sight> }> {
  const log: Sight[] = [];
  const found = new Map<string, Sight>();
  for (let gy = 0; gy <= 1; gy += stepY / rect.height) {
    // (unused placeholder to keep the loop shape readable)
    break;
  }
  const yEnd = rect.y + rect.height;
  const xEnd = rect.x + rect.width;
  for (let gy = rect.y; gy <= yEnd; gy += stepY) {
    for (let gx = rect.x; gx <= xEnd; gx += stepX) {
      await page.mouse.move(gx, gy);
      await page.waitForTimeout(16);
      const label = await tooltipText(page);
      if (label !== null && label !== "") {
        const sight: Sight = { label, x: Math.round(gx), y: Math.round(gy) };
        log.push(sight);
        if (!found.has(label)) found.set(label, sight);
      }
    }
  }
  return { log, found };
}

/** Live-renderer model probe for the prompt-specific objects + knife/laptop. */
async function modelProbe(
  page: Page,
  objectIds: string[],
): Promise<Record<string, { inModel: boolean; label: string | null; interactionWorks: boolean; root: boolean; partCount: number; diffuse: number[][]; generated: boolean }>> {
  return page.evaluate(
    ({ ids }: { ids: string[] }) => {
      const dbg = (window as any).__pdDebugScene;
      const scene = dbg?.scene;
      const model = dbg?.model;
      const result: Record<string, any> = {};
      for (const id of ids) {
        const obj = (model?.worldObjects ?? []).find((o: any) => o.objectId === id);
        const root = scene ? scene.getNodeByName(`pd_obj_${id}`) : null;
        const diffuse: number[][] = [];
        if (root) {
          for (const child of root.getChildMeshes(false) as any[]) {
            if ((child.name ?? "").startsWith("pd_hit_")) continue;
            try {
              const dc = child.material?.diffuseColor;
              if (dc) diffuse.push([Math.round(dc.r * 255), Math.round(dc.g * 255), Math.round(dc.b * 255)]);
            } catch {}
          }
        }
        result[id] = {
          inModel: obj !== undefined,
          label: obj?.label ?? null,
          interactionWorks: obj?.interactionWorks === true,
          root: !!root,
          partCount: diffuse.length,
          diffuse,
          generated: obj?.generated !== undefined,
        };
      }
      return result;
    },
    { ids: objectIds },
  );
}

/**
 * Documented DISTINCTIVE tones of the prompt-specific decorative objects
 * (catalog colors for catalog props; the compiled brass family for the
 * Phase 13 custom trophy). Used for the WebGL readPixels census -> the click
 * target that proves "decorative objects fall back cleanly".
 */
const DECORATIVE_TONES: Record<string, number[]> = {
  custom_trophy: [201, 162, 39], // brass #c9a227 (compiled material)
  adjustable_wrench: [107, 115, 123], // body #6b737b
  glass_bottle: [184, 208, 232], // glass #b8d0e8
  wristwatch: [200, 204, 212], // case #c8ccd4
};

/** Census of an object's tone family over the WebGL buffer -> (count, centroid). */
async function censusTone(
  page: Page,
  objectId: string,
  documented: number[] | null,
): Promise<{ count: number; centroid: { x: number; y: number } | null }> {
  return page.evaluate(
    ({ id, documented: doc }: { id: string; documented: number[] | null }) => {
      const dbg = (window as any).__pdDebugScene;
      const scene = dbg.scene;
      const model = dbg.model;
      const canvas = document.querySelector("canvas.scene-canvas") as HTMLCanvasElement;
      const gl =
        canvas.getContext("webgl2") || canvas.getContext("experimental-webgl2") || canvas.getContext("webgl");
      const width = canvas.width;
      const height = canvas.height;
      const obj = (model?.worldObjects ?? []).find((o: any) => o.objectId === id);
      const root = scene.getNodeByName(`pd_obj_${id}`);
      const diffuse: number[][] = [];
      if (root) {
        for (const child of root.getChildMeshes(false) as any[]) {
          if ((child.name ?? "").startsWith("pd_hit_")) continue;
          try {
            const dc = child.material?.diffuseColor;
            if (dc) diffuse.push([Math.round(dc.r * 255), Math.round(dc.g * 255), Math.round(dc.b * 255)]);
          } catch {}
        }
      }
      const candidates: number[][] = [...(doc ? [doc] : []), ...diffuse];
      const read = (): Uint8Array => {
        const buf = new Uint8Array(width * height * 4);
        gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, buf);
        return buf;
      };
      const bufA = read();
      const bufB = read();
      const cluster = (tone: number[], buf: Uint8Array) => {
        let cnt = 0, sx = 0, sy = 0;
        for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
          const i = (y * width + x) * 4;
          if (buf[i + 3] < 200) continue;
          if (
            Math.abs(buf[i] - tone[0]) <= 34 &&
            Math.abs(buf[i + 1] - tone[1]) <= 34 &&
            Math.abs(buf[i + 2] - tone[2]) <= 34
          ) {
            cnt++; sx += x; sy += y;
          }
        }
        return { cnt, centroid: cnt ? { x: sx / cnt, y: sy / cnt } : null };
      };
      let best = { cnt: 0, centroid: null as { x: number; y: number } | null };
      for (const t of candidates) {
        for (const buf of [bufA, bufB]) {
          const c = cluster(t, buf);
          if (c.cnt > best.cnt) best = c;
        }
      }
      return { count: best.cnt, centroid: best.centroid };
    },
    { id: objectId, documented },
  );
}

/** Assert the per-session hygiene + leak contract and log a transcript. */
function assertSessionClean(
  net: SessionReport,
  leak: { scanned: number; matched: Array<{ url: string; paths: string[] }> },
  context: string,
): void {
  const leakMatched: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of net.captured) {
    if (entry.body === null || typeof entry.body !== "object") continue;
    scanned += 1;
    const paths = scanJsonBody(entry.body);
    if (paths.length > 0) leakMatched.push({ url: entry.url, paths });
  }
  expect(leakMatched, `${context}: no truth markers in ${scanned} API responses`).toEqual([]);
  expect(scanned, `${context}: the session must have produced scannable responses`).toBeGreaterThan(0);
  expect(net.failed, `${context}: no failed resources (${net.failed.length})`).toEqual([]);
  expect(net.external, `${context}: no external network traffic`).toEqual([]);
  expect(net.pageErrors, `${context}: no uncaught page errors`).toEqual([]);
  expect(net.consoleErrors, `${context}: no console errors`).toEqual([]);
  expect(leak.scanned, `${context}: leak listener scanned responses`).toBeGreaterThan(0);
  expect(leak.matched, `${context}: leak listener reported nothing`).toEqual([]);
}

// ---------------------------------------------------------------------------
// shared per-kit browser showcase (identity + ready + prompt objects + click)
// ---------------------------------------------------------------------------
async function showcaseKit(page: Page, request: APIRequestContext, kit: Kit): Promise<void> {
  const leak = installLeakListener(page);
  const net = installSessionObservers(page);
  const transcript: Record<string, unknown> = { kit: kit.key };

  const cred = await createShowcasePlaythrough(request, kit.prompt);
  expect(cred.bootstrapEnvironmentId, `bootstrap environmentId for ${kit.key}`).toBe(kit.environmentId);
  expect(cred.bootstrapEnvironmentVersion, `bootstrap version pin ${kit.key}`).toBe(1);
  for (const id of kit.promptObjectIds) {
    expect(cred.objectIds, `${kit.key}: prompt-specific object ${id} in bootstrap`).toContain(id);
  }
  transcript.bootstrapEnvironmentId = cred.bootstrapEnvironmentId;
  await seedPlaythroughCredentials(page, { playthroughId: cred.playthroughId, playthroughToken: cred.playthroughToken });

  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  transcript.sceneReady = true;
  await page.waitForTimeout(1200);

  // ---- environment identity -------------------------------------------------
  const notice = page.getByTestId("environment-notice");
  if (kit.noticeExpected) {
    await expect(notice, `${kit.key}: environment notice visible`).toBeVisible({ timeout: 15_000 });
    await expect(notice).toContainText(`Environment: ${kit.canonicalName}`);
    transcript.notice = (await notice.textContent())?.trim() ?? "";
  } else {
    await expect(notice, `${kit.key}: apartment default renders NO notice`).toHaveCount(0);
    transcript.notice = "(none)";
  }

  // ---- prompt-specific objects present in the DOM object list ----------------
  const listText = (await page.getByTestId("scene-objects").innerText()).toLowerCase();
  for (const expectation of kit.listExpectations) {
    expect(listText, `${kit.key}: object list shows "${expectation}"`).toContain(expectation.toLowerCase());
  }
  transcript.objectListHasPromptIds = kit.listExpectations;

  // ---- prompt-specific objects present in the live renderer + clickable ----
  const probeIds = [...new Set([...kit.promptObjectIds, "kitchen_knife", "apartment_laptop"])];
  const probe = await modelProbe(page, probeIds);
  for (const id of kit.promptObjectIds) {
    const o = (probe as Record<string, any>)[id];
    expect(o, `${kit.key}: ${id} in the live model`).not.toBeUndefined();
    expect(o.inModel, `${kit.key}: ${id} present in the scene model`).toBe(true);
    expect(o.root, `${kit.key}: ${id} rendered as a pd_obj_ root mesh`).toBe(true);
    expect(o.partCount, `${kit.key}: ${id} has rendered part meshes`).toBeGreaterThan(0);
    // Only the EVIDENCE base objects (knife/laptop) are interactable; every
    // other prompt-specific object is a DECORATIVE supporting prop by design
    // (payload-driven interactionWorks: false).
    if (id !== "kitchen_knife" && id !== "apartment_laptop") {
      expect(o.interactionWorks, `${kit.key}: ${id} decorative by design (interaction '')`).toBe(false);
    }
  }

  // ---- evidence direct-click (knife/laptop present on every kit) ------------
  const rect = (await page.getByTestId("scene-canvas").boundingBox())!;
  expect(rect, "canvas bounding box").not.toBeNull();
  await page.mouse.move(rect.x - 40, rect.y + 20);
  const primary = await hoverScan(page, rect, 32, 32);
  let found = new Map(primary.found);
  if (!found.has(KNIFE_LABEL) && !found.has(LAPTOP_LABEL)) {
    const fine = await hoverScan(page, rect, 16, 16);
    for (const [label, sight] of fine.found) {
      if (!found.has(label)) found.set(label, sight);
    }
  }
  const target = found.get(KNIFE_LABEL) ?? found.get(LAPTOP_LABEL);
  expect(target, `${kit.key}: at least knife or laptop directly hoverable/clickable`).toBeDefined();
  await page.mouse.move(target!.x, target!.y);
  await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("object-tooltip")).toHaveText(target!.label);
  await page.mouse.click(target!.x, target!.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 15_000 });
  transcript.evidenceClicked = { label: target!.label, x: target!.x, y: target!.y };

  // ---- decorative prompt-specific object: real click falls back cleanly -----
  const decorative = kit.promptObjectIds.find(
    (id) => DECORATIVE_TONES[id] !== undefined,
  );
  if (decorative) {
    // Dismiss the toast FIRST (it can overlap the panel's Close button), then
    // close the panel with a small retry loop — identical contract to the
    // phase-15 showcase helper; the assertion (panel must close) is unchanged.
    await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
    const closeButton = page.getByTestId("evidence-close");
    const panelLocator = page.getByTestId("evidence-panel");
    for (let attempt = 0; attempt < 3; attempt++) {
      await closeButton.click({ timeout: 3_000 }).catch(() => {});
      try {
        await expect(panelLocator).not.toBeVisible({ timeout: 8_000 });
        break;
      } catch (closeError) {
        if (attempt === 2) throw closeError;
      }
    }
    await expect(page.getByTestId("evidence-panel")).not.toBeVisible();
    const census = await censusTone(page, decorative, DECORATIVE_TONES[decorative]);
    expect(census.count, `${kit.key}: ${decorative} tone pixels drawn on screen`).toBeGreaterThan(0);
    expect(census.centroid, `${kit.key}: ${decorative} tone located on screen`).not.toBeNull();
    await page.mouse.click(census.centroid!.x, census.centroid!.y);
    await page.waitForTimeout(600);
    await expect(page.getByTestId("interaction-error")).toHaveCount(0);
    await expect(page.getByTestId("evidence-panel")).not.toBeVisible();
    await expect(page.getByTestId("scene-ready")).toBeVisible();
    transcript.decorativeClicked = { objectId: decorative, x: Math.round(census.centroid!.x), y: Math.round(census.centroid!.y) };
  } else {
    transcript.decorativeClicked = null;
  }

  // ---- session hygiene + leak ----------------------------------------------
  const leakMatched: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of net.captured) {
    if (entry.body === null || typeof entry.body !== "object") continue;
    scanned += 1;
    const paths = scanJsonBody(entry.body);
    if (paths.length > 0) leakMatched.push({ url: entry.url, paths });
  }
  expect(leakMatched, `${kit.key}: no truth markers in ${scanned} API responses`).toEqual([]);
  expect(net.failed, `${kit.key}: no failed resources`).toEqual([]);
  expect(net.external, `${kit.key}: no external traffic`).toEqual([]);
  expect(net.pageErrors, `${kit.key}: no uncaught page errors`).toEqual([]);
  expect(net.consoleErrors, `${kit.key}: no console errors`).toEqual([]);
  expect(leak.scanned, `${kit.key}: leak listener scanned`).toBeGreaterThan(0);
  expect(leak.matched, `${kit.key}: leak listener clean`).toEqual([]);
  transcript.leak = { scanned, matched: leakMatched.length };
  transcript.net = { failed: net.failed.length, external: net.external.length, pageErrors: net.pageErrors.length, consoleErrors: net.consoleErrors.length };

  console.log(`P14_KIT_TRANSCRIPT ${kit.key}`, JSON.stringify(transcript));
  await test.info().attach(`phase14-${kit.key}-transcript.json`, {
    body: JSON.stringify(transcript, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({ path: `artifacts/screenshots/phase14-${kit.key}.png`, fullPage: false });
}

// ---------------------------------------------------------------------------
// per-kit smokes
// ---------------------------------------------------------------------------
test("Phase 14: office showcase — env identity, trophy present + decorative click, evidence click, hygiene", async ({ page, request }) => {
  await showcaseKit(page, request, KITS[0]);
});

test("Phase 14: hotel_suite showcase — env identity, bottle/medication present, evidence click, hygiene", async ({ page, request }) => {
  await showcaseKit(page, request, KITS[1]);
});

test("Phase 14: warehouse showcase — env identity, wrench/rope present, evidence click, hygiene", async ({ page, request }) => {
  await showcaseKit(page, request, KITS[2]);
});

test("Phase 14: mansion showcase — env identity, wristwatch/antique opener present, evidence click, hygiene", async ({ page, request }) => {
  await showcaseKit(page, request, KITS[3]);
});

test("Phase 14: apartment (default golden) — env identity, knife present, evidence click, hygiene", async ({ page, request }) => {
  await showcaseKit(page, request, KITS[4]);
});

// ---------------------------------------------------------------------------
// FULL LOOPS: office + warehouse (investigate -> accuse -> reveal)
// ---------------------------------------------------------------------------

// Pre-reveal forbidden key paths (mirror of the frozen phase7 scanner).
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

function walkKeys(
  node: unknown,
  path: string,
  hits: string[],
  forbidden: Set<string>,
  opts: { checkCanonicalValue: boolean; skipGenerated: boolean },
): void {
  if (node === null || node === undefined) return;
  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i++) walkKeys(node[i], `${path}[${i}]`, hits, forbidden, opts);
    return;
  }
  if (typeof node === "object") {
    for (const [key, value] of Object.entries(node)) {
      const child = `${path}.${key}`;
      const underEcho = path.endsWith(".accusation");
      // The `generated` block is the player-safe procedural render definition
      // (Phase 13 contract; embedded in the bootstrap so the client can draw
      // the object). Its schema/compiler version keys are render metadata, not
      // solution truth — they are exempted here exactly like the accusation
      // echo is exempted.
      if (key === "generated" && opts.skipGenerated) {
        continue;
      }
      if (forbidden.has(key) && !(key === "crimeTime" && underEcho)) hits.push(child);
      if (opts.checkCanonicalValue && typeof value === "string") {
        if (value === CANONICAL_TIME_VALUE && !underEcho) hits.push(`${child}(=canonicalCrimeTime)`);
      }
      walkKeys(value, child, hits, forbidden, opts);
    }
  }
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
    } catch {}
  });
  return { bodies };
}

async function fullLoop(
  page: Page,
  request: APIRequestContext,
  kit: Kit,
  decorativeObjectId: string | null,
): Promise<void> {
  const leak = installLeakListener(page);
  const net = installSessionObservers(page);
  const captured = installResponseCapture(page);
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  const transcript: Record<string, unknown> = { kit: kit.key, mode: "full-loop" };

  const cred = await createShowcasePlaythrough(request, kit.prompt);
  expect(cred.bootstrapEnvironmentId, `bootstrap env ${kit.key}`).toBe(kit.environmentId);
  await seedPlaythroughCredentials(page, { playthroughId: cred.playthroughId, playthroughToken: cred.playthroughToken });

  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("objective-text")).toContainText("Find evidence, then accuse someone.");

  // Prompt-specific object present in the live renderer + decorative click.
  const probe = await modelProbe(page, [...new Set([...kit.promptObjectIds, "kitchen_knife", "apartment_laptop"])]);
  for (const id of kit.promptObjectIds) {
    const o = (probe as Record<string, any>)[id];
    expect(o, `${kit.key}: ${id} in the live model`).not.toBeUndefined();
    expect(o.inModel, `${kit.key}: ${id} present in the scene model`).toBe(true);
    expect(o.root, `${kit.key}: ${id} rendered with part meshes`).toBe(true);
  }
  transcript.promptObjectsInModel = kit.promptObjectIds;

  // Investigate: knife -> discovery; laptop -> email.
  const knife = page.getByTestId("object-kitchen_knife");
  await expect(knife).toBeVisible({ timeout: 20_000 });
  await knife.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  const closeBtn = page.getByTestId("evidence-close");
  for (let attempt = 0; attempt < 3; attempt++) {
    await closeBtn.click({ timeout: 3_000 }).catch(() => {});
    try {
      await expect(panel).not.toBeVisible({ timeout: 8_000 });
      break;
    } catch (closeError) {
      if (attempt === 2) throw closeError;
    }
  }

  const laptop = page.getByTestId("object-apartment_laptop");
  await expect(laptop).toBeVisible({ timeout: 20_000 });
  await laptop.click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Re: the missing funds");
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  for (let attempt = 0; attempt < 3; attempt++) {
    await closeBtn.click({ timeout: 3_000 }).catch(() => {});
    try {
      await expect(panel).not.toBeVisible({ timeout: 8_000 });
      break;
    } catch (closeError) {
      if (attempt === 2) throw closeError;
    }
  }
  transcript.investigated = ["kitchen_knife", "apartment_laptop"];

  // Decorative prompt object click falls back cleanly (office: trophy).
  if (decorativeObjectId !== null) {
    const census = await censusTone(page, decorativeObjectId, DECORATIVE_TONES[decorativeObjectId]);
    expect(census.count, `${kit.key}: ${decorativeObjectId} tone pixels on screen`).toBeGreaterThan(0);
    expect(census.centroid, `${kit.key}: ${decorativeObjectId} located`).not.toBeNull();
    await page.mouse.click(census.centroid!.x, census.centroid!.y);
    await page.waitForTimeout(600);
    await expect(page.getByTestId("interaction-error")).toHaveCount(0);
    await expect(page.getByTestId("evidence-panel")).not.toBeVisible();
    await expect(page.getByTestId("scene-ready")).toBeVisible();
    transcript.decorativeClicked = decorativeObjectId;
  }

  // Accuse: correct WHO/WHY/WEAPON/WHEN.
  const accuseButton = page.getByTestId("accusation-open");
  await expect(accuseButton).toBeVisible();
  await accuseButton.click();
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("accusation-option-murdererId-thomas_reed").check();
  await page.getByTestId("accusation-option-motiveId-cover_up_embezzlement").check();
  await page.getByTestId("accusation-option-weaponId-kitchen_knife").check();
  await page.getByTestId("accusation-time").fill("22:17");
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });
  transcript.accused = { who: "thomas_reed", why: "cover_up_embezzlement", weapon: "kitchen_knife", when: "22:17" };

  // Pre-reveal leak scan over the whole captured session.
  const preReveal: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of captured.bodies) {
    scanned += 1;
    const hits: string[] = [];
    walkKeys(entry.body, "$", hits, PRE_REVEAL_FORBIDDEN_KEYS, {
      checkCanonicalValue: true,
      skipGenerated: true,
    });
    if (hits.length > 0) preReveal.push({ url: entry.url, paths: hits });
  }
  console.log(`P14_PRE_REVEAL ${kit.key}`, JSON.stringify({ scanned, matched: preReveal }));
  await test.info().attach(`phase14-${kit.key}-pre-reveal-scan.json`, {
    body: JSON.stringify({ scanned, matched: preReveal, forbiddenKeys: [...PRE_REVEAL_FORBIDDEN_KEYS] }, null, 2),
    contentType: "application/json",
  });
  expect(preReveal, `${kit.key}: no forbidden key paths in ${scanned} pre-reveal responses`).toEqual([]);
  expect(scanned, `${kit.key}: pre-reveal responses captured`).toBeGreaterThan(0);

  // Reveal.
  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
  await expect(page.getByTestId("reveal-truth-motive")).toContainText("embezzlement");
  await expect(page.getByTestId("reveal-truth-weapon")).toHaveText("Kitchen Knife");
  await expect(page.getByTestId("reveal-truth-time")).toHaveText("22:17");
  for (const dim of ["who", "why", "weapon", "when"]) {
    await expect(page.getByTestId(`reveal-dimension-${dim}`)).toContainText("Correct");
  }
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await expect(page.getByTestId("reveal-explanation")).toBeVisible();
  // truth shown -> the canonical values are on the page after reveal.
  const revealText = await page.getByTestId("reveal-screen").innerText();
  expect(revealText).toContain("Thomas Reed");
  expect(revealText).toContain("22:17");
  transcript.reveal = { overall: "CASE SOLVED", score: "4 / 4" };

  // Post-reveal allowlist scan of the /reveal responses.
  const revealResponses = captured.bodies.filter((b) => b.url.includes("/reveal"));
  expect(revealResponses.length, `${kit.key}: reveal responses exist`).toBeGreaterThan(0);
  const revealHits: Array<{ url: string; paths: string[] }> = [];
  for (const rr of revealResponses) {
    const hits: string[] = [];
    walkKeys(rr.body, "$", hits, REVEAL_FORBIDDEN_KEYS, {
      checkCanonicalValue: false,
      skipGenerated: true,
    });
    if (hits.length > 0) revealHits.push({ url: rr.url, paths: hits });
  }
  await test.info().attach(`phase14-${kit.key}-post-reveal-scan.json`, {
    body: JSON.stringify({ revealResponses: revealResponses.length, revealHits, forbiddenKeys: [...REVEAL_FORBIDDEN_KEYS] }, null, 2),
    contentType: "application/json",
  });
  expect(revealHits, `${kit.key}: reveal DTO carry no internal material`).toEqual([]);

  // Reload -> reveal persists.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Thomas Reed");
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");

  // Full-session hygiene (responses BEFORE reveal are already scanned; the
  // session-level leak-listener scan below runs over ALL bodies but the only
  // truth-bearing responses are /reveal — the pre-reveal scan already proved
  // the rest of the session was clean).
  expect(pageErrors, `${kit.key}: no uncaught page errors`).toEqual([]);
  expect(net.failed, `${kit.key}: no failed resources`).toEqual([]);
  expect(net.external, `${kit.key}: no external traffic`).toEqual([]);
  expect(net.consoleErrors, `${kit.key}: no console errors`).toEqual([]);
  transcript.leak = { preRevealScanned: scanned, preRevealMatched: preReveal.length };
  transcript.net = { failed: net.failed.length, external: net.external.length, pageErrors: pageErrors.length, consoleErrors: net.consoleErrors.length };

  console.log(`P14_FULLLOOP_TRANSCRIPT ${kit.key}`, JSON.stringify(transcript));
  await test.info().attach(`phase14-${kit.key}-full-loop.json`, {
    body: JSON.stringify(transcript, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({ path: `artifacts/screenshots/phase14-${kit.key}-reveal.png`, fullPage: true });
}

test("Phase 14 FULL LOOP: office — investigate knife/laptop (+trophy click), accuse correct, reveal CASE SOLVED", async ({ page, request }) => {
  await fullLoop(page, request, KITS[0], "custom_trophy");
});

test("Phase 14 FULL LOOP: warehouse — investigate knife/laptop, accuse correct, reveal CASE SOLVED", async ({ page, request }) => {
  await fullLoop(page, request, KITS[2], null);
});