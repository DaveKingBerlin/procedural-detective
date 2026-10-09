import { afterEach, describe, expect, it, vi } from "vitest";
import canonical from "./fixtures/v1_demo_apartment.pdcase.json";
import realInteraction from "./fixtures/v1_real_interaction_shape.pdcase.json";
import { reExportV1, serializeSavegameV1 } from "./exportV1";
import { loadSavegameFile } from "./loadCase";
import { FreshReplayState } from "./replayRuntime";
import { activeReplay, clearReplay, startReplay } from "./replaySession";
import {
  MAX_SHORT_TEXT_LENGTH,
  parseSavegameV1,
  SavegameParseError,
  savegameErrorMessage,
  utf8ByteLength,
  type SavegameParseDiagnostic,
} from "./savegameV1";

/**
 * Phase32-Fix2 §9/§11 — the FIELD-LEVEL contract matrix for the real
 * savegame's `interaction=""` surface (REQUIREMENTS §7 / §9 / §10 / §11).
 *
 * The real production file
 * `procedural-detective-case-CASE-cMjssq_s_xA6.pdcase` (32,442 B, §1) is NOT
 * present anywhere in this environment (searched workspace / Downloads /
 * Desktop / temp / whole C:\Users\Fujitsu tree) — see realFileShape.test.ts
 * and the phase report. The production parser `parseSavegameV1` ACCEPTS the
 * real structural characteristics on the available real exports
 * (`adv32_live_export.pdcase.json`, `qa_p32_backend_export.pdcase.json`) and
 * the committed `v1_real_file_shape.pdcase.json` — so the honest defect
 * outcome is CANNOT REPRODUCE with the physical file absent (brief §7).
 *
 * What this file PINS instead (the mandatory deliverables that were missing):
 *   - the exact `interaction` semantics on BOTH projections (placements[].interaction
 *     AND scene.worldObjects[].interaction): "" / "inspect" / "read" accepted,
 *     MISSING/null/number/object/array rejected, >MAX_SHORT_TEXT_LENGTH rejected;
 *   - the nullable/optional `evidenceId` matrix on both projections;
 *   - the `worldObjects[].subtype` DEF-068 tightening ("" rejected);
 *   - the `witnesses[].sceneObjectId` nullable matrix (REMOTE_STATEMENT null ok);
 *   - the NO-BLANKET-WEAKENING proof: every ID/empty-string-required sibling
 *     (objectId / witnessId / anchor / locationId / assetId / assetType) is
 *     still REJECTED on import;
 *   - the frozen bounded UI copy is unchanged.
 *
 * Every failing document is built by MUTATING the canonical fixture text and
 * fed to the REAL production `parseSavegameV1`; the assertions use the
 * test/debug-only `savegameParseDiagnostic` (§5) — never the UI message.
 */

const CANONICAL_TEXT: string = JSON.stringify(canonical);

