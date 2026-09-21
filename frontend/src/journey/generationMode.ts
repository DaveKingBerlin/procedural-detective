import type {
  GenerationCapabilitiesResponse,
  GenerationModeDTO,
  GenerationModeId,
} from "../api/types";

/**
 * Phase 16 Track B — generation-mode capabilities (parse / select / persist).
 *
 * The backend publishes the player-safe allowlist DTO GET
 * /api/v1/generation-capabilities: which generation modes are configured AND
 * available. This module is the ONLY place that:
 *
 *   - re-parses that trust-boundary reply into the frozen mode ids (unknown
 *     fields and mode ids are dropped — a hostile/buggy reply can never inject
 *     arbitrary options);
 *   - decides which modes the selector offers (Demo is ALWAYS offered; Local
 *     AI / Cloud AI only when the backend reports `available: true`, so an
 *     unavailable mode is NEVER rendered as an option);
 *   - persists the player-selected mode under the contract key `pd_generation_mode`
 *     (injectable storage keeps the module pure and unit-testable without a DOM).
 *
 * Hard guarantees:
 *   - NO host/IP, credentials, prompts, URLs or diagnostics ever leave this
 *     module: the only strings it produces are the frozen public labels
 *     ("Demo", "Local AI", "Cloud AI"), the DTO-provided public display model
 *     name (verbatim), the "Ready"/"Unavailable" tag derived strictly from
 *     `available`, and the frozen §36 showcase sentence (app copy);
 *   - availability is derived ONLY from the DTO `available` boolean
 *     (isLocalModeAvailable), so a stored/selected local mode with an
 *     unavailable backend surfaces the honest unavailable state — never a
 *     silent demo fallback or a pretend-local claim;
 *   - a stale/tampered stored mode id never drives the journey: reads return
 *     only the frozen ids, otherwise null.
 */

/** Contract localStorage key for the player-selected generation mode. */
export const GENERATION_MODE_STORAGE_KEY = "pd_generation_mode";

/** The three frozen mode ids, in the order the selector offers them. */
const MODE_IDS: readonly GenerationModeId[] = ["demo", "local", "live"];

/**
 * DEF-068-parity guard used for public display text all across the client:
 * a DTO-provided display string containing ANY of these tokens can never be
 * rendered (they would smuggle URLs/data URIs/host hints into the UI).
 */
const FORBIDDEN_URL_TOKENS: readonly string[] = [
  "http://",
  "https://",
  "data:",
  "file:",
  "javascript:",
];

/**
 * ADV-208 — any IPv4 dotted literal (covers private/link-local/loopback
 * RFC1918-range addresses alike). A model name such as `llama3@<private-host>`
 * or a label like `net <private-ip>` must never reach the DOM/tooltip.
 */
const IPV4_DOTTED_LITERAL = /\d{1,3}(?:\.\d{1,3}){3}/;

/**
 * True when a DTO-provided display string must never be rendered:
 *  - the classic URL/data/file/javascript scheme tokens (DEF-068 parity);
 *  - ADV-208: any IPv4 dotted literal, any `@`-joined host token and any raw
 *    HTML angle bracket (`<`/`>`) — so a model/label string that reaches the
 *    DOM/tooltip can never carry a host/IP or raw markup.
 * The rule is deliberately conservative: a legitimate model/label never
 * needs `@`, a dotted IP or a raw angle bracket, so dropping is safe and
 * deterministic (labels fall back to the frozen public copy, models to null).
 */
function isUnsafeDisplayString(value: string): boolean {
  const lowered = value.toLowerCase();
  if (FORBIDDEN_URL_TOKENS.some((token) => lowered.includes(token))) return true;
  if (IPV4_DOTTED_LITERAL.test(value)) return true;
  if (value.includes("@") || value.includes("<") || value.includes(">")) return true;
  return false;
}

/** Minimal storage surface used here (localStorage-compatible). */
export interface GenerationModeStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function defaultStorage(): GenerationModeStorage | null {
  try {
    if (typeof window !== "undefined" && typeof window.localStorage !== "undefined") {
      return window.localStorage;
    }
  } catch {
    // localStorage can throw in hardened/embedded contexts — treat as absent.
  }
  return null;
}

function writeMode(
  mode: GenerationModeId,
  storage: GenerationModeStorage | null | undefined,
): boolean {
  const store = storage ?? defaultStorage();
  if (!store) return false;
  try {
    store.setItem(GENERATION_MODE_STORAGE_KEY, mode);
    return true;
  } catch {
    return false; // Best-effort: a storage failure must never crash the page.
  }
}

