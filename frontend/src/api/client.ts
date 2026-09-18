import type {
  AccusationRequest,
  AccusationResponse,
  AnonymousSessionResponse,
  CreateCaseResponse,
  CreatePlaythroughResponse,
  DiscoveryResultDTO,
  ErrorEnvelope,
  EvidenceReadResultDTO,
  GenerationCapabilitiesResponse,
  GenerationStatusResponse,
  HealthResponse,
  InteractionResultDTO,
  InvestigationBootstrapResponse,
  ReadinessResponse,
  RevealResponse,
} from "./types";

/**
 * Minimal request body for POST /api/v1/cases. The difficulty label is
 * optional (the pipeline is deterministic and stores it).
 */
interface CreateCaseRequest {
  prompt: string;
  difficulty?: string;
}

/** Backend base URL. Overridable via VITE_API_BASE_URL; defaults to the local FastAPI dev server. */
export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const REQUEST_TIMEOUT_MS = 5000;

/** Structured error produced for non-2xx responses and transport failures. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: object | null;

  constructor(status: number, code: string, message: string, details: object | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function fetchWithTimeout(
  url: string,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
  init?: RequestInit,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(0, "TIMEOUT", `Request timed out after ${timeoutMs}ms`, null);
    }
    const message = error instanceof Error ? error.message : String(error);
    throw new ApiError(0, "NETWORK_ERROR", message, null);
  } finally {
    clearTimeout(timer);
  }
}

/** Map a non-2xx response to an ApiError, preferring the backend's error envelope. */
async function toApiError(response: Response): Promise<ApiError> {
  const status = response.status;
  let code = `HTTP_${status}`;
  let message = `Request failed with status ${status}`;
  let details: object | null = null;
  try {
    const body = (await response.json()) as Partial<ErrorEnvelope>;
    if (body && body.error && typeof body.error.code === "string") {
      code = body.error.code;
      message = typeof body.error.message === "string" ? body.error.message : message;
      details = body.error.details ?? null;
    }
  } catch {
    // Body is not JSON (e.g. 502 from a proxy): keep the status-derived error.
  }
  return new ApiError(status, code, message, details);
}

/**
 * Parse a 2xx body into the typed DTO. A 204 (or otherwise empty) body or a
 * non-JSON payload is a contract violation and must surface as an ApiError —
 * never as a raw SyntaxError/TypeError leaking from `response.json()`.
 */
async function parseJsonBody<T>(response: Response): Promise<T> {
  if (response.status === 204 || response.status === 205) {
    throw new ApiError(response.status, "INVALID_RESPONSE", "Server returned non-JSON or empty body", null);
  }
  let text: string;
  try {
    text = await response.text();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new ApiError(response.status, "INVALID_RESPONSE", `Server returned an unreadable body: ${message}`, null);
  }
  if (text.trim() === "") {
    throw new ApiError(response.status, "INVALID_RESPONSE", "Server returned non-JSON or empty body", null);
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(response.status, "INVALID_RESPONSE", "Server returned non-JSON or empty body", null);
  }
}

