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

/* ======================================================================
 * Phase 13 — declarative procedural asset render metadata.
 *
 * The `generated` block of a WorldObjectDTO: the EXACT camelCase
 * GeneratedAssetDefinition document the backend compiler emits through
 * `to_definition_json()` (backend/app/assets/compiler.py). It is the frozen
 * contract the frontend RENDERS as safe local primitives — fully bounded,
 * player-safe, declarative render metadata and nothing executable.
 * ==================================================================== */

/** One {x, y, z} float component (finite and bounded per axis). */
export interface GeneratedVec3DTO {
  x: number;
  y: number;
  z: number;
}

/** The four renderer-supported composition primitives (nothing else). */
export type GeneratedPrimitiveKind = "box" | "cylinder" | "sphere" | "plane";

/** One part transform in local space (Euler radians; bounded by validation). */
export interface GeneratedTransformDTO {
  position: GeneratedVec3DTO;
  rotation: GeneratedVec3DTO;
  scale: GeneratedVec3DTO;
}

/** One resolved definition part (renderer-facing; color is the resolved #RRGGBB). */
export interface GeneratedPartDTO {
  id: string;
  role: string;
  primitive: GeneratedPrimitiveKind;
  transform: GeneratedTransformDTO;
  color: string; // #RRGGBB — never a material token, never a derived tone
  parentId: string | null;
}

/** The derived picking box (scale only), bounded by validation. */
export interface GeneratedHitboxDTO {
  scale: GeneratedVec3DTO;
}

