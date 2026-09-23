import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { NullEngine } from "@babylonjs/core/Engines";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { AccusationRequest, AccusationResponse, InteractionResultDTO } from "../api/types";
import { AccusationFlow } from "../accusation/accusationFlow";
import AccusationPanel from "../accusation/AccusationPanel";
import { buildNotebookModel } from "../notebook/notebookModel";
import RevealScreen from "../reveal/RevealScreen";
import { FALLBACK_COLOR } from "./assetRegistry";
import { applyKnowledgeToSceneModel, bindEvidenceToSceneModel, buildInvestigationScene } from "./buildInvestigationScene";
import { summarizeDiscovery } from "./discoverySummary";
import { buildGeneratedComposite } from "./generatedRenderer";
import {
  InvestigationSession,
  type InvestigationServices,
} from "./investigationFlow";
import {
  evidenceLabelFor,
  focusBadgesFor,
  isProceduralArtifact,
  semanticLabelOrNull,
} from "./objectLabel";
import { discoveredCaptionsForWorld } from "./objectCaption";
import { tooltipLabelFor } from "./objectTooltip";
import { createInvestigationScene, meshNameFor, objectIdFromMeshName, type RenderOptions } from "./renderInvestigation";
import {
  COFFEE_MUG_PROC_ASSET_ID,
  FORK_EVIDENCE_ID,
  FORK_PROC_ASSET_ID,
  makeBootstrap,
  makeCoffeeMugDefinition,
  makeCoffeeMugWorldObject,
  makeEmailRecord,
  makeForkDefinition,
  makeForkWorldObject,
  makeRichWorldBootstrap,
  stripUndiscoveredEvidenceIds,
  TEST_TOKEN,
} from "./testFixtures";
import { validateGeneratedDefinition } from "./validation";

/**
 * Phase 19E — frontend verification of the GENERALIZED SEMANTIC WORLD OBJECT
 * pipeline (the "fork as murder weapon" reproduction case).
 *
 * The backend now: (a) deterministically injects the CaseTruth weapon as a
 * REQUIRED semantic world object (semantic objectId "fork" with a proc.*
 * render asset), (b) resolves unknown objects via catalog variant OR
 * procedural AssetSpec, (c) publishes richer worlds (≤ MAX_WORLD_OBJECTS_PER
 * KIT, default 32) with many decorative/interactive objects, and (d) NEVER
 * lets the render assetId replace the semantic identity.
 *
 * This suite PROVES the frontend half of that contract with deterministic
 * canned fixtures (the backend work is validated by backend-dev's own suite):
 *   1. any validated GeneratedAssetDefinition (fork / coffee mug) RENDERS via
 *      the existing generated renderer — never the neutral fallback gray;
 *   2. the semantic human label ("Fork", "Coffee Mug") is the ONLY UI text —
 *      zero raw proc.* / objectId / assetId leak in the label path, tooltip,
 *      caption, discovery strip, notebook and the object list strings;
 *   3. INTERACTION: evidence-linked fork -> discovery -> panel + counter;
 *      INTERACTIVE no-evidence mug -> the Phase 19C "Nothing relevant was
 *      found on <label>." copy;
 *   4. richer published worlds build + render with evidence priority and
 *      decorative objects NOT selectable as evidence;
 *   5. a DTO/e2e-style end-to-end proof: fork bootstrap -> scene -> interact
 *      inspect -> discovery -> accusation picker contains "Fork" -> reveal
 *      shows "Fork" (the real-browser Playwright run is infeasible here — no
 *      backend process — so this is proven at the unit/DTO level).
 */

const PT_ID = "PT-test-0001";
const NOOP_CANVAS = {} as HTMLCanvasElement;

function nullEngineOptions(
  overrides: { onPick?: (objectId: string) => void } = {},
): RenderOptions {
  return {
    createEngine: () => new NullEngine(),
    cameraControl: false,
    startRenderLoop: () => {},
    ...overrides,
  };
}