function mutateDocument(mutate: (doc: any) => void): string {
  const doc = JSON.parse(CANONICAL_TEXT);
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

/** The §5 diagnostic of a rejected document (fails when it parses). */
function diagnosticOf(text: string): SavegameParseDiagnostic {
  try {
    parseSavegameV1(text, utf8ByteLength(text));
    throw new Error("expected rejection");
  } catch (error) {
    if (error instanceof SavegameParseError && error.diagnostic !== undefined) {
      return error.diagnostic;
    }
    throw error;
  }
}

/** A rejected document must also keep the frozen bounded UI message. */
function assertBoundedUIMessage(error: SavegameParseError): void {
  expect(savegameErrorMessage(error.kind)).toBe("This file is not a valid Procedural Detective savegame.");
}

describe("Phase32-Fix2 §9/§11 — placements[].interaction contract", () => {
  const at = "savegame.case.publicCase.worldGraph.placements[0].interaction";

  it("accepts interaction=\"\" (decorative placement, DEF-062)", () => {
    const text = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].interaction = ""));
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.publicCase.worldGraph.placements[0].interaction).toBe("");
  });

  it("accepts interaction=\"inspect\" and interaction=\"read\"", () => {
    const inspect = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].interaction = "inspect"));
    expect(parseSavegameV1(inspect, utf8ByteLength(inspect)).publicCase.worldGraph.placements[0].interaction).toBe(
      "inspect",
    );
    const read = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].interaction = "read"));
    expect(parseSavegameV1(read, utf8ByteLength(read)).publicCase.worldGraph.placements[0].interaction).toBe("read");
  });

  it("accepts interaction AT the documented bound (300 chars)", () => {
    const text = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].interaction = "i".repeat(300)));
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.publicCase.worldGraph.placements[0].interaction.length).toBe(300);
  });

  it("rejects a MISSING interaction (TYPE_MISMATCH)", () => {
    const text = mutateDocument((doc) => void delete doc.case.publicCase.worldGraph.placements[0].interaction);
    expect(kindOf(text)).toBe("invalid");
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe(at);
    expect(diagnostic.reasonCode).toBe("TYPE_MISMATCH");
  });

  it("rejects a null interaction (TYPE_MISMATCH)", () => {
    const text = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].interaction = null));
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe(at);
    expect(diagnostic.reasonCode).toBe("TYPE_MISMATCH");
    expect(diagnostic.actualType).toBe("null");
  });

  it("rejects number / object / array interactions (TYPE_MISMATCH)", () => {
    for (const value of [42, {}, []]) {
      const text = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].interaction = value));
      const diagnostic = diagnosticOf(text);
      expect(diagnostic.path).toBe(at);
      expect(diagnostic.reasonCode).toBe("TYPE_MISMATCH");
    }
  });

  it("rejects an interaction over MAX_SHORT_TEXT_LENGTH (301 chars) as BOUND_EXCEEDED", () => {
    const text = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].interaction = "a".repeat(301)));
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe(at);
    expect(diagnostic.reasonCode).toBe("BOUND_EXCEEDED");
    expect(diagnostic.expected).toBe(`string (<= ${MAX_SHORT_TEXT_LENGTH})`);
    expect(diagnostic.actualType).toBe("string");
  });
});

describe("Phase32-Fix2 §9/§11 — scene.worldObjects[].interaction contract", () => {
  const at = "savegame.case.scene.worldObjects[0].interaction";

  it("accepts interaction=\"\" on a world object (decorative, DEF-062)", () => {
    const text = mutateDocument((doc) => (doc.case.scene.worldObjects[0].interaction = ""));
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.scene.worldObjects[0].interaction).toBe("");
  });

  it("accepts interaction=\"inspect\" and interaction=\"read\" on a world object", () => {
    const inspect = mutateDocument((doc) => (doc.case.scene.worldObjects[0].interaction = "inspect"));
    expect(parseSavegameV1(inspect, utf8ByteLength(inspect)).scene.worldObjects[0].interaction).toBe("inspect");
    const read = mutateDocument((doc) => (doc.case.scene.worldObjects[0].interaction = "read"));
    expect(parseSavegameV1(read, utf8ByteLength(read)).scene.worldObjects[0].interaction).toBe("read");
  });

  it("accepts interaction AT the documented bound (300 chars) on a world object", () => {
    const text = mutateDocument((doc) => (doc.case.scene.worldObjects[0].interaction = "i".repeat(300)));
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.scene.worldObjects[0].interaction.length).toBe(300);
  });

  it("rejects a MISSING world-object interaction (TYPE_MISMATCH)", () => {
    const text = mutateDocument((doc) => void delete doc.case.scene.worldObjects[0].interaction);
    expect(kindOf(text)).toBe("invalid");
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe(at);
    expect(diagnostic.reasonCode).toBe("TYPE_MISMATCH");
  });

  it("rejects a null world-object interaction (TYPE_MISMATCH)", () => {
    const text = mutateDocument((doc) => (doc.case.scene.worldObjects[0].interaction = null));
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe(at);
    expect(diagnostic.reasonCode).toBe("TYPE_MISMATCH");
    expect(diagnostic.actualType).toBe("null");
  });

  it("rejects number / object / array world-object interactions (TYPE_MISMATCH)", () => {
    for (const value of [42, {}, []]) {
      const text = mutateDocument((doc) => (doc.case.scene.worldObjects[0].interaction = value));
      const diagnostic = diagnosticOf(text);
      expect(diagnostic.path).toBe(at);
      expect(diagnostic.reasonCode).toBe("TYPE_MISMATCH");
    }
  });

  it("rejects a world-object interaction over MAX_SHORT_TEXT_LENGTH (301 chars) as BOUND_EXCEEDED (import-scoped bound)", () => {
    // Phase32-Fix2: the shared live-game parser only type-checks interaction;
    // the savegame-import path bounds it at MAX_SHORT_TEXT_LENGTH so an
    // unbounded interaction can never enter the normalized archive (§22).
    const text = mutateDocument((doc) => (doc.case.scene.worldObjects[0].interaction = "a".repeat(301)));
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe(at);
    expect(diagnostic.reasonCode).toBe("BOUND_EXCEEDED");
    expect(diagnostic.expected).toBe(`string (<= ${MAX_SHORT_TEXT_LENGTH})`);
    expect(diagnostic.actualType).toBe("string");
  });
});

