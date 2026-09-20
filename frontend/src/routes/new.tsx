import { useState } from "react";
import { Link, useNavigate } from "react-router";
import type { GenerationCapabilitiesResponse, GenerationModeId } from "../api/types";
import { useGenerationCapabilities } from "../hooks/useGenerationCapabilities";
import { setJourneyParams, type JourneyDifficulty } from "../journey/context";
import { PROMPT_MAX_CHARS } from "../journey/demoPrompt";
import {
  EXAMPLE_PROMPTS,
  EXAMPLE_PROMPT_IDS,
  isKnownExample,
  noteExamplePromptEdit,
  selectExamplePrompt,
  type ExamplePromptId,
} from "../journey/examplePrompts";
import { getGenerationMode, isLocalModeAvailable, LOCAL_AI_SHOWCASE_NOTE, setGenerationMode } from "../journey/generationMode";
import { GenerationModeSelector } from "../journey/generationModeSelector";
import { APP_PROVIDER_MODE, providerPathNote, providerQualifier } from "../journey/providerMode";
import { examplePromptText, validatePrompt } from "../journey/promptValidation";

/**
 * "/new" — the prompt-to-case screen (Phase 8 B, REQUIREMENTS 3.1).
 *
 * A natural-language detective scenario becomes a generated investigation:
 * the user types (or fills) a bounded prompt, optionally picks a difficulty
 * label, and presses "Generate case". Validation is minimal and safe (non
 * empty, <= 4000 chars); the accepted journey is handed to /generating
 * through the in-memory journey context — no prompt/token ever enters the
 * URL. "Try the demo case" starts the same deterministic demo journey with
 * the REQUIREMENTS 48 example prompt.
 *
 * Phase 15 Track B — the two paths are clearly labelled here too: this form
 * IS the "Generate a New Mystery" path (custom prompt emphasised, with a
 * provider-honest note: the deterministic generator in the default build,
 * "Live AI provider" only when VITE_APP_PROVIDER=live), while the demo link
 * carries its own zero-cost/deterministic sub-note.
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
   * unit-tested without a DOM.
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
  const [mode, setMode] = useState<GenerationModeId>(() =>
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
   * Phase 16.2 §20/§36 — mode-honesty flags. The UI only ever claims Local-AI
   * behavior when the backend allowlist reports local available (the selector
   * never offers an unavailable mode). A stored/selected `local` mode with an
   * unavailable/absent/down backend must surface the explicit "Local AI is
   * unavailable" note instead of silently pretending local is active — and it
   * must NOT claim the §36 showcase pipeline while unavailable (Demo remains
   * the honest fallback choice). While capabilities are unknown (null) no
   * claim and no note is rendered.
   */
  const localActive =
    mode === "local" && capabilities !== null && isLocalModeAvailable(capabilities);
  const localUnavailable =
    mode === "local" && capabilities !== null && !isLocalModeAvailable(capabilities);

  const selectMode = (next: GenerationModeId) => {
    setMode(next);
    setGenerationMode(next);
  };

  /** "Use example prompt" fills the REQUIREMENTS 48 Case A demo prompt — NOT
   *  one of the three showcase examples, so any active example mark clears. */
  const useExample = () => {
    setPrompt(examplePromptText());
    setActiveExampleId(null);
    setError(null);
  };

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
  const stageJourney = (text: string, level: JourneyDifficulty): boolean => {
    const validation = validatePrompt(text);
    if (!validation.ok || validation.trimmed === null) {
      setError(validation.error ?? "Describe a crime first — a sentence or two is enough.");
      return false;
    }
    setError(null);
    setJourneyParams({ prompt: validation.trimmed, difficulty: level });
    return true;
  };

  const handleSubmit = (event: { preventDefault: () => void }) => {
    event.preventDefault();
    if (stageJourney(prompt, difficulty)) {
      navigate("/generating");
    }
  };

  const startDemo = (): boolean => stageJourney(examplePromptText(), difficulty);

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
        {providerPathNote(APP_PROVIDER_MODE)}
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

        {/* Phase 17D Bugfix PART B — compact "Try an example:" selector. Three
            pure UI text fills demonstrating increasing Prompt-to-World
            complexity (Easy/Medium/Hard). Each button is type="button": it
            only populates the textarea — it NEVER submits the form, never
            starts generation, and never adds backend/API logic. The active
            example is exposed via aria-pressed + the --active class, and the
            pure transitions in ../journey/examplePrompts clear it
            deterministically as soon as the user edits the text. */}
        <div className="new-case-examples" data-testid="example-prompts">
          <p className="new-case-examples-heading" data-testid="example-prompts-heading">
            Try an example:
          </p>
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

        {/* Phase 16 Track B — generation-mode selector: always offers Demo and
            additionally Local AI / Cloud AI only when the backend reports them
            available; a demo-only backend is shown as the static
            "Demo mode active" notice instead. No host/IP, credentials, prompts
            or diagnostics are ever rendered — only frozen public labels. */}
        <GenerationModeSelector capabilities={capabilities} value={mode} onSelect={selectMode} />

        {/* Phase 16.2 §20 — honest unavailability. Local was STORED or freshly
            selected, but the capabilities allowlist does not report local
            available (backend down/unselected): show the explicit note and
            keep Demo selectable — never a silent fallback to demo and never a
            pretend-local claim (the generation request would otherwise fail
            with the provider-unavailable reason from the backend). */}
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
          <button type="submit" className="new-case-submit" data-testid="generate-case">
            Generate case
          </button>
          <button type="button" className="new-case-secondary" data-testid="use-example-prompt" onClick={useExample}>
            Use example prompt
          </button>
        </div>
        {/* ADV-152 — honest app-level provider qualifier next to the primary CTA
            (same wording + spot as the landing): the generate intro's per-path
            note stays untouched; this line makes the default deterministic
            build's "AI" claim unambiguous (mode-aware, config-driven). */}
        <p className="provider-qualifier" data-testid="provider-qualifier">
          {providerQualifier(APP_PROVIDER_MODE)}
        </p>
      </form>

      <div className="new-case-demo">
        <Link data-testid="try-demo-from-new" to="/generating" onClick={handleDemoLink}>
          Try the demo case
        </Link>
        <p className="new-case-demo-note" data-testid="try-demo-note">
          Deterministic demo — no API keys, no cost.
        </p>
      </div>
    </section>
  );
}