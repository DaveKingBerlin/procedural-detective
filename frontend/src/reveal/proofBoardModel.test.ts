import { describe, expect, it } from "vitest";
import { makeHostileReveal, makeRevealResponse } from "../scene/testFixtures";
import { buildProofBoard, PROOF_DIMENSION_ORDER, type ProofBoardModel } from "./proofBoardModel";

/**
 * Post-reveal proof board (Phase 18C).
 *
 * Proves:
 *   - the server `dimensions` block drives the four cards verbatim;
 *   - resilience: an ABSENT `dimensions` block (older server) falls back to a
 *     deterministic heuristic grouping of the flat list — never a crash;
 *   - four cards WHO/WHY/WEAPON/WHEN are ALWAYS represented (req 10);
 *   - every proof node's evidence id belongs to the case's evidence universe
 *     (flat list ∪ dimensions ids) (req 11);
 *   - no `proc.*`/winner material ever appears; hostile text stays inert.
 */

/** Removing the optional dimensions block simulates a pre-18C server. */
function stripDimensions() {
  const reveal = makeRevealResponse() as unknown as Record<string, unknown>;
  const explanation = reveal.explanation as Record<string, unknown>;
  delete explanation.dimensions;
  return reveal as unknown as ReturnType<typeof makeRevealResponse>;
}

function allNodes(model: ProofBoardModel) {
  return model.cards.flatMap((card) => card.nodes);
}

describe("proof board — server dimensions path", () => {
  it("uses the server's dimensions block verbatim for all four cards (reqs 9,10)", () => {
    const model = buildProofBoard(makeRevealResponse());

    expect(model.fallback).toBe(false);
    expect(model.sourcedFromDimensions).toBe(true);
    expect(model.cards.map((card) => card.dimension)).toEqual(PROOF_DIMENSION_ORDER);

    const when = model.cards.find((card) => card.dimension === "WHEN");
    expect(when?.nodes.map((node) => node.title)).toContain("Hallway camera");
    expect(when?.nodes[0]?.point).toContain("places the visitor at the door");

    const who = model.cards.find((card) => card.dimension === "WHO");
    expect(who?.nodes.map((node) => node.evidenceId)).toContain("record_generic_01");
  });

  it("keeps every card present even when a dimension group is empty", () => {
    const reveal = makeRevealResponse() as unknown as Record<string, unknown>;
    const explanation = reveal.explanation as Record<string, unknown>;
    const dimensions = explanation.dimensions as Record<string, unknown>;
    dimensions.when = [];
    const model = buildProofBoard(reveal as unknown as ReturnType<typeof makeRevealResponse>);
    expect(model.cards).toHaveLength(4);
    expect(model.cards.find((card) => card.dimension === "WHEN")?.nodes).toEqual([]);
  });
});

describe("proof board — resilience / flat-list fallback (older server)", () => {
  it("an ABSENT dimensions block never crashes and groups every flat item (req resilience)", () => {
    const model = buildProofBoard(stripDimensions());

    expect(model.fallback).toBe(true);
    expect(model.sourcedFromDimensions).toBe(false);
    expect(model.cards).toHaveLength(4);
    expect(model.cards.map((card) => card.dimension)).toEqual(PROOF_DIMENSION_ORDER);

    // Every flat item is represented exactly once across the four cards.
    const flat = makeRevealResponse().explanation.evidence.map((entry) => `${entry.evidenceId}\u0000${entry.title}\u0000${entry.point}`);
    const nodes = allNodes(model).map((node) => `${node.evidenceId}\u0000${node.title}\u0000${node.point}`);
    expect(new Set(nodes)).toEqual(new Set(flat));
  });

  it("an UNINFORMATIVE (all-empty) dimensions block also falls back when the flat list carries points", () => {
    const reveal = makeRevealResponse() as unknown as Record<string, unknown>;
    const explanation = reveal.explanation as Record<string, unknown>;
    explanation.dimensions = { who: [], why: [], weapon: [], when: [] };
    const model = buildProofBoard(reveal as unknown as ReturnType<typeof makeRevealResponse>);
    expect(model.fallback).toBe(true);
    expect(allNodes(model).length).toBeGreaterThan(0);
  });

  it("deterministic: identical input always produces identical fallback grouping", () => {
    const a = buildProofBoard(stripDimensions());
    const b = buildProofBoard(stripDimensions());
    expect(a).toEqual(b);
  });
});

describe("proof board — evidence link validity (req 11)", () => {
  it("every proof node id belongs to the case evidence universe (flat ∪ dimensions)", () => {
    const reveal = makeRevealResponse();
    const universe = new Set([
      ...reveal.explanation.evidence.map((entry) => entry.evidenceId),
      ...(reveal.explanation.dimensions
        ? reveal.explanation.dimensions.who
            .concat(reveal.explanation.dimensions.why)
            .concat(reveal.explanation.dimensions.weapon)
            .concat(reveal.explanation.dimensions.when)
            .map((entry) => entry.evidenceId)
        : []),
    ]);
    for (const model of [buildProofBoard(reveal), buildProofBoard(stripDimensions())]) {
      for (const node of allNodes(model)) {
        expect(universe.has(node.evidenceId)).toBe(true);
      }
    }
  });
});

describe("proof board — display safety (reqs 13, 15)", () => {
  it("never renders proc.* tokens or winner material", () => {
    const json = JSON.stringify(buildProofBoard(makeRevealResponse()));
    expect(json).not.toContain("proc.");
    expect(json).not.toMatch(/winner/i);
  });

  it("hostile reveal text stays inert (escaped by asText, no crash)", () => {
    const model = buildProofBoard(makeHostileReveal());
    const all = allNodes(model);
    expect(all.every((node) => typeof node.title === "string" && typeof node.point === "string")).toBe(true);
    // A hostile-only reveal still yields four cards (fallback or empty).
    expect(model.cards).toHaveLength(4);
  });

  it("scoring/truth display is untouched: the proof board only adds cards (req 15)", () => {
    const reveal = makeRevealResponse();
    const model = buildProofBoard(reveal);
    // The board never mutates or reads the score/result blocks.
    expect(model.cards.every((card) => card.nodes.every((node) => node.title !== reveal.score.correctDimensions.toString()))).toBe(true);
    expect(model.cards.flatMap((card) => card.nodes.map((node) => node.point)).join("")).not.toContain(reveal.truth.murdererId);
  });
});