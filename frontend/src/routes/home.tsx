import { Link, useNavigate, useOutletContext } from "react-router";
import type { BackendStatus } from "../hooks/useBackendStatus";
import { useGenerationCapabilities } from "../hooks/useGenerationCapabilities";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { setJourneyParams } from "../journey/context";
import { EXAMPLE_PROMPT } from "../journey/demoPrompt";
import { demoCtaLabel, demoCtaNote } from "../journey/generationMode";
import { GenerationModeDisplay } from "../journey/generationModeSelector";
import {
  providerPathNoteFromCapabilities,
  providerQualifierFromCapabilities,
} from "../journey/providerMode";

/**
 * "/" — the public landing page (Phase 8 D, REQUIREMENTS 3.1).
 *
 * A first-time visitor needs ZERO developer knowledge. The landing clearly
 * presents the TWO Phase 15 paths (Phase15.md "Demo mode"):
 *
 *   1. "Try Demo Case" — the deterministic, zero-cost demo journey: it runs
 *      immediately (through /generating) inside a real 3D investigation and
 *      is labelled as such ("Deterministic demo — no API keys, no cost").
 *   2. "Generate a New Mystery" — the SAME generation journey started from
 *      the /new prompt screen, emphasising a custom prompt. Mechanics are
 *      identical to the demo path; the note beside it is CAPABILITY-DRIVEN
 *      (Phase 18A): it reflects whatever the backend actually reports via the
 *      public generation-capabilities DTO — the deterministic generator, the
 *      local AI pipeline or the configured live provider. A build-time env
 *      value can no longer contradict the backend's report, so the page never
 *      claims a provider that is not really running.
 *
 * Phase 21B Finding 3 — the example/demo CTA is TRUTHFUL per the backend
 * capability DTO (src/journey/generationMode.ts demoCtaLabel/demoCtaNote):
 * the "Try Demo Case" + deterministic/no-cost promise appears ONLY when the
 * backend actually server-enforces the deterministic path
 * (`configuredProvider == "fake"` or, on older servers, the availability-
 * derived demo-only shape). When the backend reports the local or live
 * provider (including a configured ollama/live backend whose probe FAILED —
 * DEF-096), the SAME action (startDemo -> setJourneyParams({prompt:
 * EXAMPLE_PROMPT, difficulty: "medium"}) -> /generating -> runDemo POST
 * /cases) is renamed to "Try an example case" with the truthful per-mode
 * note and an explicit "not the free deterministic demo" warning; a
 * null/unreachable capability report downgrades the CTA to the neutral
 * label and a provider-neutral note — the frontend cannot know the provider
 * when the DTO is unavailable. No request field, mode switching or provider
 * selection ever changes.
 *
 * The legacy "New Investigation" entry stays as-is (it reaches the same
 * /new screen). The backend status indicator stays visible (data-testid
 * "backend-status" lives in the shell header; a compact copy is shown here
 * for demo troubleshooting).
 */
export interface HomeProps {
  /** Backend status override (unit tests inject a value; the route reads the shell context). */
  status?: BackendStatus;
  /**
   * Phase 16 Track B — generation-capabilities override (unit tests inject a
   * fixture; the route fetches + parses the public DTO at runtime).
   */
  capabilities?: GenerationCapabilitiesResponse | null;
}

