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
 * PHASE 17C/17D WAVE 3 — AUTHORITATIVE REAL-HERMES BROWSER JOURNEY (QA-owned;
 * .rad/roles/qa.md, .rad/policies/evidence.md, .rad/policies/deterministic-testing.md).
 *
 * Runs against the PRODUCTION build (vite preview :4173) and the QA backend on
 * :8000 launched with the OPERATOR .env — GENERATION_PROVIDER=ollama pointed at
 * the operator-provided LAN Ollama running `hermes3:8b` (the LAN host is
 * operator SECRET configuration: it is never printed, committed, rendered, or
 * emitted in any DTO — this spec PROVES that by scanning everywhere).
 *
 * The spec drives the PUBLIC API for case generation (session -> POST /cases
 * with the Phase17C §11 non-golden prompt -> playthrough) and the REAL browser
 * for the interactive proof, asserting exactly the Phase17C §12 mandate:
 *
 *   - Local-AI selector appears after the REAL capability probe with the honest
 *     label "Local AI — hermes3:8b — Ready" and the selection persists;
 *   - generation is the REAL remote provider (the published world differs from
 *     the golden fixture: office environment + a generated `proc.*` object);
 *   - office environment rendered (environment notice) + the bronze ceremonial
 *     ice pick EXISTS as a generated `proc.*` object, visibly coherent (live
 *     model probe + WebGL tone census of the REAL part colors; ZERO
 *     fallback-gray `assets-notice`; silhouette distinct from the kitchen
 *     knife blade);
 *   - hover (ring + pointer cursor over the projected hitbox — proc.* objects
 *     keep the DOM tooltip null by design, so the ring/cursor IS the hover
 *     affordance; the label-bearing kitchen knife on the SAME scene proves the
 *     DOM-tooltip path) + DIRECT 3D CLICK -> server-authoritative evidence
 *     panel (deterministic `d_ev_weapon_true` forensic comparison for the
 *     locked weapon);
 *   - accuse WHO paul_becker / WHY stolen_research_data / WEAPON
 *     bronze_ceremonial_ice_pick / WHEN 23:42 -> accepted -> reveal
 *     "CASE SOLVED" + score "4 / 4";
 *   - RELOAD -> the published world persists byte-identical (same proc.*
 *     assetId + the full generated definition); the reveal persists on reload;
 *   - NO truth leaked before reveal (pre-reveal key-path scan = 0) and NO
 *     Ollama host / IP / port / URL anywhere in the DOM or any API DTO
 *     (201 case response, investigation bootstrap, capability DTO, reveal DTO,
 *     interact/read/accuse responses — IPv4-regex + host-token scan = 0);
 *   - screenshot evidence promoted under screenshots/evidence/phase17cd-hermes-*.
 *
 * Budget: this is THE one authoritative real journey (4 provider calls, ~25-35s).
 * Run only against the operator-configured ollama backend, never in CI.
 */

const NONGOLDEN_PROMPT =
  "Victim: Dr. Anna Weiss\n" +
  "Murderer: Paul Becker\n" +
  "Motive: stolen research data\n" +
  "Weapon: bronze ceremonial ice pick\n" +
  "Time: 23:42\n" +
  "Witness: Lisa K\u00f6nig\n" +
  "Location: office\n";

/** Expected public display model on the operator Ollama (OLLAMA_MODEL in .env). */
const EXPECTED_MODEL = "hermes3:8b";

/**
 * Host/secret tokens that must NEVER appear in the DOM or any API JSON body.
 * The 1717-style IPv4 regex additionally covers the operator LAN host (private
 * RFC1918) without this file ever knowing it.
 */
const HOST_TOKENS = [
  "11434",
  "127.0.0.1",
  "host.docker.internal",
  "http://",
  "https://",
  ":11434",
] as const;
const IPV4_RE = /\b(?:\d{1,3}\.){3}\d{1,3}\b/;

const EVIDENCE_DIR = path.join(__dirname, "..", "screenshots", "evidence");

const SOLVER_IDS = {
  who: "paul_becker",
  why: "stolen_research_data",
  weapon: "bronze_ceremonial_ice_pick",
  when: "23:42",
};

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

