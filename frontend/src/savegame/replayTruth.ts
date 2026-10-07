import type {
  RevealExplanationDimensionsDTO,
  RevealResponse,
  SubmittedAccusationDTO,
} from "../api/types";
import { acceptedScoringContains, parseIso8601, submittedTick } from "./savegameTime";
import type { ReplayTruthV1, SavegameEvidenceRecordV1 } from "./savegameV1";
import { REPLAY_CASE_ID, REPLAY_PLAYTHROUGH_ID } from "./savegameV1";

/**
 * Phase 32 — the LOCAL replay solution evaluator + reveal DTO builder.
 *
 * This is a faithful port of the SMALL PURE evaluation rules the server uses
 * (backend `app/services/accusation.py::evaluate_accusation` /
 * `submitted_tick` / `app/domain/time_interval.py::accepted_scoring_time_set`
 * and the allowlist shapes of `app/services/reveal.py::reveal_dto_of` /
 * `timeline_of`). The EVALUATION RULES of a saved replay are the SAME rules
 * as a live case — only the TRUTH SOURCE differs: ReplayTruthV1 is UNTRUSTED
 * replay-scoped data, never canonical server truth (Phase32 §13/§18).
 *
 * Honest proof-boundary: the live reveal's explanation board derives from
 * the pinned payload's `solverProof` section, which is deliberately NEVER
 * exported into the `.pdcase` (ADR-003 §5). A replayed reveal therefore
 * carries the same truth/result/score/timeline semantics, but its
 * explanation blocks are EMPTY (the RevealScreen renders the documented "No
 * explanation evidence was published" fallback). No proof material is
 * invented on the client.
 *
 * - `timelineOf(records)` — deterministic derive-from-public-evidence
 *   timeline identical in spirit to `reveal.timeline_of`: public timestamps
 *   carried by the exported read records + their public titles, sorted by
 *   time (ties by title/isa), deduplicated, capped at 12.
 * - `evaluateReplayAccusation` — the four frozen booleans using the exact
 *   half-open `[T−N, T+N+1)` accepted scoring set and DEC-003 anchoring.
 * - `buildReplayReveal` — the frozen `RevealResponse` allowlist shape.
 */

export interface ReplayAccusationEvaluation {
  murdererCorrect: boolean;
  motiveCorrect: boolean;
  weaponCorrect: boolean;
  timeCorrect: boolean;
}

export const REPLAY_CASE_VERSION = 1;

/**
 * The four frozen per-dimension booleans (Phase7 D). `RReplayTruthV1` is the
 * UNTRUSTED saved truth; the accusation is the player's immutable submission.
 * The time comparison uses the EXACT server scoring window
 * `[T−N, T+N+1)` (REQUIREMENTS 31.7 / DEC-003).
 */
export function evaluateReplayAccusation(
  truth: ReplayTruthV1,
  accusation: SubmittedAccusationDTO,
): ReplayAccusationEvaluation {
  let canonicalTick = 0;
  let tolerance = 0;
  try {
    canonicalTick = parseIso8601(truth.crimeTime).epochSeconds;
    tolerance = Number(truth.accusationToleranceSeconds) || 0;
  } catch {
    // A schema-valid edited file may carry an unparseable canonical: the
    // replay never white-screens — timeCorrect becomes false (never correct).
    return {
      murdererCorrect: accusation.murdererId === truth.murdererId,
      motiveCorrect: accusation.motiveId === truth.motiveId,
      weaponCorrect: accusation.weaponId === truth.weaponId,
      timeCorrect: false,
    };
  }

  let tickCorrect = false;
  try {
    const tick = submittedTick(accusation.crimeTime, truth.crimeTime);
    tickCorrect = acceptedScoringContains(canonicalTick, tolerance, tick);
  } catch {
    tickCorrect = false; // an unparseable submitted time is never correct
  }

  return {
    murdererCorrect: accusation.murdererId === truth.murdererId,
    motiveCorrect: accusation.motiveId === truth.motiveId,
    weaponCorrect: accusation.weaponId === truth.weaponId,
    timeCorrect: tickCorrect,
  };
}

