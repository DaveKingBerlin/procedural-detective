import { useEffect, useRef } from "react";
import type { ReactElement } from "react";
import type { EvidenceReadResultDTO, VictimProfileDTO } from "../api/types";
import { buildVictimProfileView } from "./victimModel";
import { VICTIM_LABELS } from "./victimLabels";

/**
 * Phase 47 — Victim inspection record.
 *
 * Extends the EXISTING evidence-panel surface (same `<aside>` layout + design
 * system classes) with a compact investigation profile: Identity, Discovery,
 * Known facts, Forensic findings and Timeline. Identity + Discovery come
 * straight from the validated public `victim` block, so they are visible
 * immediately when the victim is selected. Known facts / Forensic findings /
 * Timeline are progressively disclosed from the session's READ-record cache
 * ONLY — the server-authoritative discovery order is never bypassed and no
 * hidden truth field is ever read.
 *
 * Accessibility: a labelled `role="dialog"` with semantic headings, real
 * `<time dateTime>` values and plain text throughout; the Close button takes
 * initial focus and focus returns to the activating control on unmount (the
 * route wires Escape through the SAME close path).
 */
export interface VictimProfilePanelProps {
  /** The validated, player-safe victim profile (never carries truth). */
  victim: VictimProfileDTO;
  /** The session's READ-record cache (server-authoritative). */
  records: readonly EvidenceReadResultDTO[];
  onClose: () => void;
}

interface IdentityRow {
  label: string;
  value: string;
  testId: string;
}

