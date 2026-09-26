import { ApiError } from "../api/client";
import type {
  AccusationCandidatesDTO,
  DiscoveryResultDTO,
  EvidenceReadResultDTO,
  InteractionResultDTO,
  InvestigationBootstrapResponse,
  PlaythroughLifecycleState,
  PlayerKnowledgeDTO,
  WitnessInterviewResponse,
  WitnessListEntryDTO,
  WitnessQuestionType,
  WitnessStatementDTO,
} from "../api/types";
import { buildInvestigationScene, applyKnowledgeToSceneModel, bindEvidenceToSceneModel, type InvestigationSceneModel } from "./buildInvestigationScene";
import { semanticLabelOrNull } from "./objectLabel";
import type { CreateInvestigationSceneResult, InvestigationSceneHandle } from "./renderInvestigation";
import { ValidationError, parseInvestigationBootstrap } from "./validation";
import {
  parseWitnessInterviewResponse,
  WITNESS_QUESTION_ORDER,
  witnessQuestionKey,
  type AskedWitnessStatement,
} from "../witness/witnessModel";

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
 *     then interact/read responses). The client NEVER marks anything known on
 *     its own: knowledge grows only with ids the server returned, and repeated
 *     discovery is idempotent.
 *   - PD-SEC-01 (Phase 20): the bootstrap no longer carries `evidenceId` for
 *     UNDISCOVERED world objects. Discovery happens ONLY through
 *     POST /objects/{id}/interact — the direct evidence discover route is gone.
 *     When the server confirms a discovery, its response id is bound onto the
 *     interacted object (player-known from then on) BEFORE the knowledge merge
 *     so the DEF-072 flag flip and every player-safe derivation (summary
 *     strip, captions, notebook) can resolve the id.
 *   - a record is fetched ONLY after the server confirmed the evidence is
 *     discovered through a successful interaction.
 *   - all failures are mapped to short player-safe messages — never raw
 *     internals, error stacks or token material.
 *
 * Dependencies are constructor-injected so the whole flow is unit-testable
 * deterministically (no network, no DOM).
 */

export type InvestigationErrorKind = "network" | "auth" | "gameplay" | "malformed" | "scene";

/** The three frozen investigation endpoints (direct evidence discovery is
 *  REMOVED — discovery happens exclusively through interactObject). */