/**
 * The semantic fork bootstrap: the golden world + the evidence-linked fork
 * weapon (proc.* render identity). The pre-reveal variant strips the
 * evidenceId (PD-SEC-01) exactly like the live backend publishes.
 */
function forkBootstrap(stripEvidenceIds = false): ReturnType<typeof makeBootstrap> {
  const bootstrap = makeBootstrap();
  bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, makeForkWorldObject()];
  return stripEvidenceIds ? stripUndiscoveredEvidenceIds(bootstrap) : bootstrap;
}

function summaryFromSessionLocal(
  session: InvestigationSession,
  model: ReturnType<typeof buildInvestigationScene>,
) {
  return summarizeDiscovery(
    session.discoveredEvidenceIdsSnapshot(),
    session.readEvidenceIdsSnapshot(),
    model.worldObjects,
    new Map(session.discoveredRecordTitles()),
  );
}

/* ======================================================================
 * 1. RENDERER — arbitrary procedural objects render, never the fallback gray
 * ==================================================================== */

describe("Phase 19E — the procedural renderer proves ANY validated definition renders", () => {
  it("compiles the fork definition into REAL parts (prongs + handle), never the neutral fallback", () => {
    const built = buildGeneratedComposite(makeForkDefinition());
    expect(built.parts).toHaveLength(2);
    for (const part of built.parts) {
      expect(part.color).not.toBe(FALLBACK_COLOR);
      expect(Number.isFinite(part.size.x) && Number.isFinite(part.size.y) && Number.isFinite(part.size.z)).toBe(true);
    }
    // Prongs are the thin flat steel heads; the handle is the grip.
    expect(built.parts[0]).toMatchObject({ kind: "box", color: "#b9c0c8" });
    expect(built.parts[1]).toMatchObject({ kind: "box", color: "#b9c0c8" });
    // Declared picking extent rides through verbatim (bounded 0.15..10).
    expect(built.hitbox).toEqual({ x: 0.15, y: 0.5, z: 0.15 });
    // Deterministic: identical definitions compile to identical composites.
    expect(buildGeneratedComposite(makeForkDefinition())).toEqual(built);
  });

  it("compiles the coffee-mug definition into REAL parts (cup + handle), never the neutral fallback", () => {
    const coffee = makeCoffeeMugDefinition();
    expect(validateGeneratedDefinition(coffee)).not.toBeNull(); // client gate accepts it
    const built = buildGeneratedComposite(coffee);
    expect(built.parts).toHaveLength(2);
    for (const part of built.parts) expect(part.color).not.toBe(FALLBACK_COLOR);
    expect(built.parts[0]).toMatchObject({ kind: "cylinder", color: "#e8e4dc" });
    expect(built.hitbox).toEqual({ x: 0.2, y: 0.3, z: 0.2 });
  });

  it("the fork definition passes the Phase 13 client validation gate (a real backend definition would)", () => {
    expect(validateGeneratedDefinition(makeForkDefinition())).not.toBeNull();
  });

  it("renders the fork object on the NullEngine with finite parts and NO proc.* mesh names", () => {
    const model = buildInvestigationScene(forkBootstrap());
    const result = createInvestigationScene(NOOP_CANVAS, model, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    const root = result.scene.getNodeByName(meshNameFor("fork")) as Mesh | null;
    expect(root, "fork root mesh").not.toBeNull();
    for (let index = 0; index < 2; index++) {
      const part = result.scene.getNodeByName(`pd_part_fork_${index}`) as Mesh | null;
      expect(part, `pd_part_fork_${index}`).not.toBeNull();
      expect(part!.material instanceof StandardMaterial).toBe(true);
      expect(Number.isFinite(part!.position.x) && Number.isFinite(part!.position.y) && Number.isFinite(part!.position.z)).toBe(true);
    }
    // The whole mesh hierarchy is named from the SEMANTIC objectId — the proc.*
    // render assetId / hash NEVER becomes a mesh name (no render-id leak).
    const rootName = root!.name;
    expect(rootName).toContain("fork");
    expect(rootName).not.toContain("proc.");
    expect(objectIdFromMeshName(rootName)).toBe("fork");
    result.dispose();
  });

  it("the coffee mug renders on the NullEngine too (a second arbitrary object)", () => {
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, makeCoffeeMugWorldObject()];
    const result = createInvestigationScene(NOOP_CANVAS, buildInvestigationScene(bootstrap), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    expect(result.scene.getNodeByName("pd_obj_coffee_mug")).not.toBeNull();
    expect(result.scene.getNodeByName("pd_part_coffee_mug_1")).not.toBeNull();
    result.dispose();
  });
});

/* ======================================================================
 * 2. SEMANTIC LABEL — "Fork"/"Coffee Mug" everywhere a name is shown, zero ids
 * ==================================================================== */

describe("Phase 19E — semantic labels with NO render-id / objectId leak", () => {
  function forkWorldObject() {
    const model = buildInvestigationScene(forkBootstrap());
    const found = model.worldObjects.find((o) => o.objectId === "fork");
    if (!found) throw new Error("fork missing from model");
    return found;
  }

  it("the fork's label path yields the HUMAN name, never proc.* / objectId / assetId", () => {
    const fork = forkWorldObject();
    expect(fork.assetId).toBe(FORK_PROC_ASSET_ID);
    expect(fork.objectId).toBe("fork");
    expect(fork.label).toBeNull(); // generated objects never echo a server label
    expect(isProceduralArtifact(fork)).toBe(true);

    const label = evidenceLabelFor(fork);
    expect(label).toBe("Fork");
    expect(label).not.toContain("proc.");
    expect(label).not.toBe(FORK_PROC_ASSET_ID);
    expect(label).not.toBe("fork");
    expect(semanticLabelOrNull(fork)).toBe("Fork");
    expect(focusBadgesFor(fork)).toEqual({ procedural: true, validatedGeometry: true });
  });

  it("the object list text (the exact expression scene.tsx renders) is the semantic label", () => {
    const model = buildInvestigationScene(forkBootstrap());
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));
    // The three list branches (interactable button, hidden a11y label, plain
    // decorative span) all render `evidenceLabelFor(obj)` after Phase 19E.
    expect(evidenceLabelFor(byId.get("fork")!)).toBe("Fork");
    expect(evidenceLabelFor(byId.get("kitchen_knife")!)).toBe("Kitchen knife");
    expect(evidenceLabelFor(byId.get("vase_01")!)).toBe("Vase");
  });

  it("the coffee mug label is its canonicalName humanized (no raw id)", () => {
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, makeCoffeeMugWorldObject()];
    const model = buildInvestigationScene(bootstrap);
    const mug = model.worldObjects.find((o) => o.objectId === "coffee_mug")!;
    expect(mug.assetId).toBe(COFFEE_MUG_PROC_ASSET_ID);
    expect(evidenceLabelFor(mug)).toBe("Coffee Mug");
    expect(semanticLabelOrNull(mug)).toBe("Coffee Mug");
    expect(evidenceLabelFor(mug)).not.toContain("proc.");
    expect(evidenceLabelFor(mug)).not.toBe("coffee_mug");
    expect(isProceduralArtifact(mug)).toBe(true);
  });

  it("the hover TOOLTIP carries the semantic label for the fork (no null, no id)", () => {
    const model = buildInvestigationScene(forkBootstrap());
    expect(tooltipLabelFor(model, "fork")).toBe("Fork");
    expect(tooltipLabelFor(model, "fork")).not.toContain("proc.");
  });

  it("the discovered-object floating CAPTION uses the semantic label when no read title exists", () => {
    const model = buildInvestigationScene(forkBootstrap());
    const merged = applyKnowledgeToSceneModel(model, {
      discoveredEvidenceIds: [FORK_EVIDENCE_ID],
      readEvidenceIds: [],
    });
    const captions = discoveredCaptionsForWorld(merged.worldObjects, new Map());
    const fork = captions.find((c) => c.objectId === "fork");
    expect(fork, "discovered fork gets a caption").not.toBeUndefined();
    expect(fork!.text).toBe("Fork");
    expect(fork!.text).not.toContain("proc.");
  });

  it("the discovery strip title falls back to the semantic label for a discovered-but-unread fork", () => {
    const model = buildInvestigationScene(forkBootstrap());
    const summary = summarizeDiscovery([FORK_EVIDENCE_ID], [], model.worldObjects, new Map());
    expect(summary.entries[0].title).toBe("Fork");
    expect(summary.entries[0].title).not.toContain("proc.");
  });

  it("the Detective Notebook Objects group labels the discovered fork 'Fork' (never an id)", () => {
    const model = buildInvestigationScene(forkBootstrap());
    const merged = applyKnowledgeToSceneModel(model, {
      discoveredEvidenceIds: [FORK_EVIDENCE_ID],
      readEvidenceIds: [FORK_EVIDENCE_ID],
    });
    const notebook = buildNotebookModel({
      discoveredEvidenceIds: [FORK_EVIDENCE_ID],
      readEvidenceIds: [FORK_EVIDENCE_ID],
      worldObjects: merged.worldObjects,
      records: [],
    });
    const objects = notebook.groups.find((g) => g.id === "objects")!;
    const forkEntry = objects.entries.find((e) => e.id === `objects-${FORK_EVIDENCE_ID}`);
    expect(forkEntry, "fork notebook entry").not.toBeUndefined();
    expect(forkEntry!.label).toBe("Fork");
    expect(forkEntry!.label).not.toContain("proc.");
    expect(forkEntry!.label).not.toBe("fork");
  });

  it("CONSOLIDATED LEAK SCAN: every player-visible surface of a fork scene carries only human text", () => {
    const model = buildInvestigationScene(forkBootstrap());
    const merged = applyKnowledgeToSceneModel(model, {
      discoveredEvidenceIds: [FORK_EVIDENCE_ID],
      readEvidenceIds: [FORK_EVIDENCE_ID],
    });
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));

    // 1. object list labels (the scene.tsx expression for every branch),
    // 2. hover tooltip labels,
    // 3. discovered floating captions,
    // 4. discovery strip titles,
    // 5. notebook Object-group labels,
    // 6. focus label + badges.
    const objectListLabels = model.worldObjects.map((obj) => evidenceLabelFor(obj));
    const tooltipLabels = model.worldObjects.map((obj) => tooltipLabelFor(model, obj.objectId));
    const captions = discoveredCaptionsForWorld(merged.worldObjects, new Map()).map((c) => c.text);
    const strip = summarizeDiscovery([FORK_EVIDENCE_ID], [], merged.worldObjects, new Map()).entries.map((e) => e.title);
    const notebook = buildNotebookModel({
      discoveredEvidenceIds: [FORK_EVIDENCE_ID],
      readEvidenceIds: [FORK_EVIDENCE_ID],
      worldObjects: merged.worldObjects,
      records: [],
    });
    const notebookLabels = notebook.groups.flatMap((g) => g.entries.map((e) => e.label));
    const fork = byId.get("fork")!;
    const focusText = [evidenceLabelFor(fork), focusBadgesFor(fork).procedural ? "Procedural Artifact" : "", focusBadgesFor(fork).validatedGeometry ? "Validated Geometry" : ""].join(" ");

    const allVisible: string[] = [
      ...objectListLabels,
      ...(tooltipLabels.filter((label) => label !== null) as string[]),
      ...captions,
      ...strip,
      ...notebookLabels,
      focusText,
    ];
    const joined = allVisible.join("\u0000");
    for (const forbidden of ["proc.", FORK_PROC_ASSET_ID, "forensic_fork_match_01", "fork", "kitchen_counter", "desk_main"]) {
      expect(joined, `no raw id/render token may appear in player-visible text (${forbidden})`).not.toContain(forbidden);
    }
    expect(joined).toContain("Fork");
  });
});

