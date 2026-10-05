import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import type { GenerationCapabilitiesResponse, GenerationModeId } from "../api/types";
import { useGenerationCapabilities } from "../hooks/useGenerationCapabilities";
import { setJourneyParams, type JourneyDifficulty, type JourneyParams } from "../journey/context";
import { rollDemoCaseId } from "../journey/demoCaseSelection";
import { PROMPT_MAX_CHARS } from "../journey/demoPrompt";
import {
  EXAMPLE_PROMPTS,
  EXAMPLE_PROMPT_IDS,
  isKnownExample,
  noteExamplePromptEdit,
  selectExamplePrompt,
  type ExamplePromptId,
} from "../journey/examplePrompts";
import { getGenerationMode, isLocalModeAvailable, LOCAL_AI_SHOWCASE_NOTE, demoCtaLabel, demoCtaNote, demoCtaState } from "../journey/generationMode";
import { GenerationModeDisplay } from "../journey/generationModeSelector";
import { GenerationProviderSelector } from "../journey/GenerationProviderSelector";
import {
  FRONTIER_SUBMIT_REQUIRED_MESSAGE,
  bridgePairedSelection,
  hasGenerationProviderOffer,
  isFrontierSubmitReady,
  persistGenerationSelection,
  resolveProviderSelection,
  type GenerationProviderSelection,
} from "../journey/generationProvider";
import LocalAiBridgePanel from "../journey/LocalAiBridgePanel";
import {
  providerPathNoteFromCapabilities,
  providerQualifierFromCapabilities,
} from "../journey/providerMode";
import { examplePromptText, validatePrompt } from "../journey/promptValidation";

/**
 * "/new" — the prompt-to-case screen (Phase 8 B, REQUIREMENTS 3.1).
 *
 * A natural-language detective scenario becomes a generated investigation:
 * the user types (or fills) a bounded prompt, optionally picks a difficulty
 * label, and presses "Generate case". Validation is minimal and safe (non
 * empty, <= 4000 chars); the accepted journey is handed to /generating
 * through the in-memory journey context — no prompt/token ever enters the
 * URL. "Try the demo case" starts the same journey with the REQUIREMENTS 48
 * example prompt.
 *
 * Phase 15 Track B — the two paths are clearly labelled here too: this form
 * IS the "Generate a New Mystery" path (custom prompt emphasised, with a
 * provider-honest note), while the demo link carries its own zero-cost/
 * deterministic sub-note. Phase 18A — the provider note is CAPABILITY-DRIVEN:
 * it reflects the backend's public generation-capabilities DTO (deterministic
 * generator / local AI pipeline / live provider), so a build-time env value
 * can never contradict the backend's actual provider.
 *
 * Phase 21 F-03 — the generation-mode area is a READ-ONLY display driven
 * solely by the capabilities DTO (src/journey/generationModeSelector.tsx).
 * The interactive provider selector was removed because the "selected" mode
 * was never sent to the backend (process-global GENERATION_PROVIDER); the
 * page never implies that a click can switch the provider.
 *
 * Phase 21B Finding 3 — the demo link label + note are TRUTHFUL per the
 * backend capability DTO (src/journey/generationMode.ts demoCtaLabel /
 * demoCtaNote). The deterministic/no-cost promise appears ONLY when the
 * backend server-enforces the deterministic path (`configuredProvider ==
 * "fake"` or the older-server availability-derived demo-only shape); an
 * ollama/live-configured backend renames the CTA to "Try an example case"
 * with the truthful per-mode note + the "not the free deterministic demo"
 * warning — EVEN when its probe fails (DEF-096: configuredProvider is
 * authoritative); a null/unreachable report downgrades to the neutral label
 * and a provider-neutral note. The ACTION itself is byte-identical in every
 * state (stageJourney(examplePromptText(), difficulty)) — no request field,
 * no mode switching.
 */
