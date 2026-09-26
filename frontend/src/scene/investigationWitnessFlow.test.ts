import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { EvidenceReadResultDTO, InteractionResultDTO, WitnessInterviewResponse } from "../api/types";
import {
  InvestigationSession,
  type InvestigationServices,
  type SceneFactory,
} from "./investigationFlow";
import {
  EMILY_TIME_EVIDENCE_ID,
  EMILY_WITNESS_ID,
  LISA_WITNESS_ID,
  makeEmilyTimeDiscoveryRecord,
  makeEmilyTimeStatement,
  makeHostileWitnessInterviewResponse,
  makeWitnessBootstrap,
  makeWitnessInterviewResponse,
  TEST_TOKEN,
} from "./testFixtures";
import { buildNotebookModel } from "../notebook/notebookModel";

/**
 * Phase 23 — witness interview session coverage (controller level, offline):
 *   - the bootstrapped player-safe witness list + ON_SCENE linkage;
 *   - askWitness guards (not loaded / unknown witness / service absent);
 *   - the live question flow POSTs the closed questionType and flows an
 *     interview discovery into the EXISTING knowledge/records machinery;
 *   - idempotent re-ask (cached, no POST, no duplicate notebook line);
 *   - neutral answers change no knowledge;
 *   - hostile response payloads + typed failures map to safe player messages.
 */

const PT_ID = "PT-test-0001";

function witnessServices(
  overrides: Partial<InvestigationServices> = {},
): InvestigationServices {
  const services: InvestigationServices = {
    getInvestigation: vi.fn(async () => makeWitnessBootstrap()),
    interactObject: vi.fn(
      async (): Promise<InteractionResultDTO> => ({
        objectId: "kitchen_knife",
        interaction: "inspect",
        evidenceId: "forensic_knife_match_01",
        discovery: null,
        result: "interacted",
      }),
    ),
    readRecord: vi.fn(async (): Promise<EvidenceReadResultDTO> => makeEmilyTimeDiscoveryRecord()),
    interviewWitness: vi.fn(
      async (
        _pt: string,
        witnessId: string,
        questionType: WitnessInterviewResponse["questionType"],
      ): Promise<WitnessInterviewResponse> => {
        if (witnessId === EMILY_WITNESS_ID) return makeWitnessInterviewResponse(questionType);
        if (witnessId === LISA_WITNESS_ID && questionType === "PERSON") {
          return {
            witnessId: LISA_WITNESS_ID,
            displayName: "Lisa König",
            questionType,
            statement: {
              summary: "Lisa says she saw a tall figure leaving the corridor.",
              observations: [],
            },
            discovery: null,
          };
        }
        throw new ApiError(404, "WITNESS_NOT_FOUND", "unknown witness", null);
      },
    ),
    ...overrides,
  };
  return services;
}

const noScene: SceneFactory | null = null;

async function startedSession(overrides: Partial<InvestigationServices> = {}): Promise<InvestigationSession> {
  const session = new InvestigationSession(witnessServices(overrides), TEST_TOKEN, noScene, {
    playthroughId: PT_ID,
  });
  const outcome = await session.start(null);
  if (!outcome.ok) throw new Error("expected ok start for witness bootstrap");
  return session;
}

describe("Phase 23 — session: witness list from the bootstrap", () => {
  it("exposes ONLY the player-safe witness list (ids + names + presence), never statements", async () => {
    const session = await startedSession();
    const witnesses = session.witnessesSnapshot();
    expect(witnesses.map((w) => w.witnessId)).toEqual([EMILY_WITNESS_ID, LISA_WITNESS_ID]);
    expect(witnesses[0]).toMatchObject({ displayName: "Emily Reed", presence: "ON_SCENE" });
    expect(witnesses[1]).toMatchObject({ displayName: "Lisa König", presence: "REMOTE_STATEMENT", sceneObjectId: null });
    // No statement content ever rides on the list.
    expect(JSON.stringify(witnesses)).not.toContain("heavy impact");
  });

  it("resolves ON_SCENE person picks to their witness and NEVER matches a remote witness to a scene object", async () => {
    const session = await startedSession();
    expect(session.sceneWitnessByObjectId(EMILY_WITNESS_ID)?.witnessId).toBe(EMILY_WITNESS_ID);
    expect(session.sceneWitnessByObjectId("kitchen_knife")).toBeNull();
    expect(session.sceneWitnessByObjectId(LISA_WITNESS_ID)).toBeNull();
  });

  it("pre-23 servers (no witnesses field) degrade to an empty list — the UI renders no witness section", async () => {
    const services = witnessServices();
    const bootstrap = makeWitnessBootstrap();
    delete (bootstrap as { witnesses?: unknown }).witnesses;
    services.getInvestigation = vi.fn(async () => bootstrap);
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, { playthroughId: PT_ID });
    const outcome = await session.start(null);
    expect(outcome.ok).toBe(true);
    expect(session.witnessesSnapshot()).toEqual([]);
    expect(session.sceneWitnessByObjectId("anything")).toBeNull();
  });
});

