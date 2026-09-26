import type {
  AccusationCandidatesDTO,
  AccusationResponse,
  EvidenceReadResultDTO,
  GeneratedAssetDefinition,
  GeneratedPartDTO,
  InvestigationBootstrapResponse,
  RevealResponse,
  WitnessInterviewResponse,
  WitnessListEntryDTO,
  WitnessQuestionType,
  WorldObjectDTO,
} from "../api/types";

/**
 * Deterministic canned fixtures for Phase 6 frontend tests (fixture only —
 * never imported by application code, so it never ships in the bundle).
 *
 * The canned bootstrap mirrors the ACTUAL backend dev-mode case
 * (backend/app/services/dev_mode_case.json — world_graph.placements),
 * i.e. the exact 9 WorldObjectDTOs GET /api/v1/playthroughs/{id}/investigation
 * emits for the golden provider (DEF-049). Phase 10 Track B: the frontend
 * asset registry now derives from the SAME catalog manifest the backend
 * resolver uses (assets/catalog/catalog.json), and every emitted assetId
 * below is an EXACT v1 catalog id. This fixture is the contract snapshot: if
 * the backend emits a 10th id, this fixture AND the catalog/registry
 * contract-sync tests will fail loudly.
 *
 * Placements (from dev_mode_case.json, sorted by the backend's objectId).
 * Phase 10 DEF-062: the DTO interaction strings are the manifest-derived
 * values — non-empty ONLY for the evidence-bearing objects. The frontend
 * affordance (interactionWorks) is payload-driven, so knife/letter_opener/
 * scissors/laptop are clickable and the env/victim objects carry "":
 *   apartment_door      DOOR_APARTMENT_01   hall_wall_01        (no interaction)
 *   apartment_lamp      PROP_LAMP_01        shelf_01            (no interaction)
 *   apartment_laptop    PROP_LAPTOP_01      desk_main           read     -> email_thomas_01
 *   apartment_table     PROP_TABLE_01       dining_table        (no interaction)
 *   kitchen_knife       PROP_KITCHEN_KNIFE_01 kitchen_counter    inspect  -> forensic_knife_match_01
 *   letter_opener       PROP_LETTER_OPENER_01 office_desk_01     inspect  -> forensic_letter_opener_01
 *   scissors            PROP_SCISSORS_01    bedside_table       inspect  -> forensic_scissors_01
 *   vase_01             PROP_VASE_01        dining_table        (no interaction)
 *   victim_body_placeholder PROP_BODY_PLACEHOLDER_01 floor_body_position (no interaction)
 */

/** The exact assetIds the backend dev-mode case emits (contract snapshot). */
export const EMITTED_ASSET_IDS: readonly string[] = [
  "PROP_KITCHEN_KNIFE_01",
  "PROP_LETTER_OPENER_01",
  "PROP_SCISSORS_01",
  "PROP_VASE_01",
  "PROP_LAPTOP_01",
  "PROP_TABLE_01",
  "DOOR_APARTMENT_01",
  "PROP_LAMP_01",
  "PROP_BODY_PLACEHOLDER_01",
];

export function makeWorldObject(overrides: Partial<WorldObjectDTO> = {}): WorldObjectDTO {
  const base: WorldObjectDTO = {
    objectId: "kitchen_knife",
    assetId: "PROP_KITCHEN_KNIFE_01",
    assetType: "sharp_weapon",
    subtype: "sharp_weapon",
    locationId: "miller_apartment_kitchen",
    anchor: "kitchen_counter",
    interaction: "inspect",
    evidenceId: "forensic_knife_match_01",
    discovered: false,
    read: false,
  };
  return { ...base, ...overrides };
}

/* ======================================================================
 * Phase 13 — declarative generated asset fixtures.
 *
 * These mirror the backend compiler's frozen output shape
 * (`to_definition_json()`): a CANNED "custom trophy" — a depth-2 parented
 * composition exactly like the backend golden CUSTOM_TROPHY spec compiled
 * with the resolved material colors (wood.dark #5b3a29, metal.brass
 * #c9a227). Purely mechanical fixture data — no truth, no server text.
 * ==================================================================== */

/** A canned `proc.*` asset id matching the backend grammar (falls through
 * the client's `startsWith("proc.")` gate). */
export const PROC_TROPHY_ASSET_ID = "proc.decor.a1b2c3d4e5f60718";

/* ======================================================================
 * Phase 19E — GENERALIZED SEMANTIC OBJECT fixtures.
 *
 * These are WORK-ALIKES of the backend's Phase 19E canned specs (the driver's
 * locked-weapon injection + the procedural AssetSpec lane in
 * backend/tests/test_phase19e_semantic_pipeline.py): a deterministic "fork"
 * (the CaseTruth-declared weapon example) and a "coffee mug" (INTERACTIVE
 * decorative object, no evidence). Their objectIds ARE the semantic ids, the
 * assetIds are `proc.*` RENDER ids, and the validated canonicalNames are the
 * ONLY human-label sources the frontend may show.
 * ==================================================================== */

/** Canned `proc.*` FORK render asset id (semantic id = "fork"). */
export const FORK_PROC_ASSET_ID = "proc.decor.f00d5a11e4b2c313";

/** The evidence association the backend links the locked fork weapon to. */
export const FORK_EVIDENCE_ID = "forensic_fork_match_01";

/** Canned `proc.*` COFFEE MUG render asset id (semantic id = "coffee_mug"). */
export const COFFEE_MUG_PROC_ASSET_ID = "proc.decor.c0ffee7a11b2c313";