export interface InvestigationServices {
  getInvestigation(playthroughId: string, token: string): Promise<InvestigationBootstrapResponse>;
  interactObject(playthroughId: string, objectId: string, interaction: string, token: string): Promise<InteractionResultDTO>;
  readRecord(playthroughId: string, recordId: string, token: string): Promise<EvidenceReadResultDTO>;
  /**
   * Phase 23 — witness interview. OPTIONAL: present only on backends that
   * publish the Phase 23 interview feature (a bootstrap `witnesses` list).
   * Absent services degrade the session to the pre-23 behavior (no witness
   * UI at all) — existing callers/tests are untouched.
   */
  interviewWitness?(
    playthroughId: string,
    witnessId: string,
    questionType: WitnessQuestionType,
    token: string,
  ): Promise<WitnessInterviewResponse>;
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

/**
 * Phase 23 — the result of asking ONE closed interview question.
 * `ok:true` always carries the deterministic player-safe statement; when the
 * question legitimately discovered evidence, `record`/`discovery.record`
 * carry the (now player-known) evidence record the UI should open. `cached`
 * is true ONLY when the answer was served from the in-memory asked-store
 * (a re-ask — no POST, no duplicate, no re-opened panel).
 */
export type WitnessAskOutcome =
  | {
      ok: true;
      witnessId: string;
      displayName: string;
      questionType: WitnessQuestionType;
      statement: WitnessStatementDTO;
      discovery: { newlyDiscovered: boolean; record: EvidenceReadResultDTO | null } | null;
      record: EvidenceReadResultDTO | null;
      cached: boolean;
    }
  | { ok: false; error: { message: string }; cached: false };

/** True for 401/403 responses — the playthrough credential is the problem. */
export function isAuthorisationFailure(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export class InvestigationSession {
  private knowledge: PlayerKnowledgeDTO | null = null;
  private model: InvestigationSceneModel | null = null;
  private lifecycleStateValue: PlaythroughLifecycleState | null = null;
  private candidatesValue: AccusationCandidatesDTO | null = null;
  private readonly records = new Map<string, EvidenceReadResultDTO>();
  private toast: SessionToast | null = null;
  private toastSeq = 0;
  private readonly playthroughId: string;
  private sceneHandle: InvestigationSceneHandle | null = null;
  /**
   * Phase 19F — the ids of the world objects this session has INSPECTED
   * (a server-confirmed 200 interaction, evidence or not). Cosmetic,
   * in-memory, NEVER persisted and NEVER sent back: it exists only so the
   * live UI can distinguish INSPECTED from EVIDENCE_DISCOVERED (the
   * server-authoritative `discovered` flags) — e.g. vase -> inspected +
   * not discovered; knife -> inspected + discovered.
   */
  private readonly inspectedIds = new Set<string>();
  /**
   * Phase 23 — the player-safe witness list from the bootstrap (ids + names +
   * presence ONLY; no statement content). Empty on pre-23 servers.
   */
  private witnessesValue: WitnessListEntryDTO[] = [];
  /**
   * Phase 23 — in-memory ASKED witness statements, keyed by
   * witnessQuestionKey(witnessId, questionType). This is cosmetic session
   * state (idempotent re-ask, panel + notebook dedupe). Statements that
   * discovered evidence ALSO flow into `records`/`knowledge`, so they survive
   * a reload via the server while the store itself is never persisted.
   */
  private readonly askedStatements = new Map<string, AskedWitnessStatement>();

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

  /** Server-authoritative lifecycle state from the bootstrap (PLAYING/ACCUSED/REVEALED). */
  get bootstrapState(): PlaythroughLifecycleState | null {
    return this.lifecycleStateValue;
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

  /** Sorted snapshot of the server-derived discovered evidence ids. */
  discoveredEvidenceIdsSnapshot(): string[] {
    return this.knowledge ? [...this.knowledge.discoveredEvidenceIds] : [];
  }

  /** Sorted snapshot of the server-derived read evidence ids. */
  readEvidenceIdsSnapshot(): string[] {
    return this.knowledge ? [...this.knowledge.readEvidenceIds] : [];
  }

  /**
   * Phase 19F — sorted snapshot of the inspected (server-confirmed 200
   * interaction) world object ids. Cosmetic in-memory state, never
   * persisted: distinguishes INSPECTED from EVIDENCE_DISCOVERED purely for
   * live UI markers ("· inspected" vs the server-authoritative
   * "· discovered" flag).
   */
  inspectedObjectIdsSnapshot(): string[] {
    return [...this.inspectedIds].sort();
  }

  /** Titles of the read records cached by this session (id -> title). */
  discoveredRecordTitles(): Map<string, string> {
    const titles = new Map<string, string>();
    for (const [id, record] of this.records) {
      titles.set(id, record.title);
    }
    return titles;
  }

  /**
   * Snapshot of the cached read records (Phase 18C Detective Notebook).
   * Empty right after a reload — {@link hydrateNotebookRecords} refills it
   * ONLY from ids the server already confirmed READ.
   */
  recordCacheSnapshot(): ReadonlyArray<EvidenceReadResultDTO> {
    return [...this.records.values()];
  }

  /**
   * Player-safe candidate universes from the bootstrap (Phase 18C). The
   * notebook's hypothesis pins reference the same candidate ids the
   * accusation page publishes. Null before a successful start.
   */
  get candidatesSnapshot(): AccusationCandidatesDTO | null {
    return this.candidatesValue;
  }

  /**
   * Phase 18C — lazy-hydrate the notebook's read-record cache AFTER a
   * reload. Fetches ONLY ids the server itself confirmed READ
   * (bootstrap `readEvidenceIds`) — never undiscovered evidence, never an
   * id the knowledge snapshot does not carry. A failed fetch degrades
   * gracefully (the notebook keeps deriving from world-object labels and
   * whatever DID load). Idempotent: cached records are never re-fetched.
   *
   * DEF-095 (Phase 20): this hydration is a PLAYING-state action. After an
   * accusation the backend's frozen Phase 7 "gameplay ends at accusation"
   * gate answers record reads with 409 NOT_PLAYING, and the scene in
   * ACCUSED/REVEALED is only restored as the player-visible world (the
   * reveal carries its own DTO) — so a post-accusation reload of /scene
   * must NEVER dispatch GET /records/*. The evidence stays player-known via
   * `readEvidenceIds`, so the notebook still renders the discovered/read
   * entries on redisplay.
   */
  async hydrateNotebookRecords(): Promise<void> {
    if (this.knowledge === null) return;
    // DEF-095: record reads are gameplay (PLAYING-only). The session knows
    // the authoritative lifecycle state from the bootstrap — do not dispatch
    // a single GET /records/* from ACCUSED/REVEALED.
    if (this.lifecycleStateValue !== "PLAYING") return;
    for (const recordId of this.knowledge.readEvidenceIds) {
      if (this.records.has(recordId)) continue;
      try {
        const record = await this.services.readRecord(this.playthroughId, recordId, this.token);
        this.records.set(recordId, record);
      } catch (error) {
        // DEF-095 (state-race edge): a 409 NOT_PLAYING means the playthrough
        // left PLAYING between the bootstrap and this fetch — the remaining
        // reads would answer the same way, so stop quietly. It is a silent
        // no-op: never a player-facing toast or failure.
        if (error instanceof ApiError && error.status === 409) return;
        // Safe degrade: a failed lazy fetch never blocks the notebook.
      }
    }
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

    // Phase 23: validate the WHOLE bootstrap once, so the player-safe witness
    // list (an optional field on this exact DTO) is parsed through the same
    // strict gate as the scene. The typed result feeds the scene builder
    // (which re-validates internally — a cheap, idempotent pure parse).
    let parsedBootstrap: InvestigationBootstrapResponse;
    try {
      parsedBootstrap = parseInvestigationBootstrap.validate(bootstrap);
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

    let model: InvestigationSceneModel;
    try {
      model = buildInvestigationScene(parsedBootstrap);
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
      discoveredEvidenceIds: [...parsedBootstrap.playerKnowledge.discoveredEvidenceIds],
      readEvidenceIds: [...parsedBootstrap.playerKnowledge.readEvidenceIds],
      visitedLocationIds: [...parsedBootstrap.playerKnowledge.visitedLocationIds],
    };
    this.lifecycleStateValue = parsedBootstrap.state;
    this.candidatesValue = parsedBootstrap.candidates;
    // Phase 23: the player-safe witness list ([] on pre-23 servers).
    this.witnessesValue = parsedBootstrap.witnesses ?? [];
    this.model = model;
    // DEF-072 invariant: the scene-model flags ALWAYS mirror the knowledge
    // snapshot — the bootstrap DTO flags define the same sets at start.
    this.syncSceneModelKnowledge();
    return { ok: true, model };
  }

  /**
   * Dispatch an object interaction. Phase 19F — UNIVERSAL OBJECT INSPECTION:
   * EVERY published semantic world object is inspectable, regardless of
   * `interactionWorks`. The old pre-19F gate returned "That object cannot be
   * interacted with." for decorative/structural objects (vase, table, door,
   * lamp, victim) whose published interaction is "". The backend now answers
   * the interact call for ANY published semantic object: an evidence-linked
   * placement runs discovery as before, and a non-evidence placement returns
   * the safe inspection result (discovery:null, evidenceId:null). Only the
   * object's OWN published interaction string is ever sent (empty for
   * decorative placements — the exact string the placement publishes);
   * wrong/disallowed interactions still arrive as ApiErrors and are mapped
   * to a safe gameplay message (409 -> "not allowed", 401/403 -> invalid
   * access, anything else -> generic).
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

    let result: InteractionResultDTO;
    try {
      result = await this.services.interactObject(this.playthroughId, objectId, worldObject.interaction, this.token);
    } catch (error) {
      return { objectId, toast: null, record: null, error: { message: this.safeInteractionError(error) } };
    }

    // The server validated the interaction (200): the object was INSPECTED.
    // Cosmetic only — idempotent Set add, never persisted.
    this.inspectedIds.add(objectId);

    const feedback: InteractionFeedback = { objectId, toast: null, record: null, error: null };

    if (result.discovery) {
      const { evidenceId, title, state } = result.discovery;
      feedback.toast = this.makeToast(
        `${state === "discovered" ? "Discovered" : "Already discovered"}: ${title}`,
        evidenceId,
      );
      // PD-SEC-01: bind the now player-known id onto the interacted object
      // BEFORE the knowledge merge — the bootstrap omits evidence ids for
      // undiscovered objects, so without this the DEF-072 flag flip/captions/
      // notebook could not resolve the discovery.
      if (this.model) {
        this.model = bindEvidenceToSceneModel(this.model, objectId, evidenceId);
      }
      this.applyDiscovery(result.discovery);
    } else {
      // Phase 19C §3 — a NON-EVIDENCE interact: the server confirmed NO
      // discovery for this object (discovery === null). The old copy
      // ("Interacted with <label>") dead-ended the player. Non-spoiling,
      // label-anchored feedback signals nothing was found here and — because
      // no knowledge changed — the scene stays fully interactive (the player
      // can obviously move on; "find evidence, then accuse" keeps guiding).
      // Phase 19E: the label is the SEMANTIC human label (catalog registry
      // label OR the humanized proc canonicalName for arbitrary generated
      // objects — never a raw objectId/assetId/proc.* token); when no
      // nameable source exists the unanchored fallback is used.
      const label = semanticLabelOrNull(worldObject);
      feedback.toast = this.makeToast(
        label
          ? `Nothing relevant was found on the ${label}.`
          : "Nothing relevant was found here.",
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

  /* ======================================================================
   * Phase 23 — witness interviews.
   *
   * The ONLY browser request is POST .../witnesses/{id}/interview with a
   * closed question type. Ids come exclusively from the player-safe bootstrap
   * list. Statements are stored in-memory (idempotent re-ask; no duplicate
   * button state / notebook line, no re-POST); statements that DISCOVERED
   * evidence ALSO flow into the existing records cache + knowledge snapshot,
   * so the notebook re-derives them after a reload from the server-persisted
   * discovery (never from a client-side statement store).
   * ==================================================================== */

  /** Snapshot of the player-safe witness list (empty on pre-23 servers). */
  witnessesSnapshot(): WitnessListEntryDTO[] {
    return [...this.witnessesValue];
  }

  /** The witness whose ON_SCENE person object is `objectId`, or null. */
  sceneWitnessByObjectId(objectId: string): WitnessListEntryDTO | null {
    return (
      this.witnessesValue.find(
        (witness) =>
          witness.presence === "ON_SCENE" &&
          (witness.sceneObjectId === objectId ||
            // Backend tolerance: when the list carries no explicit linkage, the
            // semantic person objectId IS the witness id itself.
            (witness.sceneObjectId == null && witness.witnessId === objectId)),
      ) ?? null
    );
  }

  /** The asked question types for one witness, in the closed panel order. */
  askedWitnessQuestionTypes(witnessId: string): WitnessQuestionType[] {
    const types: WitnessQuestionType[] = [];
    for (const asked of this.askedStatements.values()) {
      if (asked.witnessId === witnessId) types.push(asked.questionType);
    }
    return types.sort(
      (a, b) => WITNESS_QUESTION_ORDER.indexOf(a) - WITNESS_QUESTION_ORDER.indexOf(b),
    );
  }

  /** Cached statements of one witness (questionType -> statement). */
  witnessStatementCache(witnessId: string): ReadonlyMap<WitnessQuestionType, WitnessStatementDTO> {
    const cache = new Map<WitnessQuestionType, WitnessStatementDTO>();
    for (const asked of this.askedStatements.values()) {
      if (asked.witnessId === witnessId) cache.set(asked.questionType, asked.statement);
    }
    return cache;
  }

  /** All asked statements (the notebook "Witness statements" source). */
  askedWitnessStatementsSnapshot(): AskedWitnessStatement[] {
    return [...this.askedStatements.values()];
  }

  /** True when this (witness, question) was already answered this session. */
  hasAskedWitnessQuestion(witnessId: string, questionType: WitnessQuestionType): boolean {
    return this.askedStatements.has(witnessQuestionKey(witnessId, questionType));
  }

  /**
   * Ask ONE closed interview question. Idempotent: a re-ask returns the
   * cached statement WITHOUT a POST (no duplicate button state, no duplicate
   * notebook line) and never re-opens an evidence panel. A discovery flows
   * into the existing discovery/read machinery (record cached, knowledge
   * snapshot + scene-model flags updated). All failures map to short
   * player-safe messages — never raw internals.
   */
  async askWitness(witnessId: string, questionType: WitnessQuestionType): Promise<WitnessAskOutcome> {
    if (this.model === null || this.knowledge === null) {
      return { ok: false, error: { message: "The investigation has not loaded yet." }, cached: false };
    }
    const witness = this.witnessesValue.find((entry) => entry.witnessId === witnessId);
    if (!witness) {
      return { ok: false, error: { message: "That witness is not part of this playthrough." }, cached: false };
    }
    const key = witnessQuestionKey(witnessId, questionType);
    const asked = this.askedStatements.get(key);
    if (asked) {
      return {
        ok: true,
        witnessId,
        displayName: asked.displayName,
        questionType,
        statement: asked.statement,
        discovery: null,
        record: null,
        cached: true,
      };
    }
    const interview = this.services.interviewWitness;
    if (!interview) {
      return { ok: false, error: { message: "Witness interviews are not available for this case." }, cached: false };
    }

    let result: WitnessInterviewResponse;
    try {
      result = await interview(this.playthroughId, witnessId, questionType, this.token);
    } catch (error) {
      return { ok: false, error: { message: this.safeWitnessError(error) }, cached: false };
    }
    const parsed = parseWitnessInterviewResponse(result);
    const displayName = parsed.displayName !== "" ? parsed.displayName : witness.displayName;

    let record: EvidenceReadResultDTO | null = null;
    if (parsed.discovery !== null && parsed.discovery.record !== null) {
      const discovered = parsed.discovery.record;
      // Interview discovery reuses the existing machinery: the record is
      // cached (notebook/People + client-side panel state) and the
      // server-confirmed ids flow into the knowledge snapshot + scene-model
      // flags (discovered AND read — the interview response carries the whole
      // player-safe record, so the player has effectively read it).
      this.records.set(discovered.evidenceId, discovered);
      if (this.knowledge) {
        this.knowledge = {
          ...this.knowledge,
          discoveredEvidenceIds: sortedUnique([...this.knowledge.discoveredEvidenceIds, discovered.evidenceId]),
          readEvidenceIds: sortedUnique([...this.knowledge.readEvidenceIds, discovered.evidenceId]),
        };
      }
      this.syncSceneModelKnowledge();
      record = discovered;
    }

    const askedEntry: AskedWitnessStatement = {
      witnessId,
      displayName,
      questionType,
      statement: parsed.statement,
      evidenceId: record !== null ? record.evidenceId : null,
    };
    this.askedStatements.set(key, askedEntry);

    return {
      ok: true,
      witnessId,
      displayName,
      questionType,
      statement: parsed.statement,
      discovery:
        parsed.discovery !== null
          ? { newlyDiscovered: parsed.discovery.newlyDiscovered, record }
          : null,
      record,
      cached: false,
    };
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
    // DEF-072: the same server-derived knowledge that drives the summary strip
    // now flips the world-object flags/captions immediately (no reload needed).
    this.syncSceneModelKnowledge();
  }

  /**
   * DEF-072 — re-derive the scene-model entity flags from the current
   * server-authoritative knowledge snapshot. Deterministic, idempotent and
   * reference-stable: unchanged world objects keep their object identity.
   */
  private syncSceneModelKnowledge(): void {
    if (this.model === null || this.knowledge === null) return;
    this.model = applyKnowledgeToSceneModel(this.model, this.knowledge);
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
        // DEF-072: a completed read flips the model's `read` flag immediately
        // (the object list "· read" marker + summary strip stay in lockstep).
        this.syncSceneModelKnowledge();
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

  /** Phase 23 — map an interview failure to a short player-safe message. The
   *  server's typed failures (WITNESS_NOT_FOUND / QUESTION_NOT_AVAILABLE /
   *  QUESTION_ALREADY_ANSWERED / PLAYTHROUGH_NOT_ACTIVE / UNAUTHORIZED) never
   *  surface as raw text; 401/403 still carry the reset hint. */
  private safeWitnessError(error: unknown): string {
    if (isAuthorisationFailure(error)) {
      return "Your playthrough access is no longer valid. Reset the token from the Home page.";
    }
    if (error instanceof ApiError) {
      if (error.code === "QUESTION_ALREADY_ANSWERED") {
        return "You have already asked that question — the answer stays the same.";
      }
      if (
        error.status === 409 ||
        error.code === "WITNESS_NOT_FOUND" ||
        error.code === "QUESTION_NOT_AVAILABLE" ||
        error.code === "PLAYTHROUGH_NOT_ACTIVE"
      ) {
        return "That question is not available right now.";
      }
    }
    return "That question could not be answered. Please try again.";
  }
}

function sortedUnique(ids: string[]): string[] {
  return [...new Set(ids)].sort();
}