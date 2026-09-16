import type { ReactElement } from "react";
import type { EvidenceReadResultDTO } from "../api/types";
import { evidenceContent, type EvidenceRow } from "./evidenceContent";
import { evidenceHeaderTitle, type EvidencePreviewModel } from "./evidencePreview";

export interface EvidencePanelProps {
  record: EvidenceReadResultDTO;
  onClose: () => void;
  /**
   * Public registry label of the world object this panel was opened from
   * (Phase 8_1 D1). Rendered as "Kitchen knife — <evidence title>".
   */
  objectLabel?: string | null;
  /**
   * Pure visual object context (registry color + label) for small-evidence
   * previews (Phase 8_1 D2). No image loading, no remote content.
   */
  preview?: EvidencePreviewModel | null;
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
 *
 * Phase 8_1: when the panel is opened from a world-object interaction it also
 * shows (a) the object label next to the evidence title and (b) a purely
 * visual preview swatch built from the PUBLIC registry color + label. All of
 * that data originates in the application-owned registries, never in the
 * record payload, so no-truth-leak guarantees hold.
 */
export default function EvidencePanel({ record, onClose, objectLabel, preview }: EvidencePanelProps) {
  const { title, description, rows } = evidenceContent(record);
  const header = evidenceHeaderTitle(objectLabel, title);

  return (
    <aside
      className="evidence-panel"
      data-testid="evidence-panel"
      role="dialog"
      aria-modal="true"
      aria-label={`Evidence: ${header}`}
    >
      <header className="evidence-panel-header">
        <h3 className="evidence-panel-title">{header}</h3>
      </header>
      {preview && (
        <div className="evidence-preview" data-testid="evidence-preview">
          <span
            className="evidence-preview-swatch"
            data-testid="evidence-preview-swatch"
            style={{ backgroundColor: preview.color }}
            aria-hidden="true"
          />
          <span className="evidence-preview-label" data-testid="evidence-preview-label">
            {preview.label}
          </span>
        </div>
      )}
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