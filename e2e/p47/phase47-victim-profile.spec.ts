import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import { scanJsonBody } from "../helpers";

/**
 * Phase 47 QA — authoritative browser acceptance for the victim profile.
 *
 * Real single-origin product (FastAPI + built SPA) on 127.0.0.1:8000 with the
 * fake provider. No request mocking of the happy path: every assertion drives
 * the SHA-complete production bundle against the real API.
 *
 * Covers §28 criteria: victim profile renders (all three demos), no hidden
 * truth in the bootstrap/read network bodies, progressive disclosure, legacy /
 * missing optional fields, and accessibility (labelled dialog, focused close,
 * keyboard Escape).
 */

interface Handshake {
  prompt: string;
  expected: {
    name: string;
    age: number;
    gender: string;
    occupation: string;
    crimeTime: string;
  };
}

const DEMOS: Record<string, Handshake> = {
  "demo-apartment": {
    prompt:
      "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: \u20ac240,000 embezzlement\nWeapon: Kitchen knife\nTime: 22:17\nWitness: Emily Reed\n",
    expected: {
      name: "Sarah Miller",
      age: 39,
      gender: "Female",
      occupation: "CEO, Miller Consulting",
      crimeTime: "2026-09-11T22:17:00+02:00",
    },
  },
  "demo-gallery": {
    prompt:
      "Victim: Maria Lindqvist\nMurderer: Daniel Voss\nMotive: forged sale\nTime: 21:47\nWitness: Jana Petersen\n",
    expected: {
      name: "Maria Lindqvist",
      age: 47,
      gender: "Female",
      occupation: "Gallery Director",
      crimeTime: "2026-09-18T21:47:00+02:00",
    },
  },
  "demo-laboratory": {
    prompt:
      "Victim: Amara Okafor\nMurderer: Elias Meyer\nMotive: safety whistleblower\nTime: 19:43\nWitness: Hugo Brandt\n",
    expected: {
      name: "Dr. Amara Okafor",
      age: 44,
      gender: "Female",
      occupation: "Research Chemist",
      crimeTime: "2026-09-25T19:43:00+02:00",
    },
  },
};

interface VictimBlock {
  personId: string;
  name: string;
  age: number | null;
  gender: string | null;
  occupation: string | null;
  sceneObjectId: string | null;
  discovery: {
    evidenceId: string;
    locationId: string;
    locationName: string;
    observedAt: string;
    title: string;
  } | null;
}

async function createDemo(
  request: APIRequestContext,
  demoId: string,
): Promise<{ playthroughId: string; playthroughToken: string; victim: VictimBlock; boot: Record<string, unknown> }> {
  const sessionRes = await request.post("/api/v1/sessions/anonymous");
  expect(sessionRes.status(), "anonymous session").toBe(201);
  const session = await sessionRes.json();
  const caseRes = await request.post("/api/v1/cases", {
    headers: { Authorization: `Bearer ${session.anonymousSessionToken}` },
    data: { prompt: DEMOS[demoId].prompt, difficulty: "medium", demoCaseId: demoId },
  });
  expect(caseRes.status(), `create ${demoId}`).toBe(201);
  const created = await caseRes.json();
  expect(created.status, `${demoId} publishes`).toBe("PUBLISHED");
  const ptRes = await request.post(
    `/api/v1/cases/${created.caseId}/versions/1/playthroughs`,
    { headers: { Authorization: `Bearer ${created.creatorAccessToken}` } },
  );
  expect(ptRes.status(), "create playthrough").toBe(201);
  const pt = await ptRes.json();
  const invRes = await request.get(
    `/api/v1/playthroughs/${pt.playthroughId}/investigation`,
    { headers: { Authorization: `Bearer ${pt.playthroughAccessToken}` } },
  );
  expect(invRes.status(), "bootstrap").toBe(200);
  const boot = await invRes.json();
  expect(boot.victim, `${demoId} bootstrap carries a victim block`).toBeTruthy();
  return {
    playthroughId: pt.playthroughId,
    playthroughToken: pt.playthroughAccessToken,
    victim: boot.victim as VictimBlock,
    boot,
  };
}

