import type { AccusationCandidatesDTO, AccusationResponse } from "../api/types";
import { ValidationError } from "../scene/validation";

/**
 * Client-side accusation domain validation (Phase 7 K).
 *
 * Two jobs:
 *  1. parseAccusationResponse — trust-boundary parser for the 200 accusation
 *     body. The client never renders raw response bodies: only the typed,
 *     allowlisted fields survive here; unknown/extra fields (including any
 *     accidental truth or hidden markers) are DROPPED.
 *  2. validateAccusationForm — deterministic, client-side "missing/invalid
 *     field" checks run before the confirmation step and before submit. The
 *     IDs must belong to the server-published candidate universes, and the
 *     crime time must be a valid 24h "HH:MM" (the UI submits "HH:MM:00").
 */

export const CRIME_TIME_PATTERN = /^\d{2}:\d{2}$/;

export interface AccusationSelection {
  murdererId: string | null;
  motiveId: string | null;
  weaponId: string | null;
  /** Bare 24h time "HH:MM" as collected from the time input. */
  crimeTime: string | null;
}

export const EMPTY_SELECTION: AccusationSelection = {
  murdererId: null,
  motiveId: null,
  weaponId: null,
  crimeTime: null,
};

export type AccusationField = "murdererId" | "motiveId" | "weaponId" | "crimeTime";

export type AccusationFieldErrors = Partial<Record<AccusationField, string>>;

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

/**
 * Parse the 200 accusation response into the typed allowlist. Anything not in
 * the frozen contract (truth fields, hidden markers, diagnostics) is dropped.
 */
export function parseAccusationResponse(raw: unknown): AccusationResponse {
  if (!isRecord(raw)) {
    throw new ValidationError("Accusation response must be an object.");
  }
  const statusRaw = raw.status;
  if (statusRaw !== "ACCUSED") {
    throw new ValidationError('accusationResponse.status must be "ACCUSED".');
  }
  const playthroughId = requireString(raw, "playthroughId", "accusationResponse");
  const caseId = requireString(raw, "caseId", "accusationResponse");
  const caseVersionRaw = raw.caseVersion;
  if (typeof caseVersionRaw !== "number" || !Number.isInteger(caseVersionRaw) || caseVersionRaw < 0) {
    throw new ValidationError("accusationResponse.caseVersion must be a non-negative integer.");
  }
  const accusationRaw = raw.accusation;
  if (!isRecord(accusationRaw)) {
    throw new ValidationError("accusationResponse.accusation must be an object.");
  }
  return {
    playthroughId,
    caseId,
    caseVersion: caseVersionRaw,
    status: "ACCUSED",
    accusation: {
      murdererId: requireString(accusationRaw, "murdererId", "accusation"),
      motiveId: requireString(accusationRaw, "motiveId", "accusation"),
      weaponId: requireString(accusationRaw, "weaponId", "accusation"),
      crimeTime: requireString(accusationRaw, "crimeTime", "accusation"),
    },
  };
}

/** True when the value is a clock time: hours 00-23 and minutes 00-59. */
export function isValidCrimeTime(value: string | null): value is string {
  if (typeof value !== "string" || !CRIME_TIME_PATTERN.test(value)) return false;
  const [hours, minutes] = value.split(":").map((part) => Number(part));
  return hours >= 0 && hours <= 23 && minutes >= 0 && minutes <= 59;
}

function membershipError(field: AccusationField, id: string | null, universeSize: number): string | null {
  if (id === null || id === "") {
    return field === "crimeTime" ? "Choose the time of day of the crime." : "Choose one of the options below.";
  }
  if (universeSize === 0) {
    return "The case data does not include any eligible options.";
  }
  return null;
}

/**
 * Deterministic client-side validation of a filled-in selection.
 *
 * Returns field-level error strings (empty object = valid). Used to gate the
 * confirmation step AND re-run just before submitting, so a hostile/forged
 * selection can never send an out-of-universe id.
 */
export function validateAccusationForm(
  selection: AccusationSelection,
  candidates: AccusationCandidatesDTO,
): AccusationFieldErrors {
  const errors: AccusationFieldErrors = {};

  const suspectIds = candidates.suspects.map((entry) => entry.id);
  const motiveIds = candidates.motives.map((entry) => entry.id);
  const weaponIds = candidates.weapons.map((entry) => entry.id);

  const suspectError = membershipError("murdererId", selection.murdererId, suspectIds.length);
  if (suspectError !== null || !suspectIds.includes(selection.murdererId ?? "")) {
    errors.murdererId = suspectError ?? "Choose a suspect from the published options.";
  }

  const motiveError = membershipError("motiveId", selection.motiveId, motiveIds.length);
  if (motiveError !== null || !motiveIds.includes(selection.motiveId ?? "")) {
    errors.motiveId = motiveError ?? "Choose a motive from the published options.";
  }

  const weaponError = membershipError("weaponId", selection.weaponId, weaponIds.length);
  if (weaponError !== null || !weaponIds.includes(selection.weaponId ?? "")) {
    errors.weaponId = weaponError ?? "Choose a weapon from the published options.";
  }

  if (!isValidCrimeTime(selection.crimeTime)) {
    errors.crimeTime = "Enter a valid 24-hour time (HH:MM).";
  }

  return errors;
}