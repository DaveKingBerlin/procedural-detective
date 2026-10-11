import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { reExportV1, serializeSavegameV1 } from "./exportV1";
import suicideFixture from "./fixtures/v1_suicide_case.pdcase.json";
import witnessMurdererFixture from "./fixtures/v1_witness_murderer.pdcase.json";
import { FreshReplayState } from "./replayRuntime";
import { clearReplay, startReplay } from "./replaySession";
import { evaluateReplayAccusation } from "./replayTruth";
import { parseSavegameV1, utf8ByteLength } from "./savegameV1";

/**
 * Phase 41 — RESOLUTION-AWARE replay coverage (export -> import -> fresh
 * replay -> solve -> reveal -> re-export -> re-import with ZERO provider
 * calls), witness-murderer final-target selection, wrong-theory verdicts and
 * the pre-reveal no-leak sweep.
 *
 * Fixtures:
 *  - v1_suicide_case.pdcase.json — replayTruth `resolutionType:"SUICIDE"`,
 *    `murdererId:null`, candidates.finalTargets (suspects + witness),
 *    hasSuicideOption true, victimName "Sarah Miller";
 *  - v1_witness_murderer.pdcase.json — replayTruth `resolutionType:"HOMICIDE"`,
 *    murdererId "emily_reed" (a WITNESS who is a final target but NOT an
 *    investigative suspect).
 *
 * ZERO network/provider: replay services are in-memory; a throwing fetch spy
 * guards every sequence.
 */

const SUICIDE_TEXT: string = JSON.stringify(suicideFixture);
const WITNESS_TEXT: string = JSON.stringify(witnessMurdererFixture);

function suicideDefinition(): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(SUICIDE_TEXT, utf8ByteLength(SUICIDE_TEXT));
}

function witnessDefinition(): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(WITNESS_TEXT, utf8ByteLength(WITNESS_TEXT));
}

describe("Phase 41 — suicide savegame replay", () => {
  it("parse -> normalize: the suicide fixture keeps its resolution shape", () => {
    const definition = suicideDefinition();
    expect(definition.replayTruth.resolutionType).toBe("SUICIDE");
    expect(definition.replayTruth.murdererId).toBeNull();
    expect(definition.replayTruth.murdererName).toBeNull();
    expect(definition.candidates.hasSuicideOption).toBe(true);
    expect(definition.candidates.finalTargets?.map((t) => t.role)).toContain("witness");
    expect(definition.candidates.victimName).toBe("Sarah Miller");
    // The investigative suspects do NOT include the witness (Phase-40 cleanup).
    expect(definition.candidates.suspects.map((s) => s.id)).not.toContain("emily_reed");
  });

  it("the correct SUICIDE final theory wins (murdererId null accepted)", () => {
    const state = new FreshReplayState(suicideDefinition());
    const accepted = state.submitAccusation({
      resolutionType: "SUICIDE",
      murdererId: null,
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
    });
    expect(accepted.status).toBe("ACCUSED");
    const reveal = state.getReveal();
    expect(reveal.result.overall).toBe("solved");
    expect(reveal.result.murdererCorrect).toBe(true);
    expect(reveal.score).toEqual({ correctDimensions: 4, totalDimensions: 4 });
    expect(reveal.truth.resolutionType).toBe("SUICIDE");
    expect(reveal.truth.murdererId).toBeNull();
    expect(reveal.player.accusation.murdererId).toBeNull();
  });

  it("a WRONG homicide theory on a suicide truth is incorrect (wrong-theory verdict)", () => {
    const state = new FreshReplayState(suicideDefinition());
    state.submitAccusation({
      resolutionType: "HOMICIDE",
      murdererId: "thomas_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
    });
    const reveal = state.getReveal();
    expect(reveal.result.murdererCorrect).toBe(false);
    expect(reveal.result.overall).toBe("incorrect");
    expect(reveal.score.correctDimensions).toBe(3); // why/weapon/when still correct
  });

  it("a SUICIDE theory on a case without the suicide option is rejected 422 (mirror of the server)", () => {
    const doc = JSON.parse(SUICIDE_TEXT) as any;
    doc.case.candidates.hasSuicideOption = false;
    const definition = parseSavegameV1(JSON.stringify(doc), utf8ByteLength(JSON.stringify(doc)));
    const state = new FreshReplayState(definition);
    try {
      state.submitAccusation({
        resolutionType: "SUICIDE",
        murdererId: null,
        motiveId: "cover_up_embezzlement",
        weaponId: "kitchen_knife",
        crimeTime: "22:17:00",
      });
      expect.unreachable("should have thrown 422");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
    }
    expect(state.lifecycleState()).toBe("PLAYING");
  });

  it("export -> import -> solve -> reveal -> re-export -> re-import round-trips with ZERO fetch calls", () => {
    const fetchSpy = vi.fn(async () => {
      throw new Error("network must never be used during a replay round-trip");
    });
    const previous = globalThis.fetch;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    try {
      const definition = suicideDefinition();
      const state = new FreshReplayState(definition);
      state.submitAccusation({
        resolutionType: "SUICIDE",
        murdererId: null,
        motiveId: "cover_up_embezzlement",
        weaponId: "kitchen_knife",
        crimeTime: "22:17:00",
      });
      const reveal = state.getReveal();
      expect(reveal.result.overall).toBe("solved");
      // Re-export the (already revealed) definition and reload fresh.
      const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
      expect(exported).toContain('"resolutionType":"SUICIDE"');
      const reloaded = parseSavegameV1(exported, utf8ByteLength(exported));
      expect(reloaded.replayTruth.resolutionType).toBe("SUICIDE");
      expect(reloaded.replayTruth.murdererId).toBeNull();
      const again = new FreshReplayState(reloaded);
      expect(again.freshBootstrap().state).toBe("PLAYING");
      expect(JSON.stringify(again.freshBootstrap())).not.toContain("replayTruth");
      again.submitAccusation({
        resolutionType: "SUICIDE",
        murdererId: null,
        motiveId: "cover_up_embezzlement",
        weaponId: "kitchen_knife",
        crimeTime: "22:17:00",
      });
      expect(again.getReveal().result.overall).toBe("solved");
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = previous;
    }
  });
});

