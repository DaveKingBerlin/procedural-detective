import { execSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Phase 24 §45/§46 — deny-list module + scanner CLI hygiene (DEF-008).
 *
 * DEF-008 (QA-verified): the OLD built-bundle half of
 * phase24PrivateEndpointGuard.test.ts was gated behind `it.runIf(hasBuild)`.
 * On a FRESH GitLab clean checkout the `test`-stage job runs BEFORE the
 * `build`-stage job, `frontend/dist` is absent (gitignored), so the bundle
 * scan silently SKIPPED — a shipped bundle could carry a private endpoint
 * undetected in CI.
 *
 * The FIX is architectural, not a test toggle: `npm run build` now chains
 * `npm run scan:bundle` (frontend/scripts/scan-private-endpoints.mjs) right
 * after `vite build`, so EVERY production build — local dev, the GitLab
 * `build`-stage job, and the Dockerfile stage-1 image build — scans the exact
 * artifact it just produced. A scan that is part of the build command can
 * never skip. These tests pin that architecture:
 *
 *   1. the `scan:bundle` npm script exists and IS chained into `build`;
 *   2. the deny-list module exports the audited patterns (pure-pattern
 *      checks — hostile forms match, benign neighbors do not);
 *   3. the scanner module imports cleanly and `runScan` is deterministic
 *      (hostile dir -> violations, clean dir -> none, missing same-origin
 *      root -> violation, absent dir -> not present);
 *   4. the REAL CLI process exits non-zero on a hostile scratch dir and zero
 *      on a clean scratch dir, and exits 2 on a missing target.
 *
 * All fixture dirs are OS temp scratch dirs (cleaned in `finally`) — the
 * tests NEVER touch `frontend/dist` state beyond what `npm run build`'s hook
 * produces. Hostile fixtures are built from parts so this file itself never
 * carries a private-endpoint literal upright (belt and braces: even though
 * `*.test.ts` files are excluded from every release scan).
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(HERE, "..", "..");
const SCRIPTS_ROOT = resolve(FRONTEND_ROOT, "scripts");
const SCANNER_SCRIPT = join(SCRIPTS_ROOT, "scan-private-endpoints.mjs");
const DENY_LIST_SCRIPT = join(SCRIPTS_ROOT, "privateEndpointDenyList.mjs");
const PACKAGE_JSON = join(FRONTEND_ROOT, "package.json");

/** Build a denied port/postal literal from parts (never upright in this file). */
const PORT = ["1", "1", "4", "3", "4"].join("");
const PORT_COLON = `:${PORT}`;
const OLLAMA_ENDPOINT = `localhost${PORT_COLON}`;
const HOSTILE_HTTP = `http://${OLLAMA_ENDPOINT}/v1/chat`;

/** Private-IP literals built from octet parts. */
const LOOPBACK_IP = ["127", "0", "0", "1"].join(".");
const RFC1918_A = ["10", "0", "0", "1"].join(".");
const RFC1918_B = ["172", "16", "0", "1"].join(".");
const RFC1918_C = ["192", "168", "0", "1"].join(".");
const DOCKER_HOST = ["host", "docker", "internal"].join(".");

function makeTmpDir(): string {
  return mkdtempSync(join(tmpdir(), "pd-phase24-scan-XXXXXX"));
}

/** Write a text file into a scratch dir, creating parents as needed. */
function writeScratch(root: string, rel: string, content: string | Uint8Array): void {
  const target = join(root, ...rel.split("/"));
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, content);
}

/** Invoke the REAL scanner CLI (child Node process) and return {status, out}.
 *  Uses the quoted-string command form: Node's execSync spawns directly (no
 *  shell) and `parseCommandLine` honors double-quoted args on both OSes, so
 *  paths with spaces stay safe without shell metacharacter injection. */
