import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { AccusationCandidatesDTO } from "../api/types";
import { CANNED_REVEAL_CRIME_TIME, makeCandidates, makeHostileReveal, makePartialReveal, makeRevealResponse, makeWrongReveal } from "../scene/testFixtures";
import RevealScreen from "./RevealScreen";

/**
 * Reveal screen coverage (Phase 7 L/O) via react-dom/server (no DOM, no jsdom,
 * no network): solved/wrong/partial fixtures render truth + submitted answers +
 * per-dimension indicators + score + explanation + timeline, everything driven
 * by the DTO, and generated text stays inert.
 */

function markup(
  reveal: Parameters<typeof RevealScreen>[0]["reveal"],
  candidates: AccusationCandidatesDTO | null = makeCandidates(),
): string {
  return renderToStaticMarkup(<RevealScreen reveal={reveal} candidates={candidates} />);
}

describe("solved reveal rendering", () => {
  it("renders THE TRUTH, truth values, submitted answers, indicators, score and explanation", () => {
    const html = markup(makeRevealResponse());

    // Heading + overall + score
    expect(html).toContain("THE TRUTH");
    expect(html).toContain("CASE SOLVED");
    expect(html).toContain("Score:");
    expect(html).toContain("4 / 4");

    // Truth from the DTO
    expect(html).toContain('data-testid="reveal-truth-murderer"');
    expect(html).toContain("Ada Marsh");
    expect(html).toContain("A dispute over money");
    expect(html).toContain("Kitchen knife");
    expect(html).toContain("21:45");

    // Per-dimension indicators
    expect(html).toContain('data-testid="reveal-dimension-who"');
    expect(html).toContain('data-testid="reveal-dimension-why"');
    expect(html).toContain('data-testid="reveal-dimension-weapon"');
    expect(html).toContain('data-testid="reveal-dimension-when"');
    const correctCount = (html.match(/Correct/g) ?? []).length;
    expect(correctCount).toBe(4);
    expect(html).not.toContain("Incorrect");

    // Explanation evidence list
    expect(html).toContain('data-testid="reveal-explanation"');
    expect(html).toContain("A bank transfer");
    expect(html).toContain("The transferred amount matches");

    // Timeline sorted by time (scoped to the timeline section — the truth
    // section legitimately shows "21:45" earlier in the document).
    expect(html).toContain('data-testid="reveal-timeline"');
    const timelineSection = html.slice(html.indexOf('data-testid="reveal-timeline"'));
    const timelinePositions = ["21:38", "21:45", "22:03"].map((time) => timelineSection.indexOf(time));
    expect(timelinePositions).toEqual([...timelinePositions].sort((a, b) => a - b));
    expect(timelinePositions.every((position) => position >= 0)).toBe(true);
  });

  it("renders identical markup for the same DTO (deterministic restore)", () => {
    const reveal = makeRevealResponse();
    expect(markup(reveal)).toBe(markup({ ...reveal }));
  });

  it("submitted answers appear (via candidate resolution) next to the truth", () => {
    const html = markup(makeRevealResponse());
    // Correct case: the player's resolved name equals the truth name.
    expect(html).toContain("Ada Marsh");
    expect(html).toContain("Your accusation:");
  });
});

describe("Phase 19E — semantic weapon 'fork' reveal", () => {
  it("shows the semantic weaponName 'Fork' in the truth and never a proc.* render id", () => {
    const reveal = makeRevealResponse({
      truth: {
        murdererId: "suspect_alpha",
        murdererName: "Ada Marsh",
        motiveId: "motive_alpha",
        motiveLabel: "A dispute over money",
        weaponId: "fork",
        weaponName: "Fork",
        crimeTime: CANNED_REVEAL_CRIME_TIME,
      },
      player: {
        accusation: {
          murdererId: "suspect_alpha",
          motiveId: "motive_alpha",
          weaponId: "fork",
          crimeTime: "21:45:00",
        },
      },
    });
    const html = markup(reveal);
    expect(html).toContain("Fork");
    expect(html).not.toContain("proc.decor.");
    expect(html).not.toContain("f00d5a11e4b2c313");
    // The accusation resolution uses the SEMANTIC id against the candidate
    // universe (no render id anywhere in the reveal markup).
    expect(html).not.toContain("proc.");
  });
});

