import type { WorldObjectDTO } from "../api/types";
import type { SavegameEvidenceContentV1, SavedCaseDefinition, SavegameObjectV1, SavegameV1Document } from "./savegameV1";
import {
  SAVEGAME_EXTENSION,
  SAVEGAME_FORMAT,
  SAVEGAME_FORMAT_VERSION,
  SAVEGAME_MIME_TYPE,
} from "./savegameV1";

/**
 * Phase 32 — client-side export helpers.
 *
 *  - `serializeSavegameV1` — deterministic compact JSON with SORTED object
 *    keys + compact separators, mirroring the backend's
 *    `savegame.serialize_savegame_v1` (equal documents -> byte-identical
 *    text — no provider/session/random values ever enter the bytes).
 *  - `reExportV1` — a CLEAN FRESH export from a normalized in-memory
 *    `SavedCaseDefinition` (Phase32 §15): the definition IS already the
 *    normalized archive, so a local re-save is a direct re-serialization
 *    with a new `exportedAt` — no nested saves, no export history, no server
 *    round-trip.
 *  - `downloadSavegameText` — the client-side `.pdcase` download (Blob +
 *    URL.createObjectURL + anchor; NEVER forced).
 *  - filename helpers mirroring `backend savegame.savegame_filename`
 *    (defense-in-depth Content-Disposition-style sanitizing).
 *
 * The exported bytes contain ONLY the strict allowlist of the normalized
 * definition: metadata, public case + scene projections, candidates,
 * witnesses, evidence records and ReplayTruthV1. NO API keys, tokens,
 * prompts, provider material or internal CaseTruth object is ever included
 * (Phase32 §23 / ADR-003 §5).
 */

/** True when the value looks like a plain JSON object (for the recursive
 *  sorter). */
function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Recursive sorted serialization: object keys sorted ascending, compact
 *  separators `,`/`:`, non-ASCII kept literal (mirror of
 *  `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))`). */
export function serializeSavegameV1(document: SavegameV1Document): string {
  const render = (value: unknown): string => {
    if (value === null) return "null";
    if (typeof value === "string") return JSON.stringify(value);
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (Array.isArray(value)) {
      return `[${value.map(render).join(",")}]`;
    }
    if (isPlainRecord(value)) {
      const entries: string[] = [];
      for (const key of Object.keys(value).sort()) {
        entries.push(`${JSON.stringify(key)}:${render(value[key])}`);
      }
      return `{${entries.join(",")}}`;
    }
    return "null";
  };
  return render(document);
}

/** A fresh `exportedAt` (second precision, trailing Z) — the only thing a
 *  re-export changes (Phase32 §15). */
