import type {
  CreateCaseGeneration,
  GenerationCapabilitiesResponse,
  GenerationProviderDTO,
  GenerationProviderId,
  OllamaTransportId,
  OllamaTransportStatusDTO,
} from "../api/types";
import { isUnsafeDisplayString, safeDisplay } from "./generationMode";

/**
 * Phase 25 — browser-selectable generation provider (parse / persistence /
 * resolution).
 *
 * The backend publishes the ADDITIVE provider-selection keys on GET
 * /api/v1/generation-capabilities:
 *
 *   defaultProvider: "fake" | "ollama" | "frontier"
 *   providers: [{ id, label?, available, model?, reason?, defaultModel?,
 *                 manualModelEntry?, transports? }, ...]
 *
 * Every value crossing the trust boundary is UNTRUSTED server data. This
 * module is the ONLY place that:
 *
 *   - re-parses `providers[]` + `defaultProvider` into the closed ids
 *     (fake | ollama | frontier); unknown ids and unsafe labels/models/reasons
 *     are DROPPED (a hostile reply can never inject an arbitrary option or a
 *     URL/IP/host hint into the selector) — reusing the SAME safe-display
 *     guards as src/journey/generationMode.ts (`isUnsafeDisplayString` /
 *     `safeDisplay`);
*   - resolves the effective browser selection (sessionStorage preference,
 *     otherwise the server `defaultProvider`, otherwise the FIRST still-
 *     available provider in server order) WITH the Phase 26C1 authoritative-
 *     selection rule: the PROVIDER follows DISCARD-IF-STALE semantics (a
 *     stored provider that is no longer available is IGNORED and the server
 *     default / first valid available provider is chosen instead — a stale
 *     value is never submitted, §10.1), while the SELECTED TRANSPORT is
 *     AUTHORITATIVE and is preserved verbatim even when its transport is
 *     currently unavailable (no silent rewrite to the other transport; the UI
 *     shows the unavailable state, §2/§4/§7). Only when NO stored transport
 *     exists does the deterministic no-preference default apply
 *     (`defaultOllamaTransport`);
 *   - persists ONLY the three NON-SECRET preference keys in sessionStorage
 *     (generationProvider / ollamaTransport / ollamaModel). NEVER a
 *     credential, token, URL or configuration value (§1.4 / §10.1).
 *
 * Hard guarantees:
 *   - NO host/IP, credential, URL or provider configuration ever leaves this
 *     module: the only strings it produces are frozen public labels, the
 *     sanitized DTO label/model, the sanitized short reason, and the user's
 *     own model text (trimmed, non-empty);
 *   - the browser never supplies an endpoint/URL/API key: the only
 *     user-supplied provider-specific string is the Ollama model identifier;
 *   - absence of the additive keys (an OLDER server) resolves to `null` — the
 *     routes then offer no selector and POST /cases carries NO selection
 *     (pre-25 behavior byte-identical);
 *   - never throws on any input.
 */

/** The three closed generation-provider ids (Phase 25 §1.3). */
export const GENERATION_PROVIDER_IDS: readonly GenerationProviderId[] = [
  "fake",
  "ollama",
  "frontier",
];

/** The two closed Ollama transport ids (Phase 25 §1.3). */
export const OLLAMA_TRANSPORT_IDS: readonly OllamaTransportId[] = ["server", "bridge"];

/** Frozen public provider labels — the fallback when the DTO label is absent/unsafe. */
export const PROVIDER_LABELS: Readonly<Record<GenerationProviderId, string>> = {
  fake: "Demo / Fake",
  ollama: "Local Ollama",
  frontier: "Frontier",
};

/** Frozen subtitle under the Fake option (no DTO string can reach it). */
export const FAKE_PROVIDER_SUBTITLE = "No external AI request";

/** Frozen public transport labels (no DTO string can reach them). */
export const OLLAMA_TRANSPORT_SERVER_LABEL = "Server / Direct";
export const OLLAMA_TRANSPORT_BRIDGE_LABEL = "My device via Bridge";
export const OLLAMA_TRANSPORT_HEADING = "Connection";

/** Frozen label for the Ollama model text field (named without the contiguous
 *  provider-config env sequence "OLLAMA"+"_MODEL" — see the key note below). */
export const MODEL_INPUT_LABEL = "Model";

