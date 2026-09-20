import { describe, expect, it } from "vitest";
import {
  EMPTY_EXAMPLE_PROMPT_STATE,
  EXAMPLE_PROMPT_IDS,
  EXAMPLE_PROMPTS,
  examplePromptFor,
  isKnownExample,
  noteExamplePromptEdit,
  selectExamplePrompt,
} from "./examplePrompts";

/**
 * Phase 17D Bugfix PART B — pure example-prompt module tests.
 *
 * These prove (byte-exactly, against independently written literals) the three
 * pinned example prompts, the frozen registry, the helper copy, the pure
 * active-state transitions (select / idempotent re-select / edit-clears) and
 * that NO internal implementation detail (proc.*, AssetSpec, Phase 13/17,
 * solver internals) appears in any player-facing string.
 */

const EASY_EXPECTED = [
  "Victim: Laura Stein",
  "Murderer: Daniel Roth",
  "Motive: financial gain",
  "Weapon: kitchen knife",
  "Time: 20:15",
  "Witness: Nina Weber",
  "Location: apartment",
].join("\n");

const MEDIUM_EXPECTED = [
  "Victim: Michael Hartmann",
  "Murderer: Elena Fischer",
  "Motive: blackmail over a hidden affair",
  "Weapon: antique brass letter opener",
  "Time: 21:18",
  "Witness: Daniel Weber",
  "Location: hotel suite",
].join("\n");

const HARD_EXPECTED = [
  "Victim: Dr. Anna Weiss",
  "Murderer: Paul Becker",
  "Motive: stolen research data",
  "Weapon: bronze ceremonial ice pick",
  "Time: 23:42",
  "Witness: Lisa König",
  "Location: office",
].join("\n");

describe("EXAMPLE_PROMPTS — pinned verbatim copy", () => {
  it("pins the EASY prompt byte-exact (Phase17DBugfix PART B)", () => {
    expect(EXAMPLE_PROMPTS.easy.prompt).toBe(EASY_EXPECTED);
    expect(examplePromptFor("easy")).toBe(EASY_EXPECTED);
  });

  it("pins the MEDIUM prompt byte-exact (Phase17DBugfix PART B)", () => {
    expect(EXAMPLE_PROMPTS.medium.prompt).toBe(MEDIUM_EXPECTED);
    expect(examplePromptFor("medium")).toBe(MEDIUM_EXPECTED);
  });

  it("pins the HARD prompt byte-exact (Phase17DBugfix PART B)", () => {
    expect(EXAMPLE_PROMPTS.hard.prompt).toBe(HARD_EXPECTED);
    expect(examplePromptFor("hard")).toBe(HARD_EXPECTED);
  });

  it("keeps the registry frozen (a mutation cannot corrupt the pinned copy)", () => {
    expect(Object.isFrozen(EXAMPLE_PROMPTS)).toBe(true);
    expect(Object.isFrozen(EXAMPLE_PROMPTS.easy)).toBe(true);
    expect(Object.isFrozen(EXAMPLE_PROMPTS.medium)).toBe(true);
    expect(Object.isFrozen(EXAMPLE_PROMPTS.hard)).toBe(true);
    expect(Object.isFrozen(EXAMPLE_PROMPT_IDS)).toBe(true);
  });

  it("exposes exactly the three ids in the offering order", () => {
    expect(EXAMPLE_PROMPT_IDS).toEqual(["easy", "medium", "hard"]);
  });

  it("labels and helpers match the Phase17DBugfix PART B copy verbatim", () => {
    expect(EXAMPLE_PROMPTS.easy.label).toBe("Easy");
    expect(EXAMPLE_PROMPTS.easy.helper).toBe("Known environment and common objects.");
    expect(EXAMPLE_PROMPTS.medium.label).toBe("Medium");
    expect(EXAMPLE_PROMPTS.medium.helper).toBe("More varied setting and evidence.");
    expect(EXAMPLE_PROMPTS.hard.label).toBe("Hard");
    expect(EXAMPLE_PROMPTS.hard.helper).toBe(
      "Includes an unusual object that may require procedural 3D generation.",
    );
  });

  it("every prompt stays within the textarea length bound", () => {
    for (const id of EXAMPLE_PROMPT_IDS) {
      expect(EXAMPLE_PROMPTS[id].prompt.length).toBeGreaterThan(0);
      expect(EXAMPLE_PROMPTS[id].prompt.trim().length).toBeGreaterThan(0);
      expect(EXAMPLE_PROMPTS[id].prompt.length).toBeLessThanOrEqual(4000);
    }
  });
});

describe("EXAMPLE_PROMPTS — no internal implementation detail is exposed", () => {
  /** Every player-facing string the module carries (label + helper + prompt). */
  const allPlayerFacing = (): string[] => {
    const out: string[] = [];
    for (const id of EXAMPLE_PROMPT_IDS) {
      out.push(EXAMPLE_PROMPTS[id].label);
      out.push(EXAMPLE_PROMPTS[id].helper);
      out.push(EXAMPLE_PROMPTS[id].prompt);
    }
    return out;
  };

  it("contains no proc.* asset-id prefix anywhere", () => {
    for (const copy of allPlayerFacing()) {
      expect(copy).not.toContain("proc.");
      expect(copy.toLowerCase()).not.toContain("proc.");
    }
  });

  it("contains no AssetSpec / asset-spec reference", () => {
    for (const copy of allPlayerFacing()) {
      expect(copy.toLowerCase()).not.toContain("assetspec");
      expect(copy.toLowerCase()).not.toContain("asset spec");
    }
  });

  it("contains no Phase 13 / Phase 17 pipeline references", () => {
    for (const copy of allPlayerFacing()) {
      expect(copy.toLowerCase()).not.toContain("phase 13");
      expect(copy.toLowerCase()).not.toContain("phase 17");
    }
  });

  it("contains no solver-internal references", () => {
    for (const copy of allPlayerFacing()) {
      expect(copy.toLowerCase()).not.toContain("solver");
    }
  });
});

