import { ApiError } from "../api/client";
import type { AccusationRequest, AccusationCandidatesDTO, AccusationResponse, RevealResponse } from "../api/types";
import { parseRevealResponse } from "../reveal/revealValidation";
import {
  EMPTY_SELECTION,
  parseAccusationResponse,
  validateAccusationForm,
  type AccusationFieldErrors,
  type AccusationSelection,
} from "./accusationValidation";

/**
 * Accusation controller (Phase 7 K, pure state machine).
 *
 * The ONLY accusation state the client is allowed to hold lives here:
 *   - the player-safe candidate list (already validated),
 *   - the current WHO/WHY/WEAPON/WHEN selection,
 *   - the submission phase (editing -> confirming -> submitting ->
 *     accepted | already-submitted),
 *   - the immutable accepted accusation (echoed by the server).
 *
 * Guarantees:
 *   - candidates are used exactly as the server returned them: NO re-ordering,
 *     NO derived correctness/winnership — the panel renders plain candidate
 *     text only;
 *   - the confirmation step must pass client-side validation before anything
 *     is sent; the same validation re-runs immediately before submission so a
 *     forged selection can never leave the client with an out-of-universe id;
 *   - a request that is already in flight can never be started twice
 *     (double-submit guard at the controller, plus the UI disables the
 *     confirm button);
 *   - the first authoritative accusation is the server's; a 409
 *     CASE_ALREADY_SUBMITTED conflict surfaces the already-submitted state and
 *     fetches the now-available reveal via the injected callback;
 *   - 401/403 credentials are surfaced as a token-invalid state so the page
 *     can offer a reset; 422 maps to the server's validation message.
 *
 * Dependencies are constructor-injected for deterministic offline tests.
 */

export type AccusationPhase =
  | "editing"
  | "confirming"
  | "submitting"
  | "accepted"
  | "already-submitted";

export type AccusationService = (
  playthroughId: string,
  body: AccusationRequest,
  token: string,
) => Promise<AccusationResponse>;

export type RevealService = (playthroughId: string, token: string) => Promise<RevealResponse>;

export interface AccusationServices {
  submitAccusation: AccusationService;
  getReveal: RevealService;
}

export interface AccusationCallbacks {
  /** Called with the parsed reveal when a 409 conflict proves the case was already accused. */
  onRevealAvailable?: (reveal: RevealResponse) => void;
}

export type ConfirmOutcome =
  | { outcome: "accepted" }
  | { outcome: "already-submitted" }
  | { outcome: "busy" } // double-submit guard: a request is already in flight
  | { outcome: "invalid" } // client-side validation re-check failed before send
  | { outcome: "auth-failed" } // 401/403 expired credentials (tokenInvalid true)
  | { outcome: "failed" }; // network / 5xx / 422 — serverError holds the safe message

export class AccusationFlow {
  private selection: AccusationSelection = { ...EMPTY_SELECTION };
  private phaseValue: AccusationPhase = "editing";
  private fieldErrors: AccusationFieldErrors = {};
  private serverErrorValue: string | null = null;
  private tokenInvalidValue = false;
  private acceptedAccusationValue: AccusationResponse | null = null;
  private inFlight = false;
  private readonly listeners = new Set<() => void>();
  private readonly playthroughId: string;

  constructor(
    private readonly services: AccusationServices,
    private readonly token: string,
    identity: { playthroughId: string },
    private readonly candidates: AccusationCandidatesDTO,
    private readonly callbacks: AccusationCallbacks = {},
  ) {
    this.playthroughId = identity.playthroughId;
  }

  /* ----------------------------- observation ---------------------------- */

  /** Subscribe to state changes (the page hook uses this to re-render). */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private changed(): void {
    for (const listener of [...this.listeners]) listener();
  }

  /* ------------------------------- getters ------------------------------ */

  get phase(): AccusationPhase {
    return this.phaseValue;
  }

