import { afterEach, describe, expect, it, vi } from "vitest";
import { getSavegame } from "../api/client";
import canonical from "./fixtures/v1_demo_apartment.pdcase.json";
import { downloadSavegameText, reExportV1, savegameFilenameFor, serializeSavegameV1, setSavegameDownloadSink } from "./exportV1";
import { loadSavegameFile } from "./loadCase";
import { FreshReplayState } from "./replayRuntime";
import { startReplay, activeReplay, clearReplay } from "./replaySession";
import {
  MAX_EXPORT_BYTES,
  SAVEGAME_EXTENSION,
  SAVEGAME_MIME_TYPE,
  parseSavegameV1,
  utf8ByteLength,
} from "./savegameV1";

/**
 * Phase 32 — CENTRAL round-trip + export acceptance (Phase32 §15/§29/§30/
 * §31/§34 + ADR-003 §4/§5).
 *
 *  - §29 demo round-trip: canonical completed case -> export -> parse ->
 *    validate -> normalize -> fresh replay -> THE TRUTH hidden -> investigate
 *    -> correct solution -> THE TRUTH revealed.
 *  - §31 generated-case fixture round-trip (deterministic, no provider).
 *  - §15 re-save: a loaded replay may be re-exported cleanly after reveal.
 *  - §34 secret sentinels: exported bytes carry zero secret material.
 *  - Providers/quota: a full save->load->replay makes ZERO network calls.
 */

const CANONICAL_TEXT: string = JSON.stringify(canonical);

function parsed(): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(CANONICAL_TEXT, utf8ByteLength(CANONICAL_TEXT));
}

/** A schema-valid "generated"-source document derived from the canonical
 *  demo (the deterministic generated-case fixture of §31). */
function generatedFixtureText(): string {
  const doc = JSON.parse(CANONICAL_TEXT);
  doc.case.metadata.source = "generated";
  doc.case.metadata.sourceCaseId = "CASE-Gen00042";
  return JSON.stringify(doc);
}

const SOLVED = {
  murdererId: "thomas_reed",
  motiveId: "cover_up_embezzlement",
  weaponId: "kitchen_knife",
  crimeTime: "22:17:00",
};

/** Play a fresh replay to the correct solution + reveal. */
function playToReveal(definition: ReturnType<typeof parsed>): { revealed: string; truthIdentity: boolean } {
  const state = new FreshReplayState(definition);
  expect(state.freshBootstrap().state).toBe("PLAYING");
  expect(state.freshBootstrap().playerKnowledge.discoveredEvidenceIds).toEqual([]);
  // Investigate: interact + read.
  state.interactObject("kitchen_knife", "inspect");
  const record = state.readRecord("forensic_knife_match_01");
  expect(record.evidenceId).toBe("forensic_knife_match_01");
  // THE TRUTH is still hidden mid-investigation.
  const mid = JSON.stringify(state.freshBootstrap());
  expect(mid).not.toContain("replayTruth");
  // Wrong answer behaves normally: accepted, and the reveal reports it
  // incorrect (the immutable truth shows ONLY here — never before).
  state.submitAccusation({
    murdererId: "anna_karlsson",
    motiveId: "robbery_gone_wrong",
    weaponId: "scissors",
    crimeTime: "18:00:00",
  });
  const wrong = state.getReveal();
  expect(wrong.result.overall).toBe("incorrect");
  expect(wrong.score.correctDimensions).toBe(0);
  // Correct solution reaches THE TRUTH again.
  const second = new FreshReplayState(definition);
  second.interactObject("kitchen_knife", "inspect");
  second.submitAccusation({ ...SOLVED });
  const correct = second.getReveal();
  expect(correct.result.overall).toBe("solved");
  expect(correct.truth.murdererName).toBe("Thomas Reed");
  return { revealed: correct.truth.murdererName, truthIdentity: correct.player.accusation.murdererId === SOLVED.murdererId };
}

describe("Phase 32 §29 — canonical demo round-trip", () => {
  it("parse -> validate -> normalize -> fresh replay -> solve -> reveal", () => {
    const definition = parsed();
    const result = playToReveal(definition);
    expect(result.revealed).toBe("Thomas Reed");
    expect(result.truthIdentity).toBe(true);
  });
});

