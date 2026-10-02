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
 * Phase 26 (FixUI-A) — the witness interview panel (frontend/src/witness/).
 *
 * A labelled `role="dialog"` surface with TWO stable vertical regions inside
 * one flex-column container — never two competing layers:
 *   - the upper QUESTIONS region: the SIX closed question buttons as real
 *     `<button>`s with HUMAN labels (the enum token is never rendered). It is
 *     ALWAYS visible and ALWAYS interactive — an open answer never replaces
 *     or covers it;
 *   - the lower ANSWER region: a single DERIVED statement (summary +
 *     observations, timestamps as semantic `<time dateTime=...>` with compact
 *     HH:mm display) for the CURRENT selection — or a small neutral hint
 *     before the player picks a question.
 *
 * State model is SINGLE-SELECTION: `selectedQuestionId` plus a one-slot
 * `latestStatement` for the still-in-flight POST outcome. The displayed
 * answer is derived from `selectedQuestionId` + `statementCache` (cached
 * re-ask) + the latest non-cached `onAsk` outcome (before the session cache
 * has propagated back through the route). There is NO statement history and
 * only ONE answer region can ever exist: clicking question B while A is shown
 * replaces the answer outright — no close/return step exists in the answer.
 *
 * Ask-chain state machine (see `handleAsk` / `fireAsk` / `drainQueue`):
 * exactly ONE question POST may be in flight at a time (`busy`/`busyRef`
 * slot). A click on an UNCACHED question claims the slot immediately; a click
 * that arrives WHILE the slot is busy records the selection in a FIFO
 * pending queue (`pendingQueueRef`) instead of being silently swallowed, and
 * `drainQueue` fires the queued asks one at a time the moment each earlier
 * POST resolves — so clicking question B while A's POST is in flight shows
 * B's answer WITHOUT a second user click (Phase 26 §4 / §15D). A cached
 * click still wins instantly (cache check stays BEFORE the busy gate: zero
 * POSTs, immediate display). `askedRef` records every question whose POST
 * fired during THIS witness session, so the chain can never fire a duplicate
 * POST while the session cache is still propagating back through the route
 * (idempotency / "first ask = one POST").
 *
 * Gameplay semantics are unchanged: the session's asked-state stays
 * idempotent (re-selecting an answered question shows the CACHED statement —
 * zero POSTs, zero duplicate discovery), and re-renders never re-fire
 * `onAsk`. A seq + stale-response guard discards an outcome that resolves
 * after the selection moved to another question (rapid switching may leave a
 * POST in flight; a later click wins) — a late outcome NEVER overwrites the
 * current answer, NEVER clears a newer request's busy and NEVER displays a
 * statement for an unasked question. The `witness.witnessId` change
 * invalidates any in-flight ask and resets selection/busy/queue/asked state
 * (the route may re-render this SAME instance for a different witness —
 * Emily's answer must never carry into Thomas' panel).
 *
 * Accessibility: the panel is correctly labelled, focus moves into it
 * (autoFocus on the header Close) and returns to the activating control when
 * it unmounts. The selected question carries `aria-current="true"` + a
 * visible `.witness-question-button--selected` class; the ASKED state keeps
 * its `aria-pressed="true"` semantics. The answer region has a meaningful
 * heading and an `aria-live="polite"` container (the region replaces its
 * content in place — one announced change per switch, no noisy duplicates).
 * Hostile names/statements remain untrusted text — rendered ONLY through
 * React's default string escaping (no dangerouslySetInnerHTML / innerHTML).
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

/** The one not-yet-propagated statement a fresh `onAsk` delivered (single
 *  slot — never a collection; the session cache takes over on the next
 *  parent render). */
interface LatestStatement {
  questionType: WitnessQuestionType;
  statement: WitnessStatementDTO;
}