async function seed(page: Page, playthroughId: string, token: string): Promise<void> {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([pid, tok]) => {
      localStorage.setItem("pd_playthrough_id", pid);
      localStorage.setItem("pd_playthrough_token", tok);
    },
    [playthroughId, token] as const,
  );
}

async function openScene(page: Page): Promise<void> {
  await page.goto("/scene", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
}

test("§3 shared leak scanner allows ONLY the public victim discovery time", async ({ request }) => {
  const h = await createDemo(request, "demo-apartment");
  // The whole bootstrap is otherwise leak-clean; the single permitted
  // `observedAt` is the PUBLIC body-discovery time (Phase47.md §2A).
  expect(scanJsonBody(h.boot)).toEqual([]);
  // The carve-out is path-scoped: any other `observedAt` is still flagged.
  expect(scanJsonBody({ truth: { observedAt: "2026-09-11T22:17:00+02:00" } })).toContain(
    "$.truth.observedAt",
  );
  expect(scanJsonBody({ propositions: [{ observedAt: "x" }] })).toContain(
    "$.propositions[0].observedAt",
  );
});

for (const demoId of Object.keys(DEMOS)) {
  test(`§28 victim profile renders — ${demoId}`, async ({ page, request }) => {
    const bodyTexts: string[] = [];
    page.on("response", async (res) => {
      if (!res.url().includes("/api/")) return;
      try {
        bodyTexts.push(await res.text());
      } catch {
        /* non-textual */
      }
    });

    const h = await createDemo(request, demoId);
    const v = h.victim;
    const expected = DEMOS[demoId].expected;

    // Backend identity is authoritative and matches the committed fixture.
    expect(v.name).toBe(expected.name);
    expect(v.age).toBe(expected.age);
    expect(v.gender).toBe("female");
    expect(v.occupation).toBe(expected.occupation);
    expect(v.sceneObjectId).toBeTruthy();
    expect(v.discovery?.locationName).toBeTruthy();
    expect(v.discovery?.observedAt).toBeTruthy();

    await seed(page, h.playthroughId, h.playthroughToken);
    await openScene(page);

    const victimButton = page.getByTestId(`object-${v.sceneObjectId}`);
    await expect(victimButton, "victim body object button").toBeVisible({ timeout: 20_000 });
    await victimButton.click();

    const panel = page.getByTestId("victim-panel");
    await expect(panel).toBeVisible({ timeout: 10_000 });

    // Identity (§28: name/age/gender/occupation shown).
    await expect(page.getByTestId("victim-identity-name")).toContainText(expected.name);
    await expect(page.getByTestId("victim-identity-age")).toContainText(String(expected.age));
    await expect(page.getByTestId("victim-identity-gender")).toContainText(expected.gender);
    await expect(page.getByTestId("victim-identity-occupation")).toContainText(expected.occupation);

    // Discovery (§28: discovery location + time shown).
    await expect(page.getByTestId("victim-discovery-location")).toContainText(v.discovery!.locationName);
    await expect(page.getByTestId("victim-discovery-time")).toBeVisible();

    // The generic evidence panel is suppressed for the victim body.
    await expect(page.getByTestId("evidence-panel")).toHaveCount(0);

    // No broken values anywhere in the panel.
    const text = (await panel.innerText()) || "";
    expect(text).not.toContain("undefined");
    expect(text).not.toContain("NaN");
    expect(text).not.toContain("null");

    // No hidden truth value appears in ANY API response body.
    for (const body of bodyTexts) {
      expect(body).not.toContain(expected.crimeTime);
      expect(body).not.toContain("solverProof");
    }

    // Accessibility (§23): labelled dialog + rule-based section headings.
    await expect(panel).toHaveAttribute("role", "dialog");
    await expect(panel).toHaveAttribute("aria-label", "Victim profile");
    await expect(page.getByTestId("victim-close")).toHaveAttribute("aria-label", "Close victim record");
    await expect(page.getByTestId("victim-close")).toBeFocused();

    // Durable evidence (curated product screenshot per demo).
    await page.screenshot({
      path: `../screenshots/evidence/phase47-qa/victim-${demoId}.png`,
      fullPage: false,
    });
  });
}

test("§28 progressive disclosure — forensic facts appear only after the record is read", async ({ page, request }) => {
  const h = await createDemo(request, "demo-apartment");

  // Intercept ALL read-record responses and hold the FIRST body read so the
  // panel can be observed in its pre-read state.
  let releaseRead: (() => void) | null = null;
  let markEntered: () => void = () => {};
  const entered = new Promise<void>((resolve) => {
    markEntered = resolve;
  });
  await page.route("**/records/**", async (route) => {
    markEntered();
    await new Promise<void>((resolve) => {
      releaseRead = resolve;
    });
    await route.continue();
  });

  await seed(page, h.playthroughId, h.playthroughToken);
  await openScene(page);
  const victimButton = page.getByTestId(`object-${h.victim.sceneObjectId}`);
  await expect(victimButton).toBeVisible({ timeout: 20_000 });
  await victimButton.click();

  const panel = page.getByTestId("victim-panel");
  await expect(panel).toBeVisible({ timeout: 10_000 });
  await entered; // the body read is now held
  // Pre-read: neutral placeholders for known facts / forensics / timeline.
  await expect(page.getByTestId("victim-forensic-empty")).toBeVisible();
  await expect(page.getByTestId("victim-known-facts-empty")).toBeVisible();
  await expect(page.getByTestId("victim-timeline-empty")).toBeVisible();
  // Phase 47 §3/§14: the identity + discovery are still visible pre-read.
  await expect(page.getByTestId("victim-identity-name")).toContainText("Sarah Miller");
  await expect(page.getByTestId("victim-discovery-location")).toBeVisible();

  // Release the read; the body-discovery timeline appears progressively (it is
  // derived ONLY from the session's READ-record cache).
  (releaseRead as (() => void) | null)?.();
  await expect(page.getByTestId("victim-timeline")).toBeVisible({ timeout: 15_000 });
  // The demo body record carries no victim person_id, so it is NOT a
  // victim-linked "known fact" and there is no victim forensic record — both
  // stay neutral (progressive disclosure never fabricates facts).
  await expect(page.getByTestId("victim-known-facts-empty")).toBeVisible();
  await expect(page.getByTestId("victim-forensic-empty")).toBeVisible();
});

test("§15/§16 legacy + missing optional fields do not crash or render broken values", async ({ page, request }) => {
  const h = await createDemo(request, "demo-apartment");

  // Simulate a pre-47 server (no victim block) and a legacy victim block that
  // omits the new optional identity fields; both must stay playable and free
  // of undefined/null/NaN.
  let variant: "absent" | "missingOptional" = "absent";
  await page.route("**/investigation", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (variant === "absent") {
      delete body.victim;
    } else {
      // Legacy block: name only — age/gender/occupation/background/discovery absent.
      body.victim = { personId: "sarah_miller", name: "Sarah Miller" };
    }
    await route.fulfill({ response, json: body });
  });

  // --- pre-47 server: no victim block -> the pre-47 inspection surface. ---
  await seed(page, h.playthroughId, h.playthroughToken);
  await openScene(page);
  const victimButton = page.getByTestId(`object-${h.victim.sceneObjectId}`);
  await expect(victimButton).toBeVisible({ timeout: 20_000 });
  await victimButton.click();
  await expect(page.getByTestId("victim-panel")).toHaveCount(0);
  const surface = page.getByTestId("focus-inspection");
  await expect(surface).toBeVisible({ timeout: 10_000 });
  let text = (await surface.innerText()) || "";
  expect(text).not.toContain("undefined");
  expect(text).not.toContain("NaN");
  expect(text).not.toContain("null");

  // --- legacy block: missing optional fields -> rows omitted, no crash. ---
  variant = "missingOptional";
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });
  const victimButton2 = page.getByTestId(`object-${h.victim.sceneObjectId}`);
  await expect(victimButton2).toBeVisible({ timeout: 20_000 });
  await victimButton2.click();
  const panel = page.getByTestId("victim-panel");
  await expect(panel).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("victim-identity-name")).toContainText("Sarah Miller");
  // The absent optional rows are OMITTED entirely.
  await expect(page.getByTestId("victim-identity-age")).toHaveCount(0);
  await expect(page.getByTestId("victim-identity-gender")).toHaveCount(0);
  await expect(page.getByTestId("victim-identity-occupation")).toHaveCount(0);
  await expect(page.getByTestId("victim-discovery-empty")).toBeVisible();
  text = (await panel.innerText()) || "";
  expect(text).not.toContain("undefined");
  expect(text).not.toContain("NaN");
  expect(text).not.toContain("null");
});

