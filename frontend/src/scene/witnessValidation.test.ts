import { describe, expect, it } from "vitest";
import { makeBootstrap, makeWitnessListEntry } from "./testFixtures";
import { parseInvestigationBootstrap, ValidationError, validateWitnessListEntry } from "./validation";

/**
 * Phase 23 — strict deterministic validation of the player-safe witness list:
 *   - only {witnessId, displayName, presence, sceneObjectId?} ever survive
 *     (a hostile "statement"/"questionType"/"hidden" key is dropped);
 *   - presence is the CLOSED enum; every string is bounded;
 *   - the bootstrap `witnesses` field is OPTIONAL (pre-23 servers -> null,
 *     never a crash) but a PRESENT malformed list is rejected.
 */

describe("Phase 23 — validateWitnessListEntry", () => {
  it("accepts a valid ON_SCENE entry and keeps ONLY the allowlisted fields", () => {
    const raw = {
      witnessId: "witness_emily_reed",
      displayName: "Emily Reed",
      presence: "ON_SCENE",
      sceneObjectId: "witness_emily_reed",
      statement: { summary: "HIDDEN future answer" },
      questionType: "TIME",
      hidden: true,
    };
    const parsed = validateWitnessListEntry.validate(raw);
    expect(parsed).toEqual({
      witnessId: "witness_emily_reed",
      displayName: "Emily Reed",
      presence: "ON_SCENE",
      sceneObjectId: "witness_emily_reed",
    });
    expect(JSON.stringify(parsed)).not.toContain("HIDDEN");
    expect(JSON.stringify(parsed)).not.toContain('"statement"');
    expect(JSON.stringify(parsed)).not.toContain('"questionType"');
  });

  it("accepts a valid REMOTE_STATEMENT entry with a null sceneObjectId", () => {
    const parsed = validateWitnessListEntry.validate(makeWitnessListEntry({ presence: "REMOTE_STATEMENT", sceneObjectId: null }));
    expect(parsed.presence).toBe("REMOTE_STATEMENT");
    expect(parsed.sceneObjectId).toBeNull();
  });

  it("rejects a non-closed presence (never coerced)", () => {
    for (const presence of ["ON_SCEEN", "REMOTE", "", "on_scene", 42]) {
      expect(() =>
        validateWitnessListEntry.validate({ witnessId: "w", displayName: "n", presence }),
      ).toThrow(ValidationError);
    }
  });

  it("rejects missing/malformed identities and unbounded strings", () => {
    expect(() => validateWitnessListEntry.validate({ displayName: "n", presence: "ON_SCENE" })).toThrow(ValidationError);
    expect(() => validateWitnessListEntry.validate({ witnessId: "w", presence: "ON_SCENE" })).toThrow(ValidationError);
    expect(() =>
      validateWitnessListEntry.validate({ witnessId: "w", displayName: "x".repeat(200), presence: "ON_SCENE" }),
    ).toThrow(ValidationError);
    expect(() => validateWitnessListEntry.validate("not-an-object")).toThrow(ValidationError);
  });
});

describe("Phase 23 — parseInvestigationBootstrap with witnesses", () => {
  it("keeps the OPTIONAL witnesses field: absent => null, present => validated array", () => {
    const plain = parseInvestigationBootstrap.validate(makeBootstrap());
    expect(plain.witnesses).toBeNull();

    const withWitnesses = parseInvestigationBootstrap.validate(makeBootstrap({ witnesses: [makeWitnessListEntry()] }));
    expect(withWitnesses.witnesses).toHaveLength(1);
    expect(withWitnesses.witnesses![0].displayName).toBe("Emily Reed");
  });

  it("rejects a PRESENT but malformed witnesses list (never coerced)", () => {
    expect(() =>
      parseInvestigationBootstrap.validate(makeBootstrap({ witnesses: [{ witnessId: "x" }] as never })),
    ).toThrow(ValidationError);
    expect(() =>
      parseInvestigationBootstrap.validate(makeBootstrap({ witnesses: "nope" as never })),
    ).toThrow(ValidationError);
  });
});