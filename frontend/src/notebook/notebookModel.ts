import type { EvidenceReadResultDTO } from "../api/types";
import type { SceneWorldObject } from "../scene/buildInvestigationScene";
import { evidenceLabelFor } from "../scene/objectLabel";
import { formatCrimeTime } from "../reveal/revealFormat";

/**
 * Detective Notebook model (Phase 18C) — pure, deterministic derivation of
 * the pre-reveal "Detective Notebook" panel on the investigation page.
 *
 * Safety model (the ONE rule): the notebook shows ONLY what the player has
 * already discovered/read.
 *   - every record-derived entry is gated on `discoveredEvidenceIds`
 *     membership (the server-authoritative knowledge snapshot);
 *   - content-derived groups (People / Motive / Timeline / Digital-physical)
 *     come from the READ-records cache, which the session fills ONLY from
 *     server-confirmed discovery reads or the bootstrap-confirmed
 *     `readEvidenceIds` (lazy hydration) — never from undiscovered ids;
 *   - the Objects group lists ONLY world objects the server marked
 *     discovered, labelled through the established evidenceLabelFor path
 *     (catalog label / humanized proc canonicalName — never raw ids or
 *     `proc.*` tokens);
 *   - NO truth, hidden/eliminated candidate, solver winner, correct-answer
 *     marker or provider internals ever enters this module — it has no
 *     input capable of carrying them (only knowledge + read records +
 *     world objects).
 *
 * Everything is deterministic: identical inputs always yield identical
 * models, entries are sorted, and a reload with an empty record cache
 * re-derives the same group HEADERS + world-object groups immediately
 * (record groups catch up once the session lazy-hydrates the read ids).
 */

export type NotebookGroupId = "people" | "objects" | "motive" | "timeline" | "digital-physical";

export const NOTEBOOK_GROUP_ORDER: readonly NotebookGroupId[] = [
  "people",
  "objects",
  "motive",
  "timeline",
  "digital-physical",
];

export interface NotebookEntry {
  /** Stable deterministic per-group id (evidenceId / objectId derived). */
  id: string;
  /** Player-safe primary text (speaker / object label / title / time). */
  label: string;
  /** Optional supporting player-safe detail (statement / point / context). */
  detail: string | null;
  /** The originating evidence id when derivable (never fabricated). */
  evidenceId: string | null;
  /** True when the player has read the underlying record. */
  read: boolean;
}

export interface NotebookGroup {
  id: NotebookGroupId;
  title: string;
  emptyMessage: string;
  entries: NotebookEntry[];
}

export interface NotebookModel {
  groups: NotebookGroup[];
  discoveredEvidenceIds: readonly string[];
  readEvidenceIds: readonly string[];
}

export interface NotebookSource {
  discoveredEvidenceIds: readonly string[];
  readEvidenceIds: readonly string[];
  worldObjects: readonly SceneWorldObject[];
  /** Cached read records (session cache — empty right after a reload). */
  records: ReadonlyArray<EvidenceReadResultDTO>;
}

/** Evidence kinds whose READ content yields witness/suspect facts. */
const PEOPLE_KINDS: ReadonlySet<string> = new Set([
  "testimonial",
  "witness_observation",
  "witness_statement",
  "suspect_statement",
  "statement",
]);

/** Evidence kinds whose READ content safely implies a motive-related clue. */
const MOTIVE_KINDS: ReadonlySet<string> = new Set(["financial", "document", "digital"]);

