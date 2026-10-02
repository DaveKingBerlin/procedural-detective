// ---------------------------------------------------------------------------
// Phase 24 §45/§46 — private-endpoint DENY LIST (single source of truth).
//
// Shared VERBATIM by the two enforcement surfaces so they can never drift:
//   1. scripts/scan-private-endpoints.mjs — the built-bundle scanner chained
//      to `npm run build` (runs on EVERY production build: local developer,
//      the GitLab `build`-stage job, and the Dockerfile stage-1 image build);
//   2. src/release-hygiene/phase24PrivateEndpointGuard.test.ts — the Phase 24
//      §45/§46 source + bundle guard suite (`npm test`).
//
// Self-hygiene (Phase 24 §46 level-0 rule: NO private-endpoint literal may be
// committed anywhere): every endpoint/value here is assembled from character
// classes / parts so this module's own TEXT never contains the exact literals
// the Phase 24 §46 scans forbid — the same discipline `PRIVATE_HOST_LITERALS`
// uses in frontend/src/api/client.ts (`\.` written as an escape: the source
// text `127\.` can never match the runtime "127." pattern). The file is never
// bundled (scripts/ is outside the Vite build) and lives OUTSIDE the
// *.ts/*.tsx production-source walk, but the discipline keeps it safe even if
// either scan widens to scripts/ later.
// ---------------------------------------------------------------------------

/** The audited Ollama service port — assembled digit-by-digit so the bare
 *  port literal never appears contiguously anywhere in this file. */
const OLLAMA_PORT = "1" + "1" + "4" + "3" + "4";

/** Colon separator kept as a part so the end-to-end host-colon-port literal
 *  (the `localhost` host joined to the assembled port) never appears upright. */
const COLON = ":";

/** The exact local-Ollama endpoint literal the phase guard must never see. */
export const LOCALHOST_OLLAMA = `localhost${COLON}${OLLAMA_PORT}`;

/** The same-origin relative API root the built client must embed. */
export const SAME_ORIGIN_ROOT = "/api/v1";

/** Any `http://localhost:<port>` literal names a LOCAL API endpoint — the
 *  production bundle must be same-origin, so a port-carrying localhost HTTP
 *  URL is forbidden evidence. The bare, port-less Babylon URL-normalization
 *  fallback (`new URL("http://localhost")`) is third-party and out of scope,
 *  exactly as in the Phase 20 production-bundle scan. */
export const LOCALHOST_HTTP_PATTERNS = Object.freeze([
  /http:\/\/localhost:\d+/i,
]);

/** Private / loopback / LAN IPv4 prefixes plus the docker-host alias. The
 *  dots are written as character-class escapes (`\.` / `[.]`) so THIS file's
 *  own text never matches the very patterns it declares. */
export const PRIVATE_HOST_PATTERNS = Object.freeze([
  /\b127\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/,
  /\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/,
  /\b192\.168\.\d{1,3}\.\d{1,3}\b/,
  /\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b/,
  /\bhost[.]docker[.]internal\b/i,
]);

/** Provider-endpoint evidence: the Ollama service port, in the explicit
 *  host:port colon form AND as the bare port (both assembled from parts). */
export const PROVIDER_PORT_PATTERNS = Object.freeze([
  new RegExp(`${COLON}${OLLAMA_PORT}\\b`),   // colon + assembled port — the audited form
  new RegExp(`\\b${OLLAMA_PORT}\\b`),        // the bare assembled service port
]);

/** The exact local-Ollama endpoint as a pattern (host:port, word-bound). */
export const LOCALHOST_OLLAMA_PATTERNS = Object.freeze([
  new RegExp(`\\b${LOCALHOST_OLLAMA}\\b`, "i"),
]);

/** The complete ordered deny set — the scanner AND the guard enforce exactly
 *  these patterns, in this order. */
export const DENY_PATTERNS = Object.freeze([
  ...LOCALHOST_OLLAMA_PATTERNS,
  ...LOCALHOST_HTTP_PATTERNS,
  ...PROVIDER_PORT_PATTERNS,
  ...PRIVATE_HOST_PATTERNS,
]);