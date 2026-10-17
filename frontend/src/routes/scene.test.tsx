// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EvidenceReadResultDTO, InteractionResultDTO } from "../api/types";
import { clearReplay, replayStorageNamespace, startReplay } from "../savegame/replaySession";
import { parseSavegameV1, utf8ByteLength } from "../savegame/savegameV1";
import canonical from "../savegame/fixtures/v1_demo_apartment.pdcase.json";
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
  laptopClicks: 0,
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
      // Phase 19J: the laptop discovery (a device that will publish
      // ACTIVITY_LOG content) — first click "discovered", later "already-
      // discovered", mirroring the server's state transition.
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
            state: ++holders.laptopClicks === 1 ? "discovered" : "already-discovered",
          },
          result: "interacted",
          inspection: { relevant: true, label: "Laptop" },
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
    async (_pt: string, recordId: string): Promise<EvidenceReadResultDTO> => {
      // Phase 19J laptop record (ACTIVITY_LOG-shaped content) — its notebook/
      // counter footprint is exactly ONE evidence item like any other.
      if (recordId === "email_thomas_01") {
        return {
          evidenceId: "email_thomas_01",
          kind: "email",
          title: "Laptop activity log",
          description: null,
          openedAt: "2026-09-11T22:20:00+02:00",
          readByPlayer: true,
          content: { renderType: "ACTIVITY_LOG", entries: [] },
        };
      }
      return {
        evidenceId: "forensic_knife_match_01",
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

/** Phase 32 — the canonical server export used for replay-mount tests. */
const CANONICAL_TEXT: string = JSON.stringify(canonical);

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
    holders.laptopClicks = 0;
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

  it("Phase 19J — clicking the LAPTOP discovers its evidence ONCE (counter/strip/panel) and a repeat click is idempotent (§29)", async () => {
    await flushAsync();
    await waitForReady(container());

    // First click: exactly one discovery — panel opens, counter/strip shows
    // exactly the ONE laptop evidence item (an ACTIVITY_LOG laptop publishes
    // 15–20 visible rows but discovers ONE evidence, §28/§29).
    clickObject(container(), "apartment_laptop");
    await flushAsync();

    expect(toastText(container())).toBe("Discovered: Laptop activity log");
    expect(container().querySelector('[data-testid="evidence-panel"]')).not.toBeNull();
    expect(container().querySelector('[data-testid="discovered-entry-email_thomas_01"]')).not.toBeNull();
    expect(container().querySelectorAll('[data-testid^="discovered-entry-"]')).toHaveLength(1);
    expect(objectButton(container(), "apartment_laptop").getAttribute("aria-pressed")).toBe("true");

    // Second click: the server answer "already-discovered" — still the SAME
    // one entry, no counter growth, no duplicate panel data (idempotent).
    clickObject(container(), "apartment_laptop");
    await flushAsync();

    expect(toastText(container())).toBe("Already discovered: Laptop activity log");
    expect(container().querySelector('[data-testid="evidence-panel"]')).not.toBeNull();
    expect(container().querySelectorAll('[data-testid^="discovered-entry-"]')).toHaveLength(1);
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
    // Phase 42 §14 — the 3D pick of a NEUTRAL object also opens the unified
    // inspection target (focus surface) — one pipeline for every selectable
    // object.
    expect(container().querySelector('[data-testid="focus-inspection"]')).not.toBeNull();
    expect(container().querySelector('[data-testid="focus-inspection-name"]')?.textContent).toBe(
      "Vase",
    );
  });

  it("Phase 42 §30 — the scene fixture covers 1 special + 1 evidence-bearing + 1 neutral object", async () => {
    await flushAsync();
    await waitForReady(container());

    const world = holders.bootstrap!.scene.worldObjects;
    const byId = new Map(world.map((o) => [o.objectId, o]));
    // SPECIAL: the knife (interaction inspect + evidence-linked placement).
    expect(byId.get("kitchen_knife")?.interaction).toBe("inspect");
    expect(byId.get("kitchen_knife")?.evidenceId).toBe("forensic_knife_match_01");
    // EVIDENCE-BEARING: the laptop (read interaction + evidence).
    expect(byId.get("apartment_laptop")?.interaction).toBe("read");
    expect(byId.get("apartment_laptop")?.evidenceId).toBe("email_thomas_01");
    // NEUTRAL: the table/lamp (empty interaction, no evidence).
    expect(byId.get("apartment_table")?.interaction).toBe("");
    expect(byId.get("apartment_table")?.evidenceId).toBeNull();
    expect(byId.get("apartment_lamp")?.interaction).toBe("");
    expect(byId.get("apartment_lamp")?.evidenceId).toBeNull();
  });

  it("Phase 42 §31 — clicking the KNIFE opens the unified inspection while the special discovery behavior is preserved", async () => {
    await flushAsync();
    await waitForReady(container());

    clickObject(container(), "kitchen_knife");
    await flushAsync();

    // Special-object behavior layered on top: discovery + evidence panel.
    expect(toastText(container())).toBe("Discovered: Kitchen knife");
    expect(container().querySelector('[data-testid="evidence-panel"]')).not.toBeNull();
    expect(container().querySelector('[data-testid="discovered-entry-forensic_knife_match_01"]')).not.toBeNull();
    // AND the unified inspection target opens with the correct name.
    expect(container().querySelector('[data-testid="focus-inspection"]')).not.toBeNull();
    expect(container().querySelector('[data-testid="focus-inspection-name"]')?.textContent).toBe(
      "Kitchen knife",
    );
    // Close works for the special object too (one close path, §22).
    act(() => {
      container().querySelector<HTMLElement>('[data-testid="focus-close"]')!.click();
    });
    await flushAsync();
    expect(container().querySelector('[data-testid="focus-inspection"]')).toBeNull();
  });

  it("Phase 42 §32 — clicking the TABLE opens inspection: correct name, focus active, close works, NO evidence invented (PRIMARY)", async () => {
    await flushAsync();
    await waitForReady(container());

    clickObject(container(), "apartment_table");
    await flushAsync();

    // The inspection surface opens with the SEMANTIC name — no invented
    // description, no fabricated evidence (§17).
    expect(container().querySelector('[data-testid="focus-inspection"]')).not.toBeNull();
    expect(container().querySelector('[data-testid="focus-inspection-name"]')?.textContent).toBe(
      "Table",
    );
    // Focus state active on the selector surface.
    expect(container().querySelector('[data-testid="object-apartment_table"]')?.getAttribute("aria-pressed")).toBe(
      "true",
    );
    // Neutral stays neutral: no evidence panel, no discovered entries.
    expect(container().querySelector('[data-testid="evidence-panel"]')).toBeNull();
    expect(container().querySelector('[data-testid^="discovered-entry-"]')).toBeNull();
    expect(container().textContent).toContain("Nothing discovered yet");

    // Close inspection restores the world (surface unmounts, §22).
    act(() => {
      container().querySelector<HTMLElement>('[data-testid="focus-close"]')!.click();
    });
    await flushAsync();
    expect(container().querySelector('[data-testid="focus-inspection"]')).toBeNull();
  });

  it("Phase 42 §32 — clicking the LAMP opens the same inspection (focus active, no invented evidence)", async () => {
    await flushAsync();
    await waitForReady(container());

    clickObject(container(), "apartment_lamp");
    await flushAsync();

    expect(container().querySelector('[data-testid="focus-inspection"]')).not.toBeNull();
    expect(container().querySelector('[data-testid="focus-inspection-name"]')?.textContent).toBe(
      "Lamp",
    );
    expect(container().querySelector('[data-testid="evidence-panel"]')).toBeNull();
    expect(container().querySelector('[data-testid^="discovered-entry-"]')).toBeNull();
  });

  it("Phase 42 §33 — 3D pick and accessibility-list activation produce the SAME inspection state for one ordinary object", async () => {
    await flushAsync();
    await waitForReady(container());

    // Activate the table through the 3D scene (onPick) first.
    act(() => {
      holders.lastOnPick?.("apartment_table");
    });
    await flushAsync();
    expect(container().querySelector('[data-testid="focus-inspection-name"]')?.textContent).toBe(
      "Table",
    );
    expect(container().querySelector('[data-testid="object-apartment_table"]')?.getAttribute("aria-pressed")).toBe(
      "true",
    );

    // Close, then activate the SAME object through the accessibility list.
    act(() => {
      container().querySelector<HTMLElement>('[data-testid="focus-close"]')!.click();
    });
    await flushAsync();
    clickObject(container(), "apartment_table");
    await flushAsync();

    // Identical selected/inspection state (one shared pipeline, §19/§20).
    expect(container().querySelector('[data-testid="focus-inspection-name"]')?.textContent).toBe(
      "Table",
    );
    expect(container().querySelector('[data-testid="object-apartment_table"]')?.getAttribute("aria-pressed")).toBe(
      "true",
    );
  });

  it("Phase 42 §35 — switching table -> lamp clears the stale focus and updates the inspection (no mixed state)", async () => {
    await flushAsync();
    await waitForReady(container());

    clickObject(container(), "apartment_table");
    await flushAsync();
    expect(container().querySelector('[data-testid="focus-inspection-name"]')?.textContent).toBe(
      "Table",
    );

    clickObject(container(), "apartment_lamp");
    await flushAsync();

    // Exactly ONE inspection surface, now for the lamp — the stale Table
    // focus is cleared, the panel updates without per-object cleanup.
    expect(container().querySelectorAll('[data-testid="focus-inspection"]')).toHaveLength(1);
    expect(container().querySelector('[data-testid="focus-inspection-name"]')?.textContent).toBe(
      "Lamp",
    );
    expect(container().querySelector('[data-testid="object-apartment_lamp"]')?.getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(
      container().querySelector('[data-testid="object-apartment_table"]')?.getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("P42-ADV-06 — opening a NEUTRAL-object inspection from the accessibility list keeps keyboard focus predictable and returns it on close", async () => {
    await flushAsync();
    await waitForReady(container());

    // A keyboard/list activation puts focus on the TABLE list button, then
    // opens its (neutral) inspection from the accessibility fallback.
    const tableButton = objectButton(container(), "apartment_table");
    tableButton.focus();
    clickObject(container(), "apartment_table");
    await flushAsync();

    // The inspection surface opens and autoFocus keeps focus inside it on the
    // native Close button (the previously-intended activation behavior).
    expect(container().querySelector('[data-testid="focus-inspection"]')).not.toBeNull();
    expect(document.activeElement?.getAttribute("data-testid")).toBe("focus-close");

    // Closing the inspection returns focus to the activating control — never
    // stranded in the removed overlay, never dropped to <body>.
    act(() => {
      container().querySelector<HTMLElement>('[data-testid="focus-close"]')!.click();
    });
    await flushAsync();
    expect(container().querySelector('[data-testid="focus-inspection"]')).toBeNull();
    expect(document.activeElement).toBe(tableButton);
  });
});

describe("Phase 32 DEF-045 — replay notebook/pin restore uses the PER-FILE namespace", () => {
  let mounted: Mounted | null = null;

  beforeEach(() => {
    holders.bootstrap = makeSceneBootstrap();
    holders.lastOnPick = null;
    holders.laptopClicks = 0;
  });

  afterEach(() => {
    const root = mounted?.root;
    mounted = null;
    holders.lastOnPick = null;
    if (root) act(() => root.unmount());
    clearReplay();
    window.localStorage.clear();
  });

  it("restores pins from the per-file namespace, never the static saved-replay key", async () => {
    // A replay of the canonical fixture is active when the scene mounts.
    const definition = parseSavegameV1(CANONICAL_TEXT, utf8ByteLength(CANONICAL_TEXT));
    const namespace = replayStorageNamespace(definition);

    // Decoy content under the OLD static shared key (what the pre-fix route
    // would read when restoring pins during a replay).
    window.localStorage.setItem(
      "pd_hypothesis_v1:saved-replay",
      JSON.stringify({ suspect: "michael_carter", motive: null, weapon: null, time: null }),
    );
    // The replay file's OWN per-file namespace carries its real pins.
    window.localStorage.setItem(
      `pd_hypothesis_v1:${namespace}`,
      JSON.stringify({ suspect: "thomas_reed", motive: "cover_up_embezzlement", weapon: "kitchen_knife", time: "22:17" }),
    );

    startReplay(definition);
    mounted = mountScene();
    await flushAsync();
    await waitForReady(mounted.container);

    // The notebook opens with the PER-FILE pins ("thomas_reed"), not the
    // static-key decoy ("michael_carter") — a different loaded file must
    // never bleed its notes in.
    const suspectSelect = mounted.container.querySelector(
      '[data-testid="hypothesis-suspect-select"]',
    ) as HTMLSelectElement | null;
    expect(suspectSelect?.value).toBe("thomas_reed");
    // The static namespace is never read nor modified by the route.
    expect(window.localStorage.getItem("pd_hypothesis_v1:saved-replay")).toContain("michael_carter");
  });

  it("a replay boot touches NO live playthrough namespace", async () => {
    const definition = parseSavegameV1(CANONICAL_TEXT, utf8ByteLength(CANONICAL_TEXT));
    const livePins = JSON.stringify({ suspect: "michael_carter", motive: null, weapon: null, time: null });
    window.localStorage.setItem("pd_hypothesis_v1:PT-test-0001", livePins);

    startReplay(definition);
    mounted = mountScene();
    await flushAsync();
    await waitForReady(mounted.container);

    // The live namespace keeps its exact bytes — replay start/restore never
    // writes into or clears a live playthrough's player notes.
    expect(window.localStorage.getItem("pd_hypothesis_v1:PT-test-0001")).toBe(livePins);
    // A pure replay boot persists NOTHING: no imported case/truth bytes, no
    // per-file note keys (the player has not pinned/interviewed anything yet).
    // The ONLY entry is the pre-seeded live playthrough namespace.
    expect(Object.keys(window.localStorage).sort()).toEqual(["pd_hypothesis_v1:PT-test-0001"]);
  });
});

/**
 * Phase 47 Test 10 — save -> restore -> inspect victim. A loaded `.pdcase`
 * replay must derive the optional `victim` block from the SAVED definition
 * (no network; replay is browser-local) so the scene route renders the
 * VictimProfilePanel and progressively discloses the body record.
 */
describe("Phase 47 — a saved-case replay renders the victim profile", () => {
  let mounted: Mounted | null = null;

  beforeEach(() => {
    holders.bootstrap = makeSceneBootstrap();
  });

  afterEach(() => {
    const root = mounted?.root;
    mounted = null;
    if (root) act(() => root.unmount());
    clearReplay();
    window.localStorage.clear();
  });

  it("derives the victim block from the loaded file and renders/reveals the body record", async () => {
    const definition = parseSavegameV1(CANONICAL_TEXT, utf8ByteLength(CANONICAL_TEXT));
    startReplay(definition);
    mounted = mountScene();
    await flushAsync();
    await waitForReady(mounted.container);

    clickObject(mounted.container, "victim_body_placeholder");
    await flushAsync();

    // The victim surface (not the generic evidence panel) is shown.
    expect(mounted.container.querySelector('[data-testid="victim-panel"]')).not.toBeNull();
    expect(
      mounted.container.querySelector('[data-testid="victim-identity-name"]')?.textContent,
    ).toContain("Sarah Miller");
    // The body record was discovered+read through the replay and is now
    // progressively disclosed as the discovery timeline.
    expect(mounted.container.querySelector('[data-testid="victim-timeline"]')).not.toBeNull();
    expect(mounted.container.querySelector('[data-testid="evidence-panel"]')).toBeNull();
  });
});