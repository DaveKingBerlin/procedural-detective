import { describe, expect, it } from "vitest";
import { makeRevealResponse, makeSuicideReveal } from "../scene/testFixtures";
import { parseRevealResponse } from "./revealValidation";

/**
 * Trust-boundary parsing of the frozen RevealResponse DTO (Phase 7 E/P):
 * the parser allowlists exact fields, DROPS everything else (hidden proof,
 * aliases, diagnostics), and rejects malformed payloads loudly instead of
 * silently coercing them.
 */

describe("parseRevealResponse", () => {
  it("parses a canned reveal into the typed allowlist", () => {
    const parsed = parseRevealResponse(makeRevealResponse());
    expect(parsed.status).toBe("REVEALED");
    expect(parsed.truth.murdererName).toBe("Ada Marsh");
    expect(parsed.result.overall).toBe("solved");
    expect(parsed.score).toEqual({ correctDimensions: 4, totalDimensions: 4 });
    expect(parsed.timeline).toHaveLength(3);
    expect(parsed.explanation.evidence).toHaveLength(2);
  });

  it("drops unknown fields — including smuggled truth/proof material", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    const withSmuggled = {
      ...raw,
      solutionProof: { winners: ["suspect_alpha"], acceptedScoring: { toleranceSeconds: 300 } },
      internalDiagnostics: { ingestionProvider: "x", generationAttemptId: "gen-1" },
      truth: {
        ...(raw.truth as object),
        verifierToken: "secret",
        huggingfaceId: "hf_xyz",
      } as object,
      timeline: [...(raw.timeline as object[])],
    };
    const parsed = parseRevealResponse(withSmuggled);
    expect("solutionProof" in parsed).toBe(false);
    expect("internalDiagnostics" in parsed).toBe(false);
    expect(Object.keys(parsed.truth).includes("verifierToken")).toBe(false);
    expect(Object.keys(parsed.truth).includes("huggingfaceId")).toBe(false);
    expect(parsed.explanation.evidence.every((entry) => Object.keys(entry).sort().join(",") === "evidenceId,point,title")).toBe(
      true,
    );
  });

  it("rejects a status other than REVEALED", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    raw.status = "ACCUSED";
    expect(() => parseRevealResponse(raw)).toThrow(/REVEALED/);
  });

  it("rejects a missing truth block", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    delete raw.truth;
    expect(() => parseRevealResponse(raw)).toThrow(/truth/);
  });

  it("rejects an invalid result.overall value", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    const result = raw.result as Record<string, unknown>;
    result.overall = "partially";
    expect(() => parseRevealResponse(raw)).toThrow(/overall/);
  });

  it("rejects a non-array timeline and non-string timeline entries", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    raw.timeline = [{ time: 1, description: "x" }];
    expect(() => parseRevealResponse(raw)).toThrow(/timeline/);
  });

  it("rejects malformed explanation.evidence entries", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    const explanation = raw.explanation as Record<string, unknown>;
    explanation.evidence = [{ evidenceId: "e1", title: "t" }]; // missing point
    expect(() => parseRevealResponse(raw)).toThrow(/point/);
  });

  it("rejects non-object payloads", () => {
    for (const bad of ["nope", null, 42, [1, 2]]) {
      expect(() => parseRevealResponse(bad)).toThrow(/object/);
    }
  });

  it("is deterministic: identical raw payloads parse to identical objects", () => {
    expect(parseRevealResponse(makeRevealResponse())).toEqual(parseRevealResponse(makeRevealResponse()));
  });
});

