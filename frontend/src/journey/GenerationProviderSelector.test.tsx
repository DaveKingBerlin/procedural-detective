import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { GenerationProviderSelector } from "./GenerationProviderSelector";
import type { GenerationProviderSelection } from "./generationProvider";

/**
 * Phase 25 §16 — the browser provider selector:
 *   - renders the known providers from the capabilities API;
 *   - Fake selectable, Ollama selectable when available, Frontier selectable
 *     when available;
 *   - unavailable providers VISIBLE but DISABLED with the safe reason;
 *   - the model label/initial value comes from the capabilities API (never
 *     hard-coded);
 *   - the default provider is preselected;
 *   - a stale/unavailable stored selection is ignored (resolution happens in
 *     generationProvider.ts — pinned here through the preselection);
 *   - `disabled` locks every control while a generation is submitting (§10.2);
 *   - no secret/config URL is ever rendered.
 * Statically rendered (react-dom/server): no DOM, no network, no effects.
 */

const NOOP = () => {};

/** Phase 25 §3 conceptual fixture. */
const CAPS: GenerationCapabilitiesResponse = {
  modes: [{ id: "demo", available: true }],
  defaultProvider: "fake",
  providers: [
    { id: "fake", label: "Demo / Fake", available: true, model: null, reason: null },
    {
      id: "ollama",
      label: "Ollama",
      available: true,
      defaultModel: "qwen2.5:1.5b",
      manualModelEntry: true,
      transports: {
        server: { available: true, reason: null },
        bridge: { available: true, connected: false, reason: "not_connected" },
      },
    },
    {
      id: "frontier",
      label: "Frontier",
      available: false,
      model: null,
      reason: "not_configured",
    },
  ],
};

const FAKE_SELECTION: GenerationProviderSelection = {
  generationProvider: "fake",
  ollamaTransport: null,
  ollamaModel: "",
};

const OLLAMA_SELECTION: GenerationProviderSelection = {
  generationProvider: "ollama",
  ollamaTransport: "server",
  ollamaModel: "qwen2.5:1.5b",
};

function render(
  capabilities: GenerationCapabilitiesResponse | null,
  selection: GenerationProviderSelection | null,
  disabled = false,
): string {
  return renderToStaticMarkup(
    <GenerationProviderSelector
      capabilities={capabilities}
      selection={selection}
      disabled={disabled}
      onChange={NOOP}
    />,
  );
}

describe("GenerationProviderSelector — rendering", () => {
  it("renders the container + heading when the provider offer exists", () => {
    const markup = render(CAPS, FAKE_SELECTION);
    expect(markup).toContain('data-testid="generation-provider-selector"');
    expect(markup).toContain("AI Provider");
  });

  it("renders NOTHING when the additive provider offer is absent (older server)", () => {
    const markup = render({ modes: [{ id: "demo", available: true }] }, null);
    expect(markup).toBe("");
  });

  it("lists Fake with its demo subtitle", () => {
    const markup = render(CAPS, FAKE_SELECTION);
    expect(markup).toContain('data-testid="generation-provider-fake"');
    expect(markup).toContain("Demo / Fake");
    expect(markup).toContain("No external AI request");
  });

  it("offers Ollama with the server-configured transport choices, never hard-coded model text", () => {
    const markup = render(CAPS, OLLAMA_SELECTION);
    expect(markup).toContain('data-testid="generation-provider-ollama"');
    expect(markup).toContain('data-testid="generation-ollama-controls"');
    expect(markup).toContain("Server / Direct");
    expect(markup).toContain("My device via Bridge");
    // The model INPUT value comes from the capabilities `defaultModel`:
    expect(markup).toContain('value="qwen2.5:1.5b"');
    // The prompt-level model name is never hard-coded in this module's copy:
    expect(markup).not.toContain('placeholder="qwen2.5:1.5b"');
  });

  it("offers Frontier (selectable only when available)", () => {
    const markup = render(CAPS, FAKE_SELECTION);
    expect(markup).toContain('data-testid="generation-provider-frontier"');
    expect(markup).toContain("Frontier");
  });

  it("Frontier is SELECTABLE when the backend reports it available", () => {
    const caps: GenerationCapabilitiesResponse = {
      modes: [],
      providers: [{ id: "frontier", label: "Frontier", available: true }],
    };
    const markup = render(caps, {
      generationProvider: "frontier",
      ollamaTransport: null,
      ollamaModel: "",
    });
    const frontierBlock =
      markup.match(/data-testid="generation-provider-frontier"[\s\S]*?<\/label>/)?.[0] ?? "";
    expect(frontierBlock).not.toContain("disabled");
    expect(markup).not.toContain('data-testid="generation-provider-frontier-reason"');
  });

  it("preselects the server default provider (fake) when no session preference exists", () => {
    const markup = render(CAPS, FAKE_SELECTION);
    expect(markup).toContain('name="generation-provider" checked="" value="fake"');
  });

  it("preselects the server default-provider when it differs from fake", () => {
    const caps: GenerationCapabilitiesResponse = {
      modes: [],
      defaultProvider: "ollama",
      providers: [
        { id: "fake", available: true },
        { id: "ollama", available: true },
      ],
    };
    const markup = render(caps, OLLAMA_SELECTION);
    expect(markup).toContain('name="generation-provider" checked="" value="ollama"');
  });
});

