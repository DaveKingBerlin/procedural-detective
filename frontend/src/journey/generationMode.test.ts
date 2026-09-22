import { describe, expect, it } from "vitest";
import {
  GENERATION_MODE_STORAGE_KEY,
  LOCAL_AI_SHOWCASE_NOTE,
  availabilityTag,
  clearGenerationMode,
  generationModeLine,
  generationModeOptionLabel,
  getGenerationMode,
  isLocalModeAvailable,
  parseGenerationCapabilities,
  selectableGenerationModes,
  setGenerationMode,
  validatedJourneyMode,
  type GenerationModeStorage,
} from "./generationMode";
import { effectiveProviderMode } from "./providerMode";

/**
 * Phase 16 Track B — generation-mode capabilities allowlist parsing, the
 * demo-always / available-only offer rules, the honest label+tag copy, the
 * legacy `pd_generation_mode` read and — Phase 21 F-03 — the single
 * READ-ONLY "Generation mode" line (generationModeLine) that replaced the
 * interactive provider selector (the selected mode was never sent to the
 * backend; the provider is process-global). Everything is pure (no DOM, no
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

  it("ADV-208 — drops a model name carrying an `@`-joined IP literal", () => {
    const parsed = parseGenerationCapabilities({
      modes: [{ id: "local", available: true, label: "Local AI", model: "llama3@10.0.0.7" }],
    });
    expect(parsed.modes).toEqual([{ id: "local", available: true, label: "Local AI" }]);
  });

  it("ADV-208 — drops a label containing an RFC1918/link-local/loopback dotted IP", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "local", available: true, label: "net 192.168.1.77", model: "qwen2.5:7b" },
        { id: "live", available: true, label: "probe on 127.0.0.1", model: "169.254.10.1" },
      ],
    });
    expect(parsed.modes).toEqual([
      { id: "local", available: true, model: "qwen2.5:7b" },
      { id: "live", available: true },
    ]);
  });

  it("ADV-208 — drops raw HTML angle brackets from label/model (no markup can reach the DOM)", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "local", available: true, label: "<script>alert(1)</script>" },
        { id: "live", available: true, label: "Cloud AI", model: "<img src=x onerror=alert(1)>" },
      ],
    });
    expect(parsed.modes).toEqual([
      { id: "local", available: true },
      { id: "live", available: true, label: "Cloud AI" },
    ]);
  });

  it("ADV-208 — well-formed labels/models stay byte-identical (no sanitizer regression)", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "llama3.2:3b" },
        { id: "live", available: true, label: "Cloud AI" },
      ],
    });
    expect(parsed).toEqual({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "llama3.2:3b" },
        { id: "live", available: true, label: "Cloud AI" },
      ],
    });
    const modes = selectableGenerationModes(parsed);
    expect(modes[1].label).toBe("Local AI");
    expect(modes[1].model).toBe("llama3.2:3b");
    expect(generationModeOptionLabel(modes[1])).toBe("Local AI — llama3.2:3b — Ready");
  });
});

describe("ADV-208 — duplicate mode ids are deduped FIRST-WINS for every consumer", () => {
  it("first occurrence wins: available:false then true stays unavailable", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
    expect(parsed.modes.filter((m) => m.id === "local")).toEqual([
      { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
    ]);
    expect(isLocalModeAvailable(parsed)).toBe(false);
  });

  it("later duplicates are ignored even when the first was available", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true },
        { id: "local", available: false },
      ],
    });
    expect(parsed.modes.filter((m) => m.id === "local")).toHaveLength(1);
    expect(parsed.modes[0].available).toBe(true);
  });

  it("every consumer agrees — no truthful-provider contradiction on one page", () => {
    const parsed = parseGenerationCapabilities({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: false, label: "Local AI", model: "qwen2.5:7b" },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
    // effectiveProviderMode (first-wins via find) and selectableGenerationModes
    // (Map) MUST see the SAME deduped, unavailable local — never a "Demo
    // build" qualifier sitting next to a ready Local-AI option.
    expect(effectiveProviderMode(parsed)).toBe("fake");
    expect(selectableGenerationModes(parsed).map((m) => m.id)).toEqual(["demo"]);
    expect(isLocalModeAvailable(parsed)).toBe(false);
  });

  it("first-wins even for hand-constructed capabilities (parse bypassed)", () => {
    const modes = selectableGenerationModes({
      modes: [
        { id: "local", available: false },
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
      ],
    });
    expect(modes.map((m) => m.id)).toEqual(["demo"]);
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

  it("ADV-208 — last-line guard: a hand-constructed capabilities object cannot offer an IP model either", () => {
    const modes = selectableGenerationModes({
      modes: [
        { id: "demo", available: true },
        { id: "local", available: true, label: "Local AI", model: "meta-llama@203.0.113.7:11434" },
      ],
    });
    expect(modes[1].model).toBeNull();
    expect(generationModeOptionLabel(modes[1])).toBe("Local AI — Ready");
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

describe("generationModeLine — Phase 21 F-03 the single READ-ONLY authoritative line", () => {
  const caps = (raw: unknown) => parseGenerationCapabilities(raw);

  it("demo-only backend -> 'Generation mode: Deterministic demo' (never a fabricated provider)", () => {
    expect(
      generationModeLine(caps({ modes: [{ id: "demo", available: true }] })),
    ).toBe("Generation mode: Deterministic demo");
    expect(
      generationModeLine(
        caps({ modes: [{ id: "local", available: false, label: "Local AI" }] }),
      ),
    ).toBe("Generation mode: Deterministic demo");
  });

  it("local available -> 'Generation mode: Local AI — <model> — Ready'", () => {
    expect(
      generationModeLine(
        caps({ modes: [{ id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" }] }),
      ),
    ).toBe("Generation mode: Local AI — qwen2.5:7b — Ready");
    // without a DTO model name no fabricated model appears
    expect(
      generationModeLine(caps({ modes: [{ id: "local", available: true, label: "Local AI" }] })),
    ).toBe("Generation mode: Local AI — Ready");
  });

  it("live available -> 'Generation mode: <capability label>' (DTO verbatim)", () => {
    expect(
      generationModeLine(caps({ modes: [{ id: "live", available: true, label: "Cloud AI" }] })),
    ).toBe("Generation mode: Cloud AI");
  });

  it("live wins over local when both are reported available (ONE provider story)", () => {
    const both = caps({
      modes: [
        { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        { id: "live", available: true, label: "Cloud AI" },
      ],
    });
    // Must agree with effectiveProviderMode — the same resolver the provider
    // qualifier / per-path note use, so the page can never self-contradict.
    expect(effectiveProviderMode(both)).toBe("live");
    expect(generationModeLine(both)).toBe("Generation mode: Cloud AI");
  });

  it("unknown/unreachable/malformed payloads -> the safe deterministic-demo copy", () => {
    expect(generationModeLine(null)).toBe("Generation mode: Deterministic demo");
    expect(generationModeLine(caps({ modes: [] }))).toBe("Generation mode: Deterministic demo");
    expect(generationModeLine(caps(null))).toBe("Generation mode: Deterministic demo");
  });

  it("hostile DTO material never reaches the line (frozen public fallbacks only)", () => {
    const hostile = caps({
      modes: [
        { id: "local", available: true, label: "http://127.0.0.1:11434", model: "llama3@10.0.0.7" },
        { id: "live", available: true, label: "javascript:alert(1)" },
      ],
    });
    const line = generationModeLine(hostile);
    expect(line).not.toContain("127.0.0.1");
    expect(line).not.toContain("11434");
    expect(line).not.toContain("javascript");
    expect(line).not.toContain("@");
    // live wins; the hostile label drops to the frozen public fallback
    expect(line).toBe("Generation mode: Cloud AI");
  });

  it("agrees with effectiveProviderMode for every parsed allowlist (no contradiction)", () => {
    const payloads: unknown[] = [
      { modes: [{ id: "demo", available: true }] },
      { modes: [{ id: "local", available: true, label: "Local AI", model: "x" }] },
      { modes: [{ id: "live", available: true, label: "Cloud AI" }] },
      { modes: [] },
      null,
    ];
    for (const raw of payloads) {
      const parsed = caps(raw);
      const provider = effectiveProviderMode(parsed);
      const line = generationModeLine(parsed);
      if (provider === "fake") {
        expect(line).toContain("Deterministic demo");
      } else if (provider === "local") {
        expect(line).toContain("Local AI");
        expect(line).toContain("Ready");
      } else {
        expect(line).toContain("Cloud AI");
      }
      expect(line.startsWith("Generation mode: ")).toBe(true);
    }
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

  it("round-trips every frozen id (demo/local/live) through injectable storage", () => {
    for (const id of ["demo", "local", "live"] as const) {
      const storage = fakeStorage();
      expect(setGenerationMode(id, storage)).toBe(true);
      expect(getGenerationMode(storage)).toBe(id);
      expect(storage.entries.get(GENERATION_MODE_STORAGE_KEY)).toBe(id);
    }
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

describe("isLocalModeAvailable — §20 honesty probe over the parsed allowlist", () => {
  const caps = (raw: unknown) => parseGenerationCapabilities(raw);

  it("is true only for an explicit local-available entry", () => {
    expect(isLocalModeAvailable(caps({ modes: [{ id: "local", available: true }] }))).toBe(true);
    expect(
      isLocalModeAvailable(
        caps({ modes: [{ id: "demo", available: true }, { id: "local", available: true }] }),
      ),
    ).toBe(true);
  });

  it("is false when local is unavailable, absent, malformed or unknown", () => {
    expect(isLocalModeAvailable(caps({ modes: [{ id: "local", available: false }] }))).toBe(false);
    expect(isLocalModeAvailable(caps({ modes: [{ id: "demo", available: true }] }))).toBe(false);
    expect(isLocalModeAvailable(caps({ modes: [] }))).toBe(false);
    expect(isLocalModeAvailable(caps({ modes: [{ id: "local", available: "yes" }] }))).toBe(false);
    expect(isLocalModeAvailable(caps(null))).toBe(false);
    expect(isLocalModeAvailable(null)).toBe(false);
  });
});

describe("validatedJourneyMode — ADV-212 the /generating mode is validated against LIVE capabilities", () => {
  const caps = (raw: unknown) => parseGenerationCapabilities(raw);

  it("keeps local ONLY when the live capability report confirms local available", () => {
    expect(validatedJourneyMode("local", caps({ modes: [{ id: "local", available: true }] }))).toBe(
      "local",
    );
    expect(
      validatedJourneyMode("local", caps({ modes: [{ id: "local", available: false }] })),
    ).toBeNull();
    expect(validatedJourneyMode("local", caps({ modes: [] }))).toBeNull();
    expect(validatedJourneyMode("local", null)).toBeNull();
  });

  it("a stored local on a demo-only backend falls back to generic (no local claim)", () => {
    expect(
      validatedJourneyMode("local", caps({ modes: [{ id: "demo", available: true }] })),
    ).toBeNull();
  });

  it("a stale/tampered local with unknown capabilities resolves to generic", () => {
    expect(validatedJourneyMode("local", caps({ modes: [{ id: "live", available: true }] }))).toBeNull();
    // The duplicate-id parse above also feeds this rule: a dup'd local that
    // resolved first-wins to unavailable can never confirm a local claim.
    const duplicate = caps({
      modes: [
        { id: "local", available: false },
        { id: "local", available: true },
      ],
    });
    expect(validatedJourneyMode("local", duplicate)).toBeNull();
  });

  it("demo/live/unset are mode-independent (generic labels either way)", () => {
    for (const stored of [null, "demo", "live"] as const) {
      expect(
        validatedJourneyMode(stored, caps({ modes: [{ id: "local", available: true }] })),
      ).toBe(stored);
      expect(validatedJourneyMode(stored, caps({ modes: [] }))).toBe(stored);
      expect(validatedJourneyMode(stored, null)).toBe(stored);
    }
  });
});

describe("LOCAL_AI_SHOWCASE_NOTE — §36 accurate showcase copy", () => {
  it("carries the accurate Prompt-to-World statement with the deterministic verifier role", () => {
    expect(LOCAL_AI_SHOWCASE_NOTE).toContain("Procedural Detective");
    expect(LOCAL_AI_SHOWCASE_NOTE).toContain("generative Prompt-to-World pipeline");
    expect(LOCAL_AI_SHOWCASE_NOTE).toContain("local Llama 3.2 model");
    expect(LOCAL_AI_SHOWCASE_NOTE).toContain("the model proposes structured data");
    expect(LOCAL_AI_SHOWCASE_NOTE).toContain("deterministic validators verify and construct");
  });

  it("never overclaims (no prove/execute/arbitrary/perfect claims, no URL tokens)", () => {
    for (const overclaim of ["proves the case", "executes", "arbitrary", "perfectly", "http"]) {
      expect(LOCAL_AI_SHOWCASE_NOTE.toLowerCase()).not.toContain(overclaim);
    }
  });
});