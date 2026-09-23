import type { InvestigationSceneModel, SceneWorldObject } from "./buildInvestigationScene";
import { semanticLabelOrNull } from "./objectLabel";

/**
 * Phase 15 Track B — discovered-evidence caption model (pure, deterministic).
 *
 * A small floating DOM caption is shown in the 3D scene ONLY for DISCOVERED
 * evidence objects (the object's `discovered` flag is server-authoritative).
 * The caption text is app-authored only:
 *   1. the evidence title from a READ record (the player already knows it from
 *      the evidence panel — no truth leak), else
 *   2. the object's SEMANTIC human label (the same label the tooltip and the
 *      object list show — the public registry label, or the humanized
 *      canonicalName of a generated proc.* object, Phase 19E), else
 *   3. nothing: undiscovered objects, unknown assets and label-less objects
 *      NEVER get a caption, so no hidden/truth/server-free-text can appear.
 */

export interface ObjectCaptionModel {
  objectId: string;
  /** App-authored plain text (registry label or read-record title). */
  text: string;
}

/**
 * Derive the discovered captions from the scene model + the discovered titles
 * map. `discoveredTitles` maps evidenceId -> the title the player has already
 * seen (from read records; falls back to the registry label in the summary).
 * Output is sorted by objectId (stable regardless of input order).
 */
export function discoveredCaptionsForWorld(
  worldObjects: ReadonlyArray<SceneWorldObject>,
  discoveredTitles: ReadonlyMap<string, string>,
): ObjectCaptionModel[] {
  const captions: ObjectCaptionModel[] = [];
  for (const obj of worldObjects) {
    if (!obj.discovered || obj.evidenceId === null) continue;
    const text =
      discoveredTitles.get(obj.evidenceId) ??
      semanticLabelOrNull(obj);
    if (text === null) continue; // unknown/label-less discovered objects stay silent
    captions.push({ objectId: obj.objectId, text });
  }
  return captions.sort((a, b) => (a.objectId < b.objectId ? -1 : a.objectId > b.objectId ? 1 : 0));
}

/**
 * Discovered-caption lookup helper for the scene route: which world objects
 * are allowed to carry a caption today, and with which app-authored text.
 */
export function captionsForSceneModel(
  model: InvestigationSceneModel | null,
  discoveredTitles: ReadonlyMap<string, string>,
): ObjectCaptionModel[] {
  if (model === null) return [];
  return discoveredCaptionsForWorld(model.worldObjects, discoveredTitles);
}