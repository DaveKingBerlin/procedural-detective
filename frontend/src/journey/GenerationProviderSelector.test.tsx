// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { GenerationProviderSelector } from "./GenerationProviderSelector";
import type { GenerationProviderSelection } from "./generationProvider";

declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

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

describe("GenerationProviderSelector — Phase 26C1 the VISIBLE transport is the SELECTED transport (§2/§5/§7)", () => {
  const TRANSPORT_CHECKED_SERVER = 'name="generation-ollama-transport" checked="" value="server"';
  const TRANSPORT_CHECKED_BRIDGE = 'name="generation-ollama-transport" checked="" value="bridge"';
  const NO_TRANSPORT_CHECKED = 'name="generation-ollama-transport" checked=""';

  it("both transports available + Server selected -> the Server radio is the ONLY checked one (§8.1)", () => {
    const markup = render(CAPS, OLLAMA_SELECTION);
    expect(markup).toContain(TRANSPORT_CHECKED_SERVER);
    expect(markup).not.toContain(TRANSPORT_CHECKED_BRIDGE);
    // The same state that drives the visual control drives serialization: the
    // selection object's transport is server (nothing here can diverge).
    expect(OLLAMA_SELECTION.ollamaTransport).toBe("server");
  });

  it("both transports available + Bridge selected -> the Bridge radio is the ONLY checked one (§8.2)", () => {
    const selection: GenerationProviderSelection = { ...OLLAMA_SELECTION, ollamaTransport: "bridge" };
    const markup = render(CAPS, selection);
    expect(markup).toContain(TRANSPORT_CHECKED_BRIDGE);
    expect(markup).not.toContain(TRANSPORT_CHECKED_SERVER);
    expect(selection.ollamaTransport).toBe("bridge");
  });

  it("Bridge connected does NOT override an explicit Server radio (§8.3)", () => {
    const caps: GenerationCapabilitiesResponse = {
      ...CAPS,
      providers: [
        ...CAPS.providers!.slice(0, 1),
        {
          ...CAPS.providers![1],
          transports: {
            server: { available: true, reason: null },
            bridge: { available: true, connected: true },
          },
        },
        ...CAPS.providers!.slice(2),
      ],
    };
    const markup = render(caps, OLLAMA_SELECTION); // selection carries server
    expect(markup).toContain(TRANSPORT_CHECKED_SERVER);
    expect(markup).not.toContain(TRANSPORT_CHECKED_BRIDGE);
  });

  it("Server available does NOT override an explicit Bridge radio (§8.4)", () => {
    const selection: GenerationProviderSelection = { ...OLLAMA_SELECTION, ollamaTransport: "bridge" };
    const markup = render(CAPS, selection);
    expect(markup).toContain(TRANSPORT_CHECKED_BRIDGE);
    expect(markup).not.toContain(TRANSPORT_CHECKED_SERVER);
  });

  it("an Ollama selection WITHOUT a transport checks NO transport radio (visible == what will be posted) (§8.16)", () => {
    const markup = render(CAPS, { generationProvider: "ollama", ollamaTransport: null, ollamaModel: "" });
    expect(markup).not.toContain(NO_TRANSPORT_CHECKED);
    expect(markup).not.toContain(TRANSPORT_CHECKED_SERVER);
    expect(markup).not.toContain(TRANSPORT_CHECKED_BRIDGE);
  });

  it("an unavailable SELECTED Bridge stays checked + disabled and shows its unavailable state (§8.12)", () => {
    const caps: GenerationCapabilitiesResponse = {
      modes: [],
      defaultProvider: "ollama",
      providers: [
        {
          id: "ollama",
          label: "Ollama",
          available: true,
          transports: {
            server: { available: true },
            bridge: { available: false, reason: "not_connected" },
          },
        },
      ],
    };
    const markup = render(caps, { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: "" });
    expect(markup).toContain(TRANSPORT_CHECKED_BRIDGE);
    expect(markup).not.toContain(TRANSPORT_CHECKED_SERVER);
    const bridgeBlock = markup.match(/data-testid="generation-ollama-transport-bridge"[\s\S]*?<\/label>/)?.[0] ?? "";
    expect(bridgeBlock).toContain('checked=""');
    expect(bridgeBlock).toContain('disabled=""');
    expect(markup).toContain("not connected");
  });

  it("an unavailable SELECTED Server stays checked + disabled and shows its unavailable state (§8.13)", () => {
    const caps: GenerationCapabilitiesResponse = {
      modes: [],
      defaultProvider: "ollama",
      providers: [
        {
          id: "ollama",
          label: "Ollama",
          available: true,
          transports: {
            server: { available: false, reason: "not_configured" },
            bridge: { available: true },
          },
        },
      ],
    };
    const markup = render(caps, { generationProvider: "ollama", ollamaTransport: "server", ollamaModel: "" });
    expect(markup).toContain(TRANSPORT_CHECKED_SERVER);
    expect(markup).not.toContain(TRANSPORT_CHECKED_BRIDGE);
    const serverBlock = markup.match(/data-testid="generation-ollama-transport-server"[\s\S]*?<\/label>/)?.[0] ?? "";
    expect(serverBlock).toContain('checked=""');
    expect(serverBlock).toContain('disabled=""');
    // The frozen reason copy (never a raw diagnostic) is shown next to the
    // selected-but-unavailable transport.
    expect(markup).toContain("not configured");
  });

  it("availability is displayed independently of the selection (§5)", () => {
    const markup = render(CAPS, OLLAMA_SELECTION); // server selected, bridge available/not-connected
    expect(markup).toContain('data-testid="generation-ollama-transport-server-availability"');
    expect(markup).toContain('data-testid="generation-ollama-transport-bridge-availability"');
    // Both transports are available here -> both tags state that; the bridge
    // adds its honest session-scoped "Not connected" while still selectable.
    expect(markup).toMatch(
      /data-testid="generation-ollama-transport-server-availability"[\s\S]*?>Available<\/span>/,
    );
    expect(markup).toMatch(
      /data-testid="generation-ollama-transport-bridge-availability"[\s\S]*?>Not connected<\/span>/,
    );
    // A connected bridge renders "Connected".
    const connectedCaps: GenerationCapabilitiesResponse = {
      modes: [],
      providers: [
        { id: "fake", available: true },
        {
          id: "ollama",
          available: true,
          transports: {
            server: { available: true },
            bridge: { available: true, connected: true },
          },
        },
      ],
    };
    const connectedMarkup = render(connectedCaps, {
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "",
    });
    expect(connectedMarkup).toMatch(
      /data-testid="generation-ollama-transport-bridge-availability"[\s\S]*?>Connected<\/span>/,
    );
  });
});

