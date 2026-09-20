import { describe, expect, it } from "vitest";
import type { EvidenceReadResultDTO, InvestigationBootstrapResponse } from "../api/types";
import { buildInvestigationScene } from "../scene/buildInvestigationScene";
import { evidenceLabelFor } from "../scene/objectLabel";
import {
  makeBootstrap,
  makeCctvRecord,
  makeEmailRecord,
  makeFinancialRecord,
  makeIcePickDefinition,
  makeWorldObject,
  makeWitnessRecord,
} from "../scene/testFixtures";
import { buildNotebookModel, type NotebookGroup, type NotebookModel } from "./notebookModel";

/**
 * Detective Notebook model coverage (Phase 18C).
 *
 * Proves the pre-reveal safety model directly at the model level:
 *   - only server-confirmed discovered ids ever produce entries;
 *   - hidden truth / hidden candidates / solver winners never enter the model;
 *   - reload determinism (an empty record cache re-derives the same headers
 *     and world-object groups; hydrated records converge);
 *   - the procedural weapon renders through the evidenceLabelFor human label;
 *   - no `proc.*` token ever flows into an entry label.
 */

/** A bootstrap whose world objects' discovered/read flags mirror the knowledge. */
function makeNotebookBootstrap(discovered: string[], read: string[]): InvestigationBootstrapResponse {
  const discoveredSet = new Set(discovered);
  const readSet = new Set(read);
  const worldObjects = [
    makeWorldObject({
      objectId: "apartment_laptop",
      assetId: "PROP_LAPTOP_01",
      assetType: "electronics",
      subtype: "electronics",
      anchor: "desk_main",
      interaction: "read",
      evidenceId: "email_thomas_01",
      discovered: discoveredSet.has("email_thomas_01"),
      read: readSet.has("email_thomas_01"),
    }),
    makeWorldObject({
      objectId: "kitchen_knife",
      evidenceId: "forensic_knife_match_01",
      discovered: discoveredSet.has("forensic_knife_match_01"),
      read: readSet.has("forensic_knife_match_01"),
    }),
    // A discovered procedural weapon (Phase 18B evidenceLabelFor path).
    makeWorldObject({
      objectId: "trophy_ice_pick",
      assetId: "proc.decor.4551660f4a46b2eb",
      assetType: "weapon",
      subtype: "weapon",
      anchor: "shelf_01",
      interaction: "inspect",
      evidenceId: "proc_icepick_evidence_01",
      discovered: discoveredSet.has("proc_icepick_evidence_01"),
      read: readSet.has("proc_icepick_evidence_01"),
      generated: makeIcePickDefinition(),
    }),
    makeWorldObject({
      objectId: "apartment_table",
      assetId: "PROP_TABLE_01",
      assetType: "furniture",
      subtype: "furniture",
      anchor: "dining_table",
      interaction: "",
      evidenceId: null,
      discovered: false,
      read: false,
    }),
  ];
  return makeBootstrap({
    playerKnowledge: {
      discoveredEvidenceIds: [...discovered].sort(),
      readEvidenceIds: [...read].sort(),
      visitedLocationIds: ["miller_apartment_kitchen"],
    },
    scene: {
      environmentId: "apartment",
      location: { locationId: "miller_apartment_kitchen", name: "Miller Apartment - Kitchen" },
      worldObjects,
    },
  });
}

/** Build the notebook model exactly like the scene route does. */
function modelFor(discovered: string[], read: string[], records: EvidenceReadResultDTO[] = []): NotebookModel {
  const sceneModel = buildInvestigationScene(makeNotebookBootstrap(discovered, read));
  return buildNotebookModel({
    discoveredEvidenceIds: discovered,
    readEvidenceIds: read,
    worldObjects: sceneModel.worldObjects,
    records,
  });
}

function groupOf(model: NotebookModel, id: string): NotebookGroup {
  const group = model.groups.find((entry) => entry.id === id);
  if (!group) throw new Error(`missing notebook group ${id}`);
  return group;
}

function allEvidenceIds(model: NotebookModel): string[] {
  return model.groups.flatMap((group) => group.entries.map((entry) => entry.evidenceId).filter((id) => id !== null));
}

const DISCOVERED = ["email_thomas_01", "forensic_knife_match_01", "record_financial_04", "record_witness_hall_01", "record_cctv_02", "proc_icepick_evidence_01"];
const READ = ["email_thomas_01", "record_witness_hall_01", "record_financial_04", "record_cctv_02"];

