import { describe, expect, it } from "vitest";
import {
  clearSessionDemoCaseId,
  DEMO_CASE_IDS,
  DEMO_CASE_ID_STORAGE_KEY,
  getSessionDemoCaseId,
  isDemoCaseId,
  rollDemoCaseId,
  selectDemoCaseId,
  setSessionDemoCaseId,
  type DemoCaseId,
  type DemoCaseStorage,
} from "./demoCaseSelection";

/**
 * Phase 28 §7/§15 — the closed Demo registry + deterministic selection.
 *
 * The registry is the FRONTEND half of the sibling contract (the closed
 * backend allowlist serves exactly these fixture ids on the fake/demo path).
 * Every mapping test injects the RNG — no probabilistic tests, no real
 * Math.random. The per-session holder pins the selected id so nothing
 * mid-session can re-roll it.
 */

function fakeStorage(initial: Record<string, string> = {}): DemoCaseStorage & { dump(): Record<string, string> } {
  const map = new Map<string, string>(Object.entries(initial));
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => {
      map.set(key, value);
    },
    removeItem: (key) => {
      map.delete(key);
    },
    dump: () => Object.fromEntries(map.entries()),
  };
}

describe("demo registry — exactly 3 stable unique fixture ids (Phase 28 §3/§5/§15)", () => {
  it("registers exactly three closed ids", () => {
    expect(DEMO_CASE_IDS).toHaveLength(3);
  });

  it("uses the sibling's agreed names — unique and stable", () => {
    expect(DEMO_CASE_IDS).toEqual([
      "demo-apartment", // Demo #1 — the existing golden case
      "demo-gallery", // Demo #2
      "demo-laboratory", // Demo #3
    ]);
    expect(new Set(DEMO_CASE_IDS).size).toBe(3); // unique
  });

  it("every registered id validates through isDemoCaseId; junk does not", () => {
    for (const id of DEMO_CASE_IDS) {
      expect(isDemoCaseId(id)).toBe(true);
    }
    for (const junk of ["demo-", "demo-apt", "Demo-Apartment", "", null, 42, "demo-apartment-2"]) {
      expect(isDemoCaseId(junk)).toBe(false);
    }
  });

  it("the storage key is the documented pd_demo_case_id", () => {
    expect(DEMO_CASE_ID_STORAGE_KEY).toBe("pd_demo_case_id");
  });
});

describe("selectDemoCaseId — deterministic RNG -> fixture mapping (Phase 28 §7/§15)", () => {
  it("maps the injected rng to the registered fixtures exactly", () => {
    expect(selectDemoCaseId(() => 0)).toBe("demo-apartment");
    expect(selectDemoCaseId(() => 0.34)).toBe("demo-gallery");
    expect(selectDemoCaseId(() => 0.67)).toBe("demo-laboratory");
  });

  it("handles the 0.999… edge (the highest uniform rng value)", () => {
    expect(selectDemoCaseId(() => 0.999999999999)).toBe("demo-laboratory");
  });

  it("stays on a registered id even for an out-of-range rng (defensive % N)", () => {
    // floor(1 * 3) = 3 -> 3 % 3 = 0 -> demo-apartment; never an index miss.
    expect(selectDemoCaseId(() => 1)).toBe("demo-apartment");
  });

  it("rolls each fixture from exactly its third of [0,1)", () => {
    // Deterministic partition check across the whole unit range — no random runs.
    const buckets = new Map<DemoCaseId, number>();
    for (let step = 0; step < 3000; step += 1) {
      const rng = step / 3000;
      const id = selectDemoCaseId(() => rng);
      buckets.set(id, (buckets.get(id) ?? 0) + 1);
    }
    expect(buckets.get("demo-apartment")).toBe(1000);
    expect(buckets.get("demo-gallery")).toBe(1000);
    expect(buckets.get("demo-laboratory")).toBe(1000);
  });
});

