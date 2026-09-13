import type { EvidenceReadResultDTO } from "../api/types";

/**
 * Evidence presentation model (Phase 6 H, REQUIREMENTS 6.1).
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
 * This module is pure and unit-testable without a DOM.
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