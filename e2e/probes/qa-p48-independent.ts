/**
 * PHASE 48 — INDEPENDENT QA verification probe (QA-owned; NOT the developer's test).
 *
 * Imports the REAL product modules and independently recomputes every claim in
 * Phase48.md §9/§10/§11/§12/§13/§21/§22/§29 with QA-written logic. Emits a JSON
 * report. Run via esbuild bundle + node (see screenshots/evidence/phase48/).
 */
import {
  generateRandomCrime,
  formatRandomCrimePrompt,
  makeRandomCrimeSeed,
  mulberry32,
  eligibleWeapons,
  eligibleLocations,
  PERSON_DRAW_RETRY_CAP,
  VICTIM_AGE_MIN,
  VICTIM_AGE_MAX,
  CRIME_TIME_START_HOUR,
  CRIME_TIME_WINDOW_MINUTES,
} from "../../frontend/src/journey/randomCrimeGenerator";
import {
  MOTIVES,
  WEAPONS,
  LOCATIONS,
  FIRST_NAMES_BY_GENDER,
  LAST_NAMES,
  OCCUPATIONS,
  CRIME_GENDERS,
} from "../../frontend/src/journey/randomCrimeData";
import { getAsset, hasAsset, catalogAssetIds } from "../../frontend/src/catalog/assetCatalog";

type Difficulty = "easy" | "medium" | "hard";
const DIFFICULTIES: Difficulty[] = ["easy", "medium", "hard"];

const failures: string[] = [];
function check(name: string, ok: boolean, detail?: unknown): void {
  if (!ok) failures.push(`${name}${detail === undefined ? "" : ` :: ${JSON.stringify(detail)}`}`);
}

const report: Record<string, unknown> = {};

/* ------------------------------------------------------------------ counts */
report.counts = {
  motives: MOTIVES.length,
  motivesUnique: new Set(MOTIVES).size,
  weapons: WEAPONS.length,
  weaponsUniqueAssetIds: new Set(WEAPONS.map((w) => w.assetId)).size,
  locations: LOCATIONS.length,
  locationsUniqueNames: new Set(LOCATIONS.map((l) => l.name)).size,
  firstNamesMale: FIRST_NAMES_BY_GENDER.male.length,
  firstNamesFemale: FIRST_NAMES_BY_GENDER.female.length,
  firstNamesTotal: FIRST_NAMES_BY_GENDER.male.length + FIRST_NAMES_BY_GENDER.female.length,
  lastNames: LAST_NAMES.length,
  occupations: OCCUPATIONS.length,
};
check("MOTIVES===32", MOTIVES.length === 32, MOTIVES.length);
check("MOTIVES unique", new Set(MOTIVES).size === 32);
check("MOTIVES all non-empty", MOTIVES.every((m) => m.trim().length > 0));
check("WEAPONS===32", WEAPONS.length === 32, WEAPONS.length);
check("WEAPONS unique assetIds", new Set(WEAPONS.map((w) => w.assetId)).size === 32);
check("LOCATIONS===16", LOCATIONS.length === 16, LOCATIONS.length);
check("LOCATIONS unique", new Set(LOCATIONS.map((l) => l.name)).size === 16);
check("firstNames>=64", FIRST_NAMES_BY_GENDER.male.length + FIRST_NAMES_BY_GENDER.female.length >= 64);
check("lastNames>=64", LAST_NAMES.length >= 64);
check("occupations>=32", OCCUPATIONS.length >= 32);
check(
  "no duplicate first names across gender pools",
  new Set([...FIRST_NAMES_BY_GENDER.male, ...FIRST_NAMES_BY_GENDER.female]).size ===
    FIRST_NAMES_BY_GENDER.male.length + FIRST_NAMES_BY_GENDER.female.length,
);

