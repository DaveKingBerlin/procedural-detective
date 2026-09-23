import type { GenerationCapabilitiesResponse } from "../api/types";
import { generationModeLine } from "./generationMode";
import { effectiveProviderMode, providerIsReported } from "./providerMode";

/**
 * Phase 21 F-03 — READ-ONLY generation-mode display (landing + /new).
 *
 * The Phase 16 interactive selector was REMOVED because the selected mode was
 * never sent to the backend: the backend provider is process-global
 * (GENERATION_PROVIDER), so a client-side "choice" was a lie. The product now
 * shows ONE truthful, backend-authoritative capability line driven solely by
 * GET /api/v1/generation-capabilities — exactly what the running backend
 * reports. There is NO <select>, NO option, NO onChange/onSelect and no
 * persisted "selection": the browser never implies that a click changes the
 * provider.
 *
 * The caller (a route) owns the fetch (src/hooks/useGenerationCapabilities.ts
 * — there is NO fetch or client call in this component) and passes the parsed
 * capabilities in. Behaviour:
 *
 *   - while capabilities are unknown (`null`) nothing is rendered — no claim
 *     is made until the backend has reported;
 *   - when the capability DTO is UNAVAILABLE (endpoint unreachable, fetch
 *     failure, empty allowlist — DEF-097) the read-only line is the NEUTRAL
 *     reachability copy "Generation mode: Available once the service is
 *     reachable." — the deterministic "Demo mode active" story is a provider
 *     claim the frontend cannot make when the DTO did not report;
 *   - when only the deterministic demo mode is offerable (`effectiveProviderMode
 *     === "fake"` over a REPORTED DTO) the static honest notice "Demo mode
 *     active" (`data-testid=generation-mode-demo-notice`) is shown together
 *     with the truthful read-only line "Generation mode: Deterministic demo"
 *     (`data-testid=generation-mode-line`) — never a provider claim, never a
 *     switch;
 *   - otherwise the container (`data-testid=generation-mode-selector`) shows
 *     the single read-only line: "Generation mode: Local AI — <model> — Ready"
 *     when the backend reports the local pipeline available,
 *     "Generation mode: Local AI — <model> — Unavailable" for a configured
 *     local backend whose probe FAILED (DEF-096 — the runtime still runs that
 *     provider, so the line stays per-mode and never claims "Deterministic
 *     demo"), or "Generation mode: <capability label>[ — Unavailable]" when
 *     the backend reports/configured a live provider. The DTO label/model pass
 *     verbatim ONLY after the allowlist + last-line sanitizer guards (no
 *     host/IP/URL/raw markup ever rendered).
 */
export interface GenerationModeDisplayProps {
  /** Parsed allowlist DTO; null while the backend has not reported yet. */
  capabilities: GenerationCapabilitiesResponse | null;
}

export function GenerationModeDisplay({ capabilities }: GenerationModeDisplayProps) {
  if (capabilities === null) return null; // unknown until the backend reports

  if (!providerIsReported(capabilities)) {
    // DEF-097: the endpoint answered nothing usable (empty allowlist after a
    // fetch failure, malformed payload...). The frontend CANNOT know the
    // provider, so the deterministic "Demo mode active" notice + line are
    // suppressed and the neutral read-only line is shown instead — no page
    // surface may claim a provider in this state.
    return (
      <div className="generation-mode-selector" data-testid="generation-mode-selector">
        <p className="generation-mode-line" data-testid="generation-mode-line">
          {generationModeLine(capabilities)}
        </p>
      </div>
    );
  }

  if (effectiveProviderMode(capabilities) === "fake") {
    // Demo-only (server-enforced deterministic or the availability-derived
    // fake backend): the honest static notice plus the truthful deterministic
    // line. No selector container, no options — nothing implies a switch.
    // NOTE: `generation-mode-demo-notice` must keep its exact copy "Demo
    // mode active" (QA-owned e2e asserts it verbatim).
    return (
      <div className="generation-mode generation-mode--demo">
        <p className="generation-mode-demo-notice" data-testid="generation-mode-demo-notice">
          Demo mode active
        </p>
        <p className="generation-mode-line" data-testid="generation-mode-line">
          {generationModeLine(capabilities)}
        </p>
      </div>
    );
  }

  // A non-demo provider is configured AND available: the single read-only,
  // backend-authoritative line. Deliberately NOT a <select>: the container
  // testid is preserved (e2e relies on it) but it carries no control.
  return (
    <div className="generation-mode-selector" data-testid="generation-mode-selector">
      <p className="generation-mode-line" data-testid="generation-mode-line">
        {generationModeLine(capabilities)}
      </p>
    </div>
  );
}