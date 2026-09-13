import type { AccusationCandidatesDTO } from "../api/types";
import { AccusationFlow } from "./accusationFlow";

/**
 * Accusation panel (Phase 7 K).
 *
 * Pickers for WHO / WHY / WEAPON / WHEN built ONLY from the player-safe
 * `candidates` of the investigation bootstrap:
 *   - candidates render in the EXACT order the server returned them (the
 *     server sorts suspects alphabetically; this client MUST NOT re-order or
 *     mark a winner — there is no `correct`/`winner` anywhere in the model);
 *   - WHEN is a 24h time-of-day input collecting "HH:MM" — the flow submits
 *     it as "HH:MM:00" (no date: the date is hidden truth).
 *
 * Flow: editing -> confirmation -> submitting -> accepted/already-submitted.
 * The confirmation step is mandatory before the irreversible submit; the
 * submit button disables while in flight and the controller ignores a second
 * activation (double-submit guard). Keyboard accessible: every control has a
 * real label and Enter submits when the form is valid. All candidate and
 * error text renders through React's default escaping (inert).
 */
export interface AccusationPanelProps {
  flow: AccusationFlow;
  /** Navigate away to the reveal (accepted or already-submitted states). */
  onReveal: () => void;
  /** Clear the stored credential after a 401/403 (token reset affordance). */
  onResetToken: () => void;
}

