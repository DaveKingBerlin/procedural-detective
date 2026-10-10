import { describe, expect, it, vi } from "vitest";
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import generatedCase from "./fixtures/v1_generated_quality_case.pdcase.json";
import { reExportV1, serializeSavegameV1 } from "./exportV1";
import { FreshReplayState } from "./replayRuntime";
import { parseSavegameV1, utf8ByteLength, type SavedCaseDefinition } from "./savegameV1";

/**
 * Phase 35 §17 — FRONTEND GENERATED-CASE QUALITY GATE.
 *
 * A PUBLISHED generated case must be structurally representable by the
 * existing SavegameV1 projection: parse -> normalize -> FreshReplayState ->
 * accuse -> reveal -> re-export -> re-parse all with the REAL production
 * parser/exporter (regression test, never a truth-leak path).
 *
 * Fixture: `tests/fixtures/case_quality/generated/
 * procedural-detective-case-demo-hard-generate.ok.pdcase`, committed
 * byte-identical here as `./fixtures/v1_generated_quality_case.pdcase.json`
 * (28,428 B, sha256 6ca324e058e03574df2a18ff7e5a6af8e80f8a0090ff04330666330dd7ea62df).
 * The case is the ONE the Phase35 §17 defect was verified on: it publishes +
 * exports a world whose `worldGraph.placements[].locationId` and
 * `scene.worldObjects[].locationId` reference ROOM-level world-graph
 * locations (`office_mainroom`, `office_reception`, …) that are members of
 * `publicCase.worldGraph.locations` but NOT of `publicCase.locations` (the
 * published travel hubs `konsortium_office` / `research_lab` / `motor_lodge`).
 * The backend generation/safety validators treat the world-graph location
 * set as THE canonical placement namespace
 * (`backend/app/generation/safety.py::validate_world_graph`, `pipeline.py::
 * _world_graph_resolution_issues`), so the frontend import ACCEPTS the UNION
 * (`publicCase.locations` OR `publicCase.worldGraph.locations`) — this gate
 * proves the generated case now survives the full save/load/replay/re-export
 * cycle (Phase35 §17 / §43).
 *
 * ZERO network: the whole gate runs against the committed fixture with a
 * throwing fetch spy — no provider, no live generation, no server.
 */

const GENERATED_TEXT: string = JSON.stringify(generatedCase);

function generatedDefinition(): SavedCaseDefinition {
  return parseSavegameV1(GENERATED_TEXT, utf8ByteLength(GENERATED_TEXT));
}

/** The correct accusation for the generated fixture (from its ReplayTruthV1). */
const SOLVED = {
  murdererId: "paul_becker",
  motiveId: "stolen_research_data",
  weaponId: "bronze_ceremonial_ice_pick",
  crimeTime: "23:42:00",
};

