import { describe, expect, it } from "vitest";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { parseGenerationCapabilities } from "./generationMode";
import {
  GENERATION_PROVIDER_STORAGE_KEY,
  GENERATION_MODEL_STORAGE_KEY,
  OLLAMA_TRANSPORT_STORAGE_KEY,
  buildProviderOffers,
  clearGenerationSelection,
  clearGenerationProvider,
  getGenerationProvider,
  getOllamaModel,
  getOllamaTransport,
  hasGenerationProviderOffer,
  parseDefaultGenerationProvider,
  parseGenerationProviders,
  persistGenerationSelection,
  providerReasonLabel,
  resolveProviderSelection,
  setGenerationProvider,
  setOllamaModel,
  setOllamaTransport,
  toCreateCaseGeneration,
  type GenerationProviderSelection,
  type GenerationProviderStorage,
} from "./generationProvider";

/**
 * Phase 25 — generation-provider capabilities parsing, sessionStorage
 * persistence (injectable storage, DISCARD-IF-STALE semantics) and resolution
 * (valid session choice > server default > first available > deterministic
 * fake). Everything is pure (no DOM, no network); storage is always injected.
 */

function fakeStorage(): GenerationProviderStorage & { entries: Map<string, string> } {
  const entries = new Map<string, string>();
  return {
    entries,
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => void entries.set(key, value),
    removeItem: (key) => void entries.delete(key),
  };
}

/** The conceptual Phase 25 §3 fixture (fake default, ollama available, frontier off). */
const CAPS_WITH_PROVIDERS: GenerationCapabilitiesResponse = {
  modes: [{ id: "demo", available: true }],
  configuredProvider: "fake",
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
    { id: "frontier", label: "Frontier", available: false, model: null, reason: "not_configured" },
  ],
};

describe("parseGenerationProviders — provider offer allowlist", () => {
  it("keeps only the closed provider ids in server order (unknown ids dropped)", () => {
    const parsed = parseGenerationProviders([
      { id: "fake", available: true },
      { id: "shiny-new-ai", available: true },
      { id: "ollama", available: true },
      { id: "frontier", available: true },
    ]);
    expect(parsed.map((provider) => provider.id)).toEqual(["fake", "ollama", "frontier"]);
  });

  it("drops duplicate ids (first occurrence wins) and hostile labels/models/reasons", () => {
    const parsed = parseGenerationProviders([
      { id: "fake", available: true, label: "http://127.0.0.1:11434 — apiKey=hunter2" },
      { id: "fake", available: true },
      {
        id: "ollama",
        available: true,
        label: "Local Ollama",
        defaultModel: "<script>alert(1)</script>",
        reason: "https://evil.example/x",
      },
      { id: "frontier", available: false, reason: "not_configured", model: "llama3@10.0.0.7" },
    ]);
    expect(parsed).toEqual([
      { id: "fake", available: true },
      // hostile defaultModel + reason dropped; safe label kept.
      { id: "ollama", available: true, label: "Local Ollama" },
      { id: "frontier", available: false, reason: "not_configured" },
    ]);
  });

  it("enforces strict booleans and hostile-free transports", () => {
    const parsed = parseGenerationProviders([
      {
        id: "ollama",
        available: "yes",
        manualModelEntry: 1,
        transports: {
          server: { available: true },
          bridge: { available: "maybe", connected: 1, reason: "http://x" },
        },
      },
    ]);
    expect(parsed).toEqual([
      {
        id: "ollama",
        available: false,
        transports: {
          server: { available: true },
          bridge: { available: false },
        },
      },
    ]);
  });

  it("treats an absent/malformed list as empty (selector not offered)", () => {
    expect(parseGenerationProviders(undefined)).toEqual([]);
    expect(parseGenerationProviders("nope")).toEqual([]);
    expect(parseGenerationProviders([42, "x"])).toEqual([]);
  });
});

describe("parseDefaultGenerationProvider — closed enum", () => {
  it("keeps only fake | ollama | frontier", () => {
    expect(parseDefaultGenerationProvider("fake")).toBe("fake");
    expect(parseDefaultGenerationProvider("ollama")).toBe("ollama");
    expect(parseDefaultGenerationProvider("frontier")).toBe("frontier");
    expect(parseDefaultGenerationProvider("live")).toBeNull();
    expect(parseDefaultGenerationProvider("http://127.0.0.1:11434")).toBeNull();
    expect(parseDefaultGenerationProvider(42)).toBeNull();
    expect(parseDefaultGenerationProvider(undefined)).toBeNull();
  });
});