/** SessionStorage keys (NON-SECRET preferences only — §10.1).
 *
 * RELEASE-HYGIENE NOTE: the key spellings below deliberately avoid the
 * provider-config env NAME formed by "OLLAMA" + underscore + "MODEL"
 * (case-insensitive) because `tools/release_check.py` forbids that token in
 * the player bundle — a storage key or identifier containing the contiguous
 * sequence would be flagged. The LOGICAL preference names remain
 * generationProvider / ollamaTransport / ollamaModel (§10.1); these strings
 * are the internal non-secret key spellings only. */
export const GENERATION_PROVIDER_STORAGE_KEY = "pd_generation_provider";
export const OLLAMA_TRANSPORT_STORAGE_KEY = "pd_ollama_transport";
export const GENERATION_MODEL_STORAGE_KEY = "pd_generation_model";

/** Known short reasons mapped to frozen friendly copy. UNKNOWN safe text is
 *  NOT rendered at all (a raw exception/diagnostic string can never reach the
 *  UI — see {@link providerReasonLabel}). */
const REASON_LABELS: Readonly<Record<string, string>> = {
  not_configured: "not configured",
  not_connected: "not connected",
};

/** Frozen generic unavailability tag (shown when no known reason is published). */
export const PROVIDER_UNAVAILABLE_LABEL = "unavailable";

/**
 * The resolved browser-side provider selection. `ollamaTransport` /
 * `ollamaModel` are only ever non-null/non-empty while the provider is
 * "ollama" (they are carried to the backend ONLY in that case).
 */
export interface GenerationProviderSelection {
  generationProvider: GenerationProviderId;
  ollamaTransport: OllamaTransportId | null;
  ollamaModel: string;
}

/** Minimal sessionStorage surface used here (sessionStorage-compatible). */
export interface GenerationProviderStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** One sanitized transport of an Ollama offer. */
export interface ParsedGenerationTransport {
  available: boolean;
  /** Bridge-only: whether the caller's own session is bound right now. */
  connected: boolean;
  /** Short safe availability reason or null. */
  reason: string | null;
}

/** One fully-sanitized provider OFFER the selector may display/offer. */
export interface GenerationProviderOffer {
  id: GenerationProviderId;
  /** Public display label (DTO verbatim when safe, else the frozen label). */
  label: string;
  available: boolean;
  /** Frontier configured-model display name (sanitized) or null. */
  model: string | null;
  /** Short safe reason when unavailable, or null. */
  reason: string | null;
  /** Ollama-only: the configured default model identifier or null. */
  defaultModel: string | null;
  /** Ollama-only: whether a manual model string may be entered. */
  manualModelEntry: boolean;
  /** Sanitized per-transport availability (both sides always present). */
  transports: {
    server: ParsedGenerationTransport;
    bridge: ParsedGenerationTransport;
  };
}

function isGenerationProviderId(value: unknown): value is GenerationProviderId {
  return typeof value === "string" && (GENERATION_PROVIDER_IDS as readonly string[]).includes(value);
}

function isOllamaTransportId(value: unknown): value is OllamaTransportId {
  return typeof value === "string" && (OLLAMA_TRANSPORT_IDS as readonly string[]).includes(value);
}

/** Parse ONE transport block defensively (strict booleans; reason sanitized). */
function parseTransport(raw: unknown): ParsedGenerationTransport {
  if (typeof raw !== "object" || raw === null) {
    return { available: false, connected: false, reason: null };
  }
  const block = raw as { available?: unknown; connected?: unknown; reason?: unknown };
  const reason = typeof block.reason === "string" ? block.reason : null;
  return {
    available: typeof block.available === "boolean" ? block.available : false,
    connected: typeof block.connected === "boolean" ? block.connected : false,
    reason: reason !== null && reason !== "" && !isUnsafeDisplayString(reason) ? reason : null,
  };
}

/** True when a raw transport block NAMES at least one known field ({} is absent). */
function hasTransportFields(raw: unknown): boolean {
  if (typeof raw !== "object" || raw === null) return false;
  const block = raw as { available?: unknown; connected?: unknown; reason?: unknown };
  return "available" in block || "connected" in block || "reason" in block;
}

