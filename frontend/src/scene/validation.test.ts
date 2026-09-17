import { describe, expect, it } from "vitest";
import {
  ValidationError,
  parseInvestigationBootstrap,
  validateAccusationCandidates,
  validatePlayerKnowledge,
  validateWorldGraph,
  validateWorldObject,
} from "./validation";
import { makeBootstrap, makeCandidates, makeWorldObject } from "./testFixtures";

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

  it("accepts a world object with an EMPTY interaction string (DEF-062 no-affordance DTO)", () => {
    // DEF-062: published cases encode "no affordance" as interaction:"" (the
    // manifest-derived DTO). An empty string is valid — only a non-string or a
    // missing field is malformed.
    const parsed = validateWorldObject.validate(makeWorldObject({ interaction: "" }));
    expect(parsed).toMatchObject({ objectId: "kitchen_knife", interaction: "" });
  });

  it("rejects a world object whose interaction is missing or not a string", () => {
    const nonString = makeWorldObject() as unknown as Record<string, unknown>;
    nonString.interaction = 7;
    expectValidationError(() => validateWorldObject.validate(nonString), "interaction");
    const missing = makeWorldObject() as unknown as Record<string, unknown>;
    delete missing.interaction;
    expectValidationError(() => validateWorldObject.validate(missing), "interaction");
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

describe("accusation candidates block (Phase 7)", () => {
  it("parses the candidates block from a canned bootstrap with order preserved", () => {
    const parsed = parseInvestigationBootstrap.validate(makeBootstrap());
    expect(parsed.candidates.suspects.map((entry) => entry.id)).toEqual([
      "suspect_alpha",
      "suspect_beta",
      "suspect_gamma",
    ]);
    expect(parsed.candidates.suspects[0]).toEqual({ id: "suspect_alpha", name: "Ada Marsh" });
    expect(parsed.candidates.motives[0]).toEqual({ id: "motive_alpha", label: "A dispute over money" });
    expect(parsed.candidates.weapons[0]).toEqual({ id: "weapon_alpha", assetId: "PROP_GENERIC_01", name: "Kitchen knife" });
  });

  it("drops unknown candidate fields (a smuggled correct/winner marker never survives)", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    const candidates = raw.candidates as unknown as Record<string, unknown>;
    candidates.suspects = [
      { ...makeCandidates().suspects[0], correct: true, winner: true, secretScore: 99 },
    ];
    const parsed = parseInvestigationBootstrap.validate(raw);
    expect(Object.keys(parsed.candidates.suspects[0]).sort()).toEqual(["id", "name"]);
    expect("correct" in parsed.candidates.suspects[0]).toBe(false);
    expect("winner" in parsed.candidates.suspects[0]).toBe(false);
  });

  it("preserves array order even when the server sends a non-sorted order (never re-sorts)", () => {
    const unordered = makeCandidates({
      suspects: [
        { id: "z_last", name: "Zed" },
        { id: "a_first", name: "Alpha" },
        { id: "m_mid", name: "Mid" },
      ],
    });
    const parsed = validateAccusationCandidates.validate(unordered);
    expect(parsed.suspects.map((entry) => entry.id)).toEqual(["z_last", "a_first", "m_mid"]);
  });

  it("rejects a bootstrap without the candidates block", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    delete raw.candidates;
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "candidates");
  });

  it("rejects malformed candidates entries", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    const candidates = raw.candidates as unknown as Record<string, unknown>;
    candidates.suspects = [{ id: "a" }]; // no name
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "candidates.suspects");
    candidates.suspects = "not-an-array";
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "candidates.suspects must be an array");
  });

  it("rejects a non-object candidates payload", () => {
    expectValidationError(() => validateAccusationCandidates.validate(null), "candidates must be an object");
  });
});

describe("bootstrap lifecycle state (Phase 7)", () => {
  it("accepts the frozen lifecycle states PLAYING / ACCUSED / REVEALED", () => {
    const playing = parseInvestigationBootstrap.validate(makeBootstrap({ state: "PLAYING" }));
    const accused = parseInvestigationBootstrap.validate(makeBootstrap({ state: "ACCUSED" }));
    const revealed = parseInvestigationBootstrap.validate(makeBootstrap({ state: "REVEALED" }));
    expect(playing.state).toBe("PLAYING");
    expect(accused.state).toBe("ACCUSED");
    expect(revealed.state).toBe("REVEALED");
  });

  it("rejects states outside the frozen lifecycle", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    raw.state = "HACKED";
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "PLAYING");
    raw.state = "CREATED";
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "PLAYING");
  });
});

describe("Phase 11 — scene.environmentId (optional-accept-required)", () => {
  it("parses a scene WITH environmentId 'office'", () => {
    const bootstrap = makeBootstrap();
    (bootstrap.scene as unknown as Record<string, unknown>).environmentId = "office";
    const parsed = parseInvestigationBootstrap.validate(bootstrap);
    expect(parsed.scene.environmentId).toBe("office");
  });

  it("defaults a scene WITHOUT environmentId to 'apartment' (backwards compatible)", () => {
    const legacy = makeBootstrap();
    delete (legacy.scene as unknown as Record<string, unknown>).environmentId;
    const parsed = parseInvestigationBootstrap.validate(legacy);
    expect(parsed.scene.environmentId).toBe("apartment");
    expect(parsed.scene.worldObjects).toHaveLength(9);
  });

  it("treats a null environmentId as absent (lenient fallback)", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    (raw.scene as Record<string, unknown>).environmentId = null;
    expect(parseInvestigationBootstrap.validate(raw).scene.environmentId).toBe("apartment");
  });

  it("rejects a PRESENT but non-string environmentId (never coerces a hostile value)", () => {
    const asNumber = makeBootstrap() as unknown as Record<string, unknown>;
    (asNumber.scene as Record<string, unknown>).environmentId = 7;
    expectValidationError(() => parseInvestigationBootstrap.validate(asNumber), "environmentId");

    const asObject = makeBootstrap() as unknown as Record<string, unknown>;
    (asObject.scene as Record<string, unknown>).environmentId = { id: "office" };
    expectValidationError(() => parseInvestigationBootstrap.validate(asObject), "environmentId");
  });

  it("rejects an empty-string environmentId", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    (raw.scene as Record<string, unknown>).environmentId = "";
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "environmentId");
  });

  it("rejects an oversized environmentId (>80 characters)", () => {
    const raw = makeBootstrap() as unknown as Record<string, unknown>;
    (raw.scene as Record<string, unknown>).environmentId = "x".repeat(81);
    expectValidationError(() => parseInvestigationBootstrap.validate(raw), "environmentId");
  });
});