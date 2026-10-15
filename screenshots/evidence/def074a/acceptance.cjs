/*
 * QA Phase43-Fix1 / DEF-074A production-topology acceptance (HTTPS/Caddy).
 *
 * Drives the Dev-Box LAN TLS origin (Caddy -> backend -> SPA), NOT the dev
 * vite server. Independent of the developer's tests.
 *
 *   node acceptance.cjs
 *
 * Exit 0 == every acceptance assertion passed.
 */
const fs = require("fs");
const path = require("path");
const PW = "C:/Users/Fujitsu/coding/procedural-detective/frontend/node_modules/playwright";
const { chromium, request } = require(PW);

const BASE = process.env.PD_BASE || "https://enshrouded-server";
const OUT = process.env.PD_OUT || "C:/Users/Fujitsu/coding/procedural-detective/screenshots/evidence/def074a";
const ARTIFACTS = "C:/Users/Fujitsu/coding/procedural-detective/e2e/artifacts";
const AUTHORIZATION_ARTIFACTS = "C:/Users/Fujitsu/coding/procedural-detective/e2e/artifacts/def074a";

const report = {
  startedAt: new Date().toISOString(),
  base: BASE,
  backendTreeCommit: null,
  steps: {},
  assets: [],
  assetFailures: [],
  healthSamples: [],
  consoleErrors: [],
  requestFailures: [],
  assertions: [],
};

function assert(cond, label) {
  report.assertions.push({ label, ok: !!cond });
  console.log(`  [${cond ? "OK  " : "FAIL"}] ${label}`);
}

