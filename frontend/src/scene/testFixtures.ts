import type {
  AccusationCandidatesDTO,
  AccusationResponse,
  EvidenceReadResultDTO,
  GeneratedAssetDefinition,
  GeneratedPartDTO,
  InvestigationBootstrapResponse,
  RevealResponse,
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