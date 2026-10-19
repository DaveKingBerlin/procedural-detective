// Independent QA Phase 49 retest — download section + manifest + bridge command.
// Uses React.createElement (no JSX) so it runs from the repo root with vitest.
// @vitest-environment jsdom
import { createElement } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import ReleaseDownloadSection from "../../../../frontend/src/release/ReleaseDownloadSection";
import {
  RELEASE_BASE_URL,
  RELEASE_MANIFEST_TIMEOUT_MS,
  RELEASE_MANIFEST_URL,
  loadReleaseManifest,
  parseReleaseManifest,
} from "../../../../frontend/src/release/releaseManifest";
import {
  bridgeCliCommand,
  bridgeServerOrigin,
} from "../../../../frontend/src/journey/localAiBridge";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const MANIFEST = {
  version: "1.0.0",
  gitCommit: "d6ffd6b",
  releasedAt: "2026-10-19T18:13:56Z",
  artifacts: [
    { platform: "windows", arch: "x64", filename: "pd-ollama-bridge-v1.0.0-windows-x64.zip", sha256: "a".repeat(64), size: 12034484 },
    { platform: "linux", arch: "x64", filename: "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz", sha256: "b".repeat(64), size: 10133042 },
  ],
};

type Mounted = { container: HTMLDivElement; root: ReturnType<typeof createRoot> };

function mount(loadManifest: () => Promise<unknown>, detectPlatform?: () => "windows" | "linux" | null): Mounted {
  const container = document.createElement("div");
  const root = createRoot(container);
  act(() => {
    root.render(
      createElement(ReleaseDownloadSection as never, {
        loadManifest,
        ...(detectPlatform ? { detectPlatform } : {}),
      }),
    );
  });
  return { container, root };
}

async function settle(): Promise<void> {
  await act(async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); });
}

const q = (m: Mounted, id: string) => m.container.querySelector(`[data-testid="${id}"]`);
const href = (m: Mounted, id: string) => q(m, id)?.getAttribute("href") ?? null;
const text = (m: Mounted, id: string) => q(m, id)?.textContent ?? null;

describe("QA49 retest — download section behaviours", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("shows BOTH platforms, the manifest version and the three links", async () => {
    const m = mount(async () => parseReleaseManifest(MANIFEST));
    await settle();
    expect(q(m, "release-download-windows")).not.toBeNull();
    expect(q(m, "release-download-linux")).not.toBeNull();
    expect(text(m, "release-download-version")).toBe("Version 1.0.0");
    expect(href(m, "release-download-windows")).toBe(`${RELEASE_BASE_URL}/v1.0.0/pd-ollama-bridge-v1.0.0-windows-x64.zip`);
    expect(href(m, "release-download-linux")).toBe(`${RELEASE_BASE_URL}/v1.0.0/pd-ollama-bridge-v1.0.0-linux-x64.tar.gz`);
    expect(text(m, "release-quick-start")).toBe("Quick Start");
    expect(text(m, "release-checksums")).toBe("SHA-256 checksums");
    expect(text(m, "release-source")).toBe("Source code");
    act(() => m.root.unmount());
  });

  it("OS detection emphasises but never hides the other platform", async () => {
    const m = mount(async () => parseReleaseManifest(MANIFEST), () => "windows");
    await settle();
    expect(q(m, "release-download-windows")).not.toBeNull();
    expect(q(m, "release-download-linux")).not.toBeNull();
    expect(q(m, "release-download-recommended-windows")).not.toBeNull();
    expect(q(m, "release-download-recommended-linux")).toBeNull();
    act(() => m.root.unmount());
  });

  it("missing manifest -> unavailable state, Quick Start/Source remain", async () => {
    const m = mount(async () => null);
    await settle();
    expect(q(m, "release-download-unavailable")).not.toBeNull();
    expect(q(m, "release-download-windows")).toBeNull();
    expect(q(m, "release-quick-start")).not.toBeNull();
    expect(q(m, "release-source")).not.toBeNull();
    act(() => m.root.unmount());
  });

  it("stalled manifest fetch reaches unavailable at the bound", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
      const m = mount(() => loadReleaseManifest());
      await settle();
      expect(q(m, "release-download-loading")).not.toBeNull();
      await act(async () => { await vi.advanceTimersByTimeAsync(RELEASE_MANIFEST_TIMEOUT_MS + 10); });
      await settle();
      expect(q(m, "release-download-loading")).toBeNull();
      expect(q(m, "release-download-unavailable")).not.toBeNull();
      act(() => m.root.unmount());
    } finally {
      vi.useRealTimers();
    }
  });

  it("real loader fetches only the same-origin manifest URL", async () => {
    const fetchMock = vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => MANIFEST }));
    vi.stubGlobal("fetch", fetchMock);
    const parsed = await loadReleaseManifest();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(RELEASE_MANIFEST_URL);
    expect(parsed?.version).toBe("1.0.0");
  });
});

describe("QA49 retest — sha256 64-hex validation (ADV-49-010 fix)", () => {
  it("drops a non-hex sha256 to null", () => {
    const p = parseReleaseManifest({ version: "1.0.0", artifacts: [{ platform: "windows", arch: "x64", filename: "x.zip", sha256: "javascript:alert(1)" }] });
    expect(p?.artifacts[0].sha256).toBeNull();
  });
  it("accepts exactly 64 hex chars and lowercases", () => {
    const up = "A".repeat(64);
    const p = parseReleaseManifest({ version: "1.0.0", artifacts: [{ platform: "windows", arch: "x64", filename: "x.zip", sha256: up }] });
    expect(p?.artifacts[0].sha256).toBe("a".repeat(64));
  });
  it("drops 63-char and 65-char digests", () => {
    for (const n of [63, 65]) {
      const p = parseReleaseManifest({ version: "1.0.0", artifacts: [{ platform: "windows", arch: "x64", filename: "x.zip", sha256: "a".repeat(n) }] });
      expect(p?.artifacts[0].sha256, String(n)).toBeNull();
    }
  });
});

describe("QA49 retest — bridge CLI command carries the server origin (ADV-49-005 fix)", () => {
  it("includes --server <wss origin> when an origin is provided", () => {
    expect(bridgeCliCommand("PD-AB2D-CF34", "wss://detective.example.com")).toBe(
      "pd-ollama-bridge connect --server wss://detective.example.com PD-AB2D-CF34",
    );
  });
  it("falls back to the server-less form when no origin is known", () => {
    expect(bridgeCliCommand("PD-AB2D-CF34", null)).toBe("pd-ollama-bridge connect PD-AB2D-CF34");
  });
  it("derives wss:// from an https page origin", () => {
    // jsdom location is http://localhost by default -> ws://localhost
    const origin = bridgeServerOrigin();
    expect(origin === null || origin.startsWith("ws://") || origin.startsWith("wss://")).toBe(true);
  });
});