/**
 * Fork-shaped declarative definition (2 parts: prongs + handle) — the exact
 * backend FORK_SPEC shape (canonicalName "Fork", steel tones), bounds-valid
 * by construction and accepted by the Phase 13 client gate.
 */
export function makeForkDefinition(overrides: Partial<GeneratedAssetDefinition> = {}): GeneratedAssetDefinition {
  return {
    compilerVersion: 1,
    schemaVersion: 1,
    assetId: FORK_PROC_ASSET_ID,
    canonicalName: "Fork",
    dimensions: { x: 0.05, y: 0.3, z: 0.05 },
    parts: [
      makeGeneratedPart("part_00", {
        role: "prongs",
        transform: {
          position: { x: 0, y: 0, z: 0.02 },
          rotation: { x: 0, y: 0, z: 0 },
          scale: { x: 0.03, y: 0.1, z: 0.01 },
        },
        color: "#b9c0c8",
      }),
      makeGeneratedPart("part_01", {
        role: "handle",
        transform: {
          position: { x: 0, y: -0.12, z: 0.02 },
          rotation: { x: 0, y: 0, z: 0 },
          scale: { x: 0.025, y: 0.09, z: 0.02 },
        },
        color: "#b9c0c8",
      }),
    ],
    hitbox: { scale: { x: 0.15, y: 0.5, z: 0.15 } },
    ...overrides,
  };
}

/**
 * Coffee-mug declarative definition (cup body + handle) — the Phase 19E
 * INTERACTIVE-decorative example: a valid procedural object with interaction
 * "inspect" and NO evidence association.
 */
export function makeCoffeeMugDefinition(overrides: Partial<GeneratedAssetDefinition> = {}): GeneratedAssetDefinition {
  return {
    compilerVersion: 1,
    schemaVersion: 1,
    assetId: COFFEE_MUG_PROC_ASSET_ID,
    canonicalName: "Coffee Mug",
    dimensions: { x: 0.1, y: 0.16, z: 0.1 },
    parts: [
      makeGeneratedPart("part_00", {
        role: "body",
        primitive: "cylinder",
        transform: {
          position: { x: 0, y: 0, z: 0 },
          rotation: { x: 0, y: 0, z: 0 },
          scale: { x: 0.09, y: 0.12, z: 0.09 },
        },
        color: "#e8e4dc",
      }),
      makeGeneratedPart("part_01", {
        role: "handle",
        transform: {
          position: { x: 0.07, y: 0, z: 0 },
          rotation: { x: 0, y: 0, z: 0 },
          scale: { x: 0.03, y: 0.08, z: 0.02 },
        },
        color: "#e8e4dc",
      }),
    ],
    hitbox: { scale: { x: 0.2, y: 0.3, z: 0.2 } },
    ...overrides,
  };
}

/**
 * The semantic WEAPON world object the Phase 19E backend injects for
 * "Weapon: fork": objectId == the semantic id ("fork"), render assetId is a
 * `proc.*` id, the object carries a validated generated definition and the
 * evidence association (weapon evidence). Payload-driven interaction
 * "inspect".
 */
export function makeForkWorldObject(overrides: Partial<WorldObjectDTO> = {}): WorldObjectDTO {
  return makeWorldObject({
    objectId: "fork",
    assetId: FORK_PROC_ASSET_ID,
    assetType: "fork",
    subtype: "fork",
    locationId: "miller_apartment_kitchen",
    anchor: "kitchen_counter",
    interaction: "inspect",
    evidenceId: FORK_EVIDENCE_ID,
    generated: makeForkDefinition(),
    ...overrides,
  });
}

/**
 * The Phase 19E INTERACTIVE-DECORATIVE coffee mug: a valid procedural object
 * with interaction "inspect" and NO evidence association (server confirms
 * discovery:null -> the Phase 19C "Nothing relevant was found on <label>."
 * copy). Never a solver/weapon candidate member on the client.
 */
export function makeCoffeeMugWorldObject(overrides: Partial<WorldObjectDTO> = {}): WorldObjectDTO {
  return makeWorldObject({
    objectId: "coffee_mug",
    assetId: COFFEE_MUG_PROC_ASSET_ID,
    assetType: "coffee_mug",
    subtype: "coffee_mug",
    locationId: "miller_apartment_kitchen",
    anchor: "kitchen_counter",
    interaction: "inspect",
    evidenceId: null,
    generated: makeCoffeeMugDefinition(),
    ...overrides,
  });
}

/**
 * A RICHER published world (Phase 19E §"Scene richness"): the golden nine
 * plus the semantic fork (evidence-linked procedural weapon), the interactive
 * coffee mug (no evidence), a catalog variant (glass bottle), a catalog
 * template (claw hammer), a proc.* decorative trophy and more catalog
 * decorative objects. 17 objects total — far below the configured
 * MAX_WORLD_OBJECTS_PER_KIT (32), proving the scene model builds and renders
 * richer sets without regressions.
 */
