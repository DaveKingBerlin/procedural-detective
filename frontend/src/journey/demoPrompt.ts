/**
 * Example USER INPUT (REQUIREMENTS 3.1 / 48 Case A) used by the demo
 * journey's "Use example prompt" action.
 *
 * This is deliberately ONLY a prompt string — the same text a human would
 * type into the textarea. It is NOT solution logic, NOT a reveal, and NO part
 * of any UI logic depends on its content beyond pre-filling the input. The
 * game's truth comes from the backend generated case, never from this module.
 */
export const EXAMPLE_PROMPT: string = [
  "Victim: Sarah Miller",
  "Murderer: Thomas Reed",
  "Motive: €240,000 embezzlement",
  "Weapon: Kitchen knife",
  "Time: 22:17",
  "Witness: Emily Reed",
].join("\n");

/** Hard prompt length bound (the textarea maxLength is the same value). */
export const PROMPT_MAX_CHARS = 4000;