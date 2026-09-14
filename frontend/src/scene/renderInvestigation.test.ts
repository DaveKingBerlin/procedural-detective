import { describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines";
import type { InvestigationSceneModel } from "./buildInvestigationScene";
import { buildInvestigationScene } from "./buildInvestigationScene";
import {
  createInvestigationScene,
  meshNameFor,
  objectIdFromMeshName,
  type RenderOptions,
} from "./renderInvestigation";
import { makeBootstrap } from "./testFixtures";

/**
 * Babylon glue tests. All engine work uses the deterministic NullEngine —
 * no GPU, no DOM, no network. Real Babylon init failures are simulated by an
 * injected engine factory that throws.
 */

const EMPTY_MODEL: InvestigationSceneModel = {
  location: { locationId: "miller_apartment_kitchen", name: "Miller Apartment - Kitchen" },
  worldObjects: [],
};

const NOOP_CANVAS = {} as HTMLCanvasElement;

function nullEngineOptions(
  overrides: { onPick?: (objectId: string) => void } = {},
): RenderOptions {
  return {
    createEngine: () => new NullEngine(),
    cameraControl: false,
    startRenderLoop: () => {},
    ...overrides,
  };
}

describe("mesh naming: picking maps a mesh back to the object id", () => {
  it("encodes and decodes object ids deterministically", () => {
    expect(meshNameFor("kitchen_knife")).toBe("pd_obj_kitchen_knife");
    expect(objectIdFromMeshName(meshNameFor("kitchen_knife"))).toBe("kitchen_knife");
    expect(objectIdFromMeshName("pd_obj_apartment_laptop")).toBe("apartment_laptop");
  });

  it("never treats shell meshes or garbage as world objects", () => {
    expect(objectIdFromMeshName("floor_01")).toBeNull();
    expect(objectIdFromMeshName("pd_obj_")).toBeNull();
    expect(objectIdFromMeshName(null)).toBeNull();
    expect(objectIdFromMeshName(undefined)).toBeNull();
    expect(objectIdFromMeshName("")).toBeNull();
  });

  it("is identical for identical ids (object identity never from array index)", () => {
    expect(meshNameFor("kitchen_knife")).toBe(meshNameFor("kitchen_knife"));
    expect(objectIdFromMeshName(meshNameFor("a"))).not.toBe(objectIdFromMeshName(meshNameFor("b")));
  });
});

describe("createInvestigationScene", () => {
  it("returns ok:false with the message when the engine constructor throws", () => {
    const result = createInvestigationScene(NOOP_CANVAS, EMPTY_MODEL, {
      createEngine: () => {
        throw new Error("no gpu available");
      },
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.error).toBe("no gpu available");
  });

  it("builds an empty scene model on the NullEngine headlessly", () => {
    const result = createInvestigationScene(NOOP_CANVAS, EMPTY_MODEL, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    result.dispose();
  });

  it("instantiates one mesh per world object with registry geometry and colors", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const result = createInvestigationScene(NOOP_CANVAS, model, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    expect(result.scene.getNodeByName("pd_obj_kitchen_knife")).not.toBeNull();
    expect(result.scene.getNodeByName("pd_obj_apartment_laptop")).not.toBeNull();
    expect(result.scene.getNodeByName("pd_obj_vase_01")).not.toBeNull();
    expect(result.scene.getNodeByName("pd_obj_victim_body_placeholder")).not.toBeNull();
    // Shell meshes are named from the manifest, but never carry the object prefix.
    expect(result.scene.getNodeByName("pd_obj_floor_01")).toBeNull();

    // World objects are re-locatable through the deterministic id mapping.
    expect(objectIdFromMeshName(result.scene.getNodeByName("pd_obj_vase_01")!.name)).toBe("vase_01");
    result.dispose();
  });

  it("supports deterministic highlight toggling per object id", () => {
    const result = createInvestigationScene(
      NOOP_CANVAS,
      buildInvestigationScene(makeBootstrap()),
      nullEngineOptions(),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    expect(result.isObjectHighlighted("kitchen_knife")).toBe(false);
    result.setObjectHighlight("kitchen_knife", true);
    expect(result.isObjectHighlighted("kitchen_knife")).toBe(true);
    expect(result.isObjectHighlighted("vase_01")).toBe(false);
    result.setObjectHighlight("kitchen_knife", false);
    expect(result.isObjectHighlighted("kitchen_knife")).toBe(false);
    result.dispose();
  });

  it("dispose is idempotent-safe and never throws", () => {
    const result = createInvestigationScene(NOOP_CANVAS, EMPTY_MODEL, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    result.dispose();
    result.dispose();
  });
});

describe("Phase 8 hover affordance rings", () => {
  it("creates hidden highlight rings only for interactable world objects", () => {
    const result = createInvestigationScene(
      NOOP_CANVAS,
      buildInvestigationScene(makeBootstrap()),
      nullEngineOptions(),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    // Interactable objects get a hidden ring under the object.
    for (const objectId of ["kitchen_knife", "apartment_laptop", "vase_01"]) {
      const ring = result.scene.getNodeByName(`pd_ring_${objectId}`);
      expect(ring, `expected a ring for ${objectId}`).not.toBeNull();
      expect(ring!.isVisible).toBe(false);
    }

    // The non-interactable victim body gets NO ring.
    expect(result.scene.getNodeByName("pd_ring_victim_body_placeholder")).toBeNull();
    result.dispose();
  });
});