describe("Phase32-Fix2 §11 — evidenceId nullable/optional matrix", () => {
  it("placements[].evidenceId = null ACCEPTED", () => {
    const text = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].evidenceId = null));
    expect(parseSavegameV1(text, utf8ByteLength(text)).publicCase.worldGraph.placements[0].evidenceId).toBeNull();
  });

  it("placements[].evidenceId ABSENT is ACCEPTED (normalized to null — the validator's requireBoundedNullableString treats undefined as null; documented, not changed)", () => {
    const text = mutateDocument((doc) => void delete doc.case.publicCase.worldGraph.placements[0].evidenceId);
    expect(parseSavegameV1(text, utf8ByteLength(text)).publicCase.worldGraph.placements[0].evidenceId).toBeNull();
  });

  it("placements[].evidenceId = \"\" REJECTED (REFERENCE_MISMATCH — \"\" is never a published evidence id)", () => {
    const text = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].evidenceId = ""));
    expect(kindOf(text)).toBe("invalid");
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe("savegame.case.publicCase.worldGraph.placements[0].evidenceId");
    expect(diagnostic.reasonCode).toBe("REFERENCE_MISMATCH");
  });

  it("placements[].evidenceId = a published string ACCEPTED", () => {
    const text = mutateDocument((doc) => (doc.case.publicCase.worldGraph.placements[0].evidenceId = "forensic_knife_match_01"));
    expect(parseSavegameV1(text, utf8ByteLength(text)).publicCase.worldGraph.placements[0].evidenceId).toBe(
      "forensic_knife_match_01",
    );
  });

  it("worldObjects[].evidenceId = null ACCEPTED", () => {
    const text = mutateDocument((doc) => (doc.case.scene.worldObjects[0].evidenceId = null));
    expect(parseSavegameV1(text, utf8ByteLength(text)).scene.worldObjects[0].evidenceId).toBeNull();
  });

  it("worldObjects[].evidenceId ABSENT is ACCEPTED (PD-SEC-01: the backend strips it pre-reveal; documented, not changed)", () => {
    const text = mutateDocument((doc) => void delete doc.case.scene.worldObjects[0].evidenceId);
    expect(parseSavegameV1(text, utf8ByteLength(text)).scene.worldObjects[0].evidenceId).toBeNull();
  });

  it("worldObjects[].evidenceId = \"\" REJECTED (REFERENCE_MISMATCH — \"\" has no read record)", () => {
    const text = mutateDocument((doc) => (doc.case.scene.worldObjects[0].evidenceId = ""));
    expect(kindOf(text)).toBe("invalid");
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe("savegame.case.scene.worldObjects[0].evidenceId");
    expect(diagnostic.reasonCode).toBe("REFERENCE_MISMATCH");
  });

  it("worldObjects[].evidenceId = a recorded string ACCEPTED", () => {
    const text = mutateDocument((doc) => (doc.case.scene.worldObjects[0].evidenceId = "forensic_knife_match_01"));
    expect(parseSavegameV1(text, utf8ByteLength(text)).scene.worldObjects[0].evidenceId).toBe("forensic_knife_match_01");
  });
});

