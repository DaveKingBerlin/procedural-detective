import type { ReactElement } from "react";
import type { EvidenceRendererProps } from "./shared";
import { firstText, isRecord } from "./shared";
import GenericEvidence from "./GenericEvidence";

/**
 * Phase 19G §8 — FORENSIC_COMPARISON renderer.
 *
 * Renders the actual player-visible comparison result as plain text. The
 * result comes from the closed payload's result fields when present and
 * falls back to the DTO description/title — the same "comparison phrase"
 * the legacy kind path surfaces, but now as the rich renderer body. No
 * payload at all -> safe GenericEvidence fallback.
 */
export default function ForensicComparisonEvidence({ record }: EvidenceRendererProps): ReactElement {
  const content = isRecord(record.content) ? record.content : {};
  const result = firstText(
    content.result,
    content.summary,
    content.comparison,
    content.conclusion,
    content.text,
    record.description,
    record.title,
  );
  if (result === "") {
    return <GenericEvidence record={record} />;
  }
  return (
    <section className="evidence-forensic" aria-label="Forensic comparison">
      <h4 className="evidence-block-title">Forensic comparison</h4>
      <p className="evidence-forensic-result">{result}</p>
    </section>
  );
}