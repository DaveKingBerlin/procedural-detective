import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { GenerationCapabilitiesResponse } from "../api/types";
import NewCasePage from "./new";

/**
 * Phase 25 §16 — the /new route renders the provider selector from the
 * additive capabilities offer:
 *   - selector renders next to the generation controls;
 *   - the known providers (Fake / Ollama / Frontier) appear with their labels;
 *   - the current/default provider is preselected;
 *   - an unavailable provider is visible but disabled with the safe reason;
 *   - no secret/config URL is ever rendered;
 *   - an OLDER server (no `providers` offer) keeps the pre-25 form
 *     byte-identical (no selector).
 * Statically rendered (react-dom/server): deterministic, no network, effects
 * are not required for the static surface (the route resolves the selection
 * once capabilities arrive).
 */

const CAPS_WITH_PROVIDERS: GenerationCapabilitiesResponse = {
  modes: [{ id: "demo", available: true }],
  configuredProvider: "fake",
  defaultProvider: "fake",
  providers: [
    { id: "fake", label: "Demo / Fake", available: true, model: null, reason: null },
    {
      id: "ollama",
      label: "Local Ollama",
      available: true,
      defaultModel: "qwen2.5:1.5b",
      manualModelEntry: true,
      transports: {
        server: { available: true, reason: null },
        bridge: { available: true, connected: false, reason: "not_connected" },
      },
    },
    { id: "frontier", label: "Frontier", available: false, model: null, reason: "not_configured" },
  ],
};

function renderWith(capabilities: GenerationCapabilitiesResponse | null): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/new"]}>
      <NewCasePage capabilities={capabilities} />
    </MemoryRouter>,
  );
}

describe("/new — Phase 25 provider selector (additive capabilities offer)", () => {
  it("renders the provider selector near the generation controls", () => {
    const markup = renderWith(CAPS_WITH_PROVIDERS);
    expect(markup).toContain('data-testid="generation-provider-selector"');
    expect(markup).toContain("AI Provider");
  });

  it("lists the three known providers with their operable labels", () => {
    const markup = renderWith(CAPS_WITH_PROVIDERS);
    expect(markup).toContain('data-testid="generation-provider-fake"');
    expect(markup).toContain("Demo / Fake");
    expect(markup).toContain("No external AI request");
    expect(markup).toContain('data-testid="generation-provider-ollama"');
    expect(markup).toContain("Local Ollama");
    expect(markup).toContain('data-testid="generation-provider-frontier"');
    expect(markup).toContain("Frontier");
  });

  it("preselects the server default provider (fake here)", () => {
    const markup = renderWith(CAPS_WITH_PROVIDERS);
    expect(markup).toContain('name="generation-provider" checked="" value="fake"');
  });

  it("an unavailable provider is visible but disabled with the safe reason", () => {
    const markup = renderWith(CAPS_WITH_PROVIDERS);
    expect(markup).toContain('data-testid="generation-provider-frontier-reason"');
    expect(markup).toContain("not configured");
    expect(markup).toMatch(
      /data-testid="generation-provider-frontier"[\s\S]*<input[^>]*disabled=""[^>]*>/,
    );
  });

  it("DEFAULT-provider preselection honors the server's `defaultProvider` when it differs", () => {
    const caps: GenerationCapabilitiesResponse = {
      modes: [],
      defaultProvider: "ollama",
      providers: [
        { id: "fake", available: true },
        { id: "ollama", available: true, defaultModel: "qwen2.5:1.5b" },
        { id: "frontier", available: false, reason: "not_configured" },
      ],
    };
    const markup = renderWith(caps);
    expect(markup).toContain('name="generation-provider" checked="" value="ollama"');
    expect(markup).toContain('data-testid="generation-ollama-controls"');
    // The Ollama model input initializes from the CONFIGURED default — never a
    // hard-coded model name.
    expect(markup).toContain('value="qwen2.5:1.5b"');
  });

  it("an older server (no `providers` offer) renders NO provider selector (byte-identical pre-25 form)", () => {
    const markup = renderWith({ modes: [{ id: "demo", available: true }] });
    expect(markup).not.toContain('data-testid="generation-provider-selector"');
    expect(markup).toContain('data-testid="generation-mode-demo-notice"');
    expect(markup).toContain("Generation mode: Deterministic demo");
  });

  it("LOADING state (capabilities unknown/null): the page stays usable and renders no selector yet (§16.14)", () => {
    const markup = renderWith(null);
    expect(markup).not.toContain('data-testid="generation-provider-selector"');
    expect(markup).toContain('data-testid="prompt-input"');
    expect(markup).toContain('data-testid="generate-case"');
    expect(markup).toContain('data-testid="difficulty-select"');
  });

  it("never renders a secret/config URL or raw diagnostic from a hostile provider offer", () => {
    const hostile: GenerationCapabilitiesResponse = {
      modes: [],
      defaultProvider: "ollama",
      providers: [
        {
          id: "fake",
          available: true,
          label: "Demo / Fake",
        },
        {
          id: "ollama",
          label: "http://127.0.0.1:11434 — apiKey=hunter2",
          available: false,
          reason: "https://secret-endpoint.example — token=abc",
          defaultModel: "llama3@192.168.1.9",
        },
        { id: "frontier", available: false, reason: "exception: shell out" },
      ],
    };
    const markup = renderWith(hostile);
    expect(markup).not.toContain("127.0.0.1");
    expect(markup).not.toContain("11434");
    expect(markup).not.toContain("hunter2");
    expect(markup).not.toContain("secret-endpoint");
    expect(markup).not.toContain("192.168.1.9");
    expect(markup).not.toContain("exception");
    expect(markup).not.toContain("shell out");
    // Frozen fallbacks still render.
    expect(markup).toContain("Local Ollama");
    expect(markup).toContain("Demo / Fake");
  });
});