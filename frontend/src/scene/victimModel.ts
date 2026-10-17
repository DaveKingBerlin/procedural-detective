import type { EvidenceReadResultDTO, VictimProfileDTO } from "../api/types";
import {
  asText,
  compactTimeInfo,
  firstText,
  isRecord,
  timeEntryItems,
  type TimeEntryItem,
} from "../evidence/renderers/shared";
import { VICTIM_LABELS, victimGenderLabel } from "./victimLabels";

/**
 * Phase 47 — pure, deterministic victim-profile view model.
 *
 * Turns the validated player-safe `victim` block plus the session's READ
 * records into the exact presentation model of the victim inspection surface.
 * This module is DOM-free and NEVER throws:
 *   - only PUBLIC-SAFE fields are read; no hidden CaseTruth key is ever
 *     consulted (the read-record allowlist is `title` / `description` /
 *     `content.summary|comparison|result|entries` and nothing else);
 *   - Known facts / Forensic findings / Timeline are derived from READ records
 *     ONLY (progressive disclosure — the session cache is server-authoritative
 *     and only ever holds records the server confirmed read);
 *   - a malformed record degrades to an empty (safe) value, never a crash and
 *     never `undefined` / `null` / `NaN` in the rendered text.
 */

export interface VictimIdentityView {
  name: string;
  /** Human gender label ("Male"…) or null when absent/unknown. */
  gender: string | null;
  /** Valid age, or null when absent/malformed. */
  age: number | null;
  occupation: string | null;
}

export interface VictimDiscoveryView {
  /** Discovery location display name. */
  foundAt: string;
  /** Player-facing compact clock ("22:18"). */
  discoveredAt: string;
  /** Canonical ISO value kept for the semantic <time dateTime="...">. */
  observedAtCanonical: string;
}

export interface VictimKnownFactView {
  evidenceId: string;
  title: string;
  detail: string | null;
}

export interface VictimForensicView {
  evidenceId: string;
  title: string;
  /** Plain-text finding (comparison result / summary); never empty. */
  detail: string;
}

export interface VictimProfileView {
  /** "Victim — <discovery title>" (fallback "Victim"). */
  headerTitle: string;
  identity: VictimIdentityView;
  /** Short public background line, or null. */
  background: string | null;
  discovery: VictimDiscoveryView | null;
  knownFacts: VictimKnownFactView[];
  forensicFindings: VictimForensicView[];
  timeline: TimeEntryItem[];
}

/** True for a READ record the backend explicitly links to the victim. */
export function isVictimRelatedRecord(record: EvidenceReadResultDTO): boolean {
  return isRecord(record.content) && record.content.victimRelated === true;
}

/** True for a READ FORENSIC_COMPARISON record (closed render type). */
export function isForensicRecord(record: EvidenceReadResultDTO): boolean {
  return isRecord(record.content) && record.content.renderType === "FORENSIC_COMPARISON";
}

/** Plain-text "known fact" summary of a record (summary, else description). */
function knownFactDetail(record: EvidenceReadResultDTO): string | null {
  const content = isRecord(record.content) ? record.content : {};
  const detail = firstText(content.summary, record.description).trim();
  return detail === "" ? null : detail;
}

/**
 * Plain-text forensic finding: mirrors the closed
 * `ForensicComparisonEvidence` renderer's field preference so the victim
 * record shows the SAME player-visible comparison phrase.
 */
function forensicDetail(record: EvidenceReadResultDTO): string {
  const content = isRecord(record.content) ? record.content : {};
  return firstText(
    content.result,
    content.summary,
    content.comparison,
    content.conclusion,
    content.text,
    record.description,
    record.title,
  ).trim();
}

/** The body-discovery timeline (the discovery record's closed entries). */
function victimTimeline(
  victim: VictimProfileDTO,
  records: readonly EvidenceReadResultDTO[],
): TimeEntryItem[] {
  const discovery = victim.discovery;
  if (discovery === null || discovery.evidenceId === "") return [];
  const record = records.find((entry) => entry.evidenceId === discovery.evidenceId);
  if (record === undefined) return [];
  return timeEntryItems(record.content, ["entries", "events"], ["text", "description"]);
}