test("§16 save -> restore -> inspect preserves the visible victim record (saved-case replay)", async ({ page }) => {
  // `demo-apartment.pdcase.json` is a REAL live-reveal savegame export captured
  // by the independent Phase 47 API probe (post-reveal, identity carried in the
  // public dossier). Load it through the product's own file picker and inspect
  // the victim in the derived replay.
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.setInputFiles('[data-testid="load-case-input"]', "artifacts/p47/demo-apartment.pdcase.json");
  // Headless Chromium occasionally fails the FIRST replay WebGL init and
  // recovers via the product's own "Try again" affordance (this is the
  // pre-existing replay renderer path, not the Phase 47 victim change). Accept
  // the documented retry so the victim-record assertion is deterministic.
  const retry = page.getByRole("button", { name: /try again/i });
  await Promise.race([
    page.getByTestId("scene-ready").waitFor({ state: "visible", timeout: 30_000 }).catch(() => {}),
    retry.waitFor({ state: "visible", timeout: 30_000 }).catch(() => {}),
  ]);
  if (await retry.isVisible().catch(() => false)) {
    await retry.click();
  }
  await expect(page.getByTestId("scene-ready")).toBeVisible({ timeout: 30_000 });

  const victimButton = page.getByTestId("object-victim_body_placeholder");
  await expect(victimButton, "replay victim body object").toBeVisible({ timeout: 20_000 });
  await victimButton.click();

  const panel = page.getByTestId("victim-panel");
  await expect(panel).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("victim-identity-name")).toContainText("Sarah Miller");
  await expect(page.getByTestId("victim-identity-age")).toContainText("39");
  await expect(page.getByTestId("victim-identity-gender")).toContainText("Female");
  await expect(page.getByTestId("victim-identity-occupation")).toContainText("CEO, Miller Consulting");
  await expect(page.getByTestId("victim-discovery-location")).toContainText("Miller Apartment - Kitchen");
  await expect(page.getByTestId("victim-discovery-time")).toBeVisible();
  const text = (await panel.innerText()) || "";
  expect(text).not.toContain("undefined");
  expect(text).not.toContain("NaN");
  expect(text).not.toContain("null");
});

