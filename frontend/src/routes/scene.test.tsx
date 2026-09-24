// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EvidenceReadResultDTO, InteractionResultDTO } from "../api/types";
import type { RenderOptions } from "../scene/renderInvestigation";
import { makeBootstrap, makeForkWorldObject } from "../scene/testFixtures";

/**
 * Phase 19F — UNIVERSAL OBJECT INSPECTION: the /scene route renders the
 * "Objects in this room" accessibility fallback as an interactive button for
 * EVERY published semantic world object (decorative/structural objects such
 * as door/lamp/table/vase/victim included), drives each activation through
 * the real InvestigationSession to the backend, and distinguishes the
 * cosmetic INSPECTED state from the server-authoritative EVIDENCE_DISCOVERED
 * state.
 *
 * The route is booted headlessly in jsdom: the playthrough credential store,
 * the API client and the Babylon glue are mocked (the glue returns a fake
 * handle that still captures the onPick callback), so the same wiring a real
 * browser uses — 3D onPick -> session.interact -> applyFeedback -> toast /
 * discovery / counter — is exercised without a backend or GPU.
 */

// Hermetic holders (hoisted before the mocks run): the canned bootstrap the
// mocked client returns and the onPick callback the mocked scene factory
// captures (so tests can drive the 3D pick path without Babylon).
const holders = vi.hoisted(() => ({
  bootstrap: null as ReturnType<typeof makeBootstrap> | null,
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
    async (
      _pt: string,
      objectId: string,
      interaction: string,
      _token: string,
    ): Promise<InteractionResultDTO> => {
      // The knife is the evidence-linked placement: discovery as before.
      if (objectId === "kitchen_knife" && interaction === "inspect") {
        return {
          objectId,
          interaction,
          evidenceId: "forensic_knife_match_01",
          discovery: {
            evidenceId: "forensic_knife_match_01",
            kind: "forensic",
            title: "Kitchen knife",
            interaction,
            state: "discovered",
          },
          result: "interacted",
          inspection: { relevant: true, label: "Kitchen knife" },
        };
      }
      // Phase 19F: EVERY other published semantic object (incl. the empty-
      // interaction decoratives vase/door/lamp/table/victim and the fork)
      // answers the safe inspection result — no discovery, no evidence.
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
    async (): Promise<EvidenceReadResultDTO> => ({
      evidenceId: "forensic_knife_match_01",
      kind: "forensic",
      title: "Kitchen knife",
      description: null,
      openedAt: "2026-09-11T22:20:00+02:00",
      readByPlayer: true,
      content: {},
    }),
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

import ScenePage from "./scene";

// React's test utilities flag the act(...) environment; without this every
// act() call would also print "not configured to support act(...)" noise.
declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/** The published world the mocked backend returns: the golden nine + the semantic fork. */
function makeSceneBootstrap(): ReturnType<typeof makeBootstrap> {
  const bootstrap = makeBootstrap();
  bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, makeForkWorldObject()];
  return bootstrap;
}

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

/** Let the async investigation boot / interaction chains settle (all mocked). */
async function flushAsync(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 16; i += 1) await Promise.resolve();
  });
}

/** Poll (with microtask flushes) until the ready branch is rendered. */
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
  const el = container.querySelector(selector);
  if (!el) throw new Error(`expected element not found: ${selector}`);
  return el as T;
}

function objectButton(container: HTMLElement, objectId: string): HTMLButtonElement {
  return queryRequired<HTMLButtonElement>(container, `[data-testid="object-${objectId}"]`);
}

function clickObject(container: HTMLElement, objectId: string): void {
  const button = objectButton(container, objectId);
  act(() => {
    button.click();
  });
}

function toastText(container: HTMLElement): string {
  return queryRequired(container, '[data-testid="discovery-toast-text"]').textContent ?? "";
}

