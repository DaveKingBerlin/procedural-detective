import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Guards user-facing copy neutrality: no third-party brand shorthand or
 * development-phase jargon may leak into the visible UI text of the app's
 * routed pages (home / scene / not-found). Code comments are exempt, so the
 * scanner removes them before searching.
 */

const HERE = dirname(fileURLToPath(import.meta.url));

const UI_ROUTE_FILES = ["home.tsx", "scene.tsx", "not-found.tsx"];

const BRAND_TOKENS: ReadonlyArray<{ label: string; re: RegExp }> = [
  { label: "Babylon", re: /\bBabylon\b/i },
  { label: "Phase 2", re: /Phase\s*2/i },
];

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
      if (c === quote) {
        quote = null;
      }
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

function findBrandTokens(cleanedSource: string): string[] {
  const hits: string[] = [];
  for (const { label, re } of BRAND_TOKENS) {
    const match = cleanedSource.match(re);
    if (match && match.index !== undefined) {
      const start = Math.max(0, match.index - 24);
      const end = Math.min(cleanedSource.length, match.index + match[0].length + 24);
      hits.push(`"${label}" in: …${cleanedSource.slice(start, end).replace(/\s+/g, " ")}…`);
    }
  }
  return hits;
}

describe("user-facing copy neutrality", () => {
  for (const file of UI_ROUTE_FILES) {
    it(`keeps ${file} free of brand shorthand and phase jargon in visible text`, () => {
      const source = readFileSync(join(HERE, file), "utf8");
      const visible = stripComments(source);
      const hits = findBrandTokens(visible);
      expect(hits, `user-visible text in ${file} contains: ${hits.join("; ")}`).toEqual([]);
    });
  }
});