export function makeRichWorldBootstrap(
  overrides: Partial<InvestigationBootstrapResponse> = {},
): InvestigationBootstrapResponse {
  const base = makeBootstrap();
  base.scene.worldObjects = [
    ...base.scene.worldObjects,
    makeForkWorldObject(),
    makeCoffeeMugWorldObject(),
    makeWorldObject({
      objectId: "glass_bottle",
      assetId: "PROP_GLASS_BOTTLE_01",
      assetType: "bottle",
      subtype: "bottle",
      anchor: "kitchen_counter",
      interaction: "",
      evidenceId: null,
    }),
    makeWorldObject({
      objectId: "claw_hammer",
      assetId: "PROP_HAMMER_01",
      assetType: "tool",
      subtype: "tool",
      anchor: "shelf_01",
      interaction: "",
      evidenceId: null,
    }),
    makeProcWorldObject({ objectId: "custom_trophy", anchor: "dining_table" }),
    makeWorldObject({
      objectId: "kitchen_clock",
      assetId: "PROP_CLOCK_01",
      assetType: "clock",
      subtype: "clock",
      anchor: "hall_wall_01",
      interaction: "",
      evidenceId: null,
    }),
    makeWorldObject({
      objectId: "desk_lamp",
      assetId: "PROP_DESK_LAMP_01",
      assetType: "light",
      subtype: "light",
      anchor: "desk_main",
      interaction: "",
      evidenceId: null,
    }),
    makeWorldObject({
      objectId: "wristwatch",
      assetId: "PROP_WATCH_01",
      assetType: "watch",
      subtype: "watch",
      anchor: "bedside_table",
      interaction: "",
      evidenceId: null,
    }),
  ];
  return makeBootstrap({ ...base, ...overrides });
}

/** One canned generated part (values are bounds-valid by construction). */
export function makeGeneratedPart(
  id: string,
  overrides: Partial<GeneratedPartDTO> = {},
): GeneratedPartDTO {
  return {
    id,
    role: "body",
    primitive: "box",
    transform: {
      position: { x: 0, y: 0, z: 0 },
      rotation: { x: 0, y: 0, z: 0 },
      scale: { x: 0.3, y: 0.3, z: 0.3 },
    },
    color: "#c9a227",
    parentId: null,
    ...overrides,
  };
}

/** Small 3-part factory fixture: box -> cylinder -> cup (DEPTH 2 chains). */
export function makeTrophyDefinition(overrides: Partial<GeneratedAssetDefinition> = {}): GeneratedAssetDefinition {
  return {
    compilerVersion: 1,
    schemaVersion: 1,
    assetId: PROC_TROPHY_ASSET_ID,
    canonicalName: "Custom Trophy",
    dimensions: { x: 0.3, y: 0.5, z: 0.3 },
    parts: [
      makeGeneratedPart("part_00", {
        role: "base",
        transform: {
          position: { x: 0, y: -0.22, z: 0 },
          rotation: { x: 0, y: 0, z: 0 },
          scale: { x: 0.3, y: 0.08, z: 0.22 },
        },
        color: "#5b3a29",
      }),
      makeGeneratedPart("part_01", {
        role: "stem",
        primitive: "cylinder",
        transform: {
          position: { x: 0, y: -0.06, z: 0 },
          rotation: { x: 0, y: 0, z: 0 },
          scale: { x: 0.08, y: 0.3, z: 0.08 },
        },
        color: "#c9a227",
        parentId: "part_00",
      }),
      makeGeneratedPart("part_02", {
        role: "cup",
        primitive: "cylinder",
        transform: {
          position: { x: 0, y: 0.17, z: 0 },
          rotation: { x: 0, y: 0, z: 0 },
          scale: { x: 0.18, y: 0.14, z: 0.18 },
        },
        color: "#c9a227",
        parentId: "part_01",
      }),
    ],
    hitbox: { scale: { x: 0.3, y: 0.5, z: 0.3 } },
    ...overrides,
  };
}

/**
 * DEF-079: the REAL published thin generated definition — the bronze
 * ceremonial ice pick (dims {0.04,0.32,0.04}, blade scale {0.005,0.1,0.005},
 * hitbox {0.15,0.66,0.15}) that Phase17D legalized and the pre-fix 0.05 m
 * client floor DROPPED (the object rendered as the neutral gray placeholder).
 * Its composite part span reaches 0.32 m in the longest axis and is NOT
 * collapsed below 0.06 m on every axis, so the shape-aware visible-extent
 * gate accepts it: thin-but-long is physically realistic, not near-zero.
 */
export function makeIcePickDefinition(overrides: Partial<GeneratedAssetDefinition> = {}): GeneratedAssetDefinition {
  return {
    compilerVersion: 1,
    schemaVersion: 1,
    assetId: "proc.decor.4551660f4a46b2eb", // the REAL published ice-pick id
    canonicalName: "Bronze Ceremonial Ice Pick",
    dimensions: { x: 0.04, y: 0.32, z: 0.04 },
    parts: [
      makeGeneratedPart("part_00", {
        role: "handle",
        primitive: "cylinder",
        transform: {
          position: { x: 0, y: -0.12, z: 0 },
          rotation: { x: 0, y: 0, z: 0 },
          scale: { x: 0.04, y: 0.2, z: 0.04 }, // 20 cm long, 4 cm diameter
        },
        color: "#8a5a2b",
      }),
      makeGeneratedPart("part_01", {
        role: "blade",
        transform: {
          position: { x: 0, y: 0.05, z: 0 },
          rotation: { x: 0, y: 0, z: 0 },
          scale: { x: 0.005, y: 0.1, z: 0.005 }, // 10 cm long, 5 mm thick — the 17D thin axis
        },
        color: "#c9a227",
      }),
    ],
    hitbox: { scale: { x: 0.15, y: 0.66, z: 0.15 } },
    ...overrides,
  };
}

/** A canned proc.* world object carrying a valid trophy definition. */
export function makeProcWorldObject(overrides: Partial<WorldObjectDTO> = {}): WorldObjectDTO {
  return makeWorldObject({
    objectId: "custom_trophy",
    assetId: PROC_TROPHY_ASSET_ID,
    assetType: "decor",
    subtype: "trophy",
    locationId: "miller_consulting_office",
    anchor: "office_desk_01",
    interaction: "",
    evidenceId: null,
    generated: makeTrophyDefinition(),
    ...overrides,
  });
}