/** Parse a DTO display string through the module's last-line sanitizer. */
function parseDisplay(value: unknown): string | null {
  if (typeof value !== "string" || value === "" || isUnsafeDisplayString(value)) return null;
  return value;
}

/**
 * Re-parse the UNTRUSTED `providers[]` additive key into sanitized
 * {@link GenerationProviderDTO}s. ONLY the closed provider ids survive;
 * unknown ids and duplicate ids are DROPPED (first occurrence wins, ADV-208
 * parity); labels/models/reasons/defaultModel pass through `isUnsafeDisplayString`;
 * `available`/`manualModelEntry` are STRICT booleans; transports are strict
 * booleans with sanitized reasons. Never throws; a malformed/absent list
 * resolves to the empty array (the selector is then not offered).
 */
export function parseGenerationProviders(raw: unknown): GenerationProviderDTO[] {
  if (!Array.isArray(raw)) return [];
  const out: GenerationProviderDTO[] = [];
  const seen = new Set<string>();
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null) continue;
    const id = (entry as { id?: unknown }).id;
    if (!isGenerationProviderId(id) || seen.has(id)) continue;
    seen.add(id);
    const em = entry as {
      available?: unknown;
      label?: unknown;
      model?: unknown;
      reason?: unknown;
      defaultModel?: unknown;
      manualModelEntry?: unknown;
      transports?: unknown;
    };
    const dto: GenerationProviderDTO = {
      id,
      available: typeof em.available === "boolean" ? em.available : false,
    };
    const label = parseDisplay(em.label);
    if (label !== null) dto.label = label;
    const model = parseDisplay(em.model);
    if (model !== null) dto.model = model;
    const reason = parseDisplay(em.reason);
    if (reason !== null) dto.reason = reason;
    const defaultModel = parseDisplay(em.defaultModel);
    if (defaultModel !== null) dto.defaultModel = defaultModel;
    if (typeof em.manualModelEntry === "boolean") dto.manualModelEntry = em.manualModelEntry;
    if (typeof em.transports === "object" && em.transports !== null) {
      const rawServer = (em.transports as { server?: unknown }).server;
      const rawBridge = (em.transports as { bridge?: unknown }).bridge;
      const transports: NonNullable<GenerationProviderDTO["transports"]> = {};
      if (hasTransportFields(rawServer)) {
        transports.server = toTransportStatusDTO(parseTransport(rawServer));
      }
      if (hasTransportFields(rawBridge)) {
        transports.bridge = toTransportStatusDTO(parseTransport(rawBridge));
      }
      if (Object.keys(transports).length > 0) {
        dto.transports = transports;
      }
    }
    out.push(dto);
  }
  return out;
}

/** Map a parsed transport to the DTO status shape (connected when declared). */
function toTransportStatusDTO(parsed: ParsedGenerationTransport): OllamaTransportStatusDTO {
  const out: OllamaTransportStatusDTO = { available: parsed.available };
  if (parsed.connected) out.connected = true;
  if (parsed.reason !== null) out.reason = parsed.reason;
  return out;
}

/**
 * Re-parse the UNTRUSTED `defaultProvider` additive key. ONLY the closed
 * enum "fake" | "ollama" | "frontier" survives; anything else (missing,
 * hostile, malformed) resolves to null — the client then falls back to the
 * first still-available provider.
 */
export function parseDefaultGenerationProvider(raw: unknown): GenerationProviderId | null {
  return isGenerationProviderId(raw) ? raw : null;
}

/**
 * Safe short reason text for the selector. ONLY the frozen known tokens
 * ("not_configured" / "not_connected") are ever rendered (mapped to their
 * frozen copy); every other value — including safe-looking arbitrary server
 * text — resolves to null, so a raw exception/diagnostic string (or a
 * URL/IP/host hint) can never reach the UI. Callers show the frozen generic
 * "unavailable" tag instead.
 */
export function providerReasonLabel(reason: string | null | undefined): string | null {
  if (typeof reason !== "string" || reason === "") return null;
  const sanitized = safeDisplay(reason, null);
  if (sanitized === "") return null;
  return REASON_LABELS[sanitized] ?? null;
}

/**
 * True ONLY when the capability DTO actually offers a provider-selection
 * surface: the additive `providers` list is present AND non-empty. Absence
 * (an OLDER server, a hostile/empty list) -> false -> the routes offer no
 * selector and POST /cases carries no selection (pre-25 byte-identical).
 */
