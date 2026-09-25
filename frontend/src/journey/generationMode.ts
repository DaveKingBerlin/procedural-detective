import type {
  ConfiguredProvider,
  GenerationCapabilitiesResponse,
  GenerationModeDTO,
  GenerationModeId,
  RemoteLocalAiDTO,
} from "../api/types";
import { effectiveProviderMode, providerIsReported } from "./providerMode";

/**
 * Phase 16 Track B — generation-mode capabilities (parse / display).
 *
 * The backend publishes the player-safe allowlist DTO GET
 * /api/v1/generation-capabilities: which generation modes are configured AND
 * available. This module is the ONLY place that:
 *
 *   - re-parses that trust-boundary reply into the frozen mode ids (unknown
 *     fields and mode ids are dropped — a hostile/buggy reply can never inject
 *     arbitrary options);
 *   - resolves the single backend-authoritative display story for the
 *     READ-ONLY "Generation mode" line (Phase 21 F-03 — see
 *     {@link generationModeLine}: the backend runs ONE process-global
 *     provider, so the UI shows its reported mode and never offers a switch);
 *   - reads the legacy `pd_generation_mode` contract key (getGenerationMode)
 *     for legacy-safe leftover values only.
 *
 * Phase 21 F-03 — STORAGE DISPOSITION: the interactive provider selector was
 * REMOVED (see src/journey/generationModeSelector.tsx) because the selected
 * mode was never sent to the backend (the backend provider is process-global,
 * GENERATION_PROVIDER). NO user action writes `pd_generation_mode` anymore:
 * the landing and /new pages render the deterministic/backend-reported mode
 * as read-only information. The storage KEY is retained ONLY because:
 *   - /generating still reads it via getGenerationMode and re-validates it
 *     against the LIVE capability DTO (ADV-212 / validatedJourneyMode) as
 *     defense-in-depth against stale values left by OLDER app versions;
 *   - the QA seam / unit tests / reset flows may still inject or clear it.
 * The setter (setGenerationMode) / clearer (clearGenerationMode) remain
 * exported as documented storage utilities but are called by NO src/ code.
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
 * Phase 21B (DEF-096/ADV-232) — the CLOSED enum of the backend-configured
 * operator generation provider the DTO may carry. The client re-sanitizes the
 * trust-boundary `configuredProvider` field to EXACTLY these three values and
 * drops everything else (unknown/malformed -> treated as UNKNOWN — NEVER as
 * "fake", so an older/hostile reply can never fabricate the deterministic
 * story).
 */
const CONFIGURED_PROVIDER_IDS: readonly ConfiguredProvider[] = [
  "fake",
  "ollama",
  "live",
];

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

/**
 * Persist a mode under `pd_generation_mode`.
 *
 * Phase 21 F-03 — THE UI NO LONGER WRITES THIS KEY: the interactive provider
 * selector was removed and no src/ code calls this function. It is retained
 * only as a documented, injectable-storage utility (reset flows / QA seam /
 * tests). Writing this key from a user-action path would re-introduce the
 * Phase 21 lie (a persisted "selection" that the backend never honours).
 */
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
 *
 * Phase 21 F-03 — legacy-safe read only: no user action writes this key
 * anymore; values found here can only come from OLDER app versions or the QA
 * seam, and /generating re-validates them against the LIVE capability DTO
 * (validatedJourneyMode, ADV-212) before any label is chosen.
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

