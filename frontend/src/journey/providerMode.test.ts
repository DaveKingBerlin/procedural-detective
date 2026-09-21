import { describe, expect, it } from "vitest";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { parseGenerationCapabilities, selectableGenerationModes } from "./generationMode";
import {
  effectiveProviderMode,
  parseAppProvider,
  providerPathNote,
  providerPathNoteFromCapabilities,
  providerQualifier,
  providerQualifierFromCapabilities,
} from "./providerMode";

/**
 * Phase 15 Track B — provider notes; Phase 18A makes them CAPABILITY-DRIVEN.
 * The render path no longer reads `VITE_APP_PROVIDER` at all: the note derives
 * from the backend's public generation-capabilities DTO (the same allowlist
 * the selector consumes), so a build-time env value can never contradict the
 * backend's provider report. `parseAppProvider` is retained purely as the
 * documented legacy env parse contract (see .env.example) and can never reach
 * the render path.
 */

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

describe("parseAppProvider — env-driven mode selection", () => {
  it("maps the exact 'live' string to live", () => {
    expect(parseAppProvider("live")).toBe("live");
  });

  it("maps undefined (default fake build) to fake", () => {
    expect(parseAppProvider(undefined)).toBe("fake");
  });

  it("maps empty strings and misspellings to fake (never a false live claim)", () => {
    expect(parseAppProvider("")).toBe("fake");
    expect(parseAppProvider("Live")).toBe("fake");
    expect(parseAppProvider("LIVE")).toBe("fake");
    expect(parseAppProvider("production")).toBe("fake");
    expect(parseAppProvider(0)).toBe("fake");
    expect(parseAppProvider(null)).toBe("fake");
  });

  it("is total (never throws on any raw value)", () => {
    for (const raw of [undefined, null, 42, true, {}, [], "live", "fake"]) {
      expect(["fake", "live"]).toContain(parseAppProvider(raw));
    }
  });
});

describe("providerPathNote — honest per-mode notes", () => {
  it("keeps the fake note honest about the deterministic build", () => {
    expect(providerPathNote("fake")).toBe(
      "uses the built-in deterministic generator in this demo build",
    );
  });

  it("names the live provider only in live mode", () => {
    expect(providerPathNote("live")).toBe("Live AI provider");
  });
});

describe("providerQualifier — ADV-152 app-level honesty near the primary CTA", () => {
  it("is honest about the deterministic default build in fake mode", () => {
    expect(providerQualifier("fake")).toBe(
      "Demo build: deterministic built-in generator — no API keys, no cost. "
        + "Live AI is opt-in and not enabled in this build.",
    );
  });

  it("names the live provider only in live mode", () => {
    expect(providerQualifier("live")).toBe("Live AI provider enabled.");
  });

  it("never claims live AI for an unset/misspelled build (default is fake)", () => {
    expect(providerQualifier(parseAppProvider(undefined))).toContain("not enabled in this build");
    expect(providerQualifier(parseAppProvider(""))).toContain("deterministic built-in generator");
    expect(providerQualifier(parseAppProvider("production"))).toContain("no API keys, no cost");
  });

  it("is ADDITIVE to the per-path notes (the qualifier never replaces them)", () => {
    // ADV-152: both lines must coexist with distinct, truthful wording.
    expect(providerQualifier("fake")).not.toBe(providerPathNote("fake"));
    expect(providerQualifier("live")).not.toBe(providerPathNote("live"));
    expect(providerQualifier("fake")).toContain("no API keys, no cost");
  });
});

describe("providerPathNote / providerQualifier — Phase 18A local story", () => {
  it("describes the local pipeline truthfully (model proposes, validators construct)", () => {
    const note = providerPathNote("local");
    expect(note).toContain("local AI pipeline");
    expect(note).toContain("model proposes structured data");
    expect(note).toContain("deterministic validators");
    // Never a claim that the model proves the case, executes code or renders 3D.
    expect(note.toLowerCase()).not.toContain("proves");
    expect(note.toLowerCase()).not.toContain("renders");
  });

  it("qualifies local availability without URLs, credentials or host material", () => {
    const qualifier = providerQualifier("local");
    expect(qualifier).toContain("Local AI is available");
    expect(qualifier).toContain("no API keys, no cost");
    expect(qualifier.toLowerCase()).not.toContain("http");
    expect(qualifier).not.toMatch(/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/);
  });
});

