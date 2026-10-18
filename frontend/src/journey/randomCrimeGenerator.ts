/**
 * Phase 48 — deterministic "Generate random case" crime-seed generator.
 *
 * Produces a STRUCTURED crime object from a seeded PRNG plus the selected
 * difficulty; {@link formatRandomCrimePrompt} turns it into the EXACT 7-line
 * text a user could have typed. The prompt is treated by the existing
 * Prompt-to-World pipeline exactly like user-authored input — no backend
 * change, no special case.
 *
 * Determinism: the same seed + difficulty ALWAYS produce a byte-identical
 * structured object and prompt text. The only `Math.random()` in this feature
 * is the UI seed-maker {@link makeRandomCrimeSeed} (mirroring
 * `demoCaseSelection.rollDemoCaseId`); the pure generator never calls it.
 *
 * Gender: a person's gender is derived FIRST, then the first name is drawn
 * from the matching pool — name and gender are internally consistent. The
 * human label always comes from the canonical Phase 47 `victimGenderLabel`
 * (there is no second gender taxonomy).
 */

import type { JourneyDifficulty } from "./context";
import { victimGenderLabel } from "../scene/victimLabels";
import {
  CRIME_GENDERS,
  FIRST_NAMES_BY_GENDER,
  LAST_NAMES,
  LOCATIONS,
  MOTIVES,
  OCCUPATIONS,
  WEAPONS,
  type CrimeGender,
  type CuratedLocation,
  type CuratedWeapon,
} from "./randomCrimeData";

/** One generated person (victim / murderer / witness). */
export interface CrimePerson {
  readonly gender: CrimeGender;
  readonly firstName: string;
  readonly lastName: string;
  /** `"First Last"` — the exact text the prompt shows. */
  readonly fullName: string;
}

/** The victim additionally carries an age and an occupation. */
export interface CrimeVictim extends CrimePerson {
  /** Integer 18–85 inclusive. */
  readonly age: number;
  readonly occupation: string;
}

/** The canonical STRUCTURED generation result (formatting happens later). */
export interface RandomCrime {
  readonly victim: CrimeVictim;
  readonly murderer: CrimePerson;
  readonly witness: CrimePerson;
  readonly motive: string;
  readonly weapon: CuratedWeapon;
  /** `"HH:MM"` (24-hour, zero-padded) inside the 18:00–02:59 window. */
  readonly time: string;
  readonly location: string;
}

/** Victim age bounds (inclusive). */
export const VICTIM_AGE_MIN = 18;
export const VICTIM_AGE_MAX = 85;

/** Time window bounds (inclusive): 18:00 through 02:59. */
export const CRIME_TIME_WINDOW_MINUTES = 9 * 60; // 540 minutes
export const CRIME_TIME_START_HOUR = 18;

/**
 * Bounded retry cap for drawing pairwise-distinct persons from the SAME
 * seeded stream. The pool is >11,000 identity combinations, so a collision
 * beyond a handful of draws is astronomically unlikely; the cap guarantees the
 * generator can never loop forever.
 */
export const PERSON_DRAW_RETRY_CAP = 64;

/**
 * mulberry32 — a small, fast, deterministic 32-bit PRNG. The seed is
 * normalized with `>>> 0`. The same seed always yields the same stream.
 */