/**
 * Clear the stored mode (used by reset flows). Best-effort, never throws.
 * Phase 21 F-03 — no user-action write path exists; kept as a documented
 * storage utility for reset flows / tests.
 */
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
 * Phase 22 — sanitize the trust-boundary `remoteLocalAi` block (BYO-Ollama
 * bridge) into the typed {@link RemoteLocalAiDTO}, or null when the block is
 * absent/garbled.
 *
 * Hard rules (same spirit as every other field on this boundary):
 *   - `available` / `connected` / `ready` are STRICT booleans — anything else
 *     reads false, so a hostile reply can never fabricate an offer or a
 *     connection claim;
 *   - `model` passes the module's last-line `safeDisplay` sanitizer: a value
 *     carrying a URL/data/file/javascript token, an IPv4 dotted literal, an
 *     `@`-joined host hint or a raw angle bracket is dropped to null (the
 *     panel then omits the Model line rather than render hostile text).
 * The block exists ONLY when ENABLE_BRIDGE=true (an OFF server omits the key
 * entirely → null → the feature is not offered). Never throws.
 */
export function parseRemoteLocalAi(raw: unknown): RemoteLocalAiDTO | null {
  if (typeof raw !== "object" || raw === null) return null;
  const block = raw as {
    available?: unknown;
    connected?: unknown;
    model?: unknown;
    ready?: unknown;
  };
  // An object that names NONE of the four known fields is not a bridge block
  // at all ({} / a totally foreign shape) — treat it as absent so a malformed
  // reply can never surface an empty-but-present offer.
  const hasAnyField =
    "available" in block || "connected" in block || "model" in block || "ready" in block;
  if (!hasAnyField) return null;
  const model = typeof block.model === "string" ? block.model : null;
  return {
    available: typeof block.available === "boolean" ? block.available : false,
    connected: typeof block.connected === "boolean" ? block.connected : false,
    model: model !== null && model !== "" && !isUnsafeDisplayString(model) ? model : null,
    ready: typeof block.ready === "boolean" ? block.ready : false,
  };
}

/**
 * Phase 22 — True ONLY when the capability DTO actually OFFERS the BYO-Ollama
 * bridge: the parsed `remoteLocalAi` block exists AND its `available` is the
 * strict boolean true. Absence (feature OFF / older server / malformed
 * payload) → false → the pairing/status panel renders nothing and the
 * landing//new pages stay byte-identical to Phase 21B.
 */
export function isRemoteLocalAiOffered(
  capabilities: GenerationCapabilitiesResponse | null,
): boolean {
  return remoteLocalAiBlock(capabilities) !== null;
}

/**
 * The parsed SANITIZED `remoteLocalAi` block when the feature is offered (see
 * {@link isRemoteLocalAiOffered}), else null.
 *
 * The block is re-parsed through {@link parseRemoteLocalAi} here — the same
 * last-line discipline {@link selectableGenerationModes} uses for labels: even
 * a HAND-CONSTRUCTED capabilities object (parser bypassed) is sanitized again
 * (strict booleans, hostile model dropped) before the panel can render it.
 */
export function remoteLocalAiBlock(
  capabilities: GenerationCapabilitiesResponse | null,
): RemoteLocalAiDTO | null {
  const block = parseRemoteLocalAi(capabilities?.remoteLocalAi);
  return block !== null && block.available === true ? block : null;
}

