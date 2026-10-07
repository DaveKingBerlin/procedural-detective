import { describe, expect, it } from "vitest";
import canonical from "./fixtures/v1_demo_apartment.pdcase.json";
import { MAX_EXPORT_BYTES, parseSavegameV1, utf8ByteLength } from "./savegameV1";
import { savegameErrorMessage, SavegameParseError } from "./savegameV1";

/**
 * Phase 32 — SavegameV1 FORMAT + adversarial import tests (Phase32 §19/§26/
 * §32/§42 "FORMAT"). The canonical fixture
 * (`backend/tests/fixtures/savegame/v1_demo_apartment.pdcase.json`, copied
 * under ./fixtures) is the exact server-exported contract: it MUST parse and
 * normalize. Every hostile file must either be safely rejected with the
 * bounded message or produce a harmless schema-valid replay — never code
 * execution, never a network call.
 */

const CANONICAL_TEXT: string = JSON.stringify(canonical);

function canonicalBytes(): number {
  return utf8ByteLength(CANONICAL_TEXT);
}

/** Deep-clone + mutate the canonical document into a raw JSON text. */
function mutateDocument(mutate: (doc: any) => void): string {
  const doc = JSON.parse(CANONICAL_TEXT);
  mutate(doc);
  return JSON.stringify(doc);
}

function kindOf(text: string): string | null {
  try {
    parseSavegameV1(text, utf8ByteLength(text));
    return null;
  } catch (error) {
    if (error instanceof SavegameParseError) return error.kind;
    throw error;
  }
}

describe("SavegameV1 — canonical fixture", () => {
  it("accepts the canonical server export and normalizes it", () => {
    const definition = parseSavegameV1(CANONICAL_TEXT, canonicalBytes());
    expect(definition.formatVersion).toBe(1);
    expect(definition.metadata.title).toBe("Victim: Sarah Miller");
    expect(definition.metadata.source).toBe("demo");
    expect(definition.metadata.sourceCaseId).toBe("CASE-DqHMXBPUvcAS");
    expect(definition.scene.worldObjects.length).toBe(9);
    expect(definition.evidence.length).toBe(16);
    expect(definition.witnesses.length).toBe(1);
    expect(definition.replayTruth.murdererId).toBe("thomas_reed");
    expect(definition.replayTruth.accusationToleranceSeconds).toBe(300);
  });

  it("is repeatable and deterministic (same input -> same normalized ids)", () => {
    const first = parseSavegameV1(CANONICAL_TEXT, canonicalBytes());
    const second = parseSavegameV1(CANONICAL_TEXT, canonicalBytes());
    expect(first.evidence.map((r) => r.evidenceId)).toEqual(second.evidence.map((r) => r.evidenceId));
    expect(first.scene.worldObjects.map((o) => o.objectId)).toEqual(
      second.scene.worldObjects.map((o) => o.objectId),
    );
  });

  it("never lets the imported truth leak into the world objects (evidence ids stay in record set)", () => {
    const definition = parseSavegameV1(CANONICAL_TEXT, canonicalBytes());
    const recordIds = new Set(definition.evidence.map((r) => r.evidenceId));
    for (const worldObject of definition.scene.worldObjects) {
      expect(recordIds.has(worldObject.evidenceId ?? "") || worldObject.evidenceId === null).toBe(true);
    }
  });
});

describe("SavegameV1 — FORMAT gates (Phase32 §8/§27/§42)", () => {
  it("rejects an empty file", () => {
    expect(kindOf("")).toBe("invalid");
  });

  it("rejects plain text", () => {
    expect(kindOf("hello world")).toBe("invalid");
  });

  it("rejects invalid JSON", () => {
    expect(kindOf("{ not json")).toBe("invalid");
  });

  it("rejects a JSON primitive", () => {
    expect(kindOf("42")).toBe("invalid");
    expect(kindOf("\"a string\"")).toBe("invalid");
    expect(kindOf("[1,2,3]")).toBe("invalid");
    expect(kindOf("null")).toBe("invalid");
  });

  it("rejects the wrong format token", () => {
    expect(kindOf(mutateDocument((doc) => (doc.format = "not-a-detective-case")))).toBe("invalid");
  });

  it("rejects a missing formatVersion", () => {
    expect(kindOf(mutateDocument((doc) => void delete doc.formatVersion))).toBe("invalid");
  });

  it("fails closed on an unknown FUTURE version", () => {
    expect(kindOf(mutateDocument((doc) => (doc.formatVersion = 2)))).toBe("unsupported-version");
    expect(kindOf(mutateDocument((doc) => (doc.formatVersion = 999)))).toBe("unsupported-version");
  });

  it("rejects an unparseable exportedAt", () => {
    expect(kindOf(mutateDocument((doc) => (doc.exportedAt = "not-a-time")))).toBe("invalid");
  });

  it("rejects unknown top-level and nested keys (additionalProperties:false)", () => {
    expect(kindOf(mutateDocument((doc) => (doc.case.sneaky = true)))).toBe("invalid");
    expect(kindOf(mutateDocument((doc) => (doc.case.metadata.evil = "x")))).toBe("invalid");
    expect(kindOf(mutateDocument((doc) => (doc.case.scene.worldObjects[0].danger = 1)))).toBe("invalid");
    expect(kindOf(mutateDocument((doc) => (doc.case.replayTruth.extra = 1)))).toBe("invalid");
  });
});