  /** The immutable accepted accusation once the server accepted it. */
  get acceptedAccusation(): AccusationResponse | null {
    return this.acceptedAccusationValue;
  }

  get currentSelection(): AccusationSelection {
    return { ...this.selection };
  }

  get lastFieldErrors(): AccusationFieldErrors {
    return { ...this.fieldErrors };
  }

  get serverError(): string | null {
    return this.serverErrorValue;
  }

  get tokenInvalid(): boolean {
    return this.tokenInvalidValue;
  }

  get candidatesSnapshot(): AccusationCandidatesDTO {
    return this.candidates;
  }

  get canAccuse(): boolean {
    return this.phaseValue === "editing";
  }

  /** Submit body exactly as the frozen contract expects (HH:MM -> HH:MM:00). */
  get requestBody(): AccusationRequest | null {
    if (this.selection.murdererId === null || this.selection.motiveId === null || this.selection.weaponId === null) {
      return null;
    }
    const crimeTime = this.selection.crimeTime;
    if (typeof crimeTime !== "string" || !/^\d{2}:\d{2}$/.test(crimeTime)) return null;
    return {
      murdererId: this.selection.murdererId,
      motiveId: this.selection.motiveId,
      weaponId: this.selection.weaponId,
      crimeTime: `${crimeTime}:00`,
    };
  }

  /* ------------------------------- actions ------------------------------ */

  selectSuspect(id: string): void {
    this.selection = { ...this.selection, murdererId: id };
    // Re-open the confirmation context? No: keep the current phase, but clear
    // stale field errors while editing so feedback stays current.
    if (this.phaseValue === "editing") {
      this.fieldErrors = { ...this.fieldErrors, murdererId: undefined };
    }
    this.changed();
  }

  selectMotive(id: string): void {
    this.selection = { ...this.selection, motiveId: id };
    if (this.phaseValue === "editing") {
      this.fieldErrors = { ...this.fieldErrors, motiveId: undefined };
    }
    this.changed();
  }

  selectWeapon(id: string): void {
    this.selection = { ...this.selection, weaponId: id };
    if (this.phaseValue === "editing") {
      this.fieldErrors = { ...this.fieldErrors, weaponId: undefined };
    }
    this.changed();
  }

  /** Store the raw bare "HH:MM" value collected from the time input. */
  setCrimeTime(value: string): void {
    this.selection = { ...this.selection, crimeTime: value };
    if (this.phaseValue === "editing") {
      this.fieldErrors = { ...this.fieldErrors, crimeTime: undefined };
    }
    this.changed();
  }

  /**
   * Phase 18C — copy the player's private notebook hypothesis pins into the
   * selection ("Use my hypothesis").
   *
   * Player notes ONLY: this NEVER touches the server, never affects the
   * solver/truth/scoring, and performs NO implicit fill — a dimension the
   * player did not pin is left EMPTY (null). The existing pre-submit
   * membership validation still runs, so a stale/forged pin (e.g. from an
   * older playthrough's universe) can never be submitted: it degrades to a
   * field error, not a request.
   */
  applyHypothesis(pins: {
    suspect: string | null;
    motive: string | null;
    weapon: string | null;
    time: string | null;
  }): void {
    if (this.phaseValue !== "editing") return; // a submitted accusation is immutable
    this.selection = {
      murdererId: pins.suspect,
      motiveId: pins.motive,
      weaponId: pins.weapon,
      crimeTime: pins.time,
    };
    this.fieldErrors = {};
    this.serverErrorValue = null;
    this.tokenInvalidValue = false;
    this.changed();
  }

  /**
   * Editing -> confirmation. Applies client-side validation; when any field is
   * missing/invalid the panel stays in editing with field-level errors.
   */
  openConfirmation(): "confirmed" | "invalid" {
    if (this.phaseValue !== "editing") return "invalid";
    this.fieldErrors = validateAccusationForm(this.selection, this.candidates);
    this.serverErrorValue = null;
    this.tokenInvalidValue = false;
    if (Object.values(this.fieldErrors).some((message) => message !== undefined && message !== "")) {
      this.changed();
      return "invalid";
    }
    this.phaseValue = "confirming";
    this.changed();
    return "confirmed";
  }