describe("Phase32-Fix2 §11 — worldObjects[].subtype DEF-068 tightening", () => {
  const at = "savegame.case.scene.worldObjects[0].subtype";

  it("subtype = null ACCEPTED (the backend world-object projection ALWAYS emits subtype, null when none — publication.py:904; absent is NOT a canonical shape)", () => {
    const nulled = mutateDocument((doc) => (doc.case.scene.worldObjects[0].subtype = null));
    expect(parseSavegameV1(nulled, utf8ByteLength(nulled)).scene.worldObjects[0].subtype).toBeNull();
  });

  it("subtype = \"\" REJECTED as EMPTY_STRING (DEF-068 tightening, never accepted at import)", () => {
    const text = mutateDocument((doc) => (doc.case.scene.worldObjects[0].subtype = ""));
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe(at);
    expect(diagnostic.reasonCode).toBe("EMPTY_STRING");
    expect(diagnostic.actualType).toBe("string");
  });

  it("subtype = 301 chars REJECTED as BOUND_EXCEEDED; 300 chars ACCEPTED", () => {
    const over = mutateDocument((doc) => (doc.case.scene.worldObjects[0].subtype = "a".repeat(301)));
    const diagnostic = diagnosticOf(over);
    expect(diagnostic.path).toBe(at);
    expect(diagnostic.reasonCode).toBe("BOUND_EXCEEDED");
    const bounded = mutateDocument((doc) => (doc.case.scene.worldObjects[0].subtype = "a".repeat(300)));
    const boundedDef = parseSavegameV1(bounded, utf8ByteLength(bounded));
    expect(boundedDef.scene.worldObjects[0].subtype).not.toBeNull();
    expect(boundedDef.scene.worldObjects[0].subtype!.length).toBe(300);
  });
});

describe("Phase32-Fix2 §11 — witnesses[].sceneObjectId nullable matrix", () => {
  const at = "savegame.case.witnesses[0].sceneObjectId";

  it("sceneObjectId = null ACCEPTED (REMOTE_STATEMENT witness)", () => {
    const text = mutateDocument((doc) => {
      doc.case.witnesses[0].presence = "REMOTE_STATEMENT";
      doc.case.witnesses[0].sceneObjectId = null;
    });
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.witnesses[0].presence).toBe("REMOTE_STATEMENT");
    expect(definition.witnesses[0].sceneObjectId).toBeNull();
  });

  it("sceneObjectId = a string ACCEPTED (ON_SCENE linkage)", () => {
    const text = mutateDocument((doc) => {
      doc.case.witnesses[0].presence = "ON_SCENE";
      doc.case.witnesses[0].sceneObjectId = "apartment_door";
    });
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.witnesses[0].sceneObjectId).toBe("apartment_door");
  });

  it("sceneObjectId = \"\" REJECTED (TYPE_MISMATCH — the shared-parser bounded-nullable detail classifies as TYPE_MISMATCH)", () => {
    const text = mutateDocument((doc) => (doc.case.witnesses[0].sceneObjectId = ""));
    expect(kindOf(text)).toBe("invalid");
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe(at);
    expect(diagnostic.reasonCode).toBe("TYPE_MISMATCH");
    expect(diagnostic.actualType).toBe("string");
  });
});

describe("Phase32-Fix2 §11 — NO-BLANKET-WEAKENING: empty IDs stay rejected", () => {
  const cases: Array<[string, string, (doc: any) => void]> = [
    ["placement.objectId", "savegame.case.publicCase.worldGraph.placements[0].objectId", (d) => (d.case.publicCase.worldGraph.placements[0].objectId = "")],
    ["placement.anchor", "savegame.case.publicCase.worldGraph.placements[0].anchor", (d) => (d.case.publicCase.worldGraph.placements[0].anchor = "")],
    ["placement.locationId", "savegame.case.publicCase.worldGraph.placements[0].locationId", (d) => (d.case.publicCase.worldGraph.placements[0].locationId = "")],
    ["placement.assetId", "savegame.case.publicCase.worldGraph.placements[0].assetId", (d) => (d.case.publicCase.worldGraph.placements[0].assetId = "")],
    ["worldObject.objectId", "savegame.case.scene.worldObjects[0].objectId", (d) => (d.case.scene.worldObjects[0].objectId = "")],
    ["worldObject.assetId", "savegame.case.scene.worldObjects[0].assetId", (d) => (d.case.scene.worldObjects[0].assetId = "")],
    ["worldObject.assetType", "savegame.case.scene.worldObjects[0].assetType", (d) => (d.case.scene.worldObjects[0].assetType = "")],
    ["worldObject.anchor", "savegame.case.scene.worldObjects[0].anchor", (d) => (d.case.scene.worldObjects[0].anchor = "")],
    ["worldObject.locationId", "savegame.case.scene.worldObjects[0].locationId", (d) => (d.case.scene.worldObjects[0].locationId = "")],
    ["witness.witnessId", "savegame.case.witnesses[0].witnessId", (d) => (d.case.witnesses[0].witnessId = "")],
  ];

  it.each(cases)("%s = \"\" is REJECTED (EMPTY_IDs stay fail-closed; fixing interaction must not weaken siblings)", (_name, path, mutate) => {
    const text = mutateDocument(mutate);
    expect(kindOf(text)).toBe("invalid");
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe(path);
    // objectId/anchor/locationId/assetId/assetType on placements and world
    // objects are EMPTY_STRING; the shared witness parser classifies the
    // empty witnessId as TYPE_MISMATCH (trailing-period detail) — both are
    // REJECTED, which is the contract being pinned.
    expect(["EMPTY_STRING", "TYPE_MISMATCH"]).toContain(diagnostic.reasonCode);
  });

  it("empty witnessId is still rejected even though interaction accepts empty strings", () => {
    const text = mutateDocument((doc) => (doc.case.witnesses[0].witnessId = ""));
    const diagnostic = diagnosticOf(text);
    expect(diagnostic.path).toBe("savegame.case.witnesses[0].witnessId");
    expect(diagnostic.reasonCode).toBe("TYPE_MISMATCH");
  });
});

