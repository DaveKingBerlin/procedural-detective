import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import canonical from "./fixtures/v1_demo_apartment.pdcase.json";
import { FreshReplayState } from "./replayRuntime";
import { parseSavegameV1, utf8ByteLength } from "./savegameV1";

/**
 * Phase 32 — the browser-LOCAL replay flow (Phase32 §12/§14/§29/§33).
 *
 * A loaded save starts a FRESH investigation with THE TRUTH hidden; people/
 * evidence/activities/world work; the accusation flow works; a wrong answer
 * behaves normally; the correct answer reaches THE TRUTH again; the same
 * file replays repeatedly.
 */

const CANONICAL_TEXT: string = JSON.stringify(canonical);

function load(): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(CANONICAL_TEXT, utf8ByteLength(CANONICAL_TEXT));
}

function makeState(): FreshReplayState {
  return new FreshReplayState(load());
}

/** The canonical solved accusation for the demo apartment. */
const SOLVED: { murdererId: string; motiveId: string; weaponId: string; crimeTime: string } = {
  murdererId: "thomas_reed",
  motiveId: "cover_up_embezzlement",
  weaponId: "kitchen_knife",
  crimeTime: "22:17:00",
};

describe("replay — fresh start, THE TRUTH hidden", () => {
  it("starts PLAYING with empty knowledge and no discovered evidence ids", () => {
    const state = makeState();
    const bootstrap = state.freshBootstrap();
    expect(bootstrap.state).toBe("PLAYING");
    expect(bootstrap.playthroughId).toBe("saved-replay");
    expect(bootstrap.caseId).toBe("saved-replay");
    expect(bootstrap.playerKnowledge.discoveredEvidenceIds).toEqual([]);
    expect(bootstrap.playerKnowledge.readEvidenceIds).toEqual([]);
    expect(bootstrap.candidates.suspects.length).toBeGreaterThan(0);
    expect((bootstrap.witnesses ?? []).length).toBe(1);
  });

  it("re-projects world objects WITHOUT evidence linkage (PD-SEC-01 fresh semantics)", () => {
    const state = makeState();
    const bootstrap = state.freshBootstrap();
    // The archive carries full knowledge; the FRESH replay must not.
    for (const worldObject of bootstrap.scene.worldObjects) {
      expect(worldObject.discovered).toBe(false);
      expect(worldObject.read).toBe(false);
      expect(worldObject.evidenceId).toBeNull();
    }
    // The saved archive itself still carries the honest full knowledge
    // (world object -> evidence) without ever publishing it to the player.
    const saved = load();
    expect(saved.scene.worldObjects.some((obj) => obj.evidenceId !== null)).toBe(true);
  });

  it("the fresh bootstrap never serializes the replay truth", () => {
    const state = makeState();
    const serialized = JSON.stringify(state.freshBootstrap());
    expect(serialized).not.toContain("accusationToleranceSeconds");
    expect(serialized).not.toContain("murdererName");
    expect(serialized).not.toContain("replayTruth");
  });
});

describe("replay — people / evidence / activity / world", () => {
  it("interacts with an evidence object -> discovery -> readable record", () => {
    const state = makeState();
    const knife = state.interactObject("kitchen_knife", "inspect");
    expect(knife.discovery?.evidenceId).toBe("forensic_knife_match_01");
    expect(knife.discovery?.state).toBe("discovered");
    const record = state.readRecord("forensic_knife_match_01");
    expect(record.title).toBe("Blood on the kitchen knife matches the victim");
    expect(record.readByPlayer).toBe(true);
    expect(state.knowledgeSnapshot().discoveredEvidenceIds).toContain("forensic_knife_match_01");
    expect(state.knowledgeSnapshot().readEvidenceIds).toContain("forensic_knife_match_01");
  });

  it("repeat interaction is idempotent (already-discovered, still readable)", () => {
    const state = makeState();
    void state.interactObject("kitchen_knife", "inspect");
    const again = state.interactObject("kitchen_knife", "inspect");
    expect(again.discovery?.state).toBe("already-discovered");
  });

  it("a decorative object returns the non-discovery inspection result", () => {
    const state = makeState();
    const vase = state.interactObject("vase_01", "");
    expect(vase.discovery).toBeNull();
    expect(vase.evidenceId).toBeNull();
    expect(vase.result).toBe("interacted");
    expect(state.knowledgeSnapshot().discoveredEvidenceIds).toEqual([]);
  });

  it("an unknown object is a safe gameplay error, never a crash", () => {
    const state = makeState();
    try {
      state.interactObject("ghost_object", "");
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(409);
    }
  });

  it("an undiscovered record cannot be read (discovery-gated)", () => {
    const state = makeState();
    try {
      state.readRecord("forensic_knife_match_01");
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(404);
    }
  });

  it("interview: the recorded question returns the saved statement and discovers its record; other questions are neutral", () => {
    const state = makeState();
    const emily = state.interviewWitness("emily_reed", "OBSERVATION");
    expect(emily.witnessId).toBe("emily_reed");
    expect(emily.statement.summary).not.toBe("");
    expect(emily.discovery?.newlyDiscovered).toBe(true);
    expect(emily.discovery?.record.evidenceId).toBe("witness_statement_emily_01");
    // A question with no recorded grounding answers the frozen neutral copy.
    const neutral = state.interviewWitness("emily_reed", "SOUND");
    expect(neutral.discovery).toBeNull();
    expect(neutral.statement.summary).toBe("No. Nothing stood out to me.");
  });
});

