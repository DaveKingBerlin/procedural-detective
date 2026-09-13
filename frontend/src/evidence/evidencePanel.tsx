import type { ReactElement } from "react";
import type { EvidenceReadResultDTO } from "../api/types";
import { evidenceContent, type EvidenceRow } from "./evidenceContent";

export interface EvidencePanelProps {
  record: EvidenceReadResultDTO;
  onClose: () => void;
}

/**
 * Evidence inspection overlay (Phase 6 H, REQUIREMENTS 51).
 *
 * Renders the last-read EvidenceReadResultDTO with PLAIN TEXT only: React's
 * default string rendering escapes everything — no dangerouslySetInnerHTML,
 * no generated HTML, no raw propositions. Keyboard: the close button is a
 * real focusable control and the scene page wires Escape (see
 * handleEvidencePanelKey). This component is DOM-free at import time, so it
 * can be rendered headlessly by `react-dom/server` in tests when checking
 * that HTML-looking strings stay inert.
 */
export default function EvidencePanel({ record, onClose }: EvidencePanelProps) {
  const { title, description, rows } = evidenceContent(record);

  return (
    <aside
      className="evidence-panel"
      data-testid="evidence-panel"
      role="dialog"
      aria-modal="true"
      aria-label={`Evidence: ${title}`}
    >
      <header className="evidence-panel-header">
        <h3 className="evidence-panel-title">{title}</h3>
      </header>
      {description !== null && description !== "" && (
        <p className="evidence-description">{description}</p>
      )}
      <div className="evidence-content">{rows.map((row, index) => renderRow(row, index))}</div>
      <footer className="evidence-panel-footer">
        <button
          type="button"
          className="evidence-close"
          data-testid="evidence-close"
          onClick={onClose}
          aria-label="Close evidence panel"
        >
          Close
        </button>
      </footer>
    </aside>
  );
}

function renderRow(row: EvidenceRow, index: number): ReactElement {
  switch (row.type) {
    case "label-value":
      return (
        <p className="evidence-row" key={`row-${index}`}>
          <strong>{row.label}:</strong> <span>{row.value}</span>
        </p>
      );
    case "table":
      return (
        <table className="evidence-table" key={`row-${index}`}>
          <thead>
            <tr>
              {row.headers.map((header, h) => (
                <th key={`h-${h}`}>{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {row.rows.map((cells, r) => (
              <tr key={`r-${r}`}>
                {cells.map((cell, c) => (
                  <td key={`c-${c}`}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    case "list":
      return (
        <ul className="evidence-list" key={`row-${index}`}>
          {row.items.map((item, i) => (
            <li key={`i-${i}`}>{item}</li>
          ))}
        </ul>
      );
  }
}