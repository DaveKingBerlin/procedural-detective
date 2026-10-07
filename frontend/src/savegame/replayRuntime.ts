import { ApiError } from "../api/client";
import type {
  AccusationCandidatesDTO,
  AccusationRequest,
  AccusationResponse,
  EvidenceReadResultDTO,
  InteractionResultDTO,
  InvestigationBootstrapResponse,
  PlaythroughLifecycleState,
  PlayerKnowledgeDTO,
  RevealResponse,
  SubmittedAccusationDTO,
  WitnessInterviewResponse,
  WitnessListEntryDTO,
  WitnessQuestionType,
  WorldObjectDTO,
} from "../api/types";
import { REPLAY_CASE_VERSION, buildReplayReveal } from "./replayTruth";
import { isValidAccusationTime } from "./savegameTime";
import type { ReplayTruthV1, SavedCaseDefinition, SavegameEvidenceRecordV1 } from "./savegameV1";
import { REPLAY_CASE_ID, REPLAY_PLAYTHROUGH_ID } from "./savegameV1";

/**
 * Phase 32 — the browser-LOCAL replay runtime (ADR-003 §1 / Phase32 §4/§12).
 *
 * Given an IMMUTABLE `SavedCaseDefinition`, a {@link FreshReplayState}
 * substitutes for the backend across the THREE existing player flows:
 *
 *   - `getInvestigation`  -> a fresh PLAYING bootstrap: `playerKnowledge`
 *     all-empty, the scene from the saved world objects, the saved
 *     candidates/witnesses, synthetic ids. The world objects are RE-PROJECTED
 *     fresh (PD-SEC-01 semantics: no undiscovered evidence id is ever
 *     published) — the exported archive is an honest spoiler archive, but the
 *     REPLAY starts with knowledge EMPTY and THE TRUTH hidden (Phase32 §2/§6).
 *   - `interactObject`    -> the saved object's disposition + merges
 *     discovered/read into the fresh state.
 *   - `readRecord`        -> the saved evidence record (discovery-gated).
 *   - `interviewWitness`  -> the saved deterministic statement for the closed
 *     recorded question, or the frozen neutral answer (backend
 *     NEUTRAL_SUMMARY) for un-grounded questions.
 *   - `submitAccusation`  -> validates membership + the frozen crimeTime
 *     grammar (SAME rules as the server) and stores the immutable
 *     accusation; correctness is revealed ONLY through `getReveal`.
 *   - `getReveal`         -> 403 REVEAL_NOT_AVAILABLE until accused (the SAME
 *     gate as the server), then evaluates against ReplayTruthV1 with the
 *     EXACT server scoring rules and returns the frozen RevealResponse shape.
 *
 * Lifecycle transitions PLAYING -> ACCUSED -> REVEALED live HERE, in memory.
 * Nothing is ever persisted to browser storage (Phase32 §24), no network call
 * is ever made, and the imported truth drives ONLY this replay.
 */

/** The frozen neutral witness answer for un-grounded questions (backend
 *  `app/domain/witness.py::NEUTRAL_SUMMARY`). */
export const REPLAY_NEUTRAL_SUMMARY = "No. Nothing stood out to me.";

/** The witness-kind evidence family that can ground an interview answer
 *  (mirror of backend WITNESS_KINDS). */
export const REPLAY_WITNESS_KINDS: readonly string[] = Object.freeze([
  "witness_observation",
  "witness_statement",
  "statement",
  "testimonial",
  "suspect_statement",
]);

export type ReplayPhase = "PLAYING" | "ACCUSED" | "REVEALED";

export class FreshReplayState {
  private readonly worldObjects: readonly WorldObjectDTO[];
  private readonly recordById: ReadonlyMap<string, SavegameEvidenceRecordV1>;
  private readonly candidates: AccusationCandidatesDTO;
  private readonly witnesses: readonly WitnessListEntryDTO[];
  private readonly exportedAt: string;
  private readonly environmentId: string | null;
  private readonly location: { locationId: string; name: string };
  private readonly replayTruth: ReplayTruthV1;
  private readonly witnessStatements: ReadonlyMap<string, ReadonlyMap<WitnessQuestionType, string>>;

  // ---------------- fresh replay state (per-loaded-file) ----------------
  private readonly discovered = new Set<string>();
  private readonly read = new Set<string>();
  private accusation: SubmittedAccusationDTO | null = null;
  private phase: ReplayPhase = "PLAYING";
  private cachedReveal: RevealResponse | null = null;

