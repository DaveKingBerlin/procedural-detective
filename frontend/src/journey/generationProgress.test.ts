import { describe, expect, it } from "vitest";
import {
  STAGE_LABELS,
  clampProgress,
  friendlyGenerationStage,
  stageInfoFromPhase,
  stageLabelIndexForStage,
} from "./generationProgress";

/**
 * Deterministic generation-progress stage mapping (Phase 8 C): each friendly
 * label for each state/band, PUBLISHED -> enter (published), FAILED ->
 * fail, garbage-safe progress clamping.
 */

describe("clampProgress", () => {
  it("clamps out-of-range and garbage values to 0..100", () => {
    expect(clampProgress(null)).toBe(0);
    expect(clampProgress(Number.NaN)).toBe(0);
    expect(clampProgress(-5)).toBe(0);
    expect(clampProgress(101)).toBe(100);
    expect(clampProgress(42.4)).toBe(42);
    expect(clampProgress(42.6)).toBe(43);
  });
});

describe("stageLabelIndexForStage", () => {
  it("maps each backend stage keyword to the friendly label index", () => {
    expect(stageLabelIndexForStage("creating_case_truth")).toBe(0);
    expect(stageLabelIndexForStage("ground_truth_created")).toBe(0);
    expect(stageLabelIndexForStage("building_world")).toBe(1);
    expect(stageLabelIndexForStage("generating_evidence")).toBe(2);
    expect(stageLabelIndexForStage("checking_consistency")).toBe(3);
    expect(stageLabelIndexForStage("preparing_investigation")).toBe(4);
    expect(stageLabelIndexForStage("finalize_investigation")).toBe(4);
    expect(stageLabelIndexForStage(null)).toBeNull();
    expect(stageLabelIndexForStage("")).toBeNull();
    expect(stageLabelIndexForStage("some_unknown_stage")).toBeNull();
  });
});

describe("friendlyGenerationStage — progress bands pick the friendly label", () => {
  it("maps every band to its required label", () => {
    const cases: Array<[number, number, string]> = [
      [0, 24, "Creating case"],
      [25, 49, "Building world"],
      [50, 74, "Generating evidence"],
      [75, 89, "Checking consistency"],
      [90, 100, "Preparing investigation"],
    ];
    for (const [min, max, label] of cases) {
      expect(friendlyGenerationStage("RUNNING", null, min).label).toBe(label);
      expect(friendlyGenerationStage("RUNNING", null, max).label).toBe(label);
    }
  });

  it("uses the progress value for the running state's bar", () => {
    expect(friendlyGenerationStage("RUNNING", null, 45).progress).toBe(45);
    expect(friendlyGenerationStage("RUNNING", null, null).progress).toBe(0);
  });

  it("prefers a known stage keyword over the progress band", () => {
    const info = friendlyGenerationStage("RUNNING", "world_generation", 10);
    expect(info.label).toBe("Building world");
  });
});

describe("friendlyGenerationStage — terminal states", () => {
  it("PUBLISHED resolves to 'Preparing investigation' at 100 and the published outcome", () => {
    const info = friendlyGenerationStage("PUBLISHED", null, 40);
    expect(info.outcome).toBe("published");
    expect(info.label).toBe(STAGE_LABELS[4]);
    expect(info.progress).toBe(100);
  });

  it("FAILED resolves to the failed outcome (never a success)", () => {
    const info = friendlyGenerationStage("FAILED", null, 99);
    expect(info.outcome).toBe("failed");
    expect(info.label).toBe("Generation failed");
  });
});

describe("stageInfoFromPhase — pre-poll phases animate with the friendly labels", () => {
  it("maps the journey phases to deterministic stage info", () => {
    expect(stageInfoFromPhase("session", null, null, null).label).toBe("Creating case");
    expect(stageInfoFromPhase("create-case", null, null, null).label).toBe("Creating case");
    expect(stageInfoFromPhase("polling", "RUNNING", "checking_consistency", 80).label).toBe(
      "Checking consistency",
    );
    expect(stageInfoFromPhase("polling", "PUBLISHED", null, 100).outcome).toBe("published");
    expect(stageInfoFromPhase("playthrough", null, null, null).outcome).toBe("published");
    expect(stageInfoFromPhase("playthrough", null, null, null).label).toBe("Preparing investigation");
  });
});