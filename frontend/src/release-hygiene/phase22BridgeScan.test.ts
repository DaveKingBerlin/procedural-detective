import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Phase 22 release-hygiene scan — BYO-Ollama BRIDGE SAFETY.
 *
 * Phase 22 §26 (same-origin / CSP) and §36 (browser/player separation):
 *   - NO production module may contain a `localhost:11434` /
 *     `127.0.0.1:11434` literal (the browser NEVER talks to a local Ollama;
 *     it talks only to the app server, whose /api/v1/bridge/* routes proxy to
 *     the bridge over the bridge-owned WS);
 *   - the bridge API surface (`/api/v1/bridge/pairing`,
 *     `/api/v1/bridge/status`, `createBridgePairing`, `getBridgeStatus`) and
 *     the pairing PANEL component exist ONLY in the sanctioned CREATOR
 *     surface: the api client (definitions), the /new pairing panel and the
 *     /new route. A playthrough browser (scene/accuse/reveal) can never reach
 *     pairing controls or bridge status — enforced HERE at source level, and
 *     by the backend's own session-scoped auth at runtime.
 *
 * Comments are stripped first (they do not ship); string literals are kept —
 * only what ships matters (the phase18cLeakScan / providerUrlLeakScan style).
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = resolve(HERE, "..");

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

function relativeLabel(file: string): string {
  return relative(SRC_ROOT, file).split(sep).join("/");
}

/**
 * The ONLY production modules that may reference the bridge API surface or
 * the pairing panel (the /new CREATOR path). Everything else is a violation.
 */
const BRIDGE_SURFACE_ALLOWLIST: readonly string[] = [
  `${sep}api${sep}client.ts`, // endpoint definitions (createBridgePairing / getBridgeStatus)
  `${sep}journey${sep}LocalAiBridgePanel.tsx`, // the pairing/status panel component
  `${sep}routes${sep}new.tsx`, // the /new creator route mounts the panel
];

/** The bridge API surface tokens (endpoint helpers + path literals). */
const BRIDGE_SURFACE_TOKENS: readonly string[] = [
  "createBridgePairing",
  "getBridgeStatus",
  "/api/v1/bridge/pairing",
  "/api/v1/bridge/status",
  "LocalAiBridgePanel",
];

/** The forbidden local-Ollama direct-call literals (Phase 22 §26). */
const FORBIDDEN_LOCAL_OLLAMA_LITERALS: readonly string[] = [
  "localhost:11434",
  "127.0.0.1:11434",
];

describe("Phase 22 §26 — no browser request to a local Ollama endpoint anywhere in production source", () => {
  const files = productionSourceFiles(SRC_ROOT);

  it("scans enough production source to be meaningful", () => {
    expect(files.length).toBeGreaterThan(40);
  });

  it("contains NO localhost:11434 / 127.0.0.1:11434 literal", () => {
    const hits: string[] = [];
    for (const file of files) {
      const cleaned = stripComments(readFileSync(file, "utf8"));
      for (const literal of FORBIDDEN_LOCAL_OLLAMA_LITERALS) {
        if (cleaned.includes(literal)) hits.push(`${relativeLabel(file)} carries "${literal}"`);
      }
    }
    expect(hits, `local-Ollama literals in production source:\n- ${hits.join("\n- ")}`).toEqual([]);
  });
});

describe("Phase 22 §36 — bridge surface lives ONLY in the /new CREATOR path (player separation)", () => {
  const files = productionSourceFiles(SRC_ROOT);

  it("the bridge API surface + panel exist only in the sanctioned allowlist", () => {
    const violations: string[] = [];
    for (const file of files) {
      const label = relativeLabel(file);
      const cleaned = stripComments(readFileSync(file, "utf8"));
      for (const token of BRIDGE_SURFACE_TOKENS) {
        if (!cleaned.includes(token)) continue;
        if (!BRIDGE_SURFACE_ALLOWLIST.some((allowed) => file.endsWith(allowed))) {
          violations.push(`${label} references bridge surface token "${token}"`);
        }
      }
    }
    expect(
      violations,
      `bridge surface outside the /new creator allowlist:\n- ${violations.join("\n- ")}`,
    ).toEqual([]);
  });

  it("the playthrough routes NEVER reference bridge surface or pairing controls", () => {
    for (const route of ["scene.tsx", "accuse.tsx", "reveal.tsx", "generating.tsx"]) {
      const file = join(SRC_ROOT, "routes", route);
      const cleaned = stripComments(readFileSync(file, "utf8"));
      for (const token of BRIDGE_SURFACE_TOKENS) {
        expect(cleaned, `routes/${route} must not reference ${token}`).not.toContain(token);
      }
      // A playthrough browser renders no pairing controls and no bridge status.
      expect(cleaned, `routes/${route} must not render bridge/pairing copy`).not.toMatch(/bridge|pairing/i);
    }
  });

  it("the /new route is the ONLY route mounting the pairing panel", () => {
    for (const file of files) {
      if (!file.endsWith(`${sep}routes${sep}new.tsx`)) continue;
      const cleaned = stripComments(readFileSync(file, "utf8"));
      expect(cleaned).toContain("LocalAiBridgePanel");
    }
    for (const route of ["home.tsx", "scene.tsx", "accuse.tsx", "reveal.tsx", "generating.tsx"]) {
      const cleaned = stripComments(readFileSync(join(SRC_ROOT, "routes", route), "utf8"));
      expect(cleaned, `routes/${route} must not mount LocalAiBridgePanel`).not.toContain(
        "LocalAiBridgePanel",
      );
    }
  });
});