/** Derive the full victim view from the validated profile + READ records. */
export function buildVictimProfileView(
  victim: VictimProfileDTO,
  records: readonly EvidenceReadResultDTO[],
): VictimProfileView {
  const discovery = victim.discovery;
  const headerTitle =
    discovery !== null && discovery.title.trim() !== ""
      ? `${VICTIM_LABELS.fallbackTitle}${VICTIM_LABELS.titleSeparator}${discovery.title.trim()}`
      : VICTIM_LABELS.fallbackTitle;

  const discoveryView: VictimDiscoveryView | null =
    discovery === null
      ? null
      : {
          foundAt: discovery.locationName,
          discoveredAt: compactTimeInfo(discovery.observedAt).compact,
          observedAtCanonical: discovery.observedAt,
        };

  const readVictimRecords = records.filter(isVictimRelatedRecord);

  const knownFacts: VictimKnownFactView[] = [];
  for (const record of readVictimRecords) {
    const title = asText(record.title).trim();
    const detail = knownFactDetail(record);
    if (title === "" && detail === null) continue;
    knownFacts.push({ evidenceId: asText(record.evidenceId).trim(), title, detail });
  }

  const forensicFindings: VictimForensicView[] = [];
  for (const record of readVictimRecords) {
    if (!isForensicRecord(record)) continue;
    const detail = forensicDetail(record);
    if (detail === "") continue;
    forensicFindings.push({
      evidenceId: asText(record.evidenceId).trim(),
      title: asText(record.title).trim(),
      detail,
    });
  }

  return {
    headerTitle,
    identity: {
      name: victim.name,
      gender: victimGenderLabel(victim.gender),
      age: victim.age,
      occupation: victim.occupation,
    },
    background: victim.background,
    discovery: discoveryView,
    knownFacts,
    forensicFindings,
    timeline: victimTimeline(victim, records),
  };
}

/**
 * Phase 47 — the minimal public shape needed to identify the victim body. Both
 * the live scene model's `SceneWorldObject` and the raw `WorldObjectDTO`
 * satisfy it, so detection works without coupling to either type.
 */
export interface VictimSceneObjectLike {
  objectId: string;
  assetType: string;
  subtype: string | null;
  evidenceId: string | null;
}

/**
 * Phase 47 — true when `object` is the VICTIM body representation. Detection
 * is strict when the backend publishes a `sceneObjectId`:
 *   1. a NON-EMPTY `victim.sceneObjectId` is AUTHORITATIVE — the object matches
 *      ONLY when its objectId equals it (DEF-121: a non-victim object that
 *      happens to share the discovery evidenceId or the `victim_body` asset
 *      semantics must NOT be treated as the victim);
 *   2. otherwise (`sceneObjectId` absent/null/empty) fall back to the object
 *      whose (now discovered) evidence link equals the discovery evidenceId;
 *   3. then the object's semantic `assetType`/`subtype` is `victim_body` (a
 *      safety net for servers that omit `sceneObjectId`; witness persons are
 *      `assetType:"character"`, never `victim_body`).
 */
export function isVictimSceneObject(
  object: VictimSceneObjectLike,
  victim: VictimProfileDTO | null,
): boolean {
  if (victim === null) return false;
  const sceneObjectId = victim.sceneObjectId;
  if (typeof sceneObjectId === "string" && sceneObjectId !== "") {
    return sceneObjectId === object.objectId;
  }
  const discoveryEvidenceId = victim.discovery !== null ? victim.discovery.evidenceId : null;
  if (typeof discoveryEvidenceId === "string" && discoveryEvidenceId !== "" && object.evidenceId === discoveryEvidenceId) {
    return true;
  }
  return object.assetType === "victim_body" || object.subtype === "victim_body";
}
