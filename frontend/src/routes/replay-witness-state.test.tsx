// @vitest-environment jsdom
import { createHash } from "node:crypto";
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  EvidenceReadResultDTO,
  InteractionResultDTO,
  WitnessInterviewResponse,
  WitnessQuestionType,
} from "../api/types";
import type { RenderOptions } from "../scene/renderInvestigation";
import { makeWitnessBootstrap } from "../scene/testFixtures";
import realReplayFixture from "../savegame/fixtures/v1_replay_witness_state.pdcase.json";
import demoFixture from "../savegame/fixtures/v1_demo_apartment.pdcase.json";
import generatedFixture from "../savegame/fixtures/v1_generated_quality_case.pdcase.json";
import { clearReplay, replayStorageNamespace, startReplay } from "../savegame/replaySession";
import { reExportV1, serializeSavegameV1 } from "../savegame/exportV1";
import { parseSavegameV1, utf8ByteLength } from "../savegame/savegameV1";
import { witnessStatementKey } from "../witness/witnessStatementStore";
import ScenePage from "./scene";

/**
 * Phase 39 — saved-replay witness interview state (DEF-045 regression).
 *
 * A loaded `.pdcase` must start a FRESH investigation every time (§10/§40):
 * the witness panel opens with ZERO question-markers answered, even when a
 * PREVIOUS session on the SAME file in the SAME browser stored interview
 * answers in the per-file replay localStorage namespace. The `.pdcase` holds
 * only the IMMUTABLE case definition (question/answer availability) — it is
 * never evidence that the current player already asked a question (§4/§26).
 *
 * The real regression fixture here is the physical Ollama-generated case
 * `tests/fixtures/case_quality/generated/
 * procedural-detective-case-CASE-swwU70RglW9I.ok.pdcase`, committed
 * byte-identical as `./fixtures/v1_replay_witness_state.pdcase.json`
 * (26,403 B, sha256 d633dd328d42025b3ebc08e79cfba2966c522b84ff6e7f2ef811ec72382ee9fa):
 * one REMOTE_STATEMENT witness `daniel_weber`, one `witness_statement`
 * OBSERVATION record `d_ev_witness_danielweber`, valid `replayTruth`, and NO
 * interview-progress fields anywhere.
 *
 * ZERO provider/server: replay services are the in-memory
 * `buildReplayServices`; a throwing fetch spy guards every sequence.
 */

// --------------------------------------------------------------------------- //
// fixture identity
// --------------------------------------------------------------------------- //

const FIXTURE_NAME = "v1_replay_witness_state.pdcase.json";

function fixturePath(): string {
  // vitest runs with the frontend directory as cwd; resolve the committed
  // fixture path so the byte/hash pin can read the PHYSICAL file.
  return join(process.cwd(), "src", "savegame", "fixtures", FIXTURE_NAME);
}

function realText(): string {
  return JSON.stringify(realReplayFixture);
}

function realDefinition(): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(realText(), utf8ByteLength(realText()));
}

/** Recursively scan a parsed document for interview-progress field names. */
function forbiddenProgressKeys(value: unknown, path: string, found: string[]): void {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => forbiddenProgressKeys(entry, `${path}[${index}]`, found));
    return;
  }
  if (typeof value !== "object" || value === null) return;
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (
      key === "askedQuestionTypes" ||
      key === "discoveredStatementIds" ||
      key === "answered" ||
      key === "answeredQuestions" ||
      key === "interview" ||
      key === "interviewProgress" ||
      key === "selectedQuestion" ||
      key === "notebook" ||
      key === "currentNotebook" ||
      key === "playthrough"
    ) {
      found.push(`${path}.${key}`);
    }
    forbiddenProgressKeys(child, `${path}.${key}`, found);
  }
}

const ALL_QUESTION_TYPES: readonly WitnessQuestionType[] = [
  "OBSERVATION",
  "TIME",
  "SOUND",
  "PERSON",
  "OBJECT",
  "LOCATION",
];

// --------------------------------------------------------------------------- //
// hermetic DOM mocks (the SAME wiring scene.test.tsx / witness.scene.test.tsx use)
// --------------------------------------------------------------------------- //

const holders = vi.hoisted(() => ({
  bootstrap: null as ReturnType<typeof makeWitnessBootstrap> | null,
  lastOnPick: null as ((objectId: string) => void) | null,
}));

