import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { InteractionResultDTO } from "../api/types";
import {
  InvestigationSession,
  isAuthorisationFailure,
  type InvestigationServices,
  type SceneFactory,
} from "./investigationFlow";
import { makeBootstrap, makeEmailRecord, TEST_TOKEN } from "./testFixtures";

/**
 * Phase 6 P frontend coverage that lives at the controller level:
 * interaction dispatch, discovery feedback, 409 -> safe error, idempotent
 * already-discovered, backend-down, authorization failure, malformed
 * WorldGraph and scene (engine) initialization failure. All deterministic,
 * all offline.
 */

const PT_ID = "PT-test-0001";

function knifeInteractResult(overrides: Partial<InteractionResultDTO> = {}): InteractionResultDTO {
  return {
    objectId: "kitchen_knife",
    interaction: "inspect",
    evidenceId: "forensic_knife_match_01",
    discovery: {
      evidenceId: "forensic_knife_match_01",
      kind: "forensic",
      title: "Kitchen knife",
      interaction: "inspect",
      state: "discovered",
    },
    result: "interacted",
    ...overrides,
  };
}

function makeServices(overrides: Partial<InvestigationServices> = {}): InvestigationServices {
  const services: InvestigationServices = {
    getInvestigation: vi.fn(async () => makeBootstrap()),
    interactObject: vi.fn(async () => knifeInteractResult()),
    discoverEvidence: vi.fn(async (): Promise<Awaited<ReturnType<InvestigationServices["discoverEvidence"]>>> => ({
      evidenceId: "body_found_01",
      kind: "witness_observation",
      title: "Body found in the kitchen",
      interaction: "view_record",
      state: "discovered",
    })),
    readRecord: vi.fn(async () => makeEmailRecord()),
    ...overrides,
  };
  return services;
}

const noScene: SceneFactory | null = null;

function makeSession(services: InvestigationServices, createScene: SceneFactory | null = noScene): InvestigationSession {
  return new InvestigationSession(services, TEST_TOKEN, createScene, { playthroughId: PT_ID });
}

describe("start: bootstrap + scene model", () => {
  it("loads a bootstrap, builds the scene model and caches player-safe DTOs", async () => {
    const services = makeServices();
    const session = makeSession(services);
    const outcome = await session.start(null);

    expect(outcome.ok).toBe(true);
    if (!outcome.ok) throw new Error("expected ok start");
    expect(outcome.model.worldObjects.map((o) => o.objectId)).toEqual([
      "apartment_door",
      "apartment_lamp",
      "apartment_laptop",
      "apartment_table",
      "kitchen_knife",
      "letter_opener",
      "scissors",
      "vase_01",
      "victim_body_placeholder",
    ]);
    expect(session.sceneModel).not.toBeNull();
    expect(session.knowledgeSnapshot?.discoveredEvidenceIds).toEqual([]);
    expect(session.currentToast).toBeNull();
  });
});

