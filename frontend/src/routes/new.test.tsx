import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { GenerationCapabilitiesResponse, GenerationModeId } from "../api/types";
import { demoCtaNote } from "../journey/generationMode";
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
    // The demo link remains separate. Phase 21B Finding 3: the default
    // static render has NO capability DTO (null), so the CTA uses the
    // NEUTRAL label — the historical always-on "Try the demo case" +
    // deterministic promise is shown only for a known demo-only backend.
    expect(markup).toContain('data-testid="try-demo-from-new"');
    expect(markup).toContain("Try an example case");
    expect(markup).not.toContain("Deterministic demo — no API keys, no cost.");
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
    // Render with capabilities that expose the generation-mode display.
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage
          capabilities={{ modes: [{ id: "demo", available: true }] }}
        />
      </MemoryRouter>,
    );
    // Demo-only caps render the "Demo mode active" notice + the deterministic
    // line (Phase 21 F-03 — no selector, no provider switch).
    expect(markup).toContain('data-testid="generation-mode-demo-notice"');
    expect(markup).toContain("Demo mode active");
    expect(markup).toContain("Generation mode: Deterministic demo");
  });

  it("K11 — the demo link remains present with a truthful per-capability label + note (Phase 21B Finding 3)", () => {
    // Phase 21B Finding 3: the copy is capability-driven now. The default
    // static render (no capability DTO -> null) must use the NEUTRAL label
    // and NEVER the deterministic/no-cost promise; the demo-only backend
    // (GENERATION_PROVIDER=fake) keeps the historical deterministic claim.
    const neutral = html();
    expect(neutral).toContain('data-testid="try-demo-from-new"');
    expect(neutral).toContain("Try an example case");
    expect(neutral).not.toContain("Deterministic demo — no API keys, no cost.");

    const demoOnly = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={{ modes: [{ id: "demo", available: true }] }} />
      </MemoryRouter>,
    );
    expect(demoOnly).toContain('data-testid="try-demo-from-new"');
    expect(demoOnly).toContain("Try Demo Case");
    expect(demoOnly).toContain('data-testid="try-demo-note"');
    expect(demoOnly).toContain("Deterministic demo — no API keys, no cost.");
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
    // Phase 21B Finding 3: with no capability DTO (null) the demo link uses
    // the neutral truthful label — the old always-on "Try the demo case" is
    // reserved for a KNOWN demo-only backend.
    expect(markup).toContain("Try an example case");
  });

  it("labels the demo path as deterministic / zero-cost / no API keys ONLY for a known demo-only backend (Phase 15 + Phase 21B)", () => {
    // Phase 21B Finding 3: the default static render has NO capability DTO,
    // so the demo note must be the provider-neutral line — never the
    // deterministic/no-cost promise. The promise is asserted for the
    // demo-only capability below (the demo-only fixture keeps it truthful).
    const neutral = html();
    expect(neutral).toContain('data-testid="try-demo-note"');
    expect(neutral).not.toContain("Deterministic demo — no API keys, no cost.");

    const demoOnly = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={{ modes: [{ id: "demo", available: true }] }} />
      </MemoryRouter>,
    );
    expect(demoOnly).toContain("Deterministic demo — no API keys, no cost.");
  });

  it("emphasises the custom prompt and keeps the generate path provider-honest (Phase 15)", () => {
    const markup = html();
    // The form intro is the natural-language prompt emphasising copy.
    expect(markup).toContain("Write your own detective scenario");
    // Default DTO-unavailable render: DEF-097 — the note must NOT claim the
    // deterministic generator (the frontend cannot know the provider) and must
    // not claim live-AI behavior either.
    expect(markup).toContain('data-testid="generate-provider-note"');
    expect(markup).toContain(
      "runs through the backend-configured generation pipeline once the service is reachable",
    );
    expect(markup).not.toContain("uses the built-in deterministic generator in this demo build");
  });

  it("renders the honest provider qualifier next to the primary CTA (ADV-152)", () => {
    const markup = html();
    // Same spot + wording as the landing. Phase 18A + Phase 21B DEF-097:
    // capability-driven — with no capabilities injected the qualifier is the
    // NEUTRAL reachability copy (the frontend cannot know the provider).
    expect(markup).toContain('data-testid="provider-qualifier"');
    expect(markup).toContain(providerQualifierFromCapabilities(null));
    expect(markup).toContain("Generation is available once the service is reachable.");
    // Phase 21B Finding 3: the demo note is capability-driven too — with the
    // DTO unavailable (null) it is the neutral line, NOT the old always-on
    // deterministic promise (which is reserved for a known demo-only backend).
    expect(markup).not.toContain("Deterministic demo — no API keys, no cost.");
  });
});

