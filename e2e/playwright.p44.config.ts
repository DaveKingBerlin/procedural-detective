import { defineConfig } from "@playwright/test";

/**
 * Phase 44 QA — production-topology (HTTPS/Caddy) acceptance config.
 *
 * The canonical e2e config targets vite preview :4173 + a local backend :8000.
 * This config drives the REAL running production stack instead:
 *   Caddy TLS edge (docker-compose.prod.yml + lan + debug) -> uvicorn backend
 *   at https://enshrouded-server (internal CA; local-only ignoreHTTPSErrors).
 *
 * testDir is a dedicated subdirectory so the (unreadable, prior-run) artifacts
 * tree is never scanned.
 */
export default defineConfig({
  testDir: "./p44",
  timeout: 180_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  outputDir: "artifacts/test-results-p44",
  use: {
    baseURL: "https://enshrouded-server",
    ignoreHTTPSErrors: true,
    headless: true,
    viewport: { width: 1280, height: 800 },
    screenshot: "off",
    trace: "off",
  },
});
