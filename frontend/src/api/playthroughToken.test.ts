import { describe, expect, it } from "vitest";
import {
  PLAYTHROUGH_ID_KEY,
  PLAYTHROUGH_TOKEN_KEY,
  TOKEN_MAX_LENGTH,
  TOKEN_MIN_LENGTH,
  clearPlaythroughCredentials,
  getPlaythroughId,
  getPlaythroughToken,
  setPlaythroughId,
  setPlaythroughToken,
  validatePlaythroughToken,
  type TokenStorage,
} from "./playthroughToken";

/** In-memory localStorage stand-in so tests never need a DOM. */
function fakeStorage(): TokenStorage & { entries: Map<string, string> } {
  const entries = new Map<string, string>();
  return {
    entries,
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => void entries.set(key, value),
    removeItem: (key) => void entries.delete(key),
  };
}

const VALID_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789AB";

describe("validatePlaythroughToken", () => {
  it("accepts a trimmed opaque token of at least 20 characters", () => {
    const result = validatePlaythroughToken(`  ${VALID_TOKEN}  `);
    expect(result.ok).toBe(true);
    expect(result.normalized).toBe(VALID_TOKEN);
    expect(result.hint).toBeNull();
  });

  it("rejects empty / too-short input with a hint", () => {
    const result = validatePlaythroughToken("   ");
    expect(result.ok).toBe(false);
    expect(result.normalized).toBeNull();
    expect(result.hint).toContain(`${TOKEN_MIN_LENGTH}`);
  });

  it("rejects tokens longer than the backend bearer bound (256)", () => {
    const long = "x".repeat(TOKEN_MAX_LENGTH + 1);
    const result = validatePlaythroughToken(long);
    expect(result.ok).toBe(false);
    expect(result.hint).toContain(`${TOKEN_MAX_LENGTH}`);
  });

  it("rejects tokens containing spaces (must stay one parseable credential)", () => {
    const result = validatePlaythroughToken("abc def ghi jkl mno pqr stu vwx");
    expect(result.ok).toBe(false);
    expect(result.hint).toContain("spaces");
  });

  it("is deterministic: the same input always yields the same result", () => {
    expect(validatePlaythroughToken(VALID_TOKEN)).toEqual(validatePlaythroughToken(VALID_TOKEN));
  });
});

describe("playthrough credential storage", () => {
  it("round-trips the token and playthrough id through injectable storage", () => {
    const storage = fakeStorage();
    expect(setPlaythroughToken(VALID_TOKEN, storage)).toBe(true);
    expect(setPlaythroughId("PT-demo-0001", storage)).toBe(true);
    expect(getPlaythroughToken(storage)).toBe(VALID_TOKEN);
    expect(getPlaythroughId(storage)).toBe("PT-demo-0001");
    expect(storage.entries.get(PLAYTHROUGH_TOKEN_KEY)).toBe(VALID_TOKEN);
    expect(storage.entries.get(PLAYTHROUGH_ID_KEY)).toBe("PT-demo-0001");
  });

  it("clears both credential keys together", () => {
    const storage = fakeStorage();
    setPlaythroughToken(VALID_TOKEN, storage);
    setPlaythroughId("PT-demo-0001", storage);
    clearPlaythroughCredentials(storage);
    expect(getPlaythroughToken(storage)).toBeNull();
    expect(getPlaythroughId(storage)).toBeNull();
  });

  it("returns null (never throws) when storage is unavailable", () => {
    expect(getPlaythroughToken(null)).toBeNull();
    expect(getPlaythroughId(null)).toBeNull();
    expect(setPlaythroughToken(VALID_TOKEN, null)).toBe(false);
  });

  it("returns null (never throws) when storage access throws", () => {
    const throwing: TokenStorage = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    };
    expect(getPlaythroughToken(throwing)).toBeNull();
    expect(clearPlaythroughCredentials(throwing)).toBeUndefined();
  });
});