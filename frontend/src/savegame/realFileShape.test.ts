import { afterEach, describe, expect, it, vi } from "vitest";
import { reExportV1, serializeSavegameV1 } from "./exportV1";
import realFileShape from "./fixtures/v1_real_file_shape.pdcase.json";
import { loadSavegameFile } from "./loadCase";
import { FreshReplayState } from "./replayRuntime";
import { activeReplay, clearReplay, startReplay } from "./replaySession";
import { parseSavegameV1, SavegameParseError, utf8ByteLength } from "./savegameV1";

/**
 * Phase32-Fix §9/§11 — the REAL-FILE-SHAPED regression.
 *
 * EVIDENCE GAP (documented): the physical supplied file
 * `procedural-detective-case-CASE-mhjHP3peZnxa(1).pdcase` (32,303 bytes,
 * exportedAt 2026-10-08T23:13:39Z, formatVersion 1) is NOT present anywhere
 * in this environment. This fixture is a SANITIZED SYNTHESIS from the exact
 * structural description in Phase32-Fix-RT §2/§9 — it is NOT the physical
 * byte file. Story text/names are neutered (spoiler-neutral); the structural
 * condition that triggered the defect is preserved verbatim:
 *
 *   - `case.publicCase.worldGraph.placements[*].evidenceId = null`
 *   - `case.scene.worldObjects[*].evidenceId = null`
 *   - `case.scene.worldObjects[7].subtype = null`
 *   - `case.witnesses[0].sceneObjectId = null` with `presence = REMOTE_STATEMENT`
 *   - realistic candidates/evidence/worldGraph/replayTruth nesting
 *
 * The same-version invariant must hold permanently (Phase32-Fix §10/§36):
 * exporter -> serialize -> JSON.parse -> production validator -> normalize ->
 * fresh replay — and the fresh replay starts with THE TRUTH hidden,
 * completion false and accusation none (Phase32-Fix §11/§12/§29).
 */

const REAL_TEXT: string = JSON.stringify(realFileShape);

function realDefinition(): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(REAL_TEXT, utf8ByteLength(REAL_TEXT));
}

/** A deterministic mutation of the real-file shape that exercises the
 *  STRING-evidenceId branch of the nullable contract (DEF-069 / ADV-32F-11):
 *  one placement AND its matching world object both carry the SAME published +
 *  recorded evidence id. Every other placement/worldObject stays null, so the
 *  all-null coverage of the base fixture is preserved. */
function realTextWithStringEvidenceLinkage(): string {
  const doc = JSON.parse(REAL_TEXT) as any;
  // placement[0] objectId 'kitchen_knife' <-> worldObject[4] objectId
  // 'kitchen_knife' — the same object, linked to the same evidence record.
  doc.case.publicCase.worldGraph.placements[0].evidenceId = "forensic_knife_match_01";
  doc.case.scene.worldObjects[4].evidenceId = "forensic_knife_match_01";
  return JSON.stringify(doc);
}

const SOLVED = {
  murdererId: "thomas_reed",
  motiveId: "cover_up_embezzlement",
  weaponId: "kitchen_knife",
  crimeTime: "22:17:00",
};

describe("Phase32-Fix §9 — real-file-shaped fixture structure", () => {
  it("preserves the exact defect-triggering structural condition", () => {
    const raw = realFileShape as any;
    // Top-level shape of the supplied file (Phase32-Fix §1).
    expect(Object.keys(raw).sort()).toEqual(["case", "exportedAt", "format", "formatVersion"]);
    expect(raw.format).toBe("procedural-detective-case");
    expect(raw.formatVersion).toBe(1);
    expect(raw.exportedAt).toBe("2026-10-08T23:13:39Z");
    // The four observed nullable characteristics.
    for (const placement of raw.case.publicCase.worldGraph.placements) {
      expect(placement.evidenceId).toBeNull();
    }
    for (const worldObject of raw.case.scene.worldObjects) {
      expect(worldObject.evidenceId).toBeNull();
    }
    expect(raw.case.scene.worldObjects[7].subtype).toBeNull();
    expect(raw.case.witnesses[0].presence).toBe("REMOTE_STATEMENT");
    expect(raw.case.witnesses[0].sceneObjectId).toBeNull();
  });

  it("parse -> validate -> normalize PASSES on the real-file shape", () => {
    const definition = realDefinition();
    expect(definition.formatVersion).toBe(1);
    expect(definition.metadata.source).toBe("generated");
    expect(definition.metadata.sourceCaseId).toBe("CASE-mhjHP3peZnxa");
    expect(definition.scene.worldObjects[7].subtype).toBeNull();
    expect(definition.witnesses[0].presence).toBe("REMOTE_STATEMENT");
    expect(definition.witnesses[0].sceneObjectId).toBeNull();
    // Cross-section integrity survived: every world object has a placement,
    // every placement object is published, every record is published.
    const placementIds = new Set(definition.publicCase.worldGraph.placements.map((p) => p.objectId));
    for (const worldObject of definition.scene.worldObjects) {
      expect(placementIds.has(worldObject.objectId)).toBe(true);
    }
  });

  it("is accepted through the real Load Case flow (file picker path)", async () => {
    const outcome = await loadSavegameFile({
      size: utf8ByteLength(REAL_TEXT),
      text: async () => REAL_TEXT,
    });
    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      expect(outcome.definition.metadata.sourceCaseId).toBe("CASE-mhjHP3peZnxa");
    }
  });
});