/**
 * Re-parse the trust-boundary payload into the typed contract. Only the three
 * frozen ids survive; unknown mode objects/fields are dropped; `available` is
 * a strict boolean (anything else reads as false — never favours an option).
 * ADV-208: duplicate mode ids are DEDUPED with ONE deterministic rule (the
 * FIRST occurrence wins; later duplicates are ignored), so every consumer
 * (`effectiveProviderMode`, `selectableGenerationModes`, `isLocalModeAvailable`,
 * ...) sees the identical mode set from the same parsed payload.
 *
 * Phase 21B (DEF-096/ADV-232): the new top-level `configuredProvider`
 * (backend-authoritative operator-config generator provider, closed enum
 * "fake" | "ollama" | "live") is re-sanitized to the closed enum as well — any
 * MISSING or unknown value is DROPPED so consumers treat it as UNKNOWN (never
 * as "fake", and never driving a deterministic claim). Unknown extra fields
 * are ignored as before.
 *
 * Phase 22: the top-level `remoteLocalAi` (BYO-Ollama bridge status block) is
 * re-sanitized through {@link parseRemoteLocalAi} — strict booleans + model
 * through `safeDisplay`. A MISSING/unknown block is OMITTED (feature not
 * offered); a hostile block can neither fabricate an offer nor a connection
 * claim.
 *
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
  const parsed: GenerationCapabilitiesResponse = { modes };
  // Phase 21B (DEF-096): the closed provider enum is re-sanitized here. An
  // absent/unknown/malformed value is OMITTED from the parsed result — every
  // consumer then reads it as UNKNOWN (backward compatible with older servers)
  // and NEVER as "fake" (a hostile reply cannot fabricate the deterministic
  // story from this field).
  const configuredRaw = (raw as { configuredProvider?: unknown }).configuredProvider;
  if (typeof configuredRaw === "string" && CONFIGURED_PROVIDER_IDS.includes(configuredRaw as ConfiguredProvider)) {
    parsed.configuredProvider = configuredRaw as ConfiguredProvider;
  }
  // Phase 22 — the BYO-Ollama bridge status block. Omitted when absent so an
  // ENABLE_BRIDGE=false server keeps a byte-identical response shape.
  const remoteLocalAi = parseRemoteLocalAi((raw as { remoteLocalAi?: unknown }).remoteLocalAi);
  if (remoteLocalAi !== null) {
    parsed.remoteLocalAi = remoteLocalAi;
  }
  return parsed;
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

/**
 * DTO-provided display field with the module's last-line sanitizer: a value
 * carrying a URL/host/IP/raw-markup token (or an empty/undefined value) is
 * replaced by the frozen public fallback. Never fails, never throws.
 */
function safeDisplay(value: string | undefined, fallback: string | null): string {
  if (typeof value === "string" && value !== "" && !isUnsafeDisplayString(value)) return value;
  return fallback ?? "";
}

/**
 * Phase 21 F-03 — the SINGLE read-only "Generation mode" line shown on the
 * landing and /new. It is backend-authoritative and NEVER a provider
 * selector: the backend runs ONE process-global provider
 * (GENERATION_PROVIDER), the selected mode was never sent to the backend,
 * and NO click can change the provider. The display resolution reuses
 * `effectiveProviderMode` (live > local > deterministic), so this line can
 * never contradict the capability-driven provider qualifier / per-path note
 * rendered on the same page.
 *
 *   - fake (deterministic, server-enforced or availability-derived)
 *       -> "Generation mode: Deterministic demo"
 *   - local available       -> "Generation mode: Local AI — <model> — Ready"
 *   - local configured, probe down (DEF-096: configuredProvider "ollama" with
 *     an unavailable local entry) -> the truthful per-mode line with the
 *     availability tag, e.g. "Generation mode: Local AI — <model> —
 *     Unavailable" — NEVER "Deterministic demo" on a non-fake backend;
 *   - live available        -> "Generation mode: <DTO capability label>"
 *   - live configured, probe down -> "Generation mode: <label> — Unavailable";
 *   - DTO UNAVAILABLE (null, endpoint unreachable, empty/malformed allowlist —
 *     DEF-097) -> the neutral reachability line, NEVER the deterministic-demo
 *     copy: the frontend cannot know the provider when the DTO did not report.
 */
const GENERATION_MODE_LINE_UNKNOWN =
  "Generation mode: Available once the service is reachable.";

