import { describe, expect, it } from "vitest";
import { reExportV1, serializeSavegameV1 } from "./exportV1";
import canonical from "./fixtures/v1_demo_apartment.pdcase.json";
import deceptiveWitnessFixture from "./fixtures/v1_deceptive_witness.pdcase.json";
import { FreshReplayState } from "./replayRuntime";
import { parseSavegameV1, SavegameParseError, utf8ByteLength } from "./savegameV1";

/**
 * Phase 41 (ADV-41-04) — the backend STRICT-importer gap: a REAL server export
 * of a case that hides a lying witness carries the OPTIONAL
 * `replayTruth.witnessDeceptions` array (`{witnessId, witnessName,
 * deceptionReason?}`), and the strict import pipeline MUST accept, validate
 * and preserve it (absent stays byte-identical for legacy/deception-free
 * files). A saved replay then explains the deception post-reveal exactly like
 * the server's `truth.witnessDeceptions` (never pre-reveal).
 *
 * Fixtures:
 *  - v1_deceptive_witness.pdcase.json — HOMICIDE with an INNOCENT deceptive
 *    witness (Emily Reed lies for HIDE_UNRELATED_SECRET; Thomas Reed is the
 *    murderer) — the §13 shape a real export of such a case carries.
 *  - v1_witness_murderer.pdcase.json — HOMICIDE whose WITNESS is the murderer
 *    and is DECEPTIVE (SELF_PROTECTION) — also carries the list.
 *  - v1_demo_apartment.pdcase.json — legacy, no witnessDeceptions: re-export
 *    must stay byte-identical (no fabricated key).
 */

const CANONICAL_TEXT: string = JSON.stringify(canonical);
const DECEPTIVE_TEXT: string = JSON.stringify(deceptiveWitnessFixture);

function deceptiveDefinition(): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(DECEPTIVE_TEXT, utf8ByteLength(DECEPTIVE_TEXT));
}

function mutation(mutate: (doc: any) => void): string {
  const doc = JSON.parse(DECEPTIVE_TEXT);
  mutate(doc);
  return JSON.stringify(doc);
}

function kindOf(text: string): string | null {
  try {
    parseSavegameV1(text, utf8ByteLength(text));
    return null;
  } catch (error) {
    if (error instanceof SavegameParseError) return error.kind;
    throw error;
  }
}

