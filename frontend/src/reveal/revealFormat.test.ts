import { describe, expect, it } from "vitest";
import type { TimelineEntryDTO } from "../api/types";
import { makeCandidates, makeHostileReveal, makeRevealResponse, makeWrongReveal, makePartialReveal } from "../scene/testFixtures";
import { asText, formatCrimeTime, revealPresentation, sortTimeline } from "./revealFormat";

/**
 * Pure presentation-model coverage for the reveal screen (Phase 7 L/O):
 * time formatting (DTO-native, timezone-invariant), deterministic timeline
 * sorting, truth/player/indicator derivation for solved, wrong and partial
 * answers, candidate-name resolution and the fallback to raw ids, plus
 * hostile-string inertness at the model level.
 */

describe("formatCrimeTime", () => {
  it("extracts the authoritative HH:MM from a full ISO timestamp (no Date/timezone)", () => {
    expect(formatCrimeTime("2026-09-11T18:45:00+02:00")).toBe("18:45");
    expect(formatCrimeTime("2026-09-11T09:15:00Z")).toBe("09:15");
    expect(formatCrimeTime("2026-09-11T00:07:59+02:00")).toBe("00:07");
  });

  it("accepts a bare HH:MM:SS time (the accusation echo) and keeps only HH:MM", () => {
    expect(formatCrimeTime("18:45:00")).toBe("18:45");
    expect(formatCrimeTime("23:59:00")).toBe("23:59");
  });

  it("returns an empty string for unparseable values", () => {
    expect(formatCrimeTime("")).toBe("");
    expect(formatCrimeTime("not-a-time")).toBe("");
    expect(formatCrimeTime("2026-09-11")).toBe("");
  });
});

describe("sortTimeline", () => {
  it("sorts entries ascending by time without mutating the input", () => {
    const unsorted: TimelineEntryDTO[] = [
      { time: "2026-09-11T22:03:00+02:00", description: "third" },
      { time: "2026-09-11T21:38:00+02:00", description: "first" },
      { time: "2026-09-11T21:45:00+02:00", description: "second" },
    ];
    const sorted = sortTimeline(unsorted);
    expect(sorted.map((entry) => entry.description)).toEqual(["first", "second", "third"]);
    expect(unsorted[0].description).toBe("third"); // input untouched
  });

  it("is deterministic for identical inputs", () => {
    const a: TimelineEntryDTO[] = [
      { time: "2026-09-11T22:03:00+02:00", description: "b" },
      { time: "2026-09-11T21:38:00+02:00", description: "a" },
    ];
    expect(sortTimeline(a)).toEqual(sortTimeline([...a]));
  });
});

describe("revealPresentation (solved, DTO-driven)", () => {
  it("derives truth, submitted answers (via candidates), indicators and score from the DTO", () => {
    const reveal = makeRevealResponse();
    const model = revealPresentation(reveal, makeCandidates());

    expect(model.overall).toBe("solved");
    expect(model.score).toEqual({ correctDimensions: 4, totalDimensions: 4 });
    expect(model.truth).toEqual({
      murderer: "Ada Marsh",
      motive: "A dispute over money",
      weapon: "Kitchen knife",
      crimeTime: "21:45",
    });
    expect(model.dimensions.map((row) => row.dimension)).toEqual(["WHO", "WHY", "WEAPON", "WHEN"]);
    for (const row of model.dimensions) {
      expect(row.correct).toBe(true);
      expect(row.submitted).toBe(row.truth); // correct => submitted resolves to the same name
    }
    expect(model.explanation).toHaveLength(2);
    expect(model.explanation[0]).toEqual({
      title: "A bank transfer",
      point: "The transferred amount matches the embezzled total reported to the firm.",
    });
    // Timeline comes back sorted by time with display HH:MM.
    expect(model.timeline.map((entry) => entry.time)).toEqual(["21:38", "21:45", "22:03"]);
  });
});

describe("revealPresentation (wrong answers)", () => {
  it("resolves the player's wrong ids to their own names and marks every dimension incorrect", () => {
    const model = revealPresentation(makeWrongReveal(), makeCandidates());

    expect(model.overall).toBe("incorrect");
    expect(model.score.correctDimensions).toBe(0);
    expect(model.dimensions[0]).toMatchObject({ submitted: "Blake Niven", truth: "Ada Marsh", correct: false });
    expect(model.dimensions[1].correct).toBe(false);
    expect(model.dimensions[2].correct).toBe(false);
    expect(model.dimensions[3]).toMatchObject({ submitted: "18:20", truth: "21:45", correct: false });
  });

  it("mixed indicators reflect the per-dimension booleans (partial reveal)", () => {
    const model = revealPresentation(makePartialReveal(), makeCandidates());
    expect(model.dimensions.map((row) => row.correct)).toEqual([true, false, true, false]);
    expect(model.overall).toBe("incorrect");
    expect(model.score).toEqual({ correctDimensions: 2, totalDimensions: 4 });
  });
});

describe("revealPresentation fallbacks", () => {
  it("falls back to raw ids when candidates are unavailable", () => {
    const model = revealPresentation(makeWrongReveal(), null);
    expect(model.dimensions[0].submitted).toBe("suspect_beta");
    expect(model.dimensions[0].truth).toBe("Ada Marsh");
    expect(model.dimensions[2].submitted).toBe("weapon_beta");
  });

  it("falls back to the raw id for submitted ids NOT in the candidates (forged/unknown id)", () => {
    const model = revealPresentation(makeWrongReveal(), makeCandidates());
    // suspect_beta exists -> name. For a truly unknown id the DTO id is shown.
    const unknown = revealPresentation(
      { ...makeWrongReveal(), player: { accusation: { murdererId: "ghost_person", motiveId: "motive_gamma", weaponId: "weapon_beta", crimeTime: "18:20:00" } } },
      makeCandidates(),
    );
    expect(unknown.dimensions[0].submitted).toBe("ghost_person");
    expect(unknown.dimensions[0].correct).toBe(false);
    expect(model.dimensions[0].submitted).toBe("Blake Niven");
  });
});

describe("asText / hostile values (model-level inertness)", () => {
  it("keeps hostile and unicode strings as literal text", () => {
    const hostile = "<script>alert(1)</script>";
    expect(asText(hostile)).toBe(hostile);
    expect(asText({ nested: 1 })).toBe("");
    expect(asText(null)).toBe("");
    expect(asText(undefined)).toBe("");
    expect(asText(42)).toBe("42");
    expect(asText(true)).toBe("true");
  });

  it("hostile reveal values flow through untouched as plain strings (never executed)", () => {
    const model = revealPresentation(makeHostileReveal(), null);
    expect(model.truth.murderer).toContain("<script>alert(1)</script>");
    expect(model.explanation[0].title).toContain("ひらがな");
    expect(model.timeline[0].description).toContain("你好");
  });
});