describe("examplePromptFor / isKnownExample — pure lookup", () => {
  it("returns the exact prompt for each frozen id", () => {
    expect(examplePromptFor("easy")).toBe(EASY_EXPECTED);
    expect(examplePromptFor("medium")).toBe(MEDIUM_EXPECTED);
    expect(examplePromptFor("hard")).toBe(HARD_EXPECTED);
  });

  it("returns null for unknown / non-string ids", () => {
    expect(examplePromptFor("")).toBeNull();
    expect(examplePromptFor("Easy")).toBeNull(); // case-sensitive ids only
    expect(examplePromptFor("expert")).toBeNull();
    expect(examplePromptFor("proc.decor.4551660f4a46b2eb")).toBeNull();
    expect(examplePromptFor(null)).toBeNull();
    expect(examplePromptFor(undefined)).toBeNull();
    expect(examplePromptFor(7)).toBeNull();
    expect(examplePromptFor({})).toBeNull();
  });

  it("isKnownExample is true ONLY for the three frozen ids", () => {
    expect(isKnownExample("easy")).toBe(true);
    expect(isKnownExample("medium")).toBe(true);
    expect(isKnownExample("hard")).toBe(true);
    expect(isKnownExample("")).toBe(false);
    expect(isKnownExample("Easy")).toBe(false);
    expect(isKnownExample("hard ")).toBe(false);
    expect(isKnownExample(null)).toBe(false);
    expect(isKnownExample(undefined)).toBe(false);
    expect(isKnownExample(0)).toBe(false);
  });
});

describe("Example prompt state transitions (deterministic, pure)", () => {
  const empty = EMPTY_EXAMPLE_PROMPT_STATE;

  it("starts empty with no active example", () => {
    expect(empty).toEqual({ prompt: "", activeExampleId: null });
    expect(Object.isFrozen(empty)).toBe(true);
  });

  it("selecting an example replaces the prompt byte-exactly and marks it active", () => {
    const next = selectExamplePrompt(empty, "easy");
    expect(next.prompt).toBe(EASY_EXPECTED);
    expect(next.activeExampleId).toBe("easy");
  });

  it("re-selecting the SAME example is idempotent (same state object back)", () => {
    const once = selectExamplePrompt(empty, "medium");
    const twice = selectExamplePrompt(once, "medium");
    expect(twice).toBe(once);
  });

  it("selecting a different example replaces the prompt AND moves the active id", () => {
    const easy = selectExamplePrompt(empty, "easy");
    const hard = selectExamplePrompt(easy, "hard");
    expect(hard.prompt).toBe(HARD_EXPECTED);
    expect(hard.activeExampleId).toBe("hard");
    expect(hard.prompt).not.toBe(easy.prompt);
  });

  it("activating an example after manually matching its text still activates it", () => {
    // The user typed the hard prompt by hand (active null); clicking Hard
    // must mark it active, not no-op.
    const typed = { prompt: HARD_EXPECTED, activeExampleId: null as null };
    const clicked = selectExamplePrompt(typed, "hard");
    expect(clicked.activeExampleId).toBe("hard");
    expect(clicked.prompt).toBe(HARD_EXPECTED);
  });

  it("an unknown id leaves the state untouched", () => {
    const easy = selectExamplePrompt(empty, "easy");
    expect(selectExamplePrompt(easy, "nope")).toBe(easy);
    expect(selectExamplePrompt(empty, "proc.decor.x")).toBe(empty);
  });

  it("editing keeps the text and clears the active mark on the first divergence", () => {
    const easy = selectExamplePrompt(empty, "easy");
    const edited = noteExamplePromptEdit(easy, EASY_EXPECTED + "\nClue: diary");
    expect(edited.prompt).toBe(EASY_EXPECTED + "\nClue: diary");
    expect(edited.activeExampleId).toBeNull();
  });

  it("an edit that leaves the example text unchanged keeps it active", () => {
    const easy = selectExamplePrompt(empty, "easy");
    const same = noteExamplePromptEdit(easy, EASY_EXPECTED);
    expect(same.activeExampleId).toBe("easy");
    expect(same.prompt).toBe(EASY_EXPECTED);
  });

  it("manual typing (no example loaded) never activates an example", () => {
    const typedHard = noteExamplePromptEdit(empty, HARD_EXPECTED);
    expect(typedHard.activeExampleId).toBeNull();
    expect(typedHard.prompt).toBe(HARD_EXPECTED);
  });

  it("editing toward a DIFFERENT example still clears the active mark", () => {
    const easy = selectExamplePrompt(empty, "easy");
    const pastedHard = noteExamplePromptEdit(easy, HARD_EXPECTED);
    expect(pastedHard.activeExampleId).toBeNull();
    expect(pastedHard.prompt).toBe(HARD_EXPECTED);
  });
});