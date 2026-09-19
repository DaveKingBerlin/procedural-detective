import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installLeakListener } from "./helpers";

/**
 * PHASE 17 — GEOMETRY-VALIDATED REPAIRED OBJECT BROWSER E2E (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Phase17 §15: at least one REPAIRED local-model-generated critical object must
 * be demonstrated in the real browser. This spec drives the FULL chain through
 * the REAL OllamaStageDriver + the REAL Phase 17 geometry gate over the QA
 * fake-Ollama seam (e2e/qa-phase17-fake-ollama.py on 127.0.0.1:11497 — an
 * allowed loopback port):
 *
 *   1. the first ASSET_SPEC request returns a SCHEMA-VALID but
 *      GEOMETRICALLY-INVALID 2.5 m ice pick (inside the 0.05..4 absolute bound,
 *      so the Phase 13 schema/security validator PASSES it) -> the Phase 17
 *      Geometry Quality Validator rejects DECLARED_DIMENSIONS_IMPLAUSIBLE;
 *   2. the driver issues one ASSET_SPEC_REPAIR (bounded) and re-runs BOTH
 *      validations;
 *   3. the repaired spec compiles to a proc.* asset, the world publishes under
 *      the OFFICE environment with data-level clickability (interaction
 *      "inspect" + the forensic ice-pick evidence link);
 *   4. in the real browser: the repaired object RENDERS visibly (pd_obj_ root,
 *      part meshes, WebGL tone census far from neutral-fallback gray);
 *   5. its silhouette is DISTINGUISHABLE from the nearby critical evidence
 *      (the kitchen-knife blade steel tone vs the ice-pick brass tone — two
 *      distinct pixel families with distinct centroids on screen);
 *   6. HOVER works (hover ring + pointer cursor on the repaired object's
 *      projected hitbox — proc.* objects keep the DOM tooltip null by design,
 *      so the ring/cursor IS the hover affordance; the label-bearing golden
 *      knife on the SAME scene additionally proves the DOM tooltip path);
 *   7. DIRECT CLICK on the repaired object opens the evidence panel with the
 *      forensic ice-pick fact (tooltip->click->evidence for the knife, and
 *      mesh-pick->evidence for the repaired object);
 *   8. accuse/reveal succeeds (paul_becker / stolen_research_data /
 *      bronze_ceremonial_ice_pick / 23:42 -> CASE SOLVED 4/4);
 *   9. RELOAD preserves the identical published asset (same content-addressed
 *      proc.* assetId + byte-identical generated definition);
 *  10. hygiene: 0 forbidden pre-reveal key paths, 0 console/page errors, 0
 *      failed resources, 0 external traffic, 0 host/IP/port in the DOM.
 *
 * The QA fake-Ollama server answers the REAL /api/tags + /api/chat endpoints
 * with the canonical non-golden showcase stage chain (Anna Weiss / Paul Becker
 * / bronze ceremonial ice pick / office) so the REAL probe + REAL driver work
 * end-to-end over real HTTP with zero product changes (the standing QA seam
 * from Phase16/16_2).
 */
const SHOWCASE_PROMPT =
  "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n" +
  "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa König\nLocation: office\n";

const FAKE_OLLAMA_PORT = "11497";
const FORBIDDEN_DOM_TOKENS = ["11434", FAKE_OLLAMA_PORT, "127.0.0.1", "host.docker.internal", "http://localhost:8000"];

const PROC_OBJECT_ID = "bronze_ceremonial_ice_pick";
const PROC_EVIDENCE_ID = "forensic_icepick_match_01";

/** Compiled material tones of the REPAIRED ice pick (metal.brass shaft/point +
 * wood.dark handle) vs the KNIFE blade steel (#c8ccd4) — the distinguishable
 * pixel signatures required by §15.7. */
const ICE_PICK_BRASS = [201, 162, 39]; // metal.brass
const KNIFE_BLADE = [200, 204, 212]; // #c8ccd4 blade
const FALLBACK_GRAY = [141, 141, 147]; // neutral fallback

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

