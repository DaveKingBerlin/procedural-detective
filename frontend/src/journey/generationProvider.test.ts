import { describe, expect, it } from "vitest";
import type { FrontierProviderEntryDTO, GenerationCapabilitiesResponse } from "../api/types";
import { parseGenerationCapabilities } from "./generationMode";
import {
  FRONTIER_MODEL_STORAGE_KEY,
  FRONTIER_PROVIDER_STORAGE_KEY,
  GENERATION_PROVIDER_STORAGE_KEY,
  GENERATION_MODEL_STORAGE_KEY,
  OLLAMA_TRANSPORT_STORAGE_KEY,
  bridgePairedSelection,
  buildProviderOffers,
  clearFrontierModel,
  clearFrontierProvider,
  clearGenerationSelection,
  clearGenerationProvider,
  defaultOllamaTransport,
  getFrontierModel,
  getFrontierProvider,
  getGenerationProvider,
  getOllamaModel,
  getOllamaTransport,
  hasGenerationProviderOffer,
  isFrontierSubmitReady,
  isSafeFrontierProviderId,
  parseDefaultGenerationProvider,
  parseFrontierProviders,
  parseGenerationProviders,
  persistGenerationSelection,
  providerReasonLabel,
  resolveProviderSelection,
  sanitizeFrontierApiKey,
  setFrontierModel,
  setFrontierProvider,
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
    // No stored transport -> the no-preference default (server when both
    // sides are available and no intent signal exists, Phase 26C1 §5).
    expect(selection?.ollamaTransport).toBe("server");
    expect(selection?.ollamaModel).toBe("qwen2.5:1.5b"); // configured default, never hard-coded
  });

  it("DISCARD-IF-STALE: a stored provider that is no longer available is ignored (server default wins)", () => {
    const storage = fakeStorage();
    setGenerationProvider("frontier", storage); // frontier is unavailable in the fixture
    const selection = resolveProviderSelection(CAPS_WITH_PROVIDERS, storage);
    expect(selection?.generationProvider).toBe("fake");
  });

  it("Phase 26C1 — an unavailable STORED transport stays SELECTED (no silent fallback to the valid side)", () => {
    const storage = fakeStorage();
    setGenerationProvider("ollama", storage);
    setOllamaTransport("bridge", storage);
    const selection = resolveProviderSelection(CAPS_WITH_PROVIDERS, storage);
    expect(selection?.ollamaTransport).toBe("bridge"); // bridge still available
    // Now make the bridge unavailable: the stored bridge transport is
    // AUTHORITATIVE and is preserved verbatim (tests 12 — never rewritten to
    // the available server side, never silently switched).
    const bridgeDown: GenerationCapabilitiesResponse = {
      modes: [],
      defaultProvider: "ollama",
      providers: [
        {
          id: "ollama",
          available: true,
          defaultModel: "",
          transports: {
            server: { available: true },
            bridge: { available: false, reason: "not_connected" },
          },
        },
      ],
    };
    const selection2 = resolveProviderSelection(bridgeDown, storage);
    expect(selection2?.ollamaTransport).toBe("bridge");
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

  it("a fresh NON-ollama selection leaves the (absent) transport/model keys absent", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "frontier", ollamaTransport: null, ollamaModel: "" },
      storage,
    );
    expect(storage.entries.get(GENERATION_PROVIDER_STORAGE_KEY)).toBe("frontier");
    expect(storage.entries.has(OLLAMA_TRANSPORT_STORAGE_KEY)).toBe(false);
    expect(storage.entries.has(GENERATION_MODEL_STORAGE_KEY)).toBe(false);
  });

  it("Phase 26C1 §8 — a NON-ollama selection PRESERVES the transport/model preference keys (Fake -> Ollama restore)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "hermes3:8b" },
      storage,
    );
    // The user switches the provider to Fake: provider persists, and the
    // Ollama transport/model preferences are left as-is (inert while the
    // provider is non-ollama), so the intended transport survives.
    persistGenerationSelection(
      { generationProvider: "fake", ollamaTransport: null, ollamaModel: "" },
      storage,
    );
    expect(storage.entries.get(GENERATION_PROVIDER_STORAGE_KEY)).toBe("fake");
    expect(storage.entries.get(OLLAMA_TRANSPORT_STORAGE_KEY)).toBe("bridge");
    expect(storage.entries.get(GENERATION_MODEL_STORAGE_KEY)).toBe("hermes3:8b");
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

  it("INFONote A1a — ollama with NO usable transport omits ONLY the transport (fail-closed, no silent fallback) and even a known model keeps the payload transport-free", () => {
    // The documented no-usable-transport path: `selectProvider("ollama")`
    // under both-unavailable transports yields `ollamaTransport: null` (see
    // GenerationProviderSelector.test.tsx). Serializing that selection posts
    // {generationProvider:"ollama"} WITHOUT the transport key — the backend
    // answers 400 INVALID_GENERATION_PROVIDER, which the journey maps to the
    // frozen safe `invalidGenerationProvider` copy on submit (src/journey/
    // demoFlow.ts). This is the intended fail-closed path — the client NEVER
    // silently fabricates a fallback transport (no bridge/server rewrite).
    const noTransportButModel: GenerationProviderSelection = {
      generationProvider: "ollama",
      ollamaTransport: null,
      ollamaModel: "hermes3:8b",
    };
    expect(toCreateCaseGeneration(noTransportButModel)).toEqual({
      generationProvider: "ollama",
      ollamaModel: "hermes3:8b",
    });
    expect(
      toCreateCaseGeneration({ ...noTransportButModel, ollamaModel: "" }),
    ).toEqual({ generationProvider: "ollama" });
  });

  it("null (no provider offer) -> undefined (byte-identical no-selection call)", () => {
    expect(toCreateCaseGeneration(null)).toBeUndefined();
  });
});

