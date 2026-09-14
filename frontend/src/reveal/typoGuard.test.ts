import { describe, expect, it } from "vitest";
import { ASSET_REGISTRY } from "../scene/assetRegistry";
import { makeCandidates, makeRevealResponse } from "../scene/testFixtures";

/**
 * Phase 8 G typo guard: the visible registry label is "Kitchen knife" — the
 * historical "Kitchen Knyfe" misspelling must never resurface in any
 * player-visible label or reveal fixture string.
 */
describe("visible label typo guard — 'Kitchen Knyfe' must never exist", () => {
  it("the knife asset label is spelled correctly", () => {
    const knife = ASSET_REGISTRY.get("PROP_KITCHEN_KNIFE_01");
    expect(knife).not.toBeUndefined();
    expect(knife!.label).toBe("Kitchen knife");
  });

  it("no application-owned registry label contains the Knyfe typo", () => {
    for (const [assetId, entry] of ASSET_REGISTRY) {
      if (entry.label !== null) {
        expect(entry.label, `label for ${assetId}`).not.toMatch(/Knyfe/i);
      }
    }
  });

  it("candidate weapon names and reveal fixture strings are typo-free", () => {
    const weapons = makeCandidates().weapons;
    for (const weapon of weapons) {
      expect(weapon.name, `weapon ${weapon.id}`).not.toMatch(/Knyfe/i);
    }
    expect(makeRevealResponse().truth.weaponName).not.toMatch(/Knyfe/i);
  });
});