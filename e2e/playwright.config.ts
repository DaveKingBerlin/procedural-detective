import { defineConfig } from "@playwright/test";

/**
 * QA-owned Playwright configuration (evidenced in .rad/policies/evidence.md:
 * project-local Playwright Chromium is the authoritative browser validator).
 *
 * Servers (backend :8000, vite preview :4173) are started OUTSIDE Playwright by
 * QA via tools/process_guard (process-lifecycle policy). This config only
 * drives the browser and keeps transient output under e2e/artifacts/ (ignored).
 */
export default defineConfig({
  testDir: ".",
  timeout: 90_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  use: {
    baseURL: "http://localhost:4173",
    headless: true,
    viewport: { width: 1280, height: 800 },
    screenshot: "off",
    trace: "off",
  },
  outputDir: "artifacts/test-results",
});