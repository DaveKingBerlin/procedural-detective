import type { GenerationCapabilitiesResponse, GenerationModeId } from "../api/types";
import {
  generationModeOptionLabel,
  selectableGenerationModes,
} from "./generationMode";

/**
 * Phase 16 Track B — the generation-mode selector (landing + /new).
 *
 * A PURE renderer: the caller (a route) owns the fetch (via
 * src/hooks/useGenerationCapabilities.ts — there is NO fetch or client call
 * in any component) and passes the parsed capabilities in, plus the current
 * selection and the select callback. Behaviour:
 *
 *   - while capabilities are unknown (`null`) nothing is rendered — no claim
 *     is made until the backend has reported;
 *   - when only Demo is offerable the selector is replaced by the honest
 *     static notice "Demo mode active" (`data-testid=generation-mode-demo-notice`);
 *   - otherwise a small select (`data-testid=generation-mode-selector` with
 *     `data-testid=generation-mode-select`) offers exactly the modes the
 *     backend reported as available: Demo always, Local AI / Cloud AI only
 *     when `available: true` — never an unavailable mode as an option;
 *   - the Local option carries the honest label + verbatim model display name
 *     + the explicit Ready tag (unavailable modes are never rendered, so a
 *     rendered Local option is always honest about being Ready);
 *   - a stored selection that no longer matches an offered mode falls back to
 *     Demo (the select can never hold an unoffered value).
 */
export interface GenerationModeSelectorProps {
  /** Parsed allowlist DTO; null while the backend has not reported yet. */
  capabilities: GenerationCapabilitiesResponse | null;
  /** The current selection (falls back to Demo when not offerable). */
  value: GenerationModeId;
  /** Called with the player's chosen mode id. */
  onSelect: (mode: GenerationModeId) => void;
}

export function GenerationModeSelector({
  capabilities,
  value,
  onSelect,
}: GenerationModeSelectorProps) {
  if (capabilities === null) return null; // unknown until the backend reports

  const modes = selectableGenerationModes(capabilities);

  if (modes.length <= 1) {
    return (
      <p className="generation-mode-demo-notice" data-testid="generation-mode-demo-notice">
        Demo mode active
      </p>
    );
  }

  const effective = modes.some((mode) => mode.id === value) ? value : "demo";

  return (
    <div className="generation-mode-selector" data-testid="generation-mode-selector">
      <label htmlFor="generation-mode-select">Generation mode</label>
      <select
        id="generation-mode-select"
        data-testid="generation-mode-select"
        value={effective}
        onChange={(event) => onSelect(event.target.value as GenerationModeId)}
      >
        {modes.map((mode) => (
          <option key={mode.id} value={mode.id}>
            {generationModeOptionLabel(mode)}
          </option>
        ))}
      </select>
    </div>
  );
}