describe("parseRevealResponse — Phase 41 resolution-aware truth", () => {
  it("parses a SUICIDE truth with nullable murdererId/murdererName", () => {
    const parsed = parseRevealResponse(makeSuicideReveal());
    expect(parsed.truth.resolutionType).toBe("SUICIDE");
    expect(parsed.truth.murdererId).toBeNull();
    expect(parsed.truth.murdererName).toBeNull();
    expect(parsed.player.accusation.resolutionType).toBe("SUICIDE");
    expect(parsed.player.accusation.murdererId).toBeNull();
    expect(parsed.truth.motiveLabel).toBe("Jealousy toward the victim");
  });

  it("requires resolutionType on the truth (fail closed when missing)", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    const truth = raw.truth as Record<string, unknown>;
    delete truth.resolutionType;
    expect(() => parseRevealResponse(raw)).toThrow(/resolutionType/);
  });

  it("rejects an unknown resolutionType value", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    (raw.truth as Record<string, unknown>).resolutionType = "ACCIDENT";
    expect(() => parseRevealResponse(raw)).toThrow(/resolutionType/);
  });

  it("a HOMICIDE truth rejects a null murdererId (nullable ONLY for SUICIDE)", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    (raw.truth as Record<string, unknown>).murdererId = null;
    expect(() => parseRevealResponse(raw)).toThrow(/murdererId/);
  });

  it("a SUICIDE truth rejects a non-null murdererId", () => {
    const raw = makeSuicideReveal() as unknown as Record<string, unknown>;
    (raw.truth as Record<string, unknown>).murdererId = "suspect_alpha";
    expect(() => parseRevealResponse(raw)).toThrow(/murdererId/);
  });

  it("a SUICIDE player accusation with a non-null murdererId is malformed", () => {
    const raw = makeSuicideReveal() as unknown as Record<string, unknown>;
    const accusation = (raw.player as Record<string, unknown>).accusation as Record<string, unknown>;
    accusation.murdererId = "suspect_alpha";
    expect(() => parseRevealResponse(raw)).toThrow(/murdererId/);
  });

  it("Phase 41 (ADV-41-03) — a REAL server-shaped SUICIDE reveal parses (resolutionType on player.accusation + null murderer)", () => {
    // The exact CORRECTED server DTO for a SUICIDE playthrough: the player
    // accusation block echoes `resolutionType:"SUICIDE"` + `murdererId:null`
    // (never ""), and the truth block carries the closed resolution with null
    // murderer labels. Pre-fix the strict parser rejected the ""/missing
    // resolution shapes this replaces.
    const serverReveal = JSON.parse(
      JSON.stringify({
        playthroughId: "PT-suicide-01",
        caseId: "CASE-suicide-01",
        caseVersion: 1,
        status: "REVEALED",
        truth: {
          resolutionType: "SUICIDE",
          murdererId: null,
          murdererName: null,
          motiveId: "motive_beta",
          motiveLabel: "Jealousy toward the victim",
          weaponId: "weapon_beta",
          weaponName: "Letter opener",
          crimeTime: "2026-09-11T21:45:00+02:00",
          witnessDeceptions: [
            { witnessId: "witness_cora", witnessName: "Cora Blake", deceptionReason: "HIDE_UNRELATED_SECRET" },
          ],
        },
        player: {
          accusation: {
            resolutionType: "SUICIDE",
            murdererId: null,
            motiveId: "motive_beta",
            weaponId: "weapon_beta",
            crimeTime: "21:45:00",
          },
        },
        result: {
          murdererCorrect: true,
          motiveCorrect: true,
          weaponCorrect: true,
          timeCorrect: true,
          overall: "solved",
        },
        score: { correctDimensions: 4, totalDimensions: 4 },
        timeline: [],
        explanation: { evidence: [], dimensions: { who: [], why: [], weapon: [], when: [] } },
      }),
    );
    const parsed = parseRevealResponse(serverReveal);
    expect(parsed.truth.resolutionType).toBe("SUICIDE");
    expect(parsed.truth.murdererId).toBeNull();
    expect(parsed.truth.murdererName).toBeNull();
    expect(parsed.player.accusation.resolutionType).toBe("SUICIDE");
    expect(parsed.player.accusation.murdererId).toBeNull();
    // The post-reveal deceptive-witness block survives the trust boundary.
    expect(parsed.truth.witnessDeceptions).toEqual([
      { witnessId: "witness_cora", witnessName: "Cora Blake", deceptionReason: "HIDE_UNRELATED_SECRET" },
    ]);
  });

  it("Phase 41 (ADV-41-03) — a HOMICIDE/LEGACY reveal keeps the unchanged player-accusation echo", () => {
    // A HOMICIDE/legacy reveal has NO resolutionType on the player block and a
    // NON-EMPTY murdererId; the parser must keep accepting it byte-identically.
    const parsed = parseRevealResponse(makeRevealResponse());
    expect("resolutionType" in parsed.player.accusation).toBe(false);
    expect(parsed.player.accusation.murdererId).toBe("suspect_alpha");
    const empty = makeRevealResponse() as unknown as Record<string, unknown>;
    const accusation = (empty.player as Record<string, unknown>).accusation as Record<string, unknown>;
    accusation.murdererId = ""; // the SUICIDE-era "" sentinel must NOT be accepted on a non-SUICIDE block
    expect(() => parseRevealResponse(empty)).toThrow(/murdererId/);
  });
});

