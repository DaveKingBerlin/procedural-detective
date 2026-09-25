// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import type { EvidenceReadResultDTO } from "../api/types";
import {
  EVIDENCE_RENDER_TYPES,
  RENDERER_BY_TYPE,
  evidenceRendererFor,
  resolveEvidenceRenderer,
} from "./evidenceContent";
import EvidencePanel from "./evidencePanel";
import ActivityLogEvidence from "./renderers/ActivityLogEvidence";
import GenericEvidence from "./renderers/GenericEvidence";
import { compactTimeOf, timeEntryItems } from "./renderers/shared";

// React 19 act() support in the jsdom test environment.
declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Phase 19G — Rich Evidence Rendering & player-readable time clues.
 *
 * Coverage:
 *  - ACTIVITY_LOG: every entry shows a visible time + its text, is sorted
 *    chronologically (defensively) and uses accessible table semantics with a
 *    real <time> element associated to each entry;
 *  - unknown/missing renderType -> GenericEvidence (DTO title + description),
 *    never a crash;
 *  - MESSAGE / DOCUMENT / BODY_OBSERVATION / FORENSIC_COMPARISON / TIMELINE
 *    render their allowlisted fields as TEXT;
 *  - hostile payloads (javascript: renderType, HTML-looking entries, <script>
 *    titles) render INERT — React escapes, no element/script can form;
 *  - the closed dispatch is an explicit literal keyed map and never looks up
 *    a dynamic component name (hostile renderType falls back);
 *  - reload determinism: the same DTO renders identical panel content;
 *  - the §16 laptop invariant: concrete timestamped entries are visible —
 *    never only generic "around the locked time" wording.
 */

function recordWith(
  content: Record<string, unknown>,
  overrides: Partial<EvidenceReadResultDTO> = {},
): EvidenceReadResultDTO {
  return {
    evidenceId: "ev-render-01",
    kind: "unknown",
    title: "Evidence title",
    description: "Evidence description.",
    openedAt: "2026-09-11T22:20:00+02:00",
    readByPlayer: true,
    content,
    ...overrides,
  };
}

describe("closed dispatch (evidenceContent) — Phase 19G §13", () => {
  it("RENDERER_BY_TYPE is an explicit literal object keyed EXACTLY by the closed tokens", () => {
    expect(Object.keys(RENDERER_BY_TYPE).sort()).toEqual([...EVIDENCE_RENDER_TYPES].sort());
    expect(RENDERER_BY_TYPE["ACTIVITY_LOG"]).toBe(ActivityLogEvidence);
    expect(RENDERER_BY_TYPE["GENERIC_TEXT"]).toBe(GenericEvidence);
    for (const key of Object.keys(RENDERER_BY_TYPE)) {
      expect(typeof RENDERER_BY_TYPE[key]).toBe("function");
    }
    expect(Object.isFrozen(RENDERER_BY_TYPE)).toBe(true);
  });

  it("hostile/unknown/missing render types resolve to GenericEvidence — NEVER a provider-named component", () => {
    for (const hostile of [
      "javascript:alert(1)",
      "ActivityLogEvidence",
      "data:text/html,<script>alert(1)</script>",
      "__proto__",
      "constructor",
      42,
      null,
      undefined,
      {},
      [],
    ]) {
      expect(resolveEvidenceRenderer(hostile)).toBe(GenericEvidence);
    }
    expect(resolveEvidenceRenderer("ACTIVITY_LOG")).toBe(ActivityLogEvidence);
    expect(resolveEvidenceRenderer("TIMELINE")).not.toBe(GenericEvidence);
  });

  it("absence of renderType keeps the legacy kind-based rows path (no dispatch)", () => {
    const record = recordWith({ fromPersonId: "thomas_reed", subject: "Hi" }, { kind: "email" });
    expect(evidenceRendererFor(record)).toBeNull();
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("From");
    expect(html).toContain("thomas_reed");
    expect(html).toContain("Evidence description.");
  });

  it("an unknown renderType still dispatches — to GenericEvidence (title + description, no crash)", () => {
    const content: Record<string, unknown> = { renderType: "COFFEE_GROUNDS" };
    const record = recordWith(content);
    expect(evidenceRendererFor(record)).toBe(GenericEvidence);
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("Evidence title");
    expect(html).toContain("Evidence description.");
    expect(html).not.toContain("COFFEE_GROUNDS");
  });

  it("a NON-string renderType is treated like absence (never a crash)", () => {
    const content: Record<string, unknown> = { renderType: { dynamic: true } };
    const record = recordWith(content, { kind: "email" });
    expect(evidenceRendererFor(record)).toBeNull();
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("Evidence description.");
  });
});