describe("Phase32-Fix2 §6/§23 — the frozen bounded UI message stays", () => {
  it.each([
    ["placement interaction missing", (d: any) => void delete d.case.publicCase.worldGraph.placements[0].interaction],
    ["placement interaction 301", (d: any) => (d.case.publicCase.worldGraph.placements[0].interaction = "a".repeat(301))],
    ["worldObject interaction 301", (d: any) => (d.case.scene.worldObjects[0].interaction = "a".repeat(301))],
    ["subtype empty", (d: any) => (d.case.scene.worldObjects[0].subtype = "")],
    ["empty objectId", (d: any) => (d.case.publicCase.worldGraph.placements[0].objectId = "")],
  ])("%s keeps the frozen bounded copy (never a raw diagnostic)", (_name, mutate) => {
    const text = mutateDocument(mutate);
    try {
      parseSavegameV1(text, utf8ByteLength(text));
      throw new Error("expected rejection");
    } catch (error) {
      expect(error).toBeInstanceOf(SavegameParseError);
      if (error instanceof SavegameParseError) {
        assertBoundedUIMessage(error);
        // The production message never leaks schema paths or content.
        const message = savegameErrorMessage(error.kind);
        expect(message).not.toContain("interaction");
        expect(message).not.toContain("worldObjects");
        expect(message).not.toContain("subtype");
      }
    }
  });
});

