import type { EvidenceReadResultDTO, EvidenceRenderType } from "../api/types";
import ActivityLogEvidence from "./renderers/ActivityLogEvidence";
import BodyObservationEvidence from "./renderers/BodyObservationEvidence";
import DocumentEvidence from "./renderers/DocumentEvidence";
import ForensicComparisonEvidence from "./renderers/ForensicComparisonEvidence";
import GenericEvidence from "./renderers/GenericEvidence";
import MessageEvidence from "./renderers/MessageEvidence";
import TimelineEvidence from "./renderers/TimelineEvidence";
import type { EvidenceRenderer } from "./renderers/shared";

/**
 * Evidence presentation model (Phase 6 H, REQUIREMENTS 6.1) + the CLOSED
 * rich-renderer dispatch (Phase 19G §13).
 *
 * `evidenceContent` turns a validated EvidenceReadResultDTO into an inert,
 * plain-text presentation model (rows of label/value pairs, a readable
 * table, or a list). The UI renders ONLY this model with React's default
 * string rendering:
 *   - no dangerouslySetInnerHTML, no generated HTML, no generated React
 *     components, no eval — generated text can never execute;
 *   - every cell value funnels through `asText`, which keeps hostile,
 *     Unicode or HTML-looking strings as literal text.
 *
 * Phase 19G: when the payload carries a closed `content.renderType`, the
 * panel routes the content region through {@link RENDERER_BY_TYPE} — an
 * EXPLICIT, statically keyed map of application-owned closed renderer
 * components. A render type is never used to resolve a component name from
 * payload content: it is only ever a KEY into this literal object, and any
 * missing/unknown/hostile value resolves to the safe GenericEvidence
 * fallback. Payloads WITHOUT a renderType keep the legacy kind-based rows.
 *
 * The pure model functions (`evidenceContent`, `handleEvidencePanelKey`)
 * remain DOM-free and unit-testable without a DOM; the renderer components
 * are importable headlessly (they touch the DOM only when rendered).
 */

export type EvidenceRow =
  | { type: "label-value"; label: string; value: string }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "list"; items: string[] };

export interface EvidenceContentModel {
  title: string;
  description: string | null;
  rows: EvidenceRow[];
}

/** Escape handling shared by the evidence panel and the scene page. */
export function handleEvidencePanelKey(key: string): "close" | null {
  return key === "Escape" ? "close" : null;
}

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

/** Join an array of values into a comma-separated plain-text list. */
function listText(values: unknown): string {
  if (!Array.isArray(values)) return "";
  return values.map(asText).filter((v) => v !== "").join(", ");
}

function amountText(amount: unknown, currency: unknown): string {
  const amountStr = asText(amount);
  const currencyStr = asText(currency);
  if (amountStr === "" && currencyStr === "") return "";
  return currencyStr === "" ? amountStr : `${currencyStr} ${amountStr}`;
}

export function evidenceContent(record: EvidenceReadResultDTO): EvidenceContentModel {
  const title = typeof record.title === "string" ? record.title : "";
  const description = typeof record.description === "string" ? record.description : null;
  const kind = typeof record.kind === "string" ? record.kind : "";
  const content = isRecord(record.content) ? record.content : {};
  const rows: EvidenceRow[] = [];

  switch (kind) {
    case "object": {
      rows.push({ type: "label-value", label: "Object type", value: asText(content.subtype) });
      rows.push({ type: "label-value", label: "Location", value: asText(content.locationId) });
      break;
    }
    case "email": {
      rows.push({ type: "label-value", label: "From", value: asText(content.fromPersonId) });
      rows.push({ type: "label-value", label: "To", value: listText(content.toPersonIds) });
      rows.push({ type: "label-value", label: "Subject", value: asText(content.subject) });
      rows.push({ type: "label-value", label: "Timestamp", value: asText(content.timestamp) });
      const body = asText(content.body);
      if (body !== "") rows.push({ type: "label-value", label: "Body", value: body });
      break;
    }
    case "financial": {
      rows.push({
        type: "label-value",
        label: "Suspicious",
        value: typeof content.suspicious === "boolean" ? (content.suspicious ? "Yes" : "No") : "",
      });
      const rawRows = Array.isArray(content.rows) ? content.rows : [];
      const cells = rawRows
        .filter((row): row is Record<string, unknown> => isRecord(row))
        .map((row) => [
          asText(row.date),
          asText(row.from),
          asText(row.to),
          amountText(row.amount, row.currency),
          asText(row.description),
        ]);
      if (cells.length > 0) {
        rows.push({ type: "table", headers: ["Date", "From", "To", "Amount", "Description"], rows: cells });
      }
      break;
    }
    case "cctv":
    case "cctv_observation":
    case "view_record": {
      rows.push({ type: "label-value", label: "Camera", value: asText(content.cameraId) });
      const rawEvents = Array.isArray(content.events) ? content.events : [];
      const items = rawEvents
        .filter((event): event is Record<string, unknown> => isRecord(event))
        .map((event) => `${asText(event.time)} — ${asText(event.personId)}: ${asText(event.action)}`);
      if (items.length > 0) {
        rows.push({ type: "list", items });
      }
      break;
    }
    case "testimonial":
    case "witness_statement":
    case "statement":
    case "suspect_statement": {
      rows.push({ type: "label-value", label: "Speaker", value: asText(content.speakerName) });
      const statement = asText(content.statement);
      if (statement !== "") rows.push({ type: "label-value", label: "Statement", value: statement });
      break;
    }
    default:
      // Unknown/other kinds: title + description only (contract default -> {}).
      break;
  }

  // Drop empty label-value rows: hostile/absent content must never produce
  // ghost labels like an empty "Subject:" line.
  const populated = rows.filter((row) => row.type !== "label-value" || row.value !== "");

  return { title, description, rows: populated };
}

