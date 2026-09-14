import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { setJourneyParams, type JourneyDifficulty } from "../journey/context";
import { PROMPT_MAX_CHARS } from "../journey/demoPrompt";
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
 */
export default function NewCasePage() {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [difficulty, setDifficulty] = useState<JourneyDifficulty>("medium");
  const [error, setError] = useState<string | null>(null);

  const useExample = () => {
    setPrompt(examplePromptText());
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
      <p className="new-case-intro">
        Describe a crime in your own words. The AI builds a complete,
        logically solvable investigation around it.
      </p>

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
          onChange={(event) => {
            setPrompt(event.target.value);
            setError(null);
          }}
          autoComplete="off"
          spellCheck={false}
        />
        <p className="prompt-char-count" data-testid="prompt-char-count">
          {prompt.length} / {PROMPT_MAX_CHARS}
        </p>

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
      </form>

      <p className="new-case-demo">
        <Link data-testid="try-demo-from-new" to="/generating" onClick={handleDemoLink}>
          Try the demo case
        </Link>
      </p>
    </section>
  );
}