  constructor(definition: SavedCaseDefinition) {
    this.worldObjects = definition.scene.worldObjects;
    this.candidates = definition.candidates;
    this.witnesses = definition.witnesses;
    this.exportedAt = definition.exportedAt;
    this.environmentId = definition.scene.environmentId;
    this.location = { ...definition.scene.location };
    this.replayTruth = definition.replayTruth;

    const records = new Map<string, SavegameEvidenceRecordV1>();
    for (const record of definition.evidence) {
      records.set(record.evidenceId, record);
    }
    this.recordById = records;

    // Index the recorded interview statements (witnessId -> question -> record).
    const statements = new Map<string, Map<WitnessQuestionType, string>>();
    for (const record of definition.evidence) {
      const content = record.content;
      if (typeof content.witnessId !== "string" || content.witnessId === "") continue;
      if (!REPLAY_WITNESS_KINDS.includes(record.kind)) continue;
      if (typeof content.questionType !== "string" || content.questionType === "") continue;
      if (!isClosedQuestionType(content.questionType)) continue;
      const byRecord = statements.get(content.witnessId) ?? new Map<WitnessQuestionType, string>();
      if (!byRecord.has(content.questionType)) {
        byRecord.set(content.questionType, record.evidenceId);
      }
      statements.set(content.witnessId, byRecord);
    }
    this.witnessStatements = statements;
  }

  /* ------------------------- observation -------------------------------- */

  get phaseValue(): ReplayPhase {
    return this.phase;
  }

  /** Snapshot of the player-observable knowledge of this fresh replay. */
  knowledgeSnapshot(): PlayerKnowledgeDTO {
    return {
      discoveredEvidenceIds: [...this.discovered].sort(),
      readEvidenceIds: [...this.read].sort(),
      visitedLocationIds: [],
    };
  }

  /** The current lifecycle state (the same closed vocabulary as the server). */
  lifecycleState(): PlaythroughLifecycleState {
    return this.phase;
  }

  candidatesSnapshot(): AccusationCandidatesDTO {
    return this.candidates;
  }

  /* ------------------------- bootstrap ---------------------------------- */

  /**
   * A fresh PLAYING bootstrap exactly like a brand-new server playthrough:
   * knowledge empty, `state: "PLAYING"`, and the world objects re-projected
   * so NO undiscovered evidence linkage is published (PD-SEC-01 semantics).
   * The returned objects are NEW objects — the saved full-knowledge archive
   * is never exposed through this DTO.
   */
  freshBootstrap(): InvestigationBootstrapResponse {
    const worldObjects: WorldObjectDTO[] = this.worldObjects.map((obj) => {
      const fresh: WorldObjectDTO = {
        objectId: obj.objectId,
        assetId: obj.assetId,
        assetType: obj.assetType,
        subtype: obj.subtype,
        locationId: obj.locationId,
        anchor: obj.anchor,
        interaction: obj.interaction,
        evidenceId: null,
        discovered: false,
        read: false,
      };
      if (obj.generated !== undefined && obj.generated !== null) {
        fresh.generated = obj.generated;
      }
      if (obj.displayLabel !== undefined && obj.displayLabel !== null) {
        fresh.displayLabel = obj.displayLabel;
      }
      return fresh;
    });
    return {
      playthroughId: REPLAY_PLAYTHROUGH_ID,
      caseId: REPLAY_CASE_ID,
      caseVersion: REPLAY_CASE_VERSION,
      state: this.phase,
      playerKnowledge: this.knowledgeSnapshot(),
      scene: {
        environmentId: this.environmentId ?? "apartment",
        location: this.location,
        worldObjects,
      },
      candidates: this.candidates,
      witnesses: this.witnesses.length > 0 ? [...this.witnesses] : [],
    };
  }

  /* ------------------------- investigation actions ---------------------- */

  /**
   * Universal object inspection: an evidence-linked placement runs the
   * discovery (idempotent; `state` flips to "already-discovered" on repeat),
   * a decorative placement returns the Phase 19F non-discovery result.
   * Unknown ids never reach the player (safe gameplay error).
   */
  interactObject(objectId: string, _interaction: string): InteractionResultDTO {
    const worldObject = this.worldObjects.find((obj) => obj.objectId === objectId);
    if (worldObject === undefined) {
      throw new ApiError(409, "INTERACTION_NOT_ALLOWED", "That object is not part of this replay.", null);
    }
    const savedInteraction = worldObject.interaction;
    if (worldObject.evidenceId !== null) {
      const record = this.recordById.get(worldObject.evidenceId);
      const newly = !this.discovered.has(worldObject.evidenceId);
      this.discovered.add(worldObject.evidenceId);
      return {
        objectId,
        interaction: savedInteraction,
        evidenceId: worldObject.evidenceId,
        discovery: {
          evidenceId: worldObject.evidenceId,
          kind: record?.kind ?? "",
          title: record?.title ?? worldObject.evidenceId,
          interaction: savedInteraction,
          state: newly ? "discovered" : "already-discovered",
        },
        result: "interacted",
        inspection: { relevant: true, label: null },
      };
    }
    return {
      objectId,
      interaction: savedInteraction,
      evidenceId: null,
      discovery: null,
      result: "interacted",
      inspection: { relevant: false, label: null },
    };
  }

