import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "../api/client";
import type { AccusationCandidatesDTO } from "../api/types";
import { makeAccusationResponse, makeCandidates, TEST_TOKEN } from "../scene/testFixtures";
import { AccusationFlow, type AccusationServices } from "./accusationFlow";
import AccusationPanel from "./AccusationPanel";

/**
 * Accusation panel coverage (Phase 7 K/O) with the same pure-function +
 * react-dom/server pattern as the evidence panel: the flow is a plain object,
 * so the component renders headlessly and deterministically. No DOM, no jsdom,
 * no network.
 */

const PT_ID = "PT-test-0001";

function makeServices(overrides: Partial<AccusationServices> = {}): AccusationServices {
  return {
    submitAccusation: vi.fn(async () => makeAccusationResponse()),
    getReveal: vi.fn(async () => {
      throw new ApiError(403, "REVEAL_NOT_AVAILABLE", "no accusation yet", null);
    }),
    ...overrides,
  };
}

function makeFlow(candidates: AccusationCandidatesDTO = makeCandidates(), overrides: Partial<AccusationServices> = {}):
  AccusationFlow {
  return new AccusationFlow(makeServices(overrides), TEST_TOKEN, { playthroughId: PT_ID }, candidates);
}

function selectAll(flow: AccusationFlow): void {
  flow.selectSuspect("suspect_alpha");
  flow.selectMotive("motive_alpha");
  flow.selectWeapon("weapon_alpha");
  flow.setCrimeTime("21:45");
}

function render(flow: AccusationFlow, onResetToken: () => void = () => {}): string {
  return renderToStaticMarkup(
    <AccusationPanel flow={flow} onReveal={() => {}} onResetToken={onResetToken} />,
  );
}

/** Extract the full `<input .../>` element that carries the given data-testid. */
function inputFor(html: string, testId: string): string {
  const marker = `data-testid="${testId}"`;
  const markerIndex = html.indexOf(marker);
  if (markerIndex < 0) return "";
  const start = html.lastIndexOf("<input", markerIndex);
  const end = html.indexOf("/>", markerIndex);
  if (start < 0 || end < 0) return "";
  return html.slice(start, end + 2);
}

describe("accusation candidate rendering", () => {
  it("renders suspects/motives/weapons in the exact returned order (no client re-order)", () => {
    const flow = makeFlow();
    const html = render(flow);

    const suspects = ["suspect_alpha", "suspect_beta", "suspect_gamma"];
    const motives = ["motive_alpha", "motive_beta", "motive_gamma"];
    const weapons = ["weapon_alpha", "weapon_beta", "weapon_gamma"];

    const suspectIndexes = suspects.map((id) => html.indexOf(`accusation-option-murdererId-${id}`));
    const motiveIndexes = motives.map((id) => html.indexOf(`accusation-option-motiveId-${id}`));
    const weaponIndexes = weapons.map((id) => html.indexOf(`accusation-option-weaponId-${id}`));

    expect(suspectIndexes).toEqual([...suspectIndexes].sort((a, b) => a - b));
    expect(motiveIndexes).toEqual([...motiveIndexes].sort((a, b) => a - b));
    expect(weaponIndexes).toEqual([...weaponIndexes].sort((a, b) => a - b));
    expect(suspectIndexes.every((index) => index >= 0)).toBe(true);
  });

  it("no pre-reveal correctness indicator: the editing markup never contains Correct/Winner", () => {
    const flow = makeFlow();
    const html = render(flow);
    expect(html.toLowerCase()).not.toContain("correct");
    expect(html.toLowerCase()).not.toContain("winner");
    expect(html).toContain('data-testid="accusation-suspects"');
  });

  it("renders candidate text as inert plain text (hostile names stay literal)", () => {
    const hostile: AccusationCandidatesDTO = {
      suspects: [
        { id: "a", name: "<script>alert(1)</script>" },
        { id: "b", name: "ひらがな — 你好 — 😀" },
        { id: "c", name: "<img src=x onerror=alert(2)>" },
      ],
      motives: [{ id: "m1", label: "<script>x</script>" }],
      weapons: [{ id: "w1", assetId: "a1", name: "<b>bold</b>" }],
    };
    const html = render(makeFlow(hostile));

    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("&lt;b&gt;bold&lt;/b&gt;");
    expect(html).toContain("ひらがな");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<b>bold</b>");
  });

  it("renders a labelled 24h time-of-day input (WHEN) with no date control", () => {
    const html = render(makeFlow());
    expect(html).toContain('type="time"');
    expect(html).toContain('data-testid="accusation-time"');
    expect(html).toContain("24-hour");
    expect(html).not.toContain('type="date"');
  });
});

describe("selection state renders", () => {
  it("marks the chosen option with the checked attribute", () => {
    const flow = makeFlow();
    flow.selectSuspect("suspect_beta");
    flow.selectMotive("motive_gamma");
    flow.selectWeapon("weapon_beta");
    flow.setCrimeTime("19:00");

    const html = render(flow);

    expect(inputFor(html, "accusation-option-murdererId-suspect_beta")).toContain('checked=""');
    expect(inputFor(html, "accusation-option-murdererId-suspect_alpha")).not.toContain('checked=""');
    expect(inputFor(html, "accusation-option-motiveId-motive_gamma")).toContain('checked=""');
    expect(inputFor(html, "accusation-option-motiveId-motive_alpha")).not.toContain('checked=""');
    expect(inputFor(html, "accusation-option-weaponId-weapon_beta")).toContain('checked=""');
    expect(inputFor(html, "accusation-option-weaponId-weapon_alpha")).not.toContain('checked=""');
    expect(html).toContain('value="19:00"');
  });
});

