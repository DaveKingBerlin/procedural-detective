import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { GenerationCapabilitiesResponse } from "../api/types";
import {
  GenerationModeSelector,
  type GenerationModeSelectorProps,
} from "./generationModeSelector";

/**
 * Phase 16 Track B — selector rendering rules applied to the parsed allowlist
 * DTO: Demo is always offered, Local AI / Cloud AI only when available,
 * unavailable modes are never rendered as options, a demo-only backend shows
 * the static "Demo mode active" notice instead, and a selection that is no
 * longer offerable falls back to Demo. Purely static rendering — no network.
 */

const ALL_THREE: GenerationCapabilitiesResponse = {
  modes: [
    { id: "demo", available: true },
    { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
    { id: "live", available: true, label: "Cloud AI" },
  ],
};

function render(props: Partial<GenerationModeSelectorProps> = {}): string {
  return renderToStaticMarkup(
    <GenerationModeSelector
      capabilities={ALL_THREE}
      value="demo"
      onSelect={() => {}}
      {...props}
    />,
  );
}

describe("mode selector rendering", () => {
  it("renders the selector with Demo, Local AI and Cloud AI when all are available", () => {
    const html = render();
    expect(html).toContain('data-testid="generation-mode-selector"');
    expect(html).toContain('data-testid="generation-mode-select"');
    expect(html).toContain('<option value="demo"');
    expect(html).toContain(">Demo</option>");
    expect(html).toContain(
      '<option value="local">Local AI — qwen2.5:7b — Ready</option>',
    );
    expect(html).toContain('<option value="live">Cloud AI</option>');
  });

  it("never renders an unavailable mode as an option", () => {
    const html = render({
      capabilities: {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
          { id: "live", available: false, label: "Cloud AI" },
        ],
      },
    });
    expect(html).not.toContain("Local AI");
    expect(html).not.toContain("Cloud AI");
    expect(html).not.toContain("Unavailable");
    expect(html).toContain("Demo");
  });

  it("renders no selector when only Demo is offerable — the static notice instead", () => {
    const html = render({
      capabilities: {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: false, label: "Local AI" },
        ],
      },
    });
    expect(html).not.toContain('data-testid="generation-mode-selector"');
    expect(html).not.toContain("<option");
    expect(html).toContain('data-testid="generation-mode-demo-notice"');
    expect(html).toContain("Demo mode active");
  });

  it("shows the static notice when the backend payload omits demo entirely (fallback)", () => {
    const html = render({ capabilities: { modes: [] } });
    expect(html).not.toContain('data-testid="generation-mode-selector"');
    expect(html).toContain('data-testid="generation-mode-demo-notice"');
    expect(html).toContain("Demo mode active");
  });

  it("renders nothing while capabilities are unknown (no claim before the backend reports)", () => {
    expect(render({ capabilities: null })).toBe("");
  });

  it("falls back to Demo when the selected value is no longer offered", () => {
    const html = render({
      capabilities: {
        modes: [
          { id: "demo", available: true },
          { id: "live", available: true, label: "Cloud AI" },
        ],
      },
      value: "local", // local is not offered by this fixture
    });
    expect(html).not.toContain('value="local"');
    expect(html).toContain('<option value="demo" selected="">Demo</option>');
  });

  it("Phase 18A — a 'local selected but backend says local unavailable' state is never rendered", () => {
    // The selector can never render a select holding value="local" when the
    // backend reports local unavailable: only the honest demo notice exists.
    const html = render({
      capabilities: {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
        ],
      },
      value: "local", // stale/tampered stored mode on a demo-only backend
    });
    expect(html).not.toContain("option");
    expect(html).not.toContain('value="local"');
    expect(html).not.toContain("Local AI");
    expect(html).toContain('data-testid="generation-mode-demo-notice"');
    expect(html).toContain("Demo mode active");
  });

  it("keeps a valid selected value (no host/IP, URL or diagnostics in the markup)", () => {
    const html = render({ value: "local" });
    expect(html).toContain(
      '<option value="local" selected="">Local AI — qwen2.5:7b — Ready</option>',
    );
  });
});

describe("mode selector — no sensitive material ever reaches the markup", () => {
  it("never renders host/IP, credential, prompt or diagnostic strings from a hostile DTO", () => {
    const hostile: GenerationCapabilitiesResponse = {
      modes: [
        { id: "demo", available: true },
        {
          id: "local",
          available: true,
          label: "Local AI",
          model: "http://127.0.0.1:11434 — apiKey=hunter2 — prompt=topsecret",
        },
      ],
    };
    const html = render({ capabilities: hostile });
    expect(html).not.toContain("127.0.0.1");
    expect(html).not.toContain("hunter2");
    expect(html).not.toContain("topsecret");
  });
});