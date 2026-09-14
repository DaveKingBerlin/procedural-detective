/**
 * Pure, deterministic mapping from the backend's sanitized generation
 * snapshot ({status, stage, progress}) to the player-friendly staged labels
 * (Phase 8 C, REQUIREMENTS 3.2).
 *
 * The backend stage strings are internal-safe text, so the client maps them
 * to the required friendly labels:
 *
 *   Creating case / Building world / Generating evidence /
 *   Checking consistency / Preparing investigation
 *
 * Label selection is deterministic: a known stage keyword wins, otherwise the
 * progress percentile band decides. The UI may render a short staged
 * animation while the POST /cases request is in flight, but it MUST settle on
 * the real returned status — this module never fabricates success: PUBLISHED
 * and FAILED are the only terminal outcomes, derived strictly from the
 * server-provided status string.
 */

export type StageOutcome = "running" | "published" | "failed";

export interface StageInfo {
  outcome: StageOutcome;
  label: string;
  /** 0..100 — the value shown in the progress bar. */
  progress: number;
}

/** Required friendly stage labels, in pipeline order (REQUIREMENTS 3.2). */
export const STAGE_LABELS: readonly string[] = [
  "Creating case",
  "Building world",
  "Generating evidence",
  "Checking consistency",
  "Preparing investigation",
];

/**
 * Progress bands (percentiles) that deterministically select a label when
 * the stage string does not map to a known keyword.
 */
const STAGE_BANDS: ReadonlyArray<{ min: number; max: number; labelIndex: number }> = [
  { min: 0, max: 24, labelIndex: 0 }, // Creating case
  { min: 25, max: 49, labelIndex: 1 }, // Building world
  { min: 50, max: 74, labelIndex: 2 }, // Generating evidence
  { min: 75, max: 89, labelIndex: 3 }, // Checking consistency
  { min: 90, max: 100, labelIndex: 4 }, // Preparing investigation
];

/** Clamp any progress value (null/NaN/garbage-safe) into the 0..100 range. */
export function clampProgress(progress: number | null): number {
  if (progress === null || typeof progress !== "number" || !Number.isFinite(progress)) return 0;
  return Math.max(0, Math.min(100, Math.round(progress)));
}

/**
 * Map a backend stage string to a stage label index (deterministic string
 * matching, lowercase-insensitive). Returns null when unknown — the caller
 * falls back to the progress band.
 */
export function stageLabelIndexForStage(stage: string | null): number | null {
  if (typeof stage !== "string" || stage === "") return null;
  const text = stage.toLowerCase();
  if (text.includes("creat") || text.includes("ground")) return 0;
  if (text.includes("world")) return 1;
  if (text.includes("evid")) return 2;
  if (text.includes("consisten")) return 3;
  if (text.includes("prepar") || text.includes("invest")) return 4;
  return null;
}

/**
 * The deterministic stage model for one snapshot. PUBLISHED always yields
 * "Preparing investigation" at 100% (terminal success); FAILED yields the
 * failed marker (the caller renders the failure view, never a success).
 */
export function friendlyGenerationStage(
  status: string | null,
  stage: string | null,
  progress: number | null,
): StageInfo {
  const value = clampProgress(progress);
  if (status === "FAILED") {
    return { outcome: "failed", label: "Generation failed", progress: value };
  }
  if (status === "PUBLISHED") {
    return { outcome: "published", label: STAGE_LABELS[4], progress: 100 };
  }
  const fromStage = stageLabelIndexForStage(stage);
  const labelIndex =
    fromStage ?? STAGE_BANDS.find((band) => value >= band.min && value <= band.max)!.labelIndex;
  return { outcome: "running", label: STAGE_LABELS[labelIndex], progress: value };
}

/**
 * Map a demo-flow progress phase to a StageInfo for the progress UI. The
 * pre-polling phases (session/create-case) are shown with the first friendly
 * labels while the request is in flight; once the server reports a progress
 * snapshot the real {status, stage, progress} wins. PUBLISHED/FAILED always
 * come from the server status — never fabricated here.
 */
export function stageInfoFromPhase(
  phase: string,
  status: string | null,
  stage: string | null,
  progress: number | null,
): StageInfo {
  switch (phase) {
    case "session":
      return friendlyGenerationStage("RUNNING", "create", 6);
    case "create-case":
      return friendlyGenerationStage("RUNNING", "create", 22);
    case "polling":
      return friendlyGenerationStage(status, stage, progress);
    case "playthrough":
      return friendlyGenerationStage("PUBLISHED", null, null);
    default:
      return friendlyGenerationStage("RUNNING", "create", 6);
  }
}