describe("start: error/degraded states", () => {
  it("backend down (network failure) -> network error, retryable, never throws", async () => {
    const services = makeServices({
      getInvestigation: vi.fn(async () => {
        throw new ApiError(0, "NETWORK_ERROR", "fetch failed", null);
      }),
    });
    const outcome = await makeSession(services).start(null);

    expect(outcome.ok).toBe(false);
    if (outcome.ok) throw new Error("expected failure");
    expect(outcome.kind).toBe("network");
    expect(outcome.retryable).toBe(true);
    expect(outcome.tokenInvalid).toBe(false);
  });

  it("401 -> authorization failure with a token reset affordance", async () => {
    const services = makeServices({
      getInvestigation: vi.fn(async () => {
        throw new ApiError(401, "UNAUTHORIZED", "bearer token expired", null);
      }),
    });
    const outcome = await makeSession(services).start(null);

    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.kind).toBe("auth");
      expect(outcome.tokenInvalid).toBe(true);
    }
  });

  it("403 -> authorization failure too", async () => {
    const services = makeServices({
      getInvestigation: vi.fn(async () => {
        throw new ApiError(403, "FORBIDDEN", "token state conflict", null);
      }),
    });
    const outcome = await makeSession(services).start(null);
    expect(outcome).toEqual({
      ok: false,
      kind: "auth",
      message: "Playthrough access is not valid for this investigation.",
      tokenInvalid: true,
      retryable: false,
    });
  });

  it("malformed WorldGraph -> safe malformed error, never a white screen", async () => {
    const services = makeServices({
      getInvestigation: vi.fn(async () => ({ garbage: true }) as never),
    });
    const outcome = await makeSession(services).start(null);

    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.kind).toBe("malformed");
      expect(outcome.retryable).toBe(false);
    }
  });

  it("Babylon initialization failure (engine factory throws) -> scene error message", async () => {
    const services = makeServices();
    const createScene: SceneFactory = () => ({ ok: false, error: "no gpu available" });
    const outcome = await makeSession(services, createScene).start({} as HTMLCanvasElement);

    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.kind).toBe("scene");
      expect(outcome.message).toBe("no gpu available");
      expect(outcome.retryable).toBe(true);
    }
  });

  it("isAuthorisationFailure identifies 401/403 (and nothing else)", () => {
    expect(isAuthorisationFailure(new ApiError(401, "UNAUTHORIZED", "x", null))).toBe(true);
    expect(isAuthorisationFailure(new ApiError(403, "FORBIDDEN", "x", null))).toBe(true);
    expect(isAuthorisationFailure(new ApiError(409, "INTERACTION_NOT_ALLOWED", "x", null))).toBe(false);
    expect(isAuthorisationFailure(new Error("nope"))).toBe(false);
  });
});

describe("interaction dispatch", () => {
  it("sends exactly the object's published interaction to interactObject", async () => {
    const services = makeServices();
    const session = makeSession(services);
    await session.start(null);

    const feedback = await session.interact("kitchen_knife");

    const interactMock = services.interactObject as ReturnType<typeof vi.fn>;
    expect(interactMock).toHaveBeenCalledWith(PT_ID, "kitchen_knife", "inspect", TEST_TOKEN);
    expect(feedback.error).toBeNull();
  });

  it("shows a discovery toast with the evidence title and auto-opens the record", async () => {
    const services = makeServices();
    const session = makeSession(services);
    await session.start(null);

    const feedback = await session.interact("kitchen_knife");

    expect(feedback.toast?.text).toBe("Discovered: Kitchen knife");
    expect(feedback.toast?.evidenceId).toBe("forensic_knife_match_01");
    expect(feedback.record).not.toBeNull();
    expect(feedback.record?.evidenceId).toBe("email_thomas_01"); // canned record
    // record only fetched AFTER the server confirmed discovery
    expect(services.readRecord).toHaveBeenCalledWith(PT_ID, "forensic_knife_match_01", TEST_TOKEN);
  });

  it("laptop interaction uses the published 'read' interaction and opens the email (DEF-049)", async () => {
    const services = makeServices({
      interactObject: vi.fn(
        async (): Promise<InteractionResultDTO> => ({
          objectId: "apartment_laptop",
          interaction: "read",
          evidenceId: "email_thomas_01",
          discovery: {
            evidenceId: "email_thomas_01",
            kind: "email",
            title: "Weekend plans",
            interaction: "read",
            state: "discovered",
          },
          result: "interacted",
        }),
      ),
    });
    const session = makeSession(services);
    await session.start(null);

    const feedback = await session.interact("apartment_laptop");

    expect(services.interactObject).toHaveBeenCalledWith(PT_ID, "apartment_laptop", "read", TEST_TOKEN);
    expect(feedback.error).toBeNull();
    expect(feedback.toast?.text).toBe("Discovered: Weekend plans");
    expect(services.readRecord).toHaveBeenCalledWith(PT_ID, "email_thomas_01", TEST_TOKEN);
    expect(feedback.record?.kind).toBe("email");
    expect(session.knowledgeSnapshot?.discoveredEvidenceIds).toContain("email_thomas_01");
  });

  it("does not read a record when the interaction reveals no evidence", async () => {
    const services = makeServices({
      interactObject: vi.fn(
        async (): Promise<InteractionResultDTO> => ({
          objectId: "vase_01",
          interaction: "inspect",
          evidenceId: null,
          discovery: null,
          result: "interacted",
        }),
      ),
    });
    const session = makeSession(services);
    await session.start(null);

    const feedback = await session.interact("vase_01");

    expect(feedback.toast?.text).toBe("Interacted with Vase");
    expect(feedback.record).toBeNull();
    expect(services.readRecord).not.toHaveBeenCalled();
  });

  it("maps a 409 wrong-interaction response to a safe gameplay error", async () => {
    const services = makeServices({
      interactObject: vi.fn(async () => {
        throw new ApiError(409, "INTERACTION_NOT_ALLOWED", "interaction forbidden in this state", null);
      }),
    });
    const session = makeSession(services);
    await session.start(null);

    const feedback = await session.interact("kitchen_knife");

    expect(feedback.toast).toBeNull();
    expect(feedback.record).toBeNull();
    expect(feedback.error?.message).toBe("That action is not allowed for this object right now.");
  });

  it("never touches the network for objects that cannot be interacted with", async () => {
    const services = makeServices();
    const session = makeSession(services);
    await session.start(null);

    const feedback = await session.interact("does_not_exist");

    expect(feedback.error?.message).toBe("That object is not part of this scene.");
    expect(services.interactObject).not.toHaveBeenCalled();
  });
});