describe("ACTIVITY_LOG renderer — Phase 19G §7/§16", () => {
  function activityLog(entries: unknown[]): EvidenceReadResultDTO {
    return recordWith({
      renderType: "ACTIVITY_LOG",
      title: "Activity logged at the scene",
      entries,
    });
  }

  it("renders every entry with its visible time + text, chronologically, in an accessible table", () => {
    const record = activityLog([
      { time: "22:17", text: "Activity recorded near the scene" },
      { time: "22:19", text: "Session ended" },
      { time: "22:11", text: "User login detected" },
      { time: "22:14", text: "Research file opened" },
    ]);
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);

    // All four entries are visible with their timestamps.
    expect(html).toContain("22:11");
    expect(html).toContain("User login detected");
    expect(html).toContain("22:14");
    expect(html).toContain("Research file opened");
    expect(html).toContain("22:17");
    expect(html).toContain("Activity recorded near the scene");
    expect(html).toContain("22:19");
    expect(html).toContain("Session ended");

    // Chronological order in the rendered markup (server order was shuffled).
    const positions = ["22:11", "22:14", "22:17", "22:19"].map((time) => html.indexOf(time));
    expect(positions[0]).toBeGreaterThan(-1);
    for (let i = 1; i < positions.length; i += 1) {
      expect(positions[i]).toBeGreaterThan(positions[i - 1]);
    }

    // Compact structured view: "Activity log" heading + accessible two-column
    // table with the time associated to each entry (<time> + table semantics).
    expect(html).toContain("Activity log");
    expect(html).toContain('<h4 class="evidence-block-title">Activity log</h4>');
    expect(html).toContain('<table class="evidence-table evidence-activity-log">');
    expect(html).toContain('<th scope="col">Time</th>');
    expect(html).toContain('<th scope="col">Activity</th>');
    expect(html).toContain('<time dateTime="22:11">22:11</time>');
    expect(html).toContain('<time dateTime="22:17">22:17</time>');
    expect(html).toContain("</tbody>");
    expect(html).toContain('aria-label="Activity log"');

    // No raw JSON: payload keys/braces never reach the DOM.
    expect(html).not.toContain('"renderType"');
    expect(html).not.toContain('"entries"');
    expect(html).not.toContain("{");

    // The panel's existing dialog/close surface is preserved.
    expect(html).toContain('role="dialog"');
    expect(html).toContain('data-testid="evidence-close"');
  });

  it("PROOF — the laptop-style activity log shows CONCRETE timestamped entries, never only 'around the locked time'", () => {
    const record = recordWith(
      {
        renderType: "ACTIVITY_LOG",
        title: "Activity logged at the scene",
        summary: "A monitoring log records activity around the locked time.",
        entries: [
          { time: "22:11", text: "User login detected" },
          { time: "22:17", text: "Activity recorded near the scene" },
        ],
      },
      { title: "Activity logged at the scene" },
    );
    const html = renderToStaticMarkup(
      <EvidencePanel record={record} onClose={() => {}} objectLabel="Laptop" />,
    );

    // Panel title references the Laptop; the concrete time clue is VISIBLE —
    // the §16 acceptance gate (fails if only generic wording appeared).
    expect(html).toContain("Laptop");
    expect(html).toContain("22:11");
    expect(html).toContain("User login detected");
    expect(html).toContain("22:17");
    expect(html).toContain("Activity recorded near the scene");
    expect(html).not.toContain("around the locked time");
  });

  it("AN EMPTY/generic payload NEVER fabricates timestamps — graceful DTO fallback", () => {
    const record = recordWith(
      {
        renderType: "ACTIVITY_LOG",
        entries: [
          { time: "", text: "User login detected" }, // no concrete time -> dropped
          { time: "22:11", text: "" }, // no readable text -> dropped
          "not-an-entry",
          [],
          { time: "later", text: "" },
        ],
      },
      { title: "Activity logged at the scene" },
    );
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    // Nothing the server did not send: no <time> element, no "22:" anywhere.
    expect(html).not.toContain("<time");
    expect(html).not.toContain("22:");
    // Graceful fallback: the DTO title + description are present.
    expect(html).toContain("Activity logged at the scene");
    expect(html).toContain("Evidence description.");
  });

  it("sorts defensively and keeps equal/unparseable-time entries stable (determinism)", () => {
    const items = timeEntryItems(
      {
        entries: [
          { time: "later", text: "B" },
          { time: "22:11", text: "A" },
          { time: "later", text: "C" },
        ],
      },
      ["entries"],
      ["text"],
    );
    expect(items.map((entry) => entry.time)).toEqual(["22:11", "later", "later"]);
    expect(items.map((entry) => entry.text)).toEqual(["A", "B", "C"]);
  });
});

