import { describe, expect, it } from "vitest";
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
import { timeEntryItems } from "./renderers/shared";

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
    expect(html).toContain("2026-09-10T18:04:00+02:00");
    expect(html).toContain("<dt>Body</dt>");
    expect(html).toContain("let us talk");
    expect(html).not.toContain('"fromPersonId"');
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