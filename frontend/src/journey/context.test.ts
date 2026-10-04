import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  attachJourneySessionToken,
  clearJourneyParams,
  getJourneyParams,
  setJourneyParams,
  type JourneyParams,
} from "./context";

/**
 * Phase 24 P0 §1 — JourneyParams carries an optional in-memory
 * `anonymousSessionToken` (the bridge-pairing session bearer) from /new to
 * /generating. The context is NEVER persisted; the demo/non-bridge staging
 * stays byte-identical (no token key appears, the exact params object keeps
 * flowing). `attachJourneySessionToken` lets the bridge panel contribute its
 * session before OR after staging.
 */

beforeEach(() => {
  clearJourneyParams();
});

afterEach(() => {
  clearJourneyParams();
});

describe("JourneyParams.anonymousSessionToken — Phase 24 P0 §1", () => {
  it("carries an explicit optional anonymousSessionToken through the context", () => {
    setJourneyParams({ prompt: "a crime", difficulty: "easy", anonymousSessionToken: "tok-1" });
    expect(getJourneyParams()).toEqual({
      prompt: "a crime",
      difficulty: "easy",
      anonymousSessionToken: "tok-1",
    });
  });

  it("staging WITHOUT a token is byte-identical (no extra key, same object)", () => {
    const params: JourneyParams = { prompt: "a crime", difficulty: "medium" };
    setJourneyParams(params);
    const read = getJourneyParams() as JourneyParams;
    expect(read).toEqual({ prompt: "a crime", difficulty: "medium" });
    expect("anonymousSessionToken" in read).toBe(false);
    expect(read).toBe(params); // exactly the object the caller built
    expect(getJourneyParams()).not.toBeNull();
  });

  it("attach BEFORE staging merges the token into the NEXT staged journey", () => {
    attachJourneySessionToken("tok-paired");
    setJourneyParams({ prompt: "staged", difficulty: "hard" });
    expect(getJourneyParams()).toEqual({
      prompt: "staged",
      difficulty: "hard",
      anonymousSessionToken: "tok-paired",
    });
  });

  it("attach AFTER staging merges into the staged journey AND stays pending for a later re-stage", () => {
    setJourneyParams({ prompt: "first", difficulty: "easy" });
    attachJourneySessionToken("tok-paired");
    expect(getJourneyParams()).toEqual({
      prompt: "first",
      difficulty: "easy",
      anonymousSessionToken: "tok-paired",
    });
    // A later re-stage (prompt edited, Generate pressed again) keeps the token.
    setJourneyParams({ prompt: "second", difficulty: "medium" });
    expect(getJourneyParams()).toEqual({
      prompt: "second",
      difficulty: "medium",
      anonymousSessionToken: "tok-paired",
    });
  });

  it("clearJourneyParams resets prompt/difficulty AND any attached token", () => {
    attachJourneySessionToken("tok-paired");
    setJourneyParams({ prompt: "p", difficulty: "easy" });
    clearJourneyParams();
    expect(getJourneyParams()).toBeNull();
    // A subsequent journey must NOT inherit the cleared token.
    setJourneyParams({ prompt: "p2", difficulty: "easy" });
    const read = getJourneyParams() as JourneyParams;
    expect(read).toEqual({ prompt: "p2", difficulty: "easy" });
    expect("anonymousSessionToken" in read).toBe(false);
  });

  it("setJourneyParams(null) clears a pending attached token too", () => {
    attachJourneySessionToken("tok-paired");
    setJourneyParams(null);
    setJourneyParams({ prompt: "p", difficulty: "easy" });
    expect("anonymousSessionToken" in getJourneyParams()!).toBe(false);
  });

  it("getJourneyParams returns null before any staging", () => {
    expect(getJourneyParams()).toBeNull();
  });
});

describe("JourneyParams.generation selection — Phase 25", () => {
  it("carries the optional provider selection through the context", () => {
    setJourneyParams({
      prompt: "a crime",
      difficulty: "medium",
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "qwen2.5:1.5b",
    });
    expect(getJourneyParams()).toEqual({
      prompt: "a crime",
      difficulty: "medium",
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "qwen2.5:1.5b",
    });
  });

  it("staging WITHOUT a selection is byte-identical (no provider keys at all)", () => {
    const params: JourneyParams = { prompt: "a crime", difficulty: "easy" };
    setJourneyParams(params);
    const read = getJourneyParams() as JourneyParams;
    expect(read).toEqual({ prompt: "a crime", difficulty: "easy" });
    expect("generationProvider" in read).toBe(false);
    expect("ollamaTransport" in read).toBe(false);
    expect("ollamaModel" in read).toBe(false);
    expect(read).toBe(params);
  });

  it("an Ollama selection carries transport + model; fake carries only the provider id", () => {
    setJourneyParams({
      prompt: "p",
      difficulty: "medium",
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
    expect(getJourneyParams()).toEqual({
      prompt: "p",
      difficulty: "medium",
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "hermes3:8b",
    });
    setJourneyParams({ prompt: "p2", difficulty: "hard", generationProvider: "fake" });
    const read = getJourneyParams() as JourneyParams;
    expect(read.generationProvider).toBe("fake");
    expect("ollamaTransport" in read).toBe(false);
    expect("ollamaModel" in read).toBe(false);
  });

  it("the selection survives an attached bridge session merge and a re-stage", () => {
    setJourneyParams({ prompt: "p", difficulty: "easy", generationProvider: "frontier" });
    attachJourneySessionToken("tok-paired");
    expect(getJourneyParams()).toEqual({
      prompt: "p",
      difficulty: "easy",
      generationProvider: "frontier",
      anonymousSessionToken: "tok-paired",
    });
    // A later re-stage (prompt edited, Generate pressed again) keeps the token
    // alongside the new provider selection.
    setJourneyParams({ prompt: "p2", difficulty: "hard", generationProvider: "ollama" });
    expect(getJourneyParams()).toEqual({
      prompt: "p2",
      difficulty: "hard",
      generationProvider: "ollama",
      anonymousSessionToken: "tok-paired",
    });
  });

  it("clearJourneyParams discards the provider selection too", () => {
    setJourneyParams({
      prompt: "p",
      difficulty: "medium",
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "qwen2.5:1.5b",
    });
    clearJourneyParams();
    expect(getJourneyParams()).toBeNull();
  });

  it("the LATEST staged transport wins — a re-stage never leaks a stale transport (§8.14/§8.15/§8.17)", () => {
    setJourneyParams({
      prompt: "p",
      difficulty: "medium",
      generationProvider: "ollama",
      ollamaTransport: "server",
      ollamaModel: "qwen2.5:1.5b",
    });
    // The /new route stages the CURRENT selection — a Server -> Bridge switch
    // immediately before Generate replaces the previous transport entirely.
    setJourneyParams({
      prompt: "p",
      difficulty: "medium",
      generationProvider: "ollama",
      ollamaTransport: "bridge",
      ollamaModel: "qwen2.5:1.5b",
    });
    expect(getJourneyParams()?.ollamaTransport).toBe("bridge");
  });
});
