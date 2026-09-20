import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { EvidenceReadResultDTO, InteractionResultDTO } from "../api/types";
import {
  InvestigationSession,
  isAuthorisationFailure,
  type InvestigationServices,
  type SceneFactory,
} from "./investigationFlow";
import { makeBootstrap, makeEmailRecord, makeWitnessRecord, TEST_TOKEN } from "./testFixtures";
import { discoveredCaptionsForWorld } from "./objectCaption";

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
    // Phase 10 Track B: the v1 catalog marks the vase non-interactable, so
    // this branch is exercised with an INTERACTABLE object whose server
    // response carries no discovery/evidence (object-agnostic controller logic).
    const services = makeServices({
      interactObject: vi.fn(
        async (): Promise<InteractionResultDTO> => ({
          objectId: "kitchen_knife",
          interaction: "inspect",
          evidenceId: null,
          discovery: null,
          result: "interacted",
        }),
      ),
    });
    const session = makeSession(services);
    await session.start(null);

    const feedback = await session.interact("kitchen_knife");

    expect(feedback.toast?.text).toBe("Interacted with Kitchen knife");
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

describe("DEF-072 — live world-object flag merge (no reload needed)", () => {
  it("knife discovery flips its model flag + list/caption derivations WITHOUT a reload", async () => {
    const services = makeServices();
    const session = makeSession(services);
    const outcome = await session.start(null);
    expect(outcome.ok).toBe(true);

    const modelBefore = session.sceneModel!;
    const knifeBefore = modelBefore.worldObjects.find((o) => o.objectId === "kitchen_knife")!;
    expect(knifeBefore.discovered).toBe(false);
    expect(knifeBefore.read).toBe(false);

    await session.interact("kitchen_knife"); // server confirms discovery + read

    const merged = session.sceneModel!;
    const knife = merged.worldObjects.find((o) => o.objectId === "kitchen_knife")!;
    // model flag (what the object-list marker renders from) flips immediately:
    expect(knife.discovered).toBe(true);
    expect(knife.read).toBe(true);
    // captions derive from the SAME merged model — the knife caption exists now:
    const captions = discoveredCaptionsForWorld(
      merged.worldObjects,
      session.discoveredRecordTitles(),
    );
    expect(captions.map((c) => c.objectId)).toContain("kitchen_knife");
    expect(captions.find((c) => c.objectId === "kitchen_knife")!.text).toBeTruthy();
  });

  it("an already-read object shows read straight from the server knowledge", async () => {
    const services = makeServices({
      getInvestigation: vi.fn(async () =>
        makeBootstrap({
          playerKnowledge: {
            discoveredEvidenceIds: ["forensic_knife_match_01"],
            readEvidenceIds: ["forensic_knife_match_01"],
            visitedLocationIds: ["miller_apartment_kitchen"],
          },
        }) as never,
      ),
    });
    const session = makeSession(services);
    await session.start(null);

    const knife = session.sceneModel!.worldObjects.find((o) => o.objectId === "kitchen_knife")!;
    expect(knife.discovered).toBe(true);
    expect(knife.read).toBe(true); // "· read" list marker renders immediately
  });

  it("a decorative / no-evidence object is unaffected by a discovery", async () => {
    const services = makeServices();
    const session = makeSession(services);
    await session.start(null);

    await session.interact("kitchen_knife");

    const merged = session.sceneModel!.worldObjects;
    const vase = merged.find((o) => o.objectId === "vase_01")!;
    expect(vase.evidenceId).toBeNull();
    expect(vase.discovered).toBe(false);
    expect(vase.read).toBe(false);
    const table = merged.find((o) => o.objectId === "apartment_table")!;
    expect(table.discovered).toBe(false);
    expect(table.read).toBe(false);
  });

  it("repeated interactions are idempotent — no duplicate or disappearing objects", async () => {
    const services = makeServices();
    const session = makeSession(services);
    await session.start(null);
    const originalIds = session.sceneModel!.worldObjects.map((o) => o.objectId);

    await session.interact("kitchen_knife");
    const afterFirst = session.sceneModel!;
    await session.interact("kitchen_knife"); // already-discovered -> no knowledge change
    const afterSecond = session.sceneModel!;

    expect(afterSecond.worldObjects.map((o) => o.objectId)).toEqual(originalIds);
    expect(new Set(afterSecond.worldObjects.map((o) => o.objectId)).size).toBe(
      afterSecond.worldObjects.length,
    );
    // No knowledge churn: identical discovered/read sets after both interactions.
    expect(afterSecond.worldObjects.find((o) => o.objectId === "kitchen_knife")!.discovered).toBe(true);
    expect(session.knowledgeSnapshot?.discoveredEvidenceIds).toEqual(["forensic_knife_match_01"]);
    expect(session.knowledgeSnapshot?.readEvidenceIds).toEqual(["forensic_knife_match_01"]);
    // The no-op second pass is reference-stable (cheap — no model rebuild):
    // an identical knowledge merge returns the SAME merged model reference.
    expect(afterSecond).toBe(afterFirst);
  });
});

