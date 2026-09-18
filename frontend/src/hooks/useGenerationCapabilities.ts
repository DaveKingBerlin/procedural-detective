import { useEffect, useState } from "react";
import { getGenerationCapabilities } from "../api/client";
import type { GenerationCapabilitiesResponse } from "../api/types";
import {
  DEMO_ONLY_CAPABILITIES,
  parseGenerationCapabilities,
} from "../journey/generationMode";

/**
 * Phase 16 Track B — fetch + parse the public generation-capabilities DTO.
 *
 * The raw JSON crossing the trust boundary is ALWAYS re-parsed through the
 * allowlist (src/journey/generationMode.ts) so unknown fields/ids are dropped
 * before anything is rendered. Any failure — endpoint not implemented,
 * unreachable backend, timeout, or a body that breaks the contract — resolves
 * to the safe demo-only payload: the UI then shows the honest static
 * "Demo mode active" notice and can never claim Local/Cloud AI availability.
 */
export function loadGenerationCapabilities(
  probe: () => Promise<GenerationCapabilitiesResponse> = getGenerationCapabilities,
): Promise<GenerationCapabilitiesResponse> {
  return probe()
    .then((raw) => parseGenerationCapabilities(raw))
    .catch(() => DEMO_ONLY_CAPABILITIES);
}

/** Hook wrapper; null until the probe resolves (render nothing meanwhile). */
export function useGenerationCapabilities(): GenerationCapabilitiesResponse | null {
  const [capabilities, setCapabilities] = useState<GenerationCapabilitiesResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    void loadGenerationCapabilities().then((next) => {
      if (!cancelled) setCapabilities(next);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return capabilities;
}