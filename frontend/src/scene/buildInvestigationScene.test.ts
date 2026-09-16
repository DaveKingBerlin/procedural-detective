import { describe, expect, it } from "vitest";
import { ANCHOR_REGISTRY } from "./anchorRegistry";
import { FALLBACK_ASSET, FALLBACK_COLOR, isKnownAsset } from "./assetRegistry";
import { buildInvestigationScene } from "./buildInvestigationScene";
import { EMITTED_ASSET_IDS, makeBootstrap, makeWorldObject } from "./testFixtures";
import { ValidationError } from "./validation";

describe("buildInvestigationScene — scene creation from a deterministic fixture", () => {
  it("creates one scene object per world object with stable ids", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const ids = model.worldObjects.map((o) => o.objectId);
    expect(ids).toEqual([
      "apartment_door",
      "apartment_lamp",
      "apartment_laptop",
      "apartment_table",
      "kitchen_knife",
      "letter_opener",
      "scissors",
      "vase_01",
      "victim_body_placeholder",
    ]);
    expect(new Set(ids).size).toBe(ids.length); // unique
  });

  it("derives geometry ONLY from the anchor registry — never from the payload", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const knife = model.worldObjects.find((o) => o.objectId === "kitchen_knife");
    expect(knife).not.toBeUndefined();
    expect(knife!.position).toEqual(ANCHOR_REGISTRY.get("kitchen_counter")!.position);
    expect(knife!.position).not.toEqual({ x: 0, y: 0, z: 0 });
  });

  it("maps the expected primitives and registry colors", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));
    expect(byId.get("kitchen_knife")!.primitiveKind).toBe("box");
    expect(byId.get("apartment_laptop")!.primitiveKind).toBe("flat");
    expect(byId.get("vase_01")!.primitiveKind).toBe("cylinder");
    expect(byId.get("apartment_lamp")!.primitiveKind).toBe("cylinder");
    expect(byId.get("victim_body_placeholder")!.primitiveKind).toBe("flat");
    // Phase 8_1: the knife's registry color is now the pale-metallic blade tone.
    expect(byId.get("kitchen_knife")!.color).toBe("#c8ccd4");
    // Phase 8_1: composites are carried on the world object for the renderer.
    expect(byId.get("kitchen_knife")!.compositeKind).toBe("knife");
    expect(byId.get("apartment_laptop")!.compositeKind).toBe("laptop");
    expect(byId.get("victim_body_placeholder")!.compositeKind).toBe("victim");
    expect(byId.get("apartment_table")!.compositeKind).toBe("table");
    expect(byId.get("vase_01")!.compositeKind).toBeNull();
  });

  it("is deterministic: the same fixture builds a deep-equal model every time", () => {
    expect(buildInvestigationScene(makeBootstrap())).toEqual(buildInvestigationScene(makeBootstrap()));
  });

  it("is deterministic regardless of input ordering (shuffle -> same model)", () => {
    const bootstrap = makeBootstrap();
    const reversed = makeBootstrap();
    reversed.scene.worldObjects = [...reversed.scene.worldObjects].reverse();
    expect(buildInvestigationScene(bootstrap)).toEqual(buildInvestigationScene(reversed));
  });

  it("keeps object identity from backend ids, never array position", () => {
    const bootstrap = makeBootstrap();
    // Reorder into a non-sorted permutation: the model must resolve the SAME
    // objects with the same identities regardless of array position.
    bootstrap.scene.worldObjects = [
      bootstrap.scene.worldObjects[6],
      bootstrap.scene.worldObjects[2],
      bootstrap.scene.worldObjects[8],
      bootstrap.scene.worldObjects[0],
      bootstrap.scene.worldObjects[5],
      bootstrap.scene.worldObjects[3],
      bootstrap.scene.worldObjects[7],
      bootstrap.scene.worldObjects[1],
      bootstrap.scene.worldObjects[4],
    ];
    const model = buildInvestigationScene(bootstrap);
    expect(model.worldObjects.map((o) => o.objectId)).toEqual([
      "apartment_door",
      "apartment_lamp",
      "apartment_laptop",
      "apartment_table",
      "kitchen_knife",
      "letter_opener",
      "scissors",
      "vase_01",
      "victim_body_placeholder",
    ]);
  });

  it("flags interactability from the registry AND the published interaction", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const knife = model.worldObjects.find((o) => o.objectId === "kitchen_knife")!;
    expect(knife.interactionWorks).toBe(true);

    // PROP_BODY_PLACEHOLDER_01 is registered non-interactable (no placement
    // links it to evidence): even though its DTO publishes an interaction,
    // interactionWorks must be false.
    const body = model.worldObjects.find((o) => o.objectId === "victim_body_placeholder")!;
    expect(body.interaction).toBe("inspect");
    expect(body.interactionWorks).toBe(false);
  });
});

