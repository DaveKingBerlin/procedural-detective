// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import VictimProfilePanel from "./VictimProfilePanel";
import { VICTIM_LABELS } from "./victimLabels";
import {
  makeHostileVictimRecord,
  makeVictimBodyRecord,
  makeVictimForensicRecord,
  makeVictimProfile,
} from "./testFixtures";

// React 19 act() support in the jsdom test environment.
declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/** Phase 47 — victim profile panel (presentational + accessible). */

let container: HTMLDivElement;
let root: Root | undefined;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  root = undefined;
  container.remove();
});

function mount(records = [makeVictimBodyRecord()], onClose: () => void = () => {}): void {
  act(() => {
    root = createRoot(container);
    root.render(
      <VictimProfilePanel victim={makeVictimProfile()} records={records} onClose={onClose} />,
    );
  });
}

describe("VictimProfilePanel — identity & discovery", () => {
  it("renders name, age, gender, occupation, discovery location and time", () => {
    const html = renderToStaticMarkup(
      <VictimProfilePanel victim={makeVictimProfile()} records={[]} onClose={() => {}} />,
    );
    expect(html).toContain("Sarah Miller");
    expect(html).toContain("Female");
    expect(html).toContain(">41<");
    expect(html).toContain("Architect");
    expect(html).toContain("Miller Apartment - Kitchen");
    expect(html).toContain('dateTime="2026-09-11T22:18:31+02:00"');
    expect(html).toContain("22:18");
    expect(html).toContain("Victim — Body found in the kitchen at 22:18");
  });

  it("omits a row entirely when the value is null/empty (never undefined/null/NaN)", () => {
    const html = renderToStaticMarkup(
      <VictimProfilePanel
        victim={makeVictimProfile({ gender: null, age: null, occupation: null, background: null })}
        records={[]}
        onClose={() => {}}
      />,
    );
    expect(html).toContain("Sarah Miller");
    // Omitted rows leave no broken value behind.
    expect(html).not.toContain("undefined");
    expect(html).not.toContain("NaN");
    expect(html).not.toContain(">null<");
    expect(html).not.toContain("victim-identity-age");
    expect(html).not.toContain("victim-identity-gender");
    expect(html).not.toContain("victim-identity-occupation");
  });

  it("shows the neutral discovery message when no discovery block is published", () => {
    const html = renderToStaticMarkup(
      <VictimProfilePanel victim={makeVictimProfile({ discovery: null })} records={[]} onClose={() => {}} />,
    );
    expect(html).toContain(VICTIM_LABELS.noDiscovery);
    expect(html).not.toContain("Found at");
  });
});

describe("VictimProfilePanel — progressive disclosure", () => {
  it("shows the neutral forensic message before a forensic record is read, then the finding", () => {
    const before = renderToStaticMarkup(
      <VictimProfilePanel victim={makeVictimProfile()} records={[makeVictimBodyRecord()]} onClose={() => {}} />,
    );
    expect(before).toContain(VICTIM_LABELS.noForensics);
    expect(before).not.toContain("blunt-force trauma");

    const after = renderToStaticMarkup(
      <VictimProfilePanel
        victim={makeVictimProfile()}
        records={[makeVictimBodyRecord(), makeVictimForensicRecord()]}
        onClose={() => {}}
      />,
    );
    expect(after).toContain("blunt-force trauma");
    expect(after).not.toContain(VICTIM_LABELS.noForensics);
  });

  it("renders the body-discovery timeline entries once the record is read", () => {
    const html = renderToStaticMarkup(
      <VictimProfilePanel victim={makeVictimProfile()} records={[makeVictimBodyRecord()]} onClose={() => {}} />,
    );
    expect(html).toContain('data-testid="victim-timeline"');
    expect(html).toContain("Body found in the kitchen at 22:18");
  });

  it("shows the neutral known-facts message when no victim-related record is read", () => {
    const html = renderToStaticMarkup(
      <VictimProfilePanel victim={makeVictimProfile({ background: null })} records={[]} onClose={() => {}} />,
    );
    expect(html).toContain(VICTIM_LABELS.noKnownFacts);
  });
});

describe("VictimProfilePanel — no hidden-truth leak", () => {
  it("never renders non-allowlisted truth keys from a read record", () => {
    const html = renderToStaticMarkup(
      <VictimProfilePanel victim={makeVictimProfile()} records={[makeHostileVictimRecord()]} onClose={() => {}} />,
    );
    for (const marker of [
      "HIDDEN_SUSPECT_MARKER",
      "HIDDEN_WEAPON_MARKER",
      "HIDDEN_MOTIVE_MARKER",
      "HIDDEN_TRUTH_MARKER",
      "HIDDEN_SOLUTION_MARKER",
    ]) {
      expect(html, `leaked ${marker}`).not.toContain(marker);
    }
  });
});

describe("VictimProfilePanel — accessibility", () => {
  it("is a labelled dialog with a labelled, focused Close button", () => {
    const html = renderToStaticMarkup(
      <VictimProfilePanel victim={makeVictimProfile()} records={[]} onClose={() => {}} />,
    );
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain(`aria-label="${VICTIM_LABELS.panelAriaLabel}"`);
    expect(html).toContain(`aria-label="${VICTIM_LABELS.closeLabel}"`);
    expect(html).toContain("autofocus");
    expect(html).toContain(VICTIM_LABELS.identityHeading);
    expect(html).toContain(VICTIM_LABELS.knownFactsHeading);
    expect(html).toContain(VICTIM_LABELS.forensicHeading);
    expect(html).toContain(VICTIM_LABELS.timelineHeading);
  });

  it("closes when the Close button is activated", () => {
    const onClose = vi.fn();
    mount([], onClose);
    act(() => {
      container.querySelector<HTMLButtonElement>('[data-testid="victim-close"]')!.click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
