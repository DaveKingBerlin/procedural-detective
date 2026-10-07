import { describe, expect, it } from "vitest";
import {
  acceptedScoringContains,
  anchorBareTimeTick,
  isBareTimeOfDay,
  isFullIsoTimestamp,
  parseIso8601,
  submittedTick,
} from "./savegameTime";

/**
 * Phase 32 — the local port of the server's time algebra
 * (`app/domain/time_interval.py::parse_iso8601` + the DEC-003 anchoring
 * arithmetic of `app/services/accusation.py`). Exact rule parity is the point.
 */

describe("parseIso8601 (exact backend grammar)", () => {
  it("parses Z and ±HH:MM offsets into {epochSeconds, utcOffsetMinutes}", () => {
    const z = parseIso8601("2026-09-11T20:17:00Z");
    expect(z.utcOffsetMinutes).toBe(0);
    expect(z.epochSeconds).toBe(Date.UTC(2026, 8, 11, 20, 17, 0) / 1000);

    const plus = parseIso8601("2026-09-11T22:17:00+02:00");
    expect(plus.utcOffsetMinutes).toBe(120);
    expect(plus.epochSeconds).toBe(z.epochSeconds);

    const minus = parseIso8601("2026-09-11T15:17:00-05:00");
    expect(minus.utcOffsetMinutes).toBe(-300);
    expect(minus.epochSeconds).toBe(z.epochSeconds);
  });

  it("accepts the colon-less offset form and floors fractional seconds", () => {
    expect(parseIso8601("2026-09-11T20:17:00+0200").epochSeconds).toBe(parseIso8601("2026-09-11T20:17:00+02:00").epochSeconds);
    expect(parseIso8601("2026-09-11T20:17:00.999Z").epochSeconds).toBe(parseIso8601("2026-09-11T20:17:00Z").epochSeconds);
  });

  it("rejects malformed/out-of-range values (fail closed)", () => {
    for (const bad of [
      "2026-13-01T00:00:00Z",
      "2026-02-30T00:00:00Z",
      "2026-09-11T24:00:00Z",
      "2026-09-11T22:60:00Z",
      "2026-09-11T22:17:00+24:00",
      "2026-09-11T22:17:00+02:60",
      "22:17:00",
      "",
      "not-a-time",
    ]) {
      expect(() => parseIso8601(bad)).toThrow();
    }
  });
});

describe("DEC-003 anchoring + scoring window (REQUIREMENTS 31.7)", () => {
  const canonical = "2026-09-11T22:17:00+02:00";

  it("A bare time-of-day anchors to the canonical DATE + OFFSET", () => {
    const bare = submittedTick("22:17:00", canonical);
    const full = submittedTick("2026-09-11T22:17:00+02:00", canonical);
    expect(bare).toBe(full);
    expect(bare).toBe(Date.UTC(2026, 8, 11, 20, 17, 0) / 1000);
  });

  it("an optional seconds field is part of the grammar (HH:MM == HH:MM:00)", () => {
    expect(submittedTick("22:17", canonical)).toBe(submittedTick("22:17:00", canonical));
    expect(isBareTimeOfDay("22:17:45")).toBe(true);
    expect(isBareTimeOfDay("25:99")).toBe(false);
    expect(isBareTimeOfDay("22-17")).toBe(false);
  });

  it("isFullIsoTimestamp only discriminates the 4-digit date prefix", () => {
    expect(isFullIsoTimestamp("2026-09-11T22:17:00+02:00")).toBe(true);
    expect(isFullIsoTimestamp("22:17:00")).toBe(false);
  });

  it("the accepted scoring set is [T−N, T+N+1) exactly", () => {
    const t = Date.UTC(2026, 8, 11, 20, 17, 0) / 1000;
    // T - 300 inclusive, T + 300 inclusive (the +N tick is inside), but T + 301 exclusive.
    expect(acceptedScoringContains(t, 300, t - 300)).toBe(true);
    expect(acceptedScoringContains(t, 300, t)).toBe(true);
    expect(acceptedScoringContains(t, 300, t + 300)).toBe(true);
    expect(acceptedScoringContains(t, 300, t - 301)).toBe(false);
    expect(acceptedScoringContains(t, 300, t + 301)).toBe(false);
    // Zero tolerance allows the canonical tick only.
    expect(acceptedScoringContains(t, 0, t)).toBe(true);
    expect(acceptedScoringContains(t, 0, t - 1)).toBe(false);
    expect(acceptedScoringContains(t, 0, t + 1)).toBe(false);
  });
});

describe("anchorBareTimeTick", () => {
  it("uses the canonical date literally (a different date is a DIFFERENT tick)", () => {
    const canonical = "2026-09-11T22:17:00+02:00";
    expect(anchorBareTimeTick("22:17:00", canonical)).toBe(submittedTick("22:17:00", canonical));
    expect(() => anchorBareTimeTick("25:00", canonical)).toThrow();
  });
});