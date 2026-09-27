import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  LOCALHOST_OLLAMA,
  LOCALHOST_HTTP_PATTERNS,
  PRIVATE_HOST_PATTERNS,
  PROVIDER_PORT_PATTERNS,
  SAME_ORIGIN_ROOT,
} from "../../scripts/privateEndpointDenyList.mjs";

/**
 * Phase 24 §45/§46 — SAME-ORIGIN / PRIVATE-ENDPOINT GUARD (hermetic).
 *
 * Machine-checkable half of the Phase 24 browser-smoke guarantees, runnable
 * WITHOUT a browser or Docker (part of `npm test`, so it also runs in the
 * GitLab validate/test frontend jobs):
 *
 *   §45 — the browser must never call `localhost:11434`, a private Ollama
 *         host, or ANY private/loopback/LAN endpoint. The behavioral matrix
 *         (resolveApiBaseUrl/apiUrl against a hostile `VITE_API_BASE_URL`)
 *         lives in ../api/client.test.ts; THIS file proves the two static
 *         halves of the same contract:
 *           1. the COMMITTED PRODUCTION SOURCE (frontend/src, non-test) is
 *              free of private/Ollama endpoint literals — the ship contains
 *              nothing a hostile actor could eyeball or a scanner could flag
 *              (the generic host scan is ../providerUrlLeakScan.test.ts; the
 *              exact bridge literals are ../phase22BridgeScan.test.ts; this
 *              scan re-pins the Phase 24 §46 GAte on the same sources);
 *           2. the BUILT BUNDLE (frontend/dist AFTER `npm run build`) is free
 *              of the same literals AND still embeds the same-origin /api/v1
 *              root (the authoritative build artifact, not the source).
 *
 *   §46 — the release gate FAILS on any detected private-endpoint leakage in
 *         a committed source or a built bundle.
 *
 * DENY-LIST SOURCE OF TRUTH (DEF-008): the exact patterns this suite enforces
 * are NOT duplicated here — they are imported from
 * `frontend/scripts/privateEndpointDenyList.mjs`, the SAME module the
 * built-bundle scanner `frontend/scripts/scan-private-endpoints.mjs` uses, so
 * the two enforcement surfaces can never drift.
 *
 * DEF-008 BUNDLE HALF / CI ORDER (FIX): this suite's bundle half is gated on
 * `frontend/dist` being present, because the GitLab `test`-stage job runs
 * BEFORE the `build`-stage job and a fresh checkout has no dist. The
 * CI-CRITICAL bundle gate therefore lives in the BUILD itself: `npm run build`
 * now chains `npm run scan:bundle` (the hermetic scanner above) and FAILS the
 * build on any private-endpoint evidence in the exact artifact just produced
 * — a fix that can never be skipped, because producing dist and scanning it
 * are the SAME command. This suite's bundle half stays as the local
 * belt-and-braces assertion (it runs whenever a build is present in a working
 * tree) and exercises the SAME shared deny list.
 *
 * File hygiene: comments are stripped from the source scan (they never ship);
 * string literals are kept — ONLY what ships matters. Test fixtures are
 * excluded (hostile-string parsing tests are never compiled into the bundle).
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = resolve(HERE, "..");
const DIST_ROOT = resolve(HERE, "..", "..", "dist");

/** The loopback spellings of the local-Ollama endpoint (test-vector only:
 *  this file is a `*.test.ts` and is excluded from every release scan; the
 *  shared deny list builds `localhost:11434` from parts so NO committed file
 *  must ever carry the upright literal). */
const LOOPBACK_OLLAMA = "127.0.0.1:11434";

/** Remove // and /* *&#47; comments while leaving string literals (and JSX) intact. */
function stripComments(source: string): string {
  let result = "";
  let i = 0;
  let quote: string | null = null;
  while (i < source.length) {
    const c = source[i];
    const next = source[i + 1];
    if (quote !== null) {
      result += c;
      if (c === "\\" && next !== undefined) {
        result += next;
        i += 2;
        continue;
      }
      if (c === quote) quote = null;
      i += 1;
      continue;
    }
    if (c === "'" || c === '"' || c === "`") {
      quote = c;
      result += c;
      i += 1;
      continue;
    }
    if (c === "/" && next === "/") {
      while (i < source.length && source[i] !== "\n") i += 1;
      continue;
    }
    if (c === "/" && next === "*") {
      i += 2;
      while (i < source.length && !(source[i] === "*" && source[i + 1] === "/")) i += 1;
      i += 2;
      continue;
    }
    result += c;
    i += 1;
  }
  return result;
}

