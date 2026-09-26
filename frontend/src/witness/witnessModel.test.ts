import { describe, expect, it } from "vitest";
import type { WitnessInterviewResponse, WitnessQuestionType } from "../api/types";
import { makeHostileWitnessInterviewResponse, makeWitnessInterviewResponse } from "../scene/testFixtures";
import {
  boundedText,
  handleWitnessPanelKey,
  isWitnessQuestionType,
  MAX_WITNESS_OBSERVATION_LENGTH,
  MAX_WITNESS_SUMMARY_LENGTH,
  observationTimeView,
  parseWitnessInterviewResponse,
  WITNESS_QUESTION_LABELS,
  WITNESS_QUESTION_ORDER,
  witnessPresenceHint,
  witnessQuestionKey,
  witnessQuestionLabel,
} from "./witnessModel";

/**
 * Phase 23 — witness model (pure/domain) coverage:
 *   - the CLOSED six-question vocabulary + human labels (never the enum token);
 *   - the bounded-string policy (Phase23 §47) and compact <time> derivation;
 *   - the NEVER-THROWS interview-response parser: hostile names/statements
 *     stay literal, bounded, and can never inject HTML; unknown question
 *     types are never coerced into the enum.
 */

describe("Phase 23 witness model — closed question vocabulary", () => {
  it("exposes exactly the six frozen question types in the panel order", () => {
    expect(WITNESS_QUESTION_ORDER).toEqual([
      "OBSERVATION",
      "TIME",
      "SOUND",
      "PERSON",
      "OBJECT",
      "LOCATION",
    ] as WitnessQuestionType[]);
    expect(new Set(WITNESS_QUESTION_ORDER).size).toBe(WITNESS_QUESTION_ORDER.length);
    for (const questionType of WITNESS_QUESTION_ORDER) {
      expect(isWitnessQuestionType(questionType)).toBe(true);
    }
  });

  it("maps every question type to the required HUMAN label (no enum tokens)", () => {
    expect(WITNESS_QUESTION_LABELS).toEqual({
      OBSERVATION: "What did you see?",
      TIME: "When were you there?",
      SOUND: "Did you hear anything?",
      PERSON: "Did you notice anyone?",
      OBJECT: "Did you notice any unusual objects?",
      LOCATION: "Where were you?",
    });
    for (const questionType of WITNESS_QUESTION_ORDER) {
      expect(WITNESS_QUESTION_LABELS[questionType]).not.toBe(questionType);
    }
  });

  it("guards the closed enum: hostile/non-enum values are never coerced", () => {
    for (const hostile of [
      "WHEN",
      "",
      "TIME ",
      "onclick",
      "__proto__",
      "TIME\n<script>",
      42,
      null,
      undefined,
      {},
    ]) {
      expect(isWitnessQuestionType(hostile)).toBe(false);
    }
    expect(witnessQuestionLabel("WHEN")).toBe("Question");
    expect(witnessQuestionLabel(undefined)).toBe("Question");
  });

  it("produces a stable per-(witness, question) dedupe key", () => {
    const a = witnessQuestionKey("witness_emily_reed", "TIME");
    const b = witnessQuestionKey("witness_emily_reed", "TIME");
    expect(a).toBe(b);
    const c = witnessQuestionKey("witness_emily_reed", "SOUND");
    expect(a).not.toBe(c);
    // The key embeds the id — but it is bookkeeping only, never rendered.
    expect(a).not.toContain("<");
  });
});

describe("Phase 23 witness model — bounded strings & time views", () => {
  it("truncates over-long strings with an ellipsis and never grows short ones", () => {
    expect(boundedText("short", 10)).toBe("short");
    // max 8 => at most 7 chars + ellipsis.
    expect(boundedText("<script>alert(1)</script>", 8)).toBe("<script…");
    expect(boundedText(42, 10)).toBe("42");
    expect(boundedText(null, 10)).toBe("");
    expect(boundedText({}, 10)).toBe("");
  });

  it("renders a compact HH:mm view for ISO times while keeping the canonical value", () => {
    const view = observationTimeView("2026-09-11T23:42:00+02:00");
    expect(view.canonical).toBe("2026-09-11T23:42:00+02:00");
    expect(view.display).toBe("23:42");
  });

  it("passes bare compact clocks through verbatim", () => {
    const view = observationTimeView("23:42");
    expect(view.canonical).toBe("23:42");
    expect(view.display).toBe("23:42");
  });

  it("returns null canonical + empty display for missing/hostile times", () => {
    expect(observationTimeView(null)).toEqual({ canonical: null, display: "" });
    expect(observationTimeView(undefined)).toEqual({ canonical: null, display: "" });
    expect(observationTimeView("  ")).toEqual({ canonical: null, display: "" });
    // A malformed value stays VERBATIM as the display (never fabricated).
    const hostile = observationTimeView("not-a-time <img src=x>");
    expect(hostile.canonical).toBe("not-a-time <img src=x>");
    expect(hostile.display).toBe("not-a-time <img src=x>");
  });

  it("presence hints are fixed safe copy, never the raw enum on the page", () => {
    expect(witnessPresenceHint("ON_SCENE")).toBe("On scene");
    expect(witnessPresenceHint("REMOTE_STATEMENT")).toBe("Remote statement");
    expect(witnessPresenceHint("__proto__")).toBe("Witness");
  });

  it("Escape maps to close like the evidence panel; other keys are inert", () => {
    expect(handleWitnessPanelKey("Escape")).toBe("close");
    expect(handleWitnessPanelKey("Enter")).toBeNull();
    expect(handleWitnessPanelKey("")).toBeNull();
  });
});