/* ======================================================================
 * Phase 19G §13 — CLOSED RENDERER DISPATCH.
 *
 * The render type is DECLARATIVE metadata only. It is used exclusively as
 * a KEY into the explicit literal map below — never to resolve a component
 * name, import path, template or script from provider output.
 * ==================================================================== */

/** The closed render-type universe (Phase 19G §3). */
export const EVIDENCE_RENDER_TYPES: readonly EvidenceRenderType[] = Object.freeze([
  "GENERIC_TEXT",
  "ACTIVITY_LOG",
  "FORENSIC_COMPARISON",
  "MESSAGE",
  "DOCUMENT",
  "BODY_OBSERVATION",
  "TIMELINE",
]);

/**
 * The explicit, closed, statically keyed renderer map. Keys are the literal
 * closed tokens written here in source. `satisfies Record<EvidenceRenderType,
 * EvidenceRenderer>` is a compile-time completeness gate: dropping a closed
 * type is a type error. Any renderType value that is NOT a key of this object
 * (missing, unknown, hostile) goes to the safe GenericEvidence fallback.
 */
export const RENDERER_BY_TYPE: Readonly<Record<string, EvidenceRenderer>> = Object.freeze({
  GENERIC_TEXT: GenericEvidence,
  ACTIVITY_LOG: ActivityLogEvidence,
  FORENSIC_COMPARISON: ForensicComparisonEvidence,
  MESSAGE: MessageEvidence,
  DOCUMENT: DocumentEvidence,
  BODY_OBSERVATION: BodyObservationEvidence,
  TIMELINE: TimelineEvidence,
} satisfies Record<EvidenceRenderType, EvidenceRenderer>);

/**
 * Resolve a render-type token to its closed renderer component. Unknown,
 * missing or hostile values resolve to GenericEvidence — the dispatch NEVER
 * looks up a dynamic component name. The lookup is PROTOTYPE-SAFE: hostile
 * keys such as "__proto__" / "constructor" must never walk the map's
 * prototype chain, so the object's OWN properties are required.
 */
export function resolveEvidenceRenderer(renderType: unknown): EvidenceRenderer {
  if (
    typeof renderType === "string" &&
    Object.prototype.hasOwnProperty.call(RENDERER_BY_TYPE, renderType)
  ) {
    return RENDERER_BY_TYPE[renderType];
  }
  return GenericEvidence;
}

/**
 * The closed renderer for a discovered record, or NULL when the payload
 * carries NO renderType at all (an older server / a legacy kind): the panel
 * then keeps the kind-based rows model. Any present STRING renderType value
 * — known or not — dispatches through the closed map, landing hostile or
 * unknown values on GenericEvidence, never on dynamic code.
 */
export function evidenceRendererFor(record: EvidenceReadResultDTO): EvidenceRenderer | null {
  const content = record.content;
  if (!isRecord(content) || typeof content.renderType !== "string") return null;
  return resolveEvidenceRenderer(content.renderType);
}
