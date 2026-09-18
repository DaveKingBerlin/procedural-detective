import { describe, expect, it } from "vitest";
import {
  GENERATION_MODE_STORAGE_KEY,
  availabilityTag,
  clearGenerationMode,
  generationModeOptionLabel,
  getGenerationMode,
  parseGenerationCapabilities,
  selectableGenerationModes,
  setGenerationMode,
  type GenerationModeStorage,
} from "./generationMode";

/**
 * Phase 16 Track B — generation-mode capabilities allowlist parsing, the
 * demo-always / available-only selection rules, the honest label+tag copy and
 * the `pd_generation_mode` persistence. Everything is pure (no DOM, no
 * network).
 */

function fakeStorage(): GenerationModeStorage & { entries: Map<string, string> } {
  const entries = new Map<string, string>();
  return {
    entries,
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => void entries.set(key, value),
    removeItem: (key) => void entries.delete(key),
  };
}

describe("parseGenerationCapabilities — trust-boundary allowlist", () => {
  it("keeps the known modes with their safe fields", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        { id: "live", available: false, label: "Cloud AI" },
      ],
    });
    expect(parsed).toEqual({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        { id: "live", available: false, label: "Cloud AI" },
      ],
    });
  });

  it("drops unknown mode ids and unknown fields", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "shiny-new-ai", available: true, url: "http://secret-host:11434" },
        {
          id: "demo",
          available: true,
          url: "http://secret-host:11434",
          apiKey: "hunter2",
          prompt: "top secret",
          diagnostics: { error: "probe failed" },
        },
      ],
    });
    expect(parsed).toEqual({ modes: [{ id: "demo", available: true }] });
  });

  it("never throws and resolves to an empty allowlist for malformed payloads", () => {
    for (const raw of [null, undefined, 42, "modes", {}, { modes: "nope" }, { modes: [42, "x"] }]) {
      expect(parseGenerationCapabilities(raw)).toEqual({ modes: [] });
    }
  });

  it("accepts `available` only as a strict boolean (everything else reads false)", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "demo", available: "true" },
        { id: "local", available: 1 },
        { id: "live" },
      ],
    });
    expect(parsed.modes.map((m) => m.available)).toEqual([false, false, false]);
  });

  it("keeps label/model only when they are non-empty strings", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "local", available: true, label: 42, model: { name: "x" } },
        { id: "live", available: true, label: "", model: "" },
      ],
    });
    expect(parsed).toEqual({
      modes: [
        { id: "local", available: true },
        { id: "live", available: true },
      ],
    });
  });

  it("drops display strings that would smuggle URLs/data URIs/host hints", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        {
          id: "local",
          available: true,
          label: "http://127.0.0.1:11434",
          model: "data:text/html;base64,PHN0",
        },
        {
          id: "live",
          available: true,
          label: "javascript:alert(1)",
          model: "https://evil.example/x",
        },
      ],
    });
    expect(parsed).toEqual({
      modes: [
        { id: "local", available: true },
        { id: "live", available: true },
      ],
    });
  });
});

