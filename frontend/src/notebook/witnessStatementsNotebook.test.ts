import { describe, expect, it } from "vitest";
import type { EvidenceReadResultDTO, InvestigationBootstrapResponse } from "../api/types";
import { buildInvestigationScene } from "../scene/buildInvestigationScene";
import {
  EMILY_TIME_EVIDENCE_ID,
  EMILY_WITNESS_ID,
  EMILY_WITNESS_NAME,
  makeBootstrap,
  makeEmilyNeutralStatement,
  makeEmilyTimeDiscoveryRecord,
  makeEmilyTimeStatement,
} from "../scene/testFixtures";
import type { AskedWitnessStatement } from "../witness/witnessModel";
import { buildNotebookModel, type NotebookGroup, type NotebookModel } from "./notebookModel";

/**
 * Phase 23 — "Witness statements" notebook group coverage.
 *
 * The group lists ONLY what was legitimately asked/discovered — never hidden
 * future answers. Two sources converge under one stable per-(witness,
 * question) id: the session's in-memory ASKED statements and (after a reload,
 * when the client store is empty) the READ interview-sourced RECORDS the
 * server re-serves — so no line can ever duplicate and discovered statements
 * survive a reload with no client-side statement persistence.
 */

function sceneModel(bootstrap: InvestigationBootstrapResponse) {
  return buildInvestigationScene(bootstrap);
}

function groupOf(model: NotebookModel, id: string): NotebookGroup {
  const group = model.groups.find((entry) => entry.id === id);
  if (!group) throw new Error(`missing notebook group ${id}`);
  return group;
}

const blankBootstrap = makeBootstrap();

function emilyAsked(questionType: AskedWitnessStatement["questionType"]): AskedWitnessStatement {
  return {
    witnessId: EMILY_WITNESS_ID,
    displayName: EMILY_WITNESS_NAME,
    questionType,
    statement: makeEmilyTimeStatement(),
    evidenceId: questionType === "TIME" ? EMILY_TIME_EVIDENCE_ID : null,
  };
}

function askedNotebook(
  asked: readonly AskedWitnessStatement[],
  discovered: string[] = [],
  read: string[] = [],
  records: EvidenceReadResultDTO[] = [],
): NotebookModel {
  return buildNotebookModel({
    discoveredEvidenceIds: discovered,
    readEvidenceIds: read,
    worldObjects: sceneModel(blankBootstrap).worldObjects,
    records,
    witnessStatements: asked,
  });
}