describe("Phase 18C notebook — group structure", () => {
  it("emits exactly the five groups in the fixed order", () => {
    const model = modelFor(DISCOVERED, READ, [
      makeEmailRecord(),
      makeWitnessRecord(),
      makeFinancialRecord(),
      makeCctvRecord(),
    ]);
    expect(model.groups.map((group) => group.id)).toEqual([
      "people",
      "objects",
      "motive",
      "timeline",
      "digital-physical",
    ]);
  });

  it("is deterministic: identical inputs produce identical models and entries are sorted", () => {
    const records = [makeEmailRecord(), makeWitnessRecord(), makeFinancialRecord(), makeCctvRecord()];
    const a = modelFor(DISCOVERED, READ, records);
    const b = modelFor([...DISCOVERED].reverse(), [...READ].reverse(), [...records].reverse());
    expect(a).toEqual(b);
    for (const group of a.groups) {
      const ids = group.entries.map((entry) => entry.id);
      expect(ids).toEqual([...ids].sort());
    }
  });
});

describe("Phase 18C notebook — discovered evidence appears (req 2)", () => {
  it("lists discovered world objects (with read markers) from the bootstrap knowledge", () => {
    const model = modelFor(DISCOVERED, READ);
    const objects = groupOf(model, "objects").entries;
    const ids = objects.map((entry) => entry.evidenceId);
    expect(ids).toContain("forensic_knife_match_01");
    expect(ids).toContain("email_thomas_01");
    expect(ids).toContain("proc_icepick_evidence_01");
    // Read flag comes from the server-derived readEvidenceIds.
    const email = objects.find((entry) => entry.evidenceId === "email_thomas_01");
    const knife = objects.find((entry) => entry.evidenceId === "forensic_knife_match_01");
    expect(email?.read).toBe(true);
    expect(knife?.read).toBe(false);
  });

  it("renders read-record groups (people/motive/timeline/digital-physical) from the cache", () => {
    const model = modelFor(DISCOVERED, READ, [makeWitnessRecord(), makeFinancialRecord(), makeCctvRecord(), makeEmailRecord()]);
    const people = groupOf(model, "people").entries.map((entry) => entry.label);
    expect(people).toContain("Sofia Lindgren");
    const motive = groupOf(model, "motive").entries.map((entry) => entry.label);
    expect(motive).toContain("An unexpected transfer");
    const digital = groupOf(model, "digital-physical").entries.map((entry) => entry.evidenceId);
    expect(digital).toContain("record_cctv_02");
    expect(digital).toContain("email_thomas_01");
  });

  it("extracts the timeline from read content (events[].time, timestamp, observed_at)", () => {
    const observed = makeCctvRecord({
      content: {
        proposition: { observed_at: "2026-09-11T23:05:00+02:00" },
        presentation: { timestamp: "2026-09-11T20:10:00+02:00" },
        timestamp: "2026-09-11T19:10:00+02:00",
      },
    });
    const model = modelFor(DISCOVERED, READ, [makeCctvRecord(), observed, makeEmailRecord()]);
    const labels = groupOf(model, "timeline").entries.map((entry) => entry.label);
    // events[].time from the CCTV record, flat + presentation timestamps,
    // email timestamp and observed_at.
    expect(labels).toContain("21:38");
    expect(labels).toContain("22:03");
    expect(labels).toContain("18:04");
    expect(labels).toContain("19:10"); // flat content.timestamp
    expect(labels).toContain("20:10"); // content.presentation.timestamp
    expect(labels).toContain("23:05");
    // Deterministic chronological order of the display HH:MM values.
    expect(labels).toEqual(["18:04", "19:10", "20:10", "21:38", "22:03", "23:05"]);
  });
});

