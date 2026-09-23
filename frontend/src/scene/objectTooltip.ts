import type { InvestigationSceneModel } from "./buildInvestigationScene";
import { evidenceLabelFor } from "./objectLabel";

/**
 * Pure, deterministic DOM-hover tooltip model for the 3D scene (Phase 8_1 B1).
 *
 * The tooltip carries ONLY the object's semantic HUMAN label — the public
 * registry label for catalog objects, the humanized (validated) canonicalName
 * for generated proc.* objects (Phase 19E), or the app's safe "Evidence
 * Object" fallback — never evidence titles, never case content, never any
 * hidden/truth data, never a raw objectId/assetId/proc.* token. Security
 * guarantee: the payload type simply has no room for anything but the label.
 */

/** The ONLY data a hover tooltip may carry (public semantic label). */
export interface ObjectTooltipModel {
  objectId: string;
  label: string;
}

/**
 * Semantic human label for a world object, or null when the object is unknown
 * to the scene model (hover never fires for those anyway).
 */
export function tooltipLabelFor(model: InvestigationSceneModel | null, objectId: string | null): string | null {
  if (model === null || objectId === null) return null;
  const worldObject = model.worldObjects.find((o) => o.objectId === objectId);
  return worldObject ? evidenceLabelFor(worldObject) : null;
}

/**
 * Deterministic tooltip state transition for a hover callback. Returns null
 * whenever there is nothing public to show (hover ended or the object has no
 * registry label) — the payload never contains evidence/case content.
 */
export function tooltipForHover(
  model: InvestigationSceneModel | null,
  objectId: string | null,
): ObjectTooltipModel | null {
  const label = tooltipLabelFor(model, objectId);
  if (objectId === null || label === null) return null;
  return { objectId, label };
}
