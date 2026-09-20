import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { GenerationCapabilitiesResponse, GenerationModeId } from "../api/types";
import { providerQualifierFromCapabilities } from "../journey/providerMode";
import { examplePromptText, validatePrompt } from "../journey/promptValidation";
import NewCasePage from "./new";

/**
 * Prompt-to-case screen (Phase 8 B): form presence + data-testids, default
 * difficulty, the verbatim REQUIREMENTS 48 Case A example prompt (the
 * "Use example prompt" fill target), and the pure prompt validation.
 */

describe("/new — Phase 17D Bugfix PART B example-prompt selector (static render)", () => {
  const html = () =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage />
      </MemoryRouter>,
    );

  it("renders the example section with the three buttons (no redundant heading)", () => {
    const markup = html();
    // Section + the three buttons keep their contract test-ids; the redundant
    // "Try an example:" heading is gone (Phase 17E PART J).
    expect(markup).toContain('data-testid="example-prompts"');
    expect(markup).toContain('data-testid="example-easy"');
    expect(markup).toContain('data-testid="example-medium"');
    expect(markup).toContain('data-testid="example-hard"');
    expect(markup).not.toContain('data-testid="example-prompts-heading"');
    expect(markup).not.toContain("Try an example:");
  });

  it("shows the label + helper copy of every example on its button", () => {
    const markup = html();
    expect(markup).toContain(">Easy</span>");
    expect(markup).toContain("Known environment and common objects.");
    expect(markup).toContain(">Medium</span>");
    expect(markup).toContain("More varied setting and evidence.");
    expect(markup).toContain(">Hard</span>");
    expect(markup).toContain("Includes an unusual object that may require procedural 3D generation.");
  });

  it("uses plain type=\"button\" actions — example select can never submit the form", () => {
    const markup = html();
    // Helper: capture the full element tag for a testid, whatever the
    // attribute order, then assert its type.
    const tagOf = (testid: string): string => {
      const match = markup.match(new RegExp(`<button[^>]*data-testid="${testid}"[^>]*>`));
      if (!match) throw new Error(`<button data-testid="${testid}"> not found in markup`);
      return match[0];
    };
    // Every example button must be type="button" (NOT submit): clicking fills
    // the textarea only and can never start generation on its own.
    expect(tagOf("example-easy")).toContain('type="button"');
    expect(tagOf("example-medium")).toContain('type="button"');
    expect(tagOf("example-hard")).toContain('type="button"');
    // The explicit Generate case button remains the ONLY submit action.
    expect(tagOf("generate-case")).toContain('type="submit"');
  });

  it("starts with NO example active (all aria-pressed false, no active class)", () => {
    const markup = html();
    const tagOf = (testid: string): string => {
      const match = markup.match(new RegExp(`<button[^>]*data-testid="${testid}"[^>]*>`));
      if (!match) throw new Error(`<button data-testid="${testid}"> not found in markup`);
      return match[0];
    };
    for (const id of ["example-easy", "example-medium", "example-hard"]) {
      const tag = tagOf(id);
      expect(tag).toContain('aria-pressed="false"');
      expect(tag).not.toContain("new-case-example--active");
    }
    expect(markup).not.toContain('aria-pressed="true"');
  });

  it("exposes NO internal implementation detail in the prompt UI (D7)", () => {
    const markup = html();
    const lowered = markup.toLowerCase();
    expect(markup).not.toContain("proc.");
    expect(lowered).not.toContain("assetspec");
    expect(lowered).not.toContain("asset spec");
    expect(lowered).not.toContain("phase 13");
    expect(lowered).not.toContain("phase 17");
    expect(lowered).not.toContain("solver");
    expect(lowered).not.toContain("4551660f4a46b2eb");
  });

  it("removes the redundant 'Use example prompt' button while keeping the demo flows", () => {
    const markup = html();
    // Phase 17E PART J — the redundant button is GONE (new contract).
    expect(markup).not.toContain('data-testid="use-example-prompt"');
    expect(markup).not.toContain("Use example prompt");
    // The deterministic demo link remains separate and unchanged.
    expect(markup).toContain('data-testid="try-demo-from-new"');
    expect(markup).toContain("Try the demo case");
  });
});

