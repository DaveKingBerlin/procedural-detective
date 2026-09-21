import { describe, expect, it } from "vitest";
import type { SceneWorldObject } from "./buildInvestigationScene";
import { buildInvestigationScene } from "./buildInvestigationScene";
import {
  evidenceLabelFor,
  focusBadgesFor,
  humanizeCanonicalName,
  isProceduralArtifact,
  FALLBACK_EVIDENCE_LABEL,
} from "./objectLabel";
import {
  makeBootstrap,
  makeIcePickDefinition,
  makeProcWorldObject,
  makeTrophyDefinition,
} from "./testFixtures";

/**
 * Phase 18B — semantic evidence label + focus badges (pure, deterministic).
 * Proves: humanized canonicalName label for proc.* objects, catalog labels
 * pass through unchanged, the safe "Evidence Object" fallback (never a raw
 * id / "no label" gray-eye), honest badges only for procedural objects, and
 * zero `proc.*`/truth/winner leakage in anything the UI can render.
 */

function procWorldObject(overrides: Parameters<typeof makeProcWorldObject>[0] = {}): SceneWorldObject {
  const bootstrap = makeBootstrap();
  bootstrap.scene.worldObjects.push(makeProcWorldObject(overrides));
  const model = buildInvestigationScene(bootstrap);
  const found = model.worldObjects.find((o) => o.objectId === (overrides.objectId ?? "custom_trophy"));
  if (!found) throw new Error("proc world object missing from model");
  return found;
}

function catalogWorldObject(objectId: string): SceneWorldObject {
  const model = buildInvestigationScene(makeBootstrap());
  const found = model.worldObjects.find((o) => o.objectId === objectId);
  if (!found) throw new Error(`catalog world object missing: ${objectId}`);
  return found;
}

describe("evidenceLabelFor — the semantic label path (Phase 18B)", () => {
  it("keeps the catalog object's existing public registry label unchanged", () => {
    expect(evidenceLabelFor(catalogWorldObject("kitchen_knife"))).toBe("Kitchen knife");
    expect(evidenceLabelFor(catalogWorldObject("apartment_laptop"))).toBe("Laptop");
  });

  it("humanizes generated.canonicalName for a procedural object (ice pick)", () => {
    // Phase 18B hard-case showcase fixture: the REAL published thin definition.
    const pick = procWorldObject({
      objectId: "bronze_ceremonial_ice_pick",
      assetId: "proc.decor.4551660f4a46b2eb",
      generated: makeIcePickDefinition(),
    });
    expect(pick.generated?.canonicalName).toBe("Bronze Ceremonial Ice Pick");
    expect(evidenceLabelFor(pick)).toBe("Bronze Ceremonial Ice Pick");
  });

  it("uses the canonicalName for a trophy proc object (not its raw id)", () => {
    const trophy = procWorldObject();
    expect(evidenceLabelFor(trophy)).toBe("Custom Trophy");
  });

  it("NEVER returns the raw assetId, objectId or a proc.* token", () => {
    const trophy = procWorldObject({ objectId: "custom_trophy" });
    const label = evidenceLabelFor(trophy);
    expect(label).not.toContain("proc.");
    expect(label).not.toBe(trophy.assetId);
    expect(label).not.toBe("custom_trophy");
    expect(label).not.toBe("proc.decor.a1b2c3d4e5f60718");
  });

  it("falls back to a safe human string when there is no label and no generated name", () => {
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects.push(
      makeProcWorldObject({
        objectId: "orphan_proc",
        generated: null as never, // proc.* id WITHOUT a valid generated block
      }),
    );
    const model = buildInvestigationScene(bootstrap);
    const orphan = model.worldObjects.find((o) => o.objectId === "orphan_proc");
    expect(orphan).toBeDefined();
    expect(evidenceLabelFor(orphan!)).toBe(FALLBACK_EVIDENCE_LABEL);
    expect(evidenceLabelFor(orphan!)).not.toContain("orphan_proc");
  });

  it("is bounded and sanitized: control chars stripped, whitespace collapsed", () => {
    expect(humanizeCanonicalName("  Bronze\u0000 Ceremonial\t\tIce \u0007 Pick  ")).toBe(
      "Bronze Ceremonial Ice Pick",
    );
    expect(humanizeCanonicalName("\u0001A\u001fB\u007fC\u0000D")).toBe("A B C D");
  });

  it("caps the humanized name at 80 characters (never unbounded)", () => {
    const long = `${"Ice Pick "}${"x".repeat(90)}`;
    expect(humanizeCanonicalName(long)).toBe(`${"Ice Pick "}${"x".repeat(71)}`);
    expect(humanizeCanonicalName(long)!.length).toBe(80);
  });

  it("returns null (caller falls back) for empty / non-string canonical names", () => {
    expect(humanizeCanonicalName("")).toBeNull();
    expect(humanizeCanonicalName("   \t ")).toBeNull();
    expect(humanizeCanonicalName(null)).toBeNull();
    expect(humanizeCanonicalName(undefined)).toBeNull();
    expect(humanizeCanonicalName(42)).toBeNull();
  });

  it("label and badge strings are the ONLY text sources — no truth/winner/candidate tokens", () => {
    const pick = procWorldObject({
      objectId: "bronze_ceremonial_ice_pick",
      assetId: "proc.decor.4551660f4a46b2eb",
      generated: makeIcePickDefinition(),
    });
    const label = evidenceLabelFor(pick);
    const badges = focusBadgesFor(pick);
    const assembled = [label, badges.procedural && "Procedural Artifact", badges.validatedGeometry && "Validated Geometry"]
      .filter((v) => typeof v === "string")
      .join(" ");
    for (const token of ["truth", "winner", "correct", "murderer", "motive", "weapon", "suspect_", "Becker", "Weiss"]) {
      expect(assembled.toLowerCase(), `no ${token} in focus text`).not.toContain(token.toLowerCase());
    }
  });
});

