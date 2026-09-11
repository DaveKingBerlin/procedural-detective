import { expect, test } from "@playwright/test";

/**
 * DEGRADED scenario (DEF-016) — QA runs this AFTER the main boot smoke, with the
 * backend :8000 UP but pointed at an UNMIGRATED database. The shell must NOT
 * claim "ok": health answers 200 but readiness returns 503 NOT_READY, so the
 * backend-status indicator must flip to the distinct "degraded" state with a
 * "backend not ready ..." message, and the shell must not crash.
 */
test("shell shows degraded (not ok) when health is ok but readiness is 503 NOT_READY", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type === "error") consoleErrors.push(`[${msg.type}] ${msg.text}`);
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveTitle("Procedural Detective");

  // The shell must render the dedicated degraded state — never the green "ok".
  const statusIndicator = page.getByTestId("backend-status");
  await expect(statusIndicator).toHaveClass(/backend-status--degraded/, { timeout: 30_000 });

  const message = page.getByTestId("backend-status-message");
  await expect(message).not.toHaveText("ok");
  await expect(message).toHaveText(/backend not ready/);

  // Home page text agrees and the shell stays fully navigable (no crash).
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();
  await expect(page.getByTestId("home-backend-status")).toHaveText(/degraded/);

  await page.screenshot({ path: "artifacts/screenshots/qa3-degraded.png", fullPage: true });

  // Degraded is a controlled state, not an uncaught page error.
  await test.info().attach("degraded-page-errors.json", {
    body: JSON.stringify(pageErrors, null, 2),
    contentType: "application/json",
  });
  expect(pageErrors, "no uncaught page errors may occur").toEqual([]);
});