describe("rollDemoCaseId — session pinning + fresh-roll-only semantics (Phase 28 §8/§16/§17)", () => {
  it("rolls a NEW fixture and pins it in the per-session holder", () => {
    const storage = fakeStorage();
    const id = rollDemoCaseId(() => 0.34, storage);
    expect(id).toBe("demo-gallery");
    expect(getSessionDemoCaseId(storage)).toBe("demo-gallery");
    expect(storage.dump()).toEqual({ [DEMO_CASE_ID_STORAGE_KEY]: "demo-gallery" });
  });

  it("a FRESH Try Demo Case may roll again: the new roll overwrites the holder", () => {
    const storage = fakeStorage();
    rollDemoCaseId(() => 0, storage); // demo-apartment
    expect(getSessionDemoCaseId(storage)).toBe("demo-apartment");
    // The next independent click rolls whatever the rng says (here #3) — a
    // stale hold on Demo #1 is NEVER a hidden fallback.
    const next = rollDemoCaseId(() => 0.67, storage);
    expect(next).toBe("demo-laboratory");
    expect(getSessionDemoCaseId(storage)).toBe("demo-laboratory");
  });

  it("repeated reads return the SAME id (authoritative for the session, no re-roll)", () => {
    const storage = fakeStorage();
    rollDemoCaseId(() => 0, storage);
    expect(getSessionDemoCaseId(storage)).toBe("demo-apartment");
    expect(getSessionDemoCaseId(storage)).toBe("demo-apartment");
    expect(getSessionDemoCaseId(storage)).toBe("demo-apartment");
  });

  it("always stores an allowlisted id", () => {
    for (const rng of [() => 0, () => 0.34, () => 0.67, () => 0.999999999999]) {
      const storage = fakeStorage();
      const id = rollDemoCaseId(rng, storage);
      expect(isDemoCaseId(id)).toBe(true);
      expect(isDemoCaseId(getSessionDemoCaseId(storage))).toBe(true);
    }
  });

  it("clearSessionDemoCaseId resets the holder (Back-to-start / deliberate reset)", () => {
    const storage = fakeStorage();
    rollDemoCaseId(() => 0.34, storage);
    expect(getSessionDemoCaseId(storage)).toBe("demo-gallery");
    clearSessionDemoCaseId(storage);
    expect(getSessionDemoCaseId(storage)).toBeNull();
    expect(storage.dump()).toEqual({});
  });

  it("a stale/tampered stored value is discarded (never trusted raw)", () => {
    const storage = fakeStorage({ [DEMO_CASE_ID_STORAGE_KEY]: "demo-evil" });
    expect(getSessionDemoCaseId(storage)).toBeNull();
  });

  it("degrades gracefully with NO storage (server-render / hardened context)", () => {
    // No window.sessionStorage in this environment -> defaultStorage() is null.
    expect(getSessionDemoCaseId()).toBeNull();
    // The roll still returns a valid allowlisted id (selection never depends
    // on storage availability).
    const id = selectDemoCaseId(() => 0.67);
    expect(id).toBe("demo-laboratory");
    // set is best-effort: absent storage reports false without throwing.
    expect(setSessionDemoCaseId(id)).toBe(false);
    clearSessionDemoCaseId(); // never throws
  });
});

describe("setSessionDemoCaseId — explicit pinning API", () => {
  it("persists exactly the given allowlisted id", () => {
    const storage = fakeStorage();
    expect(setSessionDemoCaseId("demo-laboratory", storage)).toBe(true);
    expect(getSessionDemoCaseId(storage)).toBe("demo-laboratory");
  });

  it("accepts only allowlisted ids at the type level; runtime is untrusted-safe on read", () => {
    const storage = fakeStorage();
    setSessionDemoCaseId("demo-apartment", storage);
    // The read gate is what protects against tampering — the value round-trips.
    expect(getSessionDemoCaseId(storage)).toBe("demo-apartment");
  });
});