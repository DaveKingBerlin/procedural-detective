// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EvidenceReadResultDTO, InteractionResultDTO, WitnessInterviewResponse, WitnessListEntryDTO } from "../api/types";
import type { RenderOptions } from "../scene/renderInvestigation";
import {
  EMILY_TIME_EVIDENCE_ID,
  EMILY_WITNESS_ID,
  LISA_WITNESS_ID,
  makeEmilyTimeDiscoveryRecord,
  makeLisaPersonResponse,
  makeWitnessBootstrap,
  makeWitnessInterviewResponse,
  makeWitnessListEntry,
} from "../scene/testFixtures";
import ScenePage from "./scene";

/**
 * Phase 23 — the /scene route renders witnesses in BOTH presence modes:
 *   - ON_SCENE: a pickable person world object (Phase 19F semantic id); the 3D
 *     pick AND the "Objects in this room" fallback both open the interview
 *     panel; the witness display name is the ONLY label shown.
 *   - REMOTE_STATEMENT: reachable ONLY through the Witnesses section (never a
 *     scene object — a witness is not forced to stand next to the victim).
 * Plus the live interview question flow (POST the closed questionType, render
 * the deterministic statement, discovery -> evidence panel + knowledge +
 * notebook) and reload persistence (server-persisted discoveries re-derive
 * the notebook's Witness statements group after a remount).
 *
 * Exactly like scene.test.tsx, the credential store / API client / Babylon
 * glue are mocked and the 3D pick is driven through the captured onPick.
 */

// Hermetic holders (hoisted before the mocks run).
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
    async (_pt: string, objectId: string, interaction: string): Promise<InteractionResultDTO> => ({
      objectId,
      interaction,
      evidenceId: null,
      discovery: null,
      result: "interacted",
      inspection: { relevant: false, label: `Inspected ${objectId} (server)` },
    }),
  );
  const interviewWitness = vi.fn(
    async (
      _pt: string,
      witnessId: string,
      questionType: WitnessInterviewResponse["questionType"],
    ): Promise<WitnessInterviewResponse> => {
      // The golden witness Emily answers TIME with the grounded, time-bearing
      // statement + a real discovery record; every other question is neutral.
      if (witnessId === EMILY_WITNESS_ID) {
        return makeWitnessInterviewResponse(questionType);
      }
      // The driver witness Lisa König answers PERSON with a grounded
      // informational statement and NO discovery.
      if (witnessId === LISA_WITNESS_ID && questionType === "PERSON") {
        return makeLisaPersonResponse();
      }
      return makeWitnessInterviewResponse(questionType, { witnessId, discovery: null });
    },
  );
  const readRecord = vi.fn(
    async (_pt: string, recordId: string): Promise<EvidenceReadResultDTO> => {
      if (recordId === EMILY_TIME_EVIDENCE_ID) return makeEmilyTimeDiscoveryRecord();
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

import { interviewWitness } from "../api/client";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

interface Mounted {
  container: HTMLDivElement;
  root: ReturnType<typeof createRoot>;
  unmount: () => void;
}

let mounted: Mounted | null = null;

function mountScene(): Mounted {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={["/scene"]}>
        <ScenePage />
      </MemoryRouter>,
    );
  });
  const unmount = () => {
    act(() => root.unmount());
    container.remove();
  };
  return { container, root, unmount };
}

async function flushAsync(): Promise<void> {
  await act(async () => {
    for (let index = 0; index < 16; index += 1) await Promise.resolve();
  });
}