/**
 * Canned golden bootstrap built from the REAL dev_mode_case.json placements:
 * 9 world objects, exact ids/anchors/interactions/evidence links.
 *
 * Phase 7 amendment: the bootstrap gains the player-safe `candidates` block.
 * The canned candidates below are deliberately GENERIC (they carry no
 * canonical answer and no golden names) — the frontend never hardcodes case
 * truth, and these fixtures exist only to exercise parsing/rendering
 * mechanics. They mirror the REAL candidate universes' shape (three suspects
 * sorted alphabetically by id, three motives, three weapons; winner unmarked).
 */
export function makeBootstrap(
  overrides: Partial<InvestigationBootstrapResponse> = {},
): InvestigationBootstrapResponse {
  return {
    playthroughId: "PT-test-0001",
    caseId: "CASE-test-01",
    caseVersion: 1,
    state: "PLAYING",
    playerKnowledge: {
      discoveredEvidenceIds: [],
      readEvidenceIds: [],
      visitedLocationIds: ["miller_apartment_kitchen"],
    },
    scene: {
      environmentId: "apartment",
      location: { locationId: "miller_apartment_kitchen", name: "Miller Apartment - Kitchen" },
      worldObjects: [
        makeWorldObject({
          objectId: "apartment_door",
          assetId: "DOOR_APARTMENT_01",
          assetType: "door",
          subtype: "door",
          anchor: "hall_wall_01",
          interaction: "",
          evidenceId: null,
        }),
        makeWorldObject({
          objectId: "apartment_lamp",
          assetId: "PROP_LAMP_01",
          assetType: "light",
          subtype: "light",
          anchor: "shelf_01",
          interaction: "",
          evidenceId: null,
        }),
        makeWorldObject({
          objectId: "apartment_laptop",
          assetId: "PROP_LAPTOP_01",
          assetType: "electronics",
          subtype: "electronics",
          anchor: "desk_main",
          interaction: "read",
          evidenceId: "email_thomas_01",
        }),
        makeWorldObject({
          objectId: "apartment_table",
          assetId: "PROP_TABLE_01",
          assetType: "furniture",
          subtype: "furniture",
          anchor: "dining_table",
          interaction: "",
          evidenceId: null,
        }),
        makeWorldObject(),
        makeWorldObject({
          objectId: "letter_opener",
          assetId: "PROP_LETTER_OPENER_01",
          assetType: "sharp_weapon",
          subtype: "sharp_weapon",
          locationId: "miller_consulting_office",
          anchor: "office_desk_01",
          interaction: "inspect",
          evidenceId: "forensic_letter_opener_01",
        }),
        makeWorldObject({
          objectId: "scissors",
          assetId: "PROP_SCISSORS_01",
          assetType: "sharp_weapon",
          subtype: "sharp_weapon",
          locationId: "miller_consulting_office",
          anchor: "bedside_table",
          interaction: "inspect",
          evidenceId: "forensic_scissors_01",
        }),
        makeWorldObject({
          objectId: "vase_01",
          assetId: "PROP_VASE_01",
          assetType: "vase",
          subtype: null,
          anchor: "dining_table",
          interaction: "",
          evidenceId: null,
        }),
        makeWorldObject({
          objectId: "victim_body_placeholder",
          assetId: "PROP_BODY_PLACEHOLDER_01",
          assetType: "victim_body",
          subtype: "victim_body",
          anchor: "floor_body_position",
          interaction: "",
          evidenceId: null,
        }),
      ],
    },
    candidates: makeCandidates(),
    ...overrides,
  };
}

/**
 * PD-SEC-01 (Phase 20) — simulate the NEW backend contract: strip the
 * pre-reveal evidence linkage (evidenceId) from every UNDISCOVERED world
 * object in a bootstrap. The key is DELETED (not set to null), matching the
 * backend "omitted field" behavior the frontend validator must tolerate.
 * Discovered objects keep their (player-known) id.
 *
 * Pure — never mutates its input; fully type-preserving via object spreads.
 */
export function stripUndiscoveredEvidenceIds(
  bootstrap: InvestigationBootstrapResponse,
): InvestigationBootstrapResponse {
  return {
    ...bootstrap,
    scene: {
      ...bootstrap.scene,
      worldObjects: bootstrap.scene.worldObjects.map((obj) => {
        if (obj.discovered) return obj;
        const { evidenceId: _removed, ...rest } = obj;
        return rest as unknown as WorldObjectDTO;
      }),
    },
  };
}

/**
 * Phase 11 Track B caned fixture: the SAME golden asset set re-anchored on
 * OFFICE manifest anchors (the backend placer re-anchors the golden objects
 * per kit). Non-apartment world objects resolve through the office kit
 * manifest, so scene-integration tests can exercise a full non-apartment
 * scene without a backend.
 */
export function makeOfficeBootstrap(): InvestigationBootstrapResponse {
  const bootstrap = makeBootstrap();
  bootstrap.scene.environmentId = "office";
  bootstrap.scene.location = { locationId: "office_mainroom", name: "Office - Open Plan" };
  bootstrap.scene.worldObjects = [
    makeWorldObject({
      objectId: "office_desk_knife",
      assetId: "PROP_KITCHEN_KNIFE_01",
      assetType: "sharp_weapon",
      subtype: "sharp_weapon",
      locationId: "office_mainroom",
      anchor: "office_desk_a",
      interaction: "inspect",
      evidenceId: "forensic_knife_match_01",
    }),
    makeWorldObject({
      objectId: "office_laptop",
      assetId: "PROP_LAPTOP_01",
      assetType: "electronics",
      subtype: "electronics",
      locationId: "office_mainroom",
      anchor: "office_computer_01",
      interaction: "read",
      evidenceId: "email_thomas_01",
    }),
    makeWorldObject({
      objectId: "office_table",
      assetId: "PROP_TABLE_01",
      assetType: "furniture",
      subtype: "furniture",
      locationId: "office_meeting",
      anchor: "office_meeting_table",
      interaction: "",
      evidenceId: null,
    }),
    makeWorldObject({
      objectId: "office_vase",
      assetId: "PROP_VASE_01",
      assetType: "vase",
      subtype: null,
      locationId: "office_meeting",
      anchor: "office_meeting_table",
      interaction: "",
      evidenceId: null,
    }),
    makeWorldObject({
      objectId: "office_body",
      assetId: "PROP_BODY_PLACEHOLDER_01",
      assetType: "victim_body",
      subtype: "victim_body",
      locationId: "office_mainroom",
      anchor: "office_body_01",
      interaction: "",
      evidenceId: null,
    }),
  ];
  return bootstrap;
}

