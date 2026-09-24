import type { ReactElement } from "react";
import type { EvidenceRendererProps } from "./shared";
import { asText, isRecord, textList } from "./shared";
import GenericEvidence from "./GenericEvidence";

/**
 * Phase 19G §8 — MESSAGE renderer.
 *
 * Renders the closed MESSAGE payload — sender, recipients, subject, time and
 * body — as a labelled plain-text list (React escapes every value). Empty
 * allowlisted fields are dropped; a message with nothing readable falls back
 * to the safe GenericEvidence renderer.
 */
export default function MessageEvidence({ record }: EvidenceRendererProps): ReactElement {
  const content = isRecord(record.content) ? record.content : {};
  const from = asText(content.fromPersonId).trim();
  const to = textList(content.toPersonIds);
  const subject = asText(content.subject).trim();
  const timestamp = asText(content.timestamp).trim();
  const body = asText(content.body).trim();
  if (from === "" && to === "" && subject === "" && timestamp === "" && body === "") {
    return <GenericEvidence record={record} />;
  }
  return (
    <section className="evidence-message" aria-label="Message">
      <h4 className="evidence-block-title">Message</h4>
      <dl className="evidence-definition-list">
        {from !== "" && (
          <>
            <dt>From</dt>
            <dd>{from}</dd>
          </>
        )}
        {to !== "" && (
          <>
            <dt>To</dt>
            <dd>{to}</dd>
          </>
        )}
        {subject !== "" && (
          <>
            <dt>Subject</dt>
            <dd>{subject}</dd>
          </>
        )}
        {timestamp !== "" && (
          <>
            <dt>Timestamp</dt>
            <dd>{timestamp}</dd>
          </>
        )}
        {body !== "" && (
          <>
            <dt>Body</dt>
            <dd>{body}</dd>
          </>
        )}
      </dl>
    </section>
  );
}