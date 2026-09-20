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
 * The per-path notes (try-demo-note / generate-provider-note) stay unchanged;
 * this line is additive and never claims behavior the backend does not have.
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
 * Resolve the effective provider story from the parsed capability DTO — the
 * SAME allowlist-parsed payload the generation-mode selector consumes. Only
 * the DTO decides:
 *   - "live"  when the backend reports live available (GENERATION_PROVIDER=live
 *     configured AND selected);
 *   - "local" when the backend reports the local Ollama mode available (the
 *     probe passed) and no live mode is available;
 *   - "fake"  EVERY other case — including unknown (`null`) capabilities,
 *     malformed payloads and a stored/local-unavailable backend. The
 *     deterministic story is always true (the demo path always exists), so it
 *     is the honest default while the probe is still in flight.
 * A hostile/malformed payload can never favour an option: `available` must be
 * exactly the boolean true and the id must be a frozen GenerationModeId.
 */
export function effectiveProviderMode(
  capabilities: GenerationCapabilitiesResponse | null,
): AppProviderMode {
  if (capabilities === null || typeof capabilities !== "object") return "fake";
  const modes = capabilities.modes;
  if (!Array.isArray(modes)) return "fake";
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
 * routes). While capabilities are unknown (null) the deterministic default is
 * shown — never a claim about a provider the backend has not confirmed.
 */
export function providerPathNoteFromCapabilities(
  capabilities: GenerationCapabilitiesResponse | null,
): string {
  return providerPathNote(effectiveProviderMode(capabilities));
}

/** Capability-driven app-level qualifier (replaces the build-time qualifier). */
export function providerQualifierFromCapabilities(
  capabilities: GenerationCapabilitiesResponse | null,
): string {
  return providerQualifier(effectiveProviderMode(capabilities));
}