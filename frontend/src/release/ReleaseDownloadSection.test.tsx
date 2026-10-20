// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReleaseDownloadSection, {
  type ReleaseDownloadSectionProps,
} from "./ReleaseDownloadSection";
import {
  RELEASE_BASE_URL,
  RELEASE_MANIFEST_TIMEOUT_MS,
  RELEASE_MANIFEST_URL,
  releaseArtifactUrl,
  type ReleaseManifest,
} from "./releaseManifest";

/**
 * Phase 49 §22/§23/§24/§40/§46 — the "Local AI with Ollama" download section.
 *
 * Real jsdom interaction tests with an injectable loader: both platforms are
 * always visible, the version comes from the manifest, the three secondary
 * links exist, the download URL is the canonical config/manifest URL, OS
 * detection only emphasises (never hides) a platform, and NO download/navigation
 * happens on mount. Loading/success/failure states are all asserted.
 */

declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const MANIFEST: ReleaseManifest = {
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

interface Mounted {
  container: HTMLDivElement;
  root: ReturnType<typeof createRoot>;
}

function mount(props: ReleaseDownloadSectionProps): Mounted {
  const container = document.createElement("div");
  const root = createRoot(container);
  act(() => {
    root.render(<ReleaseDownloadSection {...props} />);
  });
  return { container, root };
}

async function settle(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

function q(mounted: Mounted, testid: string): Element | null {
  return mounted.container.querySelector(`[data-testid="${testid}"]`);
}

function hrefOf(mounted: Mounted, testid: string): string | null {
  const el = q(mounted, testid);
  return el === null ? null : el.getAttribute("href");
}

const textOf = (el: Element | null): string | null => (el === null ? null : el.textContent ?? null);

function unmount(mounted: Mounted): void {
  act(() => mounted.root.unmount());
}

describe("Phase 49 — download section: canonical manifest ready state (§22/§24/§46)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function renderReady(): Promise<Mounted> {
    const mounted = mount({ loadManifest: async () => MANIFEST });
    await settle();
    return mounted;
  }

  it("shows the Windows x64 download with the canonical manifest URL", async () => {
    const mounted = await renderReady();
    expect(q(mounted, "release-download-windows")).not.toBeNull();
    expect(textOf(q(mounted, "release-download-windows"))).toBe("Download for Windows x64");
    expect(hrefOf(mounted, "release-download-windows")).toBe(
      `${RELEASE_BASE_URL}/v1.0.0/pd-ollama-bridge-v1.0.0-windows-x64.zip`,
    );
    expect(hrefOf(mounted, "release-download-windows")).toBe(
      releaseArtifactUrl(MANIFEST.version, MANIFEST.artifacts[0].filename),
    );
    expect(hrefOf(mounted, "release-download-windows")).toBe(
      `${RELEASE_BASE_URL}/v1.0.0/pd-ollama-bridge-v1.0.0-windows-x64.zip`,
    );
    unmount(mounted);
  });

  it("shows the Linux x64 download with the canonical manifest URL", async () => {
    const mounted = await renderReady();
    expect(q(mounted, "release-download-linux")).not.toBeNull();
    expect(textOf(q(mounted, "release-download-linux"))).toBe("Download for Linux x64");
    expect(hrefOf(mounted, "release-download-linux")).toBe(
      `${RELEASE_BASE_URL}/v1.0.0/pd-ollama-bridge-v1.0.0-linux-x64.tar.gz`,
    );
    unmount(mounted);
  });

  it("displays the version from the manifest (never hard-coded)", async () => {
    const mounted = mount({
      loadManifest: async () => ({ ...MANIFEST, version: "2.3.4" }),
    });
    await settle();
    expect(textOf(q(mounted, "release-download-version"))).toBe("Version 2.3.4");
    expect(hrefOf(mounted, "release-download-windows")).toContain("/v2.3.4/");
    unmount(mounted);
  });

  it("renders the section heading + Requires Ollama copy", async () => {
    const mounted = await renderReady();
    expect(textOf(q(mounted, "release-download-section"))).toContain("Local AI with Ollama");
    expect(textOf(q(mounted, "release-download-section"))).toContain("Requires Ollama.");
    unmount(mounted);
  });

  it("renders Quick Start, SHA-256 checksums and Source code links (§22)", async () => {
    const mounted = await renderReady();
    expect(textOf(q(mounted, "release-quick-start"))).toBe("Quick Start");
    expect(hrefOf(mounted, "release-quick-start")).toBe(
      "https://github.com/DaveKingBerlin/procedural-detective/blob/v1.0.0/bridge/QUICKSTART.md",
    );
    expect(textOf(q(mounted, "release-checksums"))).toBe("SHA-256 checksums");
    expect(hrefOf(mounted, "release-checksums")).toBe(`${RELEASE_BASE_URL}/v1.0.0/SHA256SUMS.txt`);
    expect(hrefOf(mounted, "release-download-linux")).toBe(
      `${RELEASE_BASE_URL}/v1.0.0/pd-ollama-bridge-v1.0.0-linux-x64.tar.gz`,
    );
    expect(textOf(q(mounted, "release-source"))).toBe("Source code");
    expect(hrefOf(mounted, "release-source")).toBe(
      "https://github.com/DaveKingBerlin/procedural-detective",
    );
    unmount(mounted);
  });

  it("shows per-option metadata (platform · arch · version · archive type · size)", async () => {
    const mounted = await renderReady();
    expect(textOf(q(mounted, "release-download-meta-windows"))).toBe(
      "Windows x64 · v1.0.0 · ZIP · 18.4 MB",
    );
    expect(textOf(q(mounted, "release-download-meta-linux"))).toBe(
      "Linux x64 · v1.0.0 · TAR.GZ · 17.1 MB",
    );
    // A manifest without a size simply omits that segment.
    const withoutSize = mount({
      loadManifest: async () => ({
        ...MANIFEST,
        artifacts: [{ ...MANIFEST.artifacts[0], size: null }],
      }),
    });
    await settle();
    expect(textOf(q(withoutSize, "release-download-meta-windows"))).toBe(
      "Windows x64 · v1.0.0 · ZIP",
    );
    unmount(withoutSize);
    unmount(mounted);
  });
});