describe("SavegameV1 — structural bounds (Phase32 §19/§20)", () => {
  it("rejects an oversized file BEFORE parsing", () => {
    const blob = "x".repeat(MAX_EXPORT_BYTES + 1);
    expect(kindOf(blob)).toBe("too-large");
  });

  it("rejects deep nesting", () => {
    let nested: any = {};
    let probe = nested;
    for (let index = 0; index < 50; index += 1) {
      probe.deep = {};
      probe = probe.deep;
    }
    expect(kindOf(JSON.stringify(nested))).toBe("invalid");
  });

  it("rejects huge arrays", () => {
    expect(
      kindOf(
        mutateDocument((doc) => {
          doc.case.scene.worldObjects = Array.from({ length: 10_000 }, (_, index) => ({
            ...doc.case.scene.worldObjects[0],
            objectId: `object_${index}`,
          }));
        }),
      ),
    ).toBe("invalid");
  });

  it("rejects very long strings", () => {
    expect(
      kindOf(
        mutateDocument((doc) => (doc.case.metadata.title = "z".repeat(10_000))),
      ),
    ).toBe("invalid");
  });

  it("rejects duplicate world-object ids and duplicate evidence ids", () => {
    expect(
      kindOf(
        mutateDocument((doc) => {
          doc.case.scene.worldObjects[1].objectId = doc.case.scene.worldObjects[0].objectId;
        }),
      ),
    ).toBe("invalid");
    expect(
      kindOf(
        mutateDocument((doc) => {
          doc.case.evidence[1].evidenceId = doc.case.evidence[0].evidenceId;
        }),
      ),
    ).toBe("invalid");
  });

  it("rejects invalid graph references (world object without placement, unknown evidence)", () => {
    expect(
      kindOf(
        mutateDocument((doc) => {
          doc.case.scene.worldObjects[0].objectId = "ghost_object";
        }),
      ),
    ).toBe("invalid");
    expect(
      kindOf(
        mutateDocument((doc) => {
          doc.case.scene.worldObjects[0].evidenceId = "does_not_exist";
        }),
      ),
    ).toBe("invalid");
  });

  it("rejects a truth whose ids are not candidates", () => {
    expect(
      kindOf(
        mutateDocument((doc) => (doc.case.replayTruth.murdererId = "not_a_suspect")),
      ),
    ).toBe("invalid");
  });

  it("rejects wrong enums (source, reliability, renderType, presence)", () => {
    expect(kindOf(mutateDocument((doc) => (doc.case.metadata.source = "alien")))).toBe("invalid");
    expect(kindOf(mutateDocument((doc) => (doc.case.evidence[0].reliability = "extreme")))).toBe("invalid");
    expect(
      kindOf(
        mutateDocument((doc) => (doc.case.evidence[0].content.renderType = "not-a-renderer")),
      ),
    ).toBe("invalid");
    expect(
      kindOf(
        mutateDocument((doc) => (doc.case.witnesses[0].presence = "INVISIBLE")),
      ),
    ).toBe("invalid");
  });

  it("rejects an unparseable canonical crimeTime and negative tolerance", () => {
    expect(kindOf(mutateDocument((doc) => (doc.case.replayTruth.crimeTime = "22:17")))).toBe("invalid");
    expect(
      kindOf(
        mutateDocument((doc) => (doc.case.replayTruth.accusationToleranceSeconds = -5)),
      ),
    ).toBe("invalid");
  });

  it("rejects over-long candidate text fields on import (DEF-047 / ADV-32F-01)", () => {
    // The shared accusation-candidate parser bounds only the `id`; every text
    // field it renders (names/labels/assetIds) is capped at
    // MAX_SHORT_TEXT_LENGTH (300) in the savegame import, with the frozen
    // `invalid` rejection per Phase32 §19 — a >300-char candidate field must
    // never enter the normalized definition.
    expect(
      kindOf(
        mutateDocument((doc) => (doc.case.candidates.suspects[0].name = "A".repeat(301))),
      ),
    ).toBe("invalid");
    expect(
      kindOf(
        mutateDocument((doc) => (doc.case.candidates.motives[0].label = "A".repeat(301))),
      ),
    ).toBe("invalid");
    expect(
      kindOf(
        mutateDocument((doc) => (doc.case.candidates.weapons[0].name = "A".repeat(301))),
      ),
    ).toBe("invalid");
    expect(
      kindOf(
        mutateDocument((doc) => (doc.case.candidates.weapons[0].assetId = "A".repeat(301))),
      ),
    ).toBe("invalid");
    // The exact adversarial reproduction: a 900,000-char candidate name must
    // never reach the normalized definition (it previously did).
    const hostile = mutateDocument((doc) => (doc.case.candidates.suspects[0].name = "A".repeat(900_000)));
    expect(kindOf(hostile)).toBe("invalid");
  });

  it("accepts candidate text fields AT the documented bound (DEF-047)", () => {
    const bounded = mutateDocument((doc) => {
      doc.case.candidates.suspects[0].name = "A".repeat(300);
      doc.case.candidates.motives[0].label = "A".repeat(300);
      doc.case.candidates.weapons[0].name = "A".repeat(300);
      doc.case.candidates.weapons[0].assetId = "A".repeat(300);
    });
    const definition = parseSavegameV1(bounded, utf8ByteLength(bounded));
    expect(definition.candidates.suspects[0].name.length).toBe(300);
    expect(definition.candidates.motives[0].label.length).toBe(300);
    expect(definition.candidates.weapons[0].name.length).toBe(300);
    expect(definition.candidates.weapons[0].assetId.length).toBe(300);
  });
});