/** Persist the selected mode; returns false when no storage is available. */
export function setGenerationMode(
  mode: GenerationModeId,
  storage?: GenerationModeStorage | null,
): boolean {
  return writeMode(mode, storage);
}

/**
 * Read the stored mode id. ONLY the frozen ids are ever returned: a
 * tampered/out-of-contract value (or one for a mode the backend no longer
 * reports) resolves to null, so the journey can never be driven by a stale id.
 */
export function getGenerationMode(storage?: GenerationModeStorage | null): GenerationModeId | null {
  const store = storage ?? defaultStorage();
  if (!store) return null;
  let raw: string | null = null;
  try {
    raw = store.getItem(GENERATION_MODE_STORAGE_KEY);
  } catch {
    return null;
  }
  return typeof raw === "string" && MODE_IDS.includes(raw as GenerationModeId)
    ? (raw as GenerationModeId)
    : null;
}

/** Clear the stored mode (used by reset flows). Best-effort, never throws. */
export function clearGenerationMode(storage?: GenerationModeStorage | null): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.removeItem(GENERATION_MODE_STORAGE_KEY);
  } catch {
    // Best-effort: never crash the page.
  }
}

/** Safe payload used when the endpoint is unknown/unreachable: demo-only. */
export const DEMO_ONLY_CAPABILITIES: GenerationCapabilitiesResponse = Object.freeze({
  modes: [],
});

/**
 * Re-parse the trust-boundary payload into the typed contract. Only the three
 * frozen ids survive; unknown mode objects/fields are dropped; `available` is
 * a strict boolean (anything else reads as false — never favours an option).
 * ADV-208: duplicate mode ids are DEDUPED with ONE deterministic rule (the
 * FIRST occurrence wins; later duplicates are ignored), so every consumer
 * (`effectiveProviderMode`, `selectableGenerationModes`, `isLocalModeAvailable`,
 * ...) sees the identical mode set from the same parsed payload.
 * Never throws: a malformed reply resolves to an empty allowlist.
 */
export function parseGenerationCapabilities(raw: unknown): GenerationCapabilitiesResponse {
  const modes: GenerationModeDTO[] = [];
  if (typeof raw !== "object" || raw === null) return { modes };
  const list = (raw as { modes?: unknown }).modes;
  if (!Array.isArray(list)) return { modes };
  const seen = new Set<GenerationModeId>();
  for (const entry of list) {
    if (typeof entry !== "object" || entry === null) continue;
    const id = (entry as { id?: unknown }).id;
    if (typeof id !== "string" || !MODE_IDS.includes(id as GenerationModeId)) continue;
    if (seen.has(id as GenerationModeId)) continue; // ADV-208: first occurrence wins
    seen.add(id as GenerationModeId);
    const availableRaw = (entry as { available?: unknown }).available;
    const dto: GenerationModeDTO = {
      id,
      available: typeof availableRaw === "boolean" ? availableRaw : false,
    };
    const label = (entry as { label?: unknown }).label;
    if (typeof label === "string" && label !== "" && !isUnsafeDisplayString(label)) {
      dto.label = label;
    }
    const model = (entry as { model?: unknown }).model;
    if (typeof model === "string" && model !== "" && !isUnsafeDisplayString(model)) {
      dto.model = model;
    }
    modes.push(dto);
  }
  return { modes };
}

/**
 * The explicit availability tag shown inside the Local option. Derived ONLY
 * from the DTO's `available` boolean — never from diagnostics, probes or any
 * other signal.
 */
export function availabilityTag(available: boolean): "Ready" | "Unavailable" {
  return available ? "Ready" : "Unavailable";
}

/**
 * Phase 16.2 §36 — the Local-AI showcase sentence shown on /new ONLY while
 * Local AI mode is the ACTIVE generation mode (selected AND backend-reported
 * available). This is the accurate statement: the local model PROPOSES
 * structured data; the deterministic engine verifies and constructs. It never
 * claims the model proves the case, executes scene code or generates
 * arbitrary 3D — and it is app-authored static copy, so no DTO string can
 * ever reach it.
 */
export const LOCAL_AI_SHOWCASE_NOTE =
  "Procedural Detective can run its generative Prompt-to-World pipeline with a local Llama 3.2 model: "
  + "the model proposes structured data, and deterministic validators verify and construct "
  + "the playable investigation.";

/**
 * True ONLY when the parsed allowlist carries the Local AI mode marked
 * available. Used by /new to keep the display honest (Phase 16.2 §20): a
 * stored/selected `local` mode combined with an unavailable/absent/down
 * backend must surface the explicit "Local AI is unavailable" note — never a
 * silent demo fallback and never a claim that Local AI is active.
 */