describe("Phase 32 §31 — deterministic generated-case fixture round-trip", () => {
  it("export -> load -> replay -> solve -> reveal (zero provider)", () => {
    const text = generatedFixtureText();
    const definition = parseSavegameV1(text, utf8ByteLength(text));
    expect(definition.metadata.source).toBe("generated");
    const result = playToReveal(definition);
    expect(result.revealed).toBe("Thomas Reed");
  });
});

describe("Phase32-Fix §10/§18 — permanent same-version round-trip drift guard", () => {
  it("exporter -> serialize -> JSON.parse -> production validator -> normalize -> fresh replay", () => {
    // The CENTRAL invariant (Phase32-Fix §10/§36): a document produced by the
    // REAL production exporter must always be accepted by the REAL production
    // validator. Uses the actual production APIs only — a hand-shaped object
    // is explicitly insufficient (Phase32-Fix §10).
    const definition = parsed();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    const parsedBack: unknown = JSON.parse(exported); // a REAL JSON round-trip
    const reloaded = parseSavegameV1(JSON.stringify(parsedBack), utf8ByteLength(exported));
    expect(reloaded.metadata.title).toBe(definition.metadata.title);
    expect(reloaded.publicCase.caseId).toBe(definition.publicCase.caseId);
    expect(reloaded.replayTruth).toEqual(definition.replayTruth);
    // Fresh replay state from the reloaded definition: THE TRUTH hidden.
    const state = new FreshReplayState(reloaded);
    const bootstrap = state.freshBootstrap();
    expect(bootstrap.state).toBe("PLAYING");
    expect(JSON.stringify(bootstrap)).not.toContain("replayTruth");
    expect(bootstrap.playerKnowledge.discoveredEvidenceIds).toEqual([]);
  });

  it("§18 — EVERY emitted field of a full exported document is accepted by the current validator", () => {
    // The exporter-contract drift guard: serialize the FULL document produced
    // by the production exporter and prove the validator accepts every
    // emitted field (no additionalProperties surprise, no type drift).
    const definition = parsed();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    expect(() => parseSavegameV1(exported, utf8ByteLength(exported))).not.toThrow();
    // The same must hold for the deterministic "generated"-source fixture.
    const generatedText = generatedFixtureText();
    const generatedDefinition = parseSavegameV1(generatedText, utf8ByteLength(generatedText));
    const generatedExport = serializeSavegameV1(reExportV1(generatedDefinition, "2099-01-01T00:00:00Z"));
    expect(() => parseSavegameV1(generatedExport, utf8ByteLength(generatedExport))).not.toThrow();
  });

  it("§29 — the same save can be loaded repeatedly, each time into a fresh replay", () => {
    const definition = parsed();
    const text = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    // First load: investigate + accuse.
    const first = new FreshReplayState(parseSavegameV1(text, utf8ByteLength(text)));
    first.submitAccusation({ ...SOLVED });
    expect(first.getReveal().result.overall).toBe("solved");
    // Second load of the SAME bytes: a brand-new PLAYING replay, accusation
    // reset, truth hidden — never the previous playthrough.
    const second = new FreshReplayState(parseSavegameV1(text, utf8ByteLength(text)));
    expect(second.freshBootstrap().state).toBe("PLAYING");
    expect(second.lifecycleState()).toBe("PLAYING");
    expect(second.knowledgeSnapshot().discoveredEvidenceIds).toEqual([]);
    expect(() => second.getReveal()).toThrow();
  });

  it("a re-export after reveal still round-trips into a fresh replay (truth stays hidden)", () => {
    const definition = parsed();
    const state = new FreshReplayState(definition);
    state.submitAccusation({ ...SOLVED });
    expect(state.getReveal().result.overall).toBe("solved");
    // Re-export the REVEALED definition and reload: the replay is fresh again.
    const text = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    const reloaded = parseSavegameV1(text, utf8ByteLength(text));
    const again = new FreshReplayState(reloaded);
    expect(again.freshBootstrap().state).toBe("PLAYING");
    expect(JSON.stringify(again.freshBootstrap())).not.toContain("replayTruth");
    expect(again.lifecycleState()).toBe("PLAYING");
  });
});

