import { describe, expect, it } from "vitest";
import { parseAppProvider, providerPathNote, providerQualifier } from "./providerMode";

/**
 * Phase 15 Track B — config-driven provider notes. The exact string "live" is
 * the ONLY value that activates the live provider note; everything else stays
 * on the honest deterministic-demo copy, so a missing/misspelled env value can
 * never make the UI claim live-AI behavior it does not have.
 */

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