export default function AccusationPanel({ flow, onReveal, onResetToken }: AccusationPanelProps) {
  const { phase, lastFieldErrors: fieldErrors, serverError, tokenInvalid } = flow;
  const candidates = flow.candidatesSnapshot;
  const confirming = phase === "confirming" || phase === "submitting";
  const submitting = phase === "submitting";

  const handleSubmit = (event: { preventDefault: () => void }) => {
    event.preventDefault();
    if (phase === "editing") {
      flow.openConfirmation();
    } else if (phase === "confirming") {
      void flow.confirmAccusation();
    }
  };

  return (
    <section className="accusation-panel" data-testid="accusation-panel" aria-label="Accusation">
      {phase === "accepted" && (
        <div className="accusation-accepted" data-testid="accusation-accepted" role="status">
          <h3>Accusation accepted</h3>
          <p>Your accusation is on file for this case. You may now reveal the case.</p>
          <button type="button" data-testid="reveal-case" onClick={onReveal}>
            Reveal the case
          </button>
        </div>
      )}

      {phase === "already-submitted" && (
        <div className="accusation-already-submitted" data-testid="accusation-already-submitted" role="status">
          <h3>This case has already been submitted</h3>
          <p>An accusation is already on file for this playthrough. Reveal the case to see the truth.</p>
          <button type="button" data-testid="reveal-case" onClick={onReveal}>
            Reveal the case
          </button>
        </div>
      )}

      {(phase === "editing" || confirming) && (
        <form className="accusation-form" data-testid="accusation-form" onSubmit={handleSubmit} noValidate>
          <h3>{confirming ? "Confirm your accusation" : "Make an accusation"}</h3>

          {confirming && (
            <ConfirmationSummary flow={flow} candidates={candidates} />
          )}

          {!confirming && (
            <>
              <SuspectPicker flow={flow} candidates={candidates} fieldError={fieldErrors.murdererId} />
              <MotivePicker flow={flow} candidates={candidates} fieldError={fieldErrors.motiveId} />
              <WeaponPicker flow={flow} candidates={candidates} fieldError={fieldErrors.weaponId} />
              <WhenPicker flow={flow} fieldError={fieldErrors.crimeTime} />
            </>
          )}

          {serverError && (
            <div className="accusation-server-error" data-testid="accusation-server-error" role="alert">
              <p>{serverError}</p>
              {tokenInvalid && (
                <button type="button" data-testid="accusation-reset-token" onClick={onResetToken}>
                  Reset playthrough access
                </button>
              )}
            </div>
          )}

          <div className="accusation-actions">
            {confirming && (
              <button
                type="button"
                className="accusation-edit"
                data-testid="accusation-edit"
                onClick={() => flow.cancelConfirmation()}
                disabled={submitting}
              >
                Edit answers
              </button>
            )}
            <button
              type="submit"
              className="accusation-submit"
              data-testid={confirming ? "accusation-confirm" : "accusation-submit"}
              disabled={submitting}
            >
              {confirming ? (submitting ? "Submitting…" : "Confirm accusation") : "Make accusation"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function SuspectPicker({
  flow,
  candidates,
  fieldError,
}: {
  flow: AccusationFlow;
  candidates: AccusationCandidatesDTO;
  fieldError: string | undefined;
}) {
  return (
    <fieldset className="accusation-fieldset" data-testid="accusation-suspects">
      <legend>
        <strong>WHO</strong> — who is the murderer?
      </legend>
      {candidates.suspects.map((candidate) => (
        <label className="accusation-choice" key={candidate.id}>
          <input
            type="radio"
            name="murdererId"
            value={candidate.id}
            checked={flow.currentSelection.murdererId === candidate.id}
            onChange={() => flow.selectSuspect(candidate.id)}
            data-testid={`accusation-option-murdererId-${candidate.id}`}
          />
          <span>{candidate.name}</span>
        </label>
      ))}
      {fieldError && (
        <p className="accusation-field-error" data-testid="accusation-field-error-murdererId" role="alert">
          {fieldError}
        </p>
      )}
    </fieldset>
  );
}

function MotivePicker({
  flow,
  candidates,
  fieldError,
}: {
  flow: AccusationFlow;
  candidates: AccusationCandidatesDTO;
  fieldError: string | undefined;
}) {
  return (
    <fieldset className="accusation-fieldset" data-testid="accusation-motives">
      <legend>
        <strong>WHY</strong> — what was the motive?
      </legend>
      {candidates.motives.map((candidate) => (
        <label className="accusation-choice" key={candidate.id}>
          <input
            type="radio"
            name="motiveId"
            value={candidate.id}
            checked={flow.currentSelection.motiveId === candidate.id}
            onChange={() => flow.selectMotive(candidate.id)}
            data-testid={`accusation-option-motiveId-${candidate.id}`}
          />
          <span>{candidate.label}</span>
        </label>
      ))}
      {fieldError && (
        <p className="accusation-field-error" data-testid="accusation-field-error-motiveId" role="alert">
          {fieldError}
        </p>
      )}
    </fieldset>
  );
}

function WeaponPicker({
  flow,
  candidates,
  fieldError,
}: {
  flow: AccusationFlow;
  candidates: AccusationCandidatesDTO;
  fieldError: string | undefined;
}) {
  return (
    <fieldset className="accusation-fieldset" data-testid="accusation-weapons">
      <legend>
        <strong>WEAPON</strong> — what was the murder weapon?
      </legend>
      {candidates.weapons.map((candidate) => (
        <label className="accusation-choice" key={candidate.id}>
          <input
            type="radio"
            name="weaponId"
            value={candidate.id}
            checked={flow.currentSelection.weaponId === candidate.id}
            onChange={() => flow.selectWeapon(candidate.id)}
            data-testid={`accusation-option-weaponId-${candidate.id}`}
          />
          <span>{candidate.name}</span>
        </label>
      ))}
      {fieldError && (
        <p className="accusation-field-error" data-testid="accusation-field-error-weaponId" role="alert">
          {fieldError}
        </p>
      )}
    </fieldset>
  );
}

function WhenPicker({ flow, fieldError }: { flow: AccusationFlow; fieldError: string | undefined }) {
  return (
    <div className="accusation-when" data-testid="accusation-when">
      <label htmlFor="accusation-crime-time">
        <strong>WHEN</strong> — at what time of day did the crime happen? (24-hour, HH:MM)
      </label>
      <input
        id="accusation-crime-time"
        type="time"
        data-testid="accusation-time"
        value={flow.currentSelection.crimeTime ?? ""}
        onChange={(event) => flow.setCrimeTime(event.target.value)}
      />
      {fieldError && (
        <p className="accusation-field-error" data-testid="accusation-field-error-crimeTime" role="alert">
          {fieldError}
        </p>
      )}
    </div>
  );
}

function ConfirmationSummary({ flow, candidates }: { flow: AccusationFlow; candidates: AccusationCandidatesDTO }) {
  const selection = flow.currentSelection;
  const suspect = candidates.suspects.find((entry) => entry.id === selection.murdererId);
  const motive = candidates.motives.find((entry) => entry.id === selection.motiveId);
  const weapon = candidates.weapons.find((entry) => entry.id === selection.weaponId);

  return (
    <div className="accusation-confirmation" data-testid="accusation-confirmation">
      <p className="accusation-warning">
        Accusations are final — once confirmed they cannot be changed for this case.
      </p>
      <dl className="accusation-summary">
        <div className="accusation-summary-row">
          <dt>WHO</dt>
          <dd data-testid="accusation-summary-murderer">{suspect?.name ?? "—"}</dd>
        </div>
        <div className="accusation-summary-row">
          <dt>WHY</dt>
          <dd data-testid="accusation-summary-motive">{motive?.label ?? "—"}</dd>
        </div>
        <div className="accusation-summary-row">
          <dt>WEAPON</dt>
          <dd data-testid="accusation-summary-weapon">{weapon?.name ?? "—"}</dd>
        </div>
        <div className="accusation-summary-row">
          <dt>WHEN</dt>
          <dd data-testid="accusation-summary-time">{selection.crimeTime ?? "—"}</dd>
        </div>
      </dl>
    </div>
  );
}