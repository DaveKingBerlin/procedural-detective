import type { AccusationCandidatesDTO, ResolutionType, RevealResponse, TimelineEntryDTO } from "../api/types";
import { buildProofBoard, type ProofBoardModel } from "./proofBoardModel";

/**
 * Pure presentation model for the reveal screen (Phase 7 L + Phase 41).
 *
 * The RevealScreen renders ONLY this inert, plain-text model with React's
 * default string rendering — no dangerouslySetInnerHTML, no generated HTML,
 * no eval. Every DTO-driven value funnels through `asText`, so hostile or
 * HTML-looking strings stay literal text.
 *
 * All derivation is deterministic:
 *   - crime times collapse to a display "HH:MM" taken from the DTO itself
 *     (the authoritative local time-of-day) — never via `new Date()`/timezones;
 *   - timeline entries are sorted by time;
 *   - the player's submitted IDs are resolved to names through the player-safe
 *     candidates from the bootstrap when available (the reveal DTO itself only
 *     echoes IDs); unknown ids fall back to the raw id as plain text.
 *
 * Phase 41 — resolution-aware rendering:
 *   - the truth DTO's `resolutionType` is normalized ("HOMICIDE" | "SUICIDE");
 *   - for a SUICIDE truth the WHO row shows the self-inflicted outcome (no
 *     murderer), the player's WHO value shows either the suicide label (a
 *     SUICIDE accusation) or their chosen homicide person (a wrong theory);
 *   - for a HOMICIDE truth the existing murderer rendering stays unchanged.
 */

/** Coerce any DTO value to a plain string; hostile values stay literal. */
export function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

/**
 * Collapse an ISO timestamp (or bare "HH:MM:SS") to the display "HH:MM".
 * The ISO's own time-of-day component is authoritative — no Date/timezone
 * conversion, fully deterministic. Returns "" for unparseable input.
 */
export function formatCrimeTime(value: string): string {
  const match = /(\d{2}):(\d{2})(?::\d{2})?/.exec(value);
  if (!match) return "";
  return `${match[1]}:${match[2]}`;
}

/** Timeline sorted ascending by time (ISO strings parse deterministically). */
export function sortTimeline(entries: TimelineEntryDTO[]): TimelineEntryDTO[] {
  return [...entries].sort((a, b) => {
    const ta = Date.parse(a.time);
    const tb = Date.parse(b.time);
    const na = Number.isNaN(ta) ? 0 : ta;
    const nb = Number.isNaN(tb) ? 0 : tb;
    return na - nb;
  });
}

export interface RevealDimensionRow {
  dimension: "WHO" | "WHY" | "WEAPON" | "WHEN";
  label: string;
  truth: string;
  submitted: string;
  correct: boolean;
}

export interface RevealExplanationItem {
  title: string;
  point: string;
}

export interface RevealTimelineItem {
  time: string; // display "HH:MM"
  description: string;
}

export interface RevealPresentation {
  truth: {
    /** Phase 41 — the authoritative resolution ("HOMICIDE" | "SUICIDE"). */
    resolutionType: ResolutionType;
    /** The resolution row label: "Resolution" for SUICIDE, "Murderer" for
     *  HOMICIDE — the screen never shows a "Murderer" label in a suicide case. */
    resolutionLabel: string;
    /** The resolution row value: "Self-inflicted / suicide" for SUICIDE, else
     *  the murderer name. */
    resolutionValue: string;
    motive: string;
    weapon: string;
    crimeTime: string;
  };
  playerSubmission: { murdererId: string; motiveId: string; weaponId: string; crimeTime: string };
  dimensions: RevealDimensionRow[];
  overall: "solved" | "incorrect";
  score: { correctDimensions: number; totalDimensions: number };
  explanation: RevealExplanationItem[];
  timeline: RevealTimelineItem[];
  /** Phase 18C: post-reveal proof board (server `dimensions` or fallback). */
  proofBoard: ProofBoardModel;
}

/** The self-inflicted resolution copy used ONLY after the truth is revealed.
 *  Investigative, non-graphic (Phase 41 §34). */
