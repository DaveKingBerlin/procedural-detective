import type { ReactElement } from "react";
import type { EvidenceRendererProps } from "./shared";
import { asText, firstText, isRecord } from "./shared";
import GenericEvidence from "./GenericEvidence";

/** One normalized body-observation item (optional label + text). */
export interface BodyObservationItem {
  label: string;
  text: string;
}

/**
 * Parse the closed BODY_OBSERVATION payload (`observations` array). Accepts
 * string entries ("A cut on the left forearm") and object entries carrying a
 * label/bodyPart plus the observation text. Only non-empty observations are
 * kept — the payload is never invented.
 */
export function bodyObservationItems(content: unknown): BodyObservationItem[] {
  if (!isRecord(content)) return [];
  const raw = Array.isArray(content.observations) ? content.observations : [];
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
  return items;
}

/**
 * Phase 19G §8 — BODY_OBSERVATION renderer. Observations are a labelled,
 * readable list (plain text; React escapes every value). No observations in
 * the payload -> safe GenericEvidence fallback.
 */
export default function BodyObservationEvidence({ record }: EvidenceRendererProps): ReactElement {
  const items = bodyObservationItems(record.content);
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