describe("/new — Phase 21 F-03 read-only generation-mode display", () => {
  const renderWithCaps = (capabilities: GenerationCapabilitiesResponse | null): string =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={capabilities} />
      </MemoryRouter>,
    );

  it("renders the read-only authoritative line with the available mode's label", () => {
    const markup = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "live", available: true, label: "Cloud AI" },
      ],
    });
    // Phase 21 F-03: the container testid stays but holds a single read-only
    // line — no provider <select>, no provider options (the DIFFICULTY select
    // on this page is a legitimately different, non-provider control).
    expect(markup).toContain('data-testid="generation-mode-selector"');
    expect(markup).toContain("Generation mode: Cloud AI");
    expect(markup).not.toContain('data-testid="generation-mode-select"');
    expect(markup).not.toContain('<option value="local"');
    expect(markup).not.toContain('<option value="live"');
  });

  it("local available -> the read-only 'Local AI — <model> — Ready' line", () => {
    const markup = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
    expect(markup).toContain("Generation mode: Local AI — qwen2.5:7b — Ready");
    expect(markup).not.toContain('data-testid="generation-mode-demo-notice"');
  });

  it("shows the static demo notice + deterministic line when only demo is available", () => {
    const markup = renderWithCaps({
      modes: [{ id: "demo", available: true }],
    });
    expect(markup).not.toContain('data-testid="generation-mode-selector"');
    expect(markup).toContain('data-testid="generation-mode-demo-notice"');
    expect(markup).toContain("Demo mode active");
    expect(markup).toContain("Generation mode: Deterministic demo");
  });

  it("contains NO interactive provider selection control (no testid implies a switch)", () => {
    const markup = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
    // NOTE: the page legitimately has a DIFFICULTY select (not a provider
    // control); the provider-mode area must never host one.
    expect(markup).not.toContain('data-testid="generation-mode-select"');
    expect(markup).not.toContain('<select id="generation-mode-select"');
    expect(markup).not.toContain('value="local"');
    expect(markup).not.toContain('value="live"');
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

  it("unknown backend (capabilities still loading) -> NEUTRAL qualifier + note (no deterministic / live / local claim)", () => {
    // Phase 21B / DEF-097: while the DTO is unavailable (null — the probe is
    // pending/unreachable) the frontend CANNOT know the provider, so every
    // provider claim is suppressed: the qualifier and note are the neutral
    // reachability copy, never the "Demo build" deterministic story.
    const markup = renderWith(null);
    expect(markup).toContain("Generation is available once the service is reachable.");
    expect(markup).toContain(
      "runs through the backend-configured generation pipeline once the service is reachable",
    );
    expect(markup).not.toContain("Live AI provider");
    expect(markup).not.toContain("Local AI is available");
    expect(markup).not.toContain("uses the built-in deterministic generator in this demo build");
  });

  it("the demo link CTA is TRUTHFUL per the backend capability report (Phase 21B Finding 3)", () => {
    // Phase 21B Finding 3: the old always-on "Try the demo case" +
    // deterministic no-cost promise is shown ONLY for a KNOWN demo-only
    // backend. Each state now carries the truthful label + note from
    // src/journey/generationMode.ts (demoCtaLabel/demoCtaNote), and the link
    // is never labelled live AI.
    for (const capabilities of [DEMO_ONLY, LOCAL_READY, LIVE_READY, null]) {
      const markup = renderWith(capabilities);
      const demoMarkup = markup.match(/data-testid="try-demo-note"[\s\S]*?<\/p>/)?.[0] ?? "";
      expect(markup).toContain('data-testid="try-demo-from-new"');
      expect(demoMarkup).toContain(demoCtaNote(capabilities));
      expect(demoMarkup).not.toContain("Live AI provider");
    }
    // The demo-only backend keeps the historical deterministic promise.
    expect(renderWith(DEMO_ONLY)).toContain("Deterministic demo — no API keys, no cost.");
    // The local/live backend and the unknown/null DTO can never show it.
    expect(renderWith(LOCAL_READY)).not.toContain("Deterministic demo — no API keys, no cost.");
    expect(renderWith(LIVE_READY)).not.toContain("Deterministic demo — no API keys, no cost.");
    expect(renderWith(null)).not.toContain("Deterministic demo — no API keys, no cost.");
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

describe("/new — DEF-096 / DEF-097 (Phase 21B) truthful surfaces for probe-down and DTO-unavailable states", () => {
  const renderWithCaps = (capabilities: GenerationCapabilitiesResponse | null): string =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/new"]}>
        <NewCasePage capabilities={capabilities} />
      </MemoryRouter>,
    );

  it("DEF-096 — a probe-FAILED ollama backend (configuredProvider 'ollama', demo+local unavailable) shows NO deterministic story ANYWHERE on /new", () => {
    const markup = renderWithCaps({
      configuredProvider: "ollama",
      modes: [
        { id: "demo", available: false },
        { id: "local", available: false, label: "Local AI", model: "llama3.2:3b" },
      ],
    });
    // CTA: renamed, never the deterministic promise.
    expect(markup).toContain("Try an example case");
    expect(markup).not.toContain("Try Demo Case");
    const demoMarkup = markup.match(/data-testid="try-demo-note"[\s\S]*?<\/p>/)?.[0] ?? "";
    expect(demoMarkup).toContain("Not the free deterministic demo");
    expect(demoMarkup).not.toContain("Deterministic demo — no API keys, no cost.");
    // F-03 mode line: truthful per-mode with the availability tag.
    expect(markup).toContain("Generation mode: Local AI — llama3.2:3b — Unavailable");
    expect(markup).not.toContain("Generation mode: Deterministic demo");
    expect(markup).not.toContain("Demo mode active");
    // Qualifier keeps the truthful local-configuration copy.
    expect(markup).toContain("Local AI is available");
    expect(markup).not.toContain("Demo build: deterministic built-in generator");
  });

  it("DEF-097 — DTO-unavailable payload (empty allowlist) shows the neutral qualifier + note + mode line on /new", () => {
    const markup = renderWithCaps({ modes: [] });
    expect(markup).toContain("Generation is available once the service is reachable.");
    expect(markup).toContain(
      "runs through the backend-configured generation pipeline once the service is reachable",
    );
    expect(markup).toContain("Generation mode: Available once the service is reachable.");
    expect(markup).not.toContain("Generation mode: Deterministic demo");
    expect(markup).not.toContain("Demo mode active");
    expect(markup).not.toContain('data-testid="generation-mode-demo-notice"');
    expect(markup).not.toContain("uses the built-in deterministic generator in this demo build");
  });
});