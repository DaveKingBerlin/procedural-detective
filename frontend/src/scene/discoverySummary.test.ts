import { describe, expect, it } from "vitest";
import { buildInvestigationScene } from "./buildInvestigationScene";
import { objectiveText, summarizeDiscovery } from "./discoverySummary";
import { makeBootstrap, makeWorldObject, stripUndiscoveredEvidenceIds } from "./testFixtures";

/**
 * Player-facing discovery summary + objective updates (Phase 8 F): pure
 * derivation from server-derived knowledge, world objects and read records.
 */

const OBJECTS = [
  { evidenceId: "forensic_knife_match_01", label: "Kitchen knife", objectId: "kitchen_knife" },
  { evidenceId: "email_thomas_01", label: "Laptop", objectId: "apartment_laptop" },
  { evidenceId: null, label: "Door", objectId: "apartment_door" },
];

const RECORD_TITLES = new Map<string, string>([
  ["email_thomas_01", "Re: the missing funds"],
]);

describe("summarizeDiscovery", () => {
  it("lists discovered entries sorted by id with titles and read flags", () => {
    const summary = summarizeDiscovery(
      ["email_thomas_01", "forensic_knife_match_01"],
      ["email_thomas_01"],
      OBJECTS,
      RECORD_TITLES,
    );
    expect(summary.entries.map((e) => e.evidenceId)).toEqual([
      "email_thomas_01",
      "forensic_knife_match_01",
    ]);
    expect(summary.entries[0].title).toBe("Re: the missing funds"); // record cache wins
    expect(summary.entries[0].read).toBe(true);
    expect(summary.entries[1].title).toBe("Kitchen knife"); // object label fallback
    expect(summary.entries[1].read).toBe(false);
    expect(summary.discoveredCount).toBe(2);
  });

  it("is stable under reordering of discovered ids", () => {
    const a = summarizeDiscovery(["a", "b"], [], OBJECTS, new Map());
    const b = summarizeDiscovery(["b", "a"], [], OBJECTS, new Map());
    expect(a.entries.map((e) => e.evidenceId)).toEqual(b.entries.map((e) => e.evidenceId));
  });

  it("falls back to the raw evidence id when nothing readable is known", () => {
    const summary = summarizeDiscovery(["mystery_01"], [], OBJECTS, new Map());
    expect(summary.entries[0].title).toBe("mystery_01");
  });
});

describe("objectiveText", () => {
  const empty = summarizeDiscovery([], [], OBJECTS, new Map());
  const partial = summarizeDiscovery(["forensic_knife_match_01"], [], OBJECTS, new Map());

  it("starts with the find-evidence objective and the scene-first hint", () => {
    const text = objectiveText(empty, false);
    // The leading phrase is pinned by QA e2e specs — keep it intact.
    expect(text).toContain("Find evidence, then accuse someone.");
    // Phase 8_1: clicking objects in the scene is the PRIMARY method.
    expect(text).toContain("Click objects in the 3D scene to inspect them");
    expect(text).toContain("accessibility fallback");
  });

  it("updates to a discovered count once the player has interacted (PD-SEC-01: no pre-reveal denominator)", () => {
    expect(objectiveText(partial, true)).toContain("Discovered 1 evidence items");
    expect(objectiveText(partial, true)).toContain("clicking objects in the scene");
    expect(objectiveText(partial, true)).toContain("make your accusation");
    // The old "X / Y" form derived Y from pre-reveal evidence ids — those ids
    // are stripped under PD-SEC-01, so the count is never a fraction.
    expect(objectiveText(partial, true)).not.toContain("/");
  });

  it("uses the same honest bare count when no discoverable context is known", () => {
    const orphan = summarizeDiscovery(["x"], [], [], new Map());
    expect(objectiveText(orphan, true)).toBe(
      "Discovered 1 evidence items — keep clicking objects in the scene, then make your accusation when you are ready.",
    );
  });
});

describe("scene integration — the golden fixture summary", () => {
  it("the canned bootstrap starts with zero discovered evidence (pre-reveal ids stripped)", () => {
    const model = buildInvestigationScene(
      stripUndiscoveredEvidenceIds(makeBootstrap()),
    );
    const summary = summarizeDiscovery([], [], model.worldObjects, new Map());
    expect(summary.entries).toEqual([]);
    expect(summary.discoveredCount).toBe(0);
  });

  it("co-located objects resolve through the spacing helper (table + vase)", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const table = model.worldObjects.find((o) => o.objectId === "apartment_table")!;
    const vase = model.worldObjects.find((o) => o.objectId === "vase_01")!;
    expect(table.position).not.toEqual(vase.position);
    // The vase is lifted onto the table surface (not collapsed inside it).
    expect(vase.position.y).toBeGreaterThan(table.position.y);
    expect(table.position).toEqual({ x: 1.25, y: 0.9, z: -1.0 });
  });
});

describe("applySharedAnchorSpacing — registry purity and determinism", () => {
  it("single-object anchors keep the exact registry position", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const knife = model.worldObjects.find((o) => o.objectId === "kitchen_knife")!;
    expect(knife.position).toEqual({ x: -2.9, y: 0.95, z: 2.2 }); // kitchen_counter
  });

  it("co-located objects fan out deterministically regardless of input order", () => {
    const bootstrapA = makeBootstrap();
    const bootstrapB = makeBootstrap();
    bootstrapB.scene.worldObjects = [...bootstrapB.scene.worldObjects].reverse();
    const a = buildInvestigationScene(bootstrapA).worldObjects;
    const b = buildInvestigationScene(bootstrapB).worldObjects;
    expect(a).toEqual(b);
  });

  it("leaves a single rebuilt object untouched when it is the group surface", () => {
    const model = buildInvestigationScene(
      makeBootstrap({
        scene: {
          ...makeBootstrap().scene,
          worldObjects: [makeWorldObject({ objectId: "only_table", anchor: "dining_table" })],
        },
      }),
    );
    expect(model.worldObjects[0].position).toEqual({ x: 1.25, y: 0.9, z: -1.0 });
  });
});