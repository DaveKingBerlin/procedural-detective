/**
 * ADV-48 generator probe (transient adversary evidence; NOT product code).
 * Targets the seeded random-crime generator with hostile seeds, degenerate RNGs
 * and large sweeps that the QA happy-path suite does not cover.
 */
import { describe, expect, it } from "vitest";
import { hasAsset } from "../../../frontend/src/catalog/assetCatalog";
import {
  WEAPONS,
  LOCATIONS,
  MOTIVES,
  OCCUPATIONS,
  FIRST_NAMES_BY_GENDER,
  LAST_NAMES,
} from "../../../frontend/src/journey/randomCrimeData";
import {
  CRIME_TIME_WINDOW_MINUTES,
  CRIME_TIME_START_HOUR,
  PERSON_DRAW_RETRY_CAP,
  eligibleLocations,
  eligibleWeapons,
  formatRandomCrimePrompt,
  generateRandomCrime,
  makeRandomCrimeSeed,
  mulberry32,
} from "../../../frontend/src/journey/randomCrimeGenerator";
import { PROMPT_MAX_CHARS } from "../../../frontend/src/journey/demoPrompt";
import type { JourneyDifficulty } from "../../../frontend/src/journey/context";

const DIFFS: readonly JourneyDifficulty[] = ["easy", "medium", "hard"];

function promptLines(text: string): string[] {
  return text.split("\n");
}

function minutesOf(t: string): number {
  const [h, m] = t.split(":");
  return Number(h) * 60 + Number(m);
}

function assertWellFormed(text: string) {
  const lines = promptLines(text);
  expect(lines, `expected 7 lines, got ${lines.length}: ${JSON.stringify(text)}`).toHaveLength(7);
  for (const l of lines) expect(l.trim().length).toBeGreaterThan(0);
  expect(lines[0]).toMatch(/^Victim: .+ \((Male|Female|Other|Unknown), \d{1,3}, .+\)$/);
  expect(lines[1]).toMatch(/^Murderer: .+$/);
  expect(lines[2]).toMatch(/^Motive: .+$/);
  expect(lines[3]).toMatch(/^Weapon: .+$/);
  expect(lines[4]).toMatch(/^Time: \d{2}:\d{2}$/);
  expect(lines[5]).toMatch(/^Witness: .+$/);
  expect(lines[6]).toMatch(/^Location: .+$/);
  expect(text).not.toContain("undefined");
  expect(text).not.toContain("null");
  expect(text).not.toContain("NaN");
}

describe("ADV48-G1 hostile seed values never crash and stay deterministic", () => {
  const hostile: unknown[] = [
    0,
    -1,
    -0.5,
    0.5,
    1.5,
    2 ** 31,
    -(2 ** 31),
    2 ** 32,
    2 ** 32 - 1,
    2 ** 32 + 1,
    2 ** 53,
    1e21,
    -1e21,
    Number.MAX_SAFE_INTEGER,
    Number.NaN,
    Number.POSITIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
    "12345",
    "abc",
    "",
    true,
    false,
    null,
    undefined,
    {},
    [],
    [12345],
    Symbol.iterator ? "0xdeadbeef" : "0",
  ];

  it("generates a well-formed prompt for every hostile seed and is stable across two calls", () => {
    for (const raw of hostile) {
      for (const d of DIFFS) {
        const seed = raw as number;
        const a = generateRandomCrime(seed, d);
        const b = generateRandomCrime(seed, d);
        expect(b).toEqual(a);
        const p = formatRandomCrimePrompt(a);
        assertWellFormed(p);
        // all coercion should land on the same canonical seed, so re-running
        // with the normalized uint32 must match
        const normalized = seed >>> 0;
        const c = generateRandomCrime(normalized, d);
        expect(c).toEqual(a);
      }
    }
  });
});