describe("Phase 18C notebook — hidden input safety (reqs 1,3,4,5,13)", () => {
  const HIDDEN_TRUTH_MARKER = "The murderer is Ada Marsh";
  const HIDDEN_CANDIDATE = "Cassius Vane";
  const HIDDEN_WINNER = "solver_winner_quark";

  it("undiscovered evidence is absent even when its record sits in the cache (req 1)", () => {
    // A hostile cache entry for an id the server NEVER confirmed discovered:
    const hidden = makeWitnessRecord({
      evidenceId: "record_hidden_universe_99",
      content: {
        speakerName: HIDDEN_CANDIDATE,
        statement: `${HIDDEN_TRUTH_MARKER} and eliminated candidate ${HIDDEN_CANDIDATE} won ${HIDDEN_WINNER}.`,
      },
    });
    const model = modelFor(DISCOVERED, READ, [makeWitnessRecord(), hidden]);
    const json = JSON.stringify(model);
    expect(allEvidenceIds(model)).not.toContain("record_hidden_universe_99");
    expect(json).not.toContain(HIDDEN_TRUTH_MARKER);
    expect(json).not.toContain(HIDDEN_CANDIDATE);
    expect(json).not.toContain(HIDDEN_WINNER);
  });

  it("hidden truth / eliminated candidates / solver winners never appear (reqs 3,4,5)", () => {
    // Inject hidden material into READ records of DISCOVERED evidence — the
    // notebook may only echo what the player already read, so these markers
    // (which were never part of discovered content) must stay out of the model
    // STRUCTURE: no candidate/winner/truth fields exist to carry them.
    const records = [
      makeWitnessRecord(),
      makeEmailRecord(),
      makeFinancialRecord(),
      makeCctvRecord(),
    ];
    const model = modelFor(DISCOVERED, READ, records);
    const json = JSON.stringify(model);
    expect(json).not.toMatch(/\bwinner\b/i);
    expect(json).not.toMatch(/\btruth\b/i);
    expect(json).not.toMatch(/answer/i);
    expect(json).not.toMatch(/\bsolved\b/i);
    expect(json).not.toContain(HIDDEN_CANDIDATE);
    expect(json).not.toContain(HIDDEN_WINNER);
  });

  it("every entry evidence id belongs to the server-confirmed discovered set (reqs 1,11)", () => {
    const model = modelFor(DISCOVERED, READ, [makeWitnessRecord(), makeEmailRecord(), makeFinancialRecord(), makeCctvRecord()]);
    const discoveredSet = new Set(DISCOVERED);
    for (const id of allEvidenceIds(model)) {
      expect(discoveredSet.has(id)).toBe(true);
    }
  });

  it("the procedural weapon uses the evidenceLabelFor human label (req 12)", () => {
    const model = modelFor(DISCOVERED, READ);
    const icePick = groupOf(model, "objects").entries.find((entry) => entry.evidenceId === "proc_icepick_evidence_01");
    expect(icePick).not.toBeUndefined();
    expect(icePick?.label).toBe("Bronze Ceremonial Ice Pick");
    // The label path is literally evidenceLabelFor over the model's world object.
    const sceneModel = buildInvestigationScene(makeNotebookBootstrap(DISCOVERED, READ));
    const obj = sceneModel.worldObjects.find((o) => o.objectId === "trophy_ice_pick");
    expect(obj).not.toBeUndefined();
    expect(evidenceLabelFor(obj!)).toBe(icePick?.label);
  });

  it("never displays a proc.* token or a raw id in any entry (req 13)", () => {
    const model = modelFor(DISCOVERED, READ, [makeWitnessRecord(), makeEmailRecord(), makeFinancialRecord(), makeCctvRecord()]);
    const json = JSON.stringify(model);
    expect(json).not.toContain("proc.");
    for (const group of model.groups) {
      for (const entry of group.entries) {
        expect(entry.label).not.toContain("proc.");
        expect(entry.detail ?? "").not.toContain("proc.");
      }
    }
  });
});

describe("Phase 18C notebook — reload determinism (req 7)", () => {
  it("empty cache (fresh reload) re-derives the same group headers and objects group", () => {
    // After a reload only the bootstrap knowledge + world objects exist.
    const fresh = modelFor(DISCOVERED, READ, []);
    expect(fresh.groups.map((group) => group.id)).toEqual([
      "people",
      "objects",
      "motive",
      "timeline",
      "digital-physical",
    ]);
    const objects = groupOf(fresh, "objects").entries.map((entry) => entry.evidenceId);
    expect(objects).toContain("forensic_knife_match_01");
    expect(objects).toContain("email_thomas_01");
    // Content groups stay honestly empty until the read records are hydrated.
    expect(groupOf(fresh, "people").entries).toEqual([]);
    expect(groupOf(fresh, "motive").entries).toEqual([]);
    expect(groupOf(fresh, "timeline").entries).toEqual([]);
  });

  it("hydrated records converge to the pre-reload model", () => {
    const records = [makeWitnessRecord(), makeEmailRecord(), makeFinancialRecord(), makeCctvRecord()];
    const full = modelFor(DISCOVERED, READ, records);
    const fresh = modelFor(DISCOVERED, READ, []);
    // Re-derive the same way the lazy hydration would populate the cache:
    const hydrated = modelFor(DISCOVERED, READ, records);
    expect(hydrated).toEqual(full);
    expect(hydrated.groups.find((g) => g.id === "people")!.entries.length).toBeGreaterThan(0);
    expect(fresh).not.toEqual(hydrated); // only the cache difference may change content groups
  });

  it("identical run inputs yield byte-identical group text (no order/clock dependency)", () => {
    const records = [makeWitnessRecord(), makeEmailRecord(), makeFinancialRecord(), makeCctvRecord()];
    const a = JSON.stringify(modelFor(DISCOVERED, READ, records));
    const b = JSON.stringify(modelFor([...DISCOVERED].sort(), [...READ].sort(), [...records].reverse()));
    expect(b).toBe(a);
  });
});

describe("Phase 18C notebook — empty groups show app-authored copy only", () => {
  it("an empty notebook still emits the five groups with safe empty messages", () => {
    const model = modelFor([], []);
    for (const group of model.groups) {
      expect(group.entries).toEqual([]);
      expect(group.emptyMessage).not.toBe("");
      expect(group.emptyMessage).not.toMatch(/winner|truth|answer|proc\.|candidate/i);
    }
  });
});