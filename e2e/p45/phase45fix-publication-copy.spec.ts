import { expect, test } from "@playwright/test";

/**
 * Phase 45 Fix — USER-FACING COPY (production topology, QA-owned).
 *
 * Independently proves that a REAL `POST /api/v1/cases` response with
 * status=FAILED + failureCode=PUBLICATION_FAILED (the post-generation
 * playability/Gate 3 failure) renders the NON-prompt-blaming copy, never the
 * generic prompt-blaming copy, and never a raw internal code — through the REAL
 * SPA served by the Caddy HTTPS edge. The response is fulfilled by Playwright
 * route interception so this is deterministic and hermetic.
 */

const PROMPT =
  "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\nWeapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n";

test("Phase45 Fix — PUBLICATION_FAILED renders the non-prompt-blaming copy", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.route("**/api/v1/cases", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        caseId: "CASE-qa-copy",
        generationId: "GEN-qa-copy",
        generationAttemptId: "ATT-qa-copy",
        creatorAccessToken: "qa-creator-token",
        status: "FAILED",
        failureCode: "PUBLICATION_FAILED",
      }),
    });
  });

  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("prompt-form")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("prompt-input").fill(PROMPT);
  await page.getByTestId("generate-case").click();

  const failed = page.getByTestId("generation-failed").first();
  await expect(failed).toBeVisible({ timeout: 30_000 });
  const text = (await failed.innerText()).trim();
  console.log("RENDERED ERROR TEXT:", JSON.stringify(text));

  expect(text).toContain(
    "The generated case did not meet the playability requirements. Please try again.",
  );
  expect(text).not.toContain("Adjust the prompt");
  expect(text).not.toContain("solvable case");
  expect(text).not.toContain("PUBLICATION_FAILED");
  expect(text).not.toContain("PLAYABILITY");
  expect(pageErrors).toEqual([]);
});

test("Phase45 Fix — a control failure code still shows the prompt-blaming generic copy", async ({
  page,
}) => {
  await page.route("**/api/v1/cases", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        caseId: "CASE-qa-control",
        generationId: "GEN-qa-control",
        generationAttemptId: "ATT-qa-control",
        creatorAccessToken: "qa-creator-token",
        status: "FAILED",
        failureCode: "VALIDATION_FAILED",
      }),
    });
  });

  await page.goto("/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("prompt-form")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("prompt-input").fill(PROMPT);
  await page.getByTestId("generate-case").click();

  const failed = page.getByTestId("generation-failed").first();
  await expect(failed).toBeVisible({ timeout: 30_000 });
  const text = (await failed.innerText()).trim();
  console.log("CONTROL ERROR TEXT:", JSON.stringify(text));
  expect(text).toContain(
    "This prompt could not be turned into a solvable case. Adjust the prompt, then try again.",
  );
});