describe("Phase 17E PART K — /new UI cleanup regression (static render)", () => {
  const html = () =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage />
      </MemoryRouter>,
    );

  it("K6 — the redundant 'Use example prompt' button is GONE (query returns nothing)", () => {
    const markup = html();
    expect(markup).not.toContain('data-testid="use-example-prompt"');
    expect(markup).not.toContain("Use example prompt");
  });

  it("K7 — the redundant 'Try an example:' label is GONE while the section/testids remain", () => {
    const markup = html();
    expect(markup).not.toContain("Try an example:");
    expect(markup).not.toContain('data-testid="example-prompts-heading"');
    // The plugin-compatible section + the three card test-ids are preserved.
    expect(markup).toContain('data-testid="example-prompts"');
    expect(markup).toContain('data-testid="example-easy"');
    expect(markup).toContain('data-testid="example-medium"');
    expect(markup).toContain('data-testid="example-hard"');
  });

  it("K8 — [ Generate case ] remains present", () => {
    const markup = html();
    expect(markup).toContain('data-testid="generate-case"');
    expect(markup).toContain("Generate case");
  });

  it("K9 — Difficulty remains present", () => {
    const markup = html();
    expect(markup).toContain('data-testid="difficulty-select"');
    expect(markup).toContain("Difficulty (optional)");
  });

  it("K10 — Generation mode remains present", () => {
    // Render with capabilities that expose the generation-mode selector.
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage
          capabilities={{ modes: [{ id: "demo", available: true }] }}
        />
      </MemoryRouter>,
    );
    // Demo-only caps render the "Demo mode active" notice (no selector).
    expect(markup).toContain('data-testid="generation-mode-demo-notice"');
    expect(markup).toContain("Demo mode active");
  });

  it("K11 — the deterministic demo link remains unchanged", () => {
    const markup = html();
    expect(markup).toContain('data-testid="try-demo-from-new"');
    expect(markup).toContain("Try the demo case");
    expect(markup).toContain('data-testid="try-demo-note"');
    expect(markup).toContain("Deterministic demo — no API keys, no cost.");
  });
});

describe("validatePrompt — /new form validation", () => {
  it("rejects an empty prompt with a safe message", () => {
    const result = validatePrompt("   ");
    expect(result.ok).toBe(false);
    expect(result.trimmed).toBeNull();
    expect(result.error).not.toBeNull();
  });

  it("accepts a trimmed non-empty prompt", () => {
    const result = validatePrompt("  A murder in a hotel  ");
    expect(result.ok).toBe(true);
    expect(result.trimmed).toBe("A murder in a hotel");
    expect(result.error).toBeNull();
  });

  it("rejects prompts beyond the 4000-character bound", () => {
    const overflow = "x".repeat(4001);
    const result = validatePrompt(overflow);
    expect(result.ok).toBe(false);
    expect(result.error).toContain("4000");
  });

  it("accepts exactly the 4000-character bound", () => {
    const result = validatePrompt("x".repeat(4000));
    expect(result.ok).toBe(true);
  });
});

describe("examplePromptText — the deterministic demo prompt fill target", () => {
  it("is the REQUIREMENTS 48 Case A input VERBATIM (user input, no solution logic)", () => {
    const expected = [
      "Victim: Sarah Miller",
      "Murderer: Thomas Reed",
      "Motive: €240,000 embezzlement",
      "Weapon: Kitchen knife",
      "Time: 22:17",
      "Witness: Emily Reed",
    ].join("\n");
    expect(examplePromptText()).toBe(expected);
  });

  it("stays within the prompt length bound", () => {
    expect(examplePromptText().length).toBeLessThanOrEqual(4000);
    expect(examplePromptText().trim().length).toBeGreaterThan(0);
  });
});

