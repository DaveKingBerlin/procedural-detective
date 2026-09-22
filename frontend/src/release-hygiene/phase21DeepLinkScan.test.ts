import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Phase 21 §1 F-01 — production deep-link/reload safety (ROOT-ABSOLUTE assets).
 *
 * The app runs on BrowserRouter with routes /new /generating /scene /accuse
 * /reveal and the backend SPA fallback serves index.html at every one of them.
 * The BUILT `dist/index.html` must therefore reference its assets by
 * ROOT-ABSOLUTE URL (`/assets/...`), NEVER route-relative (`./assets/...`):
 *
 *   - a route-relative build requests `/scene/assets/...` after a refresh on
 *     /scene (the F-01 defect),
 *   - the backend then answers that request with the index.html SPA fallback,
 *   - the browser rejects the module because the response is HTML, not a
 *     JavaScript MIME type → blank screen on every deep-link/reload.
 *
 * With a root-absolute build the browser always requests `/assets/<hash>.js`
 * from the site root, which the backend serves as the REAL static file with
 * the correct content type (asserted by the backend suite against a built
 * dist; this frontend test pins the built bundle's URL contract).
 *
 * The check runs against the BUILT frontend (`frontend/dist` produced by
 * `npm run build`) exactly like the Phase 20 production bundle scan: when
 * `dist/` is absent it SKIPS cleanly (a source-only checkout never fails
 * here). `it.runIf` keeps the suite green in that case without weakening the
 * assertions when the build IS present.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST_ROOT = resolve(HERE, "..", "..", "dist");

/** The route-relative asset base the F-01 defect produced. */
const ROUTE_RELATIVE = "./assets/";

interface DistEntry {
  /** Label relative to DIST_ROOT with forward slashes (for messages). */
  label: string;
  /** Absolute path of the file. */
  path: string;
}

function listDistFiles(): DistEntry[] {
  const entries: DistEntry[] = [];
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        walk(full);
        continue;
      }
      entries.push({ label: relative(DIST_ROOT, full).split(sep).join("/"), path: full });
    }
  };
  if (existsSync(DIST_ROOT)) walk(DIST_ROOT);
  return entries;
}

/** Relative path helper that accepts a root-absolute "/assets/..." URL. */
function distPathForAssetUrl(url: string): string {
  return join(DIST_ROOT, ...url.replace(/^\/+/, "").split("/"));
}

describe("Phase 21 §1 F-01 — production build assets are ROOT-ABSOLUTE (deep-link safe)", () => {
  const hasBuild = existsSync(DIST_ROOT);
  const files = hasBuild ? listDistFiles() : [];
  const indexSource = hasBuild ? readFileSync(join(DIST_ROOT, "index.html"), "utf8") : "";

  it.runIf(hasBuild)("dist/index.html exists alongside the asset chunk files", () => {
    expect(files.some((entry) => entry.label === "index.html")).toBe(true);
    expect(files.some((entry) => entry.label.startsWith("assets/"))).toBe(true);
  });

  it.runIf(hasBuild)("loads the entry module script from a root-absolute /assets/* URL", () => {
    const script = indexSource.match(/<script[^>]*type="module"[^>]*>[\s\S]*?<\/script>/);
    expect(script, "dist/index.html must carry a <script type=module>").not.toBeNull();
    const tag = script![0];
    expect(tag, "module script must reference /assets/<hash>.js").toMatch(/src="\/assets\/[^"]+\.js"/);
    expect(tag, "module script must NEVER use a route-relative ./assets URL").not.toContain(ROUTE_RELATIVE);
  });

  it.runIf(hasBuild)("references every script/link asset by ROOT-ABSOLUTE /assets/ URL", () => {
    const assets = [...indexSource.matchAll(/(?:src|href)="([^"]+)"/g)]
      .map((match) => match[1])
      .filter((url) => url.includes("assets"));
    expect(assets.length, "dist/index.html must reference at least one asset").toBeGreaterThan(0);
    const offenders = assets.filter((url) => !url.startsWith("/assets/"));
    expect(offenders, `non-root-absolute asset URLs in dist/index.html:\n- ${offenders.join("\n- ")}`).toEqual([]);
  });

  it.runIf(hasBuild)("links the stylesheet from a root-absolute /assets/* URL", () => {
    const links = [...indexSource.matchAll(/<link[^>]*rel="stylesheet"[^>]*>/g)].map((m) => m[0]);
    expect(links.length, "dist/index.html must link a stylesheet").toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toMatch(/href="\/assets\/[^"]+\.css"/);
      expect(link).not.toContain(ROUTE_RELATIVE);
    }
  });

  it.runIf(hasBuild)("every referenced /assets/* file exists ON DISK at the root-relative path", () => {
    const urls = [...indexSource.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
    for (const url of urls) {
      expect(existsSync(distPathForAssetUrl(url)), `dist is missing the file for ${url}`).toBe(true);
    }
  });

  it.runIf(hasBuild)("contains NO route-relative ./assets anywhere in dist/index.html", () => {
    expect(indexSource, "dist/index.html must not contain ./assets references").not.toContain(ROUTE_RELATIVE);
  });

  it.runIf(hasBuild)("keeps route-relative ./assets out of every built JS chunk (best-effort)", () => {
    const offenders: string[] = [];
    for (const entry of files) {
      if (!entry.label.endsWith(".js")) continue;
      if (readFileSync(entry.path, "utf8").includes(ROUTE_RELATIVE)) {
        offenders.push(entry.label);
      }
    }
    expect(offenders, `route-relative ./assets in built chunks:\n- ${offenders.join("\n- ")}`).toEqual([]);
  });
});