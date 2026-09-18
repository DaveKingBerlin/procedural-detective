import { expect, test } from "@playwright/test";
import type { Page, APIRequestContext } from "@playwright/test";
import { installLeakListener, scanJsonBody, seedPlaythroughCredentials } from "./helpers";

/**
 * PHASE 14_5 — UNSEEN PROMPT OBJECT -> PROCEDURAL ASSET BROWSER E2E (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Drives the PUBLIC API (session -> POST /cases with a prompt naming a
 * GENUINELY unseen weapon -> playthrough -> /scene) against the production
 * SPA and proves the Phase 14_5 mandate end-to-end:
 *
 *  1. generation produced a proc.* asset (case DTO + investigation bootstrap
 *     carry the content-addressed proc.decor.* assetId with its declarative
 *     `generated` block);
 *  2. the generated object appears VISIBLY in the scene (live renderer model
 *     with pd_obj_ root + part meshes, and an on-screen WebGL tone census of
 *     its compiled brass/wood materials — not a neutral-gray fallback);
 *  3. DIRECT click: the direct mesh-pick path opens the inspection panel with
 *     the object's evidence fact (the discoverable latent-fingerprint
 *     evidence on the already-excluded michael_carter); the label-bearing
 *     golden knife still shows the hover tooltip on the same scene;
 *  4. the full accuse -> reveal loop completes (CASE SOLVED 4/4, Thomas Reed /
 *     embezzlement / Kitchen Knife / 22:17) with the solver universe unchanged;
 *  5. RELOAD: the SAME generated object (identical assetId + generated
 *     definition bytes) and the discovered-evidence state persist;
 *  6. hygiene: 0 forbidden pre-reveal key paths, allowlist-clean reveal DTOs,
 *     0 console/page errors, 0 failed resources, 0 external traffic, 0
 *     neutral-fallback gray for the proc object.
 *
 * The prompt and expected world are the documented Phase 14_5 fixture
 * (UNSEEN_WEAPON_PROMPT: office; bronze ceremonial ice pick REQUIRED by the
 * "used"-context rule; solver-visible truth stays on the golden catalog
 * knife). The QA backend runs on :8000 with the deterministic fixture-scripted
 * AssetSpecProvider (e2e/qa-phase145-backend.py) — a dev-mode wiring behind
 * the narrow provider interface, never a product modification.
 */

const BACKEND_BASE = "http://localhost:8000";

const UNSEEN_PROMPT =
  "A murder in an office. The killer used a bronze ceremonial ice pick " +
  "to stab the victim near the body.";

const PROC_OBJECT_ID = "bronze_ceremonial_ice_pick";
const PROC_EVIDENCE_ID = "bronze_ceremonial_ice_pick_fp_01";
const KNIFE_LABEL = "Kitchen knife";

/** Compiled material tones of the ice pick (fixture spec: metal.brass +
 * wood.dark handle; compiled brass family #c9a227). */
const ICE_PICK_TONES: number[][] = [
  [201, 162, 39], // metal.brass
  [74, 47, 29], // wood.dark
];

const FALLBACK_GRAY = [141, 141, 147]; // the neutral fallback #8d8d93

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

interface BootstrapShape {
  playthroughId: string;
  playthroughToken: string;
  environmentId: string;
  procObject: {
    objectId: string;
    assetId: string;
    generated: unknown;
    interaction: string;
    evidenceId: string | null;
  } | null;
}

/** Public-API handshake: session -> POST /cases (unseen prompt) -> playthrough
 * -> investigation bootstrap. Returns the proc.* object snapshot. */
