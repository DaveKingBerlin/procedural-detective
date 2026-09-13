import { expect } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

/**
 * QA-owned shared helpers for the Playwright suite.
 *
 * - `createPlaythroughViaApi` drives the public backend API exactly like the
 *   HOME "Start an investigation" entry would receive them (session -> case ->
 *   playthrough) so the browser can seed its localStorage credential.
 * - `seedPlaythroughCredentials` stores the credential under the
 *   contract-mandated localStorage keys BEFORE the /scene reload.
 * - `installLeakListener` attaches a page response listener that scans every
 *   API response body for forbidden hidden-truth key paths and returns the
 *   matched bodies (Phase 6 Q network-leak proof).
 */

export const BACKEND_BASE = "http://localhost:8000";

/** Forbidden pre-reveal key paths (REQUIREMENTS 41.4 + Phase 6 Q). */
export const FORBIDDEN_KEY_PATHS = [
  "murdererId",
  "victimId",
  "weaponId",
  "motiveId",
  "truthfulness",
  "crimeTime",
  "canonical",
  "solutionProof",
  "acceptedScoring",
  "solverProof",
  "proof",
  "diagnostics",
  "prompt",
  "providerOutput",
  "verifier",
  "tokenVerifier",
  "token",
  "seed",
  "model",
  "locked",
  "report",
  "universes",
  "universe",
  "observedAt",
  "propositions",
  "sourceRef",
  "remainingCandidateIds",
  "remainingMotiveIds",
  "remainingWeaponIds",
  "truth",
];

function walk(node: unknown, path: string, hits: string[]): void {
  if (node === null || node === undefined) return;
  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i++) walk(node[i], `${path}[${i}]`, hits);
    return;
  }
  if (typeof node === "object") {
    for (const [key, value] of Object.entries(node)) {
      const child = `${path}.${key}`;
      if (FORBIDDEN_KEY_PATHS.includes(key)) hits.push(child);
      walk(value, child, hits);
    }
  }
}

/** Recursive key-path scan over one parsed JSON body; returns matched paths. */
export function scanJsonBody(body: unknown): string[] {
  const hits: string[] = [];
  walk(body, "$", hits);
  return hits;
}

export interface PlaythroughCredential {
  playthroughId: string;
  playthroughToken: string;
}

/** Full public API handshake against the LIVE backend (browser-like, no UI). */
export async function createPlaythroughViaApi(
  request: APIRequestContext,
): Promise<PlaythroughCredential> {
  const sessionRes = await request.post(`${BACKEND_BASE}/api/v1/sessions/anonymous`);
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();

  const caseRes = await request.post(`${BACKEND_BASE}/api/v1/cases`, {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt: "Victim: sarah_miller\nMurderer: thomas_reed\n", difficulty: "medium" },
  });
  expect(caseRes.status(), "create case").toBe(201);
  const created = await caseRes.json();
  expect(created.status, "default dev provider publishes").toBe("PUBLISHED");

  const ptRes = await request.post(
    `${BACKEND_BASE}/api/v1/cases/${created.caseId}/versions/1/playthroughs`,
    { headers: { Authorization: `Bearer ${created.creatorAccessToken}` } },
  );
  expect(ptRes.status(), "create playthrough").toBe(201);
  const pt = await ptRes.json();
  return { playthroughId: pt.playthroughId, playthroughToken: pt.playthroughAccessToken };
}

/** Seed localStorage under the contract-mandated keys BEFORE navigation. */
export async function seedPlaythroughCredentials(
  page: Page,
  cred: PlaythroughCredential,
): Promise<void> {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([pid, token]) => {
      localStorage.setItem("pd_playthrough_id", pid);
      localStorage.setItem("pd_playthrough_token", token);
    },
    [cred.playthroughId, cred.playthroughToken] as const,
  );
}

export interface LeakScanReport {
  scanned: number;
  matched: Array<{ url: string; paths: string[]; body: unknown }>;
}

/**
 * Attach a response listener scanning EVERY API response body for forbidden
 * key paths. Returns the report object the test asserts against.
 */
export function installLeakListener(page: Page): LeakScanReport {
  const report: LeakScanReport = { scanned: 0, matched: [] };
  page.on("response", async (response) => {
    const url = typeof response.url === "function" ? response.url() : response.url;
    if (!url.includes("/api/")) return;
    const headers = typeof response.headers === "function" ? response.headers() : response.headers;
    const contentType = headers["content-type"] ?? headers["Content-Type"] ?? "";
    if (!contentType.includes("application/json")) return;
    report.scanned += 1;
    try {
      const body = await response.json();
      const paths = scanJsonBody(body);
      if (paths.length > 0) {
        report.matched.push({ url, paths, body });
      }
    } catch {
      // non-JSON or streamed body: nothing to scan
    }
  });
  return report;
}