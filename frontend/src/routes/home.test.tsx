import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { providerQualifierFromCapabilities } from "../journey/providerMode";
import Home from "./home";

/**
 * Landing page rendering (Phase 8 D): headline + REQUIREMENTS 62 tagline,
 * the two primary actions, the 3-bullet explanation, controls/help summary,
 * GitHub link and the backend-status indicator. Rendered statically
 * (react-dom/server + MemoryRouter) — no DOM, no network.
 *
 * Phase 18A — the provider qualifier and per-path note are CAPABILITY-DRIVEN:
 * they reflect the backend's generation-capabilities DTO (or the honest
 * deterministic default while unknown), so a build-time env value can never
 * contradict the backend's provider report.
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

  it("labels the demo path as deterministic / zero-cost / no API keys (Phase 15)", () => {
    expect(html).toContain('data-testid="try-demo-note"');
    expect(html).toContain("Deterministic demo — no API keys, no cost.");
  });

  it("renders the honest provider qualifier near the primary CTA (ADV-152)", () => {
    // The qualifier is app-level and CAPABILITY-DRIVEN (Phase 18A): with no
    // capabilities injected the route shows the honest deterministic-demo
    // default (the same copy the providerMode module produces for null caps).
    // The REQUIREMENTS §62 tagline stays verbatim above it.
    expect(html).toContain("Describe a crime. AI builds a logically solvable 3D investigation.");
    expect(html).toContain('data-testid="provider-qualifier"');
    expect(html).toContain(providerQualifierFromCapabilities(null));
  });

  it("renders the Generate a New Mystery path to /new with an honest provider note (Phase 15)", () => {
    expect(html).toMatch(/data-testid="generate-new-mystery"[^>]*href="\/new"/);
    expect(html).toContain("Generate a New Mystery");
    // Default (unknown capabilities): the note must show the honest
    // deterministic-demo default and must NOT claim live-AI behavior.
    expect(html).toContain("uses the built-in deterministic generator in this demo build");
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

  it("links to the real GitHub repository (Phase 18A — no placeholder URL)", () => {
    expect(html).toContain('data-testid="github-link"');
    expect(html).toContain('href="https://github.com/DaveKingBerlin/procedural-detective"');
    expect(html).not.toContain('href="https://github.com/"');
  });

  it("keeps the backend-status indicator and readiness test ids", () => {
    expect(html).toContain('data-testid="home-backend-status"');
    expect(html).toContain('data-testid="home-backend-message"');
    expect(html).toContain("ok");
  });
});

describe("landing page — Phase 16 Track B generation-mode selector", () => {
  const renderWithCaps = (capabilities: GenerationCapabilitiesResponse | null): string =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/"]}>
        <Home status={OK_STATUS} capabilities={capabilities} />
      </MemoryRouter>,
    );

  it("renders the selector with Local AI (label + model + Ready) and Cloud AI when available", () => {
    const html = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        { id: "live", available: true, label: "Cloud AI" },
      ],
    });
    expect(html).toContain('data-testid="generation-mode-selector"');
    expect(html).toContain("Local AI — qwen2.5:7b — Ready");
    expect(html).toContain("Cloud AI");
    expect(html).not.toContain('data-testid="generation-mode-demo-notice"');
  });

  it("shows the static demo notice instead of a selector when only demo is available", () => {
    const html = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: false, label: "Local AI" },
      ],
    });
    expect(html).not.toContain('data-testid="generation-mode-selector"');
    expect(html).toContain('data-testid="generation-mode-demo-notice"');
    expect(html).toContain("Demo mode active");
  });

  it("never renders an unavailable local mode as an option", () => {
    const html = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
    expect(html).not.toContain("Local AI — qwen2.5:7b");
  });
});

describe("landing page — Phase 18A capability-driven provider notes", () => {
  const renderWithCaps = (capabilities: GenerationCapabilitiesResponse | null): string =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/"]}>
        <Home status={OK_STATUS} capabilities={capabilities} />
      </MemoryRouter>,
    );

  const DEMO_ONLY: GenerationCapabilitiesResponse = {
    modes: [
      { id: "demo", available: true },
      { id: "local", available: false, label: "Local AI" },
    ],
  };
  const LOCAL_READY: GenerationCapabilitiesResponse = {
    modes: [
      { id: "demo", available: true },
      { id: "local", available: true, label: "Local AI", model: "llama3.2:3b" },
    ],
  };
  const LIVE_READY: GenerationCapabilitiesResponse = {
    modes: [
      { id: "demo", available: true },
      { id: "live", available: true, label: "Cloud AI" },
    ],
  };

  it("demo-only backend -> deterministic note and qualifier, never local/live claims", () => {
    const html = renderWithCaps(DEMO_ONLY);
    expect(html).toContain("uses the built-in deterministic generator in this demo build");
    expect(html).toContain("Demo build: deterministic built-in generator");
    expect(html).not.toContain("Live AI provider");
    expect(html).not.toContain("Local AI");
  });

  it("local-ready backend -> the note and qualifier reflect local availability", () => {
    const html = renderWithCaps(LOCAL_READY);
    expect(html).toContain("uses the local AI pipeline");
    expect(html).toContain("Local AI is available");
    // deterministic-demo wording must be GONE when the backend actually runs
    // the local pipeline (the old build-time note could wrongly stay on demo).
    expect(html).not.toContain("uses the built-in deterministic generator in this demo build");
  });

  it("live-ready backend -> the note and qualifier reflect the live provider", () => {
    const html = renderWithCaps(LIVE_READY);
    expect(html).toContain("Live AI provider");
    expect(html).toContain("Live AI provider enabled.");
    expect(html).not.toContain("uses the built-in deterministic generator in this demo build");
  });

  it("unknown backend (capabilities still loading) -> honest deterministic default", () => {
    const html = renderWithCaps(null);
    expect(html).toContain("uses the built-in deterministic generator in this demo build");
    // Never a live/local claim before the backend has reported.
    expect(html).not.toContain("Live AI provider");
    expect(html).not.toContain("Local AI is available");
  });

  it("the deterministic demo path stays separate and is NEVER labelled live AI", () => {
    // Even when the backend reports Live AI available, the Try Demo Case
    // action keeps its deterministic zero-cost label: it always runs the
    // built-in deterministic generator through the demo prompt/difficulty.
    for (const capabilities of [DEMO_ONLY, LOCAL_READY, LIVE_READY, null]) {
      const html = renderWithCaps(capabilities);
      const demoMarkup = html.match(/data-testid="try-demo-note"[\s\S]*?<\/p>/)?.[0] ?? "";
      expect(demoMarkup).toContain("Deterministic demo — no API keys, no cost.");
      expect(demoMarkup).not.toContain("Live AI provider");
      expect(demoMarkup).not.toContain("Local AI");
      expect(html).toMatch(/data-testid="try-demo"[^>]*>\s*Try Demo Case\s*</);
    }
  });
});