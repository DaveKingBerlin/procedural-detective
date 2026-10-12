import { useEffect, useRef } from "react";
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
 * Phase 42 ADV-06 — focus return: the Close button KEEPS `autoFocus` (the
 * previously-intended evidence-object activation behavior moves keyboard focus
 * into the surface), but the activating element is captured on the FIRST render
 * (before autoFocus moves it) and focus is restored to it when the surface
 * unmounts — the same focus-return contract the WitnessPanel provides. Now that
 * EVERY selectable object (including neutral table/lamp via the accessibility
 * list) opens this surface, closing an inspection can never strand keyboard
 * focus in a removed control.
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
  // Focus return: capture the activating control on the FIRST render (before
  // autoFocus moves focus to the Close button), then restore it when the
  // inspection surface unmounts — best-effort, never throwing (WitnessPanel
  // parity, Phase 42 ADV-06).
  const openerRef = useRef<HTMLElement | null>(null);
  if (openerRef.current === null && typeof document !== "undefined") {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }
  useEffect(() => {
    return () => {
      const opener = openerRef.current;
      if (opener !== null && typeof opener.focus === "function") {
        try {
          opener.focus();
        } catch {
          // Focus restoration is best-effort — it must never throw on unmount.
        }
      }
    };
  }, []);
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