import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { demoCtaLabel, demoCtaNote } from "../journey/generationMode";
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

  it("renders the example-case action with no external navigation (neutral label while capabilities are unknown)", () => {
    // Phase 21B Finding 3: the default static render has NO capability DTO
    // (null — the probe is pending / unreachable), so the CTA must use the
    // neutral truthful label, NOT the historical always-on "Try Demo Case"
    // + deterministic/no-cost promise (the frontend cannot know the provider
    // when the DTO is unavailable).
    expect(html).toContain('data-testid="try-demo"');
    expect(html).not.toContain('data-testid="try-demo" href=');
    expect(html).toContain("Try an example case");
    expect(html).not.toContain("Deterministic demo — no API keys, no cost.");
  });

  it("labels the demo path as deterministic / zero-cost / no API keys ONLY for a known demo-only backend (Phase 15 + Phase 21B)", () => {
    // Phase 21B Finding 3: the deterministic/no-cost promise is truthful ONLY
    // when the backend reports a demo-only allowlist (GENERATION_PROVIDER=fake);
    // it must not be shown for the unknown-capabilities default render.
    const demoHtml = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/"]}>
        <Home
          status={OK_STATUS}
          capabilities={{ modes: [{ id: "demo", available: true }] }}
        />
      </MemoryRouter>,
    );
    expect(demoHtml).toContain('data-testid="try-demo-note"');
    expect(demoHtml).toContain("Deterministic demo — no API keys, no cost.");
    // The unknown default does not carry the promise.
    expect(html).not.toContain("Deterministic demo — no API keys, no cost.");
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

describe("landing page — Phase 21 F-03 read-only generation-mode display", () => {
  const renderWithCaps = (capabilities: GenerationCapabilitiesResponse | null): string =>
    renderToStaticMarkup(
      <MemoryRouter initialEntries={["/"]}>
        <Home status={OK_STATUS} capabilities={capabilities} />
      </MemoryRouter>,
    );

  it("renders the read-only line with Local AI (label + model + Ready) when the backend reports it available", () => {
    // Phase 21 F-03: the interactive selector is GONE — the container testid
    // stays but holds a single authoritative read-only line, never a <select>.
    const html = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
    expect(html).toContain('data-testid="generation-mode-selector"');
    expect(html).toContain("Generation mode: Local AI — qwen2.5:7b — Ready");
    expect(html).not.toContain('data-testid="generation-mode-demo-notice"');
    // No select/options — nothing a click could change.
    expect(html).not.toContain("<select");
    expect(html).not.toContain("<option");
  });

  it("resolves a both-available backend to ONE truthful mode (live wins, matching the provider qualifier)", () => {
    const html = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        { id: "live", available: true, label: "Cloud AI" },
      ],
    });
    // The backend runs one process-global provider: the display (same
    // resolver as the qualifier) shows exactly one authoritative line.
    expect(html).toContain("Generation mode: Cloud AI");
    expect(html).not.toContain("Generation mode: Local AI");
  });

  it("shows the static demo notice + deterministic line instead of a selector when only demo is available", () => {
    const html = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: false, label: "Local AI" },
      ],
    });
    expect(html).not.toContain('data-testid="generation-mode-selector"');
    expect(html).toContain('data-testid="generation-mode-demo-notice"');
    expect(html).toContain("Demo mode active");
    expect(html).toContain("Generation mode: Deterministic demo");
  });

  it("never renders an unavailable local mode (no 'Local AI' claim, no option)", () => {
    const html = renderWithCaps({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
    expect(html).not.toContain("Local AI — qwen2.5:7b");
    expect(html).not.toContain("<option");
  });

  it("the page contains NO interactive provider selection control (no testid implying a switch)", () => {
    for (const capabilities of [
      {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        ],
      },
      { modes: [{ id: "demo", available: true }] },
      null,
    ]) {
      const html = renderWithCaps(capabilities);
      expect(html).not.toContain('data-testid="generation-mode-select"');
      expect(html).not.toContain('value="local"');
      expect(html).not.toContain('value="live"');
    }
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

  it("the example-case CTA is TRUTHFUL per the backend capability report (Phase 21B Finding 3)", () => {
    // Phase 21B Finding 3: the historical "Try Demo Case" + deterministic
    // no-cost promise was always-on even when the backend ran a local/live
    // provider. Now the CTA label + note must match what the backend will
    // ACTUALLY do (the demo-only backend -> "Try Demo Case" + the
    // deterministic promise; local/live -> renamed neutral CTA + per-mode
    // note; unknown -> neutral label, no promise).
    for (const capabilities of [DEMO_ONLY, LOCAL_READY, LIVE_READY, null]) {
      const htmlText = renderWithCaps(capabilities);
      const demoMarkup = htmlText.match(/data-testid="try-demo-note"[\s\S]*?<\/p>/)?.[0] ?? "";
      const demoButton = htmlText.match(/data-testid="try-demo"[^>]*>[\s\S]*?<\/button>/)?.[0] ?? "";
      expect(demoButton).toContain(demoCtaLabel(capabilities));
      expect(demoMarkup).toContain(demoCtaNote(capabilities));
      // No claim the deterministic demo runs the live AI provider.
      expect(demoMarkup).not.toContain("Live AI provider");
    }
  });

  it("demo-only backend keeps the deterministic/no-cost promise (GENERATION_PROVIDER=fake)", () => {
    const htmlText = renderWithCaps(DEMO_ONLY);
    expect(htmlText).toMatch(/data-testid="try-demo"[^>]*>\s*Try Demo Case\s*</);
    const demoMarkup = htmlText.match(/data-testid="try-demo-note"[\s\S]*?<\/p>/)?.[0] ?? "";
    expect(demoMarkup).toContain("Deterministic demo — no API keys, no cost.");
  });

  it("local/live backend renames the CTA and NEVER shows the deterministic/no-cost promise", () => {
    for (const capabilities of [LOCAL_READY, LIVE_READY]) {
      const htmlText = renderWithCaps(capabilities);
      expect(htmlText).toMatch(/data-testid="try-demo"[^>]*>\s*Try an example case\s*</);
      const demoMarkup = htmlText.match(/data-testid="try-demo-note"[\s\S]*?<\/p>/)?.[0] ?? "";
      expect(demoMarkup).not.toContain("Deterministic demo — no API keys, no cost.");
      expect(demoMarkup).toContain("Not the free deterministic demo");
    }
  });

  it("unknown/unreachable capabilities -> neutral CTA, no deterministic/no-cost promise", () => {
    const htmlText = renderWithCaps(null);
    expect(htmlText).toMatch(/data-testid="try-demo"[^>]*>\s*Try an example case\s*</);
    const demoMarkup = htmlText.match(/data-testid="try-demo-note"[\s\S]*?<\/p>/)?.[0] ?? "";
    expect(demoMarkup).not.toContain("Deterministic demo — no API keys, no cost.");
    expect(demoMarkup).not.toContain("no API keys, no cost");
    expect(demoMarkup).not.toContain("Live AI provider");
  });
});