export function generationModeLine(capabilities: GenerationCapabilitiesResponse | null): string {
  // DEF-097: no DTO report -> no provider claim of ANY kind (the deterministic
  // story is a provider claim too, and it is only true for a backend the DTO
  // actually reports as fake).
  if (!providerIsReported(capabilities)) return GENERATION_MODE_LINE_UNKNOWN;
  switch (effectiveProviderMode(capabilities)) {
    case "local": {
      const local = capabilities?.modes.find((mode) => mode.id === "local");
      const label = safeDisplay(local?.label, "Local AI");
      const model = safeDisplay(local?.model, null);
      const detail = model ? `${label} — ${model}` : label;
      // DEF-096: the tag is availability-appropriate — a configured local
      // backend with a FAILED probe must never claim "Ready".
      const tag = local?.available === true ? "Ready" : "Unavailable";
      return `Generation mode: ${detail} — ${tag}`;
    }
    case "live": {
      const live = capabilities?.modes.find((mode) => mode.id === "live");
      const label = safeDisplay(live?.label, "Cloud AI");
      if (live?.available === true) return `Generation mode: ${label}`;
      // DEF-096: a configured live backend with the probe down stays truthful.
      return `Generation mode: ${label} — Unavailable`;
    }
    case "fake":
    default:
      return "Generation mode: Deterministic demo";
  }
}

/* ======================================================================
 * Phase 21B Finding 3 — truthful example-case CTA (the demo / example
 * action that routes through the normal POST /cases journey).
 *
 * The provider selector was removed (Phase 21 F-03), so the example-case
 * action is the ONLY front-end entry that stages a journey with a FIXED
 * prompt. It uses the same `POST /cases` path as a custom prompt — the
 * backend provider is process-global (GENERATION_PROVIDER) and the request
 * never carries a mode/provider field. The CTA copy MUST therefore describe
 * exactly what the backend will do, and ONLY the capability DTO may decide
 * that:
 *
 *   - demo      (server-ENFORCED deterministic fallback, Phase 21B/DEF-096:
 *                the DTO's `configuredProvider == "fake"` AND the demo mode is
 *                available — the genuine GENERATION_PROVIDER=fake backend):
 *                the historical "Try Demo Case" + deterministic no-cost claim
 *                is truthful, because the backend WILL run the deterministic
 *                path;
 *   - local     (the DTO carries `configuredProvider == "ollama"`, OR the
 *                backend reports the local AI pipeline available): the CTA is
 *                RENAMED to a neutral example label and the note names the
 *                local AI provider (label/model from the DTO) with an explicit
 *                "not the free deterministic demo" warning — an ollama-configured
 *                backend is named local EVEN WHEN its probe is DOWN
 *                (DEF-096), because the runtime will still run that provider;
 *   - live      (the DTO carries `configuredProvider == "live"`, OR the
 *                backend reports the live provider available): the CTA is
 *                RENAMED and the note names the cloud provider, never a
 *                deterministic/no-cost promise;
 *   - unknown   (capabilities null / empty allowlist / malformed — i.e. the
 *                DTO is UNAVAILABLE, fetch failed or unreachable): the CTA is
 *                DOWNGRADED to a neutral truthful label with a provider-neutral
 *                note. The frontend CANNOT know the provider when the DTO is
 *                unavailable, so it must never claim deterministic / no-cost
 *                behavior in this state (that promise is shown only when the
 *                backend actually reports a fake-configured enablement).
 *
 * The label/note functions are PURE and sanitize every DTO-supplied label /
 * model through `safeDisplay` — a hostile or malformed report can never smuggle
 * a URL/IP/host token or a false deterministic claim into the DOM.
 * ==================================================================== */

/** The four truthful states the example-case CTA may be in. */
export type DemoCtaState = "demo" | "local" | "live" | "unknown";

/** Frozen public CTA labels (no DTO string can ever replace them). */
export const DEMO_CTA_LABEL_DEMO = "Try Demo Case";
export const DEMO_CTA_LABEL_EXAMPLE = "Try an example case";

/** Frozen note copy (per state; see module doc). */
export const DEMO_CTA_NOTE_DEMO = "Deterministic demo — no API keys, no cost.";
export const DEMO_CTA_NOTE_UNKNOWN =
  "Runs the same generation pipeline as a custom prompt.";

