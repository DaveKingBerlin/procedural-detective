// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { getBridgeStatus } from "../api/client";
import NewCasePage from "./new";
import { clearJourneyParams, getJourneyParams } from "../journey/context";
import {
  FRONTIER_MODEL_STORAGE_KEY,
  FRONTIER_PROVIDER_STORAGE_KEY,
  GENERATION_MODEL_STORAGE_KEY,
  GENERATION_PROVIDER_STORAGE_KEY,
  OLLAMA_TRANSPORT_STORAGE_KEY,
} from "../journey/generationProvider";

declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// Phase 26C1 (LOW fix) — the route's LOCAL bridge panel uses the REAL default
// services, so the route-level ordering tests drive a genuine
// [Connect local Ollama] click through mocked bridge endpoints. All the other
// tests in this file never click Connect and are unaffected (the capabilities
// override is what /new actually renders).
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    // The route's useGenerationCapabilities hook always fires once on mount;
    // a resolved minimal DTO keeps the fake-timer orderings deterministic
    // (every test supplies the `capabilities` override anyway).
    getGenerationCapabilities: vi.fn(async () => ({ modes: [] })),
    createAnonymousSession: vi.fn(async () => ({
      anonymousSessionToken: "anon-route-pairing",
      quotaWindowEndsAt: 1e12,
    })),
    createBridgePairing: vi.fn(async () => ({
      pairingSessionId: "PAIR-0001",
      pairingCode: "PD-X7K4-92QP",
      expiresAt: 1e12 + 240,
    })),
    getBridgeStatus: vi.fn(async () => ({
      remoteLocalAi: { available: true, connected: false, model: null, ready: false },
    })),
  };
});

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