describe("missing-field validation UI", () => {
  it("shows per-dimension error copy when the confirmation step is blocked", () => {
    const flow = makeFlow();
    flow.selectSuspect("suspect_alpha");
    flow.setCrimeTime("25:99");
    flow.openConfirmation();

    const html = render(flow);

    expect(html).toContain('data-testid="accusation-field-error-motiveId"');
    expect(html).toContain('data-testid="accusation-field-error-weaponId"');
    expect(html).toContain('data-testid="accusation-field-error-crimeTime"');
    expect(html).toContain("24-hour time");
    expect(html).not.toContain('data-testid="accusation-confirm"'); // still editing
  });
});

describe("confirmation step (irreversible submit)", () => {
  it("opens a confirmation summary with the selected answers before any submit control", () => {
    const flow = makeFlow();
    selectAll(flow);
    flow.openConfirmation();

    const html = render(flow);

    expect(html).toContain('data-testid="accusation-confirmation"');
    expect(html).toContain('data-testid="accusation-summary-murderer"');
    expect(html).toContain('data-testid="accusation-summary-motive"');
    expect(html).toContain('data-testid="accusation-summary-weapon"');
    expect(html).toContain('data-testid="accusation-summary-time"');
    expect(html).toContain("Ada Marsh");
    expect(html).toContain("A dispute over money");
    expect(html).toContain("Kitchen knife");
    expect(html).toContain("21:45");
    // The editing pickers are gone; the confirm control is present.
    expect(html).not.toContain('data-testid="accusation-suspects"');
    expect(html).toContain('data-testid="accusation-confirm"');
    expect(html).not.toContain('data-testid="accusation-submit"');
  });

  it("disables the confirm button while the submission is in flight", async () => {
    let release!: (value: ReturnType<typeof makeAccusationResponse>) => void;
    const gate = new Promise<ReturnType<typeof makeAccusationResponse>>((resolve) => {
      release = resolve;
    });
    const flow = makeFlow(makeCandidates(), { submitAccusation: vi.fn(() => gate) });
    selectAll(flow);
    flow.openConfirmation();

    const pending = flow.confirmAccusation();
    const html = render(flow);

    expect(html).toContain('data-testid="accusation-confirm" disabled=""');
    expect(html).toContain("Submitting…");
    expect(html).toContain('data-testid="accusation-edit" disabled=""');

    release(makeAccusationResponse());
    await pending;
  });
});

describe("accepted / already-submitted states", () => {
  it("renders the accepted state with a Reveal the case action after a 200", async () => {
    const services = makeServices();
    const flow = makeFlow(makeCandidates(), services);
    selectAll(flow);
    flow.openConfirmation();
    await flow.confirmAccusation();

    const html = render(flow);

    expect(html).toContain('data-testid="accusation-accepted"');
    expect(html).toContain('data-testid="reveal-case"');
    expect(html).toContain("Reveal the case");
  });

  it("renders the already-submitted message with a Reveal the case action after a 409", async () => {
    const services = makeServices({
      submitAccusation: vi.fn(async () => {
        throw new ApiError(409, "CASE_ALREADY_SUBMITTED", "already submitted", null);
      }),
    });
    const flow = makeFlow(makeCandidates(), services);
    selectAll(flow);
    flow.openConfirmation();
    await flow.confirmAccusation();

    const html = render(flow);

    expect(html).toContain("This case has already been submitted");
    expect(html).toContain('data-testid="reveal-case"');
  });
});

describe("expired credential UI", () => {
  it("renders the server error with a reset-token affordance when the token is invalid", async () => {
    const services = makeServices({
      submitAccusation: vi.fn(async () => {
        throw new ApiError(401, "UNAUTHORIZED", "bearer token expired", null);
      }),
    });
    const flow = makeFlow(makeCandidates(), services);
    selectAll(flow);
    flow.openConfirmation();
    await flow.confirmAccusation();

    const resetToken = vi.fn();
    const html = render(flow, resetToken);

    expect(html).toContain('data-testid="accusation-server-error"');
    expect(html).toContain('data-testid="accusation-reset-token"');
    expect(html).toContain("no longer valid");
  });

  it("renders a server (422-style) error without the reset affordance when the token is fine", async () => {
    const services = makeServices({
      submitAccusation: vi.fn(async () => {
        throw new ApiError(422, "VALIDATION_ERROR", "weaponId is not a known candidate", null);
      }),
    });
    const flow = makeFlow(makeCandidates(), services);
    selectAll(flow);
    flow.openConfirmation();
    await flow.confirmAccusation();

    const html = render(flow);

    expect(html).toContain("weaponId is not a known candidate");
    expect(html).not.toContain('data-testid="accusation-reset-token"');
  });
});

describe("keyboard accessibility", () => {
  it("pairs every picker with a labelled control and a single submit action", () => {
    const html = render(makeFlow());
    expect(html).toContain("<legend>");
    expect(html).toContain("for="); // label->input wiring
    expect(html).toContain('type="radio"');
    expect(html).toContain('data-testid="accusation-submit"');
  });
});