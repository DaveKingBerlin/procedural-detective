import { defineConfig } from "@playwright/test";

/**
 * Phase 47 QA — SPA browser acceptance config.
 *
 * Topology: a SINGLE-ORIGIN dev/product server (FastAPI serving the built SPA
 * from frontend/dist via STATIC_DIR) on http://127.0.0.1:8000 with
 * GENERATION_PROVIDER=fake. The browser loads the real production bundle and
 * calls the real API same-origin — no mocks. Servers are started OUTSIDE
 * Playwright via tools/process_guard (process-lifecycle policy).
 */
export default defineConfig({
  testDir: "./p47",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  use: {
    baseURL: "http://127.0.0.1:8000",
    headless: true,
    viewport: { width: 1280, height: 800 },
    screenshot: "off",
    trace: "off",
  },
  outputDir: "artifacts/test-results-p47",
});
