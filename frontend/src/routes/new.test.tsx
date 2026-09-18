import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { APP_PROVIDER_MODE, providerQualifier } from "../journey/providerMode";
import { examplePromptText, validatePrompt } from "../journey/promptValidation";
import NewCasePage from "./new";

/**
 * Prompt-to-case screen (Phase 8 B): form presence + data-testids, default
 * difficulty, the verbatim REQUIREMENTS 48 Case A example prompt (the
 * "Use example prompt" fill target), and the pure prompt validation.
 */

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