describe("Phase 26C1 — the SELECTED transport is authoritative (§1-§8)", () => {
  /** Ollama capabilities with the fake/frontier offer and chosen transports. */
  function ollamaCaps(transports: {
    server: { available: boolean; connected?: boolean; reason?: string | null };
    bridge: { available: boolean; connected?: boolean; reason?: string | null };
  }): GenerationCapabilitiesResponse {
    return {
      modes: [],
      defaultProvider: "ollama",
      providers: [
        { id: "fake", label: "Demo / Fake", available: true },
        {
          id: "ollama",
          label: "Ollama",
          available: true,
          defaultModel: "qwen2.5:1.5b",
          manualModelEntry: true,
          transports,
        },
        { id: "frontier", label: "Frontier", available: false, reason: "not_configured" },
      ],
    };
  }

  const BOTH_AVAILABLE = ollamaCaps({
    server: { available: true },
    bridge: { available: true, connected: false, reason: "not_connected" },
  });
  const BRIDGE_CONNECTED = ollamaCaps({
    server: { available: true },
    bridge: { available: true, connected: true },
  });
  const BRIDGE_UNAVAILABLE = ollamaCaps({
    server: { available: true },
    bridge: { available: false, reason: "not_connected" },
  });
  const SERVER_UNAVAILABLE = ollamaCaps({
    server: { available: false, reason: "not_configured" },
    bridge: { available: true, connected: false, reason: "not_connected" },
  });

  it("both transports available + Server selected -> resolve + serialize POST server (§8.1)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "server", ollamaModel: "hermes3:8b" },
      storage,
    );
    const selection = resolveProviderSelection(BOTH_AVAILABLE, storage);
    expect(selection).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "hermes3:8b",
    });
    expect(toCreateCaseGeneration(selection)).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "hermes3:8b",
    });
  });

  it("both transports available + Bridge selected -> resolve + serialize POST bridge (§8.2)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "hermes3:8b" },
      storage,
    );
    const selection = resolveProviderSelection(BOTH_AVAILABLE, storage);
    expect(selection).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
    expect(toCreateCaseGeneration(selection)).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
  });

  it("Bridge connected does NOT override an explicit Server (§8.3)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "server", ollamaModel: "hermes3:8b" },
      storage,
    );
    const selection = resolveProviderSelection(BRIDGE_CONNECTED, storage);
    expect(selection?.ollamaTransport).toBe("server");
    expect(toCreateCaseGeneration(selection)?.ollamaTransport).toBe("server");
  });

  it("Server available does NOT override an explicit Bridge (§8.4)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "hermes3:8b" },
      storage,
    );
    const selection = resolveProviderSelection(BOTH_AVAILABLE, storage);
    expect(selection?.ollamaTransport).toBe("bridge");
  });

  it("capability refresh preserves Server (even when Bridge comes connected) (§8.5)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "server", ollamaModel: "hermes3:8b" },
      storage,
    );
    expect(resolveProviderSelection(BOTH_AVAILABLE, storage)?.ollamaTransport).toBe("server");
    // A refreshed DTO now reports the bridge connected — the explicit Server
    // selection is NOT overwritten.
    expect(resolveProviderSelection(BRIDGE_CONNECTED, storage)?.ollamaTransport).toBe("server");
  });

  it("capability refresh preserves Bridge (even when Server comes back online) (§8.6)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "hermes3:8b" },
      storage,
    );
    expect(resolveProviderSelection(SERVER_UNAVAILABLE, storage)?.ollamaTransport).toBe("bridge");
    expect(resolveProviderSelection(BOTH_AVAILABLE, storage)?.ollamaTransport).toBe("bridge");
  });

  it("model changes do NOT reset the transport (§8.7)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "hermes3:8b" },
      storage,
    );
    expect(resolveProviderSelection(BOTH_AVAILABLE, storage)?.ollamaTransport).toBe("bridge");
    setOllamaModel("qwen2.5:1.5b", storage);
    const selection = resolveProviderSelection(BOTH_AVAILABLE, storage);
    expect(selection?.ollamaTransport).toBe("bridge");
    expect(selection?.ollamaModel).toBe("qwen2.5:1.5b");
  });

  it("Fake -> Ollama restores the intended persisted transport (§8.8)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "hermes3:8b" },
      storage,
    );
    // Switch the provider to Fake: the transport preference key is preserved
    // (inert while the provider is non-ollama).
    persistGenerationSelection(
      { generationProvider: "fake", ollamaTransport: null, ollamaModel: "" },
      storage,
    );
    expect(getOllamaTransport(storage)).toBe("bridge");
    // Returning to Ollama (an explicit user switch back) restores bridge.
    setGenerationProvider("ollama", storage);
    const selection = resolveProviderSelection(BOTH_AVAILABLE, storage);
    expect(selection).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
  });

  it("a successful Bridge pairing selects + persists the Bridge transport (§8.9)", () => {
    const none = bridgePairedSelection(CAPS_WITH_PROVIDERS, null);
    expect(none).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "qwen2.5:1.5b",
    });
    const storage = fakeStorage();
    persistGenerationSelection(none, storage);
    expect(storage.entries.get(GENERATION_PROVIDER_STORAGE_KEY)).toBe("ollama");
    expect(storage.entries.get(OLLAMA_TRANSPORT_STORAGE_KEY)).toBe("bridge");
  });

  it("ORDERED INTENT (older ordering): a Server choice made BEFORE the pairing began is overridden by the completed pairing (§8.9)", () => {
    // The pure `bridgePairedSelection` models the pairing's implied selection
    // and is only invoked by the route when the ordering guard is clear — a
    // provider/transport choice made BEFORE the pairing window opened is OLDER
    // intent, so the pairing (this function's output) still wins over it
    // (§3: "If the user later explicitly chooses Server, Server remains
    // selected" — for choices AFTER the pairing began; the pre-pairing
    // ordering is the reverse).
    const afterServer = bridgePairedSelection(CAPS_WITH_PROVIDERS, {
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "hermes3:8b",
    });
    expect(afterServer).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
    // The interleaved ordering (explicit choice made DURING the pairing
    // window wins over the completion) is a ROUTE-level guard in
    // src/routes/new.tsx (explicitChoiceSincePairingStartedRef) — pinned
    // end-to-end in src/routes/new.providerSelector.test.tsx.
  });

  it("reload restores the persisted Bridge transport (§8.10)", () => {
    const storage = fakeStorage();
    setGenerationProvider("ollama", storage);
    setOllamaTransport("bridge", storage);
    setOllamaModel("hermes3:8b", storage);
    // A fresh resolve from the same storage === a page reload restore.
    expect(resolveProviderSelection(BOTH_AVAILABLE, storage)).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
  });

  it("reload restores the persisted Server transport (§8.11)", () => {
    const storage = fakeStorage();
    setGenerationProvider("ollama", storage);
    setOllamaTransport("server", storage);
    setOllamaModel("hermes3:8b", storage);
    expect(resolveProviderSelection(BOTH_AVAILABLE, storage)?.ollamaTransport).toBe("server");
  });

  it("unavailable Bridge stays selected, NO Server fallback (§8.12)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "hermes3:8b" },
      storage,
    );
    const selection = resolveProviderSelection(BRIDGE_UNAVAILABLE, storage);
    expect(selection?.ollamaTransport).toBe("bridge");
    // The serialized payload still carries the SELECTED transport — no silent
    // rewrite, no fallback.
    expect(toCreateCaseGeneration(selection)).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
  });

  it("unavailable Server stays selected, NO Bridge fallback (§8.13)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(
      { generationProvider: "ollama", ollamaTransport: "server", ollamaModel: "hermes3:8b" },
      storage,
    );
    const selection = resolveProviderSelection(SERVER_UNAVAILABLE, storage);
    expect(selection?.ollamaTransport).toBe("server");
    expect(toCreateCaseGeneration(selection)).toEqual({
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "hermes3:8b",
    });
  });

  it("NO stored choice: the documented deterministic default applies (§5)", () => {
    const ollama = buildProviderOffers(BOTH_AVAILABLE).find((offer) => offer.id === "ollama");
    // both sides available, no connection -> server (documented tie-break).
    expect(defaultOllamaTransport(ollama)).toBe("server");
    expect(resolveProviderSelection(BOTH_AVAILABLE, undefined)?.ollamaTransport).toBe("server");
    // bridge connected -> bridge (intent signal).
    const connected = buildProviderOffers(BRIDGE_CONNECTED).find((offer) => offer.id === "ollama");
    expect(defaultOllamaTransport(connected)).toBe("bridge");
    expect(resolveProviderSelection(BRIDGE_CONNECTED, undefined)?.ollamaTransport).toBe("bridge");
    // unambiguous single-side availability.
    expect(
      defaultOllamaTransport(
        buildProviderOffers(SERVER_UNAVAILABLE).find((offer) => offer.id === "ollama"),
      ),
    ).toBe("bridge");
    expect(
      defaultOllamaTransport(
        buildProviderOffers(BRIDGE_UNAVAILABLE).find((offer) => offer.id === "ollama"),
      ),
    ).toBe("server");
    expect(defaultOllamaTransport(undefined)).toBeNull();
  });
});

