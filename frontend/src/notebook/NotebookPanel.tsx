import type { AccusationCandidatesDTO } from "../api/types";
import type { HypothesisPins } from "./hypothesisStore";
import type { NotebookGroup, NotebookModel } from "./notebookModel";

/**
 * Detective Notebook (Phase 18C) — the pre-reveal drawer on the
 * investigation page.
 *
 * Renders ONLY the player-safe, deterministic {@link NotebookModel}: each
 * group lists only discovered information (gated on the server-authoritative
 * knowledge snapshot by the model). Everything is app-authored copy or
 * player-safe record content — React's default string rendering keeps every
 * value inert (no dangerouslySetInnerHTML, no HTML construction).
 *
 * The hypothesis block below is a PLAYER-NOTES-ONLY workspace: the four
 * pins persist to a namespaced localStorage key (handled by the route via
 * onPinsChanged) and are NEVER sent to the server. "Use my hypothesis"
 * lives on the accusation page, which fills ONLY the pinned dimensions.
 */
export interface NotebookPanelProps {
  model: NotebookModel;
  /** Player-safe candidate universes (bootstrap) for the pin pickers. */
  candidates: AccusationCandidatesDTO | null;
  /** The currently saved player pins (reload-preserved by the route). */
  pins: HypothesisPins;
  open: boolean;
  onToggle: () => void;
  /** Persist the pins (route owns the namespaced localStorage write). */
  onPinsChanged: (pins: HypothesisPins) => void;
}

export default function NotebookPanel({
  model,
  candidates,
  pins,
  open,
  onToggle,
  onPinsChanged,
}: NotebookPanelProps) {
  return (
    <section className="notebook-drawer" data-testid="notebook-panel" aria-label="Detective notebook">
      <header className="notebook-header">
        <h3 className="notebook-heading" data-testid="notebook-heading">
          Detective Notebook
        </h3>
        <button
          type="button"
          className="notebook-toggle"
          data-testid="notebook-toggle"
          onClick={onToggle}
          aria-expanded={open}
        >
          {open ? "Hide notebook" : "Show notebook"}
        </button>
      </header>

      {open && (
        <div className="notebook-body">
          <p className="notebook-intro" data-testid="notebook-intro">
            Your case notes — only what you have discovered is ever listed here. Nothing in this
            notebook affects the case, the solver or your score.
          </p>

          {model.groups.map((group) => (
            <NotebookGroupBlock key={group.id} group={group} />
          ))}

          <HypothesisBlock candidates={candidates} pins={pins} onPinsChanged={onPinsChanged} />
        </div>
      )}
    </section>
  );
}

function NotebookGroupBlock({ group }: { group: NotebookGroup }) {
  return (
    <div className="notebook-group" data-testid={`notebook-group-${group.id}`}>
      <h4 className="notebook-group-title">{group.title}</h4>
      {group.entries.length === 0 ? (
        <p className="notebook-group-empty" data-testid={`notebook-group-${group.id}-empty`}>
          {group.emptyMessage}
        </p>
      ) : (
        <ul className="notebook-group-list" data-testid={`notebook-group-${group.id}-list`}>
          {group.entries.map((entry) => (
            <li className="notebook-entry" key={entry.id} data-testid={`notebook-entry-${entry.id}`}>
              <strong>{entry.label}</strong>
              {entry.detail !== null && entry.detail !== "" ? <span> — {entry.detail}</span> : null}
              {entry.read ? (
                <span className="notebook-entry-read" data-testid={`notebook-entry-read-${entry.id}`}>
                  {" "}
                  · read
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function HypothesisBlock({
  candidates,
  pins,
  onPinsChanged,
}: {
  candidates: AccusationCandidatesDTO | null;
  pins: HypothesisPins;
  onPinsChanged: (pins: HypothesisPins) => void;
}) {
  const update = (patch: Partial<HypothesisPins>) => onPinsChanged({ ...pins, ...patch });

  if (candidates === null) {
    return (
      <div className="hypothesis-block" data-testid="hypothesis-block">
        <h4>Your hypothesis</h4>
        <p className="hypothesis-note" data-testid="hypothesis-note">
          Pin your own suspect, motive, weapon and time here while you investigate. These are
          private notes only — they never affect the case, the solver or your score.
        </p>
        <p className="hypothesis-unavailable" data-testid="hypothesis-unavailable">
          The candidate options are not available on this page right now.
        </p>
      </div>
    );
  }

  return (
    <div className="hypothesis-block" data-testid="hypothesis-block">
      <h4>Your hypothesis (private notes)</h4>
      <p className="hypothesis-note" data-testid="hypothesis-note">
        Pin your best guess for each dimension. These notes only pre-fill the accusation form
        on the next page — they never affect the case, the solver or your score.
      </p>

      <div className="hypothesis-pickers">
        <label className="hypothesis-field" data-testid="hypothesis-suspect">
          <span className="hypothesis-label">Suspect</span>
          <select
            data-testid="hypothesis-suspect-select"
            value={pins.suspect ?? ""}
            onChange={(event) => update({ suspect: event.target.value === "" ? null : event.target.value })}
          >
            <option value="">— none —</option>
            {candidates.suspects.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
              </option>
            ))}
          </select>
        </label>

        <label className="hypothesis-field" data-testid="hypothesis-motive">
          <span className="hypothesis-label">Motive</span>
          <select
            data-testid="hypothesis-motive-select"
            value={pins.motive ?? ""}
            onChange={(event) => update({ motive: event.target.value === "" ? null : event.target.value })}
          >
            <option value="">— none —</option>
            {candidates.motives.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.label}
              </option>
            ))}
          </select>
        </label>

        <label className="hypothesis-field" data-testid="hypothesis-weapon">
          <span className="hypothesis-label">Weapon</span>
          <select
            data-testid="hypothesis-weapon-select"
            value={pins.weapon ?? ""}
            onChange={(event) => update({ weapon: event.target.value === "" ? null : event.target.value })}
          >
            <option value="">— none —</option>
            {candidates.weapons.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
              </option>
            ))}
          </select>
        </label>

        <label className="hypothesis-field" data-testid="hypothesis-time">
          <span className="hypothesis-label">Time (HH:MM)</span>
          <input
            type="time"
            data-testid="hypothesis-time-input"
            value={pins.time ?? ""}
            onChange={(event) => update({ time: event.target.value === "" ? null : event.target.value })}
          />
        </label>
      </div>

      <p className="hypothesis-use-hint" data-testid="hypothesis-use-hint">
        On the accusation page, “Use my hypothesis” fills the form ONLY with the dimensions you
        pinned here — anything left unpinned stays empty.
      </p>
    </div>
  );
}