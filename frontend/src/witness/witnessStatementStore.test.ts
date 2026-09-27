import { describe, expect, it } from "vitest";
import type { WitnessListEntryDTO } from "../api/types";
import type { AskedWitnessStatement } from "./witnessModel";
import {
  loadWitnessStatements,
  saveWitnessStatements,
  witnessStatementKey,
  type WitnessStatementStorage,
} from "./witnessStatementStore";

class MemoryStorage implements WitnessStatementStorage {
  readonly values = new Map<string, string>();
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

const WITNESSES: WitnessListEntryDTO[] = [
  {
    witnessId: "witness_emily",
    displayName: "Emily Reed",
    presence: "ON_SCENE",
    sceneObjectId: "witness_emily",
  },
];

const ANSWER: AskedWitnessStatement = {
  witnessId: "witness_emily",
  displayName: "Stale name",
  questionType: "SOUND",
  statement: { summary: "No. Nothing stood out to me.", observations: [] },
  evidenceId: "must-not-be-persisted",
};

describe("Phase 23 witness statement reload cache", () => {
  it("round-trips player-known text while dropping evidence identity and using current bootstrap identity", () => {
    const storage = new MemoryStorage();
    expect(saveWitnessStatements("PT-1", WITNESSES, [ANSWER], storage)).toBe(true);

    const serialized = storage.values.get(witnessStatementKey("PT-1"))!;
    expect(serialized).not.toContain("must-not-be-persisted");
    expect(serialized).not.toContain("Stale name");

    expect(loadWitnessStatements("PT-1", WITNESSES, storage)).toEqual([
      {
        ...ANSWER,
        displayName: "Emily Reed",
        evidenceId: null,
      },
    ]);
  });

  it("rejects foreign witnesses/questions and bounds hostile stored text", () => {
    const storage = new MemoryStorage();
    storage.setItem(
      witnessStatementKey("PT-1"),
      JSON.stringify({
        version: 1,
        entries: [
          { witnessId: "foreign", questionType: "TIME", statement: { summary: "secret" } },
          { witnessId: "witness_emily", questionType: "__proto__", statement: { summary: "bad" } },
          {
            witnessId: "witness_emily",
            questionType: "SOUND",
            displayName: "Injected",
            evidenceId: "forged",
            statement: { summary: "x".repeat(10_000), observations: [] },
          },
        ],
      }),
    );

    const restored = loadWitnessStatements("PT-1", WITNESSES, storage);
    expect(restored).toHaveLength(1);
    expect(restored[0].displayName).toBe("Emily Reed");
    expect(restored[0].evidenceId).toBeNull();
    expect(restored[0].statement.summary.length).toBeLessThanOrEqual(2000);
  });

  it("fails closed for malformed and oversized documents", () => {
    const storage = new MemoryStorage();
    storage.setItem(witnessStatementKey("PT-1"), "not-json");
    expect(loadWitnessStatements("PT-1", WITNESSES, storage)).toEqual([]);
    storage.setItem(witnessStatementKey("PT-1"), "x".repeat(64 * 1024 + 1));
    expect(loadWitnessStatements("PT-1", WITNESSES, storage)).toEqual([]);
  });
});
