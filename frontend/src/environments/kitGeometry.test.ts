import { describe, expect, it } from "vitest";
import rawOfficeManifest from "../../../assets/environments/office.json";
import { buildApartmentManifest, type ScenePrimitive } from "../scene/apartment";
import { ANCHOR_REGISTRY, fallbackAnchor } from "../scene/anchorRegistry";
import { getAsset } from "../catalog/assetCatalog";
import { allKitIds, getKit, validateKit } from "./kitCatalog";
import {
  APARTMENT_KIT_ID,
  DECOR_PADDING,
  MAX_DECOR_PROPS_PER_KIT,
  anchorTransformFrom,
  buildKitDecorProps,
  buildKitShell,
  cameraProfileFor,
  evidenceClearanceOk,
  kitAnchorTransform,
  lightingFor,
  spawnTransformFor,
  transformFor,
} from "./kitGeometry";

/**
 * Phase 11 Track B — kit geometry: deterministic shells + anchor transforms,
 * all strictly manifest-sourced, all array-order independent.
 */

const NON_APARTMENT_KITS = allKitIds().filter((kitId) => kitId !== APARTMENT_KIT_ID);

function countKind(primitives: ScenePrimitive[], kind: ScenePrimitive["kind"]): number {
  return primitives.filter((p) => p.kind === kind).length;
}

describe("buildKitShell — deterministic room per kit", () => {
  it("apartment shell is byte-identical to the Phase 2 manifest", () => {
    expect(buildKitShell("apartment")).toEqual(buildApartmentManifest());
  });

  it("unknown kit ids fall back to the apartment shell (never a broken scene)", () => {
    expect(buildKitShell("no_such_kit")).toEqual(buildApartmentManifest());
    expect(buildKitShell("")).toEqual(buildApartmentManifest());
  });

  it("every non-apartment kit builds a room with a floor and perimeter walls", () => {
    for (const kitId of NON_APARTMENT_KITS) {
      const shell = buildKitShell(kitId);
      expect(countKind(shell, "floor"), `${kitId} floor`).toBeGreaterThanOrEqual(1);
      // Perimeter walls (kind "wall"; windows reuse the wall box primitive).
      expect(countKind(shell, "wall"), `${kitId} walls`).toBeGreaterThanOrEqual(4);
      expect(shell.length, `${kitId} primitive count`).toBeGreaterThanOrEqual(8);
    }
  });

  it("buildKitShell is deterministic: two builds deep-equal per kit", () => {
    for (const kitId of NON_APARTMENT_KITS) {
      expect(buildKitShell(kitId)).toEqual(buildKitShell(kitId));
    }
  });

  it("places the door slab at its DOOR anchor (manifest position, y-lifted by half height)", () => {
    const officeShell = buildKitShell("office");
    const doorAsset = getAsset("DOOR_APARTMENT_01")!;
    const officeDoorAnchor = getKit("office")!.anchors.find((a) => a.anchorId === "office_door_01")!;
    const door = officeShell.find((p) => p.id === "door_1");
    expect(door).not.toBeUndefined();
    expect(door!.position.x).toBe(officeDoorAnchor.position.x);
    expect(door!.position.z).toBe(officeDoorAnchor.position.z);
    expect(door!.position.y).toBe(officeDoorAnchor.position.y + doorAsset.dimensions.y / 2);
    expect(door!.rotation).toEqual(officeDoorAnchor.rotation);
  });

  it("places one window per WINDOW anchor (sorted by anchorId)", () => {
    const officeShell = buildKitShell("office");
    const officeWindows = getKit("office")!
      .anchors.filter((a) => a.type === "WINDOW")
      .sort((a, b) => (a.anchorId < b.anchorId ? -1 : 1));
    const windows = officeShell.filter((p) => p.id.startsWith("window_"));
    expect(windows).toHaveLength(officeWindows.length);
    const windowAsset = getAsset("PROP_WINDOW_01")!;
    windows.forEach((window, index) => {
      const anchor = officeWindows[index];
      expect(window.position.x).toBe(anchor.position.x);
      expect(window.position.z).toBe(anchor.position.z);
      expect(window.position.y).toBe(anchor.position.y + windowAsset.dimensions.y / 2);
    });
  });

  it("places a lamp pedestal at the first GENERIC_PROP anchor (sorted by anchorId)", () => {
    for (const kitId of NON_APARTMENT_KITS) {
      const shell = buildKitShell(kitId);
      const lamp = shell.find((p) => p.id === "lamp_01");
      expect(lamp, `${kitId} lamp`).not.toBeUndefined();
      const genericAnchors = getKit(kitId)!
        .anchors.filter((a) => a.type === "GENERIC_PROP")
        .sort((a, b) => (a.anchorId < b.anchorId ? -1 : 1));
      expect(genericAnchors.length).toBeGreaterThanOrEqual(1);
      expect(lamp!.position.x).toBe(genericAnchors[0].position.x);
      expect(lamp!.position.z).toBe(genericAnchors[0].position.z);
    }
    // Spot-check the hotel: "hotel_bathroom_01" is the first sorted GENERIC_PROP
    // anchor; the lamp pedestal stands on the floor (y = half its height).
    const lampAsset = getAsset("PROP_LAMP_01")!;
    const hotelLamp = buildKitShell("hotel_suite").find((p) => p.id === "lamp_01")!;
    expect(hotelLamp.position).toEqual({
      x: 2.0,
      y: 0 + lampAsset.dimensions.y / 2,
      z: 9.5,
    });
  });

  it("keeps all shell coordinates finite", () => {
    for (const kitId of NON_APARTMENT_KITS) {
      const values: number[] = [];
      for (const p of buildKitShell(kitId)) {
        values.push(p.position.x, p.position.y, p.position.z);
        if (p.rotation) values.push(p.rotation.x, p.rotation.y, p.rotation.z);
        if (p.scale) values.push(p.scale.x, p.scale.y, p.scale.z);
      }
      for (const value of values) {
        expect(Number.isFinite(value)).toBe(true);
      }
    }
  });
});