describe("Phase 49 — OS detection emphasises but NEVER hides the alternative (§23/§46)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("a Windows browser shows BOTH downloads and recommends only Windows", async () => {
    const mounted = mount({
      loadManifest: async () => MANIFEST,
      detectPlatform: () => "windows",
    });
    await settle();
    expect(q(mounted, "release-download-windows")).not.toBeNull();
    expect(q(mounted, "release-download-linux")).not.toBeNull();
    expect(q(mounted, "release-download-recommended-windows")).not.toBeNull();
    expect(q(mounted, "release-download-recommended-linux")).toBeNull();
    unmount(mounted);
  });

  it("a Linux browser shows BOTH downloads and recommends only Linux", async () => {
    const mounted = mount({
      loadManifest: async () => MANIFEST,
      detectPlatform: () => "linux",
    });
    await settle();
    expect(q(mounted, "release-download-windows")).not.toBeNull();
    expect(q(mounted, "release-download-linux")).not.toBeNull();
    expect(q(mounted, "release-download-recommended-linux")).not.toBeNull();
    expect(q(mounted, "release-download-recommended-windows")).toBeNull();
    unmount(mounted);
  });

  it("unknown OS shows both downloads with no recommendation", async () => {
    const mounted = mount({
      loadManifest: async () => MANIFEST,
      detectPlatform: () => null,
    });
    await settle();
    expect(q(mounted, "release-download-windows")).not.toBeNull();
    expect(q(mounted, "release-download-linux")).not.toBeNull();
    expect(q(mounted, "release-download-recommended-windows")).toBeNull();
    expect(q(mounted, "release-download-recommended-linux")).toBeNull();
    unmount(mounted);
  });
});

