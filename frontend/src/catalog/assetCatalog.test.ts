import { describe, expect, it } from "vitest";
import rawCatalog from "../../../assets/catalog/catalog.json";
import {
  CATEGORY_VOCABULARY,
  CatalogValidationError,
  MATERIAL_VOCABULARY,
  RENDER_KIND_VOCABULARY,
  STATE_VOCABULARY,
  SUPPORTED_COMPOSITE_KINDS,
  TEMPLATE_VOCABULARY,
  catalogAssetIds,
  fallbackAsset,
  fallbackAssetId,
  getAsset,
  getCatalogError,
  getCatalogVersion,
  hasAsset,
  isCatalogHealthy,
  resolveAsset,
  validateCatalog,
  type CatalogAssetDescriptor,
} from "./assetCatalog";

/**
 * Phase 10 Track B — the frontend consumes the SAME frozen v1 manifest the
 * backend Asset Oracle resolver uses (assets/catalog/catalog.json). These
 * tests pin the manifest contract: every asset parses to a typed descriptor,
 * the strict validator rejects corrupt manifests with deterministic issues,
 * and the frontend resolves EXACT assetIds only (aliases are backend-only).
 */

const CATALOG_IDS_BY_MANIFEST_ORDER = [
  // Phase 10 frozen golden set (unchanged first 10: the evidence + furniture
  // + fallback the backend dev-mode case emits).
  "PROP_KITCHEN_KNIFE_01",
  "PROP_LETTER_OPENER_01",
  "PROP_SCISSORS_01",
  "PROP_LAPTOP_01",
  "PROP_TABLE_01",
  "DOOR_APARTMENT_01",
  "PROP_LAMP_01",
  "PROP_BODY_PLACEHOLDER_01",
  "PROP_VASE_01",
  "PROP_FALLBACK_01",
  // Phase 11 Track B: the backend track added 6 structural entries (the kit
  // manifest structuralAssets reference them); the manifest-order list must
  // track the single source of truth exactly.
  "PROP_WINDOW_01",
  "PROP_WALL_01",
  "PROP_DESK_01",
  "PROP_HOTEL_BED_01",
  "PROP_WAREHOUSE_SHELF_01",
  "PROP_OFFICE_CHAIR_01",
  // Phase 12 — the full 101-entry showcase manifest (one entry per asset, in
  // exact manifest order; the list is the byte-stable contract snapshot).
  "PROP_BREAD_KNIFE_01",
  "PROP_SCREWDRIVER_01",
  "PROP_HAMMER_01",
  "PROP_WRENCH_01",
  "PROP_BASEBALL_BAT_01",
  "PROP_GLASS_BOTTLE_01",
  "PROP_ROPE_01",
  "PROP_KEY_01",
  "PROP_USB_STICK_01",
  "PROP_WALLET_01",
  "PROP_WATCH_01",
  "PROP_MEDICATION_BOTTLE_01",
  "PROP_GLOVE_01",
  "PROP_SHOE_PRINT_MARKER_01",
  "PROP_PHONE_01",
  "PROP_CAMERA_01",
  "PROP_JEWELRY_BOX_01",
  "PROP_SOFA_01",
  "PROP_BED_01",
  "PROP_BEDSIDE_TABLE_01",
  "PROP_CHAIR_01",
  "PROP_ARMCHAIR_01",
  "PROP_BOOKSHELF_01",
  "PROP_KITCHEN_SHELF_01",
  "PROP_CABINET_01",
  "PROP_LOCKER_01",
  "PROP_CRATE_01",
  "PROP_DESK_CHAIR_01",
  "PROP_COFFEE_TABLE_01",
  "PROP_SIDEBOARD_01",
  "PROP_PALLET_01",
  "PROP_DESKTOP_MONITOR_01",
  "PROP_KEYBOARD_01",
  "PROP_TABLET_01",
  "PROP_CCTV_CAMERA_01",
  "PROP_ROUTER_01",
  "PROP_ACCESS_READER_01",
  "PROP_PRINTER_01",
  "PROP_TV_01",
  "PROP_DESKTOP_COMPUTER_01",
  "PROP_CONTRACT_01",
  "PROP_BANK_STATEMENT_01",
  "PROP_INVOICE_01",
  "PROP_LETTER_01",
  "PROP_NOTEBOOK_01",
  "PROP_FOLDER_01",
  "PROP_ID_CARD_01",
  "PROP_TICKET_01",
  "PROP_RECEIPT_01",
  "PROP_DOCUMENT_FRAME_01",
  "PROP_EVIDENCE_MARKER_01",
  "PROP_BLOOD_DECAL_01",
  "PROP_FINGERPRINT_MARKER_01",
  "PROP_BROKEN_GLASS_01",
  "PROP_FOOTPRINT_MARKER_01",
  "PROP_EVIDENCE_BAG_01",
  "PROP_SWAB_KIT_01",
  "PROP_CRIME_SCENE_TAPE_01",
  "PROP_EVIDENCE_CONE_01",
  "PROP_CHALK_BODY_OUTLINE_01",
  "PROP_SAFE_01",
  "PROP_LIGHT_SWITCH_01",
  "PROP_TRASH_BIN_01",
  "PROP_SINK_01",
  "PROP_KITCHEN_COUNTER_01",
  "PROP_STORAGE_BOX_01",
  "PROP_WALL_CABINET_01",
  "PROP_CUP_01",
  "PROP_PLATE_01",
  "PROP_PLANT_POT_01",
  "PROP_CLOCK_01",
  "PROP_BOOK_01",
  "PROP_PEN_01",
  "PROP_HANDBAG_01",
  "PROP_COAT_01",
  "PROP_WATER_BOTTLE_01",
  "PROP_DESK_LAMP_01",
  "PROP_TABLE_LAMP_01",
  "PROP_MAGAZINE_01",
  "PROP_CANDLESTICK_01",
  "PROP_PHOTO_FRAME_01",
  "PROP_FALLBACK_02",
  "PROP_FALLBACK_03",
  "PROP_FALLBACK_04",
  "PROP_FALLBACK_05",
];

