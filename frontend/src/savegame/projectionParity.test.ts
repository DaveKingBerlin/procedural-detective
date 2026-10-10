import { describe, expect, it } from "vitest";
import canonical from "./fixtures/v1_demo_apartment.pdcase.json";
import realInteraction from "./fixtures/v1_real_interaction_shape.pdcase.json";
import { parseSavegameV1, utf8ByteLength } from "./savegameV1";

/**
 * Phase32-Fix2 §17 — PROJECTION PARITY GUARD.
 *
 * The same logical world objects appear in TWO projections of one document:
 *
 *   - `case.publicCase.worldGraph.placements[]`   (the public dossier)
 *   - `case.scene.worldObjects[]`                 (the scene/world projection)
 *
 * Fields that are INTENTIONALLY MIRRORED (must match exactly, §17):
 *   - objectId     (identity)
 *   - assetId      (the catalog asset — both projections carry it)
 *   - locationId   (where the object is)
 *   - anchor       (where in that location)
 *   - interaction  (the affordance label; "" = decorative)
 *   - evidenceId   (both-null OR the same string — verified against the
 *                   placements->public evidence and worldObjects->record sets)
 *
 * Fields that INTENTIONALLY DIFFER (documented, NOT forced to match):
 *   - `subtype`        — worldObjects-ONLY: the placements projection has NO
 *                        subtype key at all (verify: placements carry
 *                        objectId/assetId/locationId/anchor/interaction/
 *                        evidenceId only). The backend world-object
 *                        projection ALWAYS emits subtype (null when none,
 *                        publication.py:904). NOT comparable.
 *   - `assetType`      — worldObjects-ONLY (derived by the backend
 *                        `_asset_type_for`, publication.py:563).
 *   - `discovered` / `read` — worldObjects-ONLY player-knowledge flags.
 *   - `generated` / `displayLabel` — worldObjects-ONLY asset blocks.
 *
 * ABSENT-vs-NULL normalization (DEF-076 / ADV-32F2-03): the production
 * validator treats an ABSENT optional `evidenceId` exactly like an explicit
 * `null` (`requireBoundedNullableString` returns null for BOTH undefined and
 * null), so `absent == null` is EQUAL for parity. `fixtureViews` mirrors that
 * with `?? null` on BOTH projections — the coercion is coherent with the
 * validator, not a masking of a drift. The null/absent equivalence is pinned
 * by a dedicated test below; the validator behavior is NOT changed.
 *
 * The guard is run over BOTH the canonical demo fixture and the new
 * sanitized real-interaction fixture (Phase32-Fix2 §14).
 */

interface PlacementView {
  objectId: string;
  assetId: string;
  locationId: string;
  anchor: string;
  interaction: string;
  evidenceId: string | null;
}

interface WorldObjectView {
  objectId: string;
  assetId: string;
  locationId: string;
  anchor: string;
  interaction: string;
  evidenceId: string | null;
  subtype: string | null;
}

function fixtureViews(doc: any): { placements: Map<string, PlacementView>; worldObjects: WorldObjectView[] } {
  // Accept BOTH the raw SavegameV1 document (`{ case: { publicCase, scene } }`)
  // and the normalized SavedCaseDefinition (`{ publicCase, scene }` directly).
  const publicCase = doc.case?.publicCase ?? doc.publicCase;
  const scene = doc.case?.scene ?? doc.scene;
  const placements = new Map<string, PlacementView>();
  for (const p of publicCase.worldGraph.placements as Array<Record<string, unknown>>) {
    placements.set(p.objectId as string, {
      objectId: p.objectId as string,
      assetId: p.assetId as string,
      locationId: p.locationId as string,
      anchor: p.anchor as string,
      interaction: p.interaction as string,
      evidenceId: (p.evidenceId as string | null) ?? null,
    });
  }
  const worldObjects = (scene.worldObjects as Array<Record<string, unknown>>).map((w) => ({
    objectId: w.objectId as string,
    assetId: w.assetId as string,
    locationId: w.locationId as string,
    anchor: w.anchor as string,
    interaction: w.interaction as string,
    evidenceId: (w.evidenceId as string | null) ?? null,
    subtype: (w.subtype as string | null) ?? null,
  }));
  return { placements, worldObjects };
}

/** The EXACT mirrored key set a placement projection must carry (DEF-076 /
 *  ADV-32F2-03): objectId, assetId, locationId, anchor, interaction,
 *  evidenceId — and NOTHING else. The intentionally-differing fields
 *  (subtype / assetType / discovered / read / generated / displayLabel) are
 *  worldObjects-ONLY and must never appear on a placement. */
const MIRRORED_PLACEMENT_KEYS = [
  "anchor",
  "assetId",
  "evidenceId",
  "interaction",
  "locationId",
  "objectId",
];

/** Assert every placement in the document (raw SavegameV1 OR normalized
 *  SavedCaseDefinition) carries EXACTLY the six mirrored keys. The normalized
 *  projection is rebuilt by `validatePlacement` with exactly those six keys
 *  (evidenceId normalized to null when absent), so the assertion holds for
 *  both the raw fixture and the production-parsed definition. */
function assertPlacementKeyset(doc: any): void {
  const publicCase = doc.case?.publicCase ?? doc.publicCase;
  for (const placement of publicCase.worldGraph.placements as Array<Record<string, unknown>>) {
    expect(Object.keys(placement).sort()).toEqual(MIRRORED_PLACEMENT_KEYS);
  }
}