async function expectNoHostInDom(page: Page): Promise<void> {
  const text = await page.evaluate(() => document.body?.innerText ?? "");
  for (const token of FORBIDDEN_DOM_TOKENS) {
    expect(text, `DOM must not contain ${token}`).not.toContain(token);
  }
}

/** Live-renderer model probe: per-object presence/geometry/affordance. */
async function modelProbe(
  page: Page,
  objectIds: string[],
): Promise<Record<string, { inModel: boolean; label: string | null; interactionWorks: boolean; root: boolean; partCount: number; diffuse: number[][]; generated: boolean; unknownAsset: boolean }>> {
  return page.evaluate(({ ids }: { ids: string[] }) => {
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
        unknownAsset: obj?.unknownAsset === true,
      };
    }
    return result;
  }, { ids: objectIds });
}

/** Snapshot of the proc object's identity + full generated block from the live
 * renderer model (used for the reload byte-persistence comparison). */
async function procSnapshot(page: Page, objectId: string): Promise<{ assetId: string; generated: unknown } | null> {
  return page.evaluate(({ id }: { id: string }) => {
    const dbg = (window as any).__pdDebugScene;
    const model = dbg?.model;
    const obj = (model?.worldObjects ?? []).find((o: any) => o.objectId === id);
    if (!obj) return null;
    return { assetId: obj.assetId as string, generated: obj.generated ?? null };
  }, { id: objectId });
}

/** Census of one tone family over the WebGL buffer -> internal-canvas pixel
 * count + centroid (TOP-DOWN coordinates). */
async function censusTone(page: Page, tone: number[]): Promise<{ count: number; centroid: { x: number; y: number } | null }> {
  return page.evaluate(({ tone: t }: { tone: number[] }) => {
    const canvas = document.querySelector("canvas.scene-canvas") as HTMLCanvasElement;
    const gl =
      canvas.getContext("webgl2") || canvas.getContext("experimental-webgl2") || canvas.getContext("webgl");
    const width = canvas.width;
    const height = canvas.height;
    const bufA = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, bufA);
    const bufB = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, bufB);
    const cluster = (buf: Uint8Array) => {
      let cnt = 0, sx = 0, sy = 0;
      for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
        const i = (y * width + x) * 4;
        if (buf[i + 3] < 200) continue;
        if (
          Math.abs(buf[i] - t[0]) <= 24 &&
          Math.abs(buf[i + 1] - t[1]) <= 24 &&
          Math.abs(buf[i + 2] - t[2]) <= 24
        ) {
          cnt++; sx += x; sy += y;
        }
      }
      return { cnt, centroid: cnt ? { x: sx / cnt, y: height - 1 - sy / cnt } : null };
    };
    let best = { cnt: 0, centroid: null as { x: number; y: number } | null };
    for (const buf of [bufA, bufB]) {
      const c = cluster(buf);
      if (c.cnt > best.cnt) best = c;
    }
    return { count: best.cnt, centroid: best.centroid };
  }, { tone });
}