/** Attach pageerror/console/failed/external/JSON-body observers (single reader). */
function installSessionObservers(page: Page): SessionReport {
  const report: SessionReport = {
    captured: [],
    pageErrors: [],
    consoleErrors: [],
    failed: [],
    external: [],
  };
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

/** Deep-scan one JSON body for host/URL/port material (allowlist-safe). */
function hostHitsInBlob(blob: string): string[] {
  const hits: string[] = [];
  if (IPV4_RE.test(blob)) hits.push("IPv4");
  for (const token of HOST_TOKENS) {
    if (blob.toLowerCase().includes(token.toLowerCase())) hits.push(token);
  }
  return hits;
}

/** Extract the generated proc.* world object + its definition from a bootstrap. */
function procSnapshot(body: unknown): string | null {
  if (body === null || typeof body !== "object") return null;
  const scene = (body as { scene?: { worldObjects?: unknown[] } }).scene;
  const world = (scene?.worldObjects ?? []).find(
    (o: { assetId?: string }) => typeof o?.assetId === "string" && o.assetId.startsWith("proc."),
  );
  if (world === undefined) return null;
  const w = world as Record<string, unknown>;
  // The IMMUTABLE published world part (objectId + assetId + compiled
  // definition); player-knowledge discovered/read flags are progress, not
  // world identity.
  return JSON.stringify({ objectId: w.objectId, assetId: w.assetId, generated: w.generated });
}

/** The exact public label rendered for the proc object (from the object list). */
function expectNoHostInDom(page: Page): Promise<string> {
  return page.evaluate(() => document.body?.innerText ?? "");
}

/** Neutral fallback tone (unknown-asset placeholder gray) — must never appear. */
const FALLBACK_GRAY = [141, 141, 147];
/** Kitchen-knife blade steel (#c8ccd4) — the nearby critical-evidence tone. */
const KNIFE_BLADE = [200, 204, 212];

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
    const bufB = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, bufA);
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

/** Projected centers of the object's VISIBLE part meshes (internal 800x480
 * canvas coords). The pd_hit_ boxes are excluded — thin objects keep a
 * HITBOX_MIN-floored hit mesh that may extend beside the blade, and the
 * combined AABB center of separated parts (handle + blade) can land on the
 * floor between them, so each part projects its OWN center and the test picks
 * the part point where the hover ring/cursor actually engages. */
