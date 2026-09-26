// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EvidenceReadResultDTO, WitnessListEntryDTO, WitnessQuestionType, WitnessStatementDTO } from "../api/types";
import {
  EMILY_WITNESS_ID,
  EMILY_WITNESS_NAME,
  makeEmilyNeutralStatement,
  makeEmilyTimeStatement,
  makeHostileWitnessInterviewResponse,
  makeWitnessListEntry,
} from "../scene/testFixtures";
import type { WitnessAskOutcome } from "../scene/investigationFlow";
import WitnessPanel from "./WitnessPanel";
import {
  handleWitnessPanelKey,
  parseWitnessInterviewResponse,
  witnessQuestionKey,
} from "./witnessModel";

// React 19 act() support in the jsdom test environment.
declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Phase 23 — witness interview PANEL coverage.
 *
 * Headless (react-dom/server): identity + "Witness" role, the SIX closed
 * question buttons with HUMAN labels, the labelled dialog, asked-state
 * markers, and zero token leaks.
 *
 * jsdom: selecting a question POSTs (via onAsk) the correct questionType and
 * renders the deterministic statement (summary + observations + semantic
 * <time dateTime> with compact HH:mm display); neutral answers render
 * cleanly; idempotent re-asks use the cached statement (no POST, no
 * duplicate state); "Ask another question" returns to the question grid;
 * Close/Escape wiring + focus-into-panel + focus-return; hostile
 * name/statement strings render INERT (escaped, bounded).
 *
 * CSS contract (read from src/index.css): the panel is viewport-bounded with
 * its own vertical scroll, the question list is a flex column (buttons can
 * NEVER overlap at 1280/600/360px) and statement text wraps (no horizontal
 * page overflow).
 */

const EMILY: WitnessListEntryDTO = makeWitnessListEntry({
  witnessId: EMILY_WITNESS_ID,
  displayName: EMILY_WITNESS_NAME,
  presence: "ON_SCENE",
});

function okOutcome(
  questionType: WitnessQuestionType,
  statement: WitnessStatementDTO,
  record: EvidenceReadResultDTO | null = null,
): WitnessAskOutcome {
  return {
    ok: true,
    witnessId: EMILY_WITNESS_ID,
    displayName: EMILY_WITNESS_NAME,
    questionType,
    statement,
    discovery: record !== null ? { newlyDiscovered: true, record } : null,
    record,
    cached: false,
  };
}

describe("WitnessPanel — headless markup (react-dom/server)", () => {
  function markup(overrides: Partial<Parameters<typeof WitnessPanel>[0]> = {}) {
    return renderToStaticMarkup(
      <WitnessPanel
        witness={EMILY}
        askedQuestions={[]}
        statementCache={new Map()}
        onAsk={async () => okOutcome("TIME", makeEmilyTimeStatement())}
        onClose={() => {}}
        {...overrides}
      />,
    );
  }

  it("renders the displayName + the 'Witness' role inside a labelled dialog", () => {
    const html = markup();
    expect(html).toContain("Emily Reed");
    expect(html).toContain('aria-label="Witness interview: Emily Reed"');
    expect(html).toContain('data-testid="witness-panel-role"');
    expect(html).toContain(">Witness<");
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
  });

  it("renders ALL SIX question buttons with the required HUMAN labels", () => {
    const html = markup();
    for (const [questionType, label] of [
      ["OBSERVATION", "What did you see?"],
      ["TIME", "When were you there?"],
      ["SOUND", "Did you hear anything?"],
      ["PERSON", "Did you notice anyone?"],
      ["OBJECT", "Did you notice any unusual objects?"],
      ["LOCATION", "Where were you?"],
    ] as const) {
      expect(html).toContain(`data-testid="witness-question-${questionType}"`);
      expect(html).toContain(`>${label}<`);
    }
    // Every question button is a REAL native button with type="button".
    const buttonCount = (html.match(/witness-question-button/g) ?? []).length;
    expect(buttonCount).toBe(6);
    expect(html).toContain('type="button"');
  });

  it("marks already-asked questions with an answered state (number unique)", () => {
    const askedQuestions: WitnessQuestionType[] = ["TIME", "SOUND"];
    const html = markup({ askedQuestions, statementCache: new Map() });
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("· answered");
    // Exactly TWO asked markers — no duplicates.
    expect((html.match(/· answered/g) ?? []).length).toBe(2);
    expect((html.match(/aria-pressed="true"/g) ?? []).length).toBe(2);
  });

  it("renders NO statement content before any question is asked", () => {
    const html = markup();
    expect(html).not.toContain("heavy impact");
    expect(html).not.toContain("23:42");
    expect(html).not.toContain("recalls");
    // No enum token appears as VISIBLE text (only inside data-testid hooks).
    expect(html).not.toContain(">OBSERVATION<");
    expect(html).not.toContain(">TIME<");
    expect(html).not.toContain('">TIME</button>');
  });

  it("renders an inert Close button with an accessible label", () => {
    const html = markup();
    expect(html).toContain('data-testid="witness-close"');
    expect(html).toContain('aria-label="Close interview with Emily Reed"');
    expect(html).toContain("autofocus");
  });
});