  /** Read a DISCOVERED record (idempotent). Playthrough state (openedAt/
   *  readByPlayer) is synthetic — the export deliberately owns none. */
  readRecord(recordId: string): EvidenceReadResultDTO {
    if (!this.discovered.has(recordId)) {
      throw new ApiError(404, "NOT_FOUND", "This evidence has not been discovered yet.", null);
    }
    const record = this.recordById.get(recordId);
    if (record === undefined) {
      throw new ApiError(404, "NOT_FOUND", "This evidence is not available in the replay.", null);
    }
    this.read.add(recordId);
    return this.toReadDto(record);
  }

  /** One closed interview question: the saved deterministic statement for the
   *  exact recorded (witness, question), or the frozen neutral answer. A
   *  statement-grounded question discovers its record (newly-discovered flow
   *  exactly mirrors the live interview). */
  interviewWitness(witnessId: string, questionType: WitnessQuestionType): WitnessInterviewResponse {
    const witness = this.witnesses.find((entry) => entry.witnessId === witnessId);
    const displayName = witness?.displayName ?? witnessId;
    const statementRecordId = this.witnessStatements.get(witnessId)?.get(questionType);

    if (statementRecordId !== undefined) {
      const record = this.recordById.get(statementRecordId);
      if (record !== undefined) {
        const newly = !this.discovered.has(statementRecordId);
        this.discovered.add(statementRecordId);
        this.read.add(statementRecordId);
        return {
          witnessId,
          displayName,
          questionType,
          statement: this.statementDtoOf(record),
          discovery: { newlyDiscovered: newly, record: this.toReadDto(record) },
        };
      }
    }

    return {
      witnessId,
      displayName,
      questionType,
      statement: { summary: REPLAY_NEUTRAL_SUMMARY, observations: [] },
      discovery: null,
    };
  }

  /* ------------------------- accusation ---------------------------------- */

  /**
   * The immutable accusation write (SAME rules as the server): membership in
   * the saved candidate universes + the frozen crimeTime grammar. The
   * response NEVER reveals correctness — evaluation happens ONLY in
   * getReveal (separation contract C, Phase7).
   */
  submitAccusation(body: AccusationRequest): AccusationResponse {
    if (this.phase !== "PLAYING" || this.accusation !== null) {
      throw new ApiError(409, "CASE_ALREADY_SUBMITTED", "An accusation was already submitted.", null);
    }
    const membershipError = this.validateMembership(body);
    if (membershipError !== null) {
      throw new ApiError(422, "VALIDATION_ERROR", membershipError, null);
    }
    // DEF-050 / ADV-32F-05: the SAME frozen crimeTime grammar as the live
    // server (app/services/accusation.py::parse_accusation_time): a zone-less
    // full ISO timestamp ("2026-09-11T22:17:00") is REJECTED 422 exactly like
    // the server, never accepted-then-scored-wrong. The same inputs the server
    // accepts (full ISO WITH offset/zone, or a bare 24h time-of-day) still do.
    if (!isValidAccusationTime(body.crimeTime)) {
      throw new ApiError(422, "VALIDATION_ERROR", "crimeTime is invalid", null);
    }
    const accusation: SubmittedAccusationDTO = {
      murdererId: body.murdererId,
      motiveId: body.motiveId,
      weaponId: body.weaponId,
      crimeTime: body.crimeTime,
    };
    this.accusation = accusation;
    this.phase = "ACCUSED";
    return {
      playthroughId: REPLAY_PLAYTHROUGH_ID,
      caseId: REPLAY_CASE_ID,
      caseVersion: REPLAY_CASE_VERSION,
      status: "ACCUSED",
      accusation,
    };
  }

