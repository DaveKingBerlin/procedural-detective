/**
 * PHASE 48 QA RETEST — independent frontend generator probe (QA-owned; NOT the
 * developer's test). Imports the REAL product modules and recomputes the
 * Phase-48 acceptance invariants with QA-written logic, including the NEW
 * F4 (no first==last name) and F1 (16 kit-aligned locations) checks.
 *
 * Run from frontend/ with the QA vitest config:
 *   npx vitest run --config ../e2e/probes/qa-p48-retest.vitest.config.mjs
 */
import {
  generateRandomCrime,
  formatRandomCrimePrompt,
  mulberry32,
  eligibleWeapons,
  eligibleLocations,
} from "../../frontend/src/journey/randomCrimeGenerator";
import {
  MOTIVES,
  WEAPONS,
  LOCATIONS,
  FIRST_NAMES_BY_GENDER,
  LAST_NAMES,
  OCCUPATIONS,
} from "../../frontend/src/journey/randomCrimeData";
import { getAsset, hasAsset, catalogAssetIds } from "../../frontend/src/catalog/assetCatalog";
import { expect, it } from "vitest";

type Difficulty = "easy" | "medium" | "hard";
const DIFFICULTIES: Difficulty[] = ["easy", "medium", "hard"];

/** The 16 names the backend retest probe used — the two MUST agree. */
const EXPECTED_LOCATIONS = [
  "apartment",
  "apartment kitchen",
  "apartment lounge",
  "apartment bedroom",
  "private office",
  "hotel suite",
  "warehouse",
  "office boardroom",
  "office archive",
  "office reception",
  "hotel corridor",
  "hotel restaurant kitchen",
  "warehouse loading bay",
  "hotel ballroom",
  "mansion study",
  "mansion library",
];

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
};
check("MOTIVES===32", MOTIVES.length === 32, MOTIVES.length);
check("WEAPONS===32", WEAPONS.length === 32, WEAPONS.length);
check("LOCATIONS===16", LOCATIONS.length === 16, LOCATIONS.length);

/* ---------------------------------------------- F1: exact 16 name agreement */
const actualNames = LOCATIONS.map((l) => l.name);
report.locations = {
  actual: actualNames,
  expected: EXPECTED_LOCATIONS,
  exactSetMatch:
    actualNames.length === EXPECTED_LOCATIONS.length &&
    [...actualNames].sort().join("|") === [...EXPECTED_LOCATIONS].sort().join("|"),
  tags: LOCATIONS.map((l) => ({ name: l.name, difficultyTags: l.difficultyTags })),
};
check("locations exactly match the backend probe list", report.locations.exactSetMatch as boolean);

const KIT_TOKENS = ["apartment", "office", "hotel", "warehouse", "mansion"];
const kitTokenViolations = LOCATIONS.filter((l) => {
  const tokens = l.name.toLowerCase().split(/\s+/);
  return tokens.filter((t) => KIT_TOKENS.includes(t)).length !== 1;
}).map((l) => l.name);
const roomViolations = LOCATIONS.filter((l) => l.name.toLowerCase().split(/\s+/).includes("room")).map(
  (l) => l.name,
);
check("each location carries exactly one kit token", kitTokenViolations.length === 0, kitTokenViolations);
check("no location contains the standalone word 'room'", roomViolations.length === 0, roomViolations);

/* ----------------------------------------- F4: first != last across a big sweep */
const sweepSeedCount = 100000;
const sweepSeeds: number[] = [];
{
  const g = mulberry32(0x5eed1234);
  for (let i = 0; i < sweepSeedCount; i += 1) sweepSeeds.push(Math.floor(g() * 0x100000000) >>> 0);
}
let firstLastViolations: string[] = [];
let fullNameViolations: string[] = [];
let distinctPersonViolations = 0;
let lineViolations = 0;
let victimFormatViolations = 0;
let easyHardOnlyViolations = 0;
let hardOnlySelected = new Set<string>();
let sameSeedDeterminismViolations = 0;
let personCount = 0;

const easyIdSet = new Set(eligibleWeapons("easy").map((w) => w.assetId));
const hardOnlyIdSet = new Set(
  WEAPONS.filter((w) => w.difficultyTags.length === 1 && w.difficultyTags[0] === "hard").map((w) => w.assetId),
);

