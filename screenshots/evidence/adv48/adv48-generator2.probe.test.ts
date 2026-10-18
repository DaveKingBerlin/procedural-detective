/**
 * ADV-48 generator probe #2 (transient adversary evidence; NOT product code).
 * Second wave: content collisions, environment-catalog compatibility, catalog
 * semantics, difficulty invariance and RNG-degenerate seeding.
 */
import { describe, expect, it } from "vitest";
import { getAsset, hasAsset } from "../../../frontend/src/catalog/assetCatalog";
import {
  WEAPONS,
  LOCATIONS,
  MOTIVES,
  OCCUPATIONS,
  FIRST_NAMES_BY_GENDER,
  LAST_NAMES,
} from "../../../frontend/src/journey/randomCrimeData";
import {
  eligibleWeapons,
  eligibleLocations,
  formatRandomCrimePrompt,
  generateRandomCrime,
  makeRandomCrimeSeed,
} from "../../../frontend/src/journey/randomCrimeGenerator";
import type { JourneyDifficulty } from "../../../frontend/src/journey/context";

const DIFFS: readonly JourneyDifficulty[] = ["easy", "medium", "hard"];

/* ------------------------------------------------------------------ ADV48-G8 */
describe("ADV48-G8 curated first/last token collisions (silly full names)", () => {
  it("reports every first==last combination and whether it is reachable", () => {
    const firsts = [
      ...FIRST_NAMES_BY_GENDER.male,
      ...FIRST_NAMES_BY_GENDER.female,
    ];
    const collisions = firsts.filter((f) => (LAST_NAMES as readonly string[]).includes(f));
    const reachable = new Set<string>();
    for (let seed = 0; seed < 200000; seed += 1) {
      const d = DIFFS[seed % 3];
      const c = generateRandomCrime(seed, d);
      for (const p of [c.victim, c.murderer, c.witness]) {
        if (p.firstName === p.lastName) reachable.add(`${p.firstName} ${p.lastName}`);
      }
    }
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-G8] tokenCollisions=${JSON.stringify(collisions)} reachableSillyNames=${JSON.stringify([...reachable].sort())}`,
    );
    // Documented observation only — no assertion that this is a defect.
    expect(Array.isArray(collisions)).toBe(true);
  }, 120000);
});

/* ------------------------------------------------------------------ ADV48-G9 */
describe("ADV48-G9 curated locations vs the fixed backend environment catalog", () => {
  // Mirrors backend/app/world/environment.py canonicalize_environment_hint
  // token transform + alias map (apartment/office/hotel_suite/warehouse/mansion).
  const ALIASES: Record<string, string> = {
    apartment: "apartment",
    flat: "apartment",
    condo: "apartment",
    office: "office",
    company: "office",
    workplace: "office",
    hotel: "hotel_suite",
    room: "hotel_suite",
    suite: "hotel_suite",
    warehouse: "warehouse",
    depot: "warehouse",
    storage: "warehouse",
    mansion: "mansion",
    villa: "mansion",
    manor: "mansion",
  };
  // Canonical ids are always accepted (idempotent), exactly like the backend.
  for (const id of ["apartment", "office", "hotel_suite", "warehouse", "mansion"]) {
    ALIASES[id] = id;
  }
  const token = (s: string) =>
    s.normalize("NFKC").trim().toLowerCase().replace(/ /g, "_").replace(/-/g, "_");
  const canonicalize = (s: string) => ALIASES[token(s)] ?? null;

  it("counts how many curated locations resolve onto a supported kit", () => {
    const resolved: Record<string, string | null> = {};
    for (const l of LOCATIONS) resolved[l.name] = canonicalize(l.name);
    const ok = Object.values(resolved).filter((v) => v !== null).length;
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-G9] locations=${LOCATIONS.length} resolvable=${ok} mapping=${JSON.stringify(resolved)}`,
    );
    // Evidence: most curated locations do NOT canonicalize to a supported kit.
    expect(ok).toBeGreaterThan(0);
  });
});