async function timed(promiseFactory) {
  const t0 = Date.now();
  const value = await promiseFactory();
  return { value, ms: Date.now() - t0 };
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(AUTHORIZATION_ARTIFACTS, { recursive: true });

  const api = await request.newContext({
    baseURL: BASE,
    ignoreHTTPSErrors: true,
    timeout: 60000,
  });

  // --- 0. Precondition: /health healthy and fast -----------------------------
  {
    const t0 = Date.now();
    const res = await api.get("/api/v1/health");
    const ms = Date.now() - t0;
    report.steps.preflightHealth = { status: res.status(), ms };
    console.log(`preflight /health -> ${res.status()} in ${ms}ms`);
    assert(res.status() === 200, "preflight /health is 200");
  }

  // --- 1. Build an investigation through the public API ----------------------
  const sess = await api.post("/api/v1/sessions/anonymous");
  report.steps.session = { status: sess.status() };
  assert(sess.status() === 201, "anonymous session 201");
  const sessionToken = (await sess.json()).anonymousSessionToken;

  const caseRes = await api.post("/api/v1/cases", {
    headers: { Authorization: `Bearer ${sessionToken}` },
    data: { prompt: "Victim: sarah_miller\nMurderer: thomas_reed\n", difficulty: "medium" },
  });
  const created = await caseRes.json();
  report.steps.case = { status: caseRes.status(), caseId: created.caseId, caseStatus: created.status };
  assert(caseRes.status() === 201, "create case 201");
  assert(created.status === "PUBLISHED", `fake provider publishes (status=${created.status})`);

  const ptRes = await api.post(
    `/api/v1/cases/${created.caseId}/versions/1/playthroughs`,
    { headers: { Authorization: `Bearer ${created.creatorAccessToken}` } },
  );
  const pt = await ptRes.json();
  report.steps.playthrough = { status: ptRes.status(), playthroughId: pt.playthroughId };
  assert(ptRes.status() === 201, "create playthrough 201");

  const auth = { Authorization: `Bearer ${pt.playthroughAccessToken}` };

  // --- Concurrent /health probe ---------------------------------------------
  let stopHealth = false;
  const healthTask = (async () => {
    while (!stopHealth) {
      const t0 = Date.now();
      try {
        const r = await api.get("/api/v1/health");
        report.healthSamples.push({ status: r.status(), ms: Date.now() - t0 });
      } catch (e) {
        report.healthSamples.push({ status: 0, ms: Date.now() - t0, error: String(e) });
      }
      await new Promise((r) => setTimeout(r, 150));
    }
  })();

  // --- 2. GET the investigation ---------------------------------------------
  const inv = await timed(() => api.get(`/api/v1/playthroughs/${pt.playthroughId}/investigation`, { headers: auth }));
  const invBody = await inv.value.json();
  report.steps.investigation = { status: inv.value.status(), ms: inv.ms };
  assert(inv.value.status() === 200, `GET investigation 200 (latency=${inv.ms}ms)`);
  const objects = invBody.scene ? invBody.scene.worldObjects || [] : [];
  const witnesses = invBody.witnesses || [];
  console.log(`  objects=${objects.length} witnesses=${witnesses.length}`);

  // Decorative object => published interaction "" => the phase-mandated {"interaction":""}.
  const decorative = objects.find((o) => o.interaction === "" && !o.evidenceId) || objects.find((o) => o.interaction === "");

  // --- 3. POST object interaction {"interaction":""} --------------------------
  let interactionStatus = null;
  if (decorative) {
    const r = await timed(() => api.post(
      `/api/v1/playthroughs/${pt.playthroughId}/objects/${encodeURIComponent(decorative.objectId)}/interact`,
      { headers: auth, data: { interaction: "" } },
    ));
    const body = await r.value.json();
    interactionStatus = r.value.status();
    report.steps.interaction = { status: interactionStatus, ms: r.ms, objectId: decorative.objectId, body };
    assert(interactionStatus === 200, `POST object interaction {"interaction":""} 200 (latency=${r.ms}ms)`);
  } else {
    report.steps.interaction = { status: null, reason: "no decorative object in investigation" };
    assert(false, "found a decorative object for the mandated empty interaction");
  }

  // --- 4. Ask one witness question -------------------------------------------
  let witnessStatus = null;
  if (witnesses.length) {
    const wid = witnesses[0].witnessId;
    const r = await timed(() => api.post(
      `/api/v1/playthroughs/${pt.playthroughId}/witnesses/${encodeURIComponent(wid)}/interview`,
      { headers: auth, data: { questionType: "OBSERVATION" } },
    ));
    const body = await r.value.json();
    witnessStatus = r.value.status();
    report.steps.witness = { status: witnessStatus, ms: r.ms, witnessId: wid, body };
    assert(witnessStatus === 200, `witness interview 200 (latency=${r.ms}ms)`);
  } else {
    report.steps.witness = { status: null, reason: "no witnesses in investigation" };
    assert(false, "found a witness to interview");
  }

  // --- 5. Cold browser context loads the SPA + lazy /assets -------------------
  const browser = await chromium.launch();
  const context = await browser.newContext({ baseURL: BASE, ignoreHTTPSErrors: true, viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error") report.consoleErrors.push(m.text()); });
  page.on("requestfailed", (r) => report.requestFailures.push({ url: r.url(), failure: r.failure() && r.failure().errorText }));
  page.on("response", (res) => {
    const u = res.url();
    if (u.includes("/assets/")) report.assets.push({ url: u, status: res.status() });
  });

  // Cold load of the shell (triggers index chunk + css FileResponses).
  await page.goto("/", { waitUntil: "load" });
  // Seed the credential contract keys, then render the 3D scene (heavy assets).
  await page.evaluate(
    ([pid, token]) => {
      localStorage.setItem("pd_playthrough_id", pid);
      localStorage.setItem("pd_playthrough_token", token);
    },
    [pt.playthroughId, pt.playthroughAccessToken],
  );
  await page.goto("/scene", { waitUntil: "load" });
  try {
    await page.waitForLoadState("networkidle", { timeout: 30000 });
  } catch (e) {
    report.steps.networkidle = "timeout";
  }
  await page.waitForTimeout(1500); // allow late lazy chunks to settle

  const assetUnique = [...new Map(report.assets.map((a) => [a.url, a])).values()];
  report.assetSummary = {
    totalResponses: report.assets.length,
    unique: assetUnique.length,
    nonOk: assetUnique.filter((a) => a.status !== 200),
  };
  assert(assetUnique.length > 0, `SPA requested static assets (unique=${assetUnique.length})`);
  assert(report.assetSummary.nonOk.length === 0, `all static assets HTTP 200 (nonOk=${report.assetSummary.nonOk.length})`);
  assert(report.requestFailures.length === 0, `no failed SPA requests (failed=${report.requestFailures.length})`);

  await page.screenshot({ path: path.join(OUT, "spa-scene.png"), fullPage: false });

  stopHealth = true;
  await healthTask;

  const healthOk = report.healthSamples.filter((s) => s.status === 200).length;
  const healthBad = report.healthSamples.filter((s) => s.status !== 200);
  const healthLat = report.healthSamples.map((s) => s.ms).sort((a, b) => a - b);
  report.healthSummary = {
    samples: report.healthSamples.length,
    ok: healthOk,
    bad: healthBad.length,
    minMs: healthLat[0],
    medianMs: healthLat[Math.floor(healthLat.length / 2)],
    maxMs: healthLat[healthLat.length - 1],
  };
  assert(report.healthSamples.length > 5, `concurrent /health probe ran (samples=${report.healthSamples.length})`);
  assert(healthBad.length === 0, `all concurrent /health samples 200 (bad=${healthBad.length})`);

  await context.close();
  await browser.close();
  await api.dispose();

  report.finishedAt = new Date().toISOString();
  const passed = report.assertions.every((a) => a.ok);
  report.result = passed ? "PASS" : "FAIL";

  fs.writeFileSync(path.join(OUT, "acceptance-evidence.json"), JSON.stringify(report, null, 2));
  try { fs.writeFileSync(path.join(AUTHORIZATION_ARTIFACTS, "acceptance-evidence.json"), JSON.stringify(report, null, 2)); } catch {}

  console.log("\n================ SUMMARY ================");
  console.log(JSON.stringify({
    interaction: report.steps.interaction && report.steps.interaction.status,
    witness: report.steps.witness && report.steps.witness.status,
    investigation: report.steps.investigation,
    assets: report.assetSummary,
    health: report.healthSummary,
    consoleErrors: report.consoleErrors.length,
    requestFailures: report.requestFailures.length,
    result: report.result,
  }, null, 2));
  console.log(`evidence: ${path.join(OUT, "acceptance-evidence.json")}`);
  process.exit(passed ? 0 : 1);
}

main().catch((e) => {
  console.error("FATAL", e);
  process.exit(2);
});