describe("Phase32-Fix §11/§12 — fresh replay semantics from the real-file shape", () => {
  afterEach(() => clearReplay());

  it("starts a FRESH replay: THE TRUTH hidden, completion false, accusation none", () => {
    const definition = realDefinition();
    const state = new FreshReplayState(definition);
    const bootstrap = state.freshBootstrap();
    // THE TRUTH is hidden at the start.
    expect(JSON.stringify(bootstrap)).not.toContain("replayTruth");
    expect(bootstrap.state).toBe("PLAYING");
    expect(bootstrap.playerKnowledge.discoveredEvidenceIds).toEqual([]);
    expect(bootstrap.playerKnowledge.readEvidenceIds).toEqual([]);
    // No accusation submitted yet.
    expect(state.lifecycleState()).toBe("PLAYING");
    expect(() => state.getReveal()).toThrow();
    // The imported caseId is display-only and has NO authority.
    expect(bootstrap.caseId).toBe("saved-replay");
    expect(bootstrap.playthroughId).toBe("saved-replay");
    expect(definition.metadata.sourceCaseId).not.toBe(bootstrap.caseId);
  });

  it("the exported archive is an honest spoiler archive but the replay re-projects fresh (no evidence linkage leaked)", () => {
    const definition = realDefinition();
    const state = new FreshReplayState(definition);
    const bootstrap = state.freshBootstrap();
    for (const worldObject of bootstrap.scene.worldObjects) {
      expect(worldObject.evidenceId).toBeNull();
      expect(worldObject.discovered).toBe(false);
      expect(worldObject.read).toBe(false);
    }
  });

  it("a correct accusation reveals the SAVED truth; a wrong one follows the existing rules", () => {
    // Wrong accusation behaves normally (accepted, reveal reports incorrect).
    const wrong = new FreshReplayState(realDefinition());
    wrong.submitAccusation({
      murdererId: "anna_karlsson",
      motiveId: "robbery_gone_wrong",
      weaponId: "scissors",
      crimeTime: "18:00:00",
    });
    expect(wrong.lifecycleState()).toBe("ACCUSED");
    const wrongReveal = wrong.getReveal();
    expect(wrongReveal.result.overall).toBe("incorrect");
    expect(wrongReveal.truth.murdererName).toBe("Suspect C"); // the SAVED truth, not a fresh solve
    // Correct accusation reaches THE TRUTH again.
    const correct = new FreshReplayState(realDefinition());
    correct.submitAccusation({ ...SOLVED });
    const correctReveal = correct.getReveal();
    expect(correctReveal.result.overall).toBe("solved");
    expect(correctReveal.truth.murdererName).toBe("Suspect C");
  });

  it("the same save can be loaded repeatedly, each time into a fresh replay", () => {
    const definition = realDefinition();
    const first = new FreshReplayState(definition);
    expect(first.lifecycleState()).toBe("PLAYING");
    // Re-load the SAME bytes: a second fresh state, never the previous
    // playthrough's knowledge.
    const second = new FreshReplayState(realDefinition());
    expect(second.lifecycleState()).toBe("PLAYING");
    expect(second.knowledgeSnapshot().discoveredEvidenceIds).toEqual([]);
    expect(second.freshBootstrap().state).toBe("PLAYING");
  });
});