export default function WitnessPanel({ witness, askedQuestions, statementCache, onAsk, onClose }: WitnessPanelProps): ReactElement {
  /**
   * Phase 26 — the SINGLE SELECTION. Only its question identity is stored;
   * the displayed statement is DERIVED below from `statementCache` + the
   * single `latestStatement` outcome slot. `null` shows the neutral hint.
   */
  const [selectedQuestionId, setSelectedQuestionId] = useState<WitnessQuestionType | null>(null);
  const [latestStatement, setLatestStatement] = useState<LatestStatement | null>(null);
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

  /**
   * Request token: bumped every time a NEW ask POST is registered AND on every
   * witness switch. An `onAsk` outcome may only touch state when its token is
   * still current — otherwise the resolution is for an abandoned request and
   * must not even clear `busy` (a newer request / a fresh witness owns it).
   */
  const requestSeqRef = useRef(0);
  /** Live mirror of the selection for stale-response checks at promise time. */
  const selectedQuestionIdRef = useRef<WitnessQuestionType | null>(null);
  selectedQuestionIdRef.current = selectedQuestionId;
  /**
   * ASK-CHAIN refs (live across renders — the outcome continuations and the
   * click handlers must read FRESH values, never a stale closure):
   *   - `statementCacheRef` mirrors the freshest `statementCache` prop, so the
   *     chain always skips questions the session cache now serves (zero-POST
   *     cached re-ask) even when a POST resolved BEFORE the parent render
   *     landed;
   *   - `busyRef` is the authoritative single-POST slot lock. `fireAsk`
   *     claims it synchronously before its first `await`, so even same-batch
   *     rapid clicks can never start two POSTs;
   *   - `pendingQueueRef` is the FIFO of uncached selections clicked while the
   *     slot was busy — each is asked EXACTLY ONCE, in click order, the moment
   *     the slot frees (a later selection never silently sits highlighted with
   *     no answer and no pending work);
   *   - `askedRef` records every question whose POST already fired during
   *     THIS witness session — the "no replay" guard while the session cache
   *     is still propagating back through the route.
   */
  const statementCacheRef = useRef(statementCache);
  statementCacheRef.current = statementCache;
  const busyRef = useRef(false);
  const pendingQueueRef = useRef<WitnessQuestionType[]>([]);
  const askedRef = useRef<Set<WitnessQuestionType>>(new Set());

  /**
   * Phase 26 — witness switch reset. The route may re-render this SAME panel
   * instance with a new `witness`; every previous witness's selection state
   * (and any in-flight ask) must be discarded so no stale answer carries over.
   */
  useEffect(() => {
    requestSeqRef.current += 1; // invalidate any in-flight ask of the previous witness
    busyRef.current = false; // the previous witness's POST no longer owns the slot
    pendingQueueRef.current = [];
    askedRef.current = new Set();
    setSelectedQuestionId(null);
    setLatestStatement(null);
    setBusy(false);
    setError(null);
  }, [witness.witnessId]);

  /** Remember one uncached selection whose ask still must happen (deduped:
   *  a question that is in flight / already POSTed, or already in the queue,
   *  is never queued twice). */
  const enqueueAsk = (questionType: WitnessQuestionType): void => {
    if (askedRef.current.has(questionType)) return; // in flight or asked — never replay
    const queue = pendingQueueRef.current;
    if (queue.includes(questionType)) return; // already waiting for this ask
    queue.push(questionType);
  };

  /**
   * Claim the FREE POST slot for `questionType` and run the one-at-a-time
   * ask chain. The caller guarantees the slot is free; `fireAsk` claims it
   * synchronously (before the first `await`) so exactly ONE POST is ever in
   * flight.
   */
  const fireAsk = async (questionType: WitnessQuestionType): Promise<void> => {
    busyRef.current = true;
    setBusy(true);
    askedRef.current.add(questionType); // first ask of this session = one POST
    const seq = ++requestSeqRef.current;
    try {
      const outcome = await onAsk(questionType);
      if (seq !== requestSeqRef.current) return; // superseded — a newer POST / a new witness owns the slot
      busyRef.current = false;
      setBusy(false);
      // Stale-response guard: only the CURRENT selection's outcome may touch
      // the display. A late outcome for an ABANDONED selection is silently
      // discarded (it stays recorded in askedRef, so it is never re-fired and
      // never displayed for an unasked question).
      if (selectedQuestionIdRef.current === questionType) {
        if (outcome.ok) {
          setLatestStatement({ questionType, statement: outcome.statement });
        } else {
          setError(outcome.error.message);
        }
      }
      drainQueue();
    } catch (askError) {
      if (seq !== requestSeqRef.current) return; // superseded request — drop entirely
      busyRef.current = false;
      setBusy(false); // the timeout/API error ALWAYS frees the slot — busy never sticks
      if (selectedQuestionIdRef.current === questionType) {
        setError(askError instanceof Error ? askError.message : "That question could not be answered.");
      }
      drainQueue();
    }
  };

  /**
   * Ref-scan / queue drain: while the POST slot is free, fire the queued
   * asks one at a time. Skips entries the session cache now serves (the
   * display derives from the cache — zero POSTs) and entries another ask
   * already covered. Called after every outcome resolution; the loop can
   * fire at most one POST per synchronous block because `fireAsk` immediately
   * re-locks the slot.
   */
  const drainQueue = (): void => {
    while (!busyRef.current) {
      const next = pendingQueueRef.current.shift();
      if (next === undefined) return;
      if (statementCacheRef.current.get(next) !== undefined) continue; // cached re-ask — zero POSTs
      if (askedRef.current.has(next)) continue; // already asked this session
      void fireAsk(next);
    }
  };

  const handleAsk = (questionType: WitnessQuestionType): void => {
    // A later click ALWAYS wins the selection (single-selection model).
    setSelectedQuestionId(questionType);
    setError(null);
    // The cache re-ask stays BEFORE the busy gate: a cached question renders
    // its answered statement IMMEDIATELY, even while another POST is in
    // flight — zero POSTs, no duplicate state.
    const cached = statementCacheRef.current.get(questionType);
    if (cached !== undefined) {
      return;
    }
    if (busyRef.current) {
      // The single POST slot is busy. Remember this selection so its ask
      // fires the moment the slot frees — its answer appears with NO second
      // click, and the selection is never left highlighted with no answer
      // and no pending work.
      enqueueAsk(questionType);
      return;
    }
    void fireAsk(questionType);
  };

  // DERIVE the single displayed statement from the selection + the session
  // cache + the one-slot latest outcome. The session cache is authoritative;
  // `latestStatement` only bridges the single render before the route's
  // `setWitnessRevision` propagation lands.
  let displayStatement: WitnessStatementDTO | null = null;
  if (selectedQuestionId !== null) {
    const cached = statementCache.get(selectedQuestionId);
    if (cached !== undefined) {
      displayStatement = cached;
    } else if (latestStatement !== null && latestStatement.questionType === selectedQuestionId) {
      displayStatement = latestStatement.statement;
    }
  }

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

      {/* Upper region — the questions stay PERMANENTLY visible and clickable,
          even while an answer is displayed below them. */}
      <section
        className="witness-questions"
        data-testid="witness-questions"
        aria-busy={busy}
        aria-label="Interview questions"
      >
        <p className="witness-questions-hint">Choose a question to ask.</p>
        <ul className="witness-question-list">
          {WITNESS_QUESTION_ORDER.map((questionType) => {
            const asked = askedSet.has(questionType);
            const selected = selectedQuestionId === questionType;
            const className = [
              "witness-question-button",
              asked ? "witness-question-button--asked" : null,
              selected ? "witness-question-button--selected" : null,
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <li key={questionType} className="witness-question-item">
                <button
                  type="button"
                  className={className}
                  data-testid={`witness-question-${questionType}`}
                  aria-pressed={asked}
                  aria-current={selected ? "true" : undefined}
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
      </section>

      {/* Lower region — the SINGLE derived answer, in the panel's normal flow.
          It is a flex sibling of the questions region (never an overlay card). */}
      <section
        className="witness-answer-section"
        data-testid="witness-answer-section"
        aria-labelledby="witness-answer-heading"
        aria-live="polite"
      >
        <h4 className="witness-answer-heading" id="witness-answer-heading" data-testid="witness-answer-heading">
          Answer
        </h4>
        {selectedQuestionId !== null && displayStatement !== null ? (
          <div className="witness-answer" data-testid="witness-answer">
            <h4 className="witness-answer-question" data-testid="witness-answer-question">
              {WITNESS_QUESTION_LABELS[selectedQuestionId]}
            </h4>
            <p className="witness-statement-summary" data-testid="witness-statement-summary">
              {displayStatement.summary}
            </p>
            {displayStatement.observations.length > 0 && (
              <ul className="witness-observations" data-testid="witness-observations">
                {displayStatement.observations.map((observation, index) => {
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
          </div>
        ) : (
          <p className="witness-answer-hint" data-testid="witness-answer-hint">
            Select a question to view the witness's answer.
          </p>
        )}
      </section>
    </aside>
  );
}