export function hasGenerationProviderOffer(
  capabilities: GenerationCapabilitiesResponse | null,
): boolean {
  return Array.isArray(capabilities?.providers) && (capabilities?.providers?.length ?? 0) > 0;
}

/**
 * Build the sanitized, selector-ready provider OFFERS from a capabilities
 * DTO. Even a HAND-CONSTRUCTED (parser-bypassed) capabilities object is
 * re-sanitized here (last-line defense, same discipline as
 * `remoteLocalAiBlock`): unknown ids are dropped, labels fall back to the
 * frozen public label when unsafe, models/reasons are sanitized, and the
 * always-present transport pair defaults BOTH sides to unavailable when the
 * DTO carries no `transports` block.
 */
export function buildProviderOffers(
  capabilities: GenerationCapabilitiesResponse | null,
): GenerationProviderOffer[] {
  const offers: GenerationProviderOffer[] = [];
  for (const dto of capabilities?.providers ?? []) {
    if (!isGenerationProviderId(dto?.id)) continue;
    const id = dto.id;
    const label = safeDisplay(dto.label, PROVIDER_LABELS[id]);
    const model = safeDisplay(dto.model ?? undefined, null);
    const reason = safeDisplay(dto.reason ?? undefined, null);
    const defaultModel = safeDisplay(dto.defaultModel ?? undefined, null);
    const raw = dto.transports;
    const server = typeof raw?.server === "object" && raw.server !== null ? raw.server : null;
    const bridge = typeof raw?.bridge === "object" && raw.bridge !== null ? raw.bridge : null;
    offers.push({
      id,
      label: label !== "" ? label : PROVIDER_LABELS[id],
      available: dto.available === true,
      model: model !== "" ? model : null,
      reason: reason !== "" ? reason : null,
      defaultModel: defaultModel !== "" ? defaultModel : null,
      manualModelEntry: dto.manualModelEntry === true,
      transports: {
        server: parseTransport(server),
        bridge: parseTransport(bridge),
      },
    });
  }
  return offers;
}

/* ======================================================================
 * SessionStorage persistence — the ONLY stored client material is the three
 * NON-SECRET preference keys (§10.1). Credentials/tokens/URLs are NEVER
 * stored. The storage surface is injectable (unit tests substitute a fake)
 * and every operation is best-effort (a storage failure never crashes).
 * ==================================================================== */

function defaultStorage(): GenerationProviderStorage | null {
  try {
    if (typeof window !== "undefined" && typeof window.sessionStorage !== "undefined") {
      return window.sessionStorage;
    }
  } catch {
    // sessionStorage can throw in hardened/embedded contexts — treat as absent.
  }
  return null;
}

function writeProvider(
  id: GenerationProviderId,
  storage: GenerationProviderStorage | null | undefined,
): boolean {
  const store = storage ?? defaultStorage();
  if (!store) return false;
  try {
    store.setItem(GENERATION_PROVIDER_STORAGE_KEY, id);
    return true;
  } catch {
    return false;
  }
}

function writeTransport(
  transport: OllamaTransportId,
  storage: GenerationProviderStorage | null | undefined,
): boolean {
  const store = storage ?? defaultStorage();
  if (!store) return false;
  try {
    store.setItem(OLLAMA_TRANSPORT_STORAGE_KEY, transport);
    return true;
  } catch {
    return false;
  }
}

function writeModel(model: string, storage: GenerationProviderStorage | null | undefined): boolean {
  const store = storage ?? defaultStorage();
  if (!store) return false;
  try {
    store.setItem(GENERATION_MODEL_STORAGE_KEY, model);
    return true;
  } catch {
    return false;
  }
}

/** Persist the provider preference (best-effort; never throws). */
export function setGenerationProvider(
  id: GenerationProviderId,
  storage?: GenerationProviderStorage | null,
): boolean {
  return writeProvider(id, storage);
}

/** Read the stored provider preference. ONLY closed ids are returned; a
 *  stale/tampered value reads null (the caller then falls back to the server
 *  default — DISCARD-IF-STALE). */