  /** The reveal: 403 REVEAL_NOT_AVAILABLE until accused (the SAME lifecycle
   *  gate as the server), then evaluated against the saved ReplayTruthV1 with
   *  the EXACT server scoring rules; idempotent afterwards. */
  getReveal(): RevealResponse {
    if (this.accusation === null || this.phase === "PLAYING") {
      throw new ApiError(403, "REVEAL_NOT_AVAILABLE", "No accusation has been submitted for this replay yet.", null);
    }
    if (this.cachedReveal !== null) {
      return this.cachedReveal;
    }
    const reveal = buildReplayReveal(this.replayTruth, this.accusation, this.sortedRecords());
    this.phase = "REVEALED";
    this.cachedReveal = reveal;
    return reveal;
  }

  /* ------------------------- private helpers ----------------------------- */

  private sortedRecords(): readonly SavegameEvidenceRecordV1[] {
    return [...this.recordById.values()].sort((a, b) =>
      a.evidenceId < b.evidenceId ? -1 : a.evidenceId > b.evidenceId ? 1 : 0,
    );
  }

  private validateMembership(body: AccusationRequest): string | null {
    const suspectIds = this.candidates.suspects.map((entry) => entry.id);
    const motiveIds = this.candidates.motives.map((entry) => entry.id);
    const weaponIds = this.candidates.weapons.map((entry) => entry.id);
    if (!suspectIds.includes(body.murdererId)) {
      return "The accusation was rejected because one of the options has become invalid.";
    }
    if (!motiveIds.includes(body.motiveId)) {
      return "The accusation was rejected because one of the options has become invalid.";
    }
    if (!weaponIds.includes(body.weaponId)) {
      return "The accusation was rejected because one of the options has become invalid.";
    }
    return null;
  }

  private toReadDto(record: SavegameEvidenceRecordV1): EvidenceReadResultDTO {
    return {
      evidenceId: record.evidenceId,
      kind: record.kind,
      title: record.title,
      description: record.description,
      openedAt: this.exportedAt,
      readByPlayer: true,
      // The savegame evidence content IS the closed EvidenceContentDTO shape
      // ({renderType?} + kind-allowlisted keys); the interface adds the
      // renderer index signature, so the validated object is asserted once.
      content: record.content as unknown as EvidenceReadResultDTO["content"],
    };
  }

  /** The WitnessStatementDTO projected from the saved record content: the
   *  summary + the recorded statement text as one observation (the export
   *  owns the rendered text, not the backend's derived observation rows). */
  private statementDtoOf(record: SavegameEvidenceRecordV1): { summary: string; observations: { time: string | null; text: string }[] } {
    const content = record.content;
    const summary = typeof content.summary === "string" && content.summary !== "" ? content.summary : record.title;
    const statement = typeof content.statement === "string" ? content.statement : "";
    return {
      summary,
      observations: statement !== "" ? [{ time: null, text: statement }] : [],
    };
  }
}

function isClosedQuestionType(value: string): value is WitnessQuestionType {
  return (
    value === "OBSERVATION" ||
    value === "TIME" ||
    value === "SOUND" ||
    value === "PERSON" ||
    value === "OBJECT" ||
    value === "LOCATION"
  );
}

/**
 * Build the in-memory substitutes for the THREE player service interfaces
 * (`InvestigationServices` / `AccusationServices` / `RevealServices`). The
 * flows treat playthroughId/token as opaque strings passed straight to the
 * services — synthetic ids and an empty token are acceptable because these
 * implementations ignore them.
 */
export function buildReplayServices(state: FreshReplayState): {
  getInvestigation(playthroughId: string, token: string): Promise<InvestigationBootstrapResponse>;
  interactObject(playthroughId: string, objectId: string, interaction: string, token: string): Promise<InteractionResultDTO>;
  readRecord(playthroughId: string, recordId: string, token: string): Promise<EvidenceReadResultDTO>;
  interviewWitness?(
    playthroughId: string,
    witnessId: string,
    questionType: WitnessQuestionType,
    token: string,
  ): Promise<WitnessInterviewResponse>;
  submitAccusation(playthroughId: string, body: AccusationRequest, token: string): Promise<AccusationResponse>;
  getReveal(playthroughId: string, token: string): Promise<RevealResponse>;
} {
  return {
    getInvestigation: () => Promise.resolve(state.freshBootstrap()),
    interactObject: (_playthroughId, objectId, interaction, _token) =>
      Promise.resolve(state.interactObject(objectId, interaction)),
    readRecord: (_playthroughId, recordId, _token) => Promise.resolve(state.readRecord(recordId)),
    interviewWitness: (_playthroughId, witnessId, questionType, _token) =>
      Promise.resolve(state.interviewWitness(witnessId, questionType)),
    submitAccusation: (_playthroughId, body, _token) => Promise.resolve(state.submitAccusation(body)),
    getReveal: () => Promise.resolve(state.getReveal()),
  };
}