describe("ADV-209 — proc.* tokens never survive, controls stripped, ALL branches capped", () => {
  it("a canonicalName that IS a proc.* token sanitizes to the safe fallback", () => {
    expect(humanizeCanonicalName("proc.decor.abc123")).toBe(FALLBACK_EVIDENCE_LABEL);
    expect(humanizeCanonicalName("proc.decor.abc123")).not.toContain("proc.");
  });

  it("any proc. token class inside a name is replaced, never echoed", () => {
    expect(humanizeCanonicalName("bronze proc.decor.4551660f4a46b2eb ice pick")).toBe(
      "bronze Evidence Object ice pick",
    );
    expect(humanizeCanonicalName("PROC.DECOR.X badge")).toBe("Evidence Object badge");
    expect(humanizeCanonicalName("proc.decor.abc123")).not.toMatch(/proc\./i);
  });

  it("strips C1 controls, unicode line/paragraph separators and zero-width/format chars", () => {
    expect(humanizeCanonicalName("A\u2028B\u0085C\u200bD\u200eE\u2029F\u2060G\ufeffH")).toBe(
      "A B C D E F G H",
    );
    for (const input of [
      "a\u0085b", // C1 NEL
      "a\u2028b", // unicode line separator
      "a\u2029b", // unicode paragraph separator
      "a\u200bb\u200cc\u200dd", // zero-width space / ZWNJ / ZWJ
      "a\u2066b\u2069c", // bidi isolate markers
    ]) {
      expect(humanizeCanonicalName(input)).not.toMatch(/[\u0085\u2028\u2029\u200b-\u200f\u2060-\u206f\ufeff]/);
    }
  });

  it("caps the catalog-label branch at 80 chars — a 2400-char label never escapes", () => {
    const obj = procWorldObject();
    obj.label = "x".repeat(2400);
    const label = evidenceLabelFor(obj);
    expect(label.length).toBe(80);
    expect(label.length).toBeLessThanOrEqual(80);
  });

  it("bounds a 2400-char hostile-wrapping label to the cap (no unbounded aria-label)", () => {
    const obj = procWorldObject({ generated: null as never });
    obj.label = `<script>${"a".repeat(2400)}</script>`;
    const label = evidenceLabelFor(obj);
    expect(label.length).toBeLessThanOrEqual(80);
    expect(label.length).toBe(80);
  });

  it("keeps well-formed catalog labels byte-identical (no regression)", () => {
    expect(evidenceLabelFor(catalogWorldObject("kitchen_knife"))).toBe("Kitchen knife");
    expect(evidenceLabelFor(catalogWorldObject("apartment_laptop"))).toBe("Laptop");
    expect(humanizeCanonicalName("Bronze Ceremonial Ice Pick")).toBe("Bronze Ceremonial Ice Pick");
  });
});

describe("isProceduralArtifact — the ONE honest procedural condition", () => {
  it("is true for a proc.* asset with a valid generated definition", () => {
    const pick = procWorldObject({
      objectId: "bronze_ceremonial_ice_pick",
      assetId: "proc.decor.4551660f4a46b2eb",
      generated: makeIcePickDefinition(),
    });
    expect(isProceduralArtifact(pick)).toBe(true);
  });

  it("is false for a catalog object even when interactions/evidence exist", () => {
    expect(isProceduralArtifact(catalogWorldObject("kitchen_knife"))).toBe(false);
    expect(isProceduralArtifact(catalogWorldObject("apartment_laptop"))).toBe(false);
  });

  it("is false for a proc.* asset WITHOUT a valid generated definition", () => {
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects.push(
      makeProcWorldObject({
        objectId: "broken_proc",
        generated: null as never,
      }),
    );
    const model = buildInvestigationScene(bootstrap);
    const broken = model.worldObjects.find((o) => o.objectId === "broken_proc");
    expect(isProceduralArtifact(broken!)).toBe(false);
  });
});

describe("focusBadgesFor — badge strategy (both optional, both honest)", () => {
  it("shows BOTH badges only for a procedural artifact", () => {
    const pick = procWorldObject({
      objectId: "bronze_ceremonial_ice_pick",
      assetId: "proc.decor.4551660f4a46b2eb",
      generated: makeIcePickDefinition(),
    });
    expect(focusBadgesFor(pick)).toEqual({ procedural: true, validatedGeometry: true });
  });

  it("shows NEITHER badge for a catalog object", () => {
    expect(focusBadgesFor(catalogWorldObject("kitchen_knife"))).toEqual({
      procedural: false,
      validatedGeometry: false,
    });
  });

  it("the badge strings themselves carry no proc.* / hash / provider tokens", () => {
    const pick = procWorldObject();
    const badges = focusBadgesFor(pick);
    for (const text of ["Procedural Artifact", "Validated Geometry"]) {
      if (badges.procedural) {
        expect(text).not.toContain("proc.");
        expect(text).not.toContain("decor.");
      }
    }
  });

  it("Validated Geometry honestly tracks the SAME procedural condition", () => {
    const trophy = procWorldObject({ generated: makeTrophyDefinition() });
    const badges = focusBadgesFor(trophy);
    expect(badges.procedural).toBe(badges.validatedGeometry);
  });
});