import { expect, test } from "@playwright/test";

/**
 * Backend-DOWN scenario — QA runs this AFTER the main boot smoke, with the
 * backend :8000 STOPPED (vite preview :4173 still serving). The shell must
 * survive: health fetch fails, status flips to "unavailable", no crash,
 * home page still renders.
 */
test("shell stays usable when backend is down (unavailable state, no crash)", async ({
  page,
}) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveTitle("Procedural Detective");

  const statusIndicator = page.getByTestId("backend-status");
  await expect(statusIndicator).toHaveClass(/backend-status--unavailable/, {
    timeout: 30_000,
  });

  // The shell itself must still be fully navigable.
  // Phase 8 D: the landing page replaces the old "Welcome" home copy.
  await expect(page.getByRole("heading", { name: "Procedural Detective" })).toBeVisible();
  await expect(page.getByTestId("home-backend-status")).toHaveText(/unavailable/);

  await page.screenshot({ path: "artifacts/screenshots/backend-down.png", fullPage: true });
});