export interface NewCasePageProps {
  /**
   * Phase 16 Track B — generation-capabilities override (unit tests inject a
   * fixture; the route fetches + parses the public DTO at runtime).
   */
  capabilities?: GenerationCapabilitiesResponse | null;
  /**
   * Phase 16.2 Track B — test seam mirroring `capabilities`: the stored-mode
   * value the journey reads from `pd_generation_mode` at runtime. When
   * omitted the route reads that key (falling back to Demo); a fixture value
   * lets the honesty copy (unavailable note / §36 showcase sentence) be
   * unit-tested without a DOM. Phase 21 F-03: legacy-safe read only — no user
   * action writes the key anymore; values here come from older app versions
   * or the QA storage seam and are handled as defense-in-depth.
   */
  modeOverride?: GenerationModeId | null;
}

export default function NewCasePage(overrides: NewCasePageProps = {}) {
  const navigate = useNavigate();
  const fetchedCapabilities = useGenerationCapabilities();
  const capabilities =
    overrides.capabilities !== undefined ? overrides.capabilities : fetchedCapabilities;
  const [prompt, setPrompt] = useState("");
  const [difficulty, setDifficulty] = useState<JourneyDifficulty>("medium");
  const [error, setError] = useState<string | null>(null);
  // Phase 21 F-03 — the mode is READ-ONLY information now: no user action
  // changes it (the provider is process-global), so it is read once from the
  // legacy-safe storage key / override and never written again.
  const [mode] = useState<GenerationModeId>(() =>
    overrides.modeOverride ?? getGenerationMode() ?? "demo",
  );
  /**
   * Phase 17D Bugfix PART B — id of the example prompt currently loaded in
   * the textarea (null once the user edits away / writes their own prompt).
   * Kept in sync with `prompt` ONLY through the pure transitions in
   * src/journey/examplePrompts.ts — never derived ad hoc, never special-cased.
   */
  const [activeExampleId, setActiveExampleId] = useState<ExamplePromptId | null>(null);

  /**
   * Phase 25 — the browser-selected generation provider. Resolved from the
   * PARSED capability DTO + sessionStorage when the additive provider offer
   * exists (null while capabilities are unknown / absent on an OLDER server —
   * the page then offers no selector and POST /cases carries no selection,
   * byte-identical). Persisted to sessionStorage on every change (only the
   * three non-secret preference keys, §10.1).
   */
  const [providerSelection, setProviderSelection] = useState<GenerationProviderSelection | null>(
    null,
  );

  useEffect(() => {
    if (capabilities === null) return;
    if (!hasGenerationProviderOffer(capabilities)) {
      setProviderSelection(null);
      return;
    }
    // Resolve once the DTO arrives: a valid sessionStorage choice, else the
    // server default, else the first still-available provider. A later
    // capabilities refresh must never overwrite a user's explicit choice.
    setProviderSelection((current) => current ?? resolveProviderSelection(capabilities, undefined));
  }, [capabilities]);

  /**
   * Phase 26C1 (LOW fix) — ordering guard for the pairing-completion
   * override. True once the user makes an explicit provider/transport radio
   * choice AFTER the current pairing began (see onProviderSelectionChange);
   * reset by {@link handleBridgePairingStarted} when a fresh pairing opens.
   */
  const explicitChoiceSincePairingStartedRef = useRef(false);

  const onProviderSelectionChange = (next: GenerationProviderSelection) => {
    // Phase 26C1 (LOW fix) — an explicit user interaction is the source of
    // every selector `onChange`: re-arm the pairing-completion ordering guard
    // whenever the user's provider or TRANSPORT radio choice changes (exactly
    // the `selectProvider` / `selectTransport` paths — model-only edits do NOT
    // re-arm it: the pairing selection preserves the current model). A choice
    // made AFTER this pairing began is the newer intent and wins over the
    // eventual completion (see handleBridgePaired).
    const prev = providerSelection;
    if (
      prev === null ||
      prev.generationProvider !== next.generationProvider ||
      prev.ollamaTransport !== next.ollamaTransport
    ) {
      explicitChoiceSincePairingStartedRef.current = true;
    }
    setProviderSelection(next);
    persistGenerationSelection(next, undefined);
  };

  /**
   * Phase 26C1 (LOW fix) — a fresh pairing window just opened. Any explicit
   * provider/transport choice that came BEFORE this point is OLDER intent:
   * the guard is re-armed so the completed pairing may (still) override it.
   */
  const handleBridgePairingStarted = () => {
    explicitChoiceSincePairingStartedRef.current = false;
  };

  /**
   * Phase 26C1 §3 — a successful Bridge pairing is strong Bridge intent: the
   * Ollama provider with the Bridge transport becomes selected + persisted
   * (`bridge`). A user's LATER explicit radio choice always wins over the
   * pairing selection; capability refreshes never revert it. The low-gap
   * interleaved rule follows the same line: an explicit provider/transport
   * choice made AFTER the pairing started (deterministic re-arm guard, no
   * timestamps) wins over the completion; only a choice made BEFORE the
   * pairing began is the older intent and may be overridden.
   */
  const handleBridgePaired = () => {
    // The transport-selection surface must actually exist for the pairing
    // intent to be expressed (an OLDER server without `providers` has no
    // selector and no selection keys are ever posted).
    if (!hasGenerationProviderOffer(capabilities)) return;
    // Ordered-intent rule: the pairing applies ONLY when the user has not made
    // an explicit provider/transport radio choice since this pairing began
    // (that choice is the newer intent and is preserved verbatim). A choice
    // made BEFORE handleBridgePairingStarted re-armed the guard is old intent
    // and the pairing still overrides it.
    if (explicitChoiceSincePairingStartedRef.current) return;
    onProviderSelectionChange(bridgePairedSelection(capabilities, providerSelection));
  };

  /**
   * Phase 16.2 §20/§36 — mode-honesty flags. The UI only ever claims Local-AI
   * behavior when the backend allowlist reports local available (the display
   * never shows an unavailable mode). Phase 21 F-03: no user action selects a
   * mode anymore (the provider is process-global and the selector was
   * removed), so a stored `local` value can ONLY come from OLDER app versions /
   * the QA storage seam — this is handled as defense-in-depth: a stored
   * `local` mode with an unavailable/absent/down backend surfaces the explicit
   * "Local AI is unavailable" note instead of silently pretending local is
   * active, and it must NOT claim the §36 showcase pipeline while unavailable
   * (Demo remains the honest fallback). While capabilities are unknown (null)
   * no claim and no note is rendered.
   */
  const localActive =
    mode === "local" && capabilities !== null && isLocalModeAvailable(capabilities);
  const localUnavailable =
    mode === "local" && capabilities !== null && !isLocalModeAvailable(capabilities);

  /** Phase 17D Bugfix PART B — select a showcase example: replace the textarea
   *  text with the EXACT example prompt and mark it active. Purely fills the
   *  input — it NEVER auto-starts generation (button type="button", no submit,
   *  no navigation, no fetch); the normal Prompt-to-World pipeline runs only
   *  when the user explicitly presses "Generate case". */
  const onSelectExample = (id: ExamplePromptId) => {
    if (!isKnownExample(id)) return;
    const next = selectExamplePrompt({ prompt, activeExampleId }, id);
    setPrompt(next.prompt);
    setActiveExampleId(next.activeExampleId);
    setError(null);
  };

  /** The textarea stays fully editable after any example fill: every change
   *  is kept and deterministically clears the active mark once the text
   *  diverges from the loaded example (pure transition, no ad-hoc logic). */
  const onPromptChange = (text: string) => {
    const next = noteExamplePromptEdit({ prompt, activeExampleId }, text);
    setPrompt(next.prompt);
    setActiveExampleId(next.activeExampleId);
    setError(null);
  };

  /** Validate the prompt and stage the journey; false keeps the user on page. */
  const stageJourney = (text: string, level: JourneyDifficulty, demoCaseId?: string): boolean => {
    const validation = validatePrompt(text);
    if (!validation.ok || validation.trimmed === null) {
      setError(validation.error ?? "Describe a crime first — a sentence or two is enough.");
      return false;
    }
    // Phase 30 — an ACTIVE but INCOMPLETE Frontier selection must never stage:
    // the primary guard is the disabled Generate button; this is the
    // defense-in-depth gate (the same rule gates the example-case/demo CTA).
    if (!isFrontierSubmitReady(providerSelection, capabilities)) {
      setError(FRONTIER_SUBMIT_REQUIRED_MESSAGE);
      return false;
    }
    setError(null);
    const params: JourneyParams = { prompt: validation.trimmed, difficulty: level };
    // Phase 25 — carry the browser-selected generation provider into the
    // journey ONLY when the additive provider offer exists AND a selection was
    // resolved (transport/model travel only for the Ollama provider). The
    // pre-25 / older-server journey stays byte-identical (no selection keys).
    if (hasGenerationProviderOffer(capabilities) && providerSelection !== null) {
      params.generationProvider = providerSelection.generationProvider;
      if (providerSelection.generationProvider === "ollama") {
        if (providerSelection.ollamaTransport !== null) {
          params.ollamaTransport = providerSelection.ollamaTransport;
        }
        if (providerSelection.ollamaModel !== "") {
          params.ollamaModel = providerSelection.ollamaModel;
        }
      }
      // Phase 30 — a COMPLETE Frontier selection travels in-memory: the
      // trusted provider id + model (non-secret) and — inside JourneyParams
      // ONLY, never any persistence surface — the user's API key (§15/§24).
      if (providerSelection.generationProvider === "frontier") {
        if (
          typeof providerSelection.frontierProviderId === "string" &&
          providerSelection.frontierProviderId !== ""
        ) {
          params.frontierProviderId = providerSelection.frontierProviderId;
        }
        if (
          typeof providerSelection.frontierModel === "string" &&
          providerSelection.frontierModel.trim() !== ""
        ) {
          params.frontierModel = providerSelection.frontierModel.trim();
        }
        if (
          typeof providerSelection.frontierApiKey === "string" &&
          providerSelection.frontierApiKey !== ""
        ) {
          params.frontierApiKey = providerSelection.frontierApiKey;
        }
      }
    }
    // Phase 28 — the closed Demo fixture id travels ONLY on the demo path
    // (a fresh "Try Demo Case" roll); a generated-case submit passes no id.
    if (typeof demoCaseId === "string" && demoCaseId !== "") {
      params.demoCaseId = demoCaseId;
    }
    setJourneyParams(params);
    return true;
  };

  const handleSubmit = (event: { preventDefault: () => void }) => {
    event.preventDefault();
    if (stageJourney(prompt, difficulty)) {
      navigate("/generating");
    }
  };

  const startDemo = (): boolean => {
    // Phase 28 — the random three-fixture Demo pool applies ONLY when the
    // backend reports the demo-only allowlist (`demoCtaState === "demo"` —
    // the exact "Try Demo Case" state). rollDemoCaseId also pins the
    // selection for the whole session. On a local/live/unknown backend the
    // renamed example-case action keeps its byte-identical request (the
    // backend rejects demoCaseId for any non-fake provider).
    const demoCaseId = demoCtaState(capabilities) === "demo" ? rollDemoCaseId() : undefined;
    return stageJourney(examplePromptText(), difficulty, demoCaseId);
  };

  const handleDemoLink = (event: { preventDefault: () => void }) => {
    if (!startDemo()) {
      event.preventDefault();
      return;
    }
    // The stageJourney side-effect ran; the Link navigates to /generating.
  };

  return (
    <section className="page new-case">
      <h2>New Investigation</h2>
      <p className="new-case-intro" data-testid="generate-intro">
        Write your own detective scenario, then generate a complete,
        logically solvable investigation around it — every prompt produces its
        own world of suspects, evidence and red herrings.
      </p>
      <p className="new-case-provider-note" data-testid="generate-provider-note">
        {providerPathNoteFromCapabilities(capabilities)}
      </p>
      {/* Phase 16.2 §36 — the accurate Local-AI showcase sentence, shown ONLY
          while Local AI mode is ACTIVE (selected AND backend-available). The
          model proposes structured data; deterministic validators verify and
          construct — never a claim that the model proves the case. Frozen
          app copy: no DTO string can reach it. */}
      {localActive && (
        <p className="new-case-local-showcase" data-testid="local-ai-showcase-note">
          {LOCAL_AI_SHOWCASE_NOTE}
        </p>
      )}

      <form className="new-case-form" data-testid="prompt-form" onSubmit={handleSubmit} noValidate>
        <label htmlFor="prompt-input" className="new-case-label">
          Describe the crime
        </label>
        <textarea
          id="prompt-input"
          data-testid="prompt-input"
          className="prompt-input"
          placeholder="Describe a crime or paste an example…"
          rows={8}
          maxLength={PROMPT_MAX_CHARS}
          value={prompt}
          onChange={(event) => onPromptChange(event.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        <p className="prompt-char-count" data-testid="prompt-char-count">
          {prompt.length} / {PROMPT_MAX_CHARS}
        </p>

        {/* Phase 17D Bugfix PART B / Phase 17E PART J — the Easy/Medium/Hard
            example selector. Three pure UI text fills demonstrating increasing
            Prompt-to-World complexity. Each button is type="button": it only
            populates the textarea — it NEVER submits the form, never starts
            generation, and never adds backend/API logic. The redundant
            "Try an example:" heading and the redundant "Use example prompt"
            button were removed in Phase 17E PART J (the example prompts and
            this container remain, plugin-compatible). The active example is
            exposed via aria-pressed + the --active class, and the pure
            transitions in ../journey/examplePrompts clear it deterministically
            as soon as the user edits the text. */}
        <div className="new-case-examples" data-testid="example-prompts">
          {EXAMPLE_PROMPT_IDS.map((id) => {
            const entry = EXAMPLE_PROMPTS[id];
            const isActive = activeExampleId === id;
            return (
              <button
                key={id}
                type="button"
                data-testid={`example-${id}`}
                className={`new-case-example${isActive ? " new-case-example--active" : ""}`}
                aria-pressed={isActive}
                onClick={() => onSelectExample(id)}
              >
                <span className="new-case-example-label">{entry.label}</span>
                <span className="new-case-example-helper">{entry.helper}</span>
              </button>
            );
          })}
        </div>

        <label htmlFor="difficulty-select" className="new-case-label">
          Difficulty (optional)
        </label>
        <select
          id="difficulty-select"
          data-testid="difficulty-select"
          value={difficulty}
          onChange={(event) => setDifficulty(event.target.value as JourneyDifficulty)}
        >
          <option value="easy">Easy</option>
          <option value="medium">Medium</option>
          <option value="hard">Hard</option>
        </select>

        {/* Phase 25 — the browser-selectable AI Provider selector (see
            src/journey/GenerationProviderSelector.tsx). Rendered ONLY when
            the capability DTO carries the additive `providers[]`/`defaultProvider`
            offer (an OLDER server keeps the pre-25 form byte-identical). The
            resolved selection is carried into JourneyParams -> POST /cases and
            persisted to sessionStorage (the three non-secret preference keys,
            §10.1); unavailable providers stay visible but disabled with their
            safe reason (§2). No secret, URL, credential or raw exception text
            is ever rendered. */}
        <GenerationProviderSelector
          capabilities={capabilities}
          selection={providerSelection}
          disabled={false}
          onChange={onProviderSelectionChange}
        />

        {/* Phase 21 F-03 — generation-mode READ-ONLY display (the interactive
            selector was removed: the selected mode was never sent to the
            backend, whose provider is process-global). A demo-only backend
            shows "Demo mode active" + "Generation mode: Deterministic demo";
            a configured local/live provider shows its truthful read-only
            line (with an "Unavailable" tag while its probe is down —
            DEF-096); a DTO-unavailable state shows the neutral reachability
            line, never a provider claim (DEF-097). No host/IP, credentials,
            prompts or diagnostics are ever rendered — only frozen public
            labels — and no click changes the provider. */}
        <GenerationModeDisplay capabilities={capabilities} />

        {/* Phase 22 — BYO-Ollama pairing/status panel (PER-CREATOR /new ONLY).
            Gated on the capability DTO advertising remoteLocalAi.available
            (ENABLE_BRIDGE=true); when the block is absent (§36 default OFF)
            this renders NOTHING and the generation-mode section stays
            byte-identical to Phase 21B. The panel only ever talks to the app
            server with an anonymous session it creates itself — the browser
            never touches a local Ollama and never opens a bridge WebSocket
            (the bridge CLI owns that WS). Player routes (/scene, /accuse,
            /reveal) never render this component and never call the bridge
            endpoints — the backend enforces the same session scoping. */}
        <LocalAiBridgePanel
          capabilities={capabilities}
          onBridgePaired={handleBridgePaired}
          onBridgePairingStarted={handleBridgePairingStarted}
        />

        {/* Phase 16.2 §20 — honest unavailability (Phase 21 F-03: storage is
            legacy-only now — no user action writes `pd_generation_mode`, but
            a leftover `local` value from an older app version / the QA seam
            still surfaces the explicit note when the capabilities allowlist
            does not report local available: never a silent fallback to demo
            and never a pretend-local claim (the generation request would
            otherwise fail with the provider-unavailable reason from the
            backend). Keep Demo selectable — it remains the honest fallback. */}
        {localUnavailable && (
          <p className="new-case-local-unavailable" data-testid="local-ai-unavailable" role="status">
            Local AI is unavailable right now. Demo Mode remains available.
          </p>
        )}

        {error && (
          <p className="new-case-error" data-testid="prompt-error" role="alert">
            {error}
          </p>
        )}

        <div className="new-case-actions">
          {/* Phase 30 §5 — while an ACTIVE Frontier selection is incomplete
              (provider/key/model/cost ack) the Generate button is DISABLED:
              a BYOK request must never start without the full user
              configuration. The same readiness gate blocks stageJourney
              (the example-case CTA path) as defense-in-depth. */}
          <button
            type="submit"
            className="new-case-submit"
            data-testid="generate-case"
            disabled={!isFrontierSubmitReady(providerSelection, capabilities)}
          >
            Generate case
          </button>
        </div>
{/* ADV-152 — honest app-level provider qualifier next to the primary CTA
            (same wording + spot as the landing): the generate intro's per-path
            note stays untouched; this line makes the provider story unambiguous
            and is derived from the backend capability report (Phase 18A) — it
            can never claim a provider the backend does not run. */}
        <p className="provider-qualifier" data-testid="provider-qualifier">
          {providerQualifierFromCapabilities(capabilities)}
        </p>
      </form>

      <div className="new-case-demo">
        <Link data-testid="try-demo-from-new" to="/generating" onClick={handleDemoLink}>
          {demoCtaLabel(capabilities)}
        </Link>
        {/* Phase 21B Finding 3 — capability-validated note (demoCtaNote in
            src/journey/generationMode.ts): an explicit deterministic/no-cost
            promise ONLY for a KNOWN demo-only backend; a renamed + "not the
            free deterministic demo" per-mode line for a local/live backend; a
            provider-neutral line when the DTO is unavailable. */}
        <p className="new-case-demo-note" data-testid="try-demo-note">
          {demoCtaNote(capabilities)}
        </p>
      </div>
    </section>
  );
}