describe("Phase 23 — session: askWitness guards", () => {
  it("returns a safe error before the investigation loaded", async () => {
    const session = new InvestigationSession(witnessServices(), TEST_TOKEN, noScene, {
      playthroughId: PT_ID,
    });
    const outcome = await session.askWitness(EMILY_WITNESS_ID, "TIME");
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.error.message).toContain("not loaded");
    }
  });

  it("rejects a witness id that is not in the player-safe list (no oracle)", async () => {
    const session = await startedSession();
    const outcome = await session.askWitness("hidden_person_99", "TIME");
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.error.message).toContain("not part of this playthrough");
    }
  });

  it("degrades gracefully when the service is absent (older backend)", async () => {
    const services = witnessServices();
    delete services.interviewWitness;
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, { playthroughId: PT_ID });
    await session.start(null);
    const outcome = await session.askWitness(EMILY_WITNESS_ID, "TIME");
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.error.message).toContain("not available");
    }
  });
});

describe("Phase 23 — session: the live question flow", () => {
  it("POSTs the closed questionType and flows discovered evidence into the EXISTING knowledge/records machinery", async () => {
    const services = witnessServices();
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, { playthroughId: PT_ID });
    await session.start(null);

    const outcome = await session.askWitness(EMILY_WITNESS_ID, "TIME");
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) throw new Error("expected ok");
    expect(outcome.cached).toBe(false);
    expect(outcome.statement.summary).toContain("heavy impact");
    expect(outcome.record?.evidenceId).toBe(EMILY_TIME_EVIDENCE_ID);

    expect(services.interviewWitness).toHaveBeenCalledWith(PT_ID, EMILY_WITNESS_ID, "TIME", TEST_TOKEN);

    // Discovery flows into the server-derived knowledge (discovered AND read —
    // the response carries the whole player-safe record) + the record cache.
    expect(session.discoveredEvidenceIdsSnapshot()).toContain(EMILY_TIME_EVIDENCE_ID);
    expect(session.readEvidenceIdsSnapshot()).toContain(EMILY_TIME_EVIDENCE_ID);
    expect(session.recordCacheSnapshot().map((r) => r.evidenceId)).toContain(EMILY_TIME_EVIDENCE_ID);
    expect(session.discoveredRecordTitles().get(EMILY_TIME_EVIDENCE_ID)).toContain("Emily Reed");

    // The notebook's Witness statements group lists the line exactly once.
    const model = buildNotebookModel({
      discoveredEvidenceIds: session.discoveredEvidenceIdsSnapshot(),
      readEvidenceIds: session.readEvidenceIdsSnapshot(),
      worldObjects: session.sceneModel!.worldObjects,
      records: session.recordCacheSnapshot(),
      witnessStatements: session.askedWitnessStatementsSnapshot(),
    });
    const group = model.groups.find((g) => g.id === "witness-statements")!;
    expect(group.entries).toHaveLength(1);
    expect(group.entries[0].detail).toContain("When were you there?");
  });

  it("IDEMPOTENT re-ask: cached statement, NO second POST, no duplicate notebook line", async () => {
    const services = witnessServices();
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, { playthroughId: PT_ID });
    await session.start(null);

    const first = await session.askWitness(EMILY_WITNESS_ID, "TIME");
    const second = await session.askWitness(EMILY_WITNESS_ID, "TIME");
    expect(first.ok && !first.cached).toBe(true);
    expect(second.ok && second.cached).toBe(true);
    expect((services.interviewWitness as ReturnType<typeof vi.fn>)).toHaveBeenCalledTimes(1);
    if (second.ok) {
      expect(second.statement.summary).toBe(first.ok ? first.statement.summary : "");
      // A re-ask never re-opens the evidence panel / never re-returns the record.
      expect(second.record).toBeNull();
      expect(second.discovery).toBeNull();
    }
    // One asked entry + one notebook line.
    expect(session.askedWitnessStatementsSnapshot()).toHaveLength(1);
    const model = buildNotebookModel({
      discoveredEvidenceIds: session.discoveredEvidenceIdsSnapshot(),
      readEvidenceIds: session.readEvidenceIdsSnapshot(),
      worldObjects: session.sceneModel!.worldObjects,
      records: session.recordCacheSnapshot(),
      witnessStatements: session.askedWitnessStatementsSnapshot(),
    });
    const group = model.groups.find((g) => g.id === "witness-statements")!;
    expect(group.entries).toHaveLength(1);
    expect(session.discoveredEvidenceIdsSnapshot()).toHaveLength(1);
  });

  it("a neutral answer changes NO knowledge and stays in the asked store", async () => {
    const services = witnessServices();
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, { playthroughId: PT_ID });
    await session.start(null);

    const outcome = await session.askWitness(EMILY_WITNESS_ID, "SOUND");
    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      expect(outcome.statement.summary).toBe("No. Nothing stood out to me.");
      expect(outcome.record).toBeNull();
      expect(outcome.discovery).toBeNull();
    }
    expect(session.discoveredEvidenceIdsSnapshot()).toEqual([]);
    expect(session.readEvidenceIdsSnapshot()).toEqual([]);
    expect(session.askedWitnessStatementsSnapshot()).toHaveLength(1);
  });

  it("a REMOTE driver witness (Lisa König) interviews through the same flow without a scene object", async () => {
    const session = await startedSession();
    const outcome = await session.askWitness(LISA_WITNESS_ID, "PERSON");
    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      expect(outcome.displayName).toBe("Lisa König");
      expect(outcome.statement.summary).toContain("tall figure");
    }
    expect(session.sceneWitnessByObjectId(LISA_WITNESS_ID)).toBeNull();
  });
});