for (const seed of sweepSeeds) {
  for (const difficulty of DIFFICULTIES) {
    const crime = generateRandomCrime(seed, difficulty);
    const prompt = formatRandomCrimePrompt(crime);
    for (const person of [crime.victim, crime.murderer, crime.witness]) {
      personCount += 1;
      if (person.firstName === person.lastName) {
        if (firstLastViolations.length < 10) firstLastViolations.push(`${person.firstName} ${person.lastName}`);
      }
      if (person.fullName !== `${person.firstName} ${person.lastName}`) {
        if (fullNameViolations.length < 10) fullNameViolations.push(person.fullName);
      }
    }
    const names = [crime.victim.fullName, crime.murderer.fullName, crime.witness.fullName];
    if (new Set(names).size !== 3) distinctPersonViolations += 1;
    const lines = prompt.split("\n");
    if (lines.length !== 7 || lines.some((l) => l.trim().length === 0)) lineViolations += 1;
    if (!/^Victim: .+ \((Male|Female|Other|Unknown), \d{1,3}, .+\)$/.test(lines[0])) {
      victimFormatViolations += 1;
    }
    if (difficulty === "easy" && !easyIdSet.has(crime.weapon.assetId)) easyHardOnlyViolations += 1;
    if (difficulty === "hard" && hardOnlyIdSet.has(crime.weapon.assetId)) hardOnlySelected.add(crime.weapon.assetId);
    if (formatRandomCrimePrompt(generateRandomCrime(seed, difficulty)) !== prompt) {
      sameSeedDeterminismViolations += 1;
    }
  }
}

report.sweep = {
  seeds: sweepSeedCount,
  generations: sweepSeedCount * DIFFICULTIES.length,
  personsChecked: personCount,
  firstLastViolations: firstLastViolations.length,
  firstLastSample: firstLastViolations,
  fullNameViolations: fullNameViolations.length,
  distinctPersonViolations,
  lineViolations,
  victimFormatViolations,
  easyHardOnlyViolations,
  hardOnlyWeaponsSelectedOnHard: [...hardOnlySelected].length,
  sameSeedDeterminismViolations,
};
check("no person has firstName === lastName", firstLastViolations.length === 0, firstLastViolations);
check("fullName === 'first last'", fullNameViolations.length === 0, fullNameViolations);
check("victim/murderer/witness always distinct", distinctPersonViolations === 0);
check("always exactly 7 non-empty lines", lineViolations === 0);
check("victim parenthetical format", victimFormatViolations === 0);
check("easy never picks a hard-only weapon", easyHardOnlyViolations === 0);
check("hard reaches at least one hard-only weapon", hardOnlySelected.size > 0);
check("same seed+difficulty byte-identical", sameSeedDeterminismViolations === 0);

/* -------------------------------------------------- catalog resolution 32/32 */
const catalogIds = new Set(catalogAssetIds());
const unresolved = WEAPONS.filter((w) => !hasAsset(w.assetId) || !catalogIds.has(w.assetId));
report.catalog = {
  weaponsResolved: WEAPONS.length - unresolved.length,
  weaponsTotal: WEAPONS.length,
  unresolved: unresolved.map((w) => w.assetId),
  sample: WEAPONS.slice(0, 3).map((w) => ({ assetId: w.assetId, label: getAsset(w.assetId)?.label ?? null })),
};
check("every weapon assetId resolves", unresolved.length === 0, report.catalog);

/* --------------------------------------------------------- difficulty policy */
report.policy = {
  easyWeapons: eligibleWeapons("easy").length,
  mediumWeapons: eligibleWeapons("medium").length,
  hardWeapons: eligibleWeapons("hard").length,
  easyLocations: eligibleLocations("easy").length,
  mediumLocations: eligibleLocations("medium").length,
  hardLocations: eligibleLocations("hard").length,
  firstNamesMale: FIRST_NAMES_BY_GENDER.male.length,
  firstNamesFemale: FIRST_NAMES_BY_GENDER.female.length,
  lastNames: LAST_NAMES.length,
  occupations: OCCUPATIONS.length,
};
check("hard location pool === 16", eligibleLocations("hard").length === 16);

/* ------------------------------------------------------------ determinism */
const a = generateRandomCrime(12345, "hard");
const b = generateRandomCrime(12345, "hard");
report.determinism = {
  seed12345_equalStructured: JSON.stringify(a) === JSON.stringify(b),
  seed12345_equalPrompt: formatRandomCrimePrompt(a) === formatRandomCrimePrompt(b),
  seed12345_sample: formatRandomCrimePrompt(a),
};
check("seed 12345 structured identical", report.determinism.seed12345_equalStructured as boolean);
check("seed 12345 prompt identical", report.determinism.seed12345_equalPrompt as boolean);

report.failures = failures;
report.result = failures.length === 0 ? "PASS" : "FAIL";
process.stdout.write(JSON.stringify(report, null, 2) + "\n");

it("PHASE 48 QA independent generator probe passes", () => {
  expect(failures).toEqual([]);
  expect(report.result).toBe("PASS");
});
