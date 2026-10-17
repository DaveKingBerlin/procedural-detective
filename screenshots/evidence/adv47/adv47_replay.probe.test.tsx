// @vitest-environment jsdom
/**
 * ADV-47 transient probe — a REAL backend-exported SavegameV1 through the
 * frontend validator + replay runtime (cross-stack drift + victim derivation).
 * NOT product code.
 */
import { describe, expect, it } from "vitest";
import exported from "./exported_demo_apartment.pdcase.json";
import { parseSavegameV1, utf8ByteLength } from "../../../frontend/src/savegame/savegameV1";
import { FreshReplayState } from "../../../frontend/src/savegame/replayRuntime";
import { buildVictimProfileView } from "../../../frontend/src/scene/victimModel";

const TEXT = JSON.stringify(exported);

describe("ADV-47 real backend export through the frontend replay", () => {
  it("parses the real backend-exported .pdcase", () => {
    const def = parseSavegameV1(TEXT, utf8ByteLength(TEXT));
    expect(def.publicCase.persons.some((p) => p.role === "victim")).toBe(true);
  });

  it("derives the victim identity and discovery for replay", () => {
    const def = parseSavegameV1(TEXT, utf8ByteLength(TEXT));
    const state = new FreshReplayState(def);
    const boot = state.freshBootstrap();
    const victim = boot.victim;
    expect(victim).not.toBeNull();
    expect(victim?.name).toBe("Sarah Miller");
    expect(victim?.gender).toBe("female");
    expect(victim?.age).toBe(39);
    expect(victim?.sceneObjectId).toBe("victim_body_placeholder");
    expect(victim?.discovery?.evidenceId).toBe("body_found_01");
  });

  it("loses Known facts / Forensic findings on replay because the export strips victimRelated", () => {
    const def = parseSavegameV1(TEXT, utf8ByteLength(TEXT));
    const state = new FreshReplayState(def);
    const victim = state.freshBootstrap().victim!;
    state.interactObject("victim_body_placeholder", "inspect");
    const body = state.readRecord("body_found_01");
    const view = buildVictimProfileView(victim, [body]);
    // Timeline works (derived from the discovery evidenceId directly).
    expect(view.timeline.length).toBeGreaterThan(0);
    // But no record carries the victimRelated marker after export.
    expect(("victimRelated" in (body.content as Record<string, unknown>))).toBe(false);
    expect(view.knownFacts).toEqual([]);
    expect(view.forensicFindings).toEqual([]);
  });
});
