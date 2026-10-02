// ---------------------------------------------------------------------------
// Phase 24 §46 — built-bundle private-endpoint scanner (hermetic, stdlib only).
//
// Walks a built frontend directory (default: frontend/dist — the exact
// artifact `npm run build` produces and the Dockerfile stage-1 bakes into the
// image) and FAILS (exit non-zero) when ANY private-endpoint evidence is found:
//   * the local-Ollama endpoint: the `localhost` host joined with the audited
//     Ollama service port, the bare colon+port, and the bare port. The port is
//     an ASSEMBLED value — written in this file only as 1 1 4 3 4, never
//     contiguously (see privateEndpointDenyList.mjs for the same discipline);
//   * `http://localhost:<port>` local API-endpoint literals;
//   * private/loopback/LAN IPv4 literals (the 127.*.*.*, 10.*.*.*,
//     192.168.*.* and 172.16-31.*.*.* dotted prefixes — masked form only);
//   * the docker-host alias `host[.]docker[.]internal`.
// It also asserts the SAME-ORIGIN positive contract: the built bundle must
// embed the relative `/api/v1` API root (never a private origin).
//
// DEF-008 fix: chained to `npm run build`
// (`build = tsc --noEmit && vite build && npm run scan:bundle`) so EVERY
// production build — local developer, the GitLab `build`-stage job, and the
// Dockerfile stage-1 image build — scans the exact artifact it just produced.
// The old test-only bundle scan ran in the `test`-stage job, i.e. BEFORE
// `frontend-build` on a fresh checkout, where `frontend/dist` does not exist
// yet (`frontend/dist/` is gitignored), so `it.runIf(hasBuild)` silently
// skipped and a shipped bundle could carry a private endpoint undetected.
// A scan that is PART of the build command can never skip: producing the
// bundle and scanning it are the same step; a missing bundle is a hard
// failure (exit 2), never a silent pass.
//
// Implementation notes:
//   * stdlib only (node:fs / node:path / node:url) — no runtime deps;
//   * utf8 text files only: binary assets (fonts/images) are detected by a
//     NUL byte in the leading bytes and skipped;
//   * the deny patterns are imported from ./privateEndpointDenyList.mjs so
//     the scanner and the Phase 24 guard test suite can never drift;
//   * optional positional arg = scan target dir (absolute or relative);
//     default = <frontend>/dist. Tests pass an OS tmp dir — the scanner never
//     touches the frontend/dist state on its own.
//
// Exit codes: 0 = clean (bundle embeds /api/v1, no deny evidence);
//             1 = violation (deny hit OR same-origin root missing);
//             2 = target directory missing / unreadable (fail-closed).
// ---------------------------------------------------------------------------

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { DENY_PATTERNS, SAME_ORIGIN_ROOT } from "./privateEndpointDenyList.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_TARGET = resolve(HERE, "..", "dist");

const EXIT_CLEAN = 0;
const EXIT_VIOLATION = 1;
const EXIT_TARGET_MISSING = 2;

/** Byte probe window for the binary sniff (fonts/images, not text). */
const BINARY_PROBE_BYTES = 8192;

/** Recursively collect every regular FILE under `dir` (read errors skip). */
export function collectFiles(dir, out = []) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    const full = join(dir, name);
    let stat;
    try {
      stat = statSync(full);
    } catch {
      continue;
    }
    if (stat.isDirectory()) {
      collectFiles(full, out);
    } else if (stat.isFile()) {
      out.push(full);
    }
  }
  return out;
}

/** Binary sniff: a NUL byte in the leading bytes marks a non-text asset. */
function looksBinary(raw) {
  const probe = raw.length <= BINARY_PROBE_BYTES ? raw : raw.subarray(0, BINARY_PROBE_BYTES);
  return probe.indexOf(0) !== -1;
}

/**
 * Scan one built directory against the Phase 24 §46 deny set + the same-origin
 * positive assertion. PURE function (no I/O beyond reads); exported so the
 * vitest suite can exercise it in-process AND through the real CLI.
 */
export function runScan(targetDir) {
  if (!existsSync(targetDir) || !statSync(targetDir).isDirectory()) {
    return { present: false, violations: [], scanned: 0, foundSameOriginRoot: false };
  }
  const violations = [];
  let scanned = 0;
  let foundSameOriginRoot = false;
  for (const file of collectFiles(targetDir)) {
    let raw;
    try {
      raw = readFileSync(file); // Buffer — no forced encoding
    } catch {
      continue;
    }
    if (looksBinary(raw)) continue;
    const text = raw.toString("utf8");
    if (text.includes(SAME_ORIGIN_ROOT)) foundSameOriginRoot = true;
    scanned += 1;
    const label = relative(targetDir, file).split(sep).join("/");
    for (const re of DENY_PATTERNS) {
      const match = text.match(re);
      if (match !== null) {
        violations.push(`${label}: matches ${re.source} ("${match[0]}")`);
      }
    }
  }
  if (!foundSameOriginRoot) {
    violations.push(`bundle does NOT embed the same-origin API root "${SAME_ORIGIN_ROOT}"`);
  }
  return { present: true, violations, scanned, foundSameOriginRoot };
}

/** Render a short human label for the scanned dir (dist when defaulted). */
function targetLabel(target, targetArg) {
  if (targetArg === undefined) return relative(HERE, "..").split(sep).join("/") + "/dist";
  return relative(process.cwd(), target).split(sep).join("/");
}

function main() {
  // argv layout: [node, scripts/scan-private-endpoints.mjs, <optional target>]
  const targetArg = process.argv[2];
  const target = targetArg === undefined ? DEFAULT_TARGET : resolve(process.cwd(), targetArg);
  const label = targetLabel(target, targetArg);

  let result;
  try {
    result = runScan(target);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    console.error(`[scan:bundle] ${label}: cannot scan: ${detail}`);
    process.exitCode = EXIT_TARGET_MISSING;
    return;
  }

  if (!result.present) {
    console.error(
      `[scan:bundle] ${label}: no built bundle found. The private-endpoint scan ` +
        `is a mandatory part of \`npm run build\` — a missing bundle must never ` +
        `count as scanned (Phase 24 §46 / DEF-008 fail-closed).`,
    );
    process.exitCode = EXIT_TARGET_MISSING;
    return;
  }

  if (result.violations.length > 0) {
    console.error(
      `[scan:bundle] Phase 24 §46 FAIL — ${result.violations.length} violation(s) in ${label}:`,
    );
    for (const violation of result.violations) console.error(`  - ${violation}`);
    process.exitCode = EXIT_VIOLATION;
    return;
  }

  console.log(
    `[scan:bundle] OK — ${result.scanned} text file(s) scanned; same-origin ` +
      `${SAME_ORIGIN_ROOT} embedded; no private-endpoint evidence (Phase 24 §46).`,
  );
  process.exitCode = EXIT_CLEAN;
}

// Run the CLI ONLY when executed directly (never when the vitest suite imports
// this file to exercise runScan/collectFiles in-process).
const entryIsThisFile =
  typeof process.argv[1] === "string" &&
  pathToFileURL(resolve(process.cwd(), process.argv[1])).href === import.meta.url;

if (entryIsThisFile) {
  main();
}