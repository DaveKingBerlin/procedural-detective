import type { GenerationModeId } from "../api/types";

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
 *
 * Phase 16.2 Track B (§21) — when the journey runs in **local** mode
 * (selector value `local`, travelled via `pd_generation_mode`) the SAME
 * deterministic stage/progress inputs drive the Local-AI label sequence
 * (Understanding the case… → … → Preparing the investigation…) through the
 * pure {@link localProgressLabels} mapper. Demo/unset mode keeps the generic
 * labels above, byte-for-byte unchanged.
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

/**
 * Phase 16.2 §21 — the Local-AI progress label sequence, EXACTLY as specced
 * and in order. Shown on /generating ONLY while the journey runs in local
 * mode; demo mode keeps {@link STAGE_LABELS}. These are frozen app-authored
 * strings — never server text, so nothing from a DTO can reach them.
 */
export const LOCAL_AI_STAGE_LABELS: readonly string[] = [
  "Understanding the case…",
  "Creating suspects…",
  "Planting evidence…",
  "Building the crime scene…",
  "Creating missing objects…",
  "Verifying the solution…",
  "Preparing the investigation…",
];

/**
 * Local-AI progress bands (percentiles): the 7-label sequence spread across
 * the same 0..100 range the demo bands use. Deterministically selects a
 * Local-AI label when no stage keyword maps.
 */
const LOCAL_AI_STAGE_BANDS: ReadonlyArray<{ min: number; max: number; labelIndex: number }> = [
  { min: 0, max: 14, labelIndex: 0 }, // Understanding the case…
  { min: 15, max: 29, labelIndex: 1 }, // Creating suspects…
  { min: 30, max: 44, labelIndex: 2 }, // Planting evidence…
  { min: 45, max: 59, labelIndex: 3 }, // Building the crime scene…
  { min: 60, max: 74, labelIndex: 4 }, // Creating missing objects…
  { min: 75, max: 89, labelIndex: 5 }, // Verifying the solution…
  { min: 90, max: 100, labelIndex: 6 }, // Preparing the investigation…
];

/**
 * Map the generic stage-keyword index (see {@link stageLabelIndexForStage})
 * to the semantically matching Local-AI label index. Entries that have no
 * local counterpart (Creating suspects… / Creating missing objects… are
 * reached via the progress bands) are `null` and fall back to the band.
 * Mapping order mirrors the demo keyword order (create/ground, world, evid,
 * consisten, prepar/invest).
 */
const LOCAL_LABEL_INDEX_FOR_STAGE_INDEX: ReadonlyArray<number | null> = [0, 3, 2, 5, 6];

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
 * Phase 16.2 §21 — the pure Local-AI label mapper. Given the SAME sanitized
 * stage/progress inputs the demo path consumes, it deterministically selects
 * one of the seven Local-AI labels (stage keyword wins, otherwise the local
 * percentile band) and returns the running {@link StageInfo}. PUBLISHED and
 * FAILED are terminal outcomes owned by {@link stageInfoFromPhase} (status is
 * not part of this signature) so a "running" result can never be mistaken for
 * a fabricated success.
 */
export function localProgressLabels(stage: string | null, progress: number | null): StageInfo {
  const value = clampProgress(progress);
  const stageIndex = stageLabelIndexForStage(stage);
  const fromStage = stageIndex === null ? null : LOCAL_LABEL_INDEX_FOR_STAGE_INDEX[stageIndex];
  const labelIndex =
    fromStage ??
    LOCAL_AI_STAGE_BANDS.find((band) => value >= band.min && value <= band.max)!.labelIndex;
  return { outcome: "running", label: LOCAL_AI_STAGE_LABELS[labelIndex], progress: value };
}

/**
 * Local-mode terminal handling: PUBLISHED always settles on the final Local-AI
 * label ("Preparing the investigation…") at 100%; FAILED settles on the failed
 * marker — never a fabricated success (server status is authoritative).
 */
function localAiStageInfo(status: string | null, stage: string | null, progress: number | null): StageInfo {
  const value = clampProgress(progress);
  if (status === "FAILED") {
    return { outcome: "failed", label: "Generation failed", progress: value };
  }
  if (status === "PUBLISHED") {
    return { outcome: "published", label: LOCAL_AI_STAGE_LABELS[6], progress: 100 };
  }
  return localProgressLabels(stage, progress);
}

/**
 * Map a demo-flow progress phase to a StageInfo for the progress UI. The
 * pre-polling phases (session/create-case) are shown with the first friendly
 * labels while the request is in flight; once the server reports a progress
 * snapshot the real {status, stage, progress} wins. PUBLISHED/FAILED always
 * come from the server status — never fabricated here.
 *
 * Phase 16.2 §21 — `mode` selects the label sequence: `local` drives the
 * seven Local-AI labels through {@link localProgressLabels}; demo/unset/null
 * keeps the generic staged labels byte-for-byte unchanged.
 */
export function stageInfoFromPhase(
  phase: string,
  status: string | null,
  stage: string | null,
  progress: number | null,
  mode: GenerationModeId | null = null,
): StageInfo {
  if (mode === "local") {
    switch (phase) {
      case "session":
        return localProgressLabels("create", 6);
      case "create-case":
        return localProgressLabels("create", 22);
      case "polling":
        return localAiStageInfo(status, stage, progress);
      case "playthrough":
        return localAiStageInfo("PUBLISHED", null, null);
      default:
        return localProgressLabels("create", 6);
    }
  }
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