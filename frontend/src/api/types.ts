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
 */
export interface InvestigationBootstrapResponse {
  playthroughId: string;
  caseId: string;
  caseVersion: number;
  state: "PLAYING";
  playerKnowledge: PlayerKnowledgeDTO;
  scene: {
    location: InvestigationSceneLocationDTO;
    worldObjects: WorldObjectDTO[];
  };
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