describe("Phase32-Fix2 §16 — production exporter -> importer round-trip over the interaction shape", () => {
  const INTERACTION_TEXT: string = JSON.stringify(realInteraction);

  function interactionDefinition(): ReturnType<typeof parseSavegameV1> {
    return parseSavegameV1(INTERACTION_TEXT, utf8ByteLength(INTERACTION_TEXT));
  }

  it("fixture reproduces the EXACT §4 five empty interactions + four canonical null shapes", () => {
    const raw = realInteraction as any;
    const expectedEmpty = ["apartment_door", "victim_body_placeholder", "apartment_lamp", "apartment_table", "vase_01"].sort();
    const emptyPlacementIds = raw.case.publicCase.worldGraph.placements
      .filter((p: any) => p.interaction === "")
      .map((p: any) => p.objectId)
      .sort();
    const emptyWorldObjectIds = raw.case.scene.worldObjects
      .filter((w: any) => w.interaction === "")
      .map((w: any) => w.objectId)
      .sort();
    expect(emptyPlacementIds).toEqual(expectedEmpty);
    expect(emptyWorldObjectIds).toEqual(expectedEmpty);
    expect(raw.case.publicCase.worldGraph.placements.length).toBe(9);
    expect(raw.case.scene.worldObjects.length).toBe(9);
    // The four canonical null shapes.
    expect(raw.case.publicCase.worldGraph.placements.every((p: any) => p.evidenceId === null)).toBe(true);
    expect(raw.case.scene.worldObjects.every((w: any) => w.evidenceId === null)).toBe(true);
    expect(raw.case.scene.worldObjects.find((w: any) => w.objectId === "vase_01").subtype).toBeNull();
    expect(raw.case.witnesses[0].presence).toBe("REMOTE_STATEMENT");
    expect(raw.case.witnesses[0].sceneObjectId).toBeNull();
    // A valid ReplayTruthV1 block.
    expect(raw.case.replayTruth.murdererId).toBe("thomas_reed");
  });

  it("reExportV1(definition) -> serializeSavegameV1 -> JSON.parse -> parseSavegameV1 -> normalize -> fresh replay PASSES", () => {
    const definition = interactionDefinition();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    const parsedBack: unknown = JSON.parse(exported); // a REAL JSON round-trip
    const reloaded = parseSavegameV1(JSON.stringify(parsedBack), utf8ByteLength(exported));
    // The empty interactions and null shapes SURVIVE the re-export.
    expect(reloaded.publicCase.worldGraph.placements.filter((p) => p.interaction === "").length).toBe(5);
    expect(reloaded.scene.worldObjects.filter((w) => w.interaction === "").length).toBe(5);
    for (const placement of reloaded.publicCase.worldGraph.placements) {
      expect(placement.evidenceId).toBeNull();
    }
    for (const worldObject of reloaded.scene.worldObjects) {
      expect(worldObject.evidenceId).toBeNull();
    }
    expect(reloaded.witnesses[0].sceneObjectId).toBeNull();
    // Fresh replay from the reloaded definition.
    const state = new FreshReplayState(reloaded);
    const bootstrap = state.freshBootstrap();
    expect(bootstrap.state).toBe("PLAYING");
    expect(JSON.stringify(bootstrap)).not.toContain("replayTruth");
    expect(bootstrap.playerKnowledge.discoveredEvidenceIds).toEqual([]);
  });

  it("empty-interaction decorative objects replay safely (no crash, no discovery)", () => {
    const state = new FreshReplayState(interactionDefinition());
    // A decorative object (vase_01) with interaction="" interacts neutrally.
    const vase = state.interactObject("vase_01", "");
    expect(vase.discovery).toBeNull();
    expect(vase.evidenceId).toBeNull();
    expect(vase.result).toBe("interacted");
    expect(state.knowledgeSnapshot().discoveredEvidenceIds).toEqual([]);
  });

  it("the same interaction-shape save can be loaded repeatedly into fresh replays", () => {
    const definition = interactionDefinition();
    const first = new FreshReplayState(definition);
    expect(first.lifecycleState()).toBe("PLAYING");
    const second = new FreshReplayState(interactionDefinition());
    expect(second.lifecycleState()).toBe("PLAYING");
    expect(second.freshBootstrap().playerKnowledge.discoveredEvidenceIds).toEqual([]);
  });
});

describe("Phase32-Fix2 §19-§21/§29 — replay + provider isolation on the interaction shape", () => {
  afterEach(() => {
    clearReplay();
  });

  it("loading the interaction fixture yields truthRevealed=false, completion=false, accusation=none", async () => {
    const INTERACTION_TEXT: string = JSON.stringify(realInteraction);
    const outcome = await loadSavegameFile({
      size: utf8ByteLength(INTERACTION_TEXT),
      text: async () => INTERACTION_TEXT,
    });
    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      const state = new FreshReplayState(outcome.definition);
      // truth revealed = false; completion = false; accusation = none.
      expect(JSON.stringify(state.freshBootstrap())).not.toContain("replayTruth");
      expect(state.lifecycleState()).toBe("PLAYING");
      expect(() => state.getReveal()).toThrow(); // REVEAL_NOT_AVAILABLE until accused
      expect(state.knowledgeSnapshot().discoveredEvidenceIds).toEqual([]);
    }
  });

  it("load + replay on the interaction shape makes ZERO fetch/provider/quota calls", async () => {
    const fetchSpy = vi.fn(async () => {
      throw new Error("network must never be used");
    });
    const previous = globalThis.fetch;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    try {
      const INTERACTION_TEXT: string = JSON.stringify(realInteraction);
      const outcome = await loadSavegameFile({
        size: utf8ByteLength(INTERACTION_TEXT),
        text: async () => INTERACTION_TEXT,
      });
      expect(outcome.ok).toBe(true);
      if (outcome.ok) {
        startReplay(outcome.definition);
        expect(activeReplay()).not.toBeNull();
        const state = activeReplay()!.state;
        state.interactObject("apartment_door", "");
        state.interactObject("vase_01", "");
        expect(state.knowledgeSnapshot().discoveredEvidenceIds).toEqual([]);
      }
      // Zero network calls: no Frontier, no Ollama, no Bridge, no quota.
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = previous;
    }
  });
});