/** Projected pick-hitbox center + screen diagonal for a world object. */
async function projectedHitboxCenter(
  page: Page,
  objectId: string,
): Promise<{ x: number; y: number; diagonal: number } | null> {
  return page.evaluate(({ id }: { id: string }) => {
    const dbg = (window as any).__pdDebugScene;
    const scene = dbg?.scene;
    const camera = dbg?.camera;
    if (!scene || !camera) return null;
    const root = scene.getNodeByName(`pd_obj_${id}`);
    if (!root) return null;
    const meshes: any[] = root.getChildMeshes(false) ?? [];
    const hit = meshes.find((m) => (m.name ?? "").startsWith("pd_hit_"));
    if (!hit) return null;
    const bb = hit.getBoundingInfo().boundingBox;
    if (!bb) return null;
    const c = bb.centerWorld;
    const view = camera.getViewMatrix();
    const proj = camera.getProjectionMatrix();
    const transform = (w: number[], m: any): number[] => {
      const a = m.m;
      return [
        a[0] * w[0] + a[4] * w[1] + a[8] * w[2] + a[12] * w[3],
        a[1] * w[0] + a[5] * w[1] + a[9] * w[2] + a[13] * w[3],
        a[2] * w[0] + a[6] * w[1] + a[10] * w[2] + a[14] * w[3],
        a[3] * w[0] + a[7] * w[1] + a[11] * w[2] + a[15] * w[3],
      ];
    };
    const project = (wx: number, wy: number, wz: number): { x: number; y: number } | null => {
      let v = transform([wx, wy, wz, 1], view);
      v = transform(v, proj);
      if (Math.abs(v[3]) < 1e-9) return null;
      const ndcX = v[0] / v[3];
      const ndcY = v[1] / v[3];
      if (Math.abs(ndcX) > 1.5 || Math.abs(ndcY) > 1.5) return null;
      return { x: (ndcX + 1) * 0.5 * 800, y: (1 - ndcY) * 0.5 * 480 };
    };
    const center = project(c.x, c.y, c.z);
    const corner = project(c.x + bb.extendSizeWorld.x, c.y + bb.extendSizeWorld.y, c.z + bb.extendSizeWorld.z);
    if (!center || !corner) return null;
    const diagonal = Math.hypot(corner.x - center.x, corner.y - center.y) * 2;
    return { x: center.x, y: center.y, diagonal };
  }, { id: objectId });
}

/** Internal 800x480 canvas coordinate -> viewport coordinate (CSS-scaled). */
function toViewport(
  rect: { x: number; y: number; width: number; height: number },
  internal: { x: number; y: number },
): { x: number; y: number } {
  return {
    x: rect.x + (internal.x / 800) * rect.width,
    y: rect.y + (internal.y / 480) * rect.height,
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

// ---------------------------------------------------------------------------
// forbidden-key walkers (frozen mirrors of the phase145 scanners)
// ---------------------------------------------------------------------------
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

const CANONICAL_TIME_VALUE = "2026-09-11T23:42:00+02:00";

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
      if (key === "generated" && opts.skipGenerated) continue;
      if (forbidden.has(key) && !(key === "crimeTime" && underEcho)) hits.push(child);
      if (opts.checkCanonicalValue && typeof value === "string") {
        if (value === CANONICAL_TIME_VALUE && !underEcho) hits.push(`${child}(=canonicalCrimeTime)`);
      }
      walkKeys(value, child, hits, forbidden, opts);
    }
  }
}

