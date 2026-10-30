import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { GenerationCapabilitiesResponse } from "../api/types";
import Home from "./home";

const OK_STATUS = { state: "ok" as const, message: "ok", readiness: null };

function renderHome(capabilities?: GenerationCapabilitiesResponse | null): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/"]}>
      <Home status={OK_STATUS} capabilities={capabilities} />
    </MemoryRouter>,
  );
}

describe("landing page", () => {
  const html = renderHome();

  it("shows the product headline and primary tagline", () => {
    expect(html).toContain("Procedural Detective");
    expect(html).toContain("Describe a crime. AI builds a logically solvable 3D investigation.");
  });

  it("keeps the three main actions unchanged", () => {
    const demoHtml = renderHome({ modes: [{ id: "demo", available: true }] });
    expect(demoHtml).toMatch(/data-testid="new-investigation"[^>]*href="\/new"[^>]*>New Investigation</);
    expect(demoHtml).toMatch(/data-testid="try-demo"[^>]*>\s*Try Demo Case\s*</);
    expect(demoHtml).toMatch(/data-testid="load-case"[^>]*>\s*Load Case\s*</);
    expect(demoHtml).not.toContain('data-testid="try-demo" href=');
    expect(demoHtml).not.toContain('data-testid="load-case" href=');
  });

  it("removes the complete demo-only block for every capability state", () => {
    const removedContent = [
      "Demo build: deterministic built-in generator",
      "Deterministic demo — no API keys, no cost.",
      "Generate a New Mystery",
      "uses the built-in deterministic generator in this demo build",
      "Demo mode active",
      "Generation mode: Deterministic demo",
    ];
    for (const rendered of [
      renderHome({ modes: [{ id: "demo", available: true }] }),
      renderHome({
        modes: [{ id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" }],
      }),
      renderHome(null),
    ]) {
      for (const content of removedContent) expect(rendered).not.toContain(content);
      expect(rendered).not.toContain('data-testid="provider-qualifier"');
      expect(rendered).not.toContain('data-testid="try-demo-note"');
      expect(rendered).not.toContain('data-testid="generate-new-mystery"');
      expect(rendered).not.toContain('data-testid="generate-provider-note"');
      expect(rendered).not.toContain('data-testid="generation-mode-selector"');
      expect(rendered).not.toContain('data-testid="generation-mode-demo-notice"');
    }
  });

  it("places How it works directly after the top actions at rest", () => {
    expect(html).toMatch(/<\/div><div class="landing-panel"><h3>How it works<\/h3>/);
  });

  it("keeps the How it works and Controls content", () => {
    expect(html).toContain("Describe a crime");
    expect(html).toContain("Deterministic validation");
    expect(html).toContain("Explore in 3D");
    expect(html).toContain("Controls");
    expect(html).toContain("Esc closes panels");
  });

  it("keeps the GitHub link and backend-status indicator", () => {
    expect(html).toContain('data-testid="github-link"');
    expect(html).toContain('href="https://github.com/DaveKingBerlin/procedural-detective"');
    expect(html).toContain('data-testid="home-backend-status"');
    expect(html).toContain('data-testid="home-backend-message"');
    expect(html).toContain("ok");
  });
});
describe("landing page — Load Case", () => {
  it("keeps the .pdcase-capable native file picker", () => {
    const html = renderHome();
    expect(html).toContain('data-testid="load-case"');
    expect(html).toMatch(
      /type="file"[^>]*accept="\.pdcase,application\/vnd\.procedural-detective\.case\+json,application\/json"/,
    );
  });

  it("renders no load error and no busy state at rest", () => {
    const html = renderHome();
    expect(html).not.toContain('data-testid="load-case-error"');
    expect(html).not.toContain('data-testid="load-case-busy"');
  });
});