describe("/new form rendering", () => {
  const html = () =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage />
      </MemoryRouter>,
    );

  it("renders the prompt input with the required placeholder and char counter", () => {
    const markup = html();
    expect(markup).toContain('data-testid="prompt-input"');
    expect(markup).toContain("Describe a crime or paste an example…");
    expect(markup).toContain('data-testid="prompt-char-count"');
    expect(markup).toContain("0 / 4000");
    expect(markup).toContain("maxLength=\"4000\"");
  });

  it("defaults the difficulty select to medium", () => {
    const markup = html();
    expect(markup).toContain('data-testid="difficulty-select"');
    expect(markup).toContain("value=\"medium\"");
    expect(markup).toContain("<option value=\"easy\">Easy</option>");
    expect(markup).toContain("<option value=\"hard\">Hard</option>");
  });

  it("renders the Generate case and demo-link actions (no redundant example button)", () => {
    const markup = html();
    expect(markup).toContain('data-testid="generate-case"');
    expect(markup).toContain("Generate case");
    expect(markup).not.toContain('data-testid="use-example-prompt"');
    expect(markup).not.toContain("Use example prompt");
    expect(markup).toContain('data-testid="try-demo-from-new"');
    expect(markup).toContain("Try the demo case");
  });

  it("labels the demo path as deterministic / zero-cost / no API keys (Phase 15)", () => {
    const markup = html();
    expect(markup).toContain('data-testid="try-demo-note"');
    expect(markup).toContain("Deterministic demo — no API keys, no cost.");
  });

  it("emphasises the custom prompt and keeps the generate path provider-honest (Phase 15)", () => {
    const markup = html();
    // The form intro is the natural-language prompt emphasising copy.
    expect(markup).toContain("Write your own detective scenario");
    // Default fake build: the provider note must not claim live-AI behavior.
    expect(markup).toContain('data-testid="generate-provider-note"');
    expect(markup).toContain("uses the built-in deterministic generator in this demo build");
  });

  it("renders the honest provider qualifier next to the primary CTA (ADV-152)", () => {
    const markup = html();
    // Same spot + wording as the landing. Phase 18A: capability-driven — with
    // no capabilities injected the honest deterministic-demo default is shown.
    expect(markup).toContain('data-testid="provider-qualifier"');
    expect(markup).toContain(providerQualifierFromCapabilities(null));
    // The per-path demo note stays verbatim (additive copy, nothing removed).
    expect(markup).toContain("Deterministic demo — no API keys, no cost.");
  });
});

describe("/new — Phase 16 Track B generation-mode selector", () => {
  const renderWithCaps = (capabilities: GenerationCapabilitiesResponse | null): string =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={capabilities} />
      </MemoryRouter>,
    );

  it("renders the selector with the available modes", () => {
    const markup = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "live", available: true, label: "Cloud AI" },
      ],
    });
    expect(markup).toContain('data-testid="generation-mode-selector"');
    expect(markup).toContain("Cloud AI");
  });

  it("shows the static demo notice instead of a selector when only demo is available", () => {
    const markup = renderWithCaps({
      modes: [{ id: "demo", available: true }],
    });
    expect(markup).not.toContain('data-testid="generation-mode-selector"');
    expect(markup).toContain('data-testid="generation-mode-demo-notice"');
    expect(markup).toContain("Demo mode active");
  });
});

