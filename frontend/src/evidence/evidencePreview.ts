import type { InvestigationSceneModel } from "../scene/buildInvestigationScene";
import { evidenceLabelFor } from "../scene/objectLabel";

/**
 * Pure, deterministic evidence-panel context model (Phase 8_1 D).
 *
 * When the panel is opened from a world-object interaction, the route derives
 * a small OBJECT context from the PUBLIC scene model: the object's registry
 * label + registry color, and the objectId it originated from. Nothing here
 * can carry evidence content or hidden/truth data — the sources are the
 * application-owned asset registry (via the scene model) and the DTOs.
 */

/** Object context shown alongside the evidence record in the panel. */
export interface EvidencePreviewModel {
  objectId: string;
  /** Public registry label ("Kitchen knife", "Laptop", ...). */
  label: string;
  /** Application-owned registry hex color (preview swatch). */
  color: string;
}

/**
 * Derive the object context for the evidence panel from the scene model, or
 * null when the object is not part of the scene. The color/label come from
 * the registry only — never from the record payload. Phase 18B: the label
 * funnels through the semantic label path (`evidenceLabelFor`), so a
 * generated proc.* object never falls back to its raw id — the validated,
 * humanized canonicalName (or a safe "Evidence Object" fallback) is shown.
 */
export function evidencePreviewFor(
  model: InvestigationSceneModel | null,
  objectId: string | null,
): EvidencePreviewModel | null {
  if (model === null || objectId === null) return null;
  const worldObject = model.worldObjects.find((o) => o.objectId === objectId);
  if (worldObject === undefined) return null;
  return {
    objectId: worldObject.objectId,
    label: evidenceLabelFor(worldObject),
    color: worldObject.color,
  };
}

/**
 * Combined panel header: "Kitchen knife — <evidence title>". The label is
 * the public registry label only; hostile/empty labels fall back to the bare
 * title so nothing inert or misleading is ever shown.
 */
export function evidenceHeaderTitle(objectLabel: string | null | undefined, title: string): string {
  const label = typeof objectLabel === "string" && objectLabel.trim() !== "" ? objectLabel.trim() : null;
  return label === null ? title : `${label} — ${title}`;
}