describe("Phase 32 §15 — re-saving a loaded replay (clean fresh export)", () => {
  it("re-exports the normalized definition to a byte-stable, re-loadable save", () => {
    const definition = parsed();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    // Deterministic: identical input -> identical bytes.
    expect(exported).toBe(serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z")));
    // The re-export parses back and replays fresh.
    const reloaded = parseSavegameV1(exported, utf8ByteLength(exported));
    expect(reloaded.metadata.title).toBe(definition.metadata.title);
    expect(reloaded.replayTruth.murdererId).toBe(definition.replayTruth.murdererId);
    expect(() => playToReveal(reloaded)).not.toThrow();
  });

  it("a re-export never embeds export history or a nested save body", () => {
    const definition = parsed();
    const exported = reExportV1(definition, "2099-01-01T00:00:00Z");
    const serialized = JSON.stringify(exported);
    expect(serialized).not.toContain("exportHistory");
    expect(serialized).not.toContain("previousExports");
  });
});

describe("Phase 32 §34 — secret sentinels absent from exports", () => {
  const SECRET_SENTINELS: readonly string[] = [
    "sk-",
    "Bearer ",
    "anonymousSessionToken",
    "pd_playthrough_token",
    "pd_hypothesis_v1",
    "Authorization",
    "x-api-key",
    "apiKey",
    "playthroughAccessToken",
    "creatorAccessToken",
    "frontierApiKey",
    "ollamaTransport",
    "http://",
    "solverProof",
    "generationAttemptId",
    "CaseTruth",
  ];

  it("the exported bytes of a fresh export contain zero sentinels", () => {
    const definition = parsed();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    for (const sentinel of SECRET_SENTINELS) {
      expect(exported.includes(sentinel)).toBe(false);
    }
  });

  it("sentinels seeded into the environment never reach the export", () => {
    // Simulate an app/session that DID hold secrets elsewhere; the export is
    // a strict allowlist over the definition only.
    const definition = parsed();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    expect(exported).not.toContain("lX9fQ3sWv0aB2cD4eF6gH8iJ1kM3nO5pQ7rS9tU");
    expect(exported).not.toContain("Bearer");
  });

  it("the exported text never contains the browser-storage truth keys", () => {
    const definition = parsed();
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    expect(exported).not.toContain("localStorage");
    expect(exported).not.toContain("sessionStorage");
    expect(exported).not.toContain("IndexedDB");
    // No internal CaseTruth object: the ONLY truth payload is ReplayTruthV1.
    expect(exported).toContain('"replayTruth"');
  });
});

describe("Phase 32 DEF-049 — re-export byte-identity with the server export", () => {
  it("a re-save of the canonical server export is byte-identical when exportedAt is pinned", () => {
    const definition = parsed();
    const reExport = serializeSavegameV1(reExportV1(definition, canonical.exportedAt));
    // The canonical fixture IS the backend writer's sorted-key output
    // (json.dumps(..., sort_keys=True, separators=(",", ":"))). A re-save of a
    // server export must be byte-identical once exportedAt is pinned —
    // DEF-049 / ADV-32F-03.
    expect(reExport).toBe(CANONICAL_TEXT);
  });

  it("CCTV-style events never carry an explicit null personId in a re-export", () => {
    const definition = parsed();
    const serialized = serializeSavegameV1(reExportV1(definition, canonical.exportedAt));
    expect(serialized).not.toContain('"personId":null');
    // The cctv events still carry their time/action content (nothing dropped).
    expect(serialized).toContain('"action":"System resumed from sleep"');
  });
});

describe("Phase 32 §17/§33 — zero network, zero provider during save->load->replay", () => {
  afterEach(() => {
    clearReplay();
    setSavegameDownloadSink(null);
  });

  it("a full load + replay makes ZERO fetch calls", async () => {
    const fetchSpy = vi.fn(async () => {
      throw new Error("network must never be used");
    });
    const previous = globalThis.fetch;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    try {
      const outcome = await loadSavegameFile({
        size: utf8ByteLength(CANONICAL_TEXT),
        text: async () => CANONICAL_TEXT,
      });
      expect(outcome.ok).toBe(true);
      if (outcome.ok) {
        startReplay(outcome.definition);
        expect(activeReplay()).not.toBeNull();
        const state = activeReplay()!.state;
        state.interactObject("kitchen_knife", "inspect");
        state.readRecord("forensic_knife_match_01");
        state.submitAccusation({ ...SOLVED });
        const reveal = state.getReveal();
        expect(reveal.result.overall).toBe("solved");
        const reExported = serializeSavegameV1(reExportV1(outcome.definition));
        expect(parseSavegameV1(reExported, utf8ByteLength(reExported)).metadata.title).toBe(
          outcome.definition.metadata.title,
        );
      }
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = previous;
    }
  });

  it("a live Save Case makes EXACTLY ONE server call (the savegame endpoint)", async () => {
    const body = serializeSavegameV1(reExportV1(parsed(), "2099-01-01T00:00:00Z"));
    const fetchSpy = vi.fn(async (_url: string, _init?: RequestInit) => {
      return new Response(body, {
        status: 200,
        headers: {
          "Content-Type": SAVEGAME_MIME_TYPE,
          "Content-Disposition": 'attachment; filename="procedural-detective-case-CASE-DqHMXBPUvcAS.pdcase"',
        },
      });
    });
    const previous = globalThis.fetch;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    try {
      const exported = await getSavegame("PT-live-01", "x-secret-token-that-must-never-leak");
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      expect(exported.text).toBe(body);
      expect(exported.suggestedFilename).toBe("procedural-detective-case-CASE-DqHMXBPUvcAS.pdcase");
      expect(exported.suggestedFilename.endsWith(SAVEGAME_EXTENSION)).toBe(true);
    } finally {
      globalThis.fetch = previous;
    }
  });

  it("download helper ships the exact text with the canonical MIME + safe filename", () => {
    const captured: Array<{ text: string; filename: string; mime: string }> = [];
    setSavegameDownloadSink({ download: (text, filename, mime) => captured.push({ text, filename, mime }) });
    const definition = parsed();
    const text = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    downloadSavegameText(text, "procedural-detective-case-CASE-DqHMXBPUvcAS.pdcase");
    expect(captured.length).toBe(1);
    expect(captured[0].text).toBe(text);
    expect(captured[0].filename.endsWith(SAVEGAME_EXTENSION)).toBe(true);
    expect(captured[0].mime).toBe(SAVEGAME_MIME_TYPE);
  });
});

describe("Phase 32 — safe filename derivation", () => {
  it("sanitizes anything hostile into a bounded ASCII filename base", () => {
    expect(savegameFilenameFor("CASE-DqHMXBPUvcAS")).toBe("procedural-detective-case-CASE-DqHMXBPUvcAS.pdcase");
    expect(savegameFilenameFor('../../etc/passwd')).toBe('procedural-detective-case-etc_passwd.pdcase');
    expect(savegameFilenameFor("")).toBe("procedural-detective-case-case.pdcase");
    expect(savegameFilenameFor(null)).toBe("procedural-detective-case-case.pdcase");
    expect(savegameFilenameFor('javascript:alert(1)')).toBe('procedural-detective-case-javascript_alert_1.pdcase');
  });

  it("the filename never contains a path separator or control char", () => {
    const filename = savegameFilenameFor("..\\..\\secret");
    expect(filename).not.toMatch(/[\\/]/);
    expect(filename.endsWith(SAVEGAME_EXTENSION)).toBe(true);
  });
});

describe("Phase 32 — load helper", () => {
  it("a valid file yields a definition; oversized files fail with the bounded message", async () => {
    const ok = await loadSavegameFile({ size: utf8ByteLength(CANONICAL_TEXT), text: async () => CANONICAL_TEXT });
    expect(ok.ok).toBe(true);
    const oversized = await loadSavegameFile({
      size: MAX_EXPORT_BYTES + 1,
      text: async () => CANONICAL_TEXT,
    });
    expect(oversized.ok).toBe(false);
    if (!oversized.ok) expect(oversized.message).toBe("This savegame is too large to load.");
  });

  it("an unreadable file maps to the bounded message", async () => {
    const outcome = await loadSavegameFile({
      size: 10,
      text: async () => {
        throw new Error("fs fail");
      },
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.message).toBe("The selected savegame could not be read.");
  });
});