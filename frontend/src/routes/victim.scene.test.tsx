// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EvidenceReadResultDTO, InteractionResultDTO } from "../api/types";
import type { RenderOptions } from "../scene/renderInvestigation";
import {
  makeBootstrap,
  makeVictimBodyRecord,
  makeVictimBootstrap,
  VICTIM_BODY_OBJECT_ID,
  VICTIM_DISCOVERY_EVIDENCE_ID,
} from "../scene/testFixtures";

/**
 * Phase 47 — /scene victim profile wiring.
 *
 * The victim body object renders the VictimProfilePanel (NOT the generic
 * EvidencePanel), identity appears IMMEDIATELY on selection, and the record's
 * Known facts / Timeline appear progressively once the body record is read.
 * Non-victim bootstraps keep the pre-47 behavior exactly.
 */

const holders = vi.hoisted(() => ({
  bootstrap: null as ReturnType<typeof makeVictimBootstrap> | null,
  /** DEF-114 — when set, the victim-body interaction awaits this deferred so a
   *  test can exercise a Close issued WHILE the round-trip is in flight. */
  deferred: null as { promise: Promise<void>; resolve: () => void } | null,
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
    async (
      _pt: string,
      objectId: string,
      interaction: string,
      _token: string,
    ): Promise<InteractionResultDTO> => {
      // DEF-114 — hold the victim-body interaction open until the test releases
      // it (lets a Close/ESC land while the request is in flight).
      if (objectId === VICTIM_BODY_OBJECT_ID && holders.deferred !== null) {
        await holders.deferred.promise;
      }
      if (objectId === VICTIM_BODY_OBJECT_ID) {
        return {
          objectId,
          interaction,
          evidenceId: VICTIM_DISCOVERY_EVIDENCE_ID,
          discovery: {
            evidenceId: VICTIM_DISCOVERY_EVIDENCE_ID,
            kind: "witness_observation",
            title: "Body found in the kitchen at 22:18",
            interaction,
            state: "discovered",
          },
          result: "interacted",
          inspection: { relevant: true, label: "Victim" },
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
  const readRecord = vi.fn(
    async (_pt: string, recordId: string): Promise<EvidenceReadResultDTO> => {
      if (recordId === VICTIM_DISCOVERY_EVIDENCE_ID) return makeVictimBodyRecord();
      return makeVictimBodyRecord({ evidenceId: recordId });
    },
  );
  return {
    ...actual,
    getInvestigation: vi.fn(async () => holders.bootstrap),
    interactObject,
    readRecord,
  };
});

vi.mock("../scene/renderInvestigation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../scene/renderInvestigation")>();
  return {
    ...actual,
    createInvestigationScene: vi.fn((_canvas: unknown, _model: unknown, _options: RenderOptions) => ({
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
    })),
  };
});

import ScenePage from "./scene";

declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

interface Mounted {
  container: HTMLDivElement;
  root: ReturnType<typeof createRoot>;
}

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
  return { container, root };
}

async function flushAsync(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 16; i += 1) await Promise.resolve();
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

function victimButton(container: HTMLElement): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>(
    `[data-testid="object-${VICTIM_BODY_OBJECT_ID}"]`,
  );
  if (!button) throw new Error("victim object button not found");
  return button;
}

