import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { GenerationCapabilitiesResponse, GenerationModeId } from "../api/types";
import { APP_PROVIDER_MODE, providerQualifier } from "../journey/providerMode";
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

  it("renders the compact 'Try an example:' section under the prompt textarea", () => {
    const markup = html();
    // Section + heading + the three buttons with their contract test-ids.
    expect(markup).toContain('data-testid="example-prompts"');
    expect(markup).toContain('data-testid="example-prompts-heading"');
    expect(markup).toContain("Try an example:");
    expect(markup).toContain('data-testid="example-easy"');
    expect(markup).toContain('data-testid="example-medium"');
    expect(markup).toContain('data-testid="example-hard"');
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

  it("keeps the existing 'Use example prompt' and demo flows intact", () => {
    const markup = html();
    expect(markup).toContain('data-testid="use-example-prompt"');
    expect(markup).toContain("Use example prompt");
    expect(markup).toContain('data-testid="try-demo-from-new"');
    expect(markup).toContain("Try the demo case");
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

describe("examplePromptText — the \"Use example prompt\" fill target", () => {
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

  it("renders the Generate case, Use example prompt and demo-link actions", () => {
    const markup = html();
    expect(markup).toContain('data-testid="generate-case"');
    expect(markup).toContain("Generate case");
    expect(markup).toContain('data-testid="use-example-prompt"');
    expect(markup).toContain("Use example prompt");
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
    // Same spot + wording as the landing: mode-aware, driven by providerMode.
    expect(markup).toContain('data-testid="provider-qualifier"');
    expect(markup).toContain(providerQualifier(APP_PROVIDER_MODE));
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
    expect(markup).not.toContain('data-testid="local-ai-showcase-note"');
    expect(markup).not.toContain("Prompt-to-World");
    expect(markup).not.toContain("Llama 3.2");
    expect(markup).not.toContain("proposes structured data");
    expect(markup.toLowerCase()).not.toContain("proves the case");
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