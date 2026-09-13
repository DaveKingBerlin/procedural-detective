import { ApiError } from "../api/client";
import type {
  DiscoveryResultDTO,
  EvidenceReadResultDTO,
  InteractionResultDTO,
  InvestigationBootstrapResponse,
  PlayerKnowledgeDTO,
} from "../api/types";
import { buildInvestigationScene, type InvestigationSceneModel } from "./buildInvestigationScene";
import type { CreateInvestigationSceneResult, InvestigationSceneHandle } from "./renderInvestigation";
import { ValidationError } from "./validation";

/**
 * Server-authoritative investigation session (Phase 6 I/N).
 *
 * This controller owns the ONLY client-side investigation state that is
 * allowed to exist:
 *   - loading/error state (start/outcome),
 *   - the cached player-safe DTOs (bootstrap + read records),
 *   - transient UI feedback (toast, current record panel),
 *   - camera/UI state lives in the route, outside this module.
 *
 * Knowledge rules enforced here:
 *   - discovered/read flags ALWAYS come from the server (bootstrap first,
 *     then interact/discover/read responses). The client NEVER marks
 *     anything known on its own: knowledge grows only with ids the server
 *     returned, and repeated discovery is idempotent.
 *   - a record is fetched ONLY after the server confirmed the evidence is
 *     discovered through a successful interaction.
 *   - all failures are mapped to short player-safe messages — never raw
 *     internals, error stacks or token material.
 *
 * Dependencies are constructor-injected so the whole flow is unit-testable
 * deterministically (no network, no DOM).
 */

export type InvestigationErrorKind = "network" | "auth" | "gameplay" | "malformed" | "scene";

/** The four frozen investigation endpoints, injectable for tests. */
export interface InvestigationServices {
  getInvestigation(playthroughId: string, token: string): Promise<InvestigationBootstrapResponse>;
  interactObject(playthroughId: string, objectId: string, interaction: string, token: string): Promise<InteractionResultDTO>;
  discoverEvidence(playthroughId: string, evidenceId: string, token: string): Promise<DiscoveryResultDTO>;
  readRecord(playthroughId: string, recordId: string, token: string): Promise<EvidenceReadResultDTO>;
}

/** Injectable Babylon scene creation (the route wires the real glue + canvas). */
export type SceneFactory = (canvas: HTMLCanvasElement, model: InvestigationSceneModel) => CreateInvestigationSceneResult;

export type StartOutcome =
  | { ok: true; model: InvestigationSceneModel }
  | { ok: false; kind: InvestigationErrorKind; message: string; tokenInvalid: boolean; retryable: boolean };

export interface SessionToast {
  id: string;
  text: string;
  evidenceId: string | null;
}

export interface InteractionFeedback {
  objectId: string;
  toast: SessionToast | null;
  record: EvidenceReadResultDTO | null;
  error: { message: string } | null;
}

