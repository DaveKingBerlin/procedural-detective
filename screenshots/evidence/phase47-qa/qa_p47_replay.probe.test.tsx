// @vitest-environment jsdom
/**
 * Phase 47 QA retest probe (DEF-116) — a REAL post-fix backend SavegameV1
 * export through the frontend validator + replay view model. NOT product code.
 *
 * Proves the end-to-end claim: the backend preserves the public-safe
 * `victimRelated` marker in exported record content, the frontend validator
 * accepts it, and `buildVictimProfileView` consumes it to disclose the victim
 * profile's Known facts (the marker is no longer stripped on save/reload).
 */
import { describe, expect, it } from "vitest";
import exported from "./retest_demo_apartment.pdcase.json";
import { parseSavegameV1, utf8ByteLength } from "../../../frontend/src/savegame/savegameV1";
import { FreshReplayState } from "../../../frontend/src/savegame/replayRuntime";
import { buildVictimProfileView } from "../../../frontend/src/scene/victimModel";

const TEXT = JSON.stringify(exported);

describe("Phase 47 QA retest — DEF-116 real export end-to-end", () => {
  it("the real backend export carries victimRelated and the frontend preserves it", () => {
    const def = parseSavegameV1(TEXT, utf8ByteLength(TEXT));
    const marked = def.evidence
      .filter((record) => (record.content as Record<string, unknown>).victimRelated === true)
      .map((record) => record.evidenceId);
    console.log("QA retest marked records:", marked);
    expect(marked.length).toBeGreaterThan(0);
  });

  it("the marker is consumed by buildVictimProfileView as a Known fact", () => {
    const def = parseSavegameV1(TEXT, utf8ByteLength(TEXT));
    const state = new FreshReplayState(def);
    const victim = state.freshBootstrap().victim;
    expect(victim).not.toBeNull();
    const marked = def.evidence.filter(
      (record) => (record.content as Record<string, unknown>).victimRelated === true,
    );
    const dtos = marked.map((record) => ({
      evidenceId: record.evidenceId,
      kind: record.kind,
      title: record.title,
      description: record.description ?? null,
      openedAt: def.exportedAt,
      readByPlayer: true as const,
      content: record.content as unknown as Record<string, unknown>,
    }));
    const view = buildVictimProfileView(victim!, dtos as never);
    const ids = view.knownFacts.map((fact) => fact.evidenceId);
    console.log(
      "QA retest knownFacts:",
      ids,
      "forensic:",
      view.forensicFindings.map((finding) => finding.evidenceId),
    );
    expect(ids).toEqual(expect.arrayContaining(marked.map((record) => record.evidenceId)));
  });
});
