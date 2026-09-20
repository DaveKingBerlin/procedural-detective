/**
 * Phase 17D Bugfix PART B — selectable example prompts ("Try an example:").
 *
 * Three player-facing prompt examples demonstrating increasing Prompt-to-World
 * complexity (Easy / Medium / Hard). This module is PURE and DETERMINISTIC:
 * it carries ONLY frozen player-facing copy (button label, one-line helper,
 * exact prompt text) plus the tiny state transitions the /new form needs.
 *
 * Hard guarantees:
 *  - the three prompts are pinned VERBATIM below (byte-exact) and are
 *    deliberately just the text a human would type into the textarea — no
 *    solution logic, no reveal, nothing depends on the content beyond
 *    pre-filling the input; the backend runs them through the exact same
 *    normal Prompt-to-World pipeline as any user-written prompt (NO special
 *    case generation is defined or possible here);
 *  - NO internal implementation detail appears in any player-facing string:
 *    no proc.* asset ids, no AssetSpec, no Phase 13/17 numbers, no solver
 *    internals. Those terms live only in code comments / developer notes;
 *  - EXAMPLE_PROMPTS is frozen recursively so a buggy mutation can never
 *    corrupt the pinned copy;
 *  - selectExamplePrompt / noteExamplePromptEdit are pure functions so the
 *    active-state semantics (selecting an example marks it active; any edit
 *    that diverges from the loaded example clears the active mark; selecting
 *    the same example again is idempotent) are unit-testable without a DOM.
 */

/** One selectable example prompt entry (ALL player-facing copy). */
export interface ExamplePromptEntry {
  /** Short button label shown in the "Try an example:" row. */
  readonly label: "Easy" | "Medium" | "Hard";
  /** One-line helper shown under the label. */
  readonly helper: string;
  /** The EXACT prompt text populated into the textarea (verbatim, multi-line). */
  readonly prompt: string;
}

/** The three frozen example ids (also the button data-testid suffixes). */
export type ExamplePromptId = "easy" | "medium" | "hard";

/** Ordered ids, in the order the "Try an example:" row offers them. */
export const EXAMPLE_PROMPT_IDS: readonly ExamplePromptId[] = Object.freeze([
  "easy",
  "medium",
  "hard",
]);

const EASY_PROMPT = [
  "Victim: Laura Stein",
  "Murderer: Daniel Roth",
  "Motive: financial gain",
  "Weapon: kitchen knife",
  "Time: 20:15",
  "Witness: Nina Weber",
  "Location: apartment",
].join("\n");

const MEDIUM_PROMPT = [
  "Victim: Michael Hartmann",
  "Murderer: Elena Fischer",
  "Motive: blackmail over a hidden affair",
  "Weapon: antique brass letter opener",
  "Time: 21:18",
  "Witness: Daniel Weber",
  "Location: hotel suite",
].join("\n");

const HARD_PROMPT = [
  "Victim: Dr. Anna Weiss",
  "Murderer: Paul Becker",
  "Motive: stolen research data",
  "Weapon: bronze ceremonial ice pick",
  "Time: 23:42",
  "Witness: Lisa König",
  "Location: office",
].join("\n");

/**
 * The frozen example registry. Each prompt is the exact player input a human
 * could type — nothing more. Helpers match the Phase 17D Bugfix PART B copy
 * verbatim (Easy: known environment/common objects; Medium: more varied
 * setting/evidence; Hard: unusual object that may require procedural 3D
 * generation).
 */
export const EXAMPLE_PROMPTS: Readonly<Record<ExamplePromptId, ExamplePromptEntry>> =
  Object.freeze({
    easy: Object.freeze({
      label: "Easy",
      helper: "Known environment and common objects.",
      prompt: EASY_PROMPT,
    }),
    medium: Object.freeze({
      label: "Medium",
      helper: "More varied setting and evidence.",
      prompt: MEDIUM_PROMPT,
    }),
    hard: Object.freeze({
      label: "Hard",
      helper: "Includes an unusual object that may require procedural 3D generation.",
      prompt: HARD_PROMPT,
    }),
  });

/** True ONLY for the three frozen example ids (unknown values stay false). */
export function isKnownExample(id: unknown): id is ExamplePromptId {
  return (
    typeof id === "string" &&
    (id === "easy" || id === "medium" || id === "hard")
  );
}

/**
 * Exact prompt text for a frozen example id, or null for any non-example id.
 * Pure lookup — returns the same pinned string object every time.
 */
export function examplePromptFor(id: unknown): string | null {
  if (!isKnownExample(id)) return null;
  return EXAMPLE_PROMPTS[id].prompt;
}

/** The derived prompt-state tracked by the /new form for example selection. */
export interface ExamplePromptState {
  /** Text currently in the prompt textarea. */
  readonly prompt: string;
  /**
   * Id of the example currently loaded in the textarea, or null once the
   * text has been edited away from the loaded example (the user is then
   * writing their own prompt).
   */
  readonly activeExampleId: ExamplePromptId | null;
}

/** Initial prompt state: empty textarea, no example loaded. */
export const EMPTY_EXAMPLE_PROMPT_STATE: ExamplePromptState = Object.freeze({
  prompt: "",
  activeExampleId: null,
});

/**
 * Pure selection transition: replace the textarea text with the EXACT example
 * prompt and mark that example as the loaded one. Selecting the example that
 * is already loaded (same text, same active id) is IDEMPOTENT — the very same
 * state object is returned. Unknown ids are ignored (state returned unchanged).
 */
export function selectExamplePrompt(
  state: ExamplePromptState,
  id: unknown,
): ExamplePromptState {
  if (!isKnownExample(id)) return state;
  const prompt = EXAMPLE_PROMPTS[id].prompt;
  if (state.prompt === prompt && state.activeExampleId === id) return state;
  return { prompt, activeExampleId: id };
}

/**
 * Pure edit transition: the user typed/edited the textarea. The edited text
 * is always kept (the prompt stays fully editable); the active example mark
 * survives ONLY while the text still equals the loaded example verbatim —
 * the first diverging keystroke clears it deterministically, and no amount of
 * manual typing ever reactivates an example (only clicking its button does).
 */
export function noteExamplePromptEdit(
  state: ExamplePromptState,
  text: string,
): ExamplePromptState {
  const active = state.activeExampleId;
  if (active !== null && text === EXAMPLE_PROMPTS[active].prompt) {
    return { prompt: text, activeExampleId: active };
  }
  return { prompt: text, activeExampleId: null };
}