export const SELF_INFLICTED_TRUTH_LABEL = "Self-inflicted / suicide";

/**
 * Build the reveal presentation model from the validated DTO + (optional)
 * player-safe candidates. The candidates are used ONLY to resolve the
 * player's submitted IDs to readable names; the truth always comes from the
 * reveal DTO itself.
 */
export function revealPresentation(
  reveal: RevealResponse,
  candidates: AccusationCandidatesDTO | null,
): RevealPresentation {
  const accusation = reveal.player.accusation;
  const truth = reveal.truth;

  const resolutionType: ResolutionType = truth.resolutionType === "SUICIDE" ? "SUICIDE" : "HOMICIDE";
  const selfInflictedTruth = resolutionType === "SUICIDE";

  const submittedSuspect = candidates?.suspects.find((entry) => entry.id === accusation.murdererId)?.name ?? null;
  const submittedTarget =
    submittedSuspect ??
    (candidates?.finalTargets?.find((entry) => entry.id === accusation.murdererId)?.name ?? null);
  const submittedMotive = candidates?.motives.find((entry) => entry.id === accusation.motiveId)?.label ?? null;
  const submittedWeapon = candidates?.weapons.find((entry) => entry.id === accusation.weaponId)?.name ?? null;

  // Phase 41 — the player's WHO value: a SUICIDE accusation has murdererId=null
  // and is shown as the self-inflicted resolution (never "—", never a victim
  // duplicate). A homicide accusation resolves to the final target/person name.
  const playerChoseSuicide = accusation.murdererId === null;
  const whoSubmitted = playerChoseSuicide ? SELF_INFLICTED_TRUTH_LABEL : (submittedTarget ?? asText(accusation.murdererId));

  const dimensions: RevealDimensionRow[] = [
    {
      dimension: "WHO",
      label: selfInflictedTruth ? "WHO — the resolution" : "WHO — the murderer",
      truth: selfInflictedTruth ? SELF_INFLICTED_TRUTH_LABEL : asText(truth.murdererName),
      submitted: whoSubmitted,
      correct: reveal.result.murdererCorrect,
    },
    {
      dimension: "WHY",
      label: "WHY — the motive",
      truth: asText(truth.motiveLabel),
      submitted: submittedMotive ?? asText(accusation.motiveId),
      correct: reveal.result.motiveCorrect,
    },
    {
      dimension: "WEAPON",
      label: "WEAPON — the murder weapon",
      truth: asText(truth.weaponName),
      submitted: submittedWeapon ?? asText(accusation.weaponId),
      correct: reveal.result.weaponCorrect,
    },
    {
      dimension: "WHEN",
      label: "WHEN — the crime time",
      truth: formatCrimeTime(truth.crimeTime),
      submitted: formatCrimeTime(accusation.crimeTime),
      correct: reveal.result.timeCorrect,
    },
  ];

  return {
    truth: {
      resolutionType,
      resolutionLabel: selfInflictedTruth ? "Resolution" : "Murderer",
      resolutionValue: selfInflictedTruth ? SELF_INFLICTED_TRUTH_LABEL : asText(truth.murdererName),
      motive: asText(truth.motiveLabel),
      weapon: asText(truth.weaponName),
      crimeTime: formatCrimeTime(truth.crimeTime),
    },
    playerSubmission: {
      murdererId: accusation.murdererId === null ? "" : asText(accusation.murdererId),
      motiveId: asText(accusation.motiveId),
      weaponId: asText(accusation.weaponId),
      crimeTime: asText(accusation.crimeTime),
    },
    dimensions,
    overall: reveal.result.overall,
    score: {
      correctDimensions: reveal.score.correctDimensions,
      totalDimensions: reveal.score.totalDimensions,
    },
    explanation: reveal.explanation.evidence.map((entry) => ({
      title: asText(entry.title),
      point: asText(entry.point),
    })),
    timeline: sortTimeline(reveal.timeline).map((entry) => ({
      time: formatCrimeTime(entry.time),
      description: asText(entry.description),
    })),
    proofBoard: buildProofBoard(reveal),
  };
}