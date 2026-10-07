import { afterEach, describe, expect, it } from "vitest";
import canonical from "./fixtures/v1_demo_apartment.pdcase.json";
import { loadHypothesis, saveHypothesis, type HypothesisStorage, type HypothesisPins } from "../notebook/hypothesisStore";
import { witnessStatementKey } from "../witness/witnessStatementStore";
import { clearReplay, replayOrLivePlaythroughId, replayStorageNamespace, startReplay } from "./replaySession";
import { parseSavegameV1, utf8ByteLength } from "./savegameV1";

/**
 * Phase 32 — DEF-045 / ADV-32F-04 regression: loaded saved-case replays must
 * NOT share one constant localStorage namespace for player notebook pins and
 * the witness-statement cache.
 *
 * Every loaded `.pdcase` derives a PER-FILE replay storage namespace from a
 * short stable hash of the imported PUBLIC case content (the replay truth is
 * deliberately EXCLUDED from the fingerprint):
 *  - file B's player-authored pins never appear in file A and vice versa
 *    (unless the two files share the same public case content);
 *  - live playthrough namespaces (`PT-*`, `live-pt-*`) are NEVER touched by a
 *    replay start/clear cycle;
 *  - re-loading the SAME file reuses its own pins (deterministic namespace);
 *  - only player-authored data is stored — the imported case/truth content is
 *    never written to storage (the key is a one-way hash token).
 */

const A_TEXT: string = JSON.stringify(canonical);

/** A DIFFERENT valid file: the same demo case with a DIFFERENT public title
 *  -> a different immutable content identity (the ADV-32F-04 "file B"). */
function bText(): string {
  const doc = JSON.parse(A_TEXT);
  doc.case.replayTruth.murdererId = "anna_karlsson";
  doc.case.replayTruth.murdererName = "Anna Karlsson";
  doc.case.metadata.title = "Second loaded file";
  return JSON.stringify(doc);
}

function aDefinition(): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(A_TEXT, utf8ByteLength(A_TEXT));
}

function bDefinition(): ReturnType<typeof parseSavegameV1> {
  const text = bText();
  return parseSavegameV1(text, utf8ByteLength(text));
}

function makeMemoryStorage(): HypothesisStorage & { keys(): string[]; values: Map<string, string> } {
  const values = new Map<string, string>();
  return {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
    removeItem: (key) => void values.delete(key),
    keys: () => [...values.keys()],
  };
}

const PINS_A: HypothesisPins = { suspect: "thomas_reed", motive: "cover_up_embezzlement", weapon: "kitchen_knife", time: null };

