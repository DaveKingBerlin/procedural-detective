// QA Phase 49 independent browser probe against the PRODUCTION build.
// Serves frontend/dist with SPA fallback, then drives real Chromium.
const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const DIST = path.resolve(__dirname, "..", "..", "..", "frontend", "dist");
const PORT = 4199;
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
  ".woff2": "font/woff2", ".map": "application/json",
};

let manifestShould404 = false;
const server = http.createServer((req, res) => {
  let urlPath = decodeURIComponent(req.url.split("?")[0]);
  if (urlPath === "/releases/release-manifest.json" && manifestShould404) {
    res.writeHead(404); res.end("not found"); return;
  }
  let filePath = path.join(DIST, urlPath);
  if (!filePath.startsWith(DIST)) { res.writeHead(403); res.end(); return; }
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    filePath = path.join(DIST, "index.html"); // SPA fallback
  }
  const ext = path.extname(filePath);
  res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
  fs.createReadStream(filePath).pipe(res);
});

const results = [];
function check(name, cond, detail) {
  results.push({ name, pass: !!cond, detail: detail || "" });
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}${detail ? "  :: " + detail : ""}`);
}

(async () => {
  await new Promise((r) => server.listen(PORT, "127.0.0.1", r));
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  const requests = [];
  page.on("request", (r) => requests.push(r.url()));
  let downloadCount = 0;
  page.on("download", () => { downloadCount += 1; });

  // ---- 1. manifest present -------------------------------------------------
  await page.goto(`http://127.0.0.1:${PORT}/new`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="release-download-section"]', { timeout: 15000 });

  const winLink = await page.$('[data-testid="release-download-windows"]');
  const linuxLink = await page.$('[data-testid="release-download-linux"]');
  check("section renders", true);
  check("Windows download visible", winLink !== null);
  check("Linux download visible", linuxLink !== null);

  const winHref = winLink ? await winLink.getAttribute("href") : null;
  const linuxHref = linuxLink ? await linuxLink.getAttribute("href") : null;
  check("Windows href versioned+canonical", winHref && winHref.includes("/v1.0.0/pd-ollama-bridge-v1.0.0-windows-x64.zip"), winHref);
  check("Linux href versioned+canonical", linuxHref && linuxHref.includes("/v1.0.0/pd-ollama-bridge-v1.0.0-linux-x64.tar.gz"), linuxHref);

  const versionText = await page.textContent('[data-testid="release-download-version"]').catch(() => null);
  check("version displayed from manifest", versionText && versionText.trim() === "Version 1.0.0", versionText);

  const quick = await page.$('[data-testid="release-quick-start"]');
  const sums = await page.$('[data-testid="release-checksums"]');
  const source = await page.$('[data-testid="release-source"]');
  check("Quick Start link visible", quick !== null);
  check("Checksums link visible", sums !== null);
  check("Source link visible", source !== null);

  // OS detection must not hide the other platform.
  check("OS detection did not hide a platform (both present)", winLink !== null && linuxLink !== null);

  // No auto-download / no artifact fetch.
  const artifactRequests = requests.filter((u) => u.includes(".zip") || u.includes(".tar.gz") || u.includes("/releases/download"));
  check("no artifact URL fetched on load", artifactRequests.length === 0, artifactRequests.join(","));
  check("no download event on mount", downloadCount === 0);
  const manifestReqs = requests.filter((u) => u.includes("/releases/release-manifest.json"));
  check("manifest fetched same-origin", manifestReqs.length >= 1, manifestReqs.join(","));

  // ---- 2. manifest missing -> graceful unavailable -------------------------
  manifestShould404 = true;
  const page2 = await context.newPage();
  await page2.goto(`http://127.0.0.1:${PORT}/new`, { waitUntil: "networkidle" });
  await page2.waitForSelector('[data-testid="release-download-unavailable"]', { timeout: 15000 });
  const unavailWin = await page2.$('[data-testid="release-download-windows"]');
  const unavailQuick = await page2.$('[data-testid="release-quick-start"]');
  const unavailSource = await page2.$('[data-testid="release-source"]');
  check("missing manifest -> unavailable state", true);
  check("missing manifest -> no download buttons", unavailWin === null);
  check("missing manifest -> Quick Start still available", unavailQuick !== null);
  check("missing manifest -> Source still available", unavailSource !== null);
  const pageHeading = await page2.$('h1, h2');
  check("rest of page still renders (heading present)", pageHeading !== null);

  await browser.close();
  server.close();

  const failed = results.filter((r) => !r.pass);
  console.log(`\nSUMMARY: ${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length === 0 ? 0 : 1);
})().catch((e) => { console.error("PROBE ERROR:", e); server.close(); process.exit(2); });
