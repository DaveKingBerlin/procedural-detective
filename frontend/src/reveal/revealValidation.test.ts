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