/* ======================================================================
 * Phase 30 — BYOK Frontier: catalog parsing, state distribution (§24),
 * serialization (§6) and the submit-readiness gate (§5).
 * ==================================================================== */

/** Phase 30 §9 fixture: the frontier offer with the safe provider catalog. */
const CAPS_WITH_FRONTIER: GenerationCapabilitiesResponse = {
  modes: [{ id: "demo", available: true }],
  defaultProvider: "fake",
  providers: [
    { id: "fake", label: "Demo / Fake", available: true, model: null, reason: null },
    {
      id: "frontier",
      label: "Frontier",
      available: true,
      requiresUserConfiguration: true,
      providers: [
        { id: "openai", label: "OpenAI" },
        { id: "openrouter", label: "OpenRouter" },
        { id: "groq", label: "Groq" },
      ],
    },
  ],
};

/** The "empty" frontier selection a fresh resolve produces (key always absent). */
const FRONTIER_SELECTION: GenerationProviderSelection = {
  generationProvider: "frontier",
  ollamaTransport: null,
  ollamaModel: "",
  frontierProviderId: null,
  frontierModel: "",
};

/** A COMPLETE frontier selection (provider + key + model + ack). */
function completeFrontierSelection(
  overrides: Partial<GenerationProviderSelection> = {},
): GenerationProviderSelection {
  return {
    generationProvider: "frontier",
    ollamaTransport: null,
    ollamaModel: "",
    frontierProviderId: "openai",
    frontierModel: "gpt-4o-mini",
    frontierApiKey: "sk-test-phase30-0000",
    frontierAck: true,
    ...overrides,
  };
}