function assertParity(doc: any): void {
  const { placements, worldObjects } = fixtureViews(doc);

  // DEF-076 / ADV-32F2-03: the placement projection must be EXACTLY the
  // mirrored key set — no worldObjects-only field may leak onto a placement.
  // Previously asserted only for the canonical demo fixture; now enforced for
  // EVERY fixture run through the parity guard (demo + real-interaction).
  assertPlacementKeyset(doc);

  // Every world object has a placement (identity integrity — the production
  // validator enforces this too; the guard re-asserts it independently).
  for (const worldObject of worldObjects) {
    expect(placements.has(worldObject.objectId)).toBe(true);
  }
  // And every placement has a matching world object (full bijection for the
  // fixtures under test).
  for (const placement of placements.values()) {
    expect(worldObjects.some((w) => w.objectId === placement.objectId)).toBe(true);
  }

  // The MIRRORED fields must match exactly between the two projections.
  for (const worldObject of worldObjects) {
    const placement = placements.get(worldObject.objectId)!;
    expect(worldObject.assetId).toBe(placement.assetId);
    expect(worldObject.locationId).toBe(placement.locationId);
    expect(worldObject.anchor).toBe(placement.anchor);
    // interaction parity: the same affordance label on BOTH projections
    // (""-decorative must be "" on both — this is the §4 real-file shape).
    expect(worldObject.interaction).toBe(placement.interaction);
    // evidenceId parity: both-null OR equal string (mirroring the nullable
    // contract; a drift here would break the two projections apart).
    if (placement.evidenceId === null) {
      expect(worldObject.evidenceId).toBeNull();
    } else {
      expect(worldObject.evidenceId).toBe(placement.evidenceId);
    }
  }
}

describe("Phase32-Fix2 §17 — projection parity guard", () => {
  it("canonical demo fixture: mirrored fields match, documented worldObjects-only fields are NOT forced", () => {
    assertParity(canonical);
    // Sanity: the demo fixture DOES carry worldObjects-only fields that the
    // parity guard intentionally does not compare (assetType / subtype /
    // discovered / read / generated / displayLabel).
    const demo = canonical as any;
    const worldObject = demo.case.scene.worldObjects.find((w: any) => w.objectId === "kitchen_knife");
    expect(worldObject.assetType).toBe("sharp_weapon");
    expect(worldObject.subtype).toBe("sharp_weapon");
    expect(worldObject.discovered).toBe(true);
    expect(worldObject.read).toBe(true);
    // The placement has NO subtype / assetType / discovered / read keys at all
    // (the shared `assertPlacementKeyset` inside `assertParity` above already
    // enforces the exact six-key set for EVERY placement; this explicit check
    // documents the canonical kitchen_knife example for readers).
    const placement = demo.case.publicCase.worldGraph.placements.find((p: any) => p.objectId === "kitchen_knife");
    expect(Object.keys(placement).sort()).toEqual(MIRRORED_PLACEMENT_KEYS);
  });

  it("canonical demo fixture survives the production parser with parity intact", () => {
    const text = JSON.stringify(canonical);
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    assertParity(JSON.parse(JSON.stringify(definition)));
  });

  it("sanitized real-interaction fixture: the FIVE empty interactions are empty on BOTH projections", () => {
    const { placements, worldObjects } = fixtureViews(realInteraction);
    const emptyPlacementIds = [...placements.values()].filter((p) => p.interaction === "").map((p) => p.objectId).sort();
    const emptyWorldObjectIds = worldObjects.filter((w) => w.interaction === "").map((w) => w.objectId).sort();
    expect(emptyPlacementIds).toEqual(["apartment_door", "apartment_lamp", "apartment_table", "vase_01", "victim_body_placeholder"]);
    expect(emptyWorldObjectIds).toEqual(emptyPlacementIds);
    // Full mirrored-field parity on the interaction shape too.
    assertParity(realInteraction);
  });

  it("sanitized real-interaction fixture survives the production parser with parity intact", () => {
    const text = JSON.stringify(realInteraction);
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    assertParity(JSON.parse(JSON.stringify(definition)));
  });

  it("absent evidenceId on a placement is normalized to null (absent==null parity; validator unchanged)", () => {
    // DEF-076 / ADV-32F2-03: `requireBoundedNullableString` treats an ABSENT
    // optional `evidenceId` exactly like an explicit `null` (undefined -> null),
    // so `absent == null` is EQUAL for parity. This test pins that the
    // production parser STILL parses and normalizes to null when a placement
    // omits the key entirely — the validator behavior is NOT changed, only the
    // guard documents the equivalence.
    const doc = JSON.parse(JSON.stringify(canonical)) as any;
    const kitchenPlacement = doc.case.publicCase.worldGraph.placements.find((p: any) => p.objectId === "kitchen_knife");
    const kitchenWorldObject = doc.case.scene.worldObjects.find((w: any) => w.objectId === "kitchen_knife");
    // Sanity: the canonical demo carries a STRING evidenceId on both projections
    // (kitchen_knife -> forensic_knife_match_01).
    expect(kitchenPlacement.evidenceId).toBe("forensic_knife_match_01");
    expect(kitchenWorldObject.evidenceId).toBe("forensic_knife_match_01");
    // Drop the key from BOTH projections to keep the mirror coherent.
    delete kitchenPlacement.evidenceId;
    delete kitchenWorldObject.evidenceId;
    const text = JSON.stringify(doc);
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    // The normalized placement carries an EXPLICIT null (not the raw absent).
    const normalizedPlacement = definition.publicCase.worldGraph.placements.find((p) => p.objectId === "kitchen_knife")!;
    expect(normalizedPlacement.evidenceId).toBeNull();
    // And parity still holds on the normalized definition.
    assertParity(JSON.parse(JSON.stringify(definition)));
  });
});
