import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { buildInvestigationScene } from "../scene/buildInvestigationScene";
import { makeBootstrap, makeCandidates, makeCctvRecord, makeEmailRecord, makeFinancialRecord, makeWorldObject, makeWitnessRecord } from "../scene/testFixtures";
import { EMPTY_PINS } from "./hypothesisStore";
import { buildNotebookModel } from "./notebookModel";
import NotebookPanel from "./NotebookPanel";

/**
 * Detective Notebook panel (Phase 18C) — react-dom/server markup coverage.
 *
 * The scene route only ever mounts this component; these tests render the
 * panel exactly like the route would (a server-derived model + bootstrap
 * candidates) and prove:
 *   - the six groups (incl. Phase 23 "Witness statements") + hypothesis
 *     pickers render with stable test ids;
 *   - the pre-reveal markup contains NO hidden truth / winner / hidden
 *     candidate / `proc.*` / provider material (reqs 3,4,5,13);
 *   - the post-reveal proof board is ABSENT pre-reveal (req 8);
 *   - pin changes flow through onPinsChanged with no other side effect.
 */

function makeModel(discovered: string[], read: string[]) {
  const discoveredSet = new Set(discovered);
  const readSet = new Set(read);
  const bootstrap = makeBootstrap({
    playerKnowledge: {
      discoveredEvidenceIds: [...discoveredSet].sort(),
      readEvidenceIds: [...readSet].sort(),
      visitedLocationIds: ["miller_apartment_kitchen"],
    },
    scene: {
      environmentId: "apartment",
      location: { locationId: "miller_apartment_kitchen", name: "Miller Apartment - Kitchen" },
      worldObjects: [
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
      ],
    },
    candidates: makeCandidates(),
  });
  const sceneModel = buildInvestigationScene(bootstrap);
  return {
    sceneModel,
    model: buildNotebookModel({
      discoveredEvidenceIds: discovered,
      readEvidenceIds: read,
      worldObjects: sceneModel.worldObjects,
      records: [
        ...(read.includes("email_thomas_01") ? [makeEmailRecord()] : []),
        ...(read.includes("record_witness_hall_01") ? [makeWitnessRecord()] : []),
        ...(read.includes("record_financial_04") ? [makeFinancialRecord()] : []),
        ...(read.includes("record_cctv_02") ? [makeCctvRecord()] : []),
      ],
    }),
  };
}

function markupFor(discovered: string[], read: string[]) {
  const { model } = makeModel(discovered, read);
  return renderToStaticMarkup(
    <NotebookPanel
      model={model}
      candidates={makeCandidates()}
      pins={EMPTY_PINS}
      open
      onToggle={() => {}}
      onPinsChanged={() => {}}
    />,
  );
}

const DISCOVERED = ["email_thomas_01", "forensic_knife_match_01", "record_witness_hall_01", "record_financial_04", "record_cctv_02"];
const READ = ["email_thomas_01", "record_witness_hall_01", "record_financial_04", "record_cctv_02"];

describe("Phase 18C notebook panel — structure", () => {
  it("renders the drawer, all six groups and the hypothesis block with stable test ids", () => {
    const html = markupFor(DISCOVERED, READ);
    expect(html).toContain('data-testid="notebook-panel"');
    expect(html).toContain('data-testid="notebook-heading"');
    for (const group of ["people", "witness-statements", "objects", "motive", "timeline", "digital-physical"]) {
      expect(html).toContain(`data-testid="notebook-group-${group}"`);
    }
    expect(html).toContain('data-testid="hypothesis-block"');
    expect(html).toContain('data-testid="hypothesis-suspect"');
    expect(html).toContain('data-testid="hypothesis-motive"');
    expect(html).toContain('data-testid="hypothesis-weapon"');
    expect(html).toContain('data-testid="hypothesis-time"');
    expect(html).toContain("Ada Marsh"); // published candidate option (accusation universe)
  });

  it("renders discovered entries and read markers", () => {
    const html = markupFor(DISCOVERED, READ);
    expect(html).toContain("Kitchen knife");
    expect(html).toContain("Sofia Lindgren");
    expect(html).toContain("An unexpected transfer");
    expect(html).toContain("21:38");
    expect(html).toContain("only what you have discovered is ever listed here");
  });

  it("shows empty-group copy (never fabricated facts) when nothing is read/discovered", () => {
    const html = markupFor([], []);
    for (const group of ["people", "witness-statements", "objects", "motive", "timeline", "digital-physical"]) {
      expect(html).toContain(`data-testid="notebook-group-${group}-empty"`);
    }
  });

  it("closing the drawer hides the body (toggle is app copy + aria)", () => {
    const { model } = makeModel(DISCOVERED, READ);
    const closed = renderToStaticMarkup(
      <NotebookPanel
        model={model}
        candidates={makeCandidates()}
        pins={EMPTY_PINS}
        open={false}
        onToggle={() => {}}
        onPinsChanged={() => {}}
      />,
    );
    expect(closed).toContain('data-testid="notebook-toggle"');
    expect(closed).not.toContain('data-testid="notebook-group-people"');
  });
});