describe("Phase 30 — parseFrontierProviders (the safe catalog, §9)", () => {
  it("keeps ONLY {id, label} pairs and drops every other field (endpoints/secrets never survive)", () => {
    const parsed = parseFrontierProviders([
      { id: "openai", label: "OpenAI" },
      { id: "openrouter", label: "OpenRouter" },
      // A hostile/verbose backend could add operational fields — they must be
      // ignored (the catalog surface carries ids + labels ONLY, §9).
      {
        id: "groq",
        label: "Groq",
        endpoint: "https://api.groq.example/chat/completions",
        baseUrl: "https://groq.example",
        apiKey: "sk-op",
      },
    ]);
    expect(parsed).toEqual([
      { id: "openai", label: "OpenAI" },
      { id: "openrouter", label: "OpenRouter" },
      { id: "groq", label: "Groq" },
    ]);
  });

  it("drops duplicate ids (first wins), unsafe ids and unsafe labels (fallback = the safe id)", () => {
    const parsed = parseFrontierProviders([
      { id: "openai", label: "OpenAI" },
      { id: "openai", label: "Duplicate OpenAI" },
      { id: "https://evil.example", label: "Evil" },
      { id: "openai?url=https://evil.example", label: "Sneaky" },
      { id: "openrouter\nAuthorization: x", label: "Header" },
      { id: "groq", label: "https://evil.example — token=abc" },
      { id: "together", label: "http://127.0.0.1:11434" },
      { id: "fireworks", label: "oklabel@host" },
    ]);
    expect(parsed).toEqual([
      { id: "openai", label: "OpenAI" },
      // The hostile label falls back to the SAFE id (the id passed the
      // defensive guard — the entry stays selectable without hostile text).
      { id: "groq", label: "groq" },
      { id: "together", label: "together" },
      { id: "fireworks", label: "fireworks" },
    ]);
  });

  it("treats an absent/malformed catalog as empty", () => {
    expect(parseFrontierProviders(undefined)).toEqual([]);
    expect(parseFrontierProviders("nope")).toEqual([]);
    expect(parseFrontierProviders([42, null, { label: "no-id" }])).toEqual([]);
  });
});