/* -------------------------------------------------- catalog resolution 32/32 */
const catalogIds = new Set(catalogAssetIds());
const catalogChecks = WEAPONS.map((w) => {
  const desc = getAsset(w.assetId);
  return {
    assetId: w.assetId,
    displayName: w.displayName,
    hasAsset: hasAsset(w.assetId),
    inRawCatalog: catalogIds.has(w.assetId),
    catalogLabel: desc?.label ?? null,
    catalogCategory: desc?.category ?? null,
    catalogSubtype: desc?.subtype ?? null,
  };
});
const unresolved = catalogChecks.filter((c) => !c.hasAsset || !c.inRawCatalog);
report.catalog = {
  weaponsResolved: catalogChecks.length - unresolved.length,
  weaponsTotal: catalogChecks.length,
  unresolved,
  entries: catalogChecks,
};
check("every weapon resolves to a catalog entry", unresolved.length === 0, unresolved);

/* --------------------------------------------------------- difficulty policy */
const hardOnly = WEAPONS.filter(
  (w) => w.difficultyTags.length === 1 && w.difficultyTags[0] === "hard",
);
report.policy = {
  easyPool: eligibleWeapons("easy").length,
  mediumPool: eligibleWeapons("medium").length,
  hardPool: eligibleWeapons("hard").length,
  easyLocations: eligibleLocations("easy").length,
  mediumLocations: eligibleLocations("medium").length,
  hardLocations: eligibleLocations("hard").length,
  hardOnlyWeaponIds: hardOnly.map((w) => w.assetId),
};
check("hard pool includes all 32", eligibleWeapons("hard").length === 32);
check(
  "easy pool < medium pool < hard pool",
  eligibleWeapons("easy").length < eligibleWeapons("medium").length &&
    eligibleWeapons("medium").length < eligibleWeapons("hard").length,
);

/* ------------------------------------------------------- seeded sweeps */
const sweepSeeds: number[] = [];
{
  const g = mulberry32(0xc0ffee);
  for (let i = 0; i < 5000; i += 1) sweepSeeds.push(Math.floor(g() * 0x100000000) >>> 0);
}

const easyViolations: Array<{ seed: number; assetId: string }> = [];
const hardOnlySelected = new Set<string>();
let ageMin = Infinity;
let ageMax = -Infinity;
const timeViolations: string[] = [];
const genderViolations: string[] = [];
const occupationViolations: string[] = [];
const nameGenderMismatch: string[] = [];
const distinctPersonViolations: number[] = [];
const lineViolations: number[] = [];
const promptSamples: Record<string, string[]> = { easy: [], medium: [], hard: [] };
let maxPromptLen = 0;

const easyIdSet = new Set(eligibleWeapons("easy").map((w) => w.assetId));
const hardOnlyIdSet = new Set(hardOnly.map((w) => w.assetId));

for (const seed of sweepSeeds) {
  for (const difficulty of DIFFICULTIES) {
    const crime = generateRandomCrime(seed, difficulty);
    const prompt = formatRandomCrimePrompt(crime);

    const names = [crime.victim.fullName, crime.murderer.fullName, crime.witness.fullName];
    if (new Set(names).size !== 3) distinctPersonViolations.push(seed);

    if (!CRIME_GENDERS.includes(crime.victim.gender)) genderViolations.push(String(crime.victim.gender));
    const pool = FIRST_NAMES_BY_GENDER[crime.victim.gender as "male" | "female"];
    if (!pool || !pool.includes(crime.victim.firstName)) nameGenderMismatch.push(crime.victim.fullName);
    if (!OCCUPATIONS.includes(crime.victim.occupation)) occupationViolations.push(crime.victim.occupation);
    ageMin = Math.min(ageMin, crime.victim.age);
    ageMax = Math.max(ageMax, crime.victim.age);

    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(crime.time)) timeViolations.push(crime.time);
    const [hh, mm] = crime.time.split(":").map(Number);
    const mins = hh * 60 + mm;
    const start = CRIME_TIME_START_HOUR * 60;
    const end = (CRIME_TIME_START_HOUR * 60 + CRIME_TIME_WINDOW_MINUTES - 1) % (24 * 60);
    if (!(mins >= start || mins <= end)) timeViolations.push(crime.time);

    if (difficulty === "easy" && !easyIdSet.has(crime.weapon.assetId)) {
      easyViolations.push({ seed, assetId: crime.weapon.assetId });
    }
    if (difficulty === "hard" && hardOnlyIdSet.has(crime.weapon.assetId)) {
      hardOnlySelected.add(crime.weapon.assetId);
    }

    const lines = prompt.split("\n");
    if (lines.length !== 7 || lines.some((l) => l.trim().length === 0)) lineViolations.push(seed);
    maxPromptLen = Math.max(maxPromptLen, prompt.length);

    if (promptSamples[difficulty].length < 2 && promptSamples[difficulty].length < 2 && seed % 97 === 0) {
      promptSamples[difficulty].push(`seed=${seed}\n${prompt}`);
    }
  }
}

