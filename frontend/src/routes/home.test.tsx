import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import Home from "./home";

/**
 * Landing page rendering (Phase 8 D): headline + REQUIREMENTS 62 tagline,
 * the two primary actions, the 3-bullet explanation, controls/help summary,
 * GitHub link placeholder and the backend-status indicator. Rendered
 * statically (react-dom/server + MemoryRouter) — no DOM, no network.
 */

const OK_STATUS = { state: "ok" as const, message: "ok", readiness: null };

function renderHome(): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/"]}>
      <Home status={OK_STATUS} />
    </MemoryRouter>,
  );
}

describe("landing page", () => {
  const html = renderHome();

  it("shows the product headline and the primary tagline", () => {
    expect(html).toContain("Procedural Detective");
    expect(html).toContain("Describe a crime. AI builds a logically solvable 3D investigation.");
  });

  it("renders the New Investigation primary action to /new", () => {
    expect(html).toContain('data-testid="new-investigation"');
    expect(html).toMatch(/data-testid="new-investigation"[^>]*href="\/new"/);
  });

  it("renders the Try Demo Case action with no external navigation", () => {
    expect(html).toContain('data-testid="try-demo"');
    expect(html).toContain("Try Demo Case");
  });

  it("explains the AI-native value proposition in three bullets", () => {
    expect(html).toContain("Describe a crime");
    expect(html).toContain("Deterministic validation");
    expect(html).toContain("Explore in 3D");
  });

  it("summarizes the controls and help", () => {
    expect(html).toContain("Controls");
    expect(html).toContain("Esc closes panels");
  });

  it("links to the GitHub placeholder with the expected data-testid", () => {
    expect(html).toContain('data-testid="github-link"');
    expect(html).toContain('href="https://github.com/"');
  });

  it("keeps the backend-status indicator and readiness test ids", () => {
    expect(html).toContain('data-testid="home-backend-status"');
    expect(html).toContain('data-testid="home-backend-message"');
    expect(html).toContain("ok");
  });
});