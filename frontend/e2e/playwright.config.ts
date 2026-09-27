import { defineConfig } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Phase 24 §16 — repository-owned BROWSER SMOKE harness (frontend track).
 *
 * This is the FRONTEND-OWNED Playwright Chromium harness that QAs the FULL
 * §16 flow against the DOCKER-SERVED single origin (Caddy -> SPA + /api/v1,
 * container port 8000, GENERATION_PROVIDER=fake for deterministic CI). It is
 * deliberately separate from the QA-owned `e2e/` suite (which drives a
 * dev-time backend + vite preview) so Phase 24 stack smoke stays
 * independently runnable with `docker compose up --build` + `npm run e2e`.
 *
 *   - `baseURL` defaults to the Docker-served origin `http://localhost:8000`
 *     (docker-compose.yml exposes 8000) and is overridable with
 *     `E2E_BASE_URL` for a deployed/dev origin.
 *   - The harness uses the project-local Playwright Chromium
 *     (.rad/policies/evidence.md: project-local Playwright Chromium is the
 *     authoritative browser validator). Install with
 *     `npx playwright install chromium` (first run only; the GitLab
 *     validate/test jobs document this under docs/CI.md).
 *   - NO webServer is configured: the target must be a RUNNING stack
 *     operator-started (docker compose up). The harness only drives the
 *     browser and collects evidence.
 *   - Transient output (JSON report, failure screenshots, test results)
 *     lands under repo `e2e/artifacts/browser/` — the Phase 24 suggested
 *     artifacts location, already covered by the existing `e2e/artifacts/`
 *     ignore rule (evidence policy: transient output stays ignored).
 *   - Timeout envelope: generation may use the full P-02 backend deadline
 *     (300s) so the test-level timeout is 360s; expect polling is 30s.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "..", "..");
const ARTIFACTS = resolve(REPO_ROOT, "e2e", "artifacts", "browser");

/** The Docker-served origin; override for a deployed/dev origin. */
const E2E_BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:8000";

export default defineConfig({
  testDir: ".",
  timeout: 360_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    [
      "json",
      { outputFile: resolve(ARTIFACTS, "browser-smoke.json") },
    ],
  ],
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  use: {
    baseURL: E2E_BASE_URL,
    headless: true,
    viewport: { width: 1280, height: 800 },
    screenshot: "only-on-failure",
    trace: "off",
    video: "off",
  },
  outputDir: resolve(ARTIFACTS, "test-results"),
});