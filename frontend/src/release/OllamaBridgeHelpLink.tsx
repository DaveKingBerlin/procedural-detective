import { RELEASE_DOWNLOAD_ANCHOR_ID } from "./releaseManifest";

/**
 * Phase 49 §50 — the discoverable Local-Ollama help/download entry.
 *
 * Rendered where the user chooses Local Ollama (the /new provider controls and
 * the BYO-Ollama bridge panel), it jumps to the "Local AI with Ollama" download
 * section ON THE SAME PAGE. It is intentionally a single muted line — it never
 * clutters the provider selection.
 *
 * The link is a same-page hash anchor (no router navigation, no reload, no
 * download): the section carries {@link RELEASE_DOWNLOAD_ANCHOR_ID}.
 */
export const OLLAMA_BRIDGE_HELP_COPY = "Need the Ollama Bridge? Download for Windows or Linux";

export interface OllamaBridgeHelpLinkProps {
  /** Optional extra class for placement-specific spacing. */
  className?: string;
}

export default function OllamaBridgeHelpLink({ className }: OllamaBridgeHelpLinkProps = {}) {
  return (
    <p
      className={className === undefined ? "ollama-bridge-help" : `ollama-bridge-help ${className}`}
      data-testid="ollama-bridge-help"
    >
      <a
        className="ollama-bridge-help-link"
        data-testid="ollama-bridge-help-link"
        href={`#${RELEASE_DOWNLOAD_ANCHOR_ID}`}
      >
        {OLLAMA_BRIDGE_HELP_COPY}
      </a>
    </p>
  );
}
