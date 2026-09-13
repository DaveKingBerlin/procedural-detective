/**
 * Playthrough credential persistence for the browser investigation.
 *
 * The backend authorizes EVERY investigation request with the opaque
 * playthroughAccessToken (Phase 5 B, REQUIREMENTS 40.1) sent as a Bearer
 * header — never via cookies. The token is `secrets.token_urlsafe(32)`
 * entropy and can never be reversed into a playthrough id, and the frozen
 * investigation endpoints are keyed by `{playthrough_id}` in the URL path,
 * so the demo entry also remembers the playthrough id it was issued
 * alongside.
 *
 * localStorage access is wrapped and injectable so the module stays pure
 * and unit-testable without a DOM.
 */

/** Contract-mandated localStorage key for the playthrough access token. */
export const PLAYTHROUGH_TOKEN_KEY = "pd_playthrough_token";

/**
 * Additional localStorage key: opaque tokens cannot be reversed into
 * playthrough ids, and the frozen endpoints are path-keyed by playthrough
 * id. The QA/E2E harness may seed both keys directly.
 */
export const PLAYTHROUGH_ID_KEY = "pd_playthrough_id";

/** Bounds mirrored from the backend bearer parser (backend/auth/tokens.py). */
export const TOKEN_MIN_LENGTH = 20;
export const TOKEN_MAX_LENGTH = 256;

/** Minimal storage surface used by this module (localStorage-compatible). */
export interface TokenStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function defaultStorage(): TokenStorage | null {
  try {
    if (typeof window !== "undefined" && typeof window.localStorage !== "undefined") {
      return window.localStorage;
    }
  } catch {
    // localStorage can throw in hardened/embedded contexts — treat as absent.
  }
  return null;
}

function readItem(key: string, storage: TokenStorage | null | undefined): string | null {
  const store = storage ?? defaultStorage();
  if (!store) return null;
  try {
    return store.getItem(key);
  } catch {
    return null;
  }
}

function writeItem(key: string, value: string, storage: TokenStorage | null | undefined): boolean {
  const store = storage ?? defaultStorage();
  if (!store) return false;
  try {
    store.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** Read the stored playthrough access token, if any. */
export function getPlaythroughToken(storage?: TokenStorage | null): string | null {
  return readItem(PLAYTHROUGH_TOKEN_KEY, storage);
}

/** Read the stored playthrough id (see module docstring). */
export function getPlaythroughId(storage?: TokenStorage | null): string | null {
  return readItem(PLAYTHROUGH_ID_KEY, storage);
}

/** Persist the playthrough access token; returns false when no storage is available. */
export function setPlaythroughToken(token: string, storage?: TokenStorage | null): boolean {
  return writeItem(PLAYTHROUGH_TOKEN_KEY, token, storage);
}

/** Persist the playthrough id; returns false when no storage is available. */
export function setPlaythroughId(playthroughId: string, storage?: TokenStorage | null): boolean {
  return writeItem(PLAYTHROUGH_ID_KEY, playthroughId, storage);
}

/** Clear both playthrough credentials (used by the reset-token action). */
export function clearPlaythroughCredentials(storage?: TokenStorage | null): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.removeItem(PLAYTHROUGH_TOKEN_KEY);
    store.removeItem(PLAYTHROUGH_ID_KEY);
  } catch {
    // Best-effort: a storage failure must never crash the page.
  }
}

export interface TokenValidationResult {
  ok: boolean;
  normalized: string | null;
  hint: string | null;
}

/**
 * Validate a pasted playthrough access token: trimmed, non-empty, within the
 * backend's 20..256 bearer bounds, and free of whitespace so it remains one
 * parseable opaque credential.
 */
export function validatePlaythroughToken(raw: string): TokenValidationResult {
  const trimmed = typeof raw === "string" ? raw.trim() : "";
  if (trimmed.length < TOKEN_MIN_LENGTH) {
    return {
      ok: false,
      normalized: null,
      hint: `Playthrough access token must be at least ${TOKEN_MIN_LENGTH} characters.`,
    };
  }
  if (trimmed.length > TOKEN_MAX_LENGTH) {
    return {
      ok: false,
      normalized: null,
      hint: `Playthrough access token can be at most ${TOKEN_MAX_LENGTH} characters.`,
    };
  }
  if (/\s/.test(trimmed)) {
    return {
      ok: false,
      normalized: null,
      hint: "Playthrough access token cannot contain spaces.",
    };
  }
  return { ok: true, normalized: trimmed, hint: null };
}