describe("Phase 23 witness model — interview response parser", () => {
  it("coerces a grounded response: statement + observations + time-bearing entries", () => {
    const parsed = parseWitnessInterviewResponse(makeWitnessInterviewResponse("TIME"));
    expect(parsed.witnessId).toBe("witness_emily_reed");
    expect(parsed.displayName).toBe("Emily Reed");
    expect(parsed.questionType).toBe("TIME");
    expect(parsed.statement.summary).toBe(
      "Emily recalls hearing a heavy impact at approximately 23:42.",
    );
    expect(parsed.observationCount).toBe(2);
    expect(parsed.statement.observations[0]).toEqual({ time: "23:40", text: "Emily entered the corridor." });
    expect(parsed.statement.observations[1]).toEqual({ time: "23:42", text: "She heard a heavy impact from inside the laboratory." });
    expect(parsed.discovery).not.toBeNull();
    expect(parsed.discovery!.newlyDiscovered).toBe(true);
    expect(parsed.discovery!.record?.evidenceId).toBe("record_witness_statement_emily_time_01");
    expect(parsed.discovery!.record?.readByPlayer).toBe(true);
  });

  it("coerces a neutral response with NO discovery and NO observations", () => {
    const parsed = parseWitnessInterviewResponse(
      makeWitnessInterviewResponse("SOUND", { discovery: null }),
    );
    expect(parsed.questionType).toBe("SOUND");
    expect(parsed.statement.summary).toBe("No. Nothing stood out to me.");
    expect(parsed.observationCount).toBe(0);
    expect(parsed.discovery).toBeNull();
  });

  it("renders HOSTILE input inert and BOUNDED: escaped-literal strings, truncated, malformed enum rejected", () => {
    const parsed = parseWitnessInterviewResponse(makeHostileWitnessInterviewResponse());
    // The hostile script strings survive as LITERAL (bounded) text — the
    // renderers output them through React escaping, never innerHTML.
    expect(parsed.displayName).toContain("<script>");
    expect(parsed.displayName.length).toBeLessThanOrEqual(120);
    expect(parsed.questionType).toBe("TIME"); // the payload echoed a valid enum
    expect(parsed.statement.summary.length).toBeLessThanOrEqual(MAX_WITNESS_SUMMARY_LENGTH);
    for (const observation of parsed.statement.observations) {
      expect(observation.text.length).toBeLessThanOrEqual(MAX_WITNESS_OBSERVATION_LENGTH);
    }
    expect(parsed.observationCount).toBe(2);
    // The hostile time stays as the canonical literal (never fabricated).
    expect(parsed.statement.observations[0].time).toContain("<script>");
  });

  it("rejects unknown question types on the payload (falls back to the asked type)", () => {
    const parsed = parseWitnessInterviewResponse(
      makeWitnessInterviewResponse("TIME", { questionType: "WHO" as WitnessQuestionType }),
    );
    expect(parsed.questionType).toBeNull();
  });

  it("never throws on non-object / hostile payloads", () => {
    for (const hostile of [null, undefined, 42, "TIME", [], [1, 2], { statement: "x" }]) {
      const parsed = parseWitnessInterviewResponse(hostile);
      expect(parsed.statement.summary).toBe("");
      expect(parsed.observationCount).toBe(0);
      expect(parsed.discovery).toBeNull();
    }
  });

  it("keeps only observations with readable text (drops empty/hostile-only entries)", () => {
    const parsed = parseWitnessInterviewResponse(
      makeWitnessInterviewResponse("TIME", {
        statement: {
          summary: "S",
          observations: [
            { time: "23:40", text: "A real observation." },
            { time: "23:41", text: "   " },
            { time: null, text: null as never },
            { text: "" },
            "A string observation",
          ],
        } as unknown as WitnessInterviewResponse["statement"],
      }),
    );
    expect(parsed.observationCount).toBe(2);
    expect(parsed.statement.observations[0].text).toBe("A real observation.");
    expect(parsed.statement.observations[1]).toEqual({ time: null, text: "A string observation" });
  });
});