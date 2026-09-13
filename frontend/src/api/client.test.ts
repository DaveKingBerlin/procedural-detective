import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  API_BASE_URL,
  ApiError,
  discoverEvidence,
  getHealth,
  getInvestigation,
  getReadiness,
  interactObject,
  readRecord,
} from "./client";
import { TEST_TOKEN } from "../scene/testFixtures";

const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>();

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getHealth", () => {
  it("parses a 200 health payload into the typed HealthResponse", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ status: "ok", service: "procedural-detective", version: "0.1.0" }, 200),
    );

    const health = await getHealth();

    expect(health).toEqual({ status: "ok", service: "procedural-detective", version: "0.1.0" });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url.startsWith(API_BASE_URL)).toBe(true);
    expect(url).toContain("/api/v1/health");
  });
});

describe("getReadiness", () => {
  it("parses a 200 readiness payload into the typed ReadinessResponse", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "ready", database: "ok", migrations: "ok" }, 200));

    await expect(getReadiness()).resolves.toEqual({
      status: "ready",
      database: "ok",
      migrations: "ok",
    });
  });

  it("maps a 503 NOT_READY error envelope into a structured ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: "NOT_READY", message: "database migration pending", details: null } },
        503,
      ),
    );

    const error = await getReadiness().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error("expected ApiError");
    expect(error.status).toBe(503);
    expect(error.code).toBe("NOT_READY");
    expect(error.message).toBe("database migration pending");
    expect(error.details).toBeNull();
  });

  it("maps a non-JSON 500 response to a status-derived ApiError", async () => {
    fetchMock.mockResolvedValue(new Response("boom", { status: 500 }));

    const error = await getReadiness().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error("expected ApiError");
    expect(error.status).toBe(500);
    expect(error.code).toBe("HTTP_500");
  });
});

describe("non-JSON 2xx responses", () => {
  it("maps a 200 text/plain body to an ApiError INVALID_RESPONSE instead of a raw SyntaxError", async () => {
    fetchMock.mockResolvedValue(
      new Response("plain text, not json", {
        status: 200,
        headers: { "Content-Type": "text/plain" },
      }),
    );

    const error = await getHealth().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error("expected ApiError");
    expect(error.status).toBe(200);
    expect(error.code).toBe("INVALID_RESPONSE");
  });

  it("maps a 204 empty body to an ApiError INVALID_RESPONSE instead of a raw TypeError", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    const error = await getReadiness().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error("expected ApiError");
    expect(error.status).toBe(204);
    expect(error.code).toBe("INVALID_RESPONSE");
  });
});

describe("transport failures", () => {
  it("maps a rejected fetch to a NETWORK_ERROR ApiError (backend down)", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const error = await getHealth().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error("expected ApiError");
    expect(error.status).toBe(0);
    expect(error.code).toBe("NETWORK_ERROR");
  });
});

/* ======================================================================
 * Phase 6 investigation endpoints
 * ==================================================================== */

const BOOTSTRAP_BODY = {
  playthroughId: "PT-demo-0001",
  caseId: "CASE-test-01",
  caseVersion: 1,
  state: "PLAYING",
  playerKnowledge: { discoveredEvidenceIds: [], readEvidenceIds: [], visitedLocationIds: [] },
  scene: {
    location: { locationId: "miller_apartment_kitchen", name: "Miller Apartment - Kitchen" },
    worldObjects: [],
  },
};

const INTERACT_BODY = {
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
};

describe("getInvestigation", () => {
  it("GETs the investigation URL with the playthrough token as Bearer", async () => {
    fetchMock.mockResolvedValue(jsonResponse(BOOTSTRAP_BODY, 200));

    const bootstrap = await getInvestigation("PT-demo-0001", TEST_TOKEN);

    expect(bootstrap.playthroughId).toBe("PT-demo-0001");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`${API_BASE_URL}/api/v1/playthroughs/PT-demo-0001/investigation`);
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Bearer ${TEST_TOKEN}`);
    expect((init as RequestInit).method ?? "GET").toBe("GET");
  });

  it("percent-encodes the playthrough id in the path", async () => {
    fetchMock.mockResolvedValue(jsonResponse(BOOTSTRAP_BODY, 200));
    await getInvestigation("PT/a b", TEST_TOKEN);
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("PT%2Fa%20b");
  });

  it("maps a 401 bearer failure to a structured ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: { code: "UNAUTHORIZED", message: "bearer token invalid", details: null } }, 401),
    );
    const error = await getInvestigation("PT-demo-0001", TEST_TOKEN).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error("expected ApiError");
    expect(error.status).toBe(401);
    expect(error.code).toBe("UNAUTHORIZED");
  });
});

describe("interactObject", () => {
  it("POSTs {interaction} as JSON with the bearer token", async () => {
    fetchMock.mockResolvedValue(jsonResponse(INTERACT_BODY, 200));

    const result = await interactObject("PT-demo-0001", "kitchen_knife", "inspect", TEST_TOKEN);

    expect(result.result).toBe("interacted");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(
      `${API_BASE_URL}/api/v1/playthroughs/PT-demo-0001/objects/kitchen_knife/interact`,
    );
    const requestInit = init as RequestInit;
    expect(requestInit.method).toBe("POST");
    expect(requestInit.body).toBe(JSON.stringify({ interaction: "inspect" }));
    const headers = requestInit.headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Bearer ${TEST_TOKEN}`);
    expect(headers["Content-Type"]).toBe("application/json");
  });

  it("maps a 409 INTERACTION_NOT_ALLOWED envelope to a structured ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: "INTERACTION_NOT_ALLOWED", message: "not allowed in this state", details: null } },
        409,
      ),
    );
    const error = await interactObject("PT-demo-0001", "kitchen_knife", "inspect", TEST_TOKEN).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error("expected ApiError");
    expect(error.status).toBe(409);
    expect(error.code).toBe("INTERACTION_NOT_ALLOWED");
  });
});

describe("discoverEvidence", () => {
  it("POSTs to the evidence discover endpoint", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { evidenceId: "body_found_01", kind: "witness_observation", title: "Body found", interaction: "view", state: "discovered" },
        200,
      ),
    );

    const result = await discoverEvidence("PT-demo-0001", "body_found_01", TEST_TOKEN);

    expect(result.evidenceId).toBe("body_found_01");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(
      `${API_BASE_URL}/api/v1/playthroughs/PT-demo-0001/evidence/body_found_01/discover`,
    );
    const requestInit = init as RequestInit;
    expect(requestInit.method).toBe("POST");
    const headers = requestInit.headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Bearer ${TEST_TOKEN}`);
  });
});

describe("readRecord", () => {
  it("GETs the record URL with the bearer token", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          evidenceId: "email_thomas_01",
          kind: "email",
          title: "Weekend plans",
          description: null,
          openedAt: "2026-09-11T22:20:00+02:00",
          readByPlayer: true,
          content: { subject: "Weekend plans", body: "hello", fromPersonId: "thomas_reed", toPersonIds: [], timestamp: "t" },
        },
        200,
      ),
    );

    const result = await readRecord("PT-demo-0001", "email_thomas_01", TEST_TOKEN);

    expect(result.kind).toBe("email");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(
      `${API_BASE_URL}/api/v1/playthroughs/PT-demo-0001/records/email_thomas_01`,
    );
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Bearer ${TEST_TOKEN}`);
  });
});