export function freshExportedAt(now: Date = new Date()): string {
  return now.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Canonical key-emission policy (DEF-049 / ADV-32F-03): the re-export must be
 * BYTE-IDENTICAL to a server export of the same case (when `exportedAt` is
 * pinned). The backend projection OMITS null-valued OPTIONAL keys in a few
 * exact places (its helpers never fabricate a `null` for them); the normalized
 * definition keeps explicit nulls for consumers. canonicalExport strips
 * exactly those keys so `serializeSavegameV1(reExportV1(...))` reproduces the
 * backend's `json.dumps(sort_keys=True, ...)` bytes:
 *
 *  - `scene.worldObjects[].generated` / `displayLabel` — emitted ONLY when
 *    non-null (publication.py::project_world_objects);
 *  - `publicCase.objects[].subtype` — emitted ONLY when non-null
 *    (publication.py::public_case_dict_from_payload `_objects`);
 *  - `evidence[].content.events[].personId` — emitted ONLY when non-null
 *    (publication.py::_filter_list_of_mappings drops null values).
 */

function canonicalWorldObject(worldObject: WorldObjectDTO): WorldObjectDTO {
  const out: WorldObjectDTO = { ...worldObject };
  if (out.generated === null || out.generated === undefined) delete out.generated;
  if (out.displayLabel === null || out.displayLabel === undefined) delete out.displayLabel;
  return out;
}

function canonicalPublicObject(object: SavegameObjectV1): SavegameObjectV1 {
  const out: SavegameObjectV1 = { ...object };
  if (out.subtype === null || out.subtype === undefined) delete out.subtype;
  return out;
}

function canonicalContent(content: SavegameEvidenceContentV1): SavegameEvidenceContentV1 {
  if (content.events === undefined) return { ...content };
  const events = content.events.map((event) => {
    const out: { time: string; personId?: string; action: string } = {
      time: event.time,
      action: event.action,
    };
    if (event.personId !== null && event.personId !== undefined) out.personId = event.personId;
    return out;
  });
  return { ...content, events };
}

/**
 * Rebuild the exact SavegameV1 document from a normalized in-memory
 * `SavedCaseDefinition` with a NEW `exportedAt` (Phase32 §15). The
 * definition is already the strict-allowlist archive, so this is a direct
 * projection — never a `JSON.stringify` of application state. The emitted
 * document applies the backend's canonical key-emission policy (null-valued
 * optional keys omitted in the exact places the server projection omits
 * them), so a re-save of a server export is byte-identical when `exportedAt`
 * is pinned (DEF-049 / ADV-32F-03).
 */
export function reExportV1(definition: SavedCaseDefinition, exportedAt?: string): SavegameV1Document {
  const doc: SavegameV1Document = {
    format: SAVEGAME_FORMAT,
    formatVersion: SAVEGAME_FORMAT_VERSION,
    exportedAt: exportedAt ?? freshExportedAt(),
    case: {
      metadata: { ...definition.metadata },
      publicCase: {
        ...definition.publicCase,
        objects: definition.publicCase.objects.map(canonicalPublicObject),
      },
      scene: {
        ...definition.scene,
        worldObjects: definition.scene.worldObjects.map(canonicalWorldObject),
      },
      candidates: definition.candidates,
      witnesses: [...definition.witnesses],
      evidence: definition.evidence.map((record) => ({
        ...record,
        content: canonicalContent(record.content),
      })),
      replayTruth: { ...definition.replayTruth },
    },
  };
  return doc;
}

/** Only `[A-Za-z0-9_-]` survives a suggested filename base (mirror of the
 *  backend `_FILENAME_SAFE_RE`; dots excluded so no path/shape survives). */
const FILENAME_SAFE = /[^A-Za-z0-9_-]/g;
const FILENAME_COLLAPSE = /_+/g;

/** Sanitize a case/playthrough id into a safe filename base. */
export function safeSavegameBaseName(raw: unknown): string {
  const input = String(raw ?? "");
  const safe = input
    .replace(FILENAME_SAFE, "_")
    .replace(FILENAME_COLLAPSE, "_")
    .replace(/^[_-]+|[_-]+$/g, "");
  return safe !== "" ? safe : "case";
}

/** The suggested download filename (`procedural-detective-case-<safe>.pdcase`). */
export function savegameFilenameFor(raw: unknown): string {
  return `procedural-detective-case-${safeSavegameBaseName(raw)}${SAVEGAME_EXTENSION}`;
}

export interface SavegameDownloadSink {
  /** Called with the exact text/filename/mime a real download would ship. */
  download(text: string, filename: string, mime: string): void;
}

let downloadSink: SavegameDownloadSink | null = null;

/** Test seam: substitute the tiny DOM surface `downloadSavegameText` needs.
 *  Without a sink and without a DOM the helper degrades to a silent no-op. */
export function setSavegameDownloadSink(sink: SavegameDownloadSink | null): void {
  downloadSink = sink;
}

/**
 * Trigger a client-side download of one `.pdcase` document. Never forces:
 * the anchor carries the `download` attribute and the browser decides. Uses
 * Blob + URL.createObjectURL + anchor click and revokes the object URL after
 * a short delay (no memory leak on modern browsers).
 */
export function downloadSavegameText(text: string, filename: string): void {
  if (downloadSink !== null) {
    downloadSink.download(text, filename, SAVEGAME_MIME_TYPE);
    return;
  }
  try {
    if (typeof window === "undefined" || typeof document === "undefined") return;
    const blob = new Blob([text], { type: SAVEGAME_MIME_TYPE });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noreferrer";
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch {
    // A download helper failure must never take the page down; the player
    // can retry the Save action.
  }
}

export { SAVEGAME_EXTENSION, SAVEGAME_MIME_TYPE };