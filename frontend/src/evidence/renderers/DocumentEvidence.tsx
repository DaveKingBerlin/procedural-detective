import type { ReactElement } from "react";
import type { EvidenceRendererProps } from "./shared";
import { firstText, isRecord } from "./shared";
import GenericEvidence from "./GenericEvidence";

/**
 * Phase 19G §8 — DOCUMENT renderer.
 *
 * Renders the document title + body as PLAIN TEXT (React escapes; no HTML
 * injection sink whatsoever). The title is the payload title when present,
 * else the DTO title; body text may span lines (pre-wrap). A document with
 * no body falls back to the safe GenericEvidence renderer.
 */
export default function DocumentEvidence({ record }: EvidenceRendererProps): ReactElement {
  const content = isRecord(record.content) ? record.content : {};
  const title = firstText(content.title, content.documentTitle, record.title);
  const body = firstText(content.body, content.text);
  if (body === "") {
    return <GenericEvidence record={record} />;
  }
  return (
    <section className="evidence-document" aria-label="Document">
      {title !== "" && <h4 className="evidence-block-title">{title}</h4>}
      <p className="evidence-document-body">{body}</p>
    </section>
  );
}