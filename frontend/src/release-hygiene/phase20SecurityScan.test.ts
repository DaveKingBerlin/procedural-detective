import { readdirSync, readFileSync, statSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import NotebookPanel from "../notebook/NotebookPanel";
import { EMPTY_PINS } from "../notebook/hypothesisStore";
import type { NotebookEntry, NotebookGroup, NotebookModel } from "../notebook/notebookModel";
import FocusInspection from "../scene/FocusInspection";
import { evidenceLabelFor } from "../scene/objectLabel";
import type { SceneWorldObject } from "../scene/buildInvestigationScene";

/**
 * Phase 20 release-hygiene scan — BROWSER SAFETY (Phase 20 §23).
 *
 * Invariants (mirroring the phase18cLeakScan / providerUrlLeakScan style:
 * comments stripped, string literals kept — only what ships matters):
 *   - NO production module uses `dangerouslySetInnerHTML` (hostile strings must
 *     stay inert text; everything is rendered through React's default string
 *     escaping);
 *   - NO production module contains a `javascript:` URL execution path: the
 *     ONLY `javascript:` literals allowed are the DENYLIST guards already
 *     shipped in generationMode / assetCatalog / kitCatalog
 *     (FORBIDDEN_URL_TOKENS — used to REJECT hostile input, never executed);
 *   - a hostile label / canonicalName can never EXECUTE: app-authored UI
 *     components that echo player/server content are rendered headlessly with
 *     the hostile strings and their markup is asserted to HTML-escape the
 *     payload (no `<script`, no attribute-injection / `onerror=` feet).
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = resolve(HERE, "..");

/**
 * The DENYLIST-guard modules that legitimately contain the `javascript:`
 * token as a REJECTED-input list (FORBIDDEN_URL_TOKENS). No execution path may
 * ever be built from them — they are inputs to `isKnownX` rejectors.
 */
const JAVASCRIPT_TOKEN_ALLOWLIST: readonly string[] = [
  `${sep}journey${sep}generationMode.ts`,
  `${sep}catalog${sep}assetCatalog.ts`,
  `${sep}environments${sep}kitCatalog.ts`,
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

describe("Phase 20 §23 — no dangerouslySetInnerHTML in production source", () => {
  const files = productionSourceFiles(SRC_ROOT);

  it("never renders hostile strings through dangerouslySetInnerHTML", () => {
    const hits: string[] = [];
    for (const file of files) {
      const cleaned = stripComments(readFileSync(file, "utf8"));
      if (cleaned.includes("dangerouslySetInnerHTML")) {
        hits.push(relativeLabel(file));
      }
    }
    expect(hits, `dangerouslySetInnerHTML in production sources:\n- ${hits.join("\n- ")}`).toEqual([]);
  });

  it("scans enough production source to be meaningful", () => {
    expect(files.length).toBeGreaterThan(40);
    expect(files.some((f) => f.endsWith(`${sep}notebook${sep}NotebookPanel.tsx`))).toBe(true);
    expect(files.some((f) => f.endsWith(`${sep}evidence${sep}evidencePanel.tsx`))).toBe(true);
  });
});

describe("Phase 20 §23 — no javascript: URL execution path", () => {
  const files = productionSourceFiles(SRC_ROOT);

  it("hosts the ONLY javascript: literals in the documented DENYLIST guards", () => {
    const hits: string[] = [];
    for (const file of files) {
      const label = relativeLabel(file);
      const cleaned = stripComments(readFileSync(file, "utf8"));
      if (!cleaned.includes("javascript:")) continue;
      if (!JAVASCRIPT_TOKEN_ALLOWLIST.some((allowed) => file.endsWith(allowed))) {
        hits.push(`${label} carries a javascript: literal outside the denylist guards`);
      }
    }
    expect(hits, `javascript: literals outside FORBIDDEN_URL_TOKENS:\n- ${hits.join("\n- ")}`).toEqual([]);
  });

  it("never builds an anchor href from a javascript: URL", () => {
    const hits: string[] = [];
    for (const file of files) {
      const cleaned = stripComments(readFileSync(file, "utf8"));
      if (/\bhref\s*=\s*(['"])[\s]*javascript:/i.test(cleaned)) {
        hits.push(relativeLabel(file));
      }
      // Dynamic href assignment to a javascript: scheme is equally forbidden.
      if (/\.href\s*=\s*(['"])[\s]*javascript:/i.test(cleaned)) {
        hits.push(`${relativeLabel(file)} (dynamic .href assignment)`);
      }
    }
    expect(hits, `javascript: href execution paths:\n- ${hits.join("\n- ")}`).toEqual([]);
  });
});

describe("Phase 20 §23 — hostile label / canonicalName never executes", () => {
  const HOSTILE_LABEL = '<img src=x onerror=alert(1)><script>alert("pwned")</script>';
  const HOSTILE_CANONICAL = '"><svg onload=alert(1)></svg>';

  it("renders a hostile notebook label/detail as escaped inert text (React default rendering)", () => {
    const hostileEntry: NotebookEntry = {
      id: "objects-knife",
      label: HOSTILE_LABEL,
      detail: HOSTILE_CANONICAL,
      evidenceId: "forensic_knife_match_01",
      read: false,
    };
    const group: NotebookGroup = {
      id: "objects",
      title: "Objects",
      emptyMessage: "none",
      entries: [hostileEntry],
    };
    const model: NotebookModel = {
      groups: [group],
      discoveredEvidenceIds: ["forensic_knife_match_01"],
      readEvidenceIds: [],
    };
    const markup = renderToStaticMarkup(
      createElement(NotebookPanel, {
        model,
        candidates: null,
        pins: { ...EMPTY_PINS },
        open: true,
        onToggle: () => {},
        onPinsChanged: () => {},
      }),
    );

    // The hostile payload is preserved as ESCAPED TEXT, never as markup.
    expect(markup).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(markup).toContain("&lt;script&gt;");
    // No tag can form: every `<` was escaped to `&lt;`, so no element/attribute
    // (and therefore no active handler) can exist in the DOM.
    expect(markup).not.toMatch(/<img\b|<\/img\b|<script\b|<\/script\b/);
    expect(markup).not.toMatch(/<[A-Za-z][A-Za-z0-9-]*\s+on(?:error|load)\s*=/);
  });

  it("renders a hostile humanized canonicalName through focus inspection as escaped text", () => {
    const obj = {
      objectId: "proc_art",
      assetId: "proc.decor.a1b2c3",
      assetType: "proc",
      subtype: null,
      hitboxScale: 1,
      scale: { x: 0.3, y: 0.3, z: 0.3 },
      position: { x: 0, y: 0, z: 0 },
      rotation: { x: 0, y: 0, z: 0 },
      label: null,
      interaction: "inspect",
      interactionWorks: true,
      evidenceId: null,
      discovered: false,
      read: false,
      unknownAsset: false,
      primitiveKind: "box",
      compositeKind: null,
      color: "#c8ccd4",
      generated: { canonicalName: HOSTILE_CANONICAL },
      generatedParts: null,
      generatedHitbox: null,
      templateId: null,
      templateParts: null,
      templateHitbox: null,
      templateMaterial: null,
      templateState: null,
      renderScale: 1,
    } as SceneWorldObject;

    const label = evidenceLabelFor(obj);
    const markup = renderToStaticMarkup(
      createElement(FocusInspection, {
        label,
        badges: { procedural: false, validatedGeometry: false },
        onClose: () => {},
      }),
    );

    // Every `<` in the humanized name was escaped — no tag/attribute can form.
    expect(markup).not.toMatch(/<img\b|<\/img\b|<svg\b|<\/svg\b/);
    expect(markup).not.toMatch(/<[A-Za-z][A-Za-z0-9-]*\s+on(?:error|load)\s*=/);
  });
});