// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { RevealResponse, InvestigationBootstrapResponse } from "../api/types";
import { setSavegameDownloadSink } from "../savegame/exportV1";
import { activeReplay, clearReplay, startReplay } from "../savegame/replaySession";
import { parseSavegameV1, utf8ByteLength } from "../savegame/savegameV1";
import canonical from "../savegame/fixtures/v1_demo_apartment.pdcase.json";
import { makeBootstrap, makeRevealResponse } from "../scene/testFixtures";

/**
 * Phase 32 — `/reveal` Save Case flow (Phase32 §5/§25/§36 + §42 "EXPORT"):
 * the prompt appears ONLY after the reveal; the live path calls the server's
 * savegame endpoint exactly once and downloads the server-owned text; a
 * loaded replay re-exports locally with ZERO server calls; the spoiler note
 * is honest; "Not now" dismisses.
 */

const holders = vi.hoisted(() => ({
  reveal: null as RevealResponse | null,
  revealError: null as Error | null,
  bootstrap: null as InvestigationBootstrapResponse | null,
}));

vi.mock("../api/playthroughToken", () => ({
  getPlaythroughToken: () => "lX9fQ3sWv0aB2cD4eF6gH8iJ1kM3nO5pQ7rS9tU",
  getPlaythroughId: () => "PT-live-0001",
  setPlaythroughToken: () => true,
  setPlaythroughId: () => true,
  clearPlaythroughCredentials: vi.fn(),
  validatePlaythroughToken: () => ({ ok: true }),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getInvestigation: vi.fn(async () => holders.bootstrap),
    getReveal: vi.fn(async () => {
      if (holders.revealError !== null) throw holders.revealError;
      if (holders.reveal === null) throw new ApiError(403, "REVEAL_NOT_AVAILABLE", "not accused", null);
      return holders.reveal;
    }),
    getSavegame: vi.fn((_pt: string, _token: string) => ({
      text: JSON.stringify({ format: "procedural-detective-case", exportedAt: "2099-01-01T00:00:00Z" }),
      suggestedFilename: "procedural-detective-case-CASE-DqHMXBPUvcAS.pdcase",
    })),
  };
});

import RevealPage from "./reveal";

declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const CANONICAL_TEXT: string = JSON.stringify(canonical);

interface Mounted {
  container: HTMLDivElement;
  root: ReturnType<typeof createRoot>;
}

function mountReveal(): Mounted {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={["/reveal"]}>
        <RevealPage />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

async function flushAsync(): Promise<void> {
  await act(async () => {
    for (let index = 0; index < 16; index += 1) await Promise.resolve();
  });
}

async function waitFor(container: HTMLDivElement, testId: string): Promise<void> {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (container.querySelector(`[data-testid="${testId}"]`) !== null) return;
    await act(async () => {
      await Promise.resolve();
    });
  }
  throw new Error(`data-testid "${testId}" never rendered`);
}

beforeEach(() => {
  vi.clearAllMocks();
  holders.reveal = makeRevealResponse();
  holders.revealError = null;
  holders.bootstrap = makeBootstrap({ state: "REVEALED" });
});

afterEach(() => {
  clearReplay();
  setSavegameDownloadSink(null);
  document.body.innerHTML = "";
});

