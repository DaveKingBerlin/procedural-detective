import { describe, expect, it } from "vitest";
import rawOfficeManifest from "../../../assets/environments/office.json";
import { buildApartmentManifest, type ScenePrimitive } from "../scene/apartment";
import { ANCHOR_REGISTRY, fallbackAnchor } from "../scene/anchorRegistry";
import { getAsset } from "../catalog/assetCatalog";
import { allKitIds, getKit, validateKit } from "./kitCatalog";
import {
  APARTMENT_KIT_ID,
  anchorTransformFrom,
  buildKitShell,
  cameraProfileFor,
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
});