/** Evidence kinds that constitute digital / physical evidence records. */
const DIGITAL_PHYSICAL_KINDS: ReadonlySet<string> = new Set([
  "email",
  "cctv",
  "forensic",
  "physical",
  "object",
  "document",
  "view_record",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Coerce an arbitrary DTO field to a plain string; hostile values stay literal. */
function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

const SAFE_TITLE_FALLBACK = "Discovered record";

/** Never a raw id: title ?? app-authored fallback. */
function safeTitle(record: EvidenceReadResultDTO): string {
  const title = asText(record.title).trim();
  return title !== "" ? title : SAFE_TITLE_FALLBACK;
}

/** Display "HH:MM" from a timestamp when parseable; else the raw text. */
function displayTime(value: string): string {
  const formatted = formatCrimeTime(value);
  return formatted !== "" ? formatted : value;
}

function sortEntries(entries: NotebookEntry[]): NotebookEntry[] {
  return [...entries].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

function dedupeById(entries: NotebookEntry[]): NotebookEntry[] {
  const seen = new Set<string>();
  const out: NotebookEntry[] = [];
  for (const entry of entries) {
    if (seen.has(entry.id)) continue;
    seen.add(entry.id);
    out.push(entry);
  }
  return out;
}

/** Read records involving people — people-cached facts only (never guesswork). */
function peopleEntries(records: EvidenceReadResultDTO[], discoveredSet: Set<string>, readEvidenceIds: readonly string[]): NotebookEntry[] {
  const readSet = new Set(readEvidenceIds);
  const entries: NotebookEntry[] = [];
  for (const record of records) {
    if (!discoveredSet.has(record.evidenceId)) continue;
    if (!PEOPLE_KINDS.has(record.kind)) continue;
    const content = isRecord(record.content) ? record.content : {};
    const speaker = asText(content.speakerName).trim();
    const statement = asText(content.statement).trim();
    entries.push({
      id: `people-${record.evidenceId}`,
      label: speaker !== "" ? speaker : safeTitle(record),
      detail: statement !== "" ? statement : null,
      evidenceId: record.evidenceId,
      read: readSet.has(record.evidenceId),
    });
  }
  return entries;
}

/** Discovered world objects, labelled through the evidenceLabelFor path. */
function objectsEntries(
  worldObjects: readonly SceneWorldObject[],
  readEvidenceIds: readonly string[],
): NotebookEntry[] {
  const readSet = new Set(readEvidenceIds);
  const entries: NotebookEntry[] = [];
  for (const obj of [...worldObjects].sort((a, b) => (a.objectId < b.objectId ? -1 : a.objectId > b.objectId ? 1 : 0))) {
    if (!obj.discovered || obj.evidenceId === null) continue;
    entries.push({
      id: `objects-${obj.evidenceId}`,
      label: evidenceLabelFor(obj),
      detail: null,
      evidenceId: obj.evidenceId,
      read: readSet.has(obj.evidenceId),
    });
  }
  return dedupeById(entries);
}

/** Motive-related clues — read financial/document/digital content only. */
function motiveEntries(records: EvidenceReadResultDTO[], discoveredSet: Set<string>, readEvidenceIds: readonly string[]): NotebookEntry[] {
  const readSet = new Set(readEvidenceIds);
  const entries: NotebookEntry[] = [];
  for (const record of records) {
    if (!discoveredSet.has(record.evidenceId)) continue;
    if (!MOTIVE_KINDS.has(record.kind)) continue;
    const description = asText(record.description).trim();
    entries.push({
      id: `motive-${record.evidenceId}`,
      label: safeTitle(record),
      detail: description !== "" ? description : null,
      evidenceId: record.evidenceId,
      read: readSet.has(record.evidenceId),
    });
  }
  return entries;
}

/** Timeline — timestamps/events extracted from READ content, never invented. */
function timelineEntries(records: EvidenceReadResultDTO[], discoveredSet: Set<string>, readEvidenceIds: readonly string[]): NotebookEntry[] {
  const readSet = new Set(readEvidenceIds);
  const raw: Array<{ label: string; detail: string | null; evidenceId: string; rawTime: string }> = [];
  const seen = new Set<string>();
  for (const record of records) {
    if (!discoveredSet.has(record.evidenceId)) continue;
    const content = isRecord(record.content) ? record.content : {};
    const schedule = (rawTime: string, label: string, detail: string | null) => {
      const key = `${label}\u0000${detail ?? ""}\u0000${record.evidenceId}`;
      if (seen.has(key)) return;
      seen.add(key);
      raw.push({ label, detail, evidenceId: record.evidenceId, rawTime });
    };
    // content.events[].time (+ a player-safe context snippet, if published)
    if (Array.isArray(content.events)) {
      for (const rawEvent of content.events) {
        if (!isRecord(rawEvent)) continue;
        const time = asText(rawEvent.time).trim();
        if (time === "") continue;
        const detailParts = [asText(rawEvent.personId), asText(rawEvent.action), asText(rawEvent.description)]
          .map((part) => part.trim())
          .filter((part) => part !== "");
        schedule(
          time,
          displayTime(time),
          detailParts.length > 0 ? detailParts.join(" — ") : safeTitle(record),
        );
      }
    }
    // content.timestamp (authorship / transmission moment)
    const timestamp = asText(content.timestamp).trim();
    if (timestamp !== "") {
      schedule(timestamp, displayTime(timestamp), safeTitle(record));
    }
    // content.presentation.timestamp (declarative presentation moment)
    const presentation = content.presentation;
    if (isRecord(presentation)) {
      const presentationTimestamp = asText(presentation.timestamp).trim();
      if (presentationTimestamp !== "") {
        schedule(presentationTimestamp, displayTime(presentationTimestamp), safeTitle(record));
      }
    }
    // content.proposition.observed_at (player-safe observed moment)
    const proposition = content.proposition;
    if (isRecord(proposition)) {
      const observedAt = asText(proposition.observed_at).trim();
      if (observedAt !== "") {
        schedule(observedAt, displayTime(observedAt), safeTitle(record));
      }
    }
  }
  // Deterministic chronological order by the RAW time text (ISO timestamps and
  // bare times both sort within their own formats; identical instants are
  // tie-broken by evidence id then detail), then fixed rank ids assigned AFTER
  // the sort so the ids are stable regardless of cache/input order.
  const sorted = [...raw].sort((a, b) => {
    const ta = Date.parse(a.rawTime);
    const tb = Date.parse(b.rawTime);
    const na = Number.isNaN(ta) ? 0 : ta;
    const nb = Number.isNaN(tb) ? 0 : tb;
    if (na !== nb) return na - nb;
    const byRaw = a.rawTime.localeCompare(b.rawTime);
    if (byRaw !== 0) return byRaw;
    const byEvidence = a.evidenceId.localeCompare(b.evidenceId);
    if (byEvidence !== 0) return byEvidence;
    return (a.detail ?? "").localeCompare(b.detail ?? "");
  });
  return sorted.map((entry, index) => ({
    id: `timeline-${String(index).padStart(2, "0")}`,
    label: entry.label,
    detail: entry.detail,
    evidenceId: entry.evidenceId,
    read: readSet.has(entry.evidenceId),
  }));
}

/** Digital/physical evidence — READ records of the listed kinds. */
function digitalPhysicalEntries(records: EvidenceReadResultDTO[], discoveredSet: Set<string>, readEvidenceIds: readonly string[]): NotebookEntry[] {
  const readSet = new Set(readEvidenceIds);
  const entries: NotebookEntry[] = [];
  for (const record of records) {
    if (!discoveredSet.has(record.evidenceId)) continue;
    if (!DIGITAL_PHYSICAL_KINDS.has(record.kind)) continue;
    const description = asText(record.description).trim();
    entries.push({
      id: `digital-physical-${record.evidenceId}`,
      label: safeTitle(record),
      detail: description !== "" ? description : null,
      evidenceId: record.evidenceId,
      read: readSet.has(record.evidenceId),
    });
  }
  return entries;
}

const GROUP_META: Record<NotebookGroupId, { title: string; emptyMessage: string }> = {
  people: {
    title: "People",
    emptyMessage: "No people facts noted yet — read witness statements to fill this in.",
  },
  objects: {
    title: "Objects",
    emptyMessage: "No discovered objects yet — look around the room.",
  },
  motive: {
    title: "Motive-related clues",
    emptyMessage: "No motive-related clues discovered yet — check financial, document and digital evidence.",
  },
  timeline: {
    title: "Timeline",
    emptyMessage: "No timeline entries discovered yet — timestamps appear here once you read the records that carry them.",
  },
  "digital-physical": {
    title: "Digital / physical evidence",
    emptyMessage: "No digital or physical evidence discovered yet.",
  },
};

/**
 * Build the deterministic notebook model. Pure — never throws, never reads
 * the network or storage; callers pass exactly what the session knows.
 */
export function buildNotebookModel(source: NotebookSource): NotebookModel {
  const discoveredSet = new Set(source.discoveredEvidenceIds);
  const sortedRecords = [...source.records].sort((a, b) => a.evidenceId.localeCompare(b.evidenceId));

  const groups: NotebookGroup[] = [
    {
      id: "people",
      title: GROUP_META.people.title,
      emptyMessage: GROUP_META.people.emptyMessage,
      entries: sortEntries(dedupeById(peopleEntries(sortedRecords, discoveredSet, source.readEvidenceIds))),
    },
    {
      id: "objects",
      title: GROUP_META.objects.title,
      emptyMessage: GROUP_META.objects.emptyMessage,
      entries: sortEntries(objectsEntries(source.worldObjects, source.readEvidenceIds)),
    },
    {
      id: "motive",
      title: GROUP_META.motive.title,
      emptyMessage: GROUP_META.motive.emptyMessage,
      entries: sortEntries(dedupeById(motiveEntries(sortedRecords, discoveredSet, source.readEvidenceIds))),
    },
    {
      id: "timeline",
      title: GROUP_META.timeline.title,
      emptyMessage: GROUP_META.timeline.emptyMessage,
      entries: timelineEntries(sortedRecords, discoveredSet, source.readEvidenceIds),
    },
    {
      id: "digital-physical",
      title: GROUP_META["digital-physical"].title,
      emptyMessage: GROUP_META["digital-physical"].emptyMessage,
      entries: sortEntries(dedupeById(digitalPhysicalEntries(sortedRecords, discoveredSet, source.readEvidenceIds))),
    },
  ];

  return {
    groups,
    discoveredEvidenceIds: [...source.discoveredEvidenceIds].sort(),
    readEvidenceIds: [...source.readEvidenceIds].sort(),
  };
}