describe("bundled manifest health", () => {
  it("passes the strict validator at module load (healthy, no load error)", () => {
    expect(isCatalogHealthy()).toBe(true);
    expect(getCatalogError()).toBeNull();
    expect(getCatalogVersion()).toBe(1);
  });

  it("validates the raw bundled JSON directly without throwing", () => {
    expect(() => validateCatalog(rawCatalog as unknown)).not.toThrow();
  });

  it("reads catalogVersion and the declared fallback id", () => {
    expect(fallbackAssetId()).toBe("PROP_FALLBACK_01");
    expect(fallbackAsset()?.assetId).toBe("PROP_FALLBACK_01");
    expect(fallbackAsset()?.label).toBe("Unknown object");
    expect(fallbackAsset()?.interactable).toBe(false);
  });

  it("lists every v1 asset id in manifest order (incl. the fallback asset)", () => {
    expect(catalogAssetIds()).toEqual(CATALOG_IDS_BY_MANIFEST_ORDER);
  });
});

describe("every manifest asset parses to a typed descriptor", () => {
  it("parses every asset into a descriptor that mirrors the manifest fields", () => {
    for (const descriptor of [...catalogAssetIds()].map((id) => getAsset(id))) {
      expect(descriptor).toBeDefined();
      const d = descriptor as CatalogAssetDescriptor;
      expect(d.assetId).toMatch(/^[A-Z][A-Z0-9_]+$/);
      expect(Number.isInteger(d.version)).toBe(true);
      expect(d.version).toBeGreaterThanOrEqual(1);
      expect(d.canonicalName.length).toBeGreaterThan(0);
      expect(d.subtype.length).toBeGreaterThan(0);
      expect(d.tags.length).toBeGreaterThan(0);
      expect(RENDER_KIND_VOCABULARY).toContain(d.renderKind);
      expect(CATEGORY_VOCABULARY).toContain(d.category);
      expect(Object.keys(d.dimensions).sort()).toEqual(["x", "y", "z"]);
      for (const axis of ["x", "y", "z"] as const) {
        expect(Number.isFinite(d.dimensions[axis])).toBe(true);
        expect(d.dimensions[axis]).toBeGreaterThan(0);
      }
      expect(Object.keys(d.colors).length).toBeGreaterThan(0);
      for (const hex of Object.values(d.colors)) {
        expect(hex).toMatch(/^#[0-9a-fA-F]{6}$/);
      }
      expect(d.label.length).toBeGreaterThan(0);
      expect(typeof d.interactable).toBe("boolean");
      expect(Array.isArray(d.supportedInteractions)).toBe(true);
      expect(Array.isArray(d.evidenceCapabilities)).toBe(true);
      expect(Array.isArray(d.allowedAnchors)).toBe(true);
      if (d.renderKind === "composite") {
        // Phase 12: every composite carries a frozen templateId; the six
        // legacy composites ALSO carry a compositeKind builder (template-only
        // composites have compositeKind null).
        expect(TEMPLATE_VOCABULARY).toContain(d.templateId);
        if (d.compositeKind !== null) {
          expect(d.compositeKind).toMatch(/^[a-z_]+$/);
        }
      } else {
        expect(d.compositeKind).toBeNull();
        expect(d.templateId).toBeNull();
      }
    }
  });

  it("spot-checks the frozen v1 descriptor values the golden scene relies on", () => {
    expect(getAsset("PROP_KITCHEN_KNIFE_01")).toMatchObject({
      label: "Kitchen knife",
      interactable: true,
      renderKind: "composite",
      compositeKind: "kitchen_knife",
    });
    expect(getAsset("PROP_KITCHEN_KNIFE_01")?.colors).toEqual({ blade: "#c8ccd4", handle: "#5a3b22" });
    expect(getAsset("PROP_SCISSORS_01")).toMatchObject({
      label: "Scissors",
      interactable: true,
      renderKind: "composite",
      compositeKind: "scissors",
    });
    expect(getAsset("PROP_SCISSORS_01")?.colors).toEqual({ blades: "#b8bcc4", pivot: "#5b6068" });
    expect(getAsset("PROP_TABLE_01")?.interactable).toBe(false);
    expect(getAsset("PROP_VASE_01")?.interactable).toBe(false);
    expect(getAsset("PROP_BODY_PLACEHOLDER_01")?.interactable).toBe(false);
    expect(getAsset("PROP_VASE_01")?.colors).toEqual({ body: "#7d5a4c" });
    expect(getAsset("PROP_FALLBACK_01")?.interactable).toBe(false);
  });
});

describe("frontend resolves EXACT assetIds only (aliases are backend-resolved)", () => {
  it("hasAsset/getAsset match exact ids verbatim", () => {
    expect(hasAsset("PROP_KITCHEN_KNIFE_01")).toBe(true);
    expect(hasAsset("PROP_LAPTOP_01")).toBe(true);
    expect(hasAsset("kitchen knife")).toBe(false);
    expect(hasAsset("KITCHEN KNIFE")).toBe(false);
    expect(hasAsset("")).toBe(false);
    expect(hasAsset("javascript:alert(1)")).toBe(false);
    expect(hasAsset("https://evil.example/x.glb")).toBe(false);
  });

  it("does NOT resolve canonical names or aliases — even when the manifest carries them", () => {
    // "apartment.laptop.basic" IS a declared alias of PROP_LAPTOP_01 in the
    // manifest — but alias resolution belongs to the BACKEND resolver only.
    // The frontend boundary is exact assetIds: an alias is NOT a catalog id.
    expect(getAsset("apartment.laptop.basic")).toBeUndefined();
    expect(hasAsset("apartment.laptop.basic")).toBe(false);
    expect(getAsset("letter_opener")).toBeUndefined();
    expect(getAsset("victim")).toBeUndefined();
    expect(getAsset("kitchen knife")).toBeUndefined();
  });

  it("resolveAsset maps unknown ids to the declared fallback descriptor", () => {
    expect(resolveAsset("PROP_KITCHEN_KNIFE_01")?.assetId).toBe("PROP_KITCHEN_KNIFE_01");
    expect(resolveAsset("apartment.laptop.basic")?.assetId).toBe("PROP_FALLBACK_01");
    expect(resolveAsset("ASSET.THAT.DOES.NOT.EXIST")?.assetId).toBe("PROP_FALLBACK_01");
    expect(resolveAsset("#ff0000;url(javascript:alert(1))")?.assetId).toBe("PROP_FALLBACK_01");
  });
});

describe("strict validator — deterministic rejection of corrupt manifests", () => {
  const minimalValidEntry = {
    assetId: "PROP_X_01",
    version: 1,
    canonicalName: "something",
    aliases: [],
    category: "utility",
    subtype: "prop",
    tags: [],
    renderKind: "box",
    compositeKind: null,
    dimensions: { x: 0.5, y: 0.5, z: 0.5 },
    colors: { body: "#8d8d93" },
    label: "Something",
    interactable: false,
    supportedInteractions: [],
    evidenceCapabilities: [],
    allowedAnchors: ["GENERIC_PROP"],
  };
  const validDocument = {
    catalogVersion: 1,
    fallbackAsset: "PROP_X_01",
    assets: [minimalValidEntry],
  };

  /**
   * A clone helper that ERASES the static shape so tests can mutate the raw
   * manifest exactly like a (potentially hostile) JSON payload would.
   * `rawAsset` reads from the SAME cloned instance (mutations persist).
   */
  const rawDocument = (doc: unknown = validDocument): Record<string, unknown> =>
    structuredClone(doc) as Record<string, unknown>;
  const rawAsset = (doc: Record<string, unknown>, index = 0): Record<string, unknown> =>
    (doc.assets as unknown[])[index] as Record<string, unknown>;

  const expectIssues = (raw: unknown, ...needles: string[]): readonly string[] => {
    let caught: CatalogValidationError | null = null;
    try {
      validateCatalog(raw);
    } catch (error) {
      expect(error).toBeInstanceOf(CatalogValidationError);
      caught = error as CatalogValidationError;
    }
    expect(caught, "expected the validator to reject this manifest").not.toBeNull();
    expect(caught!.issues.length).toBeGreaterThan(0);
    for (const needle of needles) {
      expect(caught!.issues.join("\n")).toContain(needle);
    }
    return caught!.issues;
  };

  it("accepts a minimal valid document", () => {
    expect(() => validateCatalog(validDocument)).not.toThrow();
  });

  it("rejects duplicate assetIds with a deterministic issue", () => {
    const dup = rawDocument();
    (dup.assets as unknown[]) = [minimalValidEntry, minimalValidEntry];
    expectIssues(dup, "duplicate assetId");
  });

  it("rejects an unknown top-level shape (not an object / assets not an array)", () => {
    expectIssues(null, "catalog document");
    expectIssues("catalog", "catalog document");
    expectIssues({ catalogVersion: 1, fallbackAsset: "X" }, "'assets' must be an array");
  });

  it("rejects missing/unknown asset keys and bad assetId patterns", () => {
    const noId = rawDocument();
    delete rawAsset(noId).assetId;
    expectIssues(noId, "missing required keys");
    const badId = rawDocument();
    rawAsset(badId).assetId = "lowercase_id";
    expectIssues(badId, "must match");
    const extra = rawDocument();
    rawAsset(extra).sneaky = "payload";
    expectIssues(extra, "unknown keys");
  });

  it("rejects duplicate assetIds even when entries are otherwise malformed", () => {
    const dupBad = rawDocument();
    const first = rawAsset(dupBad, 0);
    const second = structuredClone(minimalValidEntry) as Record<string, unknown>;
    second.assetId = "PROP_X_01";
    second.version = "not-an-int";
    second.label = "";
    (dupBad.assets as unknown[]) = [first, second];
    expectIssues(dupBad, "duplicate assetId", "version", "label");
  });

  it("rejects malformed colors (empty, non-hex, unparseable)", () => {
    const emptyColors = rawDocument();
    rawAsset(emptyColors).colors = {};
    expectIssues(emptyColors, "colors must not be empty");

    const badHex = rawDocument();
    rawAsset(badHex).colors = { body: "red" };
    expectIssues(badHex, "not #RRGGBB");

    const hostileColor = rawDocument();
    rawAsset(hostileColor).colors = { body: "url(https://evil.example/x)" };
    expectIssues(hostileColor, "not #RRGGBB");
  });

  it("rejects malformed dimensions (wrong keys, negative, non-finite)", () => {
    const wrongKeys = rawDocument();
    rawAsset(wrongKeys).dimensions = { x: 1, y: 2 };
    expectIssues(wrongKeys, "dimensions");

    const negative = rawDocument();
    rawAsset(negative).dimensions = { x: 0.5, y: -1, z: 0.5 };
    expectIssues(negative, "must be a finite number > 0");

    const strings = rawDocument();
    rawAsset(strings).dimensions = { x: "big", y: 0.5, z: 0.5 };
    expectIssues(strings, "must be a finite number");
  });

  it("rejects render/composite kinds the frontend cannot safely render", () => {
    const unknownRender = rawDocument();
    rawAsset(unknownRender).renderKind = "gouraud";
    expectIssues(unknownRender, "renderKind");

    const ghostComposite = rawDocument();
    rawAsset(ghostComposite).renderKind = "composite";
    rawAsset(ghostComposite).compositeKind = "chainsaw";
    expectIssues(ghostComposite, "no safe frontend renderer", "chainsaw");

    // Phase 12 schema: a composite with NO templateId has nothing to render
    // (template-only composites REQUIRE a template from the frozen list).
    const compositeNoTemplate = rawDocument();
    rawAsset(compositeNoTemplate).renderKind = "composite";
    rawAsset(compositeNoTemplate).compositeKind = null;
    delete (rawAsset(compositeNoTemplate) as Record<string, unknown>).templateId;
    expectIssues(compositeNoTemplate, "requires a templateId");

    // An unknown templateId is rejected (not in the frozen vocabulary).
    const unknownTemplate = rawDocument();
    rawAsset(unknownTemplate).renderKind = "composite";
    rawAsset(unknownTemplate).compositeKind = null;
    rawAsset(unknownTemplate).templateId = "no_such_template";
    expectIssues(unknownTemplate, "TEMPLATE_VOCABULARY", "no_such_template");

    const nullOnBox = rawDocument();
    rawAsset(nullOnBox).renderKind = "box";
    rawAsset(nullOnBox).compositeKind = "kitchen_knife";
    expectIssues(nullOnBox, "must be null unless renderKind");
  });

  it("rejects unsafe strings (URL schemes, paths, traversal, control chars, oversized)", () => {
    const url = rawDocument();
    rawAsset(url).label = "https://evil.example/x";
    expectIssues(url, "forbidden URL scheme");

    const traversal = rawDocument();
    rawAsset(traversal).canonicalName = "../../etc/passwd";
    expectIssues(traversal, "path traversal");

    const absolute = rawDocument();
    rawAsset(absolute).subtype = "C:\\Windows\\system32";
    expectIssues(absolute, "absolute path");

    const control = rawDocument();
    rawAsset(control).label = "bad\u0000label";
    expectIssues(control, "control characters");

    const huge = rawDocument();
    rawAsset(huge).label = "x".repeat(200);
    expectIssues(huge, "exceeds 120 characters");
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
    ["U+202B right-to-left embedding", "\u202b"],
    ["U+202C pop directional formatting", "\u202c"],
    ["U+202D left-to-right override", "\u202d"],
    ["U+202E right-to-left override", "\u202e"],
    ["U+2060 word joiner", "\u2060"],
    ["U+2061 function application", "\u2061"],
    ["U+2062 invisible times", "\u2062"],
    ["U+2063 invisible separator", "\u2063"],
    ["U+2064 invisible plus", "\u2064"],
    ["U+FEFF BOM / zero-width no-break space", "\ufeff"],
  ] as ReadonlyArray<readonly [label: string, glyph: string]>)(
    "rejects the invisible %s glyph in catalog strings (DEF-068 parity)",
    (_label, glyph) => {
      const raw = rawDocument();
      rawAsset(raw).label = `bad${glyph}label`;
      const issues = expectIssues(raw, "zero-width / bidi / line-separator glyph");
      expect(issues.length).toBeGreaterThan(0);
    },
  );

  it("the bundled catalog still validates clean under the DEF-068 glyph scan", () => {
    expect(isCatalogHealthy()).toBe(true);
    expect(getCatalogError()).toBeNull();
    for (const assetId of catalogAssetIds()) {
      const descriptor = getAsset(assetId)!;
      const label = descriptor.label;
      expect(
        [...label].some((char) => {
          const code = char.charCodeAt(0);
          return (code >= 0x200b && code <= 0x200f) || code === 0x2028 || code === 0x2029 ||
            (code >= 0x202a && code <= 0x202e) || (code >= 0x2060 && code <= 0x2064) || code === 0xfeff;
        }),
        `${assetId} label must be glyph-clean`,
      ).toBe(false);
    }
  });

  it("rejects a fallbackAsset that is not a declared assetId", () => {
    const bad = rawDocument();
    bad.fallbackAsset = "PROP_MISSING_01";
    expectIssues(bad, "fallbackAsset");
  });

  it("rejects a non-boolean interactable and bad vocabulary arrays", () => {
    const nonBool = rawDocument();
    rawAsset(nonBool).interactable = "yes";
    expectIssues(nonBool, "interactable must be a boolean");

    const badList = rawDocument();
    rawAsset(badList).supportedInteractions = [42];
    expectIssues(badList, "non-empty string");
  });

  it("produces SORTED, de-duplicated issues deterministically", () => {
    const dup = rawDocument();
    (dup.assets as unknown[]) = [minimalValidEntry, minimalValidEntry];
    const first = expectIssues(dup, "duplicate assetId");
    const second = expectIssues(dup, "duplicate assetId");
    expect(first).toEqual(second);
    expect(first).toEqual([...first].sort());
  });
});

describe("DEF-060 — bounded vocabulary/alias arrays (backend symmetry)", () => {
  const entry = {
    assetId: "PROP_Y_01",
    version: 1,
    canonicalName: "bounded thing",
    aliases: [] as string[],
    category: "utility",
    subtype: "prop",
    tags: [] as string[],
    renderKind: "box",
    compositeKind: null,
    dimensions: { x: 0.5, y: 0.5, z: 0.5 },
    colors: { body: "#8d8d93" },
    label: "Bounded thing",
    interactable: false,
    supportedInteractions: [] as string[],
    evidenceCapabilities: [] as string[],
    allowedAnchors: [] as string[],
  };
  const doc = (overrides: Record<string, unknown>) => ({
    catalogVersion: 1,
    fallbackAsset: "PROP_Y_01",
    assets: [{ ...entry, ...overrides }],
  });

  const expectRejected = (raw: unknown, fragment: string) => {
    let caught: CatalogValidationError | null = null;
    try {
      validateCatalog(raw);
    } catch (error) {
      expect(error).toBeInstanceOf(CatalogValidationError);
      caught = error as CatalogValidationError;
    }
    expect(caught, "expected rejection").not.toBeNull();
    expect(caught!.issues.join("\n")).toContain(fragment);
  };

  it("accepts arrays at exactly the documented bounds (16/16/8/8/16/16)", () => {
    const atBounds = doc({
      aliases: new Array(16).fill("a"),
      tags: new Array(16).fill("t"),
      supportedInteractions: new Array(8).fill("i"),
      evidenceCapabilities: new Array(8).fill("c"),
      allowedAnchors: new Array(16).fill("A"),
      colors: Object.fromEntries(new Array(16).fill(0).map((_, n) => [`c${n}`, "#112233"])),
    });
    expect(() => validateCatalog(atBounds)).not.toThrow();
  });

  it("rejects a 200-tag manifest with a deterministic bounds issue", () => {
    expectRejected(doc({ tags: new Array(200).fill("tag") }), "exceeds the maximum of 16 entries");
  });

  it("rejects a 200-alias manifest with a deterministic bounds issue", () => {
    expectRejected(doc({ aliases: new Array(200).fill("alias") }), "exceeds the maximum of 16 entries");
  });

  it("rejects oversized supportedInteractions (>8) and evidenceCapabilities (>8)", () => {
    expectRejected(
      doc({ supportedInteractions: new Array(9).fill("inspect") }),
      "supportedInteractions: exceeds the maximum of 8 entries",
    );
    expectRejected(
      doc({ evidenceCapabilities: new Array(9).fill("cap") }),
      "evidenceCapabilities: exceeds the maximum of 8 entries",
    );
  });

  it("rejects oversized allowedAnchors (>16) and colors (>16)", () => {
    expectRejected(
      doc({ allowedAnchors: new Array(17).fill("A") }),
      "allowedAnchors: exceeds the maximum of 16 entries",
    );
    expectRejected(
      doc({ colors: Object.fromEntries(new Array(17).fill(0).map((_, n) => [`c${n}`, "#112233"])) }),
      "colors: exceeds the maximum of 16 entries",
    );
  });
});

describe("DEF-059 (" + "Phase 12) — the frontend compositeKind list stays in lockstep with the frozen manifest", () => {
  const COMPOSITE_KINDS = ["kitchen_knife", "letter_opener", "scissors", "laptop", "victim", "table"] as const;

  it("SUPPORTED_COMPOSITE_KINDS equals exactly the manifest's LEGACY composite kinds", () => {
    // Phase 12: template-only composites carry compositeKind null, so the
    // lockstep list is derived from the NON-NULL manifest composite kinds.
    const manifestKinds = catalogAssetIds()
      .map((id) => getAsset(id))
      .filter((d): d is CatalogAssetDescriptor => d !== undefined && d.renderKind === "composite")
      .map((d) => d.compositeKind)
      .filter((kind): kind is string => kind !== null)
      .sort();
    expect(manifestKinds).toEqual([...COMPOSITE_KINDS].sort());
    expect([...SUPPORTED_COMPOSITE_KINDS].sort()).toEqual(manifestKinds);
    // The strict validator accepts the real bundled manifest end-to-end, so
    // every frozen compositeKind is accepted (the two sides stay in lockstep).
    expect(() => validateCatalog(rawCatalog as unknown)).not.toThrow();
  });

  it("rejects a future unknown compositeKind ('chainsaw') the frontend cannot build", () => {
    const doc = {
      catalogVersion: 1,
      fallbackAsset: "PROP_Z_01",
      assets: [
        {
          assetId: "PROP_Z_01",
          version: 1,
          canonicalName: "future thing",
          aliases: [],
          category: "utility",
          subtype: "prop",
          tags: [],
          renderKind: "composite",
          compositeKind: "chainsaw",
          dimensions: { x: 0.5, y: 0.5, z: 0.5 },
          colors: { body: "#8d8d93" },
          label: "Future thing",
          interactable: false,
          supportedInteractions: [],
          evidenceCapabilities: [],
          allowedAnchors: [],
        },
      ],
    };
    expect(() => validateCatalog(doc)).toThrow(CatalogValidationError);
    expect(() => validateCatalog(doc)).toThrow(/chainsaw/);
  });
});

/* ======================================================================
 * Phase 12 Track B — the frozen template/variant schema (validator extension)
 * ==================================================================== */

/** A minimal Phase 12 composite: template-only (compositeKind null). */
function makeTemplateComposite(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    assetId: "PROP_TEMPLATE_01",
    version: 1,
    canonicalName: "template thing",
    aliases: [],
    category: "evidence",
    subtype: "tool",
    tags: ["tool"],
    renderKind: "composite",
    compositeKind: null,
    templateId: "tool_hammer",
    dimensions: { x: 0.4, y: 0.3, z: 0.2 },
    colors: { head: "#70767e", handle: "#b5651d" },
    label: "Template thing",
    interactable: true,
    supportedInteractions: [],
    evidenceCapabilities: [],
    allowedAnchors: [],
    variants: [],
    ...overrides,
  };
}

