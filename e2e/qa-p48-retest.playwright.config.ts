import { defineConfig } from "@playwright/test";

/**
 * PHASE 48 QA RETEST — QA-owned Playwright config (evidence only; NOT product
 * code). Same browser/backend topology as e2e/playwright.config.ts (baseURL
 * :4173 = vite preview with the QA reverse-proxy; backend :8000 started OUTSIDE
 * Playwright) EXCEPT `testDir` points at the QA wrapper dir, so the collector
 * never walks the pre-existing ACL-locked `e2e/artifacts/**` trees (an
 * unrelated prior-run artifact dir) that make `testDir: "."` raise EPERM.
 * The wrapper imports the REAL committed spec verbatim.
 */
export default defineConfig({
  testDir: "qa-p48-retest",
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
  outputDir: "C:/Users/Fujitsu/AppData/Local/Temp/opencode/qa-retest/pw-p48-retest",
});
