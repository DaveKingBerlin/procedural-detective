import { describe, expect, it } from "vitest";
import {
  ValidationError,
  parseInvestigationBootstrap,
  validatePlayerKnowledge,
  validateWorldGraph,
  validateWorldObject,
} from "./validation";
import { makeBootstrap, makeWorldObject } from "./testFixtures";

function expectValidationError(action: () => unknown, fragment: string): void {
  try {
    action();
  } catch (error) {
    expect(error).toBeInstanceOf(ValidationError);
    if (error instanceof ValidationError) {
      expect(error.message).toContain(fragment);
    }
    return;
  }
  throw new Error(`expected ValidationError containing "${fragment}" but nothing was thrown`);
}

describe("parseInvestigationBootstrap (player-safe DTO parsing)", () => {
  it("parses a valid canned bootstrap into the typed shape", () => {
    const parsed = parseInvestigationBootstrap.validate(makeBootstrap());
    expect(parsed.playthroughId).toBe("PT-test-0001");
    expect(parsed.state).toBe("PLAYING");
    expect(parsed.caseVersion).toBe(1);
    expect(parsed.playerKnowledge.discoveredEvidenceIds).toEqual([]);
    expect(parsed.scene.location.name).toBe("Miller Apartment - Kitchen");
    // mirrors the real dev_mode_case.json placements (9 world objects)
    expect(parsed.scene.worldObjects).toHaveLength(9);
  });

  it("is deterministic: the same payload parses to the same object", () => {
    const bootstrap = makeBootstrap();
    expect(parseInvestigationBootstrap.validate(bootstrap)).toEqual(
      parseInvestigationBootstrap.validate(bootstrap),
    );
  });

  it("rejects a non-object payload", () => {
    expectValidationError(() => parseInvestigationBootstrap.validate("nope"), "must be an object");
    expectValidationError(() => parseInvestigationBootstrap.validate(null), "must be an object");
    expectValidationError(() => parseInvestigationBootstrap.validate([1, 2]), "must be an object");
  });

  it("rejects a missing playthroughId", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    delete raw.playthroughId;
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "playthroughId");
  });

  it("rejects a non-PLAYING state", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    raw.state = "CREATED";
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "PLAYING");
  });

  it("rejects a fractional caseVersion", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    raw.caseVersion = 1.5;
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "caseVersion");
  });

  it("rejects malformed playerKnowledge", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    raw.playerKnowledge = { discoveredEvidenceIds: [1], readEvidenceIds: [], visitedLocationIds: [] };
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "array of non-empty strings");
  });

  it("rejects a missing scene (never trusts partial payloads)", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    delete raw.scene;
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "scene");
  });
});

describe("validateWorldGraph", () => {
  it("accepts a valid scene with location and world objects", () => {
    const graph = validateWorldGraph.validate(makeBootstrap().scene as unknown as Record<string, unknown>);
    expect(graph.location.locationId).toBe("miller_apartment_kitchen");
    expect(graph.worldObjects.length).toBeGreaterThanOrEqual(4);
  });

  it("rejects a missing location", () => {
    const scene = makeBootstrap().scene as unknown as Record<string, unknown>;
    delete scene.location;
    expectValidationError(() => validateWorldGraph.validate(scene), "scene.location");
  });

  it("rejects a worldObjects entry that is not an array", () => {
    const scene = makeBootstrap().scene as unknown as Record<string, unknown>;
    scene.worldObjects = "kitchen_knife";
    expectValidationError(() => validateWorldGraph.validate(scene), "worldObjects");
  });

  it("rejects a world object missing assetId", () => {
    const broken = makeWorldObject({ assetId: "" });
    expectValidationError(() => validateWorldObject.validate(broken), "assetId");
  });

  it("rejects a world object with a non-boolean discovered flag", () => {
    const broken = makeWorldObject() as unknown as Record<string, unknown>;
    broken.discovered = "yes";
    expectValidationError(() => validateWorldObject.validate(broken), "discovered");
  });

  it("rejects a world object with an object-typed evidenceId instead of string|null", () => {
    const broken = makeWorldObject() as unknown as Record<string, unknown>;
    broken.evidenceId = { clue: "nope" };
    expectValidationError(() => validateWorldObject.validate(broken), "evidenceId");
  });

  it("accepts subtype null and evidenceId null", () => {
    const parsed = validateWorldObject.validate(
      makeWorldObject({ subtype: null, evidenceId: null }),
    );
    expect(parsed.subtype).toBeNull();
    expect(parsed.evidenceId).toBeNull();
  });
});

describe("validatePlayerKnowledge", () => {
  it("accepts empty and non-empty sorted id arrays", () => {
    expect(validatePlayerKnowledge.validate({ discoveredEvidenceIds: [], readEvidenceIds: [], visitedLocationIds: [] }))
      .toEqual({ discoveredEvidenceIds: [], readEvidenceIds: [], visitedLocationIds: [] });
    expect(
      validatePlayerKnowledge.validate({
        discoveredEvidenceIds: ["a", "b"],
        readEvidenceIds: ["a"],
        visitedLocationIds: ["loc"],
      }).discoveredEvidenceIds,
    ).toEqual(["a", "b"]);
  });

  it("rejects missing visitedLocationIds", () => {
    expectValidationError(
      () => validatePlayerKnowledge.validate({ discoveredEvidenceIds: [], readEvidenceIds: [] }),
      "visitedLocationIds",
    );
  });
});