async function request<T>(path: string): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE_URL}${path}`);
  if (response.ok) {
    return await parseJsonBody<T>(response);
  }
  throw await toApiError(response);
}

/** GET {base}/api/v1/health */
export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/v1/health");
}

/** GET {base}/api/v1/readiness */
export function getReadiness(): Promise<ReadinessResponse> {
  return request<ReadinessResponse>("/api/v1/readiness");
}

/**
 * GET {base}/api/v1/generation-capabilities (public, no auth)
 * -> 200 GenerationCapabilitiesResponse (Phase 16 J).
 *
 * The endpoint is an allowlist DTO: which generation modes are configured AND
 * available. Every failure (transport, non-2xx, non-JSON/empty 2xx body)
 * surfaces as a structured ApiError exactly like every other endpoint here —
 * the caller decides how to degrade (the selector falls back to the
 * always-available Demo offer, never claiming Local/Cloud AI).
 */
export function getGenerationCapabilities(): Promise<GenerationCapabilitiesResponse> {
  return request<GenerationCapabilitiesResponse>("/api/v1/generation-capabilities");
}

/* ======================================================================
 * Phase 6 investigation endpoints (frozen contract).
 *
 * Every request is authorized with the playthrough access token sent as a
 * Bearer header (the backend does NOT cookie-auth). Non-2xx responses carry
 * the standard {"error":{code,message,details}} envelope and are mapped to
 * ApiError the same way the Phase 2 endpoints are.
 * ==================================================================== */

interface AuthedRequestOptions {
  method?: "GET" | "POST";
  /** JSON-serialized request body (setting this also sets Content-Type). */
  body?: unknown;
}

async function authedRequest<T>(path: string, token: string, options: AuthedRequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  let body: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const init: RequestInit = { method: options.method ?? "GET", headers, body };
  const response = await fetchWithTimeout(`${API_BASE_URL}${path}`, REQUEST_TIMEOUT_MS, init);
  if (response.ok) {
    return await parseJsonBody<T>(response);
  }
  throw await toApiError(response);
}

/**
 * GET {base}/api/v1/playthroughs/{playthrough_id}/investigation
 * -> InvestigationBootstrapResponse (player-safe scene + PlayerKnowledge).
 */
export function getInvestigation(playthroughId: string, token: string): Promise<InvestigationBootstrapResponse> {
  return authedRequest<InvestigationBootstrapResponse>(
    `/api/v1/playthroughs/${encodeURIComponent(playthroughId)}/investigation`,
    token,
  );
}

/**
 * POST {base}/api/v1/playthroughs/{playthrough_id}/objects/{object_id}/interact
 * body {"interaction": "<object interaction>"} -> InteractionResultDTO.
 */
export function interactObject(
  playthroughId: string,
  objectId: string,
  interaction: string,
  token: string,
): Promise<InteractionResultDTO> {
  return authedRequest<InteractionResultDTO>(
    `/api/v1/playthroughs/${encodeURIComponent(playthroughId)}/objects/${encodeURIComponent(objectId)}/interact`,
    token,
    { method: "POST", body: { interaction } },
  );
}

/** POST {base}/api/v1/playthroughs/{playthrough_id}/evidence/{evidence_id}/discover -> DiscoveryResultDTO. */
export function discoverEvidence(
  playthroughId: string,
  evidenceId: string,
  token: string,
): Promise<DiscoveryResultDTO> {
  return authedRequest<DiscoveryResultDTO>(
    `/api/v1/playthroughs/${encodeURIComponent(playthroughId)}/evidence/${encodeURIComponent(evidenceId)}/discover`,
    token,
    { method: "POST", body: {} },
  );
}

/**
 * GET {base}/api/v1/playthroughs/{playthrough_id}/records/{record_id}
 * -> EvidenceReadResultDTO. Readable only AFTER the server confirmed the
 * evidence is discovered for this playthrough.
 */
export function readRecord(playthroughId: string, recordId: string, token: string): Promise<EvidenceReadResultDTO> {
  return authedRequest<EvidenceReadResultDTO>(
    `/api/v1/playthroughs/${encodeURIComponent(playthroughId)}/records/${encodeURIComponent(recordId)}`,
    token,
  );
}

/*
 * ======================================================================
 * Phase 7 accusation & reveal endpoints (frozen contract).
 *
 * POST accusation is the irreversible transition to ACCUSED; its response
 * never contains truth. GET reveal is only available once the playthrough is
 * ACCUSED (403 REVEAL_NOT_AVAILABLE before that) and is idempotent, so any
 * reload may re-fetch it.
 * ==================================================================== */

/**
 * POST {base}/api/v1/playthroughs/{playthrough_id}/accusation
 * body {"murdererId","motiveId","weaponId","crimeTime"} -> AccusationResponse.
 * 409 -> CASE_ALREADY_SUBMITTED; 422 -> validation error; both as ApiError.
 */
export function submitAccusation(
  playthroughId: string,
  body: AccusationRequest,
  token: string,
): Promise<AccusationResponse> {
  return authedRequest<AccusationResponse>(
    `/api/v1/playthroughs/${encodeURIComponent(playthroughId)}/accusation`,
    token,
    { method: "POST", body },
  );
}

/**
 * GET {base}/api/v1/playthroughs/{playthrough_id}/reveal -> RevealResponse.
 * 403 {"error":{"code":"REVEAL_NOT_AVAILABLE",..}} until the playthrough is
 * ACCUSED; idempotent afterwards (safe to re-fetch on reload/restart).
 */
export function getReveal(playthroughId: string, token: string): Promise<RevealResponse> {
  return authedRequest<RevealResponse>(
    `/api/v1/playthroughs/${encodeURIComponent(playthroughId)}/reveal`,
    token,
  );
}

/* ======================================================================
 * Phase 8 — prompt-to-case journey endpoints (frozen contract).
 *
 * POST /sessions/anonymous creates the anonymous quota identity (no auth).
 * POST /cases then generates a case under that quota identity and returns
 * the one-time creatorAccessToken; GET /generations/{id} exposes only the
 * sanitized progress snapshot; POST .../playthroughs finally pins a
 * PLAYING playthrough and returns the one-time playthroughAccessToken.
 * Every token is a Bearer credential in transit, never persisted by the
 * client beyond the contract-mandated localStorage key.
 * ==================================================================== */

/** JSON POST without credentials (anonymous session creation). */
async function unauthPost<T>(path: string): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE_URL}${path}`, REQUEST_TIMEOUT_MS, {
    method: "POST",
  });
  if (response.ok) {
    return await parseJsonBody<T>(response);
  }
  throw await toApiError(response);
}