vi.mock("../api/playthroughToken", () => ({
  getPlaythroughToken: () => "lX9fQ3sWv0aB2cD4eF6gH8iJ1kM3nO5pQ7rS9tU",
  getPlaythroughId: () => "PT-test-0001",
  setPlaythroughToken: () => true,
  setPlaythroughId: () => true,
  clearPlaythroughCredentials: vi.fn(),
  validatePlaythroughToken: () => ({ ok: true }),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  const interactObject = vi.fn(
    async (_pt: string, objectId: string, interaction: string): Promise<InteractionResultDTO> => {
      if (objectId === "apartment_laptop" && interaction === "read") {
        return {
          objectId,
          interaction,
          evidenceId: "email_thomas_01",
          discovery: {
            evidenceId: "email_thomas_01",
            kind: "email",
            title: "Laptop activity log",
            interaction,
            state: "discovered",
          },
          result: "interacted",
          inspection: { relevant: true, label: "Laptop" },
        };
      }
      return {
        objectId,
        interaction,
        evidenceId: null,
        discovery: null,
        result: "interacted",
        inspection: { relevant: false, label: `Inspected ${objectId} (server)` },
      };
    },
  );
  const interviewWitness = vi.fn(
    async (
      _pt: string,
      witnessId: string,
      questionType: WitnessQuestionType,
    ): Promise<WitnessInterviewResponse> => {
      return {
        witnessId,
        displayName: witnessId,
        questionType,
        statement: { summary: "No. Nothing stood out to me.", observations: [] },
        discovery: null,
      };
    },
  );
  const readRecord = vi.fn(
    async (_pt: string, recordId: string): Promise<EvidenceReadResultDTO> => {
      return {
        evidenceId: recordId,
        kind: "forensic",
        title: "Kitchen knife",
        description: null,
        openedAt: "2026-09-11T22:20:00+02:00",
        readByPlayer: true,
        content: {},
      };
    },
  );
  return {
    ...actual,
    getInvestigation: vi.fn(async () => holders.bootstrap),
    interactObject,
    interviewWitness,
    readRecord,
  };
});

vi.mock("../scene/renderInvestigation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../scene/renderInvestigation")>();
  return {
    ...actual,
    createInvestigationScene: vi.fn((_canvas: unknown, _model: unknown, options: RenderOptions) => {
      holders.lastOnPick = options.onPick ?? null;
      return {
        ok: true,
        engine: {},
        scene: {},
        dispose: vi.fn(),
        setObjectHighlight: vi.fn(),
        isObjectHighlighted: () => false,
        setObjectSelected: vi.fn(),
        getSelectedObjectId: () => null,
        projectObjectPoint: () => null,
        setObjectFocus: vi.fn(),
        isObjectFocused: () => false,
        getFocusedObjectId: () => null,
        tickFocus: vi.fn(),
        getFocusSnapshot: () => null,
      };
    }),
  };
});

// React's act() support flag (same as the sibling route tests).
declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// --------------------------------------------------------------------------- //
// mount helpers
// --------------------------------------------------------------------------- //

interface Mounted {
  container: HTMLElement;
  root: ReturnType<typeof createRoot>;
}

let mounted: Mounted | null = null;

function mountScene(initialEntries: string[] = ["/scene"]): Mounted {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={initialEntries}>
        <ScenePage />
      </MemoryRouter>,
    );
  });
  mounted = { container, root };
  return mounted;
}

function unmountScene(): void {
  const current = mounted;
  mounted = null;
  if (current) {
    act(() => current.root.unmount());
    current.container.remove();
  }
}

async function flushAsync(): Promise<void> {
  await act(async () => {
    for (let index = 0; index < 24; index += 1) await Promise.resolve();
  });
}

async function waitForReady(container: HTMLElement): Promise<void> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (container.querySelector('[data-testid="scene-objects"]') !== null) return;
    await act(async () => {
      await Promise.resolve();
    });
  }
  throw new Error("the /scene page never reached the READY state");
}

function queryRequired<T extends Element>(container: HTMLElement, selector: string): T {
  const element = container.querySelector(selector);
  if (!element) throw new Error(`expected element not found: ${selector}`);
  return element as T;
}

function click(container: HTMLElement, selector: string): void {
  const element = queryRequired<HTMLButtonElement>(container, selector);
  act(() => {
    element.click();
  });
}

function answeredMarkerCount(container: HTMLElement): number {
  return container.querySelectorAll('[data-testid^="witness-question-"][data-testid$="-asked"]').length;
}

function isAnswerRendered(container: HTMLElement, questionType: WitnessQuestionType): boolean {
  return container.querySelector(`[data-testid="witness-question-${questionType}-asked"]`) !== null;
}

/** Run a sequence while replacing fetch with a spy that MUST never be called. */
async function withNoNetwork(run: () => Promise<void>): Promise<void> {
  const fetchSpy = vi.fn(async () => {
    throw new Error("network must never be used during a saved-case replay");
  });
  const previous = globalThis.fetch;
  globalThis.fetch = fetchSpy as unknown as typeof fetch;
  try {
    await run();
    expect(fetchSpy).not.toHaveBeenCalled();
  } finally {
    globalThis.fetch = previous;
  }
}