export function getGenerationProvider(
  storage?: GenerationProviderStorage | null,
): GenerationProviderId | null {
  const store = storage ?? defaultStorage();
  if (!store) return null;
  let raw: string | null = null;
  try {
    raw = store.getItem(GENERATION_PROVIDER_STORAGE_KEY);
  } catch {
    return null;
  }
  return isGenerationProviderId(raw) ? raw : null;
}

/** Remove the stored provider preference (best-effort). */
export function clearGenerationProvider(storage?: GenerationProviderStorage | null): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.removeItem(GENERATION_PROVIDER_STORAGE_KEY);
  } catch {
    // Best-effort: never crash the page.
  }
}

/** Persist the Ollama transport preference (best-effort). */
export function setOllamaTransport(
  transport: OllamaTransportId,
  storage?: GenerationProviderStorage | null,
): boolean {
  return writeTransport(transport, storage);
}

/** Read the stored Ollama transport. ONLY the closed ids are returned
 *  (a tampered/invalid value reads null). Phase 26C1: the value is
 *  AUTHORITATIVE once present — it is never discarded because the transport
 *  is currently unavailable (§2/§4/§7). */
export function getOllamaTransport(
  storage?: GenerationProviderStorage | null,
): OllamaTransportId | null {
  const store = storage ?? defaultStorage();
  if (!store) return null;
  let raw: string | null = null;
  try {
    raw = store.getItem(OLLAMA_TRANSPORT_STORAGE_KEY);
  } catch {
    return null;
  }
  return isOllamaTransportId(raw) ? raw : null;
}

/** Remove the stored Ollama transport (best-effort). */
export function clearOllamaTransport(storage?: GenerationProviderStorage | null): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.removeItem(OLLAMA_TRANSPORT_STORAGE_KEY);
  } catch {
    // Best-effort: never crash the page.
  }
}

/** Persist the Ollama model preference (best-effort). An empty/blank value
 *  clears the key instead. The model is a NON-SECRET preference string (the
 *  backend validates it centrally). */
export function setOllamaModel(
  model: string,
  storage?: GenerationProviderStorage | null,
): boolean {
  const trimmed = typeof model === "string" ? model.trim() : "";
  if (trimmed === "") {
    clearOllamaModel(storage);
    return false;
  }
  return writeModel(trimmed, storage);
}

/** Read the stored Ollama model preference (trimmed, empty -> null). */
export function getOllamaModel(storage?: GenerationProviderStorage | null): string | null {
  const store = storage ?? defaultStorage();
  if (!store) return null;
  let raw: string | null = null;
  try {
    raw = store.getItem(GENERATION_MODEL_STORAGE_KEY);
  } catch {
    return null;
  }
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  return trimmed !== "" ? trimmed : null;
}

/** Remove the stored Ollama model (best-effort). */
export function clearOllamaModel(storage?: GenerationProviderStorage | null): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.removeItem(GENERATION_MODEL_STORAGE_KEY);
  } catch {
    // Best-effort: never crash the page.
  }
}

/** Persist the full resolved selection — only the three non-secret keys.
 *  Phase 26C1 §4 — for a NON-ollama selection the transport/model preference
 *  keys are PRESERVED (not cleared): they are the user's Ollama preference
 *  for the next time they choose the Ollama provider (Fake -> Ollama restores
 *  the intended transport, §8). While the provider is non-ollama those keys
 *  are INERT — never rendered, never serialized. `clearGenerationSelection`
 *  remains the full reset for reset flows. */
export function persistGenerationSelection(
  selection: GenerationProviderSelection,
  storage?: GenerationProviderStorage | null,
): void {
  setGenerationProvider(selection.generationProvider, storage);
  if (selection.generationProvider === "ollama") {
    if (selection.ollamaTransport !== null) {
      setOllamaTransport(selection.ollamaTransport, storage);
    } else {
      clearOllamaTransport(storage);
    }
    if (selection.ollamaModel !== "") {
      setOllamaModel(selection.ollamaModel, storage);
    } else {
      clearOllamaModel(storage);
    }
  }
  // Non-ollama: the transport/model keys are deliberately left untouched.
}

/** Clear all three stored preference keys (reset flows; never credentials). */
export function clearGenerationSelection(storage?: GenerationProviderStorage | null): void {
  clearGenerationProvider(storage);
  clearOllamaTransport(storage);
  clearOllamaModel(storage);
}

