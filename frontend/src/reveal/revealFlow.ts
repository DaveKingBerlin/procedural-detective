import { ApiError } from "../api/client";
import type { AccusationCandidatesDTO, InvestigationBootstrapResponse, RevealResponse } from "../api/types";
import { ValidationError, parseInvestigationBootstrap } from "../scene/validation";
import { parseRevealResponse } from "./revealValidation";

/**
 * Reveal page controller (Phase 7 E/L + restart durability).
 *
 * Loading the reveal is deliberately SIMPLE and idempotent: the page always
 * re-fetches GET /reveal from the stored credential and renders from the DTO.
 * The server allows reveal from ACCUSED (so a reload right after a successful
 * submission restores the screen) and from REVEALED (so a reload after reveal
 * restores the identical screen); before accusation the 403
 * REVEAL_NOT_AVAILABLE gate produces the gated error state.
 *
 * Deriving the location/scene candidates is best-effort: the player-safe
 * bootstrap is fetched purely to resolve the player's submitted ids to
 * readable names on the reveal screen; when it fails, the reveal still
 * renders with raw ids (the reveal DTO remains the single source of truth).
 */

export type RevealPageState =
  | { status: "loading" }
  | { status: "revealed"; reveal: RevealResponse; candidates: AccusationCandidatesDTO | null }
  | {
      status: "error";
      kind: "not-accused" | "auth" | "network" | "gameplay";
      message: string;
      tokenInvalid: boolean;
      retryable: boolean;
    };

export interface RevealServices {
  getReveal(playthroughId: string, token: string): Promise<RevealResponse>;
  getInvestigation(playthroughId: string, token: string): Promise<InvestigationBootstrapResponse>;
}

export class RevealFlow {
  private readonly playthroughId: string;

  constructor(
    private readonly services: RevealServices,
    private readonly token: string,
    identity: { playthroughId: string },
  ) {
    this.playthroughId = identity.playthroughId;
  }

  /** Fetch the reveal (+ best-effort candidates). Never throws. */
  async load(): Promise<RevealPageState> {
    let reveal: RevealResponse;
    try {
      reveal = parseRevealResponse(await this.services.getReveal(this.playthroughId, this.token));
    } catch (error) {
      return this.mapRevealError(error);
    }

    const candidates = await this.tryLoadCandidates();

    return { status: "revealed", reveal, candidates };
  }

  private async tryLoadCandidates(): Promise<AccusationCandidatesDTO | null> {
    try {
      const bootstrap = parseInvestigationBootstrap.validate(
        await this.services.getInvestigation(this.playthroughId, this.token),
      );
      return bootstrap.candidates;
    } catch {
      // The reveal is authoritative; a best-effort candidates lookup failure
      // must never block the end-of-case screen (raw ids are shown instead).
      return null;
    }
  }

  private mapRevealError(error: unknown): Extract<RevealPageState, { status: "error" }> {
    if (error instanceof ValidationError) {
      // The DTO failed allowlist parsing: retrying cannot fix a malformed
      // payload, and the truth must NEVER be white-screened.
      return {
        status: "error",
        kind: "gameplay",
        message: "The case reveal data is malformed and cannot be displayed safely.",
        tokenInvalid: false,
        retryable: false,
      };
    }
    if (error instanceof ApiError) {
      if (error.status === 403 && error.code === "REVEAL_NOT_AVAILABLE") {
        return {
          status: "error",
          kind: "not-accused",
          message: "No accusation has been submitted for this playthrough yet, so the truth is still sealed.",
          tokenInvalid: false,
          retryable: false,
        };
      }
      if (error.status === 401 || error.status === 403) {
        return {
          status: "error",
          kind: "auth",
          message: "Your playthrough access is no longer valid. Reset the token to continue.",
          tokenInvalid: true,
          retryable: false,
        };
      }
      if (error.status === 0) {
        return {
          status: "error",
          kind: "network",
          message: "The reveal service could not be reached. Check that the backend is running, then try again.",
          tokenInvalid: false,
          retryable: true,
        };
      }
      if (error.status >= 500) {
        return {
          status: "error",
          kind: "gameplay",
          message: "The reveal service reported a temporary problem.",
          tokenInvalid: false,
          retryable: true,
        };
      }
    }
    return {
      status: "error",
      kind: "gameplay",
      message: "The case truth could not be loaded for this playthrough.",
      tokenInvalid: false,
      retryable: true,
    };
  }
}