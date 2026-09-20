import { describe, expect, it } from "vitest";
import { makeRevealResponse } from "../scene/testFixtures";
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