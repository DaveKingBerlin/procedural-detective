import { describe, expect, it } from "vitest";
import {
  GENERATED_BLOCKLISTED_ROLES,
  ValidationError,
  generatedDefinitionIssues,
  parseInvestigationBootstrap,
  validateAccusationCandidates,
  validateGeneratedDefinition,
  validatePlayerKnowledge,
  validateWorldGraph,
  validateWorldObject,
} from "./validation";
import {
  makeBootstrap,
  makeCandidates,
  makeGeneratedPart,
  makeProcWorldObject,
  makeTrophyDefinition,
  makeWorldObject,
} from "./testFixtures";
import type { GeneratedAssetDefinition } from "../api/types";

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

/* ======================================================================
 * Phase 13 — declarative generated definition validation
 * ==================================================================== */

/** Mutation helper: deep-copy a trophy def so tests can break one field. */
function cloneDefinition(definition: GeneratedAssetDefinition): GeneratedAssetDefinition {
  return JSON.parse(JSON.stringify(definition)) as GeneratedAssetDefinition;
}

describe("Phase 13 — validateGeneratedDefinition (strict client gate)", () => {
  it("parses a valid parented definition into the typed shape (colors/chain intact)", () => {
    const parsed = validateGeneratedDefinition(makeTrophyDefinition());
    expect(parsed).not.toBeNull();
    expect(parsed!.parts).toHaveLength(3);
    expect(parsed!.parts[0].id).toBe("part_00");
    expect(parsed!.parts[1].parentId).toBe("part_00");
    expect(parsed!.parts[2].parentId).toBe("part_01");
    expect(parsed!.parts[2].color).toBe("#c9a227");
    expect(parsed!.hitbox.scale).toEqual({ x: 0.3, y: 0.5, z: 0.3 });
  });

  it("drops unknown document/part keys instead of copying them (allowlist only)", () => {
    const raw = makeTrophyDefinition() as unknown as Record<string, unknown>;
    raw.shaders = ["<script>"];
    raw.url = "http://evil.example/x.shader";
    raw.category = "smuggled_category";
    raw.hitbox = { ...(raw.hitbox as object), secret: 1 };
    (raw.parts as Array<Record<string, unknown>>)[0].handler = "onload";
    (raw.parts as Array<Record<string, unknown>>)[0].transform = {
      ...((raw.parts as Array<Record<string, unknown>>)[0].transform as object),
      magic: "boom",
    };
    const parsed = validateGeneratedDefinition(raw as never);
    expect(parsed).not.toBeNull();
    const keys = Object.keys(parsed!).sort();
    expect(keys).toEqual(["assetId", "canonicalName", "compilerVersion", "dimensions", "hitbox", "parts", "schemaVersion"]);
    expect("shaders" in parsed!).toBe(false);
    expect("category" in parsed!).toBe(false);
    const hitboxKeys = Object.keys(parsed!.hitbox).sort();
    expect(hitboxKeys).toEqual(["scale"]);
    const partTransformKeys = Object.keys(parsed!.parts[0].transform).sort();
    expect(partTransformKeys).toEqual(["position", "rotation", "scale"]);
  });

  it("rejects an unknown primitive (capsule / extruded_polygon) — never coerced", () => {
    const broken = cloneDefinition(makeTrophyDefinition());
    broken.parts[0].primitive = "capsule" as never;
    expect(validateGeneratedDefinition(broken)).toBeNull();
    broken.parts[0].primitive = "extruded_polygon" as never;
    expect(validateGeneratedDefinition(broken)).toBeNull();
  });

  it("rejects NaN / Infinity values anywhere in the geometry", () => {
    const brokenNaN = cloneDefinition(makeTrophyDefinition());
    brokenNaN.parts[0].transform.scale.x = Number.NaN;
    expect(validateGeneratedDefinition(brokenNaN)).toBeNull();
    const brokenInf = cloneDefinition(makeTrophyDefinition());
    brokenInf.parts[0].transform.position.y = Number.POSITIVE_INFINITY;
    expect(validateGeneratedDefinition(brokenInf)).toBeNull();
  });

  it("rejects out-of-bounds part positions (5.0 exceeds |4|)", () => {
    const broken = cloneDefinition(makeTrophyDefinition());
    broken.parts[0].transform.position.x = 5.0;
    expect(validateGeneratedDefinition(broken)).toBeNull();
  });

  it("rejects out-of-bounds part scales (0.001 below minimum, 3 above maximum)", () => {
    const tiny = cloneDefinition(makeTrophyDefinition());
    tiny.parts[0].transform.scale.y = 0.001;
    expect(validateGeneratedDefinition(tiny)).toBeNull();
    const huge = cloneDefinition(makeTrophyDefinition());
    huge.parts[0].transform.scale.z = 3;
    expect(validateGeneratedDefinition(huge)).toBeNull();
  });

  it("rejects a definition with more than 24 parts", () => {
    const broken = cloneDefinition(makeTrophyDefinition());
    broken.parts = Array.from({ length: 25 }, (_, index) =>
      makeGeneratedPart(`part_${String(index).padStart(2, "0")}`),
    );
    expect(validateGeneratedDefinition(broken)).toBeNull();
  });

  it("rejects a bad color (non-hex, malformed hex, hostile string)", () => {
    for (const color of ["red", "#12345", "#gggggg", "javascript:alert(1)"]) {
      const broken = cloneDefinition(makeTrophyDefinition());
      broken.parts[0].color = color;
      expect(validateGeneratedDefinition(broken), color).toBeNull();
    }
  });

  it("rejects a parentId that references a LATER (forward) part", () => {
    const broken = cloneDefinition(makeTrophyDefinition());
    broken.parts[0].parentId = "part_02"; // part_00 references a later part
    expect(validateGeneratedDefinition(broken)).toBeNull();
  });

  it("rejects a parentId that references a NON-EXISTENT part (dangling reference)", () => {
    const broken = cloneDefinition(makeTrophyDefinition());
    broken.parts[0].parentId = "part_99"; // no such part anywhere
    expect(validateGeneratedDefinition(broken)).toBeNull();
    broken.parts[0].parentId = "ghost_part";
    expect(validateGeneratedDefinition(broken)).toBeNull();
  });

  it("rejects a parent chain deeper than 2 hops", () => {
    const broken = cloneDefinition(makeTrophyDefinition());
    broken.parts.push(
      makeGeneratedPart("part_03", {
        role: "handle",
        parentId: "part_02", // part_00 -> part_01 -> part_02 -> part_03 = depth 3
      }),
    );
    expect(validateGeneratedDefinition(broken)).toBeNull();
  });

  it("rejects duplicate part ids", () => {
    const broken = cloneDefinition(makeTrophyDefinition());
    broken.parts[1].id = "part_00";
    expect(validateGeneratedDefinition(broken)).toBeNull();
  });

  it("rejects a non-finite hitbox scale (Infinity / out of 0.15..10)", () => {
    const infinite = cloneDefinition(makeTrophyDefinition());
    infinite.hitbox.scale.x = Number.POSITIVE_INFINITY;
    expect(validateGeneratedDefinition(infinite)).toBeNull();
    const tooSmall = cloneDefinition(makeTrophyDefinition());
    tooSmall.hitbox.scale.y = 0.01;
    expect(validateGeneratedDefinition(tooSmall)).toBeNull();
    const tooLarge = cloneDefinition(makeTrophyDefinition());
    tooLarge.hitbox.scale.z = 10.5;
    expect(validateGeneratedDefinition(tooLarge)).toBeNull();
  });

  it("rejects out-of-bounds declared dimensions and a missing hitbox/parts", () => {
    const dims = cloneDefinition(makeTrophyDefinition());
    dims.dimensions.x = 5.0;
    expect(validateGeneratedDefinition(dims)).toBeNull();
    const noHitbox = cloneDefinition(makeTrophyDefinition()) as Partial<GeneratedAssetDefinition>;
    delete noHitbox.hitbox;
    expect(validateGeneratedDefinition(noHitbox as never)).toBeNull();
    const noParts = cloneDefinition(makeTrophyDefinition());
    noParts.parts = [];
    expect(validateGeneratedDefinition(noParts)).toBeNull();
  });

  it("rejects a bad part id / role pattern and schema-version confusion", () => {
    const badId = cloneDefinition(makeTrophyDefinition());
    badId.parts[0].id = "part id with spaces";
    expect(validateGeneratedDefinition(badId)).toBeNull();
    const badRole = cloneDefinition(makeTrophyDefinition());
    badRole.parts[0].role = "UPPER_CASE";
    expect(validateGeneratedDefinition(badRole)).toBeNull();
    const versionConfusion = cloneDefinition(makeTrophyDefinition());
    versionConfusion.compilerVersion = 99;
    versionConfusion.schemaVersion = 99;
    expect(validateGeneratedDefinition(versionConfusion)).toBeNull();
  });

  it("reports deterministic SORTED issue strings (same input -> same output)", () => {
    const broken = cloneDefinition(makeTrophyDefinition());
    broken.parts[0].color = "red";
    broken.parts[0].primitive = "capsule" as never;
    broken.parts[0].transform.scale.x = 3;
    broken.parts[0].role = "bad role!";
    const first = generatedDefinitionIssues(broken);
    const second = generatedDefinitionIssues(broken);
    expect(first).toHaveLength(4);
    expect(second).toEqual(first);
    expect([...first]).toEqual([...first].sort());
  });
});