/** True for 401/403 responses — the playthrough credential is the problem. */
export function isAuthorisationFailure(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export class InvestigationSession {
  private knowledge: PlayerKnowledgeDTO | null = null;
  private model: InvestigationSceneModel | null = null;
  private readonly records = new Map<string, EvidenceReadResultDTO>();
  private toast: SessionToast | null = null;
  private toastSeq = 0;
  private readonly playthroughId: string;
  private sceneHandle: InvestigationSceneHandle | null = null;

  constructor(
    private readonly services: InvestigationServices,
    private readonly token: string,
    private readonly createScene: SceneFactory | null,
    identity: { playthroughId: string },
  ) {
    this.playthroughId = identity.playthroughId;
  }

  get hasStarted(): boolean {
    return this.model !== null;
  }

  /** Cached player-safe scene model, available after a successful start. */
  get sceneModel(): InvestigationSceneModel | null {
    return this.model;
  }

  /** Snapshot of the server-derived knowledge cache (never null after start). */
  get knowledgeSnapshot(): PlayerKnowledgeDTO | null {
    return this.knowledge;
  }

  get currentToast(): SessionToast | null {
    return this.toast;
  }

  /**
   * Load the bootstrap, derive the scene model and (when a scene factory is
   * wired) build the 3D scene. Returns a StartOutcome — this method never
   * throws; every failure becomes a safe, player-facing error.
   */
  async start(canvas: HTMLCanvasElement | null): Promise<StartOutcome> {
    let bootstrap: InvestigationBootstrapResponse;
    try {
      bootstrap = await this.services.getInvestigation(this.playthroughId, this.token);
    } catch (error) {
      return this.mapBootstrapError(error);
    }

    let model: InvestigationSceneModel;
    try {
      model = buildInvestigationScene(bootstrap);
    } catch (error) {
      if (error instanceof ValidationError) {
        return {
          ok: false,
          kind: "malformed",
          message: "The case scene data is malformed and cannot be displayed safely.",
          tokenInvalid: false,
          retryable: false,
        };
      }
      return {
        ok: false,
        kind: "gameplay",
        message: "The case scene data could not be prepared.",
        tokenInvalid: false,
        retryable: false,
      };
    }

    if (this.createScene && canvas) {
      const result = this.createScene(canvas, model);
      if (!result.ok) {
        return {
          ok: false,
          kind: "scene",
          message: result.error,
          tokenInvalid: false,
          retryable: true,
        };
      }
      this.sceneHandle = result as InvestigationSceneHandle;
    }

    this.knowledge = {
      discoveredEvidenceIds: [...bootstrap.playerKnowledge.discoveredEvidenceIds],
      readEvidenceIds: [...bootstrap.playerKnowledge.readEvidenceIds],
      visitedLocationIds: [...bootstrap.playerKnowledge.visitedLocationIds],
    };
    this.model = model;
    return { ok: true, model };
  }

  /**
   * Dispatch an object interaction. Only the object's own published
   * interaction string is ever sent; wrong/disallowed interactions arrive as
   * ApiErrors and are mapped to a safe gameplay message (409 -> "not
   * allowed", 401/403 -> invalid access, anything else -> generic).
   */
  async interact(objectId: string): Promise<InteractionFeedback> {
    const model = this.model;
    if (!model) {
      return { objectId, toast: null, record: null, error: { message: "The investigation has not loaded yet." } };
    }
    const worldObject = model.worldObjects.find((o) => o.objectId === objectId);
    if (!worldObject) {
      return { objectId, toast: null, record: null, error: { message: "That object is not part of this scene." } };
    }
    if (!worldObject.interactionWorks) {
      return { objectId, toast: null, record: null, error: { message: "That object cannot be interacted with." } };
    }

    let result: InteractionResultDTO;
    try {
      result = await this.services.interactObject(this.playthroughId, objectId, worldObject.interaction, this.token);
    } catch (error) {
      return { objectId, toast: null, record: null, error: { message: this.safeInteractionError(error) } };
    }

    const feedback: InteractionFeedback = { objectId, toast: null, record: null, error: null };

    if (result.discovery) {
      const { evidenceId, title, state } = result.discovery;
      feedback.toast = this.makeToast(
        `${state === "discovered" ? "Discovered" : "Already discovered"}: ${title}`,
        evidenceId,
      );
      this.applyDiscovery(result.discovery);
    } else {
      feedback.toast = this.makeToast(
        worldObject.label ? `Interacted with ${worldObject.label}` : `Interacted: ${objectId}`,
        result.evidenceId,
      );
    }

    // Only now — after the server confirmed discovery — may the record be read.
    if (result.evidenceId) {
      feedback.record = await this.openRecord(result.evidenceId);
      if (feedback.record === null) {
        feedback.error = { message: "Evidence was found, but its details are not readable yet." };
      }
    }
    return feedback;
  }

  /** Clear the current discovery toast (manual dismissal). */
  dismissToast(): void {
    this.toast = null;
  }

  /** Release the live 3D scene, if one was created (idempotent, never throws). */
  disposeScene(): void {
    try {
      this.sceneHandle?.dispose();
    } catch {
      // A failed engine teardown must never crash navigation or retry.
    }
    this.sceneHandle = null;
  }

  private applyDiscovery(discovery: DiscoveryResultDTO): void {
    if (!this.knowledge) return;
    // Idempotent: the Set deduplication makes repeated discoveries no-ops.
    this.knowledge = {
      ...this.knowledge,
      discoveredEvidenceIds: sortedUnique([...this.knowledge.discoveredEvidenceIds, discovery.evidenceId]),
    };
  }

  /** Read a discovered record, caching the player-safe DTO; null when unreadable. */
  private async openRecord(recordId: string): Promise<EvidenceReadResultDTO | null> {
    const cached = this.records.get(recordId);
    if (cached) return cached;
    try {
      const record = await this.services.readRecord(this.playthroughId, recordId, this.token);
      this.records.set(recordId, record);
      if (this.knowledge) {
        this.knowledge = {
          ...this.knowledge,
          readEvidenceIds: sortedUnique([...this.knowledge.readEvidenceIds, recordId]),
        };
      }
      return record;
    } catch {
      return null; // Record unreadable: keep the discovery toast, never crash.
    }
  }

  private makeToast(text: string, evidenceId: string | null): SessionToast {
    this.toast = { id: `toast-${++this.toastSeq}`, text, evidenceId };
    return this.toast;
  }

  private mapBootstrapError(error: unknown): StartOutcome {
    if (isAuthorisationFailure(error)) {
      return {
        ok: false,
        kind: "auth",
        message: "Playthrough access is not valid for this investigation.",
        tokenInvalid: true,
        retryable: false,
      };
    }
    if (error instanceof ApiError) {
      if (error.status === 0) {
        return {
          ok: false,
          kind: "network",
          message: "The investigation service could not be reached. Check that the backend is running, then try again.",
          tokenInvalid: false,
          retryable: true,
        };
      }
      if (error.status >= 500) {
        return {
          ok: false,
          kind: "gameplay",
          message: "The investigation service reported a temporary problem.",
          tokenInvalid: false,
          retryable: true,
        };
      }
      return {
        ok: false,
        kind: "gameplay",
        message: "This playthrough cannot be investigated right now.",
        tokenInvalid: false,
        retryable: false,
      };
    }
    return {
      ok: false,
      kind: "network",
      message: "The investigation could not start.",
      tokenInvalid: false,
      retryable: true,
    };
  }

  private safeInteractionError(error: unknown): string {
    if (error instanceof ApiError && (error.status === 409 || error.code === "INTERACTION_NOT_ALLOWED")) {
      return "That action is not allowed for this object right now.";
    }
    if (isAuthorisationFailure(error)) {
      return "Your playthrough access is no longer valid. Reset the token from the Home page.";
    }
    return "That interaction did not work. Please try again.";
  }
}

function sortedUnique(ids: string[]): string[] {
  return [...new Set(ids)].sort();
}