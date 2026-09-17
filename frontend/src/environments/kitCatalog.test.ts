import { describe, expect, it } from "vitest";
import officeManifest from "../../../assets/environments/office.json";
import {
  ANCHOR_TYPE_VOCABULARY,
  KIT_IDS,
  KitValidationError,
  MIN_SECONDARY_COVERED,
  REQUIRED_COVERAGE_TYPES,
  SECONDARY_COVERAGE_TYPES,
  allKitIds,
  coverageFor,
  getKit,
  getKitCatalogError,
  hasKit,
  isKitCatalogHealthy,
  validateAllKits,
  validateKit,
  type EnvironmentKitDocument,
} from "./kitCatalog";

/**
 * Phase 11 Track B — the five kit manifests are the SINGLE source of truth
 * for anchors/zones/spawn/lighting/structural assets. These tests pin the
 * shipped manifests (all five validate at module load, required anchor-class
 * coverage, spawn validity) AND prove the strict deterministic validator
 * rejects corrupt/duplicate kit manifests with sorted issues.
 */

/** Anchor-class coverage helper: anchorId lists per semantic type. */
function coveredTypes(kit: EnvironmentKitDocument): string[] {
  return ANCHOR_TYPE_VOCABULARY.filter((type) => coverageFor(kit, type).length > 0);
}