describe("Phase 13 — DEF-070 part-role carve-out (handler-shaped + blocklisted roles)", () => {
  it.each(["onload", "onclick", "onerror", "onmouseover", "onchange", "onfocus", "onblur", "onsubmit"])(
    "rejects the event-handler-shaped role %s (mirrors the backend carve-out)",
    (role) => {
      const broken = cloneDefinition(makeTrophyDefinition());
      broken.parts[0].role = role;
      expect(validateGeneratedDefinition(broken)).toBeNull();
      expect(generatedDefinitionIssues(broken).join("\n")).toContain("event-handler shaped");
    },
  );

  it.each(GENERATED_BLOCKLISTED_ROLES)(
    "rejects the blocklisted attribute/property role %s",
    (role) => {
      const broken = cloneDefinition(makeTrophyDefinition());
      broken.parts[1].role = role;
      expect(validateGeneratedDefinition(broken)).toBeNull();
      expect(generatedDefinitionIssues(broken).join("\n")).toContain("blocklisted");
    },
  );

  it.each(["handle", "blade", "base", "lid", "stem", "frame", "rail"])(
    "accepts the normal role %s (never over-restricts)",
    (role) => {
      const kept = cloneDefinition(makeTrophyDefinition());
      kept.parts[1].role = role;
      expect(validateGeneratedDefinition(kept)).not.toBeNull();
      expect(generatedDefinitionIssues(kept)).toEqual([]);
    },
  );

  it("rejects the bare prefix 'on' itself (not just on<word>)", () => {
    const broken = cloneDefinition(makeTrophyDefinition());
    broken.parts[0].role = "on";
    expect(validateGeneratedDefinition(broken)).toBeNull();
    expect(generatedDefinitionIssues(broken).join("\n")).toContain("event-handler shaped");
  });
});

