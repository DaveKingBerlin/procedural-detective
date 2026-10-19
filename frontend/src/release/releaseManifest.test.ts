import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import {
  RELEASE_BASE_URL,
  RELEASE_CHECKSUMS_FILENAME,
  RELEASE_DOWNLOAD_ANCHOR_ID,
  RELEASE_MANIFEST_TIMEOUT_MS,
  RELEASE_MANIFEST_URL,
  RELEASE_QUICKSTART_FILENAME,
  RELEASE_PLATFORMS,
  RELEASE_REPOSITORY_URL,
  archiveTypeLabel,
  artifactMetadataLine,
  checksumsUrl,
  downloadButtonLabel,
  findArtifact,
  formatApproxSize,
  loadReleaseManifest,
  parseReleaseManifest,
  quickStartUrl,
  releaseArtifactUrl,
  sourceCodeUrl,
  type ReleaseManifest,
} from "./releaseManifest";

/**
 * Phase 49 §19/§21/§24/§39 — the canonical release metadata module.
 *
 * These unit tests pin: the frozen manifest schema parse (allow-list), the
 * SINGLE canonical URL construction (release base + version + filename), the
 * frozen copy helpers, and the checked-in bundled manifest. No DOM/network.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const BUNDLED_MANIFEST = resolve(HERE, "..", "..", "public", "releases", "release-manifest.json");

const CANONICAL: ReleaseManifest = {
  version: "1.0.0",
  gitCommit: "abcdef123",
  releasedAt: "2026-10-19T00:00:00Z",
  artifacts: [
    {
      platform: "windows",
      arch: "x64",
      filename: "pd-ollama-bridge-v1.0.0-windows-x64.zip",
      sha256: "a".repeat(64),
      size: 18_400_000,
    },
    {
      platform: "linux",
      arch: "x64",
      filename: "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz",
      sha256: "b".repeat(64),
      size: 17_100_000,
    },
  ],
};

describe("Phase 49 — canonical release configuration (§21/§39)", () => {
  it("lives in exactly one module and derives the GitHub Releases paths", () => {
    expect(RELEASE_REPOSITORY_URL).toBe(
      "https://github.com/DaveKingBerlin/procedural-detective",
    );
    expect(RELEASE_BASE_URL).toBe(
      "https://github.com/DaveKingBerlin/procedural-detective/releases/download",
    );
    expect(RELEASE_MANIFEST_URL).toBe("/releases/release-manifest.json");
  });

  it("builds the versioned artifact/checksum/quick-start URLs from config + manifest version", () => {
    // The download URL comes from the canonical config + the manifest version
    // + the manifest filename — never a scattered literal.
    expect(
      releaseArtifactUrl(
        CANONICAL.version,
        "pd-ollama-bridge-v1.0.0-windows-x64.zip",
      ),
    ).toBe(
      `${RELEASE_BASE_URL}/v1.0.0/pd-ollama-bridge-v1.0.0-windows-x64.zip`,
    );
    expect(checksumsUrl("1.0.0")).toBe(
      `${RELEASE_BASE_URL}/v1.0.0/${RELEASE_CHECKSUMS_FILENAME}`,
    );
    expect(quickStartUrl("1.0.0")).toBe(
      `${RELEASE_BASE_URL}/v1.0.0/${RELEASE_QUICKSTART_FILENAME}`,
    );
    // Without a manifest version the quick-start link degrades to the repo.
    expect(quickStartUrl(null)).toBe(RELEASE_REPOSITORY_URL);
    expect(sourceCodeUrl()).toBe(RELEASE_REPOSITORY_URL);
  });

  it("anchors the download section with a stable id for the §50 help link", () => {
    expect(RELEASE_DOWNLOAD_ANCHOR_ID).toBe("local-ai-ollama-download");
  });
});

describe("Phase 49 — manifest schema parse (allow-list, §19)", () => {
  it("parses the canonical manifest shape and keeps both platforms + x64", () => {
    const parsed = parseReleaseManifest(CANONICAL);
    expect(parsed).not.toBeNull();
    expect(parsed?.version).toBe("1.0.0");
    expect(parsed?.gitCommit).toBe("abcdef123");
    expect(parsed?.artifacts).toHaveLength(2);
    expect(findArtifact(parsed, "windows")?.filename).toBe(
      "pd-ollama-bridge-v1.0.0-windows-x64.zip",
    );
    expect(findArtifact(parsed, "linux")?.filename).toBe(
      "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz",
    );
  });

  it("accepts a placeholder manifest with null sha256/size (pre-publication)", () => {
    const parsed = parseReleaseManifest({
      version: "1.0.0",
      gitCommit: null,
      releasedAt: null,
      artifacts: [
        {
          platform: "windows",
          arch: "x64",
          filename: "pd-ollama-bridge-v1.0.0-windows-x64.zip",
          sha256: null,
          size: null,
        },
      ],
    });
    expect(parsed).not.toBeNull();
    expect(parsed?.artifacts[0].sha256).toBeNull();
    expect(parsed?.artifacts[0].size).toBeNull();
  });

  it("rejects a manifest without a valid version (unavailable state)", () => {
    expect(parseReleaseManifest(null)).toBeNull();
    expect(parseReleaseManifest({})).toBeNull();
    expect(parseReleaseManifest({ version: "not-a-version", artifacts: [] })).toBeNull();
    expect(parseReleaseManifest("nope")).toBeNull();
  });

  it("drops unknown platforms, path-traversal filenames and malformed arches", () => {
    const parsed = parseReleaseManifest({
      version: "1.0.0",
      artifacts: [
        { platform: "amiga", arch: "x64", filename: "evil.zip" },
        { platform: "windows", arch: "x64", filename: "../../etc/passwd" },
        { platform: "windows", arch: "x64", filename: "http://evil.example/x.zip" },
        { platform: "linux", arch: "x64", filename: "ok.tar.gz", size: -5 },
      ],
    });
    expect(parsed?.artifacts).toHaveLength(1);
    expect(parsed?.artifacts[0].filename).toBe("ok.tar.gz");
    expect(parsed?.artifacts[0].size).toBeNull();
  });

  it("accepts exactly-64 hex sha256 and normalises it to lowercase (ADV-49-010)", () => {
    const digest = "A".repeat(64);
    const parsed = parseReleaseManifest({
      version: "1.0.0",
      artifacts: [
        { platform: "windows", arch: "x64", filename: "w.zip", sha256: digest },
      ],
    });
    expect(parsed?.artifacts[0].sha256).toBe("a".repeat(64));
  });

  it("rejects/omits a hostile sha256 and never carries it into the typed artifact (ADV-49-010)", () => {
    const parsed = parseReleaseManifest({
      version: "1.0.0",
      artifacts: [
        { platform: "windows", arch: "x64", filename: "a.zip", sha256: "not-hex" },
        {
          platform: "linux",
          arch: "x64",
          filename: "b.tar.gz",
          sha256: "<script>alert(1)</script>",
        },
        { platform: "windows", arch: "x64", filename: "c.zip", sha256: "a".repeat(63) },
        { platform: "linux", arch: "x64", filename: "d.tar.gz", sha256: "a".repeat(65) },
        { platform: "windows", arch: "x64", filename: "e.zip", sha256: "javascript:alert(1)" },
      ],
    });
    expect(parsed).not.toBeNull();
    expect(parsed?.artifacts).toHaveLength(5);
    for (const artifact of parsed!.artifacts) {
      expect(artifact.sha256).toBeNull();
    }
    // The hostile strings never survive into the typed model.
    const serialized = JSON.stringify(parsed);
    expect(serialized).not.toContain("script");
    expect(serialized).not.toContain("not-hex");
    expect(serialized).not.toContain("javascript");
  });
});

describe("Phase 49 — §24 metadata + copy helpers", () => {
  it("labels the archive type from the filename", () => {
    expect(archiveTypeLabel("pd-ollama-bridge-v1.0.0-windows-x64.zip")).toBe("ZIP");
    expect(archiveTypeLabel("pd-ollama-bridge-v1.0.0-linux-x64.tar.gz")).toBe("TAR.GZ");
    expect(archiveTypeLabel("unknown.bin")).toBe("ARCHIVE");
  });

  it("formats the approximate size and omits it when unknown", () => {
    expect(formatApproxSize(18_400_000)).toBe("18.4 MB");
    expect(formatApproxSize(512_000)).toBe("512 KB");
    expect(formatApproxSize(null)).toBeNull();
    expect(formatApproxSize(0)).toBeNull();
  });

  it("renders the Windows x64 metadata line and the download button label", () => {
    const windows = findArtifact(parseReleaseManifest(CANONICAL), "windows");
    expect(windows).not.toBeNull();
    expect(artifactMetadataLine(windows!, "1.0.0")).toBe("Windows x64 · v1.0.0 · ZIP · 18.4 MB");
    expect(downloadButtonLabel("windows", "x64")).toBe("Download for Windows x64");
    expect(downloadButtonLabel("linux", "x64")).toBe("Download for Linux x64");
  });

  it("lists both required platforms, Windows first", () => {
    expect(RELEASE_PLATFORMS.map((entry) => entry.platform)).toEqual(["windows", "linux"]);
    expect(RELEASE_PLATFORMS.every((entry) => entry.arch === "x64")).toBe(true);
  });
});

describe("Phase 49 — loadReleaseManifest is fail-closed (§39/§40)", () => {
  function fakeFetch(payload: unknown, ok = true): typeof fetch {
    return vi.fn(async () => ({
      ok,
      status: ok ? 200 : 500,
      json: async () => payload,
    })) as unknown as typeof fetch;
  }

  it("fetches ONLY the same-origin manifest URL and parses the body", async () => {
    const fetchImpl = fakeFetch(CANONICAL);
    const manifest = await loadReleaseManifest(fetchImpl);
    expect(manifest?.version).toBe("1.0.0");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetchImpl).mock.calls[0][0]).toBe(RELEASE_MANIFEST_URL);
  });

  it("resolves null on a non-2xx response, a thrown fetch and an invalid body", async () => {
    await expect(loadReleaseManifest(fakeFetch(CANONICAL, false))).resolves.toBeNull();
    const throwing = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    await expect(loadReleaseManifest(throwing)).resolves.toBeNull();
    await expect(loadReleaseManifest(fakeFetch({ nope: true }))).resolves.toBeNull();
  });

  it("ADV-49-008 — resolves null when the fetch NEVER settles (no infinite hang)", async () => {
    const neverSettles = vi.fn(
      () => new Promise<Response>(() => undefined),
    ) as unknown as typeof fetch;
    await expect(
      loadReleaseManifest(neverSettles, RELEASE_MANIFEST_URL, 10),
    ).resolves.toBeNull();
  });

  it("ADV-49-008 — aborts a stalled fetch at the bound and resolves null", async () => {
    let aborted = false;
    const honorsAbort = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        if (signal !== undefined && signal !== null) {
          signal.addEventListener("abort", () => {
            aborted = true;
            reject(new DOMException("aborted", "AbortError"));
          });
        }
      });
    }) as unknown as typeof fetch;

    const manifest = await loadReleaseManifest(honorsAbort, RELEASE_MANIFEST_URL, 10);
    expect(manifest).toBeNull();
    expect(aborted).toBe(true);
  });

  it("exposes a bounded default timeout (the production call is never unbounded)", () => {
    expect(RELEASE_MANIFEST_TIMEOUT_MS).toBe(5000);
    expect(Number.isFinite(RELEASE_MANIFEST_TIMEOUT_MS)).toBe(true);
  });
});

describe("Phase 49 — the checked-in bundled manifest ships a valid v1.0.0", () => {
  it("parses and carries both platform artifacts", () => {
    const raw = JSON.parse(readFileSync(BUNDLED_MANIFEST, "utf8")) as unknown;
    const manifest = parseReleaseManifest(raw);
    expect(manifest, `bundled manifest at ${BUNDLED_MANIFEST} must satisfy the schema`).not.toBeNull();
    expect(manifest?.version).toBe("1.0.0");
    expect(findArtifact(manifest, "windows")).not.toBeNull();
    expect(findArtifact(manifest, "linux")).not.toBeNull();
    // Filenames follow the frozen Phase 49 artifact naming contract.
    expect(findArtifact(manifest, "windows")?.filename).toBe(
      "pd-ollama-bridge-v1.0.0-windows-x64.zip",
    );
    expect(findArtifact(manifest, "linux")?.filename).toBe(
      "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz",
    );
  });
});