describe("Phase 23 notebook — Witness statements group", () => {
  it("emits the group between People and Objects, with a safe empty message, and ZERO entries when nothing was asked/discovered", () => {
    const model = askedNotebook([]);
    const group = model.groups.find((g) => g.id === "witness-statements")!;
    expect(group.title).toBe("Witness statements");
    expect(group.emptyMessage).not.toBe("");
    expect(group.emptyMessage).not.toMatch(/winner|truth|answer|proc\.|candidate/i);
    expect(group.entries).toEqual([]);
    // Position: people, witness-statements, objects, ...
    expect(model.groups.map((g) => g.id).indexOf("witness-statements")).toBe(1);
  });

  it("lists ASKED statements as '<displayName>' + '<human question> — <summary>'", () => {
    const model = askedNotebook([emilyAsked("TIME")]);
    const entries = groupOf(model, "witness-statements").entries;
    expect(entries).toHaveLength(1);
    expect(entries[0].label).toBe(EMILY_WITNESS_NAME);
    expect(entries[0].detail).toContain("When were you there?");
    expect(entries[0].detail).toContain("heavy impact at approximately 23:42");
    expect(entries[0].evidenceId).toBe(EMILY_TIME_EVIDENCE_ID);
    // The raw enum token never appears in a rendered line.
    const json = JSON.stringify(model);
    expect(json).not.toContain("witness-statements-TIME");
    expect(entries[0].detail).not.toContain("TIME");
  });

  it("lists NEUTRAL asked statements exactly once (asked = player-safe, still no evidence)", () => {
    const model = askedNotebook([
      { ...emilyAsked("SOUND"), statement: makeEmilyNeutralStatement(), evidenceId: null },
    ]);
    const entries = groupOf(model, "witness-statements").entries;
    expect(entries).toHaveLength(1);
    expect(entries[0].detail).toContain("Did you hear anything?");
    expect(entries[0].detail).toContain("Nothing stood out to me");
    expect(entries[0].evidenceId).toBeNull();
    expect(entries[0].read).toBe(false);
  });

  it("re-asking the same question does NOT duplicate the line (idempotent store)", () => {
    const model = askedNotebook([emilyAsked("TIME"), emilyAsked("TIME")]);
    expect(groupOf(model, "witness-statements").entries).toHaveLength(1);
  });

  it("reload persistence: only DISCOVERED, READ interview-sourced records re-derive the line (the client store is empty after a reload)", () => {
    const record = makeEmilyTimeDiscoveryRecord();
    // The same record on an UNDISCOVERED id must stay hidden.
    const model = askedNotebook([], [EMILY_TIME_EVIDENCE_ID], [EMILY_TIME_EVIDENCE_ID], [record]);
    const entries = groupOf(model, "witness-statements").entries;
    expect(entries).toHaveLength(1);
    expect(entries[0].label).toBe(EMILY_WITNESS_NAME);
    expect(entries[0].detail).toContain("When were you there?");
    expect(entries[0].evidenceId).toBe(EMILY_TIME_EVIDENCE_ID);
    expect(entries[0].read).toBe(true);

    // The record on an ID the server never confirmed => nothing surfaces.
    const hidden = makeEmilyTimeDiscoveryRecord({ evidenceId: "record_hidden_99" });
    const hiddenModel = askedNotebook([], [], [], [hidden]);
    const hiddenJson = JSON.stringify(hiddenModel);
    expect(groupOf(hiddenModel, "witness-statements").entries).toEqual([]);
    expect(hiddenJson).not.toContain("heavy impact");
    expect(hiddenJson).not.toContain("record_hidden_99");
  });

  it("DEDUPE: the same (witness, question) from the asked store AND a read record converges to ONE line — record-derived wins (read + evidence)", () => {
    const asked = emilyAsked("TIME");
    const record = makeEmilyTimeDiscoveryRecord();
    const model = askedNotebook([asked], [EMILY_TIME_EVIDENCE_ID], [EMILY_TIME_EVIDENCE_ID], [record]);
    const entries = groupOf(model, "witness-statements").entries;
    expect(entries).toHaveLength(1);
    expect(entries[0].label).toBe(EMILY_WITNESS_NAME);
    expect(entries[0].evidenceId).toBe(EMILY_TIME_EVIDENCE_ID);
    expect(entries[0].read).toBe(true);
  });

  it("DEDUPE even when the backend record does NOT echo the witness id — the evidenceId correlation still folds it into ONE line", () => {
    // A real backend may omit content.witnessId from a record; the live flow
    // (asked store + cached discovery record together) must never duplicate.
    const asked = emilyAsked("TIME");
    const noWitnessId = makeEmilyTimeDiscoveryRecord();
    noWitnessId.content = {
      renderType: "GENERIC_TEXT",
      summary: asked.statement.summary,
      speakerName: EMILY_WITNESS_NAME,
      statement: asked.statement.summary,
      questionType: "TIME",
    };
    const model = askedNotebook(
      [asked],
      [EMILY_TIME_EVIDENCE_ID],
      [EMILY_TIME_EVIDENCE_ID],
      [noWitnessId],
    );
    const entries = groupOf(model, "witness-statements").entries;
    expect(entries).toHaveLength(1);
    expect(entries[0].label).toBe(EMILY_WITNESS_NAME);
    expect(entries[0].detail).toContain("When were you there?");
    expect(entries[0].read).toBe(true);
  });

  it("a People-kind record WITHOUT a closed questionType is NOT an interview source (keeps working in the People group but never in Witness statements)", () => {
    const plainWitness = makeEmilyTimeDiscoveryRecord({
      evidenceId: "record_plain_witness_01",
      content: {
        speakerName: "Sofia Lindgren",
        statement: "I saw a figure hurrying out just before ten.",
      },
    });
    const model = askedNotebook([], ["record_plain_witness_01"], ["record_plain_witness_01"], [plainWitness]);
    expect(groupOf(model, "witness-statements").entries).toEqual([]);
    const people = groupOf(model, "people").entries.map((entry) => entry.label);
    expect(people).toContain("Sofia Lindgren");
  });

  it("hostile summary content stays literal and BOUNDED inside the line", () => {
    const hostile = emilyAsked("OBJECT");
    const longSummary = `<script>alert(1)</script> ${"X".repeat(3000)}`;
    const model = askedNotebook([
      { ...hostile, statement: { summary: longSummary, observations: [] } },
    ]);
    const entries = groupOf(model, "witness-statements").entries;
    expect(entries).toHaveLength(1);
    const detail = entries[0].detail ?? "";
    // The summary is bounded; the hostile literal survives (the NotebookPanel
    // renderer escapes it — React default string rendering — never innerHTML).
    expect(detail.length).toBeLessThan(2500);
    expect(detail).toContain("<script>");
    // The 3000-char tail is truncated inside the 2000-char cap (never the full
    // unbounded string, and never more X's than the cap permits).
    expect((detail.match(/X/g) ?? []).length).toBeLessThanOrEqual(2000);
    expect(detail).not.toContain("X".repeat(2500));
  });

  it("deterministic: identical inputs always produce byte-identical group text (reload stability)", () => {
    const records = [makeEmilyTimeDiscoveryRecord()];
    const a = askedNotebook([], [EMILY_TIME_EVIDENCE_ID], [EMILY_TIME_EVIDENCE_ID], records);
    const b = askedNotebook([], [...[EMILY_TIME_EVIDENCE_ID].sort()], [EMILY_TIME_EVIDENCE_ID], [...records].reverse());
    expect(JSON.stringify(b)).toBe(JSON.stringify(a));
  });
});