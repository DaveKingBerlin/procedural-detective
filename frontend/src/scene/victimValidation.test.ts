import { describe, expect, it } from "vitest";
import {
  MAX_VICTIM_AGE,
  MAX_VICTIM_TEXT_LENGTH,
  MIN_VICTIM_AGE,
  parseInvestigationBootstrap,
  parseVictimProfile,
} from "./validation";
import { makeBootstrap, makeVictimProfile } from "./testFixtures";

/**
 * Phase 47 — defensive victim-profile parsing at the trust boundary.
 *
 * The optional `victim` block must never crash the bootstrap and must never
 * emit `undefined`/`NaN`; malformed values degrade to null (the UI then omits
 * the row). The scene/candidate parsing stays strict and unchanged.
 */

describe("parseVictimProfile — valid input", () => {
  it("parses a complete player-safe profile", () => {
    const parsed = parseVictimProfile(makeVictimProfile());
    expect(parsed).toEqual(makeVictimProfile());
  });

  it("normalizes the gender token case-insensitively", () => {
    expect(parseVictimProfile(makeVictimProfile({ gender: "Female" }))?.gender).toBe("female");
    expect(parseVictimProfile(makeVictimProfile({ gender: "MALE" }))?.gender).toBe("male");
  });
});

describe("parseVictimProfile — defensive degradation", () => {
  it("returns null for non-objects and a missing/empty name", () => {
    expect(parseVictimProfile(null)).toBeNull();
    expect(parseVictimProfile("nope")).toBeNull();
    expect(parseVictimProfile([])).toBeNull();
    expect(parseVictimProfile({})).toBeNull();
    expect(parseVictimProfile({ name: "   " })).toBeNull();
    expect(parseVictimProfile({ name: 42 })).toBeNull();
  });

  it("degrades a malformed age to null (never NaN / negative / fractional)", () => {
    for (const age of ["41", Number.NaN, Number.POSITIVE_INFINITY, -5, 1.5, 999] as unknown[]) {
      expect(parseVictimProfile(makeVictimProfile({ age: age as number | null }))?.age).toBeNull();
    }
    // DEF-120 — the backend contract is [1, 120]: 0 and >120 degrade to null.
    expect(parseVictimProfile(makeVictimProfile({ age: 0 }))?.age).toBeNull();
    expect(parseVictimProfile(makeVictimProfile({ age: MAX_VICTIM_AGE + 1 }))?.age).toBeNull();
    expect(parseVictimProfile(makeVictimProfile({ age: 121 }))?.age).toBeNull();
    // The inclusive bounds themselves are accepted.
    expect(parseVictimProfile(makeVictimProfile({ age: MIN_VICTIM_AGE }))?.age).toBe(MIN_VICTIM_AGE);
    expect(parseVictimProfile(makeVictimProfile({ age: MAX_VICTIM_AGE }))?.age).toBe(MAX_VICTIM_AGE);
    expect(parseVictimProfile(makeVictimProfile({ age: 41 }))?.age).toBe(41);
  });

  it("degrades an unknown/absent gender to null", () => {
    expect(parseVictimProfile(makeVictimProfile({ gender: "alien" }))?.gender).toBeNull();
    expect(parseVictimProfile(makeVictimProfile({ gender: "" }))?.gender).toBeNull();
    expect(parseVictimProfile(makeVictimProfile({ gender: null }))?.gender).toBeNull();
  });

  it("degrades empty optional strings to null", () => {
    const parsed = parseVictimProfile(
      makeVictimProfile({ occupation: "  ", background: "", sceneObjectId: "" }),
    );
    expect(parsed?.occupation).toBeNull();
    expect(parsed?.background).toBeNull();
    expect(parsed?.sceneObjectId).toBeNull();
  });

  it("bounds over-long text (never unbounded DOM work)", () => {
    const parsed = parseVictimProfile(makeVictimProfile({ occupation: "x".repeat(20_000) }));
    expect(parsed?.occupation?.length).toBe(MAX_VICTIM_TEXT_LENGTH);
  });

  it("ignores a malformed discovery block while keeping the identity", () => {
    const parsed = parseVictimProfile(
      makeVictimProfile({
        discovery: { evidenceId: "", locationName: "", observedAt: 3 } as never,
      }),
    );
    expect(parsed?.name).toBe("Sarah Miller");
    expect(parsed?.discovery).toBeNull();
  });
});

describe("parseInvestigationBootstrap — victim integration", () => {
  it("attaches the parsed victim block when present", () => {
    const parsed = parseInvestigationBootstrap.validate({
      ...makeBootstrap(),
      victim: makeVictimProfile(),
    });
    expect(parsed.victim?.name).toBe("Sarah Miller");
  });

  it("normalizes an absent victim block to null", () => {
    const parsed = parseInvestigationBootstrap.validate(makeBootstrap());
    expect(parsed.victim ?? null).toBeNull();
  });

  it("IGNORES a malformed victim block instead of failing the whole bootstrap", () => {
    const raw = { ...makeBootstrap(), victim: { age: "old" } } as unknown as Record<string, unknown>;
    const parsed = parseInvestigationBootstrap.validate(raw);
    expect(parsed.victim ?? null).toBeNull();
    // The rest of the scene still parses (degradation, not failure).
    expect(parsed.scene.worldObjects.length).toBeGreaterThan(0);
  });

  it("tolerates a hostile-typed victim value without throwing", () => {
    const raw = { ...makeBootstrap(), victim: "<script>alert(1)</script>" } as unknown as Record<string, unknown>;
    expect(() => parseInvestigationBootstrap.validate(raw)).not.toThrow();
    expect(parseInvestigationBootstrap.validate(raw).victim ?? null).toBeNull();
  });
});