describe("Phase 30 — isSafeFrontierProviderId (defensive id guard)", () => {
  it("accepts plain trusted provider ids", () => {
    for (const id of [
      "openai",
      "openrouter",
      "groq",
      "together-ai",
      "mistral",
      "fireworks",
      "deepinfra",
      "xai",
    ]) {
      expect(isSafeFrontierProviderId(id), id).toBe(true);
    }
  });

  it("rejects URL/query/header/control-smuggling ids", () => {
    for (const id of [
      "https://evil.example",
      "openai?url=https://evil.example",
      "openai#fragment",
      "openai\\x",
      "openai/x",
      "../openai",
      "openai\nAuthorization: x",
      "openai\tgroq",
      "  openai",
      "openai@host",
      "a:b",
      "openai&x=1",
      "",
      "   ",
    ]) {
      expect(isSafeFrontierProviderId(id), JSON.stringify(id)).toBe(false);
    }
  });
});

describe("Phase 30 — sanitizeFrontierApiKey (§17: CR/LF + control chars stripped)", () => {
  it("strips CR/LF and control characters, preserving the printable secret verbatim", () => {
    expect(sanitizeFrontierApiKey("sk-test")).toBe("sk-test");
    expect(sanitizeFrontierApiKey("sk-test\r\nContinued")).toBe("sk-testContinued");
    expect(sanitizeFrontierApiKey("sk-\u0000nul\u001f")).toBe("sk-nul");
    expect(sanitizeFrontierApiKey("")).toBe("");
  });
});