describe("Phase 13 — generated block wiring in the world-object DTO", () => {
  it("a proc.* object WITHOUT a generated block parses with generated === null (fallback signal)", () => {
    const parsed = validateWorldObject.validate(
      makeProcWorldObject({ generated: undefined } as never),
    );
    expect(parsed.generated).toBeNull();
  });

  it("a proc.* object with an INVALID block yields generated === null (never throws)", () => {
    const broken = makeProcWorldObject() as unknown as Record<string, unknown>;
    const parts = (broken.generated as { parts: Array<Record<string, unknown>> }).parts;
    parts[0].primitive = "capsule";
    const parsed = validateWorldObject.validate(broken);
    expect(parsed.generated).toBeNull();
  });

  it("a proc.* object with a VALID block yields the typed definition", () => {
    const parsed = validateWorldObject.validate(makeProcWorldObject());
    expect(parsed.generated).not.toBeNull();
    expect(parsed.generated!.assetId).toBe(parsed.assetId);
    expect(parsed.generated!.parts).toHaveLength(3);
  });

  it("a NON-proc object carrying a generated block IGNORES the field (never overrides identity)", () => {
    const parsed = validateWorldObject.validate(
      makeWorldObject({ assetId: "PROP_VASE_01", generated: makeTrophyDefinition() }),
    );
    expect(parsed.assetId).toBe("PROP_VASE_01");
    expect(parsed.generated).toBeNull();
  });
});