describe("/new — Phase 26C1 the VISIBLE transport drives the SERIALIZED payload (§1-§8, jsdom)", () => {
  /** Default provider fake: the initial selection is Demo / Fake, so the user
   *  must actively switch to Ollama (the Fake -> Ollama restore path). */
  const DEFAULT_FAKE: GenerationCapabilitiesResponse = {
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

  /** Default provider ollama: the initial resolved selection is Ollama with
   *  the no-preference transport default. */
  const DEFAULT_OLLAMA: GenerationCapabilitiesResponse = {
    ...DEFAULT_FAKE,
    defaultProvider: "ollama",
  };

  /** Same Offer, refreshed with the bridge connected for this session. */
  const REFRESHED_BRIDGE_CONNECTED: GenerationCapabilitiesResponse = {
    ...DEFAULT_OLLAMA,
    providers: [
      ...DEFAULT_OLLAMA.providers!.slice(0, 1),
      {
        ...DEFAULT_OLLAMA.providers![1],
        transports: {
          server: { available: true, reason: null },
          bridge: { available: true, connected: true },
        },
      },
      ...DEFAULT_OLLAMA.providers!.slice(2),
    ],
  };

  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    sessionStorage.clear();
    clearJourneyParams();
  });

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container.remove();
    sessionStorage.clear();
    clearJourneyParams();
  });

  async function mountWith(capabilities: GenerationCapabilitiesResponse | null): Promise<void> {
    await act(async () => {
      root = createRoot(container);
      root.render(
        <MemoryRouter initialEntries={["/new"]}>
          <NewCasePage capabilities={capabilities} />
        </MemoryRouter>,
      );
      // Let the capabilities effect resolve the initial selection.
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });
  }

  /** Re-RENDER the SAME mounted route with fresh capabilities (a refresh). */
  async function refreshWith(capabilities: GenerationCapabilitiesResponse | null): Promise<void> {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/new"]}>
          <NewCasePage capabilities={capabilities} />
        </MemoryRouter>,
      );
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });
  }

  function clickRadio(testid: string): void {
    const input = container.querySelector<HTMLInputElement>(
      `[data-testid="${testid}"] input[type="radio"]`,
    );
    if (!input) throw new Error(`radio not found: ${testid}`);
    act(() => {
      input.click();
    });
  }

  function checkedProvider(): string | null {
    return container.querySelector<HTMLInputElement>('input[name="generation-provider"]:checked')
      ?.value ?? null;
  }

  function checkedTransport(): string | null {
    return container.querySelector<HTMLInputElement>('input[name="generation-ollama-transport"]:checked')
      ?.value ?? null;
  }

  /** The explicit "Generate case" submit (React's onSubmit path). */
  function submit(): void {
    const form = container.querySelector<HTMLFormElement>('[data-testid="prompt-form"]');
    if (!form) throw new Error("prompt form not found");
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
  }

  /** Fill the prompt textarea with a real keystroke (valid prompt -> submit
   *  stageJourney succeeds). */
  function typePrompt(text: string): void {
    const ta = container.querySelector<HTMLTextAreaElement>('[data-testid="prompt-input"]');
    if (!ta) throw new Error("prompt textarea not found");
    const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
    const setter = descriptor?.set;
    if (!descriptor || typeof setter !== "function") {
      throw new Error("HTMLTextAreaElement.prototype.value setter is missing");
    }
    act(() => {
      Object.defineProperty(ta, "value", { configurable: true, ...descriptor });
      setter.call(ta, text);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  it("Server -> Bridge immediately before submit posts Bridge (§8.14)", async () => {
    await mountWith(DEFAULT_OLLAMA);
    // No stored transport -> the no-preference default is the visible Server.
    expect(checkedTransport()).toBe("server");
    // The user switches to Bridge right before pressing Generate.
    clickRadio("generation-ollama-transport-bridge");
    expect(checkedTransport()).toBe("bridge");
    typePrompt("A body in the library at midnight.");
    submit();
    expect(getJourneyParams()?.generationProvider).toBe("ollama");
    expect(getJourneyParams()?.ollamaTransport).toBe("bridge");
  });

  it("Bridge -> Server immediately before submit posts Server (§8.15)", async () => {
    // A persisted Bridge preference from a previous session is restored.
    sessionStorage.setItem(GENERATION_PROVIDER_STORAGE_KEY, "ollama");
    sessionStorage.setItem(OLLAMA_TRANSPORT_STORAGE_KEY, "bridge");
    await mountWith(DEFAULT_OLLAMA);
    expect(checkedTransport()).toBe("bridge");
    // The user switches to Server right before pressing Generate.
    clickRadio("generation-ollama-transport-server");
    expect(checkedTransport()).toBe("server");
    typePrompt("A body in the library at midnight.");
    submit();
    expect(getJourneyParams()?.ollamaTransport).toBe("server");
  });

  it("visible control equals the serialized payload across every switch (§8.16)", async () => {
    await mountWith(DEFAULT_OLLAMA);
    typePrompt("A body in the library at midnight.");
    expect(checkedTransport()).toBe("server");
    submit();
    expect(getJourneyParams()?.ollamaTransport).toBe("server");

    clickRadio("generation-ollama-transport-bridge");
    expect(checkedTransport()).toBe("bridge");
    submit();
    expect(getJourneyParams()?.ollamaTransport).toBe("bridge");

    clickRadio("generation-ollama-transport-server");
    expect(checkedTransport()).toBe("server");
    submit();
    expect(getJourneyParams()?.ollamaTransport).toBe("server");
  });

  it("Fake -> Ollama restores the intended persisted transport (§8.8)", async () => {
    // The persisted Ollama preference (bridge) from an earlier session.
    sessionStorage.setItem(OLLAMA_TRANSPORT_STORAGE_KEY, "bridge");
    sessionStorage.setItem(GENERATION_MODEL_STORAGE_KEY, "hermes3:8b");
    await mountWith(DEFAULT_FAKE);
    // Initial: the server default provider (fake) — no transport shown.
    expect(checkedProvider()).toBe("fake");
    expect(container.querySelector('[data-testid="generation-ollama-controls"]')).toBeNull();
    // The user actively returns to Ollama: bridge is restored, not server-first.
    clickRadio("generation-provider-ollama");
    expect(checkedTransport()).toBe("bridge");
    typePrompt("A body in the library at midnight.");
    submit();
    expect(getJourneyParams()?.generationProvider).toBe("ollama");
    expect(getJourneyParams()?.ollamaTransport).toBe("bridge");
    // The selection was persisted (survives a reload).
    expect(sessionStorage.getItem(GENERATION_PROVIDER_STORAGE_KEY)).toBe("ollama");
    expect(sessionStorage.getItem(OLLAMA_TRANSPORT_STORAGE_KEY)).toBe("bridge");
  });

  it("a capability refresh never overwrites an explicit Server (bridge connection is display-only) (§8.5)", async () => {
    await mountWith(DEFAULT_OLLAMA);
    expect(checkedTransport()).toBe("server");
    // The refreshed DTO now reports the bridge connected for this session —
    // the explicit Server selection must NOT be replaced.
    await refreshWith(REFRESHED_BRIDGE_CONNECTED);
    expect(checkedTransport()).toBe("server");
    typePrompt("A body in the library at midnight.");
    submit();
    expect(getJourneyParams()?.ollamaTransport).toBe("server");
  });

  it("a capability refresh never overwrites an explicit Bridge (§8.6)", async () => {
    sessionStorage.setItem(GENERATION_PROVIDER_STORAGE_KEY, "ollama");
    sessionStorage.setItem(OLLAMA_TRANSPORT_STORAGE_KEY, "bridge");
    await mountWith(DEFAULT_OLLAMA);
    expect(checkedTransport()).toBe("bridge");
    await refreshWith(DEFAULT_OLLAMA); // identical-capability refresh
    expect(checkedTransport()).toBe("bridge");
    typePrompt("A body in the library at midnight.");
    submit();
    expect(getJourneyParams()?.ollamaTransport).toBe("bridge");
  });

  // Phase 26C1 LOW (test gap) — a route-level submit chain for a stored but
  // UNAVAILABLE selected transport through stageJourney: the transport is
  // AUTHORITATIVE (§2/§4/§7) — it stays checked + honestly labelled in the UI,
  // and staging carries it VERBATIM (no availability gate ever omits it).
  it("an unavailable SELECTED Bridge is checked + disabled in the UI and STILL staged — staging never drops the transport (accepted test gap)", async () => {
    sessionStorage.setItem(GENERATION_PROVIDER_STORAGE_KEY, "ollama");
    sessionStorage.setItem(OLLAMA_TRANSPORT_STORAGE_KEY, "bridge");
    sessionStorage.setItem(GENERATION_MODEL_STORAGE_KEY, "hermes3:8b");
    // The bridge transport now reports unavailable / not-connected while the
    // SELECTED transport preference stays bridge.
    const bridgeDown: GenerationCapabilitiesResponse = {
      ...DEFAULT_OLLAMA,
      providers: [
        ...DEFAULT_OLLAMA.providers!.slice(0, 1),
        {
          ...DEFAULT_OLLAMA.providers![1],
          transports: {
            server: { available: true, reason: null },
            bridge: { available: false, reason: "not_connected" },
          },
        },
        ...DEFAULT_OLLAMA.providers!.slice(2),
      ],
    };
    await mountWith(bridgeDown);
    // Visible control: the selected-but-unavailable Bridge stays CHECKED and
    // is shown as unavailable ("not connected"), NOT hidden and NOT rewritten
    // to the available Server side.
    expect(checkedTransport()).toBe("bridge");
    const bridgeInput = container.querySelector<HTMLInputElement>(
      '[data-testid="generation-ollama-transport-bridge"] input[type="radio"]',
    );
    expect(bridgeInput).not.toBeNull();
    expect(bridgeInput?.disabled).toBe(true);
    expect(bridgeInput?.checked).toBe(true);
    const availability =
      container.querySelector('[data-testid="generation-ollama-transport-bridge-availability"]')
        ?.textContent ?? "";
    expect(availability).toContain("not connected");
    // Full staging chain: /new -> stageJourney -> JourneyParams. The staging
    // path copies the selection unconditionally — the unavailable transport
    // is preserved, not dropped by any availability gate.
    typePrompt("A body in the library at midnight.");
    submit();
    expect(getJourneyParams()?.generationProvider).toBe("ollama");
    expect(getJourneyParams()?.ollamaTransport).toBe("bridge");
    expect(getJourneyParams()?.ollamaModel).toBe("hermes3:8b");
  });
});

// Phase 30 — BYOK Frontier: the route reveals the BYOK panel, gates the
// submit until provider + key + model + cost ack are complete, carries the
// selection (AND the memory-only key) through stageJourney — while the key
// NEVER enters any storage surface (§15/§24/§32).
describe("/new — Phase 30 BYOK Frontier selection (§5/§24/§32)", () => {
  const SENTINEL_KEY = "SECRET-PHASE30-MUST-NOT-PERSIST-123";

  const CAPS_WITH_FRONTIER_AVAILABLE: GenerationCapabilitiesResponse = {
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

  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    sessionStorage.clear();
    clearJourneyParams();
  });

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container.remove();
    sessionStorage.clear();
    clearJourneyParams();
  });

  async function mountWith(capabilities: GenerationCapabilitiesResponse | null): Promise<void> {
    await act(async () => {
      root = createRoot(container);
      root.render(
        <MemoryRouter initialEntries={["/new"]}>
          <NewCasePage capabilities={capabilities} />
        </MemoryRouter>,
      );
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });
  }

  function clickRadio(testid: string): void {
    const input = container.querySelector<HTMLInputElement>(
      `[data-testid="${testid}"] input[type="radio"]`,
    );
    if (!input) throw new Error(`radio not found: ${testid}`);
    act(() => {
      input.click();
    });
  }

  function setInputValue(testid: string, value: string): void {
    const input = container.querySelector<HTMLInputElement>(`[data-testid="${testid}"]`);
    if (!input) throw new Error(`input not found: ${testid}`);
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    const setter = descriptor?.set;
    if (!descriptor || typeof setter !== "function") {
      throw new Error("HTMLInputElement.prototype.value setter is missing");
    }
    act(() => {
      Object.defineProperty(input, "value", { configurable: true, ...descriptor });
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  function selectProviderOption(id: string): void {
    const select = container.querySelector<HTMLSelectElement>(
      '[data-testid="generation-frontier-provider-select"]',
    );
    if (!select) throw new Error("provider select not found");
    const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
    const setter = descriptor?.set;
    if (!descriptor || typeof setter !== "function") {
      throw new Error("HTMLSelectElement.prototype.value setter is missing");
    }
    act(() => {
      Object.defineProperty(select, "value", { configurable: true, ...descriptor });
      setter.call(select, id);
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  function setModel(value: string): void {
    setInputValue("generation-frontier-model-input", value);
  }

  function setKey(value: string): void {
    setInputValue("generation-frontier-key-input", value);
  }

  function clickAck(): void {
    const checkbox = container.querySelector<HTMLInputElement>(
      '[data-testid="generation-frontier-ack-checkbox"]',
    );
    if (!checkbox) throw new Error("ack checkbox not found");
    act(() => {
      checkbox.click();
    });
  }

  function typePrompt(text: string): void {
    const ta = container.querySelector<HTMLTextAreaElement>('[data-testid="prompt-input"]');
    if (!ta) throw new Error("prompt textarea not found");
    const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
    const setter = descriptor?.set;
    if (!descriptor || typeof setter !== "function") {
      throw new Error("HTMLTextAreaElement.prototype.value setter is missing");
    }
    act(() => {
      Object.defineProperty(ta, "value", { configurable: true, ...descriptor });
      setter.call(ta, text);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  function submit(): void {
    const form = container.querySelector<HTMLFormElement>('[data-testid="prompt-form"]');
    if (!form) throw new Error("prompt form not found");
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
  }

  function generateButton(): HTMLButtonElement {
    const button = container.querySelector<HTMLButtonElement>('[data-testid="generate-case"]');
    if (!button) throw new Error("generate-case button not found");
    return button;
  }

  /** Complete provider + key + model + ack and return to the caller with the
   *  prompt typed and one frontier radio click applied. */
  async function completeFrontierConfig(): Promise<void> {
    await mountWith(CAPS_WITH_FRONTIER_AVAILABLE);
    clickRadio("generation-provider-frontier");
    selectProviderOption("openai");
    setKey(SENTINEL_KEY);
    setModel("gpt-4o-mini");
    clickAck();
    expect(generateButton().disabled).toBe(false);
  }

  it("frontier radio click reveals the BYOK panel with the provider dropdown sourced from the capability registry", async () => {
    await mountWith(CAPS_WITH_FRONTIER_AVAILABLE);
    expect(container.querySelector('[data-testid="generation-frontier-controls"]')).toBeNull();
    clickRadio("generation-provider-frontier");
    expect(container.querySelector('[data-testid="generation-frontier-controls"]')).not.toBeNull();
    const options = Array.from(
      container.querySelectorAll("#generation-frontier-provider-select option"),
    ).map((option) => (option as HTMLOptionElement).textContent);
    expect(options).toEqual(["Select a provider", "OpenAI", "OpenRouter", "Groq"]);
    // The key starts as a PASSWORD field.
    expect(
      container.querySelector<HTMLInputElement>('[data-testid="generation-frontier-key-input"]')?.type,
    ).toBe("password");
  });

  it("the Generate button stays DISABLED until provider + key + model + cost ack are all valid", async () => {
    await mountWith(CAPS_WITH_FRONTIER_AVAILABLE);
    expect(generateButton().disabled).toBe(false); // default provider fake -> not gated
    clickRadio("generation-provider-frontier");
    // Frontier active + incomplete -> gated (disabled).
    expect(generateButton().disabled).toBe(true);
    selectProviderOption("openai");
    expect(generateButton().disabled).toBe(true);
    setKey("sk-some-key");
    expect(generateButton().disabled).toBe(true);
    setModel("gpt-4o-mini");
    expect(generateButton().disabled).toBe(true);
    clickAck();
    expect(generateButton().disabled).toBe(false);
  });

  it("submit is BLOCKED while the frontier config is incomplete (defense-in-depth gate on stageJourney)", async () => {
    await mountWith(CAPS_WITH_FRONTIER_AVAILABLE);
    clickRadio("generation-provider-frontier");
    selectProviderOption("openai");
    setKey("sk-some-key");
    setModel("gpt-4o-mini");
    // NO ack yet: a direct form submit must NOT stage the journey.
    clearJourneyParams();
    typePrompt("A body in the library at midnight.");
    submit();
    expect(getJourneyParams()).toBeNull();
    const error = container.querySelector<HTMLElement>('[data-testid="prompt-error"]')?.textContent ?? "";
    expect(error).toContain("cost notice");
  });

  it("a COMPLETE frontier submission carries provider/key/model through JourneyParams while the key NEVER reaches sessionStorage", async () => {
    await completeFrontierConfig();
    typePrompt("A body in the library at midnight.");
    submit();
    const params = getJourneyParams();
    expect(params?.generationProvider).toBe("frontier");
    expect(params?.frontierProviderId).toBe("openai");
    expect(params?.frontierModel).toBe("gpt-4o-mini");
    // The key travels ONLY in memory (JourneyParams) — it is NOT in storage.
    expect(params?.frontierApiKey).toBe(SENTINEL_KEY);
    // SessionStorage holds EXACTLY the three non-secret preference keys —
    // never the apiKey, never the ack.
    expect(sessionStorage.getItem(GENERATION_PROVIDER_STORAGE_KEY)).toBe("frontier");
    expect(sessionStorage.getItem(FRONTIER_PROVIDER_STORAGE_KEY)).toBe("openai");
    expect(sessionStorage.getItem(FRONTIER_MODEL_STORAGE_KEY)).toBe("gpt-4o-mini");
    expect(sessionStorage.length).toBe(3);
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i);
      const value = key === null ? "" : sessionStorage.getItem(key) ?? "";
      expect(value).not.toContain(SENTINEL_KEY);
      expect(String(key)).not.toContain("api");
      expect(String(key)).not.toContain("ack");
    }
  });

  it("the Frontier API key is MEMORY-ONLY: a full submit then reload REPEATS re-entry (no key on a fresh resolve)", async () => {
    await completeFrontierConfig();
    typePrompt("A body in the library at midnight.");
    submit();
    // A real RELOAD is a brand-new route instance (fresh React state). The
    // harness must unmount the old root first — a `root.render` of the same
    // component type would merely MUTATE the existing instance (React
    // reconciliation), which would wrongly keep the in-memory key.
    act(() => {
      root.unmount();
    });
    await mountWith(CAPS_WITH_FRONTIER_AVAILABLE);
    const keyInput = container.querySelector<HTMLInputElement>(
      '[data-testid="generation-frontier-key-input"]',
    );
    expect(keyInput?.value ?? "").toBe("");
    // The stored provider restores (DISCARD-IF-STALE vs the catalog)…
    expect(sessionStorage.getItem(FRONTIER_PROVIDER_STORAGE_KEY)).toBe("openai");
    // …but without a fresh key the form is gated again.
    expect(generateButton().disabled).toBe(true);
  });

  it("a stored UNKNOWN/stale Frontier provider id is DISCARDED (dropdown placeholder; submit gated)", async () => {
    sessionStorage.setItem(GENERATION_PROVIDER_STORAGE_KEY, "frontier");
    sessionStorage.setItem(FRONTIER_PROVIDER_STORAGE_KEY, "deepinfra"); // not in the catalog
    sessionStorage.setItem(FRONTIER_MODEL_STORAGE_KEY, "gpt-4o-mini");
    await mountWith(CAPS_WITH_FRONTIER_AVAILABLE);
    // The stale provider id is not restored: the dropdown shows a blank value.
    const select = container.querySelector<HTMLSelectElement>(
      '[data-testid="generation-frontier-provider-select"]',
    );
    expect(select?.value ?? "").toBe("");
    expect(generateButton().disabled).toBe(true);
  });

  it("switching AWAY from Frontier never persisted the key — later stages carry NO secret", async () => {
    await completeFrontierConfig();
    // Switch back to Demo / Fake before submitting.
    clickRadio("generation-provider-fake");
    typePrompt("A body in the library at midnight.");
    submit();
    const params = getJourneyParams();
    expect(params?.generationProvider).toBe("fake");
    expect(params).not.toHaveProperty("frontierProviderId");
    expect(params).not.toHaveProperty("frontierModel");
    expect(params).not.toHaveProperty("frontierApiKey");
    // The non-secret frontier prefs stay inert in storage; the key has no
    // storage surface at all.
    expect(sessionStorage.getItem(GENERATION_PROVIDER_STORAGE_KEY)).toBe("fake");
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i);
      const value = key === null ? "" : sessionStorage.getItem(key) ?? "";
      expect(value).not.toContain(SENTINEL_KEY);
    }
  });
});

// Phase 26C1 LOW fix — the pairing-completion ordering rule, end-to-end at
// /new. `handleBridgePaired` may select + persist the Bridge transport ONLY
// when the user has not made an explicit provider/transport radio choice since
// THIS pairing began. Two orderings are pinned:
//   1. explicit choice DURING the pairing window -> the radio choice wins over
//      the completion (the finding's interleaved timeline);
//   2. explicit choice BEFORE the pairing started -> the completion wins (the
//      pairing is the newer intent; the guard is re-armed when the window
//      opens via onBridgePairingStarted).
// Both drive a REAL [Connect local Ollama] click through the mocked bridge
// endpoints above (fake timers; deterministic poll sequence).
describe("/new — Phase 26C1 LOW fix: pairing-completion ordering guard (interleaved, route-level)", () => {
  /** DEFAULT_OLLAMA + remoteLocalAi offered so the bridge panel renders. */
  const CAPS_WITH_BRIDGE: GenerationCapabilitiesResponse = {
    modes: [{ id: "demo", available: true }],
    defaultProvider: "ollama",
    remoteLocalAi: { available: true, connected: false, model: null, ready: false },
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

  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    sessionStorage.clear();
    clearJourneyParams();
    vi.useFakeTimers();
    // Deterministic default: any poll beyond the per-test sequence reports
    // still-waiting (the panel keeps polling; a completion only happens on the
    // explicitly configured connected poll).
    const status = vi.mocked(getBridgeStatus);
    status.mockReset();
    status.mockResolvedValue({
      remoteLocalAi: { available: true, connected: false, model: null, ready: false },
    });
  });

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container.remove();
    sessionStorage.clear();
    clearJourneyParams();
    vi.useRealTimers();
  });

  async function mountRoute(): Promise<void> {
    await act(async () => {
      root = createRoot(container);
      root.render(
        <MemoryRouter initialEntries={["/new"]}>
          <NewCasePage capabilities={CAPS_WITH_BRIDGE} />
        </MemoryRouter>,
      );
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });
  }

  async function settle(): Promise<void> {
    await act(async () => {
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });
  }

  async function advance(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  function clickButton(testid: string): void {
    const element = container.querySelector(`[data-testid="${testid}"]`);
    if (!element) throw new Error(`element not found: ${testid}`);
    act(() => {
      (element as HTMLElement).click();
    });
  }

  function clickRadio(testid: string): void {
    const input = container.querySelector<HTMLInputElement>(
      `[data-testid="${testid}"] input[type="radio"]`,
    );
    if (!input) throw new Error(`radio not found: ${testid}`);
    act(() => {
      input.click();
    });
  }

  function checkedTransport(): string | null {
    return (
      container.querySelector<HTMLInputElement>('input[name="generation-ollama-transport"]:checked')
        ?.value ?? null
    );
  }

  function typePrompt(text: string): void {
    const ta = container.querySelector<HTMLTextAreaElement>('[data-testid="prompt-input"]');
    if (!ta) throw new Error("prompt textarea not found");
    const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
    const setter = descriptor?.set;
    if (!descriptor || typeof setter !== "function") {
      throw new Error("HTMLTextAreaElement.prototype.value setter is missing");
    }
    act(() => {
      Object.defineProperty(ta, "value", { configurable: true, ...descriptor });
      setter.call(ta, text);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  function submit(): void {
    const form = container.querySelector<HTMLFormElement>('[data-testid="prompt-form"]');
    if (!form) throw new Error("prompt form not found");
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
  }

  /** Configure the poll sequence: first poll still waiting, second poll
   *  connected (the pairing COMPLETES), then connected forever (safe poll
   *  continuation in the connected view). */
  function completeOnSecondPoll(): void {
    const status = vi.mocked(getBridgeStatus);
    status.mockReset();
    status
      .mockResolvedValueOnce({
        remoteLocalAi: { available: true, connected: false, model: null, ready: false },
      })
      .mockResolvedValueOnce({
        remoteLocalAi: { available: true, connected: true, model: "hermes3:8b", ready: true },
      })
      .mockResolvedValue({
        remoteLocalAi: { available: true, connected: true, model: "hermes3:8b", ready: true },
      });
  }

  it("an explicit Server radio choice made AFTER the pairing started wins over the completion (§3 interleaved)", async () => {
    // The user's established selection is Bridge (persisted) when the pairing
    // window opens; DURING the window they explicitly pick Server / Direct.
    sessionStorage.setItem(GENERATION_PROVIDER_STORAGE_KEY, "ollama");
    sessionStorage.setItem(OLLAMA_TRANSPORT_STORAGE_KEY, "bridge");
    await mountRoute();
    expect(checkedTransport()).toBe("bridge");

    // Pairing STARTS: the guard is re-armed (any earlier choice is now OLDER
    // intent, still eligible for the pairing override).
    clickButton("bridge-connect");
    await settle();
    expect(container.querySelector('[data-testid="bridge-waiting"]')).not.toBeNull();

    // The explicit Server / Direct radio choice DURING the window re-arms the
    // guard: this is NOW the newer intent.
    clickRadio("generation-ollama-transport-server");
    expect(checkedTransport()).toBe("server");

    // The poll completes the pairing (waiting -> connected) exactly once...
    completeOnSecondPoll();
    await advance(2000);
    await settle();
    await advance(2000);
    await settle();
    expect(container.querySelector('[data-testid="bridge-connected"]')).not.toBeNull();

    // ...but the explicit radio choice is the newer intent: Server stays
    // selected AND persisted (no blind bridge rewrite by the completion), and
    // the full submit chain posts Server.
    expect(checkedTransport()).toBe("server");
    expect(sessionStorage.getItem(OLLAMA_TRANSPORT_STORAGE_KEY)).toBe("server");
    typePrompt("A body in the library at midnight.");
    submit();
    expect(getJourneyParams()?.generationProvider).toBe("ollama");
    expect(getJourneyParams()?.ollamaTransport).toBe("server");
  });

  it("a Server choice made BEFORE the pairing started is the OLDER intent and is still overridden by the completion (§3 pre-pairing ordering)", async () => {
    await mountRoute();
    expect(checkedTransport()).toBe("server"); // no-preference default
    // An explicit pre-pairing interaction leaves the user on Server.
    clickRadio("generation-ollama-transport-bridge");
    expect(checkedTransport()).toBe("bridge");
    clickRadio("generation-ollama-transport-server");
    expect(checkedTransport()).toBe("server");

    // Pairing STARTS -> the pre-pairing Server choice is re-framed as OLDER
    // intent (the guard is disarmed by onBridgePairingStarted).
    clickButton("bridge-connect");
    await settle();
    expect(container.querySelector('[data-testid="bridge-waiting"]')).not.toBeNull();

    // The completion is then the NEWER intent and applies:
    completeOnSecondPoll();
    await advance(2000);
    await settle();
    await advance(2000);
    await settle();
    expect(container.querySelector('[data-testid="bridge-connected"]')).not.toBeNull();
    expect(checkedTransport()).toBe("bridge");
    expect(sessionStorage.getItem(GENERATION_PROVIDER_STORAGE_KEY)).toBe("ollama");
    expect(sessionStorage.getItem(OLLAMA_TRANSPORT_STORAGE_KEY)).toBe("bridge");

    typePrompt("A body in the library at midnight.");
    submit();
    expect(getJourneyParams()?.ollamaTransport).toBe("bridge");
  });
});