export default function Home(overrides: HomeProps = {}) {
  const navigate = useNavigate();
  const outletStatus = overrides.status ?? useOutletContext<BackendStatus>();
  const fetchedCapabilities = useGenerationCapabilities();
  const capabilities =
    overrides.capabilities !== undefined ? overrides.capabilities : fetchedCapabilities;
  const { state, message, readiness } = outletStatus;

  const startDemo = () => {
    setJourneyParams({ prompt: EXAMPLE_PROMPT, difficulty: "medium" });
    navigate("/generating");
  };

  return (
    <section className="page home landing">
      <h2 className="landing-title">Procedural Detective</h2>
      <p className="landing-tagline">
        Describe a crime. AI builds a logically solvable 3D investigation.
      </p>

      <div className="landing-actions">
        <Link className="landing-button landing-button--primary" to="/new" data-testid="new-investigation">
          New Investigation
        </Link>
        <button
          type="button"
          className="landing-button"
          data-testid="try-demo"
          onClick={startDemo}
        >
          {demoCtaLabel(capabilities)}
        </button>
      </div>
      {/* ADV-152 — honest app-level provider qualifier right below the primary
          CTA: the REQUIREMENTS §62 tagline stays verbatim, and this note makes
          the provider story unambiguous (Phase 18A: derived from the backend
          capability report — never a build-time env claim, never server text). */}
      <p className="provider-qualifier" data-testid="provider-qualifier">
        {providerQualifierFromCapabilities(capabilities)}
      </p>
      {/* Phase 21B Finding 3 — the example-case note is capability-validated
          (demoCtaNote in src/journey/generationMode.ts): an explicit
          deterministic/no-cost promise ONLY for a KNOWN demo-only backend;
          a renamed + "not the free deterministic demo" per-mode note for a
          local/live backend; a provider-neutral note when the DTO is
          unavailable. The action itself never changes (same runDemo path). */}
      <p className="landing-path-note landing-path-note--demo" data-testid="try-demo-note">
        {demoCtaNote(capabilities)}
      </p>

      <div className="landing-actions landing-actions--generate">
        <Link
          className="landing-button landing-button--primary generate-path"
          to="/new"
          data-testid="generate-new-mystery"
        >
          Generate a New Mystery
        </Link>
      </div>
      <p className="landing-path-note landing-path-note--generate" data-testid="generate-provider-note">
        {providerPathNoteFromCapabilities(capabilities)}
      </p>

      {/* Phase 21 F-03 — generation-mode READ-ONLY display (the interactive
          selector was removed: the selected mode was never sent to the
          backend, whose provider is process-global). The line is driven
          solely by the backend-generation-capabilities DTO: a demo-only
          backend shows "Demo mode active" + "Generation mode: Deterministic
          demo"; a configured local/live provider shows its truthful line
          (with an "Unavailable" tag while its probe is down — DEF-096); a
          DTO-unavailable state shows the neutral reachability line, never a
          provider claim (DEF-097). No host/IP, credentials, prompts or
          diagnostics are ever rendered — only frozen public labels — and no
          click changes the provider. */}
      <GenerationModeDisplay capabilities={capabilities} />

      <div className="landing-panel">
        <h3>How it works</h3>
        <ul className="landing-bullets">
          <li>
            <strong>Describe a crime</strong> — your plain-language prompt
            becomes a full case with one hidden truth.
          </li>
          <li>
            <strong>Deterministic validation</strong> — the truth is checked by
            rules, so every case is solvable from the evidence, not a coin flip.
          </li>
          <li>
            <strong>Explore in 3D</strong> — investigate the scene, collect
            evidence, accuse a suspect, and reveal the truth.
          </li>
        </ul>
      </div>

      <div className="landing-panel">
        <h3>Controls</h3>
        <p>
          Drag to look around · scroll to zoom · click an object to interact ·
          use the object list as an alternative · Esc closes panels.
        </p>
      </div>

      <p className="landing-github">
        <a data-testid="github-link" href="https://github.com/DaveKingBerlin/procedural-detective">
          View on GitHub
        </a>
      </p>

      <BackendStatusPanel state={state} message={message} readiness={readiness} />
    </section>
  );
}

function BackendStatusPanel(status: BackendStatus) {
  return (
    <div className="landing-status" data-testid="home-backend-status">
      <h3>Backend status</h3>
      <p>
        State:{" "}
        <span className={`status-text status-text--${status.state}`}>{status.state}</span>
        <br />
        Message: <code data-testid="home-backend-message">{status.message}</code>
        {status.readiness && (
          <>
            <br />
            <span data-testid="home-readiness">
              Readiness: database {status.readiness.database} · migrations{" "}
              {status.readiness.migrations}
            </span>
          </>
        )}
      </p>
    </div>
  );
}