beforeEach(() => {
  window.localStorage.clear();
  holders.lastOnPick = null;
  holders.bootstrap = makeWitnessBootstrap();
});

afterEach(() => {
  unmountScene();
  holders.lastOnPick = null;
  vi.clearAllMocks();
  clearReplay();
  window.localStorage.clear();
});

// --------------------------------------------------------------------------- //
// Phase 39 — the real regression fixture is a clean SavegameV1 (no progress)
// --------------------------------------------------------------------------- //

describe("Phase 39 — real saved-replay fixture contract (SavegameV1)", () => {
  it("the committed fixture is byte-identical to the pinned real file (26,403 B, d633dd32…ee9fa)", () => {
    const path = fixturePath();
    const bytes = statSync(path).size;
    const text = readFileSync(path, "utf8");
    const digest = createHash("sha256").update(text, "utf8").digest("hex");
    expect(bytes).toBe(26_403);
    expect(digest).toBe("d633dd328d42025b3ebc08e79cfba2966c522b84ff6e7f2ef811ec72382ee9fa");
  });

  it("parses as formatVersion 1 with the REAL witness + one OBSERVATION statement", () => {
    const definition = realDefinition();
    expect(definition.formatVersion).toBe(1);
    expect(definition.metadata.source).toBe("generated");
    expect(definition.metadata.sourceCaseId).toBe("CASE-swwU70RglW9I");
    expect(definition.witnesses).toEqual([
      {
        witnessId: "daniel_weber",
        displayName: "Daniel Weber",
        presence: "REMOTE_STATEMENT",
        sceneObjectId: null,
      },
    ]);
    const statement = definition.evidence.find((record) => record.evidenceId === "d_ev_witness_danielweber");
    expect(statement).toBeDefined();
    expect(statement!.kind).toBe("witness_statement");
    expect(statement!.content.witnessId).toBe("daniel_weber");
    expect(statement!.content.questionType).toBe("OBSERVATION");
    expect(statement!.content.statement).toContain("As Daniel Weber, I was at the luxury hotel suite around 21:18");
  });

  it("contains NO interview-progress fields anywhere in the document (§2/§11)", () => {
    const found: string[] = [];
    forbiddenProgressKeys(realReplayFixture, "savegame", found);
    expect(found).toEqual([]);
  });

  it("a re-export round-trip stays v1 and preserves the immutable statement (no progress added)", () => {
    const definition = realDefinition();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    const reloaded = parseSavegameV1(exported, utf8ByteLength(exported));
    expect(reloaded.formatVersion).toBe(1);
    const statement = reloaded.evidence.find((record) => record.evidenceId === "d_ev_witness_danielweber");
    expect(statement!.content.questionType).toBe("OBSERVATION");
    const found: string[] = [];
    forbiddenProgressKeys(JSON.parse(exported), "savegame", found);
    expect(found).toEqual([]);
  });
});

// --------------------------------------------------------------------------- //
// Phase 39 — THE RED -> GREEN regression: fresh replay, zero pre-answered
// --------------------------------------------------------------------------- //

