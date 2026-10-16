import { defineConfig } from "@playwright/test";

/**
 * Phase 45 QA — production-topology (HTTPS/Caddy) acceptance config.
 *
 * Drives the REAL running production stack (docker-compose.prod.yml + lan +
 * debug) through the Caddy TLS edge at https://enshrouded-server (internal CA;
 * local-only ignoreHTTPSErrors). Mirrors the Phase 44 config shape.
 */
export default defineConfig({
  testDir: "./p45",
  timeout: 240_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  outputDir: "artifacts/test-results-p45",
  use: {
    baseURL: "https://enshrouded-server",
    ignoreHTTPSErrors: true,
    headless: true,
    viewport: { width: 1280, height: 800 },
    screenshot: "off",
    trace: "off",
  },
});