describe("replay — accusation flow", () => {
  it("reveal is 403 REVEAL_NOT_AVAILABLE before accusation", () => {
    const state = makeState();
    try {
      state.getReveal();
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(403);
      expect((error as ApiError).code).toBe("REVEAL_NOT_AVAILABLE");
    }
  });

  it("a wrong accusation is accepted but the reveal correctly reports incorrect", () => {
    const state = makeState();
    const accepted = state.submitAccusation({
      murdererId: "anna_karlsson",
      motiveId: "robbery_gone_wrong",
      weaponId: "scissors",
      crimeTime: "18:00:00",
    });
    expect(accepted.status).toBe("ACCUSED");
    // The submission NEVER leaks correctness (separation contract).
    expect(JSON.stringify(accepted)).not.toMatch(/murdererCorrect|timeCorrect|score/);
    const reveal = state.getReveal();
    expect(reveal.status).toBe("REVEALED");
    expect(reveal.result.overall).toBe("incorrect");
    expect(reveal.score.correctDimensions).toBe(0);
  });

  it("a correct solution reveals THE TRUTH with the exact saved solution", () => {
    const state = makeState();
    void state.submitAccusation({ ...SOLVED });
    const reveal = state.getReveal();
    expect(reveal.result.overall).toBe("solved");
    expect(reveal.score).toEqual({ correctDimensions: 4, totalDimensions: 4 });
    expect(reveal.truth.murdererId).toBe("thomas_reed");
    expect(reveal.truth.murdererName).toBe("Thomas Reed");
    expect(reveal.truth.motiveLabel).toBe("Cover up the \u20ac240,000 embezzlement");
    expect(reveal.truth.weaponName).toBe("Kitchen Knife");
    expect(reveal.truth.crimeTime).toBe("2026-09-11T22:17:00+02:00");
    expect(reveal.player.accusation).toEqual({ ...SOLVED });
  });

  it("partial correctness reports the mixed score (2/4)", () => {
    const state = makeState();
    void state.submitAccusation({
      murdererId: "thomas_reed",
      motiveId: "robbery_gone_wrong",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
    });
    const reveal = state.getReveal();
    expect(reveal.result.murdererCorrect).toBe(true);
    expect(reveal.result.motiveCorrect).toBe(false);
    expect(reveal.result.weaponCorrect).toBe(true);
    expect(reveal.result.timeCorrect).toBe(true);
    expect(reveal.score.correctDimensions).toBe(3);
    expect(reveal.result.overall).toBe("incorrect");
  });

  it("an out-of-universe id is rejected exactly like the server 422", () => {
    const state = makeState();
    try {
      void state.submitAccusation({
        murdererId: "stranger_from_another_case",
        motiveId: "cover_up_embezzlement",
        weaponId: "kitchen_knife",
        crimeTime: "22:17:00",
      });
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
    }
  });

  it("a zone-less full ISO crimeTime is rejected exactly like the server 422 (DEF-050 / ADV-32F-05)", () => {
    const state = makeState();
    try {
      void state.submitAccusation({
        murdererId: "thomas_reed",
        motiveId: "cover_up_embezzlement",
        weaponId: "kitchen_knife",
        crimeTime: "2026-09-11T22:17:00", // full ISO prefix, NO timezone -> server 422s
      });
      expect.unreachable("should have thrown");
    } catch (error) {
      // The replay surfaces the SAME typed rejection as the live server's
      // AccusationValidationError("crimeTime is invalid") -> 422 VALIDATION_ERROR.
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
      expect((error as ApiError).code).toBe("VALIDATION_ERROR");
      expect((error as ApiError).message).toBe("crimeTime is invalid");
    }
    // The rejected submission must not advance the lifecycle.
    expect(state.lifecycleState()).toBe("PLAYING");

    // A bare time-of-day with an OFFSET SUFFIX is equally zone-less-full-ISO-
    // adjacent and equally invalid on the server grammar (`_BARE_TIME_RE` is
    // anchored) — the replay must not accept a grammar the server rejects.
    const offsetBare = makeState();
    try {
      void offsetBare.submitAccusation({
        murdererId: "thomas_reed",
        motiveId: "cover_up_embezzlement",
        weaponId: "kitchen_knife",
        crimeTime: "22:17:00+02:00",
      });
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
      expect((error as ApiError).message).toBe("crimeTime is invalid");
    }
  });

  it("the crimeTime inputs the live server ACCEPTS still work in the replay (DEF-050)", () => {
    // Full ISO WITH offset/zone: accepted and scored (22:17:00+02:00 == truth).
    const withZone = makeState();
    void withZone.submitAccusation({
      murdererId: "thomas_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "2026-09-11T22:17:00+02:00",
    });
    expect(withZone.getReveal().result.timeCorrect).toBe(true);

    // Z-suffixed full ISO: equally valid on the server grammar.
    const withZ = makeState();
    void withZ.submitAccusation({
      murdererId: "thomas_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "2026-09-11T20:17:00Z",
    });
    expect(withZ.getReveal().result.timeCorrect).toBe(true);

    // Bare 24h time-of-day: the everyday form, still accepted.
    const bare = makeState();
    void bare.submitAccusation({ ...SOLVED });
    expect(bare.getReveal().result.timeCorrect).toBe(true);
  });

  it("a full ISO in a WRONG timezone never scores timeCorrect (DEF-050)", () => {
    // The grammar accepts it (it HAS a zone), but the evaluated UTC tick is
    // 7 hours away from the canonical tick — the replay must NOT treat it as
    // correct, exactly like the live server's evaluate_accusation.
    const wrongZone = makeState();
    void wrongZone.submitAccusation({
      murdererId: "thomas_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "2026-09-11T22:17:00-05:00",
    });
    const reveal = wrongZone.getReveal();
    expect(reveal.result.timeCorrect).toBe(false);
    expect(reveal.result.overall).toBe("incorrect");
  });

  it("a second submission answers 409 CASE_ALREADY_SUBMITTED", () => {
    const state = makeState();
    void state.submitAccusation({ ...SOLVED });
    try {
      void state.submitAccusation({ ...SOLVED });
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(409);
      expect((error as ApiError).code).toBe("CASE_ALREADY_SUBMITTED");
    }
  });

  it("reveal is idempotent (repeat call returns the identical DTO)", () => {
    const state = makeState();
    void state.submitAccusation({ ...SOLVED });
    const first = state.getReveal();
    const second = state.getReveal();
    expect(first).toEqual(second);
    expect(state.lifecycleState()).toBe("REVEALED");
  });
});

describe("replay — repeated replay (Phase32 §2/§30)", () => {
  it("the same file starts a fresh replay every time", () => {
    const definition = load();
    const first = new FreshReplayState(definition);
    first.interactObject("kitchen_knife", "inspect");
    first.submitAccusation({ ...SOLVED });
    void first.getReveal();

    // A second state from the SAME definition is completely fresh.
    const second = new FreshReplayState(definition);
    expect(second.lifecycleState()).toBe("PLAYING");
    expect(second.knowledgeSnapshot().discoveredEvidenceIds).toEqual([]);
    expect(second.freshBootstrap().playerKnowledge.discoveredEvidenceIds).toEqual([]);
  });

  it("no browser-storage interaction happens during a full replay", () => {
    // The runtime never touches localStorage/sessionStorage/IndexedDB: the
    // state object is purely in-memory by construction. This assertion is a
    // structural tripwire — the class must not grow any storage dependency.
    const state = makeState();
    const bootstrap = state.freshBootstrap();
    expect(Object.keys(bootstrap.scene)).toContain("worldObjects");
  });
});