/* ------------------------------------------------------------------ ADV48-G10 */
describe("ADV48-G10 same seed across difficulties keeps persons+motive identical", () => {
  it("only weapon/location change with difficulty (persons/motive are difficulty-invariant)", () => {
    const s = 424242;
    const e = generateRandomCrime(s, "easy");
    const m = generateRandomCrime(s, "medium");
    const h = generateRandomCrime(s, "hard");
    const personsSame =
      JSON.stringify([e.victim, e.murderer, e.witness, e.motive]) ===
      JSON.stringify([h.victim, h.murderer, h.witness, h.motive]);
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-G10] personsAndMotiveIdenticalAcrossDifficulties=${personsSame} weapon(e/m/h)=${e.weapon.displayName} / ${m.weapon.displayName} / ${h.weapon.displayName}`,
    );
    expect(personsSame).toBe(true);
  });
});

/* ------------------------------------------------------------------ ADV48-G11 */
describe("ADV48-G11 per-difficulty coverage of the eligible pools", () => {
  it("every eligible weapon + location is reachable by some seed in 300k draws", () => {
    const reachWeapon: Record<string, Set<string>> = { easy: new Set(), medium: new Set(), hard: new Set() };
    const reachLoc: Record<string, Set<string>> = { easy: new Set(), medium: new Set(), hard: new Set() };
    for (let seed = 0; seed < 300000; seed += 1) {
      const d = DIFFS[seed % 3];
      const c = generateRandomCrime(seed, d);
      reachWeapon[d].add(c.weapon.assetId);
      reachLoc[d].add(c.location);
    }
    const report = DIFFS.map((d) => ({
      d,
      weapon: `${reachWeapon[d].size}/${eligibleWeapons(d).length}`,
      loc: `${reachLoc[d].size}/${eligibleLocations(d).length}`,
    }));
    // eslint-disable-next-line no-console
    console.log(`[ADV48-G11] coverage=${JSON.stringify(report)}`);
    expect(reachWeapon.hard.size).toBe(WEAPONS.length);
  }, 120000);
});

/* ------------------------------------------------------------------ ADV48-G12 */
describe("ADV48-G12 makeRandomCrimeSeed hostile injected rng values", () => {
  it("huge/NaN/Infinity/negative rng never yields a non-uint32 seed", () => {
    const values = [
      0,
      -0,
      1,
      1 - Number.EPSILON,
      2 ** 32,
      2 ** 53,
      1e21,
      -1,
      -1e21,
      Number.NaN,
      Number.POSITIVE_INFINITY,
      Number.NEGATIVE_INFINITY,
    ];
    const out: Record<string, number> = {};
    for (const v of values) {
      const seed = makeRandomCrimeSeed(() => v);
      out[String(v)] = seed;
      expect(Number.isInteger(seed)).toBe(true);
      expect(seed).toBeGreaterThanOrEqual(0);
      expect(seed).toBeLessThan(2 ** 32);
    }
    // eslint-disable-next-line no-console
    console.log(`[ADV48-G12] seeds=${JSON.stringify(out)}`);
  });
});

/* ------------------------------------------------------------------ ADV48-G13 */
describe("ADV48-G13 prompt distinctness + no placeholder labels", () => {
  it("5000 sequential seeds yield mostly distinct prompts and no placeholder names", () => {
    const seen = new Set<string>();
    for (let seed = 0; seed < 5000; seed += 1) {
      const p = formatRandomCrimePrompt(generateRandomCrime(seed, "hard"));
      expect(p).not.toMatch(/Person [A-Z]|Victim \d|Witness [A-Z]/);
      seen.add(p);
    }
    // eslint-disable-next-line no-console
    console.log(`[ADV48-G13] distinctPrompts=${seen.size}/5000`);
    expect(seen.size).toBeGreaterThan(4900);
  });
});

/* ------------------------------------------------------------------ ADV48-G14 */
describe("ADV48-G14 catalog semantics of every curated 'weapon'", () => {
  it("logs category/subtype/label and flags non-weapon-like catalog entries", () => {
    const rows = WEAPONS.map((w) => {
      const d = getAsset(w.assetId);
      return {
        assetId: w.assetId,
        displayName: w.displayName,
        ok: hasAsset(w.assetId),
        category: d?.category ?? null,
        subtype: d?.subtype ?? null,
        label: d?.label ?? null,
        interactable: d?.interactable ?? null,
      };
    });
    const nonWeapon = rows.filter((r) => !/weapon|knife|blade/i.test(`${r.subtype} ${r.label}`));
    // eslint-disable-next-line no-console
    console.log(
      `[ADV48-G14] total=${rows.length} nonWeaponSubtype=${nonWeapon.length} sample=${JSON.stringify(nonWeapon.slice(0, 6))}`,
    );
    expect(rows.every((r) => r.ok)).toBe(true);
  });
});

/* ------------------------------------------------------------------ ADV48-G15 */
describe("ADV48-G15 victim age / time full-range reachability", () => {
  it("age 18..85 and all 540 minutes are reachable", () => {
    const ages = new Set<number>();
    const times = new Set<string>();
    for (let seed = 0; seed < 300000; seed += 1) {
      const c = generateRandomCrime(seed, "medium");
      ages.add(c.victim.age);
      times.add(c.time);
    }
    // eslint-disable-next-line no-console
    console.log(`[ADV48-G15] distinctAges=${ages.size}/68 distinctTimes=${times.size}/540`);
    expect(ages.size).toBe(68);
    expect(times.size).toBe(540);
  }, 120000);
});

/* ------------------------------------------------------------------ ADV48-G16 */
describe("ADV48-G16 motive/occupation coverage", () => {
  it("all 32 motives and 40 occupations are reachable", () => {
    const motives = new Set<string>();
    const occs = new Set<string>();
    for (let seed = 0; seed < 300000; seed += 1) {
      const c = generateRandomCrime(seed, "hard");
      motives.add(c.motive);
      occs.add(c.victim.occupation);
    }
    // eslint-disable-next-line no-console
    console.log(`[ADV48-G16] distinctMotives=${motives.size}/32 distinctOccupations=${occs.size}/40`);
    expect(motives.size).toBe(MOTIVES.length);
    expect(occs.size).toBe(OCCUPATIONS.length);
  }, 120000);
});