describe("Phase 23 — session: hostile payloads and typed failures", () => {
  it("a HOSTILE interview response is coerced safely (bounded literal text) and never throws", async () => {
    const services = witnessServices();
    services.interviewWitness = vi.fn(async () => makeHostileWitnessInterviewResponse() as never as WitnessInterviewResponse);
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, { playthroughId: PT_ID });
    await session.start(null);

    const outcome = await session.askWitness(EMILY_WITNESS_ID, "TIME");
    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      expect(outcome.statement.summary).toContain("<script>");
      expect(outcome.statement.summary.length).toBeLessThanOrEqual(2000);
    }
    // No knowledge was created (the hostile payload carried no discovery record).
    expect(session.discoveredEvidenceIdsSnapshot()).toEqual([]);
  });

  it("typed failures map to safe player messages (WITNESS_NOT_FOUND / QUESTION_NOT_AVAILABLE / auth)", async () => {
    const services = witnessServices({
      interviewWitness: vi.fn(async () => {
        throw new ApiError(404, "WITNESS_NOT_FOUND", "no such witness", null);
      }),
    });
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, { playthroughId: PT_ID });
    await session.start(null);
    // Use a VALID list witness so the request actually reaches the endpoint.
    const outcome = await session.askWitness(EMILY_WITNESS_ID, "TIME");
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      // The raw internal id/text never surfaces in a player-safe message.
      expect(outcome.error.message).not.toContain("no such witness");
      expect(outcome.error.message).not.toContain(EMILY_WITNESS_ID);
      expect(outcome.error.message).toBe("That question is not available right now.");
    }
  });

  it("401 from the interview endpoint carries the token-reset hint", async () => {
    const services = witnessServices({
      interviewWitness: vi.fn(async () => {
        throw new ApiError(401, "UNAUTHORIZED", "token expired", null);
      }),
    });
    const session = new InvestigationSession(services, TEST_TOKEN, noScene, { playthroughId: PT_ID });
    await session.start(null);
    const outcome = await session.askWitness(EMILY_WITNESS_ID, "TIME");
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.error.message).toContain("Reset the token");
    }
  });
});

describe("Phase 23 — session: asked-state helpers", () => {
  it("reports asked question types in the closed panel order and caches their statements", async () => {
    const session = await startedSession();
    await session.askWitness(EMILY_WITNESS_ID, "OBJECT");
    await session.askWitness(EMILY_WITNESS_ID, "TIME");
    expect(session.askedWitnessQuestionTypes(EMILY_WITNESS_ID)).toEqual(["TIME", "OBJECT"]);
    expect(session.hasAskedWitnessQuestion(EMILY_WITNESS_ID, "TIME")).toBe(true);
    expect(session.hasAskedWitnessQuestion(EMILY_WITNESS_ID, "SOUND")).toBe(false);
    const cache = session.witnessStatementCache(EMILY_WITNESS_ID);
    expect(cache.get("TIME")?.summary).toBe(makeEmilyTimeStatement().summary);
    expect(cache.get("SOUND")).toBeUndefined();
  });
});