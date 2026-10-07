/**
 * Phase 32 — frozen save/load UI copy.
 *
 * The exact player-facing strings of Phase32 §25 (the Save Case prompt) and
 * §7 (the honest spoiler note). Kept in ONE module so the round-trip tests
 * and the routes share the same literals (no drift, no raw internals).
 */

/** §25 — the prompt title. */
export const SAVE_CASE_PROMPT_TITLE = "Save this case?";

/** §25 — the prompt body (only shown AFTER THE TRUTH was legitimately
 *  revealed; the download is never forced). */
export const SAVE_CASE_PROMPT_BODY =
  "Save a copy so you can play this exact investigation again later or share it with someone else.";

/** §7 — the required honest spoiler limitation. The solution is inside the
 *  portable file so the case can be replayed without regeneration; the UI
 *  hides it during play, but no fake Base64/obfuscation secrecy is claimed. */
export const REVEAL_SPOILER_NOTE =
  "The savegame contains the complete case, including its solution, so it can be played again without regeneration. Avoid opening the file manually if you do not want spoilers.";

/** §26 — the bounded load-error copy (the ONLY load-failure strings ever
 *  shown; no raw parser/schema text, no stack, no filename). */
export const LOAD_ERROR_TOO_LARGE = "This savegame is too large to load.";
export const LOAD_ERROR_UNREADABLE = "The selected savegame could not be read.";
export const LOAD_ERROR_INVALID = "This file is not a valid Procedural Detective savegame.";
export const LOAD_ERROR_NEWER_VERSION =
  "This savegame was created by a newer incompatible version.";