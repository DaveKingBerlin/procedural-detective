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
 * ADV-209 — control/format characters that must never reach focus markup:
 * C0 controls (\u0000-\u001f) and DEL (\u007f) as before, PLUS C1 controls
 * (\u0080-\u009f, incl. \u0085 NEL), the unicode line/paragraph separators
 * (\u2028/\u2029), soft hyphen (\u00ad) and the zero-width / bidi-format
 * characters (\u200b-\u200f, \u202a-\u202e, \u2060-\u206f, \ufeff). All are
 * replaced with a regular space (readable), never rendered raw.
 */
const CONTROL_OR_FORMAT_CHARS = /[\u0000-\u001f\u007f-\u009f\u00ad\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u206f\ufeff]/g;

/**
 * ADV-209 — a `proc.` token class (the non-space run starting at `proc.`,
 * case-insensitive) is NEVER allowed to survive in humanized output: a
 * server-authored value such as `proc.decor.abc123` must not echo an id/hash
 * into focus markup. The token is replaced with the safe human fallback.
 */
const PROC_TOKEN_CLASS = /proc\.\S*/gi;

function collapseAndTrim(value: string): string {
  return value
    .replace(CONTROL_OR_FORMAT_CHARS, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Bounded, sanitized humanization of a server-authored canonicalName:
 * strips control/format characters, collapses whitespace, trims, replaces any
 * `proc.` token class with the safe fallback and caps the length at
 * {@link LABEL_MAX_LENGTH}. Returns null when there is nothing safe to show
 * (so callers fall back).
 */
export function humanizeCanonicalName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const cleaned = collapseAndTrim(value);
  if (cleaned === "") return null;
  // ADV-209: the "proc.* never displayed" guarantee holds for the NAME field
  // too — a hostile canonicalName that IS a `proc.` token becomes the safe
  // human fallback, never the raw token.
  return cleaned.replace(PROC_TOKEN_CLASS, FALLBACK_EVIDENCE_LABEL).slice(0, LABEL_MAX_LENGTH);
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

/** The minimal label-bearing surface the semantic label path reads.
 *  (SceneWorldObject satisfies it; so do slimmer structural fixtures in tests.) */
export interface SemanticLabelSource {
  label: string | null;
  generated?: { canonicalName?: string } | null;
}

/**
 * The semantic human label of a world object when a NAMEABLE source exists:
 *  - the established public registry label when one exists (catalog objects),
 *  - otherwise a sanitized humanization of generated.canonicalName.
 * Returns null when neither source exists (unknown/label-less objects), so
 * callers with a dedicated "no nameable object" fallback (e.g. the Phase 19C
 * "Nothing relevant was found here." toast) can keep it. Never returns a raw
 * assetId, objectId or "proc.*" token.
 */
export function semanticLabelOrNull(obj: SemanticLabelSource): string | null {
  const catalogLabel = typeof obj.label === "string" ? obj.label.trim() : "";
  if (catalogLabel !== "") return humanizeCanonicalName(catalogLabel);
  return humanizeCanonicalName(obj.generated?.canonicalName);
}

/**
 * The primary player-facing label of a world object:
 *  - the established public registry label when one exists (catalog objects) —
 *    sanitized and BOUNDED like every other branch (ADV-209: a 2400-char
 *    label must never reach the focus aria-label);
 *  - otherwise a sanitized humanization of generated.canonicalName;
 *  - otherwise the safe "Evidence Object" fallback.
 * Never returns a raw assetId, objectId or "proc.*" token.
 */
export function evidenceLabelFor(obj: SemanticLabelSource): string {
  return semanticLabelOrNull(obj) ?? FALLBACK_EVIDENCE_LABEL;
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