describe("parseGenerationCapabilities — the additive keys ride the SAME trust-boundary parse", () => {
  it("carries sanitized defaultProvider + providers when the server emits them", () => {
    const parsed = parseGenerationCapabilities({ ...CAPS_WITH_PROVIDERS });
    expect(parsed.defaultProvider).toBe("fake");
    expect(parsed.providers?.map((provider) => provider.id)).toEqual(["fake", "ollama", "frontier"]);
    expect(parsed.modes).toEqual([{ id: "demo", available: true }]);
  });

  it("the configuredProvider close-enum parse still works with the additive keys present", () => {
    const fake = parseGenerationCapabilities({ ...CAPS_WITH_PROVIDERS, configuredProvider: "fake" });
    expect(fake.configuredProvider).toBe("fake");
    const ollama = parseGenerationCapabilities({
      configuredProvider: "ollama",
      defaultProvider: "ollama",
      modes: [],
      providers: [{ id: "ollama", available: true }],
    });
    expect(ollama.configuredProvider).toBe("ollama");
    expect(ollama.defaultProvider).toBe("ollama");
    expect(ollama.providers).toEqual([{ id: "ollama", available: true }]);
  });

  it("OMITS the additive keys when absent (older server — byte-identical pre-25 payload)", () => {
    const parsed = parseGenerationCapabilities({ modes: [{ id: "demo", available: true }] });
    expect(parsed).toEqual({ modes: [{ id: "demo", available: true }] });
    expect("defaultProvider" in parsed).toBe(false);
    expect("providers" in parsed).toBe(false);
  });
});

describe("hasGenerationProviderOffer / buildProviderOffers", () => {
  it("offers ONLY when the additive providers list is present and non-empty", () => {
    expect(hasGenerationProviderOffer(CAPS_WITH_PROVIDERS)).toBe(true);
    expect(hasGenerationProviderOffer({ modes: [{ id: "demo", available: true }] })).toBe(false);
    expect(hasGenerationProviderOffer(null)).toBe(false);
  });

  it("re-sanitizes even a hand-constructed object (last-line defense)", () => {
    const hostile: GenerationCapabilitiesResponse = {
      modes: [],
      providers: [
        {
          id: "fake",
          available: true,
          label: "Demo / Fake",
          reason: "https://secret-endpoint.example",
        },
      ],
    };
    const offers = buildProviderOffers(hostile);
    expect(offers[0].label).toBe("Demo / Fake");
    expect(offers[0].reason).toBeNull();
  });
});

describe("providerReasonLabel — safe short reasons", () => {
  it("maps the known tokens to frozen copy and drops EVERY other value (no raw exceptions)", () => {
    expect(providerReasonLabel("not_configured")).toBe("not configured");
    expect(providerReasonLabel("not_connected")).toBe("not connected");
    expect(providerReasonLabel("exception: connection refused")).toBeNull();
    expect(providerReasonLabel("unknown reason")).toBeNull();
    expect(providerReasonLabel("")).toBeNull();
    expect(providerReasonLabel("http://127.0.0.1:11434")).toBeNull();
    expect(providerReasonLabel(null)).toBeNull();
  });
});

describe("resolveProviderSelection — §10 selection resolution", () => {
  it("selects the server default when no valid session preference exists", () => {
    const selection = resolveProviderSelection(CAPS_WITH_PROVIDERS, undefined);
    expect(selection).toEqual({
      generationProvider: "fake",
      ollamaTransport: null,
      ollamaModel: "",
    });
  });

  it("restores a valid stored session choice", () => {
    const storage = fakeStorage();
    setGenerationProvider("ollama", storage);
    const selection = resolveProviderSelection(CAPS_WITH_PROVIDERS, storage);
    expect(selection?.generationProvider).toBe("ollama");
    expect(selection?.ollamaTransport).toBe("server"); // stored transport absent -> server first
    expect(selection?.ollamaModel).toBe("qwen2.5:1.5b"); // configured default, never hard-coded
  });

  it("DISCARD-IF-STALE: a stored provider that is no longer available is ignored (server default wins)", () => {
    const storage = fakeStorage();
    setGenerationProvider("frontier", storage); // frontier is unavailable in the fixture
    const selection = resolveProviderSelection(CAPS_WITH_PROVIDERS, storage);
    expect(selection?.generationProvider).toBe("fake");
  });

  it("DISCARD-IF-STALE: a stored ollama transport that is no longer available falls back to the valid side", () => {
    const storage = fakeStorage();
    setGenerationProvider("ollama", storage);
    setOllamaTransport("bridge", storage);
    const selection = resolveProviderSelection(CAPS_WITH_PROVIDERS, storage);
    expect(selection?.ollamaTransport).toBe("bridge"); // bridge still available
    // Now make the bridge unavailable -> stored bridge is discarded -> server side.
    const bridgeDown: GenerationCapabilitiesResponse = {
      modes: [],
      defaultProvider: "ollama",
      providers: [
        {
          id: "ollama",
          available: true,
          transports: {
            server: { available: true },
            bridge: { available: false, reason: "not_connected" },
          },
        },
      ],
    };
    const selection2 = resolveProviderSelection(bridgeDown, storage);
    expect(selection2?.ollamaTransport).toBe("server");
  });

  it("uses the first still-available provider when neither default nor stored fit", () => {
    const storage = fakeStorage();
    setGenerationProvider("frontier", storage);
    const caps: GenerationCapabilitiesResponse = {
      modes: [],
      defaultProvider: "frontier", // server default also unavailable
      providers: [
        { id: "fake", available: true },
        { id: "ollama", available: true },
        { id: "frontier", available: false, reason: "not_configured" },
      ],
    };
    const selection = resolveProviderSelection(caps, storage);
    expect(selection?.generationProvider).toBe("fake");
  });

  it("a stored model pre-fills the Ollama input (still never a hard-coded name)", () => {
    const storage = fakeStorage();
    setGenerationProvider("ollama", storage);
    setOllamaModel("hermes3:8b", storage);
    const selection = resolveProviderSelection(CAPS_WITH_PROVIDERS, storage);
    expect(selection?.ollamaModel).toBe("hermes3:8b");
  });

  it("returns null when the additive offer is absent (OLDER server -> no selector)", () => {
    expect(resolveProviderSelection(null, undefined)).toBeNull();
    expect(resolveProviderSelection({ modes: [{ id: "demo", available: true }] }, undefined)).toBeNull();
  });
});