describe("Phase 47 — selecting the victim opens the investigation record", () => {
  let mounted: Mounted | null = null;

  beforeEach(() => {
    holders.bootstrap = makeVictimBootstrap();
    mounted = mountScene();
  });

  afterEach(() => {
    const root = mounted?.root;
    mounted = null;
    holders.deferred = null;
    if (root) act(() => root.unmount());
  });

  it("shows identity IMMEDIATELY on selection and suppresses the generic evidence panel", async () => {
    const container = mounted!.container;
    await flushAsync();
    await waitForReady(container);

    act(() => {
      victimButton(container).click();
    });
    // No flush yet: the profile must already be visible (identity immediate).
    const panel = container.querySelector('[data-testid="victim-panel"]');
    expect(panel).not.toBeNull();
    expect(container.querySelector('[data-testid="victim-panel-title"]')?.textContent).toBe(
      "Victim — Body found in the kitchen at 22:18",
    );
    expect(container.querySelector('[data-testid="victim-identity-name"]')?.textContent).toContain(
      "Sarah Miller",
    );
    expect(container.querySelector('[data-testid="victim-discovery-location"]')?.textContent).toContain(
      "Miller Apartment - Kitchen",
    );
    // The victim surface REPLACES the generic evidence panel (no duplicate).
    expect(container.querySelector('[data-testid="evidence-panel"]')).toBeNull();

    await flushAsync();
    // Still the victim panel after the interaction/read settles.
    expect(container.querySelector('[data-testid="victim-panel"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="evidence-panel"]')).toBeNull();
  });

  it("progressively discloses known facts + timeline once the body record is read", async () => {
    const container = mounted!.container;
    await flushAsync();
    await waitForReady(container);

    act(() => {
      victimButton(container).click();
    });
    await flushAsync();

    // Known facts + timeline derive from the READ body record.
    expect(container.querySelector('[data-testid="victim-known-facts"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="victim-timeline"]')).not.toBeNull();
    // No victim-related forensic record is in the read cache yet.
    expect(container.querySelector('[data-testid="victim-forensic-empty"]')).not.toBeNull();
  });

  it("closes via the Close button (selection + panel cleared)", async () => {
    const container = mounted!.container;
    await flushAsync();
    await waitForReady(container);

    act(() => {
      victimButton(container).click();
    });
    await flushAsync();
    expect(container.querySelector('[data-testid="victim-panel"]')).not.toBeNull();

    act(() => {
      container.querySelector<HTMLButtonElement>('[data-testid="victim-close"]')!.click();
    });
    await flushAsync();

    expect(container.querySelector('[data-testid="victim-panel"]')).toBeNull();
    expect(victimButton(container).getAttribute("aria-pressed")).toBe("false");
  });

  it("closes with Escape (route-level keyboard close)", async () => {
    const container = mounted!.container;
    await flushAsync();
    await waitForReady(container);

    act(() => {
      victimButton(container).click();
    });
    await flushAsync();
    expect(container.querySelector('[data-testid="victim-panel"]')).not.toBeNull();

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    await flushAsync();

    expect(container.querySelector('[data-testid="victim-panel"]')).toBeNull();
  });

  it("DEF-114 — a Close issued WHILE the body interaction is in flight stays closed", async () => {
    const container = mounted!.container;
    await flushAsync();
    await waitForReady(container);

    // Arm a deferred interaction: it will NOT resolve until we release it.
    let release!: () => void;
    holders.deferred = {
      promise: new Promise<void>((resolve) => {
        release = resolve;
      }),
      resolve: () => release(),
    };

    act(() => {
      victimButton(container).click();
    });
    // The victim surface opens immediately (before the round-trip resolves).
    expect(container.querySelector('[data-testid="victim-panel"]')).not.toBeNull();

    // Close it while the interaction is still in flight.
    act(() => {
      container.querySelector<HTMLButtonElement>('[data-testid="victim-close"]')!.click();
    });
    expect(container.querySelector('[data-testid="victim-panel"]')).toBeNull();

    // Let the delayed interaction resolve; the durable close must win.
    act(() => {
      holders.deferred!.resolve();
    });
    await flushAsync();

    expect(container.querySelector('[data-testid="victim-panel"]')).toBeNull();
    // The generic evidence panel must not open in its place either.
    expect(container.querySelector('[data-testid="evidence-panel"]')).toBeNull();
    expect(victimButton(container).getAttribute("aria-pressed")).toBe("false");
  });
});

describe("Phase 47 — a bootstrap without a victim is unchanged", () => {
  let mounted: Mounted | null = null;

  beforeEach(() => {
    holders.bootstrap = makeBootstrap();
    mounted = mountScene();
  });

  afterEach(() => {
    const root = mounted?.root;
    mounted = null;
    if (root) act(() => root.unmount());
  });

  it("renders no victim panel for the plain (pre-47) bootstrap", async () => {
    const container = mounted!.container;
    await flushAsync();
    await waitForReady(container);

    act(() => {
      victimButton(container).click();
    });
    await flushAsync();

    expect(container.querySelector('[data-testid="victim-panel"]')).toBeNull();
    // The universal inspection surface still opens (pre-47 behavior intact).
    expect(container.querySelector('[data-testid="focus-inspection"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="focus-inspection-name"]')?.textContent).toBe(
      "Victim",
    );
  });
});