describe("Phase 30 — parseGenerationCapabilities carries the frontier offer (requiresUserConfiguration + catalog)", () => {
  it("carries the sanitized BYOK offer through the additive parse", () => {
    const parsed = parseGenerationCapabilities({ ...CAPS_WITH_FRONTIER });
    expect(parsed.providers?.find((p) => p.id === "frontier")).toEqual({
      id: "frontier",
      label: "Frontier",
      available: true,
      requiresUserConfiguration: true,
      providers: [
        { id: "openai", label: "OpenAI" },
        { id: "openrouter", label: "OpenRouter" },
        { id: "groq", label: "Groq" },
      ],
    });
  });

  it("buildProviderOffers re-sanitizes even a hand-constructed (parser-bypassed) catalog", () => {
    const hostile: GenerationCapabilitiesResponse = {
      modes: [],
      providers: [
        {
          id: "frontier",
          label: "Frontier",
          available: true,
          requiresUserConfiguration: true,
          providers: [
            { id: "openai", label: "OpenAI" },
            {
              id: "https://evil.example",
              label: "Evil",
              endpoint: "https://evil.example/api",
              // A hand-constructed hostile entry (endpoint/URL surface)
              // asserted through the DTO type so the last-line re-parse is
              // what gets exercised.
            } as FrontierProviderEntryDTO,
            { id: "openai", label: "Duplicate" },
            { id: "openrouter", label: "https://evil.example — token=abc" },
          ],
        },
      ],
    };
    const frontier = buildProviderOffers(hostile).find((offer) => offer.id === "frontier");
    expect(frontier?.requiresUserConfiguration).toBe(true);
    expect(frontier?.providers).toEqual([
      { id: "openai", label: "OpenAI" },
      { id: "openrouter", label: "openrouter" },
    ]);
    // No endpoint/secret text can ever reach the offer surface.
    expect(JSON.stringify(frontier)).not.toContain("evil.example");
  });
});

describe("Phase 30 — Frontier sessionStorage state distribution (§24)", () => {
  it("persists exactly the provider id + model keys (never the API key, never the ack)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(completeFrontierSelection(), storage);
    expect(storage.entries.get(GENERATION_PROVIDER_STORAGE_KEY)).toBe("frontier");
    expect(storage.entries.get(FRONTIER_PROVIDER_STORAGE_KEY)).toBe("openai");
    expect(storage.entries.get(FRONTIER_MODEL_STORAGE_KEY)).toBe("gpt-4o-mini");
    // EXACTLY the three non-secret keys — the memory-only apiKey and the ack
    // must leave NO trace in storage.
    expect(storage.entries.size).toBe(3);
    for (const [key, value] of storage.entries) {
      expect(String(key)).not.toContain("key");
      expect(String(value)).not.toContain("sk-test-phase30");
    }
  });

  it("set/get/clear round-trip the frontier provider id; hostile stored values read null", () => {
    const storage = fakeStorage();
    expect(setFrontierProvider("openai", storage)).toBe(true);
    expect(getFrontierProvider(storage)).toBe("openai");
    expect(setFrontierProvider("https://evil.example", storage)).toBe(false);
    storage.setItem(FRONTIER_PROVIDER_STORAGE_KEY, "openai?url=https://evil.example");
    expect(getFrontierProvider(storage)).toBeNull();
    clearFrontierProvider(storage);
    expect(getFrontierProvider(storage)).toBeNull();
  });

  it("set/get/clear round-trip the frontier model (trimmed; blank clears)", () => {
    const storage = fakeStorage();
    expect(getFrontierModel(storage)).toBeNull();
    expect(setFrontierModel("  gpt-4o-mini  ", storage)).toBe(true);
    expect(getFrontierModel(storage)).toBe("gpt-4o-mini");
    expect(setFrontierModel("   ", storage)).toBe(false);
    expect(storage.entries.has(FRONTIER_MODEL_STORAGE_KEY)).toBe(false);
    clearFrontierModel(storage);
    expect(storage.entries.has(FRONTIER_MODEL_STORAGE_KEY)).toBe(false);
  });

  it("persisting a NON-frontier selection leaves the frontier preference keys INERT (Fake -> Frontier restore)", () => {
    const storage = fakeStorage();
    persistGenerationSelection(completeFrontierSelection(), storage);
    // Switch to Fake: the non-secret frontier prefs stay (inert), so returning
    // to Frontier restores provider + model while the key must be re-entered.
    persistGenerationSelection(
      { generationProvider: "fake", ollamaTransport: null, ollamaModel: "" },
      storage,
    );
    expect(storage.entries.get(GENERATION_PROVIDER_STORAGE_KEY)).toBe("fake");
    expect(storage.entries.get(FRONTIER_PROVIDER_STORAGE_KEY)).toBe("openai");
    expect(storage.entries.get(FRONTIER_MODEL_STORAGE_KEY)).toBe("gpt-4o-mini");
    // The reset clears the frontier preference keys too.
    clearGenerationSelection(storage);
    expect(storage.entries.has(FRONTIER_PROVIDER_STORAGE_KEY)).toBe(false);
    expect(storage.entries.has(FRONTIER_MODEL_STORAGE_KEY)).toBe(false);
  });
});