/* ======================================================================
 * 3. INTERACTION — evidence-linked discovery AND nothing-relevant copy
 * ==================================================================== */

describe("Phase 19E — interaction paths for the new semantic-role objects", () => {
  /** Realistic services for the fork bootstrap (pre-reveal, evidence stripped). */
  function forkServices(overrides: Partial<InvestigationServices> = {}): InvestigationServices {
    const devices: InvestigationServices = {
      getInvestigation: vi.fn(async () => forkBootstrap(true)),
      interactObject: vi.fn(
        async (): Promise<InteractionResultDTO> => ({
          objectId: "fork",
          interaction: "inspect",
          evidenceId: FORK_EVIDENCE_ID,
          discovery: {
            evidenceId: FORK_EVIDENCE_ID,
            kind: "forensic",
            title: "Fork",
            interaction: "inspect",
            state: "discovered",
          },
          result: "interacted",
        }),
      ),
      readRecord: vi.fn(async () => makeEmailRecord({ evidenceId: FORK_EVIDENCE_ID, title: "Fork" })),
      ...overrides,
    };
    return devices;
  }

  const noScene = null as null;

  it("evidence-linked semantic weapon: interact -> server discovery -> panel + counter + no raw ids", async () => {
    const services = forkServices();
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, {
      playthroughId: PT_ID,
    });
    const outcome = await session.start(null);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) throw new Error("expected ok");

    // Pre-reveal the fork carries NO evidence id (PD-SEC-01) and no truth.
    const before = session.sceneModel!.worldObjects.find((o) => o.objectId === "fork")!;
    expect(before.evidenceId).toBeNull();
    expect(before.discovered).toBe(false);

    const feedback = await session.interact("fork");

    // The server-bound discovery publishes correctly (submit the SEMANTIC id).
    expect(feedback.error).toBeNull();
    expect(feedback.toast?.text).toBe("Discovered: Fork");
    expect(feedback.toast?.evidenceId).toBe(FORK_EVIDENCE_ID);
    expect(feedback.record).not.toBeNull(); // the panel opens with the record
    expect(services.readRecord).toHaveBeenCalledWith(PT_ID, FORK_EVIDENCE_ID, TEST_TOKEN);

    // The interacted object's knowledge flips WITHOUT a reload (DEF-072).
    const fork = session.sceneModel!.worldObjects.find((o) => o.objectId === "fork")!;
    expect(fork.evidenceId).toBe(FORK_EVIDENCE_ID);
    expect(fork.discovered).toBe(true);
    expect(fork.read).toBe(true);

    // The discovered counter increments; the strip uses the human label.
    const summary = summaryFromSessionLocal(session, session.sceneModel!);
    expect(summary.discoveredCount).toBe(1);
    expect(summary.entries[0].title).toBe("Fork");
  });

  it("INTERACTIVE decorative (coffee mug, no evidence): the Phase 19C 'Nothing relevant was found on <label>.' copy", async () => {
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, makeCoffeeMugWorldObject()];
    const services: InvestigationServices = {
      getInvestigation: vi.fn(async () => bootstrap),
      interactObject: vi.fn(
        async (): Promise<InteractionResultDTO> => ({
          objectId: "coffee_mug",
          interaction: "inspect",
          evidenceId: null,
          discovery: null,
          result: "interacted",
        }),
      ),
      readRecord: vi.fn(async () => makeEmailRecord()),
    };
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, { playthroughId: PT_ID });
    const outcome = await session.start(null);
    expect(outcome.ok).toBe(true);

    const feedback = await session.interact("coffee_mug");
    expect(feedback.error).toBeNull();
    // Human label anchored — never "here." and never a raw proc.*/id token.
    expect(feedback.toast?.text).toBe("Nothing relevant was found on the Coffee Mug.");
    expect(feedback.record).toBeNull();
    expect(services.readRecord).not.toHaveBeenCalled();

    // No knowledge changed: decorative objects consume no interaction state.
    const mug = session.sceneModel!.worldObjects.find((o) => o.objectId === "coffee_mug")!;
    expect(mug.discovered).toBe(false);
    expect(mug.read).toBe(false);
    expect(session.discoveredEvidenceIdsSnapshot()).toEqual([]);
    expect(summaryFromSessionLocal(session, session.sceneModel!).discoveredCount).toBe(0);
  });
});