describe("GenerationProviderSelector — availability / disabled states", () => {
  it("disables an unavailable provider radio but keeps it visible with the safe reason", () => {
    const markup = render(CAPS, FAKE_SELECTION);
    expect(markup).toContain('data-testid="generation-provider-frontier-reason"');
    expect(markup).toContain("not configured");
    // The radio is disabled (unavailable), so it cannot be selected.
    expect(markup).toMatch(/data-testid="generation-provider-frontier"[\s\S]*disabled=""/);
  });

  it("leaves available providers selectable", () => {
    const markup = render(CAPS, FAKE_SELECTION);
    expect(markup).toMatch(/data-testid="generation-provider-fake"[\s\S]*?<input[^>]*type="radio"/);
    // The fake radio (available) has NO disabled attribute.
    const fakeBlock = markup.match(/data-testid="generation-provider-fake"[\s\S]*?<\/label>/)?.[0] ?? "";
    expect(fakeBlock).not.toContain("disabled");
  });

  it("shows both Ollama transports DISABLED (visible but unusable) when no transports offer exists; manual model entry stays available", () => {
    const caps: GenerationCapabilitiesResponse = {
      modes: [],
      providers: [{ id: "ollama", available: true }],
    };
    const markup = render(caps, { generationProvider: "ollama", ollamaTransport: null, ollamaModel: "" });
    // No transports block -> both transport options are visible but disabled.
    expect(markup).toContain('data-testid="generation-ollama-transport-server"');
    expect(markup).toContain('data-testid="generation-ollama-transport-bridge"');
    expect(markup).toMatch(
      /data-testid="generation-ollama-transport-server"[\s\S]*?<input[^>]*disabled=""[^>]*>/,
    );
    expect(markup).toMatch(
      /data-testid="generation-ollama-transport-bridge"[\s\S]*?<input[^>]*disabled=""[^>]*>/,
    );
    // The model field still lets manual entry work.
    expect(markup).toContain('data-testid="generation-ollama-model-input"');
  });

  it("disabled (submitting) locks every radio and the model input (§10.2)", () => {
    const markup = render(CAPS, OLLAMA_SELECTION, true);
    // Every control carries disabled when the generation is being submitted.
    const radioTags = markup.match(/<input[^>]*type="radio"[^>]*>/g) ?? [];
    expect(radioTags.length).toBeGreaterThan(0);
    for (const tag of radioTags) {
      expect(tag).toContain('disabled=""');
    }
    const modelInput = markup.match(
      /<input[^>]*data-testid="generation-ollama-model-input"[^>]*>/,
    )?.[0] ?? "";
    expect(modelInput).toContain('disabled=""');
  });
});

describe("GenerationProviderSelector — hostile/reason safety", () => {
  it("never renders a secret URL/config host from a hostile provider label/reason/model", () => {
    const hostile: GenerationCapabilitiesResponse = {
      modes: [],
      defaultProvider: "ollama",
      providers: [
        {
          id: "ollama",
          label: "http://127.0.0.1:11434 — apiKey=hunter2",
          available: false,
          reason: "https://secret-endpoint.example — token=abc",
          defaultModel: "llama3@192.168.1.9",
        },
      ],
    };
    const markup = render(hostile, OLLAMA_SELECTION);
    expect(markup).not.toContain("127.0.0.1");
    expect(markup).not.toContain("11434");
    expect(markup).not.toContain("hunter2");
    expect(markup).not.toContain("secret-endpoint");
    expect(markup).not.toContain("192.168.1.9");
    // The frozen public fallback label stays.
    expect(markup).toContain("Local Ollama");
  });

  it("keeps the raw exception/diagnostic text OUT of the reason line (frozen 'unavailable' instead)", () => {
    const hostile: GenerationCapabilitiesResponse = {
      modes: [],
      providers: [{ id: "frontier", available: false, reason: "exception: connection refused" }],
    };
    const markup = render(hostile, FAKE_SELECTION);
    expect(markup).toContain("unavailable");
    expect(markup).not.toContain("exception");
    expect(markup).not.toContain("connection refused");
  });
});