export function isLocalModeAvailable(capabilities: GenerationCapabilitiesResponse | null): boolean {
  const entry = capabilities?.modes.find((mode) => mode.id === "local");
  return entry?.available === true;
}

/**
 * ADV-212 — the ONLY journey-mode value that may drive the Local-AI label
 * sequence on /generating. The stored `pd_generation_mode` (localStorage) is
 * NOT trusted by itself: the `local` claim survives ONLY when the LIVE
 * capability DTO confirms the local pipeline is actually available
 * (first-wins-parsed — see {@link parseGenerationCapabilities}). Any other
 * stored value (demo/live/unset) and every unavailable/unknown/local-less
 * capability set resolves to the mode-independent value, so the generic/demo
 * label sequence is shown. A capabilities fetch failure surfaces as
 * demo-only/`null` here, so the journey can never claim a local pipeline the
 * backend has not confirmed.
 */
export function validatedJourneyMode(
  storedMode: GenerationModeId | null,
  capabilities: GenerationCapabilitiesResponse | null,
): GenerationModeId | null {
  if (storedMode !== "local") return storedMode; // demo/live/unset keep generic labels
  return isLocalModeAvailable(capabilities) ? "local" : null;
}

/** One mode the selector may actually offer (never an unavailable local/live). */
export interface SelectableGenerationMode {
  id: GenerationModeId;
  /** Public display label — DTO verbatim when the backend supplies one. */
  label: string;
  /** Public model display name (local mode only) or null. */
  model: string | null;
  /** Honest availability, derived ONLY from the DTO `available`. */
  ready: boolean;
}

/**
 * Resolve the offered modes from the parsed capabilities:
 *   - Demo is ALWAYS offered (a missing or unavailable demo entry falls back
 *     to the built-in demo option — the selector must never be Demo-less);
 *   - Local AI appears ONLY when the backend reports it available;
 *   - Cloud AI appears ONLY when the backend reports it available.
 * When the resolved list is demo-only the UI renders the static
 * "Demo mode active" notice instead of a selector.
 */
export function selectableGenerationModes(
  capabilities: GenerationCapabilitiesResponse | null,
): SelectableGenerationMode[] {
  const found = new Map<GenerationModeId, GenerationModeDTO>();
  for (const mode of capabilities?.modes ?? []) {
    if (MODE_IDS.includes(mode.id as GenerationModeId) && !found.has(mode.id as GenerationModeId)) {
      // ADV-208: first occurrence wins here too — even a hand-constructed
      // (non-parse) capabilities object can never split the consumers.
      found.set(mode.id as GenerationModeId, mode);
    }
  }
  const demo = found.get("demo");
  const list: SelectableGenerationMode[] = [
    {
      id: "demo",
      label: demo?.label && !isUnsafeDisplayString(demo.label) ? demo.label : "Demo",
      model: null,
      // The deterministic provider backs every journey when no other mode is
      // available, so the demo offer is always honest.
      ready: true,
    },
  ];
  for (const id of ["local", "live"] as const) {
    const mode = found.get(id);
    if (!mode || !mode.available) continue; // unavailable modes are never offered
    list.push({
      id,
      label:
        mode.label && !isUnsafeDisplayString(mode.label)
          ? mode.label
          : id === "local"
            ? "Local AI"
            : "Cloud AI",
      // The display model name is copied verbatim ONLY when it carries no
      // unsafe token (parse already guarantees this; kept here so even
      // a hand-constructed capabilities object cannot smuggle a URL through).
      model:
        mode.model && !isUnsafeDisplayString(mode.model)
          ? mode.model
          : null,
      ready: true,
    });
  }
  return list;
}

/**
 * The option label rendered for one offered mode. Demo is a plain label; the
 * Local option carries the honest label + the DTO display model name (verbatim)
 * + the explicit Ready/Unavailable tag; Cloud AI uses its DTO label.
 */
export function generationModeOptionLabel(mode: SelectableGenerationMode): string {
  if (mode.id === "demo") return mode.label;
  if (mode.id === "local") {
    const tag = availabilityTag(mode.ready);
    // Last-line guard: never render a token that would smuggle a URL/IP/host
    // or raw markup into the DOM.
    const model = !isUnsafeDisplayString(mode.model ?? "") ? mode.model : null;
    const label = !isUnsafeDisplayString(mode.label) ? mode.label : "Local AI";
    return model ? `${label} — ${model} — ${tag}` : `${label} — ${tag}`;
  }
  return !isUnsafeDisplayString(mode.label) ? mode.label : "Cloud AI";
}