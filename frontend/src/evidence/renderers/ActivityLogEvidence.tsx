import type { ReactElement } from "react";
import type { EvidenceRendererProps } from "./shared";
import { timeEntryItems } from "./shared";
import GenericEvidence from "./GenericEvidence";

/**
 * Phase 19G §7 — ACTIVITY_LOG renderer.
 *
 * Compact structured view: an "Activity log" heading plus one accessible row
 * per concrete entry, with the time visibly separated (two-column table:
 * Time / Activity) and associated with its text both visually and via table
 * semantics + a real <time> element. Entries are sorted chronologically
 * (defensively — stable for unparseable times). Every value is plain TEXT
 * (React escapes); no raw JSON, no horizontal overflow (fixed table layout).
 *
 * Phase 19H — TIME PRESENTATION: the PLAYER-FACING time is the COMPACT local
 * clock ("23:41"), while the CANONICAL full ISO timestamp (with its offset)
 * is preserved verbatim in the semantic <time dateTime="..."> attribute —
 * the DTO keeps the full value, nothing is converted or discarded
 * (compactTimeOf in shared.ts is deterministic and never fabricates).
 * Per-payload DISPLAY granularity is normalized too: when any entry of the
 * payload needs seconds, every entry renders as HH:mm:ss (ADV-247, see
 * normalizeTimeEntryDisplays in shared.ts).
 *
 * KEY INVARIANT (Phase 19G §16): concrete, player-visible timestamps the
 * server actually sent. If the payload carries no concrete entries the
 * renderer falls back to the DTO title/description — it NEVER fabricates a
 * timestamp.
 */
export default function ActivityLogEvidence({ record }: EvidenceRendererProps): ReactElement {
  const items = timeEntryItems(record.content, ["entries"], ["text"]);
  if (items.length === 0) {
    return <GenericEvidence record={record} />;
  }
  return (
    <section className="evidence-activities" aria-label="Activity log">
      <h4 className="evidence-block-title">Activity log</h4>
      <table className="evidence-table evidence-activity-log">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Activity</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => (
            <tr key={`activity-${index}`}>
              <td className="evidence-activity-time">
                <time dateTime={item.time}>{item.displayTime}</time>
              </td>
              <td className="evidence-activity-text">{item.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}