/**
 * Resolve the truthful example-case CTA state from the capability DTO. The
 * deterministic "demo" state requires the backend to ACTUALLY enforce the
 * deterministic fallback (Phase 21B / DEF-096):
 *   - `configuredProvider == "fake"` (server-enforced deterministic) with the
 *     demo mode available -> "demo" (the no-cost promise is truthful);
 *   - `configuredProvider == "ollama"` -> "local" — EVEN IF the probe failed
 *     and the local entry is unavailable, because the runtime WILL run that
 *     provider on the next POST /cases (an Ollama deploy is never relabelled
 *     as deterministic demo);
 *   - `configuredProvider == "live"` -> "live" (same probe-down rule);
 *   - `configuredProvider` ABSENT (older server): the availability-based
 *     derivation — "live"/"local" when the backend reports them available,
 *     "demo" when the demo mode is available with no local/live available
 *     (the genuine pre-21B fake-backend shape), backward compatible.
 * Everything else — null, an empty allowlist, a malformed payload, or a report
 * where no demo mode is actually available — resolves to "unknown": the
 * deterministic/no-cost promise is NEVER inferred from an absent or unreadable
 * report.
 */
export function demoCtaState(capabilities: GenerationCapabilitiesResponse | null): DemoCtaState {
  if (capabilities === null || typeof capabilities !== "object") return "unknown";
  const modes = capabilities.modes;
  if (!Array.isArray(modes) || modes.length === 0) return "unknown";
  const available = (id: GenerationModeId): boolean => {
    const entry = modes.find((mode) => mode.id === id);
    return entry?.available === true;
  };
  // DEF-096: the backend-configured provider is authoritative when present —
  // an ollama/live deploy yields the renamed local/live CTA even while its
  // probe is down (the runtime still runs that provider).
  const configured = capabilities.configuredProvider;
  if (configured === "fake") return available("demo") ? "demo" : "unknown";
  if (configured === "ollama") return "local";
  if (configured === "live") return "live";
  // configuredProvider absent (older server): availability-based fallback.
  if (available("live")) return "live";
  if (available("local")) return "local";
  if (available("demo")) return "demo";
  return "unknown";
}

/**
 * The truthful example-case CTA label: "Try Demo Case" ONLY when the backend
 * actually reports the demo-only deterministic allowlist; every other state
 * (local / live / unknown) uses the neutral truthful rename.
 */
export function demoCtaLabel(capabilities: GenerationCapabilitiesResponse | null): string {
  return demoCtaState(capabilities) === "demo"
    ? DEMO_CTA_LABEL_DEMO
    : DEMO_CTA_LABEL_EXAMPLE;
}

/**
 * The truthful per-state note beside the example-case CTA. Never a claim the
 * backend did not make: demo-only yields the deterministic/no-cost promise
 * ONLY for a known demo-only report; local/live name the real provider with
 * the yellow "not the free deterministic demo" warning; unknown / unavailable
 * payloads fall back to the provider-neutral pipeline note.
 */
export function demoCtaNote(capabilities: GenerationCapabilitiesResponse | null): string {
  switch (demoCtaState(capabilities)) {
    case "local": {
      const local = capabilities?.modes.find((mode) => mode.id === "local");
      const label = safeDisplay(local?.label, "Local AI");
      const model = safeDisplay(local?.model, null);
      const detail = model ? `${label} — ${model}` : label;
      return `Example case runs the local AI provider (${detail}). Not the free deterministic demo.`;
    }
    case "live": {
      const live = capabilities?.modes.find((mode) => mode.id === "live");
      const label = safeDisplay(live?.label, "Cloud AI");
      return `Example case runs the cloud AI provider (${label}). Not the free deterministic demo.`;
    }
    case "demo":
      return DEMO_CTA_NOTE_DEMO;
    case "unknown":
    default:
      return DEMO_CTA_NOTE_UNKNOWN;
  }
}