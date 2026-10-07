/**
 * Phase 32 — LOCAL port of the server's exact integer-tick time algebra that
 * a replay needs (backend `app/domain/time_interval.py::parse_iso8601` +
 * the DEC-003 anchoring arithmetic in `app/services/accusation.py`).
 *
 * The EVALUATION RULES of a saved replay are the SAME rules as the server:
 * a bare submitted "HH:MM[:SS]" is anchored onto the saved canonical crime
 * DATE + the saved canonical timezone offset AT EVALUATION TIME and the
 * accepted scoring set is the exact half-open `[T−N, T+N+1)` window
 * (REQUIREMENTS 31.7 / DEC-003). This module is a faithful, deterministic
 * TypeScript port of those small pure pieces — nothing more.
 *
 * Every function here is pure, bounded and NEVER makes a network call.
 */

/** Matches the backend's ASCII-only full ISO-8601-with-offset grammar. The
 *  date is taken literally from the canonical string (DEC-003 anchoring). */
const FULL_ISO_DATE_RE = /^(?<y>[0-9]{4})-(?<mo>[0-9]{2})-(?<d>[0-9]{2})T/;

const ISO_RE =
  /^(?<y>[0-9]{4})-(?<mo>[0-9]{2})-(?<d>[0-9]{2})T(?<h>[0-9]{2}):(?<mi>[0-9]{2}):(?<s>[0-9]{2})(?:\.(?<frac>[0-9]+))?(?<zone>Z|z|[+-][0-9]{2}:?[0-9]{2})$/;

/** Bare 24h time-of-day "HH:MM[:SS]" (DEC-003): strict two-digit fields,
 *  hour 00-23, minute/second 00-59. "25:99:00" / "22:17:60" never match. */
const BARE_TIME_RE = /^(?<h>[01][0-9]|2[0-3]):(?<mi>[0-5][0-9])(?::(?<s>[0-5][0-9]))?$/;

/** Mirrors the backend accusation's `_CRIME_TIME_MAX` (64) — the accepted
 *  crimeTime string length ceiling at the replay submission gate. */
const MAX_ACCUSATION_TIME_TEXT_LENGTH = 64;

/** Parse an ISO-8601-with-offset timestamp -> {epochSeconds, utcOffsetMinutes}.
 *
 *  Strictness mirrored from the backend (DEF-034):
 *   - ASCII digits only;
 *   - offset hours <= 23, offset minutes <= 59;
 *   - second == 60 (leap second) is deterministically clamped to 59;
 *   - impossible calendar days raise.
 *
 *  Throws `SavegameTimeError` for malformed/out-of-range input.
 */
export function parseIso8601(timestamp: string): { epochSeconds: number; utcOffsetMinutes: number } {
  if (typeof timestamp !== "string") {
    throw new SavegameTimeError("timestamp must be a string");
  }
  const match = ISO_RE.exec(timestamp.trim());
  if (match === null) {
    throw new SavegameTimeError("invalid ISO-8601 timestamp");
  }
  const groups = match.groups as { [name: string]: string };
  const y = Number(groups.y);
  const mo = Number(groups.mo);
  const d = Number(groups.d);
  const h = Number(groups.h);
  const mi = Number(groups.mi);
  let s = Number(groups.s);
  if (mo < 1 || mo > 12) throw new SavegameTimeError("month out of range");
  if (d < 1 || d > 31) throw new SavegameTimeError("day out of range");
  if (h > 23) throw new SavegameTimeError("hour out of range");
  if (mi > 59) throw new SavegameTimeError("minute out of range");
  if (s > 60) throw new SavegameTimeError("second out of range");
  if (s === 60) s = 59; // leap second -> clamp (integer-second tick domain)
  const zone = groups.zone.toUpperCase();
  let offsetMinutes = 0;
  if (zone !== "Z") {
    const sign = zone[0] === "+" ? 1 : -1;
    const offsetHours = Number(zone.slice(1, 3));
    const offsetMinutesPart = Number(zone.slice(-2));
    if (offsetHours > 23) throw new SavegameTimeError("offset hour out of range");
    if (offsetMinutesPart > 59) throw new SavegameTimeError("offset minute out of range");
    offsetMinutes = sign * (offsetHours * 60 + offsetMinutesPart);
  }
  let epoch: number;
  // `datetime`-equivalent validation: `Date.UTC` rolls impossible calendar
  // days (e.g. 2026-02-30 -> March 2) instead of throwing, so the probe is
  // ALWAYS compared and a rolled-over value raises (DEF-034 parity).
  const probe = new Date(Date.UTC(y, mo - 1, d, h, mi, s));
  if (
    probe.getUTCFullYear() !== y ||
    probe.getUTCMonth() !== mo - 1 ||
    probe.getUTCDate() !== d ||
    probe.getUTCHours() !== h ||
    probe.getUTCMinutes() !== mi ||
    probe.getUTCSeconds() !== s
  ) {
    throw new SavegameTimeError("invalid calendar date");
  }
  epoch = probe.getTime() / 1000;
  return { epochSeconds: Math.trunc(epoch) - offsetMinutes * 60, utcOffsetMinutes: offsetMinutes };
}

