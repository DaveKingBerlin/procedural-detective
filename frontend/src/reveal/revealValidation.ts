import type { ExplanationPointDTO, RevealExplanationDimensionsDTO, RevealResponse } from "../api/types";
import { ValidationError } from "../scene/validation";

/**
 * Trust-boundary parser for the frozen RevealResponse DTO.
 *
 * The reveal screen is ENTIRELY DTO-driven: it renders only what this parser
 * allowlists from the wire. Everything else — hidden proof/internal fields,
 * aliases, diagnostics, stray markers — is DROPPED and can never reach the
 * component or the bundle's runtime data. Malformed payloads throw
 * ValidationError (callers map that to the safe error state).
 */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function requireString(owner: Record<string, unknown>, field: string, where: string): string {
  const value = owner[field];
  if (!isNonEmptyString(value)) {
    throw new ValidationError(`${where}.${field} must be a non-empty string.`);
  }
  return value;
}

function requireBoolean(owner: Record<string, unknown>, field: string, where: string): boolean {
  const value = owner[field];
  if (typeof value !== "boolean") {
    throw new ValidationError(`${where}.${field} must be a boolean.`);
  }
  return value;
}

function requireNonNegativeInteger(owner: Record<string, unknown>, field: string, where: string): number {
  const value = owner[field];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ValidationError(`${where}.${field} must be a non-negative integer.`);
  }
  return value;
}

/** The accusation echo block shared by the reveal DTO (`player.accusation`). */
function parseAccusation(raw: unknown): { murdererId: string; motiveId: string; weaponId: string; crimeTime: string } {
  if (!isRecord(raw)) {
    throw new ValidationError("reveal.player.accusation must be an object.");
  }
  return {
    murdererId: requireString(raw, "murdererId", "reveal.player.accusation"),
    motiveId: requireString(raw, "motiveId", "reveal.player.accusation"),
    weaponId: requireString(raw, "weaponId", "reveal.player.accusation"),
    crimeTime: requireString(raw, "crimeTime", "reveal.player.accusation"),
  };
}

/* ======================================================================
 * Phase 18C — lenient parser for the OPTIONAL post-reveal `dimensions`
 * block (the proof-board grouping).
 *
 * This block is deliberately NOT allowed to fail the reveal: an older
 * server omits it entirely, and a malformed block must degrade to the
 * flat-list fallback grouping — never a ValidationError, never a crash.
 * Every entry is allowlisted to exactly {evidenceId, title, point};
 * anything else in a list is DROPPED (unknown fields can never survive
 * into the proof board or the bundle's runtime data).
 * ==================================================================== */

function parseOptionalPointList(value: unknown): ExplanationPointDTO[] | null {
  if (!Array.isArray(value)) return null;
  const points: ExplanationPointDTO[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) continue;
    const { evidenceId, title, point } = entry;
    if (typeof evidenceId === "string" && typeof title === "string" && typeof point === "string") {
      points.push({ evidenceId, title, point });
    }
  }
  return points;
}

function parseOptionalDimensions(raw: unknown): RevealExplanationDimensionsDTO | null {
  if (!isRecord(raw)) return null;
  const who = parseOptionalPointList(raw.who);
  const why = parseOptionalPointList(raw.why);
  const weapon = parseOptionalPointList(raw.weapon);
  const when = parseOptionalPointList(raw.when);
  if (who === null && why === null && weapon === null && when === null) return null;
  return { who: who ?? [], why: why ?? [], weapon: weapon ?? [], when: when ?? [] };
}

