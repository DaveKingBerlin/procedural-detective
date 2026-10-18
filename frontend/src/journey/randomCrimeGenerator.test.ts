import { describe, expect, it } from "vitest";
import { getAsset, hasAsset } from "../catalog/assetCatalog";
import type { JourneyDifficulty } from "./context";
import { PROMPT_MAX_CHARS } from "./demoPrompt";
import {
  FIRST_NAMES_BY_GENDER,
  LAST_NAMES,
  LOCATION_COUNT,
  LOCATIONS,
  MOTIVE_COUNT,
  MOTIVES,
  OCCUPATION_COUNT,
  OCCUPATIONS,
  WEAPON_COUNT,
  WEAPONS,
  type CuratedWeapon,
} from "./randomCrimeData";
import {
  CRIME_TIME_START_HOUR,
  CRIME_TIME_WINDOW_MINUTES,
  VICTIM_AGE_MAX,
  VICTIM_AGE_MIN,
  eligibleWeapons,
  formatRandomCrimePrompt,
  generateRandomCrime,
  makeRandomCrimeSeed,
  mulberry32,
} from "./randomCrimeGenerator";

/**
 * Phase 48 — pure node unit tests for the deterministic random-crime seed.
 *
 * Everything here is seed-driven and deterministic: no `Math.random`, no DOM,
 * no network. The generator's structured output and its formatted prompt are
 * both asserted, since string formatting is NOT the canonical model.
 */

const DIFFICULTIES: readonly JourneyDifficulty[] = ["easy", "medium", "hard"];

/** A spread of deterministic seeds for the "many seeds" sweeps. */
const SEEDS: readonly number[] = [
  0, 1, 2, 3, 7, 42, 123, 999, 12345, 54321, 999999, 0xdeadbeef, 0xffffffff,
];

function minutesOf(time: string): number {
  const [hh, mm] = time.split(":");
  return Number(hh) * 60 + Number(mm);
}

describe("determinism — same seed + difficulty is byte-identical", () => {
  it("seed 12345 + Hard generates an identical structured object AND prompt", () => {
    const first = generateRandomCrime(12345, "hard");
    const second = generateRandomCrime(12345, "hard");
    expect(second).toEqual(first);
    expect(formatRandomCrimePrompt(second)).toBe(formatRandomCrimePrompt(first));
  });

  it("the seed is normalized with >>> 0 (equal seeds modulo 2^32 agree)", () => {
    const a = generateRandomCrime(12345, "medium");
    const b = generateRandomCrime(12345 >>> 0, "medium");
    const c = generateRandomCrime(12345 + 0x100000000, "medium");
    expect(b).toEqual(a);
    expect(c).toEqual(a);
  });

  it("the mulberry32 stream itself is deterministic", () => {
    const a = mulberry32(7);
    const b = mulberry32(7);
    for (let i = 0; i < 50; i += 1) {
      expect(a()).toBe(b());
    }
  });
});

describe("different seeds produce different prompts", () => {
  it("pairwise distinct prompts for seeds 1, 2, 12345, 999999", () => {
    const seeds = [1, 2, 12345, 999999];
    const prompts = seeds.map((seed) => formatRandomCrimePrompt(generateRandomCrime(seed, "hard")));
    for (let i = 0; i < prompts.length; i += 1) {
      for (let j = i + 1; j < prompts.length; j += 1) {
        expect(prompts[i]).not.toBe(prompts[j]);
      }
    }
  });
});

describe("persons are pairwise distinct", () => {
  it("victim, murderer and witness never share a full name across seeds/difficulties", () => {
    for (const difficulty of DIFFICULTIES) {
      for (const seed of SEEDS) {
        const crime = generateRandomCrime(seed, difficulty);
        const names = [crime.victim.fullName, crime.murderer.fullName, crime.witness.fullName];
        expect(new Set(names).size).toBe(3);
      }
    }
  });
});

describe("no person shares a first and last name (Phase 48 F4)", () => {
  it("every generated person's first name differs from their last name", () => {
    for (const difficulty of DIFFICULTIES) {
      for (let seed = 0; seed < 10000; seed += 1) {
        const crime = generateRandomCrime(seed, difficulty);
        for (const person of [crime.victim, crime.murderer, crime.witness]) {
          expect(person.firstName).not.toBe(person.lastName);
          expect(person.fullName).toBe(`${person.firstName} ${person.lastName}`);
        }
      }
    }
  });
});