  cancelConfirmation(): void {
    if (this.phaseValue === "confirming") {
      this.fieldErrors = {};
      this.serverErrorValue = null;
      this.phaseValue = "editing";
      this.changed();
    }
  }

  /**
   * The irreversible submission (confirm button). Double-submit safe: the
   * synchronous in-flight guard means two rapid activations can only ever
   * produce ONE network request.
   */
  async confirmAccusation(): Promise<ConfirmOutcome> {
    if (this.inFlight) return { outcome: "busy" };
    if (this.phaseValue !== "confirming") return { outcome: "invalid" };

    // Re-validate the exact selection that will be sent; a forged action can
    // never smuggle an out-of-universe id past the membership re-check.
    const errors = validateAccusationForm(this.selection, this.candidates);
    if (Object.values(errors).some((message) => message !== undefined && message !== "")) {
      this.fieldErrors = errors;
      this.phaseValue = "editing";
      this.changed();
      return { outcome: "invalid" };
    }

    const body = this.requestBody;
    if (body === null) {
      this.phaseValue = "editing";
      this.changed();
      return { outcome: "invalid" };
    }

    this.inFlight = true;
    this.phaseValue = "submitting";
    this.serverErrorValue = null;
    this.tokenInvalidValue = false;
    this.changed();

    try {
      const response = await this.services.submitAccusation(this.playthroughId, body, this.token);
      this.acceptedAccusationValue = parseAccusationResponse(response);
      this.phaseValue = "accepted";
      this.changed();
      return { outcome: "accepted" };
    } catch (error) {
      return this.mapSubmissionError(error);
    } finally {
      this.inFlight = false;
    }
  }

  private mapSubmissionError(error: unknown): ConfirmOutcome {
    if (error instanceof ApiError && error.status === 409 && error.code === "CASE_ALREADY_SUBMITTED") {
      // The case was already accused elsewhere (reload, second tab): reveal is
      // now available from ACCUSED. Fetch it so the page can move on.
      this.phaseValue = "already-submitted";
      this.serverErrorValue = "This case has already been submitted.";
      this.changed();
      void this.fetchRevealAfterConflict();
      return { outcome: "already-submitted" };
    }
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      this.phaseValue = "confirming";
      this.serverErrorValue = "Your playthrough access is no longer valid. Reset the token to continue.";
      this.tokenInvalidValue = true;
      this.changed();
      return { outcome: "auth-failed" };
    }
    this.phaseValue = "confirming";
    this.serverErrorValue = this.safeServerMessage(error);
    this.changed();
    return { outcome: "failed" };
  }

  /** After a 409 the reveal is available — prefetch it (idempotent server call). */
  private async fetchRevealAfterConflict(): Promise<void> {
    try {
      const reveal = parseRevealResponse(await this.services.getReveal(this.playthroughId, this.token));
      this.callbacks.onRevealAvailable?.(reveal);
    } catch {
      // The already-submitted state remains; the /reveal route is authoritative
      // and will retry fetching the reveal itself.
    }
  }

  private safeServerMessage(error: unknown): string {
    if (error instanceof ApiError) {
      if (error.status === 422) {
        const message = error.message?.trim();
        if (message && message !== `Request failed with status ${error.status}`) return message;
        return "The accusation was rejected because one of the options has become invalid.";
      }
      if (error.status === 0) return "The accusation could not be submitted. Check that the backend is running, then try again.";
      if (error.status >= 500) return "The accusation service reported a temporary problem. Please try again.";
      const message = error.message?.trim();
      if (message && message !== `Request failed with status ${error.status}` && message !== "") return message;
    }
    return "The accusation could not be submitted. Please try again.";
  }
}