describe("bundled kit manifests — the five Phase 11 environments validate", () => {
  it("loads all five kits healthy at module load (no load error)", () => {
    expect(isKitCatalogHealthy()).toBe(true);
    expect(getKitCatalogError()).toBeNull();
  });

  it("exposes exactly the five kit ids in stable order", () => {
    expect(allKitIds()).toEqual(KIT_IDS);
    expect(KIT_IDS).toEqual(["apartment", "office", "hotel_suite", "warehouse", "mansion"]);
  });

  it("every environmentId is unique across the bundled manifest set", () => {
    const ids = allKitIds();
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("each kit declares unique anchorIds and unique zoneIds referencing declared zones", () => {
    for (const kitId of allKitIds()) {
      const kit = getKit(kitId)!;
      const anchorIds = kit.anchors.map((a) => a.anchorId);
      expect(new Set(anchorIds).size, `${kitId} anchor ids`).toBe(anchorIds.length);
      const zoneIds = kit.zones.map((z) => z.zoneId);
      expect(new Set(zoneIds).size, `${kitId} zone ids`).toBe(zoneIds.length);
      const zoneSet = new Set(zoneIds);
      for (const anchor of kit.anchors) {
        expect(zoneSet.has(anchor.zoneId), `${kitId} ${anchor.anchorId} zone`).toBe(true);
      }
    }
  });

  it("each kit has the REQUIRED anchor-class coverage (BODY/FLOOR_EVIDENCE/DESK_EVIDENCE/GENERIC_PROP/DOOR/WINDOW + spawn)", () => {
    for (const kitId of allKitIds()) {
      const kit = getKit(kitId)!;
      const covered = new Set(coveredTypes(kit));
      for (const required of REQUIRED_COVERAGE_TYPES) {
        expect(covered.has(required), `${kitId} must cover ${required}`).toBe(true);
      }
    }
  });

  it("each kit covers at least two of the secondary anchor classes", () => {
    for (const kitId of allKitIds()) {
      const kit = getKit(kitId)!;
      const covered = new Set(coveredTypes(kit));
      const secondaryCovered = SECONDARY_COVERAGE_TYPES.filter((type) => covered.has(type)).length;
      expect(secondaryCovered, `${kitId} secondary coverage`).toBeGreaterThanOrEqual(MIN_SECONDARY_COVERED);
    }
  });

  it("spawn references a declared PLAYER_SPAWN anchor with valid, finite transforms", () => {
    for (const kitId of allKitIds()) {
      const kit = getKit(kitId)!;
      const spawnAnchor = kit.anchors.find((a) => a.anchorId === kit.spawn.anchorId);
      expect(spawnAnchor, `${kitId} spawn anchor`).not.toBeUndefined();
      expect(spawnAnchor!.type, `${kitId} spawn type`).toBe("PLAYER_SPAWN");
      expect(kit.spawn.position).toEqual(spawnAnchor!.position);
      for (const value of [kit.spawn.position.x, kit.spawn.position.y, kit.spawn.position.z]) {
        expect(Number.isFinite(value), `${kitId} spawn finite`).toBe(true);
      }
    }
  });

  it("keeps every anchor position finite inside the local-space bounds (|x|,|z|<=20, 0<=y<=8)", () => {
    for (const kitId of allKitIds()) {
      const kit = getKit(kitId)!;
      for (const anchor of kit.anchors) {
        const { x, y, z } = anchor.position;
        expect(Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z), `${kitId} ${anchor.anchorId}`).toBe(true);
        expect(Math.abs(x), `${kitId} ${anchor.anchorId} x`).toBeLessThanOrEqual(20);
        expect(Math.abs(z), `${kitId} ${anchor.anchorId} z`).toBeLessThanOrEqual(20);
        expect(y, `${kitId} ${anchor.anchorId} y`).toBeGreaterThanOrEqual(0);
        expect(y, `${kitId} ${anchor.anchorId} y`).toBeLessThanOrEqual(8);
      }
    }
  });

  it("keeps the spawn at least 0.4 clear of every other anchor (never intersects geometry)", () => {
    for (const kitId of allKitIds()) {
      const kit = getKit(kitId)!;
      for (const anchor of kit.anchors) {
        if (anchor.anchorId === kit.spawn.anchorId) continue;
        const distance = Math.hypot(
          kit.spawn.position.x - anchor.position.x,
          kit.spawn.position.y - anchor.position.y,
          kit.spawn.position.z - anchor.position.z,
        );
        expect(distance, `${kitId} spawn vs ${anchor.anchorId}`).toBeGreaterThanOrEqual(0.4);
      }
    }
  });

  it("validates the raw bundled office manifest directly without throwing", () => {
    expect(() => validateKit(officeManifest as unknown)).not.toThrow();
  });
});

describe("getKit / hasKit — EXACT environment ids only", () => {
  it("returns the typed kit for exact ids and undefined for unknown ids", () => {
    expect(hasKit("apartment")).toBe(true);
    expect(hasKit("office")).toBe(true);
    expect(hasKit("warehouse")).toBe(true);
    expect(getKit("hotel_suite")?.canonicalName).toBe("Hotel Suite");
    expect(getKit("no_such_kit")).toBeUndefined();
    expect(hasKit("no_such_kit")).toBe(false);
    expect(hasKit("")).toBe(false);
    expect(hasKit("javascript:alert(1)")).toBe(false);
    expect(hasKit("https://evil.example/kit.json")).toBe(false);
  });

  it("never resolves aliases or free text (aliases are backend-resolved)", () => {
    // "flat" / "hotel" are DECLARED aliases in the manifests — alias
    // resolution belongs to the BACKEND resolver only, exactly like the
    // catalog boundary. The frontend accepts exact environmentIds only.
    expect(getKit("flat")).toBeUndefined();
    expect(getKit("hotel")).toBeUndefined();
    expect(getKit("villa")).toBeUndefined();
  });
});

/* ======================================================================
 * Strict deterministic validator — corrupt manifests are rejected
 * ==================================================================== */

/** A minimal VALID kit fixture the mutation tests corrupt one field at a time. */
function makeKit(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    environmentId: "test_flat",
    version: 1,
    canonicalName: "Test Flat",
    aliases: [],
    tags: [],
    zones: [
      { zoneId: "z_a", label: "Zone A", rooms: ["room_a"] },
      { zoneId: "z_b", label: "Zone B", rooms: ["room_b"] },
      { zoneId: "z_c", label: "Zone C", rooms: ["room_c"] },
      { zoneId: "z_d", label: "Zone D", rooms: ["room_d"] },
      { zoneId: "z_e", label: "Zone E", rooms: ["room_e"] },
    ],
    anchors: [
      { anchorId: "spawn_01", type: "PLAYER_SPAWN", zoneId: "z_a", position: { x: 0, y: 0, z: 0 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: [], exclusive: true, required: true },
      { anchorId: "body_01", type: "BODY", zoneId: "z_a", position: { x: 3, y: 0, z: 3 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: ["character"], exclusive: true, required: true },
      { anchorId: "floor_ev_01", type: "FLOOR_EVIDENCE", zoneId: "z_a", position: { x: 0.5, y: 0.02, z: 2 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: ["evidence"], exclusive: false, required: true },
      { anchorId: "desk_01", type: "DESK_EVIDENCE", zoneId: "z_b", position: { x: 4, y: 0.8, z: 8 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: ["evidence", "electronics", "furniture", "decor"], exclusive: false, required: true },
      { anchorId: "doc_01", type: "DOCUMENT", zoneId: "z_b", position: { x: 4, y: 0.8, z: 8 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: ["evidence", "electronics"], exclusive: false, required: true },
      { anchorId: "comp_01", type: "COMPUTER", zoneId: "z_b", position: { x: 4, y: 0.8, z: 7 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: ["electronics", "evidence"], exclusive: true, required: true },
      { anchorId: "door_01", type: "DOOR", zoneId: "z_c", position: { x: 0.5, y: 0, z: 6 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: ["structural"], exclusive: true, required: true },
      { anchorId: "window_01", type: "WINDOW", zoneId: "z_c", position: { x: 0, y: 0, z: 7 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: ["structural"], exclusive: true, required: true },
      { anchorId: "shelf_01", type: "STORAGE", zoneId: "z_d", position: { x: 6, y: 0, z: 2 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: ["furniture", "decor", "utility"], exclusive: false, required: true },
      { anchorId: "generic_01", type: "GENERIC_PROP", zoneId: "z_e", position: { x: 7.5, y: 0, z: 3 }, rotation: { x: 0, y: 0, z: 0 }, allowedCategories: ["decor", "furniture", "utility"], exclusive: false, required: true },
    ],
    spawn: { anchorId: "spawn_01", position: { x: 0, y: 0, z: 0 }, rotation: { x: 0, y: 0, z: 0 } },
    lighting: { profile: "warm_flat", keyIntensity: 0.9, hemiIntensity: 0.5, accentColor: "#e8b878" },
    structuralAssets: ["DOOR_APARTMENT_01", "PROP_WINDOW_01", "PROP_WALL_01", "PROP_DESK_01", "PROP_LAMP_01", "PROP_TABLE_01"],
    styleHint: "test_style",
    defaultAnchorCoverage: {
      PLAYER_SPAWN: ["spawn_01"],
      BODY: ["body_01"],
      FLOOR_EVIDENCE: ["floor_ev_01"],
      DESK_EVIDENCE: ["desk_01"],
      TABLE_PROP: [],
      COMPUTER: ["comp_01"],
      DOCUMENT: ["doc_01"],
      WALL_EVIDENCE: [],
      DOOR: ["door_01"],
      WINDOW: ["window_01"],
      STORAGE: ["shelf_01"],
      CCTV: [],
      ACCESS_CONTROL: [],
      GENERIC_PROP: ["generic_01"],
    },
    ...overrides,
  };
}

function cloneKit(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return structuredClone(makeKit(overrides)) as Record<string, unknown>;
}

/** Assert validateKit rejects with a deterministic issue containing `needle`. */
function expectKitRejected(raw: unknown, ...needles: string[]): readonly string[] {
  let caught: KitValidationError | null = null;
  try {
    validateKit(raw);
  } catch (error) {
    expect(error).toBeInstanceOf(KitValidationError);
    caught = error as KitValidationError;
  }
  expect(caught, "expected the validator to reject this manifest").not.toBeNull();
  expect(caught!.issues.length).toBeGreaterThan(0);
  const joined = caught!.issues.join("\n");
  for (const needle of needles) {
    expect(joined).toContain(needle);
  }
  return caught!.issues;
}

describe("validateKit — deterministic rejection of corrupt manifests", () => {
  it("accepts the minimal valid document", () => {
    expect(() => validateKit(makeKit())).not.toThrow();
  });

  it("rejects a duplicate anchorId with a deterministic issue", () => {
    const dup = cloneKit();
    (dup.anchors as unknown[]).push(structuredClone((dup.anchors as unknown[])[0]));
    // Two spawn anchors also trigger the exclusive shared-position probe — the
    // duplicate anchorId issue is the exact deterministic needle here.
    expectKitRejected(dup, "duplicate anchorId \"spawn_01\"");
  });

  it("rejects a duplicate zoneId", () => {
    const dup = cloneKit();
    (dup.zones as unknown[])[1] = structuredClone((dup.zones as unknown[])[0]);
    expectKitRejected(dup, "duplicate zoneId \"z_a\"");
  });

  it("rejects an anchor referencing an undeclared zone", () => {
    const bad = cloneKit();
    ((bad.anchors as unknown[])[0] as Record<string, unknown>).zoneId = "ghost_zone";
    expectKitRejected(bad, "unknown zoneId \"ghost_zone\"");
  });

  it("rejects an anchor type outside the ANCHOR_TYPE_VOCABULARY", () => {
    const bad = cloneKit();
    ((bad.anchors as unknown[])[5] as Record<string, unknown>).type = "TELEPORTER";
    expectKitRejected(bad, "not in the ANCHOR_TYPE_VOCABULARY");
  });

  it("rejects out-of-bounds anchor positions (|x|>20, y<0, y>8, |z|>20)", () => {
    const farX = cloneKit();
    ((farX.anchors as unknown[])[0] as Record<string, unknown>).position = { x: 25, y: 0, z: 0 };
    expectKitRejected(farX, ".x: must satisfy |v| <= 20");

    const yBelow = cloneKit();
    ((yBelow.anchors as unknown[])[1] as Record<string, unknown>).position = { x: 3, y: -0.5, z: 3 };
    expectKitRejected(yBelow, ".y: must be within [0, 8]");

    const yAbove = cloneKit();
    ((yAbove.anchors as unknown[])[1] as Record<string, unknown>).position = { x: 3, y: 9, z: 3 };
    expectKitRejected(yAbove, ".y: must be within [0, 8]");

    const farZ = cloneKit();
    ((farZ.anchors as unknown[])[0] as Record<string, unknown>).position = { x: 0, y: 0, z: -21 };
    expectKitRejected(farZ, ".z: must satisfy |v| <= 20");
  });

  it("rejects a spawn that intersects another anchor (< 0.4 clearance)", () => {
    const bad = cloneKit();
    // Move the spawn onto the body anchor (3,0,3) — 0.0 clearance.
    (bad.spawn as Record<string, unknown>).position = { x: 3, y: 0, z: 3 };
    expectKitRejected(bad, "spawn must not intersect geometry");
  });

  it("rejects a spawn that does not reference a declared PLAYER_SPAWN anchor", () => {
    const noAnchor = cloneKit();
    (noAnchor.spawn as Record<string, unknown>).anchorId = "no_such_anchor";
    expectKitRejected(noAnchor, "does not reference a declared anchor");

    const wrongType = cloneKit();
    (wrongType.spawn as Record<string, unknown>).anchorId = "body_01";
    expectKitRejected(wrongType, "must reference an anchor of type PLAYER_SPAWN");
  });

  it("rejects an anchor position outside the local-space position bounds (non-finite)", () => {
    const nan = cloneKit();
    (nan.anchors as unknown[]).forEach((anchor) => {
      (anchor as Record<string, unknown>).position = { x: Number.POSITIVE_INFINITY, y: 0, z: 0 };
    });
    expectKitRejected(nan, "must be a finite number");
  });

  it("rejects unknown structural asset ids (not present in the bundled catalog)", () => {
    const bad = cloneKit({ structuralAssets: ["PROP_NOT_A_THING_01", "PROP_WINDOW_01", "PROP_WALL_01", "PROP_DESK_01", "PROP_LAMP_01", "PROP_TABLE_01"] });
    expectKitRejected(bad, "does not exist in the asset catalog");
  });

  it("rejects unsafe strings (URL schemes) in manifest text fields", () => {
    const bad = cloneKit({ canonicalName: "https://evil.example/kit" });
    expectKitRejected(bad, "forbidden URL scheme");
  });

  it.each([
    ["U+200B zero-width space", "\u200b"],
    ["U+200C zero-width non-joiner", "\u200c"],
    ["U+200D zero-width joiner", "\u200d"],
    ["U+200E left-to-right mark", "\u200e"],
    ["U+200F right-to-left mark", "\u200f"],
    ["U+2028 line separator", "\u2028"],
    ["U+2029 paragraph separator", "\u2029"],
    ["U+202A left-to-right embedding", "\u202a"],
    ["U+202E right-to-left override", "\u202e"],
    ["U+2060 word joiner", "\u2060"],
    ["U+2064 invisible plus", "\u2064"],
    ["U+FEFF BOM / zero-width no-break space", "\ufeff"],
  ] as ReadonlyArray<readonly [label: string, glyph: string]>)(
    "rejects the invisible %s glyph in kit strings (DEF-068 parity)",
    (_label, glyph) => {
      const bad = cloneKit({ canonicalName: `bad${glyph}manufacturing` });
      expectKitRejected(bad, "zero-width / bidi / line-separator glyph");
    },
  );

  it("the five bundled kits still validate clean under the DEF-068 glyph scan", () => {
    expect(isKitCatalogHealthy()).toBe(true);
    expect(getKitCatalogError()).toBeNull();
  });

  it("rejects missing required default-anchor coverage (no BODY anchor)", () => {
    const bad = cloneKit();
    const coverage = bad.defaultAnchorCoverage as Record<string, string[]>;
    coverage.BODY = [];
    expectKitRejected(bad, "required anchor type \"BODY\"");
  });

  it("rejects missing secondary coverage (fewer than two secondary types)", () => {
    const bad = cloneKit();
    const coverage = bad.defaultAnchorCoverage as Record<string, string[]>;
    coverage.COMPUTER = [];
    coverage.DOCUMENT = [];
    coverage.STORAGE = [];
    expectKitRejected(bad, "at least 2 of the secondary types");
  });

  it("rejects an invalid BODY anchor off the floor plane", () => {
    const bad = cloneKit();
    ((bad.anchors as unknown[])[1] as Record<string, unknown>).position = { x: 3, y: 1.2, z: 3 };
    expectKitRejected(bad, "BODY anchor must lie on the floor plane");
  });

  it("produces SORTED, de-duplicated issues deterministically", () => {
    const dup = cloneKit();
    (dup.anchors as unknown[]) = [(makeKit().anchors as unknown[])[0], (makeKit().anchors as unknown[])[0]];
    const first = expectKitRejected(dup, "duplicate anchorId");
    const second = expectKitRejected(dup, "duplicate anchorId");
    expect(first).toEqual(second);
    expect(first).toEqual([...first].sort());
  });
});

describe("validateAllKits — cross-kit identity", () => {
  it("rejects two kit manifests declaring the same environmentId", () => {
    let caught: KitValidationError | null = null;
    try {
      validateAllKits([makeKit(), makeKit()]);
    } catch (error) {
      expect(error).toBeInstanceOf(KitValidationError);
      caught = error as KitValidationError;
    }
    expect(caught).not.toBeNull();
    expect(caught!.issues.join("\n")).toContain('duplicate environmentId "test_flat"');
  });

  it("accepts a set of distinct kit manifests", () => {
    const second = makeKit({ environmentId: "test_office" });
    expect(validateAllKits([makeKit(), second]).map((kit) => kit.environmentId)).toEqual(
      ["test_flat", "test_office"],
    );
  });

  it("collects every kit's issues before throwing (bulk deterministic report)", () => {
    const broken = makeKit({ canonicalName: "https://evil.example/x" });
    let caught: KitValidationError | null = null;
    try {
      validateAllKits([makeKit(), broken]);
    } catch (error) {
      caught = error as KitValidationError;
    }
    expect(caught).not.toBeNull();
    expect(caught!.issues.join("\n")).toContain("kits[1]: canonicalName");
  });
});