/**
 * Phase 18D showcase fixture: the SAME golden asset set re-anchored on HOTEL
 * SUITE manifest anchors (the backend placer re-anchors per kit). Deliberately
 * avoids the Phase 18D decor anchors (hotel_generic_01 hosts the lounge
 * composition) — world objects here park on the study/desk/bedroom anchors so
 * scene-integration tests exercise a full hotel scene without overlapping the
 * deterministic shell decor.
 */
export function makeHotelSuiteBootstrap(): InvestigationBootstrapResponse {
  const bootstrap = makeBootstrap();
  bootstrap.scene.environmentId = "hotel_suite";
  bootstrap.scene.location = { locationId: "hotel_study", name: "Hotel Suite - Study Nook" };
  bootstrap.scene.worldObjects = [
    makeWorldObject({
      objectId: "hotel_desk_knife",
      assetId: "PROP_KITCHEN_KNIFE_01",
      assetType: "sharp_weapon",
      subtype: "sharp_weapon",
      locationId: "hotel_study",
      anchor: "hotel_desk_01",
      interaction: "inspect",
      evidenceId: "forensic_knife_match_01",
    }),
    makeWorldObject({
      objectId: "hotel_laptop",
      assetId: "PROP_LAPTOP_01",
      assetType: "electronics",
      subtype: "electronics",
      locationId: "hotel_study",
      anchor: "hotel_computer_01",
      interaction: "read",
      evidenceId: "email_thomas_01",
    }),
    makeWorldObject({
      objectId: "hotel_table",
      assetId: "PROP_TABLE_01",
      assetType: "furniture",
      subtype: "furniture",
      locationId: "hotel_study",
      anchor: "hotel_desk_02",
      interaction: "",
      evidenceId: null,
    }),
    makeWorldObject({
      objectId: "hotel_body",
      assetId: "PROP_BODY_PLACEHOLDER_01",
      assetType: "victim_body",
      subtype: "victim_body",
      locationId: "hotel_bedroom",
      anchor: "hotel_body_01",
      interaction: "",
      evidenceId: null,
    }),
  ];
  return bootstrap;
}

/**
 * Canned player-safe candidate universes (the shape the backend publishes in
 * the bootstrap `candidates` block). Deliberately generic and winner-free:
 * no `correct`/`winner` fields exist (see phase-7 leak boundary), and the
 * arrays preserve the server's alphabetical ordering — the client must not
 * re-order them.
 */
export function makeCandidates(overrides: Partial<AccusationCandidatesDTO> = {}): AccusationCandidatesDTO {
  return {
    suspects: [
      { id: "suspect_alpha", name: "Ada Marsh" },
      { id: "suspect_beta", name: "Blake Niven" },
      { id: "suspect_gamma", name: "Casey Holt" },
    ],
    motives: [
      { id: "motive_alpha", label: "A dispute over money" },
      { id: "motive_beta", label: "Jealousy toward the victim" },
      { id: "motive_gamma", label: "Panic after a failed scheme" },
    ],
    weapons: [
      { id: "weapon_alpha", assetId: "PROP_GENERIC_01", name: "Kitchen knife" },
      { id: "weapon_beta", assetId: "PROP_GENERIC_02", name: "Letter opener" },
      { id: "weapon_gamma", assetId: "PROP_GENERIC_03", name: "Scissors" },
    ],
    ...overrides,
  };
}

/**
 * Canned 200 accusation response — the server accepted the accusation. The
 * response NEVER contains truth: only the immutable submitted dimensions.
 */
export function makeAccusationResponse(
  overrides: Partial<AccusationResponse> = {},
): AccusationResponse {
  return {
    playthroughId: "PT-test-0001",
    caseId: "CASE-test-01",
    caseVersion: 1,
    status: "ACCUSED",
    accusation: {
      murdererId: "suspect_alpha",
      motiveId: "motive_alpha",
      weaponId: "weapon_alpha",
      crimeTime: "21:45:00",
    },
    ...overrides,
  };
}

/**
 * Canned reveal DTO. The canonical truth here is GENERIC (never the golden
 * case's canonical answers) — the reveal screen is entirely DTO-driven, and
 * fixtures must stay free of golden literals so the shipped bundle can never
 * contain case answers.
 *
 * The player's accusation matches the truth exactly (solved case).
 */
export const CANNED_REVEAL_CRIME_TIME = "2026-09-11T21:45:00+02:00";

