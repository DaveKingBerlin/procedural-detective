/**
 * Phase 48 — curated data for the "Generate random case" prompt helper.
 *
 * This module carries ONLY frozen, app-owned player-facing data used by
 * {@link ./randomCrimeGenerator}. It is deliberately pure data: no RNG, no
 * `Math.random`, no side effects, no backend contract. The generated seed is
 * treated by the existing pipeline EXACTLY like text a user typed — this data
 * never special-cases generation.
 *
 * Gender taxonomy: the generator draws a gender from {@link CRIME_GENDERS}
 * (`male`/`female`), a SUBSET of the canonical Phase 47 victim-gender
 * vocabulary in `../scene/victimLabels.ts` (`male`/`female`/`other`/`unknown`).
 * The human label is always produced by that module's `victimGenderLabel` —
 * there is no second gender taxonomy here.
 *
 * Weapon assetIds are EXISTING ids from `assets/catalog/catalog.json`; every
 * one is asserted to resolve via the catalog oracle in the unit tests.
 */

import type { JourneyDifficulty } from "./context";

/** The two genders the generator draws from (subset of the Phase 47 vocab). */
export type CrimeGender = "male" | "female";

/** Ordered gender pool keys — the derived gender is picked FIRST. */
export const CRIME_GENDERS: readonly CrimeGender[] = Object.freeze(["male", "female"]);

/** One curated weapon: a real catalog assetId plus its player-facing name. */
export interface CuratedWeapon {
  /** An EXISTING `assets/catalog/catalog.json` assetId (validated in tests). */
  readonly assetId: string;
  /** The short, human-readable name shown on the "Weapon:" line. */
  readonly displayName: string;
  /** The difficulties this weapon is eligible at. */
  readonly difficultyTags: readonly JourneyDifficulty[];
}

/** One curated location with its difficulty tags. */
export interface CuratedLocation {
  readonly name: string;
  readonly difficultyTags: readonly JourneyDifficulty[];
}

/**
 * Gender-keyed first-name pools. The derived gender is chosen first and the
 * first name is then drawn from the MATCHING pool, so a generated person's
 * name and gender can never disagree. 80 names total (40 + 40).
 */
export const FIRST_NAMES_BY_GENDER: Readonly<Record<CrimeGender, readonly string[]>> =
  Object.freeze({
    male: Object.freeze([
      "James",
      "Daniel",
      "Thomas",
      "Michael",
      "David",
      "Robert",
      "Paul",
      "Peter",
      "Mark",
      "John",
      "Andrew",
      "Stefan",
      "Lukas",
      "Felix",
      "Jonas",
      "Maximilian",
      "Alexander",
      "Sebastian",
      "Tobias",
      "Martin",
      "Karl",
      "Heinrich",
      "Victor",
      "Oliver",
      "Simon",
      "Adrian",
      "Bruno",
      "Erik",
      "Frank",
      "Georg",
      "Hugo",
      "Ivan",
      "Julian",
      "Klaus",
      "Leo",
      "Marcus",
      "Nikolas",
      "Oscar",
      "Patrick",
      "Quentin",
    ]),
    female: Object.freeze([
      "Sarah",
      "Laura",
      "Anna",
      "Elena",
      "Nina",
      "Lisa",
      "Emily",
      "Julia",
      "Sophie",
      "Marie",
      "Clara",
      "Hannah",
      "Emma",
      "Mia",
      "Lena",
      "Greta",
      "Ingrid",
      "Katharina",
      "Christina",
      "Sabine",
      "Petra",
      "Monika",
      "Ursula",
      "Helga",
      "Beatrix",
      "Charlotte",
      "Diana",
      "Eva",
      "Fiona",
      "Gabriella",
      "Helena",
      "Irene",
      "Johanna",
      "Katrin",
      "Luisa",
      "Miriam",
      "Nadia",
      "Olivia",
      "Paula",
      "Rosa",
    ]),
  });

/** 72 curated last names (>= 64). */
export const LAST_NAMES: readonly string[] = Object.freeze([
  "Miller",
  "Reed",
  "Roth",
  "Weber",
  "Fischer",
  "Hartmann",
  "Stein",
  "Becker",
  "Weiss",
  "König",
  "Schmidt",
  "Schneider",
  "Wagner",
  "Meyer",
  "Schulz",
  "Hoffmann",
  "Schäfer",
  "Koch",
  "Bauer",
  "Richter",
  "Klein",
  "Wolf",
  "Neumann",
  "Schwarz",
  "Zimmermann",
  "Braun",
  "Krüger",
  "Hofmann",
  "Lange",
  "Schmitt",
  "Werner",
  "Schmitz",
  "Krause",
  "Meier",
  "Lehmann",
  "Schulte",
  "Scheer",
  "Vogel",
  "Friedrich",
  "Keller",
  "Günther",
  "Frank",
  "Berger",
  "Winkler",
  "Rothmann",
  "Beck",
  "Lorenz",
  "Baumann",
  "Franke",
  "Albrecht",
  "Schuster",
  "Simon",
  "Ludwig",
  "Böhm",
  "Winter",
  "Kraus",
  "Martin",
  "Schumacher",
  "Krämer",
  "Vogt",
  "Steinbach",
  "Jäger",
  "Otto",
  "Sommer",
  "Groß",
  "Seidel",
  "Heinrich",
  "Brandt",
  "Haas",
  "Schreiber",
  "Graf",
  "Dietrich",
]);