describe("selectableGenerationModes — Demo always, Local/Live only when available", () => {
  it("always offers Demo even when the backend omits it (fallback)", () => {
    const modes = selectableGenerationModes(
      parseGenerationCapabilities({ modes: [{ id: "live", available: true, label: "Cloud AI" }] }),
    );
    expect(modes.map((m) => m.id)).toEqual(["demo", "live"]);
    expect(modes[0].label).toBe("Demo");
    expect(modes[0].ready).toBe(true);
  });

  it("offers Local AI and Cloud AI ONLY when the backend reports available", () => {
    const modes = selectableGenerationModes(
      parseGenerationCapabilities({
        modes: [
          { id: "demo", available: true },
          { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
          { id: "live", available: true, label: "Cloud AI" },
        ],
      }),
    );
    expect(modes.map((m) => m.id)).toEqual(["demo", "live"]);
  });

  it("never renders an unavailable mode as an option (local+live both unavailable)", () => {
    const modes = selectableGenerationModes(
      parseGenerationCapabilities({
        modes: [
          { id: "demo", available: true },
          { id: "local", available: false, label: "Local AI" },
          { id: "live", available: false, label: "Cloud AI" },
        ],
      }),
    );
    expect(modes.map((m) => m.id)).toEqual(["demo"]);
  });

  it("keeps the DTO label and model verbatim for an available local mode", () => {
    const modes = selectableGenerationModes(
      parseGenerationCapabilities({
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        ],
      }),
    );
    expect(modes[1]).toEqual({ id: "local", label: "Local AI", model: "qwen2.5:7b", ready: true });
  });

  it("falls back to neutral frozen labels when the DTO omits them", () => {
    const modes = selectableGenerationModes(
      parseGenerationCapabilities({
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true },
          { id: "live", available: true },
        ],
      }),
    );
    expect(modes.map((m) => m.label)).toEqual(["Demo", "Local AI", "Cloud AI"]);
  });

  it("resolves null (unknown/loading) to the demo-only offer", () => {
    expect(selectableGenerationModes(null).map((m) => m.id)).toEqual(["demo"]);
  });
});

describe("availabilityTag — derived ONLY from the DTO `available`", () => {
  it("maps true to Ready and false to Unavailable", () => {
    expect(availabilityTag(true)).toBe("Ready");
    expect(availabilityTag(false)).toBe("Unavailable");
  });
});

describe("generationModeOptionLabel — honest label + model + tag", () => {
  it("labels the Local option with the verbatim model name and the derived Ready tag", () => {
    const modes = selectableGenerationModes(
      parseGenerationCapabilities({
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        ],
      }),
    );
    expect(generationModeOptionLabel(modes[1])).toBe("Local AI — qwen2.5:7b — Ready");
  });

  it("keeps the Local label honest (Ready tag present) even without a model name", () => {
    const modes = selectableGenerationModes(
      parseGenerationCapabilities({
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI" },
        ],
      }),
    );
    expect(generationModeOptionLabel(modes[1])).toBe("Local AI — Ready");
  });

  it("uses the DTO label verbatim for Cloud AI and the plain Demo label", () => {
    const modes = selectableGenerationModes(
      parseGenerationCapabilities({
        modes: [{ id: "live", available: true, label: "Cloud AI" }],
      }),
    );
    expect(generationModeOptionLabel(modes[0])).toBe("Demo");
    expect(generationModeOptionLabel(modes[1])).toBe("Cloud AI");
  });
});

describe("pd_generation_mode persistence", () => {
  it("round-trips a selected mode through injectable storage", () => {
    const storage = fakeStorage();
    expect(setGenerationMode("local", storage)).toBe(true);
    expect(getGenerationMode(storage)).toBe("local");
    expect(storage.entries.get(GENERATION_MODE_STORAGE_KEY)).toBe("local");
  });

  it("cleanly clears the stored mode", () => {
    const storage = fakeStorage();
    setGenerationMode("live", storage);
    clearGenerationMode(storage);
    expect(getGenerationMode(storage)).toBeNull();
    expect(storage.entries.has(GENERATION_MODE_STORAGE_KEY)).toBe(false);
  });

  it("returns null for storage values outside the frozen ids (no stale/tampered drive)", () => {
    const storage = fakeStorage();
    storage.setItem(GENERATION_MODE_STORAGE_KEY, "banana");
    expect(getGenerationMode(storage)).toBeNull();
    storage.setItem(GENERATION_MODE_STORAGE_KEY, "shiny-new-ai");
    expect(getGenerationMode(storage)).toBeNull();
  });

  it("never throws when storage is unavailable or throws", () => {
    expect(getGenerationMode(null)).toBeNull();
    expect(setGenerationMode("demo", null)).toBe(false);
    const throwing: GenerationModeStorage = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    };
    expect(getGenerationMode(throwing)).toBeNull();
    expect(clearGenerationMode(throwing)).toBeUndefined();
  });
});