export function makeRevealResponse(overrides: Partial<RevealResponse> = {}): RevealResponse {
  const base: RevealResponse = {
    playthroughId: "PT-test-0001",
    caseId: "CASE-test-01",
    caseVersion: 1,
    status: "REVEALED",
    truth: {
      murdererId: "suspect_alpha",
      murdererName: "Ada Marsh",
      motiveId: "motive_alpha",
      motiveLabel: "A dispute over money",
      weaponId: "weapon_alpha",
      weaponName: "Kitchen knife",
      crimeTime: CANNED_REVEAL_CRIME_TIME,
    },
    player: {
      accusation: {
        murdererId: "suspect_alpha",
        motiveId: "motive_alpha",
        weaponId: "weapon_alpha",
        crimeTime: `${CANNED_REVEAL_CRIME_TIME.slice(11, 19)}`,
      },
    },
    result: {
      murdererCorrect: true,
      motiveCorrect: true,
      weaponCorrect: true,
      timeCorrect: true,
      overall: "solved",
    },
    score: { correctDimensions: 4, totalDimensions: 4 },
    timeline: [
      { time: "2026-09-11T21:38:00+02:00", description: "A visitor enters the apartment" },
      { time: "2026-09-11T22:03:00+02:00", description: "The visitor leaves in a hurry" },
      { time: CANNED_REVEAL_CRIME_TIME, description: "The crime occurs" },
    ],
    explanation: {
      evidence: [
        {
          evidenceId: "record_generic_01",
          title: "A bank transfer",
          point: "The transferred amount matches the embezzled total reported to the firm.",
        },
        {
          evidenceId: "record_generic_02",
          title: "Weapon match",
          point: "The kitchen knife carries traces matching the description of the crime.",
        },
      ],
      // Phase 18C: the backend's authoritative per-dimension proof-board
      // grouping. Generic fixture material only — never golden literals.
      dimensions: {
        who: [
          {
            evidenceId: "record_generic_01",
            title: "A bank transfer",
            point: "The transferred amount matches the embezzled total reported to the firm.",
          },
        ],
        why: [
          {
            evidenceId: "record_generic_01",
            title: "A bank transfer",
            point: "The transferred amount matches the embezzled total reported to the firm.",
          },
        ],
        weapon: [
          {
            evidenceId: "record_generic_02",
            title: "Weapon match",
            point: "The kitchen knife carries traces matching the description of the crime.",
          },
        ],
        when: [
          {
            evidenceId: "record_generic_03",
            title: "Hallway camera",
            point: "The camera log places the visitor at the door just before the crime.",
          },
        ],
      },
    },
    ...overrides,
  };
  return base;
}

/** A reveal where every player dimension is WRONG (overall "incorrect", 0/4). */
export function makeWrongReveal(overrides: Partial<RevealResponse> = {}): RevealResponse {
  return makeRevealResponse({
    player: {
      accusation: {
        murdererId: "suspect_beta",
        motiveId: "motive_gamma",
        weaponId: "weapon_beta",
        crimeTime: "18:20:00",
      },
    },
    result: {
      murdererCorrect: false,
      motiveCorrect: false,
      weaponCorrect: false,
      timeCorrect: false,
      overall: "incorrect",
    },
    score: { correctDimensions: 0, totalDimensions: 4 },
    ...overrides,
  });
}

/** A reveal where exactly two dimensions are correct (mixed indicators). */
export function makePartialReveal(overrides: Partial<RevealResponse> = {}): RevealResponse {
  return makeRevealResponse({
    player: {
      accusation: {
        murdererId: "suspect_alpha",
        motiveId: "motive_gamma",
        weaponId: "weapon_alpha",
        crimeTime: "18:20:00",
      },
    },
    result: {
      murdererCorrect: true,
      motiveCorrect: false,
      weaponCorrect: true,
      timeCorrect: false,
      overall: "incorrect",
    },
    score: { correctDimensions: 2, totalDimensions: 4 },
    ...overrides,
  });
}

/** A reveal whose every text field looks executable — inertness test fodder. */
export function makeHostileReveal(): RevealResponse {
  const hostile = "<script>alert(1)</script>";
  const unicode = `${hostile} — ひらがな — 你好 — 😀`;
  return {
    playthroughId: hostile,
    caseId: hostile,
    caseVersion: 1,
    status: "REVEALED",
    truth: {
      murdererId: hostile,
      murdererName: unicode,
      motiveId: hostile,
      motiveLabel: unicode,
      weaponId: hostile,
      weaponName: unicode,
      crimeTime: unicode,
    },
    player: {
      accusation: {
        murdererId: hostile,
        motiveId: hostile,
        weaponId: hostile,
        crimeTime: unicode,
      },
    },
    result: {
      murdererCorrect: false,
      motiveCorrect: false,
      weaponCorrect: false,
      timeCorrect: false,
      overall: "incorrect",
    },
    score: { correctDimensions: 0, totalDimensions: 4 },
    timeline: [
      { time: "2026-09-11T21:38:00+02:00", description: unicode },
      { time: hostile, description: unicode },
    ],
    explanation: {
      evidence: [{ evidenceId: hostile, title: unicode, point: unicode }],
    },
  };
}

/** A 43-char opaque-looking token (matches backend token_urlsafe(32) length). */
export const TEST_TOKEN = "lX9fQ3sWv0aB2cD4eF6gH8iJ1kM3nO5pQ7rS9tU";

export function makeEmailRecord(overrides: Partial<EvidenceReadResultDTO> = {}): EvidenceReadResultDTO {
  return {
    evidenceId: "email_thomas_01",
    kind: "email",
    title: "Weekend plans",
    description: "A short email from Thomas.",
    openedAt: "2026-09-11T22:20:00+02:00",
    readByPlayer: true,
    content: {
      fromPersonId: "thomas_reed",
      toPersonIds: ["sarah_miller"],
      subject: "Weekend plans",
      body: "Sarah, let us talk before the weekend.",
      timestamp: "2026-09-10T18:04:00+02:00",
    },
    ...overrides,
  };
}