describe("Phase32-Fix §10/§18 — production round-trip over the real-file shape", () => {
  afterEach(() => {
    clearReplay();
  });

  it("exporter -> serialize -> JSON.parse -> production validator -> normalize -> fresh replay", () => {
    const definition = realDefinition();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    // A real JSON round-trip (not a hand-shaped object): serialize -> parse.
    const parsed: unknown = JSON.parse(exported);
    const reloaded = parseSavegameV1(JSON.stringify(parsed), utf8ByteLength(exported));
    expect(reloaded.metadata.title).toBe(definition.metadata.title);
    expect(reloaded.replayTruth.murdererId).toBe(definition.replayTruth.murdererId);
    // Fresh replay state from the reloaded definition.
    const state = new FreshReplayState(reloaded);
    expect(state.freshBootstrap().state).toBe("PLAYING");
    expect(JSON.stringify(state.freshBootstrap())).not.toContain("replayTruth");
    // The null characteristics survive the full round trip.
    for (const worldObject of reloaded.scene.worldObjects) {
      expect(worldObject.evidenceId).toBeNull();
    }
    expect(reloaded.witnesses[0].sceneObjectId).toBeNull();
    // And the reloaded save replays to the saved truth.
    state.submitAccusation({ ...SOLVED });
    expect(state.getReveal().truth.murdererName).toBe("Suspect C");
  });

  it("DEF-069 — a string evidenceId linkage round-trips through export -> validator (null coverage kept)", () => {
    // The permanent drift guard must cover the STRING branch of the nullable
    // contract too (ADV-32F-11): a future drift in the string-evidenceId
    // acceptance path would otherwise be invisible to the real-file guard.
    const text = realTextWithStringEvidenceLinkage();
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.publicCase.worldGraph.placements[0].evidenceId).toBe("forensic_knife_match_01");
    expect(definition.scene.worldObjects[4].evidenceId).toBe("forensic_knife_match_01");
    // The CENTRAL invariant (Phase32-Fix §10): exporter -> serialize -> parse
    // -> production validator, now exercising the string-linkage branch.
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    const parsedBack: unknown = JSON.parse(exported);
    const reloaded = parseSavegameV1(JSON.stringify(parsedBack), utf8ByteLength(exported));
    expect(reloaded.publicCase.worldGraph.placements[0].evidenceId).toBe("forensic_knife_match_01");
    expect(reloaded.scene.worldObjects[4].evidenceId).toBe("forensic_knife_match_01");
    // The all-null branch is NOT weakened: 8/9 placements and 8/9 world
    // objects stay null through the round trip.
    expect(reloaded.publicCase.worldGraph.placements.filter((p) => p.evidenceId === null).length).toBe(8);
    expect(reloaded.scene.worldObjects.filter((w) => w.evidenceId === null).length).toBe(8);
  });

  it("zero provider / zero Bridge / zero generation quota on load + replay (§14)", async () => {
    const fetchSpy = vi.fn(async () => {
      throw new Error("network must never be used");
    });
    const previous = globalThis.fetch;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    try {
      const outcome = await loadSavegameFile({
        size: utf8ByteLength(REAL_TEXT),
        text: async () => REAL_TEXT,
      });
      expect(outcome.ok).toBe(true);
      if (outcome.ok) {
        startReplay(outcome.definition);
        expect(activeReplay()).not.toBeNull();
        const state = activeReplay()!.state;
        state.submitAccusation({ ...SOLVED });
        const reveal = state.getReveal();
        expect(reveal.result.overall).toBe("solved");
      }
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = previous;
    }
  });
});

describe("Phase32-Fix §5 — test/debug-only diagnostic shape", () => {
  it("a null where a non-nullable string is required yields path/reason/expected/actualType", () => {
    const doc = JSON.parse(REAL_TEXT);
    // metadata.title is required non-null: null must fail closed.
    doc.case.metadata.title = null;
    const text = JSON.stringify(doc);
    try {
      parseSavegameV1(text, utf8ByteLength(text));
      throw new Error("expected rejection");
    } catch (error) {
      expect(error).toBeInstanceOf(SavegameParseError);
      if (error instanceof SavegameParseError) {
        const diagnostic = error.diagnostic;
        expect(diagnostic).toBeDefined();
        expect(diagnostic!.path).toBe("case.metadata.title");
        expect(diagnostic!.reasonCode).toBe("TYPE_MISMATCH");
        expect(diagnostic!.expected).toBe("string");
        expect(diagnostic!.actualType).toBe("null");
      }
    }
  });

  it("the production UI message stays the frozen bounded copy (never the diagnostic)", async () => {
    const doc = JSON.parse(REAL_TEXT);
    doc.case.witnesses[0].sceneObjectId = 42;
    const text = JSON.stringify(doc);
    const outcome = await awaitLoadOutcome(text);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.message).toBe("This file is not a valid Procedural Detective savegame.");
      // No raw schema text, no path, no file contents in the player message.
      expect(outcome.message).not.toContain("sceneObjectId");
      expect(outcome.message).not.toContain("case.");
      expect(outcome.message).not.toContain("42");
    }
  });
});

async function awaitLoadOutcome(text: string): Promise<{ ok: boolean; message: string }> {
  const outcome = await loadSavegameFile({ size: utf8ByteLength(text), text: async () => text });
  return { ok: outcome.ok, message: outcome.ok ? "" : outcome.message };
}