describe("WitnessPanel — interaction (jsdom)", () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    root = null;
    container.remove();
  });

  async function flushAsync(): Promise<void> {
    await act(async () => {
      for (let index = 0; index < 12; index += 1) await Promise.resolve();
    });
  }

  function mount(
    overrides: Partial<Parameters<typeof WitnessPanel>[0]> = {},
  ) {
    const onAsk = vi.fn(
      async (questionType: WitnessQuestionType): Promise<WitnessAskOutcome> => {
        if (questionType === "SOUND") {
          return okOutcome("SOUND", makeEmilyNeutralStatement());
        }
        return okOutcome(questionType, makeEmilyTimeStatement());
      },
    );
    const onClose = vi.fn();
    act(() => {
      root = createRoot(container);
      root.render(
        <WitnessPanel
          witness={EMILY}
          askedQuestions={[]}
          statementCache={new Map()}
          onAsk={onAsk}
          onClose={onClose}
          {...overrides}
        />,
      );
    });
    return { onAsk, onClose };
  }

  const questionButton = (questionType: WitnessQuestionType): HTMLButtonElement => {
    const button = container.querySelector<HTMLButtonElement>(
      `[data-testid="witness-question-${questionType}"]`,
    );
    if (!button) throw new Error(`missing question button ${questionType}`);
    return button;
  };

  it("selecting a question calls onAsk with the correct closed questionType (each of the six)", async () => {
    const { onAsk } = mount();
    for (const questionType of ["OBSERVATION", "TIME", "SOUND", "PERSON", "OBJECT", "LOCATION"] as const) {
      onAsk.mockClear();
      act(() => {
        questionButton(questionType).dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await flushAsync();
      expect(onAsk).toHaveBeenCalledTimes(1);
      expect(onAsk).toHaveBeenCalledWith(questionType);
      // The statement renders (the SOUND one is neutral; all others grounded).
      expect(container.querySelector('[data-testid="witness-answer"]')).not.toBeNull();
      // Return to the question grid for the next assertion.
      act(() => {
        container.querySelector<HTMLButtonElement>('[data-testid="witness-ask-another"]')!.click();
      });
    }
  });

  it("renders the grounded statement: summary + observations with semantic <time dateTime> and compact HH:mm display", async () => {
    mount();
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();

    const summary = container.querySelector('[data-testid="witness-statement-summary"]');
    expect(summary?.textContent).toContain("heavy impact at approximately 23:42");
    const times = Array.from(container.querySelectorAll("time"));
    expect(times).toHaveLength(2);
    // Compact visible clock, canonical value in the semantic attribute.
    expect(times[0].textContent).toBe("23:40");
    expect(times[0].getAttribute("dateTime")).toBe("23:40");
    expect(times[1].textContent).toBe("23:42");
    expect(times[1].getAttribute("dateTime")).toBe("23:42");
    const observations = container.querySelectorAll("li.witness-observation");
    expect(observations).toHaveLength(2);
    expect(observations[1].textContent).toContain("She heard a heavy impact");
    // The asked question label heads the answer (human label, never the enum).
    expect(
      container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
    ).toBe("When were you there?");
  });

  it("renders a NEUTRAL answer cleanly (no observations list, no discovery panel)", async () => {
    const { onAsk } = mount();
    act(() => {
      questionButton("SOUND").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();
    expect(onAsk).toHaveBeenCalledWith("SOUND");
    expect(
      container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
    ).toContain("Nothing stood out to me");
    expect(container.querySelector('[data-testid="witness-observations"]')).toBeNull();
    expect(container.querySelector('[data-testid="evidence-panel"]')).toBeNull();
  });

  it("IDEMPOTENT re-ask: an answered question is served from the cache — no POST, no duplicate state, same statement", async () => {
    const cached = makeEmilyTimeStatement();
    const statementCache = new Map<WitnessQuestionType, WitnessStatementDTO>([["TIME", cached]]);
    const askedQuestions: WitnessQuestionType[] = ["TIME"];
    const { onAsk } = mount({ askedQuestions, statementCache });

    // The asked-state renders as exactly ONE answered marker + aria-pressed.
    expect(container.querySelectorAll('[data-testid="witness-question-TIME-asked"]')).toHaveLength(1);
    expect(questionButton("TIME").getAttribute("aria-pressed")).toBe("true");

    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();

    // No POST happened — the cached statement rendered instead.
    expect(onAsk).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="witness-statement-summary"]')?.textContent).toBe(
      cached.summary,
    );
    // Re-asking never adds a second marker; returning to the grid keeps ONE.
    act(() => {
      container.querySelector<HTMLButtonElement>('[data-testid="witness-ask-another"]')!.click();
    });
    expect(container.querySelectorAll('[data-testid="witness-question-TIME-asked"]')).toHaveLength(1);
    expect(questionButton("TIME").getAttribute("aria-pressed")).toBe("true");
  });

  it("'Ask another question' returns to the six-button grid after an answer", async () => {
    mount();
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();
    expect(container.querySelector('[data-testid="witness-answer"]')).not.toBeNull();
    act(() => {
      container.querySelector<HTMLButtonElement>('[data-testid="witness-ask-another"]')!.click();
    });
    expect(container.querySelector('[data-testid="witness-answer"]')).toBeNull();
    expect(container.querySelector('[data-testid="witness-questions"]')).not.toBeNull();
    for (const questionType of ["OBSERVATION", "TIME", "SOUND", "PERSON", "OBJECT", "LOCATION"] as const) {
      expect(questionButton(questionType)).not.toBeNull();
    }
  });

  it("Close activates onClose; Escape maps to close via handleWitnessPanelKey", () => {
    const { onClose } = mount();
    act(() => {
      container.querySelector<HTMLButtonElement>('[data-testid="witness-close"]')!.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(handleWitnessPanelKey("Escape")).toBe("close");
  });

  it("focus moves INTO the panel (Close autofocuses) and RETURNS to the opening control on unmount", async () => {
    // A focusable opener OUTSIDE the panel (exactly like a Witnesses button).
    // It lives in its own wrapper so the panel's root render never replaces it.
    const openerHost = document.createElement("div");
    document.body.appendChild(openerHost);
    const opener = document.createElement("button");
    opener.textContent = "Interview Emily Reed";
    openerHost.appendChild(opener);
    opener.focus();
    expect(document.activeElement).toBe(opener);

    const panelHost = document.createElement("div");
    document.body.appendChild(panelHost);
    const panelRoot = createRoot(panelHost);
    act(() => {
      panelRoot.render(
        <WitnessPanel
          witness={EMILY}
          askedQuestions={[]}
          statementCache={new Map()}
          onAsk={async () => okOutcome("TIME", makeEmilyTimeStatement())}
          onClose={() => {}}
        />,
      );
    });
    await flushAsync();
    // autoFocus moved focus to the real Close button inside the panel.
    expect(document.activeElement?.getAttribute("data-testid")).toBe("witness-close");

    act(() => {
      panelRoot.unmount();
    });
    // Unmount cleanup restored focus to the activating control.
    expect(document.activeElement).toBe(opener);
    openerHost.remove();
    panelHost.remove();
  });

  it("HOSTILE name/statement content renders INERT (escaped) and BOUNDED", async () => {
    const hostile = makeHostileWitnessInterviewResponse();
    const parsed = parseWitnessInterviewResponse(hostile);
    const hostileWitness: WitnessListEntryDTO = {
      witnessId: EMILY_WITNESS_ID,
      displayName: parsed.displayName,
      presence: "REMOTE_STATEMENT",
      sceneObjectId: null,
    };
    mount({
      witness: hostileWitness,
      onAsk: vi.fn(async (): Promise<WitnessAskOutcome> => ({
        ok: true,
        witnessId: EMILY_WITNESS_ID,
        displayName: parsed.displayName,
        questionType: "TIME",
        statement: parsed.statement,
        discovery: null,
        record: null,
        cached: false,
      })),
    });

    // No live script/image nodes ever exist in the DOM — hostile attribute
    // values and text are inert literal strings, never parsed back into nodes.
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('[data-testid="witness-panel-name"]')?.textContent).toContain(
      "<script>",
    );

    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull(); // "<b>bold</b>" stays text
    const summaryText =
      container.querySelector('[data-testid="witness-statement-summary"]')?.textContent ?? "";
    expect(summaryText).toContain("<script>alert(1)</script>");
    expect(summaryText).toContain("<img src=x");
    const observationText =
      container.querySelector('[data-testid="witness-observations"]')?.textContent ?? "";
    expect(observationText).toContain("<script>alert(1)</script>");
    expect(observationText).toContain("ひらがな");
    expect(observationText).toContain("😀");
    // The hostile <time> canonical value stays a literal DATETIME attribute;
    // the visible clock text is the same literal (never executed).
    const time = container.querySelector("[data-testid=\"witness-observation-time-0\"]");
    expect(time?.getAttribute("dateTime")).toContain("<script>");
    // Bounded: the 4000-char summary is capped.
    expect(summaryText.length).toBeLessThanOrEqual(2001);
  });

  it("statement cache keys are stable per (witness, question) for the re-ask path", () => {
    expect(witnessQuestionKey(EMILY_WITNESS_ID, "TIME")).toBe(witnessQuestionKey(EMILY_WITNESS_ID, "TIME"));
    expect(witnessQuestionKey(EMILY_WITNESS_ID, "TIME")).not.toBe(witnessQuestionKey(EMILY_WITNESS_ID, "SOUND"));
  });
});

describe("WitnessPanel — responsive CSS contract (1280/600/360, Phase23 §36)", () => {
  // jsdom has no layout engine, so the width-independent guarantees are pinned
  // against the SHIPPED stylesheet (same approach as the Phase 19J test): the
  // panel is viewport-bounded + self-scrolling, question buttons are a flex
  // column (can never overlap at ANY width) and statement text wraps.
  const css = readFileSync(join(process.cwd(), "src", "index.css"), "utf8")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");

  function rule(selectorPattern: string): string {
    const source = selectorPattern.replace(/\s*\n\s*/g, "\\s*");
    const match = new RegExp(`${source}\\s*\\{([^}]*)\\}`).exec(css);
    if (!match) throw new Error(`missing shipped rule matching ${selectorPattern}`);
    return match[1];
  }

  it("the panel box is viewport-bounded with its own vertical scroll — usable at 360px, never horizontally oversized", () => {
    const panel = rule(`\\.witness-panel`);
    expect(panel).toMatch(/width\s*:\s*min\(24rem,\s*calc\(100%\s*-\s*2rem\)\)/);
    expect(panel).toMatch(/max-height\s*:\s*calc\(100%\s*-\s*2rem\)/);
    expect(panel).toMatch(/overflow\s*:\s*auto/);
    expect(panel).not.toMatch(/min-width/);
  });

  it("question buttons are a single flex column — overlap is structurally impossible at every width", () => {
    const list = rule(`\\.witness-question-list`);
    expect(list).toMatch(/display\s*:\s*flex/);
    expect(list).toMatch(/flex-direction\s*:\s*column/);
    const button = rule(`\\.witness-question-button`);
    expect(button).toMatch(/min-width\s*:\s*0/);
    expect(button).toMatch(/overflow-wrap\s*:\s*anywhere/);
  });

  it("statement text wraps inside its box — no horizontal page overflow", () => {
    const summary = rule(`\\.witness-statement-summary`);
    expect(summary).toMatch(/overflow-wrap\s*:\s*anywhere/);
    const observation = rule(`\\.witness-observation-text`);
    expect(observation).toMatch(/overflow-wrap\s*:\s*anywhere/);
  });

  it("the hostile-name heading still wraps (overflow-wrap on the panel name)", () => {
    const name = rule(`\\.witness-panel-name`);
    expect(name).toMatch(/overflow-wrap\s*:\s*anywhere/);
  });
});