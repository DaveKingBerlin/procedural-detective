import { describe, expect, it } from "vitest";
import canonical from "./fixtures/v1_demo_apartment.pdcase.json";
import { parseSavegameV1, utf8ByteLength } from "./savegameV1";
import { evaluateReplayAccusation, timelineOf } from "./replayTruth";

/**
 * Phase 32 — LOCAL solution-evaluation port tests.
 *
 * The evaluation rules must be the SAME as the server (backend
 * `evaluate_accusation` / `submitted_tick` / `accepted_scoring_time_set`):
 * id equality for WHO/WHY/WEAPON + the exact half-open `[T−N, T+N+1)` window
 * for time with DEC-003 anchoring of a bare time-of-day onto the canonical
 * crime date + timezone offset.
 */

const CANONICAL_TEXT: string = JSON.stringify(canonical);

function load(): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(CANONICAL_TEXT, utf8ByteLength(CANONICAL_TEXT));
}

function accusation(overrides: Partial<{ murdererId: string; motiveId: string; weaponId: string; crimeTime: string }> = {}) {
  return {
    murdererId: "thomas_reed",
    motiveId: "cover_up_embezzlement",
    weaponId: "kitchen_knife",
    crimeTime: "22:17:00",
    ...overrides,
  };
}

describe("replay truth evaluation — canonical demo apartment", () => {
  it("solves the canonical case with the exact truth + anchoring (22:17:00)", () => {
    const definition = load();
    const result = evaluateReplayAccusation(definition.replayTruth, accusation());
    expect(result.murdererCorrect).toBe(true);
    expect(result.motiveCorrect).toBe(true);
    expect(result.weaponCorrect).toBe(true);
    expect(result.timeCorrect).toBe(true);
  });

  it("accepts any wall clock inside the accepted window [T−N, T+N+1)", () => {
    const definition = load();
    // Canonical T = 22:17 (UTC+02:00); tolerance 300s. 22:12 is T−300
    // (inclusive). 22:22 is T+300 (the half-open window includes it).
    expect(
      evaluateReplayAccusation(definition.replayTruth, accusation({ crimeTime: "22:12:00" })).timeCorrect,
    ).toBe(true);
    expect(
      evaluateReplayAccusation(definition.replayTruth, accusation({ crimeTime: "22:22:00" })).timeCorrect,
    ).toBe(true);
  });

  it("CLOSED window: T−301 is OUTSIDE [T−300, T+301)", () => {
    const definition = load();
    expect(
      evaluateReplayAccusation(definition.replayTruth, accusation({ crimeTime: "22:11:59" })).timeCorrect,
    ).toBe(false);
  });

  it("anchors a bare time onto the canonical date + canonical offset", () => {
    const definition = load();
    // Bare "21:15:00" anchored to 2026-09-11 +02:00 == 2026-09-11T19:15:00Z.
    // T (2026-09-11T20:17:00Z) minus a long way -> NOT in the window.
    expect(
      evaluateReplayAccusation(definition.replayTruth, accusation({ crimeTime: "21:15:00" })).timeCorrect,
    ).toBe(false);
    // The same bare time expressed as a FULL ISO with the very offset the
    // anchoring uses is equivalent by construction.
    expect(
      evaluateReplayAccusation(definition.replayTruth, accusation({ crimeTime: "2026-09-11T22:17:00+02:00" }))
        .timeCorrect,
    ).toBe(true);
  });

  it("empty seconds-field anchoring behaves like :00 (bare grammar allows HH:MM)", () => {
    const definition = load();
    expect(
      evaluateReplayAccusation(definition.replayTruth, accusation({ crimeTime: "22:17" })).timeCorrect,
    ).toBe(true);
  });

  it("WHO/WHY/WEAPON use exact id equality (different case -> incorrect)", () => {
    const definition = load();
    const result = evaluateReplayAccusation(
      definition.replayTruth,
      accusation({ murdererId: "anna_karlsson", motiveId: "robbery_gone_wrong", weaponId: "scissors" }),
    );
    expect(result.murdererCorrect).toBe(false);
    expect(result.motiveCorrect).toBe(false);
    expect(result.weaponCorrect).toBe(false);
  });

  it("a schema-valid edited crimeTime that cannot parse is never correct", () => {
    const definition = load();
    const result = evaluateReplayAccusation(definition.replayTruth, accusation({ crimeTime: "not-a-time" }));
    expect(result.timeCorrect).toBe(false);
  });

  it("an unparseable edited canonical is REJECTED at import (fail closed, no partial truth)", () => {
    const doc = JSON.parse(CANONICAL_TEXT);
    doc.case.replayTruth.crimeTime = "garbage";
    expect(() => parseSavegameV1(JSON.stringify(doc), 0)).toThrow();
  });
});

describe("replay truth — timeline derivation (deterministic, bounded)", () => {
  it("builds a bounded, sorted timeline from the saved public records", () => {
    const definition = load();
    const timeline = timelineOf(definition.evidence);
    expect(timeline.length).toBeGreaterThan(0);
    expect(timeline.length).toBeLessThanOrEqual(12);
    // Sorted ascending by time.
    const epochs = timeline.map((entry) => Date.parse(entry.time));
    expect(epochs).toEqual([...epochs].sort((a, b) => a - b));
    // Descriptions come from public titles only.
    const titles = new Set(definition.evidence.map((record) => record.title));
    for (const entry of timeline) {
      expect(titles.has(entry.description)).toBe(true);
    }
  });

  it("never invents timestamps for records without parseable times", () => {
    const doc = JSON.parse(CANONICAL_TEXT);
    for (const record of doc.case.evidence) {
      record.content.entries = undefined;
      record.content.events = undefined;
    }
    const bare = parseSavegameV1(JSON.stringify(doc), utf8ByteLength(JSON.stringify(doc)));
    expect(timelineOf(bare.evidence)).toEqual([]);
  });
});