describe("parseRevealResponse — Phase 18C optional dimensions block", () => {
  it("parses the server dimensions block into the typed allowlist when present", () => {
    const parsed = parseRevealResponse(makeRevealResponse());
    expect(parsed.explanation.dimensions).not.toBeNull();
    expect(parsed.explanation.dimensions?.who.map((entry) => entry.evidenceId)).toContain("record_generic_01");
    expect(parsed.explanation.dimensions?.when[0]).toEqual({
      evidenceId: "record_generic_03",
      title: "Hallway camera",
      point: "The camera log places the visitor at the door just before the crime.",
    });
  });

  it("an ABSENT dimensions block (older server) parses fine — never throws", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    const explanation = raw.explanation as Record<string, unknown>;
    delete explanation.dimensions;
    const parsed = parseRevealResponse(raw);
    expect(parsed.explanation.evidence).toHaveLength(2);
    expect(parsed.explanation.dimensions).toBeUndefined();
  });

  it("a MALFORMED dimensions block degrades to undefined — never throws, never leaks", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    const explanation = raw.explanation as Record<string, unknown>;
    // Wrong shapes per group:
    explanation.dimensions = {
      who: "nope",
      why: [{ evidenceId: 42, title: {}, point: null }],
      weapon: [null, "junk", { evidenceId: "e1", title: "t", point: "p", winner: "secret" }],
      when: [],
    };
    const parsed = parseRevealResponse(raw);
    expect(parsed.explanation.dimensions).toEqual({ who: [], why: [], weapon: [{
      evidenceId: "e1",
      title: "t",
      point: "p",
    }], when: [] });
    // The unknown `winner` field inside a point is dropped by the allowlist.
    expect(parsed.explanation.dimensions!.weapon[0]).toEqual({ evidenceId: "e1", title: "t", point: "p" });
  });

  it("a dimensions entry never carries unknown fields into the typed allowlist", () => {
    const raw = makeRevealResponse() as unknown as Record<string, unknown>;
    const explanation = raw.explanation as Record<string, unknown>;
    explanation.dimensions = {
      who: [{ evidenceId: "e1", title: "t", point: "p", winner: "x", hiddenTruth: "y" }],
      why: [],
      weapon: [],
      when: [],
    };
    const parsed = parseRevealResponse(raw);
    expect(Object.keys(parsed.explanation.dimensions!.who[0]).sort()).toEqual(["evidenceId", "point", "title"]);
  });
});

describe("parseRevealResponse — Phase 41 witnessDeceptions block (ADV-41-04)", () => {
  function withDeceptions(witnessDeceptions: unknown): unknown {
    const raw = makeSuicideReveal() as unknown as Record<string, unknown>;
    (raw.truth as Record<string, unknown>).witnessDeceptions = witnessDeceptions;
    return raw;
  }

  it("an ABSENT witnessDeceptions block (no liar / pre-41 server) parses fine — never throws", () => {
    const raw = makeSuicideReveal() as unknown as Record<string, unknown>;
    delete (raw.truth as Record<string, unknown>).witnessDeceptions;
    const parsed = parseRevealResponse(raw);
    expect(parsed.truth.witnessDeceptions).toBeUndefined();
  });

  it("a present block with valid entries is preserved (post-reveal only)", () => {
    const parsed = parseRevealResponse(
      withDeceptions([
        { witnessId: "witness_cora", witnessName: "Cora Blake", deceptionReason: "HIDE_UNRELATED_SECRET" },
        { witnessId: "witness_dan", witnessName: "Dan Wu", deceptionReason: null },
      ]),
    );
    expect(parsed.truth.witnessDeceptions).toEqual([
      { witnessId: "witness_cora", witnessName: "Cora Blake", deceptionReason: "HIDE_UNRELATED_SECRET" },
      { witnessId: "witness_dan", witnessName: "Dan Wu", deceptionReason: null },
    ]);
  });

  it("a malformed entry fails closed (never silently dropped)", () => {
    expect(() => parseRevealResponse(withDeceptions("nope"))).toThrow(/array/);
    expect(() => parseRevealResponse(withDeceptions([{ witnessId: "a" }]))).toThrow(/witnessName/);
    expect(() => parseRevealResponse(withDeceptions([{ witnessName: "B", witnessId: "a", deceptionReason: 42 }]))).toThrow(/deceptionReason/);
    expect(() => parseRevealResponse(withDeceptions([{ witnessName: "B", witnessId: "a", truthfulness: "DECEPTIVE" }]))).toThrow(/not an allowed key/);
  });

  it("an absent deceptionReason is legal (the reason is OPTIONAL on the server DTO)", () => {
    const parsed = parseRevealResponse(
      withDeceptions([{ witnessId: "witness_cora", witnessName: "Cora Blake" }]),
    );
    expect(parsed.truth.witnessDeceptions).toEqual([
      { witnessId: "witness_cora", witnessName: "Cora Blake", deceptionReason: null },
    ]);
  });
});