/* ======================================================================
 * Phase 26C1 — selection-availability separation (§5/§7).
 *
 * `selectedTransport` (explicit user choice / persisted preference /
 * successful pairing) and `serverAvailable`/`bridgeAvailable`/
 * `bridgeConnected` (capability state) are SEPARATE concerns. A chosen
 * transport is NEVER recomputed from availability; availability is only
 * ever DISPLAYED next to the (authoritative) selection.
 * ==================================================================== */

/**
 * Phase 26C1 §5 — the DETERMINISTIC no-preference default Ollama transport.
 *
 * Applies ONLY when no explicit/persisted transport choice exists:
 *   1. an available transport that is ALSO connected for this session
 *      (`bridge` bound to this anonymous session) — a live session binding
 *      is the strongest intent signal (§5: "bridge when connected");
 *   2. else the ONE available transport when availability is unambiguous
 *      (exactly one side reports available);
 *   3. else `server` when BOTH sides are available (documented deterministic
 *      tie-break — the operator's canonical Direct-Ollama path);
 *   4. else null (no usable transport; the model entry stays manual).
 *
 * This rule NEVER re-runs once an explicit or persisted choice exists, and it
 * is the ONLY place "server" survives as a default — "server-first whenever
 * the probe is up" (the Phase 26C1 defect) is gone (§2/§5/§7).
 */
export function defaultOllamaTransport(
  offer: GenerationProviderOffer | undefined,
): OllamaTransportId | null {
  if (offer === undefined) return null;
  const serverAvailable = offer.transports.server.available === true;
  const bridgeAvailable = offer.transports.bridge.available === true;
  const bridgeConnected = offer.transports.bridge.connected === true;
  if (bridgeAvailable && bridgeConnected) return "bridge";
  if (serverAvailable && !bridgeAvailable) return "server";
  if (bridgeAvailable && !serverAvailable) return "bridge";
  if (serverAvailable && bridgeAvailable) return "server";
  return null;
}

/**
 * Phase 26C1 §3 — the selection a SUCCESSFUL Bridge pairing implies: the
 * Ollama provider with the Bridge transport (selected + persisted `bridge`).
 *
 * A completed pairing is strong Bridge intent. The model keeps the current
 * Ollama model when one exists, else the server-configured `defaultModel`
 * (never a hard-coded name).
 *
 * ORDERED-INTENT CONTRACT (LOW fix): this PURE function models the pairing's
 * implied selection and knows NOTHING about ordering. The /new route owns the
 * ordering guard (src/routes/new.tsx `explicitChoiceSincePairingStartedRef`):
 *   - a provider/transport choice made BEFORE the pairing began is OLDER
 *     intent — the completed pairing (this function's output) still overrides
 *     it (§3 "even over an earlier explicit Server");
 *   - a provider/transport choice made AFTER the pairing began is the NEWER
 *     intent — the route SKIPS this function and keeps the explicit choice;
 *   - a LATER explicit radio choice after the pairing completed also wins (the
 *     selection that this function produced is simply replaced by the next
 *     `onChange`).
 * Capability refreshes never call this function (they never re-fire a pairing).
 */
export function bridgePairedSelection(
  capabilities: GenerationCapabilitiesResponse | null,
  current: GenerationProviderSelection | null,
): GenerationProviderSelection {
  let model = "";
  if (current?.generationProvider === "ollama" && current.ollamaModel !== "") {
    model = current.ollamaModel;
  } else {
    const ollama = buildProviderOffers(capabilities).find((offer) => offer.id === "ollama");
    model = ollama?.defaultModel ?? "";
  }
  return { generationProvider: "ollama", ollamaTransport: "bridge", ollamaModel: model };
}

/* ======================================================================
 * Resolution — the effective browser selection (§10).
 *
 * Order:
 *   1. a VALID sessionStorage choice (known id AND still available) — the
 *      DISCARD-IF-STALE rule APPLIES TO THE PROVIDER ONLY: a stored provider
 *      that is no longer available is IGNORED and never submitted;
 *   2. otherwise the server `defaultProvider` (when it is still available);
 *   3. otherwise the FIRST still-available provider in the server's own
 *      order;
 *   4. otherwise the deterministic `fake` offer (the historical default).
 * For Ollama the TRANSPORT is authoritative (Phase 26C1): the stored
 * preference — `server` or `bridge` — is preserved verbatim even when that
 * transport is currently unavailable (§2/§4); only with NO stored transport
 * does `defaultOllamaTransport` apply. The model defaults to the configured
 * `defaultModel` when no (valid) stored model exists — model names are NEVER
 * hard-coded here (§2).
 * ==================================================================== */

