import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { GenerationCapabilitiesResponse } from "../api/types";
import {
  GenerationModeDisplay,
  type GenerationModeDisplayProps,
} from "./generationModeSelector";

/**
 * Phase 21 F-03 — the generation-mode area is a READ-ONLY display driven
 * solely by the backend generation-capabilities DTO. The interactive provider
 * selector was REMOVED because the "selected" mode was never sent to the
 * backend (process-global GENERATION_PROVIDER) — so this component:
 *
 *   - renders NOTHING while capabilities are unknown (no claim before the
 *     backend reports);
 *   - shows the NEUTRAL reachability line "Generation mode: Available once
 *     the service is reachable." on a DTO-UNAVAILABLE payload (empty
 *     allowlist after a fetch failure / malformed) — NEVER the
 *     deterministic-demo story (DEF-097);
 *   - shows the honest static "Demo mode active" notice + the truthful
 *     "Generation mode: Deterministic demo" line ONLY on a REPORTED demo-only
 *     backend (fake-configured or older-server availability-derived);
 *   - shows the single read-only line "Generation mode: Local AI — <model> —
 *     Ready" when the backend reports the corresponding mode available, and
 *     "Generation mode: Local AI — <model> — Unavailable" / "Generation mode:
 *     <live label> — Unavailable" for a CONFIGURED local/live backend whose
 *     probe FAILED (DEF-096 — the runtime still runs that provider, so the
 *     line stays per-mode and never claims "Deterministic demo");
 *   - NEVER renders an interactive provider control: no <select>, no option,
 *     no onChange, no testid implying a switch, no "selected value" markup.
 *
 * Purely static rendering — no network.
 */