export default function VictimProfilePanel({
  victim,
  records,
  onClose,
}: VictimProfilePanelProps): ReactElement {
  const view = buildVictimProfileView(victim, records);

  // Focus return: capture the activating control on the FIRST render (before
  // autoFocus moves focus to the Close button), then restore it when the panel
  // unmounts — best-effort, never throwing (WitnessPanel / FocusInspection
  // parity).
  const openerRef = useRef<HTMLElement | null>(null);
  if (openerRef.current === null && typeof document !== "undefined") {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }
  useEffect(() => {
    return () => {
      const opener = openerRef.current;
      if (opener !== null && typeof opener.focus === "function") {
        try {
          opener.focus();
        } catch {
          // Focus restoration is best-effort — it must never throw on unmount.
        }
      }
    };
  }, []);

  const identityRows: IdentityRow[] = [
    {
      label: VICTIM_LABELS.nameLabel,
      value: view.identity.name,
      testId: "victim-identity-name",
    },
    {
      label: VICTIM_LABELS.genderLabel,
      value: view.identity.gender ?? "",
      testId: "victim-identity-gender",
    },
    {
      label: VICTIM_LABELS.ageLabel,
      value: view.identity.age === null ? "" : String(view.identity.age),
      testId: "victim-identity-age",
    },
    {
      label: VICTIM_LABELS.occupationLabel,
      value: view.identity.occupation ?? "",
      testId: "victim-identity-occupation",
    },
    // Omit a row entirely when the value is null/empty — never render
    // undefined/null/NaN.
  ].filter((row) => row.value !== "");

  const hasBackground = view.background !== null && view.background !== "";
  const hasKnownFacts = hasBackground || view.knownFacts.length > 0;

  return (
    <aside
      className="evidence-panel victim-panel"
      data-testid="victim-panel"
      role="dialog"
      aria-modal="true"
      aria-label={VICTIM_LABELS.panelAriaLabel}
    >
      <header className="evidence-panel-header">
        <h3 className="evidence-panel-title" data-testid="victim-panel-title">
          {view.headerTitle}
        </h3>
      </header>

      <section className="victim-section" aria-label={VICTIM_LABELS.identityHeading}>
        <h4 className="evidence-block-title">{VICTIM_LABELS.identityHeading}</h4>
        {identityRows.map((row) => (
          <p className="evidence-row" key={row.testId} data-testid={row.testId}>
            <strong>{row.label}:</strong> <span>{row.value}</span>
          </p>
        ))}
      </section>

      <section className="victim-section" aria-label={VICTIM_LABELS.discoveryHeading}>
        <h4 className="evidence-block-title">{VICTIM_LABELS.discoveryHeading}</h4>
        {view.discovery === null ? (
          <p className="evidence-row victim-empty" data-testid="victim-discovery-empty">
            {VICTIM_LABELS.noDiscovery}
          </p>
        ) : (
          <>
            <p className="evidence-row" data-testid="victim-discovery-location">
              <strong>{VICTIM_LABELS.foundAtLabel}:</strong> <span>{view.discovery.foundAt}</span>
            </p>
            <p className="evidence-row" data-testid="victim-discovery-time">
              <strong>{VICTIM_LABELS.discoveredAtLabel}:</strong>{" "}
              <time dateTime={view.discovery.observedAtCanonical}>{view.discovery.discoveredAt}</time>
            </p>
          </>
        )}
      </section>

      <section className="victim-section" aria-label={VICTIM_LABELS.knownFactsHeading}>
        <h4 className="evidence-block-title">{VICTIM_LABELS.knownFactsHeading}</h4>
        {!hasKnownFacts ? (
          <p className="evidence-row victim-empty" data-testid="victim-known-facts-empty">
            {VICTIM_LABELS.noKnownFacts}
          </p>
        ) : (
          <ul className="victim-facts" data-testid="victim-known-facts">
            {hasBackground && (
              <li className="victim-fact" data-testid="victim-background">
                {view.background}
              </li>
            )}
            {view.knownFacts.map((fact) => (
              <li
                className="victim-fact"
                key={fact.evidenceId !== "" ? fact.evidenceId : fact.title}
                data-testid={`victim-fact-${fact.evidenceId}`}
              >
                {fact.title !== "" && <span className="victim-fact-title">{fact.title}</span>}
                {fact.detail !== null && (
                  <span className="victim-fact-detail"> — {fact.detail}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="victim-section" aria-label={VICTIM_LABELS.forensicHeading}>
        <h4 className="evidence-block-title">{VICTIM_LABELS.forensicHeading}</h4>
        {view.forensicFindings.length === 0 ? (
          <p className="evidence-row victim-empty" data-testid="victim-forensic-empty">
            {VICTIM_LABELS.noForensics}
          </p>
        ) : (
          <ul className="victim-facts" data-testid="victim-forensic">
            {view.forensicFindings.map((finding) => (
              <li
                className="victim-fact"
                key={finding.evidenceId !== "" ? finding.evidenceId : finding.title}
                data-testid={`victim-forensic-${finding.evidenceId}`}
              >
                {finding.detail}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="victim-section" aria-label={VICTIM_LABELS.timelineHeading}>
        <h4 className="evidence-block-title">{VICTIM_LABELS.timelineHeading}</h4>
        {view.timeline.length === 0 ? (
          <p className="evidence-row victim-empty" data-testid="victim-timeline-empty">
            {VICTIM_LABELS.noTimeline}
          </p>
        ) : (
          <div
            className="evidence-activity-scroll"
            data-testid="victim-timeline"
            role="region"
            tabIndex={0}
            aria-label="Victim timeline entries"
          >
            <table className="evidence-table evidence-timeline">
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Event</th>
                </tr>
              </thead>
              <tbody>
                {view.timeline.map((item, index) => (
                  <tr key={`victim-timeline-${index}`}>
                    <td className="evidence-activity-time">
                      <time dateTime={item.time}>{item.displayTime}</time>
                    </td>
                    <td className="evidence-activity-text">{item.text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <footer className="evidence-panel-footer">
        <button
          type="button"
          className="evidence-close"
          data-testid="victim-close"
          onClick={onClose}
          aria-label={VICTIM_LABELS.closeLabel}
          autoFocus
        >
          {VICTIM_LABELS.closeText}
        </button>
      </footer>
    </aside>
  );
}
