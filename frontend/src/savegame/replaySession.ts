import { getPlaythroughId } from "../api/playthroughToken";
import { serializeSavegameV1 } from "./exportV1";
import { FreshReplayState } from "./replayRuntime";
import type { SavedCaseDefinition, SavegameV1Document } from "./savegameV1";
import { SAVEGAME_FORMAT, SAVEGAME_FORMAT_VERSION } from "./savegameV1";

/**
 * Phase 32 — the ACTIVE saved-replay session (browser-LOCAL source mode).
 *
 * Module-level IN-MEMORY holder following the `journey/context.ts` pattern:
 * a successfully loaded `.pdcase` starts a fresh replay whose
 * `FreshReplayState` is shared by the three player routes (scene / accuse /
 * reveal) so discovered knowledge, the submitted accusation and the local
 * reveal evaluation survive route navigation.
 *
 * Hard guarantees:
 *  - nothing here is ever persisted to localStorage/sessionStorage/IndexedDB
 *    (Phase32 §24) — a refresh returns to the main page and requires
 *    reloading the file;
 *  - a live server playthrough's credentials in localStorage are NEVER
 *    touched or cleared by loading/leaving a replay (§5 — separate in-memory
 *    session);
 *  - the imported `sourceCaseId` is display-only and never used for any
 *    lookup/authority (Phase32 §22).
 *
 * Player-authored notebook pins / witness-statement cache (Phase 18C / 23)
 * ARE persisted by the player routes via `replayOrLivePlaythroughId()`. While
 * a replay is active that function returns a PER-FILE namespace derived from
 * a short stable hash of the imported case content (DEF-045 / ADV-32F-04), so
 * file B's notes never leak into file A, live `PT-*` namespaces are never
 * touched, and re-loading the SAME file reuses its own pins. The derived key
 * is a one-way hash — the imported truth/case content is NEVER written to
 * storage, only the namespace token.
 */

export interface ActiveReplay {
  readonly isReplay: true;
  readonly definition: SavedCaseDefinition;
  readonly state: FreshReplayState;
}

let active: ActiveReplay | null = null;

/** Begin a fresh replay of a validated normalized definition. */
export function startReplay(definition: SavedCaseDefinition): ActiveReplay {
  active = Object.freeze({
    isReplay: true,
    definition,
    state: new FreshReplayState(definition),
  }) as ActiveReplay;
  return active;
}

/** The active saved replay, or null when the player is in a live session. */
export function activeReplay(): ActiveReplay | null {
  return active;
}

/** Leave the replay (return to the main menu). Never touches live credentials. */
export function clearReplay(): void {
  active = null;
}

// --------------------------------------------------------------------------- //
// per-file replay storage namespace (DEF-045 / ADV-32F-04)
// --------------------------------------------------------------------------- //

/** FNV-1a 32-bit over an independent second offset basis — combined into a
 *  short, stable, alphanumeric base-36 token (12 chars ≈ 62 bits of entropy).
 *  Deterministic across loads and browsers; never uses the loaded file's
 *  `exportedAt` (a re-export changes only that field and MUST reuse the same
 *  pins). */
function stableReplayHash(text: string): string {
  let hashA = 0x811c9dc5;
  let hashB = 0x01b88557;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    hashA ^= code;
    hashA = (hashA * 0x01000193) >>> 0;
    hashB ^= code;
    hashB = (hashB * 0x01000193) >>> 0;
  }
  const first = hashA.toString(36).padStart(7, "0");
  const second = hashB.toString(36).padStart(5, "0");
  return `${first}${second.slice(0, 5)}`;
}

/**
 * The per-file replay note namespace for ONE normalized case definition.
 *
 * Derived ONLY from the immutable PUBLIC case content (the normalized
 * `case` payload: metadata WITHOUT the display-only `sourceCaseId`,
 * publicCase, scene, candidates, witnesses, evidence) via a one-way hash.
 * The ReplayTruthV1 solution block is DELIBERATELY EXCLUDED — the
 * fingerprint must NEVER depend on the replay truth/key (DEF-045 /
 * ADV-32F-04), and no raw imported id or control char ever reaches the key
 * verbatim (only the hashed token does). Deterministic per file:
 * re-loading the same file (or a re-export with a fresh `exportedAt`)
 * yields the SAME namespace and therefore reuses its player-authored pins;
 * two files that differ ONLY in their truth stay in the SAME namespace.
 */
export function replayStorageNamespace(definition: SavedCaseDefinition): string {
  const identity = {
    format: SAVEGAME_FORMAT,
    formatVersion: SAVEGAME_FORMAT_VERSION,
    case: {
      metadata: {
        title: definition.metadata.title,
        difficulty: definition.metadata.difficulty,
        source: definition.metadata.source,
      },
      publicCase: definition.publicCase,
      scene: definition.scene,
      candidates: definition.candidates,
      witnesses: definition.witnesses,
      evidence: definition.evidence,
    },
  };
  return `saved-replay-${stableReplayHash(serializeSavegameV1(identity as unknown as SavegameV1Document))}`;
}

/**
 * The playthrough id the player routes should use for cosmetic, in-memory,
 * per-session namespacing (notebook pins, witness-statement cache): the
 * PER-FILE replay namespace while a saved replay is active, else the live
 * stored credential. A replay must NEVER spread its player notes into a live
 * playthrough's localStorage namespace (and vice versa), and different replay
 * FILES must never share one namespace (DEF-045 / ADV-32F-04).
 */
export function replayOrLivePlaythroughId(): string {
  if (active !== null) return replayStorageNamespace(active.definition);
  return getPlaythroughId() ?? "";
}