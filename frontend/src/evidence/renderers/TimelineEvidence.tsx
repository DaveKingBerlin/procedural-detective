import type { ReactElement } from "react";
import type { EvidenceRendererProps } from "./shared";
import { compactTimeOf, timeEntryItems } from "./shared";
import GenericEvidence from "./GenericEvidence";

/**
 * Phase 19G §13 — TIMELINE renderer (a closed render type of the contract).
 *
 * Structured chronological list of timeline entries (payload `entries` or
 * `events`, each carrying a visible time + text), rendered exactly like the
 * activity log — accessible two-column table with a <time> per entry, plain
 * text only. An empty timeline falls back to the safe GenericEvidence
 * renderer; timestamps are never fabricated.
 *
 * Phase 19H — the player-facing time is the COMPACT local clock ("23:41");
 * the canonical full ISO timestamp stays in <time dateTime="..."> (see
 * compactTimeOf in shared.ts).
 */
export default function TimelineEvidence({ record }: EvidenceRendererProps): ReactElement {
  const items = timeEntryItems(record.content, ["entries", "events"], ["text", "description"]);
  if (items.length === 0) {
    return <GenericEvidence record={record} />;
  }
  return (
    <section className="evidence-timeline-block" aria-label="Timeline">
      <h4 className="evidence-block-title">Timeline</h4>
      <table className="evidence-table evidence-timeline">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Event</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => (
            <tr key={`timeline-${index}`}>
              <td className="evidence-activity-time">
                <time dateTime={item.time}>{compactTimeOf(item.time)}</time>
              </td>
              <td className="evidence-activity-text">{item.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}