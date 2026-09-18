import { describe, expect, it, vi } from "vitest";
import { loadGenerationCapabilities } from "./useGenerationCapabilities";

/**
 * Phase 16 Track B — the capabilities probe simplifies to a parsed allowlist
 * and NEVER throws: an endpoint failure resolves to the demo-only payload, so
 * the UI can never be tricked into claiming Local/Cloud AI availability. (The
 * hook's useEffect wiring is inert under static render; this covers the probe
 * exactly like useBackendStatus.test.ts covers checkBackendStatus.)
 */

describe("loadGenerationCapabilities (Phase 16 Track B)", () => {
  it("parses the fetched payload through the allowlist (unknown fields dropped)", async () => {
    const probe = vi.fn(async () => ({
      modes: [
        { id: "demo", available: true },
        {
          id: "local",
          available: true,
          label: "Local AI",
          model: "qwen2.5:7b",
          url: "http://127.0.0.1:11434",
          apiKey: "hunter2",
        },
      ],
    }));

    const capabilities = await loadGenerationCapabilities(probe);

    expect(capabilities).toEqual({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
  });

  it("never throws: an endpoint/reject failure resolves to the demo-only allowlist", async () => {
    const probe = vi.fn(async () => {
      throw new Error("backend unreachable");
    });

    expect(await loadGenerationCapabilities(probe)).toEqual({ modes: [] });
  });
});