describe("Phase 49 — no auto-download / no programmatic navigation (§23/§46)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("fetches ONLY the same-origin manifest; artifact URLs are never fetched or navigated to", async () => {
    const fetchMock = vi.fn(async (_url: string) => ({
      ok: true,
      status: 200,
      json: async () => MANIFEST,
    }));
    vi.stubGlobal("fetch", fetchMock);
    const openSpy = vi.fn();
    window.open = openSpy as unknown as typeof window.open;
    const locationBefore = window.location.href;

    const mounted = mount({}); // DEFAULT loader -> real fetch of the manifest
    await settle();

    // The only network call is the same-origin manifest.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(RELEASE_MANIFEST_URL);
    for (const call of fetchMock.mock.calls) {
      const target = String(call[0]);
      expect(target).not.toContain(".zip");
      expect(target).not.toContain(".tar.gz");
      expect(target).not.toContain("/releases/download");
    }
    // The artifact URLs exist ONLY as anchor hrefs.
    expect(hrefOf(mounted, "release-download-windows")).toContain(".zip");
    expect(hrefOf(mounted, "release-download-linux")).toContain(".tar.gz");
    // No window.open and no location change from mounting.
    expect(openSpy).not.toHaveBeenCalled();
    expect(window.location.href).toBe(locationBefore);
    unmount(mounted);
  });
});

describe("Phase 49 — loading / failure states (§39/§40/§46)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state before the manifest resolves, then the downloads", async () => {
    let resolve!: (manifest: ReleaseManifest | null) => void;
    const pending = new Promise<ReleaseManifest | null>((r) => {
      resolve = r;
    });
    const mounted = mount({ loadManifest: () => pending });
    expect(q(mounted, "release-download-loading")).not.toBeNull();
    expect(q(mounted, "release-download-windows")).toBeNull();

    await act(async () => {
      resolve(MANIFEST);
      await pending;
    });
    await settle();
    expect(q(mounted, "release-download-loading")).toBeNull();
    expect(q(mounted, "release-download-windows")).not.toBeNull();
    unmount(mounted);
  });

  it("shows the unavailable state (with Quick Start + Source) when the manifest is null", async () => {
    const mounted = mount({ loadManifest: async () => null });
    await settle();
    expect(q(mounted, "release-download-unavailable")).not.toBeNull();
    expect(q(mounted, "release-download-windows")).toBeNull();
    expect(q(mounted, "release-download-linux")).toBeNull();
    expect(q(mounted, "release-download-version")).toBeNull();
    // Quick Start (degraded to the repo) and Source stay available.
    expect(q(mounted, "release-quick-start")).not.toBeNull();
    expect(hrefOf(mounted, "release-quick-start")).toBe(
      "https://github.com/DaveKingBerlin/procedural-detective",
    );
    expect(q(mounted, "release-source")).not.toBeNull();
    // No version -> no checksums link.
    expect(q(mounted, "release-checksums")).toBeNull();
    unmount(mounted);
  });

  it("degrades to the unavailable state when the loader rejects", async () => {
    const mounted = mount({
      loadManifest: async () => {
        throw new Error("manifest host unreachable");
      },
    });
    await settle();
    expect(q(mounted, "release-download-unavailable")).not.toBeNull();
    expect(q(mounted, "release-download-windows")).toBeNull();
    unmount(mounted);
  });

  it("ADV-49-008 — a STALLED manifest fetch does not block the section: it reaches unavailable at the bound", async () => {
    vi.useFakeTimers();
    try {
      // A hanging same-origin request: the bounded loader aborts it.
      vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
      const mounted = mount({}); // default loader -> the real bounded fetch
      await settle();
      expect(q(mounted, "release-download-loading")).not.toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(RELEASE_MANIFEST_TIMEOUT_MS + 1);
      });
      await settle();

      expect(q(mounted, "release-download-loading")).toBeNull();
      expect(q(mounted, "release-download-unavailable")).not.toBeNull();
      expect(q(mounted, "release-download-windows")).toBeNull();
      unmount(mounted);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not break the rest of the page when one platform is missing from the manifest", async () => {
    const mounted = mount({
      loadManifest: async () => ({
        ...MANIFEST,
        artifacts: [MANIFEST.artifacts[0]], // windows only
      }),
    });
    await settle();
    expect(q(mounted, "release-download-windows")).not.toBeNull();
    expect(q(mounted, "release-download-linux")).toBeNull();
    expect(q(mounted, "release-download-missing-linux")).not.toBeNull();
    expect(textOf(q(mounted, "release-download-version"))).toBe("Version 1.0.0");
    unmount(mounted);
  });
});