describe("effectiveProviderMode — the capability report is the ONLY authority", () => {
  it("resolves live only when the backend reports live available", () => {
    expect(effectiveProviderMode(LIVE_READY)).toBe("live");
  });

  it("resolves local when the backend reports the Ollama mode available and no live mode", () => {
    expect(effectiveProviderMode(LOCAL_READY)).toBe("local");
  });

  it("resolves the deterministic default for a demo-only backend", () => {
    expect(effectiveProviderMode(DEMO_ONLY)).toBe("fake");
  });

  it("resolves fake when local is listed but unavailable (backend says so)", () => {
    expect(
      effectiveProviderMode({
        modes: [
          { id: "demo", available: true },
          { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
        ],
      }),
    ).toBe("fake");
  });

  it("resolves fake for an empty allowlist (unreachable/down backend)", () => {
    expect(effectiveProviderMode({ modes: [] })).toBe("fake");
  });

  it("resolves fake while capabilities are unknown", () => {
    expect(effectiveProviderMode(null)).toBe("fake");
  });

  it("resolves fake for a malformed/hostile payload (never favours an option)", () => {
    expect(
      effectiveProviderMode(
        { modes: [{ id: "local", available: "maybe" }] } as unknown as GenerationCapabilitiesResponse,
      ),
    ).toBe("fake");
    expect(effectiveProviderMode("olalam" as unknown as GenerationCapabilitiesResponse)).toBe("fake");
    expect(effectiveProviderMode({ modes: "not-an-array" } as unknown as GenerationCapabilitiesResponse)).toBe("fake");
  });

  it("ADV-208 — duplicate mode ids resolve identically for EVERY consumer (no Demo/local split)", () => {
    // The Phase 18A contradiction: first-wins (find) vs last-wins (Map) on
    // duplicate `local` entries. The PARSER dedupes first-wins, so both
    // consumers must agree on the SAME unavailable local.
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
    expect(effectiveProviderMode(parsed)).toBe("fake");
    expect(providerPathNoteFromCapabilities(parsed)).toBe(providerPathNote("fake"));
    expect(selectableGenerationModes(parsed).map((m) => m.id)).toEqual(["demo"]);
  });

  it("ADV-208 — the selector and the provider note agree when local IS the first (deduped) entry", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        { id: "local", available: false },
      ],
    });
    expect(effectiveProviderMode(parsed)).toBe("local");
    expect(selectableGenerationModes(parsed).map((m) => m.id)).toEqual(["demo", "local"]);
    expect(providerPathNoteFromCapabilities(parsed)).toBe(providerPathNote("local"));
  });
});

describe("capability-driven notes — VITE_APP_PROVIDER can never contradict the backend", () => {
  it("the per-path note derives from the DTO, not from any build env value", () => {
    // The capability-driven functions never consult import.meta.env: given the
    // same DTO the outcome is fixed, and the deterministic default is used
    // while the backend has not reported.
    expect(providerPathNoteFromCapabilities(DEMO_ONLY)).toBe(providerPathNote("fake"));
    expect(providerPathNoteFromCapabilities(LOCAL_READY)).toBe(providerPathNote("local"));
    expect(providerPathNoteFromCapabilities(LIVE_READY)).toBe(providerPathNote("live"));
    expect(providerPathNoteFromCapabilities(null)).toBe(providerPathNote("fake"));
  });

  it("the qualifier derives from the DTO, not from any build env value", () => {
    expect(providerQualifierFromCapabilities(DEMO_ONLY)).toBe(providerQualifier("fake"));
    expect(providerQualifierFromCapabilities(LOCAL_READY)).toBe(providerQualifier("local"));
    expect(providerQualifierFromCapabilities(LIVE_READY)).toBe(providerQualifier("live"));
    expect(providerQualifierFromCapabilities(null)).toBe(providerQualifier("fake"));
  });

  it("a demo-only backend can never render a live or local claim, whatever the env says", () => {
    // Even if an operator set VITE_APP_PROVIDER=live at build time, the render
    // path never reads it — the note stays the honest deterministic copy.
    expect(providerPathNoteFromCapabilities(DEMO_ONLY)).not.toContain("Live AI provider");
    expect(providerPathNoteFromCapabilities(DEMO_ONLY)).not.toContain("Local AI");
    // And a live-enabled env can never downgrade a local-ready backend to the
    // demo-build lie either.
    expect(providerPathNoteFromCapabilities(LOCAL_READY)).not.toContain(
      "uses the built-in deterministic generator in this demo build",
    );
  });
});