describe("/new — Phase 16.2 §20 mode-honest Local-AI availability", () => {
  const renderWith = (
    capabilities: GenerationCapabilitiesResponse | null,
    modeOverride: GenerationModeId | null,
  ): string =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={capabilities} modeOverride={modeOverride} />
      </MemoryRouter>,
    );

  it("shows the explicit Local AI unavailable note when local is stored but the backend says unavailable", () => {
    const markup = renderWith({ modes: [{ id: "demo", available: true }] }, "local");
    expect(markup).toContain('data-testid="local-ai-unavailable"');
    expect(markup).toContain("Local AI is unavailable right now.");
    expect(markup).toContain("Demo Mode remains available.");
  });

  it("shows the note when the probe failed and resolved to the demo-only payload (backend down)", () => {
    const markup = renderWith({ modes: [] }, "local");
    expect(markup).toContain('data-testid="local-ai-unavailable"');
    expect(markup).toContain("Local AI is unavailable right now.");
  });

  it("does NOT silently pretend local is active — no selected local option, no showcase claim", () => {
    const markup = renderWith({ modes: [{ id: "demo", available: true }] }, "local");
    expect(markup).not.toContain('value="local"');
    expect(markup).not.toContain('data-testid="local-ai-showcase-note"');
  });

  it("does not show the unavailable note when the backend reports local available", () => {
    const markup = renderWith(
      {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        ],
      },
      "local",
    );
    expect(markup).not.toContain('data-testid="local-ai-unavailable"');
  });

  it("renders nothing about unavailability while capabilities are still unknown (no claim)", () => {
    const markup = renderWith(null, "local");
    expect(markup).not.toContain('data-testid="local-ai-unavailable"');
  });

  it("keeps the default demo journey free of any unavailability note", () => {
    const markup = renderWith({ modes: [{ id: "demo", available: true }] }, null);
    expect(markup).not.toContain('data-testid="local-ai-unavailable"');
  });
});

describe("/new — Phase 16.2 §36 showcase copy honesty", () => {
  const renderWith = (
    capabilities: GenerationCapabilitiesResponse | null,
    modeOverride: GenerationModeId | null,
  ): string =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={capabilities} modeOverride={modeOverride} />
      </MemoryRouter>,
    );

  const LOCAL_AVAILABLE: GenerationCapabilitiesResponse = {
    modes: [
      { id: "demo", available: true },
      { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
    ],
  };

  it("shows the accurate muted showcase sentence when Local AI mode is active", () => {
    const markup = renderWith(LOCAL_AVAILABLE, "local");
    expect(markup).toContain('data-testid="local-ai-showcase-note"');
    expect(markup).toContain("generative Prompt-to-World pipeline");
    expect(markup).toContain("local Llama 3.2 model");
    expect(markup).toContain("the model proposes structured data");
    expect(markup).toContain("deterministic validators verify and construct");
  });

  it("never claims the local pipeline in demo/fake mode (no claiming copy)", () => {
    const markup = renderWith(LOCAL_AVAILABLE, null);
    // The §36 SHOWCASE element is rendered ONLY while local mode is ACTIVE
    // (selected AND backend-reported available). In plain demo mode it is
    // never rendered, and no in-context pipeline claim (Prompt-to-World /
    // Llama / "proves the case") ever appears.
    expect(markup).not.toContain('data-testid="local-ai-showcase-note"');
    expect(markup).not.toContain("Prompt-to-World");
    expect(markup).not.toContain("Llama 3.2");
    expect(markup.toLowerCase()).not.toContain("proves the case");
    // Phase 18A: the app-level QUALIFIER may describe local AVAILABILITY
    // ("the model proposes structured data ... is available") because the
    // backend capability report is the authority — that is additive honesty,
    // it never expands the showcase element itself.
    expect(markup).toContain("Local AI is available");
  });

  it("does not show the showcase claim while local is selected but unavailable", () => {
    const markup = renderWith({ modes: [{ id: "demo", available: true }] }, "local");
    expect(markup).not.toContain('data-testid="local-ai-showcase-note"');
    expect(markup).not.toContain("Prompt-to-World");
  });

  it("never renders host/IP, credential, prompt or diagnostic strings from any DTO", () => {
    const hostile: GenerationCapabilitiesResponse = {
      modes: [
        { id: "demo", available: true },
        {
          id: "local",
          available: true,
          label: "Local AI",
          model: "http://127.0.0.1:11434 — apiKey=hunter2 — prompt=topsecret — diagnostics=boom",
        },
      ],
    };
    const markup = renderWith(hostile, "local");
    expect(markup).not.toContain("127.0.0.1");
    expect(markup).not.toContain("hunter2");
    expect(markup).not.toContain("topsecret");
    expect(markup).not.toContain("diagnostics");
    expect(markup).not.toContain("11434");
  });
});