describe("Phase 30 — resolveProviderSelection restores frontier prefs with DISCARD-IF-STALE (catalog membership)", () => {
  it("restores a stored frontier provider id + model when both are still valid", () => {
    const storage = fakeStorage();
    setGenerationProvider("frontier", storage);
    setFrontierProvider("openrouter", storage);
    setFrontierModel("claude-3-5-sonnet", storage);
    const selection = resolveProviderSelection(CAPS_WITH_FRONTIER, storage);
    expect(selection?.generationProvider).toBe("frontier");
    expect(selection?.frontierProviderId).toBe("openrouter");
    expect(selection?.frontierModel).toBe("claude-3-5-sonnet");
    // The API key is memory-only: a fresh resolve NEVER resurrects a secret.
    expect(selection?.frontierApiKey).toBeUndefined();
  });

  it("DISCARD-IF-STALE: a stored provider id no longer in the catalog resolves to null (dropdown resets)", () => {
    const storage = fakeStorage();
    setGenerationProvider("frontier", storage);
    setFrontierProvider("deepinfra", storage); // not in the fixture catalog
    const selection = resolveProviderSelection(CAPS_WITH_FRONTIER, storage);
    expect(selection?.generationProvider).toBe("frontier");
    expect(selection?.frontierProviderId).toBeNull();
  });

  it("a hostile stored provider id (URL/query injection) is dropped, never restored", () => {
    const storage = fakeStorage();
    setGenerationProvider("frontier", storage);
    storage.setItem(FRONTIER_PROVIDER_STORAGE_KEY, "openai?url=https://evil.example");
    const selection = resolveProviderSelection(CAPS_WITH_FRONTIER, storage);
    expect(selection?.frontierProviderId).toBeNull();
    expect(selection?.frontierModel).toBe("");
  });

  it("an unavailable stored FRONTIER provider is ignored by the provider DISCARD-IF-STALE rule (fake wins)", () => {
    const storage = fakeStorage();
    setGenerationProvider("frontier", storage);
    setFrontierProvider("openai", storage);
    const selection = resolveProviderSelection(CAPS_WITH_PROVIDERS, storage); // frontier unavailable fixture
    expect(selection?.generationProvider).toBe("fake");
    // The non-frontier selection carries NO frontier keys at all (the resolved
    // shape is byte-identical to Phase 25 for fake/ollama).
    expect(selection).not.toHaveProperty("frontierProviderId");
    expect(selection).not.toHaveProperty("frontierModel");
  });
});

