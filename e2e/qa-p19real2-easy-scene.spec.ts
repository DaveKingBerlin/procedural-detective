import { expect, test } from "@playwright/test";
import { installLeakListener, seedPlaythroughCredentials } from "./helpers";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * PHASE 19B REAL-HOST ACCEPTANCE RE-RUN — LIGHT BROWSER CHECK for the
 * published EASY case (QA-owned).
 *
 * Easy PUBLISHED on the real local-Ollama stack in this gate
 * (CASE-Cr21zetVapyg, environment=apartment, 3 provider calls, 0 procedural
 * assets — kitchen_knife resolved CATALOG_EXACT to PROP_KITCHEN_KNIFE_01).
 * This spec pins a playthrough (already created via the public API) and loads
 * /scene in the production SPA (vite preview :4173 against the real backend
 * :8000). Scene load + investigation reads are frozen-API only — ZERO model
 * calls, so this is not a "second generation" (Phase 19 §17 is respected).
 *
 * Assertions (light, per the acceptance instruction):
 *   - the scene canvas renders and reports ready;
 *   - the kitchen knife world object is present in the on-page object list
 *     AND rendered (live-renderer model probe, catalog object);
 *   - NO technical asset ids in the DOM: zero "proc.", zero "PROP_", zero
 *     "CASE-" as visible text;
 *   - 0 page errors / 0 console errors / 0 failed requests / 0 external
 *     traffic / 0 truth-key leaks.
 */

const BACKEND_BASE = "http://localhost:8000";
const CREDS_PATH = path.join(__dirname, "artifacts", "qa-p19real2-easy-creds.json");

test("P19B-real2 Easy scene: loads from the real published payload, zero technical ids in the DOM", async ({
  page,
}) => {
  const creds = JSON.parse(fs.readFileSync(CREDS_PATH, "utf-8")) as {
    caseId: string;
    playthroughId: string;
    playthroughToken: string;
  };

  const leak = installLeakListener(page);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  const externalTraffic: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });
  page.on("requestfailed", (req) => failedRequests.push(`REQFAIL ${req.method()} ${req.url()}`));
  page.on("request", (req) => {
    const rawUrl = req.url();
    if (typeof rawUrl === "string" && !/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\//.test(rawUrl)) {
      externalTraffic.push(`${req.method()} ${rawUrl}`);
    }
  });
  page.on("response", (res) => {
    if (res.status() >= 400) failedRequests.push(`HTTP ${res.status()} ${res.url()}`);
  });

  await seedPlaythroughCredentials(page, {
    playthroughId: creds.playthroughId,
    playthroughToken: creds.playthroughToken,
  });
  await page.goto("/scene", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("scene-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);

  // The kitchen knife is a CATALOG world object: the documented label
  // contract (Phase 13/18B) renders the human server name "Kitchen knife"
  // (never the raw objectId) — the objectId form only appears for label-null
  // procedural objects. Assert the HUMAN label, and that no raw technical id
  // form is anywhere in the list.
  const listText = (await page.getByTestId("scene-objects").innerText()).toLowerCase();
  expect(listText, "kitchen knife listed as an object in the scene").toContain("kitchen knife");
  expect(listText, "catalog object list never shows the raw objectId form").not.toContain("kitchen_knife");

  // Live-renderer probe: the catalog knife resolves and renders.
  const probe = await page.evaluate(() => {
    const dbg = (window as any).__pdDebugScene;
    if (!dbg) return null;
    const model = dbg.model;
    const obj = model.worldObjects.find((o: any) => o.objectId === "kitchen_knife");
    const scene = dbg.scene;
    const root = scene.getNodeByName("pd_obj_kitchen_knife");
    return {
      present: obj !== undefined,
      inModelRoot: !!root,
      objectId: obj?.objectId,
      assetId: obj?.assetId,
      label: obj?.label,
      unknownAsset: obj?.unknownAsset,
      interactionWorks: obj?.interactionWorks,
      affordances: obj?.affordances,
    };
  });
  const dbg = (await page.evaluate(() => Boolean((window as any).__pdDebugScene)));
  if (dbg) {
    expect(probe, "live debug scene probe").not.toBeNull();
    expect(probe!.present, "kitchen_knife present in the live scene model").toBe(true);
    expect(probe!.inModelRoot, "kitchen_knife has a live pd_obj_ root").toBe(true);
    expect(probe!.assetId, "render asset is the catalog knife").toBe("PROP_KITCHEN_KNIFE_01");
    expect(probe!.unknownAsset, "not an unknown asset").toBe(false);
  }

  // DOM hygiene: no technical/raw ids as visible text anywhere.
  const bodyText = await page.evaluate(() => document.body?.innerText ?? "");
  expect(bodyText, "no proc.* in the scene DOM").not.toContain("proc.");
  expect(bodyText, "no PROP_ render ids in the scene DOM").not.toContain("PROP_");
  expect(bodyText, "no CASE- ids in the scene DOM").not.toContain("CASE-");
  expect(bodyText, "no GA- attempt ids in the scene DOM").not.toContain("GA-");
  expect(bodyText, "no GEN- ids in the scene DOM").not.toContain("GEN-");

  await page.screenshot({
    path: "artifacts/screenshots/qa-p19real2-easy-scene.png",
    fullPage: false,
  });

  console.log("P19B_REAL2_EASY", JSON.stringify({
    sceneReady: true,
    kitchenKnifeListed: true,
    debugProbe: dbg ? probe : "debug seam off, skipped (probe not required)",
    leakScanned: leak.scanned,
    leakMatched: leak.matched.length,
    pageErrors,
    consoleErrors,
    failedRequests,
    externalTraffic,
  }));

  expect(leak.matched, `no forbidden truth keys in ${leak.scanned} API responses`).toEqual([]);
  expect(leak.scanned, "bootstrap responses must have been scanned").toBeGreaterThan(0);
  expect(pageErrors, "no uncaught page errors").toEqual([]);
  expect(consoleErrors, "no console errors").toEqual([]);
  expect(failedRequests, "no failed requests / failed resources").toEqual([]);
  expect(externalTraffic, "no external traffic").toEqual([]);
});