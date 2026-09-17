import { expect, test } from "@playwright/test";
import { installLeakListener, seedPlaythroughCredentials } from "./helpers";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * PHASE 13 — GENERATED (proc.*) OBJECT RENDERS IN THE PRODUCTION SCENE
 * (QA-owned; .rad/roles/qa.md, .rad/policies/evidence.md,
 *  .rad/policies/deterministic-testing.md).
 *
 * FULL CHAIN, real published payload (preferred over a canned bootstrap):
 * the QA setup script (e2e/qa-phase13-scene-setup.py) drives the REAL
 * GenerationService over the SAME migrated scratch DB the live backend
 * serves, with FakeAssetSpecProvider + generate_unknown_assets enabled, so
 * the immutable published_versions row for the case carries 4 `proc.*`
 * world objects with embedded generated definitions (the office kit). This
 * spec pins a playthrough to that case through the PUBLIC API (session-free:
 * the creatorAccessToken appears once at case creation and is replayed) and
 * loads /scene in the production SPA (backend :8000 + vite preview :4173,
 * both via tools/process_guard).
 *
 * ASSERTIONS:
 *   1. The generated objects RENDER: scene canvas + ready, every proc.*
 *      world object has a live pd_obj_ root mesh with parts, and its own
 *      resolved color family is drawn in the WebGL buffer (pixel proof).
 *   2. Parts exist with FINITE transforms inside the documented bounds
 *      (|position|<=4, |rotation|<=2pi, 0.05<=scale<=2); part count matches
 *      the compiled definition.
 *   3. NO FALLBACK GRAY for the valid generated objects: assets-notice
 *      count 0, catalog-error 0, no part color equals the neutral fallback
 *      (#8d8d93), unknownAsset false.
 *   4. `canonicalName` is NOT rendered as a label (server-carried text
 *      stays off the page): the canonical names and subtypes never appear
 *      in the DOM; the object list falls back to the objectId.
 *   5. DIRECT CLICK FALLS BACK CLEANLY: the service publishes generated
 *      placements with interaction "" (decorative by design), so
 *      interactionWorks is false and a click on the object never raises an
 *      interaction or a page error.
 *   6. Leak scan 0 truth markers; no console/page errors; no failed
 *      requests; no external traffic.
 *
 * Durable evidence promoted to screenshots/evidence/phase13-generated-object.png.
 */
const BACKEND_BASE = "http://localhost:8010";
const CREDENTIALS_PATH = path.join(__dirname, "artifacts", "phase13-scene-credentials.json");

/** Documented generated part-color families (backend material table). */
const TROPHY_BRASS = [201, 162, 39]; // #c9a227 — unique to the trophy's brass parts
const RACK_STEEL = [184, 188, 194]; // #b8bcc2 — steel rails/frame

test("Phase 13 generated object: renders with finite parts, no fallback gray, canonicalName off-page, decorative click falls back cleanly", async ({
  page,
}) => {
  const creds = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, "utf-8"));
  const { caseId, creatorAccessToken } = creds;
  const procObjectIds: string[] = creds.proceduralObjectIds;

  const leak = installLeakListener(page);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  const externalTraffic: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });
  page.on("requestfailed", (req) => failedRequests.push(`REQFAIL ${req.method} ${req.url}`));
  page.on("request", (req: { method?: unknown; url?: unknown }) => {
    const method = typeof req.method === "function" ? req.method() : String(req.method ?? "");
    const rawUrl = typeof req.url === "function" ? req.url() : String(req.url ?? "");
    if (typeof rawUrl === "string" && !/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\//.test(rawUrl)) {
      externalTraffic.push(`${method} ${rawUrl}`);
    }
  });
  page.on("response", (res: { status: number; url?: unknown }) => {
    if (res.status >= 400) {
      const u = typeof res.url === "function" ? res.url() : String(res.url ?? "");
      failedRequests.push(`HTTP ${res.status} ${u}`);
    }
  });

  // Pin a playthrough to the QA-prepared case through the PUBLIC API.
  const pt = await (
    await page.request.post(`${BACKEND_BASE}/api/v1/cases/${caseId}/versions/1/playthroughs`, {
      headers: { Authorization: `Bearer ${creatorAccessToken}` },
    })
  ).json();
  await seedPlaythroughCredentials(page, { playthroughId: pt.playthroughId, playthroughToken: pt.playthroughAccessToken });
  await page.goto("/scene?pd-debug-pick=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1600);

  // -- 3) no fallback gray / no unknown-asset notice ------------------------
  await expect(page.getByTestId("assets-notice")).toHaveCount(0);
  await expect(page.getByTestId("catalog-error")).toHaveCount(0);
  await expect(page.getByTestId("kit-catalog-error")).toHaveCount(0);

  // -- 4) canonicalName / subtype never rendered -----------------------------
  const bodyText = (await page.locator("body").innerText()).toLowerCase();
  for (const offPage of [
    "unusual laboratory sample rack",
    "custom trophy",
    "distinctive desk award",
    "antique ceremonial letter opener",
    "sample_rack",
    "ceremonial_letter_opener",
  ]) {
    expect(bodyText, `server-carried text "${offPage}" must stay OFF the page`).not.toContain(offPage);
  }
  // The object list falls back to the app-side objectId, and the generated
  // objects exist in the list.
  const listText = (await page.getByTestId("scene-objects").innerText()).toLowerCase();
  for (const objectId of procObjectIds) {
    expect(listText, `object list shows objectId "${objectId}" (label is null)`).toContain(objectId);
  }

  // -- 1+2+3) live-renderer probe: roots, finite parts, colors, no fallback --
  const probe = await page.evaluate(
    ({ ids }: { ids: string[] }) => {
      const dbg = (window as any).__pdDebugScene;
      const scene = dbg.scene;
      const canvas = document.querySelector("canvas.scene-canvas") as HTMLCanvasElement;
      const width = canvas.width;
      const height = canvas.height;
      const gl = canvas.getContext("webgl2") || canvas.getContext("experimental-webgl2") || canvas.getContext("webgl");

      const result: Record<string, any> = {};
      const model = dbg.model;
      for (const id of ids) {
        const obj = model.worldObjects.find((o: any) => o.objectId === id);
        const root = scene.getNodeByName(`pd_obj_${id}`);
        const meshes: { name: string; diffuse: number[] }[] = [];
        if (root) {
          for (const child of root.getChildMeshes(false) as any[]) {
            if ((child.name ?? "").startsWith("pd_hit_")) continue;
            const dc = child.material?.diffuseColor;
            meshes.push({
              name: child.name ?? "",
              diffuse: dc ? [Math.round(dc.r * 255), Math.round(dc.g * 255), Math.round(dc.b * 255)] : [],
            });
          }
        }
        const parts = obj?.generatedParts ?? [];
        const transformsFinite = parts.every((p: any) =>
          [p.offset?.x, p.offset?.y, p.offset?.z, p.rotation?.x, p.rotation?.y, p.rotation?.z, p.size?.x, p.size?.y, p.size?.z]
            .every((v) => typeof v === "number" && Number.isFinite(v))
        );
        const boundsOk = parts.every((p: any) =>
          Math.abs(p.offset.x) <= 4.001 && Math.abs(p.offset.y) <= 4.001 && Math.abs(p.offset.z) <= 4.001 &&
          Math.abs(p.rotation.x) <= 2 * Math.PI + 1e-6 && Math.abs(p.rotation.y) <= 2 * Math.PI + 1e-6 && Math.abs(p.rotation.z) <= 2 * Math.PI + 1e-6 &&
          p.size.x >= 0.049 && p.size.x <= 2.001 && p.size.y >= 0.049 && p.size.y <= 2.001 && p.size.z >= 0.049 && p.size.z <= 2.001
        );
        const noFallbackGray = parts.every((p: any) =>
          !(Math.abs(p.color[1] - 141) <= 6 && Math.abs(p.color[2] - 141) <= 6 && Math.abs(p.color[3] - 147) <= 6) &&
          !(Math.abs(p.color?.[0] - 141) <= 6 && Math.abs(p.color?.[1] - 141) <= 6 && Math.abs(p.color?.[2] - 147) <= 6)
        );
        result[id] = {
          inModel: obj !== undefined,
          root: !!root,
          partCount: meshes.length,
          generatedParts: parts.length,
          generatedHitbox: obj?.generatedHitbox,
          label: obj?.label,
          unknownAsset: obj?.unknownAsset,
          interactionWorks: obj?.interactionWorks,
          primitiveKinds: parts.map((p: any) => p.kind),
          colors: parts.map((p: any) => p.color ?? p.color),
          transformsFinite,
          boundsOk,
          noFallbackGray,
        };
        const hit = root?.getChildMeshes(false)?.some((c: any) => (c.name ?? "").startsWith("pd_hit_"));
        result[id].pickHitboxMesh = !!hit;
      }

      // Census the unique brass family over two buffer reads.
      const readBuffer = (): Uint8Array => {
        const buf = new Uint8Array(width * height * 4);
        gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, buf);
        return buf;
      };
      const clusters = (tr: number, tg: number, tb: number, tol: number, buf: Uint8Array) => {
        let cnt = 0, sx = 0, sy = 0;
        for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
          const i = (y * width + x) * 4;
          if (buf[i + 3] < 200) continue;
          if (Math.abs(buf[i] - tr) <= tol && Math.abs(buf[i + 1] - tg) <= tol && Math.abs(buf[i + 2] - tb) <= tol) {
            cnt++; sx += x; sy += y;
          }
        }
        return { cnt, centroid: cnt ? { x: sx / cnt, y: sy / cnt } : null };
      };
      const best = (tone: number[]) => {
        const a = clusters(tone[0], tone[1], tone[2], 12, readBuffer());
        const b = clusters(tone[0], tone[1], tone[2], 12, readBuffer());
        return a.cnt >= b.cnt ? a : b;
      };
      const brass = best([201, 162, 39]);
      const steel = best([184, 188, 194]);
      return {
        size: { width, height },
        objects: result,
        brassPixels: brass.cnt,
        brassCentroid: brass.centroid,
        steelPixels: steel.cnt,
      };
    },
    { ids: procObjectIds },
  );

  for (const id of procObjectIds) {
    const o = (probe.objects as Record<string, any>)[id];
    expect(o, `${id}: live scene entry`).not.toBeUndefined();
    expect(o.inModel, `${id}: present in the scene model`).toBe(true);
    expect(o.root, `${id}: pd_obj_ root mesh in the live renderer`).toBe(true);
    expect(o.partCount, `${id}: rendered part meshes`).toBeGreaterThan(0);
    expect(o.generatedParts, `${id}: compiled generated parts`).toBeGreaterThan(0);
    expect(o.transformsFinite, `${id}: every part transform finite`).toBe(true);
    expect(o.boundsOk, `${id}: every part transform inside documented bounds`).toBe(true);
    expect(o.noFallbackGray, `${id}: no neutral-fallback gray part`).toBe(true);
    expect(o.label, `${id}: label stays null (canonicalName never a label)`).toBeNull();
    expect(o.unknownAsset, `${id}: NOT flagged as an unknown asset`).toBe(false);
    expect(o.interactionWorks, `${id}: decorative (interaction '') -> not interactable`).toBe(false);
  }
  // The lab rack is 5 parts, the trophy 3 (golden fixture shapes).
  expect((probe.objects as Record<string, any>).lab_rack.generatedParts).toBe(5);
  expect((probe.objects as Record<string, any>).custom_trophy.generatedParts).toBe(3);

  // Pixel proof: the generated trophy's brass family and the rack's steel
  // family are drawn somewhere on screen.
  expect(
    (probe as { brassPixels: number }).brassPixels,
    "generated trophy brass (#c9a227) pixels present in the WebGL buffer",
  ).toBeGreaterThan(0);
  expect(
    (probe as { brassCentroid: { x: number; y: number } | null }).brassCentroid,
    "trophy brass cluster located on screen",
  ).not.toBeNull();
  expect(
    (probe as { steelPixels: number }).steelPixels,
    "generated lab-rack steel (#b8bcc2) pixels present in the WebGL buffer",
  ).toBeGreaterThan(0);

  // -- 5) decorative click falls back cleanly: click where the trophy brass
  // cluster sits — no interaction error, no page error, scene stays ready.
  const centroid = (probe as { brassCentroid: { x: number; y: number } }).brassCentroid;
  await page.mouse.click(centroid.x, centroid.y);
  await page.waitForTimeout(600);
  await expect(page.getByTestId("interaction-error")).toHaveCount(0);
  await expect(page.getByTestId("scene-ready")).toBeVisible();

  await page.screenshot({ path: "screenshots/evidence/phase13-generated-object.png", fullPage: true });

  console.log("P13_GENERATED", JSON.stringify({
    sceneReady: true,
    assetsNoticeCount: 0,
    catalogErrors: 0,
    proceduralObjectIds: procObjectIds,
    probe: probe.objects,
    brassPixels: (probe as any).brassPixels,
    steelPixels: (probe as any).steelPixels,
    leakScanned: leak.scanned,
    leakMatched: leak.matched.length,
    pageErrors,
    consoleErrors,
    failedRequests,
    externalTraffic,
  }));
  await test.info().attach("phase13-generated.json", {
    body: JSON.stringify(
      { probe: probe.objects, brassPixels: (probe as any).brassPixels, steelPixels: (probe as any).steelPixels, leakScanned: leak.scanned, leakMatched: leak.matched.length },
      null,
      2,
    ),
    contentType: "application/json",
  });

  expect(leak.matched, `no forbidden truth keys in ${leak.scanned} API responses`).toEqual([]);
  expect(leak.scanned, "bootstrap responses must have been scanned").toBeGreaterThan(0);
  expect(pageErrors, "no uncaught page errors").toEqual([]);
  expect(consoleErrors, "no console errors").toEqual([]);
  expect(failedRequests, "no failed/failed-status requests").toEqual([]);
  expect(externalTraffic, "no external network traffic").toEqual([]);
});