import { createAnonymousSession } from "./client";
import type { AnonymousSessionResponse } from "./types";

/**
 * In-memory-only anonymous-session holder (Phase 24 P0 — §8 same root cause).
 *
 * ONE anonymous session per PAGE LIFETIME. The /new bridge pairing panel and
 * the /generating journey must present the SAME anonymous identity to the
 * backend: the bridge binding is SESSION-SCOPED server-side
 * (`lookup_for_scope(session)`), so a generation that mints a second session
 * sees BRIDGE_NOT_CONNECTED even while /bridge/status on the pairing session
 * reports connected. This holder caches the first successful
 * POST /sessions/anonymous response at module level — it resets on page load —
 * so a /generating retry or a back-to-start + regenerate NEVER mints a second
 * session within one page.
 *
 * HARD RULES:
 *   - NEVER persisted: this module touches NO storage API. The
 *     anonymous-session bearer is a bearer credential and per the documented
 *     client rule "bearer tokens are never persisted beyond the
 *     contract-mandated localStorage key" it stays in memory only.
 *   - The server-side anonymous-session rate limit is UNTOUCHED: we never
 *     auto-create on a 429 (that would defeat the limit) — a failed mint
 *     propagates as a normal ApiError and the cache stays empty (a later
 *     caller may retry the mint itself).
 *   - The minting supplier is injectable for deterministic unit tests; the
 *     default is the real client function.
 */

/** The cached anonymous-session identity for this page lifetime. */
export interface CachedAnonymousSession {
  anonymousSessionToken: string;
  quotaWindowEndsAt: number;
}

/** Injectable minting seam (default: the real client API call). */
export type AnonymousSessionSupplier = () => Promise<AnonymousSessionResponse>;

/** The single session cached for this page lifetime (null before the first mint). */
let cached: CachedAnonymousSession | null = null;

/** Read the currently cached anonymous session, or null when none was minted yet. */
export function getCachedAnonymousSession(): CachedAnonymousSession | null {
  return cached;
}

/** Hard-reset the in-memory cache (tests / reset flows). NEVER persisted. */
export function resetAnonymousSessionCache(): void {
  cached = null;
}

/**
 * Return the cached anonymous session when one exists for this page lifetime;
 * otherwise mint ONE via the supplier and cache it. The underlying
 * createAnonymousSession call happens AT MOST ONCE per page — subsequent calls
 * reuse the cached identity (same token, same quota window). Errors propagate
 * (no auto-create on a 429 — the server rate limit stays authoritative).
 */
export async function createOrReuseAnonymousSession(
  supplier: AnonymousSessionSupplier = createAnonymousSession,
): Promise<AnonymousSessionResponse> {
  if (cached !== null) {
    return {
      anonymousSessionToken: cached.anonymousSessionToken,
      quotaWindowEndsAt: cached.quotaWindowEndsAt,
    };
  }
  const session = await supplier();
  cached = {
    anonymousSessionToken: session.anonymousSessionToken,
    quotaWindowEndsAt: session.quotaWindowEndsAt,
  };
  return session;
}
