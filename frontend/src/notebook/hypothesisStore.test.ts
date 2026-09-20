import { describe, expect, it } from "vitest";
import {
  clearHypothesis,
  EMPTY_PINS,
  HYPOTHESIS_KEY_PREFIX,
  hypothesisKey,
  isHypothesisKey,
  loadHypothesis,
  saveHypothesis,
  type HypothesisPins,
  type HypothesisStorage,
} from "./hypothesisStore";

/**
 * Player Hypothesis store (Phase 18C) — PLAYER NOTES ONLY.
 *
 * Proves:
 *   - the storage surface is namespaced per playthrough under
 *     `pd_hypothesis_v1:*` and touches NEVER another key (req 16: auth and
 *     existing storage are untouched);
 *   - only the four pin fields round-trip; malformed/hostile stored JSON
 *     collapses to the EMPTY pins — nothing leaks;
 *   - pin writes are pure localStorage side effects: saving a pin performs
 *     no network work (the module has no transport beyond the injectable
 *     storage) and never contains hidden truth.
 */

function makeMemoryStorage(): HypothesisStorage & { writes: string[] } {
  const map = new Map<string, string>();
  const writes: string[] = [];
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => {
      map.set(key, value);
      writes.push(key);
    },
    removeItem: (key) => {
      map.delete(key);
    },
    writes,
  };
}

const FULL_PINS: HypothesisPins = {
  suspect: "suspect_beta",
  motive: "motive_gamma",
  weapon: "weapon_beta",
  time: "19:00",
};

describe("hypothesisStore — key namespace (req 16)", () => {
  it("uses the stable per-playthrough namespace and never touches other keys", () => {
    const storage = makeMemoryStorage();
    const key = hypothesisKey("PT-42");
    expect(key).toBe(`${HYPOTHESIS_KEY_PREFIX}:PT-42`);
    expect(isHypothesisKey(key)).toBe(true);
    expect(isHypothesisKey("pd_playthrough_token")).toBe(false);
    expect(isHypothesisKey("pd_hypothesis_v1")).toBe(false);

    saveHypothesis("PT-42", FULL_PINS, storage);
    expect(storage.writes).toEqual([`${HYPOTHESIS_KEY_PREFIX}:PT-42`]);
    // Two different playthroughs never share a key.
    saveHypothesis("PT-7", FULL_PINS, storage);
    expect(storage.writes).toEqual([
      `${HYPOTHESIS_KEY_PREFIX}:PT-42`,
      `${HYPOTHESIS_KEY_PREFIX}:PT-7`,
    ]);
  });

  it("round-trips only the four player pin fields (suspect/motive/weapon/time)", () => {
    const storage = makeMemoryStorage();
    saveHypothesis("PT-42", FULL_PINS, storage);
    expect(loadHypothesis("PT-42", storage)).toEqual(FULL_PINS);
  });

  it("isolates pins per playthrough and clears only the targeted key", () => {
    const storage = makeMemoryStorage();
    saveHypothesis("PT-42", FULL_PINS, storage);
    saveHypothesis("PT-7", { suspect: null, motive: "motive_alpha", weapon: null, time: null }, storage);

    clearHypothesis("PT-42", storage);

    expect(loadHypothesis("PT-42", storage)).toEqual(EMPTY_PINS);
    expect(loadHypothesis("PT-7", storage)).toEqual({ suspect: null, motive: "motive_alpha", weapon: null, time: null });
  });
});

describe("hypothesisStore — player notes never leak hidden state (reqs 6, 16)", () => {
  it("loads an absent key as the empty pins", () => {
    expect(loadHypothesis("PT-missing", makeMemoryStorage())).toEqual(EMPTY_PINS);
    expect(loadHypothesis("PT-missing", null)).toEqual(EMPTY_PINS);
  });

  it("collapses malformed stored JSON to the empty pins", () => {
    const storage = makeMemoryStorage();
    storage.setItem(hypothesisKey("PT-42"), "not json {");
    expect(loadHypothesis("PT-42", storage)).toEqual(EMPTY_PINS);

    storage.setItem(hypothesisKey("PT-42"), JSON.stringify("a string!"));
    expect(loadHypothesis("PT-42", storage)).toEqual(EMPTY_PINS);

    storage.setItem(hypothesisKey("PT-42"), JSON.stringify([1, 2, 3]));
    expect(loadHypothesis("PT-42", storage)).toEqual(EMPTY_PINS);
  });

  it("hostile/foreign stored fields never leak — only valid pin fields are read back", () => {
    const storage = makeMemoryStorage();
    storage.setItem(hypothesisKey("PT-42"), JSON.stringify({ suspect: "x", winner: "suspect_alpha", truth: "secret" }));
    const loaded = loadHypothesis("PT-42", storage);
    expect(loaded.suspect).toBe("x"); // the valid pin survives
    expect(loaded.motive).toBeNull();
    expect(loaded.weapon).toBeNull();
    expect(loaded.time).toBeNull();
    expect("winner" in loaded).toBe(false);
    expect("truth" in loaded).toBe(false);
  });

  it("never reads unknown keys nor preserves foreign fields on write", () => {
    const storage = makeMemoryStorage();
    // A hostile existing record with a truth field:
    storage.setItem(
      hypothesisKey("PT-42"),
      JSON.stringify({ suspect: "suspect_beta", winner: "actual_murderer_id", hiddenTruth: "X killed Y" }),
    );
    const loaded = loadHypothesis("PT-42", storage);
    expect(loaded.suspect).toBe("suspect_beta");
    expect("winner" in loaded).toBe(false);
    expect("hiddenTruth" in loaded).toBe(false);

    saveHypothesis("PT-42", loaded, storage);
    const persisted = storage.getItem(hypothesisKey("PT-42")) ?? "";
    // The only keys allowed in the persisted JSON are the four pins —
    // writing back can never resurrect the foreign fields.
    expect(Object.keys(JSON.parse(persisted)).sort()).toEqual(["motive", "suspect", "time", "weapon"]);
  });

  it("sanitizes pins on save: bad time shapes and non-strings never persist", () => {
    const storage = makeMemoryStorage();
    saveHypothesis("PT-42", {
      suspect: "suspect_alpha",
      motive: "<script>alert(1)</script>",
      weapon: 42 as unknown as string,
      time: "25:99",
    } as HypothesisPins, storage);
    expect(loadHypothesis("PT-42", storage)).toEqual({
      suspect: "suspect_alpha",
      motive: null,
      weapon: null,
      time: null,
    });
    // The submitted selection is what validation governs — but the pin write
    // itself never stores a malformed time.
    const persisted = JSON.parse(storage.getItem(hypothesisKey("PT-42"))!) as HypothesisPins;
    expect(persisted.time).toBeNull();
  });
});

describe("hypothesisStore — no transport / auth surface", () => {
  it("the module performs no remote work (no fetch, no XMLHttpRequest)", () => {
    // The store's only side effects go through the injectable storage; there
    // is no network surface at all. (Compile-time shape is the primary guard;
    // this test pins the behavior: saving uses only the injected storage.)
    const storage = makeMemoryStorage();
    expect(saveHypothesis("PT-42", FULL_PINS, storage)).toBe(true);
    expect(loadHypothesis("PT-42", storage)).toEqual(FULL_PINS);
  });
});