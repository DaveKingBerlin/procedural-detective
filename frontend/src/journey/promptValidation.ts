import { EXAMPLE_PROMPT, PROMPT_MAX_CHARS } from "./demoPrompt";

/**
 * Pure validation for the prompt-to-case form (Phase 8 B).
 *
 * The prompt must be non-empty and within the safe bounded limit. This is a
 * pure function so the /new form's validation is unit-testable without a DOM.
 */
export interface PromptValidation {
  ok: boolean;
  /** Trimmed prompt when ok — the value the journey actually sends. */
  trimmed: string | null;
  /** Player-safe error message when not ok. */
  error: string | null;
}

export function validatePrompt(text: string): PromptValidation {
  const trimmed = typeof text === "string" ? text.trim() : "";
  if (trimmed === "") {
    return {
      ok: false,
      trimmed: null,
      error: "Describe a crime first — a sentence or two is enough.",
    };
  }
  if (trimmed.length > PROMPT_MAX_CHARS) {
    return { ok: false, trimmed: null, error: `Keep the prompt under ${PROMPT_MAX_CHARS} characters.` };
  }
  return { ok: true, trimmed, error: null };
}

/**
 * The "Use example prompt" action fills the textarea with the REQUIREMENTS 48
 * Case A example — the same input a human would type. Pure helper so tests
 * cover the fill target without a DOM.
 */
export function examplePromptText(): string {
  return EXAMPLE_PROMPT;
}