/** The player-safe timeline: public timestamps of the saved evidence records
 *  + record titles, deterministic, capped at 12 (mirror of reveal.timeline_of). */
export function timelineOf(records: readonly SavegameEvidenceRecordV1[]): { time: string; description: string }[] {
  const MAX_TIMELINE = 12;
  interface Entry {
    epoch: number;
    time: string;
    description: string;
  }
  const entries: Entry[] = [];
  for (const record of records) {
    const content = record.content;
    const description = record.title;
    const seen = new Set<string>();
    const pushTime = (value: string | null | undefined): void => {
      if (typeof value !== "string" || value === "" || seen.has(value)) return;
      seen.add(value);
      let epoch: number;
      try {
        epoch = parseIso8601(value).epochSeconds;
      } catch {
        return; // only exact ISO-8601-with-offset timestamps are used
      }
      entries.push({ epoch, time: value, description });
    };
    // The export carries the public render payload: TIMELINE/ACTIVITY_LOG
    // entries and raw event times carry the observed timestamps.
    for (const entry of content.entries ?? []) pushTime(entry.time);
    for (const event of content.events ?? []) pushTime(event.time);
  }
  entries.sort((a, b) =>
    a.epoch !== b.epoch ? a.epoch - b.epoch : a.description < b.description ? -1 : a.description > b.description ? 1 : a.time < b.time ? -1 : a.time > b.time ? 1 : 0,
  );
  const unique: Entry[] = [];
  const seenPairs = new Set<string>();
  for (const entry of entries) {
    const key = `${entry.epoch}\u0000${entry.description}`;
    if (seenPairs.has(key)) continue;
    seenPairs.add(key);
    unique.push(entry);
    if (unique.length >= MAX_TIMELINE) break;
  }
  return unique.map((entry) => ({ time: entry.time, description: entry.description }));
}

const EMPTY_DIMENSIONS: RevealExplanationDimensionsDTO = {
  who: [],
  why: [],
  weapon: [],
  when: [],
};

/**
 * Build the frozen `RevealResponse` allowlist shape of a replayed reveal.
 * Every truth/result/score/timeline value is derived ONLY from the saved
 * ReplayTruthV1 + the player's immutable accusation + the saved public
 * evidence records — never from any hidden/server material.
 */
export function buildReplayReveal(
  truth: ReplayTruthV1,
  accusation: SubmittedAccusationDTO,
  records: readonly SavegameEvidenceRecordV1[],
): RevealResponse {
  const evaluation = evaluateReplayAccusation(truth, accusation);
  const booleans = [
    evaluation.murdererCorrect,
    evaluation.motiveCorrect,
    evaluation.weaponCorrect,
    evaluation.timeCorrect,
  ];
  const overall: "solved" | "incorrect" = booleans.every(Boolean) ? "solved" : "incorrect";
  const correct = booleans.filter(Boolean).length;

  return {
    playthroughId: REPLAY_PLAYTHROUGH_ID,
    caseId: REPLAY_CASE_ID,
    caseVersion: REPLAY_CASE_VERSION,
    status: "REVEALED",
    truth: {
      murdererId: truth.murdererId,
      murdererName: truth.murdererName,
      motiveId: truth.motiveId,
      motiveLabel: truth.motiveLabel,
      weaponId: truth.weaponId,
      weaponName: truth.weaponName,
      crimeTime: truth.crimeTime,
    },
    player: { accusation },
    result: {
      murdererCorrect: evaluation.murdererCorrect,
      motiveCorrect: evaluation.motiveCorrect,
      weaponCorrect: evaluation.weaponCorrect,
      timeCorrect: evaluation.timeCorrect,
      overall,
    },
    score: { correctDimensions: correct, totalDimensions: 4 },
    timeline: timelineOf(records),
    explanation: {
      evidence: [],
      dimensions: EMPTY_DIMENSIONS,
    },
  };
}