describe("other closed renderers — Phase 19G §8", () => {
  it("MESSAGE renders sender / recipients / subject / time / body as text", () => {
    const record = recordWith({
      renderType: "MESSAGE",
      fromPersonId: "sarah_miller",
      toPersonIds: ["thomas_reed", "michael_cole"],
      subject: "Weekend plans",
      timestamp: "2026-09-10T18:04:00+02:00",
      body: "Sarah, let us talk before the weekend.",
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("<dt>From</dt>");
    expect(html).toContain("sarah_miller");
    expect(html).toContain("<dt>To</dt>");
    expect(html).toContain("thomas_reed, michael_cole");
    expect(html).toContain("<dt>Subject</dt>");
    expect(html).toContain("Weekend plans");
    expect(html).toContain("<dt>Timestamp</dt>");
    // Phase 19H/DEF-103: the VISIBLE Timestamp value is the COMPACT local
    // clock; the canonical full ISO is preserved in the semantic <time dateTime>.
    expect(html).toContain('<time dateTime="2026-09-10T18:04:00+02:00">18:04</time>');
    expect(html).not.toContain(">2026-09-10T18:04:00+02:00<");
    expect(html).toContain("<dt>Body</dt>");
    expect(html).toContain("let us talk");
    expect(html).not.toContain('"fromPersonId"');
  });

  it("DEF-103 — MESSAGE Timestamp row shows the COMPACT local time (never the raw full ISO); the canonical value stays in the DTO", () => {
    const record = recordWith({
      renderType: "MESSAGE",
      fromPersonId: "thomas_reed",
      toPersonIds: ["sarah_miller"],
      subject: "Re: the weekend",
      timestamp: "2026-09-11T21:04:00+02:00",
      body: "See you at the station.",
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    // Player-facing text: compact HH:mm clock — the raw ISO is NEVER visible.
    expect(html).toContain(">21:04</time>");
    expect(html).not.toContain(">2026-09-11T21:04:00+02:00<");
    expect(html).not.toMatch(/>2026-09-11T21:04:00\+02:00</);
    // The canonical full ISO is preserved in the semantic <time dateTime> and
    // unchanged in the DTO (the renderer is a pure function — no mutation).
    expect(html).toContain('dateTime="2026-09-11T21:04:00+02:00"');
    expect(record.content.timestamp).toBe("2026-09-11T21:04:00+02:00");
  });

  it("DEF-103 — DOCUMENT has no raw-ISO surface: a timestamp-bearing document payload renders title+body only, never the full ISO", () => {
    const record = recordWith({
      renderType: "DOCUMENT",
      title: "Financial record",
      body: "Monthly statement.",
      timestamp: "2026-09-11T21:04:00+02:00",
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("Financial record");
    expect(html).toContain("Monthly statement.");
    // The DOCUMENT renderer has no bare Timestamp row, so the allowlisted
    // timestamp can never surface as raw ISO player text either.
    expect(html).not.toContain("Timestamp");
    expect(html).not.toContain("2026-09-11T21:04:00+02:00");
    expect(html).not.toContain("21:04");
    // The canonical value stays in the DTO.
    expect(record.content.timestamp).toBe("2026-09-11T21:04:00+02:00");
  });

  it("DOCUMENT renders title + body as plain text (no HTML injection)", () => {
    const record = recordWith({
      renderType: "DOCUMENT",
      title: "Will and testament",
      body: "I leave the apartment to my sister.\nSigned, T.",
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("Will and testament");
    expect(html).toContain("I leave the apartment to my sister.");
    expect(html).toContain("Signed, T.");
    expect(html).toContain("evidence-document-body");
  });

  it("BODY_OBSERVATION renders the observations as a labelled list", () => {
    const record = recordWith({
      renderType: "BODY_OBSERVATION",
      observations: [
        "A cut on the left forearm",
        { label: "Left wrist", text: "Bruising consistent with restraint" },
        { location: "Neck", observation: "Small puncture mark" },
      ],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain('<h4 class="evidence-block-title">Observations</h4>');
    expect(html).toContain("evidence-observations");
    expect(html).toContain("A cut on the left forearm");
    expect(html).toContain("Left wrist:");
    expect(html).toContain("Bruising consistent with restraint");
    expect(html).toContain("Neck:");
    expect(html).toContain("Small puncture mark");
  });

  it("BODY_OBSERVATION renders the allowlisted `statement` with the `speakerName` label — NOT the generic fallback", () => {
    const record = recordWith({
      renderType: "BODY_OBSERVATION",
      speakerName: "Thomas Reed",
      statement: "I saw the victim in the kitchen shortly before the noise.",
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain('aria-label="Body observations"');
    expect(html).toContain("Thomas Reed:");
    expect(html).toContain("I saw the victim in the kitchen shortly before the noise.");
    // The rich body rendered (statement + speaker label); the panel description
    // paragraph and the generic DTO-only section are not the body.
    expect(html).not.toContain('aria-label="Evidence content"');
    expect(html).not.toContain("Evidence description.");
  });

  it("BODY_OBSERVATION prefers a structured `observations[]` list over `statement`", () => {
    const record = recordWith({
      renderType: "BODY_OBSERVATION",
      speakerName: "Thomas Reed",
      statement: "A secondary statement that must NOT win.",
      observations: [
        { label: "Left wrist", text: "Bruising consistent with restraint" },
        "A cut on the left forearm",
      ],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("Bruising consistent with restraint");
    expect(html).toContain("A cut on the left forearm");
    expect(html).not.toContain("A secondary statement");
    expect(html).not.toContain("Thomas Reed:");
  });

  it("BODY_OBSERVATION falls through an EMPTY `observations[]` to `statement` (no fabricated items)", () => {
    const record = recordWith({
      renderType: "BODY_OBSERVATION",
      observations: [],
      speakerName: "Thomas Reed",
      statement: "I saw the victim in the kitchen shortly before the noise.",
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain('aria-label="Body observations"');
    expect(html).toContain("Thomas Reed:");
    expect(html).toContain("I saw the victim in the kitchen shortly before the noise.");
  });

  it("BODY_OBSERVATION falls back to `summary` then record title/description; fully empty stays safe", () => {
    // `content.summary` alone (the backend always emits it).
    const withSummary = recordWith({
      renderType: "BODY_OBSERVATION",
      summary: "A doctor note summarises the visible injuries.",
    });
    const html1 = renderToStaticMarkup(<EvidencePanel record={withSummary} onClose={() => {}} />);
    expect(html1).toContain('aria-label="Body observations"');
    expect(html1).toContain("A doctor note summarises the visible injuries.");

    // No observations/statement/summary -> the DTO title/description serve.
    const emptyPayload = recordWith({ renderType: "BODY_OBSERVATION" });
    const html2 = renderToStaticMarkup(<EvidencePanel record={emptyPayload} onClose={() => {}} />);
    expect(html2).toContain('aria-label="Body observations"');
    expect(html2).toContain("Evidence description.");

    // Nothing readable ANYWHERE -> graceful GenericEvidence fallback, no crash.
    const bare = recordWith({ renderType: "BODY_OBSERVATION" }, { title: "", description: null });
    const html3 = renderToStaticMarkup(<EvidencePanel record={bare} onClose={() => {}} />);
    expect(html3).toContain("evidence-generic");
    expect(html3).not.toContain("evidence-observations");
  });

  it("BODY_OBSERVATION keeps hostile statement/speaker text literal (React escapes)", () => {
    const record = recordWith({
      renderType: "BODY_OBSERVATION",
      speakerName: "<img src=x onerror=alert(1)>",
      statement: "<script>alert('body')</script> — ひらがな",
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&lt;img");
    expect(html).toContain("ひらがな");
  });

  it("FORENSIC_COMPARISON renders the comparison result text (payload or DTO)", () => {
    const record = recordWith({
      renderType: "FORENSIC_COMPARISON",
      result: "Traces on the kitchen knife match the metal of the letter opener.",
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("Forensic comparison");
    expect(html).toContain("Traces on the kitchen knife match the metal of the letter opener.");

    // Falls back to the DTO phrase when the payload result is empty.
    const fallback = recordWith(
      { renderType: "FORENSIC_COMPARISON", result: "" },
      { description: "The knife matches the letter opener." },
    );
    const html2 = renderToStaticMarkup(<EvidencePanel record={fallback} onClose={() => {}} />);
    expect(html2).toContain("The knife matches the letter opener.");
  });

  it("TIMELINE renders chronological entries with visible times", () => {
    const record = recordWith({
      renderType: "TIMELINE",
      events: [
        { time: "22:03", description: "The visitor leaves in a hurry" },
        { time: "21:38", description: "A visitor enters the apartment" },
      ],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("Timeline");
    expect(html).toContain('<time dateTime="21:38">21:38</time>');
    expect(html).toContain('<time dateTime="22:03">22:03</time>');
    expect(html.indexOf("21:38")).toBeGreaterThan(-1);
    expect(html.indexOf("22:03")).toBeGreaterThan(html.indexOf("21:38"));
  });
});

describe("hostile payloads render INERT — Phase 19G §5/§18", () => {
  it("a javascript: renderType falls back and nothing executable or HTML-like forms", () => {
    const content: Record<string, unknown> = {
      renderType: "javascript:alert(1)",
      entries: [{ time: "<script>alert(1)</script>", text: "<img src=x onerror=alert(2)>" }],
    };
    const record = recordWith(content, { title: "<script>alert(1)</script>" });
    expect(resolveEvidenceRenderer(content.renderType)).toBe(GenericEvidence);
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    // The DTO title is escaped literal text.
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script>");
  });

  it("activity-log entries carrying HTML / dangerouslySetInnerHTML-like intent stay literal text", () => {
    const record = recordWith({
      renderType: "ACTIVITY_LOG",
      entries: [
        { time: "22:11", text: "<img src=x onerror=alert(2)>" },
        { time: "22:12", text: "<script>window.danger=1</script> — ひらがな — 你好 — 😀" },
      ],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;window.danger=1&lt;/script&gt;");
    expect(html).toContain("&lt;img");
    expect(html).toContain("ひらがな");
    expect(html).toContain("你好");
    expect(html).toContain("😀");
  });
});

describe("reload determinism — Phase 19G §10", () => {
  it("renders byte-identical panel content for the same ACTIVITY_LOG DTO", () => {
    const record = recordWith({
      renderType: "ACTIVITY_LOG",
      entries: [
        { time: "22:19", text: "Session ended" },
        { time: "22:11", text: "User login detected" },
      ],
    });
    const first = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    const second = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(second).toBe(first);
  });

  it("the generic fallback is pure too", () => {
    const record = recordWith({ renderType: "GENERIC_TEXT" }, { description: "A short summary." });
    const first = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    const second = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(second).toBe(first);
  });
});

describe("Phase 19H — Activity Log layout & time presentation (compact HH:mm, no overlap)", () => {
  const ISO_ENTRIES = [
    { time: "2026-09-11T23:41:50+02:00", text: "User login detected" },
    { time: "2026-09-11T23:43:12+02:00", text: "File opened" },
    { time: "2026-09-11T23:47:05+02:00", text: "Activity recorded at the scene" },
    { time: "2026-09-11T23:50:33+02:00", text: "Session ended" },
  ];

  function count(html: string, needle: string): number {
    let n = 0;
    let from = 0;
    for (;;) {
      const i = html.indexOf(needle, from);
      if (i < 0) break;
      n += 1;
      from = i + 1;
    }
    return n;
  }

  it("compactTimeOf — deterministic and NEVER fabricated (full ISO -> HH:mm, compact passthrough, malformed raw)", () => {
    // Full ISO-8601-with-offset (the production payload shape): HH:mm of the
    // LOCAL time the offset encodes — no conversion, no fabrication.
    expect(compactTimeOf("2026-09-11T23:41:50+02:00")).toBe("23:41");
    expect(compactTimeOf("2026-09-11T23:41:50Z")).toBe("23:41");
    expect(compactTimeOf("2026-09-11T23:41+02:00")).toBe("23:41");
    expect(compactTimeOf("2026-09-11 23:41:50+02:00")).toBe("23:41");
    // ADV-245: a VALID ISO with a LOWERCASE "t" separator (the RFC-3339 case-
    // insensitive "T") must collapse the same way — never fall through to the
    // ~25-char raw fallback that re-entered the nowrap ink overlap.
    expect(compactTimeOf("2026-09-11t23:41:50+02:00")).toBe("23:41");
    expect(compactTimeOf("2026-09-11t23:41:50Z")).toBe("23:41");
    expect(compactTimeOf("2026-09-11t23:41+02:00")).toBe("23:41");
    // Already-compact clock text passes through verbatim.
    expect(compactTimeOf("23:41")).toBe("23:41");
    expect(compactTimeOf("23:41:50")).toBe("23:41:50");
    expect(compactTimeOf("9:41")).toBe("9:41");
    // ADV-246: null / undefined / number inputs coerce safely (like asText) —
    // never a crash, never a fabricated time.
    expect(compactTimeOf(null)).toBe("");
    expect(compactTimeOf(undefined)).toBe("");
    expect(compactTimeOf(42)).toBe("42");
    expect(compactTimeOf(20260911)).toBe("20260911");
    expect(compactTimeOf(false)).toBe("false");
    // Malformed/hostile values fall back to the raw string — safely literal.
    expect(compactTimeOf("not-a-time")).toBe("not-a-time");
    expect(compactTimeOf("22")).toBe("22");
    expect(compactTimeOf("2026-09-11")).toBe("2026-09-11");
    expect(compactTimeOf("")).toBe("");
    expect(compactTimeOf("<script>alert(1)</script>")).toBe("<script>alert(1)</script>");
  });

  it("renders the 4-entry production-shape fixture as FOUR distinct rows: compact times, canonical datetimes, no overlap cause left", () => {
    const record = recordWith({
      renderType: "ACTIVITY_LOG",
      title: "Activity logged at the scene",
      entries: ISO_ENTRIES,
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);

    // (1) exactly four distinct rendered body rows (+ the header row).
    expect(html.match(/<tr>/g) ?? []).toHaveLength(5);
    expect(count(html, 'class="evidence-activity-time"')).toBe(4);
    expect(count(html, 'class="evidence-activity-text"')).toBe(4);

    // (2) four <time> elements; (3+4) each carries the canonical full ISO
    // datetime while the VISIBLE value is the compact HH:mm clock.
    expect(count(html, "<time ")).toBe(4);
    expect(html).toContain('<time dateTime="2026-09-11T23:41:50+02:00">23:41</time>');
    expect(html).toContain('<time dateTime="2026-09-11T23:43:12+02:00">23:43</time>');
    expect(html).toContain('<time dateTime="2026-09-11T23:47:05+02:00">23:47</time>');
    expect(html).toContain('<time dateTime="2026-09-11T23:50:33+02:00">23:50</time>');
    for (const t of ["23:41", "23:43", "23:47", "23:50"]) {
      expect(html).toContain(`>${t}</time>`);
    }
    // The full ISO appears ONLY as the dateTime attribute (exactly 4 times) —
    // never as visible text (the pre-19H overlap cause).
    expect(count(html, "+02:00")).toBe(4);
    expect(html).not.toMatch(/>2026-09-11T23:41:50\+02:00</);

    // (5) every activity text appears exactly once, on its own row.
    for (const text of ["User login detected", "File opened", "Activity recorded at the scene", "Session ended"]) {
      expect(count(html, text)).toBe(1);
    }

    // (6) NO inline positioning, and the row/cell class lists carry no rule
    // that could absolutely-position or height-constrain a row (evidence-*
    // layout classes only). The table is a normal in-flow semantic table:
    // body rows follow the head directly inside <tbody>.
    expect(html).not.toContain('style="');
    expect(html).not.toContain("position:");
    expect(html).not.toContain("absolute");
    expect(html).toContain('<table class="evidence-table evidence-activity-log">');
    expect(html).toContain("<thead>");
    expect(html).toContain("</thead><tbody><tr>");
    expect(html).toContain("</tbody>");
  });

  it("applies the SAME compact presentation to TIMELINE (general rule — no Laptop special-case)", () => {
    const record = recordWith({
      renderType: "TIMELINE",
      events: [
        { time: "2026-09-11T21:38:07+02:00", description: "A visitor enters the apartment" },
        { time: "2026-09-11T22:03:41+02:00", description: "The visitor leaves in a hurry" },
      ],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain('<time dateTime="2026-09-11T21:38:07+02:00">21:38</time>');
    expect(html).toContain('<time dateTime="2026-09-11T22:03:41+02:00">22:03</time>');
    expect(html).not.toMatch(/>2026-09-11T21:38:07\+02:00</);
  });

  it("ADV-245 — a VALID lowercase-t ISO renders as a compact clock in the table (no ~100px raw fallback in the nowrap cell)", () => {
    const record = recordWith({
      renderType: "ACTIVITY_LOG",
      entries: [{ time: "2026-09-11t23:41:50+02:00", text: "User login detected" }],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain('<time dateTime="2026-09-11t23:41:50+02:00">23:41</time>');
    expect(html).not.toMatch(/>2026-09-11t23:41:50\+02:00</);
  });

  it("ADV-247 — mixed compact clock shapes normalize to ONE granularity (HH:mm:ss when any entry needs seconds) with logical order", () => {
    const record = recordWith({
      renderType: "TIMELINE",
      events: [
        { time: "22:11:30", description: "Half past the hour" },
        { time: "22:11", description: "On the hour" },
      ],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    // BOTH rows render at HH:mm:ss — never a bare seconds-less clock beside a
    // seconds-full clock. dateTime keeps the canonical value verbatim.
    expect(html).toContain('<time dateTime="22:11">22:11:00</time>');
    expect(html).toContain('<time dateTime="22:11:30">22:11:30</time>');
    expect(html).not.toContain(">22:11</time>");
    // Ordering runs on the normalized time string: 22:11:00 before 22:11:30 —
    // textual AND logical, not lexicographic luck.
    const first = html.indexOf("22:11:00");
    const second = html.indexOf("22:11:30");
    expect(first).toBeGreaterThan(-1);
    expect(second).toBeGreaterThan(first);
  });

  it("ADV-247 — a compact HH:mm:ss passthrough beside a full-ISO entry normalizes BOTH to HH:mm:ss; dateTime stays canonical", () => {
    const record = recordWith({
      renderType: "ACTIVITY_LOG",
      entries: [
        { time: "2026-09-11T20:00+02:00", text: "Camera line" },
        { time: "20:00:30", text: "Door sensor line" },
      ],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain('<time dateTime="2026-09-11T20:00+02:00">20:00:00</time>');
    expect(html).toContain('<time dateTime="20:00:30">20:00:30</time>');
    expect(html).not.toContain(">20:00</time>");
    // "20:00:00" < "20:00:30" — the normalized string orders logically.
    expect(html.indexOf("20:00:00</time>")).toBeGreaterThan(-1);
    expect(html.indexOf("20:00:30</time>")).toBeGreaterThan(html.indexOf("20:00:00</time>"));
  });

  it("ADV-247 — a uniform full-ISO payload stays HH:mm (Phase 19H contract): ISO seconds never force ':00' padding by themselves", () => {
    const record = recordWith({
      renderType: "TIMELINE",
      events: [
        { time: "2026-09-11T21:38:07+02:00", description: "A visitor enters the apartment" },
        { time: "2026-09-11T22:03:41+02:00", description: "The visitor leaves in a hurry" },
      ],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain('<time dateTime="2026-09-11T21:38:07+02:00">21:38</time>');
    expect(html).toContain('<time dateTime="2026-09-11T22:03:41+02:00">22:03</time>');
    expect(html).not.toContain("21:38:00");
  });
});

describe("Phase 19H — jsdom layout regression (live DOM: distinct rows, normal in-flow table, nothing absolute)", () => {
  // jsdom ships NO layout engine, so getBoundingClientRect is uniformly
  // (0,0,0,0) across elements. "Each row has its own bounding box" is
  // therefore proven structurally here — four sibling table rows sharing no
  // positioning — plus the computed UA-sheet display (table-row: a normal
  // in-flow block). Real-browser geometry for this exact fixture is measured
  // by the QA/Playwright layer (evidence policy); this suite pins the
  // renderer/CSS contract that makes overlap structurally impossible (compact
  // visible time, canonical datetime preserved, no absolute/height rules).
  const ISO_ENTRIES = [
    { time: "2026-09-11T23:41:50+02:00", text: "User login detected" },
    { time: "2026-09-11T23:43:12+02:00", text: "File opened" },
    { time: "2026-09-11T23:47:05+02:00", text: "Activity recorded at the scene" },
    { time: "2026-09-11T23:50:33+02:00", text: "Session ended" },
  ];

  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container.remove();
  });

  function mountActivityLog(entries: unknown[]): void {
    const record = recordWith({
      renderType: "ACTIVITY_LOG",
      title: "Activity logged at the scene",
      entries,
    });
    act(() => {
      root = createRoot(container);
      root.render(<EvidencePanel record={record} onClose={() => {}} />);
    });
  }

  it("renders 4 DISTINCT table-row siblings whose cells keep compact visible times and canonical datetimes", () => {
    mountActivityLog(ISO_ENTRIES);
    const rows = Array.from(container.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(4);
    expect(Array.from(container.querySelectorAll("time"))).toHaveLength(4);
    for (const row of rows) {
      const cs = getComputedStyle(row);
      expect(cs.display).toBe("table-row"); // normal in-flow block
      expect(cs.position).toBe(""); // no author rule positions the row
      expect(row.querySelectorAll("td.evidence-activity-time")).toHaveLength(1);
      expect(row.querySelectorAll("td.evidence-activity-text")).toHaveLength(1);
      const time = row.querySelector("time")!;
      expect(time.textContent).toMatch(/^\d{2}:\d{2}$/); // compact visible time
      expect(time.getAttribute("dateTime")).toMatch(/^2026-09-11T\d{2}:\d{2}:\d{2}\+02:00$/);
    }
    const table = container.querySelector("table.evidence-activity-log")!;
    expect(getComputedStyle(table).display).toBe("table");
    expect(getComputedStyle(table).position).toBe("");
    // Rows sit DIRECTLY in <tbody> (no wrapper that could be positioned or
    // floated between them) — the container height grows with the rows.
    expect(container.querySelector("tbody")!.childElementCount).toBe(4);
    // The full ISO is never the VISIBLE value any more (pre-19H overlap cause).
    expect(container.querySelector("time")!.textContent).not.toContain("T");
  });

  it("ADV-245 — the PRODUCTION time-cell rule clips a long raw fallback inside its 5.5rem column (overflow:hidden + ellipsis, nowrap kept for the compact clock)", () => {
    // A well-formed compact entry + a LONG non-matching value: compactTimeOf
    // keeps malformed text VERBATIM (never fabricated), so without the clip
    // the raw fallback would repaint the pre-19H ~100px ink overlap. This test
    // asserts the ACTUAL shipped stylesheet (read from src/index.css, so the
    // assertion can never drift from production) resolves the cell to a
    // bleed-proof rule inside a fixed 5.5rem column.
    const longRaw = "23:41:50 heavy-probe-line-2026-09-11t23:41:50+02:00-extra";
    mountActivityLog([
      { time: "23:41", text: "User login detected" },
      { time: longRaw, text: "Hostile long non-clock value" },
    ]);

    const style = document.createElement("style");
    // The actual SHIPPED stylesheet. (In the jsdom test environment the global
    // URL class is jsdom's — it resolves relative URLs against a stub
    // http://localhost:3000 — so the stylesheet path is resolved from the
    // vitest working directory, which is the frontend/ package root under the
    // documented `cd frontend && npm test` invocation.)
    style.textContent = readFileSync(join(process.cwd(), "src", "index.css"), "utf8");
    document.head.appendChild(style);
    try {
      const cells = Array.from(
        container.querySelectorAll("td.evidence-activity-time"),
      ) as HTMLElement[];
      expect(cells).toHaveLength(2);
      for (const cell of cells) {
        const cs = getComputedStyle(cell);
        // The last-resort clip: the cell box bounds the ink and truncates
        // with an ellipsis — ink can NEVER bleed over the Activity column...
        expect(cs.overflow).toBe("hidden");
        expect(cs.textOverflow).toBe("ellipsis");
        expect(cs.maxWidth).toBe("100%");
        // ...while the normal compact clock keeps its nowrap presentation.
        expect(cs.whiteSpace).toBe("nowrap");
        // The time column itself is a fixed, static 5.5rem box.
        expect(cs.width).toBe("5.5rem");
        expect(cs.position).toBe("");
      }
      // Fixed table layout + a 5.5rem first column bound the cell width.
      const table = container.querySelector("table.evidence-activity-log")!;
      expect(getComputedStyle(table).tableLayout).toBe("fixed");
      expect(getComputedStyle(table).width).toBe("100%");
      const th = container.querySelector("th")!;
      expect(getComputedStyle(th).width).toBe("5.5rem");
      // Visible text contract: the compact entry shows its compact clock; the
      // raw fallback stays VERBATIM (never fabricated) and is CLIPPED by the
      // resolved cell rule. (jsdom ships no layout engine — scrollWidth and
      // clientWidth are uniformly 0, so clipping is proven by the resolved
      // CSS contract above; real ink geometry is QA/Playwright's domain.)
      const times = Array.from(container.querySelectorAll("time"));
      expect(times.map((t) => t.textContent)).toEqual(["23:41", longRaw]);
      for (const cell of cells) {
        const t = cell.querySelector("time")!;
        expect(t.scrollWidth).toBeLessThanOrEqual(t.clientWidth);
        expect(cell.scrollWidth).toBeLessThanOrEqual(cell.clientWidth);
      }
    } finally {
      style.remove();
    }
  });
});

describe("Phase 19J — 15/20-row activity logs: distinct rows, compact times, scroll region (§31/§32/§54)", () => {
  /**
   * Strictly chronological full-ISO production-shape entries (20:31 →
   * 21:28 across 15/20 rows): unique timestamps, distinct ignored-safe texts.
   * `isoAt(i)` and `clockAt(i)` mirror the SAME arithmetic so the expected
   * <time> markup can be asserted without string drift.
   */
  function startMinuteOf(index: number): number {
    return 20 * 60 + 31 + index * 3;
  }
  function isoAt(index: number): string {
    const minute = startMinuteOf(index);
    const hh = String(Math.floor(minute / 60)).padStart(2, "0");
    const mm = String(minute % 60).padStart(2, "0");
    return `2026-09-11T${hh}:${mm}:00+02:00`;
  }
  function clockAt(index: number): string {
    const minute = startMinuteOf(index);
    const hh = String(Math.floor(minute / 60)).padStart(2, "0");
    const mm = String(minute % 60).padStart(2, "0");
    return `${hh}:${mm}`;
  }
  function activityEntries(count: number): Array<{ time: string; text: string }> {
    const entries: Array<{ time: string; text: string }> = [];
    for (let index = 0; index < count; index += 1) {
      entries.push({
        time: isoAt(index),
        text: `Scheduled activity ${String(index + 1).padStart(2, "0")}`,
      });
    }
    return entries;
  }

  const SHIPPED_CSS = readFileSync(join(process.cwd(), "src", "index.css"), "utf8");

  /** Extract the shipped CSS rule block for a selector (real newlines kept). */
  function shippedRule(selectorPattern: string): string {
    const match = new RegExp(`${selectorPattern}\\s*\\{([^}]*)\\}`).exec(SHIPPED_CSS);
    if (!match) throw new Error(`missing shipped rule matching ${selectorPattern}`);
    return match[1];
  }

  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container.remove();
  });

  function mountActivityLog(entries: unknown[]): void {
    const record = recordWith({
      renderType: "ACTIVITY_LOG",
      title: "Activity logged at the scene",
      entries,
    });
    act(() => {
      root = createRoot(container);
      root.render(<EvidencePanel record={record} onClose={() => {}} />);
    });
  }

  it("renders the SHIPPED activity-log scroll-region CSS contract (max-height cap + vertical auto scroll, no forced height/min-width)", () => {
    const block = shippedRule(`\\.evidence-activity-scroll`);
    expect(block).toMatch(/max-height\s*:\s*24rem/);
    expect(block).toMatch(/overflow-y\s*:\s*auto/);
    expect(block).toMatch(/overflow-x\s*:\s*hidden/);
    // The cap is a CEILING, not a forced box: no height/min-width rule that
    // could force a scrollbar when the table fits or force horizontal pages.
    expect(block).not.toMatch(/(?<!-)height\s*:/);
    expect(block).not.toMatch(/min-width/);
  });

  for (const count of [15, 20]) {
    it(`${count} entries render ${count} DISTINCT sibling rows: visible HH:mm, canonical dateTime, normal in-flow table (no overlap cause)`, () => {
      mountActivityLog(activityEntries(count));
      const rows = Array.from(container.querySelectorAll("tbody tr"));
      expect(rows).toHaveLength(count);
      const times = Array.from(container.querySelectorAll("time"));
      expect(times).toHaveLength(count);
      expect(container.querySelectorAll("td.evidence-activity-time")).toHaveLength(count);
      expect(container.querySelectorAll("td.evidence-activity-text")).toHaveLength(count);
      for (const row of rows) {
        const cs = getComputedStyle(row);
        expect(cs.display).toBe("table-row"); // normal in-flow block
        expect(cs.position).toBe(""); // no author rule positions the row
        expect(row.querySelectorAll("td.evidence-activity-time")).toHaveLength(1);
        expect(row.querySelectorAll("td.evidence-activity-text")).toHaveLength(1);
        const time = row.querySelector("time")!;
        expect(time.textContent).toMatch(/^\d{2}:\d{2}$/); // compact visible time
        expect(time.getAttribute("dateTime")).toMatch(/^2026-09-11T\d{2}:\d{2}:\d{2}\+02:00$/);
      }
      // Every canonical datetime is present once, in chronological order.
      const datetimes = times.map((t) => t.getAttribute("dateTime") ?? "");
      expect([...datetimes].sort()).toEqual(datetimes);
      // All rows sit DIRECTLY in <tbody> (no positioned wrapper between them).
      expect(container.querySelector("tbody")!.childElementCount).toBe(count);
      const table = container.querySelector("table.evidence-activity-log")!;
      expect(getComputedStyle(table).display).toBe("table");
      expect(getComputedStyle(table).position).toBe("");
    });
  }

  it("STATIC markup — both counts keep the compact contract: N <tr> rows, N <time>, visible HH:mm for every entry, canonical ISO only in dateTime", () => {
    for (const count of [15, 20]) {
      const entries = activityEntries(count);
      const record = recordWith({ renderType: "ACTIVITY_LOG", title: "Activity logged at the scene", entries });
      const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
      expect(html.match(/<tr>/g) ?? []).toHaveLength(count + 1); // + thead row
      expect(html.match(/<time /g) ?? []).toHaveLength(count);
      expect(html.match(/>\d{2}:\d{2}<\/time>/g) ?? []).toHaveLength(count);
      // The canonical full ISO appears EXACTLY once per entry and only as the
      // dateTime attribute (never as visible text) — the 19H overlap cause.
      expect(html.match(/\+02:00/g) ?? []).toHaveLength(count);
      for (let index = 0; index < count; index += 1) {
        expect(html).toContain(`<time dateTime="${isoAt(index)}">${clockAt(index)}</time>`);
      }
      expect(html).not.toMatch(/>2026-09-11T\d{2}:\d{2}:\d{2}\+02:00</);
    }
  });

  it("reload/identical-render determinism — the same 15- and 20-row DTO renders byte-identical markup (§18/§54)", () => {
    for (const entries of [activityEntries(15), activityEntries(20)]) {
      const record = recordWith({ renderType: "ACTIVITY_LOG", title: "Activity logged at the scene", entries });
      const first = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
      const second = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
      expect(second).toBe(first);
    }
  });

  it("the scroll region is a labelled keyboard-focusable landmark with a fixed heading and logical reading order (§32/§34)", () => {
    const record = recordWith({ renderType: "ACTIVITY_LOG", title: "Activity logged at the scene", entries: activityEntries(20) });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    // The region wrapper is deterministic markup: class + role + tabindex +
    // aria-label, with NO inline style/positioning or forced dimensions.
    expect(html).toContain(
      '<div class="evidence-activity-scroll" role="region" tabindex="0" aria-label="Activity log entries">',
    );
    expect(html).not.toContain('style="');
    // Reading order: fixed heading -> scroll region -> table -> Close button.
    const headingPos = html.indexOf(">Activity log</h4>");
    const regionPos = html.indexOf('class="evidence-activity-scroll"');
    const tablePos = html.indexOf('<table class="evidence-table evidence-activity-log">');
    const firstTimePos = html.indexOf("<time ");
    const closePos = html.indexOf('data-testid="evidence-close"');
    expect(headingPos).toBeGreaterThan(-1);
    expect(regionPos).toBeGreaterThan(headingPos);
    expect(tablePos).toBeGreaterThan(regionPos);
    expect(firstTimePos).toBeGreaterThan(tablePos);
    expect(closePos).toBeGreaterThan(firstTimePos);
    // The semantic table surface is intact (NOT replaced by canvas-only text).
    expect(html).toContain("<thead>");
    expect(html).toContain("</thead><tbody><tr>");
    expect(html).toContain('<th scope="col">Time</th>');
    expect(html).toContain('<th scope="col">Activity</th>');
  });

  it("a SHORT table uses the SAME deterministic wrapper but no scrollbar is forced — max-height is a ceiling, not a fixed box (§32)", () => {
    // 4 rows already fit the natural panel — the wrapper stays the single code
    // path (no conditional markup), but nothing forces the region taller than
    // its content: the shipped cap only engages when rows outgrow 24rem.
    mountActivityLog(activityEntries(4));
    const wrapper = container.querySelector("div.evidence-activity-scroll")!;
    expect(wrapper).not.toBeNull();
    expect(wrapper.getAttribute("role")).toBe("region");
    expect(wrapper.getAttribute("tabindex")).toBe("0");
    const region = shippedRule(`\\.evidence-activity-scroll`);
    expect(region).toMatch(/max-height\s*:\s*24rem/);
    expect(region).toMatch(/overflow-y\s*:\s*auto/);
    expect(region).not.toMatch(/(?<!-)height\s*:/);
    expect(Array.from(container.querySelectorAll("tbody tr"))).toHaveLength(4);
  });

  it("TIMELINE gets the SAME bounded scroll region (both renderers — no Laptop special-case, §32/§33)", () => {
    const record = recordWith({
      renderType: "TIMELINE",
      events: [
        { time: "2026-09-11T21:38:07+02:00", description: "A visitor enters the apartment" },
        { time: "2026-09-11T22:03:41+02:00", description: "The visitor leaves in a hurry" },
      ],
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain(
      '<div class="evidence-activity-scroll" role="region" tabindex="0" aria-label="Timeline entries">',
    );
    expect(html).toContain('<table class="evidence-table evidence-timeline">');
    expect(html).toContain('<time dateTime="2026-09-11T21:38:07+02:00">21:38</time>');
    // Reading order: fixed heading -> region -> table.
    expect(html.indexOf("evidence-activity-scroll")).toBeGreaterThan(html.indexOf(">Timeline</h4>"));
    expect(html.indexOf('class="evidence-table evidence-timeline">')).toBeGreaterThan(
      html.indexOf("evidence-activity-scroll"),
    );
  });

  it("hostile labels & over-long text stay INERT and BOUNDED (§49/§31): escaped, clipped time cell, wrapping activity cell, 15 rows intact", () => {
    const longRaw = "23:41:50 heavy-probe-line-2026-09-11t23:41:50+02:00-extra";
    const entries = activityEntries(15);
    entries[3] = {
      time: isoAt(3),
      text: "<img src=x onerror=alert(1)> <script>window.evil=1</script> — ひらがな — 😀",
    };
    // A malformed long value is kept VERBATIM (never fabricated) so it must be
    // clipped inside its 5.5rem time cell; the 5000-char activity must wrap.
    entries[7] = { time: longRaw, text: "X".repeat(5000) };
    mountActivityLog(entries);

    expect(container.querySelectorAll("tbody tr")).toHaveLength(15); // nothing dropped
    const html = container.innerHTML;
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;window.evil=1&lt;/script&gt;");
    expect(html).toContain("&lt;img");
    expect(html).toContain("ひらがな");
    expect(html).toContain("😀");

    const style = document.createElement("style");
    style.textContent = SHIPPED_CSS;
    document.head.appendChild(style);
    try {
      // Time cells: the last-resort clip bounds ink to the fixed 5.5rem column
      // (an over-long raw fallback can NEVER bleed into the Activity column).
      const timeCells = Array.from(container.querySelectorAll("td.evidence-activity-time")) as HTMLElement[];
      expect(timeCells).toHaveLength(15);
      for (const cell of timeCells) {
        const cs = getComputedStyle(cell);
        expect(cs.overflow).toBe("hidden");
        expect(cs.textOverflow).toBe("ellipsis");
        expect(cs.whiteSpace).toBe("nowrap");
        expect(cs.width).toBe("5.5rem");
        expect(cs.position).toBe("");
      }
      // Activity cells: any unbroken long text wraps inside the cell — no cell
      // bleed and no horizontal page scroll can ever form.
      const textCells = Array.from(container.querySelectorAll("td.evidence-activity-text")) as HTMLElement[];
      expect(textCells).toHaveLength(15);
      for (const cell of textCells) {
        expect(getComputedStyle(cell).overflowWrap).toBe("anywhere");
      }
      // The hostile row still shows its compact clock; the malformed long value
      // stays VERBATIM inside its clipped cell.
      const times = Array.from(container.querySelectorAll("time"));
      const byClock = new Map(times.map((t) => [t.textContent, t]));
      expect(byClock.get(clockAt(3))?.getAttribute("dateTime")).toBe(isoAt(3));
      expect(byClock.get(longRaw)).toBeDefined();
    } finally {
      style.remove();
    }
  });
});

describe("Phase 19J §33 — width-independent responsive contracts (1280/600/360)", () => {
  // jsdom ships NO layout engine, so "at 1280/600/360" is pinned as the
  // SHIPPED stylesheet contract that makes the phase guarantees hold at EVERY
  // panel width (same approach as the ADV-245 regression test; real-pixel
  // geometry stays the QA/Playwright layer's domain). Every assertion reads
  // src/index.css directly, so the contract can never drift from production.
  // The worktree stylesheet is LF or CRLF depending on git autocrlf, so the
  // regex must never depend on line endings: normalize before matching.
  const css = readFileSync(join(process.cwd(), "src", "index.css"), "utf8")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");

  function rule(selectorPattern: string): string {
    // Multi-selector patterns span real (possibly CRLF) newlines in this file;
    // collapse separator whitespace to \s* so the match is line-ending agnostic.
    const source = selectorPattern.replace(/\s*\n\s*/g, "\\s*");
    const match = new RegExp(`${source}\\s*\\{([^}]*)\\}`).exec(css);
    if (!match) throw new Error(`missing shipped rule matching ${selectorPattern}`);
    return match[1];
  }

  it("the panel box is viewport-bounded with its own vertical scroll — usable at 360px, never horizontally oversized", () => {
    const panel = rule(`\\.evidence-panel`);
    expect(panel).toMatch(/width\s*:\s*min\(24rem,\s*calc\(100%\s*-\s*2rem\)\)/);
    expect(panel).toMatch(/max-height\s*:\s*calc\(100%\s*-\s*2rem\)/);
    expect(panel).toMatch(/overflow\s*:\s*auto/);
  });

  it("fixed table layout + full width + stable 5.5rem time column at every width: time readable, no table bleed, no horizontal page scroll", () => {
    const table = rule(`\\.evidence-activity-log,
\\.evidence-timeline`);
    expect(table).toMatch(/table-layout\s*:\s*fixed/);
    expect(table).toMatch(/width\s*:\s*100%/);
    const header = rule(`\\.evidence-activity-log th:first-child,
\\.evidence-timeline th:first-child`);
    expect(header).toMatch(/width\s*:\s*5\.5rem/);
    const cell = rule(`\\.evidence-activity-time`);
    expect(cell).toMatch(/width\s*:\s*5\.5rem/);
    // no min-width anywhere in the table/scroll-region rules -> the table can
    // never force the page wider than the viewport.
    expect(table).not.toMatch(/min-width/);
    expect(cell).not.toMatch(/min-width/);
  });

  it("activity text wraps at any width; long values stay inside their cells (wrap, not bleed)", () => {
    const textCell = rule(`\\.evidence-activity-text`);
    expect(textCell).toMatch(/overflow-wrap\s*:\s*anywhere/);
    expect(textCell).toMatch(/vertical-align\s*:\s*top/);
  });

  it("the panel can scroll vertically: the long-log scroll region caps at 24rem with overflow-y auto (15–20 rows scroll inside)", () => {
    const region = rule(`\\.evidence-activity-scroll`);
    expect(region).toMatch(/max-height\s*:\s*24rem/);
    expect(region).toMatch(/overflow-y\s*:\s*auto/);
  });
});