/**
 * A NEUTRAL fallback entry (DEF-066: the declared fallback of every fixture
 * document must be `utility` + non-interactable, mirroring the backend
 * DEF-058 invariant the validator now enforces). Kept OUT of
 * makeTemplateComposite so template-composite fixtures stay realistic
 * (evidence/interactable) while their documents validate cleanly.
 */
function makeNeutralFallbackEntry(): Record<string, unknown> {
  return {
    assetId: "PROP_FALLBACK_NEUTRAL_01",
    version: 1,
    canonicalName: "neutral fallback",
    aliases: [],
    category: "utility",
    subtype: "fallback",
    tags: [],
    renderKind: "box",
    compositeKind: null,
    templateId: null,
    dimensions: { x: 0.4, y: 0.4, z: 0.4 },
    colors: { body: "#8d8d93" },
    label: "Unknown object",
    interactable: false,
    supportedInteractions: [],
    evidenceCapabilities: [],
    allowedAnchors: [],
    variants: [],
  };
}

function makeTemplateDoc(
  asset: Record<string, unknown> | Record<string, unknown>[],
): Record<string, unknown> {
  const assets = Array.isArray(asset) ? asset : [asset];
  return {
    catalogVersion: 1,
    fallbackAsset: "PROP_FALLBACK_NEUTRAL_01",
    assets: [...assets, makeNeutralFallbackEntry()],
  };
}