test("Phase 17: geometry-gate-repaired proc.* object renders visibly, silhouette distinguishable, hover+direct-click, accuse->reveal, reload identical, leak-free", async ({
  page,
}) => {
  test.setTimeout(240_000);
  const leak = installLeakListener(page);
  const net = installSessionObservers(page);
  const transcript: Record<string, unknown> = { prompt: SHOWCASE_PROMPT };

  // ---- 0. capability: local AVAILABLE through the REAL probe over the QA seam
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const caps = await page.evaluate(async () => {
    const r = await fetch("http://localhost:8000/api/v1/generation-capabilities");
    return { status: r.status, body: await r.json() };
  });
  expect(caps.status).toBe(200);
  const localMode = (caps.body as { modes: Array<{ id: string; available: boolean }> }).modes.find(
    (m) => m.id === "local",
  );
  expect(localMode?.available, "local mode available (REAL probe over the QA seam)").toBe(true);

  // select Local AI so the /new showcase sentence + driver path are active
  const selector = page.getByTestId("generation-mode-selector");
  await expect(selector).toBeVisible({ timeout: 30_000 });
  const select = page.getByTestId("generation-mode-select");
  await select.selectOption("local");
  await page.waitForTimeout(200);

  // ---- 1. driver chain with the geometry-gate repair through the REAL UI -----
  await page.goto("/new", { waitUntil: "domcontentloaded" });
  const showcase = page.getByTestId("local-ai-showcase-note");
  await expect(showcase).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("prompt-input").fill(SHOWCASE_PROMPT);
  await page.getByTestId("generate-case").click();
  await expect(page).toHaveURL(/\/generating/, { timeout: 15_000 });
  await expect(page.getByTestId("enter-investigation")).toBeVisible({ timeout: 120_000 });
  await page.getByTestId("enter-investigation").click();
  await expect(page).toHaveURL(/\/scene/, { timeout: 30_000 });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-error")).toHaveCount(0);
  await page.waitForTimeout(1400);

  // the __pdDebugScene renderer probe is gated behind the DEF-056 diagnostic
  // query; reload with it enabled (credentials persist in localStorage, same
  // playthrough — the phase162 pattern).
  const hasDebugPre = await page.evaluate(() => Boolean((window as any).__pdDebugScene));
  if (!hasDebugPre) {
    await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("scene-error")).toHaveCount(0);
    await page.waitForTimeout(1200);
  }
  await expect(
    page.evaluate(() => Boolean((window as any).__pdDebugScene)),
    "debug scene handle available for the renderer probe",
  ).resolves.toBe(true);

  // the object list carries the repaired object (office gilt: the world is the
  // canonical Phase16_2 office chain).
  const notice = page.getByTestId("environment-notice");
  await expect(notice).toBeVisible({ timeout: 15_000 });
  await expect(notice).toContainText("Environment: Office");
  const listText = (await page.getByTestId("scene-objects").innerText()).toLowerCase();
  expect(listText, "repaired object present in the object list").toContain(PROC_OBJECT_ID);
  transcript.environment = "office";

  // ---- 2. the REPAIRED proc.* object renders visibly ----------------------
  const probe = await modelProbe(page, [PROC_OBJECT_ID, "kitchen_knife"]);
  const ice = (probe as Record<string, any>)[PROC_OBJECT_ID];
  expect(ice, "ice pick in the live model").not.toBeUndefined();
  expect(ice.inModel, "repaired ice pick present in the scene model").toBe(true);
  expect(ice.root, "repaired ice pick rendered as a pd_obj_ root mesh").toBe(true);
  expect(ice.partCount, "repaired ice pick has rendered part meshes").toBeGreaterThanOrEqual(3);
  expect(ice.generated, "repaired ice pick model carries the generated block").toBe(true);
  expect(ice.interactionWorks, "repaired ice pick directly interactable (interaction inspect)").toBe(true);
  expect(ice.unknownAsset, "repaired ice pick is NOT an unknown neutral asset").toBe(false);
  expect(ice.label, "generated objects keep label null (no server text in the DOM)").toBeNull();
  const notFallback = ice.diffuse.every(
    (c: number[]) => !(Math.abs(c[0] - FALLBACK_GRAY[0]) <= 6 && Math.abs(c[1] - FALLBACK_GRAY[1]) <= 6 && Math.abs(c[2] - FALLBACK_GRAY[2]) <= 6),
  );
  expect(notFallback, "0 fallback-gray parts for the repaired object").toBe(true);
  // capture the content-addressed assetId NOW (reveal derives the weapon label
  // from it deterministically: weapon_label_of capitalizes the proc.* id).
  const firstSnapshot = await procSnapshot(page, PROC_OBJECT_ID);
  expect(firstSnapshot, "proc snapshot for label derivation").not.toBeNull();
  const procAssetId = firstSnapshot!.assetId;
  expect(procAssetId, "assetId is a content-addressed proc.* id").toMatch(/^proc\.[a-z0-9_.-]+\.[a-f0-9]{16}$/);
  const procWeaponLabel = (() => {
    // deterministic mirror of backend weapon_label_of for a proc.* id:
    // no PROP_/DOOR_/FURN_/TECH_ prefix -> id split on "_", each word capitalized.
    const words = procAssetId.split("_").filter((w) => w.length > 0);
    return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ") || procAssetId;
  })();
  transcript.modelProbe = {
    root: ice.root,
    partCount: ice.partCount,
    interactionWorks: ice.interactionWorks,
    unknownAsset: ice.unknownAsset,
    diffuse: ice.diffuse,
    assetId: procAssetId,
    expectedWeaponLabel: procWeaponLabel,
  };

  // visible: the repaired brass family IS on screen (WebGL census)
  const brass = await censusTone(page, ICE_PICK_BRASS);
  expect(brass.count, "repaired object brass family drawn on screen").toBeGreaterThan(0);
  expect(brass.centroid, "repaired object located on screen").not.toBeNull();

  // ---- 3. silhouette DISTINGUISHABLE from the nearby critical evidence ------
  // The office kit's kitchen knife (blade steel #c8ccd4) is nearby critical
  // evidence. Two distinct tone families with distinct centroids = a
  // distinguishable silhouette (the ice-pick brass vs the knife blade steel).
  const knife = (probe as Record<string, any>).kitchen_knife;
  expect(knife?.inModel, "kitchen knife present in the scene model").toBe(true);
  const blade = await censusTone(page, KNIFE_BLADE);
  expect(blade.count, "knife blade steel family drawn on screen").toBeGreaterThan(0);
  expect(blade.centroid, "knife blade located on screen").not.toBeNull();
  const dx = Math.hypot(
    (brass.centroid!.x - blade.centroid!.x),
    (brass.centroid!.y - blade.centroid!.y),
  );
  expect(dx, "ice-pick brass and knife-blade steel centroids differ on screen (>24px)").toBeGreaterThan(24);
  transcript.silhouette = {
    brass: brass,
    knifeBlade: blade,
    centroidDistance: Math.round(dx),
    distinguishable: dx > 24,
  };
  await page.screenshot({ path: "artifacts/screenshots/phase17-repaired-object.png" });

  // ---- 4. HOVER works on the repaired object --------------------------------
  // proc.* objects keep the DOM tooltip null by design (canonicalName never a
  // label); the hover affordance is ring + pointer cursor over the projected
  // hitbox. The label-bearing knife on the same scene proves the DOM tooltip
  // path (tooltip -> click -> evidence below).
  const rect = (await page.getByTestId("scene-canvas").boundingBox())!;
  const pickTarget = await projectedHitboxCenter(page, PROC_OBJECT_ID);
  expect(pickTarget, "projected hitbox center of the repaired object").not.toBeNull();
  const hoverPt = toViewport(rect, { x: pickTarget!.x, y: pickTarget!.y });
  await page.mouse.move(hoverPt.x, hoverPt.y);
  await page.waitForTimeout(250);
  // hover ring visible + cursor = pointer on the canvas
  const hoverState = await page.evaluate(({ id }: { id: string }) => {
    const dbg = (window as any).__pdDebugScene;
    const scene = dbg?.scene;
    const canvas = document.querySelector("canvas.scene-canvas") as HTMLCanvasElement;
    const ring = scene ? scene.getNodeByName(`pd_ring_${id}`) : null;
    return {
      cursor: canvas.style.cursor,
      ringVisible: ring ? ring.isVisible : false,
    };
  }, { id: PROC_OBJECT_ID });
  expect(hoverState.cursor, "hover sets the pointer cursor on the repaired object").toBe("pointer");
  expect(hoverState.ringVisible, "hover ring visible on the repaired object").toBe(true);
  transcript.hover = { cursor: hoverState.cursor, ringVisible: hoverState.ringVisible, at: hoverPt };

  // ---- 5. DIRECT CLICK on the repaired object -> evidence panel -------------
  expect(pickTarget!.diagonal, "hitbox screen footprint").toBeGreaterThan(0);
  await page.mouse.click(hoverPt.x, hoverPt.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  const panelText = await panel.innerText();
  expect(panelText.toLowerCase(), "panel shows the forensic ice-pick fact (evidence id)").toContain(PROC_EVIDENCE_ID);
  transcript.directClick = {
    hitboxDiagonal: Math.round(pickTarget!.diagonal),
    viewport: { x: Math.round(hoverPt.x), y: Math.round(hoverPt.y) },
    panelSubstring: panelText.slice(0, 160),
  };
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await page.getByTestId("evidence-close").click().catch(() => {});
  await expect(panel).not.toBeVisible();
  expect(await page.getByTestId("interaction-error").count(), "0 interaction errors").toBe(0);

  // tooltip path proof: the kitchen knife (label-bearing registry asset) shows
  // the DOM tooltip and clicking it also opens an evidence panel.
  await page.mouse.move(rect.x - 40, rect.y + 20);
  const primary = await hoverScan(page, rect, 32, 32);
  let found = new Map(primary.found);
  if (!found.has("Kitchen knife")) {
    const fine = await hoverScan(page, rect, 16, 16);
    for (const [label, sight] of fine.found) {
      if (!found.has(label)) found.set(label, sight);
    }
  }
  const knifeSight = found.get("Kitchen knife");
  expect(knifeSight, "kitchen knife directly hoverable (DOM tooltip)").toBeDefined();
  await page.mouse.move(knifeSight!.x, knifeSight!.y);
  await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("object-tooltip")).toHaveText("Kitchen knife");
  await page.mouse.click(knifeSight!.x, knifeSight!.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await page.getByTestId("evidence-close").click().catch(() => {});
  await expect(panel).not.toBeVisible();
  transcript.tooltipClick = knifeSight;

  // ---- 6. accuse -> reveal -> CASE SOLVED -----------------------------------
  const accusationOpen = page.getByTestId("accusation-open");
  await expect(accusationOpen).toBeVisible();
  await accusationOpen.click();
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("accusation-option-murdererId-paul_becker").check();
  await page.getByTestId("accusation-option-motiveId-stolen_research_data").check();
  await page.getByTestId("accusation-option-weaponId-bronze_ceremonial_ice_pick").check();
  await page.getByTestId("accusation-time").fill("23:42");
  await page.getByTestId("accusation-submit").click();
  await expect(page.getByTestId("accusation-confirmation")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });
  transcript.accused = { who: "paul_becker", why: "stolen_research_data", weapon: PROC_OBJECT_ID, when: "23:42" };

  // pre-reveal leak scan over the whole captured session.
  // Carve-out: `generationAttemptId` is a DOCUMENTED PUBLIC field of the case-
  // creation response (schemas/cases.py) that the frontend requires for
  // generation progress polling — this spec drives the generation IN-PAGE (the
  // phase145 scanner seeded via the API context, so it never observed /cases).
  const preReveal: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of net.captured) {
    if (entry.body === null || typeof entry.body !== "object") continue;
    if (entry.url.includes("generation-capabilities")) continue; // public allowlist DTO
    scanned += 1;
    const hits: string[] = [];
    walkKeys(entry.body, "$", hits, PRE_REVEAL_FORBIDDEN_KEYS, {
      checkCanonicalValue: true,
      skipGenerated: true,
    });
    if (hits.length > 0) preReveal.push({ url: entry.url, paths: hits });
  }
  const preRevealDocumentedCarveout = preReveal.filter((m) =>
    !(m.url.endsWith("/api/v1/cases") && m.paths.every((p) => p === "$.generationAttemptId")),
  );
  console.log(`P17_PRE_REVEAL ${JSON.stringify({ scanned, matched: preReveal, carvedOutDocumented: preReveal.length - preRevealDocumentedCarveout.length })}`);
  await test.info().attach("phase17-pre-reveal-scan.json", {
    body: JSON.stringify({ scanned, matched: preReveal, carvedOutDocumented: preReveal.length - preRevealDocumentedCarveout.length, forbiddenKeys: [...PRE_REVEAL_FORBIDDEN_KEYS] }, null, 2),
    contentType: "application/json",
  });
  expect(preRevealDocumentedCarveout, `no forbidden key paths in ${scanned} pre-reveal responses (beyond the documented /cases generationAttemptId)`)
    .toEqual([]);
  expect(scanned, "pre-reveal responses captured").toBeGreaterThan(0);

  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-truth-murderer")).toHaveText("Paul Becker");
  await expect(page.getByTestId("reveal-truth-motive")).toContainText("research data");
  // the weapon truth label is derived deterministically from the proc.* assetId
  await expect(page.getByTestId("reveal-truth-weapon")).toHaveText(procWeaponLabel);
  await expect(page.getByTestId("reveal-truth-time")).toHaveText("23:42");
  for (const dim of ["who", "why", "weapon", "when"]) {
    await expect(page.getByTestId(`reveal-dimension-${dim}`)).toContainText("Correct");
  }
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  transcript.reveal = { overall: "CASE SOLVED", score: "4 / 4", weaponLabel: procWeaponLabel };

  // ---- 7. RELOAD preserves the identical published asset --------------------
  const preReloadSnapshot = await procSnapshot(page, PROC_OBJECT_ID);
  expect(preReloadSnapshot, "pre-reload proc snapshot").not.toBeNull();
  expect(preReloadSnapshot!.assetId, "assetId is a content-addressed proc.* id").toMatch(/^proc\.[a-z0-9_.-]+\.[a-f0-9]{16}$/);

  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);

  const reloadProbe = await modelProbe(page, [PROC_OBJECT_ID]);
  const iceReload = (reloadProbe as Record<string, any>)[PROC_OBJECT_ID];
  expect(iceReload.inModel, "repaired object persists after reload").toBe(true);
  expect(iceReload.root, "repaired object re-rendered after reload").toBe(true);
  const reloadSnapshot = await procSnapshot(page, PROC_OBJECT_ID);
  expect(reloadSnapshot, "post-reload proc snapshot").not.toBeNull();
  expect(reloadSnapshot!.assetId, "same content-addressed proc.* assetId after reload").toBe(preReloadSnapshot!.assetId);
  expect(
    JSON.stringify(reloadSnapshot!.generated),
    "byte-identical generated definition after reload",
  ).toBe(JSON.stringify(preReloadSnapshot!.generated));
  transcript.reload = {
    assetIdBefore: preReloadSnapshot!.assetId,
    assetIdAfter: reloadSnapshot!.assetId,
    generatedIdentical: JSON.stringify(reloadSnapshot!.generated) === JSON.stringify(preReloadSnapshot!.generated),
  };

  // ---- 8. hygiene -----------------------------------------------------------
  // The generic leak listener is a PRE-REVEAL scan (the reveal response
  // deliberately carries truth); the pre-reveal scan above already proved every
  // pre-reveal response clean, and the reveal allowlist-scan below proves the
  // reveal DTOs carry no INTERNAL material (solverProof/diagnostics/…).
  const revealResponses = net.captured.filter((b) => b.url.includes("/reveal"));
  expect(revealResponses.length, "reveal responses exist").toBeGreaterThan(0);
  const revealHits: Array<{ url: string; paths: string[] }> = [];
  for (const rr of revealResponses) {
    const hits: string[] = [];
    walkKeys(rr.body, "$", hits, REVEAL_FORBIDDEN_KEYS, { checkCanonicalValue: false, skipGenerated: true });
    if (hits.length > 0) revealHits.push({ url: rr.url, paths: hits });
  }
  await test.info().attach("phase17-post-reveal-scan.json", {
    body: JSON.stringify({ revealResponses: revealResponses.length, revealHits, forbiddenKeys: [...REVEAL_FORBIDDEN_KEYS] }, null, 2),
    contentType: "application/json",
  });
  expect(revealHits, "reveal DTOs carry no internal material").toEqual([]);
  await expectNoHostInDom(page);
  await page.screenshot({ path: "../screenshots/evidence/phase17-geometry-repaired.png", fullPage: false });
  expect(net.failed, `no failed resources (${net.failed.length})`).toEqual([]);
  expect(net.external, "no external network traffic").toEqual([]);
  expect(net.pageErrors, "no uncaught page errors").toEqual([]);
  expect(net.consoleErrors, "no console errors").toEqual([]);
  expect(leak.scanned, "leak listener scanned responses").toBeGreaterThan(0);
  transcript.leak = { preRevealScanned: scanned, preRevealMatched: preReveal.length, revealScanned: revealResponses.length, revealMatched: revealHits.length, leakListenerScanned: leak.scanned };
  transcript.net = { failed: net.failed.length, external: net.external.length, pageErrors: net.pageErrors.length, consoleErrors: net.consoleErrors.length };

  console.log(`P17_TRANSCRIPT ${JSON.stringify(transcript)}`);
  await test.info().attach("phase17-transcript.json", {
    body: JSON.stringify(transcript, null, 2),
    contentType: "application/json",
  });
});