describe("DEF-095 — the notebook drawer has no record-read surface (opening in ACCUSED/REVEALED cannot fetch GET /records/*)", () => {
  it("open/toggle renders ONLY the passed model and fires exactly the given callbacks — never a record read", () => {
    // The scene route mounts the drawer with a PRE-DERIVED server model; the
    // drawer itself has no service/callback that could read records (the only
    // notebook read path is the session's hydrateNotebookRecords, which is
    // PLAYING-gated — pinned in investigationFlow.test.ts). Opening or toggling
    // the drawer in ACCUSED/REVEALED therefore cannot dispatch GET /records/*.
    const { model } = makeModel(DISCOVERED, READ);
    const onToggle = vi.fn();
    const onPinsChanged = vi.fn();

    // Closed render: no side effect at all.
    renderToStaticMarkup(
      <NotebookPanel
        model={model}
        candidates={makeCandidates()}
        pins={EMPTY_PINS}
        open={false}
        onToggle={onToggle}
        onPinsChanged={onPinsChanged}
      />,
    );
    expect(onToggle).not.toHaveBeenCalled();
    expect(onPinsChanged).not.toHaveBeenCalled();

    // Open render: identical model-driven markup — the read entries are EXACTLY
    // the cached records passed in; nothing is fetched or invented on open.
    const openHtml = renderToStaticMarkup(
      <NotebookPanel
        model={model}
        candidates={makeCandidates()}
        pins={EMPTY_PINS}
        open
        onToggle={onToggle}
        onPinsChanged={onPinsChanged}
      />,
    );
    expect(openHtml).toContain('data-testid="notebook-group-people"');
    expect(openHtml).toContain("Sofia Lindgren"); // the cached READ witness record
    expect(openHtml).toContain("Kitchen knife"); // the discovered world object
    expect(onToggle).not.toHaveBeenCalled();
    expect(onPinsChanged).not.toHaveBeenCalled();
  });
});

describe("Phase 18C notebook panel — pre-reveal safety (reqs 3,4,5,8,13)", () => {
  it("the pre-reveal markup contains NO proof board (req 8)", () => {
    const html = markupFor(DISCOVERED, READ);
    expect(html).not.toContain('data-testid="reveal-proof-board"');
    expect(html).not.toContain("proof-card");
  });

  it("the pre-reveal markup contains no hidden truth / winner / hidden candidate markers", () => {
    const html = markupFor(DISCOVERED, READ);
    expect(html).not.toMatch(/winner/i);
    expect(html).not.toMatch(/verdict/i);
    expect(html).not.toContain("Cassius Vane"); // a candidate OUTSIDE the published universe
    expect(html).not.toContain("The murderer is");
    expect(html).not.toMatch(/proc\./);
  });

  it("hostile record content renders inert (escaped, no script/img children)", () => {
    const hostile = makeWitnessRecord({
      content: {
        speakerName: "<script>alert(1)</script>",
        statement: "<img src=x onerror=alert(2)> — ひらがな — 你好 — 😀",
      },
    });
    const { sceneModel } = makeModel(DISCOVERED, READ);
    const model = buildNotebookModel({
      discoveredEvidenceIds: DISCOVERED,
      readEvidenceIds: READ,
      worldObjects: sceneModel.worldObjects,
      records: [hostile],
    });
    const html = renderToStaticMarkup(
      <NotebookPanel model={model} candidates={makeCandidates()} pins={EMPTY_PINS} open onToggle={() => {}} onPinsChanged={() => {}} />,
    );
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).toContain("ひらがな");
    expect(html).toContain("你好");
    expect(html).toContain("😀");
  });
});

describe("Phase 18C notebook panel — pin interaction", () => {
  it("delegates pin edits to onPinsChanged only (no storage/network side effects in the component)", () => {
    const { model } = makeModel(DISCOVERED, READ);
    const onPinsChanged = vi.fn();
    const html = renderToStaticMarkup(
      <NotebookPanel
        model={model}
        candidates={makeCandidates()}
        pins={{ suspect: "suspect_beta", motive: null, weapon: null, time: null }}
        open
        onToggle={() => {}}
        onPinsChanged={onPinsChanged}
      />,
    );
    expect(html).toContain('value="suspect_beta"');
    // The component itself only renders; the route owns persistence.
    expect(onPinsChanged).not.toHaveBeenCalled();
  });

  it("renders a clear 'you must explicitly accuse' context note (pins never submit anything)", () => {
    const html = markupFor(DISCOVERED, READ);
    expect(html).toContain("never affect the case, the solver or your score");
    expect(html).toContain("stays empty");
  });
});