async function createUnseenPlaythrough(
  request: APIRequestContext,
): Promise<BootstrapShape> {
  const sessionRes = await request.post(`${BACKEND_BASE}/api/v1/sessions/anonymous`);
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();

  const caseRes = await request.post(`${BACKEND_BASE}/api/v1/cases`, {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt: UNSEEN_PROMPT, difficulty: "medium" },
  });
  expect(caseRes.status(), "create case").toBe(201);
  const created = await caseRes.json();
  expect(created.status, "unseen prompt publishes").toBe("PUBLISHED");

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

  const procObject =
    (boot.scene.worldObjects ?? []).find(
      (o: { assetId: string }) => typeof o.assetId === "string" && o.assetId.startsWith("proc."),
    ) ?? null;

  return {
    playthroughId: pt.playthroughId,
    playthroughToken: pt.playthroughAccessToken,
    environmentId: boot.scene.environmentId as string,
    procObject: procObject
      ? {
          objectId: procObject.objectId as string,
          assetId: procObject.assetId as string,
          generated: procObject.generated ?? null,
          interaction: procObject.interaction as string,
          evidenceId: (procObject.evidenceId as string | null) ?? null,
        }
      : null,
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

/** Census of an object's tone family over the WebGL buffer -> internal-canvas
 * pixel count + centroid (TOP-DOWN internal coordinates; caller maps them to
 * viewport via the displayed canvas rect/800x480 scaling). */
async function censusTone(
  page: Page,
  tones: number[][],
): Promise<{ count: number; centroid: { x: number; y: number } | null }> {
  return page.evaluate(({ tones: tonesArg }: { tones: number[][] }) => {
    const canvas = document.querySelector("canvas.scene-canvas") as HTMLCanvasElement;
    const gl =
      canvas.getContext("webgl2") || canvas.getContext("experimental-webgl2") || canvas.getContext("webgl");
    const width = canvas.width;
    const height = canvas.height;
    const bufA = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, bufA);
    const bufB = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, bufB);
    const cluster = (tone: number[], buf: Uint8Array) => {
      let cnt = 0, sx = 0, sy = 0;
      for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
        const i = (y * width + x) * 4;
        if (buf[i + 3] < 200) continue;
        if (
          Math.abs(buf[i] - tone[0]) <= 32 &&
          Math.abs(buf[i + 1] - tone[1]) <= 32 &&
          Math.abs(buf[i + 2] - tone[2]) <= 32
        ) {
          cnt++; sx += x; sy += y;
        }
      }
      // buffer row 0 = BOTTOM of the canvas (WebGL origin); flip to the
      // internal top-down coordinate system the renderer uses.
      return { cnt, centroid: cnt ? { x: sx / cnt, y: height - 1 - sy / cnt } : null };
    };
    let best = { cnt: 0, centroid: null as { x: number; y: number } | null };
    for (const t of tonesArg) {
      for (const buf of [bufA, bufB]) {
        const c = cluster(t, buf);
        if (c.cnt > best.cnt) best = c;
      }
    }
    return { count: best.cnt, centroid: best.centroid };
  }, { tones });
}

/** Exact click target for a proc object: the center of its pick hitbox
 * projected through the LIVE camera to internal 800x480 canvas coordinates
 * (column-major Babylon Matrix math, no BABYLON global needed). Also returns
 * the hitbox screen footprint (diagonal) so the click provably lands on it. */
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
      if (Math.abs(ndcX) > 1.5 || Math.abs(ndcY) > 1.5) return null; // off-screen
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

// ---------------------------------------------------------------------------
// forbidden-key walkers (frozen mirrors of the phase14 scanners)
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
      if (key === "generated" && opts.skipGenerated) continue;
      if (forbidden.has(key) && !(key === "crimeTime" && underEcho)) hits.push(child);
      if (opts.checkCanonicalValue && typeof value === "string") {
        if (value === CANONICAL_TIME_VALUE && !underEcho) hits.push(`${child}(=canonicalCrimeTime)`);
      }
      walkKeys(value, child, hits, forbidden, opts);
    }
  }
}