describe("DEF-045 — per-file replay storage namespaces", () => {
  afterEach(() => {
    clearReplay();
  });

  it("file A's pins do not appear in file B", () => {
    const storage = makeMemoryStorage();
    const namespaceA = replayStorageNamespace(aDefinition());
    const namespaceB = replayStorageNamespace(bDefinition());
    expect(namespaceA).not.toBe(namespaceB);

    saveHypothesis(namespaceA, PINS_A, storage);
    // File B's namespace has its own fresh pin area.
    expect(loadHypothesis(namespaceB, storage)).toEqual({ suspect: null, motive: null, weapon: null, time: null });
    // And the witness-statement cache keys differ per file too.
    expect(witnessStatementKey(namespaceA)).not.toBe(witnessStatementKey(namespaceB));
    // Nothing was ever written under the old static shared key.
    expect(storage.getItem("pd_hypothesis_v1:saved-replay")).toBeNull();
    expect(storage.getItem("pd_witness_statements_v1:saved-replay")).toBeNull();
  });

  it("a live playthrough namespace (PT-*) is never touched by a replay start/clear cycle", () => {
    const storage = makeMemoryStorage();
    // A real live playthrough with player notes of its own.
    saveHypothesis("PT-live-001", PINS_A, storage);
    expect(loadHypothesis("PT-live-001", storage)).toEqual(PINS_A);
    const keysBefore = [...storage.keys()].sort();

    startReplay(aDefinition());
    expect(replayOrLivePlaythroughId()).toBe(replayStorageNamespace(aDefinition()));
    // Replay notes never write into the live namespace.
    saveHypothesis(replayOrLivePlaythroughId(), { suspect: "anna_karlsson", motive: null, weapon: null, time: null }, storage);
    expect(loadHypothesis("PT-live-001", storage)).toEqual(PINS_A);
    // clearReplay returns to the live credential path.
    clearReplay();
    expect(replayOrLivePlaythroughId()).toBe("");
    // The replay start/write/clear cycle created NO live PT-* keys and
    // removed NONE: the key set is exactly as before plus the replay's own
    // per-file namespace entry.
    const keysAfter = storage.keys().filter((key) => !key.includes(`saved-replay-`)).sort();
    expect(keysAfter).toEqual(keysBefore);
  });

  it("re-loading the same file reuses its pins (deterministic namespace)", () => {
    const storage = makeMemoryStorage();
    const definition = aDefinition();
    startReplay(definition);
    const namespace = replayOrLivePlaythroughId();
    saveHypothesis(namespace, PINS_A, storage);

    // Leave and reload the SAME file: the same namespace is derived again.
    clearReplay();
    startReplay(aDefinition());
    expect(replayOrLivePlaythroughId()).toBe(namespace);
    expect(loadHypothesis(replayOrLivePlaythroughId(), storage)).toEqual(PINS_A);
    clearReplay();
  });

  it("the namespace is a short stable token — imported truth content never reaches storage", () => {
    const definition = aDefinition();
    const namespace = replayStorageNamespace(definition);
    expect(namespace).toMatch(/^saved-replay-[0-9a-z]{12}$/);
    // A re-export only changes exportedAt -> the SAME file identity.
    const reexportedText = JSON.stringify({ ...JSON.parse(A_TEXT), exportedAt: "2099-12-31T23:59:59Z" });
    const reexportedDefinition = parseSavegameV1(reexportedText, utf8ByteLength(reexportedText));
    expect(replayStorageNamespace(reexportedDefinition)).toBe(namespace);
    // The derived token is a hash: the case content itself (incl. truth ids)
    // is never written to storage verbatim — only the hashed namespace is.
    expect(namespace).not.toContain("thomas_reed");
    expect(namespace).not.toContain("2026-09-11T22:17:00");
  });

  it("the fingerprint NEVER depends on the replay truth (DEF-045)", () => {
    // ReplayTruthV1 is the replay-scoped SOLUTION — it must not influence the
    // player-notes storage key. Two schema-valid files that differ ONLY in the
    // truth block share the SAME namespace (their public case is identical),
    // so a tampered truth twin cannot scatter or leak notes.
    const textTruthB = (() => {
      const doc = JSON.parse(A_TEXT);
      doc.case.replayTruth.murdererId = "anna_karlsson";
      doc.case.replayTruth.murdererName = "Anna Karlsson";
      doc.case.replayTruth.motiveId = "revenge_for_affair";
      doc.case.replayTruth.motiveLabel = "Revenge for a suspected affair";
      doc.case.replayTruth.weaponId = "scissors";
      doc.case.replayTruth.weaponName = "Scissors";
      doc.case.replayTruth.crimeTime = "2026-09-11T22:17:00+02:00";
      return JSON.stringify(doc);
    })();
    const truthA = aDefinition();
    const truthB = parseSavegameV1(textTruthB, utf8ByteLength(textTruthB));
    expect(replayStorageNamespace(truthB)).toBe(replayStorageNamespace(truthA));
    // And a REAL content change (different public title) still moves files.
    expect(replayStorageNamespace(bDefinition())).not.toBe(replayStorageNamespace(aDefinition()));
  });
});