describe("wrong-answer reveal rendering", () => {
  it("renders the incorrect verdict, wrong submitted names and all-incorrect indicators", () => {
    const html = markup(makeWrongReveal());

    expect(html).toContain("CASE NOT SOLVED");
    expect(html).toContain("0 / 4");
    expect(html).toContain('data-testid="reveal-dimension-result"');
    const incorrectCount = (html.match(/Incorrect/g) ?? []).length;
    expect(incorrectCount).toBe(4);

    // Truth + wrong submitted answers both present.
    expect(html).toContain("Ada Marsh");
    expect(html).toContain("Blake Niven"); // the player's (wrong) suspect
    expect(html).toContain("18:20"); // the player's (wrong) time
    expect(html).toContain("21:45"); // the truthful time
  });
});

describe("partial reveal rendering", () => {
  it("renders a 2/4 score with mixed Correct/Incorrect indicators", () => {
    const html = markup(makePartialReveal());

    expect(html).toContain("CASE NOT SOLVED");
    expect(html).toContain("2 / 4");
    const correctCount = (html.match(/>Correct</g) ?? []).length;
    const incorrectCount = (html.match(/>Incorrect</g) ?? []).length;
    expect(correctCount).toBe(2);
    expect(incorrectCount).toBe(2);
  });
});

describe("reveal without candidates", () => {
  it("renders the player's submitted raw ids as text when candidates are unknown", () => {
    const html = markup(makeWrongReveal(), null);
    expect(html).toContain("suspect_beta");
    expect(html).toContain("weapon_beta");
    expect(html).toContain("Ada Marsh"); // truth is unaffected
  });
});

describe("inert generated reveal text", () => {
  it("renders hostile strings literally — no script/img/foreign HTML child elements", () => {
    const html = markup(makeHostileReveal(), null);

    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).toContain("ひらがな");
    expect(html).toContain("你好");
    expect(html).toContain("😀");
  });
});

describe("reveal markup surface", () => {
  it("exposes the reveal-screen test id and the truth/player/result sections", () => {
    const html = markup(makeRevealResponse());
    expect(html).toContain('data-testid="reveal-screen"');
    expect(html).toContain('data-testid="reveal-truth"');
    expect(html).toContain('data-testid="reveal-accusation"');
    expect(html).toContain('data-testid="reveal-overall"');
    expect(html).toContain('data-testid="reveal-score"');
  });
});

describe("Phase 18C proof board rendering (post-reveal)", () => {
  it("the proof board section is present post-reveal with all four cards (reqs 9,10)", () => {
    const html = markup(makeRevealResponse());

    expect(html).toContain('data-testid="reveal-proof-board"');
    for (const dimension of ["who", "why", "weapon", "when"]) {
      expect(html).toContain(`data-testid="proof-card-${dimension}"`);
      expect(html).toContain(`data-testid="proof-card-${dimension}-title"`);
    }
    // Server-dimensioned nodes render with their published titles/points.
    expect(html).toContain("Hallway camera");
    expect(html).toContain("places the visitor at the door");
    expect(html).toContain('data-testid="proof-node-when-0"');
  });

  it("the pre-reveal scene page is not the place for the proof board: it only exists on the reveal screen", () => {
    // The Only place this component renders is inside RevealScreen (post-reveal).
    const html = markup(makeRevealResponse());
    expect(html.indexOf('data-testid="reveal-proof-board"')).toBeGreaterThan(html.indexOf('data-testid="reveal-explanation"'));
  });

  it("fallback grouping still renders four cards when dimensions are absent (older server)", () => {
    const reveal = makeRevealResponse() as unknown as Record<string, unknown>;
    const explanation = reveal.explanation as Record<string, unknown>;
    delete explanation.dimensions;
    const html = markup(reveal as unknown as Parameters<typeof RevealScreen>[0]["reveal"]);
    expect(html).toContain('data-testid="reveal-proof-board"');
    for (const dimension of ["who", "why", "weapon", "when"]) {
      expect(html).toContain(`data-testid="proof-card-${dimension}"`);
    }
    // Every published flat item still appears somewhere on the board.
    expect(html).toContain("A bank transfer");
    expect(html).toContain("Weapon match");
  });

  it("the proof board contains no proc.* tokens and never disturbs scoring markup (reqs 13,15)", () => {
    const html = markup(makeRevealResponse());
    const boardSection = html.slice(html.indexOf('data-testid="reveal-proof-board"'));
    expect(boardSection).not.toContain("proc.");
    // Scoring display is untouched by the board's presence.
    expect(html).toContain('data-testid="reveal-score"');
    expect(html).toContain("4 / 4");
  });

  it("hostile proof text stays inert on the board (escaped, no script/img children)", () => {
    const html = markup(makeHostileReveal(), null);
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
  });
});