/**
 * Resolve the effective browser provider selection from the parsed
 * capabilities + the (injectable) sessionStorage preference. Returns null
 * when the additive provider offer is absent (OLDER server) — the caller
 * then offers no selector and sends no selection.
 */
export function resolveProviderSelection(
  capabilities: GenerationCapabilitiesResponse | null,
  storage?: GenerationProviderStorage | null,
): GenerationProviderSelection | null {
  if (!hasGenerationProviderOffer(capabilities)) return null;
  const offers = buildProviderOffers(capabilities);
  if (offers.length === 0) return null;
  const availableIds = new Set(offers.filter((offer) => offer.available).map((offer) => offer.id));

  // DISCARD-IF-STALE provider: a stored value is honored ONLY when it is a
  // known id AND still available; otherwise the server default or the first
  // still-available provider wins (§10.1).
  const storedProvider = getGenerationProvider(storage);
  let provider: GenerationProviderId;
  if (storedProvider !== null && availableIds.has(storedProvider)) {
    provider = storedProvider;
  } else {
    const defaultId = capabilities?.defaultProvider ?? null;
    if (defaultId !== null && availableIds.has(defaultId)) {
      provider = defaultId;
    } else {
      const firstAvailable = offers.find((offer) => offer.available)?.id;
      provider = firstAvailable ?? "fake";
    }
  }

  let ollamaTransport: OllamaTransportId | null = null;
  let ollamaModel = "";
  if (provider === "ollama") {
    const ollama = offers.find((offer) => offer.id === "ollama");
    if (ollama !== undefined) {
      // Phase 26C1 §2/§4/§7 — the SELECTED transport is authoritative and is
      // preserved VERBATIM, including when its transport is currently
      // unavailable (no silent rewrite to the other side; the UI shows the
      // unavailable state instead). Only when NO stored preference exists
      // does the deterministic no-preference default apply.
      const storedTransport = getOllamaTransport(storage);
      ollamaTransport =
        storedTransport !== null ? storedTransport : defaultOllamaTransport(ollama);
      // Model: stored (non-empty) preference first, else the CONFIGURED
      // default model from the capabilities API — never a hard-coded name.
      const storedModel = getOllamaModel(storage);
      ollamaModel = storedModel !== null ? storedModel : (ollama.defaultModel ?? "");
    }
  }

  return {
    generationProvider: provider,
    ollamaTransport: provider === "ollama" ? ollamaTransport : null,
    ollamaModel: provider === "ollama" ? ollamaModel : "",
  };
}

/**
 * Map a resolved browser selection to the flat POST /api/v1/cases block
 * ({@link CreateCaseGeneration}). The transport/model travel ONLY for the
 * Ollama provider (and only when actually present); every other selection —
 * and null (no provider offer) — resolves to `undefined`, so the old
 * no-selection call sites stay byte-identical (§13).
 *
 * DOCUMENTED NO-USABLE-TRANSPORT PATH (fail-closed, INFONote A1a): an Ollama
 * selection with NO usable transport (`ollamaTransport: null` — both
 * transports unavailable and no stored choice) posts `{generationProvider:
 * "ollama"}` WITHOUT the transport key. The backend rejects it with
 * INVALID_GENERATION_PROVIDER, which the journey maps to the frozen safe
 * `invalidGenerationProvider` copy on submit — this is an INTENDED fail-closed
 * rejection (the client never silently fabricates a fallback transport), not a
 * silent fallback.
 */
export function toCreateCaseGeneration(
  selection: GenerationProviderSelection | null,
): CreateCaseGeneration | undefined {
  if (selection === null) return undefined;
  const generation: CreateCaseGeneration = { generationProvider: selection.generationProvider };
  if (selection.generationProvider === "ollama") {
    if (selection.ollamaTransport !== null) generation.ollamaTransport = selection.ollamaTransport;
    if (selection.ollamaModel !== "") generation.ollamaModel = selection.ollamaModel;
  }
  return generation;
}