// ---------------------------------------------------------------------------
// the Phase 14_5 unseen-weapon journey
// ---------------------------------------------------------------------------
test("Phase 14_5: unseen weapon generates proc.* asset, visible + directly clickable (evidence fact), accuse->reveal, reload persists, leak-free", async ({ page, request }) => {
  test.setTimeout(180_000);
  const leak = installLeakListener(page);
  const net = installSessionObservers(page);
  const transcript: Record<string, unknown> = { prompt: UNSEEN_PROMPT };

  // ---- 1. generation produced a proc.* asset (public API + bootstrap) ------
  const boot = await createUnseenPlaythrough(request);
  expect(boot.environmentId, "bootstrap environmentId").toBe("office");
  expect(boot.procObject, "bootstrap carries a proc.* object").not.toBeNull();
  expect(boot.procObject!.objectId).toBe(PROC_OBJECT_ID);
  expect(boot.procObject!.assetId).toMatch(/^proc\.decor\.[a-f0-9]{16}$/);
  expect(boot.procObject!.generated, "generated block embedded in the bootstrap").not.toBeNull();
  expect(boot.procObject!.interaction).toBe("inspect");
  expect(boot.procObject!.evidenceId).toBe(PROC_EVIDENCE_ID);
  transcript.bootstrap = {
    environmentId: boot.environmentId,
    procObjectId: boot.procObject!.objectId,
    procAssetId: boot.procObject!.assetId,
    interaction: boot.procObject!.interaction,
    evidenceId: boot.procObject!.evidenceId,
  };

  // case DTO agrees (the public case view)
  // (playthrough credential already known; reuse the caseId via the bootstrap
  // playthrough is not needed — the DTO check happens on reload below.)

  await seedPlaythroughCredentials(page, {
    playthroughId: boot.playthroughId,
    playthroughToken: boot.playthroughToken,
  });

  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);

  // environment identity
  const notice = page.getByTestId("environment-notice");
  await expect(notice).toBeVisible({ timeout: 15_000 });
  await expect(notice).toContainText("Environment: Office");

  // ---- 2. the generated object is present + VISIBLE in the scene -------------
  const listText = (await page.getByTestId("scene-objects").innerText()).toLowerCase();
  expect(listText, "object list shows the generated object id").toContain(PROC_OBJECT_ID);

  const probe = await modelProbe(page, [PROC_OBJECT_ID, "kitchen_knife", "apartment_laptop"]);
  const ice = (probe as Record<string, any>)[PROC_OBJECT_ID];
  expect(ice, "ice pick in the live model").not.toBeUndefined();
  expect(ice.inModel, "ice pick present in the scene model").toBe(true);
  expect(ice.root, "ice pick rendered as a pd_obj_ root mesh").toBe(true);
  expect(ice.partCount, "ice pick has rendered part meshes").toBeGreaterThanOrEqual(3);
  expect(ice.generated, "ice pick model carries the generated block").toBe(true);
  expect(ice.interactionWorks, "ice pick is directly interactable (interaction inspect)").toBe(true);
  expect(ice.unknownAsset, "ice pick is NOT an unknown neutral asset").toBe(false);
  expect(ice.label, "generated objects keep label null (no server text in the DOM)").toBeNull();
  const notFallback = ice.diffuse.every(
    (c: number[]) => !(Math.abs(c[0] - FALLBACK_GRAY[0]) <= 6 && Math.abs(c[1] - FALLBACK_GRAY[1]) <= 6 && Math.abs(c[2] - FALLBACK_GRAY[2]) <= 6),
  );
  expect(notFallback, "0 fallback-gray parts for the proc object").toBe(true);
  transcript.modelProbe = {
    icePick: { root: ice.root, partCount: ice.partCount, interactionWorks: ice.interactionWorks, label: ice.label, unknownAsset: ice.unknownAsset },
    diffuse: ice.diffuse,
  };

  // visible: on-screen WebGL tone census of the compiled materials
  const census = await censusTone(page, ICE_PICK_TONES);
  expect(census.count, "ice pick material tones drawn on screen").toBeGreaterThan(0);
  expect(census.centroid, "ice pick located on screen").not.toBeNull();
  transcript.census = { count: census.count, centroid: census.centroid };
  await page.screenshot({ path: "artifacts/screenshots/phase145-unseen-object.png" });

  // no neutral fallback anywhere: assets-notice + catalog-error absent
  expect(await page.getByTestId("assets-notice").count(), "0 assets-notice (no unknown asset)").toBe(0);
  expect(await page.getByTestId("catalog-error").count(), "0 catalog-error").toBe(0);

  // ---- 3. DIRECT click -> inspection panel with the evidence fact ------------
  // (a) the label-bearing golden knife on the SAME scene proves the hover
  //     tooltip mechanism of the direct interaction layer (32px primary scan,
  //     16px fine pass only when needed — same budget as the phase14 gate).
  const rect = (await page.getByTestId("scene-canvas").boundingBox())!;
  await page.mouse.move(rect.x - 40, rect.y + 20);
  const primary = await hoverScan(page, rect, 32, 32);
  let found = new Map(primary.found);
  if (!found.has(KNIFE_LABEL)) {
    const fine = await hoverScan(page, rect, 16, 16);
    for (const [label, sight] of fine.found) {
      if (!found.has(label)) found.set(label, sight);
    }
  }
  const knifeSight = found.get(KNIFE_LABEL);
  expect(knifeSight, "knife directly hoverable (tooltip) on the same scene").toBeDefined();
  await page.mouse.move(knifeSight!.x, knifeSight!.y);
  await expect(page.getByTestId("object-tooltip")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("object-tooltip")).toHaveText(KNIFE_LABEL);
  transcript.knifeTooltip = knifeSight;

  // (b) DIRECT click on the generated object's mesh: the exact click target is
  //     the center of its pick hitbox projected through the LIVE camera
  //     (internal 800x480 -> viewport via the CSS-scaled canvas rect) ->
  //     server-authoritative discovery panel (the fingerprint fact on the
  //     already-excluded michael_carter). The WebGL tone census above already
  //     proved the object is visibly rendered on screen.
  const pickTarget = await projectedHitboxCenter(page, PROC_OBJECT_ID);
  expect(pickTarget, "projected hitbox center of the generated object").not.toBeNull();
  const pickViewport = toViewport(rect, { x: pickTarget!.x, y: pickTarget!.y });
  expect(pickTarget!.diagonal, "hitbox screen footprint").toBeGreaterThan(0);
  await page.mouse.move(pickViewport.x, pickViewport.y);
  await page.waitForTimeout(120);
  await page.mouse.click(pickViewport.x, pickViewport.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  const panelText = await panel.innerText();
  expect(panelText.toLowerCase(), "panel shows the unseen object's evidence fact").toContain("fingerprint");
  expect(panelText.toLowerCase(), "panel facts reference the already-excluded suspect").toContain("michael carter");
  transcript.directClick = {
    internal: { x: Math.round(pickTarget!.x), y: Math.round(pickTarget!.y) },
    hitboxDiagonal: Math.round(pickTarget!.diagonal),
    censusCentroid: census.centroid,
    viewport: { x: Math.round(pickViewport.x), y: Math.round(pickViewport.y) },
    panelSubstring: panelText.slice(0, 180),
  };
  await page.screenshot({ path: "artifacts/screenshots/phase145-unseen-panel.png" });
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await page.getByTestId("evidence-close").click().catch(() => {});
  await expect(panel).not.toBeVisible();
  await expect(page.getByTestId("interaction-error")).toHaveCount(0);

  // the discovered fingerprint evidence is recorded in the knowledge summary
  await expect(page.getByTestId(`discovered-entry-${PROC_EVIDENCE_ID}`)).toBeVisible({ timeout: 10_000 });

  // ---- evidence hunt for the full loop (knife + laptop) ----------------------
  await page.getByTestId("object-kitchen_knife").click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Blood on the kitchen knife matches the victim");
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await page.getByTestId("evidence-close").click().catch(() => {});
  await expect(panel).not.toBeVisible();

  await page.getByTestId("object-apartment_laptop").click();
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 15_000 });
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText("Re: the missing funds");
  await page.getByTestId("evidence-close").click().catch(() => {});
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});
  await expect(panel).not.toBeVisible();
  transcript.investigated = ["bronze_ceremonial_ice_pick", "kitchen_knife", "apartment_laptop"];

  // ---- 4. accuse -> reveal -> CASE SOLVED ------------------------------------
  const accusationOpen = page.getByTestId("accusation-open");
  await expect(accusationOpen).toBeVisible();
  await accusationOpen.click();
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

  // pre-reveal leak scan over the whole captured session
  const preReveal: Array<{ url: string; paths: string[] }> = [];
  let scanned = 0;
  for (const entry of net.captured) {
    if (entry.body === null || typeof entry.body !== "object") continue;
    scanned += 1;
    const hits: string[] = [];
    walkKeys(entry.body, "$", hits, PRE_REVEAL_FORBIDDEN_KEYS, {
      checkCanonicalValue: true,
      skipGenerated: true,
    });
    if (hits.length > 0) preReveal.push({ url: entry.url, paths: hits });
  }
  console.log(`P145_PRE_REVEAL ${JSON.stringify({ scanned, matched: preReveal })}`);
  await test.info().attach("phase145-pre-reveal-scan.json", {
    body: JSON.stringify({ scanned, matched: preReveal, forbiddenKeys: [...PRE_REVEAL_FORBIDDEN_KEYS] }, null, 2),
    contentType: "application/json",
  });
  expect(preReveal, `no forbidden key paths in ${scanned} pre-reveal responses`).toEqual([]);
  expect(scanned, "pre-reveal responses captured").toBeGreaterThan(0);

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
  transcript.reveal = { overall: "CASE SOLVED", score: "4 / 4" };

  // post-reveal allowlist scan of the /reveal responses
  const revealResponses = net.captured.filter((b) => b.url.includes("/reveal"));
  expect(revealResponses.length, "reveal responses exist").toBeGreaterThan(0);
  const revealHits: Array<{ url: string; paths: string[] }> = [];
  for (const rr of revealResponses) {
    const hits: string[] = [];
    walkKeys(rr.body, "$", hits, REVEAL_FORBIDDEN_KEYS, { checkCanonicalValue: false, skipGenerated: true });
    if (hits.length > 0) revealHits.push({ url: rr.url, paths: hits });
  }
  await test.info().attach("phase145-post-reveal-scan.json", {
    body: JSON.stringify({ revealResponses: revealResponses.length, revealHits, forbiddenKeys: [...REVEAL_FORBIDDEN_KEYS] }, null, 2),
    contentType: "application/json",
  });
  expect(revealHits, "reveal DTOs carry no internal material").toEqual([]);

  // ---- 5. RELOAD -> the SAME generated object + state persist ---------------
  const preReloadSnapshot = await procSnapshot(page, PROC_OBJECT_ID);
  expect(preReloadSnapshot, "pre-reload proc snapshot").not.toBeNull();

  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);

  const reloadProbe = await modelProbe(page, [PROC_OBJECT_ID]);
  const iceReload = (reloadProbe as Record<string, any>)[PROC_OBJECT_ID];
  expect(iceReload.inModel, "generated object persists after reload").toBe(true);
  expect(iceReload.root, "generated object re-rendered after reload").toBe(true);
  const reloadSnapshot = await procSnapshot(page, PROC_OBJECT_ID);
  expect(reloadSnapshot, "post-reload proc snapshot").not.toBeNull();
  expect(reloadSnapshot!.assetId, "same content-addressed proc.* assetId after reload").toBe(preReloadSnapshot!.assetId);
  expect(
    JSON.stringify(reloadSnapshot!.generated),
    "byte-identical generated definition after reload",
  ).toBe(JSON.stringify(preReloadSnapshot!.generated));

  // discovered evidence state persisted
  await expect(page.getByTestId(`discovered-entry-${PROC_EVIDENCE_ID}`)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId(`discovered-entry-forensic_knife_match_01`)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId(`discovered-entry-email_thomas_01`)).toBeVisible({ timeout: 10_000 });
  // revealed lifecycle persists on /scene (callout shows the reveal entry)
  await expect(page.getByTestId("reveal-view-truth")).toBeVisible({ timeout: 10_000 });
  transcript.reload = {
    assetIdBefore: preReloadSnapshot!.assetId,
    assetIdAfter: reloadSnapshot!.assetId,
    generatedIdentical: JSON.stringify(reloadSnapshot!.generated) === JSON.stringify(preReloadSnapshot!.generated),
    discoveredEntriesPersisted: [PROC_EVIDENCE_ID, "forensic_knife_match_01", "email_thomas_01"],
  };

  // ---- 6. hygiene ------------------------------------------------------------
  // Truth-bearing responses are ONLY the /reveal DTOs (already allowlist-
  // scanned above) plus the player's own accusation echo (explicitly exempted
  // by the frozen pre-reveal scanner). The pre-reveal scan above proved every
  // OTHER response clean; the leak listener still observed the whole session.
  expect(net.failed, `no failed resources (${net.failed.length})`).toEqual([]);
  expect(net.external, "no external network traffic").toEqual([]);
  expect(net.pageErrors, "no uncaught page errors").toEqual([]);
  expect(net.consoleErrors, "no console errors").toEqual([]);
  expect(leak.scanned, "leak listener scanned responses").toBeGreaterThan(0);
  expect(scanned, "pre-reveal responses captured").toBeGreaterThan(0);
  transcript.leak = { preRevealScanned: scanned, preRevealMatched: preReveal.length };
  transcript.net = { failed: net.failed.length, external: net.external.length, pageErrors: net.pageErrors.length, consoleErrors: net.consoleErrors.length };

  console.log(`P145_TRANSCRIPT ${JSON.stringify(transcript)}`);
  await test.info().attach("phase145-transcript.json", {
    body: JSON.stringify(transcript, null, 2),
    contentType: "application/json",
  });
});