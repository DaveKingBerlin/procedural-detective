import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Phase 18A release-hygiene scan — NO provider endpoint or LAN IP literal may
 * exist in the frontend SOURCE (the shipped build never carries operator
 * infrastructure). Only `.env.example` at the repository root may document
 * example endpoints (outside this directory, and outside this scan).
 *
 * What is scanned:
 *   - every `*.ts` / `*.tsx` file under `frontend/src` EXCEPT `*.test.ts(x)`
 *     (hostile-string fixtures belong to parsing tests and are never compiled
 *     into the app bundle — Vite only links modules the routes actually
 *     import).
 *
 * What is forbidden in production source:
 *   - private/LAN/loopback IP literals (127.x, 10.x, 192.168.x, 172.16-31.x,
 *     and the docker-host alias `host.docker.internal`);
 *   - any `http(s)://` literal whose host is NOT in the explicit allowlist:
 *       * a bare scheme token (`"http://"` / `"https://"`) — the DENYLIST
 *         guards already shipped in generationMode/assetCatalog/kitCatalog;
 *       * `localhost[:port]` — the VITE_API_BASE_URL local-dev default;
 *       * `github.com` — the canonical public repository link on the landing.
 *
 * Comments are removed first (they do not ship); string literals are kept.
 * The scan is an INVARIANT, not a report: adding a provider URL / LAN IP to
 * the source fails the suite, mirroring the release scan the backend track
 * owns for built artifacts and tracked files.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = resolve(HERE, "..");

/** Private / loopback / LAN IPv4 prefixes plus the docker host alias. */
const PRIVATE_HOST_IP: readonly RegExp[] = [
  /\b127\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/,
  /\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/,
  /\b192\.168\.\d{1,3}\.\d{1,3}\b/,
  /\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b/,
  /\bhost\.docker\.internal\b/i,
];

/** Hosts that are legitimately part of the product source. */
const ALLOWED_URL_HOSTS: ReadonlyArray<{ label: string; test: (host: string) => boolean }> = [
  { label: "localhost (API dev origin)", test: (host) => host === "localhost" || host.startsWith("localhost:") },
  { label: "github.com (canonical repo link)", test: (host) => host === "github.com" },
];

/** The URL host parser: lower-cased host without scheme, port or path. */
function urlHostsOf(source: string): Array<{ host: string; index: number }> {
  const hosts: Array<{ host: string; index: number }> = [];
  const re = /https?:\/\//gi;
  let match: RegExpExecArray | null;
  while ((match = re.exec(source)) !== null) {
    const rest = source.slice(match.index + match[0].length);
    const hostMatch = rest.match(/^[A-Za-z0-9.-]+/);
    hosts.push({ host: (hostMatch?.[0] ?? "").toLowerCase(), index: match.index });
  }
  return hosts;
}

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

function describeHit(file: string, detail: string): string {
  return `${relative(file, SRC_ROOT)} — ${detail}`;
}

describe("Phase 18A — no provider URL / LAN IP literal in frontend source", () => {
  const files = productionSourceFiles(SRC_ROOT);

  it("scans every production source file under frontend/src", () => {
    // A healthy scan covers the route/journey/scene/api surface — cheap
    // guard against the walk silently narrowing in the future.
    expect(files.length).toBeGreaterThan(40);
    expect(files.some((f) => f.endsWith(`${sep}journey${sep}providerMode.ts`))).toBe(true);
    expect(files.some((f) => f.endsWith(`${sep}routes${sep}home.tsx`))).toBe(true);
  });

  it("keeps production sources free of private/loopback/LAN IP and docker-host literals", () => {
    const hits: string[] = [];
    for (const file of files) {
      const cleaned = stripComments(readFileSync(file, "utf8"));
      for (const re of PRIVATE_HOST_IP) {
        const match = cleaned.match(re);
        if (match) {
          hits.push(describeHit(file, `${re.source} matched "${match[0]}"`));
        }
      }
    }
    expect(hits, `private IP / LAN literals in production sources:\n- ${hits.join("\n- ")}`).toEqual([]);
  });

  it("allows only the documented hosts in http(s) literals (no provider endpoints)", () => {
    const hits: string[] = [];
    for (const file of files) {
      const cleaned = stripComments(readFileSync(file, "utf8"));
      for (const { host, index } of urlHostsOf(cleaned)) {
        const allowed =
          host === "" ||
          ALLOWED_URL_HOSTS.some(({ test }) => test(host));
        if (allowed) continue;
        const around = cleaned.slice(Math.max(0, index - 8), index + host.length + 8).replace(/\s+/g, " ");
        hits.push(describeHit(file, `host "${host}" in …${around}…`));
      }
    }
    expect(hits, `undocumented http(s) hosts in production sources:\n- ${hits.join("\n- ")}`).toEqual([]);
  });
});