describe("Phase 41 — witness-murderer replay", () => {
  it("the witness murderer is a FINAL homicide target but NOT an investigative suspect", () => {
    const definition = witnessDefinition();
    expect(definition.replayTruth.resolutionType).toBe("HOMICIDE");
    expect(definition.replayTruth.murdererId).toBe("emily_reed");
    const suspectIds = definition.candidates.suspects.map((s) => s.id);
    expect(suspectIds).not.toContain("emily_reed");
    expect(definition.candidates.finalTargets?.map((t) => t.id)).toContain("emily_reed");
  });

  it("accusing the WITNESS (a final target, not a suspect) wins", () => {
    const state = new FreshReplayState(witnessDefinition());
    state.submitAccusation({
      resolutionType: "HOMICIDE",
      murdererId: "emily_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
    });
    const reveal = state.getReveal();
    expect(reveal.result.overall).toBe("solved");
    expect(reveal.truth.murdererName).toBe("Emily Reed");
    expect(reveal.result.murdererCorrect).toBe(true);
  });

  it("a person outside the FINAL target universe is rejected 422 even if the id exists", () => {
    const state = new FreshReplayState(witnessDefinition());
    try {
      state.submitAccusation({
        resolutionType: "HOMICIDE",
        murdererId: "david_kim", // a published person (role family) but NOT a target
        motiveId: "cover_up_embezzlement",
        weaponId: "kitchen_knife",
        crimeTime: "22:17:00",
      });
      expect.unreachable("should have thrown 422");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
    }
  });

  it("re-export of a witness-murderer save preserves the final-target truth", () => {
    const definition = witnessDefinition();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    const reloaded = parseSavegameV1(exported, utf8ByteLength(exported));
    expect(reloaded.replayTruth.murdererId).toBe("emily_reed");
    expect(reloaded.candidates.finalTargets?.length).toBe(4);
  });

  it("the witness murderer's DECEPTION is preserved by re-export and surfaces ONLY at reveal (ADV-41-04)", () => {
    // A real export of a witness-murderer case carries the OPTIONAL
    // witnessDeceptions list (the lying witness IS the killer, SELF_PROTECTION).
    const definition = witnessDefinition();
    expect(definition.replayTruth.witnessDeceptions).toEqual([
      { witnessId: "emily_reed", witnessName: "Emily Reed", deceptionReason: "SELF_PROTECTION" },
    ]);
    // Pre-reveal: the fresh bootstrap never leaks the deception list.
    const state = new FreshReplayState(definition);
    const bootstrap = JSON.stringify(state.freshBootstrap());
    expect(bootstrap).not.toContain("witnessDeceptions");
    expect(bootstrap).not.toContain("SELF_PROTECTION");
    // Post-reveal: the deceptions surface exactly like the server reveal DTO.
    state.submitAccusation({
      resolutionType: "HOMICIDE",
      murdererId: "emily_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
    });
    const reveal = state.getReveal();
    expect(reveal.truth.witnessDeceptions).toEqual([
      { witnessId: "emily_reed", witnessName: "Emily Reed", deceptionReason: "SELF_PROTECTION" },
    ]);
  });
});