describe("sessionStorage persistence — the three non-secret keys only", () => {
  it("set/get/clear round-tripping with the injectable storage", () => {
    const storage = fakeStorage();
    expect(getGenerationProvider(storage)).toBeNull();
    expect(setGenerationProvider("olllama" as never, storage)).toBe(true);
    expect(getGenerationProvider(storage)).toBeNull(); // hostile value reads null
    expect(setGenerationProvider("ollama", storage)).toBe(true);
    expect(getGenerationProvider(storage)).toBe("ollama");
    clearGenerationProvider(storage);
    expect(getGenerationProvider(storage)).toBeNull();
  });

  it("transport round-trip reads only the closed ids", () => {
    const storage = fakeStorage();
    setOllamaTransport("bridge", storage);
    expect(getOllamaTransport(storage)).toBe("bridge");
    setOllamaTransport("weird" as never, storage);
    expect(getOllamaTransport(storage)).toBeNull();
  });

  it("model round-trip trims and clears on blank", () => {
    const storage = fakeStorage();
    setOllamaModel("  qwen2.5:1.5b  ", storage);
    expect(getOllamaModel(storage)).toBe("qwen2.5:1.5b");
    expect(storage.entries.get(GENERATION_MODEL_STORAGE_KEY)).toBe("qwen2.5:1.5b");
    setOllamaModel("   ", storage);
    expect(getOllamaModel(storage)).toBeNull();
    expect(storage.entries.has(GENERATION_MODEL_STORAGE_KEY)).toBe(false);
  });

  it("persistGenerationSelection writes exactly the three keys and never anything else", () => {
    const storage = fakeStorage();
    const selection: GenerationProviderSelection = {
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    };
    persistGenerationSelection(selection, storage);
    expect(storage.entries.get(GENERATION_PROVIDER_STORAGE_KEY)).toBe("ollama");
    expect(storage.entries.get(OLLAMA_TRANSPORT_STORAGE_KEY)).toBe("bridge");
    expect(storage.entries.get(GENERATION_MODEL_STORAGE_KEY)).toBe("hermes3:8b");
    expect(storage.entries.size).toBe(3);
    clearGenerationSelection(storage);
    expect(storage.entries.size).toBe(0);
  });

  it("persisting a NON-ollama selection clears the transport/model keys", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "frontier", ollamaTransport: null, ollamaModel: "" },
      storage,
    );
    expect(storage.entries.get(GENERATION_PROVIDER_STORAGE_KEY)).toBe("frontier");
    expect(storage.entries.has(OLLAMA_TRANSPORT_STORAGE_KEY)).toBe(false);
    expect(storage.entries.has(GENERATION_MODEL_STORAGE_KEY)).toBe(false);
  });
});

describe("toCreateCaseGeneration — the flat POST /cases block", () => {
  it("fake/frontier travel ONLY as the provider id (no transport/model)", () => {
    expect(
      toCreateCaseGeneration({ generationProvider: "fake", ollamaTransport: null, ollamaModel: "" }),
    ).toEqual({ generationProvider: "fake" });
    expect(
      toCreateCaseGeneration({
        generationProvider: "frontier",
        ollamaTransport: null,
        ollamaModel: "",
      }),
    ).toEqual({ generationProvider: "frontier" });
  });

  it("ollama travels with transport + model when present", () => {
    expect(
      toCreateCaseGeneration({
        generationProvider: "ollama",
        ollamaTransport: "bridge",
        ollamaModel: "hermes3:8b",
      }),
    ).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
  });

  it("ollama with no transport/model omits those optional fields", () => {
    expect(
      toCreateCaseGeneration({ generationProvider: "ollama", ollamaTransport: null, ollamaModel: "" }),
    ).toEqual({ generationProvider: "ollama" });
  });

  it("null (no provider offer) -> undefined (byte-identical no-selection call)", () => {
    expect(toCreateCaseGeneration(null)).toBeUndefined();
  });
});