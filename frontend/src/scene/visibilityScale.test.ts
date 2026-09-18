import { describe, expect, it } from "vitest";
import { buildInvestigationScene, kitEvidenceRenderScale } from "./buildInvestigationScene";
import { makeBootstrap, makeOfficeBootstrap } from "./testFixtures";

/**
 * Phase 15 Track B — evidence legibility scale in the larger environment kits.
 *
 * Guarantees under test:
 *  - the apartment kit ALWAYS renders at scale 1 (golden scene byte-identical);
 *  - non-evidence objects in other kits never scale;
 *  - discoverable, interactable evidence below the legibility floor scales up
 *    deterministically (bounded by MAX_EVIDENCE_RENDER_SCALE);
 *  - the same (kit, object) pair always yields the same factor.
 */

describe("kitEvidenceRenderScale — deterministic evidence legibility factor", () => {
  it("keeps the apartment kit at factor 1 for every evidence object", () => {
    expect(kitEvidenceRenderScale("apartment", "forensic_knife_match_01", true, { x: 0.24, y: 0.024, z: 0.045 })).toBe(1);
  });

  it("never scales non-evidence objects, even in a large kit", () => {
    expect(kitEvidenceRenderScale("warehouse", null, true, { x: 0.24, y: 0.024, z: 0.045 })).toBe(1);
  });

  it("never scales evidence without a published interaction affordance", () => {
    expect(kitEvidenceRenderScale("mansion", "e_01", false, { x: 0.1, y: 0.1, z: 0.1 })).toBe(1);
  });

  it("scales undersized evidence deterministically in the office kit", () => {
    const factor = kitEvidenceRenderScale("office", "e_knife", true, { x: 0.24, y: 0.024, z: 0.045 });
    expect(factor).toBeGreaterThan(1);
    expect(factor).toBe(kitEvidenceRenderScale("office", "e_knife", true, { x: 0.24, y: 0.024, z: 0.045 }));
  });

  it("is bounded by MAX_EVIDENCE_RENDER_SCALE (a wristwatch never becomes a room prop)", () => {
    const factor = kitEvidenceRenderScale("hotel_suite", "e_watch", true, { x: 0.03, y: 0.03, z: 0.008 });
    expect(factor).toBeLessThanOrEqual(3);
    expect(factor).toBeGreaterThan(1);
  });

  it("treats degenerate/absent scales as no-scale (never Infinity)", () => {
    expect(kitEvidenceRenderScale("warehouse", "e_x", true, { x: 0, y: 0, z: 0 })).toBe(1);
  });

  it("leaves already-legible evidence at factor 1", () => {
    expect(kitEvidenceRenderScale("warehouse", "e_box", true, { x: 0.6, y: 0.2, z: 0.3 })).toBe(1);
  });

  it("keeps near-floor evidence close to its natural size (no big jumps)", () => {
    // The catalog laptop (0.42 longest axis) sits just below the 0.45 floor —
    // the factor must stay small, never a double-digit scale-up.
    const factor = kitEvidenceRenderScale("warehouse", "e_laptop", true, { x: 0.42, y: 0.03, z: 0.28 });
    expect(factor).toBeGreaterThanOrEqual(1);
    expect(factor).toBeLessThan(1.15);
  });
});

describe("renderScale — carried through the scene model build", () => {
  it("apartment golden bootstrap: every object renders at scale 1 (byte-identical)", () => {
    const model = buildInvestigationScene(makeBootstrap());
    expect(model.environmentId).toBe("apartment");
    for (const obj of model.worldObjects) {
      expect(obj.renderScale, `${obj.objectId} stays at 1 in the apartment`).toBe(1);
    }
  });

  it("office bootstrap: evidence objects carry a renderScale > 1, furniture stays 1", () => {
    const model = buildInvestigationScene(makeOfficeBootstrap());
    expect(model.environmentId).toBe("office");
    const knife = model.worldObjects.find((o) => o.objectId === "office_desk_knife");
    const table = model.worldObjects.find((o) => o.objectId === "office_table");
    const body = model.worldObjects.find((o) => o.objectId === "office_body");
    expect(knife?.evidenceId, "knife is evidence").not.toBeNull();
    expect(knife!.renderScale).toBeGreaterThan(1);
    expect(table!.renderScale, "furniture never scales").toBe(1);
    expect(body!.renderScale, "victim body never scales").toBe(1);
  });

  it("the model renderScale is a pure function of kit + object (repeatable)", () => {
    const first = buildInvestigationScene(makeOfficeBootstrap());
    const second = buildInvestigationScene(makeOfficeBootstrap());
    expect(first.worldObjects.map((o) => [o.objectId, o.renderScale])).toEqual(
      second.worldObjects.map((o) => [o.objectId, o.renderScale]),
    );
  });
});