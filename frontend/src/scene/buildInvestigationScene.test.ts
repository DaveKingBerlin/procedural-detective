import { describe, expect, it } from "vitest";
import { getAsset, hasAsset } from "../catalog/assetCatalog";
import { getKit } from "../environments/kitCatalog";
import { buildTemplateComposite } from "../templates/templateRegistry";
import { ANCHOR_REGISTRY } from "./anchorRegistry";
import { ASSET_REGISTRY, FALLBACK_ASSET, FALLBACK_COLOR, isKnownAsset, resolveAsset, type AssetEntry, type AssetRegistry } from "./assetRegistry";
import { applyKnowledgeToSceneModel, buildInvestigationScene, type InvestigationSceneModel } from "./buildInvestigationScene";
import { EMITTED_ASSET_IDS, makeBootstrap, makeIcePickDefinition, makeOfficeBootstrap, makeProcWorldObject, makeTrophyDefinition, makeWorldObject } from "./testFixtures";
import { ValidationError } from "./validation";
import type { GeneratedAssetDefinition } from "../api/types";

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

  it("flags interaction affordances from the DTO interaction string (DEF-062)", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const knife = model.worldObjects.find((o) => o.objectId === "kitchen_knife")!;
    expect(knife.interactionWorks).toBe(true);

    // The victim body publishes NO interaction (""): interactionWorks is off —
    // the affordance is payload-driven, never inferred from the asset type.
    const body = model.worldObjects.find((o) => o.objectId === "victim_body_placeholder")!;
    expect(body.interaction).toBe("");
    expect(body.interactionWorks).toBe(false);
  });
});

describe("DEF-062 — interaction affordances are PAYLOAD-driven, not catalog-driven", () => {
  /**
   * Regression for ADV-144 / DEF-062 (MED): `interactionWorks` must NOT be
   * gated by the bundled catalog's `interactable` flag. A newer catalog
   * shipped later could otherwise add/remove click affordances on
   * ALREADY-PUBLISHED cases while the server payload stays byte-identical.
   * The affordance is a property of the published DTO (non-empty
   * `interaction`), so a MUTATED catalog must produce the IDENTICAL
   * interactionWorks map for the same canned payload.
   */
  function affordanceMap(model: InvestigationSceneModel): Array<[string, boolean]> {
    return model.worldObjects.map((o) => [o.objectId, o.interactionWorks]);
  }

  it("flipping every catalog interactable flag does not change published-case affordances", () => {
    const flippedRegistry: AssetRegistry = new Map(
      [...ASSET_REGISTRY].map(([id, entry]) => [
        id,
        { ...entry, interactable: !entry.interactable },
      ]),
    );
    // Sanity: the flipped registry is NOT the default one (a no-op test would
    // prove nothing).
    expect(flippedRegistry.get("PROP_KITCHEN_KNIFE_01")!.interactable).not.toBe(
      ASSET_REGISTRY.get("PROP_KITCHEN_KNIFE_01")!.interactable,
    );

    const model = buildInvestigationScene(makeBootstrap());
    const flippedModel = buildInvestigationScene(makeBootstrap(), flippedRegistry);
    expect(affordanceMap(flippedModel)).toEqual(affordanceMap(model));
  });

  it("golden affordances derive from the DTO interaction strings (manifest-derived DTO)", () => {
    // The fixture publishes the manifest-derived DTO: non-empty interactions
    // only for the evidence-bearing objects (knife/opener/scissors/laptop).
    const model = buildInvestigationScene(makeBootstrap());
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));
    for (const id of ["kitchen_knife", "letter_opener", "scissors", "apartment_laptop"]) {
      expect(byId.get(id)!.interaction, id).not.toBe("");
      expect(byId.get(id)!.interactionWorks, `${id} affordance on`).toBe(true);
    }
    for (const id of ["apartment_door", "apartment_lamp", "apartment_table", "vase_01", "victim_body_placeholder"]) {
      expect(byId.get(id)!.interaction, id).toBe("");
      expect(byId.get(id)!.interactionWorks, `${id} affordance off`).toBe(false);
    }
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

  it("resolves every emitted assetId through the CATALOG with 0 fallbacks (Phase 10)", () => {
    // The golden scene is fully Asset-Oracle-driven: every assetId the canned
    // bootstrap emits is an EXACT catalog id, so the model carries the
    // catalog's label/color and never degrades to a neutral fallback.
    const model = buildInvestigationScene(makeBootstrap());
    for (const obj of model.worldObjects) {
      expect(hasAsset(obj.assetId), `${obj.assetId} must be a catalog id`).toBe(true);
      expect(obj.unknownAsset, `${obj.assetId} must resolve through the catalog`).toBe(false);
      const descriptor = getAsset(obj.assetId);
      expect(obj.label, `${obj.assetId} label comes from the catalog`).toBe(descriptor!.label);
      expect(obj.scale, `${obj.assetId} scale comes from the catalog dimensions`).toEqual(descriptor!.dimensions);
      expect(obj.interactionWorks, `${obj.assetId} affordance comes from the DTO interaction`).toBe(
        obj.interaction !== "",
      );
    }
  });

  it("registers evidence-linked objects interactable and the body non-interactable", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));
    // evidence-linked placements publish non-empty DTO interactions -> affordance on
    for (const id of ["kitchen_knife", "letter_opener", "scissors", "apartment_laptop"]) {
      expect(byId.get(id)!.interactionWorks, `${id} must be interactable`).toBe(true);
    }
    // DEF-062: affordances are PAYLOAD-driven. The manifest-derived DTO
    // publishes empty interaction strings for table/door/lamp/vase/victim, so
    // interactionWorks is off — independent of any catalog interactable flag.
    for (const id of ["victim_body_placeholder", "apartment_table", "apartment_door", "apartment_lamp", "vase_01"]) {
      expect(byId.get(id)!.interaction, `${id} publishes no interaction`).toBe("");
      expect(byId.get(id)!.interactionWorks, `${id} must NOT be interactable`).toBe(false);
    }
  });
});

