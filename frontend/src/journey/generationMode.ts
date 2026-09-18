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
 *     name (verbatim) and the "Ready"/"Unavailable" tag derived strictly from
 *     `available`;
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

/** True when a DTO-provided display string contains a forbidden URL token. */
function containsForbiddenUrlToken(value: string): boolean {
  const lowered = value.toLowerCase();
  return FORBIDDEN_URL_TOKENS.some((token) => lowered.includes(token));
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
 * Never throws: a malformed reply resolves to an empty allowlist.
 */
export function parseGenerationCapabilities(raw: unknown): GenerationCapabilitiesResponse {
  const modes: GenerationModeDTO[] = [];
  if (typeof raw !== "object" || raw === null) return { modes };
  const list = (raw as { modes?: unknown }).modes;
  if (!Array.isArray(list)) return { modes };
  for (const entry of list) {
    if (typeof entry !== "object" || entry === null) continue;
    const id = (entry as { id?: unknown }).id;
    if (typeof id !== "string" || !MODE_IDS.includes(id as GenerationModeId)) continue;
    const availableRaw = (entry as { available?: unknown }).available;
    const dto: GenerationModeDTO = {
      id,
      available: typeof availableRaw === "boolean" ? availableRaw : false,
    };
    const label = (entry as { label?: unknown }).label;
    if (typeof label === "string" && label !== "" && !containsForbiddenUrlToken(label)) {
      dto.label = label;
    }
    const model = (entry as { model?: unknown }).model;
    if (typeof model === "string" && model !== "" && !containsForbiddenUrlToken(model)) {
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
    if (MODE_IDS.includes(mode.id as GenerationModeId)) {
      found.set(mode.id as GenerationModeId, mode);
    }
  }
  const demo = found.get("demo");
  const list: SelectableGenerationMode[] = [
    {
      id: "demo",
      label: demo?.label && !containsForbiddenUrlToken(demo.label) ? demo.label : "Demo",
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
        mode.label && !containsForbiddenUrlToken(mode.label)
          ? mode.label
          : id === "local"
            ? "Local AI"
            : "Cloud AI",
      // The display model name is copied verbatim ONLY when it carries no
      // forbidden URL token (parse already guarantees this; kept here so even
      // a hand-constructed capabilities object cannot smuggle a URL through).
      model:
        mode.model && !containsForbiddenUrlToken(mode.model)
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
    // Last-line guard: never render a token that would smuggle a URL/data URI.
    const model = !containsForbiddenUrlToken(mode.model ?? "") ? mode.model : null;
    const label = !containsForbiddenUrlToken(mode.label) ? mode.label : "Local AI";
    return model ? `${label} — ${model} — ${tag}` : `${label} — ${tag}`;
  }
  return !containsForbiddenUrlToken(mode.label) ? mode.label : "Cloud AI";
}