describe("idempotent already-discovered handling", () => {
  it("shows 'Already discovered', reuses the cached record and keeps knowledge stable", async () => {
    const services = makeServices({
      interactObject: vi.fn(async () =>
        knifeInteractResult({
          discovery: {
            evidenceId: "forensic_knife_match_01",
            kind: "forensic",
            title: "Kitchen knife",
            interaction: "inspect",
            state: "already-discovered",
          },
        }) as InteractionResultDTO,
      ),
    });
    const session = makeSession(services);
    await session.start(null);

    const first = await session.interact("kitchen_knife");
    const second = await session.interact("kitchen_knife");

    expect(first.toast?.text).toContain("Already discovered");
    expect(second.toast?.text).toContain("Already discovered");
    expect(services.readRecord).toHaveBeenCalledTimes(1); // cached after first read
    expect(session.knowledgeSnapshot?.discoveredEvidenceIds).toEqual(["forensic_knife_match_01"]);
    expect(session.knowledgeSnapshot?.readEvidenceIds).toEqual(["forensic_knife_match_01"]);
  });
});

describe("server-authoritative knowledge", () => {
  it("only adds ids the server returned — never marks anything on its own", async () => {
    const services = makeServices({
      getInvestigation: vi.fn(async () =>
        makeBootstrap({
          playerKnowledge: {
            discoveredEvidenceIds: ["already_known_01"],
            readEvidenceIds: [],
            visitedLocationIds: ["miller_apartment_kitchen"],
          },
        }) as never,
      ),
    });
    const session = makeSession(services);
    await session.start(null);

    await session.interact("kitchen_knife"); // server returns forensic_knife_match_01

    expect(session.knowledgeSnapshot?.discoveredEvidenceIds).toEqual([
      "already_known_01",
      "forensic_knife_match_01",
    ]);
  });

  it("guards a failed record read after discovery (403) without crashing", async () => {
    const services = makeServices({
      readRecord: vi.fn(async () => {
        throw new ApiError(403, "EVIDENCE_NOT_DISCOVERED", "record not readable yet", null);
      }),
    });
    const session = makeSession(services);
    await session.start(null);

    const feedback = await session.interact("kitchen_knife");

    expect(feedback.toast?.text).toBe("Discovered: Kitchen knife");
    expect(feedback.record).toBeNull();
    expect(feedback.error?.message).toContain("not readable yet");
  });

  it("dismisses the toast on request", async () => {
    const services = makeServices();
    const session = makeSession(services);
    await session.start(null);

    await session.interact("kitchen_knife");
    expect(session.currentToast).not.toBeNull();
    session.dismissToast();
    expect(session.currentToast).toBeNull();
  });

  it("disposeScene is safe and idempotent when no 3D scene was created", async () => {
    const session = makeSession(makeServices());
    await session.start(null);
    session.disposeScene();
    session.disposeScene();
    expect(session.sceneModel).not.toBeNull(); // knowledge/model untouched
  });
});