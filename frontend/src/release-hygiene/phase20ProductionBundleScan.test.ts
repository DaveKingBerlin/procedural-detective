import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Phase 20 §12.2 — production BUNDLE scan (PD-SEC-04).
 *
 * Scans the BUILT frontend (`frontend/dist` after `npm run build`) and
 * asserts the OLD production default `http://localhost:8000` and every
 * private/loopback/LAN host + provider-endpoint literal is absent. The
 * production SPA reaches its API SAME-ORIGIN via the relative `/api/v1`
 * root; `http://localhost:8000` may exist only as an explicit build-time
 * `VITE_API_BASE_URL` override, never as a shipped default.
 *
 * The scan targets the audited invariants only:
 *   - the exact old default `http://localhost:8000`;
 *   - any `local`-host HTTP literal with a port (`http://localhost:<port>`);
 *   - private/loopback/LAN IPv4 literals (127./10./192.168./172.16-31.);
 *   - the docker-host alias `host.docker.internal`;
 *   - provider-endpoint evidence (the Ollama port `11434`).
 *
 * Vendor documentation URLs (`cdn.babylonjs.com`, `react.dev`, `www.w3.org`,
 * `reactrouter.com`, ...) and Babylon's internal `new URL("http://localhost")`
 * URL-normalization fallback are third-party strings that are NOT the API
 * base and NOT an endpoint the app ever calls — they are outside this scan's
 * scope (the source-level providerUrlLeakScan keeps OUR source clean).
 *
 * Runs under `npm test` (vitest). The build is produced by CI/`npm run build`
 * BEFORE the test suite; when `dist/` is absent the scan SKIPS cleanly (a
 * source-only checkout never fails here).
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST_ROOT = resolve(HERE, "..", "..", "dist");

/** The exact audited production default that must never ship. */
const LOCALHOST_DEFAULT = "http://localhost:8000";

/** The same-origin relative API root the built client must embed. */
const SAME_ORIGIN_ROOT = "/api/v1";

/** Private / loopback / LAN IPv4 prefixes plus the docker host alias. */
const PRIVATE_HOST: readonly RegExp[] = [
  /\b127\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/,
  /\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/,
  /\b192\.168\.\d{1,3}\.\d{1,3}\b/,
  /\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b/,
  /\bhost\.docker\.internal\b/i,
];

/** Provider-endpoint evidence: the Ollama service port used by live providers. */
const PROVIDER_URL: readonly RegExp[] = [
  /\b11434\b/,
];

function builtFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      builtFiles(full, out);
      continue;
    }
    out.push(full);
  }
  return out;
}

function describeHit(file: string, detail: string): string {
  return `${relative(DIST_ROOT, file).split(sep).join("/")} — ${detail}`;
}

describe("Phase 20 §12.2 — production bundle is SAME-ORIGIN (PD-SEC-04)", () => {
  const hasBuild = existsSync(DIST_ROOT);
  const files = hasBuild ? builtFiles(DIST_ROOT) : [];

  it.runIf(hasBuild)("scans the built bundle for the localhost production default", () => {
    const hits: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      if (source.includes(LOCALHOST_DEFAULT)) hits.push(describeHit(file, LOCALHOST_DEFAULT));
      // Any port-carrying local HTTP literal is a local API endpoint (the
      // bare no-port Babylon URL-normalization fallback is not).
      if (/http:\/\/localhost:\d+/i.test(source)) {
        const match = source.match(/http:\/\/localhost:\d+/i);
        hits.push(describeHit(file, match?.[0] ?? "http://localhost:<port>"));
      }
    }
    expect(hits, `local API endpoints in the production bundle:\n- ${hits.join("\n- ")}`).toEqual([]);
  });

  it.runIf(hasBuild)("embeds the same-origin relative API root (the built default)", () => {
    const joined = files.map((file) => readFileSync(file, "utf8")).join("\n");
    expect(joined).toContain(SAME_ORIGIN_ROOT);
  });

  it.runIf(hasBuild)("keeps private/loopback/LAN host literals out of the bundle", () => {
    const hits: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const re of PRIVATE_HOST) {
        const match = source.match(re);
        if (match) hits.push(describeHit(file, `${re.source} matched "${match[0]}"`));
      }
    }
    expect(hits, `private/loopback/LAN literals in the bundle:\n- ${hits.join("\n- ")}`).toEqual([]);
  });

  it.runIf(hasBuild)("keeps provider-endpoint literals out of the bundle", () => {
    const hits: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const re of PROVIDER_URL) {
        if (re.test(source)) hits.push(describeHit(file, `provider pattern ${re.source}`));
        re.lastIndex = 0;
      }
    }
    expect(hits, `provider endpoint literals in the bundle:\n- ${hits.join("\n- ")}`).toEqual([]);
  });
});