/** The full validated declarative generated asset definition (frozen contract). */
export interface GeneratedAssetDefinition {
  compilerVersion: number;
  schemaVersion: number;
  assetId: string;
  canonicalName: string;
  dimensions: GeneratedVec3DTO;
  parts: GeneratedPartDTO[];
  hitbox: GeneratedHitboxDTO;
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
  /**
   * Phase 13: optional declarative render metadata. Projected by the backend
   * ONLY for `proc.*` assets with a validated embedded generatedDefinition;
   * ABSENT for every other asset (the client ignores it for non-proc assets —
   * the field never overrides catalog identity).
   */
  generated?: GeneratedAssetDefinition | null;
  /**
   * Phase 26 (C5) — the SEMANTIC humanized label of the object, published by
   * the backend ONLY for objects resolved through a trusted asset fallback
   * (provenance NORMALIZED_EXACT / CATEGORY_FALLBACK / GENERIC_FALLBACK): the
   * fallback turns the original asset into a known catalog asset so it
   * renders, but the object's semantic identity stays the original (e.g.
   * "Bronze Ceremonial Ice Pick" vs the substitute "Kitchen knife" label).
   * Absent/null for every other object — the client then uses the catalog
   * label (or null for unknown assets) exactly as before.
   */
  displayLabel?: string | null;
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
 *
 * Phase 23 (witness interviews): the bootstrap OPTIONALLY gains a player-safe
 * `witnesses` list. It is ABSENT from servers that do not publish the Phase
 * 23 interview feature (the client then renders NO witness UI — graceful
 * degradation, never a crash). The list carries ONLY witness ids, display
 * names, the closed presence enum and the optional scene-object linkage —
 * NO statement or question-availability content (interview answers are only
 * fetched through POST .../witnesses/{id}/interview AFTER the player asks).
 */
export interface InvestigationBootstrapResponse {
  playthroughId: string;
  caseId: string;
  caseVersion: number;
  state: PlaythroughLifecycleState;
  playerKnowledge: PlayerKnowledgeDTO;
  scene: {
    /** Phase 11: the exact environment kit id chosen by the backend resolver. */
    environmentId: string;
    location: InvestigationSceneLocationDTO;
    worldObjects: WorldObjectDTO[];
  };
  candidates: AccusationCandidatesDTO;
  /**
   * Phase 23 — player-safe witness list (ids + display names ONLY). Absent on
   * pre-23 servers; never contains statements or hidden question availability.
   */
  witnesses?: WitnessListEntryDTO[] | null;
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
 * One evidence-backed proof point in a post-reveal proof-board dimension
 * (Phase 18C). Identical in shape to the flat explanation entries.
 */
export interface ExplanationPointDTO {
  evidenceId: string;
  title: string;
  point: string;
}

/**
 * Phase 18C — post-reveal proof-board grouping the backend adds to the
 * reveal DTO: the flat `explanation.evidence` list grouped per dimension
 * (WHO / WHY / WEAPON / WHEN). Always present in REVEALED responses from
 * the current backend; the client MUST treat absence (an older server) or
 * malformation as a fall back to the flat list — never a crash.
 */
export interface RevealExplanationDimensionsDTO {
  who: ExplanationPointDTO[];
  why: ExplanationPointDTO[];
  weapon: ExplanationPointDTO[];
  when: ExplanationPointDTO[];
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
  explanation: {
    evidence: ExplanationEvidenceDTO[];
    /** Post-reveal proof-board grouping; absent/null from pre-18C servers. */
    dimensions?: RevealExplanationDimensionsDTO | null;
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

/**
 * Phase 19F — the universal object-inspection block of an interact response.
 *
 * For a NON-EVIDENCE published semantic object the backend answers the
 * interact call with `inspection: { relevant: false, label: "<Humanized
 * Label>" }` (discovery:null, evidenceId:null — the object was merely
 * inspected; no evidence, no solver state). Evidence-linked interactions MAY
 * also carry an inspection block (relevant:true + a label) but the frontend
 * MUST treat it as optional — `discovery`/`evidenceId` remain the
 * authoritative evidence path.
 */
export interface ObjectInspectionDTO {
  /** False for decorative/non-evidence objects; true for evidence-linked ones. */
  relevant: boolean;
  /** The humanized semantic label ("Table", "Kitchen knife", ...) — null-safe. */
  label: string | null;
}

/** 200 body of POST .../objects/{object_id}/interact. */
export interface InteractionResultDTO {
  objectId: string;
  interaction: string;
  evidenceId: string | null;
  discovery: DiscoveryResultDTO | null;
  result: "interacted";
  /**
   * Phase 19F — universal inspection block. Optional: older/evidence-only
   * interactions may omit it, and the frontend never depends on it for the
   * discovery flow (evidenceId/discovery stay authoritative).
   */
  inspection?: ObjectInspectionDTO | null;
}

/* ======================================================================
 * Phase 23 — witness interview contract (implemented in parallel by the
 * backend agent).
 *
 * REST surface the browser needs (the ONLY witness requests the client
 * ever makes):
 *   POST {base}/api/v1/playthroughs/{playthrough_id}/witnesses/{witnessId}
 *        /interview
 *        body {"questionType": "OBSERVATION|TIME|PERSON|OBJECT|LOCATION|SOUND"}
 *        -> 200 WitnessInterviewResponse (deterministic player-safe statement)
 *   GET {base}/api/v1/witnesses/{witnessId} (public witness view: id,
 *        displayName, presence, available question types). The client does
 *        NOT call this at runtime — the bootstrap witness list already
 *        carries the id/displayName/presence the panel needs and v1 makes
 *        ALL SIX question types available for every witness.
 *
 * Safety model:
 *   - witness ids reach the browser ONLY through the player-safe bootstrap
 *     `witnesses` list (a hidden person is never enumerated);
 *   - no statement content exists on the client before a question is asked;
 *   - the closed question enum is the only thing the client ever POSTs.
 * ==================================================================== */

/** The closed Phase 23 interview question universe (Phase23 §4). */
export type WitnessQuestionType = "OBSERVATION" | "TIME" | "PERSON" | "OBJECT" | "LOCATION" | "SOUND";

/** Closed witness presence modes (Phase23 §17) — a witness is either
 *  represented in the 3D scene or reachable only through the UI. */
export type WitnessPresence = "ON_SCENE" | "REMOTE_STATEMENT";

/**
 * One player-safe witness list entry of the investigation bootstrap
 * (Phase23 §15/§16). It carries ONLY the witness identity + presence and the
 * optional linkage to the ON_SCENE person world object — NEVER statements,
 * NEVER otherwise-hidden person data, NEVER question-availability semantics.
 */
export interface WitnessListEntryDTO {
  /** The semantic witness/person id (player-safe published id). */
  witnessId: string;
  /** Player-safe display name ("Lisa King-Queen"). Untrusted text — render as text. */
  displayName: string;
  presence: WitnessPresence;
  /**
   * ON_SCENE only: the world-object id of the pickable person representation
   * in the 3D scene (Phase 19F semantic picking resolves child meshes to this
   * id). Absent/null for REMOTE_STATEMENT witnesses (and tolerated when the
   * backend publishes the person under an objectId equal to `witnessId`).
   */
  sceneObjectId?: string | null;
}

/** One structured witness observation (Phase23 §5). `time` is optional;
 *  when present it is concrete player-safe clock text ("23:42" or ISO). */
export interface WitnessObservationDTO {
  time?: string | null;
  text: string;
}

/** The player-safe deterministic witness statement (Phase23 §5). */
export interface WitnessStatementDTO {
  summary: string;
  observations: WitnessObservationDTO[];
}

/** The discovery half of an interview response — reuses the EXISTING
 *  discovery machinery (idempotent; the record is an ordinary player-safe
 *  evidence record that flows into discoveredEvidenceIds/readEvidenceIds). */
export interface WitnessInterviewDiscoveryDTO {
  newlyDiscovered: boolean;
  record: EvidenceReadResultDTO;
}

/** 200 body of POST .../witnesses/{witnessId}/interview. Every text field is
 *  UNTRUSTED generated text — the client renders it as text only. */
export interface WitnessInterviewResponse {
  witnessId: string;
  displayName: string;
  questionType: WitnessQuestionType;
  statement: WitnessStatementDTO;
  discovery: WitnessInterviewDiscoveryDTO | null;
}

/* ======================================================================
 * Phase 19G — closed evidence render-type contract (implemented in
 * parallel by the backend agent).
 *
 * EvidenceReadResultDTO.content may now carry a closed `renderType` that
 * the backend derives DETERMINISTICALLY. It is DECLARATIVE metadata only:
 * a KEY into the frontend's own explicit renderer map — it can never map
 * to code, dynamic component names, templates or HTML. Absence of
 * `renderType` (an older server or a non-structure payload) is interpreted
 * as the GENERIC_TEXT fallback and keeps the legacy kind-based viewers.
 * Additive: every previous content shape stays valid.
 * ==================================================================== */

/** Closed evidence render-type universe (Phase 19G §3/§13). */
export type EvidenceRenderType =
  | "GENERIC_TEXT"
  | "ACTIVITY_LOG"
  | "FORENSIC_COMPARISON"
  | "MESSAGE"
  | "DOCUMENT"
  | "BODY_OBSERVATION"
  | "TIMELINE";

/** One typed activity-log/timeline entry (Phase 19G §4/§7). `time` is the
 *  concrete player-visible time text the server sent (e.g. "22:11"). */
export interface EvidenceActivityLogEntryDTO {
  time: string;
  text: string;
}

/**
 * The closed player-safe structured evidence payload. `renderType` is the
 * only semantic key; every other field is plain text/array content that the
 * matching closed renderer shows as TEXT (unknown keys are ignored).
 */
export interface EvidenceContentDTO {
  renderType?: EvidenceRenderType | null;
  [key: string]: unknown;
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
  /** Player-safe structured content; may carry a closed `renderType` (Phase
   *  19G). Absent/unknown renderType falls back to GENERIC_TEXT rendering. */
  content: EvidenceContentDTO;
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
  failureCode?: string | null;
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
  failureCode?: string | null;
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

/* ======================================================================
 * Phase 16 — generation-mode capabilities contract (frozen, implemented in
 * parallel by the backend agent).
 *
 * GET {base}/api/v1/generation-capabilities (public, no auth) -> 200
 *   {"modes":[{id,available,label?,model?},...]}.
 *
 * This is an ALLOWLIST DTO: the client re-parses every reply through
 * src/journey/generationMode.ts, which keeps ONLY the three frozen mode ids
 * and drops unknown fields/ids. The response NEVER carries URLs, credentials,
 * prompts, network details or availability reasons.
 * ==================================================================== */

/** The only generation modes the client may ever offer (Phase 16 I/J). */
export type GenerationModeId = "demo" | "local" | "live";

/**
 * Phase 21B (DEF-096/ADV-232) — the backend-CONFIGURED operator generation
 * provider (the exact raw `generation_provider` setting), re-expressed as the
 * closed enum "fake" | "ollama" | "live". This is the backend-AUTHORITATIVE
 * "what will actually run" signal: it is emitted VERBATIM from the server and
 * is INDEPENDENT of probe availability, so an ollama-configured backend with a
 * FAILED probe still reports "ollama" (the runtime WILL run that provider on
 * the next POST /cases) instead of collapsing to the fake-only demo shape.
 *
 * The field is SANITIZED by the backend (defensive allowlist, fail-closed to
 * "fake") and NEVER carries a URL/host/IP/port/credential — it is a bare enum
 * token. On the CLIENT the parser re-sanitizes it to exactly this closed enum
 * and drops any other value. Missing/unknown MUST be treated as UNKNOWN
 * (never as "fake"): an OLDER server omits the field, so the client falls back
 * to the availability-based derivation for backward compatibility.
 */
export type ConfiguredProvider = "fake" | "ollama" | "live";

/** One player-safe generation mode entry from the capability DTO. */
export interface GenerationModeDTO {
  id: string;
  available: boolean;
  /** Public display name (e.g. "Local AI" / "Cloud AI") — shown verbatim. */
  label?: string;
  /** Public model display name (local mode only) — shown verbatim. */
  model?: string;
}

/**
 * Phase 22 — the sanitized BYO-Ollama remote-local-AI status block.
 *
 * Mirror of the server's `remoteLocalAi` object published in TWO places:
 *   - the top-level `remoteLocalAi` of GET /api/v1/generation-capabilities
 *     (present ONLY when the bridge feature is enabled — ENABLE_BRIDGE=true;
 *     the pre-bridge DTO stays byte-identical and OMITS the key entirely);
 *   - the body of GET /api/v1/bridge/status (session-scoped).
 *
 * It carries ONLY booleans and the sanitized model label — NEVER a token,
 * secret, IP or Ollama URL. The client re-parses every crossing value
 * through src/journey/generationMode.ts (strict booleans; `model` through the
 * existing `safeDisplay` sanitizer; anything hostile is dropped).
 */
export interface RemoteLocalAiDTO {
  /** Bridge feature enabled and usable for this caller. */
  available: boolean;
  /** A live bridge is bound to THIS requester's session. */
  connected: boolean;
  /** Sanitized model label reported by the bound bridge (never a URL/IP). */
  model: string | null;
  /** Connected and currently accepting a new job. */
  ready: boolean;
}

/* ======================================================================
 * Phase 25 — browser-selectable generation provider contract (implemented in
 * parallel by the backend agent).
 *
 * GET /api/v1/generation-capabilities GAINS two additive keys — `defaultProvider`
 * and `providers[]` — while every pre-25 key stays byte-identical:
 *
 *   {
 *     "modes": [...], "configuredProvider": "fake", "remoteLocalAi": {...},
 *     "defaultProvider": "fake",
 *     "providers": [
 *       {"id":"fake","label":"Demo / Fake","available":true,"model":null,"reason":null},
 *       {"id":"ollama","label":"Ollama","available":true,"defaultModel":"qwen2.5:1.5b",
 *        "manualModelEntry":true,
 *        "transports":{"server":{"available":true,"reason":null},
 *                      "bridge":{"available":true,"connected":false,"reason":"not_connected"}}},
 *       {"id":"frontier","label":"Frontier","available":false,
 *        "model":"<configured-model or null>","reason":"not_configured"}
 *     ]
 *   }
 *
 * `providers`/`defaultProvider` are UNTRUSTED server data: the client re-parses
 * every value through src/journey/generationProvider.ts, sanitizes labels /
 * models / reasons through the existing safe-display guards and DROPS unknown /
 * unsafe provider ids. Absence of the additive keys (an OLDER server) keeps the
 * pre-25 behavior byte-identical — no provider selector is offered.
 *
 * POST /api/v1/cases GAINS one flat optional block:
 *   {"generationProvider": "fake|ollama|frontier",
 *    "ollamaTransport": "server|bridge",
 *    "ollamaModel": "qwen2.5:1.5b"}
 * All optional; omitted => server default. The browser never supplies URLs,
 * credentials or arbitrary provider configuration (§1.3).
 * ==================================================================== */

/** The ONLY logical generation-provider ids the browser may select (Phase 25 §1.3). */
export type GenerationProviderId = "fake" | "ollama" | "frontier";

/** The ONLY Ollama transport ids the browser may select (Phase 25 §1.3). */
export type OllamaTransportId = "server" | "bridge";

/** Availability/reason block of ONE Ollama transport inside a provider offer. */
export interface OllamaTransportStatusDTO {
  available: boolean;
  /** Bridge-only: session-scoped live binding (never a token/IP/URL). */
  connected?: boolean;
  /** Short safe availability reason (e.g. "not_connected"), sanitized client-side. */
  reason?: string | null;
}

/**
 * Phase 30 — ONE trusted-hosted-provider catalog entry published by the
 * backend's generation-capabilities DTO. It carries ONLY the logical provider
 * id + public display label — NEVER an endpoint, base URL, port, credential,
 * header, or raw server Settings (§9/§7). The registry is server-owned and
 * immutable; the browser submits the bare id and the backend resolves the
 * verified HTTPS endpoint.
 */
export interface FrontierProviderEntryDTO {
  /** Logical trusted provider id ("openai", "openrouter", "groq", ...). */
  id: string;
  /** Public display label ("OpenAI", "OpenRouter", "Groq", ...) — safe text only. */
  label: string;
}

/** One entry of the additive `providers` list of generation-capabilities. */
export interface GenerationProviderDTO {
  /** "fake" | "ollama" | "frontier" — anything else is dropped by the parser. */
  id: string;
  /** Public display label ("Demo / Fake", "Ollama", "Frontier") — safe text only. */
  label?: string;
  available: boolean;
  /** Frontier-configured model display name (never a user-selected value). */
  model?: string | null;
  /** Short safe availability reason (e.g. "not_configured") when unavailable. */
  reason?: string | null;
  /** Ollama-only: the configured DEFAULT model identifier (display/safe). */
  defaultModel?: string | null;
  /** Ollama-only: true when the user may type a custom model string. */
  manualModelEntry?: boolean;
  /** Ollama-only: per-transport availability (server/bridge). */
  transports?: {
    server?: OllamaTransportStatusDTO;
    bridge?: OllamaTransportStatusDTO;
  };
  /**
   * Phase 30 — Frontier-only: true when the browser must supply the user's
   * own configuration (provider id + API key + model) before generation is
   * allowed. A `frontier` entry with this true is BYOK-enabled.
   */
  requiresUserConfiguration?: boolean;
  /**
   * Phase 30 — Frontier-only: the SAFE provider CATALOG (ids + public labels
   * only, §9). Never endpoints/URLs/secrets. Absent on pre-30 servers.
   */
  providers?: FrontierProviderEntryDTO[] | null;
}

/**
 * Phase 30 — the browser-side BYOK Frontier request block of POST /api/v1/cases
 * (§6). The browser sends ONLY the logical trusted provider id, the user's
 * transient API key and the model — NEVER a URL, endpoint, header or config
 * value. The backend resolves the provider id to its trusted registry endpoint
 * and uses the key for the current attempt only.
 */
export interface CreateCaseFrontier {
  /** Logical trusted provider id from the capability catalog. */
  provider: string;
  /** User-supplied transient API key (memory-only; never persisted). */
  apiKey: string;
  /** User-supplied validated model identifier. */
  model: string;
}

/** Phase 25 §4 — the flat optional generation-selection block of POST /api/v1/cases. */
export interface CreateCaseGeneration {
  generationProvider?: GenerationProviderId;
  ollamaTransport?: OllamaTransportId;
  ollamaModel?: string;
  /**
   * Phase 28 — the OPTIONAL closed Demo-fixture id selected by the frontend's
   * "Try Demo Case" action ("demo-apartment" | "demo-gallery" |
   * "demo-laboratory"). Sent ONLY on the demo/fake path; the backend validates
   * it against a closed allowlist. Absent for generated cases (the POST /cases
   * body stays byte-identical, §13).
   */
  demoCaseId?: string;
  /**
   * Phase 30 — the OPTIONAL BYOK Frontier block. Travels ONLY when the caller
   * actually carries a COMPLETE frontier selection (`generationProvider ===
   * "frontier"` AND a non-empty apiKey). Never contains a URL (§6). Absent
   * keeps fake/ollama/no-selection bodies byte-identical.
   */
  frontier?: CreateCaseFrontier | null;
}

/** Phase 25 §4 — the flat optional generation-selection block of POST /api/v1/cases. */
export interface CreateCaseGeneration {
  generationProvider?: GenerationProviderId;
  ollamaTransport?: OllamaTransportId;
  ollamaModel?: string;
  /**
   * Phase 28 — the OPTIONAL closed Demo-fixture id selected by the frontend's
   * "Try Demo Case" action ("demo-apartment" | "demo-gallery" |
   * "demo-laboratory"). Sent ONLY on the demo/fake path; the backend validates
   * it against a closed allowlist. Absent for generated cases (the POST /cases
   * body stays byte-identical, §13).
   */
  demoCaseId?: string;
}

/** 200 body of GET {base}/api/v1/generation-capabilities. */
export interface GenerationCapabilitiesResponse {
  modes: GenerationModeDTO[];
  /**
   * Phase 21B (DEF-096/ADV-232) — backend-authoritative operator-config
   * generator provider (closed enum "fake" | "ollama" | "live"; sanitized by
   * the server, never a URL/IP/port/credential). Client-parse keeps ONLY the
   * closed enum; unknown/missing values are dropped so consumers treat the
   * field as UNKNOWN (never as "fake") and fall back to the availability-based
   * derivation (backward compatible with OLDER servers that omit the field).
   */
  configuredProvider?: ConfiguredProvider | null;
  /**
   * Phase 22 — BYO-Ollama bridge status scoped to the requester's anonymous
   * session, present ONLY when ENABLE_BRIDGE=true (absent entirely when the
   * feature is OFF, keeping the existing DTO byte-identical). Absence means
   * the feature is not offered. Every field is re-sanitized by the client
   * parser before any display (strict booleans; model through safeDisplay).
   */
  remoteLocalAi?: RemoteLocalAiDTO | null;
  /**
   * Phase 25 — backend-authoritative DEFAULT generation provider (closed enum
   * "fake" | "ollama" | "frontier"; re-sanitized like `configuredProvider`).
   * Used by the client when NO valid sessionStorage preference exists. Absent
   * on OLDER servers (no provider selector is then offered).
   */
  defaultProvider?: GenerationProviderId | null;
  /**
   * Phase 25 — the additive provider-offer list (fake|ollama|frontier).
   * UNTRUSTED server data: the client re-parses every entry through
   * src/journey/generationProvider.ts (labels/models/reasons through the
   * safe-display guards; unknown/unsafe ids dropped). Absent on OLDER servers
   * -> the browser offers no selector (pre-25 behavior stays byte-identical).
   */
  providers?: GenerationProviderDTO[] | null;
}

/* ======================================================================
 * Phase 22 — BYO-Ollama bridge REST contract (implemented in parallel by
 * the backend agent).
 *
 * POST {base}/api/v1/bridge/pairing (Bearer anonymousSessionToken) -> 201
 *   {"pairingSessionId","pairingCode","expiresAt"} — the code (PD-XXXX-XXXX)
 *   is short-lived, single-use and shown to the user as a display-only
 *   secret; it is entered into the LOCAL BRIDGE CLI, never the browser↔
 *   server WebSocket (the bridge<->server WS is bridge-owned only).
 * GET {base}/api/v1/bridge/status (Bearer anonymousSessionToken) -> 200
 *   {"remoteLocalAi": {available, connected, model, ready}} — sanitized,
 *   session-scoped.
 *
 * Both are authenticated with the anonymous session bearer and exist ONLY
 * when ENABLE_BRIDGE=true (otherwise 404 like every unmounted path).
 * ==================================================================== */

/** 201 body of POST {base}/api/v1/bridge/pairing. */
export interface BridgePairingResponse {
  /** Opaque server-side pairing record id. */
  pairingSessionId: string;
  /** Short-lived single-use pairing code (PD-XXXX-XXXX), shown exactly once. */
  pairingCode: string;
  /** UTC epoch seconds when the code expires. */
  expiresAt: number;
}

/** 200 body of GET {base}/api/v1/bridge/status. */
export interface BridgeStatusResponse {
  remoteLocalAi: RemoteLocalAiDTO;
}
