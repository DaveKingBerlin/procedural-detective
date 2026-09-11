import { useEffect, useState } from "react";
import { ApiError, getHealth, getReadiness } from "../api/client";
import type { HealthResponse, ReadinessResponse } from "../api/types";

export type BackendState = "checking" | "ok" | "degraded" | "unavailable";

export interface BackendStatus {
  state: BackendState;
  message: string;
  readiness: ReadinessResponse | null;
}

const IDLE: BackendStatus = {
  state: "checking",
  message: "checking…",
  readiness: null,
};

const NOT_READY_FALLBACK = "backend not ready";

/**
 * Short human reason for a not-ready backend, derived from the readiness
 * ApiError's details (e.g. {"database":"error","migrations":"ok"}) or message,
 * falling back to a generic "backend not ready" when neither is available.
 */
function shortNotReadyReason(error: ApiError): string | null {
  const parts: string[] = [];
  if (error.details && typeof error.details === "object") {
    const d = error.details as Record<string, unknown>;
    if (typeof d.database === "string" && d.database !== "ok") parts.push(`database ${d.database}`);
    if (typeof d.migrations === "string" && d.migrations !== "ok") parts.push(`migrations ${d.migrations}`);
  }
  if (parts.length > 0) return parts.join(", ");
  if (error.code !== "NOT_READY") return null;
  const message = error.message?.trim();
  return message && message !== "" && message !== `Request failed with status ${error.status}`
    ? message
    : null;
}

function notReadyMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const reason = shortNotReadyReason(error);
    return reason ? `${NOT_READY_FALLBACK} - ${reason}` : NOT_READY_FALLBACK;
  }
  return NOT_READY_FALLBACK;
}

/**
 * Shell-level backend probe: fetches health, then readiness when health is ok.
 *
 * The API being reachable but NOT ready (readiness rejected) is surfaced as the
 * distinct "degraded" state — the shell must never render "ok" just because the
 * health endpoint answered. Never throws: every failure resolves to an
 * "unavailable"/"degraded" state so the shell stays usable with the backend down.
 */
export async function checkBackendStatus(
  probeHealth: () => Promise<HealthResponse> = getHealth,
  probeReadiness: () => Promise<ReadinessResponse> = getReadiness,
): Promise<BackendStatus> {
  let health: HealthResponse;
  try {
    health = await probeHealth();
  } catch (error) {
    const message = error instanceof ApiError ? error.code : "unavailable";
    return { state: "unavailable", message, readiness: null };
  }
  if (health.status !== "ok") {
    return { state: "unavailable", message: `health: ${health.status}`, readiness: null };
  }
  try {
    const readiness = await probeReadiness();
    if (readiness.status !== "ready") {
      return { state: "degraded", message: `readiness: ${readiness.status}`, readiness };
    }
    return { state: "ok", message: "ok", readiness };
  } catch (error) {
    return { state: "degraded", message: notReadyMessage(error), readiness: null };
  }
}

/** Hook wrapper around {@link checkBackendStatus}; keeps IDLE until the probe resolves. */
export function useBackendStatus(): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>(IDLE);

  useEffect(() => {
    let cancelled = false;
    void checkBackendStatus().then(
      (next) => {
        if (!cancelled) setStatus(next);
      },
      () => {
        // Never-crash guarantee: an unexpected rejection still maps to a safe state.
        if (!cancelled) setStatus({ state: "unavailable", message: "unavailable", readiness: null });
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return status;
}