/** 40 curated, plausible crime-story occupations (>= 32). */
export const OCCUPATIONS: readonly string[] = Object.freeze([
  "teacher",
  "doctor",
  "lawyer",
  "journalist",
  "museum curator",
  "accountant",
  "researcher",
  "hotel manager",
  "architect",
  "professor",
  "chef",
  "politician",
  "artist",
  "pharmacist",
  "consultant",
  "detective",
  "historian",
  "engineer",
  "nurse",
  "banker",
  "editor",
  "librarian",
  "actor",
  "photographer",
  "pilot",
  "dentist",
  "judge",
  "diplomat",
  "scientist",
  "translator",
  "sommelier",
  "bartender",
  "receptionist",
  "security guard",
  "software developer",
  "novelist",
  "violinist",
  "surgeon",
  "tailor",
  "auctioneer",
]);

/** EXACTLY 32 unique motives (verbatim). */
export const MOTIVES: readonly string[] = Object.freeze([
  "financial gain",
  "inheritance",
  "jealousy",
  "revenge",
  "blackmail",
  "concealing fraud",
  "fear of exposure",
  "career rivalry",
  "romantic conflict",
  "family dispute",
  "corporate espionage",
  "stolen research",
  "professional sabotage",
  "embezzlement",
  "debt",
  "insurance fraud",
  "political leverage",
  "covering up theft",
  "concealing an affair",
  "custody dispute",
  "property dispute",
  "business betrayal",
  "personal humiliation",
  "protecting a secret",
  "witness silencing",
  "extortion",
  "identity fraud",
  "academic rivalry",
  "criminal partnership dispute",
  "inheritance manipulation",
  "fear of dismissal",
  "retaliation for betrayal",
]);

/**
 * EXACTLY 32 weapons (10 easy + 11 medium + 11 hard). Every assetId is an
 * existing catalog id. Availability policy (see randomCrimeGenerator):
 *   easy   -> tags include "easy"
 *   medium -> tags include "easy" or "medium"
 *   hard   -> all 32
 * Easy entries are tagged for BOTH easy and medium so their declared
 * availability matches the medium policy.
 */
export const WEAPONS: readonly CuratedWeapon[] = Object.freeze([
  // --- easy (also available at medium) ---
  { assetId: "PROP_KITCHEN_KNIFE_01", displayName: "kitchen knife", difficultyTags: ["easy", "medium"] },
  { assetId: "PROP_BREAD_KNIFE_01", displayName: "serrated bread knife", difficultyTags: ["easy", "medium"] },
  { assetId: "PROP_LETTER_OPENER_01", displayName: "antique letter opener", difficultyTags: ["easy", "medium"] },
  { assetId: "PROP_SCISSORS_01", displayName: "heavy scissors", difficultyTags: ["easy", "medium"] },
  { assetId: "PROP_HAMMER_01", displayName: "claw hammer", difficultyTags: ["easy", "medium"] },
  { assetId: "PROP_CANDLESTICK_01", displayName: "brass candlestick", difficultyTags: ["easy", "medium"] },
  { assetId: "PROP_VASE_01", displayName: "heavy ceramic vase", difficultyTags: ["easy", "medium"] },
  { assetId: "PROP_GLASS_BOTTLE_01", displayName: "glass bottle", difficultyTags: ["easy", "medium"] },
  { assetId: "PROP_SCREWDRIVER_01", displayName: "screwdriver", difficultyTags: ["easy", "medium"] },
  { assetId: "PROP_WRENCH_01", displayName: "adjustable wrench", difficultyTags: ["easy", "medium"] },
  // --- medium ---
  { assetId: "PROP_BASEBALL_BAT_01", displayName: "baseball bat", difficultyTags: ["medium"] },
  { assetId: "PROP_LAMP_01", displayName: "heavy table lamp", difficultyTags: ["medium"] },
  { assetId: "PROP_DESK_LAMP_01", displayName: "metal desk lamp", difficultyTags: ["medium"] },
  { assetId: "PROP_TABLE_LAMP_01", displayName: "table lamp", difficultyTags: ["medium"] },
  { assetId: "PROP_PLANT_POT_01", displayName: "ceramic plant pot", difficultyTags: ["medium"] },
  { assetId: "PROP_CLOCK_01", displayName: "wall clock", difficultyTags: ["medium"] },
  { assetId: "PROP_BOOK_01", displayName: "heavy hardcover book", difficultyTags: ["medium"] },
  { assetId: "PROP_PLATE_01", displayName: "ceramic plate", difficultyTags: ["medium"] },
  { assetId: "PROP_CUP_01", displayName: "ceramic cup", difficultyTags: ["medium"] },
  { assetId: "PROP_PEN_01", displayName: "metal pen", difficultyTags: ["medium"] },
  { assetId: "PROP_WATER_BOTTLE_01", displayName: "water bottle", difficultyTags: ["medium"] },
  // --- hard (unusual) ---
  { assetId: "PROP_ROPE_01", displayName: "length of cord", difficultyTags: ["hard"] },
  { assetId: "PROP_CRATE_01", displayName: "wooden crate", difficultyTags: ["hard"] },
  { assetId: "PROP_TRASH_BIN_01", displayName: "metal trash bin", difficultyTags: ["hard"] },
  { assetId: "PROP_OFFICE_CHAIR_01", displayName: "office chair", difficultyTags: ["hard"] },
  { assetId: "PROP_DESK_CHAIR_01", displayName: "desk chair", difficultyTags: ["hard"] },
  { assetId: "PROP_CHAIR_01", displayName: "wooden chair", difficultyTags: ["hard"] },
  { assetId: "PROP_ARMCHAIR_01", displayName: "armchair", difficultyTags: ["hard"] },
  { assetId: "PROP_BEDSIDE_TABLE_01", displayName: "nightstand", difficultyTags: ["hard"] },
  { assetId: "PROP_COFFEE_TABLE_01", displayName: "coffee table", difficultyTags: ["hard"] },
  { assetId: "PROP_STORAGE_BOX_01", displayName: "storage box", difficultyTags: ["hard"] },
  { assetId: "PROP_JEWELRY_BOX_01", displayName: "jewelry box", difficultyTags: ["hard"] },
]);

