import {
  SavegameParseError,
  savegameErrorMessage,
  parseSavegameV1,
} from "./savegameV1";
import { MAX_EXPORT_BYTES } from "./savegameV1";
import type { SavedCaseDefinition } from "./savegameV1";

/**
 * Phase 32 — the browser-side `Load Case` file intake (Phase32 §11 / §26).
 *
 * A minimal `File`-shaped dependency keeps the module DOM-free and
 * unit-testable. The flow mirrors the spec exactly:
 *
 *   open native picker -> file-size precheck -> JSON parse -> strict
 *   SavegameV1 validation -> normalization -> SavedCaseDefinition.
 *
 * Errors map to the FROZEN bounded messages of Phase32 §26; a picker cancel
 * (no file selected) is not an error and never calls this module.
 */

export interface LoadCaseFile {
  /** The picked file's size in bytes (the raw transport bound). */
  size: number;
  /** Reads the whole file as UTF-8 text. */
  text(): Promise<string>;
}

export type LoadCaseOutcome =
  | { ok: true; definition: SavedCaseDefinition }
  | { ok: false; message: string };

/** Read + strictly validate one picked `.pdcase` file. Never throws. */
export async function loadSavegameFile(file: LoadCaseFile): Promise<LoadCaseOutcome> {
  if (file.size > MAX_EXPORT_BYTES) {
    return { ok: false, message: savegameErrorMessage("too-large") };
  }
  let text: string;
  try {
    text = await file.text();
  } catch {
    return { ok: false, message: savegameErrorMessage("unreadable") };
  }
  try {
    const definition = parseSavegameV1(text, file.size);
    return { ok: true, definition };
  } catch (error) {
    if (error instanceof SavegameParseError) {
      return { ok: false, message: savegameErrorMessage(error.kind) };
    }
    return { ok: false, message: savegameErrorMessage("unreadable") };
  }
}