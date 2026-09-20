import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Phase 18C release-hygiene scan for the Detective Notebook / player
 * hypothesis / proof-board modules.
 *
 * Invariants (mirroring the providerUrlLeakScan style: comments stripped,
 * string literals kept — only what ships matters):
 *   - the new modules NEVER call the network or an existing API function
 *     (no `fetch(`, no `XMLHttpRequest`, no `/api/v1`, none of the frozen
 *     endpoint helpers — req 16: "auth unchanged / no new endpoints");
 *   - hypothesisStore is the ONLY new module allowed to touch localStorage;
 *     the model/presentation modules stay side-effect free;
 *   - no hidden-truth / winner / `proc.*` token may survive in the
 *     PRODUCTION source of the new model/presentation modules (the player
 *     can never see them pre-reveal); and the ONLY storage key namespace
 *     introduced is the documented `pd_hypothesis_v1`.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = resolve(HERE, "..");

/** The Phase 18C production modules (never the *.test.* files). */
const MODULES: string[][] = [
  ["notebook", "notebookModel.ts"],
  ["notebook", "hypothesisStore.ts"],
  ["notebook", "NotebookPanel.tsx"],
  ["reveal", "proofBoardModel.ts"],
  ["reveal", "ProofBoard.tsx"],
];

/** The frozen api/client.ts function names — none may appear in the new modules. */
const ENDPOINT_HELPERS = [
  "getInvestigation",
  "interactObject",
  "discoverEvidence",
  "readRecord",
  "submitAccusation",
  "getReveal",
  "createAnonymousSession",
  "createCase",
  "getGenerationProgress",
  "createPlaythrough",
  "getHealth",
  "getReadiness",
];

/** Remove // and /* *&#47; comments without touching string literals/JSX. */
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

function readModule(pathParts: string[]): string {
  return readFileSync(join(SRC_ROOT, ...pathParts), "utf8");
}

describe("Phase 18C — new modules never add network/auth surface (reqs 6,16)", () => {
  it("scans exactly the new Phase 18C production modules", () => {
    expect(MODULES.length).toBe(5);
    for (const parts of MODULES) {
      const source = readModule(parts);
      expect(source.length).toBeGreaterThan(200);
    }
  });

  it("contains no remote call surface at all (no fetch, no XHR, no API path, no endpoint helper)", () => {
    const hits: string[] = [];
    for (const parts of MODULES) {
      const cleaned = stripComments(readModule(parts));
      if (/fetch\s*\(|XMLHttpRequest/i.test(cleaned)) hits.push(`${parts.join("/")} has a remote call`);
      if (/\/api\/v1\//.test(cleaned)) hits.push(`${parts.join("/")} references an API path`);
      for (const helper of ENDPOINT_HELPERS) {
        if (cleaned.includes(helper)) hits.push(`${parts.join("/")} references ${helper}`);
      }
    }
    expect(hits).toEqual([]);
  });

  it("hypothesisStore is the ONLY new module touching localStorage", () => {
    for (const parts of MODULES) {
      if (parts.join("/") === "notebook/hypothesisStore.ts") continue;
      const cleaned = stripComments(readModule(parts));
      expect(cleaned).not.toContain("localStorage");
      expect(cleaned).not.toContain("window.");
    }
  });

  it("introduces exactly the documented pd_hypothesis_v1 storage namespace (and nothing else)", () => {
    const store = stripComments(readModule(["notebook", "hypothesisStore.ts"]));
    const namespaces = [...store.matchAll(/"pd_[A-Za-z0-9_]+"/g)].map((match) => match[0]);
    expect(namespaces).toEqual(['"pd_hypothesis_v1"']);
  });
});

describe("Phase 18C — new production model/presentation source is truth-safe (reqs 3,5,13)", () => {
  it("no hidden-truth / winner / proc.* tokens survive in the model or presentation modules", () => {
    const modelModules = [
      ["notebook", "notebookModel.ts"],
      ["reveal", "proofBoardModel.ts"],
      ["reveal", "ProofBoard.tsx"],
    ];
    for (const parts of modelModules) {
      const cleaned = stripComments(readModule(parts));
      expect(cleaned, `${parts.join("/")} must not carry proc.* tokens`).not.toMatch(/proc\./);
      expect(cleaned, `${parts.join("/")} must not carry winner material`).not.toMatch(/\bwinner\b/i);
      expect(cleaned, `${parts.join("/")} must not carry answer markers`).not.toMatch(/correct.?answer|accepted.?answer/i);
    }
  });
});