describe("Phase 30 — toCreateCaseGeneration serializes the BYOK block (§6)", () => {
  it("a COMPLETE frontier selection emits frontier: {provider, apiKey, model} — and NO URL anywhere", () => {
    const generation = toCreateCaseGeneration(completeFrontierSelection());
    expect(generation).toEqual({
      generationProvider: "frontier",
      frontier: { provider: "openai", apiKey: "sk-test-phase30-0000", model: "gpt-4o-mini" },
    });
    const serialized = JSON.stringify(generation);
    expect(serialized).not.toContain("http");
    expect(serialized).not.toContain("url");
    expect(serialized).not.toContain("endpoint");
  });

  it("an INCOMPLETE frontier selection emits NO frontier block (fail-closed flat generationProvider only)", () => {
    // Missing key.
    expect(toCreateCaseGeneration(completeFrontierSelection({ frontierApiKey: "" }))).toEqual({
      generationProvider: "frontier",
    });
    // Whitespace-only key.
    expect(toCreateCaseGeneration(completeFrontierSelection({ frontierApiKey: "   " }))).toEqual({
      generationProvider: "frontier",
    });
    // Missing provider id.
    expect(toCreateCaseGeneration(completeFrontierSelection({ frontierProviderId: null }))).toEqual({
      generationProvider: "frontier",
    });
    // Missing model.
    expect(toCreateCaseGeneration(completeFrontierSelection({ frontierModel: "  " }))).toEqual({
      generationProvider: "frontier",
    });
  });

  it("fake/ollama/no-selection serializations stay BYTE-IDENTICAL to Phase 25/28", () => {
    expect(
      toCreateCaseGeneration({ generationProvider: "fake", ollamaTransport: null, ollamaModel: "" }),
    ).toEqual({ generationProvider: "fake" });
    expect(
      toCreateCaseGeneration({
        generationProvider: "ollama",
        ollamaTransport: "bridge",
        ollamaModel: "hermes3:8b",
      }),
    ).toEqual({ generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "hermes3:8b" });
    expect(toCreateCaseGeneration(null)).toBeUndefined();
  });
});

describe("Phase 30 — isFrontierSubmitReady (§5: provider + key + model + ack gating)", () => {
  it("is NOT gated for null selections, non-frontier providers and older servers", () => {
    expect(isFrontierSubmitReady(null, CAPS_WITH_FRONTIER)).toBe(true);
    expect(
      isFrontierSubmitReady(
        { generationProvider: "fake", ollamaTransport: null, ollamaModel: "" },
        CAPS_WITH_FRONTIER,
      ),
    ).toBe(true);
    expect(
      isFrontierSubmitReady(
        { generationProvider: "ollama", ollamaTransport: null, ollamaModel: "" },
        CAPS_WITH_FRONTIER,
      ),
    ).toBe(true);
    // No additive offer (older server) -> nothing to gate.
    expect(isFrontierSubmitReady(FRONTIER_SELECTION, { modes: [{ id: "demo", available: true }] })).toBe(
      true,
    );
  });

  it("is TRUE only when provider + key + model + ack are all present AND the provider is in the CURRENT catalog", () => {
    expect(isFrontierSubmitReady(completeFrontierSelection(), CAPS_WITH_FRONTIER)).toBe(true);
    expect(isFrontierSubmitReady(FRONTIER_SELECTION, CAPS_WITH_FRONTIER)).toBe(false);
    expect(isFrontierSubmitReady(completeFrontierSelection({ frontierApiKey: "" }), CAPS_WITH_FRONTIER)).toBe(
      false,
    );
    expect(
      isFrontierSubmitReady(completeFrontierSelection({ frontierApiKey: "   " }), CAPS_WITH_FRONTIER),
    ).toBe(false);
    expect(isFrontierSubmitReady(completeFrontierSelection({ frontierAck: false }), CAPS_WITH_FRONTIER)).toBe(
      false,
    );
    expect(isFrontierSubmitReady(completeFrontierSelection({ frontierModel: "" }), CAPS_WITH_FRONTIER)).toBe(
      false,
    );
    // A provider id NOT in the current catalog is stale -> not ready.
    expect(
      isFrontierSubmitReady(completeFrontierSelection({ frontierProviderId: "deepinfra" }), CAPS_WITH_FRONTIER),
    ).toBe(false);
  });

  it("stays NOT gated when frontier is no longer offered/available (its radio is disabled; the server rejects explicitly)", () => {
    // Frontier is unavailable in this fixture (CAPS_WITH_PROVIDERS) — a leftover
    // frontier selection must not lock the form: the explicit provider
    // rejection surfaces through the safe error copy instead.
    expect(isFrontierSubmitReady(completeFrontierSelection(), CAPS_WITH_PROVIDERS)).toBe(true);
  });
});