/**
 * EXACTLY 16 curated locations with difficulty tags. Availability policy:
 *   easy   -> tags include "easy"
 *   medium -> tags include "easy" or "medium"
 *   hard   -> all 16
 *
 * Phase 48 F1 — every name is aligned to one of the five supported environment
 * kits (apartment / office / hotel_suite / warehouse / mansion) and resolves
 * UNAMBIGUOUSLY via `backend/app/environments/resolver.py::resolve_environment`:
 * each name carries exactly ONE kit token and never the standalone word
 * `room` (the hotel_suite alias `room` collides otherwise). The unit tests
 * lock both invariants.
 */
export const LOCATIONS: readonly CuratedLocation[] = Object.freeze([
  // --- easy (also available at medium) ---
  { name: "apartment", difficultyTags: ["easy", "medium"] },
  { name: "apartment kitchen", difficultyTags: ["easy", "medium"] },
  { name: "apartment lounge", difficultyTags: ["easy", "medium"] },
  { name: "apartment bedroom", difficultyTags: ["easy", "medium"] },
  { name: "private office", difficultyTags: ["easy", "medium"] },
  { name: "hotel suite", difficultyTags: ["easy", "medium"] },
  { name: "warehouse", difficultyTags: ["easy", "medium"] },
  // --- medium ---
  { name: "office boardroom", difficultyTags: ["medium"] },
  { name: "office archive", difficultyTags: ["medium"] },
  { name: "office reception", difficultyTags: ["medium"] },
  { name: "hotel corridor", difficultyTags: ["medium"] },
  { name: "hotel restaurant kitchen", difficultyTags: ["medium"] },
  { name: "warehouse loading bay", difficultyTags: ["medium"] },
  // --- hard ---
  { name: "hotel ballroom", difficultyTags: ["hard"] },
  { name: "mansion study", difficultyTags: ["hard"] },
  { name: "mansion library", difficultyTags: ["hard"] },
]);

/* ======================================================================
 * Derived counts (single source of truth for the tests / UI)
 * ==================================================================== */

/** Total curated first names across both gender pools. */
export const FIRST_NAME_COUNT: number =
  FIRST_NAMES_BY_GENDER.male.length + FIRST_NAMES_BY_GENDER.female.length;

/** Total curated last names. */
export const LAST_NAME_COUNT: number = LAST_NAMES.length;

/** Total curated occupations. */
export const OCCUPATION_COUNT: number = OCCUPATIONS.length;

/** Total curated motives (exactly 32). */
export const MOTIVE_COUNT: number = MOTIVES.length;

/** Total curated weapons (exactly 32). */
export const WEAPON_COUNT: number = WEAPONS.length;

/** Total curated locations (exactly 16). */
export const LOCATION_COUNT: number = LOCATIONS.length;