describe("Phase 39 — fresh saved replay starts with ZERO answered witness questions", () => {
  it("real file + a prior session's per-file storage -> 0 answered markers, neutral hint, fresh ask", async () => {
    await withNoNetwork(async () => {
      const definition = realDefinition();
      const namespace = replayStorageNamespace(definition);

      // A PREVIOUS session on this SAME file in this SAME browser stored its
      // whole interview in the per-file replay namespace (`pd_witness_statements_v1:
      // saved-replay-<content-hash>`) — exactly what the reported bug produced:
      // the next load showed all six questions pre-answered.
      window.localStorage.setItem(
        witnessStatementKey(namespace),
        JSON.stringify({
          version: 1,
          entries: ALL_QUESTION_TYPES.map((questionType) => ({
            witnessId: "daniel_weber",
            questionType,
            statement: { summary: `prior ${questionType} answer`, observations: [] },
          })),
        }),
      );

      startReplay(definition);
      mountScene();
      await flushAsync();
      await waitForReady(mounted!.container);

      click(mounted!.container, '[data-testid="witness-daniel_weber"]');
      await flushAsync();

      // REGRESSION: the fresh replay must NOT inherit the previous session's
      // asked set. Pre-fix this is 6 (RED); post-fix it is 0 (GREEN).
      expect(answeredMarkerCount(mounted!.container)).toBe(0);
      // Neutral unselected hint (already correct, pinned by §16).
      expect(
        mounted!.container.querySelector('[data-testid="witness-answer-hint"]'),
      ).not.toBeNull();
      expect(mounted!.container.querySelector('[data-testid="witness-answer"]')).toBeNull();

      // Asking ONE question marks exactly ONE answered marker, rendered idle.
      click(mounted!.container, '[data-testid="witness-question-OBSERVATION"]');
      await flushAsync();
      expect(
        mounted!.container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
      ).toContain("Daniel Weber was near the luxury hotel suite around 21:18.");
      expect(answeredMarkerCount(mounted!.container)).toBe(1);
      expect(isAnswerRendered(mounted!.container, "OBSERVATION")).toBe(true);
    });
  });

  it("fresh Demo replay parity: Demo save -> Load -> empty asked markers", async () => {
    const definition = parseSavegameV1(
      JSON.stringify(demoFixture),
      utf8ByteLength(JSON.stringify(demoFixture)),
    );
    expect(definition.metadata.source).toBe("demo");
    startReplay(definition);
    mountScene();
    await flushAsync();
    await waitForReady(mounted!.container);

    click(mounted!.container, '[data-testid="witness-emily_reed"]');
    await flushAsync();
    expect(answeredMarkerCount(mounted!.container)).toBe(0);
    expect(
      mounted!.container.querySelector('[data-testid="witness-answer-hint"]'),
    ).not.toBeNull();
  });

  it("generated-shape parity: a generated replay file loads with empty asked markers", async () => {
    const definition = parseSavegameV1(
      JSON.stringify(generatedFixture),
      utf8ByteLength(JSON.stringify(generatedFixture)),
    );
    expect(definition.metadata.source).toBe("generated");
    startReplay(definition);
    mountScene();
    await flushAsync();
    await waitForReady(mounted!.container);

    click(mounted!.container, '[data-testid="witness-lisa_koenig"]');
    await flushAsync();
    expect(answeredMarkerCount(mounted!.container)).toBe(0);
    expect(
      mounted!.container.querySelector('[data-testid="witness-answer-hint"]'),
    ).not.toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// Phase 39 — §10/§40: the SAME file loaded a second time never leaks progress
// --------------------------------------------------------------------------- //

describe("Phase 39 — same file loaded twice starts a fresh interview every load", () => {
  it("Load #1 asks OBSERVATION; Load #2 starts with 0 answered markers and an empty notebook group", async () => {
    await withNoNetwork(async () => {
      const definition = realDefinition();
      const namespace = replayStorageNamespace(definition);

      // ---- Load #1: fresh replay -> ask exactly one question. ----
      startReplay(definition);
      mountScene();
      await flushAsync();
      await waitForReady(mounted!.container);
      click(mounted!.container, '[data-testid="witness-daniel_weber"]');
      await flushAsync();
      expect(answeredMarkerCount(mounted!.container)).toBe(0);

      click(mounted!.container, '[data-testid="witness-question-OBSERVATION"]');
      await flushAsync();
      expect(answeredMarkerCount(mounted!.container)).toBe(1);
      expect(
        mounted!.container.querySelector(
          '[data-testid="notebook-group-witness-statements-list"]',
        )!.querySelectorAll("li"),
      ).toHaveLength(1);

      // A replay must NEVER persist interview progress into the per-file
      // namespace — the in-memory session is the only owner (§14/§19).
      expect(window.localStorage.getItem(witnessStatementKey(namespace))).toBeNull();

      // ---- Leave the replay, then load the SAME file again. ----
      unmountScene();
      clearReplay();

      startReplay(definition);
      mountScene();
      await flushAsync();
      await waitForReady(mounted!.container);
      click(mounted!.container, '[data-testid="witness-daniel_weber"]');
      await flushAsync();

      // Load #2 MUST be fresh: zero answered markers (§10) and zero notebook
      // witness lines — nothing from Load #1 may bleed in.
      expect(answeredMarkerCount(mounted!.container)).toBe(0);
      expect(
        mounted!.container.querySelector('[data-testid="witness-answer-hint"]'),
      ).not.toBeNull();
      const freshGroup = mounted!.container.querySelector(
        '[data-testid="notebook-group-witness-statements"]',
      );
      expect(freshGroup).not.toBeNull();
      expect(
        mounted!.container.querySelectorAll('[data-testid^="notebook-entry-witness-statements-"]'),
      ).toHaveLength(0);
      expect(
        mounted!.container.querySelector(
          '[data-testid="notebook-group-witness-statements-empty"]',
        ),
      ).not.toBeNull();

      // Asking on Load #2 still works and marks that NEW interview progress.
      click(mounted!.container, '[data-testid="witness-question-OBSERVATION"]');
      await flushAsync();
      expect(answeredMarkerCount(mounted!.container)).toBe(1);
      expect(
        mounted!.container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
      ).toContain("Daniel Weber was near the luxury hotel suite around 21:18.");
    });
  });
});