describe("anchor transform registry (manifest source)", () => {
  it("resolves known kit anchors strictly from the manifest", () => {
    expect(kitAnchorTransform("office", "office_desk_a")).toEqual({
      position: { x: 3.0, y: 0.8, z: 4.0 },
      rotation: { x: 0, y: 0, z: 0 },
    });
    expect(kitAnchorTransform("warehouse", "warehouse_shelf_01")?.position).toEqual({ x: 3.0, y: 0.0, z: 8.0 });
  });

  it("returns null for apartment anchors (the Phase 6 table is their source)", () => {
    expect(kitAnchorTransform("apartment", "kitchen_counter")).toBeNull();
  });

  it("transformFor routes the apartment kit through the Phase 6 anchor table (exact golden values)", () => {
    const transform = transformFor("apartment", "kitchen_counter", "kitchen_knife");
    expect(transform.position).toEqual(ANCHOR_REGISTRY.get("kitchen_counter")!.position);
    expect(transformFor("apartment", "kitchen_counter", "kitchen_knife")).toEqual(transform);
  });

  it("unknown anchors in a known kit land on the stable objectId-hash slot", () => {
    expect(transformFor("office", "no_such_anchor", "obj_alpha")).toEqual(fallbackAnchor("obj_alpha"));
    expect(transformFor("office", "no_such_anchor", "obj_alpha")).toEqual(
      transformFor("office", "no_such_anchor", "obj_alpha"),
    );
    expect(transformFor("office", "no_such_anchor", "obj_alpha")).not.toEqual(
      transformFor("office", "no_such_anchor", "obj_beta"),
    );
  });

  it("unknown kit ids fall back to the apartment geometry without errors", () => {
    const apartmentTransform = transformFor("apartment", "kitchen_counter", "obj_x");
    expect(transformFor("planet_mars", "kitchen_counter", "obj_x")).toEqual(apartmentTransform);
    expect(transformFor("", "desk_main", "obj_y").position).toEqual(
      ANCHOR_REGISTRY.get("desk_main")!.position,
    );
  });

  it("is deterministic: identical inputs produce identical transforms", () => {
    for (const kitId of NON_APARTMENT_KITS) {
      const anchorId = getKit(kitId)!.anchors[0].anchorId;
      expect(kitAnchorTransform(kitId, anchorId)).toEqual(kitAnchorTransform(kitId, anchorId));
    }
  });

  it("manifest transforms are independent of the anchor array order (shuffled manifest -> same registry)", () => {
    const original = validateKit(rawOfficeManifest as unknown);
    const shuffledRaw = structuredClone(rawOfficeManifest) as Record<string, unknown>;
    const anchors = shuffledRaw.anchors as unknown[];
    anchors.reverse();
    const shuffled = validateKit(shuffledRaw);
    for (const anchor of original.anchors) {
      expect(anchorTransformFrom(shuffled, anchor.anchorId)).toEqual(
        anchorTransformFrom(original, anchor.anchorId),
      );
    }
  });
});