function runCli(targetDir: string): { status: number; out: string } {
  const nodeBin = process.argv[0];
  const command = `"${nodeBin}" "${SCANNER_SCRIPT}" "${targetDir}"`;
  try {
    const out = execSync(command, { encoding: "utf8" });
    return { status: 0, out: String(out) };
  } catch (error) {
    const e = error as { status?: number; stdout?: unknown };
    return { status: e.status ?? 1, out: e.stdout ? String(e.stdout) : "" };
  }
}

describe("Phase 24 §46 — scan:bundle is a deterministic part of the frontend build (DEF-008)", () => {
  it("exposes an npm script `scan:bundle` pointing at the hermetic scanner", () => {
    const pkg = JSON.parse(readFileSync(PACKAGE_JSON, "utf8")) as {
      scripts?: Record<string, string>;
    };
    expect(pkg.scripts?.["scan:bundle"]).toBe("node scripts/scan-private-endpoints.mjs");
  });

  it("chains the bundle scan into `npm run build` (never skippable on a fresh checkout)", () => {
    const pkg = JSON.parse(readFileSync(PACKAGE_JSON, "utf8")) as {
      scripts?: Record<string, string>;
    };
    const build = pkg.scripts?.["build"] ?? "";
    expect(build).toContain("vite build");
    expect(build).toContain("npm run scan:bundle");
    // typecheck stays a separate, explicitly-invoked step.
    expect(pkg.scripts?.["typecheck"]).toBe("tsc --noEmit");
  });
});

describe("Phase 24 §46 — deny-list module (pure-pattern checks)", () => {
  it("exports the audited local-Ollama endpoint assembled from parts", async () => {
    const deny = await import(pathToFileURL(DENY_LIST_SCRIPT).href);
    expect(deny.LOCALHOST_OLLAMA).toBe(OLLAMA_ENDPOINT);
    expect(deny.SAME_ORIGIN_ROOT).toBe("/api/v1");
  });

  it("matches EVERY hostile form the Phase 24 §45 contract forbids", async () => {
    const deny = await import(pathToFileURL(DENY_LIST_SCRIPT).href);
    const hostile: string[] = [
      OLLAMA_ENDPOINT,                              // localhost:11434
      HOSTILE_HTTP,                                 // http://localhost:11434/...
      `http://localhost${PORT_COLON}`,              // any http://localhost:<port>
      LOOPBACK_IP,                                  // 127.0.0.1
      RFC1918_A, RFC1918_B, RFC1918_C,              // private LAN IPv4
      DOCKER_HOST,                                  // host.docker.internal
    ];
    for (const sample of hostile) {
      expect(deny.DENY_PATTERNS.some((re: RegExp) => re.test(sample)), sample).toBe(true);
    }
  });

  it("does NOT match benign neighbors (public hosts / other ports / no drift)", async () => {
    const deny = await import(pathToFileURL(DENY_LIST_SCRIPT).href);
    const benign: string[] = [
      "cdn.babylonjs.com",
      "https://example.com/api/v1",
      "30001",                                       // not the Ollama port
      ["192", "168", "1"].join("."),                 // short 192.168.x — not a full IPv4
      "myhost.docker.internal",                      // NOT the alias (no left boundary)
      ["host", "docker", "internalx"].join("."),     // suffix glued — not the alias
    ];
    // NOTE: a port-carrying localhost HTTP URL is intentionally NOT benign —
    // it IS the audited local-endpoint evidence (the production default must
    // stay same-origin); that's pinned by the explicit assertion below.
    for (const sample of benign) {
      expect(deny.DENY_PATTERNS.some((re: RegExp) => re.test(sample)), sample).toBe(false);
    }
    expect(
      deny.DENY_PATTERNS.some((re: RegExp) => re.test(`http://localhost:${"8080"}`)),
    ).toBe(true);
  });
});

