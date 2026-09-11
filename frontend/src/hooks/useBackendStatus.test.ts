import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { checkBackendStatus } from "./useBackendStatus";

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

describe("checkBackendStatus", () => {
  it("reports degraded (not ok) when the API is reachable but readiness is NOT_READY", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ status: "ok", service: "procedural-detective", version: "0.1.0" }, 200),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            error: {
              code: "NOT_READY",
              message: "database migration pending",
              details: { database: "error", migrations: "ok" },
            },
          },
          503,
        ),
      );

    const status = await checkBackendStatus();

    expect(status.state).toBe("degraded");
    expect(status.message).not.toBe("ok");
    expect(status.message).toContain("backend not ready");
    expect(status.readiness).toBeNull();
  });

  it("reports ok when health is ok AND readiness is ready", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ status: "ok", service: "procedural-detective", version: "0.1.0" }, 200),
      )
      .mockResolvedValueOnce(
        jsonResponse({ status: "ready", database: "ok", migrations: "ok" }, 200),
      );

    const status = await checkBackendStatus();

    expect(status.state).toBe("ok");
    expect(status.message).toBe("ok");
    expect(status.readiness).toEqual({ status: "ready", database: "ok", migrations: "ok" });
  });

  it("reports unavailable when the health probe hits a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const status = await checkBackendStatus();

    expect(status.state).toBe("unavailable");
    expect(status.message).toBe("NETWORK_ERROR");
    expect(status.readiness).toBeNull();
  });
});