describe("curated data counts and integrity", () => {
  it("has exactly 32 motives, each non-empty and unique", () => {
    expect(MOTIVES).toHaveLength(32);
    expect(MOTIVE_COUNT).toBe(32);
    for (const motive of MOTIVES) {
      expect(motive.trim().length).toBeGreaterThan(0);
    }
    expect(new Set(MOTIVES).size).toBe(32);
  });

  it("has exactly 32 weapons with resolvable catalog ids", () => {
    expect(WEAPONS).toHaveLength(32);
    expect(WEAPON_COUNT).toBe(32);
    const ids = new Set<string>();
    for (const weapon of WEAPONS) {
      expect(weapon.displayName.trim().length).toBeGreaterThan(0);
      expect(weapon.assetId).toMatch(/^[A-Z][A-Z0-9_]+$/);
      expect(hasAsset(weapon.assetId)).toBe(true);
      expect(getAsset(weapon.assetId)).toBeDefined();
      ids.add(weapon.assetId);
    }
    expect(ids.size).toBe(32);
  });

  it("meets the curated minimum counts", () => {
    const firstNameTotal =
      FIRST_NAMES_BY_GENDER.male.length + FIRST_NAMES_BY_GENDER.female.length;
    expect(firstNameTotal).toBeGreaterThanOrEqual(64);
    expect(LAST_NAMES.length).toBeGreaterThanOrEqual(64);
    expect(OCCUPATIONS.length).toBeGreaterThanOrEqual(32);
    expect(OCCUPATION_COUNT).toBe(OCCUPATIONS.length);
    expect(LOCATIONS).toHaveLength(16);
    expect(LOCATION_COUNT).toBe(16);
    // Every pool entry is non-empty.
    for (const name of [...FIRST_NAMES_BY_GENDER.male, ...FIRST_NAMES_BY_GENDER.female]) {
      expect(name.trim().length).toBeGreaterThan(0);
    }
    for (const location of LOCATIONS) {
      expect(location.name.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("curated locations are kit-aligned and unambiguous (Phase 48 F1)", () => {
  /** The five supported environment-kit tokens. */
  const KIT_TOKENS: readonly string[] = ["apartment", "office", "hotel", "warehouse", "mansion"];

  it("has exactly 16 unique, non-empty, difficulty-tagged names", () => {
    expect(LOCATIONS).toHaveLength(16);
    expect(LOCATION_COUNT).toBe(16);
    expect(new Set(LOCATIONS.map((location) => location.name)).size).toBe(16);
    for (const location of LOCATIONS) {
      expect(location.name.trim().length).toBeGreaterThan(0);
      expect(location.difficultyTags.length).toBeGreaterThan(0);
    }
  });

  it("no location name contains the standalone word 'room'", () => {
    for (const location of LOCATIONS) {
      const tokens = location.name.toLowerCase().split(/\s+/);
      expect(tokens).not.toContain("room");
    }
  });

  it("each name carries EXACTLY ONE supported kit token (never two)", () => {
    for (const location of LOCATIONS) {
      const tokens = location.name.toLowerCase().split(/\s+/);
      const kitTokens = tokens.filter((token) => KIT_TOKENS.includes(token));
      expect(kitTokens).toHaveLength(1);
    }
  });
});

describe("difficulty weapon policy", () => {
  it("easy selects only easy-tagged weapons", () => {
    const easyIds = new Set(
      WEAPONS.filter((weapon) => weapon.difficultyTags.includes("easy")).map((w) => w.assetId),
    );
    expect(easyIds.size).toBeGreaterThan(0);
    for (let seed = 0; seed < 500; seed += 1) {
      const crime = generateRandomCrime(seed, "easy");
      expect(easyIds.has(crime.weapon.assetId)).toBe(true);
    }
  });

  it("hard can select a hard-tagged (unusual) weapon", () => {
    const hardIds = new Set(
      WEAPONS.filter((weapon) => weapon.difficultyTags.includes("hard")).map((w) => w.assetId),
    );
    expect(hardIds.size).toBeGreaterThan(0);
    const selected = new Set<string>();
    for (let seed = 0; seed < 200; seed += 1) {
      selected.add(generateRandomCrime(seed, "hard").weapon.assetId);
    }
    let sawHard = false;
    for (const id of selected) {
      if (hardIds.has(id)) sawHard = true;
    }
    expect(sawHard).toBe(true);
  });

  it("eligibleWeapons matches the documented policy shape", () => {
    expect(eligibleWeapons("hard")).toHaveLength(32);
    for (const weapon of eligibleWeapons("easy")) {
      expect(weapon.difficultyTags).toContain("easy");
    }
    for (const weapon of eligibleWeapons("medium")) {
      expect(
        weapon.difficultyTags.includes("easy") || weapon.difficultyTags.includes("medium"),
      ).toBe(true);
    }
    expect(eligibleWeapons("easy").length).toBeLessThan(eligibleWeapons("medium").length);
    expect(eligibleWeapons("medium").length).toBeLessThan(eligibleWeapons("hard").length);
  });
});

describe("time formatting and window", () => {
  it("is always HH:MM and inside 18:00–02:59", () => {
    for (const difficulty of DIFFICULTIES) {
      for (const seed of SEEDS) {
        const { time } = generateRandomCrime(seed, difficulty);
        expect(time).toMatch(/^([01]\d|2[0-3]):[0-5]\d$/);
        const minutes = minutesOf(time);
        const start = CRIME_TIME_START_HOUR * 60;
        const end = (CRIME_TIME_START_HOUR * 60 + CRIME_TIME_WINDOW_MINUTES - 1) % (24 * 60);
        expect(minutes >= start || minutes <= end).toBe(true);
      }
    }
  });
});

describe("prompt shape", () => {
  it("is exactly 7 non-empty lines with the stable labels", () => {
    for (const difficulty of DIFFICULTIES) {
      for (const seed of SEEDS) {
        const prompt = formatRandomCrimePrompt(generateRandomCrime(seed, difficulty));
        const lines = prompt.split("\n");
        expect(lines).toHaveLength(7);
        for (const line of lines) {
          expect(line.trim().length).toBeGreaterThan(0);
        }
        expect(lines[0]).toMatch(/^Victim: .+ \((Male|Female|Other|Unknown), \d{1,3}, .+\)$/);
        expect(lines[1]).toMatch(/^Murderer: .+$/);
        expect(lines[2]).toMatch(/^Motive: .+$/);
        expect(lines[3]).toMatch(/^Weapon: .+$/);
        expect(lines[4]).toMatch(/^Time: \d{2}:\d{2}$/);
        expect(lines[5]).toMatch(/^Witness: .+$/);
        expect(lines[6]).toMatch(/^Location: .+$/);
      }
    }
  });

  it("stays well under the prompt length bound", () => {
    for (const difficulty of DIFFICULTIES) {
      for (const seed of SEEDS) {
        const prompt = formatRandomCrimePrompt(generateRandomCrime(seed, difficulty));
        expect(prompt.length).toBeLessThan(PROMPT_MAX_CHARS);
      }
    }
  });
});

describe("victim profile", () => {
  it("gender is in the canonical vocab and the first name comes from the matching pool", () => {
    for (const difficulty of DIFFICULTIES) {
      for (const seed of SEEDS) {
        const { victim } = generateRandomCrime(seed, difficulty);
        expect(["male", "female", "other", "unknown"]).toContain(victim.gender);
        expect(FIRST_NAMES_BY_GENDER[victim.gender as "male" | "female"]).toContain(victim.firstName);
        expect(victim.fullName).toBe(`${victim.firstName} ${victim.lastName}`);
      }
    }
  });

  it("age is an integer in 18–85 and the occupation is curated", () => {
    for (const difficulty of DIFFICULTIES) {
      for (const seed of SEEDS) {
        const { victim } = generateRandomCrime(seed, difficulty);
        expect(Number.isInteger(victim.age)).toBe(true);
        expect(victim.age).toBeGreaterThanOrEqual(VICTIM_AGE_MIN);
        expect(victim.age).toBeLessThanOrEqual(VICTIM_AGE_MAX);
        expect(OCCUPATIONS).toContain(victim.occupation);
      }
    }
  });

  it("the selected location is one of the curated names", () => {
    const names = new Set(LOCATIONS.map((location) => location.name));
    for (const difficulty of DIFFICULTIES) {
      for (const seed of SEEDS) {
        expect(names.has(generateRandomCrime(seed, difficulty).location)).toBe(true);
      }
    }
  });
});

describe("makeRandomCrimeSeed — the only Math.random surface", () => {
  it("maps injected rng values to deterministic unsigned 32-bit seeds", () => {
    expect(makeRandomCrimeSeed(() => 0)).toBe(0);
    expect(makeRandomCrimeSeed(() => 0.5)).toBe(0x80000000);
    expect(makeRandomCrimeSeed(() => 0.999999999999)).toBe(0xffffffff);
    // Out-of-range rng stays a valid unsigned int (defensive >>> 0).
    expect(makeRandomCrimeSeed(() => 1)).toBe(0);
  });

  it("produces an integer seed that drives a valid crime", () => {
    const seed = makeRandomCrimeSeed(() => 0.42);
    expect(Number.isInteger(seed)).toBe(true);
    const crime = generateRandomCrime(seed, "medium");
    expect(formatRandomCrimePrompt(crime).split("\n")).toHaveLength(7);
  });
});

describe("curated weapon shape", () => {
  it("every weapon carries a non-empty displayName and at least one difficulty tag", () => {
    const weapons: readonly CuratedWeapon[] = WEAPONS;
    for (const weapon of weapons) {
      expect(weapon.displayName.length).toBeGreaterThan(0);
      expect(weapon.difficultyTags.length).toBeGreaterThan(0);
    }
  });
});