describe("Phase 41 — wrong-theory verdicts (§21)", () => {
  function truth(overrides: Partial<Parameters<typeof evaluateReplayAccusation>[0]> = {}) {
    return {
      resolutionType: "HOMICIDE" as "HOMICIDE" | "SUICIDE",
      murdererId: "thomas_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "2026-09-11T22:17:00+02:00",
      accusationToleranceSeconds: 300,
      murdererName: "Thomas Reed",
      motiveLabel: "Cover up an embezzlement",
      weaponName: "Kitchen Knife",
      ...overrides,
    };
  }
  function accusation(overrides: Partial<Parameters<typeof evaluateReplayAccusation>[1]> = {}) {
    return {
      resolutionType: "HOMICIDE" as "HOMICIDE" | "SUICIDE",
      murdererId: "thomas_reed" as string | null,
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
      ...overrides,
    };
  }

  it("homicide truth + winning person => correct; wrong person or suicide => incorrect", () => {
    expect(evaluateReplayAccusation(truth(), accusation()).murdererCorrect).toBe(true);
    expect(
      evaluateReplayAccusation(truth(), accusation({ murdererId: "anna_karlsson" })).murdererCorrect,
    ).toBe(false);
    expect(
      evaluateReplayAccusation(truth(), accusation({ resolutionType: "SUICIDE", murdererId: null }))
        .murdererCorrect,
    ).toBe(false);
  });

  it("suicide truth + suicide => correct; any homicide person => incorrect", () => {
    const suicideTruth = truth({ resolutionType: "SUICIDE", murdererId: null, murdererName: null });
    expect(
      evaluateReplayAccusation(suicideTruth, accusation({ resolutionType: "SUICIDE", murdererId: null }))
        .murdererCorrect,
    ).toBe(true);
    expect(
      evaluateReplayAccusation(
        suicideTruth,
        accusation({ murdererId: "thomas_reed" }), // the "winning" person is wrong on a suicide truth
      ).murdererCorrect,
    ).toBe(false);
  });

  it("a legacy accusation body (no resolutionType) is HOMICIDE", () => {
    expect(
      evaluateReplayAccusation(
        truth(),
        { resolutionType: undefined, murdererId: "thomas_reed", motiveId: "cover_up_embezzlement", weaponId: "kitchen_knife", crimeTime: "22:17:00" },
      ).murdererCorrect,
    ).toBe(true);
  });
});

describe("Phase 41 — pre-reveal NO-LEAK sweep", () => {
  const LEAK_TERMS = [
    "resolutionType",
    "truthfulness",
    "deceptionReason",
    "deceptive",
    "correctOptionIndex",
    "trueMurderer",
    "winner",
  ];

  it("the fresh replay bootstrap (incl. final targets) leaks none of the hidden terms", () => {
    for (const definition of [suicideDefinition(), witnessDefinition()]) {
      const state = new FreshReplayState(definition);
      const serialized = JSON.stringify(state.freshBootstrap());
      for (const term of LEAK_TERMS) {
        expect(serialized.toLowerCase()).not.toContain(term);
      }
      // Public affordances survive (integrity of the addition).
      expect(serialized).toContain("hasSuicideOption");
      expect(serialized).toContain("finalTargets");
    }
  });

  it("the accusation submission echo leaks only the player's OWN resolution (never the truth)", () => {
    const state = new FreshReplayState(suicideDefinition());
    const accepted = state.submitAccusation({
      resolutionType: "SUICIDE",
      murdererId: null,
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
    });
    const serialized = JSON.stringify(accepted);
    for (const term of ["truth", "murdererName", "motiveLabel", "weaponName", "correct"]) {
      expect(serialized.toLowerCase()).not.toContain(term);
    }
    // The player's submitted resolution type IS their own choice (safe).
    expect(serialized).toContain('"resolutionType":"SUICIDE"');
  });

  it("no safe-count telemetry in the candidate DTO reveals the correct option index", () => {
    const candidates = suicideDefinition().candidates;
    expect(candidates.hasSuicideOption).toBe(true);
    expect(candidates.finalTargets?.length).toBeGreaterThan(0);
    // No per-option correctness/winner marker exists anywhere in the DTO.
    expect(JSON.stringify(candidates)).not.toMatch(/correct|winner|index/i);
  });

  afterEach(() => {
    clearReplay();
  });

  it("startReplay keeps the same Phase-39 semantics: interview progress never serialized", () => {
    const replay = startReplay(suicideDefinition());
    replay.state.interviewWitness("emily_reed", "OBSERVATION");
    // The asked/progress lives in the in-memory FreshReplayState, never
    // serialized into a re-export.
    const exported = serializeSavegameV1(reExportV1(replay.definition, "2099-01-01T00:00:00Z"));
    expect(exported).not.toContain("askedWitnessStatements");
    expect(exported).not.toContain("interviewProgress");
  });
});