export function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return function next(): number {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * The ONE random wrapper of this feature: maps a uniform `[0, 1)` RNG to a
 * 32-bit unsigned seed. `rng` defaults to `Math.random`; tests inject a fixed
 * value so seeding is deterministic. The pure generator never calls this.
 */
export function makeRandomCrimeSeed(rng: () => number = Math.random): number {
  return Math.floor(rng() * 0x100000000) >>> 0;
}

/** Uniform integer index into an array of `length` (defensive `% length`). */
function pickIndex(rng: () => number, length: number): number {
  return Math.floor(rng() * length) % length;
}

/**
 * Bounded retry cap for drawing a last name that differs from the (already
 * drawn) first name. "Frank"/"Martin"/"Simon"/"Heinrich" are valid in BOTH
 * pools, so a collision is possible; the cap guarantees the draw can never
 * loop forever. The pool has 72 last names, so a genuine collision beyond a
 * handful of draws is astronomically unlikely.
 */
export const LAST_NAME_RETRY_CAP = 16;

/**
 * Draw a fresh person: gender first, then a name from the matching pool.
 *
 * Phase 48 F4 — a person whose first name equals their last name (e.g.
 * "Frank Frank") reads as a bug, so the SAME seeded stream is used to redraw a
 * different last name when it collides. The redraw is BOUNDED (then a
 * deterministic pool scan is the last resort), so determinism and the
 * distinct-persons logic are preserved and the loop can never be unbounded.
 * The gender/name-pool consistency rule is untouched.
 */
function drawPerson(rng: () => number): CrimePerson {
  const gender = CRIME_GENDERS[pickIndex(rng, CRIME_GENDERS.length)];
  const firstPool = FIRST_NAMES_BY_GENDER[gender];
  const firstName = firstPool[pickIndex(rng, firstPool.length)];

  let lastName = LAST_NAMES[pickIndex(rng, LAST_NAMES.length)];
  let lastNameAttempts = 0;
  while (lastName === firstName && lastNameAttempts < LAST_NAME_RETRY_CAP) {
    lastNameAttempts += 1;
    lastName = LAST_NAMES[pickIndex(rng, LAST_NAMES.length)];
  }
  // Defensive last resort (never expected): pick the first curated last name
  // that differs from the first name, deterministically.
  if (lastName === firstName) {
    lastName = LAST_NAMES.find((candidate) => candidate !== firstName) ?? lastName;
  }

  return { gender, firstName, lastName, fullName: `${firstName} ${lastName}` };
}

function identityOf(person: CrimePerson): string {
  return `${person.gender}:${person.firstName}:${person.lastName}`;
}

/**
 * Draw `count` pairwise-distinct persons (distinct identity AND distinct full
 * name) from the seeded stream, with a BOUNDED retry cap. If the cap is ever
 * reached (astronomically unlikely), the remaining slots are filled by a
 * deterministic pool scan that skips used names — never an unbounded loop and
 * never a placeholder name.
 */
function drawDistinctPersons(rng: () => number, count: number): CrimePerson[] {
  const persons: CrimePerson[] = [];
  const identities = new Set<string>();
  const fullNames = new Set<string>();

  let attempts = 0;
  while (persons.length < count && attempts < PERSON_DRAW_RETRY_CAP) {
    attempts += 1;
    const candidate = drawPerson(rng);
    const identity = identityOf(candidate);
    if (identities.has(identity) || fullNames.has(candidate.fullName)) continue;
    identities.add(identity);
    fullNames.add(candidate.fullName);
    persons.push(candidate);
  }

  if (persons.length < count) {
    for (const gender of CRIME_GENDERS) {
      const firstPool = FIRST_NAMES_BY_GENDER[gender];
      for (const firstName of firstPool) {
        for (const lastName of LAST_NAMES) {
          if (persons.length >= count) break;
          // Phase 48 F4 — never emit an identical first/last name here either.
          if (lastName === firstName) continue;
          const fullName = `${firstName} ${lastName}`;
          const identity = `${gender}:${firstName}:${lastName}`;
          if (identities.has(identity) || fullNames.has(fullName)) continue;
          identities.add(identity);
          fullNames.add(fullName);
          persons.push({ gender, firstName, lastName, fullName });
        }
        if (persons.length >= count) break;
      }
      if (persons.length >= count) break;
    }
  }

  return persons;
}

/** Integer age in `[VICTIM_AGE_MIN, VICTIM_AGE_MAX]`. */
function drawAge(rng: () => number): number {
  return VICTIM_AGE_MIN + pickIndex(rng, VICTIM_AGE_MAX - VICTIM_AGE_MIN + 1);
}

/**
 * A minute-level `"HH:MM"` inside the 18:00–02:59 window. 540 distinct
 * minutes (18:00 .. 23:59 .. 02:59), wrapping past midnight.
 */
function drawTime(rng: () => number): string {
  const offset = pickIndex(rng, CRIME_TIME_WINDOW_MINUTES);
  const totalMinutes = (CRIME_TIME_START_HOUR * 60 + offset) % (24 * 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

/** Weapons eligible at a difficulty (see the policy in randomCrimeData). */
export function eligibleWeapons(difficulty: JourneyDifficulty): readonly CuratedWeapon[] {
  if (difficulty === "hard") return WEAPONS;
  if (difficulty === "medium") {
    return WEAPONS.filter(
      (weapon) => weapon.difficultyTags.includes("easy") || weapon.difficultyTags.includes("medium"),
    );
  }
  return WEAPONS.filter((weapon) => weapon.difficultyTags.includes("easy"));
}

/** Locations eligible at a difficulty (same policy shape as weapons). */
export function eligibleLocations(difficulty: JourneyDifficulty): readonly CuratedLocation[] {
  if (difficulty === "hard") return LOCATIONS;
  if (difficulty === "medium") {
    return LOCATIONS.filter(
      (location) =>
        location.difficultyTags.includes("easy") || location.difficultyTags.includes("medium"),
    );
  }
  return LOCATIONS.filter((location) => location.difficultyTags.includes("easy"));
}

/**
 * Deterministically generate a structured crime from `seed` + `difficulty`.
 * Pure: same seed + difficulty => byte-identical output.
 */
export function generateRandomCrime(seed: number, difficulty: JourneyDifficulty): RandomCrime {
  const rng = mulberry32(seed);
  const [victimPerson, murderer, witness] = drawDistinctPersons(rng, 3);

  const victim: CrimeVictim = {
    ...victimPerson,
    age: drawAge(rng),
    occupation: OCCUPATIONS[pickIndex(rng, OCCUPATIONS.length)],
  };
  const motive = MOTIVES[pickIndex(rng, MOTIVES.length)];

  const weaponPool = eligibleWeapons(difficulty);
  const weapon = weaponPool[pickIndex(rng, weaponPool.length)];

  const time = drawTime(rng);

  const locationPool = eligibleLocations(difficulty);
  const location = locationPool[pickIndex(rng, locationPool.length)].name;

  return { victim, murderer, witness, motive, weapon, time, location };
}

/**
 * Format a structured crime into the EXACT 7-line prompt. Stable labels, no
 * blank lines, no extra lines. The victim's gender is the canonical Phase 47
 * capitalized label.
 */
export function formatRandomCrimePrompt(crime: RandomCrime): string {
  const genderLabel = victimGenderLabel(crime.victim.gender) ?? "Unknown";
  return [
    `Victim: ${crime.victim.fullName} (${genderLabel}, ${crime.victim.age}, ${crime.victim.occupation})`,
    `Murderer: ${crime.murderer.fullName}`,
    `Motive: ${crime.motive}`,
    `Weapon: ${crime.weapon.displayName}`,
    `Time: ${crime.time}`,
    `Witness: ${crime.witness.fullName}`,
    `Location: ${crime.location}`,
  ].join("\n");
}