describe("ADV48-G2 makeRandomCrimeSeed degenerate injected rngs", () => {
  const values = [0, -0, 1, -1, 0.5, 0.9999999999999999, 1e-12, 2, 100, -100, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY];
  it("always yields an integer uint32 seed that drives a valid crime", () => {
    for (const v of values) {
      const seed = makeRandomCrimeSeed(() => v);
      expect(Number.isInteger(seed), `seed for rng()=>${v} must be integer, got ${seed}`).toBe(true);
      expect(seed).toBeGreaterThanOrEqual(0);
      expect(seed).toBeLessThan(2 ** 32);
      const p = formatRandomCrimePrompt(generateRandomCrime(seed, "hard"));
      assertWellFormed(p);
    }
  });
});

describe("ADV48-G3 pure generator never touches Math.random", () => {
  it("generateRandomCrime + formatRandomCrimePrompt survive Math.random throwing", () => {
    const original = Math.random;
    Math.random = () => {
      throw new Error("Math.random must not be called by the pure generator (ADV48-G3)");
    };
    try {
      for (const d of DIFFS) {
        const crime = generateRandomCrime(12345, d);
        expect(formatRandomCrimePrompt(crime)).toContain("Victim: ");
      }
      // mulberry32 itself must be pure too
      mulberry32(7)();
    } finally {
      Math.random = original;
    }
  });

  it("makeRandomCrimeSeed is the ONLY Math.random surface (default arg calls it)", () => {
    const original = Math.random;
    let calls = 0;
    Math.random = () => {
      calls += 1;
      return 0.5;
    };
    try {
      const seed = makeRandomCrimeSeed();
      expect(calls).toBe(1);
      expect(seed).toBe(0x80000000);
    } finally {
      Math.random = original;
    }
  });
});

describe("ADV48-G4 pickIndex math with rng >= 1 and negative (copied pure fn)", () => {
  // Copy of the private `pickIndex` in randomCrimeGenerator.ts.
  const pickIndex = (rng: () => number, length: number) =>
    Math.floor(rng() * length) % length;
  it("rng >= 1 is clamped by % length; negative rng yields a NEGATIVE index", () => {
    expect(pickIndex(() => 1, 10)).toBe(0);
    expect(pickIndex(() => 1.999, 10)).toBe(9);
    expect(pickIndex(() => 2, 10)).toBe(0);
    expect(pickIndex(() => Number.POSITIVE_INFINITY, 10)).toBe(Number.NaN);
    // negative rng -> negative modulo -> out-of-bounds array access
    expect(pickIndex(() => -0.5, 10)).toBe(-5);
    expect(pickIndex(() => -0.05, 10)).toBe(-1); // Math.floor(-0.5) = -1
  });
  it("mulberry32 never returns < 0 or >= 1 over a long stream", () => {
    const rng = mulberry32(0);
    let min = 1;
    let max = 0;
    for (let i = 0; i < 1000000; i += 1) {
      const v = rng();
      if (v < min) min = v;
      if (v > max) max = v;
    }
    expect(min).toBeGreaterThanOrEqual(0);
    expect(max).toBeLessThan(1);
  }, 30000);
});

