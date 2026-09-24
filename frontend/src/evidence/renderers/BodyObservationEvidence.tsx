import type { ReactElement } from "react";
import type { EvidenceRendererProps } from "./shared";
import { asText, firstText, isRecord } from "./shared";
import GenericEvidence from "./GenericEvidence";

/** One normalized body-observation item (optional label + text). */
export interface BodyObservationItem {
  label: string;
  text: string;
}

/** The DTO surface used for the summary-level fallback (title/description). */
interface BodyObservationRecord {
  title: string | null;
  description: string | null;
}

/**
 * Deterministic BODY_OBSERVATION extraction (Phase 19G §8). Only the
 * player-safe ALLOWLISTED fields of the DTO/content are read, in a fixed
 * priority:
 *
 *  1. a structured `observations[]` array (string entries or object entries
 *     carrying label/location/bodyPart + text/observation/description);
 *  2. the allowlisted `statement` field, labelled by `speakerName` — the four
 *     BODY_OBSERVATION kinds (testimonial / witness_statement / statement /
 *     suspect_statement) expose exactly `speakerName` + `statement`;
 *  3. the safe `content.summary` text, else the record's own
 *     title/description — the same DTO text the player can already read.
 *
 * An empty `observations[]` does NOT block the later steps, and only
 * non-empty values are ever kept — nothing is fabricated. When every field is
 * empty/absent the result is `[]` and the caller falls back to
 * GenericEvidence.
 */
export function bodyObservationItems(
  content: unknown,
  record: BodyObservationRecord | null = null,
): BodyObservationItem[] {
  const payload = isRecord(content) ? content : {};

  // 1. Structured `observations[]` when the payload carries one.
  const raw = Array.isArray(payload.observations) ? payload.observations : [];
  const items: BodyObservationItem[] = [];
  for (const entry of raw) {
    if (isRecord(entry)) {
      const text = firstText(entry.text, entry.observation, entry.description).trim();
      if (text === "") continue;
      items.push({ label: firstText(entry.label, entry.location, entry.bodyPart).trim(), text });
    } else {
      const text = asText(entry).trim();
      if (text === "") continue;
      items.push({ label: "", text });
    }
  }
  if (items.length > 0) return items;

  // 2. The allowlisted `statement` (+ `speakerName` label).
  const statement = asText(payload.statement).trim();
  if (statement !== "") {
    return [{ label: asText(payload.speakerName).trim(), text: statement }];
  }

  // 3. Safe summary-level text (the render payload always carries `summary`),
  //    else the record title/description.
  const summary = firstText(
    payload.summary,
    record === null ? "" : record.description,
    record === null ? "" : record.title,
  ).trim();
  if (summary !== "") return [{ label: "", text: summary }];
  return [];
}

/**
 * Phase 19G §8 — BODY_OBSERVATION renderer. Observations are a labelled,
 * readable list (plain text; React escapes every value). The actual
 * player-safe observation text is rendered deterministically from the
 * ALLOWLISTED fields — `observations[]`, else `statement`
 * (+ `speakerName`), else `summary`/DTO title/description. No readable
 * text anywhere -> safe GenericEvidence fallback.
 */
export default function BodyObservationEvidence({ record }: EvidenceRendererProps): ReactElement {
  const items = bodyObservationItems(record.content, record);
  if (items.length === 0) {
    return <GenericEvidence record={record} />;
  }
  return (
    <section className="evidence-observations-block" aria-label="Body observations">
      <h4 className="evidence-block-title">Observations</h4>
      <ul className="evidence-observations">
        {items.map((item, index) => (
          <li key={`observation-${index}`}>
            {item.label !== "" ? (
              <span className="evidence-observation-label">{item.label}: </span>
            ) : null}
            {item.text}
          </li>
        ))}
      </ul>
    </section>
  );
}