describe("buildInvestigationScene — unknown asset id behavior", () => {
  it("never throws: unknown assets become a neutral placeholder (payload affordance intact)", () => {
    const bootstrap = makeBootstrap({
      scene: {
        ...makeBootstrap().scene,
        worldObjects: [
          // base makeWorldObject publishes interaction:"inspect".
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
    // DEF-062: the affordance is PAYLOAD-driven — the DTO publishes
    // interaction:"inspect", so the neutral fallback stays clickable even for
    // an assetId the local catalog does not know (cross-version stability).
    expect(mystery.interaction).toBe("inspect");
    expect(mystery.interactionWorks).toBe(true);
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

/* ======================================================================
 * Phase 11 Track B — environment kits integration
 * ==================================================================== */

describe("Phase 11 Track B — environment kits in the scene model", () => {
  it("carries the parsed environmentId on the model", () => {
    expect(buildInvestigationScene(makeBootstrap()).environmentId).toBe("apartment");
    expect(buildInvestigationScene(makeOfficeBootstrap()).environmentId).toBe("office");
  });

  it("office objects resolve transforms STRICTLY from the office manifest anchors", () => {
    const office = getKit("office")!;
    const deskAnchor = office.anchors.find((a) => a.anchorId === "office_desk_a")!;
    const model = buildInvestigationScene(makeOfficeBootstrap());
    const knife = model.worldObjects.find((o) => o.objectId === "office_desk_knife")!;
    expect(knife.position).toEqual(deskAnchor.position);
    expect(knife.rotation).toEqual(deskAnchor.rotation);
  });

  it("stays deterministic for a non-apartment kit: identical builds deep-equal", () => {
    expect(buildInvestigationScene(makeOfficeBootstrap())).toEqual(
      buildInvestigationScene(makeOfficeBootstrap()),
    );
  });

  it("keeps objectIds and geometry stable under input shuffle (office kit)", () => {
    const original = buildInvestigationScene(makeOfficeBootstrap());
    const shuffled = makeOfficeBootstrap();
    shuffled.scene.worldObjects = [...shuffled.scene.worldObjects].reverse();
    expect(buildInvestigationScene(shuffled)).toEqual(original);
  });

  it("an apartment bootstrap is byte-identical to the legacy payload WITHOUT environmentId", () => {
    const legacy = makeBootstrap();
    delete (legacy.scene as unknown as Record<string, unknown>).environmentId;
    const legacyModel = buildInvestigationScene(legacy);
    const apartmentModel = buildInvestigationScene(makeBootstrap());
    expect(legacyModel).toEqual(apartmentModel);
    expect(legacyModel.environmentId).toBe("apartment");
  });

  it("an unknown environmentId falls back to apartment geometry without errors", () => {
    const mars = makeBootstrap();
    mars.scene.environmentId = "planet_mars";
    const marsModel = buildInvestigationScene(mars);
    const apartmentModel = buildInvestigationScene(makeBootstrap());
    expect(marsModel.environmentId).toBe("planet_mars");
    expect(marsModel.location).toEqual(apartmentModel.location);
    expect(marsModel.worldObjects.map((o) => o.objectId)).toEqual(
      apartmentModel.worldObjects.map((o) => o.objectId),
    );
    // Same anchors -> same apartment transforms (fallback rendering).
    for (let index = 0; index < marsModel.worldObjects.length; index++) {
      expect(marsModel.worldObjects[index].position).toEqual(
        apartmentModel.worldObjects[index].position,
      );
    }
  });

  it("keeps interaction affordances payload-driven for kit objects (evidence on, decor off)", () => {
    const model = buildInvestigationScene(makeOfficeBootstrap());
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));
    expect(byId.get("office_desk_knife")!.interaction).toBe("inspect");
    expect(byId.get("office_desk_knife")!.interactionWorks).toBe(true);
    expect(byId.get("office_vase")!.interaction).toBe("");
    expect(byId.get("office_vase")!.interactionWorks).toBe(false);
    expect(byId.get("office_body")!.interactionWorks).toBe(false);
  });

  it("office transforms differ from the apartment table (kit manifests are the source)", () => {
    const model = buildInvestigationScene(makeOfficeBootstrap());
    const knife = model.worldObjects.find((o) => o.objectId === "office_desk_knife")!;
    // kitchen_counter's apartment slot vs the office desk anchor must differ.
    expect(knife.position).not.toEqual(ANCHOR_REGISTRY.get("kitchen_counter")!.position);
  });
});

/* ======================================================================
 * Phase 12 Track B — template-backed composites in the scene model
 * ==================================================================== */

describe("Phase 12 — template-backed composites (templateId, no legacy builder)", () => {
  function hammerBootstrap(): ReturnType<typeof makeBootstrap> {
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects = [
      makeWorldObject({
        objectId: "hammer",
        assetId: "PROP_HAMMER_01",
        assetType: "tool",
        subtype: "tool",
        locationId: "miller_apartment_kitchen",
        anchor: "kitchen_counter",
        interaction: "",
        evidenceId: null,
      }),
    ];
    return bootstrap;
  }

  it("sizes a template-only composite from the factory bounds at the DEFAULT variant", () => {
    const model = buildInvestigationScene(hammerBootstrap());
    expect(model.worldObjects).toHaveLength(1);
    const hammer = model.worldObjects[0];
    expect(hammer.unknownAsset).toBe(false);
    expect(hammer.compositeKind).toBeNull();
    expect(hammer.templateId).toBe("tool_hammer");
    expect(hammer.templateParts).not.toBeNull();
    expect(hammer.templateParts!.length).toBeGreaterThanOrEqual(1);
    for (const part of hammer.templateParts!) {
      expect(Number.isFinite(part.size.x)).toBe(true);
      expect(/^#[0-9a-fA-F]{6}$/.test(part.color)).toBe(true);
    }
    // The world-object scale IS the factory's absolute bounds (scale applied).
    const descriptor = getAsset("PROP_HAMMER_01")!;
    const built = buildTemplateComposite("tool_hammer", { colors: descriptor.colors, scale: 1 });
    expect(hammer.scale).toEqual(built.bounds);
    // The factory hitbox rides along for the pick-hitbox policy.
    expect(hammer.templateHitbox).toEqual(built.hitbox);
  });

  it("resolves a default-variant material token + state on the world object", () => {
    const hammer = buildInvestigationScene(hammerBootstrap()).worldObjects[0];
    // The manifest hammer default is material metal.steel / state clean.
    expect(hammer.templateMaterial).toBe("metal.steel");
    expect(hammer.templateState).toBe("clean");
  });

  it("an UNKNOWN template degrades to the neutral fallback path (never crashes)", () => {
    // The validator blocks unknown templateIds in the real manifest, but a
    // DEFENSIVE check on a MUTATED registry entry must still degrade safely
    // (template fields null -> ordinary primitive rendering, no crash).
    const registry = new Map<string, AssetEntry>([...ASSET_REGISTRY]);
    registry.set("PROP_HAMMER_01", {
      ...resolveAsset("PROP_HAMMER_01"),
      templateId: "no_such_template",
      templateParts: null,
      templateHitbox: null,
      templateFaceBounds: null,
    });
    const model = buildInvestigationScene(hammerBootstrap(), registry);
    const hammer = model.worldObjects[0];
    expect(hammer.templateParts).toBeNull();
    expect(hammer.unknownAsset).toBe(false);
    expect(hammer.primitiveKind).toBe("box");
    // The model stays buildable and deterministic.
    expect(buildInvestigationScene(hammerBootstrap(), registry)).toEqual(model);
  });
});

/* ======================================================================
 * Phase 13 Track B — declarative generated assets in the scene model
 * ==================================================================== */

/** A bootstrap whose world set is the golden nine PLUS one proc.* trophy. */
function trophyBootstrap(): ReturnType<typeof makeBootstrap> {
  const bootstrap = makeBootstrap();
  bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, makeProcWorldObject()];
  return bootstrap;
}

describe("Phase 13 — proc.* generated objects in the scene model", () => {
  it("a valid generated block exposes compiled parts + the declared hitbox on the model", () => {
    const model = buildInvestigationScene(trophyBootstrap());
    expect(model.worldObjects).toHaveLength(10);
    const trophy = model.worldObjects.find((o) => o.objectId === "custom_trophy");
    expect(trophy).not.toBeUndefined();
    expect(trophy!.generated).not.toBeNull();
    expect(trophy!.generatedParts).not.toBeNull();
    expect(trophy!.generatedParts!.length).toBe(3); // box -> cylinder -> cup
    // The world-object scale IS the declared picking extent (spacing/ring math
    // sees the real footprint).
    expect(trophy!.generatedHitbox).toEqual({ x: 0.3, y: 0.5, z: 0.3 });
    expect(trophy!.scale).toEqual({ x: 0.3, y: 0.5, z: 0.3 });
    // The proc.* id is not a catalog id, but the object is NOT "unknown" — it
    // renders from its own declarative definition, so no neutral notice is due.
    expect(trophy!.unknownAsset).toBe(false);
    expect(isKnownAsset(trophy!.assetId)).toBe(false);
    // Placement still comes from the anchor registry (objectId/anchor kept).
    const anchor = ANCHOR_REGISTRY.get("office_desk_01")!;
    expect(trophy!.position).toEqual(anchor.position);
    // Generated labels stay null (server text never reaches the page).
    expect(trophy!.label).toBeNull();
  });

  it("DEF-079: a REAL thin ice-pick generated object builds with generatedParts and is NOT dropped to the neutral placeholder", () => {
    // The published thin def (dims {0.04,0.32,0.04}, blade scale {0.005,0.1,0.005})
    // is exactly the geometry the pre-fix 0.05 m client floor rejected — the
    // object rendered as gray fallback with unknownAsset=true. Post-fix it
    // builds as a normal generated object at the drop point.
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects = [
      ...bootstrap.scene.worldObjects,
      makeProcWorldObject({
        objectId: "bronze_ceremonial_ice_pick",
        generated: makeIcePickDefinition(),
      }),
    ];
    const model = buildInvestigationScene(bootstrap);
    const pick = model.worldObjects.find((o) => o.objectId === "bronze_ceremonial_ice_pick")!;
    expect(pick.unknownAsset).toBe(false);
    expect(pick.generated).not.toBeNull();
    expect(pick.generatedParts).not.toBeNull();
    expect(pick.generatedParts!.length).toBe(2); // handle + blade — never the single fallback box
    expect(pick.generatedParts![0].color).toBe("#8a5a2b"); // real handle wood color, not FALLBACK_COLOR
    expect(pick.generatedParts![1].color).not.toBe(FALLBACK_COLOR);
    expect(pick.generatedHitbox).toEqual({ x: 0.15, y: 0.66, z: 0.15 });
    // A genuinely collapsed clump still falls back at the SAME drop point.
    const clumped = makeProcWorldObject({
      objectId: "ice_pick_clump",
      generated: makeTrophyDefinition(),
    });
    for (const part of (clumped.generated as GeneratedAssetDefinition).parts) {
      part.transform.position = { x: 0, y: 0, z: 0 };
      part.transform.scale = { x: 0.001, y: 0.001, z: 0.001 };
    }
    const clumpBootstrap = makeBootstrap();
    clumpBootstrap.scene.worldObjects = [...clumpBootstrap.scene.worldObjects, clumped];
    const clumpModel = buildInvestigationScene(clumpBootstrap);
    const clumpObj = clumpModel.worldObjects.find((o) => o.objectId === "ice_pick_clump")!;
    expect(clumpObj.unknownAsset).toBe(true);
    expect(clumpObj.generatedParts).toBeNull();
  });

  it("a generated object is interactable ONLY when its DTO interaction is non-empty (payload-driven)", () => {
    const quiet = buildInvestigationScene(trophyBootstrap());
    const quietTrophy = quiet.worldObjects.find((o) => o.objectId === "custom_trophy")!;
    expect(quietTrophy.interaction).toBe("");
    expect(quietTrophy.interactionWorks).toBe(false);

    const interactive = makeProcWorldObject({ interaction: "inspect" });
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, interactive];
    const model = buildInvestigationScene(bootstrap);
    const loudTrophy = model.worldObjects.find((o) => o.objectId === "custom_trophy")!;
    expect(loudTrophy.interaction).toBe("inspect");
    expect(loudTrophy.interactionWorks).toBe(true);
  });

  it("an INVALID generated block falls back to the neutral primitive while neighbors still render", () => {
    const broken = makeProcWorldObject();
    const tampered: GeneratedAssetDefinition = makeTrophyDefinition();
    tampered.parts[0].primitive = "capsule" as never; // rejected by the client gate
    broken.generated = tampered;
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, broken];
    const model = buildInvestigationScene(bootstrap);

    expect(model.worldObjects).toHaveLength(10);
    const trophy = model.worldObjects.find((o) => o.objectId === "custom_trophy")!;
    expect(trophy.generated).toBeNull();
    expect(trophy.generatedParts).toBeNull();
    expect(trophy.unknownAsset).toBe(true);
    expect(trophy.primitiveKind).toBe(FALLBACK_ASSET.primitiveKind);
    expect(trophy.color).toBe(FALLBACK_COLOR);
    expect(trophy.scale).toEqual({ x: FALLBACK_ASSET.scale.x, y: FALLBACK_ASSET.scale.y, z: FALLBACK_ASSET.scale.z });
    // The other nine golden objects are untouched (neighbors still render).
    expect(new Set(model.worldObjects.map((o) => o.objectId)).size).toBe(10);
    expect(model.worldObjects.some((o) => o.objectId === "kitchen_knife" && o.interactionWorks)).toBe(true);
  });

  it("a NON-proc object carrying a generated block IGNORES it (catalog identity wins)", () => {
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects = [
      ...bootstrap.scene.worldObjects,
      makeWorldObject({
        objectId: "vase_with_block",
        assetId: "PROP_VASE_01",
        anchor: "dining_table",
        interaction: "",
        evidenceId: null,
        generated: makeTrophyDefinition(),
      }),
    ];
    const model = buildInvestigationScene(bootstrap);
    const vase = model.worldObjects.find((o) => o.objectId === "vase_with_block")!;
    expect(vase.generated).toBeNull();
    expect(vase.generatedParts).toBeNull();
    expect(vase.unknownAsset).toBe(false);
    // Catalog geometry is fully intact (cylinder vase path, not the trophy).
    expect(vase.compositeKind).toBeNull();
    expect(vase.color).toBe(resolveAsset("PROP_VASE_01").color);
    // Deterministic: the same bootstrap builds a deep-equal model again.
    expect(buildInvestigationScene(bootstrap)).toEqual(model);
  });

  it("generated parts are deterministic: identical bootstraps build deep-equal parts", () => {
    expect(buildInvestigationScene(trophyBootstrap())).toEqual(buildInvestigationScene(trophyBootstrap()));
    const first = buildInvestigationScene(trophyBootstrap()).worldObjects.find((o) => o.objectId === "custom_trophy")!;
    const second = buildInvestigationScene(trophyBootstrap()).worldObjects.find((o) => o.objectId === "custom_trophy")!;
    expect(first.generatedParts).toEqual(second.generatedParts);
    expect(first.generatedParts!.length).toBe(3);
  });
});

describe("applyKnowledgeToSceneModel — DEF-072 live knowledge merge", () => {
  function byId(model: InvestigationSceneModel) {
    return new Map(model.worldObjects.map((o) => [o.objectId, o]));
  }

  it("flips the knife's discovered/read flags from the server knowledge set", () => {
    const model = buildInvestigationScene(makeBootstrap()); // all flags false
    const knife = byId(model).get("kitchen_knife")!;
    expect(knife.discovered).toBe(false);
    expect(knife.read).toBe(false);

    const merged = applyKnowledgeToSceneModel(model, {
      discoveredEvidenceIds: ["forensic_knife_match_01"],
      readEvidenceIds: ["forensic_knife_match_01"],
    });

    const mergedKnife = byId(merged).get("kitchen_knife")!;
    expect(mergedKnife.discovered).toBe(true);
    expect(mergedKnife.read).toBe(true);
    // Only the knife's flags changed — the laptop digest stays as built.
    expect(byId(merged).get("apartment_laptop")!.discovered).toBe(false);
    expect(byId(merged).get("apartment_laptop")!.read).toBe(false);
  });

  it("leaves decorative / no-evidence objects completely untouched", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const before = byId(model).get("vase_01")!;
    const merged = applyKnowledgeToSceneModel(model, {
      discoveredEvidenceIds: ["forensic_knife_match_01"],
      readEvidenceIds: ["forensic_knife_match_01"],
    });
    // No-evidence objects keep their exact object identity (reference-stable).
    expect(byId(merged).get("vase_01")).toBe(before);
    expect(byId(merged).get("apartment_table")).toBe(byId(model).get("apartment_table"));
  });

  it("is identity-preserving and idempotent when the flags already match", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const knowledge = { discoveredEvidenceIds: [], readEvidenceIds: [] };
    // A no-change merge returns THE SAME model reference (callers skip re-renders).
    expect(applyKnowledgeToSceneModel(model, knowledge)).toBe(model);
    // Applying the merge twice never duplicates or drops objects.
    const once = applyKnowledgeToSceneModel(model, {
      discoveredEvidenceIds: ["forensic_knife_match_01"],
      readEvidenceIds: ["forensic_knife_match_01"],
    });
    const twice = applyKnowledgeToSceneModel(once, {
      discoveredEvidenceIds: ["forensic_knife_match_01"],
      readEvidenceIds: ["forensic_knife_match_01"],
    });
    expect(once).toEqual(twice);
    expect(once.worldObjects.map((o) => o.objectId)).toEqual(model.worldObjects.map((o) => o.objectId));
    expect(new Set(once.worldObjects.map((o) => o.objectId)).size).toBe(once.worldObjects.length);
  });

  it("keeps unchanged objects by reference and replaces only the flipped one", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const merged = applyKnowledgeToSceneModel(model, {
      discoveredEvidenceIds: ["forensic_knife_match_01"],
      readEvidenceIds: [],
    });
    // Unchanged evidence objects keep their identity; the knife is replaced.
    expect(byId(merged).get("letter_opener")).toBe(byId(model).get("letter_opener"));
    expect(byId(merged).get("scissors")).toBe(byId(model).get("scissors"));
    expect(byId(merged).get("kitchen_knife")).not.toBe(byId(model).get("kitchen_knife")!);
  });
});