/** A record whose every text field looks like executable HTML — inertness test fodder. */
export function makeHostileRecord(): EvidenceReadResultDTO {
  const hostile = "<script>alert(1)</script>";
  return {
    evidenceId: "hostile_01",
    kind: "email",
    title: hostile,
    description: hostile,
    openedAt: "2026-09-11T22:20:00+02:00",
    readByPlayer: true,
    content: {
      fromPersonId: hostile,
      toPersonIds: [hostile, "<img src=x onerror=alert(2)>"],
      subject: hostile,
      body: hostile + " — ひらがな — 你好 — 😀",
      timestamp: hostile,
    },
  };
}

/* ======================================================================
 * Phase 18C — Detective Notebook fixtures.
 *
 * Player-safe READ records across the kinds the notebook groups on. These
 * are generic fixtures (no golden literals): they exercise the grouping
 * mechanics, not any real case's answer. `makeWitnessRecord` etc. may be
 * overridden to inject HIDDEN truth markers so safety tests can prove
 * those markers never reach the notebook model or markup.
 * ==================================================================== */

/** A read witness statement (People group + speaker safety tests). */
export function makeWitnessRecord(overrides: Partial<EvidenceReadResultDTO> = {}): EvidenceReadResultDTO {
  return {
    evidenceId: "record_witness_hall_01",
    kind: "witness_statement",
    title: "A neighbour's statement",
    description: "Statement from the neighbour across the hall.",
    openedAt: "2026-09-11T22:20:00+02:00",
    readByPlayer: true,
    content: {
      speakerName: "Sofia Lindgren",
      statement: "I saw a tall figure hurrying out just before ten.",
    },
    ...overrides,
  };
}

/** A read financial record (Motive group). */
export function makeFinancialRecord(overrides: Partial<EvidenceReadResultDTO> = {}): EvidenceReadResultDTO {
  return {
    evidenceId: "record_financial_04",
    kind: "financial",
    title: "An unexpected transfer",
    description: "A large transfer that stands out from the monthly pattern.",
    openedAt: "2026-09-11T22:20:00+02:00",
    readByPlayer: true,
    content: { suspicious: true, rows: [] },
    ...overrides,
  };
}

/** A read CCTV record (Timeline group via content.events[].time). */
export function makeCctvRecord(overrides: Partial<EvidenceReadResultDTO> = {}): EvidenceReadResultDTO {
  return {
    evidenceId: "record_cctv_02",
    kind: "cctv",
    title: "Hallway camera",
    description: "Night footage from the hallway camera.",
    openedAt: "2026-09-11T22:20:00+02:00",
    readByPlayer: true,
    content: {
      cameraId: "hall_cam_1",
      events: [
        { time: "2026-09-11T21:38:00+02:00", personId: "unknown", action: "enters the hallway" },
        { time: "2026-09-11T22:03:00+02:00", personId: "unknown", action: "leaves the hallway" },
      ],
    },
    ...overrides,
  };
}

/** A read object-kind record (Digital/physical group). */
export function makeObjectRecord(overrides: Partial<EvidenceReadResultDTO> = {}): EvidenceReadResultDTO {
  return {
    evidenceId: "record_object_trophy_01",
    kind: "object",
    title: "A heavy ornament",
    description: "A heavy decorative object from the victim's study.",
    openedAt: "2026-09-11T22:20:00+02:00",
    readByPlayer: true,
    content: { subtype: "trophy", locationId: "miller_consulting_office" },
    ...overrides,
  };
}

/* ======================================================================
 * Phase 23 — WITNESS INTERVIEW fixtures.
 *
 * Two deterministically canned witnesses mirror the backend contract:
 *   - Emily Reed — the CANONICAL/golden witness (backend golden fixture
 *     person id "emily_reed"); ON_SCENE (a pickable person world object). Her
 *     ID IS the semantic person id, so 3D child meshes resolve to it.
 *   - Lisa König — the DRIVER witness ("lisa_koenig", the ollama driver's
 *     canonical witness); REMOTE_STATEMENT (reachable through the Witnesses
 *     section only, never rendered physically — no forcing a witness to
 *     stand next to the victim).
 * The canned statements are deterministic, player-safe, evidence-grounded
 * text (NO case truth, NO hidden solver material) and follow the Phase 23
 * statement model exactly: {summary, observations:[{time?, text}]}.
 * ==================================================================== */

/** Golden canonical witness — Emily Reed (ON_SCENE). */
export const EMILY_WITNESS_ID = "witness_emily_reed";
export const EMILY_WITNESS_NAME = "Emily Reed";

/** Driver witness — Lisa König (REMOTE_STATEMENT, Unicode name). */
export const LISA_WITNESS_ID = "witness_lisa_koenig";
export const LISA_WITNESS_NAME = "Lisa König";

/** The interview-discovery evidence id for Emily's TIME question. */
export const EMILY_TIME_EVIDENCE_ID = "record_witness_statement_emily_time_01";

/** Witness interview question types (the closed enum). */
export const WITNESS_QUESTION_TYPES: readonly WitnessQuestionType[] = [
  "OBSERVATION",
  "TIME",
  "SOUND",
  "PERSON",
  "OBJECT",
  "LOCATION",
];

/** One player-safe witness list entry (ids + names + presence ONLY). */
export function makeWitnessListEntry(
  overrides: Partial<WitnessListEntryDTO> = {},
): WitnessListEntryDTO {
  return {
    witnessId: EMILY_WITNESS_ID,
    displayName: EMILY_WITNESS_NAME,
    presence: "ON_SCENE",
    sceneObjectId: EMILY_WITNESS_ID,
    ...overrides,
  };
}

