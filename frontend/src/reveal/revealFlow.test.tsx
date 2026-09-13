import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "../api/client";
import type { InvestigationBootstrapResponse, RevealResponse } from "../api/types";
import { makeBootstrap, makeRevealResponse, TEST_TOKEN } from "../scene/testFixtures";
import RevealScreen from "./RevealScreen";
import { RevealFlow, type RevealServices } from "./revealFlow";

/**
 * Reveal page controller coverage (Phase 7 E/H/O): restart durability and
 * client-side 403 gating. The /reveal route always re-fetches GET /reveal and
 * renders from the DTO, so an ACCUSED or REVEALED playthrough restores the
 * identical screen after any reload. Before accusation the frozen 403
 * REVEAL_NOT_AVAILABLE gate produces the gated error state.
 */

const PT_ID = "PT-test-0001";

function makeServices(overrides: Partial<RevealServices> = {}): RevealServices {
  return {
    getReveal: vi.fn(async () => makeRevealResponse()),
    getInvestigation: vi.fn(async () => makeBootstrap()),
    ...overrides,
  };
}

function makeFlow(services: RevealServices): RevealFlow {
  return new RevealFlow(services, TEST_TOKEN, { playthroughId: PT_ID });
}

describe("reload of ACCUSED state (reveal available)", () => {
  it("a canned ACCUSED bootstrap means the case was accused — GET reveal returns the DTO and the screen restores", async () => {
    // Server truth: the playthrough is ACCUSED; reveal is authorized from ACCUSED.
    const accusedBootstrap: InvestigationBootstrapResponse = makeBootstrap({ state: "ACCUSED" });
    const services = makeServices({
      getInvestigation: vi.fn(async () => accusedBootstrap),
      getReveal: vi.fn(async () => makeRevealResponse()),
    });
    const state = await makeFlow(services).load();

    expect(state.status).toBe("revealed");
    if (state.status !== "revealed") throw new Error("expected revealed");
    expect(state.reveal.status).toBe("REVEALED");
    expect(state.reveal.truth.murdererName).toBe("Ada Marsh");
    // Candidates were loaded from the ACCUSED bootstrap and used for name resolution.
    expect(state.candidates?.suspects[0].name).toBe("Ada Marsh");
    expect(services.getReveal).toHaveBeenCalledWith(PT_ID, TEST_TOKEN);
  });
});

describe("reload of REVEALED state (identical restore)", () => {
  it("two consecutive loads fetch /reveal again and render IDENTICAL markup (idempotent restore)", async () => {
    const canned = makeRevealResponse();
    const services = makeServices({ getReveal: vi.fn(async () => canned) });

    const first = await makeFlow(services).load();
    const second = await makeFlow(services).load();
    expect(services.getReveal).toHaveBeenCalledTimes(2);

    if (first.status !== "revealed" || second.status !== "revealed") throw new Error("expected revealed");
    expect(first.reveal).toEqual(second.reveal);
    expect(renderToStaticMarkup(<RevealScreen reveal={first.reveal} candidates={first.candidates} />)).toBe(
      renderToStaticMarkup(<RevealScreen reveal={second.reveal} candidates={second.candidates} />),
    );
  });

  it("a REVEALED bootstrap still parses and provides candidates for name resolution", async () => {
    const services = makeServices({
      getInvestigation: vi.fn(async () => makeBootstrap({ state: "REVEALED" })),
    });
    const state = await makeFlow(services).load();
    expect(state.status).toBe("revealed");
    if (state.status !== "revealed") throw new Error("expected revealed");
    expect(state.candidates).not.toBeNull();
  });
});

describe("client-side 403 gating (direct navigation before accusation)", () => {
  it("maps REVEAL_NOT_AVAILABLE to the gated error state (not-accused)", async () => {
    const services = makeServices({
      getReveal: vi.fn(async () => {
        throw new ApiError(403, "REVEAL_NOT_AVAILABLE", "the playthrough has no accusation yet", null);
      }),
    });
    const state = await makeFlow(services).load();

    expect(state.status).toBe("error");
    if (state.status !== "error") throw new Error("expected error");
    expect(state.kind).toBe("not-accused");
    expect(state.tokenInvalid).toBe(false);
    expect(state.retryable).toBe(false);
    expect(state.message).toMatch(/No accusation/);
  });

  it("a non-conflict 403 (forbidden) is an expired-credential error with a reset affordance", async () => {
    const services = makeServices({
      getReveal: vi.fn(async () => {
        throw new ApiError(403, "FORBIDDEN", "token cannot access this playthrough", null);
      }),
    });
    const state = await makeFlow(services).load();
    expect(state.status).toBe("error");
    if (state.status !== "error") throw new Error("expected error");
    expect(state.kind).toBe("auth");
    expect(state.tokenInvalid).toBe(true);
  });
});

describe("reveal error handling", () => {
  it("401 expired credential -> auth error with tokenInvalid", async () => {
    const services = makeServices({
      getReveal: vi.fn(async () => {
        throw new ApiError(401, "UNAUTHORIZED", "bearer token expired", null);
      }),
    });
    const state = await makeFlow(services).load();
    expect(state.status).toBe("error");
    if (state.status !== "error") throw new Error("expected error");
    expect(state.kind).toBe("auth");
    expect(state.tokenInvalid).toBe(true);
    expect(state.retryable).toBe(false);
  });

  it("backend unreachable -> retryable network error", async () => {
    const services = makeServices({
      getReveal: vi.fn(async () => {
        throw new ApiError(0, "NETWORK_ERROR", "fetch failed", null);
      }),
    });
    const state = await makeFlow(services).load();
    expect(state.status).toBe("error");
    if (state.status !== "error") throw new Error("expected error");
    expect(state.kind).toBe("network");
    expect(state.retryable).toBe(true);
  });

  it("5xx -> retryable gameplay error", async () => {
    const services = makeServices({
      getReveal: vi.fn(async () => {
        throw new ApiError(500, "INTERNAL", "boom", null);
      }),
    });
    const state = await makeFlow(services).load();
    expect(state.status).toBe("error");
    if (state.status !== "error") throw new Error("expected error");
    expect(state.kind).toBe("gameplay");
    expect(state.retryable).toBe(true);
  });
});

describe("best-effort candidates (leak-safe degradation)", () => {
  it("renders the reveal from the DTO even when the bootstrap lookup fails", async () => {
    const services = makeServices({
      getInvestigation: vi.fn(async () => {
        throw new ApiError(404, "NOT_FOUND", "no scene for this playthrough", null);
      }),
    });
    const state = await makeFlow(services).load();

    expect(state.status).toBe("revealed");
    if (state.status !== "revealed") throw new Error("expected revealed");
    expect(state.candidates).toBeNull();
    expect(state.reveal.truth.murdererName).toBe("Ada Marsh");
    // getReveal was still the singular authority; the bootstrap failure never leaks.
    expect(services.getReveal).toHaveBeenCalledTimes(1);
  });

  it("a malformed reveal DTO surfaces as a safe non-retryable gameplay error (never a white screen)", async () => {
    const services = makeServices({
      getReveal: vi.fn(async () => ({ garbage: true }) as unknown as Promise<RevealResponse>),
    });
    const state = await makeFlow(services).load();
    expect(state.status).toBe("error");
    if (state.status !== "error") throw new Error("expected error");
    expect(state.retryable).toBe(false);
  });
});