export function parseRevealResponse(raw: unknown): RevealResponse {
  if (!isRecord(raw)) {
    throw new ValidationError("Reveal response must be an object.");
  }
  if (raw.status !== "REVEALED") {
    throw new ValidationError('reveal.status must be "REVEALED".');
  }

  const playthroughId = requireString(raw, "playthroughId", "reveal");
  const caseId = requireString(raw, "caseId", "reveal");
  const caseVersion = requireNonNegativeInteger(raw, "caseVersion", "reveal");

  const truthRaw = raw.truth;
  if (!isRecord(truthRaw)) {
    throw new ValidationError("reveal.truth must be an object.");
  }

  const playerRaw = raw.player;
  if (!isRecord(playerRaw)) {
    throw new ValidationError("reveal.player must be an object.");
  }

  const resultRaw = raw.result;
  if (!isRecord(resultRaw)) {
    throw new ValidationError("reveal.result must be an object.");
  }
  const overallRaw = resultRaw.overall;
  if (overallRaw !== "solved" && overallRaw !== "incorrect") {
    throw new ValidationError('reveal.result.overall must be "solved" or "incorrect".');
  }

  const scoreRaw = raw.score;
  if (!isRecord(scoreRaw)) {
    throw new ValidationError("reveal.score must be an object.");
  }

  const timelineRaw = raw.timeline;
  if (!Array.isArray(timelineRaw)) {
    throw new ValidationError("reveal.timeline must be an array.");
  }

  const explanationRaw = raw.explanation;
  if (!isRecord(explanationRaw)) {
    throw new ValidationError("reveal.explanation must be an object.");
  }
  const evidenceRaw = explanationRaw.evidence;
  if (!Array.isArray(evidenceRaw)) {
    throw new ValidationError("reveal.explanation.evidence must be an array.");
  }
  // Phase 18C: the proof-board `dimensions` block is OPTIONAL — a malformed
  // or absent block is dropped (null), never fatal; the reveal screen falls
  // back to a heuristic grouping of the flat evidence list.
  const dimensions = parseOptionalDimensions(explanationRaw.dimensions);

  return {
    playthroughId,
    caseId,
    caseVersion,
    status: "REVEALED",
    truth: {
      murdererId: requireString(truthRaw, "murdererId", "reveal.truth"),
      murdererName: requireString(truthRaw, "murdererName", "reveal.truth"),
      motiveId: requireString(truthRaw, "motiveId", "reveal.truth"),
      motiveLabel: requireString(truthRaw, "motiveLabel", "reveal.truth"),
      weaponId: requireString(truthRaw, "weaponId", "reveal.truth"),
      weaponName: requireString(truthRaw, "weaponName", "reveal.truth"),
      crimeTime: requireString(truthRaw, "crimeTime", "reveal.truth"),
    },
    player: { accusation: parseAccusation(playerRaw.accusation) },
    result: {
      murdererCorrect: requireBoolean(resultRaw, "murdererCorrect", "reveal.result"),
      motiveCorrect: requireBoolean(resultRaw, "motiveCorrect", "reveal.result"),
      weaponCorrect: requireBoolean(resultRaw, "weaponCorrect", "reveal.result"),
      timeCorrect: requireBoolean(resultRaw, "timeCorrect", "reveal.result"),
      overall: overallRaw,
    },
    score: {
      correctDimensions: requireNonNegativeInteger(scoreRaw, "correctDimensions", "reveal.score"),
      totalDimensions: requireNonNegativeInteger(scoreRaw, "totalDimensions", "reveal.score"),
    },
    timeline: timelineRaw.map((entry, index) => {
      if (!isRecord(entry)) {
        throw new ValidationError(`reveal.timeline[${index}] must be an object.`);
      }
      return {
        time: requireString(entry, "time", `reveal.timeline[${index}]`),
        description: requireString(entry, "description", `reveal.timeline[${index}]`),
      };
    }),
    explanation: {
      evidence: evidenceRaw.map((entry, index) => {
        if (!isRecord(entry)) {
          throw new ValidationError(`reveal.explanation.evidence[${index}] must be an object.`);
        }
        return {
          evidenceId: requireString(entry, "evidenceId", `reveal.explanation.evidence[${index}]`),
          title: requireString(entry, "title", `reveal.explanation.evidence[${index}]`),
          point: requireString(entry, "point", `reveal.explanation.evidence[${index}]`),
        };
      }),
      ...(dimensions !== null ? { dimensions } : {}),
    },
  };
}