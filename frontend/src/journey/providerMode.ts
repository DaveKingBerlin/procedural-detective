/**
 * Phase 15 Track B — config-driven app-provider display mode.
 *
 * The generation path behind "Generate a New Mystery" is ALWAYS the same
 * browser journey (POST /cases -> progress -> published playthrough). What
 * changes is the PROVIDER the backend uses:
 *   - "fake"  (default, deterministic demo build): the backend's built-in
 *     deterministic generator answers — zero credentials, zero cost, and the
 *     UI says so;
 *   - "live"  (opt-in via LLM_API_KEY etc.): the same path reaches the
 *     configured provider and prompt influence is real.
 *
 * The runtime signal is `import.meta.env.VITE_APP_PROVIDER` (built into the
 * bundle at build time by Vite). Anything other than the exact string "live"
 * is treated as the honest default "fake" — a misspelled/absent value can
 * never make the demo claim live-AI behavior it does not have.
 *
 * IMPORTANT: the button label NEVER claims live-AI behavior in fake mode.
 * The page only ever shows app-authored, config-driven notes; nothing here is
 * server text, and no provider/prompt details ever reach the DOM.
 */

/** The two display modes the demo UI may claim. */
export type AppProviderMode = "fake" | "live";

/**
 * Deterministic parser: only the exact string "live" enables the live note;
 * every other value (undefined, "", "Live", "FAKE", ...) resolves to "fake".
 */
export function parseAppProvider(raw: unknown): AppProviderMode {
  return raw === "live" ? "live" : "fake";
}

/**
 * The honest per-mode one-line note shown beside the generate path (Phase 15
 * copy: fake keeps the deterministic build honest; live names the provider).
 */
export function providerPathNote(mode: AppProviderMode): string {
  return mode === "live"
    ? "Live AI provider"
    : "uses the built-in deterministic generator in this demo build";
}

/**
 * ADV-152 — the app-level provider qualifier shown NEAR the primary CTA on the
 * landing and /new. The REQUIREMENTS §62 hero tagline ("Describe a crime. AI
 * builds a logically solvable 3D investigation.") is kept VERBATIM (product
 * copy); this qualifier makes the default-build honesty explicit RIGHT where
 * the visitor is about to act, so "AI" can never be misread as a live-AI claim
 * in the deterministic demo build:
 *   - "fake" (default): the honest demo-build wording — deterministic built-in
 *     generator, no API keys, no cost, and live AI is EXPLICITLY NOT enabled;
 *   - "live": the short enabled-provider wording.
 * The per-path notes (try-demo-note / generate-provider-note) stay unchanged;
 * this line is additive and never claims behavior the build does not have.
 */
export function providerQualifier(mode: AppProviderMode): string {
  return mode === "live"
    ? "Live AI provider enabled."
    : "Demo build: deterministic built-in generator — no API keys, no cost. "
      + "Live AI is opt-in and not enabled in this build.";
}

/** App-provider mode bound once at module load from the Vite env (default "fake"). */
export const APP_PROVIDER_MODE: AppProviderMode = parseAppProvider(import.meta.env.VITE_APP_PROVIDER);