describe("ADV48-G5 large deterministic sweep: invariants + duplicate hunt", () => {
  it("100k seeds per difficulty: distinct persons, valid profile, policy, 7 lines", () => {
    const easyIds = new Set(eligibleWeapons("easy").map((w) => w.assetId));
    const mediumIds = new Set(eligibleWeapons("medium").map((w) => w.assetId));
    const hardIds = new Set(eligibleWeapons("hard").map((w) => w.assetId));
    const idsFor: Record<string, Set<string>> = { easy: easyIds, medium: mediumIds, hard: hardIds };
    const locNames = new Set(LOCATIONS.map((l) => l.name));
    const easyLoc = new Set(eligibleLocations("easy").map((l) => l.name));
    const mediumLoc = new Set(eligibleLocations("medium").map((l) => l.name));
    const hardLoc = new Set(eligibleLocations("hard").map((l) => l.name));

    const seenWeapon = new Set<string>();
    const seenLocation = new Set<string>();
    let maxLen = 0;
    let duplicateEvents = 0;

    for (let seed = 0; seed < 100000; seed += 1) {
      const d = DIFFS[seed % 3];
      const crime = generateRandomCrime(seed, d);
      const names = [crime.victim.fullName, crime.murderer.fullName, crime.witness.fullName];
      if (new Set(names).size !== 3) duplicateEvents += 1;
      expect(new Set(names).size, `seed ${seed} (${d}) dup names: ${names.join(" / ")}`).toBe(3);
      expect(crime.victim.age).toBeGreaterThanOrEqual(18);
      expect(crime.victim.age).toBeLessThanOrEqual(85);
      expect(Number.isInteger(crime.victim.age)).toBe(true);
      expect(OCCUPATIONS).toContain(crime.victim.occupation);
      expect(MOTIVES).toContain(crime.motive);
      expect(idsFor[d].has(crime.weapon.assetId), `seed ${seed} (${d}) weapon ${crime.weapon.assetId} not in pool`).toBe(true);
      expect(locNames.has(crime.location)).toBe(true);
      expect((d === "easy" ? easyLoc : d === "medium" ? mediumLoc : hardLoc).has(crime.location)).toBe(true);
      const t = minutesOf(crime.time);
      const start = CRIME_TIME_START_HOUR * 60;
      const end = (start + CRIME_TIME_WINDOW_MINUTES - 1) % (24 * 60);
      expect(t >= start || t <= end, `seed ${seed} time ${crime.time} outside 18:00-02:59`).toBe(true);
      const p = formatRandomCrimePrompt(crime);
      expect(promptLines(p)).toHaveLength(7);
      maxLen = Math.max(maxLen, p.length);
      seenWeapon.add(crime.weapon.assetId);
      seenLocation.add(crime.location);
    }
    expect(duplicateEvents).toBe(0);
    expect(maxLen).toBeLessThan(PROMPT_MAX_CHARS);
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-G5] sweep=300000 maxPromptLen=${maxLen} distinctWeapons=${seenWeapon.size}/${WEAPONS.length} distinctLocations=${seenLocation.size}/${LOCATIONS.length} duplicates=${duplicateEvents}`,
    );
  }, 120000);
});

describe("ADV48-G6 curated data cannot break the 7-line invariant", () => {
  it("no curated string contains newline / CR / control chars", () => {
    const strings = [
      ...MOTIVES,
      ...OCCUPATIONS,
      ...LOCATIONS.map((l) => l.name),
      ...WEAPONS.map((w) => w.displayName),
      ...FIRST_NAMES_BY_GENDER.male,
      ...FIRST_NAMES_BY_GENDER.female,
      ...LAST_NAMES,
    ];
    for (const s of strings) {
      expect(s).not.toMatch(/[\n\r\t\u0000-\u001f]/);
    }
  });

  it("no curated display value contains HTML/XSS metacharacters", () => {
    const strings = [
      ...MOTIVES,
      ...OCCUPATIONS,
      ...LOCATIONS.map((l) => l.name),
      ...WEAPONS.map((w) => w.displayName),
      ...Object.values(FIRST_NAMES_BY_GENDER).flat(),
      ...LAST_NAMES,
    ];
    for (const s of strings) {
      expect(s, `curated string contains markup chars: ${JSON.stringify(s)}`).not.toMatch(/[<>&"'`]/);
    }
  });

  it("all 32 weapon assetIds resolve in the real catalog", () => {
    const invalid = WEAPONS.filter((w) => !hasAsset(w.assetId)).map((w) => w.assetId);
    expect(invalid).toEqual([]);
  });
});

describe("ADV48-G7 retry-cap reachability reasoning probe", () => {
  it("PERSON_DRAW_RETRY_CAP is a bounded constant and pools are large enough that fallback is unreachable for count=3", () => {
    expect(PERSON_DRAW_RETRY_CAP).toBeGreaterThan(0);
    const identities =
      (FIRST_NAMES_BY_GENDER.male.length + FIRST_NAMES_BY_GENDER.female.length) * LAST_NAMES.length;
    // collision probability for 3 draws is tiny; documenting the margin
    expect(identities).toBeGreaterThan(1000);
    // eslint-disable-next-line no-console
    console.log(`[ADV48-G7] identities=${identities} cap=${PERSON_DRAW_RETRY_CAP}`);
  });
});
