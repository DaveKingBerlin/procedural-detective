import { describe, expect, it } from "vitest";
import { applyKnowledgeToSceneModel, type InvestigationSceneModel, type SceneWorldObject } from "./buildInvestigationScene";
import { discoveredCaptionsForWorld } from "./objectCaption";

/**
 * Phase 15 Track B — discovered-object floating captions.
 *
 * Guarantees under test:
 *  - captions exist ONLY for objects the SERVER marked discovered,
 *  - the text comes ONLY from the read-record titles or the PUBLIC registry
 *    label (app-authored) — never from server-free-text/labels,
 *  - undiscovered evidence, unknown assets and label-less objects never leak,
 *  - output is sorted by objectId (deterministic).
 */

let seq = 0;
function worldObject(overrides: Partial<SceneWorldObject>): SceneWorldObject {
  seq += 1;
  const id = overrides.objectId ?? `obj_${seq}`;
  return {
    objectId: id,
    assetId: "PROP_KITCHEN_KNIFE_01",
    assetType: "sharp_weapon",
    subtype: "sharp_weapon",
    primitiveKind: "box",
    compositeKind: "knife",
    hitboxScale: 1,
    color: "#c8ccd4",
    scale: { x: 0.24, y: 0.024, z: 0.045 },
    label: "Kitchen knife",
    position: { x: 0, y: 0, z: 0 },
    rotation: { x: 0, y: 0, z: 0 },
    interaction: "inspect",
    interactionWorks: true,
    evidenceId: "forensic_knife_match_01",
    discovered: false,
    read: false,
    unknownAsset: false,
    generated: null,
    generatedParts: null,
    generatedHitbox: null,
    templateId: null,
    templateParts: null,
    templateHitbox: null,
    templateMaterial: null,
    templateState: null,
    renderScale: 1,
    ...overrides,
  };
}

function model(...objects: SceneWorldObject[]): InvestigationSceneModel {
  return {
    location: { locationId: "x", name: "x" },
    environmentId: "apartment",
    worldObjects: objects,
  };
}

describe("discoveredCaptionsForWorld — discovered-only scene captions", () => {
  it("captions ONLY objects the server marked discovered", () => {
    const knife = worldObject({ discovered: true });
    const laptop = worldObject({
      objectId: "apartment_laptop",
      evidenceId: "email_thomas_01",
      label: "Laptop",
      discovered: false,
    });
    const captions = discoveredCaptionsForWorld(model(knife, laptop).worldObjects, new Map());
    expect(captions).toEqual([{ objectId: "obj_1", text: "Kitchen knife" }]);
  });

  it("prefers the read-record title when the player has already seen it", () => {
    const knife = worldObject({ discovered: true, read: true });
    const titles = new Map([["forensic_knife_match_01", "Blood on the kitchen knife matches the victim"]]);
    const captions = discoveredCaptionsForWorld(model(knife).worldObjects, titles);
    expect(captions[0].text).toBe("Blood on the kitchen knife matches the victim");
  });

  it("never captions label-less / unknown assets even when discovered", () => {
    const unknown = worldObject({ discovered: true, evidenceId: "e_unknown", label: null, unknownAsset: true });
    expect(discoveredCaptionsForWorld(model(unknown).worldObjects, new Map())).toEqual([]);
  });

  it("never captions undiscovered evidence (no preview of hidden content)", () => {
    const undiscovered = worldObject({ discovered: false, evidenceId: "secret_01", label: "Letter opener" });
    const withTitles = new Map([["secret_01", "Motive hidden until discovery"]]);
    expect(discoveredCaptionsForWorld(model(undiscovered).worldObjects, withTitles)).toEqual([]);
  });

  it("sorts by objectId regardless of input order (deterministic)", () => {
    const b = worldObject({ objectId: "beta", label: "B", discovered: true });
    const a = worldObject({ objectId: "alpha", label: "A", discovered: true });
    const captions = discoveredCaptionsForWorld(model(b, a).worldObjects, new Map());
    expect(captions.map((c) => c.objectId)).toEqual(["alpha", "beta"]);
  });

  it("is empty for an empty world", () => {
    expect(discoveredCaptionsForWorld([], new Map())).toEqual([]);
  });

  it("carries only {objectId, text} — no ids/labels of non-discovered objects", () => {
    const knife = worldObject({ discovered: true });
    const hidden = worldObject({ objectId: "hidden_vault", label: "Safe", evidenceId: "e_vault", discovered: false });
    const captions = discoveredCaptionsForWorld(model(knife, hidden).worldObjects, new Map());
    expect(captions).toHaveLength(1);
    expect(captions[0].objectId).not.toBe("hidden_vault");
    expect(JSON.stringify(captions)).not.toContain("Safe");
  });
});

describe("DEF-072 — captions appear from the LIVE merged model (no reload)", () => {
  it("a knife discovery merges into the model and its caption shows immediately", () => {
    // The model starts undiscovered — as a live bootstrap would.
    const knife = worldObject({ objectId: "kitchen_knife", discovered: false, read: false });
    const vase = worldObject({ objectId: "vase_01", label: "Vase", evidenceId: null });
    const base = model(knife, vase);

    expect(discoveredCaptionsForWorld(base.worldObjects, new Map())).toEqual([]);

    // The discovery writes into the SceneModel the SAME way the session does:
    const merged = applyKnowledgeToSceneModel(base, {
      discoveredEvidenceIds: ["forensic_knife_match_01"],
      readEvidenceIds: ["forensic_knife_match_01"],
    });

    const captions = discoveredCaptionsForWorld(merged.worldObjects, new Map());
    expect(captions).toEqual([{ objectId: "kitchen_knife", text: "Kitchen knife" }]);
    // The no-evidence object never captions and never appears in the model set.
    expect(captions.map((c) => c.objectId)).not.toContain("vase_01");
    expect(merged.worldObjects.map((o) => o.objectId)).toEqual(["kitchen_knife", "vase_01"]);
  });

  it("prefers the read-record title once the record is read — still without a reload", () => {
    const knife = worldObject({ objectId: "kitchen_knife", discovered: false, read: false });
    const merged = applyKnowledgeToSceneModel(model(knife), {
      discoveredEvidenceIds: ["forensic_knife_match_01"],
      readEvidenceIds: ["forensic_knife_match_01"],
    });
    const titles = new Map([["forensic_knife_match_01", "Blood on the kitchen knife matches the victim"]]);
    const captions = discoveredCaptionsForWorld(merged.worldObjects, titles);
    expect(captions[0].text).toBe("Blood on the kitchen knife matches the victim");
  });
});