describe("GenerationProviderSelector — interactive selectProvider (INFONote A1a: no-usable-transport fail-closed path)", () => {
  /** Ollama offered but BOTH transports unavailable; default provider fake. */
  const NO_USABLE_TRANSPORT: GenerationCapabilitiesResponse = {
    modes: [],
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
          server: { available: false, reason: "not_configured" },
          bridge: { available: false, reason: "not_connected" },
        },
      },
    ],
  };

  const FAKE_SELECTION: GenerationProviderSelection = {
    generationProvider: "fake",
    ollamaTransport: null,
    ollamaModel: "",
  };

  afterEach(() => {
    sessionStorage.clear();
  });

  it("selectProvider('ollama') with both transports unavailable emits ollamaTransport: null (documented fail-closed — never a fabricated fallback)", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const changes: GenerationProviderSelection[] = [];
    const root = createRoot(container);
    act(() => {
      root.render(
        <GenerationProviderSelector
          capabilities={NO_USABLE_TRANSPORT}
          selection={FAKE_SELECTION}
          onChange={(next) => void changes.push(next)}
        />,
      );
    });
    const ollamaRadio = container.querySelector<HTMLInputElement>(
      '[data-testid="generation-provider-ollama"] input[type="radio"]',
    );
    if (!ollamaRadio) throw new Error("ollama provider radio not found");
    act(() => {
      ollamaRadio.click();
    });
    expect(changes.length).toBe(1);
    expect(changes[0]).toEqual({
      generationProvider: "ollama",
      // Both transports unavailable + no stored choice -> the deterministic
      // no-preference default resolves to NULL (no usable transport). The
      // user then sees the unavailable state; serializing this selection
      // omits the transport and the backend rejects it fail-closed with
      // INVALID_GENERATION_PROVIDER -> the safe `invalidGenerationProvider`
      // copy on submit (pinned unit-side in generationProvider.test.ts and
      // demoFlow.test.ts). Never a silent server/bridge fallback.
      ollamaTransport: null,
      ollamaModel: "qwen2.5:1.5b",
    });
    act(() => {
      root.unmount();
    });
    container.remove();
  });
});

