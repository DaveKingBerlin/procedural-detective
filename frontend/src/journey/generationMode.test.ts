import { describe, expect, it } from "vitest";
import {
  GENERATION_MODE_STORAGE_KEY,
  LOCAL_AI_SHOWCASE_NOTE,
  availabilityTag,
  clearGenerationMode,
  demoCtaLabel,
  demoCtaNote,
  demoCtaState,
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
 *
 * Phase 21B Finding 3 — the example-case CTA (demoCtaLabel / demoCtaNote /
 * demoCtaState) is TRUTHFUL per the capability DTO: the deterministic/no-cost
 * promise is shown ONLY for a KNOWN demo-only allowlist; a local/live backend
 * renames the CTA with a per-mode note; a null/unreachable DTO downgrades to
 * the neutral label. The action itself never changes (same runDemo path).
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

  it("Phase 21B (DEF-096) — re-sanitizes `configuredProvider` to the closed enum 'fake'|'ollama'|'live'", () => {
    const fake = parseGenerationCapabilities({
      configuredProvider: "fake",
      modes: [{ id: "demo", available: true }],
    });
    expect(fake.configuredProvider).toBe("fake");
    const ollama = parseGenerationCapabilities({
      configuredProvider: "ollama",
      modes: [{ id: "demo", available: false }, { id: "local", available: false, label: "Local AI" }],
    });
    expect(ollama.configuredProvider).toBe("ollama");
    const live = parseGenerationCapabilities({
      configuredProvider: "live",
      modes: [{ id: "demo", available: false }, { id: "live", available: false, label: "Cloud AI" }],
    });
    expect(live.configuredProvider).toBe("live");
  });

  it("Phase 21B (DEF-096) — missing/unknown/malformed `configuredProvider` is DROPPED (consumers read UNKNOWN, never 'fake')", () => {
    // The key is OMITTED from the parsed result — identical to an OLDER
    // server that never emits the field — so a hostile reply can never
    // fabricate the deterministic story through this field.
    expect(parseGenerationCapabilities({ configuredProvider: "http://127.0.0.1:11434", modes: [] })).toEqual({ modes: [] });
    expect(parseGenerationCapabilities({ configuredProvider: "LOCAL", modes: [] })).toEqual({ modes: [] });
    expect(parseGenerationCapabilities({ configuredProvider: "shiny-new-ai", modes: [] })).toEqual({ modes: [] });
    expect(parseGenerationCapabilities({ configuredProvider: 42, modes: [] })).toEqual({ modes: [] });
    expect(parseGenerationCapabilities({ modes: [] })).toEqual({ modes: [] });
    expect(parseGenerationCapabilities(null)).toEqual({ modes: [] });
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

  it("DEF-097 — unknown/unreachable/malformed payloads -> the NEUTRAL reachability line (never a deterministic claim)", () => {
    // Phase 21B / DEF-097: the frontend CANNOT know the provider when the DTO
    // did not report (null, empty allowlist after a fetch failure, malformed),
    // so the F-03 line must be neutral — the "Deterministic demo" copy is a
    // provider claim and is reserved for a reported fake backend.
    expect(generationModeLine(null)).toBe(
      "Generation mode: Available once the service is reachable.",
    );
    expect(generationModeLine(caps({ modes: [] }))).toBe(
      "Generation mode: Available once the service is reachable.",
    );
    expect(generationModeLine(caps(null))).toBe(
      "Generation mode: Available once the service is reachable.",
    );
    // Never a fabricated "Deterministic demo" in the DTO-unavailable state.
    expect(generationModeLine(null)).not.toContain("Deterministic demo");
  });

  it("DEF-096 — a configured local backend with a FAILED probe shows the truthful 'Unavailable' per-mode line, never 'Deterministic demo'", () => {
    // Phase 21B / DEF-096: the DTO carries configuredProvider 'ollama' but the
    // probe failed (local + demo both unavailable). The runtime WILL still run
    // the Ollama provider on the next POST /cases, so the line must stay
    // per-mode with the availability-appropriate tag — never the demo story.
    const probeFailed = caps({
      configuredProvider: "ollama",
      modes: [
        { id: "demo", available: false },
        { id: "local", available: false, label: "Local AI", model: "llama3.2:3b" },
      ],
    });
    expect(generationModeLine(probeFailed)).toBe(
      "Generation mode: Local AI — llama3.2:3b — Unavailable",
    );
    expect(generationModeLine(probeFailed)).not.toContain("Deterministic demo");
  });

  it("DEF-096 — a configured live backend with a FAILED probe shows 'Cloud AI — Unavailable'", () => {
    const probeFailed = caps({
      configuredProvider: "live",
      modes: [
        { id: "demo", available: false },
        { id: "live", available: false, label: "Cloud AI" },
      ],
    });
    expect(generationModeLine(probeFailed)).toBe("Generation mode: Cloud AI — Unavailable");
    expect(generationModeLine(probeFailed)).not.toContain("Deterministic demo");
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

  it("agrees with effectiveProviderMode for every REPORTED allowlist; unknown DTOs get the neutral line (no contradiction)", () => {
    const payloads: Array<{ raw: unknown; lineSnippet: string }> = [
      { raw: { modes: [{ id: "demo", available: true }] }, lineSnippet: "Deterministic demo" },
      {
        raw: { modes: [{ id: "local", available: true, label: "Local AI", model: "x" }] },
        lineSnippet: "Local AI — x — Ready",
      },
      { raw: { modes: [{ id: "live", available: true, label: "Cloud AI" }] }, lineSnippet: "Cloud AI" },
      {
        raw: {
          configuredProvider: "ollama",
          modes: [
            { id: "demo", available: false },
            { id: "local", available: false, label: "Local AI", model: "llama3.2:3b" },
          ],
        },
        lineSnippet: "Local AI — llama3.2:3b — Unavailable",
      },
    ];
    for (const { raw, lineSnippet } of payloads) {
      const parsed = caps(raw);
      const provider = effectiveProviderMode(parsed);
      const line = generationModeLine(parsed);
      if (provider === "fake") {
        expect(line).toBe("Generation mode: Deterministic demo");
      }
      expect(line).toBe(`Generation mode: ${lineSnippet}`);
      expect(line.startsWith("Generation mode: ")).toBe(true);
    }
    // DTO-unavailable states stay on the NEUTRAL line — never the
    // deterministic story (DEF-097), which would contradict the neutral CTA.
    for (const raw of [{ modes: [] }, null]) {
      expect(generationModeLine(caps(raw))).toBe(
        "Generation mode: Available once the service is reachable.",
      );
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

describe("demoCtaState — Phase 21B Finding 3 truthful example-case CTA resolution", () => {
  const caps = (raw: unknown) => parseGenerationCapabilities(raw);

  it("resolves 'demo' ONLY for a known, non-empty demo-only allowlist (GENERATION_PROVIDER=fake)", () => {
    // The genuine fake backend always reports a non-empty allowlist with the
    // demo mode available and local/live absent-or-unavailable.
    expect(
      demoCtaState(caps({ modes: [{ id: "demo", available: true }] })),
    ).toBe("demo");
    expect(
      demoCtaState(
        caps({
          modes: [
            { id: "demo", available: true },
            { id: "local", available: false, label: "Local AI" },
          ],
        }),
      ),
    ).toBe("demo");
  });

  it("resolves 'local' when the backend reports the local pipeline available", () => {
    expect(
      demoCtaState(
        caps({
          modes: [
            { id: "demo", available: true },
            { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
          ],
        }),
      ),
    ).toBe("local");
  });

  it("resolves 'live' when the backend reports the live provider available (live wins)", () => {
    expect(
      demoCtaState(
        caps({
          modes: [
            { id: "demo", available: true },
            { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
            { id: "live", available: true, label: "Cloud AI" },
          ],
        }),
      ),
    ).toBe("live");
  });

  it("resolves 'unknown' for a null/unreachable/empty/malformed DTO — never a demo claim", () => {
    // Phase 21B: the frontend cannot know the provider when the DTO is
    // unavailable, so NO state may infer the deterministic promise from
    // absence — an empty allowlist is the fetch-failure payload.
    expect(demoCtaState(null)).toBe("unknown");
    expect(demoCtaState(caps({ modes: [] }))).toBe("unknown");
    expect(demoCtaState(caps(null))).toBe("unknown");
    expect(demoCtaState(caps("olalam"))).toBe("unknown");
    expect(
      demoCtaState(caps({ modes: [{ id: "local", available: false, label: "Local AI" }] })),
    ).toBe("unknown");
  });

  it("never resolves 'demo' when no demo mode is actually available (hostile payload)", () => {
    expect(
      demoCtaState(caps({ modes: [{ id: "demo", available: false }] })),
    ).toBe("unknown");
  });
});

describe("demoCtaState — Phase 21B hosted-demo deterministic fallback (backend authority)", () => {
  const caps = (raw: unknown) => parseGenerationCapabilities(raw);

  it("when demo-only (older server — configuredProvider absent), the journey mode/labels stay demo and match the backend capability report", () => {
    const demoOnly = caps({ modes: [{ id: "demo", available: true }] });
    // The backend is authoritative -> effective provider is fake.
    expect(effectiveProviderMode(demoOnly)).toBe("fake");
    // The journey mode resolution keeps the demo/generic labels (nothing
    // ever switches to a local/live claim on a demo-only backend).
    expect(validatedJourneyMode(null, demoOnly)).toBeNull();
    expect(validatedJourneyMode("demo", demoOnly)).toBe("demo");
    // The read-only mode line and the CTA agree: deterministic demo.
    expect(generationModeLine(demoOnly)).toBe("Generation mode: Deterministic demo");
    expect(demoCtaState(demoOnly)).toBe("demo");
    expect(demoCtaLabel(demoOnly)).toBe("Try Demo Case");
    expect(demoCtaNote(demoOnly)).toBe("Deterministic demo — no API keys, no cost.");
  });

  it("DEF-096 — a probe-FAILED ollama backend (configuredProvider 'ollama', demo+local unavailable) yields NOT-'demo' CTA, NOT-'fake' mode and a truthful Unavailable line", () => {
    // The ADV-233 probe-failed DTO (new backend shape): demo.available false
    // + local.available false + configuredProvider "ollama". The configured
    // provider is AUTHORITATIVE: the runtime will still build the Ollama
    // provider on POST /cases, so the deterministic story must never appear.
    const probeFailed = caps({
      configuredProvider: "ollama",
      modes: [
        { id: "demo", available: false },
        { id: "local", available: false, label: "Local AI", model: "llama3.2:3b" },
      ],
    });
    expect(probeFailed.configuredProvider).toBe("ollama");
    expect(effectiveProviderMode(probeFailed)).not.toBe("fake");
    expect(effectiveProviderMode(probeFailed)).toBe("local");
    expect(demoCtaState(probeFailed)).not.toBe("demo");
    expect(demoCtaState(probeFailed)).toBe("local");
    expect(demoCtaLabel(probeFailed)).toBe("Try an example case");
    expect(demoCtaNote(probeFailed)).not.toBe("Deterministic demo — no API keys, no cost.");
    expect(demoCtaNote(probeFailed)).toContain("Not the free deterministic demo");
    // The F-03 line stays per-mode with the availability-appropriate tag.
    expect(generationModeLine(probeFailed)).toBe(
      "Generation mode: Local AI — llama3.2:3b — Unavailable",
    );
  });

  it("configuredProvider 'fake' stays byte-identical to the availability-based fake backend (server-enforced deterministic)", () => {
    const withFake = caps({
      configuredProvider: "fake",
      modes: [{ id: "demo", available: true }],
    });
    expect(effectiveProviderMode(withFake)).toBe("fake");
    expect(demoCtaState(withFake)).toBe("demo");
    expect(demoCtaLabel(withFake)).toBe("Try Demo Case");
    expect(demoCtaNote(withFake)).toBe("Deterministic demo — no API keys, no cost.");
    expect(generationModeLine(withFake)).toBe("Generation mode: Deterministic demo");
  });

  it("configuredProvider 'live' resolves the live CTA even when the probe is down", () => {
    const liveDown = caps({
      configuredProvider: "live",
      modes: [
        { id: "demo", available: false },
        { id: "live", available: false, label: "Cloud AI" },
      ],
    });
    expect(effectiveProviderMode(liveDown)).toBe("live");
    expect(demoCtaState(liveDown)).toBe("live");
    expect(demoCtaLabel(liveDown)).toBe("Try an example case");
    expect(demoCtaNote(liveDown)).toContain("runs the cloud AI provider");
    expect(generationModeLine(liveDown)).toBe("Generation mode: Cloud AI — Unavailable");
  });

  it("a local/live backend can never make the journey claim deterministic demo", () => {
    for (const raw of [
      {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        ],
      },
      {
        modes: [
          { id: "demo", available: true },
          { id: "live", available: true, label: "Cloud AI" },
        ],
      },
    ]) {
      const parsed = caps(raw);
      expect(effectiveProviderMode(parsed)).not.toBe("fake");
      expect(demoCtaState(parsed)).not.toBe("demo");
      expect(demoCtaNote(parsed)).not.toBe("Deterministic demo — no API keys, no cost.");
    }
  });
});

describe("demoCtaLabel — Phase 21B Finding 3 truthful CTA label", () => {
  const caps = (raw: unknown) => parseGenerationCapabilities(raw);

  it("keeps 'Try Demo Case' ONLY for a fake-configured backend (server-enforced deterministic) or the older-server availability-derived demo-only shape", () => {
    // configuredProvider present: only "fake" keeps the deterministic CTA
    // (DEF-096 — the demo promise is truthful ONLY when the backend actually
    // server-enforces the deterministic path).
    expect(
      demoCtaLabel(
        caps({ configuredProvider: "fake", modes: [{ id: "demo", available: true }] }),
      ),
    ).toBe("Try Demo Case");
    // Older server (configuredProvider absent): the availability-derived
    // demo-only shape stays the deterministic CTA (backward compatible).
    expect(demoCtaLabel(caps({ modes: [{ id: "demo", available: true }] }))).toBe("Try Demo Case");
    expect(
      demoCtaLabel(
        caps({
          modes: [
            { id: "demo", available: true },
            { id: "local", available: false, label: "Local AI" },
          ],
        }),
      ),
    ).toBe("Try Demo Case");
  });

  it("ADV-233 — a probe-FAILED ollama backend DTO (configuredProvider 'ollama', demo+local unavailable) MUST NOT keep 'Try Demo Case'", () => {
    // The ADV-233 lock: this DTO is an OLLAMA-CONFIGURED box with a
    // temporarily unreachable /api/tags — it is NOT demo-only. The configured
    // provider is authoritative, so the CTA is the neutral rename, never the
    // deterministic promise (the action would run the Ollama provider).
    expect(
      demoCtaLabel(
        caps({
          configuredProvider: "ollama",
          modes: [
            { id: "demo", available: false },
            { id: "local", available: false, label: "Local AI" },
          ],
        }),
      ),
    ).toBe("Try an example case");
  });

  it("renames the CTA for a local/live backend (never overclaims determinism)", () => {
    for (const raw of [
      {
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        ],
      },
      {
        modes: [
          { id: "demo", available: true },
          { id: "live", available: true, label: "Cloud AI" },
        ],
      },
    ]) {
      expect(demoCtaLabel(caps(raw))).toBe("Try an example case");
    }
  });

  it("downgrades to the neutral label when the DTO is unavailable (fetch failure / null)", () => {
    // Phase 21B: capability fetch FAILURE must never present the historical
    // "Try Demo Case" + deterministic no-cost promise — the frontend cannot
    // know the provider when the DTO is unavailable.
    expect(demoCtaLabel(null)).toBe("Try an example case");
    expect(demoCtaLabel(caps({ modes: [] }))).toBe("Try an example case");
  });
});

describe("demoCtaNote — Phase 21B Finding 3 truthful per-state note", () => {
  const caps = (raw: unknown) => parseGenerationCapabilities(raw);

  it("keeps the deterministic/no-cost promise ONLY for the known demo-only backend", () => {
    expect(demoCtaNote(caps({ modes: [{ id: "demo", available: true }] }))).toBe(
      "Deterministic demo — no API keys, no cost.",
    );
  });

  it("local available -> names the local AI provider + label/model from the DTO, never the no-cost promise", () => {
    const note = demoCtaNote(
      caps({
        modes: [
          { id: "demo", available: true },
          { id: "local", available: true, label: "Local AI", model: "qwen2.5:7b" },
        ],
      }),
    );
    expect(note).toContain("runs the local AI provider");
    expect(note).toContain("Local AI — qwen2.5:7b");
    // The warning that this is NOT the free deterministic demo must appear.
    expect(note).toContain("Not the free deterministic demo");
    expect(note).not.toContain("Deterministic demo — no API keys, no cost.");
    expect(note).not.toContain("no API keys, no cost");
  });

  it("live available -> names the cloud AI provider, never a deterministic claim", () => {
    const note = demoCtaNote(
      caps({
        modes: [
          { id: "demo", available: true },
          { id: "live", available: true, label: "Cloud AI" },
        ],
      }),
    );
    expect(note).toContain("runs the cloud AI provider");
    expect(note).toContain("Cloud AI");
    expect(note).toContain("Not the free deterministic demo");
    expect(note).not.toContain("Deterministic demo — no API keys, no cost.");
    expect(note).not.toContain("no cost");
  });

  it("unknown DTO -> provider-neutral note with NO deterministic/no-cost promise", () => {
    const note = demoCtaNote(null);
    expect(note).not.toContain("Deterministic demo");
    expect(note).not.toContain("no API keys, no cost");
    expect(note).not.toContain("Demo Case");
  });

  it("hostile DTO material never reaches the note (frozen fallbacks only)", () => {
    const hostile = caps({
      modes: [
        { id: "local", available: true, label: "http://127.0.0.1:11434", model: "llama3@10.0.0.7" },
      ],
    });
    const note = demoCtaNote(hostile);
    expect(note).not.toContain("127.0.0.1");
    expect(note).not.toContain("11434");
    expect(note).not.toContain("@");
    expect(note).toContain("Local AI");
  });
});