describe("Phase35 §17 — generated-case quality gate", () => {
  it("the real published generated fixture passes the production parser and normalizes", () => {
    const definition = generatedDefinition();
    expect(definition.metadata.source).toBe("generated");
    expect(definition.metadata.sourceCaseId).toBe("CASE-1");
    expect(definition.replayTruth.murdererId).toBe("paul_becker");
    // The union reference rule is genuinely exercised: the fixture's rooms
    // are world-graph members, NOT published travel locations.
    const published = new Set(definition.publicCase.locations.map((l) => l.locationId));
    const rooms = new Set(definition.publicCase.worldGraph.locations.map((l) => l.locationId));
    expect(published.has("office_reception")).toBe(false);
    expect(rooms.has("office_reception")).toBe(true);
    expect(
      definition.publicCase.worldGraph.placements.some((p) => rooms.has(p.locationId) && !published.has(p.locationId)),
    ).toBe(true);
  });

  it("normalizes into a FRESH replay (truth hidden, completion false, accusation none)", () => {
    const state = new FreshReplayState(generatedDefinition());
    // Lifecycle: PLAYING -> not revealed, not completed, no accusation yet.
    expect(state.lifecycleState()).toBe("PLAYING");
    // The reveal is gated exactly like the server: 403 REVEAL_NOT_AVAILABLE —
    // THE TRUTH is not reachable before an accusation exists.
    try {
      state.getReveal();
      expect.unreachable("getReveal must be gated before accusation");
    } catch (error) {
      expect(error).toHaveProperty("status", 403);
      expect(error).toHaveProperty("code", "REVEAL_NOT_AVAILABLE");
    }
    // Knowledge is empty and the truth never serializes into the bootstrap.
    const bootstrap = state.freshBootstrap();
    expect(bootstrap.state).toBe("PLAYING");
    expect(bootstrap.playerKnowledge.discoveredEvidenceIds).toEqual([]);
    expect(bootstrap.playerKnowledge.readEvidenceIds).toEqual([]);
    expect(JSON.stringify(bootstrap)).not.toContain("replayTruth");
    expect(JSON.stringify(bootstrap)).not.toContain("murdererName");
    // The exported archive still honestly carries the full-knowledge world
    // objects — it just never publishes them pre-reveal.
    expect(generatedDefinition().scene.worldObjects.some((o) => o.evidenceId !== null)).toBe(true);
  });

  it("re-export -> serialize -> JSON.parse -> parse -> normalize round-trips cleanly", () => {
    const definition = generatedDefinition();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    const reparsed: unknown = JSON.parse(exported);
    const reloaded = parseSavegameV1(JSON.stringify(reparsed), utf8ByteLength(exported));
    expect(reloaded.metadata.title).toBe(definition.metadata.title);
    expect(reloaded.publicCase.caseId).toBe(definition.publicCase.caseId);
    expect(reloaded.replayTruth).toEqual(definition.replayTruth);
    expect(reloaded.publicCase.worldGraph.placements).toEqual(definition.publicCase.worldGraph.placements);
  });

  it("a CORRECT accusation reveals the SAVED replayTruth (room-level world intact)", () => {
    const state = new FreshReplayState(generatedDefinition());
    // Pre-reveal the world still projects the room-level location refs.
    const bootstrap = state.freshBootstrap();
    expect(bootstrap.scene.worldObjects.some((o) => o.locationId === "office_reception")).toBe(true);
    const accepted = state.submitAccusation({ ...SOLVED });
    expect(accepted.status).toBe("ACCUSED");
    const reveal = state.getReveal();
    expect(reveal.result.overall).toBe("solved");
    expect(reveal.score).toEqual({ correctDimensions: 4, totalDimensions: 4 });
    expect(reveal.truth.murdererId).toBe("paul_becker");
    expect(reveal.truth.murdererName).toBe("Paul Becker");
    expect(reveal.truth.motiveId).toBe("stolen_research_data");
    expect(reveal.truth.motiveLabel).toBe("Wanted to steal the research data");
    expect(reveal.truth.weaponId).toBe("bronze_ceremonial_ice_pick");
    expect(reveal.truth.weaponName).toBe("Bronze Ceremonial Ice Pick");
    expect(reveal.truth.crimeTime).toBe("2026-09-11T23:42:00+02:00");
    expect(reveal.player.accusation).toEqual({ ...SOLVED });
  });

  it("a WRONG accusation follows the server rules (accepted, reveal reports incorrect, never leaks truth)", () => {
    const state = new FreshReplayState(generatedDefinition());
    const accepted = state.submitAccusation({
      murdererId: "marcus_fischer",
      motiveId: "financial_settlement",
      weaponId: "kitchen_knife",
      crimeTime: "18:00:00",
    });
    expect(accepted.status).toBe("ACCUSED");
    expect(JSON.stringify(accepted)).not.toMatch(/murdererCorrect|timeCorrect|score/);
    const reveal = state.getReveal();
    expect(reveal.result.overall).toBe("incorrect");
    expect(reveal.score.correctDimensions).toBe(0);
  });

  it("repeated loads always start a fresh replay of the SAME file", () => {
    const text = serializeSavegameV1(reExportV1(generatedDefinition(), "2099-01-01T00:00:00Z"));
    const first = new FreshReplayState(parseSavegameV1(text, utf8ByteLength(text)));
    first.submitAccusation({ ...SOLVED });
    expect(first.getReveal().result.overall).toBe("solved");
    // The same bytes loaded again: brand-new PLAYING replay, accusation none,
    // truth hidden — never the previous playthrough.
    const second = new FreshReplayState(parseSavegameV1(text, utf8ByteLength(text)));
    expect(second.freshBootstrap().state).toBe("PLAYING");
    expect(second.lifecycleState()).toBe("PLAYING");
    expect(second.knowledgeSnapshot().discoveredEvidenceIds).toEqual([]);
    try {
      second.getReveal();
      expect.unreachable("the fresh replay must keep the truth gated");
    } catch (error) {
      expect(error).toHaveProperty("code", "REVEAL_NOT_AVAILABLE");
    }
  });

  it("every scene.worldObjects[].locationId is in the union and consistent with its placement", () => {
    const definition = generatedDefinition();
    const published = new Set(definition.publicCase.locations.map((l) => l.locationId));
    const rooms = new Set(definition.publicCase.worldGraph.locations.map((l) => l.locationId));
    const union = new Set([...published, ...rooms]);
    const placementById = new Map(
      definition.publicCase.worldGraph.placements.map((p) => [p.objectId, p] as const),
    );
    for (const worldObject of definition.scene.worldObjects) {
      // The world-object location reference is a UNION member…
      expect(union.has(worldObject.locationId)).toBe(true);
      // …and identical to its matching placement projection (parity).
      const placement = placementById.get(worldObject.objectId);
      expect(placement, `worldObject ${worldObject.objectId} must have a placement`).toBeDefined();
      expect(placement!.locationId).toBe(worldObject.locationId);
    }
    for (const placement of definition.publicCase.worldGraph.placements) {
      expect(union.has(placement.locationId)).toBe(true);
    }
  });

  it("the entire save -> replay -> re-export -> re-load gate makes ZERO fetch/provider calls", () => {
    const fetchSpy = vi.fn(async () => {
      throw new Error("network must never be used during the savegame quality gate");
    });
    const previous = globalThis.fetch;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    try {
      const definition = generatedDefinition();
      const state = new FreshReplayState(definition);
      state.interactObject("kitchen_knife", "inspect");
      state.readRecord("d_ev_weapon_false_kitchenknife");
      state.submitAccusation({ ...SOLVED });
      const reveal = state.getReveal();
      expect(reveal.result.overall).toBe("solved");
      const roundTripped = serializeSavegameV1(reExportV1(definition));
      const reloaded = parseSavegameV1(roundTripped, utf8ByteLength(roundTripped));
      expect(reloaded.replayTruth.murdererId).toBe("paul_becker");
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = previous;
    }
  });
});

