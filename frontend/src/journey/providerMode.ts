import type { GenerationCapabilitiesResponse, GenerationModeId } from "../api/types";

/**
 * Phase 18A — capability-driven app-provider display story.
 *
 * Phase 15 Track B introduced a build-time note (`VITE_APP_PROVIDER`) for the
 * provider text beside the generate path. Phase 18A makes the note TRUTHFUL by
 * deriving it from the backend's PUBLIC capability report (GET
 * /api/v1/generation-capabilities) instead: the backend is the only authority
 * on which provider actually runs, so the note can never contradict it.
 *
 * The three effective stories:
 *   - "fake"  (DEFAULT): the backend runs its built-in deterministic generator
 *     (GENERATION_PROVIDER unset/fake) — zero credentials, zero cost, and the
 *     UI says so. This remains the honest default for a plain `npm run build`;
 *   - "local": the backend runs the LOCAL AI (Ollama) pipeline: the model
 *     PROPOSES structured data; deterministic validators verify and construct
 *     the playable investigation;
 *   - "live": the backend runs the configured live provider.
 *
 * Phase 21B (DEF-096/ADV-232) — configuredProvider authority: the DTO's new
 * top-level `configuredProvider` ("fake" | "ollama" | "live", the sanitized
 * operator-config provider) is AUTHORITATIVE — an ollama/live-configured
 * backend resolves to "local"/"live" EVEN while its availability probe is
 * down/failed (the runtime still runs that provider on the next POST /cases),
 * so a probe failure can never collapse an Ollama deploy to the fake/demo
 * story. When `configuredProvider` is ABSENT (older server) the availability-
 * based derivation applies (backward compatible).
 *
 * Phase 21B (DEF-097) — DTO-unavailable neutrality: when the capability DTO is
 * NULL/unreachable/empty (the frontend cannot know the provider), the qualifier
 * and per-path note are the NEUTRAL reachability copy — never the
 * deterministic-demo claim, never a provider claim of any kind.
 *
 * Availability is derived ONLY from the capability DTO `available` booleans
 * (the same parse the selector uses), so a stored `local`/`live` selection or
 * a `VITE_APP_PROVIDER` env value can NEVER make the UI claim a provider the
 * backend does not report. `import.meta.env.VITE_APP_PROVIDER` is NO LONGER
 * consulted anywhere in the render path — the env cannot contradict the
 * backend report even in principle (the legacy parser is kept only as the
 * documented parse contract for readers of `.env.example`).
 *
 * IMPORTANT: the button labels NEVER claim live-AI behavior in fake mode.
 * The pages only ever show app-authored notes; nothing here is server text,
 * and no provider/prompt details ever reach the DOM.
 */

/** The three display stories the demo UI may claim. "fake" is the default. */
export type AppProviderMode = "fake" | "local" | "live";

/**
 * Deterministic parser for the legacy `VITE_APP_PROVIDER` value: only the
 * exact string "live" enables the live story; every other value (undefined,
 * "", "Live", "FAKE", ...) resolves to "fake". RETAINED AS DOCUMENTATION of
 * the historical env contract and for unit coverage — the render path no
 * longer reads this env (see module header), so this cannot contradict the
 * backend capability report.
 */
export function parseAppProvider(raw: unknown): AppProviderMode {
  return raw === "live" ? "live" : "fake";
}

/**
 * The honest per-mode one-line note shown beside the generate path. Phase 15
 * copy is kept VERBATIM for fake/live; Phase 18A adds the truthful local
 * wording (the model proposes; deterministic validation constructs — it never
 * claims the model proves the case or runs the scene).
 */
export function providerPathNote(mode: AppProviderMode): string {
  switch (mode) {
    case "live":
      return "Live AI provider";
    case "local":
      return "uses the local AI pipeline — the model proposes structured data; "
        + "deterministic validators build the investigation";
    case "fake":
    default:
      return "uses the built-in deterministic generator in this demo build";
  }
}

/**
 * ADV-152 — the app-level provider qualifier shown NEAR the primary CTA on the
 * landing and /new. The REQUIREMENTS §62 hero tagline ("Describe a crime. AI
 * builds a logically solvable 3D investigation.") is kept VERBATIM (product
 * copy); this qualifier makes the provider story explicit RIGHT where the
 * visitor is about to act:
 *   - "fake" (the default build): the honest demo-build wording —
 *     deterministic built-in generator, no API keys, no cost, and live AI is
 *     EXPLICITLY NOT enabled;
 *   - "local": the backend runs the local pipeline — model proposes,
 *     deterministic validators construct; live AI still explicitly off;
 *   - "live": the short enabled-provider wording.
 * The per-path notes (generate-provider-note) stay unchanged; the
 * example-case demo note (try-demo-note) is ALSO capability-driven since
 * Phase 21B Finding 3 (src/journey/generationMode.ts demoCtaLabel /
 * demoCtaNote). This line is additive and never claims behavior the backend
 * does not have.
 */
export function providerQualifier(mode: AppProviderMode): string {
  switch (mode) {
    case "live":
      return "Live AI provider enabled.";
    case "local":
      return "Local AI is available: the model proposes structured data; "
        + "deterministic validators verify and construct the investigation — "
        + "no API keys, no cost. Live AI is opt-in and not enabled in this build.";
    case "fake":
    default:
      return "Demo build: deterministic built-in generator — no API keys, no cost. "
        + "Live AI is opt-in and not enabled in this build.";
  }
}

