import { describe, expect, it } from "vitest";
import {
  LOCAL_AI_STAGE_LABELS,
  STAGE_LABELS,
  clampProgress,
  friendlyGenerationStage,
  localProgressLabels,
  stageInfoFromPhase,
  stageLabelIndexForStage,
} from "./generationProgress";

/**
 * Deterministic generation-progress stage mapping (Phase 8 C): each friendly
 * label for each state/band, PUBLISHED -> enter (published), FAILED ->
 * fail, garbage-safe progress clamping.
 *
 * Phase 16.2 §21: local mode (`mode === "local"`) maps the SAME deterministic
 * stage/progress inputs to the seven Local-AI labels; demo/unset keeps the
 * generic staged labels byte-for-byte unchanged.
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

describe("LOCAL_AI_STAGE_LABELS — the frozen §21 sequence", () => {
  it("is exactly the §21 Local-AI sequence, in order", () => {
    expect(LOCAL_AI_STAGE_LABELS).toEqual([
      "Understanding the case…",
      "Creating suspects…",
      "Planting evidence…",
      "Building the crime scene…",
      "Creating missing objects…",
      "Verifying the solution…",
      "Preparing the investigation…",
    ]);
  });

  it("is frozen app-authored text — no URL/credential/prompt token can ever reach the UI", () => {
    for (const label of LOCAL_AI_STAGE_LABELS) {
      const lowered = label.toLowerCase();
      for (const token of ["http", "https", "data:", "file:", "javascript:", "apiKey", "prompt="]) {
        expect(lowered).not.toContain(token);
      }
    }
  });
});

describe("localProgressLabels — §21 pure band mapping (mode == local)", () => {
  it("maps every percentile band to its Local-AI label (min and max)", () => {
    const cases: Array<[number, number, string]> = [
      [0, 14, "Understanding the case…"],
      [15, 29, "Creating suspects…"],
      [30, 44, "Planting evidence…"],
      [45, 59, "Building the crime scene…"],
      [60, 74, "Creating missing objects…"],
      [75, 89, "Verifying the solution…"],
      [90, 100, "Preparing the investigation…"],
    ];
    for (const [min, max, label] of cases) {
      expect(localProgressLabels(null, min).label).toBe(label);
      expect(localProgressLabels(null, max).label).toBe(label);
    }
  });

  it("maps every band boundary exactly once (no gaps, no overlaps)", () => {
    const seen = new Set<string>();
    for (let value = 0; value <= 100; value++) {
      seen.add(localProgressLabels(null, value).label);
    }
    expect(Array.from(seen)).toEqual(Array.from(LOCAL_AI_STAGE_LABELS));
  });

  it("prefers a known stage keyword over the band, mapped semantically", () => {
    expect(localProgressLabels("creating_case_truth", 5).label).toBe("Understanding the case…");
    expect(localProgressLabels("building_world", 5).label).toBe("Building the crime scene…");
    expect(localProgressLabels("generating_evidence", 5).label).toBe("Planting evidence…");
    expect(localProgressLabels("checking_consistency", 5).label).toBe("Verifying the solution…");
    expect(localProgressLabels("preparing_investigation", 5).label).toBe(
      "Preparing the investigation…",
    );
  });

  it("returns a running StageInfo with the clamped progress", () => {
    const info = localProgressLabels("unknown_stage", 42.6);
    expect(info.outcome).toBe("running");
    expect(info.progress).toBe(43);
    expect(info.label).toBe("Planting evidence…");
    const garbage = localProgressLabels(null, Number.NaN);
    expect(garbage.outcome).toBe("running");
    expect(garbage.progress).toBe(0);
  });
});

describe("stageInfoFromPhase — §21 mode-aware label selection", () => {
  it("uses the Local-AI labels for every phase when mode is local", () => {
    expect(stageInfoFromPhase("session", null, null, null, "local").label).toBe(
      "Understanding the case…",
    );
    expect(stageInfoFromPhase("create-case", null, null, null, "local").label).toBe(
      "Understanding the case…",
    );
    expect(
      stageInfoFromPhase("polling", "RUNNING", "world_generation", 50, "local").label,
    ).toBe("Building the crime scene…");
    expect(
      stageInfoFromPhase("polling", "RUNNING", null, 80, "local").label,
    ).toBe("Verifying the solution…");
    expect(stageInfoFromPhase("playthrough", null, null, null, "local").outcome).toBe("published");
    expect(stageInfoFromPhase("playthrough", null, null, null, "local").label).toBe(
      "Preparing the investigation…",
    );
  });

  it("settles local mode on the final Local-AI label at 100 on PUBLISHED (server truth)", () => {
    const info = stageInfoFromPhase("polling", "PUBLISHED", null, 40, "local");
    expect(info.outcome).toBe("published");
    expect(info.label).toBe("Preparing the investigation…");
    expect(info.progress).toBe(100);
  });

  it("never fabricates success in local mode — FAILED stays failed", () => {
    const info = stageInfoFromPhase("polling", "FAILED", null, 99, "local");
    expect(info.outcome).toBe("failed");
    expect(info.label).toBe("Generation failed");
  });

  it("keeps the generic demo labels unchanged for mode demo and null", () => {
    for (const mode of [null, "demo"] as const) {
      expect(stageInfoFromPhase("session", null, null, null, mode).label).toBe("Creating case");
      expect(
        stageInfoFromPhase("polling", "RUNNING", "world_generation", 30, mode).label,
      ).toBe("Building world");
      expect(stageInfoFromPhase("playthrough", null, null, null, mode).label).toBe(
        "Preparing investigation",
      );
    }
  });
});