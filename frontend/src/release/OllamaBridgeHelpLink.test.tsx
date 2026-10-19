import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { GenerationProviderSelector } from "../journey/GenerationProviderSelector";
import { parseGenerationCapabilities } from "../journey/generationMode";
import LocalAiBridgePanel from "../journey/LocalAiBridgePanel";
import OllamaBridgeHelpLink, { OLLAMA_BRIDGE_HELP_COPY } from "./OllamaBridgeHelpLink";
import { RELEASE_DOWNLOAD_ANCHOR_ID } from "./releaseManifest";

/**
 * Phase 49 §50 — the discoverable Local-Ollama help/download link.
 *
 * It must render the frozen copy linked to the download-section anchor, and it
 * must actually appear where the user chooses Local Ollama (the /new provider
 * controls and the BYO-Ollama bridge panel) without adding a control there.
 */

const OFFERED: GenerationCapabilitiesResponse = parseGenerationCapabilities({
  modes: [{ id: "demo", available: true }],
  configuredProvider: "fake",
  remoteLocalAi: { available: true, connected: false, model: null, ready: false },
});

const PROVIDERS: GenerationCapabilitiesResponse = {
  modes: [{ id: "demo", available: true }],
  defaultProvider: "ollama",
  providers: [
    { id: "fake", available: true },
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
  ],
};

describe("Phase 49 §50 — Ollama bridge help link", () => {
  it("renders the frozen copy pointing at the download-section anchor", () => {
    const markup = renderToStaticMarkup(<OllamaBridgeHelpLink />);
    expect(markup).toContain('data-testid="ollama-bridge-help"');
    expect(markup).toContain("Need the Ollama Bridge? Download for Windows or Linux");
    expect(markup).toContain(`href="#${RELEASE_DOWNLOAD_ANCHOR_ID}"`);
    expect(OLLAMA_BRIDGE_HELP_COPY).toBe(
      "Need the Ollama Bridge? Download for Windows or Linux",
    );
  });

  it("appears in the provider selector when Local Ollama is active (does not alter the radios)", () => {
    const markup = renderToStaticMarkup(
      <GenerationProviderSelector
        capabilities={PROVIDERS}
        selection={{
          generationProvider: "ollama",
          ollamaTransport: "server",
          ollamaModel: "qwen2.5:1.5b",
        }}
        onChange={() => {}}
      />,
    );
    expect(markup).toContain('data-testid="ollama-bridge-help"');
    // The provider radios are untouched by the help line.
    expect(markup).toContain('name="generation-provider"');
  });

  it("appears in the BYO-Ollama bridge panel idle state", () => {
    const markup = renderToStaticMarkup(<LocalAiBridgePanel capabilities={OFFERED} />);
    expect(markup).toContain('data-testid="local-ai-bridge-panel"');
    expect(markup).toContain('data-testid="ollama-bridge-help"');
  });
});