/**
 * DEF-097 (Phase 21B) — NEUTRAL app-level provider qualifier for the state
 * where the capability DTO is UNAVAILABLE (endpoint unreachable, fetch
 * failure, empty allowlist, malformed payload — i.e. `null` capabilities or a
 * resolved empty payload). The frontend CANNOT know the provider when the DTO
 * did not report, so EVERY provider claim (deterministic demo / Local AI /
 * Live AI) is suppressed in this state. Worded to be non-spoiling and to point
 * at the reachable once the service is back — the exact contract the CTA note
 * already uses ("Runs the same generation pipeline as a custom prompt.").
 */
export const PROVIDER_QUALIFIER_UNKNOWN =
  "Generation is available once the service is reachable.";

/**
 * DEF-097 (Phase 21B) — NEUTRAL per-path provider note for the same
 * DTO-unavailable state (see {@link PROVIDER_QUALIFIER_UNKNOWN}). Sentence
 * fragment (lower-case, as the fake/local/live notes are) that claims NO
 * provider identity when the frontend cannot know it.
 */
export const PROVIDER_PATH_NOTE_UNKNOWN =
  "runs through the backend-configured generation pipeline once the service is reachable";

/**
 * True ONLY when the capability DTO actually reported usable mode information:
 * a non-null object carrying a non-empty `modes` array. This is the exact
 * "the backend told us something" predicate used by every truthful surface
 * (CTA, qualifier, per-path note, F-03 line, generation-mode display):
 * `null`, a malformed payload and the empty-allowlist fetch-failure payload
 * ({modes: []}) all resolve to FALSE here, so no page surface may claim a
 * provider in that state (DEF-096/DEF-097).
 */
export function providerIsReported(
  capabilities: GenerationCapabilitiesResponse | null,
): boolean {
  if (capabilities === null || typeof capabilities !== "object") return false;
  return Array.isArray(capabilities.modes) && capabilities.modes.length > 0;
}

/**
 * Resolve the effective provider story from the parsed capability DTO — the
 * SAME allowlist-parsed payload the generation-mode selector consumes. The
 * DTO decides, in this order:
 *   - BACKEND-CONFIGURED AUTHORITY (Phase 21B / DEF-096): when the DTO carries
 *     `configuredProvider` ("fake" | "ollama" | "live", the operator-config
 *     generator provider re-parsed to the closed enum), that value is
 *     AUTHORITATIVE — the runtime WILL run that provider on the next
 *     POST /cases even while an availability probe is down or failed. So
 *     "ollama" resolves to "local" and "live" resolves to "live" REGARDLESS
 *     of the `available` booleans (a probe failure must never collapse an
 *     Ollama-configured backend to the fake/demo story); "fake" resolves to
 *     "fake" exactly as the availability-based logic does today;
 *   - BACKWARD-COMPATIBLE FALLBACK (OLDER server that omits
 *     `configuredProvider`): the pre-21B derivation — "live" when the backend
 *     reports live available, "local" when the backend reports the local
 *     Ollama mode available, "fake" in every other case (a demo-only backend,
 *     an unavailable local/live, a malformed payload or unknown `null`
 *     capabilities — the deterministic path always exists, so it is the
 *     honest default while the probe is still in flight).
 * A hostile/malformed payload can never favour an option: `available` must be
 * exactly the boolean true, the id must be a frozen GenerationModeId, and
 * `configuredProvider` must be exactly one of the three closed values.
 */
export function effectiveProviderMode(
  capabilities: GenerationCapabilitiesResponse | null,
): AppProviderMode {
  if (capabilities === null || typeof capabilities !== "object") return "fake";
  const modes = capabilities.modes;
  if (!Array.isArray(modes)) return "fake";
  // DEF-096: the sanitized operator-config provider is authoritative whenever
  // it is present — an ollama/live deploy keeps its real identity even when
  // the availability probe FAILED (the runtime still runs that provider).
  const configured = capabilities.configuredProvider;
  if (configured === "fake") return "fake";
  if (configured === "ollama") return "local";
  if (configured === "live") return "live";
  // configuredProvider absent (older server): availability-based derivation.
  const available = (id: GenerationModeId): boolean => {
    const entry = modes.find((mode) => mode.id === id);
    return entry?.available === true;
  };
  if (available("live")) return "live";
  if (available("local")) return "local";
  return "fake";
}

/**
 * Capability-driven per-path note (replaces the build-time note in the
 * routes). DEF-097: while the capability DTO is UNAVAILABLE (null, endpoint
 * unreachable, empty/malformed allowlist) the note is NEUTRAL — the frontend
 * cannot know the provider in that state, so it never claims the deterministic
 * generator (nor any other provider). When the DTO reported, the truthful
 * per-mode note for {@link effectiveProviderMode} is shown.
 */
export function providerPathNoteFromCapabilities(
  capabilities: GenerationCapabilitiesResponse | null,
): string {
  if (!providerIsReported(capabilities)) return PROVIDER_PATH_NOTE_UNKNOWN;
  return providerPathNote(effectiveProviderMode(capabilities));
}

/**
 * Capability-driven app-level qualifier (replaces the build-time qualifier).
 * DEF-097: same neutral rule as {@link providerPathNoteFromCapabilities} —
 * a DTO-unavailable state yields the neutral qualifier, never a provider
 * claim; a reported DTO yields the truthful per-mode qualifier.
 */
export function providerQualifierFromCapabilities(
  capabilities: GenerationCapabilitiesResponse | null,
): string {
  if (!providerIsReported(capabilities)) return PROVIDER_QUALIFIER_UNKNOWN;
  return providerQualifier(effectiveProviderMode(capabilities));
}