import { describe, expect, it } from "vitest";
import type { GenerationCapabilitiesResponse } from "../api/types";
import { parseGenerationCapabilities, selectableGenerationModes } from "./generationMode";
import {
  PROVIDER_PATH_NOTE_UNKNOWN,
  PROVIDER_QUALIFIER_UNKNOWN,
  effectiveProviderMode,
  parseAppProvider,
  providerIsReported,
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
  });

  it("the qualifier derives from the DTO, not from any build env value", () => {
    expect(providerQualifierFromCapabilities(DEMO_ONLY)).toBe(providerQualifier("fake"));
    expect(providerQualifierFromCapabilities(LOCAL_READY)).toBe(providerQualifier("local"));
    expect(providerQualifierFromCapabilities(LIVE_READY)).toBe(providerQualifier("live"));
  });

  it("DEF-097 — a NULL/unreachable DTO yields the NEUTRAL qualifier + note (no deterministic/live claim)", () => {
    // The frontend CANNOT know the provider when the DTO is unavailable, so
    // NO provider claim of any kind is made — the page's neutral CTA is never
    // contradicted by a "Demo build" / "Live AI" claim beside it.
    expect(providerQualifierFromCapabilities(null)).toBe(PROVIDER_QUALIFIER_UNKNOWN);
    expect(providerQualifierFromCapabilities(null)).not.toContain("Demo build");
    expect(providerQualifierFromCapabilities(null)).not.toContain("Live AI");
    expect(providerPathNoteFromCapabilities(null)).toBe(PROVIDER_PATH_NOTE_UNKNOWN);
    expect(providerPathNoteFromCapabilities(null)).not.toContain("deterministic");
  });

  it("DEF-097 — the empty-allowlist fetch-failure payload is DTO-unavailable too (neutral, not deterministic)", () => {
    const empty = { modes: [] } as GenerationCapabilitiesResponse;
    const malformed = { modes: "nope" } as unknown as GenerationCapabilitiesResponse;
    for (const unavailable of [empty, malformed]) {
      expect(providerQualifierFromCapabilities(unavailable)).toBe(PROVIDER_QUALIFIER_UNKNOWN);
      expect(providerPathNoteFromCapabilities(unavailable)).toBe(PROVIDER_PATH_NOTE_UNKNOWN);
    }
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

describe("effectiveProviderMode — Phase 21B configuredProvider is authoritative (DEF-096)", () => {
  it("configuredProvider 'ollama' resolves 'local' EVEN when the probe failed (both modes unavailable)", () => {
    const probeFailed: GenerationCapabilitiesResponse = {
      configuredProvider: "ollama",
      modes: [
        { id: "demo", available: false },
        { id: "local", available: false, label: "Local AI", model: "llama3.2:3b" },
      ],
    };
    expect(effectiveProviderMode(probeFailed)).toBe("local");
  });

  it("configuredProvider 'live' resolves 'live' EVEN when the probe failed", () => {
    const probeFailed: GenerationCapabilitiesResponse = {
      configuredProvider: "live",
      modes: [
        { id: "demo", available: false },
        { id: "live", available: false, label: "Cloud AI" },
      ],
    };
    expect(effectiveProviderMode(probeFailed)).toBe("live");
  });

  it("configuredProvider 'fake' resolves 'fake' exactly like the availability-based logic", () => {
    const withFake: GenerationCapabilitiesResponse = {
      configuredProvider: "fake",
      modes: [{ id: "demo", available: true }],
    };
    expect(effectiveProviderMode(withFake)).toBe("fake");
  });

  it("configuredProvider ABSENT falls back to the availability-based derivation (older server)", () => {
    // No configuredProvider key: the pre-21B behavior holds unchanged.
    expect(effectiveProviderMode(null)).toBe("fake");
    expect(effectiveProviderMode({ modes: [] })).toBe("fake");
    expect(effectiveProviderMode(DEMO_ONLY)).toBe("fake");
    expect(effectiveProviderMode(LOCAL_READY)).toBe("local");
    expect(effectiveProviderMode(LIVE_READY)).toBe("live");
  });

  it("providerIsReported — the DTO told us something ONLY for a non-null object with a non-empty modes array", () => {
    expect(providerIsReported(null)).toBe(false);
    expect(providerIsReported({ modes: [] })).toBe(false);
    expect(providerIsReported({ modes: "nope" } as unknown as GenerationCapabilitiesResponse)).toBe(false);
    expect(providerIsReported(DEMO_ONLY)).toBe(true);
    expect(providerIsReported(LOCAL_READY)).toBe(true);
    expect(providerIsReported(parseGenerationCapabilities({ configuredProvider: "ollama", modes: [] }))).toBe(false);
  });
});