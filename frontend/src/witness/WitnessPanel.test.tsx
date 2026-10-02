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
  LISA_WITNESS_ID,
  LISA_WITNESS_NAME,
  makeEmilyNeutralStatement,
  makeEmilyTimeStatement,
  makeHostileWitnessInterviewResponse,
  makeWitnessListEntry,
} from "../scene/testFixtures";
import type { WitnessAskOutcome } from "../scene/investigationFlow";
import WitnessPanel from "./WitnessPanel";
import {
  WITNESS_QUESTION_LABELS,
  WITNESS_QUESTION_ORDER,
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
 * Phase 23 + Phase 26 (FixUI-A) — witness interview PANEL coverage.
 *
 * The panel is now TWO stable vertical regions inside one flex-column dialog:
 * the six question buttons stay PERMANENTLY visible above a single DERIVED
 * answer. Phase 26 pins the new model:
 *   - single-selection state (`selectedQuestionId` — no statement collection);
 *   - clicking question B replaces answer A outright (no Close step);
 *   - `witness-ask-another` is GONE — dismissal is the header Close + Escape;
 *   - switching `witness.witnessId` resets the selection (clean redisplay);
 *   - a late `onAsk` outcome for an abandoned selection is DISCARDED;
 *   - idempotent re-ask semantics are unchanged (cache only, no POST).
 *
 * Headless (react-dom/server): identity + "Witness" role, the SIX closed
 * question buttons with HUMAN labels, the labelled dialog, asked-state
 * markers, the neutral answer hint and zero token leaks.
 *
 * jsdom: selecting a question POSTs (via onAsk) the correct questionType and
 * renders the deterministic statement; replacement behavior; exactly-one
 * answer invariant; stale/late-response guard; witness-switch reset; Close/
 * Escape + focus-into-panel + focus-return; hostile strings inert + bounded.
 *
 * CSS contract (read from src/index.css): the panel is viewport-bounded with
 * a normal flex-column Questions → Answer flow, both regions scroll inside
 * the panel, `.witness-answer` is NEVER absolutely/fixed positioned, the
 * question list is a flex column (buttons can NEVER overlap at ANY width)
 * and statement text wraps (no horizontal page overflow).
 */

const EMILY: WitnessListEntryDTO = makeWitnessListEntry({
  witnessId: EMILY_WITNESS_ID,
  displayName: EMILY_WITNESS_NAME,
  presence: "ON_SCENE",
});

const LISA: WitnessListEntryDTO = makeWitnessListEntry({
  witnessId: LISA_WITNESS_ID,
  displayName: LISA_WITNESS_NAME,
  presence: "REMOTE_STATEMENT",
  sceneObjectId: null,
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

  it("renders NO statement content before a question is selected — the answer region shows a NEUTRAL hint instead", () => {
    const html = markup();
    expect(html).not.toContain("heavy impact");
    expect(html).not.toContain("23:42");
    expect(html).not.toContain("recalls");
    // No enum token appears as VISIBLE text (only inside data-testid hooks).
    expect(html).not.toContain(">OBSERVATION<");
    expect(html).not.toContain(">TIME<");
    expect(html).not.toContain('">TIME</button>');
    // Phase 26 initial state: an answer region with a small neutral hint.
    expect(html).toContain('data-testid="witness-answer-hint"');
    expect(html).toContain("Select a question to view the witness");
    expect(html).toContain("answer.");
    expect(html).toContain('data-testid="witness-answer-heading"');
    expect(html).not.toContain('data-testid="witness-answer-question"');
  });

  it("renders an inert Close button with an accessible label", () => {
    const html = markup();
    expect(html).toContain('data-testid="witness-close"');
    expect(html).toContain('aria-label="Close interview with Emily Reed"');
    expect(html).toContain("autofocus");
  });

  it("renders NO 'Ask another question' control anywhere (the statement Close is removed by FixUI-A)", () => {
    const html = markup();
    expect(html).not.toContain('data-testid="witness-ask-another"');
    expect(html).not.toContain("witness-ask-another");
    expect(html).not.toContain("Ask another question");
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

  it("selecting a question calls onAsk with the correct closed questionType (each of the six, answer replaced each time)", async () => {
    const { onAsk } = mount();
    for (const questionType of WITNESS_QUESTION_ORDER) {
      onAsk.mockClear();
      act(() => {
        questionButton(questionType).dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await flushAsync();
      expect(onAsk).toHaveBeenCalledTimes(1);
      expect(onAsk).toHaveBeenCalledWith(questionType);
      // The statement renders headed by the SELECTED question's human label
      // (the previous question's answer is replaced — one region only).
      expect(container.querySelectorAll('[data-testid="witness-answer"]')).toHaveLength(1);
      expect(
        container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
      ).toBe(WITNESS_QUESTION_LABELS[questionType]);
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
    // Phase 26: the questions STAY visible alongside the cached answer.
    expect(container.querySelector('[data-testid="witness-questions"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-testid="witness-answer"]')).toHaveLength(1);

    // Re-asking again stays cached: still no POST, still exactly ONE marker.
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();
    expect(onAsk).not.toHaveBeenCalled();
    expect(container.querySelectorAll('[data-testid="witness-question-TIME-asked"]')).toHaveLength(1);
    expect(questionButton("TIME").getAttribute("aria-pressed")).toBe("true");
  });

  it("question buttons REMAIN visible and clickable while an answer is shown (questions + answer coexist)", async () => {
    mount();
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();

    expect(container.querySelector('[data-testid="witness-answer"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="witness-questions"]')).not.toBeNull();
    for (const questionType of WITNESS_QUESTION_ORDER) {
      const button = questionButton(questionType);
      expect(button).not.toBeNull();
      expect(button.disabled).toBe(false);
    }
  });

  it("clicking question B REPLACES answer A with answer B (A's content disappears)", async () => {
    mount();
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();
    expect(
      container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
    ).toContain("heavy impact");

    // Without any close step, selecting SOUND swaps the answer.
    act(() => {
      questionButton("SOUND").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();
    const summary = container.querySelector('[data-testid="witness-statement-summary"]');
    expect(summary?.textContent).toBe("No. Nothing stood out to me.");
    expect(summary?.textContent).not.toContain("heavy impact");
    expect(
      container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
    ).toBe("Did you hear anything?");
    // A's content is GONE from the answer region — exactly one answer exists.
    expect(container.querySelectorAll('[data-testid="witness-answer"]')).toHaveLength(1);
  });

  it("exactly ONE answer container exists after multiple sequential selections", async () => {
    mount();
    for (const questionType of ["TIME", "SOUND", "OBSERVATION", "LOCATION"] as const) {
      act(() => {
        questionButton(questionType).dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await flushAsync();
      expect(container.querySelectorAll('[data-testid="witness-answer"]')).toHaveLength(1);
    }
    // The final selection is LOCATION — nothing stacked or overlapped.
    expect(
      container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
    ).toBe(WITNESS_QUESTION_LABELS.LOCATION);
  });

  it("RAPID switching: a late response for an ABANDONED selection is DISCARDED (single POST in flight)", async () => {
    // TIME is UNCACHED and its POST is DELAYED; SOUND is already read (cached).
    let resolveDelayed: ((outcome: WitnessAskOutcome) => void) | null = null;
    const onAsk = vi.fn(
      async (questionType: WitnessQuestionType): Promise<WitnessAskOutcome> => {
        return new Promise<WitnessAskOutcome>((resolve) => {
          resolveDelayed = resolve;
          void questionType;
        });
      },
    );
    const cachedNeutral = makeEmilyNeutralStatement();
    const { onClose } = mount({
      onAsk,
      statementCache: new Map<WitnessQuestionType, WitnessStatementDTO>([["SOUND", cachedNeutral]]),
      askedQuestions: ["SOUND"],
    });

    // Click TIME: a delayed POST starts (selection = TIME, busy).
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });

    // While it is in flight, click SOUND — a CACHED question: its answer shows
    // immediately with NO second POST.
    act(() => {
      questionButton("SOUND").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(onAsk).toHaveBeenCalledWith("TIME");
    expect(
      container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
    ).toBe("Did you hear anything?");
    expect(
      container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
    ).toBe(cachedNeutral.summary);

    // The delayed TIME response now lands — the selection moved to SOUND, so
    // the late response is DISCARDED (never overwrites the current answer).
    act(() => {
      expect(resolveDelayed).not.toBeNull();
      resolveDelayed?.(okOutcome("TIME", makeEmilyTimeStatement()));
    });
    await flushAsync();

    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(container.querySelectorAll('[data-testid="witness-answer"]')).toHaveLength(1);
    expect(
      container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
    ).toBe("Did you hear anything?");
    expect(
      container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
    ).toBe(cachedNeutral.summary);
    expect(container.textContent).not.toContain("heavy impact");
    // Header Close still wires the dismissal; onClose was NOT auto-triggered.
    expect(onClose).not.toHaveBeenCalled();
  });

  it("REGRESSION (triaged MEDIUM): clicking question B while an UNCACHED POST A is in flight MUST auto-ask B once the slot frees — B's answer renders with NO second click, exactly two POSTs (A then B), A's late content never shown", async () => {
    // Deterministic deferred promise: POST A (TIME) stays in flight until the
    // test resolves it — no wall-clock sleeps anywhere.
    const timeGate: Array<(outcome: WitnessAskOutcome) => void> = [];
    const onAsk = vi.fn(
      async (questionType: WitnessQuestionType): Promise<WitnessAskOutcome> => {
        if (questionType === "TIME") {
          return new Promise<WitnessAskOutcome>((resolve) => {
            timeGate.push(resolve);
          });
        }
        return okOutcome(questionType, makeEmilyNeutralStatement());
      },
    );
    mount({ onAsk });

    // 1) Click A (TIME) — uncached, POST 1 fires and stays in flight.
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(onAsk).toHaveBeenCalledWith("TIME");
    expect(questionButton("TIME").getAttribute("aria-current")).toBe("true");

    // 2) Click B (SOUND) — UNCACHED — while POST A is still in flight. B must
    //    become the SELECTED/aria-current question immediately, but no second
    //    POST may start yet (one POST at a time).
    act(() => {
      questionButton("SOUND").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(questionButton("SOUND").getAttribute("aria-current")).toBe("true");
    expect(questionButton("TIME").getAttribute("aria-current")).not.toBe("true");

    // 3) Resolve A. WITHOUT any further click, B must now be asked
    //    automatically (the POST slot freed) and B's answer must render.
    act(() => {
      expect(timeGate).toHaveLength(1);
      timeGate[0]?.(okOutcome("TIME", makeEmilyTimeStatement()));
    });
    await flushAsync();

    expect(onAsk).toHaveBeenCalledTimes(2);
    expect(onAsk.mock.calls.map((call) => call[0])).toEqual(["TIME", "SOUND"]);
    expect(
      container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
    ).toBe(WITNESS_QUESTION_LABELS.SOUND);
    expect(
      container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
    ).toBe("No. Nothing stood out to me.");
    // Exactly ONE witness-answer region — never A and B side by side.
    expect(container.querySelectorAll('[data-testid="witness-answer"]')).toHaveLength(1);
    // A's late content never appeared anywhere (the outcome was for an
    // abandoned selection at resolve time).
    expect(container.textContent).not.toContain("heavy impact");
  });

  it("RAPID-switch chain A→B→C (each clicked while the previous POST is in flight): every question is asked IN SEQUENCE, exactly one POST at a time, only the LATEST selection's answer is visible at the end", async () => {
    // Deterministic deferred gates for A (TIME) and B (SOUND); C
    // (OBSERVATION) answers immediately with a DISTINCT statement.
    const gates = new Map<WitnessQuestionType, Array<(outcome: WitnessAskOutcome) => void>>([
      ["TIME", []],
      ["SOUND", []],
    ]);
    const onAsk = vi.fn(
      async (questionType: WitnessQuestionType): Promise<WitnessAskOutcome> => {
        const gate = gates.get(questionType);
        if (gate !== undefined) {
          return new Promise<WitnessAskOutcome>((resolve) => {
            gate.push(resolve);
          });
        }
        return okOutcome(questionType, {
          summary: "The third selection's answer: from the east window.",
          observations: [],
        });
      },
    );
    mount({ onAsk });

    // Click A — POST 1 in flight.
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(onAsk).toHaveBeenCalledTimes(1);

    // Click B while A is in flight — B's POST must NOT start yet (one slot).
    act(() => {
      questionButton("SOUND").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(questionButton("SOUND").getAttribute("aria-current")).toBe("true");

    // Click C while A is STILL in flight — C's POST must NOT start yet either.
    act(() => {
      questionButton("OBSERVATION").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(questionButton("OBSERVATION").getAttribute("aria-current")).toBe("true");

    // Resolve A: B's ask fires automatically (the slot freed); A's late
    // outcome is dropped (selection moved on).
    act(() => {
      gates.get("TIME")![0]?.(okOutcome("TIME", makeEmilyTimeStatement()));
    });
    await flushAsync();
    expect(onAsk).toHaveBeenCalledTimes(2);
    expect(onAsk.mock.calls.map((call) => call[0])).toEqual(["TIME", "SOUND"]);
    expect(container.textContent).not.toContain("heavy impact");

    // Resolve B: C's ask fires automatically; B's outcome is also dropped.
    act(() => {
      gates.get("SOUND")![0]?.(okOutcome("SOUND", makeEmilyNeutralStatement()));
    });
    await flushAsync();
    expect(onAsk).toHaveBeenCalledTimes(3);
    expect(onAsk.mock.calls.map((call) => call[0])).toEqual(["TIME", "SOUND", "OBSERVATION"]);

    // Only the LATEST selection (C) is visible at the end — ONE answer region.
    expect(container.querySelectorAll('[data-testid="witness-answer"]')).toHaveLength(1);
    expect(
      container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
    ).toBe(WITNESS_QUESTION_LABELS.OBSERVATION);
    expect(
      container.querySelector('[data-testid="witness-statement-summary"]')?.textContent,
    ).toBe("The third selection's answer: from the east window.");
  });

  it("switching witnesses RESETS the selection — no stale answer carries into another witness", async () => {
    const { onAsk } = mount();
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();
    expect(
      container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
    ).toBe("When were you there?");
    expect(container.textContent).toContain("heavy impact");

    // The route may re-render the SAME panel instance with another witness.
    act(() => {
      root?.render(
        <WitnessPanel
          witness={LISA}
          askedQuestions={[]}
          statementCache={new Map()}
          onAsk={onAsk}
          onClose={() => {}}
        />,
      );
    });
    await flushAsync();

    // New witness, clean answer state: neutral hint, no Emily statement text,
    // no selection marker on ANY question.
    expect(container.querySelector('[data-testid="witness-panel-name"]')?.textContent).toBe(
      LISA_WITNESS_NAME,
    );
    expect(container.querySelector('[data-testid="witness-answer"]')).toBeNull();
    expect(container.querySelector('[data-testid="witness-answer-hint"]')).not.toBeNull();
    expect(container.textContent).not.toContain("heavy impact");
    for (const questionType of WITNESS_QUESTION_ORDER) {
      expect(questionButton(questionType).getAttribute("aria-current")).toBeNull();
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

  it("the statement view has NO 'Ask another question' control; the HEADER interview Close stays", async () => {
    mount();
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();
    expect(container.querySelector('[data-testid="witness-answer"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="witness-ask-another"]')).toBeNull();
    const close = container.querySelector<HTMLButtonElement>('[data-testid="witness-close"]');
    expect(close).not.toBeNull();
    expect(close?.getAttribute("aria-label")).toBe("Close interview with Emily Reed");
  });

  it("the selected question exposes aria-current='true' + a selected class; asked keeps aria-pressed", async () => {
    const statementCache = new Map<WitnessQuestionType, WitnessStatementDTO>([
      ["SOUND", makeEmilyNeutralStatement()],
    ]);
    const askedQuestions: WitnessQuestionType[] = ["SOUND"];
    mount({ statementCache, askedQuestions });

    // Asked semantics stay aria-pressed based.
    expect(questionButton("SOUND").getAttribute("aria-pressed")).toBe("true");

    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();

    expect(questionButton("TIME").getAttribute("aria-current")).toBe("true");
    expect(questionButton("TIME").classList.contains("witness-question-button--selected")).toBe(true);
    expect(questionButton("SOUND").getAttribute("aria-current")).not.toBe("true");
    expect(questionButton("SOUND").classList.contains("witness-question-button--selected")).toBe(false);
    // Selection and asked-state are independent: asked = aria-pressed, selected = aria-current.
    expect(questionButton("SOUND").getAttribute("aria-pressed")).toBe("true");
    expect(questionButton("TIME").getAttribute("aria-pressed")).toBe("false");
  });

  it("keyboard interaction works: the six question buttons are REAL focusable native buttons and activation reaches onAsk", async () => {
    const { onAsk } = mount();
    // Every question is a real native <button type="button"> — a browser Tab
    // order is guaranteed (tabIndex 0) and NO state ever disables them, so
    // keyboard-only players can always Tab to and activate each question.
    for (const questionType of WITNESS_QUESTION_ORDER) {
      const button = questionButton(questionType);
      expect(button.type).toBe("button");
      expect(button.tabIndex).toBe(0);
      expect(button.disabled).toBe(false);
    }
    // Focus moves onto a question button (Tab landing / programmatic focus).
    const button = questionButton("TIME");
    act(() => {
      button.focus();
    });
    expect(document.activeElement).toBe(button);

    // Enter/Space on a FOCUSED native button perform the browser's implicit
    // activation — a click event. (jsdom implements NO keyboard layout engine
    // and NO implicit button activation, so the onClick wiring IS the
    // activation path: assert it demands the SAME closed questionType.)
    act(() => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();
    expect(onAsk).toHaveBeenCalledWith("TIME");
    expect(
      container.querySelector('[data-testid="witness-answer-question"]')?.textContent,
    ).toBe(WITNESS_QUESTION_LABELS.TIME);
  });

  it("the answer renders in the panel's NORMAL FLOW — a sibling of the questions region, never an overlay", async () => {
    mount();
    act(() => {
      questionButton("TIME").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushAsync();

    const panel = container.querySelector('[data-testid="witness-panel"]');
    const questions = container.querySelector('[data-testid="witness-questions"]');
    const answerSection = container.querySelector('[data-testid="witness-answer-section"]');
    const answer = container.querySelector('[data-testid="witness-answer"]');
    expect(questions).not.toBeNull();
    expect(answerSection).not.toBeNull();
    expect(answer).not.toBeNull();
    expect(panel?.contains(answerSection)).toBe(true);
    expect(answerSection?.contains(answer)).toBe(true);
    // No overlay affordances on the answer (static class names — the shipped
    // CSS absolute/fixed prohibition is asserted in the contract block below).
    expect(answerSection?.getAttribute("class")).not.toMatch(/modal|overlay|dialog-card/);
    expect(answer?.getAttribute("class")).not.toMatch(/modal|overlay/);
    // The answer region has a meaningful heading for AT users.
    expect(container.querySelector('[data-testid="witness-answer-heading"]')?.textContent).toBe(
      "Answer",
    );
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

describe("WitnessPanel — CSS contract (Phase 26: questions always above the single answer, never an overlay)", () => {
  // jsdom has no layout engine, so the layout guarantees are pinned against
  // the SHIPPED stylesheet (same approach as the Phase 19J test): the panel is
  // viewport-bounded with a normal flex-column Questions → Answer flow, each
  // region scrolls inside the panel (never over the other), `.witness-answer`
  // is NEVER absolutely/fixed positioned, question buttons are a flex column
  // (can never overlap at ANY width) and statement text wraps (no horizontal
  // page overflow).
  const css = readFileSync(join(process.cwd(), "src", "index.css"), "utf8")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");

  function rule(selectorPattern: string): string {
    const source = selectorPattern.replace(/\s*\n\s*/g, "\\s*");
    const match = new RegExp(`${source}\\s*\\{([^}]*)\\}`).exec(css);
    if (!match) throw new Error(`missing shipped rule matching ${selectorPattern}`);
    return match[1];
  }

  it("the panel box is viewport-bounded at 360px with a normal vertical flex flow (no min-width, no horizontal overflow)", () => {
    const panel = rule(`\\.witness-panel`);
    expect(panel).toMatch(/width\s*:\s*min\(24rem,\s*calc\(100%\s*-\s*2rem\)\)/);
    expect(panel).toMatch(/max-height\s*:\s*calc\(100%\s*-\s*2rem\)/);
    expect(panel).toMatch(/display\s*:\s*flex/);
    expect(panel).toMatch(/flex-direction\s*:\s*column/);
    expect(panel).toMatch(/overflow\s*:\s*hidden/);
    expect(panel).not.toMatch(/min-width/);
  });

  it("questions and answer are flex-flow regions that scroll INSIDE the panel — the answer is NEVER absolutely/fixed positioned", () => {
    const questions = rule(`\\.witness-questions`);
    expect(questions).toMatch(/flex-shrink/);
    expect(questions).toMatch(/min-width\s*:\s*0/);
    expect(questions).toMatch(/overflow-y\s*:\s*auto/);

    const answerSection = rule(`\\.witness-answer-section`);
    expect(answerSection).toMatch(/flex\s*:/);
    expect(answerSection).toMatch(/min-height\s*:\s*0/);
    expect(answerSection).toMatch(/overflow-y\s*:\s*auto/);

    // The answer card itself must stay in normal flow — no position at all.
    const answer = rule(`\\.witness-answer`);
    expect(answer).not.toMatch(/position\s*:/);
    // And no shipped `.witness-answer` rule may ever introduce fixed/absolute.
    expect(css).not.toMatch(/\.witness-answer\s*\{[^}]*position\s*:\s*(absolute|fixed)/);
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
    const answer = rule(`\\.witness-answer`);
    expect(answer).toMatch(/overflow-wrap\s*:\s*anywhere/);
  });

  it("the hostile-name heading still wraps (overflow-wrap on the panel name)", () => {
    const name = rule(`\\.witness-panel-name`);
    expect(name).toMatch(/overflow-wrap\s*:\s*anywhere/);
  });
});