/** True when `value` starts with a 4-digit ISO date component (the backend's
 *  `_FULL_ISO_DATE_RE` discriminator between a full timestamp and a bare
 *  time-of-day). */
export function isFullIsoTimestamp(value: string): boolean {
  return typeof value === "string" && FULL_ISO_DATE_RE.test(value);
}

/** True when `value` is a valid bare 24h "HH:MM[:SS]" time-of-day. */
export function isBareTimeOfDay(value: string): boolean {
  return typeof value === "string" && BARE_TIME_RE.test(value);
}

/**
 * The replay accusation's crimeTime gate — a faithful port of the server's
 * frozen `accusation.parse_accusation_time` (backend
 * `app/services/accusation.py`). A value is accepted ONLY when it is either a
 * fully-parsable full ISO-8601 WITH a timezone offset/zone, or a bare 24h
 * "HH:MM[:SS]". A zone-less full ISO timestamp (e.g. `"2026-09-11T22:17:00"`)
 * IS full-ISO-prefixed (the date discriminator matches) but FAILS the strict
 * offset grammar -> the live server answers 422 and the replay must too
 * (DEF-050 / ADV-32F-05).
 */
export function isValidAccusationTime(raw: unknown): boolean {
  if (typeof raw !== "string" || raw === "" || raw.length > MAX_ACCUSATION_TIME_TEXT_LENGTH) {
    return false;
  }
  const candidate = raw.trim();
  if (isFullIsoTimestamp(candidate)) {
    try {
      parseIso8601(candidate);
    } catch {
      return false;
    }
    return true;
  }
  return isBareTimeOfDay(candidate);
}

/**
 * DEC-003 anchoring: the UTC epoch tick of a submitted bare time-of-day
 * anchored onto the canonical crime DATE + canonical timezone offset.
 * `canonical` must be a valid full ISO-8601-with-offset timestamp.
 */
export function anchorBareTimeTick(bare: string, canonical: string): number {
  const match = BARE_TIME_RE.exec(bare);
  if (match === null) {
    throw new SavegameTimeError("invalid bare time-of-day");
  }
  const groups = match.groups as { [name: string]: string };
  const hour = Number(groups.h);
  const minute = Number(groups.mi);
  const second = Number(groups.s ?? "0");
  const dateMatch = FULL_ISO_DATE_RE.exec(canonical);
  if (dateMatch === null) {
    throw new SavegameTimeError("invalid canonical timestamp");
  }
  const { utcOffsetMinutes } = parseIso8601(canonical);
  const dateGroups = dateMatch.groups as { [name: string]: string };
  const year = Number(dateGroups.y);
  const month = Number(dateGroups.mo);
  const day = Number(dateGroups.d);
  return Math.trunc(Date.UTC(year, month - 1, day, hour, minute, second) / 1000) - utcOffsetMinutes * 60;
}

/**
 * The evaluation-time UTC epoch tick of a submitted crimeTime (DEC-003):
 * full ISO values map directly to their UTC tick; bare times are anchored to
 * the canonical crime date + canonical offset.
 */
export function submittedTick(crimeTime: string, canonical: string): number {
  const raw = String(crimeTime).trim();
  if (isFullIsoTimestamp(raw)) {
    return parseIso8601(raw).epochSeconds;
  }
  return anchorBareTimeTick(raw, canonical);
}

/**
 * The exact accepted scoring set of REQUIREMENTS 31.7: the inclusive
 * human-readable range `T−N through T+N` is the half-open tick window
 * `[T−N, T+N+1)`.
 */
export function acceptedScoringContains(
  canonicalTick: number,
  toleranceSeconds: number,
  tick: number,
): boolean {
  return tick >= canonicalTick - toleranceSeconds && tick < canonicalTick + toleranceSeconds + 1;
}

/** Typed error for every malformed time value crossing the replay trust
 *  boundary. Callers map it to a safe bounded message — never raw input. */
export class SavegameTimeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SavegameTimeError";
  }
}