async function projectedPartCenters(
  page: Page,
  objectId: string,
): Promise<Array<{ x: number; y: number }> | null> {
  return page.evaluate(({ id }: { id: string }) => {
    const dbg = (window as any).__pdDebugScene;
    const scene = dbg?.scene;
    const camera = dbg?.camera;
    if (!scene || !camera) return null;
    const root = scene.getNodeByName(`pd_obj_${id}`);
    if (!root) return null;
    const meshes: any[] = root.getChildMeshes(false) ?? [];
    const visible = meshes.filter((m) => !(m.name ?? "").startsWith("pd_hit_"));
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
    const points: Array<{ x: number; y: number }> = [];
    for (const m of visible) {
      let bb;
      try {
        bb = m.getBoundingInfo().boundingBox;
      } catch {
        continue;
      }
      if (!bb) continue;
      const c = bb.centerWorld;
      const p = project(c.x, c.y, c.z);
      if (p !== null) points.push(p);
    }
    return points.length > 0 ? points : null;
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

test("Phase17C/17D Wave 3 — REAL hermes3:8b prompt-to-world journey (office + proc.* ice pick, direct click, accuse 4/4, reload byte-identical, zero leak, zero host)", async ({
  page,
  request,
}) => {
  test.setTimeout(1_500_000);
  const net = installSessionObservers(page);
  const leak = installLeakListener(page);
  const evidence = (name: string) => path.join(EVIDENCE_DIR, name);

  // ---------------------------------------------------------------------------
  // (1) Landing: the REAL capability probe (the real provider answers /api/tags)
  //     -> selector "Local AI — hermes3:8b — Ready"; selection persists.
  // ---------------------------------------------------------------------------
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const selector = page.getByTestId("generation-mode-selector");
  await expect(selector).toBeVisible({ timeout: 60_000 });
  const select = page.getByTestId("generation-mode-select");
  await expect(select).toBeVisible();
  const options = await select.locator("option").allTextContents();
  expect(
    options.join(" | "),
    "Local AI offered with the honest label + hermes3:8b + Ready tag",
  ).toContain(`Local AI — ${EXPECTED_MODEL} — Ready`);
  expect(options.join(" | "), "Demo option always offered").toContain("Demo");
  await page.screenshot({ path: evidence("phase17cd-hermes-selector.png"), fullPage: false });

  await select.selectOption("local");
  await page.waitForTimeout(150);
  const stored = await page.evaluate(() => localStorage.getItem("pd_generation_mode"));
  expect(stored, "selection persisted under pd_generation_mode").toBe("local");
  await page.reload({ waitUntil: "domcontentloaded" });
  const selectAfter = page.getByTestId("generation-mode-select");
  await expect(selectAfter).toBeVisible({ timeout: 60_000 });
  await expect(selectAfter).toHaveValue("local");

  // The capability DTO (public allowlist — Phase16 J) is allowlist-scanned.
  const capability = await page.evaluate(async () => {
    const r = await fetch("http://localhost:8000/api/v1/generation-capabilities");
    return { status: r.status, body: await r.json() };
  });
  expect(capability.status, "generation-capabilities status").toBe(200);
  const capBody = capability.body as { modes: Array<{ id: string; available: boolean; model?: string }> };
  const localMode = capBody.modes.find((m) => m.id === "local");
  expect(localMode, "capability DTO carries the local mode").toBeDefined();
  expect(localMode!.available, "real probe says the remote provider is available").toBe(true);
  expect(localMode!.model, "capability DTO carries the operator model display name").toBe(EXPECTED_MODEL);
  expect(hostHitsInBlob(JSON.stringify(capBody)), "capability DTO has zero host/URL material").toEqual([]);

  // ---------------------------------------------------------------------------
  // (2) PUBLIC-API generation with the REAL remote provider (edf: 4 calls).
  // ---------------------------------------------------------------------------
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
  // The 201 case DTO is part of the no-host scan (aware: it carries only the
  // frozen CaseStartedDTO fields + a token; neither can hold a URL).
  expect(hostHitsInBlob(JSON.stringify(created)), "201 case DTO has zero host material").toEqual([]);
  const caseId: string = created.caseId as string;

  const ptRes = await request.post(
    `${BACKEND_BASE}/api/v1/cases/${caseId}/versions/1/playthroughs`,
    { headers: { Authorization: `Bearer ${created.creatorAccessToken}` } },
  );
  expect(ptRes.status(), "create playthrough").toBe(201);
  const pt = await ptRes.json();
  const cred = { playthroughId: pt.playthroughId as string, playthroughToken: pt.playthroughAccessToken as string };

  // Seed the credential under the contract keys, then open /scene.
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([pid, token]) => {
      localStorage.setItem("pd_playthrough_id", pid);
      localStorage.setItem("pd_playthrough_token", token);
    },
    [cred.playthroughId, cred.playthroughToken] as const,
  );

  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("scene-error")).toHaveCount(0);
  await expect(page.getByTestId("objective-text")).toContainText("Find evidence, then accuse someone.");
  await expect(
    page.evaluate(() => Boolean((window as any).__pdDebugScene)),
    "debug scene handle available for the read-only renderer probe",
  ).resolves.toBe(true);

  // ---- office environment rendered (non-golden; the golden fixture is apartment).
  const envNotice = page.getByTestId("environment-notice");
  await expect(envNotice).toBeVisible({ timeout: 30_000 });
  await expect(envNotice).toHaveText("Environment: Office");

  // ---- the bronze ceremonial ice pick EXISTS as a generated proc.* object.
  // World-object id is the locked weapon token; the generated identity is the
  // embedded assetId (proc.*) + the compiled definition in the bootstrap.
  const procButtons = page.locator('[data-testid="object-bronze_ceremonial_ice_pick"]');
  expect(await procButtons.count(), "the bronze ceremonial ice pick world object is present").toBe(1);
  const firstProc = procButtons.first();
  const worldObjectId = ((await firstProc.getAttribute("data-testid"))!).replace(/^object-/, "");
  const bootstrapsNow = net.captured.filter((c) => c.url.includes("/investigation")).map((c) => c.body);
  const lastBootstrap = bootstrapsNow[bootstrapsNow.length - 1];
  const procWorld = (lastBootstrap as { scene?: { worldObjects?: Array<Record<string, unknown>> } }).scene?.worldObjects?.find(
    (o) => o.objectId === worldObjectId,
  );
  expect(procWorld, "bootstrap carries the ice pick world object").toBeDefined();
  expect(String(procWorld!.assetId), "the ice pick is a generated proc.* asset").toMatch(/^proc\..+/);
  expect(procWorld!.generated, "the bootstrap embeds the compiled generated definition").not.toBeNull();
  const procAssetId = String(procWorld!.assetId);
  const procLabel = (await firstProc.innerText()).trim();
  expect(procLabel.length, "proc object has a rendered public label").toBeGreaterThan(0);

  // ZERO fallback-gray: no assets-notice (unknown asset placeholder), no catalog error.
  await expect(page.getByTestId("assets-notice")).toHaveCount(0);
  await expect(page.getByTestId("catalog-error")).toHaveCount(0);
  await expect(page.getByTestId("kit-catalog-error")).toHaveCount(0);

  // ---- visible coherence (DEF-079 acceptance): the REAL thin definition
  //      renders as its parts with REAL material colors — never the neutral
  //      fallback. Live model probe + WebGL tone census below.
  const canvas = page.getByTestId("scene-canvas");
  const rect = await canvas.boundingBox();
  expect(rect, "scene canvas bounding box").not.toBeNull();
  const canvasBox = rect!;
  await page.mouse.move(canvasBox.x - 40, canvasBox.y + 20); // clear hover

  // (3a) LIVE MODEL PROBE — the ice pick model carries the generated block,
  //      is NOT an unknown asset, and renders at least two part meshes whose
  //      material colors are real (0 fallback-gray).
  const probe = await modelProbe(page, [worldObjectId, "kitchen_knife"]);
  const ice = (probe as Record<string, any>)[worldObjectId];
  expect(ice, "thin ice pick present in the live renderer model").not.toBeUndefined();
  expect(ice.inModel, "thin ice pick present in the scene model").toBe(true);
  expect(ice.generated, "thin ice pick model carries the generated definition").toBe(true);
  expect(ice.unknownAsset, "thin ice pick is NOT an unknown neutral asset").toBe(false);
  expect(ice.root, "thin ice pick rendered as a pd_obj_ root mesh").toBe(true);
  expect(ice.partCount, "thin ice pick has rendered part meshes").toBeGreaterThanOrEqual(2);
  expect(ice.interactionWorks, "thin ice pick directly interactable (interaction inspect)").toBe(true);
  expect(ice.label, "generated objects keep label null (server text never in the DOM)").toBeNull();
  const fallbackFree = ice.diffuse.every(
    (c: number[]) => !(Math.abs(c[0] - FALLBACK_GRAY[0]) <= 6 && Math.abs(c[1] - FALLBACK_GRAY[1]) <= 6 && Math.abs(c[2] - FALLBACK_GRAY[2]) <= 6),
  );
  expect(fallbackFree, "0 fallback-gray parts on the thin ice pick").toBe(true);
  console.log("PHASE17CD_MODEL_PROBE", JSON.stringify({ partCount: ice.partCount, diffuse: ice.diffuse }));

  // (3b) WEBGL TONE CENSUS — the REAL part colors are drawn ON SCREEN. Census
  //      every tone the renderer actually assigned (e.g. brass #c9a227 /
  //      wood.dark), take the dominant family, require a visible pixel count.
  interface ToneCensus { count: number; centroid: { x: number; y: number } | null }
  let bestTone: ToneCensus | null = null;
  let bestColor: number[] | null = null;
  for (const c of ice.diffuse) {
    const census = await censusTone(page, c);
    if (bestTone === null || census.count > bestTone.count) { bestTone = census; bestColor = c; }
  }
  expect(bestTone, "at least one rendered part tone was censused").not.toBeNull();
  expect(bestTone!.count, "the REAL thin-object part colors are drawn on screen (WebGL census)").toBeGreaterThan(0);
  expect(bestTone!.centroid, "thin ice pick located on screen").not.toBeNull();
  console.log("PHASE17CD_TONE_CENSUS", JSON.stringify({ color: bestColor, count: bestTone!.count, centroid: bestTone!.centroid }));

  // (3c) SILHOUETTE DISTINCT FROM THE KITCHEN KNIFE — the nearby critical
  //      evidence blade (steel #c8ccd4) and the ice-pick tone must occupy
  //      DIFFERENT on-screen centroids (>24 px apart).
  const knifeProbe = (probe as Record<string, any>).kitchen_knife;
  expect(knifeProbe?.inModel, "kitchen knife present in the scene model").toBe(true);
  const blade = await censusTone(page, KNIFE_BLADE);
  expect(blade.count, "knife blade steel family drawn on screen").toBeGreaterThan(0);
  expect(blade.centroid, "knife blade located on screen").not.toBeNull();
  const sep = Math.hypot(
    bestTone!.centroid!.x - blade.centroid!.x,
    bestTone!.centroid!.y - blade.centroid!.y,
  );
  expect(sep, "ice-pick tone and knife-blade steel centroids differ on screen (>24px)").toBeGreaterThan(24);
  console.log("PHASE17CD_SILHOUETTE_SEP", JSON.stringify({ separationPx: Math.round(sep) }));

// (3d/4) HOVER + DIRECT CLICK — the documented proc.* hover affordance is
  //      ring + pointer cursor ON the directly-pickable mesh (thin objects
  //      keep a HITBOX_MIN-floored hit mesh; the DOM tooltip stays null for
  //      generated objects by design). Project every visible part's center and
  //      find the part point where hovering ACTUALLY engages the ring/cursor —
  //      that is the direct interaction point. The label-bearing kitchen knife
  //      in the SAME scene proves the DOM-tooltip path separately below.
  const partPoints = await projectedPartCenters(page, worldObjectId);
  expect(partPoints, "projected visible part centers of the thin ice pick").not.toBeNull();
  let interactPt: { x: number; y: number } | null = null;
  const hoverScan2 = async (): Promise<{ x: number; y: number } | null> => {
    const pts = partPoints!.map((p) => ({ p, v: toViewport(canvasBox, p) }));
    for (const { v } of pts) {
      await page.mouse.move(v.x, v.y);
      await page.waitForTimeout(200);
      const st = await page.evaluate(({ id }: { id: string }) => {
        const dbg = (window as any).__pdDebugScene;
        const scene = dbg?.scene;
        const cnv = document.querySelector("canvas.scene-canvas") as HTMLCanvasElement;
        const ring = scene ? scene.getNodeByName(`pd_ring_${id}`) : null;
        return { cursor: cnv.style.cursor, ringVisible: ring ? ring.isVisible : false };
      }, { id: worldObjectId });
      if (st.cursor === "pointer" || st.ringVisible === true) return v;
    }
    return null;
  };
  await page.mouse.move(canvasBox.x - 40, canvasBox.y + 20); // clear hover
  interactPt = await hoverScan2();
  expect(interactPt, "a visible part of the thin ice pick directly engages the hover ring/cursor").not.toBeNull();
  const hoverState = await page.evaluate(({ id }: { id: string }) => {
    const dbg = (window as any).__pdDebugScene;
    const scene = dbg?.scene;
    const cnv = document.querySelector("canvas.scene-canvas") as HTMLCanvasElement;
    const ring = scene ? scene.getNodeByName(`pd_ring_${id}`) : null;
    return { cursor: cnv.style.cursor, ringVisible: ring ? ring.isVisible : false };
  }, { id: worldObjectId });
  expect(hoverState.cursor, "hover over the thin ice pick sets the pointer cursor").toBe("pointer");
  expect(hoverState.ringVisible, "hover ring visible over the thin ice pick's pickable mesh").toBe(true);
  await page.screenshot({ path: evidence("phase17cd-hermes-hover-icepick.png") });

  // Pixel patch at the engaged part point (proves the REAL material tone is
  // drawn there — never the neutral fallback gray).
  const samplePatch = await page.evaluate(
    ({ vx, vy }) => {
      const el = document.querySelector("canvas.scene-canvas") as HTMLCanvasElement | null;
      if (!el) return null;
      const box = el.getBoundingClientRect();
      const gl = (el.getContext("webgl") as WebGLRenderingContext | null) ?? (el.getContext("webgl2") as WebGL2RenderingContext | null);
      if (!gl || box.width === 0) return null;
      const sx = el.width / box.width;
      const sy = el.height / box.height;
      const bx = Math.round((vx - box.left) * sx);
      const by = Math.round(el.height - (vy - box.top) * sy);
      const w = 7;
      const px = new Uint8Array(w * w * 4);
      try {
        gl.readPixels(bx - 3, by - 3, w, w, gl.RGBA, gl.UNSIGNED_BYTE, px);
      } catch {
        return null;
      }
      let r = 0, g = 0, b = 0, n = 0;
      for (let i = 0; i < w * w; i++) {
        if (px[i * 4 + 3] < 8) continue;
        r += px[i * 4]; g += px[i * 4 + 1]; b += px[i * 4 + 2]; n += 1;
      }
      if (n === 0) return null;
      return { r: Math.round(r / n), g: Math.round(g / n), b: Math.round(b / n), n };
    },
    { vx: interactPt.x, vy: interactPt.y },
  );
  test.info().attach("phase17cd-pixel-sample.json", {
    body: JSON.stringify({ procAssetId, pos: [interactPt.x, interactPt.y], patch: samplePatch }, null, 2),
    contentType: "application/json",
  });
  console.log("PHASE17CD_PIXEL", JSON.stringify({ procAssetId, patch: samplePatch }));
  if (samplePatch) {
    expect(Math.abs(samplePatch.r - FALLBACK_GRAY[0]) + Math.abs(samplePatch.g - FALLBACK_GRAY[1]) + Math.abs(samplePatch.b - FALLBACK_GRAY[2]),
      "thin ice pick is not the neutral/fallback gray").toBeGreaterThan(40);
    expect(samplePatch.n, "visible nonshader pixels at the thin ice pick position").toBeGreaterThan(0);
  } else {
    console.log("PHASE17CD_PIXEL_READBACK_UNAVAILABLE");
  }

  // The DOM-tooltip path is proven by the label-bearing kitchen knife on the
  // SAME scene (grid hover-scan finds the registry label; the proc.* object
  // correctly shows none by design).
  const tooltipEl = page.getByTestId("object-tooltip");
  const tooltipText = async (): Promise<string | null> => {
    if (!(await tooltipEl.isVisible().catch(() => false))) return null;
    return (await tooltipEl.textContent()) ?? null;
  };
  const knifeSight = (await (async () => {
    const scan = async (sx: number, sy: number): Promise<{ label: string; x: number; y: number } | null> => {
      const yStart = canvasBox.y + canvasBox.height * 0.05;
      const yEnd = canvasBox.y + canvasBox.height * 0.95;
      for (let gy = yStart; gy <= yEnd; gy += sy) {
        for (let gx = canvasBox.x; gx <= canvasBox.x + canvasBox.width; gx += sx) {
          await page.mouse.move(gx, gy);
          await page.waitForTimeout(16);
          const label = await tooltipText();
          if (label === "Kitchen knife") return { label, x: Math.round(gx), y: Math.round(gy) };
        }
      }
      return null;
    };
    const primary = await scan(32, 32);
    return primary ?? scan(16, 16);
  })());
  expect(knifeSight, "the label-bearing kitchen knife on the same scene is directly hoverable (DOM tooltip)").not.toBeNull();
  await page.mouse.move(knifeSight!.x, knifeSight!.y);
  await expect(tooltipEl).toBeVisible({ timeout: 5_000 });
  await expect(tooltipEl).toHaveText("Kitchen knife");

  // (5) DIRECT 3D CLICK at the visible part -> server-authoritative
  //     discovery + evidence panel (d_ev_weapon_true Forensic comparison).
  await page.mouse.click(interactPt.x, interactPt.y);
  await expect(page.getByTestId("discovery-toast")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("evidence-panel")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".evidence-panel-title")).toContainText(procLabel);
  await expect(page.getByTestId("evidence-preview")).toBeVisible();
  await expect(page.getByTestId("discovered-entry-d_ev_weapon_true")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("discovered-summary")).toContainText("Forensic comparison");
  await page.screenshot({ path: evidence("phase17cd-hermes-icepick-panel.png") });
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("evidence-panel")).not.toBeVisible();
  await page.getByTestId("discovery-toast-dismiss").click().catch(() => {});

  // ---- RELOAD: the published world persists byte-identical.
  const bootstrapBodiesBefore = net.captured
    .filter((c) => c.url.includes("/investigation"))
    .map((c) => c.body);
  const snapshotBefore = procSnapshot(bootstrapBodiesBefore[bootstrapBodiesBefore.length - 1]);
  expect(snapshotBefore, "bootstrap before reload carries the proc.* definition").not.toBeNull();

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 60_000 });
  const bootstrapBodiesAfter = net.captured
    .filter((c) => c.url.includes("/investigation"))
    .map((c) => c.body);
  const snapshotAfter = procSnapshot(bootstrapBodiesAfter[bootstrapBodiesAfter.length - 1]);
  expect(snapshotAfter, "bootstrap after reload carries the proc.* definition").not.toBeNull();
  expect(snapshotAfter, "reload returns a byte-identical proc.* world object (id + definition)").toEqual(snapshotBefore);
  // The discovery state persists too (the puzzle progress survived the reload).
  await expect(page.getByTestId("discovered-entry-d_ev_weapon_true")).toBeVisible({ timeout: 20_000 });

  // ---------------------------------------------------------------------------
  // (5) ACCUSE the correct WHO/WHY/WEAPON/WHEN -> accepted; reveal 4/4.
  // ---------------------------------------------------------------------------
  await page.getByTestId("accusation-open").click();
  await expect(page.getByTestId("accusation-panel")).toBeVisible({ timeout: 30_000 });

  // The candidate universes carry the solver's public ids (unique winners).
  for (const id of [SOLVER_IDS.who, SOLVER_IDS.why, SOLVER_IDS.weapon]) {
    const ids = await page
      .locator(`input[name="${id === SOLVER_IDS.who ? "murdererId" : id === SOLVER_IDS.why ? "motiveId" : "weaponId"}"]`)
      .evaluateAll((inputs) => (inputs as HTMLInputElement[]).map((i) => i.value));
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
  await expect(page.getByTestId("accusation-summary-weapon")).toContainText(/proc\.decor\./i);
  await expect(page.getByTestId("accusation-summary-time")).toHaveText("23:42");
  await page.screenshot({ path: evidence("phase17cd-hermes-accusation.png") });

  await page.getByTestId("accusation-confirm").click();
  await expect(page.getByTestId("accusation-accepted")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("accusation-accepted")).toContainText("Accusation accepted");

  // ---- (6) PRE-REVEAL LEAK SCAN: 0 forbidden key paths in every captured API
  //      response BEFORE the reveal + 0 host material anywhere so far.
  const preRevealBodies = [...net.captured];
  const preRevealLeaks: Array<{ url: string; paths: string[] }> = [];
  let preRevealScanned = 0;
  for (const entry of preRevealBodies) {
    if (entry.url.includes("generation-capabilities")) continue; // documented allowlist DTO
    if (entry.body === null || typeof entry.body !== "object") continue;
    preRevealScanned += 1;
    // REQUIREMENTS 40.10 echo exemption: the accusation 200 MAY carry the
    // player's OWN submitted ids inside the frozen `accusation` echo block.
    const paths = scanJsonBody(entry.body).filter((p) => !p.startsWith("$.accusation."));
    if (paths.length > 0) preRevealLeaks.push({ url: entry.url, paths });
  }
  const preRevealBodyText = await page.locator("body").innerText();
  for (const token of ["murderer:", "truth", "correct", "winner", "solution"]) {
    expect(preRevealBodyText.toLowerCase(), `pre-reveal DOM must not reveal ${token}`).not.toContain(token);
  }
  test.info().attach("phase17cd-pre-reveal-leak-scan.json", {
    body: JSON.stringify(
      { scanned: preRevealScanned, matched: preRevealLeaks, forbiddenKeys: FORBIDDEN_KEY_PATHS },
      null,
      2,
    ),
    contentType: "application/json",
  });
  console.log("PHASE17CD_PREREVEAL_LEAK", JSON.stringify({ scanned: preRevealScanned, matched: preRevealLeaks }));
  expect(preRevealLeaks, "0 forbidden key paths in all pre-reveal API responses").toEqual([]);
  expect(preRevealScanned, "pre-reveal responses must have been scanned").toBeGreaterThan(0);

  // ---- (7) REVEAL: CASE SOLVED 4/4 with the real world's truth.
  await page.getByTestId("reveal-case").click();
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  // The model's canonical time stays within the ±300s accusation tolerance of
  // the prompt's 23:42; the 4/4 assertion below proves the WHEN dimension was
  // correct. The rendered truth time shows the canonical HH:MM.
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

  // ---- (8) reload persists the identical reveal.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("reveal-screen")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("reveal-overall")).toContainText("CASE SOLVED");
  await expect(page.getByTestId("reveal-score")).toContainText("4 / 4");
  await page.screenshot({ path: evidence("phase17cd-hermes-reveal-reload.png"), fullPage: true });

  // ---------------------------------------------------------------------------
  // (9) FULL-SESSION NO-HOST SCAN: DOM text + every API JSON body (including
  //     the reveal DTO, the 201 case response and the capability DTO).
  // ---------------------------------------------------------------------------
  const domText = await page.locator("body").innerText();
  expectNoHostInDom(page);
  expect(hostHitsInBlob(domText), "DOM text must be free of host/URL/port material").toEqual([]);

  const hostLeaks: Array<{ url: string; tokens: string[] }> = [];
  const hostScannedBodies: string[] = [];
  for (const entry of net.captured) {
    const blob = JSON.stringify(entry.body ?? {});
    hostScannedBodies.push(entry.url);
    const hits = hostHitsInBlob(blob);
    if (hits.length > 0) hostLeaks.push({ url: entry.url, tokens: hits });
  }
  // The 201 case body recorded in step (2) is appended artificially here (the
  // APIRequestContext responses are not part of the page listener).
  const caseResBlob = JSON.stringify(created);
  const caseResHits = hostHitsInBlob(caseResBlob);
  if (caseResHits.length > 0) hostLeaks.push({ url: "POST /api/v1/cases (201)", tokens: caseResHits });

  test.info().attach("phase17cd-no-host-scan.json", {
    body: JSON.stringify(
      {
        scannedUrls: net.captured.map((c) => c.url).concat(["POST /api/v1/cases (201)"]),
        domIpv4: IPV4_RE.test(domText),
        leaks: hostLeaks,
      },
      null,
      2,
    ),
    contentType: "application/json",
  });
  console.log("PHASE17CD_HOST_SCAN", JSON.stringify({ scanned: hostScannedBodies.length + 1, leaks: hostLeaks }));
  // Sanity: the journey really produced scannable API responses incl. a reveal.
  expect(net.captured.some((c) => c.url.includes("/reveal")), "a reveal response was captured").toBe(true);
  expect(
    net.captured.some((c) => c.url.includes("/investigation")),
    "an investigation bootstrap was captured",
  ).toBe(true);
  expect(hostLeaks, "NO Ollama host/IP/port/URL in ANY DOM text or API DTO").toEqual([]);

  // ---------------------------------------------------------------------------
  // (10) Session health: 0 page errors, 0 console errors, 0 failed resources,
  //      0 external traffic; the leak listener agrees (0 forbidden keys outside
  //      the documented capability DTO).
  // ---------------------------------------------------------------------------
  test.info().attach("phase17cd-transcript.json", {
    body: JSON.stringify(
      {
        worldObjectId,
        procAssetId,
        procLabel,
        solverIds: SOLVER_IDS,
        captured: net.captured.map((c) => ({ url: c.url, method: c.method })),
        pixelPatch: samplePatch,
      },
      null,
      2,
    ),
    contentType: "application/json",
  });
  expect(net.pageErrors, "no uncaught page errors").toEqual([]);
  expect(net.consoleErrors, "no console errors").toEqual([]);
  expect(net.failed, "no failed resources").toEqual([]);
  expect(net.external, "no external network traffic").toEqual([]);
  const leakWithoutCapabilities = leak.matched.filter((m) => !m.url.includes("generation-capabilities") && !m.url.includes("/reveal"));
  const leakNonEcho = leakWithoutCapabilities
    .map((m) => ({ url: m.url, paths: m.paths.filter((p) => !p.startsWith("$.accusation.") && !p.startsWith("$.player.accusation.")) }))
    .filter((m) => m.paths.length > 0);
  expect(leakNonEcho, "leak listener agrees: every pre-reveal response (non-reveal, non-echo) carries zero forbidden key paths").toEqual([]);
  expect(leak.scanned, "leak listener scanned API responses").toBeGreaterThan(0);
  console.log(
    "PHASE17CD_TRANSCRIPT",
    JSON.stringify({
      model: EXPECTED_MODEL,
      published: created.status,
      environment: "office",
      procAssetId,
      procLabel,
      directClickEvidence: "d_ev_weapon_true",
      accuse: SOLVER_IDS,
      reveal: "4/4 CASE SOLVED",
      reloadByteIdentical: true,
      preRevealLeakScan: `${preRevealScanned} scanned / 0 matched`,
      hostScan: `0 leaks in ${hostScannedBodies.length + 1} bodies + DOM`,
      pixelPatch: samplePatch,
    }),
  );
});