describe("contract sync with the backend dev fixture (dev_mode_case.json)", () => {
  /**
   * DEF-049 regression: the backend dev-mode case emits exactly these 9
   * assetIds. Every one MUST resolve to a registered, non-fallback entry —
   * otherwise the laptop/table/door/lamp/victim silently degrade into neutral
   * non-interactable placeholders and the golden E2E cannot discover the
   * laptop's email. The fixture mirrors dev_mode_case.json placements; if the
   * backend emits a different set, this suite fails loudly.
   */
  it("mirrors the exact 9 emitted assetIds (never silently out of sync)", () => {
    const emitted = new Set(makeBootstrap().scene.worldObjects.map((o) => o.assetId));
    expect(emitted.size).toBe(9);
    expect([...emitted].sort()).toEqual([...EMITTED_ASSET_IDS].sort());
  });

  it("resolves every emitted assetId to a NON-fallback registry entry", () => {
    const model = buildInvestigationScene(makeBootstrap());
    for (const obj of model.worldObjects) {
      expect(isKnownAsset(obj.assetId), `${obj.assetId} must be registered`).toBe(true);
      expect(obj.unknownAsset, `${obj.assetId} resolves to a fallback`).toBe(false);
      expect(obj.color, `${obj.assetId} uses the fallback color`).not.toBe(FALLBACK_COLOR);
    }
  });

  it("builds exactly the real worldObject count (9) with stable ids", () => {
    const model = buildInvestigationScene(makeBootstrap());
    expect(model.worldObjects).toHaveLength(9);
    expect(new Set(model.worldObjects.map((o) => o.objectId)).size).toBe(9);
  });

  it("registers evidence-linked objects interactable and the body non-interactable", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));
    // evidence-linked placements MUST be interactable
    for (const id of ["kitchen_knife", "letter_opener", "scissors", "apartment_laptop"]) {
      expect(byId.get(id)!.interactionWorks, `${id} must be interactable`).toBe(true);
    }
    // the victim body has no evidence-linked placement -> NOT interactable
    expect(byId.get("victim_body_placeholder")!.interactionWorks).toBe(false);
    // env objects without evidence remain interactable (no discovery returned)
    for (const id of ["apartment_table", "apartment_door", "apartment_lamp", "vase_01"]) {
      expect(byId.get(id)!.interactionWorks, `${id} should stay interactable`).toBe(true);
    }
  });
});

describe("buildInvestigationScene — unknown asset id behavior", () => {
  it("never throws: unknown assets become a neutral, non-interactable fallback", () => {
    const bootstrap = makeBootstrap({
      scene: {
        ...makeBootstrap().scene,
        worldObjects: [
          makeWorldObject({ objectId: "mystery_box", assetId: "ASSET.THAT.DOES.NOT.EXIST" }),
        ],
      },
    });
    const model = buildInvestigationScene(bootstrap);
    const mystery = model.worldObjects[0];
    expect(mystery.unknownAsset).toBe(true);
    expect(mystery.label).toBeNull();
    expect(mystery.primitiveKind).toBe(FALLBACK_ASSET.primitiveKind);
    expect(mystery.color).toBe(FALLBACK_COLOR);
    expect(mystery.interactionWorks).toBe(false);
  });

  it("places unknown-anchor objects via the stable objectId hash slot", () => {
    const bootstrap = makeBootstrap({
      scene: {
        ...makeBootstrap().scene,
        worldObjects: [makeWorldObject({ objectId: "mystery_box", anchor: "no_such_anchor" })],
      },
    });
    const first = buildInvestigationScene(bootstrap).worldObjects[0].position;
    const second = buildInvestigationScene(bootstrap).worldObjects[0].position;
    expect(first).toEqual(second);
  });
});

describe("buildInvestigationScene — malformed payload safety", () => {
  it("throws ValidationError for a malformed WorldGraph (never trusts it)", () => {
    const raw = { playthroughId: "PT-x", caseId: "C", caseVersion: 1, state: "PLAYING", playerKnowledge: { discoveredEvidenceIds: [], readEvidenceIds: [], visitedLocationIds: [] }, scene: { location: { locationId: "l", name: "n" } } };
    expect(() => buildInvestigationScene(raw as never)).toThrow(ValidationError);
  });

  it("throws ValidationError when a world object is missing its assetId", () => {
    const broken = makeBootstrap();
    broken.scene.worldObjects = [{} as never];
    expect(() => buildInvestigationScene(broken as never)).toThrow(ValidationError);
  });
});

describe("phase 8_1 — accessibility fallback regression (the DOM list stays usable)", () => {
  it("important evidence (knife, laptop) stays interactable with a public label for the list path", () => {
    // The below-scene object-list path renders a button for every object with
    // interactionWorks === true and a visible label. Both must survive the
    // composite change so the keyboard/accessibility flow stays intact.
    const model = buildInvestigationScene(makeBootstrap());
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));

    for (const id of ["kitchen_knife", "apartment_laptop"]) {
      const obj = byId.get(id);
      expect(obj?.interactionWorks, `${id} must remain interactable`).toBe(true);
      expect(obj?.label, `${id} needs a public label for the list button`).not.toBeNull();
    }
    // The list still exposes discovered state through the server flags.
    expect(byId.get("kitchen_knife")?.discovered).toBe(false);
    expect(byId.get("kitchen_knife")?.evidenceId).toBe("forensic_knife_match_01");
  });
});