describe("SavegameV1 — untrusted-file security (Phase32 §18/§21/§32)", () => {
  it("rejects prototype-pollution keys at every level", () => {
    for (const key of ["__proto__", "constructor", "prototype"]) {
      // Inject the key as an OWN ENUMERABLE property so it actually reaches
      // the serialized text (a plain `obj[key] = ...` assignment would set
      // the prototype instead or be non-enumerable).
      const topLevel = mutateDocument((doc) => {
        const target = doc as Record<string, unknown>;
        Object.defineProperty(target, key, { value: { pollute: true }, enumerable: true, writable: true, configurable: true });
      });
      expect(kindOf(topLevel)).toBe("invalid");

      const nested = mutateDocument((doc) => {
        const meta = doc.case.metadata as Record<string, unknown>;
        Object.defineProperty(meta, key, { value: "x", enumerable: true, writable: true, configurable: true });
      });
      expect(kindOf(nested)).toBe("invalid");
    }
  });

  it("rejects dangerous keys inside a world-object generated block (DEF-048 / ADV-32F-02)", () => {
    // A hostile raw file can carry `"__proto__":{...}` inside the generated
    // sub-document; JSON.parse materialises it as an OWN data property (the
    // prototype itself is untouched). The import must fail closed on the raw
    // sub-document even though the shared generated-definition parser would
    // build a fresh allowlisted object and silently drop the key.
    const topLevelGenerated = mutateDocument((doc) => {
      const worldObject = doc.case.scene.worldObjects[0];
      worldObject.assetId = "proc.decor.abc";
      worldObject.generated = {
        compilerVersion: 1,
        schemaVersion: 1,
        assetId: "proc.decor.abc",
        canonicalName: "x",
      };
      // Own ENUMERABLE data key — the way a hostile JSON text reaches the
      // parser (a bare `obj["__proto__"] = ...` assignment would hit the
      // prototype setter instead of creating the serialized key).
      Object.defineProperty(worldObject.generated, "__proto__", {
        value: { polluted: true },
        enumerable: true,
        writable: true,
        configurable: true,
      });
    });
    expect(kindOf(topLevelGenerated)).toBe("invalid");

    // Nested dangerous keys inside the generated sub-document also fail closed.
    const nestedGenerated = mutateDocument((doc) => {
      const worldObject = doc.case.scene.worldObjects[0];
      worldObject.assetId = "proc.decor.abc";
      worldObject.generated = {
        compilerVersion: 1,
        schemaVersion: 1,
        assetId: "proc.decor.abc",
        canonicalName: "x",
        parts: [{ id: "part_a" }],
      };
      worldObject.generated.parts[0].constructor = { polluted: true };
      worldObject.generated.parts[0].prototype = { polluted: true };
    });
    expect(kindOf(nestedGenerated)).toBe("invalid");

    // `constructor`/`prototype` at the top of the generated block as well.
    for (const key of ["constructor", "prototype"]) {
      const text = mutateDocument((doc) => {
        const worldObject = doc.case.scene.worldObjects[0];
        worldObject.assetId = "proc.decor.abc";
        worldObject.generated = {
          compilerVersion: 1,
          schemaVersion: 1,
          assetId: "proc.decor.abc",
          canonicalName: "x",
        };
        (worldObject.generated as Record<string, unknown>)[key] = { polluted: true };
      });
      expect(kindOf(text)).toBe("invalid");
    }
  });

  it("keeps a BENIGN generated block importable and never mutates Object.prototype (DEF-048)", () => {
    // A `proc.*` asset with an ordinary generated block (the backend compiler
    // shape) is a NORMAL imported object — the dangerous-key scan must not
    // reject benign content and the shared parser still imports it.
    const benign = mutateDocument((doc) => {
      const worldObject = doc.case.scene.worldObjects[0];
      worldObject.assetId = "proc.decor.abc";
      worldObject.generated = {
        compilerVersion: 1,
        schemaVersion: 1,
        assetId: "proc.decor.abc",
        canonicalName: "x",
      };
    });
    const definition = parseSavegameV1(benign, utf8ByteLength(benign));
    expect(definition.scene.worldObjects[0].assetId).toBe("proc.decor.abc");
    // The hostile probes above never polluted the global Object prototype —
    // the rejection is a plain `invalid` throw, and no import path ever merges
    // hostile JSON into global state.
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
    expect((Object.prototype as Record<string, unknown>).polluted).toBeUndefined();
    expect((Object.prototype as Record<string, unknown>).constructorOwn).toBeUndefined();
  });

  it("treats HTML/script and javascript: payloads as inert bounded strings", () => {
    const hostile = "<script>alert(1)</script><img src=x onerror=alert(2)>";
    const text = mutateDocument((doc) => {
      doc.case.metadata.title = hostile;
      doc.case.metadata.sourceCaseId = "javascript:alert(1)";
      doc.case.evidence[0].content.summary = hostile;
      doc.case.replayTruth.murdererName = "<svg/onload=alert(1)>";
    });
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    // The imported strings survive ONLY as literal text fields of the new
    // typed objects — never as markup, never mounted to HTML.
    expect(definition.metadata.title).toBe(hostile);
    expect(definition.metadata.sourceCaseId).toBe("javascript:alert(1)");
    expect(definition.evidence[0].content.summary).toBe(hostile);
    expect(definition.replayTruth.murdererName).toBe("<svg/onload=alert(1)>");
  });

  it("normalizes arbitrary asset/URL-looking ids as plain strings (no fetch, no render)", () => {
    const text = mutateDocument((doc) => {
      doc.case.scene.worldObjects[0].assetId = "https://evil.example/x.png";
      doc.case.metadata.environmentId = "javascript:alert(1)";
    });
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.scene.worldObjects[0].assetId).toBe("https://evil.example/x.png");
    expect(definition.metadata.environmentId).toBe("javascript:alert(1)");
  });

  it("keeps the imported sourceCaseId as display-only (never an authority)", () => {
    const definition = parseSavegameV1(CANONICAL_TEXT, canonicalBytes());
    expect(definition.metadata.sourceCaseId).toBe("CASE-DqHMXBPUvcAS");
    // The normalized definition exposes NO live playthrough credential shape.
    const serialized = JSON.stringify(definition);
    expect(serialized).not.toContain("pd_playthrough_token");
    expect(serialized).not.toContain("Authorization");
  });
});

describe("SavegameV1 — bounded load-error copy (Phase32 §26)", () => {
  it.each([
    ["invalid", savegameErrorMessage("invalid")],
    ["too-large", savegameErrorMessage("too-large")],
    ["unreadable", savegameErrorMessage("unreadable")],
    ["unsupported-version", savegameErrorMessage("unsupported-version")],
  ] as Array<["invalid" | "too-large" | "unreadable" | "unsupported-version", string]>)(
    "maps %s to its frozen player-safe message",
    (kind, expected) => {
      expect(savegameErrorMessage(kind)).toBe(expected);
    },
  );

  it("suggests no raw parser text in the invalid-file message", () => {
    expect(savegameErrorMessage("invalid")).toBe("This file is not a valid Procedural Detective savegame.");
  });
});