test("§23 keyboard close (Escape) dismisses the victim profile", async ({ page, request }) => {
  const h = await createDemo(request, "demo-apartment");
  await seed(page, h.playthroughId, h.playthroughToken);
  await openScene(page);
  const victimButton = page.getByTestId(`object-${h.victim.sceneObjectId}`);
  await expect(victimButton).toBeVisible({ timeout: 20_000 });
  await victimButton.click();
  await expect(page.getByTestId("victim-panel")).toBeVisible({ timeout: 10_000 });
  // Let the body discovery/read settle first (the basic close contract).
  await expect(page.getByTestId("victim-timeline")).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("victim-panel")).toHaveCount(0);
});

test("§23 keyboard close must be DURABLE while the body discovery is still pending (DEF-114 regression)", async ({ page, request }) => {
  const h = await createDemo(request, "demo-apartment");
  await seed(page, h.playthroughId, h.playthroughToken);
  await openScene(page);

  // Model a slow server: the body interaction resolves ~1.5s late.
  await page.route("**/objects/**/interact", async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });

  const victimButton = page.getByTestId(`object-${h.victim.sceneObjectId}`);
  await expect(victimButton).toBeVisible({ timeout: 20_000 });
  await victimButton.click();
  const panel = page.getByTestId("victim-panel");
  await expect(panel).toBeVisible({ timeout: 10_000 });
  // The user dismisses the record before the pending discovery resolves.
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  // It must STAY dismissed once the late interaction resolves (currently it
  // re-opens — see DEF-114).
  await page.waitForTimeout(2500);
  await expect(panel, "victim profile must not re-open after a late discovery").toHaveCount(0);
});