const ALL_THREE: GenerationCapabilitiesResponse = {
  modes: [
    { id: "demo", available: true },
    { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
    { id: "live", available: true, label: "Cloud AI" },
  ],
};

function render(props: Partial<GenerationModeDisplayProps> = {}): string {
  return renderToStaticMarkup(
    <GenerationModeDisplay capabilities={ALL_THREE} {...props} />,
  );
}

describe("read-only generation-mode display — truthfulness (Phase 21 F-03)", () => {
  it("renders nothing while capabilities are unknown (no claim before the backend reports)", () => {
    expect(render({ capabilities: null })).toBe("");
  });

  it("demo-only backend -> the deterministic line + the honest notice, NO selector and NO options", () => {
    const html = render({
      capabilities: {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
          { id: "live", available: false, label: "Cloud AI" },
        ],
      },
    });
    expect(html).toContain(
      "Generation mode: Deterministic demo",
    );
    expect(html).toContain('data-testid="generation-mode-demo-notice"');
    expect(html).toContain("Demo mode active");
    // The demo-only backend must NOT claim local/live availability.
    expect(html).not.toContain("Local AI");
    expect(html).not.toContain("Cloud AI");
    expect(html).not.toContain("Ready");
    // No selector container, no <select>, no options.
    expect(html).not.toContain('data-testid="generation-mode-selector"');
    expect(html).not.toContain("<select");
    expect(html).not.toContain("<option");
  });

  it("local available -> the single read-only line 'Local AI — <model> — Ready'", () => {
    const html = render({
      capabilities: {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        ],
      },
    });
    expect(html).toContain('data-testid="generation-mode-selector"');
    expect(html).toContain("Generation mode: Local AI — qwen2.5:7b — Ready");
    // The demo notice is replaced by the authoritative line when local runs.
    expect(html).not.toContain('data-testid="generation-mode-demo-notice"');
    expect(html).not.toContain("Demo mode active");
  });

  it("local available without a DTO model -> 'Local AI — Ready' (never a fabricated model name)", () => {
    const html = render({
      capabilities: {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI" },
        ],
      },
    });
    expect(html).toContain("Generation mode: Local AI — Ready");
    expect(html).toContain('data-testid="generation-mode-selector"');
  });

  it("live available -> the read-only line carries the DTO capability label", () => {
    const html = render({
      capabilities: {
        modes: [
          { id: "demo", available: true },
          { id: "live", available: true, label: "Cloud AI" },
        ],
      },
    });
    expect(html).toContain('data-testid="generation-mode-selector"');
    expect(html).toContain("Generation mode: Cloud AI");
  });

  it("live wins over local when both are reported available (ONE authoritative provider)", () => {
    // The backend runs a single process-global provider; the display (like
    // providerMode.effectiveProviderMode) must resolve to ONE truthful line
    // and can never offer a second, contradicting "choice".
    const html = render();
    expect(html).toContain("Generation mode: Cloud AI");
    expect(html).not.toContain("Local AI");
  });

  it("DEF-097 — DTO-unavailable payload (empty allowlist after a fetch failure) -> the NEUTRAL reachability line, NO demo story", () => {
    // Phase 21B / DEF-097: the frontend cannot know the provider when the DTO
    // did not report, so the deterministic "Demo mode active" notice + line
    // must NOT appear — they would contradict the page's neutral CTA. The
    // read-only line is the neutral reachability copy instead.
    const html = render({ capabilities: { modes: [] } });
    expect(html).toContain(
      "Generation mode: Available once the service is reachable.",
    );
    expect(html).not.toContain("Generation mode: Deterministic demo");
    expect(html).not.toContain('data-testid="generation-mode-demo-notice"');
    expect(html).not.toContain("Demo mode active");
    expect(html).toContain('data-testid="generation-mode-line"');
    expect(html).not.toContain("<select");
  });

  it("DEF-096 — a configured local backend with a FAILED probe shows the truthful 'Unavailable' per-mode line (never the demo story)", () => {
    const html = render({
      capabilities: {
        configuredProvider: "ollama",
        modes: [
          { id: "demo", available: false },
          { id: "local", available: false, label: "Local AI", model: "llama3.2:3b" },
        ],
      },
    });
    // The runtime WILL still run the Ollama provider: the line stays per-mode
    // with the availability-appropriate tag, and NO demo claim appears.
    expect(html).toContain("Generation mode: Local AI — llama3.2:3b — Unavailable");
    expect(html).not.toContain("Generation mode: Deterministic demo");
    expect(html).not.toContain('data-testid="generation-mode-demo-notice"');
    expect(html).not.toContain("Demo mode active");
    expect(html).toContain('data-testid="generation-mode-selector"');
  });

  it("hostile DTO — never renders host/IP, credential, prompt or diagnostic strings", () => {
    const hostile: GenerationCapabilitiesResponse = {
      modes: [
        { id: "demo", available: true },
        {
          id: "local",
          available: true,
          label: "Local AI",
          model: "http://127.0.0.1:11434 — apiKey=hunter2 — prompt=topsecret",
        },
        {
          id: "live",
          available: true,
          label: "http://evil.example",
          model: "data:text/html;base64,PHN0",
        },
      ],
    };
    const html = render({ capabilities: hostile });
    expect(html).not.toContain("127.0.0.1");
    expect(html).not.toContain("hunter2");
    expect(html).not.toContain("topsecret");
    expect(html).not.toContain("11434");
    expect(html).not.toContain("evil.example");
    // The frozen public labels survive as the safe fallback.
    expect(html).toContain("Generation mode: Cloud AI");
  });
});

describe("read-only generation-mode display — no interactive provider control", () => {
  it("contains NO interactive <select> / listbox for the provider mode in any state", () => {
    for (const capabilities of [
      ALL_THREE,
      { modes: [{ id: "demo", available: true }] },
      { modes: [] },
      null,
    ]) {
      const html = render({ capabilities });
      expect(html).not.toContain("<select");
      expect(html).not.toContain("role=\"listbox\"");
      expect(html).not.toContain("onChange");
    }
  });

  it("carries NO testid implying a switch (no generation-mode-select, no option testids)", () => {
    const html = render();
    expect(html).not.toContain('data-testid="generation-mode-select"');
    expect(html).not.toContain('data-testid="generation-mode-select-option"');
    expect(html).not.toContain("value=\"local\"");
    expect(html).not.toContain("value=\"live\"");
  });
});