describe("Phase 24 §46 — scan script (hermetic CLI, tmp scratch dirs)", () => {
  it("exists and parses as an importable module with a pure runScan", async () => {
    const scanner = await import(pathToFileURL(SCANNER_SCRIPT).href);
    expect(typeof scanner.runScan).toBe("function");
    expect(typeof scanner.collectFiles).toBe("function");
  });

  it("runScan reports violations on a hostile scratch dir and is clean on a clean one", async () => {
    const scanner = await import(pathToFileURL(SCANNER_SCRIPT).href);
    const hostileDir = makeTmpDir();
    const cleanDir = makeTmpDir();
    try {
      writeScratch(hostileDir, "assets/index.js", `export const bad = "${HOSTILE_HTTP}";`);
      writeScratch(hostileDir, "assets/root.js", `const apiRoot = "/api/v1";`);
      const hostile = scanner.runScan(hostileDir);
      expect(hostile.present).toBe(true);
      expect(hostile.violations.length).toBeGreaterThan(0);

      writeScratch(cleanDir, "assets/index.js", `const apiRoot = "/api/v1";`);
      const clean = scanner.runScan(cleanDir);
      expect(clean.present).toBe(true);
      expect(clean.violations).toEqual([]);
      expect(clean.foundSameOriginRoot).toBe(true);
    } finally {
      rmSync(hostileDir, { recursive: true, force: true });
      rmSync(cleanDir, { recursive: true, force: true });
    }
  });

  it("runScan fails when the same-origin /api/v1 root is missing (no private origin import)", async () => {
    const scanner = await import(pathToFileURL(SCANNER_SCRIPT).href);
    const dir = makeTmpDir();
    try {
      writeScratch(dir, "index.html", `<!doctype html><title>no api root</title>`);
      const result = scanner.runScan(dir);
      expect(result.present).toBe(true);
      expect(result.violations.some((v: string) => v.includes("/api/v1"))).toBe(true);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("skips binary assets (NUL bytes) and still enforces the text deny set", async () => {
    const scanner = await import(pathToFileURL(SCANNER_SCRIPT).href);
    const dir = makeTmpDir();
    try {
      writeScratch(dir, "assets/root.js", `const apiRoot = "/api/v1";`);
      // A fake font: NUL-prefixed binary that happens to carry hostile bytes.
      const binary = Buffer.concat([
        Buffer.from([0x00, 0x01, 0x02]),
        Buffer.from(HOSTILE_HTTP, "utf8"),
      ]);
      writeScratch(dir, "assets/fake.woff", binary);
      const result = scanner.runScan(dir);
      expect(result.present).toBe(true);
      expect(result.violations).toEqual([]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("runScan treats an absent target as NOT PRESENT (fail-closed, never a silent pass)", async () => {
    const scanner = await import(pathToFileURL(SCANNER_SCRIPT).href);
    const missing = join(tmpdir(), `pd-phase24-does-not-exist-${Date.now()}`);
    const result = scanner.runScan(missing);
    expect(result.present).toBe(false);
  });

  it("the REAL CLI exits NON-ZERO when a hostile literal is injected into a scratch dir", async () => {
    const dir = makeTmpDir();
    try {
      writeScratch(dir, "assets/index.js", `export const bad = "${HOSTILE_HTTP}";`);
      writeScratch(dir, "assets/root.js", `const apiRoot = "/api/v1";`);
      const run = runCli(dir);
      expect(run.status).toBe(1); // EXIT_VIOLATION
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("the REAL CLI exits ZERO on a clean scratch dir embedding /api/v1", async () => {
    const dir = makeTmpDir();
    try {
      writeScratch(dir, "assets/root.js", `const apiRoot = "/api/v1";`);
      const run = runCli(dir);
      expect(run.status).toBe(0); // EXIT_CLEAN
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("the REAL CLI exits 2 when the target directory does not exist", async () => {
    const missing = join(tmpdir(), `pd-phase24-missing-${Date.now()}`);
    const run = runCli(missing);
    expect(run.status).toBe(2); // EXIT_TARGET_MISSING — a fresh checkout without
    // dist can never masquerade as a passed scan.
  });
});