/** PRODUCTION source files only: *.ts / *.tsx, never *.test.ts(x). */
function productionSourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (!statSync(full).isDirectory()) {
      const isTest = name.endsWith(".test.ts") || name.endsWith(".test.tsx");
      if (!isTest && (name.endsWith(".ts") || name.endsWith(".tsx"))) {
        out.push(full);
      }
      continue;
    }
    productionSourceFiles(full, out);
  }
  return out;
}

/** Every built file under dist (walked recursively). */
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

function relativeLabel(file: string): string {
  return relative(SRC_ROOT, file).split(sep).join("/");
}

describe("Phase 24 §46 — committed production source contains NO private/Ollama endpoint literal", () => {
  const files = productionSourceFiles(SRC_ROOT);

  it("scans enough production source to be meaningful (no silent walk narrowing)", () => {
    expect(files.length).toBeGreaterThan(40);
    expect(files.some((f) => f.endsWith(`${sep}api${sep}client.ts`))).toBe(true);
  });

  it("contains NO localhost:11434 / 127.0.0.1:11434 literal", () => {
    const hits: string[] = [];
    for (const file of files) {
      const cleaned = stripComments(readFileSync(file, "utf8"));
      for (const literal of [LOCALHOST_OLLAMA, LOOPBACK_OLLAMA]) {
        if (cleaned.includes(literal)) hits.push(`${relativeLabel(file)} carries "${literal}"`);
      }
    }
    expect(hits, `local-Ollama literals in production source:\n- ${hits.join("\n- ")}`).toEqual([]);
  });

  it("contains NO bare Ollama service-port (11434) literal", () => {
    const hits: string[] = [];
    for (const file of files) {
      const cleaned = stripComments(readFileSync(file, "utf8"));
      for (const re of PROVIDER_PORT_PATTERNS) {
        const match = cleaned.match(re);
        if (match) hits.push(`${relativeLabel(file)} carries "${match[0]}"`);
      }
    }
    expect(hits, `Ollama port literals in production source:\n- ${hits.join("\n- ")}`).toEqual([]);
  });

  it("contains NO private/loopback/LAN IPv4 or docker-host literal", () => {
    const hits: string[] = [];
    for (const file of files) {
      const cleaned = stripComments(readFileSync(file, "utf8"));
      for (const re of PRIVATE_HOST_PATTERNS) {
        const match = cleaned.match(re);
        if (match) hits.push(`${relativeLabel(file)} carries "${match[0]}"`);
      }
    }
    expect(hits, `private-IP literals in production source:\n- ${hits.join("\n- ")}`).toEqual([]);
  });
});

describe("Phase 24 §45/§46 — production bundle is SAME-ORIGIN and free of private endpoints", () => {
  // CI-critical guarantee lives in `npm run build` -> `scan:bundle` (DEF-008);
  // this half runs whenever a local build is present (belt-and-braces on the
  // SAME shared deny list the scanner uses).
  const hasBuild = existsSync(DIST_ROOT);
  const files = hasBuild ? builtFiles(DIST_ROOT) : [];

  it.runIf(hasBuild)("embeds the same-origin relative API root (the built default)", () => {
    const joined = files.map((file) => readFileSync(file, "utf8")).join("\n");
    expect(joined).toContain(SAME_ORIGIN_ROOT);
  });

  it.runIf(hasBuild)("contains NO localhost:11434 or http://localhost:<port> literal", () => {
    const hits: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      if (source.includes(LOCALHOST_OLLAMA)) hits.push(`${relative(DIST_ROOT, file)} carries "${LOCALHOST_OLLAMA}"`);
      for (const re of LOCALHOST_HTTP_PATTERNS) {
        const match = source.match(re);
        if (match) hits.push(`${relative(DIST_ROOT, file)} carries "${match[0]}"`);
      }
    }
    expect(hits, `local endpoint literals in the bundle:\n- ${hits.join("\n- ")}`).toEqual([]);
  });

  it.runIf(hasBuild)("contains NO Ollama service-port (11434) literal", () => {
    const hits: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const re of PROVIDER_PORT_PATTERNS) {
        if (re.test(source)) hits.push(`${relative(DIST_ROOT, file)} matches provider pattern ${re.source}`);
      }
    }
    expect(hits, `Ollama port literals in the bundle:\n- ${hits.join("\n- ")}`).toEqual([]);
  });

  it.runIf(hasBuild)("contains NO private/loopback/LAN IPv4 or docker-host literal", () => {
    const hits: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const re of PRIVATE_HOST_PATTERNS) {
        const match = source.match(re);
        if (match) hits.push(`${relative(DIST_ROOT, file)} carries "${match[0]}"`);
      }
    }
    expect(hits, `private-IP literals in the bundle:\n- ${hits.join("\n- ")}`).toEqual([]);
  });
});