describe("reveal — Save Case availability", () => {
  it("renders NO Save prompt while the error gate is shown (not-accused)", async () => {
    holders.revealError = new ApiError(403, "REVEAL_NOT_AVAILABLE", "no accusation", null);
    const mounted = mountReveal();
    await waitFor(mounted.container, "reveal-error");
    expect(mounted.container.querySelector('[data-testid="save-case-prompt"]')).toBeNull();
  });

  it("offers Save Case only after the reveal (revealed state)", async () => {
    const mounted = mountReveal();
    await waitFor(mounted.container, "reveal-screen");
    await waitFor(mounted.container, "save-case-prompt");
    expect(mounted.container.querySelector('[data-testid="save-case-confirm"]')).not.toBeNull();
    expect(mounted.container.querySelector('[data-testid="save-case-dismiss"]')).not.toBeNull();
    // Honest §7 spoiler note is present.
    const note = mounted.container.querySelector('[data-testid="save-case-spoiler-note"]');
    expect(note?.textContent).toContain("contains the complete case");
    expect(note?.textContent).toContain("Avoid opening the file manually");
  });

  it("Save Case (live) calls the server savegame endpoint exactly once and downloads it", async () => {
    const captured: Array<string> = [];
    setSavegameDownloadSink({ download: (text) => void captured.push(text) });
    const mounted = mountReveal();
    await waitFor(mounted.container, "save-case-prompt");
    const confirm = mounted.container.querySelector('button[data-testid="save-case-confirm"]') as HTMLButtonElement;
    await act(async () => {
      confirm.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(mounted.container, "save-case-saved");
    // The live Save makes EXACTLY ONE call with the LIVE playthrough token.
    const client = await import("../api/client");
    const getSavegame = client.getSavegame as ReturnType<typeof vi.fn>;
    expect(getSavegame).toHaveBeenCalledTimes(1);
    expect(getSavegame).toHaveBeenCalledWith("PT-live-0001", "lX9fQ3sWv0aB2cD4eF6gH8iJ1kM3nO5pQ7rS9tU");
    expect(captured.length).toBe(1);
    expect(captured[0]).toContain('"format":"procedural-detective-case"');
  });

  it("Not now dismisses the prompt without any download", async () => {
    const captured: Array<string> = [];
    setSavegameDownloadSink({ download: (text) => void captured.push(text) });
    const mounted = mountReveal();
    await waitFor(mounted.container, "save-case-prompt");
    const dismiss = mounted.container.querySelector('button[data-testid="save-case-dismiss"]') as HTMLButtonElement;
    await act(async () => {
      dismiss.click();
    });
    await flushAsync();
    expect(mounted.container.querySelector('[data-testid="save-case-prompt"]')).toBeNull();
    expect(captured.length).toBe(0);
  });
});

describe("reveal — loaded replay Save Case (Phase32 §15)", () => {
  it("a loaded replay re-exports locally with ZERO server savegame calls", async () => {
    const definition = parseSavegameV1(CANONICAL_TEXT, utf8ByteLength(CANONICAL_TEXT));
    startReplay(definition);
    // Drive the shared in-memory replay to the REVEALED lifecycle (the route
    // would otherwise gate on 403 REVEAL_NOT_AVAILABLE, exactly like a fresh
    // server playthrough).
    const state = activeReplay()!.state;
    state.interactObject("kitchen_knife", "inspect");
    state.submitAccusation({
      murdererId: "thomas_reed",
      motiveId: "cover_up_embezzlement",
      weaponId: "kitchen_knife",
      crimeTime: "22:17:00",
    });
    expect(state.getReveal().result.overall).toBe("solved");

    const captured: Array<string> = [];
    setSavegameDownloadSink({ download: (text) => void captured.push(text) });
    holders.bootstrap = makeBootstrap({ state: "REVEALED" });
    const mounted = mountReveal();
    await waitFor(mounted.container, "save-case-prompt");
    expect(mounted.container.querySelector('[data-testid="replay-source-label"]')?.textContent).toBe("Saved Case");
    expect(mounted.container.querySelector('[data-testid="replay-back-to-menu"]')).not.toBeNull();

    const confirm = mounted.container.querySelector('button[data-testid="save-case-confirm"]') as HTMLButtonElement;
    await act(async () => {
      confirm.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(mounted.container, "save-case-saved");
    expect(captured.length).toBe(1);
    // Local re-export: the live savegame endpoint is NEVER called.
    const client = await import("../api/client");
    const getSavegame = client.getSavegame as ReturnType<typeof vi.fn>;
    expect(getSavegame).not.toHaveBeenCalled();
    expect(captured[0]).toContain('"format"');
    expect(captured[0]).toContain("procedural-detective-case");
  });
});