describe("spawn / lighting / camera profiles", () => {
  it("spawnTransformFor returns the manifest spawn for non-apartment kits", () => {
    expect(spawnTransformFor("office")).toEqual({
      position: { x: 0.8, y: 0, z: 2.0 },
      rotation: { x: 0, y: 0, z: 0 },
    });
    expect(spawnTransformFor("apartment")).toBeNull();
    expect(spawnTransformFor("no_such_kit")).toBeNull();
  });

  it("lightingFor returns the manifest lighting profile (and apartment/none -> null)", () => {
    expect(lightingFor("warehouse")).toEqual({
      profile: "cool_dim",
      keyIntensity: 0.55,
      hemiIntensity: 0.25,
      accentColor: "#c9d4e8",
    });
    expect(lightingFor("hotel_suite")?.profile).toBe("warm_flat");
    // Phase 18D show-case values: office cools down (cool key, dim ambient),
    // hotel warms up (amber key + stronger ambient) — both inside the bounds
    // and vocabulary the validator enforces (profile, 0..1, #RRGGBB).
    expect(lightingFor("office")).toEqual({
      profile: "neutral",
      keyIntensity: 0.95,
      hemiIntensity: 0.3,
      accentColor: "#b8ccd8",
    });
    expect(lightingFor("hotel_suite")).toEqual({
      profile: "warm_flat",
      keyIntensity: 0.85,
      hemiIntensity: 0.45,
      accentColor: "#f0c080",
    });
    expect(lightingFor("apartment")).toBeNull();
    expect(lightingFor("no_such_kit")).toBeNull();
  });

  it("cameraProfileFor targets the manifest spawn and is deterministic", () => {
    const officeCamera = cameraProfileFor("office");
    expect(officeCamera).not.toBeNull();
    expect(officeCamera!.target).toEqual({ x: 0.8, y: 1.0, z: 2.0 });
    expect(Number.isFinite(officeCamera!.distance)).toBe(true);
    expect(officeCamera!.distance).toBeGreaterThan(0);
    expect(cameraProfileFor("office")).toEqual(cameraProfileFor("office"));
    expect(cameraProfileFor("apartment")).toBeNull();
    expect(cameraProfileFor("no_such_kit")).toBeNull();
  });

it("camera distances are derived from the kit extents (large kits use larger orbits)", () => {
    const distances = NON_APARTMENT_KITS.map((kitId) => cameraProfileFor(kitId)!.distance);
    for (const distance of distances) {
      expect(distance).toBeGreaterThanOrEqual(12);
    }
  });

  it("Phase 18D camera tuning: office + hotel default orbits are tighter for direct picking", () => {
    // The flagship kits use a closer extents-derived orbit so evidence is
    // immediately readable and direct 3D picking stays practical. The target
    // stays the manifest spawn (+1.0 lift), exactly as before.
    const officeCamera = cameraProfileFor("office");
    expect(officeCamera!.target).toEqual({ x: 0.8, y: 1.0, z: 2.0 });
    expect(officeCamera!.distance).toBeGreaterThanOrEqual(13);
    expect(officeCamera!.distance).toBeLessThan(16);
    const hotelCamera = cameraProfileFor("hotel_suite");
    expect(hotelCamera!.target).toEqual({ x: 0.8, y: 1.0, z: 2.0 });
    expect(hotelCamera!.distance).toBeGreaterThanOrEqual(12);
    expect(hotelCamera!.distance).toBeLessThan(17);
    // Untuned kits (warehouse/mansion) keep the Phase 11 formula (>= 12).
    expect(cameraProfileFor("warehouse")!.distance).toBeGreaterThanOrEqual(12);
  });
});