describe("/new — Phase 18A capability-driven provider notes", () => {
  const renderWith = (capabilities: GenerationCapabilitiesResponse | null): string =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={capabilities} />
      </MemoryRouter>,
    );

  const DEMO_ONLY: GenerationCapabilitiesResponse = {
    modes: [
      { id: "demo", available: true },
      { id: "local", available: false, label: "Local AI" },
    ],
  };
  const LOCAL_READY: GenerationCapabilitiesResponse = {
    modes: [
      { id: "demo", available: true },
      { id: "local", available: true, label: "Local AI", model: "llama3.2:3b" },
    ],
  };
  const LIVE_READY: GenerationCapabilitiesResponse = {
    modes: [
      { id: "demo", available: true },
      { id: "live", available: true, label: "Cloud AI" },
    ],
  };

  it("demo-only backend -> deterministic note and qualifier, never local/live claims", () => {
    const markup = renderWith(DEMO_ONLY);
    expect(markup).toContain("uses the built-in deterministic generator in this demo build");
    expect(markup).toContain("Demo build: deterministic built-in generator");
    expect(markup).not.toContain("Live AI provider");
    expect(markup).not.toContain("Local AI is available");
  });

  it("local-ready backend -> the note and qualifier reflect local availability", () => {
    const markup = renderWith(LOCAL_READY);
    expect(markup).toContain("uses the local AI pipeline");
    expect(markup).toContain("Local AI is available");
    expect(markup).not.toContain("uses the built-in deterministic generator in this demo build");
  });

  it("local-unavailable backend -> the deterministic note; the explicit unavailable note only for a stored local", () => {
    const markup = renderWith(DEMO_ONLY);
    expect(markup).toContain("uses the built-in deterministic generator in this demo build");
  });

  it("live-ready backend -> the note and qualifier reflect the live provider", () => {
    const markup = renderWith(LIVE_READY);
    expect(markup).toContain("Live AI provider");
    expect(markup).toContain("Live AI provider enabled.");
    expect(markup).not.toContain("uses the built-in deterministic generator in this demo build");
  });

  it("unknown backend (capabilities still loading) -> honest deterministic default", () => {
    const markup = renderWith(null);
    expect(markup).toContain("uses the built-in deterministic generator in this demo build");
    expect(markup).not.toContain("Live AI provider");
    expect(markup).not.toContain("Local AI is available");
  });

  it("the deterministic demo link stays separate and is NEVER labelled live AI", () => {
    // Whatever the backend reports, "Try the demo case" keeps its
    // deterministic zero-cost sub-note and never claims live behavior.
    for (const capabilities of [DEMO_ONLY, LOCAL_READY, LIVE_READY, null]) {
      const markup = renderWith(capabilities);
      const demoMarkup = markup.match(/data-testid="try-demo-note"[\s\S]*?<\/p>/)?.[0] ?? "";
      expect(demoMarkup).toContain("Deterministic demo — no API keys, no cost.");
      expect(demoMarkup).not.toContain("Live AI provider");
      expect(markup).toContain('data-testid="try-demo-from-new"');
    }
  });

  it("the §36 local showcase sentence still requires local mode ACTIVE + available", () => {
    // Capability-driven notes must not soften the §36 gate: the showcase copy
    // appears ONLY while local is selected AND the backend reports it ready.
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={LOCAL_READY} modeOverride="local" />
      </MemoryRouter>,
    );
    expect(markup).toContain('data-testid="local-ai-showcase-note"');
    const demoOnly = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={DEMO_ONLY} modeOverride="local" />
      </MemoryRouter>,
    );
    expect(demoOnly).not.toContain('data-testid="local-ai-showcase-note"');
    expect(demoOnly).toContain('data-testid="local-ai-unavailable"');
  });
});