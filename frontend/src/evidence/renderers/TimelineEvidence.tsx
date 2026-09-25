import type { ReactElement } from "react";
import type { EvidenceRendererProps } from "./shared";
import { timeEntryItems } from "./shared";
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
 * compactTimeOf in shared.ts). DISPLAY granularity is normalized per payload
 * (ADV-247): when any entry needs seconds, every entry renders HH:mm:ss.
 *
 * Phase 19J §32/§34 — long timelines get the SAME bounded keyboard-scrollable
 * region as the activity log (`.evidence-activity-scroll`, max-height +
 * overflow-y:auto, labelled `role="region"` + tabIndex 0 for keyboard
 * scrolling; engages only when the rows outgrow the cap). Reading order stays
 * heading -> table -> Close; no rows are hidden; no animation.
 */
export default function TimelineEvidence({ record }: EvidenceRendererProps): ReactElement {
  const items = timeEntryItems(record.content, ["entries", "events"], ["text", "description"]);
  if (items.length === 0) {
    return <GenericEvidence record={record} />;
  }
  return (
    <section className="evidence-timeline-block" aria-label="Timeline">
      <h4 className="evidence-block-title">Timeline</h4>
      <div
        className="evidence-activity-scroll"
        role="region"
        tabIndex={0}
        aria-label="Timeline entries"
      >
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
                  <time dateTime={item.time}>{item.displayTime}</time>
                </td>
                <td className="evidence-activity-text">{item.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}