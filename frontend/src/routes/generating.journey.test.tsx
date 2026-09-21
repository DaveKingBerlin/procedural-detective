// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GenerationJourney } from "./generating";
import type { CapabilityLoader } from "./generating";
import type { DemoFlowResult } from "../journey/demoFlow";
import type { JourneyParams } from "../journey/context";

// React's test utilities need the act() environment flag (same as the other
// jsdom suites in this repo).
declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/**
 * ADV-212 — the /generating journey never claims the Local-AI label sequence
 * from an unverified localStorage value. The stored `pd_generation_mode` is
 * validated against the LIVE generation-capabilities DTO before ANY label is
 * chosen: a stale/tampered `local` on a demo-only (or failing) capability
 * probe resolves to the generic/demo sequence, and the SAME validated mode
 * drives both the pre-poll animation and the run's progress snapshots.
 */

const PARAMS: JourneyParams = { prompt: "A crime", difficulty: "medium" };

const demoOnly: CapabilityLoader = async () => ({
  modes: [{ id: "demo", available: true }],
});
const localReady: CapabilityLoader = async () => ({
  modes: [
    { id: "demo", available: true },
    { id: "local", available: true, label: "Local AI", model: "llama3.2:3b" },
  ],
});
const failing: CapabilityLoader = async () => {
  throw new Error("probe unreachable");
};

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  // Stale/tampered stored mode: every scenario below starts from this state.
  localStorage.setItem("pd_generation_mode", "local");
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  container.remove();
  localStorage.clear();
});

/** Let the async capability probe + effect chain settle inside an act scope. */
async function settleEffects(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

interface Mounted {
  run: ReturnType<typeof vi.fn<(...args: readonly unknown[]) => Promise<DemoFlowResult>>>;
  /** Resolve the gated run promise (ends the journey with a safe failure). */
  releaseRun: () => Promise<void>;
}

function mountJourney(loadCapabilities: CapabilityLoader): Mounted {
  let release: ((result: DemoFlowResult) => void) | null = null;
  const run = vi.fn<(...args: readonly unknown[]) => Promise<DemoFlowResult>>(
    (_prompt, _difficulty, _onProgress, _mode) => {
      const gate = new Promise<DemoFlowResult>((resolve) => {
        release = resolve;
      });
      return gate;
    },
  );
  act(() => {
    root = createRoot(container);
    root.render(
      <MemoryRouter initialEntries={["/generating"]}>
        <GenerationJourney
          params={PARAMS}
          run={run}
          onSuccess={() => {
            throw new Error("must never auto-enter in these scenarios");
          }}
          loadCapabilities={loadCapabilities}
        />
      </MemoryRouter>,
    );
  });
  const releaseRun = async () => {
    await act(async () => {
      if (!release) throw new Error("run gate not created yet");
      release({ ok: false, failure: { kind: "failed", message: "test only" } });
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });
  };
  return { run, releaseRun };
}

function stageLabel(): string | null {
  const el = container.querySelector<HTMLElement>('[data-testid="generation-stage-label"]');
  return el?.textContent ?? null;
}

function failedView(): boolean {
  return container.querySelector('[data-testid="generation-failed"]') !== null;
}

describe("ADV-212 — the /generating label sequence requires LIVE capability confirmation", () => {
  it("stale local storage + demo-only capabilities -> generic labels; run receives null", async () => {
    const { run, releaseRun } = mountJourney(demoOnly);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][3]).toBeNull(); // validated mode, never raw storage
    expect(stageLabel()).toBe("Creating case");
    expect(stageLabel()).not.toContain("Understanding the case");
    await releaseRun();
    expect(failedView()).toBe(true); // the run really happened with the validated mode
  });

  it("local storage + capabilities confirm local ready -> local labels; run receives local", async () => {
    const { run, releaseRun } = mountJourney(localReady);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][3]).toBe("local");
    expect(stageLabel()).toBe("Understanding the case…");
    await releaseRun();
  });

  it("capabilities fetch failure -> generic labels (never a local pipeline claim)", async () => {
    const { run, releaseRun } = mountJourney(failing);
    await settleEffects();
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][3]).toBeNull();
    expect(stageLabel()).toBe("Creating case");
    expect(stageLabel()).not.toContain("Understanding the case");
    expect(stageLabel()).not.toContain("crime scene");
    await releaseRun();
    expect(failedView()).toBe(true);
  });

  it("well-formed local flow unchanged: capabilities confirm -> the Local-AI sequence is claimed", async () => {
    // (Stand-in for the /new-driven happy path: storage says local AND the
    // live DTO confirms it — the §21 sequence is exactly as before.)
    const { run, releaseRun } = mountJourney(localReady);
    await settleEffects();
    expect(run.mock.calls[0][3]).toBe("local");
    expect(stageLabel()).toBe("Understanding the case…");
    await releaseRun();
  });
});