describe("Phase35 §25 — the three golden demo savegames still parse through the production import", () => {
  /** The repo-level immutable corpus (Phase35 §2/§3): byte/hash-pinned golden
   *  demo exports. The frontend import MUST keep accepting them — the golden
   *  cases degenerate onto the published location set
   *  (`worldGraph.locations == publicCase.locations`), so the Phase35 §17
   *  placement/worldObject UNION is a strict SUPERSET of what they need.
   *  Read-only: the corpus stays authoritative in `tests/fixtures/`. */
  const GOLDEN_DIR = join(fileURLToPath(new URL("../../../tests/fixtures/case_quality/golden/demo", import.meta.url)));
  const GOLDEN_FILES: readonly string[] = [
    "procedural-detective-case-CASE-iA2tcy7PkB1P.ok.pdcase",
    "procedural-detective-case-CASE-Yw0tGvxleJab.ok.pdcase",
    "procedural-detective-case-CASE-htbCd0X0mDkt.ok.pdcase",
  ];

  it("each golden demo file parses, normalizes and starts a fresh replay", () => {
    for (const file of GOLDEN_FILES) {
      const full = join(GOLDEN_DIR, file);
      const text = readFileSync(full, "utf8");
      const bytes = statSync(full).size;
      const definition = parseSavegameV1(text, bytes);
      expect(definition.metadata.source).toBe("demo");
      expect(definition.publicCase.travelRules.length).toBeGreaterThan(0);
      // The union rule must be a superset for the goldens: every placement /
      // world-object location reference resolves against the UNION without a
      // single rejection.
      const published = new Set(definition.publicCase.locations.map((l) => l.locationId));
      const rooms = new Set(definition.publicCase.worldGraph.locations.map((l) => l.locationId));
      const union = new Set([...published, ...rooms]);
      for (const placement of definition.publicCase.worldGraph.placements) {
        expect(union.has(placement.locationId)).toBe(true);
      }
      for (const worldObject of definition.scene.worldObjects) {
        expect(union.has(worldObject.locationId)).toBe(true);
      }
      // And a fresh replay starts PLAYING with the truth hidden.
      const state = new FreshReplayState(definition);
      expect(state.freshBootstrap().state).toBe("PLAYING");
      expect(JSON.stringify(state.freshBootstrap())).not.toContain("replayTruth");
    }
  });
});