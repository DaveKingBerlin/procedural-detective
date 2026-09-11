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