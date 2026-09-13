import type { AccusationCandidatesDTO, RevealResponse } from "../api/types";
import { revealPresentation } from "./revealFormat";

/**
 * End-of-case reveal screen (Phase 7 L).
 *
 * PURE DTO-driven: this component renders ONLY `revealPresentation`, which is
 * derived from a validated RevealResponse (parsed by revealValidation, unknown
 * wire fields dropped). Nothing is hardcoded — the murderer/motive/weapon/time
 * shown are whatever the reveal DTO's allowlist carries.
 *
 * Security posture:
 *   - every value is rendered with React's default string rendering (inert;
 *     no dangerouslySetInnerHTML, no eval, no HTML construction);
 *   - outcome strings ("Correct"/"Incorrect") are static app copy chosen by
 *     the server-computed boolean flags — no generated code paths;
 *   - candidate text (names, labels) is ALWAYS plain text and may never be
 *     interpreted as markup.
 */
export interface RevealScreenProps {
  reveal: RevealResponse;
  /** Player-safe candidates (bootstrap) — optional; used only to resolve the player's submitted ids to names. */
  candidates?: AccusationCandidatesDTO | null;
}

export default function RevealScreen({ reveal, candidates = null }: RevealScreenProps) {
  const model = revealPresentation(reveal, candidates);
  const solved = model.overall === "solved";
  const score = `${model.score.correctDimensions} / ${model.score.totalDimensions}`;

  return (
    <section className="reveal-screen" data-testid="reveal-screen" aria-label="Case reveal">
      <h2 className="reveal-heading" data-testid="reveal-heading">
        THE TRUTH
      </h2>
      <p className={`reveal-overall reveal-overall--${solved ? "solved" : "incorrect"}`} data-testid="reveal-overall">
        {solved ? "CASE SOLVED — every dimension of your accusation was correct." : "CASE NOT SOLVED — here is the immutable truth of the case."}
      </p>
      <p className="reveal-score" data-testid="reveal-score">
        Score: <strong>{score}</strong> correct dimensions
      </p>

      <div className="reveal-truth" data-testid="reveal-truth">
        <h3>The truth</h3>
        <dl className="reveal-truth-list">
          <TruthRow label="Murderer" value={model.truth.murderer} dataTestId="reveal-truth-murderer" />
          <TruthRow label="Motive" value={model.truth.motive} dataTestId="reveal-truth-motive" />
          <TruthRow label="Weapon" value={model.truth.weapon} dataTestId="reveal-truth-weapon" />
          <TruthRow label="Crime time" value={model.truth.crimeTime} dataTestId="reveal-truth-time" />
        </dl>
      </div>

      <div className="reveal-accusation" data-testid="reveal-accusation">
        <h3>Your accusation</h3>
        <p className="reveal-accusation-note">
          Your submitted answers are shown against the truth below.
        </p>
        {model.dimensions.map((row) => (
          <div
            className="reveal-dimension"
            data-testid={`reveal-dimension-${row.dimension.toLowerCase()}`}
            key={row.dimension}
          >
            <h4>{row.label}</h4>
            <p>
              Truth: <span data-testid="reveal-dimension-truth">{row.truth}</span>
            </p>
            <p>
              Your accusation: <span data-testid="reveal-dimension-submitted">{row.submitted}</span>
            </p>
            <p
              className={row.correct ? "reveal-result-correct" : "reveal-result-incorrect"}
              data-testid="reveal-dimension-result"
            >
              {row.correct ? "Correct" : "Incorrect"}
            </p>
          </div>
        ))}
      </div>

      <div className="reveal-explanation" data-testid="reveal-explanation">
        <h3>How the case is proven</h3>
        {model.explanation.length === 0 ? (
          <p>No explanation evidence was published for this case.</p>
        ) : (
          <ul className="reveal-explanation-list">
            {model.explanation.map((item, index) => (
              <li className="reveal-explanation-item" key={index}>
                <strong>{item.title}</strong>
                {item.point !== "" ? <span> — {item.point}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="reveal-timeline" data-testid="reveal-timeline">
        <h3>Timeline</h3>
        {model.timeline.length === 0 ? (
          <p>No timeline was published for this case.</p>
        ) : (
          <ol className="reveal-timeline-list">
            {model.timeline.map((entry, index) => (
              <li className="reveal-timeline-entry" key={index} data-testid="reveal-timeline-entry">
                <time>{entry.time}</time>
                <span aria-hidden="true"> — </span>
                <span>{entry.description}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}

function TruthRow({ label, value, dataTestId }: { label: string; value: string; dataTestId: string }) {
  return (
    <div className="reveal-truth-row">
      <dt>{label}</dt>
      <dd data-testid={dataTestId}>{value}</dd>
    </div>
  );
}