/** POST {base}/api/v1/sessions/anonymous -> 201 AnonymousSessionResponse. */
export function createAnonymousSession(): Promise<AnonymousSessionResponse> {
  return unauthPost<AnonymousSessionResponse>("/api/v1/sessions/anonymous");
}

/**
 * POST {base}/api/v1/cases (Bearer anonymousSessionToken)
 * body {"prompt","difficulty"?} -> 201 CreateCaseResponse.
 * 429 {"error":{"code":"ADMISSION_DENIED",..}} surfaces quota exhaustion.
 */
export function createCase(
  anonymousSessionToken: string,
  prompt: string,
  difficulty?: string,
): Promise<CreateCaseResponse> {
  const body: CreateCaseRequest = { prompt };
  if (typeof difficulty === "string" && difficulty !== "") {
    body.difficulty = difficulty;
  }
  return authedRequest<CreateCaseResponse>("/api/v1/cases", anonymousSessionToken, {
    method: "POST",
    body,
  });
}

/**
 * GET {base}/api/v1/generations/{generation_id} (Bearer creatorAccessToken)
 * -> 200 GenerationStatusResponse (sanitized status/progress/stage only).
 */
export function getGenerationProgress(
  generationId: string,
  creatorAccessToken: string,
): Promise<GenerationStatusResponse> {
  return authedRequest<GenerationStatusResponse>(
    `/api/v1/generations/${encodeURIComponent(generationId)}`,
    creatorAccessToken,
  );
}

/**
 * POST {base}/api/v1/cases/{case_id}/versions/{case_version}/playthroughs
 * (Bearer creatorAccessToken) -> 201 CreatePlaythroughResponse.
 */
export function createPlaythrough(
  creatorAccessToken: string,
  caseId: string,
  caseVersion: number,
): Promise<CreatePlaythroughResponse> {
  return authedRequest<CreatePlaythroughResponse>(
    `/api/v1/cases/${encodeURIComponent(caseId)}/versions/${encodeURIComponent(String(caseVersion))}/playthroughs`,
    creatorAccessToken,
    { method: "POST", body: {} },
  );
}