/* ======================================================================
 * 4. RICHER WORLD — many objects, determinism, evidence priority, decor out
 * ==================================================================== */

describe("Phase 19E — richer published worlds (MAX_WORLD_OBJECTS_PER_KIT ≤ 32)", () => {
  it("builds the 17-object rich world: every object renders, no fallback, unique sorted ids", () => {
    const model = buildInvestigationScene(makeRichWorldBootstrap());
    expect(model.worldObjects).toHaveLength(17);

    const ids = model.worldObjects.map((o) => o.objectId);
    expect(new Set(ids).size).toBe(ids.length); // unique
    expect(ids).toEqual([...ids].sort()); // deterministic objectId order

    for (const obj of model.worldObjects) {
      expect(obj.unknownAsset, `${obj.objectId} must never be an unknown fallback`).toBe(false);
      if (obj.assetId.startsWith("proc.")) {
        expect(obj.generated).not.toBeNull();
        expect(obj.generatedParts).not.toBeNull();
        for (const part of obj.generatedParts!) expect(part.color).not.toBe(FALLBACK_COLOR);
      } else if (!obj.assetId.startsWith("procedural_")) {
        expect(obj.color).not.toBe(FALLBACK_COLOR);
      }
    }
    // Deterministic: the same rich world builds deep-equal twice.
    expect(buildInvestigationScene(makeRichWorldBootstrap())).toEqual(model);
  });

  it("gives evidence-relevant objects INTERACTION affordance (payload-driven)", () => {
    const model = buildInvestigationScene(makeRichWorldBootstrap());
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));

    // Evidence-linked objects are interactable (payload-driven DTO interaction).
    for (const id of ["kitchen_knife", "apartment_laptop", "letter_opener", "scissors", "fork"]) {
      expect(byId.get(id)!.interactionWorks, `${id} affordance`).toBe(true);
    }
    // The fork (evidence) carries the HUMAN label through every UI surface.
    expect(evidenceLabelFor(byId.get("fork")!)).toBe("Fork");
  });

  it("decorative objects are NOT selectable-as-evidence: no evidenceId, no discovery trigger, not interactable", () => {
    const model = buildInvestigationScene(makeRichWorldBootstrap());
    const byId = new Map(model.worldObjects.map((o) => [o.objectId, o]));

    for (const id of ["glass_bottle", "claw_hammer", "custom_trophy", "kitchen_clock", "desk_lamp", "wristwatch", "vase_01", "apartment_table"]) {
      const obj = byId.get(id)!;
      expect(obj.evidenceId, `${id} must carry no evidence association`).toBeNull();
      expect(obj.interactionWorks, `${id} must not be interactable`).toBe(false);
    }

    // INTERACTIVE decorative (coffee mug): clickable, but it carries NO
    // evidence association and a knowledge merge never touches it. In the
    // live flow the server returns the evidence id ONLY for the actual
    // object (fork), and bindEvidenceToSceneModel targets that objectId.
    const mug = byId.get("coffee_mug")!;
    expect(mug.interactionWorks).toBe(true);
    const bound = bindEvidenceToSceneModel(model, "fork", FORK_EVIDENCE_ID);
    expect(bound.worldObjects.find((o) => o.objectId === "fork")!.evidenceId).toBe(FORK_EVIDENCE_ID);
    expect(bound.worldObjects.find((o) => o.objectId === "coffee_mug")!.evidenceId).toBeNull();
  });

  it("discovering the fork never marks the decorative coffee mug (knowledge stays per-object)", () => {
    // The rich world (fork + coffee mug + vase) with the pre-reveal evidence
    // ids stripped — exactly what the live backend publishes.
    const model = buildInvestigationScene(stripUndiscoveredEvidenceIds(makeRichWorldBootstrap()));
    // PD-SEC-01 pre-reveal: the fork has NO evidenceId, so a knowledge merge
    // cannot flip it — exactly the protection that keeps decorative objects
    // (also evidenceId-less) out of the discovered set too.
    const unboundMerged = applyKnowledgeToSceneModel(model, {
      discoveredEvidenceIds: [FORK_EVIDENCE_ID],
      readEvidenceIds: [FORK_EVIDENCE_ID],
    });
    expect(unboundMerged.worldObjects.find((o) => o.objectId === "fork")!.discovered).toBe(false);

    // The discovery path binds the server-confirmed id to the OBJECT (fork),
    // then the knowledge merge flips ONLY that object's flags.
    const bound = bindEvidenceToSceneModel(model, "fork", FORK_EVIDENCE_ID);
    const merged = applyKnowledgeToSceneModel(bound, {
      discoveredEvidenceIds: [FORK_EVIDENCE_ID],
      readEvidenceIds: [FORK_EVIDENCE_ID],
    });
    expect(merged.worldObjects.find((o) => o.objectId === "fork")!.discovered).toBe(true);
    expect(merged.worldObjects.find((o) => o.objectId === "fork")!.read).toBe(true);
    // Decorative objects stay untouched — never selectable-as-evidence.
    expect(merged.worldObjects.find((o) => o.objectId === "vase_01")!.discovered).toBe(false);
    expect(merged.worldObjects.find((o) => o.objectId === "coffee_mug")!.discovered).toBe(false);
  });
});