describe("GenerationProviderSelector — Phase 30 BYOK Frontier panel (§5/§24/§33)", () => {
  /** Phase 30 §9 fixture: frontier available with the trusted provider catalog. */
  const FRONTIER_CAPS: GenerationCapabilitiesResponse = {
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

  const ACTIVE_FRONTIER: GenerationProviderSelection = {
    generationProvider: "frontier",
    ollamaTransport: null,
    ollamaModel: "",
    frontierProviderId: null,
    frontierModel: "",
  };

  it("reveals the provider/key/model/cost-ack panel ONLY when Frontier is the ACTIVE provider", () => {
    // Fake selection: no frontier panel anywhere.
    const fakeMarkup = render(FRONTIER_CAPS, FAKE_SELECTION);
    expect(fakeMarkup).not.toContain('data-testid="generation-frontier-controls"');
    expect(fakeMarkup).not.toContain('data-testid="generation-frontier-key-input"');
    // Frontier active: the panel appears.
    const markup = render(FRONTIER_CAPS, ACTIVE_FRONTIER);
    expect(markup).toContain('data-testid="generation-frontier-controls"');
    expect(markup).toContain('data-testid="generation-frontier-provider-select"');
    expect(markup).toContain('data-testid="generation-frontier-key-input"');
    expect(markup).toContain('data-testid="generation-frontier-model-input"');
    expect(markup).toContain('data-testid="generation-frontier-ack-checkbox"');
  });

  it("populates the provider dropdown from the server's safe catalog (ids + labels only; no URL anywhere)", () => {
    const markup = render(FRONTIER_CAPS, ACTIVE_FRONTIER);
    expect(markup).toContain("<option value=\"openai\">OpenAI</option>");
    expect(markup).toContain("<option value=\"openrouter\">OpenRouter</option>");
    expect(markup).toContain("<option value=\"groq\">Groq</option>");
    expect(markup).not.toContain("https://");
    expect(markup).not.toContain("api.");
  });

  it("renders the API key as a PASSWORD field with the Show toggle", () => {
    const markup = render(FRONTIER_CAPS, ACTIVE_FRONTIER);
    expect(markup).toContain('type="password"');
    expect(markup).toContain('data-testid="generation-frontier-key-toggle"');
    expect(markup).toContain("Show");
    // autoComplete discourages password-manager capture; never "off" secrets in a visible field.
    expect(markup).toContain('autoComplete="new-password"');
  });

  it("renders the frozen privacy copy (§33) and the cost acknowledgement copy (§25)", () => {
    const markup = render(FRONTIER_CAPS, ACTIVE_FRONTIER);
    expect(markup).toContain("Use your own hosted AI API");
    expect(markup).toContain("sends the generation request through our server using your key");
    expect(markup).toContain("is not stored");
    expect(markup).toContain("may charge your account for usage");
    // The §33 line must NEVER claim the key is invisible to the server.
    expect(markup).not.toContain("never see your key");
    expect(markup).not.toContain("we never see");
    expect(markup).toContain(
      "I understand that this request uses my API key and may create charges with the selected provider.",
    );
  });

  it("never renders a secret/URL from a hostile provider catalog label into the dropdown", () => {
    const hostile = {
      ...FRONTIER_CAPS,
      providers: [
        ...FRONTIER_CAPS.providers!.slice(0, 1),
        {
          id: "frontier",
          label: "Frontier",
          available: true,
          providers: [{ id: "openai", label: "https://evil.example — token=abc" }],
        },
      ],
    } as GenerationCapabilitiesResponse;
    const markup = render(hostile, ACTIVE_FRONTIER);
    expect(markup).not.toContain("evil.example");
    expect(markup).not.toContain("token=abc");
    // The SAFE id falls back as the visible label — the option stays usable.
    expect(markup).toContain("<option value=\"openai\">openai</option>");
  });

  it("a hostile catalog URL/query id is dropped from the dropdown entirely", () => {
    const hostile = {
      ...FRONTIER_CAPS,
      providers: [
        ...FRONTIER_CAPS.providers!.slice(0, 1),
        {
          id: "frontier",
          label: "Frontier",
          available: true,
          providers: [{ id: "https://evil.example", label: "Evil" }],
        },
      ],
    } as GenerationCapabilitiesResponse;
    const markup = render(hostile, ACTIVE_FRONTIER);
    expect(markup).not.toContain("evil.example");
    expect(markup).not.toContain("<option value=\"https");
  });
});

describe("GenerationProviderSelector — Phase 30 interactive secrets (jsdom)", () => {
  const FRONTIER_CAPS: GenerationCapabilitiesResponse = {
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
          { id: "groq", label: "Groq" },
        ],
      },
    ],
  };

  /** The shared "active frontier, empty fields" selection state. */
  const ACTIVE_FRONTIER: GenerationProviderSelection = {
    generationProvider: "frontier",
    ollamaTransport: null,
    ollamaModel: "",
    frontierProviderId: null,
    frontierModel: "",
    frontierApiKey: "",
  };

  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    sessionStorage.clear();
  });

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container.remove();
    sessionStorage.clear();
  });

  /** Render the selector with a frontier selection and capture the onChange payloads. */
  function mountFrontier(
    selection: GenerationProviderSelection,
  ): GenerationProviderSelection[] {
    const changes: GenerationProviderSelection[] = [];
    act(() => {
      root = createRoot(container);
      root.render(
        <GenerationProviderSelector
          capabilities={FRONTIER_CAPS}
          selection={selection}
          onChange={(next) => void changes.push(next)}
        />,
      );
    });
    return changes;
  }

  function clickToggle(): void {
    const toggle = container.querySelector<HTMLElement>('[data-testid="generation-frontier-key-toggle"]');
    if (!toggle) throw new Error("key toggle not found");
    act(() => {
      toggle.click();
    });
  }

  function setKeyInput(value: string): void {
    const input = container.querySelector<HTMLInputElement>('[data-testid="generation-frontier-key-input"]');
    if (!input) throw new Error("key input not found");
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

  function clickProviderRadio(testid: string): void {
    const input = container.querySelector<HTMLInputElement>(`[data-testid="${testid}"] input[type="radio"]`);
    if (!input) throw new Error(`radio not found: ${testid}`);
    act(() => {
      input.click();
    });
  }

  it("Show/Hide toggles the key field between password and text", () => {
    const keyInput = () =>
      container.querySelector<HTMLInputElement>('[data-testid="generation-frontier-key-input"]');
    mountFrontier(ACTIVE_FRONTIER);
    expect(keyInput()?.type).toBe("password");
    clickToggle();
    expect(keyInput()?.type).toBe("text");
    expect(container.querySelector('[data-testid="generation-frontier-key-toggle"]')?.textContent).toBe(
      "Hide",
    );
    clickToggle();
    expect(keyInput()?.type).toBe("password");
    expect(container.querySelector('[data-testid="generation-frontier-key-toggle"]')?.textContent).toBe(
      "Show",
    );
  });

  it("typing the key emits a sanitized, memory-only selection change (CR/LF stripped)", () => {
    const changes = mountFrontier(ACTIVE_FRONTIER);
    setKeyInput("sk-secret\r\nphase30");
    expect(changes.length).toBe(1);
    expect(changes[0].frontierApiKey).toBe("sk-secretphase30");
  });

  it("switching the Frontier PROVIDER clears ONLY the re-entered key (documented §24 decision)", () => {
    const changes = mountFrontier({
      generationProvider: "frontier",
      ollamaTransport: null,
      ollamaModel: "",
      frontierProviderId: "openai",
      frontierModel: "gpt-4o-mini",
      frontierApiKey: "sk-openai-1234",
      frontierAck: true,
    });
    selectProviderOption("groq");
    expect(changes.length).toBe(1);
    expect(changes[0].frontierProviderId).toBe("groq");
    expect(changes[0].frontierApiKey).toBe("");
    // The NON-SENSITIVE model preference + generic cost ack are preserved:
    // only the provider-bound secret must be re-entered (§24 documented).
    expect(changes[0].frontierModel).toBe("gpt-4o-mini");
    expect(changes[0].frontierAck).toBe(true);
  });

  it("switching AWAY from Frontier drops the secret from state (and keeps nothing sensitive)", () => {
    const changes = mountFrontier({
      generationProvider: "frontier",
      ollamaTransport: null,
      ollamaModel: "",
      frontierProviderId: "openai",
      frontierModel: "gpt-4o-mini",
      frontierApiKey: "sk-openai-1234",
      frontierAck: true,
    });
    clickProviderRadio("generation-provider-fake");
    expect(changes.length).toBe(1);
    const next = changes[0];
    expect(next.generationProvider).toBe("fake");
    // The new selection object carries NO frontier fields at all — the
    // memory-only key and the ack are GONE from state.
    expect(next).not.toHaveProperty("frontierApiKey");
    expect(next).not.toHaveProperty("frontierAck");
    expect(next).not.toHaveProperty("frontierProviderId");
  });

  it("the component itself performs NO storage writes (the sentinel key never reaches sessionStorage)", () => {
    mountFrontier(ACTIVE_FRONTIER);
    setKeyInput("SECRET-PHASE30-MUST-NOT-PERSIST-123");
    // This component never touches storage — the parent owns persistence. A
    // sentinel key value can therefore not exist in any storage surface here.
    // (The route-level persistence contract is pinned in new.providerSelector.)
    const stored: Array<[string, string]> = [];
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i);
      if (key !== null) stored.push([key, sessionStorage.getItem(key) ?? ""]);
    }
    expect(stored).toEqual([]);
  });
});