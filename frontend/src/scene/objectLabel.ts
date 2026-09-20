import type { GeneratedAssetDefinition } from "../api/types";
import type { SceneWorldObject } from "./buildInvestigationScene";

/**
 * Phase 18B — semantic evidence label + focus badges (pure, deterministic).
 *
 * This is the ONE place focus-mode text is derived. It reuses the established
 * semantic label path (the public registry label from the scene model) and
 * extends it to generated `proc.*` objects whose validated, server-authored
 * `generated.canonicalName` (e.g. "Bronze Ceremonial Ice Pick", ≤ 80 chars)
 * is the ONLY name-bearing source that may be humanized:
 *
 *  - catalog objects keep their EXISTING public registry label (unchanged);
 *  - generated proc.* objects get a BOUNDED, sanitized humanization of
 *    `generated.canonicalName`; the raw `assetId`, the `proc.decor.<hash>`
 *    token and the world objectId are NEVER echoed;
 *  - every other object falls back to a safe human string ("Evidence Object")
 *    so there is never a "no label" gray-eye failure.
 *
 * Badges: "Procedural Artifact" and "Validated Geometry" appear if and ONLY
 * IF the object is procedural (assetId starts with `proc.` AND it carries a
 * validated generated definition). No hidden truth, no weapon reveal, no
 * solver candidates, no provider internals ever enter this module.
 */

/** Never-leak fallback human label for unknown / label-less focus objects. */
export const FALLBACK_EVIDENCE_LABEL = "Evidence Object";

/** Fixed badge text shown only for procedural artifacts. */
export const PROCEDURAL_ARTIFACT_BADGE = "Procedural Artifact";

/** Fixed badge text shown only for the same procedural condition. */
export const VALIDATED_GEOMETRY_BADGE = "Validated Geometry";

/** Bound on the humanized canonical name (backend cap mirror: ≤ 80 chars). */
export const LABEL_MAX_LENGTH = 80;

/**
 * Bounded, sanitized humanization of a server-authored canonicalName:
 * strips control characters, collapses whitespace, trims and caps the length.
 * Returns null when there is nothing safe to show (so callers fall back).
 */
export function humanizeCanonicalName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const cleaned = value
    .replace(/[\u0000-\u001f\u007f]/g, " ") // control chars -> space (never rendered raw)
    .replace(/[\t\r\n ]+/g, " ")
    .trim();
  if (cleaned === "") return null;
  return cleaned.slice(0, LABEL_MAX_LENGTH);
}

/**
 * True only for procedural objects: assetId starts with `proc.` AND a valid
 * generated definition is present (the exact "Procedural Artifact" condition).
 */
export function isProceduralArtifact(obj: SceneWorldObject): boolean {
  return (
    typeof obj.assetId === "string" &&
    obj.assetId.startsWith("proc.") &&
    obj.generated !== null &&
    obj.generated !== undefined
  );
}

/**
 * The primary player-facing label of a world object:
 *  - the established public registry label when one exists (catalog objects);
 *  - otherwise a sanitized humanization of generated.canonicalName;
 *  - otherwise the safe "Evidence Object" fallback.
 * Never returns a raw assetId, objectId or "proc.*" token.
 */
export function evidenceLabelFor(obj: SceneWorldObject): string {
  const catalogLabel = typeof obj.label === "string" ? obj.label.trim() : "";
  if (catalogLabel !== "") return catalogLabel;
  const humanized = humanizeCanonicalName(obj.generated?.canonicalName);
  if (humanized !== null) return humanized;
  return FALLBACK_EVIDENCE_LABEL;
}

/** The honest, public-safe focus badges for one world object. */
export interface FocusBadges {
  /** "Procedural Artifact" — iff the object is a validated procedural asset. */
  procedural: boolean;
  /** "Validated Geometry" — iff the same procedural condition holds. */
  validatedGeometry: boolean;
}

/**
 * Deterministic badge derivation. Both badges share ONE honest condition
 * (isProceduralArtifact); nothing else is ever shown.
 */
export function focusBadgesFor(obj: SceneWorldObject): FocusBadges {
  const procedural = isProceduralArtifact(obj);
  return { procedural, validatedGeometry: procedural };
}

/** Type guard used by the focus UI to access only the validated name source. */
export function isGeneratedDefinition(value: GeneratedAssetDefinition | null): value is GeneratedAssetDefinition {
  return value !== null && value !== undefined;
}