const VALID_VARIANTS = [
  {
    name: "standard",
    params: {
      material: { allowlist: ["metal.steel", "metal.brass"], default: "metal.steel" },
      state: { allowlist: ["clean", "weathered"], default: "clean" },
      scale: { min: 0.9, max: 1.1, default: 1.0 },
    },
  },
  {
    name: "worn",
    params: {
      material: { allowlist: ["metal.steel", "metal.brass"], default: "metal.steel" },
      state: { allowlist: ["clean", "weathered"], default: "weathered" },
      scale: { min: 0.9, max: 1.1, default: 1.0 },
    },
  },
];

describe("Phase 12 — the FULL extended schema validates the real 101-entry manifest", () => {
  it("the bundled manifest validates with ZERO issues under the extended schema", () => {
    const document = validateCatalog(rawCatalog as unknown);
    expect(document.assets).toHaveLength(101);
    expect(document.catalogVersion).toBe(1);
    for (const descriptor of document.assets) {
      if (descriptor.renderKind === "composite") {
        expect(descriptor.templateId, `${descriptor.assetId} carries a frozen templateId`).not.toBeNull();
        expect(TEMPLATE_VOCABULARY).toContain(descriptor.templateId);
        expect(descriptor.variants.length).toBeLessThanOrEqual(3);
      } else {
        expect(descriptor.templateId).toBeNull();
        expect(descriptor.variants).toEqual([]);
      }
    }
    // Every composite carries a BUILDABLE factory template (getTemplate !==
    // fallback): the manifest and the frozen vocabulary stay in lockstep.
    const unknownTemplates = document.assets
      .filter((d) => d.renderKind === "composite" && d.templateId !== null)
      .filter((d) => !TEMPLATE_VOCABULARY.includes(d.templateId as string));
    expect(unknownTemplates).toEqual([]);
  });

  it("every variant allowlist/member the REAL manifest uses is frozen-legal", () => {
    const document = validateCatalog(rawCatalog as unknown);
    for (const descriptor of document.assets) {
      for (const variant of descriptor.variants) {
        expect(variant.name).toMatch(/^[a-z0-9_]{1,24}$/);
        for (const [key, spec] of Object.entries(variant.params)) {
          if (key === "scale") {
            expect(spec.minValue).not.toBeNull();
            expect(spec.maxValue).not.toBeNull();
            expect(spec.minValue!).toBeGreaterThanOrEqual(0.5);
            expect(spec.maxValue!).toBeLessThanOrEqual(2.0);
          } else if (key === "color") {
            expect(spec.allowlist.every((hex) => /^#[0-9a-fA-F]{6}$/.test(hex))).toBe(true);
          } else if (key === "material") {
            expect(spec.allowlist.every((token) => MATERIAL_VOCABULARY.includes(token))).toBe(true);
          } else if (key === "state") {
            expect(spec.allowlist.every((token) => STATE_VOCABULARY.includes(token))).toBe(true);
          }
        }
      }
    }
  });

  it("accepts a minimal Phase 12 composite with valid bounded variants", () => {
    const doc = makeTemplateDoc(makeTemplateComposite({ variants: VALID_VARIANTS }));
    expect(() => validateCatalog(doc)).not.toThrow();
  });
});

describe("Phase 12 — strict rejection of corrupt template/variant schema", () => {
  const expectIssues = (raw: unknown, ...needles: string[]): readonly string[] => {
    let caught: CatalogValidationError | null = null;
    try {
      validateCatalog(raw);
    } catch (error) {
      expect(error).toBeInstanceOf(CatalogValidationError);
      caught = error as CatalogValidationError;
    }
    expect(caught, "expected the validator to reject this manifest").not.toBeNull();
    const joined = caught!.issues.join("\n");
    for (const needle of needles) {
      expect(joined).toContain(needle);
    }
    return caught!.issues;
  };

  it("rejects an unknown composite templateId", () => {
    const doc = makeTemplateDoc(makeTemplateComposite({ templateId: "no_such_template" }));
    expectIssues(doc, "TEMPLATE_VOCABULARY");
  });

  it("rejects a templateId on a NON-composite asset", () => {
    const doc = makeTemplateDoc({
      ...makeTemplateComposite(),
      renderKind: "box",
      templateId: "tool_hammer",
      compositeKind: null,
    });
    expectIssues(doc, "templateId must be null unless renderKind");
  });

  it("rejects more than 3 named variants per asset", () => {
    const four = [
      ...VALID_VARIANTS,
      { name: "third", params: VALID_VARIANTS[0].params },
      { name: "fourth", params: VALID_VARIANTS[0].params },
    ];
    const doc = makeTemplateDoc(makeTemplateComposite({ variants: four }));
    expectIssues(doc, "exceeds the maximum of 3 variants");
  });

  it("rejects an out-of-range declared scale (below the category floor)", () => {
    const badScale = structuredClone(VALID_VARIANTS);
    (badScale[0].params.scale as Record<string, unknown>).min = 0.1;
    const doc = makeTemplateDoc(makeTemplateComposite({ variants: badScale }));
    expectIssues(doc, "must be within [0.5, 2]");
  });

  it("rejects an unsafe material token ('neon.glow') in an allowlist", () => {
    const bad = structuredClone(VALID_VARIANTS);
    (bad[0].params.material as Record<string, unknown>).allowlist = ["neon.glow"];
    (bad[0].params.material as Record<string, unknown>).default = "neon.glow";
    const doc = makeTemplateDoc(makeTemplateComposite({ variants: bad }));
    expectIssues(doc, "MATERIAL_VOCABULARY");
  });

  it("rejects a non-hex color allowlist entry and a non-hex default", () => {
    const bad = structuredClone(VALID_VARIANTS);
    (bad[0] as Record<string, unknown>).params = {
      color: { allowlist: ["#30343e", "steel"], default: "#30343e" },
    };
    const doc = makeTemplateDoc(makeTemplateComposite({ variants: bad }));
    expectIssues(doc, "#RRGGBB");

    const badDefault = structuredClone(VALID_VARIANTS);
    (badDefault[0] as Record<string, unknown>).params = {
      color: { allowlist: ["#30343e"], default: "graphite" },
    };
    expectIssues(makeTemplateDoc(makeTemplateComposite({ variants: badDefault })), "#RRGGBB");
  });

  it("rejects conflicting per-asset variant bounds (only DEFAULTS may differ)", () => {
    const conflicting = structuredClone(VALID_VARIANTS);
    const worn = conflicting[1].params as Record<string, unknown>;
    worn.scale = { min: 0.8, max: 1.2, default: 1.0 };
    const doc = makeTemplateDoc(makeTemplateComposite({ variants: conflicting }));
    expectIssues(doc, "conflicts with an earlier variant", "allowlist/scale bounds must agree");
  });

  it("rejects an unknown variant parameter key and a bad variant name", () => {
    const unknownKey = structuredClone(VALID_VARIANTS);
    ((unknownKey[0].params as Record<string, unknown>).hue = { allowlist: ["#30343e"], default: "#30343e" });
    expectIssues(makeTemplateDoc(makeTemplateComposite({ variants: unknownKey })), "unknown variant parameter", "hue");

    const badName = structuredClone(VALID_VARIANTS);
    (badName[0] as Record<string, unknown>).name = "Standard!";
    expectIssues(makeTemplateDoc(makeTemplateComposite({ variants: badName })), "must match ^[a-z0-9_]{1,24}$");
  });

  it("rejects a default that is not present in its allowlist", () => {
    const bad = structuredClone(VALID_VARIANTS);
    (bad[0].params.state as Record<string, unknown>).default = "damaged";
    const doc = makeTemplateDoc(makeTemplateComposite({ variants: bad }));
    expectIssues(doc, "default: must be present in the allowlist");
  });

  it("rejects an oversized variant allowlist (>16 entries)", () => {
    const big = structuredClone(VALID_VARIANTS);
    (big[0].params.material as Record<string, unknown>).allowlist = new Array(17).fill("metal.steel");
    const doc = makeTemplateDoc(makeTemplateComposite({ variants: big }));
    expectIssues(doc, "exceeds the maximum of 16 entries");
  });
});

/* ======================================================================
 * DEF-066 (LOW, OPEN) — the NEUTRAL-FALLBACK invariant mirrors the backend
 * DEF-058 check. `validateCatalog` must reject a manifest whose declared
 * fallbackAsset is an INTERACTABLE or NON-utility asset (the frontend would
 * otherwise resolve unknown assetIds to the knife's scale/color instead of
 * the neutral placeholder). Wording mirrors backend/app/assets/catalog.py
 * _fallback_issues.
 * ==================================================================== */

describe("DEF-066 — the neutral-fallback invariant (backend DEF-058 mirror)", () => {
  /** A minimal single-entry document whose fallback IS that entry. */
  const makeDoc = (entry: Record<string, unknown>): Record<string, unknown> => ({
    catalogVersion: 1,
    fallbackAsset: entry.assetId as string,
    assets: [entry],
  });

  /** The neutral, fully-valid fallback shape (utility + NON-interactable). */
  const neutralEntry = (overrides: Record<string, unknown> = {}): Record<string, unknown> => ({
    assetId: "PROP_FALLBACK_01",
    version: 1,
    canonicalName: "neutral fallback",
    aliases: [],
    category: "utility",
    subtype: "fallback",
    tags: [],
    renderKind: "box",
    compositeKind: null,
    templateId: null,
    dimensions: { x: 0.4, y: 0.4, z: 0.4 },
    colors: { body: "#8d8d93" },
    label: "Unknown object",
    interactable: false,
    supportedInteractions: [],
    evidenceCapabilities: [],
    allowedAnchors: [],
    ...overrides,
  });

  const expectIssues = (raw: unknown, ...needles: string[]): readonly string[] => {
    let caught: CatalogValidationError | null = null;
    try {
      validateCatalog(raw);
    } catch (error) {
      expect(error).toBeInstanceOf(CatalogValidationError);
      caught = error as CatalogValidationError;
    }
    expect(caught, "expected the validator to reject this manifest").not.toBeNull();
    const joined = caught!.issues.join("\n");
    for (const needle of needles) {
      expect(joined).toContain(needle);
    }
    return caught!.issues;
  };

  it("the real 101-entry manifest still validates clean (PROP_FALLBACK_01 passes the invariant)", () => {
    expect(() => validateCatalog(rawCatalog as unknown)).not.toThrow();
    const document = validateCatalog(rawCatalog as unknown);
    expect(document.assets).toHaveLength(101);
    const fallback = document.assets.find((d) => d.assetId === "PROP_FALLBACK_01");
    expect(fallback, "PROP_FALLBACK_01 declared").not.toBeUndefined();
    expect(fallback!.category).toBe("utility");
    expect(fallback!.interactable).toBe(false);
  });

  it("a knife-as-fallback manifest (interactable + evidence) is rejected with fallback issues", () => {
    const knifeAsFallback = neutralEntry({ category: "evidence", interactable: true, label: "Kitchen knife" });
    const issues = expectIssues(
      makeDoc(knifeAsFallback),
      "fallbackAsset",
      "must have category 'utility'",
      "must be non-interactable",
    );
    // Both violations are reported deterministically on the SAME manifest.
    expect(issues.join("\n")).toContain('has category "evidence"');
    expect(issues.join("\n")).toContain("is interactable");
  });

  it("a utility-but-INTERACTABLE fallback is rejected (non-interactable issue)", () => {
    const issues = expectIssues(
      makeDoc(neutralEntry({ interactable: true })),
      "fallbackAsset",
      "must be non-interactable",
    );
    // The category is already 'utility', so only the interactable issue fires.
    expect(issues.join("\n")).not.toContain("has category");
    expect(issues.join("\n")).toContain('fallbackAsset "PROP_FALLBACK_01" is interactable');
  });

  it("a NON-utility fallback (decor) is rejected (utility-category issue)", () => {
    const issues = expectIssues(
      makeDoc(neutralEntry({ category: "decor" })),
      "fallbackAsset",
      "must have category 'utility'",
    );
    expect(issues.join("\n")).not.toContain("is interactable");
  });

  it("a declared-but-MISSING fallbackAsset is rejected (already-declared check)", () => {
    const doc = makeDoc(neutralEntry());
    doc.fallbackAsset = "PROP_NOT_DECLARED_01";
    expectIssues(doc, "fallbackAsset", "is not a declared assetId");
  });

  it("the neutral PROP_FALLBACK_01 shape PASSES (utility, non-interactable)", () => {
    expect(() => validateCatalog(makeDoc(neutralEntry()))).not.toThrow();
    // Even with extra unrelated fields (templateId present but null), the
    // neutral fallback stays accepted.
    expect(() =>
      validateCatalog(makeDoc(neutralEntry({ templateId: null, variants: [] }))),
    ).not.toThrow();
  });
});