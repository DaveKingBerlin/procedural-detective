import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAnonymousSession } from "./client";
import {
  createOrReuseAnonymousSession,
  getCachedAnonymousSession,
  resetAnonymousSessionCache,
} from "./anonymousSession";

// The holder's default minting seam is the real client API call — mocked here
// so the tests prove the underlying createAnonymousSession is called AT MOST
// ONCE per page lifetime with zero network.
vi.mock("./client", () => ({
  createAnonymousSession: vi.fn(),
}));

/**
 * Phase 24 P0 §8 — anonymous-session churn fix.
 *
 * The in-memory holder caches the first successful POST /sessions/anonymous
 * for a page lifetime, so the bridge pairing panel AND the /generating
 * journey share ONE anonymous session (the backend's bridge binding is
 * session-scoped; a second session sees BRIDGE_NOT_CONNECTED). These tests
 * prove the underlying client call happens EXACTLY ONCE, that subsequent
 * createOrReuseAnonymousSession() calls return the same token, and that a
 * hard reset refreshes. Purely in-memory: no storage API is ever touched.
 */

const mockedCreateAnonymousSession = () => vi.mocked(createAnonymousSession);

beforeEach(() => {
  resetAnonymousSessionCache();
  mockedCreateAnonymousSession().mockReset();
});

afterEach(() => {
  resetAnonymousSessionCache();
  mockedCreateAnonymousSession().mockReset();
});

describe("createOrReuseAnonymousSession — one session per page lifetime", () => {
  it("calls the underlying createAnonymousSession EXACTLY ONCE and returns the same token afterwards", async () => {
    mockedCreateAnonymousSession().mockResolvedValue({
      anonymousSessionToken: "anon-page-token-1",
      quotaWindowEndsAt: 987654321,
    });

    const first = await createOrReuseAnonymousSession();
    const second = await createOrReuseAnonymousSession();
    const third = await createOrReuseAnonymousSession();

    expect(mockedCreateAnonymousSession()).toHaveBeenCalledTimes(1);
    expect(first.anonymousSessionToken).toBe("anon-page-token-1");
    expect(second.anonymousSessionToken).toBe(first.anonymousSessionToken);
    expect(third.anonymousSessionToken).toBe(first.anonymousSessionToken);
    expect(second.quotaWindowEndsAt).toBe(987654321);
  });

  it("a hard reset refreshes the session (a NEW underlying createAnonymousSession call)", async () => {
    mockedCreateAnonymousSession()
      .mockResolvedValueOnce({ anonymousSessionToken: "anon-page-token-1", quotaWindowEndsAt: 1 })
      .mockResolvedValueOnce({ anonymousSessionToken: "anon-page-token-2", quotaWindowEndsAt: 2 });

    const before = await createOrReuseAnonymousSession();
    resetAnonymousSessionCache();
    const afterReset = await createOrReuseAnonymousSession();

    expect(mockedCreateAnonymousSession()).toHaveBeenCalledTimes(2);
    expect(before.anonymousSessionToken).toBe("anon-page-token-1");
    expect(afterReset.anonymousSessionToken).toBe("anon-page-token-2");
  });

  it("does not cache a FAILED mint (no auto-create; the server rate limit stays authoritative)", async () => {
    const error = new Error("429 ADMISSION_DENIED");
    mockedCreateAnonymousSession().mockRejectedValueOnce(error);

    await expect(createOrReuseAnonymousSession()).rejects.toThrow(error);
    expect(getCachedAnonymousSession()).toBeNull();

    // A later call may retry the mint itself (the cache never pre-baked a
    // poisoned session).
    mockedCreateAnonymousSession().mockResolvedValueOnce({
      anonymousSessionToken: "anon-page-token-3",
      quotaWindowEndsAt: 3,
    });
    const retried = await createOrReuseAnonymousSession();
    expect(retried.anonymousSessionToken).toBe("anon-page-token-3");
    expect(mockedCreateAnonymousSession()).toHaveBeenCalledTimes(2);
  });

  it("getCachedAnonymousSession exposes the in-memory identity while present and null after a reset", async () => {
    expect(getCachedAnonymousSession()).toBeNull();
    mockedCreateAnonymousSession().mockResolvedValue({
      anonymousSessionToken: "anon-page-token-4",
      quotaWindowEndsAt: 42,
    });
    await createOrReuseAnonymousSession();
    expect(getCachedAnonymousSession()).toEqual({
      anonymousSessionToken: "anon-page-token-4",
      quotaWindowEndsAt: 42,
    });
    resetAnonymousSessionCache();
    expect(getCachedAnonymousSession()).toBeNull();
  });
});
