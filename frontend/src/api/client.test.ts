import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE_URL, ApiError, getHealth, getReadiness } from "./client";

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