describe("Phase 41 — replayTruth.witnessDeceptions (ADV-41-04)", () => {
  it("a REAL server-shaped replayTruth with witnessDeceptions PARSES and preserves the entries", () => {
    const definition = deceptiveDefinition();
    expect(definition.replayTruth.witnessDeceptions).toEqual([
      { witnessId: "emily_reed", witnessName: "Emily Reed", deceptionReason: "HIDE_UNRELATED_SECRET" },
    ]);
  });

  it("present witnessDeceptions round-trip through re-export and stay parseable", () => {
    const definition = deceptiveDefinition();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    expect(exported).toContain('"witnessDeceptions"');
    expect(exported).toContain('"HIDE_UNRELATED_SECRET"');
    const reloaded = parseSavegameV1(exported, utf8ByteLength(exported));
    expect(reloaded.replayTruth.witnessDeceptions).toEqual(definition.replayTruth.witnessDeceptions);
  });

  it("ABSENT witnessDeceptions (legacy) stays byte-identical — no fabricated key", () => {
    const legacy = parseSavegameV1(CANONICAL_TEXT, utf8ByteLength(CANONICAL_TEXT));
    expect(legacy.replayTruth.witnessDeceptions).toBeUndefined();
    const reExport = serializeSavegameV1(reExportV1(legacy, canonical.exportedAt));
    expect(reExport).toBe(CANONICAL_TEXT);
  });

  it("an empty witnessDeceptions list is accepted (a present-but-empty list is legal)", () => {
    const text = mutation((doc) => {
      doc.case.replayTruth.witnessDeceptions = [];
    });
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.replayTruth.witnessDeceptions).toEqual([]);
    // A present-but-empty list stays present through re-export (the server
    // also emits an explicit empty list when no witness lied).
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    expect(exported).toContain('"witnessDeceptions":[]');
  });

  describe("malformed entries fail closed", () => {
    it("an unknown key inside an entry is rejected", () => {
      const text = mutation((doc) => {
        doc.case.replayTruth.witnessDeceptions[0].truthfulness = "DECEPTIVE";
      });
      expect(kindOf(text)).toBe("invalid");
    });

    it("a missing witnessId / missing witnessName is rejected", () => {
      const missingId = mutation((doc) => {
        delete doc.case.replayTruth.witnessDeceptions[0].witnessId;
      });
      expect(kindOf(missingId)).toBe("invalid");
      const missingName = mutation((doc) => {
        delete doc.case.replayTruth.witnessDeceptions[0].witnessName;
      });
      expect(kindOf(missingName)).toBe("invalid");
    });

    it("an ABSENT deceptionReason is legal (the reason is OPTIONAL on the server DTO) and normalizes to null", () => {
      const text = mutation((doc) => {
        delete doc.case.replayTruth.witnessDeceptions[0].deceptionReason;
      });
      const definition = parseSavegameV1(text, utf8ByteLength(text));
      expect(definition.replayTruth.witnessDeceptions?.[0]).toEqual({
        witnessId: "emily_reed",
        witnessName: "Emily Reed",
        deceptionReason: null,
      });
    });

    it("an empty or over-bound witnessId / witnessName is rejected", () => {
      const emptyId = mutation((doc) => {
        doc.case.replayTruth.witnessDeceptions[0].witnessId = "";
      });
      expect(kindOf(emptyId)).toBe("invalid");
      const longName = mutation((doc) => {
        doc.case.replayTruth.witnessDeceptions[0].witnessName = "A".repeat(301);
      });
      expect(kindOf(longName)).toBe("invalid");
    });

    it("a malformed deceptionReason (non-string) is rejected; empty string and null are accepted (bounded-nullable)", () => {
      const numberReason = mutation((doc) => {
        doc.case.replayTruth.witnessDeceptions[0].deceptionReason = 42;
      });
      expect(kindOf(numberReason)).toBe("invalid");
      // An EMPTY string is a legal bounded-nullable reason (accepted and kept).
      const emptyReason = mutation((doc) => {
        doc.case.replayTruth.witnessDeceptions[0].deceptionReason = "";
      });
      const withEmpty = parseSavegameV1(emptyReason, utf8ByteLength(emptyReason));
      expect(withEmpty.replayTruth.witnessDeceptions?.[0].deceptionReason).toBe("");
      const nullReason = mutation((doc) => {
        doc.case.replayTruth.witnessDeceptions[0].deceptionReason = null;
      });
      const withNull = parseSavegameV1(nullReason, utf8ByteLength(nullReason));
      expect(withNull.replayTruth.witnessDeceptions?.[0].deceptionReason).toBeNull();
    });

    it("a non-object entry and an over-bounded list are rejected", () => {
      const notObject = mutation((doc) => {
        doc.case.replayTruth.witnessDeceptions[0] = "nope";
      });
      expect(kindOf(notObject)).toBe("invalid");
      const hugeList = mutation((doc) => {
        doc.case.replayTruth.witnessDeceptions = Array.from({ length: 33 }, (_, index) => ({
          witnessId: `witness_${index}`,
          witnessName: `Witness ${index}`,
          deceptionReason: null,
        }));
      });
      expect(kindOf(hugeList)).toBe("invalid");
    });
  });
});

describe("Phase 41 — witnessDeceptions surface post-reveal (never pre-reveal)", () => {
  it("a fresh replay bootstrap NEVER leaks witnessDeceptions or deceptionReason", () => {
    const state = new FreshReplayState(deceptiveDefinition());
    const serialized = JSON.stringify(state.freshBootstrap());
    expect(serialized).not.toContain("witnessDeceptions");
    expect(serialized).not.toContain("deceptionReason");
    expect(serialized).not.toContain("HIDE_UNRELATED_SECRET");
    expect(serialized).not.toContain("replayTruth");
  });

  it("the REPLAY REVEAL surfaces the deceptions in truth.witnessDeceptions (post-reveal)", () => {
    const state = new FreshReplayState(deceptiveDefinition());
    state.submitAccusation({
      resolutionType: "HOMICIDE",
      murdererId: "thomas_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
    });
    const reveal = state.getReveal();
    expect(reveal.result.overall).toBe("solved");
    expect(reveal.truth.witnessDeceptions).toEqual([
      { witnessId: "emily_reed", witnessName: "Emily Reed", deceptionReason: "HIDE_UNRELATED_SECRET" },
    ]);
  });

  it("a deception-free save reveals with NO witnessDeceptions key", () => {
    const definition = parseSavegameV1(CANONICAL_TEXT, utf8ByteLength(CANONICAL_TEXT));
    const state = new FreshReplayState(definition);
    state.submitAccusation({
      murdererId: "thomas_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
    });
    const reveal = state.getReveal();
    expect(reveal.truth.witnessDeceptions).toBeUndefined();
  });
});