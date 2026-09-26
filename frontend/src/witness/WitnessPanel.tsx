import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import type { WitnessListEntryDTO, WitnessQuestionType, WitnessStatementDTO } from "../api/types";
import type { WitnessAskOutcome } from "../scene/investigationFlow";
import {
  boundedText,
  MAX_WITNESS_DISPLAY_NAME,
  observationTimeView,
  WITNESS_QUESTION_LABELS,
  WITNESS_QUESTION_ORDER,
} from "./witnessModel";

/**
 * Phase 23 — the witness interview panel (frontend/src/witness/).
 *
 * A modal, labelled `role="dialog"` surface that shows:
 *   - the witness displayName + the "Witness" role line;
 *   - the SIX closed question buttons as real `<button>`s with HUMAN labels
 *     (the enum token is never rendered);
 *   - after a question: the deterministic player-safe statement (summary +
 *     observations) rendered exactly like Phase 19G text-only — timestamps use
 *     semantic `<time dateTime=...>` with compact HH:mm display — plus an
 *     "Ask another question" return button;
 *   - the session's asked-state (idempotent re-ask: re-selecting an answered
 *     question shows the cached statement — no POST, no duplicate);
 *   - Close as a real button; the scene route wires Escape.
 *
 * Accessibility (Phase23 §35): the panel is correctly labelled, focus moves
 * into it (autoFocus on the Close button) and returns to the activating
 * control when it closes. Hostile names/statements are untrusted text —
 * rendered ONLY through React's default string escaping (no
 * dangerouslySetInnerHTML / innerHTML).
 */
export interface WitnessPanelProps {
  /** One player-safe witness entry (id, displayName, presence). */
  witness: WitnessListEntryDTO;
  /** Question types already answered this session (rendered as answered). */
  askedQuestions: readonly WitnessQuestionType[];
  /** Cached statements per question (idempotent re-ask, no duplicate state). */
  statementCache: ReadonlyMap<WitnessQuestionType, WitnessStatementDTO>;
  /** Ask handler wired by the route to the InvestigationSession. */
  onAsk: (questionType: WitnessQuestionType) => Promise<WitnessAskOutcome>;
  /** Close handler (the route restores the previous UI state). */
  onClose: () => void;
}

/** One displayed answer (question + the deterministic statement). */
interface AnswerView {
  questionType: WitnessQuestionType;
  statement: WitnessStatementDTO;
}

export default function WitnessPanel({ witness, askedQuestions, statementCache, onAsk, onClose }: WitnessPanelProps): ReactElement {
  const [answer, setAnswer] = useState<AnswerView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Focus return: capture the activating control on the FIRST render (before
  // autoFocus moves it), then restore it when the panel unmounts.
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

  const handleAsk = async (questionType: WitnessQuestionType): Promise<void> => {
    if (busy) return;
    const cached = statementCache.get(questionType);
    if (cached !== undefined) {
      // Idempotent re-ask: the session already answered this question — show
      // the SAME statement without a POST and without re-opening evidence.
      setAnswer({ questionType, statement: cached });
      setError(null);
      return;
    }
    setBusy(true);
    setError(null);
    const outcome: WitnessAskOutcome = await onAsk(questionType);
    setBusy(false);
    if (outcome.ok) {
      setAnswer({ questionType, statement: outcome.statement });
    } else {
      setError(outcome.error.message);
    }
  };

  const name = boundedText(witness.displayName, MAX_WITNESS_DISPLAY_NAME);
  const askedSet = new Set(askedQuestions);

  return (
    <aside
      className="witness-panel"
      data-testid="witness-panel"
      role="dialog"
      aria-modal="true"
      aria-label={`Witness interview: ${name}`}
    >
      <header className="witness-panel-header">
        <div className="witness-panel-identity">
          <h3 className="witness-panel-name" data-testid="witness-panel-name">
            {name}
          </h3>
          <p className="witness-panel-role" data-testid="witness-panel-role">
            Witness
          </p>
        </div>
        <button
          type="button"
          className="witness-close"
          data-testid="witness-close"
          onClick={onClose}
          aria-label={`Close interview with ${name}`}
          autoFocus
        >
          Close
        </button>
      </header>

      {error !== null && (
        <p className="witness-error" role="alert" data-testid="witness-error">
          {error}
        </p>
      )}

      {answer === null ? (
        <div className="witness-questions" data-testid="witness-questions" aria-busy={busy}>
          <p className="witness-questions-hint">Choose a question to ask.</p>
          <ul className="witness-question-list">
            {WITNESS_QUESTION_ORDER.map((questionType) => {
              const asked = askedSet.has(questionType);
              return (
                <li key={questionType} className="witness-question-item">
                  <button
                    type="button"
                    className={asked ? "witness-question-button witness-question-button--asked" : "witness-question-button"}
                    data-testid={`witness-question-${questionType}`}
                    disabled={busy}
                    aria-pressed={asked}
                    onClick={() => void handleAsk(questionType)}
                  >
                    {WITNESS_QUESTION_LABELS[questionType]}
                  </button>
                  {asked && (
                    <span
                      className="witness-question-asked"
                      data-testid={`witness-question-${questionType}-asked`}
                    >
                      {" "}
                      · answered
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <div className="witness-answer" data-testid="witness-answer">
          <h4 className="witness-answer-question" data-testid="witness-answer-question">
            {WITNESS_QUESTION_LABELS[answer.questionType]}
          </h4>
          <p className="witness-statement-summary" data-testid="witness-statement-summary">
            {answer.statement.summary}
          </p>
          {answer.statement.observations.length > 0 && (
            <ul className="witness-observations" data-testid="witness-observations">
              {answer.statement.observations.map((observation, index) => {
                const time = observationTimeView(observation.time);
                return (
                  <li key={`observation-${index}`} className="witness-observation">
                    {time.canonical !== null ? (
                      <time
                        className="witness-observation-time"
                        dateTime={time.canonical}
                        data-testid={`witness-observation-time-${index}`}
                      >
                        {time.display}
                      </time>
                    ) : null}
                    <span className="witness-observation-text">{observation.text}</span>
                  </li>
                );
              })}
            </ul>
          )}
          <button
            type="button"
            className="witness-ask-another"
            data-testid="witness-ask-another"
            onClick={() => {
              setAnswer(null);
              setError(null);
            }}
          >
            Ask another question
          </button>
        </div>
      )}
    </aside>
  );
}