/**
 * Typed DTOs mirroring the Phase 2 backend contract exactly.
 *
 * Contract (fixed, implemented in parallel by the backend agent):
 *   GET {base}/api/v1/health      -> 200 {"status":"ok","service":"procedural-detective","version":"0.1.0"}
 *   GET {base}/api/v1/readiness   -> 200 {"status":"ready","database":"ok","migrations":"ok"}
 *                                   or 503 {"error":{"code":"NOT_READY","message":"...","details":null}}
 *   All non-2xx:                    {"error":{"code":"<SCREAMING_SNAKE>","message":"<text>","details":<object|null>}}
 *
 * These types are intentionally duplicated on the client side (a shared/
 * package arrives only once the contract grows — Phase 2 spec).
 */

/** Response of GET {base}/api/v1/health. */
export interface HealthResponse {
  status: string; // "ok"
  service: string; // "procedural-detective"
  version: string; // "0.1.0"
}

/** Response of GET {base}/api/v1/readiness. */
export interface ReadinessResponse {
  status: string; // "ready"
  database: string; // "ok"
  migrations: string; // "ok"
}

/** Structured error body returned for every non-2xx response. */
export interface ErrorEnvelope {
  error: {
    code: string; // SCREAMING_SNAKE
    message: string;
    details: object | null;
  };
}

/* ======================================================================
 * Phase 6 — browser investigation contract (frozen, implemented in
 * parallel by the backend agent). These DTO shapes are the ONLY thing the
 * client may trust from the investigation endpoints; everything crossing
 * the trust boundary is re-validated by src/scene/validation.ts.
 * ==================================================================== */

/** Server-authoritative discovery disposition returned by interact/discover. */
export type DiscoveryState = "discovered" | "already-discovered";

/** Player-observable knowledge scoped to exactly one playthrough (REQUIREMENTS 36). */
export interface PlayerKnowledgeDTO {
  discoveredEvidenceIds: string[];
  readEvidenceIds: string[];
  visitedLocationIds: string[];
}

/** A single interactable world object in the player-safe WorldGraph DTO (REQUIREMENTS 26/27). */
export interface WorldObjectDTO {
  objectId: string;
  assetId: string;
  assetType: string;
  subtype: string | null;
  locationId: string;
  anchor: string;
  interaction: string;
  evidenceId: string | null;
  discovered: boolean;
  read: boolean;
}

/** Current investigation location, as published in the player-safe scene. */
export interface InvestigationSceneLocationDTO {
  locationId: string;
  name: string;
}

/**
 * 200 body of GET /api/v1/playthroughs/{playthrough_id}/investigation.
 *
 * NOTE: the payload carries NO coordinates — geometry is derived client-side
 * from (locationId, anchor, assetId) via the anchor/asset registries.
 *
 * Phase 7 amendment (frozen): the bootstrap GAINS a player-safe `candidates`
 * block used ONLY by the accusation UI. Candidates carry no correctness
 * markers and MUST be rendered in the exact order the server returns them
 * (the server sorts suspects by id; the client never re-orders or marks a
 * winner). `state` reflects the frozen playthrough lifecycle — the scene is
 * still playable while ACCUSED/REVEALED, so only CREATED/unknown values are
 * rejected.
 */
export interface InvestigationBootstrapResponse {
  playthroughId: string;
  caseId: string;
  caseVersion: number;
  state: PlaythroughLifecycleState;
  playerKnowledge: PlayerKnowledgeDTO;
  scene: {
    location: InvestigationSceneLocationDTO;
    worldObjects: WorldObjectDTO[];
  };
  candidates: AccusationCandidatesDTO;
}

/* ======================================================================
 * Phase 7 — accusation & reveal contract (frozen, implemented in parallel
 * by the backend agent).
 * ==================================================================== */

/** Frozen playthrough lifecycle: CREATED -> PLAYING -> ACCUSED -> REVEALED. */
export type PlaythroughLifecycleState = "PLAYING" | "ACCUSED" | "REVEALED";

/** One WHO candidate (SUSPECT_ELIGIBLE universe). The server never marks a winner. */
export interface SuspectCandidateDTO {
  id: string;
  name: string;
}

/** One WHY candidate (MOTIVE_CANDIDATE universe). */
export interface MotiveCandidateDTO {
  id: string;
  label: string;
}

/** One WEAPON candidate (POTENTIAL_WEAPON universe). */
export interface WeaponCandidateDTO {
  id: string;
  assetId: string;
  name: string;
}

/**
 * Player-safe candidate universes published in the investigation bootstrap.
 * `suspects` arrive SORTED ALPHABETICALLY by id; the client preserves that
 * order exactly and never derives/renders any correctness/winnership marker.
 */
