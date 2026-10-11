import type { AccusationCandidatesDTO, AccusationResponse, ResolutionType } from "../api/types";
import { ValidationError } from "../scene/validation";

/**
 * Client-side accusation domain validation (Phase 7 K + Phase 41).
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
 *
 * Phase 41 — final-theory semantics: a selection is either a HOMICIDE theory
 * (resolutionType="HOMICIDE" + a person in candidates.finalTargets, falling
 * back to suspects for legacy payloads) XOR a SUICIDE theory
 * (resolutionType="SUICIDE" + murdererId=null + candidates.hasSuicideOption).
 * The victim is never a homicide target — the final-target universe excludes
 * it, so membership in finalTargets IS the not-the-victim check.
 */

export const CRIME_TIME_PATTERN = /^\d{2}:\d{2}$/;

export interface AccusationSelection {
  /** Phase 41 — the chosen resolution theory (null = nothing chosen yet). */
  resolutionType: ResolutionType | null;
  /** Phase 41 — the responsible person id; null for SUICIDE or nothing chosen. */
  murdererId: string | null;
  motiveId: string | null;
  weaponId: string | null;
  /** Bare 24h time "HH:MM" as collected from the time input. */
  crimeTime: string | null;
}

export const EMPTY_SELECTION: AccusationSelection = {
  resolutionType: null,
  murdererId: null,
  motiveId: null,
  weaponId: null,
  crimeTime: null,
};

export type AccusationField = "resolutionType" | "murdererId" | "motiveId" | "weaponId" | "crimeTime";

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
 * The FINAL homicide target universe of a candidate payload (Phase 41 §8):
 * `finalTargets` when published (suspects + witnesses, victim excluded),
 * otherwise the legacy `suspects` universe. Deterministic and order-preserving;
 * NEVER re-sorted, never a correctness marker.
 */
export function finalHomicideTargets(candidates: AccusationCandidatesDTO): readonly { id: string; name: string; role: string }[] {
  if (candidates.finalTargets !== undefined && candidates.finalTargets !== null && candidates.finalTargets.length > 0) {
    return candidates.finalTargets as readonly { id: string; name: string; role: string }[];
  }
  return candidates.suspects.map((entry) => ({ id: entry.id, name: entry.name, role: "suspect" }));
}

/** The final homicide target ids of a candidate payload (see finalHomicideTargets). */
export function finalHomicideTargetIds(candidates: AccusationCandidatesDTO): string[] {
  return finalHomicideTargets(candidates).map((entry) => entry.id);
}

/** True when the player's selection is a SUICIDE theory. */
export function isSuicideSelection(selection: AccusationSelection): boolean {
  return selection.resolutionType === "SUICIDE";
}

/** The public labeled murderer value of a selection (confirmation summary). */
export function finalTheoryPersonLabel(
  selection: AccusationSelection,
  candidates: AccusationCandidatesDTO,
): string {
  if (selection.resolutionType === "SUICIDE") {
    return `${candidates.victimName ?? "The victim"} — suicide / self-inflicted`;
  }
  const target = finalHomicideTargets(candidates).find((entry) => entry.id === selection.murdererId);
  return target?.name ?? "—";
}

/**
 * Parse the 200 accusation response into the typed allowlist. Anything not in
 * the frozen contract (truth fields, hidden markers, diagnostics) is dropped.
 * Phase 41 — an echoed `resolutionType:"SUICIDE"` carries `murdererId:null`;
 * legacy echoes (no resolutionType) keep a non-null murdererId.
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
  // Phase 41 — optional echoed resolution type (absent for legacy echoes).
  const resolutionTypeRaw = accusationRaw.resolutionType;
  let resolutionType: ResolutionType | null = null;
  if (resolutionTypeRaw !== undefined && resolutionTypeRaw !== null) {
    if (resolutionTypeRaw !== "HOMICIDE" && resolutionTypeRaw !== "SUICIDE") {
      throw new ValidationError('accusation.resolutionType must be "HOMICIDE" or "SUICIDE".');
    }
    resolutionType = resolutionTypeRaw;
  }
  // Phase 41 — murdererId is nullable ONLY in a SUICIDE echo.
  const murdererIdRaw = accusationRaw.murdererId;
  let murdererId: string | null;
  if (resolutionType === "SUICIDE") {
    if (murdererIdRaw !== null) {
      throw new ValidationError("accusation.murdererId must be null for SUICIDE.");
    }
    murdererId = null;
  } else {
    if (!isNonEmptyString(murdererIdRaw)) {
      throw new ValidationError("accusation.murdererId must be a non-empty string.");
    }
    murdererId = murdererIdRaw;
  }
  return {
    playthroughId,
    caseId,
    caseVersion: caseVersionRaw,
    status: "ACCUSED",
    accusation: {
      ...(resolutionType !== null ? { resolutionType } : {}),
      murdererId,
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
 * selection can never send an out-of-universe id or a wrong resolution.
 *
 * Phase 41 rules:
 *  - SUICIDE requires candidates.hasSuicideOption AND murdererId === null;
 *  - HOMICIDE (or a legacy no-resolutionType selection) requires a person in
 *    the final homicide target universe (murdererId is then never the victim,
 *    because the final-target universe excludes the victim);
 *  - any other resolution value is a closed client-side error — never a raw
 *    exception, never a network request.
 */
export function validateAccusationForm(
  selection: AccusationSelection,
  candidates: AccusationCandidatesDTO,
): AccusationFieldErrors {
  const errors: AccusationFieldErrors = {};

  const resolution = selection.resolutionType;
  const targetIds = finalHomicideTargetIds(candidates);
  const motiveIds = candidates.motives.map((entry) => entry.id);
  const weaponIds = candidates.weapons.map((entry) => entry.id);

  if (resolution === "SUICIDE") {
    if (candidates.hasSuicideOption !== true) {
      errors.murdererId = "The suicide option is not available for this case.";
    } else if (selection.murdererId !== null) {
      errors.murdererId = "A suicide theory cannot name a perpetrator.";
    }
  } else if (resolution === "HOMICIDE" || resolution === null) {
    const suspectError = membershipError("murdererId", selection.murdererId, targetIds.length);
    if (suspectError !== null || !targetIds.includes(selection.murdererId ?? "")) {
      errors.murdererId = suspectError ?? "Choose a suspect from the published options.";
    }
  } else {
    // Unknown / unsupported resolution value — closed client-side error.
    errors.resolutionType = "Choose one of the options below.";
    errors.murdererId = "Choose one of the options below.";
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