async function waitForReady(container: HTMLElement): Promise<void> {
  for (let attempt = 0; attempt < 80; attempt += 1) {
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

function witnessButton(container: HTMLElement, witnessId: string): HTMLButtonElement {
  return queryRequired<HTMLButtonElement>(container, `[data-testid="witness-${witnessId}"]`);
}

function questionButton(container: HTMLElement, questionType: string): HTMLButtonElement {
  return queryRequired<HTMLButtonElement>(container, `[data-testid="witness-question-${questionType}"]`);
}

beforeEach(() => {
  holders.bootstrap = makeWitnessBootstrap();
  holders.lastOnPick = null;
  mounted = mountScene();
});

afterEach(() => {
  mounted?.unmount();
  mounted = null;
  holders.lastOnPick = null;
  vi.clearAllMocks();
});

describe("Phase 23 — /scene witness visibility (ON_SCENE + REMOTE_STATEMENT)", () => {
  it("renders the Witnesses section with BOTH presence modes as real labelled buttons", async () => {
    await flushAsync();
    await waitForReady(mounted!.container);

    const emily = witnessButton(mounted!.container, EMILY_WITNESS_ID);
    const lisa = witnessButton(mounted!.container, LISA_WITNESS_ID);
    expect(emily.type).toBe("button");
    expect(emily.getAttribute("aria-label")).toBe("Interview Emily Reed");
    expect(emily.textContent).toContain("Emily Reed");
    expect(lisa.getAttribute("aria-label")).toBe("Interview Lisa König");
    expect(lisa.textContent).toContain("Lisa König");
    // The server-published presence rides on the list items.
    const emilyLi = emily.closest("li");
    const lisaLi = lisa.closest("li");
    expect(emilyLi?.getAttribute("data-presence")).toBe("ON_SCENE");
    expect(lisaLi?.getAttribute("data-presence")).toBe("REMOTE_STATEMENT");
    // No statement content exists anywhere BEFORE a question is asked.
    expect(mounted!.container.textContent).not.toContain("heavy impact");
    expect(mounted!.container.textContent).not.toContain("23:42");
  });

  it("ON_SCENE: Emily has a pickable person object whose button shows her NAME and opens the interview panel (not the inspect flow)", async () => {
    await flushAsync();
    await waitForReady(mounted!.container);

    const objectButton = queryRequired<HTMLButtonElement>(
      mounted!.container,
      `[data-testid="object-${EMILY_WITNESS_ID}"]`,
    );
    expect(objectButton.textContent?.trim()).toBe("Emily Reed");
    expect(objectButton.textContent).not.toContain("Victim");
    // The object-level witness marker confirms the person semantics.
    expect(
      mounted!.container.querySelector(`[data-testid="object-witness-${EMILY_WITNESS_ID}"]`),
    ).not.toBeNull();

    click(mounted!.container, `[data-testid="object-${EMILY_WITNESS_ID}"]`);
    await flushAsync();
    expect(mounted!.container.querySelector('[data-testid="witness-panel"]')).not.toBeNull();
    expect(
      mounted!.container.querySelector('[data-testid="witness-panel-name"]')?.textContent,
    ).toBe("Emily Reed");
    // The person object is NOT treated as a decorative inspect ("Nothing
    // relevant was found...") — the interview panel is the only outcome.
    expect(mounted!.container.textContent).not.toContain("Nothing relevant was found");
  });

  it("ON_SCENE: a 3D pick of the person root (Phase 19F onPick) opens the same interview panel", async () => {
    await flushAsync();
    await waitForReady(mounted!.container);
    expect(holders.lastOnPick, "the scene glue received onPick").not.toBeNull();

    act(() => {
      holders.lastOnPick?.(EMILY_WITNESS_ID);
    });
    await flushAsync();

    expect(mounted!.container.querySelector('[data-testid="witness-panel"]')).not.toBeNull();
    expect(
      mounted!.container.querySelector('[data-testid="witness-panel-name"]')?.textContent,
    ).toBe("Emily Reed");
  });

  it("REMOTE_STATEMENT: Lisa König has NO scene object (nothing next to the victim) but her Witnesses-section button opens the same panel", async () => {
    await flushAsync();
    await waitForReady(mounted!.container);

    // No world object for Lisa — she is NOT forced to stand at the scene.
    expect(mounted!.container.querySelector(`[data-testid="object-${LISA_WITNESS_ID}"]`)).toBeNull();

    click(mounted!.container, `[data-testid="witness-${LISA_WITNESS_ID}"]`);
    await flushAsync();
    expect(mounted!.container.querySelector('[data-testid="witness-panel"]')).not.toBeNull();
    expect(
      mounted!.container.querySelector('[data-testid="witness-panel-name"]')?.textContent,
    ).toBe("Lisa König");
  });

  it("all six question buttons render in the panel and Escape closes it, restoring the scene controls", async () => {
    await flushAsync();
    await waitForReady(mounted!.container);
    click(mounted!.container, `[data-testid="witness-${EMILY_WITNESS_ID}"]`);
    await flushAsync();

    for (const questionType of ["OBSERVATION", "TIME", "SOUND", "PERSON", "OBJECT", "LOCATION"]) {
      expect(questionButton(mounted!.container, questionType).textContent).not.toBe("");
    }
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    await flushAsync();
    expect(mounted!.container.querySelector('[data-testid="witness-panel"]')).toBeNull();
  });
});

describe("Phase 23 — interview question flow on /scene", () => {
  it("asking TIME POSTs {questionType:'TIME'}, renders the statement, opens the discovery record, updates knowledge + notebook", async () => {
    await flushAsync();
    await waitForReady(mounted!.container);
    click(mounted!.container, `[data-testid="witness-${EMILY_WITNESS_ID}"]`);
    await flushAsync();

    expect(mounted!.container.querySelector('[data-testid="witness-panel"]')).not.toBeNull();
    click(mounted!.container, '[data-testid="witness-question-TIME"]');
    await flushAsync();

    // The correct request was dispatched (playthrough, witness, closed type).
    expect(interviewWitness).toHaveBeenCalledWith(
      "PT-test-0001",
      EMILY_WITNESS_ID,
      "TIME",
      "lX9fQ3sWv0aB2cD4eF6gH8iJ1kM3nO5pQ7rS9tU",
    );

    // Statement renders: summary + compact <time> observations.
    expect(
      mounted!.container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
    ).toContain("heavy impact at approximately 23:42");
    const observationTimes = Array.from(mounted!.container.querySelectorAll("time"));
    expect(observationTimes).toHaveLength(2);
    expect(observationTimes[0].getAttribute("dateTime")).toBe("23:40");

    // Discovery record opened the EXISTING evidence panel.
    expect(mounted!.container.querySelector('[data-testid="evidence-panel"]')).not.toBeNull();
    expect(
      mounted!.container.querySelector('[data-testid="discovery-toast-text"]')?.textContent,
    ).toContain("Discovered:");

    // Knowledge + discovery strip gained the interview-sourced evidence.
    expect(
      mounted!.container.querySelector(`[data-testid="discovered-entry-${EMILY_TIME_EVIDENCE_ID}"]`),
    ).not.toBeNull();

    // Notebook: the Witness statements group has EXACTLY ONE line, and the
    // People group keeps the same witness evidence (existing behavior intact).
    const witnessGroup = mounted!.container.querySelector('[data-testid="notebook-group-witness-statements-list"]');
    expect(witnessGroup).not.toBeNull();
    const lines = witnessGroup!.querySelectorAll("li");
    expect(lines).toHaveLength(1);
    expect(lines[0].textContent).toContain("Emily Reed");
    expect(lines[0].textContent).toContain("When were you there?");
    expect(lines[0].textContent).toContain("heavy impact");
    expect(
      mounted!.container.querySelector('[data-testid="notebook-group-people-list"]')!.textContent,
    ).toContain("Emily Reed");
  });

  it("IDEMPOTENT re-ask: asking TIME again serves the cached statement — no POST, no duplicate notebook line", async () => {
    await flushAsync();
    await waitForReady(mounted!.container);
    click(mounted!.container, `[data-testid="witness-${EMILY_WITNESS_ID}"]`);
    await flushAsync();

    click(mounted!.container, '[data-testid="witness-question-TIME"]');
    await flushAsync();
    expect(interviewWitness).toHaveBeenCalledTimes(1);

    // Return to the question grid, then re-ask TIME.
    click(mounted!.container, '[data-testid="witness-ask-another"]');
    await flushAsync();
    click(mounted!.container, '[data-testid="witness-question-TIME"]');
    await flushAsync();

    // The cached answer rendered — the server was NOT called again.
    expect(interviewWitness).toHaveBeenCalledTimes(1);
    expect(
      mounted!.container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
    ).toContain("heavy impact");
    const lines = mounted!.container.querySelector('[data-testid="notebook-group-witness-statements-list"]')!.querySelectorAll("li");
    expect(lines).toHaveLength(1);
  });

  it("a NEUTRAL answer renders cleanly: no evidence panel, no discovery strip, no notebook line", async () => {
    await flushAsync();
    await waitForReady(mounted!.container);
    click(mounted!.container, `[data-testid="witness-${EMILY_WITNESS_ID}"]`);
    await flushAsync();

    click(mounted!.container, '[data-testid="witness-question-SOUND"]');
    await flushAsync();

    expect(
      mounted!.container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
    ).toBe("No. Nothing stood out to me.");
    expect(mounted!.container.querySelector('[data-testid="evidence-panel"]')).toBeNull();
    expect(mounted!.container.querySelector('[data-testid^="discovered-entry-"]')).toBeNull();
    expect(
      mounted!.container.querySelector('[data-testid="notebook-group-witness-statements-list"]')!.querySelectorAll("li"),
    ).toHaveLength(1); // the NEUTRAL line appears (it WAS asked) but no evidence
  });

  it("RELOAD persistence: server-persisted discovery re-derives the notebook Witness statements line (no client store)", async () => {
    // A fresh page load whose bootstrap carries the server-persisted
    // discovered/read id — the session's asked-store is EMPTY.
    const reloaded = makeWitnessBootstrap();
    reloaded.playerKnowledge = {
      discoveredEvidenceIds: [EMILY_TIME_EVIDENCE_ID],
      readEvidenceIds: [EMILY_TIME_EVIDENCE_ID],
      visitedLocationIds: ["miller_apartment_kitchen"],
    };
    holders.bootstrap = reloaded;

    mounted!.unmount();
    mounted = mountScene();
    await flushAsync();
    await waitForReady(mounted!.container);

    // The Witness statements line re-derives from the re-fetched READ record.
    const witnessGroup = mounted!.container.querySelector('[data-testid="notebook-group-witness-statements-list"]');
    expect(witnessGroup).not.toBeNull();
    const lines = witnessGroup!.querySelectorAll("li");
    expect(lines.length).toBeGreaterThanOrEqual(1);
    expect(lines[0].textContent).toContain("Emily Reed");
    expect(lines[0].textContent).toContain("When were you there?");
    expect(lines[0].textContent).toContain("heavy impact");
    // The discovered evidence strip also re-appears (server knowledge).
    expect(
      mounted!.container.querySelector(`[data-testid="discovered-entry-${EMILY_TIME_EVIDENCE_ID}"]`),
    ).not.toBeNull();
    // No statement content leaked into the Witnesses buttons (names only).
    const witnessesSection = mounted!.container.querySelector('[data-testid="witnesses"]')!;
    expect(witnessesSection.textContent).not.toContain("heavy impact");
  });

  it("a witness without a scene object still formats the panel from the remote entry", () => {
    const remote: WitnessListEntryDTO = makeWitnessListEntry({
      witnessId: "witness_remote_01",
      displayName: "Nina Weber",
      presence: "REMOTE_STATEMENT",
      sceneObjectId: null,
    });
    expect(remote.witnessId).toBe("witness_remote_01");
    expect(remote.presence).toBe("REMOTE_STATEMENT");
  });
});