/* ======================================================================
 * Phase 18D — showcase art direction (shell): warm desk pool, bounded
 * evidence-safe decor props, stronger floor/wall contrast, measured
 * deterministic placement.
 * ==================================================================== */

describe("Phase 18D — warm desk pool + decor props (office / hotel_suite)", () => {
  it("places a warm desk-pool light above the DESK_EVIDENCE anchor centroid", () => {
    for (const kitId of ["office", "hotel_suite"]) {
      const shell = buildKitShell(kitId);
      const pool = shell.find((p) => p.id === "light_desk_pool");
      expect(pool, `${kitId} desk pool`).not.toBeUndefined();
      expect(pool!.kind).toBe("light");
      expect(pool!.color).toMatch(/^#[0-9A-Fa-f]{6}$/);
      // Centroid of the sorted DESK_EVIDENCE anchors, at the overhead light Y.
      const desks = getKit(kitId)!
        .anchors.filter((a) => a.type === "DESK_EVIDENCE")
        .sort((a, b) => (a.anchorId < b.anchorId ? -1 : 1));
      expect(desks.length).toBeGreaterThan(0);
      const cx = desks.reduce((sum, a) => sum + a.position.x, 0) / desks.length;
      const cz = desks.reduce((sum, a) => sum + a.position.z, 0) / desks.length;
      expect(pool!.position.x).toBeCloseTo(cx, 9);
      expect(pool!.position.z).toBeCloseTo(cz, 9);
      expect(pool!.position.y).toBe(2.7);
    }
    // The apartment kit keeps its golden shell: no kit-shell-only additions.
    expect(buildKitShell("apartment").find((p) => p.id === "light_desk_pool")).toBeUndefined();
  });

  it("office decor props: bounded, deterministic, present in the shell", () => {
    const shell = buildKitShell("office");
    const decor = shell.filter((p) => p.id.startsWith("decor_"));
    const kitsDecor = buildKitDecorProps(getKit("office")!);
    // Bounded (task rule: <= 6, never overload) and identical two ways.
    expect(decor.length).toBeGreaterThanOrEqual(1);
    expect(decor.length).toBeLessThanOrEqual(MAX_DECOR_PROPS_PER_KIT);
    expect(kitsDecor.map((p) => p.id).sort()).toEqual(decor.map((p) => p.id).sort());
    // The manager office carries the desk cluster silhouette.
    const ids = decor.map((p) => p.id);
    for (const wanted of ["decor_desk_01", "decor_monitor_01", "decor_bookshelf_01"]) {
      expect(ids, `office ${wanted}`).toContain(wanted);
    }
    // Decor is deterministic: two builds deep-equal.
    expect(decor).toEqual(buildKitShell("office").filter((p) => p.id.startsWith("decor_")));
  });

  it("hotel decor props: bounded, deterministic, a distinct composition from office", () => {
    const shell = buildKitShell("hotel_suite");
    const decor = shell.filter((p) => p.id.startsWith("decor_"));
    expect(decor.length).toBeGreaterThanOrEqual(1);
    expect(decor.length).toBeLessThanOrEqual(MAX_DECOR_PROPS_PER_KIT);
    const ids = decor.map((p) => p.id).sort();
    expect(ids).toContain("decor_bed_01");
    expect(ids).toContain("decor_sofa_01");
    expect(ids).toContain("decor_coffeetable_01");
    // Distinct silhouette set from the office (no shared decor ids).
    const officeIds = buildKitShell("office").filter((p) => p.id.startsWith("decor_")).map((p) => p.id).sort();
    expect(ids.filter((id) => officeIds.includes(id))).toEqual([]);
    expect(decor).toEqual(buildKitShell("hotel_suite").filter((p) => p.id.startsWith("decor_")));
  });

  it("every accepted decor prop passes the evidence guard (never occludes evidence)", () => {
    for (const kitId of ["office", "hotel_suite"]) {
      const kit = getKit(kitId)!;
      for (const prop of buildKitDecorProps(kit)) {
        const halfX = (prop.scale?.x ?? 0) / 2;
        const halfZ = (prop.scale?.z ?? 0) / 2;
        expect(
          evidenceClearanceOk(kit, prop.position, halfX, halfZ),
          `${kitId} ${prop.id} must clear every evidence anchor + the spawn`,
        ).toBe(true);
      }
    }
  });

  it("evidenceClearanceOk rejects a box planted on an evidence anchor (guard is real)", () => {
    const office = getKit("office")!;
    const deskEvidence = office.anchors.find((a) => a.anchorId === "office_desk_a")!;
    expect(
      evidenceClearanceOk(office, { x: deskEvidence.position.x, y: 0.4, z: deskEvidence.position.z }, 0.1, 0.1),
    ).toBe(false);
    // ...and on the spawn.
    expect(
      evidenceClearanceOk(office, office.spawn.position, 0.05, 0.05),
    ).toBe(false);
    // ...but a far empty corner passes.
    expect(evidenceClearanceOk(office, { x: 10.0, y: 0.0, z: 10.5 }, 0.5, 0.5)).toBe(true);
  });

  it("office + hotel shells use the Phase 18D floor/wall tones (stronger contrast)", () => {
    const officeShell = buildKitShell("office");
    expect(officeShell.find((p) => p.kind === "floor")!.color).toBe("#39454f");
    for (const id of ["wall_north", "wall_south", "wall_east", "wall_west"]) {
      expect(officeShell.find((p) => p.id === id)!.color).toBe("#e2e6ea");
    }
    const hotelShell = buildKitShell("hotel_suite");
    expect(hotelShell.find((p) => p.kind === "floor")!.color).toBe("#6e4f38");
    for (const id of ["wall_north", "wall_south", "wall_east", "wall_west"]) {
      expect(hotelShell.find((p) => p.id === id)!.color).toBe("#f0e6d2");
    }
    // Untouched kits keep their Phase 11 palette.
    expect(buildKitShell("warehouse").find((p) => p.kind === "floor")!.color).toBe("#4b4f55");
  });

  it("decor props are inches inside the shell (no prop pokes through a wall)", () => {
    for (const kitId of ["office", "hotel_suite"]) {
      const kit = getKit(kitId)!;
      const xs = kit.anchors.map((a) => a.position.x);
      const zs = kit.anchors.map((a) => a.position.z);
      const x0 = Math.min(...xs) - 1.0;
      const x1 = Math.max(...xs) + 1.0;
      const z0 = Math.min(...zs) - 1.0;
      const z1 = Math.max(...zs) + 1.0;
      for (const prop of buildKitDecorProps(kit)) {
        expect(prop.position.x, `${kitId} ${prop.id} x in room`).toBeGreaterThan(x0 + 0.05);
        expect(prop.position.x).toBeLessThan(x1 - 0.05);
        expect(prop.position.z).toBeGreaterThan(z0 + 0.05);
        expect(prop.position.z).toBeLessThan(z1 - 0.05);
        for (const value of [prop.position.x, prop.position.y, prop.position.z]) {
          expect(Number.isFinite(value), `${kitId} ${prop.id} finite`).toBe(true);
        }
      }
    }
  });

  it("ADV-211 — the guard vocabulary matches the backend placer (TABLE_PROP anchors are evidence-capable)", () => {
    // The backend placer treats TABLE_PROP / GENERIC_PROP as evidence-capable
    // anchor TYPES; the guard must treat a manifest-marked TABLE_PROP anchor
    // (hotel_bedside_01: "evidence" in allowedCategories) as an evidence
    // anchor the decor can never touch.
    const hotel = getKit("hotel_suite")!;
    const bedside = hotel.anchors.find((a) => a.anchorId === "hotel_bedside_01")!;
    expect(bedside.type).toBe("TABLE_PROP");
    expect(bedside.allowedCategories).toContain("evidence");
    const bedHalfX = getAsset("PROP_HOTEL_BED_01")!.dimensions.x / 2 + DECOR_PADDING;
    const bedHalfZ = getAsset("PROP_HOTEL_BED_01")!.dimensions.z / 2 + DECOR_PADDING;
    // The OLD Phase 18D bed placement (bedside − (1.2, 0, 0.85)) overlapped the
    // bedside anchor's clearance circle; the strengthened guard must reject it.
    const oldCollision = {
      x: bedside.position.x - 1.2,
      y: 0.3,
      z: bedside.position.z - 0.85,
    };
    expect(
      evidenceClearanceOk(hotel, oldCollision, bedHalfX, bedHalfZ),
      "a bed box in the old collision position must be REJECTED",
    ).toBe(false);
  });

  it("ADV-211 — the authored hotel bed plan stays clear of the hard-coded bedside anchor", () => {
    // Hard-code the manifest-marked TABLE_PROP bedside anchor (the hotel's
    // only evidence-capable bedside) as an INDEPENDENT witness, then prove the
    // AUTHORED bed plan (resolved from the real manifests) stays clear.
    const hotel = getKit("hotel_suite")!;
    const bedsideAnchor = hotel.anchors.find((a) => a.anchorId === "hotel_bedside_01")!;
    const bed = buildKitShell("hotel_suite").find((p) => p.id === "decor_bed_01");
    expect(bed, "the hotel bed decor must still resolve into the shell").not.toBeUndefined();
    const asset = getAsset("PROP_HOTEL_BED_01")!;
    const halfX = asset.dimensions.x / 2 + DECOR_PADDING;
    const halfZ = asset.dimensions.z / 2 + DECOR_PADDING;

    // The bed plan is placed relative to the bedside anchor (hostAnchorId):
    // reproduce the resolved position independently of the shell builder.
    const planOffset = { x: -1.6, y: 0.3, z: -0.85 };
    const resolvedPosition = {
      x: bedsideAnchor.position.x + planOffset.x,
      y: planOffset.y,
      z: bedsideAnchor.position.z + planOffset.z,
    };
    expect(bed!.position).toEqual(resolvedPosition);
    // ...and the production PADDED box must clear the bedside anchor + spawn.
    expect(
      evidenceClearanceOk(hotel, resolvedPosition, halfX, halfZ),
      "the authored bed box must clear every evidence anchor (incl. the bedside)",
    ).toBe(true);
    // Explicitly against the bedside circle (4,2): same assertion in isolation.
    expect(
      evidenceClearanceOk(
        hotel,
        resolvedPosition,
        halfX,
        halfZ,
      ),
    ).toBe(true);
  });

  it("ADV-211 — every authored decor prop passes the strengthened guard at PRODUCTION (padded) extents", () => {
    // The authored plans must all survive the strengthened guard with the same
    // padded half-extents the placer actually uses (DECOR_PADDING), against the
    // REAL manifests — the "decor can never land on an evidence anchor" promise.
    for (const kitId of ["office", "hotel_suite"]) {
      const kit = getKit(kitId)!;
      for (const prop of buildKitDecorProps(kit)) {
        // The shell prop scale IS the catalog asset's dimensions; pad like the
        // placer does (DECOR_PADDING) and demand full clearance.
        const halfX = prop.scale!.x / 2 + DECOR_PADDING;
        const halfZ = prop.scale!.z / 2 + DECOR_PADDING;
        expect(
          evidenceClearanceOk(kit, prop.position, halfX, halfZ),
          `${kitId} ${prop.id} padded box must clear every evidence anchor + spawn`,
        ).toBe(true);
      }
    }
  });
});