export interface AccusationCandidatesDTO {
  suspects: SuspectCandidateDTO[];
  motives: MotiveCandidateDTO[];
  weapons: WeaponCandidateDTO[];
}

/** POST .../accusation request body. crimeTime is a bare 24h time "HH:MM:SS". */
export interface AccusationRequest {
  murdererId: string;
  motiveId: string;
  weaponId: string;
  crimeTime: string;
}

/** The immutable accepted accusation echoed by 200/409/reveal responses. */
export interface SubmittedAccusationDTO {
  murdererId: string;
  motiveId: string;
  weaponId: string;
  crimeTime: string;
}

/** 200 body of POST .../accusation — ACCEPTED, but reveals NO truth. */
export interface AccusationResponse {
  playthroughId: string;
  caseId: string;
  caseVersion: number;
  status: "ACCUSED";
  accusation: SubmittedAccusationDTO;
}

/** Canonical truth block of the reveal DTO (explicit server allowlist). */
export interface RevealTruthDTO {
  murdererId: string;
  murdererName: string;
  motiveId: string;
  motiveLabel: string;
  weaponId: string;
  weaponName: string;
  /** Full ISO timestamp carrying the authoritative local time-of-day. */
  crimeTime: string;
}

/** Per-dimension deterministic evaluation produced by the server. */
export interface RevealResultDTO {
  murdererCorrect: boolean;
  motiveCorrect: boolean;
  weaponCorrect: boolean;
  timeCorrect: boolean;
  overall: "solved" | "incorrect";
}

export interface RevealScoreDTO {
  correctDimensions: number;
  totalDimensions: number;
}

export interface TimelineEntryDTO {
  time: string; // ISO
  description: string;
}

export interface ExplanationEvidenceDTO {
  evidenceId: string;
  title: string;
  point: string;
}

/**
 * 200 body of GET .../reveal — the explicit allowlist the reveal screen
 * renders. Every field crossing the trust boundary is re-parsed by
 * src/reveal/revealValidation.ts (unknown fields dropped).
 */
export interface RevealResponse {
  playthroughId: string;
  caseId: string;
  caseVersion: number;
  status: "REVEALED";
  truth: RevealTruthDTO;
  player: { accusation: SubmittedAccusationDTO };
  result: RevealResultDTO;
  score: RevealScoreDTO;
  timeline: TimelineEntryDTO[];
  explanation: { evidence: ExplanationEvidenceDTO[] };
}

/** Discovery half of an interaction/discover response. */
export interface DiscoveryResultDTO {
  evidenceId: string;
  kind: string;
  title: string;
  interaction: string;
  state: DiscoveryState;
}

/** 200 body of POST .../objects/{object_id}/interact. */
export interface InteractionResultDTO {
  objectId: string;
  interaction: string;
  evidenceId: string | null;
  discovery: DiscoveryResultDTO | null;
  result: "interacted";
}

/**
 * 200 body of GET .../records/{record_id} — the fully readable,
 * allowlisted player payload for one discovered evidence record.
 */
export interface EvidenceReadResultDTO {
  evidenceId: string;
  kind: string;
  title: string;
  description: string | null;
  openedAt: string;
  readByPlayer: true;
  content: Record<string, unknown>;
}

/* ======================================================================
 * Phase 8 — prompt-to-case journey contract (anonymous session -> case
 * -> generation progress -> playthrough). Mirrors the backend schemas
 * exactly (sessions.py / cases.py / generations.py / playthroughs.py).
 * ==================================================================== */

/** POST /api/v1/sessions/anonymous -> 201 (no auth). */
export interface AnonymousSessionResponse {
  anonymousSessionToken: string;
  quotaWindowEndsAt: number;
}

/** POST /api/v1/cases -> 201 (Bearer anonymousSessionToken). */
export interface CreateCaseResponse {
  caseId: string;
  generationId: string;
  generationAttemptId: string;
  /** Appears exactly once, at creation — never stored by the client. */
  creatorAccessToken: string;
  status: string;
}

/** GET /api/v1/generations/{generationId} -> 200 (Bearer creatorAccessToken). */
export interface GenerationStatusResponse {
  caseId: string;
  generationId: string;
  /** Sanitized lifecycle status (PUBLISHED / FAILED / RUNNING / ...). */
  status: string;
  /** 0..100 — derived by the backend from the durable generation snapshot. */
  progress: number;
  /** Internal-safe stage text (mapped client-side to friendly labels). */
  stage: string | null;
}

/** POST /api/v1/cases/{caseId}/versions/{caseVersion}/playthroughs -> 201. */
export interface CreatePlaythroughResponse {
  playthroughId: string;
  caseId: string;
  caseVersion: number;
  /** Appears exactly once, at creation — the only place this token is seen. */
  playthroughAccessToken: string;
  status: string;
}