describe("Phase 19F — /scene accessibility fallback (universal inspectable buttons)", () => {
  let mounted: Mounted | null = null;

  beforeEach(() => {
    holders.bootstrap = makeSceneBootstrap();
    holders.lastOnPick = null;
    mounted = mountScene();
  });

  afterEach(() => {
    const root = mounted?.root;
    mounted = null;
    holders.lastOnPick = null;
    if (root) act(() => root.unmount());
  });

  const container = (): HTMLElement => {
    if (!mounted) throw new Error("test mounted page is gone");
    return mounted.container;
  };

  it("renders an INTERACTIVE BUTTON for EVERY published semantic world object (no plain-text entries)", async () => {
    await flushAsync();
    await waitForReady(container());

    // door/lamp/table/vase/victim (empty published interaction), the
    // interactive evidence objects AND the procedural fork all render buttons.
    for (const objectId of [
      "apartment_door",
      "apartment_lamp",
      "apartment_laptop",
      "apartment_table",
      "kitchen_knife",
      "letter_opener",
      "scissors",
      "vase_01",
      "victim_body_placeholder",
      "fork",
    ]) {
      const button = objectButton(container(), objectId);
      expect(button.getAttribute("type"), `${objectId} is a native button`).toBe("button");
    }

    // No list item is a plain-text-only entry: every li has a button.
    const lis = Array.from(container().querySelectorAll(".scene-objects li"));
    expect(lis.length, "one li per published object").toBe(10);
    for (const li of lis) {
      expect(li.querySelector("button"), `${li.textContent} li has a button`).not.toBeNull();
    }
  });

  it("each button carries the SEMANTIC human label as its accessible content (no raw ids)", async () => {
    await flushAsync();
    await waitForReady(container());

    for (const [objectId, label] of [
      ["apartment_door", "Door"],
      ["apartment_lamp", "Lamp"],
      ["apartment_table", "Table"],
      ["vase_01", "Vase"],
      ["victim_body_placeholder", "Victim"],
      ["kitchen_knife", "Kitchen knife"],
      ["fork", "Fork"],
    ] as const) {
      const text = objectButton(container(), objectId).textContent?.trim() ?? "";
      expect(text, `${objectId} label`).toBe(label);
      expect(text).not.toContain("proc.");
      expect(text).not.toContain(objectId);
    }
    // No render/proc.* id leaks anywhere in the fallback list markup.
    expect(container().textContent).not.toContain("proc.");
    expect(container().textContent).not.toContain("decor.");
  });

  it("keyboard/button activation of the VASE inspect -> nothing-found toast, no discovery, counter unchanged, no model mutation", async () => {
    await flushAsync();
    await waitForReady(container());

    // The button is a native type="button" (Enter/Space activate in a real
    // browser); activation dispatches the exact published (EMPTY) interaction.
    clickObject(container(), "vase_01");
    await flushAsync();

    expect(toastText(container())).toBe("Nothing relevant was found on the Vase.");
    // No discovery: no strip entry, no discovered marker on ANY object, and
    // the counter stays at zero ("Nothing discovered yet").
    expect(container().querySelector('[data-testid^="discovered-entry-"]')).toBeNull();
    expect(container().querySelector('[data-testid="object-discovered-kitchen_knife"]')).toBeNull();
    expect(container().textContent).toContain("Nothing discovered yet");

    // INSPECTED state is shown; EVIDENCE_DISCOVERED is NOT.
    expect(container().querySelector('[data-testid="object-inspected-vase_01"]')).not.toBeNull();
    expect(container().querySelector('[data-testid="object-discovered-vase_01"]')).toBeNull();
  });

  it("door/lamp/table all reach the nothing-found inspection copy too (universal, not just vase)", async () => {
    await flushAsync();
    await waitForReady(container());

    for (const [objectId, label] of [
      ["apartment_door", "Door"],
      ["apartment_lamp", "Lamp"],
      ["apartment_table", "Table"],
      ["victim_body_placeholder", "Victim"],
    ] as const) {
      clickObject(container(), objectId);
      await flushAsync();
      expect(toastText(container()), objectId).toBe(`Nothing relevant was found on the ${label}.`);
      expect(container().querySelector(`[data-testid="object-discovered-${objectId}"]`), "no false discovery").toBeNull();
      expect(container().querySelector('[data-testid^="discovered-entry-"]'), "counter unchanged").toBeNull();
      expect(container().querySelector(`[data-testid="object-inspected-${objectId}"]`)).not.toBeNull();
    }
  });

  it("clicking the KNIFE keeps the discovery flow unchanged (panel, counter, markers)", async () => {
    await flushAsync();
    await waitForReady(container());

    clickObject(container(), "kitchen_knife");
    await flushAsync();

    expect(toastText(container())).toBe("Discovered: Kitchen knife");
    // The record panel opens with the discovered record.
    expect(container().querySelector('[data-testid="evidence-panel"]')).not.toBeNull();
    // The discovered counter increments and the strip entry appears.
    expect(container().querySelector('[data-testid="discovered-entry-forensic_knife_match_01"]')).not.toBeNull();
    // Markers: inspected AND discovered are both true (distinct states).
    expect(container().querySelector('[data-testid="object-inspected-kitchen_knife"]')).not.toBeNull();
    expect(container().querySelector('[data-testid="object-discovered-kitchen_knife"]')).not.toBeNull();
    expect(container().querySelector('[data-testid="object-read-kitchen_knife"]')).not.toBeNull();
    // The selected (panel-owner) state is reflected on the button.
    expect(objectButton(container(), "kitchen_knife").getAttribute("aria-pressed")).toBe("true");
  });

  it("a 3D pick (onPick) of the vase routes to the SAME nothing-found toast (universal picking round-trip)", async () => {
    await flushAsync();
    await waitForReady(container());

    // The mocked Babylon glue captured the route's onPick — fire it exactly
    // like a real mesh click on the published vase root would.
    expect(holders.lastOnPick, "scene glue received the onPick callback").not.toBeNull();
    act(() => {
      holders.lastOnPick?.("vase_01");
    });
    await flushAsync();

    expect(toastText(container())).toBe("Nothing relevant was found on the Vase.");
    expect(container().querySelector('[data-testid^="discovered-entry-"]')).toBeNull();
    expect(container().querySelector('[data-testid="object-inspected-vase_01"]')).not.toBeNull();
  });
});