describe("Phase 18C — Detective Notebook session access (record cache + hydration)", () => {
  it("exposes the bootstrap candidates and an empty record cache before any read", async () => {
    const services = makeServices();
    const session = makeSession(services);
    const outcome = await session.start(null);
    expect(outcome.ok).toBe(true);

    expect(session.candidatesSnapshot?.suspects.map((entry) => entry.id)).toContain("suspect_alpha");
    expect(session.recordCacheSnapshot()).toEqual([]);
  });

  it("recordCacheSnapshot returns the cached read records after discovery reads", async () => {
    const services = makeServices();
    const session = makeSession(services);
    await session.start(null);

    await session.interact("kitchen_knife"); // server confirms + reads a record
    const cached = session.recordCacheSnapshot();
    // The canned readRecord mock returns makeEmailRecord (id email_thomas_01)
    // for whichever id is read — the cache holds exactly what the server sent.
    expect(cached.map((record) => record.evidenceId)).toContain("email_thomas_01");
  });

  it("hydrateNotebookRecords fetches ONLY server-confirmed read ids (never undiscovered evidence)", async () => {
    const readRecordMock = vi.fn(async (): Promise<EvidenceReadResultDTO> => makeWitnessRecord());
    const services = makeServices({
      getInvestigation: vi.fn(async () =>
        makeBootstrap({
          playerKnowledge: {
            discoveredEvidenceIds: ["record_witness_hall_01", "forensic_knife_match_01"],
            // The knife is discovered but NOT read — it must never be fetched
            // by the notebook hydration.
            readEvidenceIds: ["record_witness_hall_01"],
            visitedLocationIds: ["miller_apartment_kitchen"],
          },
        }) as never,
      ),
      readRecord: readRecordMock,
    });
    const session = makeSession(services);
    await session.start(null);

    await session.hydrateNotebookRecords();

    expect(readRecordMock).toHaveBeenCalledTimes(1);
    expect(readRecordMock).toHaveBeenCalledWith(PT_ID, "record_witness_hall_01", TEST_TOKEN);
    expect(readRecordMock).not.toHaveBeenCalledWith(PT_ID, "forensic_knife_match_01", TEST_TOKEN);
    expect(session.recordCacheSnapshot().map((record) => record.evidenceId)).toEqual([
      "record_witness_hall_01",
    ]);
  });

  it("hydration is idempotent: already-cached records are never re-fetched", async () => {
    const readRecordMock = vi.fn(async (): Promise<EvidenceReadResultDTO> => makeEmailRecord());
    const services = makeServices({
      getInvestigation: vi.fn(async () =>
        makeBootstrap({
          playerKnowledge: {
            discoveredEvidenceIds: ["email_thomas_01"],
            readEvidenceIds: ["email_thomas_01"],
            visitedLocationIds: ["miller_apartment_kitchen"],
          },
        }) as never,
      ),
      readRecord: readRecordMock,
    });
    const session = makeSession(services);
    await session.start(null);

    await session.hydrateNotebookRecords();
    await session.hydrateNotebookRecords();
    await session.hydrateNotebookRecords();

    expect(readRecordMock).toHaveBeenCalledTimes(1);
  });

  it("a failed lazy fetch degrades gracefully (cache keeps what loaded; no throw)", async () => {
    const services = makeServices({
      getInvestigation: vi.fn(async () =>
        makeBootstrap({
          playerKnowledge: {
            discoveredEvidenceIds: ["record_witness_hall_01", "record_cctv_02"],
            readEvidenceIds: ["record_cctv_02"],
            visitedLocationIds: ["miller_apartment_kitchen"],
          },
        }) as never,
      ),
      readRecord: vi.fn(async () => {
        throw new ApiError(500, "INTERNAL_ERROR", "record service hiccup", null);
      }),
    });
    const session = makeSession(services);
    await session.start(null);

    await session.hydrateNotebookRecords(); // must NOT throw
    expect(session.recordCacheSnapshot()).toEqual([]);
  });

  it("hydration is a no-op before the session started (no knowledge, no fetch)", async () => {
    const services = makeServices();
    const session = makeSession(services);

    await session.hydrateNotebookRecords(); // must NOT throw

    expect(services.readRecord).not.toHaveBeenCalled();
  });
});