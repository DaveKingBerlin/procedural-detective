import type { ReactElement } from "react";
import type { FocusBadges } from "./objectLabel";
import { FALLBACK_EVIDENCE_LABEL, PROCEDURAL_ARTIFACT_BADGE, VALIDATED_GEOMETRY_BADGE } from "./objectLabel";

/**
 * Phase 18B — Forensic Focus inspection surface.
 *
 * A small, purely presentational overlay inside the 3D canvas shell that
 * announces the focused evidence object while the camera inspects it:
 *  - the PRIMARY player-facing text is the humanized name (from
 *    `evidenceLabelFor` — never a raw id, never a `proc.*` token);
 *  - the optional "Procedural Artifact" / "Validated Geometry" badges render
 *    ONLY from the honest, public-safe `FocusBadges` model;
 *  - an accessible Close button ("Close inspection") restores the exact
 *    previous state (the route wires the restore path — ESC does the same).
 *
 * This component is DOM-free at import time (it only renders plain text via
 * React's escaping), so it can be rendered headlessly in tests and mounted in
 * jsdom for interaction coverage (same style as evidencePanel.tsx).
 */
export interface FocusInspectionProps {
  /** Safe humanized evidence label (callers use evidenceLabelFor). */
  label: string;
  /** Public-safe badge flags derived by focusBadgesFor. */
  badges: FocusBadges;
  /** Closes the inspection and restores the world camera state. */
  onClose: () => void;
}

export default function FocusInspection({ label, badges, onClose }: FocusInspectionProps): ReactElement {
  const safeLabel = typeof label === "string" && label.trim() !== "" ? label.trim() : FALLBACK_EVIDENCE_LABEL;
  return (
    <div
      className="focus-inspection"
      data-testid="focus-inspection"
      role="region"
      aria-label={`Inspection: ${safeLabel}`}
    >
      <p className="focus-inspection-name" data-testid="focus-inspection-name">
        {safeLabel}
      </p>
      <span className="focus-badges">
        {badges.procedural && (
          <span className="focus-badge" data-testid="focus-badge-procedural">
            {PROCEDURAL_ARTIFACT_BADGE}
          </span>
        )}
        {badges.validatedGeometry && (
          <span className="focus-badge" data-testid="focus-badge-validated">
            {VALIDATED_GEOMETRY_BADGE}
          </span>
        )}
      </span>
      <button
        type="button"
        className="focus-inspection-close"
        data-testid="focus-close"
        onClick={onClose}
        aria-label="Close inspection"
        autoFocus
      >
        Close inspection
      </button>
    </div>
  );
}