/* ======================================================================
 * 5. DTO/E2E-STYLE PROOF — fork bootstrap -> scene -> interact -> discovery ->
 *    accusation picker contains Fork -> reveal shows Fork
 *
 * A real-browser Playwright run against the backend is NOT feasible in this
 * session (no backend process is running and the semantic pipeline is
 * backend-dev's uncommitted work), so the full journey is proven at the
 * unit/DTO level with the canned fixtures + the same player-safe surfaces.
 * ==================================================================== */

describe("Phase 19E — 'Weapon: fork' end-to-end proof (unit/DTO level)", () => {
  it("accusation picker shows the semantic weapon 'Fork' with the semantic id value and NO winner marking", () => {
    const flow = new AccusationFlow(
      { submitAccusation: vi.fn(), getReveal: vi.fn() },
      TEST_TOKEN,
      { playthroughId: PT_ID },
      {
        suspects: [{ id: "suspect_alpha", name: "Ada Marsh" }],
        motives: [{ id: "motive_alpha", label: "A dispute over money" }],
        weapons: [
          { id: "weapon_alpha", assetId: "PROP_GENERIC_01", name: "Kitchen knife" },
          { id: "fork", assetId: FORK_PROC_ASSET_ID, name: "Fork" },
        ],
      },
    );
    const html = renderToStaticMarkup(
      <AccusationPanel flow={flow} onReveal={() => {}} onResetToken={() => {}} />,
    );
    expect(html).toContain("Fork");
    expect(html).not.toContain(FORK_PROC_ASSET_ID);
    expect(html).not.toContain("proc.decor.");
    expect(html).toContain('value="fork"');
    // Pre-reveal winner-unmarked invariant: no correctness/winner marker anywhere.
    expect(html.toLowerCase()).not.toContain("correct");
    expect(html.toLowerCase()).not.toContain("winner");
  });

  it("the reveal truth carries the semantic weaponName and the screen shows 'Fork'", () => {
    const reveal = {
      playthroughId: PT_ID,
      caseId: "CASE-test-01",
      caseVersion: 1,
      status: "REVEALED" as const,
      truth: {
        murdererId: "suspect_alpha",
        murdererName: "Ada Marsh",
        motiveId: "motive_alpha",
        motiveLabel: "A dispute over money",
        weaponId: "fork",
        weaponName: "Fork",
        crimeTime: "2026-09-11T21:45:00+02:00",
      },
      player: {
        accusation: {
          murdererId: "suspect_alpha",
          motiveId: "motive_alpha",
          weaponId: "fork",
          crimeTime: "21:45:00",
        },
      },
      result: {
        murdererCorrect: true,
        motiveCorrect: true,
        weaponCorrect: true,
        timeCorrect: true,
        overall: "solved" as const,
      },
      score: { correctDimensions: 4, totalDimensions: 4 },
      timeline: [{ time: "2026-09-11T21:45:00+02:00", description: "The crime occurs" }],
      explanation: {
        evidence: [
          {
            evidenceId: FORK_EVIDENCE_ID,
            title: "Fork",
            point: "The fork carries traces matching the description of the crime.",
          },
        ],
        dimensions: {
          who: [],
          why: [],
          weapon: [
            {
              evidenceId: FORK_EVIDENCE_ID,
              title: "Fork",
              point: "The fork carries traces matching the description of the crime.",
            },
          ],
          when: [],
        },
      },
    };
    const html = renderToStaticMarkup(
      <RevealScreen reveal={reveal as never} candidates={null} />,
    );
    expect(html).toContain("Fork");
    expect(html).not.toContain("proc.decor.");
  });

  it("accusation submission resolves + validates the SEMANTIC weapon id 'fork' (not the render id)", async () => {
    const submitAccusation = vi.fn(
      async (
        _playthroughId: string,
        _body: AccusationRequest,
        _token: string,
      ): Promise<AccusationResponse> => ({
        playthroughId: PT_ID,
        caseId: "CASE-test-01",
        caseVersion: 1,
        status: "ACCUSED",
        accusation: {
          murdererId: "suspect_alpha",
          motiveId: "motive_alpha",
          weaponId: "fork",
          crimeTime: "21:45:00",
        },
      }),
    );
    const flow = new AccusationFlow(
      { submitAccusation, getReveal: vi.fn() },
      TEST_TOKEN,
      { playthroughId: PT_ID },
      {
        suspects: [{ id: "suspect_alpha", name: "Ada Marsh" }],
        motives: [{ id: "motive_alpha", label: "A dispute over money" }],
        weapons: [{ id: "fork", assetId: FORK_PROC_ASSET_ID, name: "Fork" }],
      },
    );
    flow.selectSuspect("suspect_alpha");
    flow.selectMotive("motive_alpha");
    flow.selectWeapon("fork");
    flow.setCrimeTime("21:45");
    expect(flow.openConfirmation()).toBe("confirmed");
    const outcome = await flow.confirmAccusation();
    expect(outcome).toEqual({ outcome: "accepted" });
    // The submission carries the SEMANTIC id — the render asset id never leaves
    // the client as a weapon dimension.
    expect(submitAccusation).toHaveBeenCalledWith(
      PT_ID,
      { murdererId: "suspect_alpha", motiveId: "motive_alpha", weaponId: "fork", crimeTime: "21:45:00" },
      TEST_TOKEN,
    );
    const sentBody = (submitAccusation.mock.calls[0] as unknown as Array<unknown>)[1] as AccusationRequest;
    expect(sentBody.weaponId).toBe("fork");
    expect(sentBody.weaponId).not.toContain("proc.");
  });
});