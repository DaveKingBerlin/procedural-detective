// @vitest-environment jsdom
/**
 * ADV-47 transient adversarial probe — NOT product code.
 * Run from frontend/:
 *   npx vitest run --config ..\screenshots\evidence\adv47\adv47.vitest.config.mjs
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import VictimProfilePanel from "../../../frontend/src/scene/VictimProfilePanel";
import { parseVictimProfile } from "../../../frontend/src/scene/validation";
import { buildVictimProfileView, isVictimSceneObject } from "../../../frontend/src/scene/victimModel";
import type { VictimProfileDTO, EvidenceReadResultDTO } from "../../../frontend/src/api/types";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function render(victim: VictimProfileDTO, records: EvidenceReadResultDTO[] = []): string {
  return renderToStaticMarkup(<VictimProfilePanel victim={victim} records={records} onClose={() => {}} />);
}

const HOSTILE_VICTIM: VictimProfileDTO = {
  personId: "v",
  name: '<img src=x onerror=alert(1)><script>alert(2)</script>',
  age: null,
  gender: null,
  occupation: null,
  background: null,
  sceneObjectId: null,
  discovery: {
    evidenceId: "body_found_01",
    locationId: "kitchen",
    locationName: "<b>Kitchen</b>",
    observedAt: "not-a-time",
    title: "<script>t</script>",
  },
};

describe("ADV-47 parseVictimProfile hostile inputs", () => {
  it("never yields a bogus age for extreme/typed ages (age 0 IS accepted)", () => {
    for (const age of [-5, 999, 1e9, Number.NaN, Infinity, "39", true, 1.5]) {
      const parsed = parseVictimProfile({ personId: "v", name: "V", age });
      expect(parsed?.age ?? null).toBeNull();
    }
    // Documented frontend/backend divergence: the backend omits age 0 (min 1),
    // the lenient frontend parser ACCEPTS it and renders "Age: 0".
    expect(parseVictimProfile({ personId: "v", name: "V", age: 0 })?.age).toBe(0);
  });

  it("normalizes arbitrary gender to a closed token and keeps name", () => {
    for (const gender of ["MALE", "banana", "", 123, ["female"], "female"]) {
      const parsed = parseVictimProfile({ personId: "v", name: "V", gender });
      expect(["male", "female", "other", "unknown"]).toContain(parsed?.gender ?? "unknown");
    }
  });

  it("ignores a name-less block entirely", () => {
    expect(parseVictimProfile({ personId: "v", age: 40 })).toBeNull();
    expect(parseVictimProfile("string")).toBeNull();
    expect(parseVictimProfile(null)).toBeNull();
  });

  it("drops a malformed discovery block to null", () => {
    expect(parseVictimProfile({ personId: "v", name: "V", discovery: { evidenceId: "x" } })?.discovery).toBeNull();
    expect(parseVictimProfile({ personId: "v", name: "V", discovery: "nope" })?.discovery).toBeNull();
  });
});

describe("ADV-47 VictimProfilePanel hostile rendering is inert", () => {
  it("escapes XSS payloads and renders no broken values", () => {
    const html = render(HOSTILE_VICTIM);
    expect(html).not.toContain("<img src=x onerror=alert(1)>");
    expect(html).not.toContain("<script>alert(2)</script>");
    expect(html).not.toContain("undefined");
    expect(html).not.toContain("NaN");
    expect(html).not.toContain(">null<");
    expect(html).toContain("&lt;img");
  });

  it("buildVictimProfileView never throws on hostile records", () => {
    const hostile: EvidenceReadResultDTO = {
      evidenceId: "r1",
      kind: "forensic",
      title: "<script>x</script>",
      description: null,
      openedAt: "2026-09-11T22:00:00+02:00",
      readByPlayer: true,
      content: {
        renderType: "FORENSIC_COMPARISON",
        victimRelated: true,
        comparison: "<img src=x onerror=alert(1)>",
        entries: [{ time: "<script>", text: "boom" }, null, 42, "str"] as unknown as [],
        events: "not-an-array" as unknown as [],
      },
    };
    const view = buildVictimProfileView(HOSTILE_VICTIM, [hostile]);
    expect(view.forensicFindings.length).toBe(1);
    expect(view.forensicFindings[0].detail).toContain("img src=x");
  });
});

describe("ADV-47 isVictimSceneObject boundaries", () => {
  it("does not flag a non-victim object when a victim exists", () => {
    const victim: VictimProfileDTO = { ...HOSTILE_VICTIM, sceneObjectId: "body", discovery: null };
    expect(
      isVictimSceneObject({ objectId: "lamp", assetType: "prop", subtype: "lamp", evidenceId: "x" }, victim),
    ).toBe(false);
  });

  it("flags ANY object sharing the discovery evidence id as the victim body", () => {
    const victim: VictimProfileDTO = {
      ...HOSTILE_VICTIM,
      sceneObjectId: "body",
      discovery: {
        evidenceId: "shared_ev",
        locationId: "l",
        locationName: "L",
        observedAt: "2026-01-01T00:00:00Z",
        title: "t",
      },
    };
    expect(
      isVictimSceneObject(
        { objectId: "some_other_prop", assetType: "prop", subtype: "prop", evidenceId: "shared_ev" },
        victim,
      ),
    ).toBe(true);
  });

  it("flags an object whose assetType/subtype is victim_body even when sceneObjectId differs", () => {
    const victim: VictimProfileDTO = { ...HOSTILE_VICTIM, sceneObjectId: "body", discovery: null };
    expect(
      isVictimSceneObject(
        { objectId: "impostor", assetType: "victim_body", subtype: null, evidenceId: null },
        victim,
      ),
    ).toBe(true);
  });
});