report.sweep = {
  seeds: sweepSeeds.length,
  generations: sweepSeeds.length * DIFFICULTIES.length,
  easyHardOnlyViolations: easyViolations.length,
  easyViolationSample: easyViolations.slice(0, 5),
  hardOnlyUnusualWeaponsSelected: [...hardOnlySelected],
  ageMin,
  ageMax,
  timeViolations: timeViolations.length,
  timeViolationSample: timeViolations.slice(0, 5),
  genderViolations: genderViolations.length,
  occupationViolations: occupationViolations.length,
  nameGenderMismatch: nameGenderMismatch.length,
  distinctPersonViolations: distinctPersonViolations.length,
  lineViolations: lineViolations.length,
  maxPromptLen,
  promptSamples,
};
check("easy never selects a hard-only weapon", easyViolations.length === 0, easyViolations.slice(0, 5));
check("hard selects at least one unusual (hard-only) weapon", hardOnlySelected.size > 0);
check("age within [18,85]", ageMin >= VICTIM_AGE_MIN && ageMax <= VICTIM_AGE_MAX, { ageMin, ageMax });
check("all times canonical HH:MM within 18:00-02:59", timeViolations.length === 0, timeViolations.slice(0, 5));
check("gender always drawn", genderViolations.length === 0);
check("occupation always curated", occupationViolations.length === 0);
check("first name always matches gender pool", nameGenderMismatch.length === 0);
check("victim/murderer/witness always distinct", distinctPersonViolations.length === 0, distinctPersonViolations.slice(0, 5));
check("always exactly 7 non-empty lines", lineViolations.length === 0, lineViolations.slice(0, 5));

/* ------------------------------------------------------------ determinism */
const detSeed = 12345;
const a1 = generateRandomCrime(detSeed, "hard");
const a2 = generateRandomCrime(detSeed, "hard");
const pa = formatRandomCrimePrompt(a1);
const pb = formatRandomCrimePrompt(a2);
const distinctOutputs = new Set(
  [1, 2, 3, 7, 42, 999, 12345, 999999].map((s) =>
    formatRandomCrimePrompt(generateRandomCrime(s, "hard")),
  ),
);
report.determinism = {
  seed12345_equalStructured: JSON.stringify(a1) === JSON.stringify(a2),
  seed12345_equalPrompt: pa === pb,
  seed12345_sample: pa,
  distinctOutputsFor8Seeds: distinctOutputs.size,
  kindsOfPersonsDistinct: new Set([a1.victim.fullName, a1.murderer.fullName, a1.witness.fullName]).size,
};
check("same seed+difficulty byte-identical", pa === pb);
check("8 distinct seeds -> 8 distinct prompts", distinctOutputs.size === 8, distinctOutputs.size);

/* ---------------------------------------------------- seed normalization */
const mx = mulberry32(7);
const my = mulberry32(7);
let mulberryOk = true;
for (let i = 0; i < 100; i += 1) if (mx() !== my()) mulberryOk = false;
report.seedNormalization = {
  mulberry32Deterministic: mulberryOk,
  makeRandomCrimeSeed_0: makeRandomCrimeSeed(() => 0),
  makeRandomCrimeSeed_half: makeRandomCrimeSeed(() => 0.5),
  makeRandomCrimeSeed_one: makeRandomCrimeSeed(() => 1),
  retryCap: PERSON_DRAW_RETRY_CAP,
};

/* ------------------------------------------------------------------ output */
report.failures = failures;
report.result = failures.length === 0 ? "PASS" : "FAIL";
process.stdout.write(JSON.stringify(report, null, 2) + "\n");
if (failures.length > 0) process.exitCode = 1;