/** The ON_SCENE witness PERSON world object (semantic id == witness id). */
export function makeEmilyReedWorldObject(overrides: Partial<WorldObjectDTO> = {}): WorldObjectDTO {
  return makeWorldObject({
    objectId: EMILY_WITNESS_ID,
    assetId: "PROP_BODY_PLACEHOLDER_01",
    assetType: "character",
    subtype: "witness",
    locationId: "miller_apartment_kitchen",
    anchor: "hall_wall_01",
    interaction: "",
    evidenceId: null,
    ...overrides,
  });
}

/**
 * Golden Phase 23 bootstrap: the standard golden scene + the two witnesses
 * (Emily ON_SCENE with her pickable person object; Lisa REMOTE_STATEMENT).
 * The `witnesses` list is the ONLY witness source — no hidden statement
 * content exists anywhere on the payload.
 */
export function makeWitnessBootstrap(
  overrides: Partial<InvestigationBootstrapResponse> = {},
): InvestigationBootstrapResponse {
  const base = makeBootstrap();
  base.scene.worldObjects = [...base.scene.worldObjects, makeEmilyReedWorldObject()];
  return makeBootstrap({
    ...base,
    witnesses: [
      makeWitnessListEntry(),
      makeWitnessListEntry({
        witnessId: LISA_WITNESS_ID,
        displayName: LISA_WITNESS_NAME,
        presence: "REMOTE_STATEMENT",
        sceneObjectId: null,
      }),
    ],
    ...overrides,
  });
}

/** Emily's TIME answer: grounded, time-bearing, supporting WHEN (§21). */
export function makeEmilyTimeStatement(): WitnessInterviewResponse["statement"] {
  return {
    summary: "Emily recalls hearing a heavy impact at approximately 23:42.",
    observations: [
      { time: "23:40", text: "Emily entered the corridor." },
      { time: "23:42", text: "She heard a heavy impact from inside the laboratory." },
    ],
  };
}

/** The interview-discovery record for Emily's TIME question (an ordinary
 *  player-safe witness_statement record, exactly as the existing machinery
 *  publishes — kind-allowlisted content + Phase 19G render payload). */
export function makeEmilyTimeDiscoveryRecord(
  overrides: Partial<EvidenceReadResultDTO> = {},
): EvidenceReadResultDTO {
  return {
    evidenceId: EMILY_TIME_EVIDENCE_ID,
    kind: "witness_statement",
    title: "Emily Reed — when she was there",
    description: "A statement collected from the witness Emily Reed.",
    openedAt: "2026-09-11T23:20:00+02:00",
    readByPlayer: true,
    content: {
      renderType: "GENERIC_TEXT",
      summary: "Emily recalls hearing a heavy impact at approximately 23:42.",
      speakerName: EMILY_WITNESS_NAME,
      statement: "Emily recalls hearing a heavy impact at approximately 23:42.",
      witnessId: EMILY_WITNESS_ID,
      questionType: "TIME",
      observations: [
        { time: "23:40", text: "Emily entered the corridor." },
        { time: "23:42", text: "She heard a heavy impact from inside the laboratory." },
      ],
    },
    ...overrides,
  };
}

/** Emily's SOUND answer: honestly NEUTRAL — no evidence, no discovery (§10). */
export function makeEmilyNeutralStatement(): WitnessInterviewResponse["statement"] {
  return {
    summary: "No. Nothing stood out to me.",
    observations: [],
  };
}

/**
 * Deterministic canned interview response. `discovery` defaults to the TIME
 * discovery (newlyDiscovered true); pass discovery:null for neutral answers.
 */
export function makeWitnessInterviewResponse(
  questionType: WitnessQuestionType,
  overrides: Partial<WitnessInterviewResponse> = {},
): WitnessInterviewResponse {
  const statement =
    questionType === "TIME" ? makeEmilyTimeStatement() : makeEmilyNeutralStatement();
  const discovery =
    questionType === "TIME"
      ? { newlyDiscovered: true, record: makeEmilyTimeDiscoveryRecord() }
      : null;
  return {
    witnessId: EMILY_WITNESS_ID,
    displayName: EMILY_WITNESS_NAME,
    questionType,
    statement,
    discovery,
    ...overrides,
  };
}

/** A canned Lisa König (driver) grounded PERSON answer — no discovery. */
export function makeLisaPersonResponse(): WitnessInterviewResponse {
  return {
    witnessId: LISA_WITNESS_ID,
    displayName: LISA_WITNESS_NAME,
    questionType: "PERSON",
    statement: {
      summary:
        "Lisa says she saw a tall figure hurrying out of the corridor shortly after the noise.",
      observations: [
        { time: "23:43", text: "A tall figure left the corridor at speed." },
      ],
    },
    discovery: null,
  };
}

/** A HOSTILE interview response — every untrusted field looks executable.
 *  The frontend must render every value inert (escaped) and BOUNDED. */
export function makeHostileWitnessInterviewResponse(): WitnessInterviewResponse {
  const hostile = "<script>alert(1)</script>";
  return {
    witnessId: `${hostile} -- ひらがな -- 😀`,
    displayName: `${hostile} — Lisa</span><img src=x onerror=alert(2)>`,
    questionType: "TIME",
    statement: {
      summary:
        `${hostile} <img src=x onerror=alert(2)> — "Heard a heavy impact at 23:42." — ` +
        "X".repeat(4000),
      observations: [
        { time: hostile, text: `${hostile} <b>bold</b> ひらがな 😀` + "Y".repeat(1200) },
        { text: "A second observation without a time." },
      ],
    },
    discovery: null,
  };
}