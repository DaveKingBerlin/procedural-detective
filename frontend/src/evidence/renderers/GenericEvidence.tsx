import type { ReactElement } from "react";
import type { EvidenceRendererProps } from "./shared";

/**
 * Phase 19G — safe GENERIC_TEXT fallback renderer.
 *
 * Renders ONLY the DTO title + description as plain text (React escapes
 * everything). This is the closed fallback for the explicit "GENERIC_TEXT"
 * render type, for any unknown/missing/hostile render type, and for any rich
 * renderer whose payload turned out to carry no readable content — it can
 * never break the panel for valid unknown evidence (Phase 19G §8/§13).
 */
export default function GenericEvidence({ record }: EvidenceRendererProps): ReactElement {
  const title = typeof record.title === "string" ? record.title : "";
  const description = typeof record.description === "string" ? record.description : "";
  return (
    <section className="evidence-generic" aria-label="Evidence content">
      {title !== "" && <h4 className="evidence-block-title">{title}</h4>}
      {description !== "" && <p className="evidence-generic-description">{description}</p>}
    </section>
  );
}